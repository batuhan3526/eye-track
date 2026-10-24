# gaze/tests/test_mapping.py
import pytest
from eye_gaze.mapping import GazeMapper, fit_with_rejection


def _truth(gx, gy):
    # affine ground truth (matches the affine mapper)
    return (1920 * (0.1 + 0.8 * gx), 1080 * (0.15 + 0.7 * gy))


def test_fit_recovers_known_affine_mapping():
    gaze = [(x / 4.0, y / 4.0) for x in range(5) for y in range(5)]
    screen = [_truth(*g) for g in gaze]
    m = GazeMapper()
    m.fit(gaze, screen)
    px, py = m.predict((0.5, 0.5))
    tx, ty = _truth(0.5, 0.5)
    assert abs(px - tx) < 1.0 and abs(py - ty) < 1.0


def test_predict_clamps_out_of_range_gaze():
    # gaze far outside the calibrated range must NOT extrapolate off-screen;
    # it parks at the matching edge prediction instead.
    gaze = [(0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8), (0.5, 0.5)]
    screen = [_truth(*g) for g in gaze]
    m = GazeMapper()
    m.fit(gaze, screen)
    assert m.predict((10.0, 10.0)) == m.predict((0.8, 0.8))


def test_fit_predict_with_head_pose_features():
    # 4-dim feature (gx, gy, hx, hy); the mapper must handle arbitrary length
    vals = [0.0, 0.5, 1.0]
    gaze, screen = [], []
    for gx in vals:
        for gy in vals:
            hx, hy = 0.1 * gx, 0.2 * gy
            gaze.append((gx, gy, hx, hy))
            screen.append((1920 * (0.1 + 0.5 * gx + 0.3 * hx),
                           1080 * (0.2 + 0.6 * gy + 0.1 * hy)))
    m = GazeMapper()
    m.fit(gaze, screen)
    px, py = m.predict(gaze[4])
    assert abs(px - screen[4][0]) < 1.0 and abs(py - screen[4][1]) < 1.0


def test_reach_margin_extends_beyond_calibration():
    # with a reach margin, looking past the calibrated range reaches further
    # than the calibrated edge (so screen corners become reachable).
    gaze = [(0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8), (0.5, 0.5)]
    screen = [_truth(*g) for g in gaze]
    m = GazeMapper(reach_margin=0.5)
    m.fit(gaze, screen)
    far = m.predict((5.0, 5.0))
    edge = m.predict((0.8, 0.8))
    assert far[0] > edge[0] and far[1] > edge[1]


def test_too_few_points_raises():
    m = GazeMapper()
    with pytest.raises(ValueError):
        m.fit([(0, 0)], [(0, 0)])


def test_predict_before_fit_raises():
    with pytest.raises(RuntimeError):
        GazeMapper().predict((0.5, 0.5))


def test_predictions_capped_near_calibrated_screen_area():
    # even with an absurd reach margin and far-out gaze, predictions must
    # stay within ~30% beyond the calibrated screen targets - the cursor can
    # never be flung thousands of px off screen by a noisy fit.
    gaze = [(0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8), (0.5, 0.5)]
    screen = [_truth(*g) for g in gaze]
    m = GazeMapper(reach_margin=2.0)
    m.fit(gaze, screen)
    px, py = m.predict((100.0, 100.0))
    xs = [s[0] for s in screen]
    ys = [s[1] for s in screen]
    assert px <= max(xs) + 0.31 * (max(xs) - min(xs))
    assert py <= max(ys) + 0.31 * (max(ys) - min(ys))


def test_near_constant_feature_cannot_explode_predictions():
    # Reproduces the field bug: head-position features barely vary during
    # calibration (user holds still), then drift outside that tiny range in
    # live use. Without regularisation the fit puts huge coefficients on
    # those columns and predictions fly thousands of px off screen.
    import random
    rnd = random.Random(1)
    gaze, screen = [], []
    for x in range(4):
        for y in range(4):
            gx, gy = x / 3.0, y / 3.0
            nx = 0.5 + rnd.uniform(-0.002, 0.002)   # near-constant nose x
            s = 0.30 + rnd.uniform(-0.001, 0.001)   # near-constant eye width
            gaze.append((gx, gy, nx, s))
            screen.append((1920 * gx + rnd.uniform(-5, 5),
                           1080 * gy + rnd.uniform(-5, 5)))
    m = GazeMapper(reach_margin=0.25)
    m.fit(gaze, screen)
    px, py = m.predict((0.5, 0.5, 0.60, 0.35))  # head far outside range
    assert -400 < px < 2320 and -300 < py < 1380  # near-screen, not +3000px


def test_fit_with_rejection_drops_corrupted_points():
    # 16 clean points + 2 garbage points (user blinked / glanced away during
    # calibration). Rejection must recover near-clean accuracy.
    vals = [i / 3.0 for i in range(4)]
    gaze = [(x, y) for x in vals for y in vals]
    screen = [_truth(*g) for g in gaze]
    bad_gaze = gaze + [(0.05, 0.9), (0.95, 0.1)]
    bad_screen = screen + [(1900, 50), (30, 1000)]  # wildly wrong targets
    m_plain = GazeMapper()
    m_plain.fit(bad_gaze, bad_screen)
    m_rej, err = fit_with_rejection(bad_gaze, bad_screen)
    tx, ty = _truth(0.5, 0.5)
    px, py = m_rej.predict((0.5, 0.5))
    qx, qy = m_plain.predict((0.5, 0.5))
    clean_err = abs(px - tx) + abs(py - ty)
    plain_err = abs(qx - tx) + abs(qy - ty)
    assert clean_err < plain_err / 2  # much closer to truth than naive fit
    assert err < 30  # reported mean residual reflects the cleaned fit


def test_fit_with_rejection_reports_error_and_handles_failure():
    m, err = fit_with_rejection([(0, 0)], [(0, 0)])
    assert m is None and err is None
    gaze = [(x / 4.0, y / 4.0) for x in range(5) for y in range(5)]
    screen = [_truth(*g) for g in gaze]
    m, err = fit_with_rejection(gaze, screen)
    assert m is not None and err < 1.0  # noiseless data fits ~exactly


def test_fit_with_rejection_poly2_falls_back_when_too_few_points():
    # 6 points cannot support quadratic+head features; must fall back to
    # affine instead of failing outright.
    gaze = [(0.2, 0.2, 0.0, 0.0), (0.8, 0.2, 0.1, 0.0), (0.2, 0.8, 0.0, 0.1),
            (0.8, 0.8, 0.1, 0.1), (0.5, 0.5, 0.05, 0.05), (0.5, 0.2, 0.05, 0.0)]
    screen = [_truth(g[0], g[1]) for g in gaze]
    m, err = fit_with_rejection(gaze, screen, poly2=True)
    assert m is not None


def test_poly2_recovers_curved_mapping():
    # a mildly nonlinear gaze->screen relation that an affine fit cannot
    # represent exactly; the quadratic fit must recover it.
    def truth(gx, gy):
        return (1920 * (0.1 + 0.6 * gx + 0.2 * gx * gx),
                1080 * (0.1 + 0.5 * gy + 0.3 * gy * gy + 0.1 * gx * gy))
    vals = [i / 3.0 for i in range(4)]
    gaze = [(x, y) for x in vals for y in vals]
    screen = [truth(*g) for g in gaze]
    m = GazeMapper(poly2=True)
    m.fit(gaze, screen)
    px, py = m.predict((0.4, 0.55))
    tx, ty = truth(0.4, 0.55)
    assert abs(px - tx) < 1.0 and abs(py - ty) < 1.0


def test_poly2_survives_dict_roundtrip():
    import json
    vals = [i / 3.0 for i in range(4)]
    gaze = [(x, y) for x in vals for y in vals]
    screen = [(1920 * g[0] * g[0], 1080 * g[1]) for g in gaze]
    m = GazeMapper(poly2=True)
    m.fit(gaze, screen)
    m2 = GazeMapper.from_dict(json.loads(json.dumps(m.to_dict())))
    assert m2.predict((0.5, 0.5)) == m.predict((0.5, 0.5))


def test_n_inputs_is_raw_gaze_dims_even_with_poly2():
    vals = [i / 3.0 for i in range(4)]
    gaze = [(x, y) for x in vals for y in vals]
    screen = [(1920 * g[0], 1080 * g[1]) for g in gaze]
    m = GazeMapper(poly2=True)
    m.fit(gaze, screen)
    assert m.n_inputs == 2  # raw inputs, not expanded feature count


def test_n_inputs_reports_fitted_feature_count():
    m = GazeMapper()
    assert m.n_inputs is None
    gaze = [(0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8), (0.5, 0.5)]
    m.fit(gaze, [_truth(*g) for g in gaze])
    assert m.n_inputs == 2


def test_n_inputs_survives_dict_roundtrip():
    # a calibration saved with head-pose features (4 inputs) must be
    # detectable after reload, so the driver can reject it when
    # use_head_pose has been turned off (2 inputs) since it was saved.
    import json
    vals = [0.0, 0.5, 1.0]
    gaze = [(gx, gy, 0.1 * gx, 0.2 * gy) for gx in vals for gy in vals]
    screen = [(1920 * g[0], 1080 * g[1]) for g in gaze]
    m = GazeMapper()
    m.fit(gaze, screen)
    m2 = GazeMapper.from_dict(json.loads(json.dumps(m.to_dict())))
    assert m2.n_inputs == 4


def test_to_dict_from_dict_roundtrip():
    import json
    gaze = [(x / 4.0, y / 4.0) for x in range(5) for y in range(5)]
    screen = [_truth(*g) for g in gaze]
    m = GazeMapper(reach_margin=0.2)
    m.fit(gaze, screen)
    m2 = GazeMapper.from_dict(json.loads(json.dumps(m.to_dict())))
    assert m2.predict((0.5, 0.5)) == m.predict((0.5, 0.5))


# --- the quality number must be measured on points the fit did NOT see ------
#
# The label used to be the in-sample residual after outlier rejection. With 15
# coefficients and 32 dots that flatters badly (simulated: 73 px reported,
# 141 px on held-out points), and the field log agrees: F7 corrections of
# 300-970 px right after calibrations labelled ORTA.

import random

from eye_gaze.mapping import holdout_error


def _noisy(n, noise, seed=3):
    rnd = random.Random(seed)
    g = [(rnd.random(), rnd.random()) + tuple(rnd.random() for _ in range(9))
         for _ in range(n)]
    s = [(_truth(a[0], a[1])[0] + rnd.gauss(0, noise),
          _truth(a[0], a[1])[1] + rnd.gauss(0, noise)) for a in g]
    return g, s


def test_holdout_is_near_zero_for_a_perfect_relation():
    gaze = [(x / 6.0, y / 6.0) for x in range(7) for y in range(7)]
    screen = [_truth(*g) for g in gaze]
    assert holdout_error(gaze, screen, poly2=False) < 2.0


def test_holdout_exposes_overfitting_that_the_in_sample_error_hides():
    # 11 features, poly2, few points, noisy: the regime of a dots calibration
    g, s = _noisy(20, noise=40)
    _, in_sample = fit_with_rejection(g, s, poly2=True)
    held = holdout_error(g, s, poly2=True)
    assert held > 1.3 * in_sample


def test_contiguous_blocks_for_time_series():
    # Pursuit samples 1/30 s apart are near-duplicates: interleaved folds
    # would test each sample against its own neighbour.
    g, s = _noisy(300, noise=30)
    e = holdout_error(g, s, poly2=True, blocks=10)
    assert e is not None and e > 0


def test_too_few_points_gives_none():
    assert holdout_error([(0, 0)] * 3, [(0, 0)] * 3) is None


def test_one_nan_calibration_sample_does_not_poison_the_fit():
    """A single NaN point made every ridge coefficient NaN, and
    holdout_error then divided by zero - a crash at startup."""
    import math
    from eye_gaze.mapping import holdout_error
    g, s = [], []
    for i in range(5):
        for j in range(5):
            gx, gy = i / 4, j / 4
            g.append((gx, gy))
            s.append(_truth(gx, gy))
    g[7] = (float("nan"), 0.5)
    m, err = fit_with_rejection(g, s)
    assert m is not None and math.isfinite(err)
    px, py = m.predict((0.5, 0.5))
    assert math.isfinite(px) and math.isfinite(py)
    cv = holdout_error(g, s)
    assert cv is not None and math.isfinite(cv)


def test_a_changed_screen_is_an_exact_rescale_not_a_recalibration():
    """Windows scaling 150% -> 100% on the same monitor: every calibration
    target is a fraction of the screen, so the fit rescales exactly."""
    gaze = [(x / 4.0, y / 4.0) for x in range(5) for y in range(5)]

    def at(sw, sh):
        return [(sw * (0.1 + 0.8 * gx), sh * (0.15 + 0.7 * gy))
                for gx, gy in gaze]
    m = GazeMapper()
    m.fit(gaze, at(2560, 1440))
    m.screen = (2560, 1440)
    truth = GazeMapper()
    truth.fit(gaze, at(3840, 2160))
    r = m.rescaled(3840, 2160)
    for g in [(0.3, 0.7), (0.9, 0.1), (0.5, 0.5)]:
        assert r.predict(g) == pytest.approx(truth.predict(g), abs=0.5)
    assert r.screen == (3840, 2160)
    assert m.rescaled(2560, 1440) is m


def test_setup_stamp_round_trips_and_old_files_are_left_alone():
    m = GazeMapper()
    m.fit([(x / 4.0, y / 4.0) for x in range(5) for y in range(5)],
          [_truth(x / 4.0, y / 4.0) for x in range(5) for y in range(5)])
    m.screen, m.camera = (2560, 1440), (1280, 960)
    back = GazeMapper.from_dict(m.to_dict())
    assert back.screen == (2560, 1440) and back.camera == (1280, 960)
    d = m.to_dict()
    del d["screen"], d["camera"]
    old = GazeMapper.from_dict(d)              # a July file: no stamp
    assert old.screen is None and old.rescaled(1920, 1080) is old


# --- per-axis models; a v2 file from before them is read exactly as before ---

# A calibration.json written by 1.2.0 (poly2, four inputs), and what that
# version predicted from it, to the last bit.
_V2_FILE = {
    "v": 2, "reach": 0.25, "poly2": True,
    "cx": [-897.322407723128, 3076.234115673573, -81.96315253392149,
           56.88100731829802, 122.10978405278873, 90.23454670568184,
           97.87959092407351, -9.598162014139039],
    "cy": [-765.9992123526357, -64.11019022525632, 2114.282828703599,
           -134.6960513399056, -62.672962394239356, 128.465937012014,
           343.6461461700408, -77.7968127789979],
    "lo": [0.3209068926397473, 0.34668537049683384, -0.042640925614626186,
           -0.028942249448461747],
    "hi": [0.7127411381864391, 0.6475532244824462, 0.05034948075067841,
           0.0375368922256334],
    "slo": [39.44682824696244, 5.300834220723969],
    "shi": [1270.2407219914714, 696.6070073526312],
    "screen": [1280, 720], "camera": [640, 480], "err_cv": 87,
    "feat": "pe1-hp0-ht0-dg0"}
_V2_PREDICTIONS = [
    ((0.5, 0.5, 0.0, 0.0), (644.4420677506017, 357.6659244872999)),
    ((0.31, 0.36, 0.01, -0.02), (45.215627656993156, 23.374938529671923)),
    ((0.9, 0.1, 0.05, 0.05), (1639.478890114824, -161.24503824420344)),
    ((0.2, 0.8, -0.1, 0.1), (-213.78589128633294, 903.9988592922034)),
]


def test_a_v2_file_predicts_bit_identically():
    import json
    m = GazeMapper.from_dict(json.loads(json.dumps(_V2_FILE)))
    assert m.axes is None and m.selection is None
    assert m.n_inputs == 4
    for g, want in _V2_PREDICTIONS:
        assert m.predict(g) == want
    # and writes back without keys it never had
    d = m.to_dict()
    assert "axes" not in d and "sel" not in d and "open_eye" not in d
    assert GazeMapper.from_dict(d).predict(_V2_PREDICTIONS[0][0]) \
        == _V2_PREDICTIONS[0][1]


def _axes_data(n=60, seed=4):
    rnd = random.Random(seed)
    g = [(rnd.random(), rnd.random(), rnd.random(), rnd.random())
         for _ in range(n)]
    s = [(1920 * (0.1 + 0.8 * a[0]) + 50 * a[2], 1080 * (0.2 + 0.6 * a[1]))
         for a in g]
    return g, s


def test_each_axis_uses_only_its_own_inputs():
    g, s = _axes_data()
    axes = (((0, 2), None, 1e-3), ((1,), None, 1e-2))
    m = GazeMapper(reach_margin=0.25, axes=axes)
    m.fit(g, s)
    assert m.n_inputs == 4 and len(m.to_dict()["cx"]) == 3
    a = m.predict((0.5, 0.5, 0.5, 0.5))
    b = m.predict((0.5, 0.5, 0.5, 0.9))         # input 3 is used by neither
    c = m.predict((0.5, 0.5, 0.9, 0.5))         # input 2 only by x
    assert a == b
    assert c[0] != a[0] and c[1] == a[1]
    px, _ = m.predict((0.5, 0.5, 0.0, 0.0))
    assert px == pytest.approx(1920 * 0.5, abs=5)


def test_per_axis_quadratic_terms_round_trip():
    import json
    g, s = _axes_data()
    axes = (((0, 1), (0, 1), 1e-3), ((0, 1, 2), None, 1e-1))
    m = GazeMapper(reach_margin=0.1, axes=axes)
    m.fit(g, s)
    d = json.loads(json.dumps(m.to_dict()))
    assert d["axes"] == [{"in": [0, 1], "quad": [0, 1], "lam": 1e-3},
                         {"in": [0, 1, 2], "quad": None, "lam": 1e-1}]
    m2 = GazeMapper.from_dict(d)
    assert m2.axes == axes
    for v in g[:10]:
        assert m2.predict(v) == m.predict(v)


def test_per_axis_mapper_keeps_reach_clamp_and_screen_cap():
    g, s = _axes_data()
    m = GazeMapper(axes=(((0,), None, 1e-3), ((1,), None, 1e-3)))
    m.fit(g, s)
    assert m.predict((10.0, 10.0, 0, 0)) == m.predict((max(a[0] for a in g),
                                                       max(a[1] for a in g),
                                                       0, 0))


def test_a_malformed_axes_entry_is_refused():
    d = dict(_V2_FILE, axes=[{"in": [0, 9], "quad": None, "lam": 1e-3},
                             {"in": [1], "quad": None, "lam": 1e-3}])
    with pytest.raises(Exception):
        GazeMapper.from_dict(d)


def test_predict_many_matches_predict_row_by_row():
    import numpy as np
    g, s = _axes_data()
    far = [(2.0, -1.0, 0.5, 0.5), (-3.0, 4.0, 9.0, -9.0)]   # clamp + cap
    for m in (GazeMapper(reach_margin=0.25, poly2=True),
              GazeMapper(reach_margin=0.25),
              GazeMapper(reach_margin=0.1,
                         axes=(((0, 2), (0, 1), 1e-3), ((1,), None, 1e-2)))):
        m.fit(g, s)
        rows = g[:10] + far
        many = m.predict_many(rows)
        one = np.array([m.predict(v) for v in rows])
        assert np.allclose(many, one, rtol=1e-12, atol=1e-9)


def test_a_per_axis_mapper_is_written_as_version_3():
    """1.2.0 reads any "v": 2 file and ignores keys it does not know: a
    per-axis file written as 2 would be predicted with the wrong feature
    vector there. As 3 it is rejected and 1.2.0 restores its own backup."""
    g, s = _axes_data()
    m = GazeMapper(axes=(((0, 2), None, 1e-3), ((1,), None, 1e-2)))
    m.fit(g, s)
    assert m.to_dict()["v"] == 3
    assert GazeMapper.from_dict(_V2_FILE).to_dict()["v"] == 2
    plain = GazeMapper()
    plain.fit(g, s)
    assert plain.to_dict()["v"] == 2
