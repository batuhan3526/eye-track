# gaze/tests/test_overlays_linux.py
"""The magnifier capture and the dwell ring on Linux.

Two halves:
  * policy, runnable anywhere (Windows included): Wayland switches both off
    with a reason that is said, not swallowed; the cursor uses the X11
    monitor list with its real origins; the Windows paths are untouched.
  * the real thing against Xvfb (Docker: packaging/linux/test.sh): mss
    screenshots and the override-redirect ring window - including the rule
    the Windows ring keeps with WDA_EXCLUDEFROMCAPTURE: the ring never shows
    up in the magnified image.
"""
import importlib.util
import os
import sys
import time
import types

import numpy as np
import pytest

from eye_gaze import dwell_ring as dr
from eye_gaze import platform as pf
from eye_gaze import zoom
from eye_gaze.dwell import DwellClicker


@pytest.fixture
def linux(monkeypatch):
    """A Linux process with a fresh backend choice and no session yet."""
    monkeypatch.setattr(pf, "OS", "linux")
    for var in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pf, "_backend", [None])
    yield monkeypatch


@pytest.fixture
def wayland(linux):
    linux.setenv("XDG_SESSION_TYPE", "wayland")
    linux.setenv("WAYLAND_DISPLAY", "wayland-0")
    # XWayland: a DISPLAY exists, but a screenshot through it sees only the
    # X clients, and an X window cannot stay above the Wayland ones.
    linux.setenv("DISPLAY", ":0")
    yield linux


def _recording_mss(monkeypatch):
    used = []
    fake = types.ModuleType("mss")
    fake.MSS = lambda *a, **k: used.append(("MSS", a, k))
    fake.mss = lambda *a, **k: used.append(("mss", a, k))
    monkeypatch.setitem(sys.modules, "mss", fake)
    return used


def _x11_session(monkeypatch):
    """An X11 session whose libraries count as installed (python-xlib is
    not on the Windows test machine)."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(pf, "_importable", lambda module: True)


def _dweller():
    return DwellClicker(radius_px=40, dwell_seconds=1.0)


# --- Wayland: off, and said --------------------------------------------------

def test_wayland_zoom_disabled_with_reason(wayland):
    used = _recording_mss(wayland)
    assert zoom.grab_region(0, 0, 64, 48) is None
    assert used == []                       # not even tried through XWayland
    ok, why = pf.overlay_status("zoom_capture")
    assert ok is False
    assert "Wayland" in why and "Xorg" in why
    # What capabilities() reports is what grab_region decided on.
    assert pf._desktop_caps("wayland")["zoom_capture"] == (ok, why)


def test_f6_on_wayland_says_why_in_ascii(wayland, capsys):
    import gaze_driver as gd
    wayland.setattr(gd.cursor, "get_cursor", lambda: (100, 100))
    z = gd._ZoomClick(800, 600)
    z.begin()
    out = capsys.readouterr().out
    assert "Buyutec: ekran goruntusu alinamadi." in out
    assert "Wayland" in out and "Xorg" in out
    out.encode("ascii")                     # cp1254 console
    assert not z.active


def test_driver_says_once_at_start_that_f6_is_unavailable(wayland):
    import gaze_driver as gd
    line = gd._zoom_notice("f6")
    assert line is not None
    assert "F6" in line and "Wayland" in line
    line.encode("ascii")
    assert gd._zoom_notice("") is None      # no magnifier key: nothing to say


def test_driver_start_notice_is_silent_when_capture_works(linux):
    import gaze_driver as gd
    _x11_session(linux)
    assert gd._zoom_notice("f6") is None


def test_driver_start_notice_is_silent_on_windows(monkeypatch):
    import gaze_driver as gd
    monkeypatch.setattr(pf, "OS", "windows")
    assert gd._zoom_notice("f6") is None


def test_ring_attach_none_on_wayland(wayland):
    made = []
    wayland.setattr(dr, "X11RingWindow", lambda *a, **k: made.append(1))
    logs = []
    assert dr.attach({"dwell_ring": True}, _dweller(), log=logs.append) is None
    assert made == []
    assert len(logs) == 1
    assert "Xorg" in logs[0]
    logs[0].encode("ascii")
    ok, why = pf.overlay_status("dwell_ring")
    assert ok is False
    assert pf._desktop_caps("wayland")["dwell_ring"] == (ok, why)


def test_ring_attach_on_wayland_is_quiet_when_dwell_is_off(wayland):
    logs = []
    assert dr.attach({"dwell_ring": True}, None, log=logs.append) is None
    assert dr.attach({"dwell_ring": False}, _dweller(),
                     log=logs.append) is None
    assert logs == []                       # nothing was asked for


def test_attach_on_x11_makes_the_x11_window(linux):
    _x11_session(linux)
    made = []

    class Fake:
        def __init__(self):
            made.append(self)

        def create(self, size):
            pass

        def exclude_from_capture(self):
            return False
    linux.setattr(dr, "X11RingWindow", Fake)
    ring = dr.attach({"dwell_ring": True}, _dweller(), log=lambda s: None)
    assert ring is not None and len(made) == 1
    assert ring.capture_safe is False       # hidden AND flushed before a grab


def test_attach_on_windows_still_makes_the_win32_window(monkeypatch):
    monkeypatch.setattr(pf, "OS", "windows")
    made = []

    class Fake:
        def __init__(self):
            made.append(self)

        def create(self, size):
            pass

        def exclude_from_capture(self):
            return True
    monkeypatch.setattr(dr, "Win32RingWindow", Fake)
    monkeypatch.setattr(dr, "X11RingWindow",
                        lambda: pytest.fail("X11 window on Windows"))
    assert dr.attach({"dwell_ring": True}, _dweller(),
                     log=lambda s: None) is not None
    assert len(made) == 1


def test_x11_without_mss_says_the_magnifier_is_off(linux):
    linux.setenv("XDG_SESSION_TYPE", "x11")
    linux.setenv("DISPLAY", ":0")
    linux.setitem(sys.modules, "mss", None)  # not importable
    assert zoom.grab_region(0, 0, 64, 48) is None
    ok, why = pf.overlay_status("zoom_capture")
    assert ok is False and "mss" in why


def test_no_session_turns_both_overlays_off(linux):
    for key in ("zoom_capture", "dwell_ring"):
        ok, why = pf.overlay_status(key)
        assert ok is False and why


def test_screen_scale_is_one_on_linux(linux):
    # X11 coordinates are physical pixels: no DPI virtualisation to undo.
    linux.setattr(zoom, "_gdi", lambda: pytest.fail("GDI on Linux"))
    assert zoom.screen_scale() == 1.0


def test_linux_grab_region_is_not_rescaled(linux):
    _x11_session(linux)
    asked = []

    class Shot:
        width, height = 64, 48
        bgra = bytes(64 * 48 * 4)

    class Sct:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, mon):
            asked.append(mon)
            return Shot()
    fake = types.ModuleType("mss")
    fake.MSS = Sct
    linux.setitem(sys.modules, "mss", fake)
    img = zoom.grab_region(10, 20, 64, 48)
    assert asked == [{"left": 10, "top": 20, "width": 64, "height": 48}]
    assert img.shape == (48, 64, 3)


def test_a_failing_x11_grab_says_why_once(linux, capsys):
    _x11_session(linux)
    linux.setattr(zoom, "_X11_GRAB_SAID", [False])

    def broken(*a, **k):
        raise OSError("Library libxcb-render.so not found")
    fake = types.ModuleType("mss")
    fake.MSS = broken
    linux.setitem(sys.modules, "mss", fake)
    assert zoom.grab_region(0, 0, 64, 48) is None
    assert zoom.grab_region(0, 0, 64, 48) is None
    out = capsys.readouterr().out
    assert out.count("libxcb-render") == 1
    out.encode("ascii")


# --- cursor: the X11 monitors, origins included ------------------------------

class _TwoMonitors:
    """X11 primary on the RIGHT at (1920, 0); a second monitor left of it,
    lower by 180 px. The primary is not at (0, 0)."""

    def __init__(self):
        self.moves = []

    def primary(self):
        return (1920, 0, 2560, 1440)

    def screens(self):
        return [(1920, 0, 2560, 1440), (0, 180, 1920, 1080)]

    def set_cursor(self, x, y):
        self.moves.append((x, y))


@pytest.fixture
def two_monitors(monkeypatch):
    from eye_gaze import cursor
    fake = _TwoMonitors()
    monkeypatch.setattr(cursor, "_user32", None)
    monkeypatch.setattr(pf, "linux_backend", lambda: fake)
    return fake


def test_linux_monitor_rect_uses_the_x11_monitors(two_monitors):
    from eye_gaze import cursor
    assert cursor.monitor_rect(100, 500) == (0, 180, 1920, 1080)
    assert cursor.monitor_rect(3000, 700) == (1920, 0, 2560, 1440)
    assert cursor.monitor_rect(1920, 0) == (1920, 0, 2560, 1440)
    # Above the left monitor (y < 180): the nearest one, not the primary.
    assert cursor.monitor_rect(100, 50) == (0, 180, 1920, 1080)
    assert cursor.monitor_rect(9000, 9000) == (1920, 0, 2560, 1440)


def test_linux_primary_clamp_keeps_the_primary_origin(two_monitors):
    from eye_gaze import cursor
    assert cursor.screen_size() == (2560, 1440)
    assert cursor.set_cursor(0, 0) == (1920, 0)
    assert cursor.set_cursor(99999, 99999) == (4479, 1439)
    assert cursor.set_cursor(2000, 300) == (2000, 300)
    assert cursor.set_cursor(100, 500, virtual=True) == (100, 500)
    assert cursor.virtual_screen() == (0, 0, 4480, 1440)
    assert two_monitors.moves == [(1920, 0), (4479, 1439), (2000, 300),
                                  (100, 500)]


def test_windows_primary_clamp_is_unchanged(monkeypatch):
    from eye_gaze import cursor
    moves = []

    class U:
        def GetSystemMetrics(self, i):
            return {0: 1707, 1: 960}[i]

        def SetCursorPos(self, x, y):
            moves.append((x, y))
    monkeypatch.setattr(cursor, "_user32", U())
    monkeypatch.setattr(pf, "linux_backend",
                        lambda: pytest.fail("Linux backend on Windows"))
    assert cursor.set_cursor(-5, 5000) == (0, 959)
    assert moves == [(0, 959)]


# --- the real thing: Xvfb ----------------------------------------------------

def _have(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


xvfb = pytest.mark.skipif(
    os.name == "nt" or not os.environ.get("DISPLAY")
    or not (_have("Xlib") and _have("mss")),
    reason="needs Linux with an X server, python-xlib and mss "
           "(Docker: packaging/linux/test.sh)")


@pytest.fixture
def x11(monkeypatch):
    """An observer connection to the test's X server (Xvfb)."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    from Xlib import display
    d = display.Display()
    yield d
    d.close()


def _solid(d, x, y, w, h, pixel):
    from Xlib import X
    s = d.screen()
    win = s.root.create_window(x, y, w, h, 0, s.root_depth, X.InputOutput,
                               X.CopyFromParent, background_pixel=pixel,
                               override_redirect=True)
    win.map()
    d.sync()
    return win


def _shown_ring(cx=400, cy=300, size=40, logs=None):
    d = _dweller()
    d.update(cx, cy, 0.0)
    d.update(cx, cy, 0.5)
    w = dr.X11RingWindow()
    ring = dr.DwellRing(d, size, w,
                        log=(logs.append if logs is not None else print))
    assert ring.enabled
    ring.tick(0.5)
    w._d.sync()
    return ring, w


def _arc_pixels(img):
    """How many pixels are the ring's yellow arc (BGR 0, 200, 255)."""
    diff = np.abs(img.astype(int) - np.array([0, 200, 255]))
    return int(np.count_nonzero(diff.max(axis=2) <= 4))


def _id(obj):
    return getattr(obj, "id", obj)


@xvfb
def test_grab_region_returns_bgr_of_requested_size(x11):
    win = _solid(x11, 100, 120, 80, 60, 0xFF0000)    # red, 24-bit TrueColor
    try:
        img = zoom.grab_region(90, 110, 64, 48)
        assert img is not None
        assert img.shape == (48, 64, 3) and img.dtype == np.uint8
        assert tuple(int(v) for v in img[30, 30]) == (0, 0, 255)  # BGR
        assert tuple(int(v) for v in img[0, 0]) != (0, 0, 255)    # outside
    finally:
        win.destroy()
        x11.sync()


@xvfb
def test_ring_window_maps_and_unmaps(x11):
    from Xlib import X, error
    from Xlib.ext import shape
    focus_before = _id(x11.get_input_focus().focus)
    ring, w = _shown_ring(400, 300, 40)
    try:
        obs = x11.create_resource_object("window", w.window.id)
        attrs = obs.get_attributes()
        assert attrs.map_state == X.IsViewable
        assert attrs.override_redirect         # never managed, never focused
        geo = obs.get_geometry()
        assert (geo.x, geo.y, geo.width, geo.height) == (380, 280, 40, 40)
        # Click-through: the input region is empty.
        assert list(obs.shape_get_rectangles(shape.SK.Input).rectangles) == []
        assert _id(x11.get_input_focus().focus) == focus_before
        ring.hide()
        w._d.sync()
        assert obs.get_attributes().map_state == X.IsUnmapped
    finally:
        ring.close()
    assert w.window is None and w._d is None
    with pytest.raises(error.BadWindow):
        obs.get_attributes()


@xvfb
def test_ring_without_compositor_is_cut_to_the_band(x11):
    from Xlib.ext import shape
    ring, w = _shown_ring(400, 300, 40)
    try:
        assert w.argb is False                 # Xvfb runs no compositor
        obs = x11.create_resource_object("window", w.window.id)
        rects = obs.shape_get_rectangles(shape.SK.Bounding).rectangles
        covered = np.zeros((40, 40), bool)
        for r in rects:
            covered[r.y:r.y + r.height, r.x:r.x + r.width] = True
        assert not covered[20, 20]             # the target stays visible
        assert covered[2:6, 18:22].any()       # the band at 12 o'clock
    finally:
        ring.close()


@xvfb
def test_ring_stays_on_top(x11):
    ring, w = _shown_ring(400, 300, 40)
    other = _solid(x11, 350, 250, 100, 100, 0x0000FF)
    try:
        top = x11.screen().root.query_tree().children[-1]
        assert top.id == other.id               # mapped later: above us
        ring.tick(0.6)
        w._d.sync()
        top = x11.screen().root.query_tree().children[-1]
        assert top.id == w.window.id
    finally:
        other.destroy()
        x11.sync()
        ring.close()


@xvfb
def test_ring_never_in_magnifier_capture(x11):
    region = (360, 260, 80, 80)
    before = zoom.grab_region(*region)
    ring, w = _shown_ring(400, 300, 40)
    try:
        shown = zoom.grab_region(*region)
        assert _arc_pixels(shown) > 20          # it IS on the screen
        assert _arc_pixels(before) == 0
        # The middle of the ring is left alone (the target under it).
        assert tuple(shown[40, 40]) == tuple(before[40, 40])

        class Magnifier:
            img = None

            def begin(self):
                self.img = zoom.grab_region(*region)
        z = Magnifier()
        dr.guard_capture(z, ring)
        z.begin()
        assert _arc_pixels(z.img) == 0
        assert np.array_equal(z.img, before)
    finally:
        ring.close()


@xvfb
def test_ring_repaints_after_being_covered(x11):
    region = (360, 260, 80, 80)
    ring, w = _shown_ring(400, 300, 40)
    try:
        cover = _solid(x11, 300, 200, 200, 200, 0x00FF00)
        cover.destroy()
        x11.sync()
        deadline = time.monotonic() + 2.0
        img = zoom.grab_region(*region)
        while _arc_pixels(img) <= 20 and time.monotonic() < deadline:
            ring.tick(0.6)                      # pump() answers the Expose
            w._d.sync()
            img = zoom.grab_region(*region)
        assert _arc_pixels(img) > 20
    finally:
        ring.close()


@xvfb
def test_ring_uses_an_argb_window_under_a_compositor(x11):
    from Xlib import X
    from Xlib.ext import shape
    # Pose as a compositing manager: own _NET_WM_CM_S<screen>.
    atom = x11.intern_atom("_NET_WM_CM_S%d" % x11.get_default_screen())
    owner = x11.screen().root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
    owner.set_selection_owner(atom, X.CurrentTime)
    x11.sync()
    w = dr.X11RingWindow()
    try:
        w.create(40)
        if not w.argb:
            pytest.skip("this X server offers no 32-bit TrueColor visual")
        obs = x11.create_resource_object("window", w.window.id)
        assert obs.get_geometry().depth == 32
        w.draw(dr.render_ring(40, 0.5))
        w.move(100, 100)
        w._d.sync()
        assert obs.get_attributes().map_state == X.IsViewable
        assert list(obs.shape_get_rectangles(shape.SK.Input).rectangles) == []
        w.hide()
    finally:
        w.destroy()
        owner.destroy()
        x11.sync()


@xvfb
def test_an_x_error_turns_the_ring_off_once(x11):
    logs = []
    ring, w = _shown_ring(400, 300, 40, logs=logs)
    # Somebody else destroys our window (a window manager crash, xkill).
    x11.create_resource_object("window", w.window.id).destroy()
    x11.sync()
    for i in range(10):
        ring.tick(0.55 + i * 0.01)              # never raises
        if not ring.enabled:
            break
        w._d.sync()
    assert ring.enabled is False
    assert len(logs) == 1 and "KAPATILDI" in logs[0]
    ring.tick(0.7)
    ring.close()
    assert len(logs) == 1
    assert w._d is None
