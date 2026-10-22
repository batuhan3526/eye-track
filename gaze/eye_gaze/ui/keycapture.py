# gaze/eye_gaze/ui/keycapture.py
""""Şimdi tuşa basın": the next key or gamepad button, by the very name the
driver will hook (spec 6.7) - so whatever is caught here is what works.

The driver must be STOPPED while this runs (the launcher calls
Supervisor.stop() first): it hooks and swallows its own keys, and on Linux
it grabs the keyboard device, so a press would never reach us - or worse,
would click.

  capture_key(timeout_s)  Windows: the `keyboard` library's read_event (a
                          temporary global hook it removes itself; nothing
                          swallowed, the press also reaches the focused
                          window - the capture dialog takes focus for that).
                          Linux: every keys.KNOWN name hooked through the
                          backend with suppress=True, the first press taken
                          and swallowed, every hook removed afterwards
                          (also on timeout or error).
  capture_pad(timeout_s)  the first button pressed after the call
                          (GamepadReader.pressed_names), ignoring any held
                          when it started.

Both return None when nothing was pressed in time and raise
CaptureUnavailable (Turkish text) when nothing CAN be pressed.
"""
import queue
import threading
import time

from eye_gaze import platform as _platform
from eye_gaze.i18n import t
from eye_gaze.platform import keys as _keys


class CaptureUnavailable(RuntimeError):
    """str(): why no key or button can be caught here (Turkish)."""


def normalize_key_name(name):
    """The config.yaml spelling of a key name the keyboard library or a
    backend reported: lower case, single spaces, aliases folded into the
    shared keys.KNOWN names ("spacebar" -> "space"). A name outside KNOWN
    (e.g. the Turkish "ı" key on Windows) is kept as reported: the Windows
    driver hooks it by that same name."""
    n = " ".join(str(name or "").split()).lower()
    if not n:
        return ""
    return _keys.normalize(n) or n


def capture_key(timeout_s=10, kb=None):
    """The next key pressed, as config.yaml names it; None after timeout_s."""
    try:
        kb = kb if kb is not None else _platform.keyboard_api()
    except Exception as exc:
        raise CaptureUnavailable(
            t("keycapture.no_keyboard", error=exc)) from exc
    if _platform.OS == "windows":
        return _capture_read_event(kb, timeout_s)
    return _capture_hooks(kb, timeout_s)


def _capture_read_event(kb, timeout_s):
    got = queue.Queue()
    done = threading.Event()

    def reader():
        # read_event hooks, waits for ONE event and unhooks: after a timeout
        # this thread ends at the next key, its result thrown away (that
        # hook swallows nothing, so the stray wait costs the user nothing).
        while not done.is_set():
            try:
                ev = kb.read_event(suppress=False)
            except Exception as exc:
                got.put(exc)
                return
            if done.is_set():
                return
            if getattr(ev, "event_type", "") == "down":
                name = normalize_key_name(getattr(ev, "name", ""))
                if name:
                    got.put(name)
                    return

    threading.Thread(target=reader, name="tus-yakala", daemon=True).start()
    try:
        result = got.get(timeout=timeout_s)
    except queue.Empty:
        return None
    finally:
        done.set()
    if isinstance(result, Exception):
        raise CaptureUnavailable(t("keycapture.keyboard_failed",
                                   error=result))
    return result


def _capture_hooks(kb, timeout_s):
    got = queue.Queue()
    taken = threading.Event()

    def make(name):
        def on_event(e):
            if getattr(e, "event_type", "") == "down" and not taken.is_set():
                taken.set()
                got.put(name)
            return False        # swallowed: it must not type or click
        return on_event

    removers = []
    try:
        for name in sorted(_keys.KNOWN):
            try:
                removers.append(kb.hook_key(name, make(name), suppress=True))
            except Exception:
                continue
        if not removers:
            raise CaptureUnavailable(t("keycapture.no_permission"))
        try:
            return got.get(timeout=timeout_s)
        except queue.Empty:
            return None
    finally:
        for rm in removers:
            try:
                rm()
            except Exception:
                pass


def capture_pad(timeout_s=10, reader=None, clock=time.monotonic,
                sleep=time.sleep, poll_s=0.03):
    """The first gamepad / switch-interface button pressed after the call,
    by its shared name (A, B, ..., BTN1...); None after timeout_s."""
    if reader is None:
        from eye_gaze.switch import GamepadReader
        reader = GamepadReader()
    if reader.buttons() is None:
        raise CaptureUnavailable(t("keycapture.no_pad"))
    held = set(reader.pressed_names())
    end = clock() + float(timeout_s)
    while clock() < end:
        names = list(reader.pressed_names())
        fresh = [n for n in names if n not in held]
        if fresh:
            return fresh[0]
        held &= set(names)          # released: a new press of it counts
        sleep(poll_s)
    return None
