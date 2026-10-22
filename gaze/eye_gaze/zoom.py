# gaze/eye_gaze/zoom.py
"""In-driver magnifier click: capture a region of the screen around the
cursor, show it fullscreen, let the user aim by gaze inside the magnified
view, then map the chosen point back to real screen coordinates and click.

Gives OptiKey-grade precision clicking without OptiKey running.
"""
import ctypes

_SRCCOPY_CAPTUREBLT = 0x40CC0020  # SRCCOPY | CAPTUREBLT (layered windows too)


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", ctypes.c_uint32 * 3)]


_API = None


def _gdi():
    """(user32, gdi32) with the prototypes declared, or (None, None).

    USER and GDI handles are documented to fit in 32 bits even on Win64, so
    this is NOT the truncation that crashed the clipboard - there a HGLOBAL is
    a real 64-bit pointer. Declaring them anyway costs nothing and removes the
    question: nobody reading this should have to work out which handles are
    which width before believing the code is right.
    """
    global _API
    if _API is not None:
        return _API
    try:
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        u.GetDC.argtypes = [ctypes.c_void_p]
        u.GetDC.restype = ctypes.c_void_p
        u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.ReleaseDC.restype = ctypes.c_int
        g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        g.CreateCompatibleDC.restype = ctypes.c_void_p
        g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_int]
        g.CreateCompatibleBitmap.restype = ctypes.c_void_p
        g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        g.SelectObject.restype = ctypes.c_void_p
        g.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                             ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        g.BitBlt.restype = ctypes.c_int
        g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_uint]
        g.GetDIBits.restype = ctypes.c_int
        g.DeleteObject.argtypes = [ctypes.c_void_p]
        g.DeleteObject.restype = ctypes.c_int
        g.DeleteDC.argtypes = [ctypes.c_void_p]
        g.DeleteDC.restype = ctypes.c_int
        g.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
        g.GetDeviceCaps.restype = ctypes.c_int
    except Exception:
        _API = (None, None)
        return _API
    _API = (u, g)
    return _API


def physical_rect(x, y, w, h, scale):
    """Logical (x, y, w, h) -> the physical pixels the same area covers."""
    s = float(scale)
    return (int(round(x * s)), int(round(y * s)),
            max(1, int(round(w * s))), max(1, int(round(h * s))))


def screen_scale():
    """Physical / logical pixel ratio of the primary screen (1.5 at 150%).

    This process is deliberately DPI-unaware: the cursor, the screen size and
    calibration.json all live in logical pixels, and making it aware would
    rescale every saved calibration. An unaware BitBlt, however, reads
    PHYSICAL pixels - so the capture rectangle has to be scaled by this.
    DESKTOPHORZRES reports the real width even to an unaware process.

    Linux: always 1.0. X11 has no DPI virtualisation - the cursor, the
    monitors and the screenshot all speak the same physical pixels.
    """
    from eye_gaze import platform
    if platform.OS != "windows":
        return 1.0
    user32, gdi = _gdi()
    if user32 is None:
        return 1.0
    hdc = None
    try:
        hdc = user32.GetDC(None)
        logical = gdi.GetDeviceCaps(hdc, 8)        # HORZRES
        physical = gdi.GetDeviceCaps(hdc, 118)     # DESKTOPHORZRES
        if logical > 0 and physical > 0:
            return max(1.0, min(4.0, physical / float(logical)))
    except Exception:
        pass
    finally:
        if hdc is not None:
            try:
                user32.ReleaseDC(None, hdc)
            except Exception:
                pass
    return 1.0


def grab_region(x, y, w, h, scale=None):
    """Screenshot of the (x, y, w, h) screen region as a BGR numpy array,
    or None when capture is unavailable (no desktop, Wayland).

    Every object is released in a `finally`. Capture fails for perfectly
    ordinary reasons - a UAC prompt, a protected window, a locked session - and
    before this the failure path raised straight past the three cleanup calls,
    leaking a bitmap and two device contexts on every failed F6 press. A
    process that reaches the 10 000 GDI object limit stops being able to draw
    anything at all, and F6 is how this user hits small targets.

    Linux: mss on X11; None on Wayland and without an X display
    (platform.overlay_status("zoom_capture") says why).
    """
    from eye_gaze import platform
    if platform.OS != "windows":
        return _grab_x11(*physical_rect(x, y, w, h,
                                        1.0 if scale is None else scale))
    user32, gdi = _gdi()
    if user32 is None:
        return None
    # The image comes back at PHYSICAL resolution (sharper when magnified);
    # the caller maps clicks with the logical region, which is unchanged.
    x, y, w, h = physical_rect(x, y, w, h,
                               screen_scale() if scale is None else scale)
    hdc = mem = bmp = old = None
    try:
        import numpy as np
        hdc = user32.GetDC(None)
        mem = gdi.CreateCompatibleDC(hdc)
        bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
        old = gdi.SelectObject(mem, bmp)
        if not gdi.BitBlt(mem, 0, 0, w, h, hdc, int(x), int(y),
                          _SRCCOPY_CAPTUREBLT):
            return None
        bi = _BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h  # top-down
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0  # BI_RGB
        buf = (ctypes.c_char * (w * h * 4))()
        got = gdi.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        if got != h:
            return None
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        return arr[:, :, :3].copy()  # BGRA -> BGR
    except Exception:
        return None
    finally:
        try:
            if old is not None and mem is not None:
                # Put the DC's original bitmap back before deleting it: a DC
                # still holding our bitmap will not release it.
                gdi.SelectObject(mem, old)
            if bmp is not None:
                gdi.DeleteObject(bmp)
            if mem is not None:
                gdi.DeleteDC(mem)
            if hdc is not None:
                user32.ReleaseDC(None, hdc)
        except Exception:
            pass


def _grab_x11(x, y, w, h):
    """BGR screenshot of the X11 root region, or None.

    A fresh mss connection per grab: F6 is pressed a few times a minute at
    most, and a connection of our own is never shared with another thread.
    mss 10's default backend is XCB, so it installs no process-wide Xlib
    error handler under Tk and OpenCV. The dwell ring is not excluded from
    this capture (X11 has no such thing): dwell_ring.guard_capture() unmaps
    it before the grab."""
    from eye_gaze import platform
    if not platform.overlay_status("zoom_capture")[0]:
        return None
    try:
        import mss
        import numpy as np
        make = getattr(mss, "MSS", None) or mss.mss
        with make() as sct:
            shot = sct.grab({"left": int(x), "top": int(y),
                             "width": int(w), "height": int(h)})
        arr = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(
            shot.height, shot.width, 4)
        return arr[:, :, :3].copy()  # BGRA -> BGR
    except Exception as exc:
        # The driver says "no screenshot" on every press; WHY is said once
        # (a missing libxcb-render made every grab fail with no clue).
        if not _X11_GRAB_SAID[0]:
            _X11_GRAB_SAID[0] = True
            try:
                print("[zoom] X11 ekran goruntusu alinamadi: "
                      + platform.ascii_text("%s: %s" % (type(exc).__name__,
                                                        exc))[:160],
                      flush=True)
            except Exception:
                pass
        return None


_X11_GRAB_SAID = [False]


def map_zoom_point(px, py, x0, y0, zw, zh, sw, sh):
    """Cursor position (px, py) on the fullscreen magnified view -> the real
    screen point inside the captured region (x0, y0, zw, zh)."""
    rx = x0 + (px / max(1, sw - 1)) * (zw - 1)
    ry = y0 + (py / max(1, sh - 1)) * (zh - 1)
    return (int(round(rx)), int(round(ry)))
