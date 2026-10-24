# gaze/eye_gaze/physical.py
"""Screen scale from the monitor's physical size.

Every pixel constant of the cursor chain (deadzone 20 px, dwell radius 40 px,
max speed 1200 px/s, ...) was tuned on one kind of screen: about 0.233 mm per
logical pixel (a 27" 2560x1440, or a 27" 4K at 150 %). On a 4K 27" at 100 %
the same 20 px is a third smaller on the glass; on a 27" 1080p it is a third
larger. The factor here is REF_MM_PER_PX / (this screen's mm per logical
pixel): the pixel constants are multiplied by it, so they keep their size in
millimetres - which is what the eye's jitter and the user's aim are measured
in.

Logical pixels, because this process is DPI-unaware (and must stay so): the
cursor, the calibration and every constant live in the coordinates Windows
hands a DPI-unaware process. The physical size is the panel's own, so a 4K
panel at 150 % comes out the same as the 2560x1440 reference.

Where the size comes from:
  Windows - the primary monitor's EDID (registry, found through
            EnumDisplayDevices' device interface name), else GetDeviceCaps
            HORZSIZE/VERTSIZE of the screen DC;
  Linux   - XRandR's millimetres for the primary monitor (X11, or XWayland
            under the evdev backend); nothing on a pure Wayland session;
  other   - nothing.
A size that is missing or implausible (no EDID, a projector's 0x0, an EDID
that stores the aspect ratio "16x9 cm", a panel that does not match the
screen's shape) means factor 1.0.

The factor is clamped to CLAMP, and inside BAND it is exactly 1.0: common
desktop screens (21.5-24" 1080p at 0.84-0.94, 27-32" 1440p, laptops at their
usual Windows scaling) keep the v1.2.0 numbers bit for bit, and only a screen
physically far from the reference is scaled (27" 1080p 0.75, 4K at 100 %
1.26-1.50, 13-15" 1080p at 100 % 1.3-1.5). The mapping outlier
floor (eye_gaze.mapping, 60 px) is NOT scaled: it is part of every fit, and
scaling it would change the refit of an existing calibration.

scaled_config() is the one place the rule is applied. Never raises.
"""
import collections
import math
import os
import sys

REF_MM_PER_PX = 0.2331          # 27" 2560x1440: 596.7 mm / 2560 px
CLAMP = (0.6, 1.6)
BAND = (0.80, 1.25)             # inside: exactly 1.0

# A monitor's diagonal outside this is a wrong reading, not a monitor.
_MIN_DIAG_MM = 250.0            # ~10"
_MAX_DIAG_MM = 2600.0           # ~100"
_ASPECT_TOL = 0.15              # panel shape vs screen shape

ScreenScale = collections.namedtuple(
    "ScreenScale", "factor raw mm_per_px mm px source applied")

# Settings in logical pixels (or px/s) and the driver's own fallback when a
# line is missing. beta is per (px/s), so it is DIVIDED by the factor.
SCALED = {
    "deadzone": 20.0,
    "max_cursor_speed": 0.0,
    "dwell_radius": 40.0,
    "snap_radius": 120.0,
    "dwell_ring_px": 40,
    "implicit_max_px": 100.0,
    "pursuit_speed": 480.0,
    "saccade_velocity_px_s": 1500.0,
    "saccade_min_px": 120.0,
    "beta": 0.007,
}
_INVERSE = frozenset(["beta"])
_INTEGER = frozenset(["dwell_ring_px"])


# --- the rule --------------------------------------------------------------------

def apply_band(raw):
    """`raw` when the screen is far from the reference, else exactly 1.0."""
    return 1.0 if BAND[0] <= raw <= BAND[1] else raw


def scale_px(value, factor, inverse=False):
    """One pixel constant at `factor` (a per-pixel one when `inverse`)."""
    return value / factor if inverse else value * factor


def _finite(value):
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def scaled_config(cfg, factor):
    """`cfg` with every pixel constant scaled by `factor` (a copy).

    factor 1.0 returns `cfg` itself, untouched. A value that is not a usable
    number is left as it is: its own module's fallback deals with it.
    """
    if factor == 1.0:
        return cfg
    out = dict(cfg)
    for key, default in SCALED.items():
        v = _finite(out.get(key, default))
        if v is None:
            continue
        v = scale_px(v, factor, key in _INVERSE)
        out[key] = int(round(v)) if key in _INTEGER else v
    return out


# --- EDID ------------------------------------------------------------------------

_EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


def parse_edid(data):
    """(width_mm, height_mm) from an EDID block, or None.

    The first detailed timing descriptor carries the size in mm; the basic
    parameters (bytes 21/22) in cm are the fallback."""
    try:
        b = bytes(data)
    except Exception:
        return None
    if len(b) < 128 or b[:8] != _EDID_HEADER or sum(b[:128]) % 256:
        return None
    if b[54] or b[55]:                  # pixel clock: a timing descriptor
        w = b[66] | ((b[68] & 0xF0) << 4)
        h = b[67] | ((b[68] & 0x0F) << 8)
        if w > 0 and h > 0:
            return (w, h)
    if b[21] and b[22]:
        return (b[21] * 10, b[22] * 10)
    return None


# --- Windows -----------------------------------------------------------------------

def edid_key(device_id):
    r"""HKLM path holding the EDID of a monitor, from its device interface
    name (\\?\DISPLAY#<model>#<instance>#{guid}), or None."""
    try:
        parts = str(device_id).split("#")
    except Exception:
        return None
    if len(parts) < 3 or not parts[0].upper().endswith("DISPLAY"):
        return None
    model, inst = parts[1], parts[2]
    if not model or not inst:
        return None
    return ("SYSTEM\\CurrentControlSet\\Enum\\DISPLAY\\%s\\%s\\Device Parameters"
            % (model, inst))


def _primary_monitor_ids():
    """Device interface names of the monitors on the primary display."""
    import ctypes
    from ctypes import wintypes

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD),
                    ("DeviceName", wintypes.WCHAR * 32),
                    ("DeviceString", wintypes.WCHAR * 128),
                    ("StateFlags", wintypes.DWORD),
                    ("DeviceID", wintypes.WCHAR * 128),
                    ("DeviceKey", wintypes.WCHAR * 128)]

    user32 = ctypes.WinDLL("user32")
    enum = user32.EnumDisplayDevicesW
    enum.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                     ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD]
    enum.restype = wintypes.BOOL
    PRIMARY, ACTIVE, INTERFACE_NAME = 0x4, 0x1, 0x1
    ids = []
    i = 0
    while i < 32:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        if not enum(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        if not dd.StateFlags & PRIMARY:
            continue
        for j in range(8):
            mon = DISPLAY_DEVICEW()
            mon.cb = ctypes.sizeof(DISPLAY_DEVICEW)
            if not enum(dd.DeviceName, j, ctypes.byref(mon), INTERFACE_NAME):
                break
            if mon.StateFlags & ACTIVE and mon.DeviceID:
                ids.append(mon.DeviceID)
        break
    return ids


def _registry_edid(key):
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
        value, _kind = winreg.QueryValueEx(k, "EDID")
    return value


def _device_caps_mm():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32")
    gdi32 = ctypes.WinDLL("gdi32")
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.GetDeviceCaps.restype = ctypes.c_int
    hdc = user32.GetDC(None)
    if not hdc:
        return None
    try:
        HORZSIZE, VERTSIZE = 4, 6
        return (gdi32.GetDeviceCaps(hdc, HORZSIZE),
                gdi32.GetDeviceCaps(hdc, VERTSIZE))
    finally:
        user32.ReleaseDC(None, hdc)


def windows_candidates(monitor_ids=None, read_edid=None, device_caps=None):
    """("EDID" | "GetDeviceCaps", (w_mm, h_mm)) readings, best first.
    Each part is injectable; a part that fails is skipped."""
    monitor_ids = monitor_ids or _primary_monitor_ids
    read_edid = read_edid or _registry_edid
    device_caps = device_caps or _device_caps_mm
    try:
        ids = list(monitor_ids())
    except Exception:
        ids = []
    for dev in ids:
        key = edid_key(dev)
        if key is None:
            continue
        try:
            mm = parse_edid(read_edid(key))
        except Exception:
            mm = None
        if mm is not None:
            yield ("EDID", mm)
    try:
        mm = device_caps()
    except Exception:
        mm = None
    if mm is not None:
        yield ("GetDeviceCaps", tuple(mm))


# --- Linux ---------------------------------------------------------------------------

def linux_candidates(backend=None):
    """("XRandR", (w_mm, h_mm)) from the platform backend, if it knows."""
    try:
        if backend is None:
            from eye_gaze import platform
            backend = platform.linux_backend()
        fn = getattr(backend, "primary_mm", None)
        mm = fn() if fn is not None else None
    except Exception:
        mm = None
    if mm is not None:
        yield ("XRandR", tuple(mm))


# --- detection ---------------------------------------------------------------------

def _os():
    if os.name == "nt":
        return "windows"
    return sys.platform


def default_candidates():
    name = _os()
    if name == "windows":
        return windows_candidates()
    if name.startswith("linux"):
        return linux_candidates()
    return iter(())


def _plausible(mm, px):
    """(w_mm, h_mm) matched to the screen's orientation, or None."""
    try:
        w, h = float(mm[0]), float(mm[1])
        pw, ph = float(px[0]), float(px[1])
    except Exception:
        return None
    if not all(math.isfinite(v) and v > 0 for v in (w, h, pw, ph)):
        return None
    if not _MIN_DIAG_MM <= math.hypot(w, h) <= _MAX_DIAG_MM:
        return None
    shape = pw / ph
    for a, b in ((w, h), (h, w)):           # a rotated panel reports landscape
        if abs(math.log((a / b) / shape)) <= math.log(1.0 + _ASPECT_TOL):
            return (a, b)
    return None


def detect(px, candidates=None):
    """ScreenScale for a primary screen of `px` = (w, h) logical pixels.
    Never raises; factor 1.0 whenever the size is unknown or implausible."""
    try:
        px = (int(px[0]), int(px[1]))
    except Exception:
        px = (0, 0)
    none = ScreenScale(1.0, None, None, None, px, None, False)
    if px[0] <= 0 or px[1] <= 0:
        return none
    try:
        if candidates is None:
            candidates = default_candidates()
        for source, mm in candidates:
            fit = _plausible(mm, px)
            if fit is None:
                continue
            mmpp = math.hypot(*fit) / math.hypot(*px)
            raw = min(CLAMP[1], max(CLAMP[0], REF_MM_PER_PX / mmpp))
            factor = apply_band(raw)
            return ScreenScale(factor, raw, mmpp, fit, px, source,
                               factor != 1.0)
    except Exception:
        pass
    return none


def describe(scale):
    """One ASCII console line: the factor and whether it was applied."""
    try:
        if scale.source is None:
            return ("Ekran olcegi: fiziksel boyut bilinmiyor - piksel ayarlari"
                    " oldugu gibi (x1.00).")
        head = ("Ekran olcegi: %dx%d mm (%s), %.4f mm/piksel, oran x%.2f"
                % (round(scale.mm[0]), round(scale.mm[1]), scale.source,
                   scale.mm_per_px, scale.raw))
        if scale.applied:
            return head + " - UYGULANDI (piksel ayarlari x%.2f)." % scale.factor
        return (head + " - UYGULANMADI (referansa yakin %.2f-%.2f;"
                " piksel ayarlari oldugu gibi)." % BAND)
    except Exception:
        return "Ekran olcegi: x1.00."
