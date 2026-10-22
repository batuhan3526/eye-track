# gaze/eye_gaze/cursor.py
"""Move the cursor. NOTE: this module never clicks - clicking is OptiKey + finger switch.

Windows: user32. Elsewhere (_user32 is None) every call goes to the Linux
input backend (eye_gaze.platform.linux_backend()).
"""
import ctypes

_user32 = getattr(ctypes, "windll", None)
_user32 = _user32.user32 if _user32 is not None else None


def _backend():
    from eye_gaze import platform
    return platform.linux_backend()


def screen_size():
    if _user32 is None:
        _x, _y, w, h = _backend().primary()
        return (w, h)
    return (_user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1))


def primary_rect():
    """(x, y, w, h) of the primary monitor. Windows: (0, 0, w, h) - the
    primary's top-left defines the origin there. Linux: wherever the X11 /
    evdev backend says it is (often not (0, 0))."""
    if _user32 is None:
        x, y, w, h = _backend().primary()
        return (int(x), int(y), int(w), int(h))
    w, h = screen_size()
    return (0, 0, w, h)


def virtual_screen():
    """(x, y, w, h) of the full virtual desktop spanning ALL monitors.
    x/y can be negative (a monitor left of / above the primary)."""
    if _user32 is None:
        rects = _backend().screens()
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        return (x0, y0, x1 - x0, y1 - y0)
    return (_user32.GetSystemMetrics(76), _user32.GetSystemMetrics(77),   # SM_X/YVIRTUALSCREEN
            _user32.GetSystemMetrics(78), _user32.GetSystemMetrics(79))   # SM_CX/CYVIRTUALSCREEN


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_cursor():
    """Current cursor position (screen px)."""
    if _user32 is None:
        x, y = _backend().get_cursor()
        return (int(x), int(y))
    p = _POINT()
    _user32.GetCursorPos(ctypes.byref(p))
    return (int(p.x), int(p.y))


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


def monitor_rect(x, y):
    """(x, y, w, h) of the monitor holding the point (nearest one if none),
    in this process's (logical) coordinates; the primary screen when the
    API is unavailable. Never raises."""
    if _user32 is None:
        return _linux_monitor_rect(x, y)
    try:
        u = ctypes.WinDLL("user32")          # own prototypes, not shared
        u.MonitorFromPoint.argtypes = [_POINT, ctypes.c_ulong]
        u.MonitorFromPoint.restype = ctypes.c_void_p
        u.GetMonitorInfoW.argtypes = [ctypes.c_void_p,
                                      ctypes.POINTER(_MONITORINFO)]
        u.GetMonitorInfoW.restype = ctypes.c_int
        h = u.MonitorFromPoint(_POINT(int(x), int(y)), 2)  # NEAREST
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if h and u.GetMonitorInfoW(h, ctypes.byref(mi)):
            r = mi.rcMonitor
            if r.right > r.left and r.bottom > r.top:
                return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        pass
    w, h = screen_size()
    return (0, 0, w, h)


def _linux_monitor_rect(x, y):
    try:
        rects = _backend().screens()
        for r in rects:
            if r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3]:
                return tuple(r)

        def gap(r):
            dx = max(r[0] - x, 0, x - (r[0] + r[2] - 1))
            dy = max(r[1] - y, 0, y - (r[1] + r[3] - 1))
            return dx * dx + dy * dy
        return tuple(min(rects, key=gap))
    except Exception:
        from eye_gaze import platform
        return platform.GUESSED_SCREEN


def clamp(x, y, w, h, ox=0, oy=0):
    return (max(ox, min(int(x), ox + w - 1)), max(oy, min(int(y), oy + h - 1)))


def set_cursor(x, y, virtual=False):
    """Move the cursor, clamped to the primary monitor (default) or to the
    whole multi-monitor virtual desktop (virtual=True).

    Linux: the primary monitor's real rectangle. An X11 primary is often
    not at (0, 0) - a monitor left of it puts it at (1920, 0) - and clamping
    to (0, 0, w, h) pinned the cursor to the wrong monitor."""
    if virtual:
        ox, oy, w, h = virtual_screen()
    elif _user32 is None:
        ox, oy, w, h = _backend().primary()
    else:
        ox, oy = 0, 0
        w, h = screen_size()
    cx, cy = clamp(x, y, w, h, ox, oy)
    if _user32 is not None:
        _user32.SetCursorPos(cx, cy)
    else:
        _backend().set_cursor(cx, cy)
    return (cx, cy)
