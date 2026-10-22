# gaze/eye_gaze/actions.py
"""Mouse actions via the Windows API (ctypes).

Off Windows (user32 unavailable) they go to the Linux input backend
(eye_gaze.platform.linux_backend()); its capabilities() says when that
backend cannot click. The gaze driver calls these when a finger switch /
blink fires.
"""
import ctypes

_u = getattr(ctypes, "windll", None)
_u = _u.user32 if _u is not None else None

_LEFTDOWN = 0x0002
_LEFTUP = 0x0004
_RIGHTDOWN = 0x0008
_RIGHTUP = 0x0010
_WHEEL = 0x0800


_LINUX_BUTTONS = {_LEFTDOWN: ("left", True), _LEFTUP: ("left", False),
                  _RIGHTDOWN: ("right", True), _RIGHTUP: ("right", False)}


def _backend():
    from eye_gaze import platform
    return platform.linux_backend()


def _event(flags, data=0):
    if _u is not None:
        _u.mouse_event(flags, 0, 0, ctypes.c_int(int(data)), 0)
    elif flags == _WHEEL:
        _backend().scroll(int(data))
    else:
        _backend().button(*_LINUX_BUTTONS[flags])


def left_down():
    _event(_LEFTDOWN)


def left_up():
    _event(_LEFTUP)


def left_click():
    left_down()
    left_up()


def right_click():
    _event(_RIGHTDOWN)
    _event(_RIGHTUP)


def double_click():
    left_click()
    left_click()


def escape():
    """Tap Esc. Pressed while the left button is still held, it CANCELS a
    Windows drag-and-drop instead of dropping the item wherever the frozen
    cursor happens to be."""
    if _u is not None:
        _u.keybd_event(0x1B, 0, 0, 0)
        _u.keybd_event(0x1B, 0, 0x0002, 0)   # KEYEVENTF_KEYUP
    else:
        _backend().keyboard.send("esc")


def scroll(amount):
    """Scroll the wheel. amount > 0 scrolls up, < 0 down (in wheel units)."""
    _event(_WHEEL, int(amount))
