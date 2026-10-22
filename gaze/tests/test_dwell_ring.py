# gaze/tests/test_dwell_ring.py
"""The visible dwell progress ring.

Dwell clicking without feedback is clicking blind: the user cannot tell
whether holding their gaze still is about to click, or whether a small
tremor has just restarted the count. The ring fills as the dwell builds,
vanishes when it resets and flashes on the click.

The geometry/state half is pure and tested here; the Win32 window is a thin
object behind an interface, replaced by a fake. What matters most is the
failure contract: a ring that cannot be drawn must turn itself off and say so
ONCE - never raise into the gaze loop, never take the cursor down with it.
"""
import ctypes
import math

import numpy as np
import pytest

from eye_gaze import dwell_ring as dr
from eye_gaze.dwell import DwellClicker


# --- geometry ---------------------------------------------------------------

def test_ring_window_is_centred_on_the_point():
    assert dr.ring_origin(500, 300, 40) == (480, 280)


def test_ring_origin_keeps_odd_sizes_centred():
    assert dr.ring_origin(500, 300, 41) == (480, 280)


def test_ring_origin_works_on_a_monitor_left_of_the_primary():
    assert dr.ring_origin(-100, 50, 40) == (-120, 30)


# --- rendering --------------------------------------------------------------

def _px_at(img, angle_deg):
    """Pixel in the middle of the ring band at a clockwise angle from 12."""
    s = img.shape[0]
    c = (s - 1) / 2.0
    th = dr.ring_thickness(s)
    rm = s / 2.0 - 0.5 - th / 2.0
    a = math.radians(angle_deg)
    x = int(round(c + rm * math.sin(a)))
    y = int(round(c - rm * math.cos(a)))
    return img[y, x]


def _is_arc(px):
    b, g, r, a = (int(v) for v in px)
    return a > 150 and r > 150


def test_render_shape_and_type():
    img = dr.render_ring(40, 0.5)
    assert img.shape == (40, 40, 4)
    assert img.dtype == np.uint8
    assert img.flags["C_CONTIGUOUS"]


def test_centre_and_corners_are_fully_transparent():
    """The ring sits AROUND the cursor - the target itself stays visible."""
    img = dr.render_ring(40, 1.0)
    assert img[20, 20, 3] == 0
    assert img[0, 0, 3] == 0
    assert img[39, 39, 3] == 0


def test_colours_are_premultiplied():
    """UpdateLayeredWindow with AC_SRC_ALPHA needs premultiplied BGRA; an
    un-premultiplied bitmap draws a bright halo around every soft edge."""
    for p, f in ((0.3, False), (1.0, False), (1.0, True)):
        img = dr.render_ring(40, p, flash=f).astype(int)
        assert (img[:, :, :3] <= img[:, :, 3:4]).all()


def test_quarter_progress_fills_the_top_right_only():
    img = dr.render_ring(60, 0.25)
    assert _is_arc(_px_at(img, 45))
    assert not _is_arc(_px_at(img, 135))
    assert not _is_arc(_px_at(img, 270))
    # the unfilled part is still drawn, as a faint track
    assert _px_at(img, 270)[3] > 0


def test_full_progress_fills_all_round():
    img = dr.render_ring(60, 1.0)
    for a in (10, 100, 190, 280, 350):
        assert _is_arc(_px_at(img, a)), a


def test_zero_progress_draws_only_the_track():
    img = dr.render_ring(60, 0.0)
    for a in (10, 100, 190, 280):
        assert not _is_arc(_px_at(img, a))


def test_flash_is_a_full_ring_in_a_different_colour():
    img = dr.render_ring(60, 0.0, flash=True)
    full = dr.render_ring(60, 1.0)
    for a in (10, 100, 190, 280):
        px = _px_at(img, a)
        assert px[3] > 200
        assert tuple(px) != tuple(_px_at(full, a))


def test_tiny_sizes_still_render():
    img = dr.render_ring(12, 0.5)
    assert img.shape == (12, 12, 4)
    assert img[:, :, 3].max() > 0


# --- state: what to show, frame by frame -------------------------------------

def _dweller():
    return DwellClicker(radius_px=40, dwell_seconds=1.0)


def test_nothing_is_shown_before_the_dweller_was_ever_fed():
    m = dr.DwellRingModel(40)
    assert m.view(_dweller(), 5.0) is None


def test_ring_appears_as_the_dwell_builds_and_tracks_the_cursor():
    d, m = _dweller(), dr.DwellRingModel(40)
    d.update(500, 300, 0.0)
    d.update(503, 301, 0.5)
    v = m.view(d, 0.5)
    assert v is not None
    assert (v.left, v.top) == dr.ring_origin(503, 301, 40)
    assert v.progress == pytest.approx(0.5, abs=1.0 / m.steps)
    assert not v.flash


def test_ring_is_not_shown_for_the_first_instant_of_every_fixation():
    """Every ordinary glance starts a dwell. Showing the ring at 1% would
    make it flicker on and off all over the screen while the user reads."""
    d, m = _dweller(), dr.DwellRingModel(40)
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.05)
    assert m.view(d, 0.05) is None


def test_ring_disappears_when_the_dwell_resets():
    d, m = _dweller(), dr.DwellRingModel(40)
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.6)
    assert m.view(d, 0.6) is not None
    d.update(900, 300, 0.63)                  # looked away: new anchor
    assert m.view(d, 0.63) is None


def test_ring_hides_when_the_dweller_stops_being_fed():
    """Pause, closed eyes, a lost camera: the loop stops feeding the dweller.
    progress_at(now) would keep climbing to 100% and show a click that is
    never coming - the ring must go instead."""
    d, m = _dweller(), dr.DwellRingModel(40, max_age=0.3)
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    assert m.view(d, 0.6) is not None        # a blink-length gap is fine
    assert m.view(d, 1.2) is None


def test_ring_progress_is_frozen_while_not_fed():
    d, m = _dweller(), dr.DwellRingModel(40, max_age=0.3)
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    assert m.view(d, 0.75).progress == pytest.approx(0.5, abs=1.0 / m.steps)


def test_click_flashes_once_then_the_ring_goes():
    d, m = _dweller(), dr.DwellRingModel(40, flash_seconds=0.3)
    d.update(500, 300, 0.0)
    assert d.update(502, 300, 1.0)
    v = m.view(d, 1.0)
    assert v is not None and v.flash
    assert (v.left, v.top) == dr.ring_origin(502, 300, 40)
    d.update(502, 300, 1.1)
    assert m.view(d, 1.1).flash
    d.update(502, 300, 1.4)
    assert m.view(d, 1.4) is None             # one-shot: no progress after
    d.update(502, 300, 2.5)
    assert m.view(d, 2.5) is None             # and no second flash


def test_repeat_mode_flashes_on_every_click():
    d = DwellClicker(radius_px=40, dwell_seconds=1.0, repeat=True)
    m = dr.DwellRingModel(40, flash_seconds=0.2)
    flashes = []
    t = 0.0
    while t < 3.05:
        d.update(500, 300, t)
        v = m.view(d, t)
        if v is not None and v.flash and (not flashes or t - flashes[-1] > 0.5):
            flashes.append(round(t, 2))
        t += 0.05
    assert len(flashes) == 3


# --- the overlay: model + window, behind the failure contract ----------------

class FakeWindow:
    def __init__(self, fail_create=False, fail_draw=False, excluded=True):
        self.calls = []
        self.fail_create = fail_create
        self.fail_draw = fail_draw
        self.excluded = excluded
        self.visible = False
        self.pos = None
        self.draws = 0

    def create(self, size):
        self.calls.append(("create", size))
        if self.fail_create:
            raise OSError("CreateWindowExW failed")

    def exclude_from_capture(self):
        self.calls.append(("exclude",))
        return self.excluded

    def draw(self, bgra):
        if self.fail_draw:
            raise OSError("UpdateLayeredWindow failed")
        assert bgra.dtype == np.uint8
        self.draws += 1

    def move(self, left, top):
        self.pos = (left, top)
        self.visible = True

    def hide(self):
        self.calls.append(("hide",))
        self.visible = False

    def flush(self):
        self.calls.append(("flush",))

    def pump(self):
        pass

    def destroy(self):
        self.calls.append(("destroy",))
        self.visible = False


def _ring(win=None, **kw):
    logs = []
    d = _dweller()
    w = win or FakeWindow()
    ring = dr.DwellRing(d, 40, w, log=logs.append, **kw)
    return ring, d, w, logs


def test_overlay_shows_moves_and_hides():
    ring, d, w, _ = _ring()
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    ring.tick(0.5)
    assert w.visible and w.pos == dr.ring_origin(500, 300, 40)
    d.update(900, 300, 0.55)
    ring.tick(0.55)
    assert not w.visible


def test_overlay_redraws_only_when_the_picture_changes():
    """A redraw is a bitmap upload per frame; moving is not."""
    ring, d, w, _ = _ring()
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    ring.tick(0.5)
    ring.tick(0.5)
    d.update(505, 302, 0.5)
    ring.tick(0.5)
    assert w.draws == 1
    assert w.pos == dr.ring_origin(505, 302, 40)


def test_overlay_hides_once_not_every_frame():
    ring, d, w, _ = _ring()
    for t in (1.0, 1.1, 1.2):
        ring.tick(t)
    assert w.calls.count(("hide",)) <= 1


def test_window_that_cannot_be_created_disables_the_ring_quietly():
    ring, d, w, logs = _ring(FakeWindow(fail_create=True))
    assert not ring.enabled
    assert len(logs) == 1 and "halka" in logs[0].lower()
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    ring.tick(0.5)                           # no-op, no raise
    ring.prepare_capture()
    ring.close()


def test_a_failing_draw_turns_the_ring_off_and_never_raises():
    ring, d, w, logs = _ring(FakeWindow(fail_draw=True))
    d.update(500, 300, 0.0)
    for t in (0.5, 0.6, 0.7):
        d.update(500, 300, t)
        ring.tick(t)                         # must not raise
    assert not ring.enabled
    assert len(logs) == 1
    assert ("destroy",) in w.calls


def test_log_messages_are_ascii():
    """The console is cp1254: a non-ASCII log line raises inside print."""
    ring, d, w, logs = _ring(FakeWindow(fail_create=True))
    for ln in logs:
        ln.encode("ascii")


def test_ring_hides_before_the_magnifier_screenshot():
    """Even when the window is excluded from capture: hiding is cheap and the
    exclusion API is not something to bet the zoom image on."""
    ring, d, w, _ = _ring(FakeWindow(excluded=True))
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    ring.tick(0.5)
    ring.prepare_capture()
    assert not w.visible
    assert ("flush",) not in w.calls


def test_without_capture_exclusion_the_hide_is_waited_for():
    """DWM composes on its own schedule: a BitBlt straight after ShowWindow
    (SW_HIDE) can still read the ring. DwmFlush waits for the next frame."""
    ring, d, w, _ = _ring(FakeWindow(excluded=False))
    assert not ring.capture_safe
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    ring.tick(0.5)
    ring.prepare_capture()
    assert not w.visible
    assert ("flush",) in w.calls


def test_ring_comes_back_after_the_capture_and_redraws():
    ring, d, w, _ = _ring()
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    ring.tick(0.5)
    ring.prepare_capture()
    d.update(500, 300, 0.55)
    ring.tick(0.55)
    assert w.visible


def test_close_destroys_the_window_and_is_idempotent():
    ring, d, w, _ = _ring()
    ring.close()
    ring.close()
    assert w.calls.count(("destroy",)) == 1
    ring.tick(1.0)                           # after close: no-op


# --- wiring helpers the driver calls ------------------------------------------

class FakeZoom:
    def __init__(self, order):
        self.order = order

    def begin(self):
        self.order.append("grab")


def test_attach_returns_none_without_dwell_click():
    assert dr.attach({"dwell_ring": True}, None, window=FakeWindow()) is None


def test_attach_returns_none_when_switched_off():
    assert dr.attach({"dwell_ring": False}, _dweller(), window=FakeWindow(),
                     log=lambda s: None) is None


def test_attach_returns_none_when_the_window_fails():
    logs = []
    assert dr.attach({"dwell_ring": True}, _dweller(),
                     window=FakeWindow(fail_create=True), log=logs.append) is None
    assert len(logs) == 1


def test_attach_uses_the_configured_size_within_sane_bounds():
    w = FakeWindow()
    ring = dr.attach({"dwell_ring": True, "dwell_ring_px": 64}, _dweller(),
                     window=w, log=lambda s: None)
    assert ring.size == 64 and ("create", 64) in w.calls
    w2 = FakeWindow()
    dr.attach({"dwell_ring": True, "dwell_ring_px": 3}, _dweller(),
              window=w2, log=lambda s: None)
    assert ("create", 12) in w2.calls
    w3 = FakeWindow()
    dr.attach({"dwell_ring": True, "dwell_ring_px": "kirk"}, _dweller(),
              window=w3, log=lambda s: None)
    assert ("create", 40) in w3.calls


def test_attach_hides_the_ring_before_every_magnifier_grab():
    order = []
    w = FakeWindow()
    w.hide = lambda: order.append("hide")
    z = FakeZoom(order)
    d = _dweller()
    ring = dr.attach({"dwell_ring": True}, d, zoomer=z, window=w,
                     log=lambda s: None)
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    ring.tick(0.5)
    z.begin()
    assert order == ["hide", "grab"]


def test_a_failing_hide_never_blocks_the_magnifier():
    order = []
    w = FakeWindow()

    def boom():
        raise OSError("x")
    w.hide = boom
    z = FakeZoom(order)
    d = _dweller()
    ring = dr.attach({"dwell_ring": True}, d, zoomer=z, window=w,
                     log=lambda s: None)
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    ring.tick(0.5)
    z.begin()
    assert order == ["grab"]


def test_the_guard_works_on_the_drivers_real_magnifier(monkeypatch):
    """guard_capture wraps an instance method - prove that holds for the
    driver's _ZoomClick (no __slots__, begin looked up on the instance by
    _route_clicks), with the screen grab itself stubbed out."""
    import gaze_driver as gd
    from eye_gaze import zoom
    order = []
    monkeypatch.setattr(zoom, "grab_region",
                        lambda *a, **k: order.append("grab") or None)
    monkeypatch.setattr(gd.cursor, "get_cursor", lambda: (100, 100))
    w = FakeWindow()
    w.hide = lambda: order.append("hide")
    d = _dweller()
    z = gd._ZoomClick(800, 600)
    ring = dr.attach({"dwell_ring": True}, d, zoomer=z, window=w,
                     log=lambda s: None)
    d.update(500, 300, 0.0)
    d.update(500, 300, 0.5)
    ring.tick(0.5)
    z.begin()                                # what _route_clicks calls on F6
    assert order == ["hide", "grab"]
    assert not z.active                      # grab stub returned None


def test_driver_wires_the_ring(monkeypatch):
    """The driver imports the factory under the name its main() calls."""
    import gaze_driver as gd
    assert gd.attach_dwell_ring is dr.attach


# --- config -------------------------------------------------------------------

def test_ring_settings_are_known_with_defaults():
    import eye_gaze.config as c
    assert {"dwell_ring", "dwell_ring_px"} <= c.KNOWN_KEYS
    assert c.DEFAULTS["dwell_ring"] is True
    assert c.DEFAULTS["dwell_ring_px"] == 40


def test_ring_size_written_the_turkish_way_is_a_number(tmp_path, monkeypatch):
    import eye_gaze.config as c
    monkeypatch.setattr(c, "_report", lambda m: None)
    p = tmp_path / "c.yaml"
    p.write_text("left_click_key: space\ndwell_ring_px: '48,0'\n", encoding="utf-8")
    cfg = c.load_config(str(p))
    assert cfg["dwell_ring_px"] == 48 and isinstance(cfg["dwell_ring_px"], int)


def test_shipped_config_turns_the_ring_on(shipped_config):
    import yaml
    with open(shipped_config, "r", encoding="utf-8") as f:
        shipped = yaml.safe_load(f)
    assert shipped["dwell_ring"] is True
    assert shipped["dwell_ring_px"] == 40


# --- the real Win32 window: declared prototypes only --------------------------
#
# 64-bit handles: an undeclared restype truncates an HWND/HDC/HBITMAP to a C
# int (see test_win32_handles.py for the crash that taught this project).

WINDOWS = hasattr(ctypes, "windll")


@pytest.mark.skipif(not WINDOWS, reason="Windows-only")
def test_the_ring_window_prototypes_return_full_width_handles():
    api = dr._api()
    if api is None:
        pytest.skip("user32/gdi32 yok")
    u, g, k = api["user32"], api["gdi32"], api["kernel32"]
    for fn in (u.CreateWindowExW, k.GetModuleHandleW, u.GetDC,
               g.CreateCompatibleDC, g.CreateDIBSection, g.SelectObject):
        assert fn.restype is ctypes.c_void_p, fn


@pytest.mark.skipif(not WINDOWS, reason="Windows-only")
def test_the_real_window_is_created_hidden_and_destroyed():
    """Created, never shown: nothing appears on the user's screen."""
    w = dr.Win32RingWindow()
    try:
        w.create(40)
    except OSError:
        pytest.skip("pencere olusturulamadi (masaustu yok)")
    try:
        assert w.hwnd
        u = dr._api()["user32"]
        assert not u.IsWindowVisible(w.hwnd)
        ex = u.GetWindowLongW(w.hwnd, -20)                     # GWL_EXSTYLE
        for bit in (0x20, 0x80000, 0x8, 0x80, 0x08000000):
            assert ex & bit, hex(bit)
        w.draw(dr.render_ring(40, 0.5))     # upload works on a hidden window
        assert not u.IsWindowVisible(w.hwnd)
    finally:
        w.destroy()
    assert w.hwnd is None
