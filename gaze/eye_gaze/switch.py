# gaze/eye_gaze/switch.py
"""Finger-switch input: rising-edge detection over keyboard keys and/or a
gamepad or joystick button. The pure EdgeDetector is unit-tested; the actual
device polling is I/O and degrades to "not pressed" when unavailable.
"""
import ctypes
import time

from eye_gaze import injected
from eye_gaze import platform as _platform

# One set of button names on every system, so one config.yaml works on both:
# Xbox-type pads (XInput on Windows, evdev on Linux) and generic joysticks
# (winmm on Windows, evdev on Linux), whose buttons are BTN1..BTN32.
BUTTON_NAMES = (("A", "B", "X", "Y", "START", "BACK", "LB", "RB", "LS", "RS",
                 "UP", "DOWN", "LEFT", "RIGHT")
                + tuple("BTN%d" % n for n in range(1, 33)))

# Bit masks of GamepadReader.buttons(). The Xbox names are XInput's wButtons
# bits, unchanged (configs and tests pin them); BTNn sits above the 16 XInput
# bits, so a mask may be wider than 16 bits (Python ints: no consumer stores
# it in a fixed-width field).
_BTN_SHIFT = 16
GAMEPAD_BUTTONS = {
    "A": 0x1000, "B": 0x2000, "X": 0x4000, "Y": 0x8000,
    "LB": 0x0100, "RB": 0x0200, "START": 0x0010, "BACK": 0x0020,
    "LS": 0x0040, "RS": 0x0080,
    "UP": 0x0001, "DOWN": 0x0002, "LEFT": 0x0004, "RIGHT": 0x0008,
}
GAMEPAD_BUTTONS.update({"BTN%d" % n: 1 << (_BTN_SHIFT + n - 1)
                        for n in range(1, 33)})
# Today's configs spell the d-pad as XInput does.
GAMEPAD_BUTTONS.update({"DPAD_UP": 0x0001, "DPAD_DOWN": 0x0002,
                        "DPAD_LEFT": 0x0004, "DPAD_RIGHT": 0x0008})

_ORDER = {n: i for i, n in enumerate(BUTTON_NAMES)}


def valid_button(name):
    """True if name is a known gamepad/joystick button name (any case)."""
    return bool(name) and str(name).upper() in GAMEPAD_BUTTONS


def button_names_text():
    """The valid names for a console warning (ASCII, one line)."""
    return ", ".join(BUTTON_NAMES[:14]) + ", BTN1..BTN32"


def order_names(names):
    """Button names in BUTTON_NAMES order (an unknown name last, sorted)."""
    return sorted(set(names), key=lambda n: (_ORDER.get(n, len(_ORDER)), n))


def names_of(mask):
    """The names of a buttons() mask, in BUTTON_NAMES order; [] for None."""
    if not mask:
        return []
    return [n for n in BUTTON_NAMES if mask & GAMEPAD_BUTTONS[n]]


class EdgeDetector:
    """Rising-edge detector. update(pressed) returns True only on a
    False -> True transition (so a held button fires exactly once)."""

    def __init__(self):
        self._prev = False

    def update(self, pressed):
        pressed = bool(pressed)
        edge = pressed and not self._prev
        self._prev = pressed
        return edge


class _XInputGamepad(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort),
                ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


class _XInputState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint),
                ("Gamepad", _XInputGamepad)]


def _load_xinput():
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            return getattr(windll, name)
        except Exception:
            continue
    return None


class GamepadReader:
    """The switch user's pad or joystick, by the shared button names.

    Windows: XInput controller 0 first; while it does not answer, the first
    winmm joystick (joystick_win - most switch adapters are plain HID
    joysticks). While a joystick answers, the empty XInput slot is asked only
    every XINPUT_RETRY seconds, so an Xbox pad plugged in later takes over.
    Linux: the input backend's pad (evdev), as it is.

    buttons() is None when no device answers - distinct from 0 (connected,
    nothing pressed) so the caller can back off: Microsoft documents polling an
    empty XInput slot every frame as expensive. A device unplugged mid-press
    is None at once, so no button stays held (alarm_switch_hold).
    """

    XINPUT_RETRY = 2.0      # s between XInput polls while a joystick answers

    def __init__(self, xinput="auto", joystick="auto", pad="auto",
                 clock=time.monotonic):
        self._clock = clock
        self._state = _XInputState()
        self._x = None
        self._joy = None
        self._generic = False       # the joystick answered last time
        self._xinput_next = 0.0
        if pad == "auto":
            pad = _linux_pad() if _platform.OS == "linux" else None
        self._pad = pad
        if pad is not None:
            return
        self._x = _load_xinput() if xinput == "auto" else xinput
        if self._x is not None:
            try:
                self._x.XInputGetState.argtypes = [ctypes.c_uint,
                                                   ctypes.POINTER(_XInputState)]
                self._x.XInputGetState.restype = ctypes.c_uint
            except Exception:
                pass
        if joystick == "auto":
            joystick = None
            if getattr(ctypes, "windll", None) is not None:
                from eye_gaze import joystick_win
                joystick = joystick_win.GenericJoystick()
        self._joy = joystick

    def _xinput(self, index):
        if self._x is None:
            return None
        try:
            if self._x.XInputGetState(index, ctypes.byref(self._state)) != 0:
                return None  # not connected
            return self._state.Gamepad.wButtons
        except Exception:
            return None

    def buttons(self, index=0):
        if self._pad is not None:
            try:
                b = self._pad.buttons(index)
            except Exception:
                return None
            return None if b is None else int(b)
        if not self._generic or self._clock() >= self._xinput_next:
            b = self._xinput(index)
            if b is not None:
                self._generic = False
                return b
            self._xinput_next = self._clock() + self.XINPUT_RETRY
        raw = None
        if self._joy is not None:
            try:
                raw = self._joy.buttons()
            except Exception:
                raw = None
        self._generic = raw is not None
        return None if raw is None else (int(raw) & 0xFFFFFFFF) << _BTN_SHIFT

    def pressed(self, name, index=0):
        if self._pad is not None:
            try:
                return bool(self._pad.pressed(name, index))
            except Exception:
                return False
        return bool((self.buttons(index) or 0)
                    & GAMEPAD_BUTTONS.get(str(name).upper(), 0))

    def pressed_names(self, index=0):
        """Names of the buttons held now, in BUTTON_NAMES order (the
        settings' button catcher); [] without a device."""
        if self._pad is not None:
            try:
                return order_names(self._pad.pressed_names() or [])
            except Exception:
                return []
        return names_of(self.buttons(index))


def _linux_pad():
    """The Linux input backend's pad, or None (no evdev access)."""
    try:
        return _platform.linux_backend().gamepad()
    except Exception:
        return None


class KeyPresses:
    """The driver's own keys, as discrete presses taken from the global hook.

    Polling keyboard.is_pressed() once per video frame had three faults, all
    seen from the chair: the key still reached the focused app (Space typed a
    space or scrolled the page before the click landed, F5 reloaded the browser
    behind the AAC grid, F3 opened its find bar), a tap shorter than one frame
    was lost, and a held key's auto-repeat was indistinguishable from presses.

    Here every hooked key is counted once per physical press (repeats are
    ignored until the key-up) and, while `suppressing` is True, swallowed
    before any application sees it. `suppressing` is a live switch: pause is
    the caregiver's mode, and their keyboard must type spaces again.

    The hook callback runs on the OS hook thread and must stay trivial - a slow
    low-level hook is silently removed by Windows.
    """

    def __init__(self, keys, suppress=True, kb=None, defer=False):
        """defer=True: the keys are named but hooked only by arm() (the
        driver's late grabs on Linux X11, see gaze_driver.main)."""
        import threading
        self._lock = threading.Lock()
        self._held = set()
        self._count = {}
        self._removers = []
        self._hooked = {}       # name -> its remover (for disarm)
        self.bad_keys = []
        self.suppressing = bool(suppress)
        self._suppress_cfg = bool(suppress)
        if kb is None:
            try:
                kb = _platform.keyboard_api()
                injected.install()
            except Exception:
                kb = None
        self.ok = kb is not None
        self._kb = kb
        self._pending = []      # named, not hooked yet (defer=True)
        if kb is None:
            return
        seen = []
        for k in keys:
            name = str(k or "").strip().lower()
            if name and name not in seen:
                seen.append(name)
        if defer:
            self._pending = seen
        else:
            self._hook_names(seen)

    def arm(self, names=None):
        """Hook the keys a defer=True hub has not hooked yet - all of them,
        or only those in `names`. Idempotent; a key that fails is added to
        bad_keys as at construction."""
        if self._kb is None:
            return
        want = None if names is None else {
            str(n or "").strip().lower() for n in names}
        now = [n for n in self._pending if want is None or n in want]
        self._pending = [n for n in self._pending if n not in now]
        self._hook_names(now)

    def _hook_names(self, names):
        kb = self._kb
        for name in names:
            try:
                rm = kb.hook_key(name, self._handler(name),
                                 suppress=self._suppress_cfg)
            except Exception:
                self.bad_keys.append(name)
                continue
            self._removers.append(rm)
            self._hooked[name] = rm
            self._count[name] = 0

    def disarm(self, keep=()):
        """Unhook every key but those in `keep` and name them again as not
        hooked, so a later arm() hooks them back. Linux X11 only: while the
        driver's models load after a camera wait, the alarm's keys (`keep`)
        are the only grabs (gaze_driver.main). A key held at that moment is
        released here: its key-up would never reach this hub."""
        stay = {str(n or "").strip().lower() for n in keep}
        for name in [n for n in self._hooked if n not in stay]:
            rm = self._hooked.pop(name)
            try:
                self._removers.remove(rm)
            except ValueError:
                pass
            try:
                rm()
            except Exception:
                pass
            with self._lock:
                self._held.discard(name)
                self._count[name] = 0
            if name not in self._pending:
                self._pending.append(name)

    def _handler(self, name):
        def on_event(e):
            if injected.is_injected(e):
                return True     # OSK / OptiKey typing: the app's, not ours
            with self._lock:
                if getattr(e, "event_type", "") == "down":
                    if name not in self._held:
                        self._held.add(name)
                        self._count[name] = self._count.get(name, 0) + 1
                else:
                    self._held.discard(name)
            # False blocks the event (only honoured for suppress=True hooks).
            return not (self._suppress_cfg and self.suppressing)
        return on_event

    def take(self, key):
        """Consume one pending press of `key`; True if there was one."""
        name = str(key or "").strip().lower()
        with self._lock:
            if self._count.get(name, 0) > 0:
                self._count[name] -= 1
                return True
        return False

    def held(self, key):
        name = str(key or "").strip().lower()
        with self._lock:
            return name in self._held

    def clear(self):
        with self._lock:
            for name in self._count:
                self._count[name] = 0

    def close(self):
        self._pending = []      # closed before arm(): never hooked later
        for rm in self._removers:
            try:
                rm()
            except Exception:
                pass
        self._removers = []
        self._hooked = {}


def key_pressed(key):
    """True if the keyboard key is currently held (platform keyboard API)."""
    if not key:
        return False
    try:
        return _platform.keyboard_api().is_pressed(key)
    except Exception:
        return False
