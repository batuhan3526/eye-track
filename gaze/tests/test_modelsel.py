# gaze/tests/test_modelsel.py
"""Per-person, per-axis model selection, validated across posture.

One fixed model (every feature, quadratic eye terms) fitted the training
frames well and then flung the cursor the moment the user sat differently: a
head-position feature that only carried the posture of one pass looked like
signal inside that pass. The selection holds out whole passes, so a feature
has to keep working after a posture change to be chosen.
"""
import math

import numpy as np
import pytest

from eye_gaze import modelsel
from eye_gaze.mapping import GazeMapper, fit_with_rejection
from eye_gaze.pursuit import PursuitPath

SW, SH = 1280, 720
HEAD = "pe0-hp1-ht0-dg0"            # gx, gy, hx, hy


def _targets(rng, n):
    return rng.uniform(0.05 * SW, 0.95 * SW, n), rng.uniform(0.05 * SH, 0.95 * SH, n)


def _posture_offset(n=300, seed=1):
    """Head rotation tracks the target cleanly INSIDE a pass, but carries a
    large offset that changes between passes (the user sat differently)."""
    rng = np.random.default_rng(seed)
    g, s, grp = [], [], []
    for p, off in enumerate((0.0, 0.05)):
        x, y = _targets(rng, n)
        xn, yn = x / SW - 0.5, y / SH - 0.5
        gx = 0.2 * xn + rng.normal(0, 0.02, n)
        gy = 0.2 * yn + rng.normal(0, 0.02, n)
        hx = 0.1 * xn + rng.normal(0, 0.002, n) + off
        hy = 0.1 * yn + rng.normal(0, 0.002, n) + off
        g += list(zip(gx, gy, hx, hy))
        s += list(zip(x, y))
        grp += [p] * n
    return g, s, grp


def _useful_head(n=300, seed=2):
    """The screen point depends on eye AND head rotation, the same way in
    both passes: eye features alone cannot tell where the user looks."""
    rng = np.random.default_rng(seed)
    g, s, grp = [], [], []
    for p in range(2):
        x, y = _targets(rng, n)
        xn, yn = x / SW - 0.5, y / SH - 0.5
        hx = rng.normal(0, 0.05, n)
        hy = rng.normal(0, 0.05, n)
        gx = (xn - 2.0 * hx) / 5.0 + rng.normal(0, 0.002, n)
        gy = (yn - 2.0 * hy) / 5.0 + rng.normal(0, 0.002, n)
        g += list(zip(gx, gy, hx, hy))
        s += list(zip(x, y))
        grp += [p] * n
    return g, s, grp


def _eye_only(curve, n=400, seed=3, noise=0.001):
    rng = np.random.default_rng(seed)
    x, y = _targets(rng, n)
    xn, yn = x / SW - 0.5, y / SH - 0.5
    gx = 0.4 * xn + curve * (0.4 * xn) ** 2 + rng.normal(0, noise, n)
    gy = 0.3 * yn + curve * (0.3 * yn) ** 2 + rng.normal(0, noise, n)
    return list(zip(gx, gy)), list(zip(x, y))


def _cross_pass_rmse(mapper_factory, g, s, grp):
    """Worst-direction RMSE (px) training on one pass, testing on the other."""
    g, s, grp = np.array(g), np.array(s), np.array(grp)
    worst = 0.0
    for tr, te in ((0, 1), (1, 0)):
        m = mapper_factory([tuple(v) for v in g[grp == tr]],
                           [tuple(v) for v in s[grp == tr]])
        pred = np.array([m.predict(tuple(v)) for v in g[grp == te]])
        rmse = np.sqrt(((pred - s[grp == te]) ** 2).mean(axis=0))
        worst = max(worst, float(rmse.max()))
    return worst


# --- the feature layout ------------------------------------------------------


def test_every_flag_combination_has_a_layout_matching_its_count():
    seen = set()
    for pe in (False, True):
        for hp in (False, True):
            for ht in (False, True):
                for dg in (False, True):
                    n = modelsel.input_count(pe, hp, ht, dg)
                    base = 2 + (2 if pe else 0) + (2 if hp else 0) \
                        + (3 if (hp and ht) else 0)
                    assert n == base + (2 if dg else 0)
                    groups = modelsel.feature_groups(
                        n, "pe%d-hp%d-ht%d-dg%d" % (pe, hp, hp and ht, dg))
                    flat = sorted(i for v in groups.values() for i in v)
                    assert flat == list(range(n))
                    assert groups["eye"] == [0, 1]
                    assert ("deep" in groups) == dg
                    seen.add(n)
    assert seen == {2, 4, 6, 7, 8, 9, 11}


def test_the_full_layout_names_each_feature():
    g = modelsel.feature_groups(11, "pe1-hp1-ht1-dg1")
    assert g == {"eye": [0, 1], "eyediff": [2, 3], "headrot": [4, 5],
                 "headpos": [6, 7, 8], "deep": [9, 10]}


def test_without_a_stamp_only_an_unambiguous_count_is_trusted():
    assert modelsel.feature_groups(2) == {"eye": [0, 1]}
    assert modelsel.feature_groups(11)["deep"] == [9, 10]
    assert modelsel.feature_groups(7) == {"eye": [0, 1], "headrot": [2, 3],
                                          "headpos": [4, 5, 6]}
    # 9 inputs is pe1-hp1-ht1-dg0 OR pe0-hp1-ht1-dg1: which pair is deep?
    assert modelsel.feature_groups(9) == {"eye": [0, 1],
                                          "rest": list(range(2, 9))}
    # a stamp that disagrees with the count is not believed
    assert modelsel.feature_groups(9, "pe1-hp1-ht1-dg1") == {
        "eye": [0, 1], "rest": list(range(2, 9))}


def test_the_ladder_is_nested_from_simplest_to_richest():
    full = modelsel.feature_groups(11, "pe1-hp1-ht1-dg1")
    steps = [idx for _, idx in modelsel.ladder(full)]
    assert steps == [(9, 10), (0, 1, 9, 10), (0, 1, 2, 3, 9, 10),
                     (0, 1, 2, 3, 4, 5, 9, 10), tuple(range(11))]
    assert modelsel.primary_pair(full) == (9, 10)
    eye = modelsel.feature_groups(2)
    assert [idx for _, idx in modelsel.ladder(eye)] == [(0, 1)]
    assert modelsel.primary_pair(eye) == (0, 1)
    head = modelsel.feature_groups(7)
    assert [idx for _, idx in modelsel.ladder(head)] == [
        (0, 1), (0, 1, 2, 3), tuple(range(7))]


def test_passes_are_recovered_from_time_resets():
    t = [0.0, 0.1, 0.2, 0.05, 0.3, 0.0, 0.4]
    assert modelsel.groups_from_t(t).tolist() == [0, 0, 0, 1, 1, 2, 2]
    assert modelsel.groups_from_t([]).tolist() == []


# --- selection ---------------------------------------------------------------


def test_a_head_feature_that_is_only_posture_is_dropped():
    g, s, grp = _posture_offset()
    sel = modelsel.select(g, s, grp, feat=HEAD, reach=0.25)
    assert sel is not None
    rep = sel.report
    assert rep["cv"] == "pass" and rep["k"] == 2
    assert "headrot" not in rep["x"]["groups"]
    assert "headrot" not in rep["y"]["groups"]
    # the fixed all-features model flings across posture; the selected one
    # does not
    legacy = modelsel.validate(g, s, grp, reach=0.25, poly2=True)
    assert rep["err"] < 0.5 * legacy["err"]
    fixed = _cross_pass_rmse(
        lambda a, b: fit_with_rejection(a, b, 0.25, poly2=True)[0], g, s, grp)
    chosen = _cross_pass_rmse(
        lambda a, b: fit_with_rejection(a, b, 0.25, axes=sel.mapper.axes)[0],
        g, s, grp)
    assert chosen < 0.5 * fixed


def test_a_genuinely_useful_head_feature_is_kept():
    g, s, grp = _useful_head()
    sel = modelsel.select(g, s, grp, feat=HEAD, reach=0.25)
    assert "headrot" in sel.report["x"]["groups"]
    assert "headrot" in sel.report["y"]["groups"]
    assert sel.err_cv < 40


def test_quadratic_terms_only_when_the_truth_is_curved():
    g, s = _eye_only(curve=0.0)
    lin = modelsel.select(g, s, None, reach=0.25)
    assert lin.report["x"]["quad"] is None and lin.report["y"]["quad"] is None
    g, s = _eye_only(curve=3.0)
    cur = modelsel.select(g, s, None, reach=0.25)
    assert cur.report["x"]["quad"] == [0, 1]
    assert cur.report["y"]["quad"] == [0, 1]


def test_quadratic_terms_are_not_offered_when_the_option_is_off():
    g, s = _eye_only(curve=3.0)
    sel = modelsel.select(g, s, None, reach=0.25, allow_quad=False)
    assert sel.report["x"]["quad"] is None and sel.report["y"]["quad"] is None


def test_one_group_is_validated_on_contiguous_blocks():
    g, s = _eye_only(curve=0.0)
    rep = modelsel.select(g, s, [0] * len(g), reach=0.25).report
    assert rep["cv"] == "blocks" and rep["k"] == 5


def test_the_report_has_what_the_grade_and_the_screen_need():
    g, s, grp = _posture_offset()
    sel = modelsel.select(g, s, grp, feat=HEAD, reach=0.25)
    rep = sel.report
    assert sel.mapper.selection == rep
    assert sel.err_cv == rep["err"] and sel.mapper.err_cv == rep["err"]
    for ax in ("x", "y"):
        a = rep[ax]
        assert a["rmse"] > 0 and 0.5 < a["slope"] < 1.2
        assert a["lam"] in (1e-3, 1e-2, 1e-1)
        assert set(a["groups"]) <= {"eye", "headrot"}
    assert rep["n"] == len(g)


def test_the_selected_mapper_survives_the_file_and_a_rescale():
    import json
    g, s, grp = _useful_head()
    m = modelsel.select(g, s, grp, feat=HEAD, reach=0.25).mapper
    d = json.loads(json.dumps(m.to_dict()))
    m2 = GazeMapper.from_dict(d)
    assert m2.n_inputs == 4
    assert m2.selection == m.selection
    for v in g[:20]:
        assert m2.predict(v) == m.predict(v)
    m2.screen = (SW, SH)
    big = m2.rescaled(2 * SW, 2 * SH)
    assert big is not m2 and big.selection == m.selection
    for v in g[:5]:
        px, py = m2.predict(v)
        bx, by = big.predict(v)
        assert bx == pytest.approx(2 * px) and by == pytest.approx(2 * py)


def test_too_few_points_select_nothing():
    assert modelsel.select([(0.1, 0.2)] * 5, [(1.0, 2.0)] * 5, None) is None
    assert modelsel.validate([(0.1, 0.2)] * 5, [(1.0, 2.0)] * 5, None) is None


# --- pursuit: the eye lag is chosen on held-out frames ----------------------


def _pursuit(lat, passes=2, seed=5, noise=0.002):
    path = PursuitPath(SW, SH, 480.0)
    rng = np.random.default_rng(seed)
    samples, grp = [], []
    for p in range(passes):
        t = 0.0
        while t < path.duration + 0.2:
            x, y = path.pos(t - lat)
            gx = (x / SW - 0.5) * 0.4 + rng.normal(0, noise)
            gy = (y / SH - 0.5) * 0.3 + rng.normal(0, noise)
            samples.append(((gx, gy), t))
            grp.append(p)
            t += 1 / 30.0
    return samples, path, grp


def test_the_eye_lag_is_chosen_on_held_out_passes():
    samples, path, grp = _pursuit(lat=0.18)
    sel, lat = modelsel.select_pursuit(samples, path, grp, reach=0.25)
    assert lat == pytest.approx(0.18)
    assert sel.report["latency"] == pytest.approx(0.18)
    assert sel.report["cv"] == "pass"
    assert sel.err_cv < 40


def test_a_fixed_eye_lag_is_used_as_given():
    samples, path, grp = _pursuit(lat=0.18)
    sel, lat = modelsel.select_pursuit(samples, path, grp, reach=0.25,
                                       latencies=(0.09,))
    assert lat == pytest.approx(0.09)


def test_the_lag_candidates_span_zero_to_half_a_second():
    lags = modelsel.LATENCIES
    assert lags[0] == 0.0 and 0.45 <= lags[-1] <= 0.51
    assert all(b - a == pytest.approx(0.03) for a, b in zip(lags, lags[1:]))


def test_without_groups_the_passes_are_recovered_from_t():
    samples, path, _ = _pursuit(lat=0.12)
    sel, _ = modelsel.select_pursuit(samples, path, None, reach=0.25)
    assert sel.report["cv"] == "pass" and sel.report["k"] == 2
