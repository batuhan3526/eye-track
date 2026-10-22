# gaze/eye_gaze/dwell_ring.py
"""Visible dwell progress: a ring around the cursor that fills while a dwell
click builds, disappears when the dwell resets and flashes on the click.

Dwell clicking without it is clicking blind - the user cannot tell whether
holding still is about to click, or whether a tremor just restarted the count.

Three layers, each testable on its own:
  render_ring / ring_origin   pure geometry and pixels (numpy)
  DwellRingModel              pure state: what to show at time t
  DwellRing                   model + a window object, behind the failure
                              contract: it NEVER raises into the gaze loop. Any
                              fault turns the ring off for the session and is
                              logged once; the cursor and clicks are untouched.
Win32RingWindow is the thin real window: layered, click-through
(WS_EX_TRANSPARENT), topmost, never activated, not in the taskbar, and
excluded from screen capture where Windows supports it. X11RingWindow is
its Linux twin (override-redirect, empty input shape, raised on every move);
X11 cannot exclude a window from a screenshot, so the magnifier's grab is
preceded by an unmap and a round trip (DwellRing.prepare_capture). Wayland
allows neither window nor screenshot: attach() says so once, returns None.

Coordinates are the process's LOGICAL pixels (it is DPI-unaware on purpose,
like the cursor and calibration.json); DWM scales the window like any other.
On X11 logical = physical.
"""
import ctypes
import math
import os
import time
from collections import namedtuple

import numpy as np

# BGR colours and alphas. The track is dark and faint so the ring reads on
# both light and dark pages; the arc is a warm yellow; the click flash green.
_TRACK = ((40, 40, 40), 110)
_ARC = ((0, 200, 255), 240)
_FLASH = ((80, 235, 60), 255)

MIN_PX, MAX_PX, DEFAULT_PX = 12, 400, 40

RingView = namedtuple("RingView", "left top progress flash")


def ring_origin(cx, cy, size):
    """Top-left of a size x size window centred on the point (cx, cy)."""
    half = int(size) // 2
    return (int(round(cx)) - half, int(round(cy)) - half)


def ring_thickness(size):
    return max(3, int(round(size * 0.14)))


def render_ring(size, progress, flash=False):
    """size x size premultiplied BGRA (uint8) image of the ring.

    The arc runs clockwise from 12 o'clock to `progress` (0..1); the rest of
    the band is a faint track. flash=True draws the full band in the click
    colour. The middle stays fully transparent so the target is not covered.
    """
    s = int(size)
    c = (s - 1) / 2.0
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
    dx, dy = xx - c, yy - c
    r = np.sqrt(dx * dx + dy * dy)
    outer = s / 2.0 - 0.5
    inner = outer - ring_thickness(s)
    # 1-px antialiased band edges
    cover = np.clip(outer - r + 0.5, 0.0, 1.0) * np.clip(r - inner + 0.5, 0.0, 1.0)
    frac = (np.arctan2(dx, -dy) % (2 * math.pi)) / (2 * math.pi)
    if flash:
        lit = np.ones_like(r, dtype=bool)
        (col, alpha) = _FLASH
    else:
        p = max(0.0, min(1.0, float(progress)))
        lit = frac <= p if p > 0.0 else np.zeros_like(r, dtype=bool)
        (col, alpha) = _ARC
    out = np.zeros((s, s, 4), dtype=np.float32)
    for (bgr, a), mask in (((col, alpha), lit), (_TRACK, ~lit)):
        aa = cover * mask * (a / 255.0)
        for ch in range(3):
            out[:, :, ch] += bgr[ch] * aa          # premultiplied
        out[:, :, 3] += 255.0 * aa
    return np.ascontiguousarray(np.clip(np.round(out), 0, 255).astype(np.uint8))


class DwellRingModel:
    """What the ring shows at time `now`, from a DwellClicker's own state.

    Shown only while the dweller is being FED (the loop stops feeding it when
    paused, with the eyes closed or the camera lost; progress_at(now) would then
    climb to 100% for a click that is never coming), and only once the dwell is
    past `show_from` - every ordinary glance starts a dwell, and a ring at 1%
    would flicker all over the screen while the user reads.
    """

    def __init__(self, size, flash_seconds=0.3, max_age=0.3, show_from=0.15,
                 steps=60):
        self.size = int(size)
        self.flash_seconds = float(flash_seconds)
        self.max_age = float(max_age)
        self.show_from = float(show_from)
        self.steps = int(steps)
        self._seen_fires = 0
        self._flash_until = None
        self._flash_pos = None

    def view(self, dweller, now):
        """RingView to show, or None for hidden."""
        fires = getattr(dweller, "fires", 0)
        if fires != self._seen_fires:
            self._seen_fires = fires
            self._flash_until = now + self.flash_seconds
            self._flash_pos = dweller.last_pos
        if self._flash_until is not None:
            if now < self._flash_until and self._flash_pos is not None:
                left, top = ring_origin(*self._flash_pos, self.size)
                return RingView(left, top, 1.0, True)
            self._flash_until = None
        t = dweller.last_t
        if t is None or dweller.last_pos is None or now - t > self.max_age:
            return None
        p = dweller.progress_at(t)
        if p < self.show_from:
            return None
        level = int(p * self.steps) / float(self.steps)
        left, top = ring_origin(*dweller.last_pos, self.size)
        return RingView(left, top, level, False)


class DwellRing:
    """The ring the driver ticks once per frame. Never raises.

    `window` is anything with create(size), exclude_from_capture() -> bool,
    draw(bgra), move(left, top) [shows it, topmost], hide(), flush(), pump()
    and destroy(). A fault in any of them disables the ring for the session
    and is logged once.
    """

    def __init__(self, dweller, size, window, log=print, model=None):
        self.size = int(size)
        self._d = dweller
        self._w = window
        self._log = log
        self._model = model or DwellRingModel(self.size)
        self.enabled = False
        self.capture_safe = False
        self._shown = False
        self._drawn = None
        self._closed = False
        try:
            window.create(self.size)
            self.enabled = True
        except Exception as exc:
            self._fail("olusturulamadi", exc, destroy=False)
            return
        try:
            self.capture_safe = bool(window.exclude_from_capture())
        except Exception:
            self.capture_safe = False

    def tick(self, now):
        if not self.enabled:
            return
        try:
            self._w.pump()
            v = self._model.view(self._d, now)
            if v is None:
                self._hide()
                return
            key = (v.progress, v.flash)
            if key != self._drawn:
                self._w.draw(render_ring(self.size, v.progress, v.flash))
                self._drawn = key
            self._w.move(v.left, v.top)
            self._shown = True
        except Exception as exc:
            self._fail("calisirken hata verdi", exc)

    def _hide(self):
        if self._shown:
            self._shown = False
            self._w.hide()

    def hide(self):
        """Take the ring off the screen now (calibration, drift measurement:
        the loop stops ticking there, and a frozen ring would sit on top of
        the dots). The next tick() shows it again if a dwell is building."""
        if not self.enabled:
            return
        try:
            self._hide()
        except Exception as exc:
            self._fail("gizlenemedi", exc)

    def prepare_capture(self):
        """Take the ring off the screen before a screenshot (the magnifier).

        Hidden even when excluded from capture: hiding is cheap, and the
        magnified image is what this user aims with. Without the exclusion
        DwmFlush() waits for DWM to actually compose the hidden frame."""
        if not self.enabled or not self._shown:
            return
        try:
            self._hide()
            if not self.capture_safe:
                self._w.flush()
        except Exception as exc:
            self._fail("gizlenemedi", exc)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.enabled:
            self.enabled = False
            try:
                self._w.destroy()
            except Exception:
                pass

    def _fail(self, what, exc, destroy=True):
        self.enabled = False
        self._shown = False
        # ascii(): the console is cp1254; a UnicodeEncodeError raised inside
        # this handler would escape the very guard it is part of.
        try:
            self._log("Dwell halkasi " + what + " - halka KAPATILDI, bakarak"
                      " tiklama aynen calisiyor. (" + type(exc).__name__ + ": "
                      + ascii(str(exc))[1:-1][:120] + ")")
        except Exception:
            pass
        if destroy and not self._closed:
            self._closed = True
            try:
                self._w.destroy()
            except Exception:
                pass


def _ring_size(cfg):
    try:
        v = int(round(float(str(cfg.get("dwell_ring_px", DEFAULT_PX)).replace(",", "."))))
    except (TypeError, ValueError):
        v = DEFAULT_PX
    return max(MIN_PX, min(MAX_PX, v))


def guard_capture(zoomer, ring):
    """Make zoomer.begin() hide the ring before it grabs the screen.

    Wraps the instance's bound method, so the magnifier class itself is not
    edited. A failing hide never stops the magnifier from opening."""
    orig = zoomer.begin

    def begin(*args, **kwargs):
        try:
            ring.prepare_capture()
        except Exception:
            pass
        return orig(*args, **kwargs)

    zoomer.begin = begin


def attach(cfg, dweller, zoomer=None, log=print, window=None):
    """The driver's one call: a ticking DwellRing, or None (dwell click off,
    ring switched off, or no window could be made - logged once)."""
    if dweller is None or not cfg.get("dwell_ring", True):
        return None
    size = _ring_size(cfg)
    try:
        win = window if window is not None else _system_window(log)
        if win is None:
            return None
        ring = DwellRing(dweller, size, win, log=log)
    except Exception as exc:          # noqa: BLE001 - never block startup
        try:
            log("Dwell halkasi acilamadi (" + type(exc).__name__ + ")."
                " Bakarak tiklama aynen calisiyor.")
        except Exception:
            pass
        return None
    if not ring.enabled:
        return None
    if zoomer is not None:
        guard_capture(zoomer, ring)
    try:
        log("  Dwell halkasi: acik (%d px)" % size)
    except Exception:
        pass
    return ring


def _system_window(log):
    """This system's ring window: Win32 on Windows, X11 on an X11 session;
    None (said once, here) where the session allows no overlay window."""
    from eye_gaze import platform
    if platform.OS == "windows":
        return Win32RingWindow()
    ok, why = platform.overlay_status("dwell_ring")
    if not ok:
        try:
            log("Dwell halkasi bu oturumda yok: " + platform.ascii_text(why)
                + ". Bakarak tiklama aynen calisiyor.")
        except Exception:
            pass
        return None
    return X11RingWindow()


# --- the real window ---------------------------------------------------------

_API = None

_WS_POPUP = 0x80000000
_WS_EX = (0x00080000      # LAYERED
          | 0x00000020    # TRANSPARENT: mouse goes straight through
          | 0x00000008    # TOPMOST
          | 0x00000080    # TOOLWINDOW: no taskbar button, no Alt+Tab
          | 0x08000000)   # NOACTIVATE: never takes the focus
_SWP = 0x0001 | 0x0010 | 0x0040      # NOSIZE | NOACTIVATE | SHOWWINDOW
_HWND_TOPMOST = -1
_WDA_EXCLUDEFROMCAPTURE = 0x11
_ULW_ALPHA = 0x2
_CLASS = "EyeGazeDwellRing"


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _BLEND(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte),
                ("AlphaFormat", ctypes.c_ubyte)]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BIH), ("bmiColors", ctypes.c_uint32 * 3)]


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p), ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p), ("hIconSm", ctypes.c_void_p)]


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", ctypes.c_uint32), ("pt", _POINT),
                ("lPrivate", ctypes.c_uint32)]


def _api():
    """{"user32", "gdi32", "kernel32", "dwmapi"} with every prototype declared,
    or None. Handles are 64-bit: an undeclared restype truncates them."""
    global _API
    if _API is not None:
        return _API or None
    try:
        vp, i, u_, b = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_int
        u = ctypes.WinDLL("user32", use_last_error=True)
        g = ctypes.WinDLL("gdi32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        k.GetModuleHandleW.restype = vp
        u.DefWindowProcW.argtypes = [vp, u_, ctypes.c_size_t, ctypes.c_ssize_t]
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.RegisterClassExW.argtypes = [ctypes.POINTER(_WNDCLASSEXW)]
        u.RegisterClassExW.restype = ctypes.c_ushort
        u.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p,
                                      ctypes.c_wchar_p, ctypes.c_uint32,
                                      i, i, i, i, vp, vp, vp, vp]
        u.CreateWindowExW.restype = vp
        u.DestroyWindow.argtypes = [vp]
        u.DestroyWindow.restype = b
        u.ShowWindow.argtypes = [vp, i]
        u.ShowWindow.restype = b
        u.IsWindowVisible.argtypes = [vp]
        u.IsWindowVisible.restype = b
        u.GetWindowLongW.argtypes = [vp, i]
        u.GetWindowLongW.restype = ctypes.c_long
        u.SetWindowPos.argtypes = [vp, vp, i, i, i, i, u_]
        u.SetWindowPos.restype = b
        u.UpdateLayeredWindow.argtypes = [vp, vp, ctypes.POINTER(_POINT),
                                          ctypes.POINTER(_SIZE), vp,
                                          ctypes.POINTER(_POINT), ctypes.c_uint32,
                                          ctypes.POINTER(_BLEND), ctypes.c_uint32]
        u.UpdateLayeredWindow.restype = b
        u.GetDC.argtypes = [vp]
        u.GetDC.restype = vp
        u.ReleaseDC.argtypes = [vp, vp]
        u.ReleaseDC.restype = i
        u.PeekMessageW.argtypes = [ctypes.POINTER(_MSG), vp, u_, u_, u_]
        u.PeekMessageW.restype = b
        u.TranslateMessage.argtypes = [ctypes.POINTER(_MSG)]
        u.TranslateMessage.restype = b
        u.DispatchMessageW.argtypes = [ctypes.POINTER(_MSG)]
        u.DispatchMessageW.restype = ctypes.c_ssize_t
        g.CreateCompatibleDC.argtypes = [vp]
        g.CreateCompatibleDC.restype = vp
        g.CreateDIBSection.argtypes = [vp, ctypes.POINTER(_BMI), u_,
                                       ctypes.POINTER(vp), vp, ctypes.c_uint32]
        g.CreateDIBSection.restype = vp
        g.SelectObject.argtypes = [vp, vp]
        g.SelectObject.restype = vp
        g.DeleteObject.argtypes = [vp]
        g.DeleteObject.restype = b
        g.DeleteDC.argtypes = [vp]
        g.DeleteDC.restype = b
        api = {"user32": u, "gdi32": g, "kernel32": k, "dwmapi": None}
        try:
            u.SetWindowDisplayAffinity.argtypes = [vp, ctypes.c_uint32]
            u.SetWindowDisplayAffinity.restype = b
        except AttributeError:             # older than Windows 7
            pass
        try:
            d = ctypes.WinDLL("dwmapi")
            d.DwmFlush.argtypes = []
            d.DwmFlush.restype = ctypes.c_long
            api["dwmapi"] = d
        except (OSError, AttributeError):
            pass
    except Exception:
        _API = {}
        return None
    _API = api
    return _API


class Win32RingWindow:
    """Layered, click-through, topmost, never-activated popup holding a
    premultiplied BGRA bitmap. Owned by the thread that creates it - the
    driver's main loop, which also pumps its messages (cv2.waitKey and pump())."""

    def __init__(self):
        self.hwnd = None
        self._size = 0
        self._mem = self._dib = self._old = None
        self._bits = None

    def create(self, size):
        api = _api()
        if api is None:
            raise OSError("user32/gdi32 yok")
        u, g, k = api["user32"], api["gdi32"], api["kernel32"]
        hinst = k.GetModuleHandleW(None)
        wc = _WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
        # DefWindowProcW itself, not a Python callback: nothing of ours runs
        # inside the message pump, so nothing of ours can throw there.
        wc.lpfnWndProc = ctypes.cast(u.DefWindowProcW, ctypes.c_void_p).value
        wc.hInstance = hinst
        wc.lpszClassName = _CLASS
        if not u.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:                  # ERROR_CLASS_ALREADY_EXISTS
                raise ctypes.WinError(err)
        s = int(size)
        hwnd = u.CreateWindowExW(_WS_EX, _CLASS, "Dwell halkasi", _WS_POPUP,
                                 -s * 4, -s * 4, s, s, None, None, hinst, None)
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = hwnd
        try:
            mem = g.CreateCompatibleDC(None)
            if not mem:
                raise ctypes.WinError(ctypes.get_last_error())
            self._mem = mem
            bmi = _BMI()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BIH)
            bmi.bmiHeader.biWidth = s
            bmi.bmiHeader.biHeight = -s          # top-down rows, like numpy
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bits = ctypes.c_void_p()
            dib = g.CreateDIBSection(mem, ctypes.byref(bmi), 0,
                                     ctypes.byref(bits), None, 0)
            if not dib or not bits.value:
                raise ctypes.WinError(ctypes.get_last_error())
            self._dib = dib
            self._bits = bits.value
            self._old = g.SelectObject(mem, dib)
            self._size = s
        except Exception:
            self.destroy()
            raise

    def exclude_from_capture(self):
        api = _api()
        fn = getattr(api["user32"], "SetWindowDisplayAffinity", None)
        if fn is None or not self.hwnd:
            return False
        return bool(fn(self.hwnd, _WDA_EXCLUDEFROMCAPTURE))

    def draw(self, bgra):
        s = self._size
        arr = np.ascontiguousarray(bgra, dtype=np.uint8)
        if arr.shape != (s, s, 4):
            raise ValueError("halka boyutu uyusmuyor")
        ctypes.memmove(self._bits, arr.ctypes.data, s * s * 4)
        u = _api()["user32"]
        blend = _BLEND(0, 0, 255, 1)            # AC_SRC_OVER, AC_SRC_ALPHA
        ok = u.UpdateLayeredWindow(self.hwnd, None, None,
                                   ctypes.byref(_SIZE(s, s)), self._mem,
                                   ctypes.byref(_POINT(0, 0)), 0,
                                   ctypes.byref(blend), _ULW_ALPHA)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def move(self, left, top):
        # HWND_TOPMOST every time: the magnifier and the AAC grid re-assert
        # their own topmost state each frame and would otherwise cover it.
        _api()["user32"].SetWindowPos(self.hwnd, ctypes.c_void_p(_HWND_TOPMOST),
                                      int(left), int(top), 0, 0, _SWP)

    def hide(self):
        if self.hwnd:
            _api()["user32"].ShowWindow(self.hwnd, 0)       # SW_HIDE

    def flush(self):
        d = _api()["dwmapi"]
        if d is not None:
            d.DwmFlush()

    def pump(self):
        if not self.hwnd:
            return
        u = _api()["user32"]
        msg = _MSG()
        for _ in range(16):
            if not u.PeekMessageW(ctypes.byref(msg), self.hwnd, 0, 0, 1):
                break                                        # PM_REMOVE
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

    def destroy(self):
        api = _api()
        if api is None:
            return
        u, g = api["user32"], api["gdi32"]
        try:
            if self._mem and self._old:
                g.SelectObject(self._mem, self._old)
            if self._dib:
                g.DeleteObject(self._dib)
            if self._mem:
                g.DeleteDC(self._mem)
        finally:
            self._mem = self._dib = self._old = self._bits = None
            if self.hwnd:
                hwnd, self.hwnd = self.hwnd, None
                u.DestroyWindow(hwnd)


# --- the X11 window (Linux) ----------------------------------------------------

# After the unmap before a screenshot: the server repaints a bare root at
# once, but the windows under the ring repaint themselves on Expose and a
# compositor recomposes on its next frame - both well inside this.
_X11_REPAINT_WAIT = 0.05
# python-xlib sends no BIG-REQUESTS: one PutImage must stay under the core
# protocol's 256 KiB, and a 400 px ring is 640 KB - it goes up in bands.
_X11_PUT_BYTES = 192 * 1024
# Without a compositor alpha is not blended: the window is cut to the pixels
# at least this opaque (the faint track is 110 across its middle).
_X11_SHAPE_ALPHA = 48
_X11_MAX_ERRORS = 8


def _mask_rects(mask):
    """The True pixels of a 2-D bool mask as one-row (x, y, w, h) runs."""
    rects = []
    for y in range(mask.shape[0]):
        row = mask[y]
        if not row.any():
            continue
        edges = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts = np.flatnonzero(edges == 1)
        ends = np.flatnonzero(edges == -1)
        for a, b in zip(starts, ends):
            rects.append((int(a), int(y), int(b - a), 1))
    return rects


def _x11_pixels(bgra, argb, msb_first):
    """ZPixmap bytes (32 bits per pixel) of the premultiplied BGRA ring.

    ARGB visual: the bytes as they are - a compositor expects premultiplied
    alpha. Opaque visual: alpha cannot be shown, so the colour is
    un-premultiplied and the shape mask decides what is drawn at all."""
    arr = np.ascontiguousarray(bgra, dtype=np.uint8)
    if not argb:
        a = arr[:, :, 3:4].astype(np.float32)
        rgb = np.where(a > 0, np.round(arr[:, :, :3] * 255.0
                                       / np.maximum(a, 1.0)), 0.0)
        out = np.empty_like(arr)
        out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        out[:, :, 3] = 255
        arr = out
    if msb_first:
        arr = arr[:, :, ::-1]                    # B G R A -> A R G B
    return np.ascontiguousarray(arr).tobytes()


class X11RingWindow:
    """The ring as an X11 window, behind the same interface as
    Win32RingWindow (create / exclude_from_capture / draw / move / hide /
    flush / pump / destroy).

    Override-redirect: the window manager never manages it, so it is never
    focused, decorated or listed in a taskbar. The INPUT shape is empty, so
    every click and hover goes through to the target under it. It is raised
    (stack_mode Above) on every move, as the Win32 one re-asserts
    HWND_TOPMOST: the magnifier and the AAC grid raise themselves too.

    With a compositor (someone owns _NET_WM_CM_S<screen>) the window has a
    32-bit ARGB visual and the ring is alpha-blended. Without one alpha would
    be drawn as a black square: the window then takes the root's visual and
    its BOUNDING shape is cut to the ring band (the faint track turns
    opaque, the middle stays empty).

    X11 cannot keep a window out of a screenshot: exclude_from_capture() is
    False, so DwellRing.prepare_capture() unmaps it and calls flush() - a
    round trip, then a short wait for what was under it to repaint.

    Its own Display connection, used only by the thread that ticks the ring
    (the gaze loop): Xlib must not be shared between threads, and this never
    touches the input backend or its lock. X errors are collected by the
    connection's error handler and raised from the next call, so DwellRing
    turns the ring off and says so once. The window and its colormap die
    with the connection, so destroy() cannot leak them on the server.
    """

    def __init__(self, display_name=None):
        self._display_name = display_name or os.environ.get("DISPLAY")
        self._d = None
        self.window = None
        self._gc = None
        self._cmap = None
        self._size = 0
        self._depth = 0
        self.argb = False
        self._msb = False
        self._mask = None
        self._pixels = None
        self._mapped = False
        self._errors = []

    def create(self, size):
        from Xlib import X, display
        from Xlib.ext import shape
        s = int(size)
        d = display.Display(self._display_name)
        self._d = d
        try:
            d.set_error_handler(self._on_error)
            if not d.has_extension("SHAPE"):
                raise OSError("X sunucusunda SHAPE eklentisi yok")
            v = d.shape_query_version()
            if (v.major_version, v.minor_version) < (1, 1):
                # The input shape (click-through) came with SHAPE 1.1.
                raise OSError("X sunucusunun SHAPE surumu eski (%d.%d)"
                              % (v.major_version, v.minor_version))
            screen = d.screen()
            root = screen.root
            visual = None
            if self._composited(d):
                visual = self._true_color(d, screen, 32, None)
            if visual is not None:
                self._cmap = root.create_colormap(visual.visual_id,
                                                  X.AllocNone)
                self.argb, self._depth = True, 32
                win = root.create_window(
                    -4 * s, -4 * s, s, s, 0, 32, X.InputOutput,
                    visual.visual_id, background_pixel=0, border_pixel=0,
                    colormap=self._cmap, override_redirect=True,
                    event_mask=X.ExposureMask)
            else:
                if self._true_color(d, screen, screen.root_depth,
                                    screen.root_visual) is None:
                    raise OSError("desteklenmeyen ekran renk bicimi "
                                  "(derinlik %d)" % screen.root_depth)
                self._depth = screen.root_depth
                win = root.create_window(
                    -4 * s, -4 * s, s, s, 0, self._depth, X.InputOutput,
                    X.CopyFromParent, background_pixel=screen.black_pixel,
                    border_pixel=0, override_redirect=True,
                    event_mask=X.ExposureMask)
            self.window = win
            win.set_wm_name("Dwell halkasi")
            win.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted,
                                 0, 0, [])
            if not self.argb:
                # Nothing visible until the first draw() cuts the band.
                win.shape_rectangles(shape.SO.Set, shape.SK.Bounding,
                                     X.Unsorted, 0, 0, [])
            self._gc = win.create_gc()
            self._msb = d.display.info.image_byte_order == X.MSBFirst
            self._size = s
            d.sync()
            self._check()
        except Exception:
            self.destroy()
            raise

    @staticmethod
    def _composited(d):
        atom = d.intern_atom("_NET_WM_CM_S%d" % d.get_default_screen())
        owner = d.get_selection_owner(atom)
        return bool(getattr(owner, "id", owner))

    @staticmethod
    def _true_color(d, screen, depth, visual_id):
        """A TrueColor visual of `depth` with 8-bit R, G, B at the usual
        places and 32 bits per pixel in images, or None."""
        from Xlib import X
        bpp = {f.depth: f.bits_per_pixel
               for f in d.display.info.pixmap_formats}
        if bpp.get(depth) != 32:
            return None
        for dep in screen.allowed_depths:
            if dep.depth != depth:
                continue
            for vis in dep.visuals:
                if visual_id is not None and vis.visual_id != visual_id:
                    continue
                if (vis.visual_class == X.TrueColor
                        and vis.red_mask == 0xff0000
                        and vis.green_mask == 0x00ff00
                        and vis.blue_mask == 0x0000ff):
                    return vis
        return None

    def exclude_from_capture(self):
        return False

    def draw(self, bgra):
        s = self._size
        arr = np.ascontiguousarray(bgra, dtype=np.uint8)
        if arr.shape != (s, s, 4):
            raise ValueError("halka boyutu uyusmuyor")
        if not self.argb:
            mask = arr[:, :, 3] >= _X11_SHAPE_ALPHA
            if self._mask is None or not np.array_equal(mask, self._mask):
                from Xlib import X
                from Xlib.ext import shape
                self.window.shape_rectangles(shape.SO.Set, shape.SK.Bounding,
                                             X.Unsorted, 0, 0,
                                             _mask_rects(mask))
                self._mask = mask
        self._pixels = _x11_pixels(arr, self.argb, self._msb)
        if self._mapped:
            self._put()
            self._d.flush()
        self._check()

    def _put(self):
        from Xlib import X
        s = self._size
        row = s * 4
        rows = max(1, _X11_PUT_BYTES // row)
        for y in range(0, s, rows):
            n = min(rows, s - y)
            self.window.put_image(self._gc, 0, y, s, n, X.ZPixmap,
                                  self._depth, 0,
                                  self._pixels[y * row:(y + n) * row])

    def move(self, left, top):
        from Xlib import X
        self.window.configure(x=int(left), y=int(top), stack_mode=X.Above)
        if not self._mapped:
            self.window.map()
            self._mapped = True
            # An unmapped window keeps no pixels: draw them again.
            if self._pixels is not None:
                self._put()
        self._d.flush()
        self._check()

    def hide(self):
        if self._mapped and self.window is not None:
            self.window.unmap()
            self._mapped = False
            self._d.flush()

    def flush(self):
        """Wait until the unmap is done and what was under it is redrawn."""
        if self._d is None:
            return
        self._d.sync()
        self._check()
        time.sleep(_X11_REPAINT_WAIT)

    def pump(self):
        """Read this connection's events; repaint after an Expose (X keeps
        no copy of a window that was covered and uncovered)."""
        if self._d is None:
            return
        from Xlib import X
        exposed = False
        for _ in range(64):
            if not self._d.pending_events():
                break
            if self._d.next_event().type == X.Expose:
                exposed = True
        if exposed and self._mapped and self._pixels is not None:
            self._put()
            self._d.flush()
        self._check()

    def _on_error(self, err, request=None):
        if len(self._errors) < _X11_MAX_ERRORS:
            self._errors.append(err)

    def _check(self):
        if self._errors:
            err = self._errors[0]
            self._errors = []
            raise OSError("X11 hatasi: %s" % type(err).__name__)

    def destroy(self):
        d, self._d = self._d, None
        if d is None:
            return
        try:
            for fn in (getattr(self._gc, "free", None),
                       getattr(self.window, "destroy", None),
                       getattr(self._cmap, "free", None)):
                if fn is not None:
                    try:
                        fn()
                    except Exception:
                        pass
        finally:
            self.window = self._gc = self._cmap = None
            self._mapped = False
            self._pixels = self._mask = None
            try:
                d.close()
            except Exception:
                pass
