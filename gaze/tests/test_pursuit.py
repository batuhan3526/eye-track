# gaze/tests/test_pursuit.py
import math

from eye_gaze.pursuit import PursuitPath, best_latency_fit


def _simple_path():
    # 1000 px right, then 500 px down, at 100 px/s
    return PursuitPath(sw=1000, sh=500, speed=100.0,
                       waypoints=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])


def test_duration_is_total_length_over_speed():
    p = _simple_path()
    assert abs(p.duration - 15.0) < 1e-9  # (1000 + 500) / 100


def test_pos_start_middle_end():
    p = _simple_path()
    assert p.pos(0.0) == (0.0, 0.0)
    assert p.pos(5.0) == (500.0, 0.0)      # halfway along first segment
    x, y = p.pos(12.5)
    assert abs(x - 1000.0) < 1e-9 and abs(y - 250.0) < 1e-9
    assert p.pos(15.0) == (1000.0, 500.0)


def test_pos_clamps_outside_range():
    p = _simple_path()
    assert p.pos(-3.0) == (0.0, 0.0)
    assert p.pos(99.0) == (1000.0, 500.0)


def test_near_turn_at_waypoints_not_mid_segment():
    p = _simple_path()
    assert p.near_turn(0.0, guard=0.35)          # start
    assert p.near_turn(10.0, guard=0.35)         # corner at 1000px = t 10s
    assert p.near_turn(15.0, guard=0.35)         # end
    assert not p.near_turn(5.0, guard=0.35)      # mid first segment
    assert not p.near_turn(12.5, guard=0.35)     # mid second segment
    assert p.near_turn(10.3, guard=0.35)         # within guard of the corner
    assert not p.near_turn(10.6, guard=0.35)


def test_best_latency_fit_recovers_true_eye_lag():
    # synthesize a user whose eyes trail the ball by exactly 0.18 s with a
    # simple linear gaze->screen relation; the scan must pick 0.18 and fit
    # accurately with it.
    import random
    rnd = random.Random(5)
    path = PursuitPath(sw=2000, sh=1000, speed=400.0,
                       waypoints=[(0.05, 0.1), (0.95, 0.1), (0.95, 0.9), (0.05, 0.9)])
    true_lat = 0.18
    samples = []
    t = 0.4
    while t < path.duration:
        x, y = path.pos(t - true_lat)
        g = (x / 2000.0 + rnd.gauss(0, 0.002), y / 1000.0 + rnd.gauss(0, 0.002))
        samples.append((g, t))
        t += 1.0 / 30.0
    mapper, err, lat = best_latency_fit(samples, path)
    assert lat == true_lat
    assert err < 15.0  # near-noise-floor residual at the right latency


def test_best_latency_fit_too_few_samples_returns_none():
    path = PursuitPath(sw=1000, sh=500, speed=100.0,
                       waypoints=[(0.0, 0.0), (1.0, 0.0)])
    mapper, err, lat = best_latency_fit([((0.5, 0.5), 3.0)] * 10, path)
    assert mapper is None and err is None and lat is None


def test_default_path_covers_screen():
    p = PursuitPath(sw=2560, sh=1440, speed=480.0)
    xs, ys = [], []
    t = 0.0
    while t <= p.duration:
        x, y = p.pos(t)
        xs.append(x)
        ys.append(y)
        t += 0.05
    # reaches near all four edges (calibration coverage)
    assert min(xs) < 0.10 * 2560 and max(xs) > 0.90 * 2560
    assert min(ys) < 0.10 * 1440 and max(ys) > 0.90 * 1440
    # a leisurely sub-minute sweep, not a marathon
    assert 15.0 < p.duration < 60.0


def test_pursuit_pairs_matches_the_fit_pairing():
    from eye_gaze.pursuit import PursuitPath, pursuit_pairs
    path = PursuitPath(1920, 1080, 480.0)
    samples = [((float(i), 0.0), i / 30.0) for i in range(int(path.duration * 30))]
    g, s = pursuit_pairs(samples, path, 0.12)
    assert len(g) == len(s) > 100
    # every kept pair is the ball position 0.12 s before the gaze sample
    gi = int(g[50][0])
    assert s[50] == path.pos(gi / 30.0 - 0.12)
    assert not any(path.near_turn(int(x[0]) / 30.0 - 0.12) for x in g)


# --- the path covers the screen vertically as well as horizontally ----------
#
# The four-row zigzag gave the vertical model four levels only: its short
# vertical links were all but dropped by the turn guard. A tester's cursor
# then reached ~20% of the screen height.

import pytest

from eye_gaze import pursuit

# landscape screens from 5:4 to 32:9
_SCREENS = [(1280, 1024), (1024, 768), (1366, 768), (1920, 1080),
            (1920, 1200), (2560, 1440), (3440, 1440), (5120, 1440)]


def _trace(p, step=0.02):
    pts, t = [], 0.0
    while t <= p.duration:
        pts.append(p.pos(t))
        t += step
    return pts


def _kept_motion(p, step=1 / 30.0):
    """(horizontal, vertical) seconds of ball motion the guard keeps."""
    h = v = 0.0
    t = 0.0
    while t < p.duration:
        if not p.near_turn(t):
            (x0, y0), (x1, y1) = p.pos(t), p.pos(t + 1e-3)
            if abs(y1 - y0) > abs(x1 - x0):
                v += step
            else:
                h += step
        t += step
    return h, v


def test_the_default_path_is_the_rows():
    p = PursuitPath(2560, 1440, 480.0)
    assert pursuit.DEFAULT_LAYOUT == "rows"
    assert p._pts == PursuitPath(2560, 1440, 480.0, layout="rows")._pts
    assert p._pts != PursuitPath(2560, 1440, 480.0, layout="grid")._pts


def test_no_setting_and_the_factory_setting_both_mean_rows():
    import os
    import yaml
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "config.default.yaml"), encoding="utf-8") as f:
        factory = yaml.safe_load(f)
    assert pursuit.layout_of(factory.get("calibration_path")) == "rows"
    assert pursuit.layout_of({}.get("calibration_path")) == "rows"


def test_the_rows_layout_is_the_old_path_unchanged():
    old = [(0.06, 0.08), (0.94, 0.08), (0.94, 0.37), (0.06, 0.37),
           (0.06, 0.63), (0.94, 0.63), (0.94, 0.92), (0.06, 0.92), (0.5, 0.5)]
    p = PursuitPath(1000, 500, 480.0, layout="rows")
    assert p._pts == [(fx * 1000, fy * 500) for fx, fy in old]


@pytest.mark.parametrize("sw,sh", _SCREENS)
def test_grid_covers_the_same_envelope_as_the_rows(sw, sh):
    pts = _trace(PursuitPath(sw, sh, 480.0, layout="grid"))
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    assert min(xs) == pytest.approx(0.06 * sw) and max(xs) == pytest.approx(0.94 * sw)
    assert min(ys) == pytest.approx(0.08 * sh) and max(ys) == pytest.approx(0.92 * sh)


@pytest.mark.parametrize("sw,sh", _SCREENS)
def test_grid_is_about_as_long_as_the_rows(sw, sh):
    grid = PursuitPath(sw, sh, 480.0, layout="grid")
    rows = PursuitPath(sw, sh, 480.0, layout="rows")
    assert 0.85 * rows.length <= grid.length <= 1.15 * rows.length


@pytest.mark.parametrize("sw,sh", _SCREENS)
def test_grid_sweeps_the_full_height_in_several_columns(sw, sh):
    p = PursuitPath(sw, sh, 480.0, layout="grid")
    cols = set()
    for a, b in zip(p._pts, p._pts[1:]):
        if abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) >= 0.4 * sh:
            cols.add(round(a[0] / sw, 2))
    assert cols == {0.06, 0.5, 0.94}


@pytest.mark.parametrize("sw,sh", [(1366, 768), (1920, 1080), (2560, 1440)])
def test_grid_keeps_far_more_vertical_pursuit_than_the_rows(sw, sh):
    _, v_rows = _kept_motion(PursuitPath(sw, sh, 480.0, layout="rows"))
    h, v = _kept_motion(PursuitPath(sw, sh, 480.0, layout="grid"))
    assert v >= 3.0 * max(v_rows, 0.5)
    assert v >= 0.25 * (h + v)          # a real share, not a token


def test_the_turn_guard_is_short_before_a_turn_and_longer_after_it():
    # 1000 px right, then 1000 px down, at 100 px/s: the corner is at t = 10
    p = PursuitPath(1000, 1000, 100.0,
                    waypoints=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    before, after = pursuit.TURN_GUARD
    assert before < after
    assert p.near_turn(10.0 - 0.5 * before)
    assert not p.near_turn(10.0 - 1.5 * before)
    assert p.near_turn(10.0 + 0.9 * after)
    assert not p.near_turn(10.0 + 1.1 * after)
    assert p.near_turn(0.9 * after)                 # the ball starts moving
    assert not p.near_turn(1.1 * after)


def test_a_waypoint_on_a_straight_line_is_no_turn():
    p = PursuitPath(1000, 1000, 100.0,
                    waypoints=[(0.0, 0.0), (0.5, 0.0), (1.0, 0.0)])
    assert not p.near_turn(5.0)


@pytest.mark.parametrize("layout", ["rows", "grid"])
@pytest.mark.parametrize("speed", [380.0, 480.0, 600.0, 900.0])
def test_the_guard_shrinks_on_short_segments_so_half_of_each_survives(speed,
                                                                      layout):
    # A small screen: the vertical links last well under a second.
    p = PursuitPath(1024, 768, speed, layout=layout)
    for i, (a, b) in enumerate(zip(p._cum, p._cum[1:])):
        dur = (b - a) / speed
        ts = [a / speed + dur * k / 200.0 for k in range(1, 200)]
        kept = sum(not p.near_turn(t) for t in ts) / len(ts)
        assert kept >= 0.49, (i, dur, kept)


def test_on_a_large_screen_the_guard_is_the_full_one():
    p = PursuitPath(2560, 1440, 480.0, layout="grid")
    before, after = pursuit.TURN_GUARD
    c = p._cum[1] / p.speed               # first corner (a real turn)
    assert p.near_turn(c - 0.9 * before) and p.near_turn(c + 0.9 * after)


def test_a_path_without_a_turn_guard_keeps_the_old_rule():
    """Samples recorded before the turn guard are re-paired exactly as they
    were: 0.35 s either side of every waypoint."""
    p = PursuitPath(1000, 500, 100.0, guard=None,
                    waypoints=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
    assert p.near_turn(10.3) and p.near_turn(9.7)
    assert not p.near_turn(10.4) and not p.near_turn(9.6)


@pytest.mark.parametrize("value,want", [
    (None, "rows"), ("", "rows"), ("grid", "grid"), (" Grid ", "grid"),
    ("izgara", "grid"), ("rows", "rows"), ("ROWS", "rows"), ("satir", "rows")])
def test_calibration_path_setting(value, want):
    said = []
    assert pursuit.layout_of(value, say=said.append) == want
    assert said == []


def test_an_unknown_calibration_path_says_so_and_uses_the_rows():
    said = []
    assert pursuit.layout_of("zigzag", say=said.append) == "rows"
    assert len(said) == 1 and "zigzag" in said[0]
    said[0].encode("ascii")
