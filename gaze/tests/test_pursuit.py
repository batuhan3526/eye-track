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
