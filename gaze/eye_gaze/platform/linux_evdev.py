# gaze/eye_gaze/platform/linux_evdev.py
"""evdev backend (Wayland, or X11 with input permission): keyboards and
gamepads are read from /dev/input; the cursor is a uinput absolute pointer
(the tablet method: Wayland does not let a program read or set the cursor,
so "where the cursor is" is where we last put it); the keys we send and the
keys we give back come from a uinput virtual keyboard.

Swallowing a key (the keyd/kmonad method)
-----------------------------------------
A device cannot hand one key to us alone: it is grabbed whole (EVIOCGRAB),
and every event that is not swallowed is written, unchanged, to the virtual
keyboard "gozfaresi-klavye". A key's release and its auto-repeats (value 2)
follow its press: a swallowed press stays swallowed to the end, a press
given back is always released in the virtual keyboard, whatever a callback
returns meanwhile - otherwise the application would see a release without a
press, or a key that never comes up.

A device is grabbed while a hook asks to swallow one of ITS keys - a full
keyboard, a three-key foot switch or switch interface alike - and only when
no key of it is held: grabbing during a press would give its release to us
alone and leave the key stuck down in the application. A device of nothing
but keys (EV_SYN/KEY/MSC/LED/REP, no BTN_*) gives its events back through
the shared virtual keyboard. Any other (a keyboard with a mouse, a switch
with mouse buttons) gets its own clone, "gozfaresi-klon-N", made from its
capabilities (UInput.from_device): every event that is not swallowed goes
back through it in the device's own SYN_REPORT frames, so a mouse movement's
REL_X and REL_Y stay one movement. The clone is made on a helper thread
before the grab (the reader never waits for it; what happens meanwhile goes
to the desktop and is drained, never replayed), destroyed with the grab, and
a device whose clone cannot be made is not tried again (said once). Touch
devices (touchpads, touchscreens,
pens: ABS_MT_* or BTN_TOOL_*/BTN_TOUCH) are never grabbed - a clone of one
loses the multi-touch gestures - so their keys reach the hooks but cannot be
swallowed (said once, and in capabilities()).

Our own uinput devices (names starting "gozfaresi", clones included) are
never read, so nothing we send or give back comes round to our hooks. Every event read here
comes from a device, never from an on-screen keyboard (those type through
the display server): injected=False always.

Safety: a grab must never outlive our giving the keys back
----------------------------------------------------------
A grabbed keyboard whose events are not given back is a dead keyboard, so
every grab is let go - and every key (or button) we hold down in the
virtual keyboard or a clone is released - on close(), at exit (atexit), on SIGTERM/SIGINT/SIGHUP (the
previous handler then runs as before), when giving a key back fails, when
the reader thread fails, and by the watchdog: one round of the reader (its
callbacks included) taking over _WATCHDOG seconds. A watchdog that dies
takes the grabs with it (swallowing off, said). The kernel itself drops the
grabs when the process dies.

After a stall or a failure the reader comes back by itself, the grabs after
_REARM_COOLDOWN seconds (once all keys are up) - at most _REARM_MAX times in
_REARM_WINDOW seconds; past that, swallowing stays off for good (a stall) or
the reader stops (a failure), said once, and capabilities() reports it.
Meanwhile keyboard.alive() is False. A key held through all this stays one
hold: the devices stay open, what was missed is read back from the kernel's
key state (EVIOCGKEY) and the difference reported - a release as "up", a
press as "down" - so a consumer hears exactly one "up" per "down", and an
auto-repeat is never taken for a new press. The same resync follows a
SYN_DROPPED (events lost to a full buffer). A device unplugged with a key
down reports that key "up" (unless another device still holds it).

Gamepads are read, never grabbed: devices with BTN_GAMEPAD or BTN_JOYSTICK
(switch interfaces are often generic joysticks). See _pad_map().

Typing (write(), and the letters of send()) needs the desktop's layout: a
key code types whatever the layout puts on it - on a Turkish Q keyboard the
US "i" key types "ı". The layout is read from the X server (X11, or XWayland
on Wayland, which mirrors the desktop's): keysym -> X keycode (= evdev code
+ 8) and shift level. Without an X keymap write() types nothing at all
(ValueError: the dictation then stays on the clipboard) and send() falls
back to US key positions.

The cursor's range is the union of the screens (X11 or XWayland through
linux_x11; without either, Tk measures the screen once). A desktop change
makes a new pointer device with the new range.
"""
import atexit
import glob
import os
import select
import shutil
import signal
import socket
import struct
import sys
import tempfile
import threading
import time
import weakref

from eye_gaze.i18n import t as _t
from eye_gaze.platform import keys

NAME = "evdev"
UINPUT = "/dev/uinput"
PROC_DEVICES = "/proc/bus/input/devices"
OWN_PREFIX = "gozfaresi"
KEYBOARD_NAME = "gozfaresi-klavye"
POINTER_NAME = "gozfaresi-isaretci"
INSTALLER = "izin-kur.sh"

# Seconds between rescans for plugged-in devices; the reader's wake-up while
# a grab waits for held keys to come up, and its idle wake-up otherwise.
_SCAN_PERIOD = 2.0
_GRAB_RETRY = 0.05
_IDLE = 0.5
# One reader round longer than this: every grab is let go.
_WATCHDOG = 2.0
# After a stall or a failure: grabs come back after the cooldown, at most
# _REARM_MAX times within _REARM_WINDOW seconds.
_REARM_COOLDOWN = 3.0
_REARM_MAX = 5
_REARM_WINDOW = 60.0
# close() waits this long for the reader thread.
_JOIN = 2.0
# The screen list is reused this long (set_cursor asks every frame).
_SCREENS_TTL = 1.0
GUESSED_SCREEN = (0, 0, 1920, 1080)

# Kernel ABI (linux/input-event-codes.h): fixed numbers, so the tests' fake
# evdev module needs no ecodes table.
EV_SYN, EV_KEY, EV_REL, EV_ABS, EV_MSC, EV_LED, EV_REP, EV_FF = (
    0x00, 0x01, 0x02, 0x03, 0x04, 0x11, 0x14, 0x15)
SYN_REPORT, SYN_DROPPED = 0, 3
KEY_A = 30
KEY_LEFTSHIFT = 42
KEY_RIGHTALT = 100            # AltGr (ISO_Level3_Shift) on Turkish layouts
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_JOYSTICK = 0x120          # BTN_TRIGGER: a generic joystick's first button
BTN_GAMEPAD = 0x130           # BTN_SOUTH
ABS_X, ABS_Y, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x10, 0x11
REL_WHEEL = 0x08
_BTN_FIRST, _BTN_END = 0x100, 0x160      # mouse, joystick, pad, digitizer
_BTN_TOOL_FIRST, _BTN_TOOL_END = 0x140, 0x150   # BTN_TOOL_*, BTN_TOUCH...
_ABS_MT_FIRST, _ABS_MT_END = 0x2f, 0x3f         # ABS_MT_SLOT..ABS_MT_TOOL_Y

# The virtual keyboard's codes: every KEY_*, no BTN_* (a mouse or joystick
# button would make the system take it for a mouse or a joystick).
_VKBD_CODES = (list(range(1, 0x100)) + list(range(0x160, 0x220))
               + list(range(0x230, 0x2c0)))

# Pad buttons. BTN_NORTH/BTN_WEST are the kernel's BTN_X/BTN_Y.
_PAD_NAMES = {
    0x130: "A", 0x131: "B", 0x133: "X", 0x134: "Y",
    0x13b: "START", 0x13a: "BACK", 0x136: "LB", 0x137: "RB",
    0x13d: "LS", 0x13e: "RS",
    0x220: "UP", 0x221: "DOWN", 0x222: "LEFT", 0x223: "RIGHT",
}
# Today's config spells the d-pad as XInput does.
_PAD_ALIASES = {"DPAD_UP": "UP", "DPAD_DOWN": "DOWN", "DPAD_LEFT": "LEFT",
                "DPAD_RIGHT": "RIGHT"}
_HAT_NAMES = {ABS_HAT0X: ("LEFT", "RIGHT"), ABS_HAT0Y: ("UP", "DOWN")}
# A HID joystick's buttons 17 and up (the kernel puts buttons 1-16 on
# BTN_TRIGGER..BTN_DEAD and the rest on BTN_TRIGGER_HAPPY1..40).
_BTN_HAPPY_FIRST, _BTN_HAPPY_END = 0x2c0, 0x2e8
_MAX_BTN = 32


def _pad_map(codes):
    """{key code: button name} of one pad or joystick (its EV_KEY codes).

    Xbox-type buttons keep their names (A, B, X, Y...). Every other button
    of the joystick block (BTN_TRIGGER..), the gamepad block and
    BTN_TRIGGER_HAPPY* is BTN1..BTN32 by its place in the device's own code
    list, so a 20-button joystick's buttons 17-20 (BTN_TRIGGER_HAPPY1-4) are
    BTN17-BTN20 and a device with gaps is still numbered 1, 2, 3. Windows
    numbers the same device by winmm's button order (joystick_win), which
    may differ: the settings catch the button again on the other system."""
    names = {}
    n = 0
    for code in sorted(codes):
        name = _PAD_NAMES.get(code)
        if name is None and (BTN_JOYSTICK <= code < 0x140
                             or _BTN_HAPPY_FIRST <= code < _BTN_HAPPY_END):
            n += 1
            if n > _MAX_BTN:
                continue
            name = "BTN%d" % n
        if name is not None:
            names[code] = name
    return names


def _log(msg):
    """Notices go to the console (and gaze.log): ASCII only."""
    from eye_gaze.platform import ascii_text
    try:
        print("[evdev] " + ascii_text(msg), flush=True)
    except Exception:
        pass


# --- permissions ---------------------------------------------------------------

def _access(path, mode):
    """os.access as a seam: tests patch this, never os.access itself."""
    try:
        return os.access(path, mode)
    except Exception:
        return False


def _event_devices():
    return sorted(glob.glob("/dev/input/event*"))


def _read_proc_devices():
    try:
        with open(PROC_DEVICES, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


_WORD_BITS = 8 * struct.calcsize("P")    # the kernel prints longs


def _bitmap(text):
    """A /proc bitmap ("B: KEY=1f 0 ffff...", most significant long first)
    as one integer."""
    value = 0
    for word in text.split():
        value = (value << _WORD_BITS) | int(word, 16)
    return value


def _keyboard_nodes_in(text):
    """Event nodes of the keyboards (EV_KEY with KEY_A, not ours) listed in
    a /proc/bus/input/devices text."""
    nodes = []
    for block in text.split("\n\n"):
        name, events, ev, key = "", [], 0, 0
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line[len("N: Name="):].strip().strip('"')
            elif line.startswith("H: Handlers="):
                events = ["/dev/input/" + h
                          for h in line.split("=", 1)[1].split()
                          if h.startswith("event")]
            elif line.startswith("B: EV="):
                ev = _bitmap(line.split("=", 1)[1])
            elif line.startswith("B: KEY="):
                key = _bitmap(line.split("=", 1)[1])
        if name.startswith(OWN_PREFIX):
            continue
        if (ev >> EV_KEY) & 1 and (key >> KEY_A) & 1:
            nodes.extend(events)
    return nodes


def _keyboard_nodes():
    """Keyboard event nodes, or None when the kernel's list cannot be read."""
    text = _read_proc_devices()
    if text is None:
        return None
    return _keyboard_nodes_in(text)


def _keyboards_readable():
    """(any keyboard found, every keyboard readable).

    EVERY keyboard: Steam's udev rules give the seat user /dev/uinput and
    the gamepads but not the keyboards, and evdev chosen there would hook
    none of the driver's keys. Other devices (the power button, gamepads) do
    not count. Without the kernel's list, every event node does."""
    nodes = _keyboard_nodes()
    if nodes is None:
        nodes = _event_devices()
    return (bool(nodes),
            bool(nodes) and all(_access(p, os.R_OK) for p in nodes))


def permitted():
    """Every keyboard readable AND /dev/uinput writable."""
    found, readable = _keyboards_readable()
    return found and readable and _access(UINPUT, os.W_OK)


def _user_name():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        import getpass
        return getpass.getuser()


def _group_state():
    """"active" (this process has the input group), "pending" (the user is
    in it, but this session began before), "none", or None (unknown)."""
    try:
        import grp
        import pwd
        g = grp.getgrnam("input")
        if g.gr_gid in os.getgroups():
            return "active"
        user = _user_name()
        if user in g.gr_mem or pwd.getpwnam(user).pw_gid == g.gr_gid:
            return "pending"
        return "none"
    except Exception:
        return None


def permission_ok():
    """(ok, reason) for the evdev backend; the reason is for the windows
    (language catalog: platform.evdev.*)."""
    found, readable = _keyboards_readable()
    uinput_there = _access(UINPUT, os.F_OK)
    uinput_ok = uinput_there and _access(UINPUT, os.W_OK)
    if found and readable and uinput_ok:
        return True, _t("platform.evdev.ok")
    install, relogin = _t("platform.evdev.install"), _t("platform.evdev.relogin")
    problems = []
    if found and not readable:
        if _group_state() == "pending":
            problems.append(_t("platform.evdev.group_pending", relogin=relogin))
        else:
            problems.append(_t("platform.evdev.no_group", install=install,
                              relogin=relogin))
    if not uinput_there:
        problems.append(_t("platform.evdev.no_uinput", install=install))
    elif not uinput_ok and not (found and not readable):
        problems.append(_t("platform.evdev.uinput_denied", install=install))
    if not found:
        problems.append(_t("platform.evdev.no_keyboard"))
    return False, "; ".join(problems)


def _gaze_dir():
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def _installer_path():
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        src = os.path.join(base, "linux", INSTALLER)
        # An AppImage's files sit on the user's FUSE mount, which root may
        # not read: pkexec gets a copy in a private temp folder.
        folder = tempfile.mkdtemp(prefix="gozfaresi-izin-")
        dst = os.path.join(folder, INSTALLER)
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o755)
        return dst
    return os.path.join(_gaze_dir(), "linux", INSTALLER)


def install_permissions_cmd():
    """argv that installs the permissions (udev rule, input group, uinput)
    for this user, asking for an administrator password:
    ["pkexec", script, user]. Built here, never run."""
    return ["pkexec", _installer_path(), _user_name()]


# --- the backend ------------------------------------------------------------

def _x_screens_source():
    """An X11Backend for the monitor list (X11 or XWayland), or None."""
    if not os.environ.get("DISPLAY"):
        return None
    try:
        from eye_gaze.platform import linux_x11
        return linux_x11.X11Backend()
    except Exception:
        return None


class _XKeymap:
    """The desktop's keyboard layout as the X server has it (X11, or
    XWayland mirroring the Wayland desktop's): keysym -> [(X keycode,
    level)], level 0 plain, 1 Shift, 4 AltGr, 5 AltGr+Shift (the core map's
    columns). An own connection: Xlib must not be shared between threads."""

    def __init__(self, display_name):
        from eye_gaze.platform import linux_x11 as lx
        lx._load_xlib()
        self._lx = lx
        self._d = lx._xdisplay.Display(display_name)
        self._lock = threading.Lock()

    def keycodes(self, keysym):
        with self._lock:
            d = self._d
            # A layout change reaches every client as MappingNotify.
            while d.pending_events():
                ev = d.next_event()
                if ev.type == self._lx._X.MappingNotify:
                    d.refresh_keyboard_mapping(ev)
            return list(d.keysym_to_keycodes(keysym))

    def close(self):
        with self._lock:
            try:
                self._d.close()
            except Exception:
                pass


def _x_keymap_source():
    """An _XKeymap, or None without a reachable X server."""
    name = os.environ.get("DISPLAY")
    if not name:
        return None
    try:
        return _XKeymap(name)
    except Exception:
        return None


def _fallback_screen_size():
    from eye_gaze import platform as pf
    return pf._tk_screen_size()


def _import_evdev():
    import evdev
    return evdev


def _bbox(screens):
    x0 = min(s[0] for s in screens)
    y0 = min(s[1] for s in screens)
    x1 = max(s[0] + s[2] for s in screens)
    y1 = max(s[1] + s[3] for s in screens)
    return (x0, y0, x1 - x0, y1 - y0)


_QUIET = {}


def _uinput_class(ev):
    """ev.UInput without its search for the new node: python-evdev opens
    that node (UInput.device) and waits up to ~2 s for udev to set its
    permissions - a reader of our own device we never use, and a wait that,
    taken on the reader thread, would freeze every grabbed keyboard."""
    base = ev.UInput
    cls = _QUIET.get(base)
    if cls is None:
        class QuietUInput(base):
            def _find_device(self, *args, **kwargs):
                return None
        cls = _QUIET[base] = QuietUInput
    return cls


def _close_own_reader(ui):
    """Should a UInput still have opened its own node (UInput.device), that
    reader goes: we never read our own devices."""
    dev = getattr(ui, "device", None)
    if dev is not None:
        try:
            dev.close()
        except Exception:
            pass


_said_no_signals = [False]


class EvdevBackend:
    name = NAME

    @classmethod
    def available(cls):
        return permitted()

    def __init__(self, evdev_module=None):
        # Must not call platform.linux_backend()/keyboard_api(): those hold
        # the (non-reentrant) backend lock while this runs.
        ev = evdev_module if evdev_module is not None else _import_evdev()
        self._ev = ev
        self.start_errors = []
        self.screen_guessed = False
        self._lock = threading.Lock()        # screens
        self._ptr_lock = threading.Lock()    # pointer device
        self._screens = None
        self._primary = None
        self._screens_at = 0.0
        self._measured = None
        self._closed = False
        self._pointer = None
        self._pointer_box = None
        self._cursor = None
        self._prev_handlers = {}
        self._x = _x_screens_source()
        self._keymap = _x_keymap_source()
        self.keyboard = None
        try:
            self._pointer_box = self._desktop()
            self._pointer = self._make_pointer(self._pointer_box)
            self.keyboard = EvdevKeyboard(ev, keymap=self._keymap)
            self._pad = EvdevPad(self.keyboard)
            self.keyboard.start()
        except Exception:
            # The choice falls through to the next backend: nothing of ours
            # may stay behind (a grab, a virtual device).
            if self.keyboard is not None:
                self.keyboard.close()
            self._close_pointer()
            self._close_x()
            raise
        self._guard_exits()

    # --- screens --------------------------------------------------------------

    def screens(self):
        with self._lock:
            self._refresh_screens()
            return list(self._screens)

    def primary(self):
        with self._lock:
            self._refresh_screens()
            return self._primary

    def primary_mm(self):
        """The primary monitor's size in mm from the X server (X11 or
        XWayland), or None - pure Wayland tells this program nothing."""
        x = self._x
        if x is None:
            return None
        try:
            fn = getattr(x, "primary_mm", None)
            return fn() if fn is not None else None
        except Exception:
            return None

    def _refresh_screens(self):
        now = time.monotonic()
        if self._screens is not None and now - self._screens_at < _SCREENS_TTL:
            return
        found = primary = None
        if self._x is not None:
            try:
                found = [tuple(int(v) for v in s) for s in self._x.screens()]
                primary = tuple(int(v) for v in self._x.primary())
            except Exception:
                found = None
        if not found:
            if self._measured is None:
                size = _fallback_screen_size()
                if size and size[0] > 0 and size[1] > 0:
                    self._measured = [(0, 0, int(size[0]), int(size[1]))]
                else:
                    self.screen_guessed = True
                    self._measured = [GUESSED_SCREEN]
            found = self._measured
        self._screens = found
        self._primary = primary if primary in found else found[0]
        self._screens_at = now

    def _desktop(self):
        return _bbox(self.screens())

    # --- pointer --------------------------------------------------------------

    def _make_pointer(self, box):
        """An absolute pointer whose range is the desktop: value v on an
        axis of 0..w-1 is desktop pixel x0 + v."""
        ev = self._ev
        w, h = max(1, box[2]), max(1, box[3])
        dev = _uinput_class(ev)(
            events={
                EV_KEY: [BTN_LEFT, BTN_RIGHT, BTN_MIDDLE],
                EV_ABS: [(ABS_X, ev.AbsInfo(0, 0, w - 1, 0, 0, 0)),
                         (ABS_Y, ev.AbsInfo(0, 0, h - 1, 0, 0, 0))],
                EV_REL: [REL_WHEEL],
            },
            name=POINTER_NAME)
        _close_own_reader(dev)
        return dev

    def _close_pointer(self):
        p, self._pointer = self._pointer, None
        if p is not None:
            try:
                p.close()
            except Exception:
                pass

    def _close_x(self):
        for attr in ("_x", "_keymap"):
            x = getattr(self, attr, None)
            setattr(self, attr, None)
            if x is not None:
                try:
                    x.close()
                except Exception:
                    pass

    # --- windows (through X11 / XWayland when there is a display) -------------

    def foreground_pid(self):
        """pid of the focused window's program, or None - always None on a
        Wayland desktop without XWayland: no protocol says who has focus."""
        x = self._x
        if x is None:
            return None
        try:
            return x.foreground_pid()
        except Exception:
            return None

    def raise_window(self, title):
        """X11Backend.raise_window through the display; False without one."""
        x = self._x
        if x is None:
            return False
        try:
            return bool(x.raise_window(title))
        except Exception:
            return False

    def get_cursor(self):
        """Where we last put the cursor (Wayland tells nobody where it is);
        the primary screen's centre before that."""
        if self._cursor is None:
            x, y, w, h = self.primary()
            return (x + w // 2, y + h // 2)
        return self._cursor

    def set_cursor(self, x, y):
        box = self._desktop()
        with self._ptr_lock:
            if self._closed:
                return
            if box != self._pointer_box:
                # Monitors changed: a device's range is fixed at creation.
                try:
                    new = self._make_pointer(box)
                except Exception as exc:
                    new = None
                    _log("UYARI: ekran degisti ama yeni isaretci kurulamadi "
                         "(%s); eski aralik kullaniliyor." % exc)
                if new is not None:
                    self._close_pointer()
                    self._pointer, self._pointer_box = new, box
            x0, y0, w, h = self._pointer_box
            x = min(max(int(x), x0), x0 + w - 1)
            y = min(max(int(y), y0), y0 + h - 1)
            p = self._pointer
            p.write(EV_ABS, ABS_X, x - x0)
            p.write(EV_ABS, ABS_Y, y - y0)
            p.syn()
            self._cursor = (x, y)

    def button(self, btn, down):
        code = {"left": BTN_LEFT, "right": BTN_RIGHT,
                "middle": BTN_MIDDLE}.get(btn)
        if code is None:
            raise ValueError("unknown mouse button: %r" % (btn,))
        with self._ptr_lock:
            if self._closed:
                return
            self._pointer.write(EV_KEY, code, 1 if down else 0)
            self._pointer.syn()

    def scroll(self, amount):
        """amount in Windows wheel units like actions.scroll(): 120 = one
        notch, > 0 up."""
        amount = int(amount)
        if not amount:
            return
        notches = max(1, int(round(abs(amount) / 120.0)))
        with self._ptr_lock:
            if self._closed:
                return
            self._pointer.write(EV_REL, REL_WHEEL,
                                notches if amount > 0 else -notches)
            self._pointer.syn()

    # --- the rest ---------------------------------------------------------------

    def gamepad(self):
        return self._pad

    def capabilities(self):
        """Overrides of the generic evdev entries in eye_gaze.platform."""
        kb = self.keyboard
        caps = {}
        if kb.watchdog_dead and not (kb.gave_up or kb.closed):
            # Keys are still heard; swallowing without a watchdog is not
            # safe, so it is off (and alive() is False: not fully working).
            caps["suppress_keys"] = (
                False, _t("platform.evdev.watchdog_dead", error=kb.error))
        elif not kb.alive():
            if kb.gave_up or kb.closed:
                dead = _t("platform.keys_dead",
                         error=kb.error or _t("platform.evdev.closed"))
            else:
                dead = _t("platform.keys_restarting", error=kb.error)
            for k in ("global_keys", "suppress_keys", "gamepad"):
                caps[k] = (False, dead)
        else:
            if not kb.keyboards_found():
                caps["global_keys"] = (
                    False, _t("platform.evdev.no_readable_keyboard"))
            if kb.grab_gave_up:
                caps["suppress_keys"] = (
                    False, _t("platform.evdev.grab_gave_up", error=kb.error))
            else:
                loose = kb.unswallowed()
                if loose:
                    caps["suppress_keys"] = (
                        True, _t("platform.evdev.loose",
                                names=", ".join(loose)))
        if self.screen_guessed:
            caps["cursor"] = (True, _t("platform.evdev.pointer_guessed"))
        return caps

    def _guard_exits(self):
        """Let go of the keyboard on the way out, whichever way."""
        atexit.register(_close_at_exit, weakref.ref(self))
        if threading.current_thread() is not threading.main_thread():
            # signal.signal works on the main thread only. atexit, close()
            # and the kernel (at process death) still let go.
            if not _said_no_signals[0]:
                _said_no_signals[0] = True
                _log("UYARI: sinyal isleyicileri kurulamadi (ana is parcacigi "
                     "degil); klavyeyi cikista atexit ve cekirdek birakir.")
            return
        for name in ("SIGTERM", "SIGINT", "SIGHUP"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                prev = signal.getsignal(sig)
                if prev is signal.SIG_IGN or prev is None:
                    continue    # ignored, or a C handler we cannot chain
                signal.signal(sig, self._on_signal)
                self._prev_handlers[sig] = prev
            except Exception:
                continue

    def _on_signal(self, signum, frame):
        self.keyboard.let_go()
        prev = self._prev_handlers.get(signum, signal.SIG_DFL)
        if callable(prev):
            return prev(signum, frame)
        # The default action (terminate), now that the keyboard is free.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    def _restore_signals(self):
        if threading.current_thread() is not threading.main_thread():
            return
        for sig, prev in list(self._prev_handlers.items()):
            try:
                if signal.getsignal(sig) == self._on_signal:
                    signal.signal(sig, prev)
            except Exception:
                pass
        self._prev_handlers = {}

    def close(self):
        with self._ptr_lock:
            if self._closed:
                return
            self._closed = True
        self._restore_signals()
        self.keyboard.close()
        with self._ptr_lock:
            self._close_pointer()
        self._close_x()


def _close_at_exit(ref):
    backend = ref()
    if backend is not None:
        try:
            backend.close()
        except Exception:
            pass


# --- the keyboard (and the reader of every device) -----------------------------------

def _classify(caps):
    """(key codes, reads keys, plain, touch, pad) for a capabilities dict.

    plain = nothing but keys (given back through the shared virtual
    keyboard); touch = never grabbed (see the module docstring)."""
    codes = frozenset(caps.get(EV_KEY, ()))
    reads_keys = any(0 < c < _BTN_FIRST for c in codes)
    plain = (set(caps) <= {EV_SYN, EV_KEY, EV_MSC, EV_LED, EV_REP}
             and not any(_BTN_FIRST <= c < _BTN_END for c in codes))
    abs_codes = set(c[0] if isinstance(c, (tuple, list)) else c
                    for c in caps.get(EV_ABS, ()))
    touch = (any(_BTN_TOOL_FIRST <= c < _BTN_TOOL_END for c in codes)
             or any(_ABS_MT_FIRST <= c < _ABS_MT_END for c in abs_codes))
    pad = BTN_GAMEPAD in codes or BTN_JOYSTICK in codes
    return codes, reads_keys, plain, touch, pad


class _Device:
    __slots__ = ("path", "dev", "name", "codes", "reads_keys", "plain",
                 "touch", "pad", "grabbed", "clone", "frame", "down", "pre",
                 "forwarded", "dropping", "pad_down", "pad_map",
                 "grab_failed_at", "clone_building", "unclonable")

    def __init__(self, path, dev, name, caps):
        self.path = path
        self.dev = dev
        self.name = name
        (self.codes, self.reads_keys, self.plain, self.touch,
         self.pad) = _classify(caps)
        self.grabbed = False      # grabbed, and its events are given back
        self.clone = None         # its own uinput copy (not plain devices)
        self.frame = []           # clone events waiting for SYN_REPORT
        self.down = set()         # pressed while we watched (press reported)
        self.pre = set()          # held before we watched (no press reported)
        self.forwarded = set()    # held down by us (virtual keyboard / clone)
        self.dropping = False     # after SYN_DROPPED until SYN_REPORT
        self.pad_down = set()     # pad button names held
        self.pad_map = _pad_map(self.codes) if self.pad else {}
        self.grab_failed_at = None
        self.clone_building = False   # a helper thread is making the clone
        self.unclonable = False       # making it failed: never grabbed


class _Hook:
    __slots__ = ("name", "code", "callback", "suppress")

    def __init__(self, name, code, callback, suppress):
        self.name = name
        self.code = code
        self.callback = callback
        self.suppress = suppress


# Keysyms the X keymaps use for Turkish letters (the legacy Latin-2/3 ones,
# not the 0x01000000 + Unicode form).
_LEGACY_KEYSYMS = {"ı": 0x2b9, "İ": 0x2a9, "ş": 0x1ba, "Ş": 0x1aa,
                   "ğ": 0x2bb, "Ğ": 0x2ab}
# Core keymap column -> modifier keys that select it.
_LEVEL_MODS = {0: (), 1: (KEY_LEFTSHIFT,), 4: (KEY_RIGHTALT,),
               5: (KEY_RIGHTALT, KEY_LEFTSHIFT)}
_VKBD_SET = frozenset(_VKBD_CODES)


def _keysyms_of(ch):
    if ch == "\n":
        return [0xff0d]                     # Return
    if ch == "\t":
        return [0xff09]                     # Tab
    o = ord(ch)
    out = [_LEGACY_KEYSYMS[ch]] if ch in _LEGACY_KEYSYMS else []
    if 0x20 <= o <= 0x7e or 0xa0 <= o <= 0xff:
        out.append(o)                       # Latin-1 keysyms are the code
    else:
        out.append(0x01000000 | o)
    return out


class EvdevKeyboard:
    """The `keyboard` library subset (see eye_gaze.platform) on evdev, and
    the reader of every input device (the pads' state is kept here too).

    Callbacks run on the reader thread and must return at once (count, set a
    flag): a round over _WATCHDOG seconds lets go of every grab. Several
    hooks on one key behave like the library's: the event passes only if no
    suppressing hook returned a false value."""

    def __init__(self, ev, keymap=None):
        from eye_gaze.platform import KeyEvent
        self._KeyEvent = KeyEvent
        self._ev = ev
        self._keymap = keymap               # _XKeymap or None
        self._lock = threading.Lock()       # hooks; device add/remove
        self._out_lock = threading.Lock()   # uinput writes, grabbed flags
        self._stall_lock = threading.Lock()
        self._hooks = {}
        self._by_code = {}
        self._next_id = 1
        self._want_grab = False
        self._suppress_codes = frozenset()
        self._devs = {}                     # path -> _Device
        self._skip = set()                  # (path, inode, rdev): not ours
        self._said = set()
        self._clones = 0
        self._thread = None
        self._stop = False
        self._stop_ev = threading.Event()
        self.closed = False
        self.error = None
        self._dead = False                  # reader failed, re-arming
        self.gave_up = False                # reader stopped for good
        self._stalled = False               # watchdog fired, round not over
        self.watchdog_dead = False          # no watchdog: no grabs
        self._busy_since = None
        self._grab_ok_at = 0.0
        self.grab_gave_up = False           # swallowing off for good
        self._lift_pending = False          # _release_all could not lift
        self._rearms = []
        self._next_scan = 0.0
        self._wake_r, self._wake_w = socket.socketpair()
        self._wake_r.setblocking(False)
        try:
            self._vk = _uinput_class(ev)(events={EV_KEY: list(_VKBD_CODES)},
                                         name=KEYBOARD_NAME)
        except Exception:
            self._close_wake()
            raise
        _close_own_reader(self._vk)

    # --- public API -------------------------------------------------------------

    def hook_key(self, name, callback, suppress=False):
        n = keys.normalize(name)
        if n is None:
            raise ValueError("unknown key name: %r" % (name,))
        code = keys.to_evdev(n)
        with self._lock:
            if self.closed or self.gave_up:
                raise RuntimeError("evdev key listener stopped: %s"
                                   % (self.error or "closed"))
            hid = self._next_id
            self._next_id += 1
            self._hooks[hid] = _Hook(n, code, callback, bool(suppress))
            self._by_code.setdefault(code, []).append(hid)
            self._update_want()
        self._wake()

        def remove():
            self._unhook(hid)
        return remove

    def _unhook(self, hid):
        with self._lock:
            h = self._hooks.pop(hid, None)
            if h is None:
                return
            ids = self._by_code.get(h.code, [])
            if hid in ids:
                ids.remove(hid)
            if not ids:
                self._by_code.pop(h.code, None)
            self._update_want()
        self._wake()

    def unhook_all(self):
        with self._lock:
            self._hooks.clear()
            self._by_code.clear()
            self._update_want()
        self._wake()

    def _update_want(self):
        # A grab is only for swallowing: a device is grabbed while a hook
        # wants to swallow one of its keys, else read side by side with the
        # desktop.
        self._suppress_codes = frozenset(h.code for h in self._hooks.values()
                                         if h.suppress)
        self._want_grab = bool(self._suppress_codes)

    def _layout_codes(self, ch):
        """[evdev codes] that type `ch` in the desktop's layout (modifiers
        first), or None."""
        best = None
        for ks in _keysyms_of(ch):
            try:
                found = self._keymap.keycodes(ks)
            except Exception:
                found = []
            for xcode, level in found:
                code = int(xcode) - 8
                if level not in _LEVEL_MODS or code not in _VKBD_SET:
                    continue
                rank = (sorted(_LEVEL_MODS).index(level), code)
                if best is None or rank < best[0]:
                    best = (rank, list(_LEVEL_MODS[level]) + [code])
            if best is not None:
                return best[1]
        return None

    def send(self, combo):
        """"ctrl+v", "esc", "ctrl+c, ctrl+v" - the library's syntax. A
        letter is the key that types it in the desktop's layout (Ctrl+V is
        V on any layout); everything else - and every letter without an X
        keymap - is its US key position."""
        strokes = []
        for part in str(combo).split(","):
            names = [s.strip() for s in part.split("+") if s.strip()]
            codes = []
            for p in names:
                n = keys.normalize(p)
                if n is None:
                    raise ValueError("unknown key in %r" % (combo,))
                layout = None
                if self._keymap is not None and len(n) == 1 and n.isalpha():
                    layout = self._layout_codes(n)
                for c in layout or [keys.to_evdev(n)]:
                    if c not in codes:
                        codes.append(c)
            strokes.extend((c, 1) for c in codes)
            strokes.extend((c, 0) for c in reversed(codes))
        self._type(strokes)

    def write(self, text):
        """Types `text` through the desktop's layout (read from the X
        keymap: Turkish letters, punctuation, AltGr characters). A character
        the layout has no key for - or any character at all without an X
        keymap - is a ValueError, and then nothing is typed: a guessed key
        types the wrong letter (on a Turkish Q layout the US "i" key types
        "ı"), which is worse than the clipboard the dictation keeps."""
        text = str(text or "")
        if not text:
            return
        if self._keymap is None:
            raise ValueError("cannot type without the desktop's keyboard "
                             "layout (no X keymap)")
        strokes = []
        for ch in text:
            codes = self._layout_codes(ch)
            if codes is None:
                raise ValueError("the keyboard layout has no key for %r"
                                 % (ch,))
            mods, key = codes[:-1], codes[-1]
            strokes.extend((m, 1) for m in mods)
            strokes.extend([(key, 1), (key, 0)])
            strokes.extend((m, 0) for m in reversed(mods))
        self._type(strokes)

    def _type(self, strokes):
        """Our keys go out of the virtual keyboard, which nobody here reads:
        they never reach our own hooks."""
        if not strokes:
            return
        with self._out_lock:
            try:
                if self.closed:
                    raise RuntimeError("keyboard closed")
                for code, value in strokes:
                    self._vk.write(EV_KEY, code, value)
                    self._vk.syn()
            finally:
                # A long text held the lock while _release_all needed it.
                self._lift_if_pending()

    def is_pressed(self, name):
        """The kernel's key state of every key device (EVIOCGKEY): true for
        a swallowed key as well, and it needs no working reader."""
        n = keys.normalize(name)
        if n is None:
            raise ValueError("unknown key name: %r" % (name,))
        code = keys.to_evdev(n)
        with self._lock:
            if self.closed:
                return False
            for d in self._devs.values():
                if not d.reads_keys:
                    continue
                try:
                    if code in d.dev.active_keys():
                        return True
                except Exception:
                    if code in d.down or code in d.pre:
                        return True
        return False

    def alive(self):
        """False while the reader is stalled or re-arming (self.error says
        why), without its watchdog, and for good once it stopped (gave_up)
        or was closed."""
        if self.closed or self.watchdog_dead:
            return False
        t = self._thread
        return t is None or (t.is_alive() and not self._dead
                             and not self._stalled)

    def keyboards_found(self):
        return any(d.reads_keys for d in list(self._devs.values()))

    def unswallowed(self):
        """Names of the devices whose hooked keys cannot be swallowed
        (touch devices, no clone, or grabbed by another program)."""
        codes = self._suppress_codes
        if not codes:
            return []
        return sorted({d.name for d in list(self._devs.values())
                       if d.codes & codes
                       and (d.touch or d.unclonable
                            or d.grab_failed_at is not None)})

    def pad_names(self):
        """Pad buttons held (sorted), or None without a pad or a reader.
        Pads are never grabbed: they go on without the watchdog."""
        t = self._thread
        if self.closed or (t is not None and (not t.is_alive() or self._dead
                                              or self._stalled)):
            return None
        pads = [d for d in list(self._devs.values()) if d.pad]
        if not pads:
            return None
        names = set()
        for d in pads:
            names |= d.pad_down
        return sorted(names)

    def let_go(self):
        """Let go of every grab now, from any thread (a signal handler);
        if the program goes on, grabbing resumes after the cooldown."""
        self._grab_ok_at = time.monotonic() + _REARM_COOLDOWN
        self._release_all()

    def close(self):
        with self._lock:
            if self.closed:
                return
            self.closed = True
            self._stop = True
        self._stop_ev.set()
        self._wake()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(_JOIN)
        self._release_all()
        if t is None or not t.is_alive():
            # Only once the reader is gone: closing a descriptor it may still
            # select() on would let a reused number land in another file.
            with self._lock:
                devs, self._devs = list(self._devs.values()), {}
            for d in devs:
                self._close_dev(d)
            self._close_wake()
        with self._out_lock:
            try:
                self._vk.close()
            except Exception:
                pass

    def _close_wake(self):
        for s in (self._wake_r, self._wake_w):
            try:
                s.close()
            except Exception:
                pass

    # --- the reader -----------------------------------------------------------------

    def start(self):
        """The first scan here (capabilities() right after the start knows
        the keyboards), then the reader and its watchdog."""
        self._scan()
        self._next_scan = time.monotonic() + _SCAN_PERIOD
        self._thread = threading.Thread(target=self._run, name="evdev-keys",
                                        daemon=True)
        self._thread.start()
        threading.Thread(target=self._watchdog, name="evdev-watchdog",
                         daemon=True).start()

    def _wake(self):
        try:
            self._wake_w.send(b"x")
        except Exception:
            pass

    def _say(self, key, msg):
        if key not in self._said:
            self._said.add(key)
            _log(msg)

    def _current(self, d):
        return self._devs.get(d.path) is d

    def _run(self):
        try:
            while not self._stop:
                try:
                    self._loop()
                except Exception as exc:
                    if self._stop:
                        break
                    if not self._recover("%s: %s" % (type(exc).__name__,
                                                     exc)):
                        break
        except BaseException as exc:
            # The recovery itself failed: said, and for good.
            self.error = "%s: %s" % (type(exc).__name__, exc)
            _log("HATA: tus dinleyicisi yeniden kurulamadi (%s); surucu "
                 "tuslari calismiyor, programi yeniden baslatin." % self.error)
        finally:
            if not self._stop:
                self.gave_up = True     # ended without close(): for good
            self._shutdown()

    def _loop(self):
        ready = []
        while not self._stop:
            self._busy_since = time.monotonic()
            try:
                for d in ready:
                    if self._current(d):
                        self._service(d)
                if time.monotonic() >= self._next_scan:
                    self._scan()
                    self._next_scan = time.monotonic() + _SCAN_PERIOD
                pending = self._apply_grabs()
            finally:
                self._end_round()
            if self._stop:
                return
            if pending:
                timeout = _GRAB_RETRY
            else:
                timeout = min(_IDLE, max(0.0,
                                         self._next_scan - time.monotonic()))
            devs = list(self._devs.values())
            r, _, _ = select.select([self._wake_r] + [d.dev for d in devs],
                                    [], [], timeout)
            if self._wake_r in r:
                try:
                    while self._wake_r.recv(4096):
                        pass
                except OSError:
                    pass
            ready = [d for d in devs if d.dev in r]

    def _end_round(self):
        with self._stall_lock:
            self._busy_since = None
            stalled = self._stalled
        if not stalled:
            return
        now = time.monotonic()
        self._stalled = False
        if self._spend_rearm(now):
            self._grab_ok_at = now + _REARM_COOLDOWN
            _log("tus dinleyicisi yeniden calisiyor; tus yutma %.0f sn sonra "
                 "geri gelir." % _REARM_COOLDOWN)
        elif not self.grab_gave_up:
            self.grab_gave_up = True
            _log("HATA: tus dinleyicisi %d kez takildi (%s); klavye yakalama "
                 "birakildi. Surucu tuslari duyuluyor ama uygulamaya da "
                 "gidiyor; programi yeniden baslatin." % (_REARM_MAX,
                                                          self.error))

    def _spend_rearm(self, now):
        """Count one re-arm; False past the budget."""
        self._rearms = [t for t in self._rearms if now - t < _REARM_WINDOW]
        self._rearms.append(now)
        return len(self._rearms) <= _REARM_MAX

    def _watchdog(self):
        try:
            while not self._stop_ev.wait(0.05):
                fire = False
                with self._stall_lock:
                    since = self._busy_since
                    if (since is not None and not self._stalled
                            and time.monotonic() - since > _WATCHDOG):
                        self._stalled = True
                        fire = True
                if fire:
                    self.error = ("a key callback took over %.1f s"
                                  % _WATCHDOG)
                    self._release_all()
                    _log("UYARI: bir tus islemi klavyeyi %.0f sn'den uzun "
                         "dondurdu; klavye kilitli kalmasin diye yakalama "
                         "birakildi." % _WATCHDOG)
        except BaseException as exc:
            # Nobody would let go of a stuck grab any more: no more grabs.
            self.error = "watchdog: %s: %s" % (type(exc).__name__, exc)
            self.watchdog_dead = True
            self.grab_gave_up = True
            self._release_all()
            _log("HATA: tus bekcisi durdu (%s); klavye kilitlenmesin diye "
                 "yakalama kapatildi. Surucu tuslari uygulamaya da gidiyor; "
                 "programi yeniden baslatin." % self.error)

    def _recover(self, reason):
        """The reader failed: let go, wait, read the key state back and go
        on. False when stopping or past the re-arm budget."""
        self.error = reason
        self._dead = True
        self._release_all()
        if not self._spend_rearm(time.monotonic()):
            self.gave_up = True
            _log("HATA: tus dinleyicisi %d kez durdu (%s); birakildi. Surucu "
                 "tuslari calismiyor, programi yeniden baslatin."
                 % (_REARM_MAX, reason))
            return False
        _log("HATA: tus dinleyicisi durdu (%s); klavye birakildi, %.0f sn "
             "sonra yeniden kurulacak." % (reason, _REARM_COOLDOWN))
        if self._stop_ev.wait(_REARM_COOLDOWN):
            return False
        for d in list(self._devs.values()):
            # Nothing was grabbed meanwhile: the downtime's events went to
            # the desktop. Dropped here; the kernel's state says the rest.
            try:
                self._discard_queued(d)
                self._resync(d)
            except Exception as exc:
                self._drop(d, exc)
        self._dead = False
        self._grab_ok_at = time.monotonic()
        _log("tus dinleyicisi yeniden kuruldu.")
        return True

    def _shutdown(self):
        """The reader is gone for good: free the keyboard, and report every
        key still down as up - nobody will report its release."""
        self._release_all()
        held = set()
        for d in list(self._devs.values()):
            held |= d.down
            d.down.clear()
            d.pad_down.clear()
        for code in sorted(held):
            self._dispatch(code, "up")

    # --- devices --------------------------------------------------------------------

    @staticmethod
    def _node_key(path):
        # The inode too: a node number freed by an unplug is soon another
        # device's.
        try:
            st = os.stat(path)
            return (path, st.st_ino, st.st_rdev)
        except OSError:
            return (path, None, None)

    def _scan(self):
        paths = set(_event_devices())
        for path in sorted(paths):
            if path in self._devs:
                continue
            key = self._node_key(path)
            if key in self._skip:
                continue
            try:
                dev = self._ev.InputDevice(path)
            except Exception:
                # Unreadable or gone again: udev sets a new node's
                # permissions a moment after the plug - tried next scan.
                continue
            try:
                name = str(dev.name or "")
                if name.startswith(OWN_PREFIX):
                    self._skip.add(key)
                    dev.close()
                    continue
                d = _Device(path, dev, name, dev.capabilities(absinfo=False))
                if not (d.reads_keys or d.pad):
                    self._skip.add(key)
                    dev.close()
                    continue
                active = set(dev.active_keys())
            except Exception:
                try:
                    dev.close()
                except Exception:
                    pass
                continue
            d.pre = active
            if d.pad:
                d.pad_down = {n for n in map(d.pad_map.get, active) if n}
            with self._lock:
                self._devs[path] = d
            _log("aygit eklendi: %s (%s)" % (name, path))
        for path, d in list(self._devs.items()):
            if path not in paths:
                self._drop(d, "removed")
        self._skip = {k for k in self._skip if k[0] in paths}

    def _close_dev(self, d):
        self._drop_clone(d)
        try:
            d.dev.close()
        except Exception:
            pass

    def _drop(self, d, reason):
        """Device gone (unplugged, read error): its keys are up now."""
        with self._lock:
            if self._devs.get(d.path) is d:
                del self._devs[d.path]
        with self._out_lock:
            d.grabbed = False
            self._lift(d)
        held = sorted(d.down)
        d.down.clear()
        d.pre.clear()
        d.pad_down.clear()
        for code in held:
            if not self._held_elsewhere(code, d):
                self._dispatch(code, "up")
        self._close_dev(d)
        _log("aygit ayrildi: %s (%s): %s" % (d.name, d.path, reason))

    def _service(self, d):
        try:
            events = list(d.dev.read())
        except BlockingIOError:
            return
        except OSError as exc:
            self._drop(d, exc)
            return
        for e in events:
            self._on_event(d, e)

    @staticmethod
    def _discard_queued(d):
        d.frame = []
        while True:
            try:
                if not list(d.dev.read()):
                    return
            except BlockingIOError:
                return

    def _on_event(self, d, e):
        t, code, value = e.type, e.code, e.value
        if d.dropping:
            if t == EV_SYN and code == SYN_REPORT:
                d.dropping = False
                try:
                    self._resync(d)
                except OSError as exc:
                    self._drop(d, exc)
            return
        if t == EV_SYN:
            if code == SYN_DROPPED:
                d.dropping = True
                with self._out_lock:
                    d.frame = []        # half a frame: never given back
            elif code == SYN_REPORT:
                self._flush(d)
            return
        if t == EV_KEY:
            if d.pad:
                name = d.pad_map.get(code)
                if name is not None:
                    if value == 1:
                        d.pad_down.add(name)
                    elif value == 0:
                        d.pad_down.discard(name)
            if d.reads_keys:
                self._on_key(d, code, value)
            return
        if t == EV_ABS and d.pad and code in _HAT_NAMES:
            lo, hi = _HAT_NAMES[code]
            d.pad_down.discard(lo)
            d.pad_down.discard(hi)
            if value < 0:
                d.pad_down.add(lo)
            elif value > 0:
                d.pad_down.add(hi)
        if not d.plain:
            self._queue(d, t, code, value)

    def _held_elsewhere(self, code, d):
        return any(code in o.down for o in list(self._devs.values())
                   if o is not d)

    def _on_key(self, d, code, value):
        if value == 1 and code not in d.down:
            d.pre.discard(code)
            d.down.add(code)
            if self._dispatch(code, "down"):
                self._forward(d, code, 1)
            return
        if value in (1, 2):
            if code not in d.down:
                return          # held before we watched: never a new press
            self._dispatch(code, "down")        # a repeat: callbacks know
            self._forward(d, code, 2)
            return
        if value == 0:
            d.pre.discard(code)
            if code in d.down:
                d.down.discard(code)
                if not self._held_elsewhere(code, d):
                    self._dispatch(code, "up")
            self._forward(d, code, 0)

    def _resync(self, d):
        """Report what changed while we did not read (SYN_DROPPED, a
        failure): the kernel's key state is the truth."""
        active = set(d.dev.active_keys())
        for code in sorted(d.down - active):
            self._on_key(d, code, 0)
        d.pre &= active
        for code in sorted(active - d.down - d.pre):
            self._on_key(d, code, 1)            # a press we did not see
        self._flush(d)
        if d.pad:
            hat = {n for n in d.pad_down
                   if n in ("UP", "DOWN", "LEFT", "RIGHT")}
            d.pad_down = {n for n in map(d.pad_map.get, active) if n} | hat

    def _dispatch(self, code, kind):
        """Call every hook of `code`; True if the event may pass."""
        with self._lock:
            hooks = [self._hooks[h] for h in self._by_code.get(code, ())
                     if h in self._hooks]
        passed = True
        for h in hooks:
            try:
                ok = h.callback(self._KeyEvent(kind, h.name, False))
            except Exception:
                ok = True       # a broken callback must not eat the key
            if h.suppress and not ok:
                passed = False
        return passed

    # --- giving events back -----------------------------------------------------------

    def _forward(self, d, code, value):
        """Give one key event of grabbed device `d` back - a release or a
        repeat only when its press was given back. A plain device's event
        goes out at once through the virtual keyboard, a clone's waits for
        its frame's SYN_REPORT."""
        failed = None
        with self._out_lock:
            if not d.grabbed:
                return
            if value != 1 and code not in d.forwarded:
                return
            if value == 1:
                d.forwarded.add(code)
            elif value == 0:
                d.forwarded.discard(code)
            if not d.plain:
                d.frame.append((EV_KEY, code, value))
                return
            try:
                self._vk.write(EV_KEY, code, value)
                self._vk.syn()
            except Exception as exc:
                failed = exc
        if failed is not None:
            self._give_back_failed(failed)

    def _queue(self, d, t, code, value):
        """A clone device's non-key event, given back with its frame."""
        with self._out_lock:
            if d.grabbed:
                d.frame.append((t, code, value))

    def _flush(self, d):
        """SYN_REPORT: a clone device's frame goes out as one frame."""
        if d.plain:
            return
        failed = None
        with self._out_lock:
            frame, d.frame = d.frame, []
            if not (d.grabbed and frame and d.clone is not None):
                return
            try:
                for t, code, value in frame:
                    d.clone.write(t, code, value)
                d.clone.syn()
            except Exception as exc:
                failed = exc
        if failed is not None:
            self._give_back_failed(failed)

    def _give_back_failed(self, exc):
        # A grabbed device we cannot give back is a dead device.
        self.grab_gave_up = True
        self.error = "%s: %s" % (type(exc).__name__, exc)
        self._release_all()
        _log("HATA: tus sanal klavyeye verilemedi (%s); klavye birakildi. "
             "Surucu tuslari uygulamaya da gidiyor; programi yeniden "
             "baslatin." % self.error)

    def _lift(self, d):
        """Release every key and button we hold down for `d` in the virtual
        keyboard or its clone; a waiting frame is dropped (under
        _out_lock)."""
        d.frame = []
        target = self._vk if d.plain else d.clone
        if not d.forwarded or target is None:
            d.forwarded.clear()
            return
        for code in sorted(d.forwarded):
            try:
                target.write(EV_KEY, code, 0)
            except Exception:
                pass
        d.forwarded.clear()
        try:
            target.syn()
        except Exception:
            pass

    def _release_all(self):
        """Ungrab every device and lift every key we hold in the virtual
        keyboard and the clones. From any thread (watchdog, signal, close);
        never waits long - a stuck holder of _out_lock must not keep the
        grabs."""
        devs = list(self._devs.values())
        got = self._out_lock.acquire(timeout=0.25)
        try:
            grabbed = [d for d in devs if d.grabbed]
            for d in devs:
                d.grabbed = False
                if got:
                    self._lift(d)
            if not got:
                # Lifted by whoever holds the lock, when it lets go.
                self._lift_pending = True
        finally:
            if got:
                self._out_lock.release()
        for d in grabbed:
            try:
                d.dev.ungrab()
            except Exception:
                pass
            # Destroying the clone releases whatever is down in it - also
            # when the lift above could not run.
            self._drop_clone(d)

    def _drop_clone(self, d):
        clone, d.clone = d.clone, None
        if clone is not None:
            try:
                clone.close()
            except Exception:
                pass

    def _lift_if_pending(self):
        """Under _out_lock: the lift _release_all had to leave."""
        if self._lift_pending:
            self._lift_pending = False
            for d in list(self._devs.values()):
                self._lift(d)

    def _grab_wanted(self):
        return (self._want_grab and not self._stop and not self._dead
                and not self._stalled and not self.grab_gave_up
                and not self.watchdog_dead
                and time.monotonic() >= self._grab_ok_at)

    def _wants(self, d):
        """Should `d` be grabbed now: a hook swallows one of its keys."""
        return (not d.touch and not d.unclonable
                and bool(d.codes & self._suppress_codes)
                and self._grab_wanted())

    def _apply_grabs(self):
        """Grab or let go as the hooks want. True while a grab waits (for
        held keys to come up, or for a clone)."""
        if self._lift_pending:
            with self._out_lock:
                self._lift_if_pending()
        pending = False
        for d in list(self._devs.values()):
            if self._wants(d):
                if not d.grabbed and not self._try_grab(d):
                    pending = True
            elif d.grabbed:
                with self._out_lock:
                    d.grabbed = False
                    self._lift(d)
                try:
                    d.dev.ungrab()
                except Exception:
                    pass
                self._drop_clone(d)
            elif d.touch and d.codes & self._suppress_codes:
                self._say(("touch", d.path, d.name),
                          "UYARI: '%s' (%s) bir dokunmatik aygit; "
                          "yakalanmiyor, bu aygittaki surucu tuslari "
                          "uygulamaya da gider." % (d.name, d.path))
        return pending

    def _make_clone(self, d, number):
        try:
            props = list(d.dev.input_props())
        except Exception:
            props = []
        # Not EV_REP: the kernel would add its own repeats to the ones we
        # give back. Not EV_FF: rumble stays with the real device.
        clone = _uinput_class(self._ev).from_device(
            d.dev, filtered_types=(EV_SYN, EV_FF, EV_REP),
            name="%s-klon-%d" % (OWN_PREFIX, number), input_props=props)
        _close_own_reader(clone)
        return clone

    def _start_clone(self, d):
        """Make `d`'s clone on a helper thread: creating a uinput device is
        a kernel call (and python-evdev may wait for udev), and the reader
        must not stop reading the grabbed keyboards meanwhile - nor would
        the watchdog let a long round pass."""
        if d.clone_building or d.unclonable or d.clone is not None:
            return
        d.clone_building = True
        self._clones += 1
        threading.Thread(target=self._build_clone, args=(d, self._clones),
                         name="evdev-clone", daemon=True).start()

    def _build_clone(self, d, number):
        clone = err = None
        try:
            clone = self._make_clone(d, number)
        except Exception as exc:
            err = exc
        keep = False
        with self._lock:
            if (clone is not None and not self.closed
                    and self._devs.get(d.path) is d):
                d.clone = clone
                keep = True
            if err is not None:
                d.unclonable = True     # not tried again: said once
            d.clone_building = False
        if clone is not None and not keep:
            try:
                clone.close()
            except Exception:
                pass
        if err is not None:
            self._say(("clone", d.path, d.name),
                      "UYARI: '%s' (%s) icin sanal kopya kurulamadi (%s); "
                      "yakalanmiyor, bu aygittaki surucu tuslari uygulamaya "
                      "da gider." % (d.name, d.path, err))
        self._wake()

    def _try_grab(self, d):
        """Grab `d` unless a key of it is held. False = try again soon."""
        now = time.monotonic()
        if (d.grab_failed_at is not None
                and now - d.grab_failed_at < _SCAN_PERIOD):
            return True
        if not d.plain and d.clone is None:
            # The clone first: the events of its making went to the desktop
            # (nothing is grabbed yet) and are drained below, never replayed.
            self._start_clone(d)
            return d.unclonable
        self._service(d)            # what came before the grab is not ours
        if not self._current(d) or not self._wants(d):
            return True             # gone, or no longer wanted (a callback)
        try:
            if d.dev.active_keys():
                return False
            d.dev.grab()
        except OSError as exc:
            d.grab_failed_at = now
            self._say(("grab", d.path, d.name),
                      "UYARI: '%s' (%s) klavyesi yakalanamadi (%s; baska bir "
                      "program yakalamis olabilir); bu klavyedeki surucu "
                      "tuslari uygulamaya da gider." % (d.name, d.path, exc))
            return True
        try:
            # A key pressed between the check and the grab: the desktop saw
            # its press and must see its release.
            held = bool(d.dev.active_keys())
        except OSError:
            held = True
        if held:
            try:
                d.dev.ungrab()
            except Exception:
                pass
            return False
        with self._out_lock:
            d.frame = []
            d.grabbed = True
        d.grab_failed_at = None
        return True


class EvdevPad:
    """Every pad and joystick of /dev/input as one: GamepadReader's
    buttons()/pressed(name), plus pressed_names() for the settings' button
    catcher (in switch.BUTTON_NAMES order, as on Windows). buttons() is None
    without a pad (the caller backs off), else a bit mask over
    switch.GAMEPAD_BUTTONS, BTN1..BTN32 included."""

    def __init__(self, keyboard):
        self._kb = keyboard

    def pressed_names(self):
        from eye_gaze.switch import order_names
        return order_names(self._kb.pad_names() or [])

    def buttons(self, index=0):
        names = self._kb.pad_names()
        if names is None:
            return None
        from eye_gaze.switch import GAMEPAD_BUTTONS
        mask = 0
        for n in names:
            mask |= GAMEPAD_BUTTONS.get(n, 0)
        return mask

    def pressed(self, name, index=0):
        n = str(name or "").strip().upper()
        n = _PAD_ALIASES.get(n, n)
        return n in (self._kb.pad_names() or ())
