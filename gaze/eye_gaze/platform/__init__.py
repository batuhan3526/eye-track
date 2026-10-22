# gaze/eye_gaze/platform/__init__.py
"""What differs between Windows and Linux, behind one interface.

Windows keeps exactly today's code paths: keyboard_api() IS the `keyboard`
module (imported at call time, so a missing library raises ImportError where
it always did, and tests that swap sys.modules["keyboard"] keep working).

On Linux one input backend is chosen once per process (linux_backend()):

  1. evdev  - every keyboard in /dev/input readable and /dev/uinput writable
              (works on Wayland and X11; needs the one-time permission setup);
  2. x11    - an X11 session with DISPLAY set (XTest + XGrabKey, no root);
  3. null   - neither: nothing can be driven, and capabilities() says why.

A backend that is available but fails to start falls through to the next one;
its error is kept in the chosen backend's `start_errors`.

Backend interface (every backend, NullBackend included):
  name: str; screens() -> [(x, y, w, h)]; primary() -> (x, y, w, h);
  get_cursor() -> (x, y); set_cursor(x, y); button("left"|"right", down);
  scroll(amount) (Windows wheel units as in actions.scroll: 120 = one
  notch, > 0 up); keyboard (see below); gamepad() -> pad | None; close();
  optional capabilities() -> {key: (bool, reason)} overriding the defaults.

The keyboard object offers the subset of the `keyboard` library the driver
uses: hook_key(name, callback, suppress=False) -> remover, send(combo),
write(text), is_pressed(name) -> bool, unhook_all(). The callback gets an
event with event_type ("down"/"up"), name and injected (the backend sets
it; eye_gaze.injected only patches the Windows library); returning False
swallows the event. Key names are the `keyboard` library's (see keys.py), so
one config.yaml works on both systems. Callbacks must return at once: the X11
backend cuts every hook when one keeps the keyboard frozen over a second. A
Linux keyboard may also offer alive() -> bool (False once its key listener
died; the backend's capabilities() then reports the keys as not working).

capabilities() reasons are full Turkish, for the Tk windows. The console is
cp1254 and must stay ASCII: callers that print a reason pass it through
ascii_text() first.
"""
import os
import threading
import unicodedata

from eye_gaze.i18n import t as _t

OS = "windows" if os.name == "nt" else "linux"

CAPABILITY_KEYS = (
    "cursor", "global_keys", "suppress_keys", "injected_detection", "gamepad",
    "zoom_capture", "dwell_ring", "snap", "osk", "clipboard", "onecore_tts",
    "system_dictation", "audio",
)
# Capabilities that exist only on Linux (False on Windows). None today.
LINUX_ONLY = frozenset()

_INPUT_KEYS = ("cursor", "global_keys", "suppress_keys",
               "injected_detection", "gamepad")

GUESSED_SCREEN = (0, 0, 1920, 1080)

_backend = [None]
_backend_lock = threading.Lock()


def session_type():
    """"windows", "x11", "wayland" or "none" (no graphical session).

    XDG_SESSION_TYPE is trusted only when it says x11 or wayland: startx from
    a text console leaves it at "tty" although an X display is running."""
    if OS == "windows":
        return "windows"
    declared = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if declared in ("x11", "wayland"):
        return declared
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "none"


def keyboard_api():
    """The keyboard API: the `keyboard` module on Windows, the Linux
    backend's keyboard object on Linux."""
    if OS == "windows":
        import keyboard
        return keyboard
    return linux_backend().keyboard


def linux_backend():
    """The process-wide Linux input backend (chosen on first use)."""
    with _backend_lock:
        if _backend[0] is None:
            _backend[0] = _choose_backend()
        return _backend[0]


def _choose_backend():
    from eye_gaze.platform import linux_evdev, linux_x11
    errors = []
    for cls in (linux_evdev.EvdevBackend, linux_x11.X11Backend):
        try:
            if not cls.available():
                continue
            backend = cls()
        except Exception as exc:
            errors.append("%s: %s: %s" % (cls.name, type(exc).__name__, exc))
            continue
        try:
            backend.start_errors = list(errors)
        except Exception:
            pass
        return backend
    return NullBackend(start_errors=errors)


class KeyEvent:
    """What a Linux backend hands a hook_key callback."""
    __slots__ = ("event_type", "name", "injected")

    def __init__(self, event_type, name, injected=False):
        self.event_type = event_type
        self.name = name
        self.injected = bool(injected)


class NullKeyboard:
    """No keyboard backend. hook_key raises ValueError, as the `keyboard`
    library does for a key it cannot hook, so KeyPresses lists every key in
    bad_keys and Hotkeys falls back to the window-key mode."""

    def hook_key(self, name, callback, suppress=False):
        raise ValueError("no keyboard backend: cannot hook %r" % (name,))

    def send(self, combo):
        raise RuntimeError("no keyboard backend: cannot send %r" % (combo,))

    def write(self, text):
        raise RuntimeError("no keyboard backend: cannot type text")

    def is_pressed(self, name):
        return False

    def unhook_all(self):
        pass


def _tk_screen_size():
    """(w, h) of the default screen via Tk, or None without a display."""
    try:
        import tkinter
        root = tkinter.Tk()
        try:
            root.withdraw()
            return (int(root.winfo_screenwidth()),
                    int(root.winfo_screenheight()))
        finally:
            root.destroy()
    except Exception:
        return None


class NullBackend:
    """Neither evdev nor X11 can be used. The cursor, clicks and scroll do
    nothing - capabilities() reports that (once, at start), so this is not a
    silent no-op. The screen is measured through Tk when a display exists;
    1920x1080 is only a last-resort guess, flagged in screen_guessed and in
    the cursor reason."""

    name = "null"

    def __init__(self, start_errors=()):
        self.keyboard = NullKeyboard()
        self.start_errors = list(start_errors)
        self.screen_guessed = False
        self._screens = None
        self._cursor = None

    def screens(self):
        if self._screens is None:
            size = _tk_screen_size()
            if size and size[0] > 0 and size[1] > 0:
                self._screens = [(0, 0, size[0], size[1])]
            else:
                self.screen_guessed = True
                self._screens = [GUESSED_SCREEN]
        return list(self._screens)

    def primary(self):
        return self.screens()[0]

    def get_cursor(self):
        if self._cursor is None:
            x, y, w, h = self.primary()
            return (x + w // 2, y + h // 2)
        return self._cursor

    def set_cursor(self, x, y):
        self._cursor = (int(x), int(y))

    def button(self, btn, down):
        pass

    def scroll(self, amount):
        pass

    def gamepad(self):
        return None

    def close(self):
        pass


# --- capabilities -------------------------------------------------------

def _xorg():
    return _t("platform.xorg")


def _no_session():
    return _t("platform.no_session")


def _null_reason(session, backend):
    if session == "wayland":
        why = _t("platform.wayland_input", xorg=_xorg())
    elif session == "x11":
        why = _t("platform.no_display")
    else:
        why = _no_session()
    errors = getattr(backend, "start_errors", None)
    if errors:
        why += _t("platform.start_failed", errors="; ".join(errors))
    if getattr(backend, "screen_guessed", False):
        why += _t("platform.screen_guessed")
    return why


def _input_caps(session, backend):
    name = getattr(backend, "name", "null")
    if name == "evdev":
        return {
            "cursor": (True, _t("platform.uinput_pointer")),
            "global_keys": (True, "evdev (/dev/input)"),
            "suppress_keys": (True, _t("platform.evdev_grab")),
            # The on-screen keyboards type through the display server, never
            # through /dev/input: their keys do not reach this hook at all.
            "injected_detection": (True, _t("platform.evdev_osk")),
            "gamepad": (True, "evdev (/dev/input)"),
        }
    if name == "x11":
        return {
            "cursor": (True, "X11 XTest"),
            "global_keys": (True, "X11 XGrabKey"),
            "suppress_keys": (True, "X11 XGrabKey"),
            "injected_detection": (False, _t("platform.x11_injected")),
            "gamepad": (False, _t("platform.x11_gamepad")),
        }
    why = _null_reason(session, backend)
    return {k: (False, why) for k in _INPUT_KEYS}


def _importable(module):
    import importlib.util
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _overlay_caps(session):
    """The magnifier's screenshot and the dwell ring's window on Linux.

    Wayland gives neither to an ordinary client. XWayland's DISPLAY is no way
    round it: a screenshot through it sees only the X clients (black where
    the Wayland windows are), and an X window cannot stay above them."""
    if session == "wayland":
        return {
            "zoom_capture": (False, _t("platform.wayland_screenshot",
                                      xorg=_xorg())),
            "dwell_ring": (False, _t("platform.wayland_overlay",
                                    xorg=_xorg())),
        }
    if session != "x11":
        return {k: (False, _no_session())
                for k in ("zoom_capture", "dwell_ring")}
    if not os.environ.get("DISPLAY"):
        why = _t("platform.no_display")
        return {k: (False, why) for k in ("zoom_capture", "dwell_ring")}
    caps = {"zoom_capture": (True, _t("platform.x11_screenshot")),
            "dwell_ring": (True, _t("platform.x11_overlay"))}
    if not _importable("mss"):
        caps["zoom_capture"] = (False, _t("platform.no_mss"))
    if not _importable("Xlib"):
        caps["dwell_ring"] = (False, _t("platform.no_xlib"))
    return caps


def overlay_status(key):
    """(available, Turkish reason) for "zoom_capture" or "dwell_ring" in this
    session - what zoom.grab_region() and dwell_ring.attach() decide on, and
    what capabilities() reports. Cheap: no backend, no X connection."""
    if OS == "windows":
        return (True, "")
    return _overlay_caps(session_type())[key]


def _desktop_caps(session):
    if session == "none":
        return {k: (False, _no_session())
                for k in ("zoom_capture", "dwell_ring", "osk", "clipboard")}
    # Clipboard and on-screen keyboard: the same choice linux_misc makes
    # when they are used, so what is reported is what will run.
    from eye_gaze.platform import linux_misc
    caps = dict(_overlay_caps(session))
    caps["clipboard"] = linux_misc.clipboard_status()
    caps["osk"] = linux_misc.osk_status()
    return caps


def capabilities():
    """{key: (available, reason)} for every key in CAPABILITY_KEYS.

    The reason is Turkish (Tk text); print it through ascii_text()."""
    if OS == "windows":
        return {k: (k not in LINUX_ONLY,
                    _t("platform.linux_only") if k in LINUX_ONLY else "")
                for k in CAPABILITY_KEYS}
    session = session_type()
    backend = linux_backend()
    caps = _input_caps(session, backend)
    caps.update(_desktop_caps(session))
    caps["snap"] = (False, _t("platform.snap_windows_only"))
    caps["onecore_tts"] = (False, _t("platform.onecore_windows_only"))
    caps["system_dictation"] = (False, _t("platform.winh_windows_only"))
    from eye_gaze import playback
    caps["audio"] = playback.audio_status()
    try:
        override = backend.capabilities() if hasattr(
            backend, "capabilities") else {}
    except Exception:
        override = {}
    for key, value in (override or {}).items():
        if key in caps:
            caps[key] = value
    return caps


_TR_ASCII = str.maketrans({"ı": "i", "İ": "I"})


def ascii_text(text):
    """`text` for the cp1254 console: Turkish letters lose their marks,
    anything else non-ASCII becomes '?'."""
    s = unicodedata.normalize("NFKD", str(text).translate(_TR_ASCII))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.encode("ascii", "replace").decode("ascii")
