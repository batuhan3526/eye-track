# gaze/eye_gaze/joystick_win.py
"""Generic HID joysticks on Windows through winmm joyGetPosEx.

Most switch adapters for disabled users are not XInput pads but plain HID
joysticks: XInput never sees them, winmm does. switch.GamepadReader asks
XInput first and comes here only when no XInput pad answers.

Buttons are numbered as winmm numbers them (button n = bit n-1 of
dwButtons, 1..32). Linux numbers the same device by its own key-code list
(see platform/linux_evdev._pad_map), so a BTNn learned on one system may
name another button on the other: the settings catch the button again.

Microsoft documents probing an absent joystick as slow (the first probe of
ids 0..15 took 34 ms on the development machine), so they are probed once
when the reader is made - at start-up, not in the gaze loop - and then at
most once per RESCAN seconds while none answers. A joystick that answered is read alone, every call: a
read of a present joystick is cheap.
"""
import ctypes
import time

JOY_RETURNBUTTONS = 0x80
JOYERR_NOERROR = 0
MAX_JOYSTICKS = 16          # winmm ids 0..15
_DWORD = ctypes.c_uint32     # not wintypes: importable on Linux too


class JOYINFOEX(ctypes.Structure):
    _fields_ = [("dwSize", _DWORD),
                ("dwFlags", _DWORD),
                ("dwXpos", _DWORD),
                ("dwYpos", _DWORD),
                ("dwZpos", _DWORD),
                ("dwRpos", _DWORD),
                ("dwUpos", _DWORD),
                ("dwVpos", _DWORD),
                ("dwButtons", _DWORD),
                ("dwButtonNumber", _DWORD),
                ("dwPOV", _DWORD),
                ("dwReserved1", _DWORD),
                ("dwReserved2", _DWORD)]


def _load_winmm():
    """winmm with joyGetPosEx's prototype declared, or None (not Windows)."""
    if getattr(ctypes, "windll", None) is None:
        return None
    try:
        lib = ctypes.WinDLL("winmm")
        fn = lib.joyGetPosEx
        fn.argtypes = [ctypes.c_uint, ctypes.POINTER(JOYINFOEX)]
        fn.restype = ctypes.c_uint          # MMRESULT
        return lib
    except Exception:
        return None


class GenericJoystick:
    """The first winmm joystick that answers.

    buttons() -> dwButtons (bit n-1 = button n), or None while no joystick
    answers. A joystick that stops answering (unplugged, even mid-press) is
    None at once - every button released - and is looked for again by the
    next scan."""

    RESCAN = 2.0            # s between probes of ids 0..15 while none answers

    def __init__(self, api="auto", clock=time.monotonic, rescan=None):
        self._api = _load_winmm() if api == "auto" else api
        self._clock = clock
        self._rescan = self.RESCAN if rescan is None else rescan
        self._info = JOYINFOEX()
        self._id = None
        self._next_scan = 0.0
        self.buttons()          # the slow first probe, while starting up

    def _read(self, joy_id):
        info = self._info
        info.dwSize = ctypes.sizeof(JOYINFOEX)
        info.dwFlags = JOY_RETURNBUTTONS
        try:
            if self._api.joyGetPosEx(joy_id, ctypes.byref(info)) != JOYERR_NOERROR:
                return None
        except Exception:
            return None
        return int(info.dwButtons) & 0xFFFFFFFF

    def buttons(self):
        if self._api is None:
            return None
        if self._id is not None:
            b = self._read(self._id)
            if b is not None:
                return b
            self._id = None
            self._next_scan = self._clock() + self._rescan
            return None
        now = self._clock()
        if now < self._next_scan:
            return None
        self._next_scan = now + self._rescan
        for joy_id in range(MAX_JOYSTICKS):
            b = self._read(joy_id)
            if b is not None:
                self._id = joy_id
                return b
        return None
