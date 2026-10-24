# gaze/tests/test_physical.py
"""Screen scale from the monitor's physical size (eye_gaze.physical).

Nothing here touches a real monitor: every OS probe is injected.
"""
import math

import pytest

from eye_gaze import physical
from eye_gaze.physical import ScreenScale


def edid(w_mm=597, h_mm=336, w_cm=None, h_cm=None, clock=True, checksum=True):
    """A minimal 128-byte EDID block with a size in the first detailed
    timing descriptor (mm) and in the basic parameters (cm)."""
    b = bytearray(128)
    b[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    b[21] = round(w_mm / 10) if w_cm is None else w_cm
    b[22] = round(h_mm / 10) if h_cm is None else h_cm
    if clock:
        b[54], b[55] = 0x56, 0x5E
    b[66] = w_mm & 0xFF
    b[67] = h_mm & 0xFF
    b[68] = ((w_mm >> 8) << 4) | (h_mm >> 8)
    if checksum:
        b[127] = (-sum(b[:127])) % 256
    else:
        b[127] = (1 - sum(b[:127])) % 256
    return bytes(b)


# --- EDID ------------------------------------------------------------------------

def test_edid_detailed_timing_size_in_mm():
    assert physical.parse_edid(edid(597, 336)) == (597, 336)


def test_edid_falls_back_to_the_cm_size():
    assert physical.parse_edid(edid(531, 299, clock=False)) == (530, 300)


@pytest.mark.parametrize("data", [b"", b"\x00" * 128, b"junk" * 40, None,
                                  edid(checksum=False)])
def test_edid_garbage_is_none(data):
    assert physical.parse_edid(data) is None


# --- the factor --------------------------------------------------------------------

def test_reference_screen_is_exactly_one():
    s = physical.detect((2560, 1440), candidates=[("EDID", (597, 336))])
    assert s.factor == 1.0 and not s.applied
    assert s.raw == pytest.approx(1.0, abs=0.01)
    assert s.source == "EDID"


def test_4k_27in_unscaled_is_bigger():
    # 3840x2160 logical (100 %): pixels 2/3 the size -> constants x1.5
    s = physical.detect((3840, 2160), candidates=[("EDID", (597, 336))])
    assert s.applied and s.factor == pytest.approx(1.5, abs=0.01)


def test_same_panel_at_150_percent_scaling_is_the_reference():
    # The DPI-unaware process sees 2560x1440 logical px on a 4K 27" panel.
    s = physical.detect((2560, 1440), candidates=[("EDID", (597, 336))])
    assert s.factor == 1.0


def test_inside_the_band_is_exactly_one():
    # 25" 2560x1440 -> 1.08 (inside, untouched)
    s = physical.detect((2560, 1440), candidates=[("EDID", (553, 311))])
    assert physical.BAND[0] <= s.raw <= physical.BAND[1]
    assert s.factor == 1.0 and not s.applied


def test_band_edges():
    assert physical.BAND == (0.80, 1.25)
    for raw, applied in ((0.799, True), (0.80, False), (1.25, False), (1.251, True)):
        assert physical.apply_band(raw) == (raw if applied else 1.0)


def _panel(diag_in, aspect=(16, 9)):
    """(w_mm, h_mm) of a panel from its diagonal and aspect ratio."""
    k = diag_in * 25.4 / math.hypot(*aspect)
    return (aspect[0] * k, aspect[1] * k)


# (name, diagonal ", logical px the DPI-unaware process sees, scaled?)
COMMON_SCREENS = [
    ('21.5" 1080p', 21.5, (1920, 1080), False),     # 0.94
    ('23.8" 1080p', 23.8, (1920, 1080), False),     # 0.85
    ('24" 1080p', 24.0, (1920, 1080), False),       # 0.84
    ('27" 1080p', 27.0, (1920, 1080), True),        # 0.75
    ('27" 1440p', 27.0, (2560, 1440), False),       # 1.00
    ('32" 1440p', 32.0, (2560, 1440), False),       # 0.84
    ('27" 4K at 100 %', 27.0, (3840, 2160), True),  # 1.50
    ('32" 4K at 100 %', 32.0, (3840, 2160), True),  # 1.26
    ('27" 4K at 150 %', 27.0, (2560, 1440), False), # 1.00
    ('13.3" 1080p at 150 %', 13.3, (1280, 720), False),   # 1.01
    ('14" 1080p at 150 %', 14.0, (1280, 720), False),     # 0.96
    ('15.6" 1080p at 125 %', 15.6, (1536, 864), False),   # 1.04
    ('15.6" 1080p at 100 %', 15.6, (1920, 1080), True),   # 1.30
]


@pytest.mark.parametrize("name,diag,px,scaled", COMMON_SCREENS,
                         ids=[c[0] for c in COMMON_SCREENS])
def test_common_screens(name, diag, px, scaled):
    s = physical.detect(px, candidates=[("EDID", _panel(diag))])
    assert s.source == "EDID"
    assert s.applied is scaled, (name, s.raw)
    if not scaled:
        assert s.factor == 1.0
        cfg = {"deadzone": 20, "max_cursor_speed": 1200, "beta": 0.002}
        assert physical.scaled_config(cfg, s.factor) is cfg   # bit for bit


def test_clamped_to_its_range():
    big = physical.detect((1920, 1080), candidates=[("EDID", _panel(40.0))])
    assert big.factor == physical.CLAMP[0] and big.applied    # 40" 1080p: 0.50
    tiny = physical.detect((3840, 2160), candidates=[("EDID", (294, 165))])  # 13" 4K
    assert tiny.factor == physical.CLAMP[1]


@pytest.mark.parametrize("mm", [(0, 0), (16, 9), (160, 90), (5000, 2800),
                                (597, 597), (float("nan"), 300), None, ("a", 1)])
def test_implausible_sizes_mean_one(mm):
    s = physical.detect((2560, 1440), candidates=[("EDID", mm)])
    assert s.factor == 1.0 and not s.applied and s.source is None


def test_rotated_monitor_is_matched_by_orientation():
    s = physical.detect((1440, 2560), candidates=[("EDID", (597, 336))])
    assert s.factor == 1.0 and s.mm_per_px == pytest.approx(0.2331, abs=0.001)


def test_first_plausible_candidate_wins():
    s = physical.detect((3840, 2160), candidates=[
        ("EDID", (16, 9)), ("GetDeviceCaps", (597, 336))])
    assert s.source == "GetDeviceCaps" and s.applied


def test_unknown_is_one():
    s = physical.detect((2560, 1440), candidates=[])
    assert s.factor == 1.0 and s.source is None and not s.applied
    assert physical.detect((0, 0), candidates=[("EDID", (597, 336))]).factor == 1.0


def test_a_failing_probe_never_raises():
    def boom():
        raise OSError("no monitor")
        yield
    assert physical.detect((2560, 1440), candidates=boom()).factor == 1.0


# --- Windows probe (all injected) ---------------------------------------------------

DEV = (r"\\?\DISPLAY#GSM5B7F#5&2a1b3c4d&0&UID4352"
       r"#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}")


def test_windows_registry_path_from_the_device_interface_name():
    assert physical.edid_key(DEV) == (
        r"SYSTEM\CurrentControlSet\Enum\DISPLAY\GSM5B7F"
        r"\5&2a1b3c4d&0&UID4352\Device Parameters")
    assert physical.edid_key("MONITOR\\GSM5B7F\\{x}\\0001") is None
    assert physical.edid_key("") is None


def test_windows_candidates_edid_then_device_caps():
    seen = []

    def read(key):
        seen.append(key)
        return edid(597, 336)
    got = list(physical.windows_candidates(
        monitor_ids=lambda: [DEV], read_edid=read,
        device_caps=lambda: (600, 340)))
    assert got == [("EDID", (597, 336)), ("GetDeviceCaps", (600, 340))]
    assert seen == [physical.edid_key(DEV)]


def test_windows_candidates_survive_broken_parts():
    def bad():
        raise OSError("SetupAPI")
    got = list(physical.windows_candidates(
        monitor_ids=bad, read_edid=bad, device_caps=lambda: (600, 340)))
    assert got == [("GetDeviceCaps", (600, 340))]
    got = list(physical.windows_candidates(
        monitor_ids=lambda: [DEV], read_edid=lambda k: None,
        device_caps=bad))
    assert got == []


# --- Linux probe ------------------------------------------------------------------

class _Backend:
    def __init__(self, mm):
        self.mm = mm

    def primary_mm(self):
        if isinstance(self.mm, Exception):
            raise self.mm
        return self.mm


def test_linux_candidates_from_the_backend():
    assert list(physical.linux_candidates(_Backend((597, 336)))) == [
        ("XRandR", (597, 336))]
    assert list(physical.linux_candidates(_Backend(None))) == []
    assert list(physical.linux_candidates(_Backend(OSError("x")))) == []
    assert list(physical.linux_candidates(object())) == []   # Wayland/null


def test_x11_backend_reports_the_primary_monitors_mm(monkeypatch):
    from eye_gaze.platform import linux_x11 as lx
    import threading

    class M:
        def __init__(self, x, w, h, wmm, hmm, primary):
            self.x, self.y = x, 0
            self.width_in_pixels, self.height_in_pixels = w, h
            self.width_in_millimeters, self.height_in_millimeters = wmm, hmm
            self.primary = primary

    class Root:
        def xrandr_get_monitors(self, is_active=True):
            class R:
                monitors = [M(0, 1920, 1080, 531, 299, False),
                            M(1920, 2560, 1440, 597, 336, True)]
            return R()

    class D:
        def pending_events(self):
            return 0

    b = object.__new__(lx.X11Backend)
    b._lock = threading.Lock()
    b._d, b._root, b._randr = D(), Root(), True
    b._screens, b._primary, b._screens_at = None, None, 0.0
    b._primary_mm = None
    assert b.primary() == (1920, 0, 2560, 1440)
    assert b.primary_mm() == (597, 336)


def test_evdev_backend_asks_its_x_source(monkeypatch):
    from eye_gaze.platform import linux_evdev as le
    b = object.__new__(le.EvdevBackend)
    b._x = _Backend((597, 336))
    assert b.primary_mm() == (597, 336)
    b._x = None
    assert b.primary_mm() is None
    b._x = _Backend(OSError("gone"))
    assert b.primary_mm() is None


def test_unsupported_os_has_no_candidates(monkeypatch):
    monkeypatch.setattr(physical, "_os", lambda: "darwin")
    assert list(physical.default_candidates()) == []
    assert physical.detect((2560, 1440)).factor == 1.0


# --- applying it: one rule for every pixel constant ----------------------------------

def test_scaled_config_scales_every_pixel_constant():
    cfg = {"deadzone": 20.0, "max_cursor_speed": 1200.0, "beta": 0.004,
           "dwell_radius": 40, "snap_radius": 120.0, "dwell_ring_px": 40,
           "implicit_max_px": 100.0, "pursuit_speed": 480,
           "saccade_velocity_px_s": 1500.0, "saccade_min_px": 120.0,
           "mincutoff": 0.45, "zoom_scale": 0.4}
    out = physical.scaled_config(cfg, 1.5)
    assert out is not cfg and cfg["deadzone"] == 20.0      # a copy
    assert out["deadzone"] == pytest.approx(30.0)
    assert out["max_cursor_speed"] == pytest.approx(1800.0)
    assert out["dwell_radius"] == pytest.approx(60.0)
    assert out["snap_radius"] == pytest.approx(180.0)
    assert out["dwell_ring_px"] == 60 and isinstance(out["dwell_ring_px"], int)
    assert out["implicit_max_px"] == pytest.approx(150.0)
    assert out["pursuit_speed"] == pytest.approx(720.0)
    assert out["saccade_velocity_px_s"] == pytest.approx(2250.0)
    assert out["saccade_min_px"] == pytest.approx(180.0)
    # beta is Hz per (px/s): same physical speed = 1.5x the px/s
    assert out["beta"] == pytest.approx(0.004 / 1.5)
    assert out["mincutoff"] == 0.45 and out["zoom_scale"] == 0.4


def test_missing_keys_are_scaled_from_the_drivers_defaults():
    out = physical.scaled_config({}, 0.75)
    assert out["deadzone"] == pytest.approx(15.0)
    assert out["dwell_radius"] == pytest.approx(30.0)
    assert out["pursuit_speed"] == pytest.approx(360.0)
    assert out["max_cursor_speed"] == 0.0              # off stays off
    assert "beta" not in out or out["beta"] == pytest.approx(0.007 / 0.75)


def test_factor_one_is_the_same_dict_values_bit_for_bit():
    cfg = {"deadzone": 20, "beta": 0.002, "snap_radius": "garbage"}
    out = physical.scaled_config(cfg, 1.0)
    assert out == cfg
    assert out is cfg


def test_unusable_values_are_left_to_their_own_fallback():
    out = physical.scaled_config({"snap_radius": "garbage", "deadzone": True,
                                  "dwell_ring_px": float("inf")}, 1.5)
    assert out["snap_radius"] == "garbage"
    assert out["deadzone"] is True
    assert math.isinf(out["dwell_ring_px"])


def test_describe_is_ascii_and_says_whether_it_applied():
    on = physical.detect((3840, 2160), candidates=[("EDID", (597, 336))])
    off = physical.detect((2560, 1440), candidates=[("EDID", (597, 336))])
    none = physical.detect((2560, 1440), candidates=[])
    for s in (on, off, none):
        assert physical.describe(s).isascii()
    assert "UYGULANDI" in physical.describe(on)
    assert "UYGULANMADI" in physical.describe(off)
    assert "bilinmiyor" in physical.describe(none)


def test_screen_scale_is_a_plain_record():
    s = ScreenScale(1.0, None, None, None, (1, 1), None, False)
    assert s.factor == 1.0


# --- the driver applies it once, before every consumer -------------------------------

def test_driver_scales_before_any_pixel_constant_is_read():
    import inspect
    import gaze_driver as gd
    src = inspect.getsource(gd.main)
    at = src.index("physical.scaled_config(cfg, screen_scale.factor)")
    assert src.count("physical.scaled_config(") == 1
    for consumer in ("DwellClicker(", 'pursuit_speed = float(cfg.get("pursuit_speed"',
                     "OneEuroFilter(", "SpeedLimiter(", "FixationStabilizer(",
                     "make_saccade_jump(cfg)", "attach_dwell_ring(cfg",
                     "make_target_snap(cfg", "ImplicitCorrector.from_config("):
        assert src.index(consumer) > at, consumer
    # the sensitivity line is printed from the unscaled (preset) values
    assert src.index("sensitivity.describe(cfg)") < at


def test_driver_frame_counts_follow_the_measured_rate():
    import inspect
    import gaze_driver as gd
    src = inspect.getsource(gd.main)
    assert "gaze_median_window" not in src      # read via sensitivity.median_seconds
    assert "deque(maxlen=timing.median_frames)" in src
    assert "if timing.tick(now):" in src and "hist = timing.resize(hist)" in src
    assert "loop_i % timing.scroll_frames == 0" in src
    assert "loop_i % 4 ==" not in src


# --- TVs and projectors: the mm model assumes desk viewing distance ---------------

def test_a_tv_counts_as_unknown():
    s = physical.detect((1920, 1080), candidates=[("EDID", _panel(55.0))])
    assert s.factor == 1.0 and not s.applied
    assert s.raw is None and s.source == "EDID"     # measured, but not used
    line = physical.describe(s)
    assert line.isascii() and "TV" in line and "x1.00" in line


def test_a_tv_stops_the_search():
    s = physical.detect((1920, 1080), candidates=[
        ("EDID", _panel(55.0)), ("GetDeviceCaps", _panel(24.0))])
    assert s.factor == 1.0 and s.source == "EDID"


def test_43_inch_is_the_last_monitor():
    at = physical.detect((1920, 1080), candidates=[("EDID", _panel(43.0))])
    assert at.raw is not None and at.applied            # 1092 mm: a monitor
    over = physical.detect((1920, 1080), candidates=[("EDID", _panel(44.0))])
    assert over.raw is None and over.factor == 1.0     # 1118 mm: a TV
    assert physical.MAX_DESK_DIAG_MM == 1100.0


def test_a_40_inch_monitor_is_still_scaled_by_the_band():
    lo = physical.detect((1920, 1080), candidates=[("EDID", _panel(40.0))])
    assert lo.applied and lo.factor == physical.CLAMP[0]
    k4 = physical.detect((3840, 2160), candidates=[("EDID", _panel(40.0))])
    assert not k4.applied and k4.raw is not None and k4.factor == 1.0   # 1.01
