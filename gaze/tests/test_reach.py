# gaze/tests/test_reach.py
"""Full reach of the screen edges (eye_gaze.reach).

Field report (1.3.2, 2560x1440): the cursor did not get to the far left and
right. The calibration had measured why - its held-out predictions spanned
93% of the calibrated range (coverage slope 0.93), so looking at an edge put
the cursor ~90 px short. The live cursor now undoes that measured shrink
(bounded), and a narrow band at each screen edge carries it onto the edge.
"""
import math

import pytest

from eye_gaze import reach
from eye_gaze.mapping import GazeMapper


def _mapper(sx=0.8, sy=0.9, rx=40.0, ry=20.0, sel=True, slo=(100, 50),
            shi=(900, 550)):
    """A real mapper with literal coefficients: predict((a, b)) is exactly
    (1000 a, 1000 b) for a in 0..3, b in 0..1; calibrated range x 100..900
    (centre 500), y 50..550 (centre 300)."""
    d = {"v": 2, "cx": [0.0, 1000.0, 0.0], "cy": [0.0, 0.0, 1000.0],
         "lo": [0.0, 0.0], "hi": [3.0, 1.0], "reach": 0.0, "poly2": False,
         "slo": list(slo), "shi": list(shi)}
    if sel:
        d["sel"] = {"x": {"slope": sx, "rmse": rx}, "y": {"slope": sy, "rmse": ry}}
    return GazeMapper.from_dict(d)


# --- undoing the measured shrink ----------------------------------------------


def test_a_shrunk_axis_is_stretched_back_around_the_calibrated_centre():
    r = reach.Reach(True)
    x, y = r.predict(_mapper(), (0.3, 0.2))        # plain mapper: (300, 200)
    assert x == pytest.approx(250.0)               # 500 + (300 - 500) / 0.8
    assert y == pytest.approx(188.8889, abs=1e-3)  # 300 + (200 - 300) / 0.9


def test_the_field_calibration_reaches_the_edge_it_fell_short_of():
    # 2560 wide, ball path 6%..94% (153.6..2406.4), slope 0.93: looking at
    # the left edge the plain model said ~90 px; stretched it says ~0.
    m = _mapper(sx=0.93, slo=(153.6, 50), shi=(2406.4, 550))
    x, _ = reach.Reach(True).predict(m, (0.0896, 0.3))     # plain: 89.6
    assert x == pytest.approx(0.0, abs=0.5)


def test_the_stretch_is_capped_for_a_badly_shrunk_calibration():
    # slope 0.5 would double every error; at most 1/0.8 = 1.25x is applied
    x, _ = reach.Reach(True).predict(_mapper(sx=0.5), (0.3, 0.2))
    assert x == pytest.approx(250.0)


@pytest.mark.parametrize("slope", [1.0, 1.08])
def test_an_axis_that_already_reaches_is_left_alone(slope):
    x, _ = reach.Reach(True).predict(_mapper(sx=slope), (0.3, 0.2))
    assert x == pytest.approx(300.0)


@pytest.mark.parametrize("slope", [None, float("nan"), "0.8", 0.0, -0.9, True])
def test_an_unusable_slope_means_no_stretch(slope):
    x, _ = reach.Reach(True).predict(_mapper(sx=slope), (0.3, 0.2))
    assert x == pytest.approx(300.0)


def test_a_calibration_without_a_selection_report_is_used_as_it_is():
    assert reach.Reach(True).predict(_mapper(sel=False), (0.3, 0.2)) == \
        pytest.approx((300.0, 200.0))


def test_off_means_the_plain_mapper():
    assert reach.Reach(False).predict(_mapper(), (0.3, 0.2)) == \
        pytest.approx((300.0, 200.0))


def test_a_new_calibration_brings_its_own_stretch():
    # F9 / undo swap the mapper mid-session: the old one's factors must not
    # stick to the new one.
    r = reach.Reach(True)
    assert r.predict(_mapper(sx=0.8), (0.3, 0.2))[0] == pytest.approx(250.0)
    assert r.predict(_mapper(sx=1.0), (0.3, 0.2))[0] == pytest.approx(300.0)


def test_a_view_predicts_like_the_reach_it_came_from():
    # measure_drift and the saccade detector take an object with predict()
    m = _mapper()
    r = reach.Reach(True)
    assert r.view(m).predict((0.3, 0.2)) == pytest.approx((250.0, 188.8889),
                                                          abs=1e-3)


# --- the edge band -----------------------------------------------------------


@pytest.mark.parametrize("u,want", [
    (1000, 1000), (990, 1000), (950, 1000),      # outer half of the band: edge
    (925, 950), (910, 920),                      # inner half: twice as fast
    (900, 900), (500, 500), (100, 100),          # inside: untouched
    (75, 50), (50, 0), (10, 0), (0, 0),          # the same at the low edge
])
def test_the_edge_band_carries_the_cursor_onto_the_edge(u, want):
    assert reach.warp(u, 0, 1000, 50) == pytest.approx(want)


def test_the_edge_band_has_no_jump_and_never_turns_back():
    out = [reach.warp(u / 10.0, 0, 1000, 50) for u in range(0, 10001)]
    steps = [b - a for a, b in zip(out, out[1:])]
    assert min(steps) >= 0.0                     # monotone
    assert max(steps) <= 0.2 + 1e-9              # at most 2x a 0.1 step


def test_no_band_and_a_tiny_screen_are_safe():
    assert reach.warp(990, 0, 1000, 0) == 990
    # a band wider than a quarter of the axis is narrowed to it
    assert reach.warp(60, 0, 100, 80) == pytest.approx(70.0)   # band 25
    assert reach.warp(80, 0, 100, 80) == 100


def test_the_band_follows_the_measured_error_and_the_screen():
    m = _mapper(rx=127.0, ry=400.0)
    bx, by = reach.Reach(True).bands(m, 2560, 1440)
    assert bx == pytest.approx(31.75)            # a quarter of 127 px
    assert by == pytest.approx(28.8)             # capped at 2% of 1440
    bx, by = reach.Reach(True).bands(_mapper(sel=False), 2560, 1440)
    assert (bx, by) == pytest.approx((25.6, 14.4))   # 1% without a report


def test_edge_works_within_the_given_limits():
    m = _mapper(rx=100.0, ry=100.0)              # bands 25 px x, 21.6 px y
    r = reach.Reach(True)
    limits = (-1920, 0, -1, 1079)                # a monitor left of primary
    assert r.edge(m, -10.0, 1070.0, limits) == pytest.approx((-1, 1079))
    assert r.edge(m, -1910.0, 5.0, limits) == pytest.approx((-1920, 0))
    assert r.edge(m, -960.0, 540.0, limits) == pytest.approx((-960, 540))
    # inner half of each band: -51 + 2 * 11, 1035.8 + 2 * 14.2
    assert r.edge(m, -40.0, 1050.0, limits) == pytest.approx((-29, 1064.2))


def test_edge_off_leaves_the_cursor_where_it_is():
    m = _mapper(rx=100.0, ry=100.0)
    assert reach.Reach(False).edge(m, 1910.0, 5.0, (0, 0, 1919, 1079)) == \
        (1910.0, 5.0)


# --- what the calibration result says ------------------------------------------


@pytest.mark.parametrize("slope,on,want", [
    (0.93, True, 100), (0.6, True, 75), (1.02, True, 100),
    (0.93, False, 93), (None, True, None)])
def test_coverage_says_what_the_live_cursor_reaches(slope, on, want):
    cov = reach.Reach(on).coverage(_mapper(sx=slope, sy=0.95))
    assert cov[0] == want


# --- the setting ----------------------------------------------------------------


@pytest.mark.parametrize("user,want", [
    ({"edge_reach": False, "cursor_sensitivity": 3}, False),
    ({"edge_reach": True}, True),
    ({"cursor_sensitivity": "auto"}, True),      # a 1.3.x config
    ({"cursor_sensitivity": 1}, True),
    ({"left_click_key": "space"}, False),        # a v1.2.0 config: as it was
    ({}, False), (None, False),
])
def test_the_setting_is_on_for_new_configs_and_off_for_old_ones(user, want):
    cfg = dict(user or {})
    reach.resolve(cfg, user)
    assert cfg["edge_reach"] is want


def test_resolve_keeps_a_coerced_value():
    cfg = {"edge_reach": False}      # config._coerce_numbers turned "hayir" into False
    reach.resolve(cfg, {"edge_reach": "hayir"})
    assert cfg["edge_reach"] is False


# --- in the driver ---------------------------------------------------------------


def test_the_driver_wires_the_reach_everywhere_the_cursor_is_predicted():
    """main() is one long loop; this guards the wiring the way the other
    cursor features do (tests/test_saccade.py): every live prediction goes
    through the reach (the cursor, the saccade detector, F7's drift dot),
    and the edge band sits after the smoothing, before target snapping."""
    import inspect
    import gaze_driver as gd
    src = inspect.getsource(gd.main)
    assert 'reach.Reach(cfg.get("edge_reach"' in src
    assert "edge_reach.predict(mapper, feat)" in src
    assert "mapper.predict(feat)" not in src
    assert "saccader.step(edge_reach.view(mapper).predict" in src
    assert "edge_reach.view(mapper))" in src             # measure_drift's last argument
    i_stab = src.index("stabilizer.update(lx, ly)")
    i_edge = src.index("edge_reach.edge(mapper, lx, ly")
    i_snap = src.index("snapper.adjust(")
    assert i_stab < i_edge < i_snap


def _cursor_at_edge(on, seed=3):
    """The driver's chain (median -> 1-Euro -> speed limit -> stabilizer ->
    edge band; level 3) for 3 s of looking at the right edge of a 2560 px
    screen, with the field calibration's shrink (slope 0.93): the plain
    model says ~2470 there. Returns the mean cursor x over the last second."""
    import random
    from collections import deque

    from eye_gaze.limiter import SpeedLimiter
    from eye_gaze.oneeuro import OneEuroFilter
    from eye_gaze.stabilizer import FixationStabilizer
    m = _mapper(sx=0.93, rx=127.0, slo=(153.6, 50), shi=(2406.4, 550))
    r = reach.Reach(on)
    rnd = random.Random(seed)
    hist = deque(maxlen=5)
    fx = OneEuroFilter(30.0, 0.45, 0.004, 1.0)
    lim = SpeedLimiter(1700.0)
    stab = FixationStabilizer(16.0)
    xs = []
    for i in range(90):
        t = i / 30.0
        hist.append((2.47 + rnd.gauss(0, 0.03), 0.3))
        g = tuple(sorted(c)[len(c) // 2] for c in zip(*hist))
        sx, sy = r.predict(m, g)
        sx = min(max(sx, 0.0), 2559.0)
        lx, ly = lim.update(fx(sx, t), 300.0, t)
        lx, ly = stab.update(lx, ly)
        lx, ly = r.edge(m, lx, ly, (0, 0, 2559, 1439))
        if t >= 2.0:
            xs.append(lx)
    return sum(xs) / len(xs)


def test_looking_at_the_edge_puts_the_cursor_on_it():
    assert _cursor_at_edge(True) == pytest.approx(2559.0, abs=1.0)
    assert _cursor_at_edge(False) < 2500.0       # the field complaint


def test_the_setting_reaches_the_driver_through_load_config(tmp_path):
    from eye_gaze.config import load_config
    cases = (("cursor_sensitivity: 2\nleft_click_key: space\n", True),
             ("left_click_key: space\n", False),
             ("cursor_sensitivity: 2\nleft_click_key: space\n"
              "edge_reach: hayir\n", False),
             ("left_click_key: space\nedge_reach: evet\n", True))
    for i, (text, want) in enumerate(cases):
        p = tmp_path / ("c%d.yaml" % i)
        p.write_text(text, encoding="utf-8")
        assert load_config(str(p))["edge_reach"] is want, text


def test_the_log_line_says_what_the_reach_does():
    # gaze.log is what a remote tester sends back: it must show whether the
    # correction ran and how strong it was.
    m = _mapper(sx=0.93, sy=0.95, rx=127.0, ry=93.0)
    line = reach.Reach(True).describe(m, 2560, 1440)
    assert "acik" in line and "1.08" in line and "1.05" in line
    assert "32" in line and "23" in line
    assert line.isascii()
    off = reach.Reach(False).describe(m, 2560, 1440)
    assert "kapali" in off and "edge_reach" in off
    bare = reach.Reach(True).describe(_mapper(sel=False), 2560, 1440)
    assert "acik" in bare and "26" in bare and "14" in bare


def test_a_fault_turns_the_reach_off_and_never_stops_the_cursor():
    # The cursor loop is this user's only way to use the computer: like the
    # saccade jump and target snapping, the reach never raises - on a fault
    # it switches itself off for the session and says so once.
    class Broken:
        selection = {"x": {"slope": 0.8, "rmse": 40.0},
                     "y": {"slope": 0.9, "rmse": 20.0}}

        def predict(self, gaze):
            return 300.0, 200.0

        @property
        def calibrated_range(self):
            raise ValueError("bozuk kalibrasyon")

    said = []
    r = reach.Reach(True, log=said.append)
    assert r.predict(Broken(), (0.3, 0.2)) == (300.0, 200.0)
    assert r.enabled is False
    assert len(said) == 1 and "KAPATILDI" in said[0] and said[0].isascii()
    assert r.edge(Broken(), 5.0, 5.0, (0, 0, 99, 99)) == (5.0, 5.0)
    assert len(said) == 1


def test_a_fault_in_the_edge_band_turns_it_off_too():
    said = []
    r = reach.Reach(True, log=said.append)
    m = _mapper()
    assert r.edge(m, 5.0, 5.0, ("a", 0, 99, 99)) == (5.0, 5.0)
    assert r.enabled is False and len(said) == 1
