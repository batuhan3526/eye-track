# gaze/tests/test_calquality.py
"""How a calibration is graded and what the user is told about it.

The grade bands were pixels fixed for a 2560x1440 screen, so the same
accuracy graded better on a small screen than on a large one. They are now
a fraction of the screen diagonal - 100/200 px at 2560x1440 mean exactly
what they meant - and the driver, the startup refit and the save rule all
read them from the one place.
"""
import math

import numpy as np
import pytest

from eye_gaze import calquality as cq

FEAT = "pe0-hp1-ht1-dg0"        # eye 0-1, head rotation 2-3, position 4-6


# --- the bands ---------------------------------------------------------------


def test_the_bands_mean_what_they_meant_at_2560x1440():
    assert cq.bands((2560, 1440)) == pytest.approx((100.0, 200.0))
    assert cq.bands((2560, 1440), held_out=False) == pytest.approx((80.0, 160.0))


def test_an_unknown_screen_keeps_the_old_pixel_bands():
    for screen in (None, (0, 0), "bozuk", (float("nan"), 1)):
        assert cq.bands(screen) == pytest.approx((100.0, 200.0))


@pytest.mark.parametrize("sw,sh", [(1920, 1080), (1366, 768), (3840, 2160),
                                   (1280, 1024)])
def test_the_bands_scale_with_the_screen_diagonal(sw, sh):
    f = math.hypot(sw, sh) / math.hypot(2560, 1440)
    good, bad = cq.bands((sw, sh))
    assert good == pytest.approx(100.0 * f) and bad == pytest.approx(200.0 * f)
    assert cq.bands([sw, sh]) == cq.bands((sw, sh))     # as read from json


def test_labels():
    s = (1920, 1080)                       # bands 75 / 150 px
    assert cq.label(None, s) == "?"
    assert cq.label(70, s) == "IYI"
    assert cq.label(80, s) == "ORTA"
    assert cq.label(149, s) == "ORTA"
    assert cq.label(150, s) == "KOTU"
    assert cq.label(150, (2560, 1440)) == "ORTA"
    assert cq.is_bad(150, s) and not cq.is_bad(150, (2560, 1440))
    assert not cq.is_bad(None, s)
    assert cq.label(65, s, held_out=False) == "ORTA"    # 60 / 120 in-sample


def test_the_driver_and_the_refit_use_the_same_rule(tmp_path):
    import gaze_driver as gd
    from eye_gaze import caldata
    for err in (60, 80, 120, 160, 260):
        for screen in ((1920, 1080), (2560, 1440), None):
            assert gd._cal_label(err, screen) == cq.label(err, screen)
            assert caldata._label(err, screen) == cq.label(err, screen)
    assert caldata._WORSE == cq.WORSE


def test_the_save_rule_scales_with_the_screen(tmp_path):
    import gaze_driver as gd
    from eye_gaze.mapping import GazeMapper
    p = tmp_path / "calibration.json"
    m = GazeMapper()
    m.fit([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)],
          [(0, 0), (100, 0), (0, 100), (100, 100)])
    gd._save_calibration(str(p), m, err_cv=60)
    # 170 px: ORTA at 2560x1440, KOTU (and clearly worse) at 1920x1080
    assert gd._should_save(str(p), 170)[0] is True
    assert gd._should_save(str(p), 170, screen=(2560, 1440))[0] is True
    ok, why = gd._should_save(str(p), 170, screen=(1920, 1080))
    assert ok is False and "170" in why


# --- what is measured besides the grade ---------------------------------------


def _passes(shift, n=200, seed=1):
    """Two passes; the head features of the second sit `shift` (in units of
    the within-pass jitter) away from the first."""
    rng = np.random.default_rng(seed)
    g, grp = [], []
    for p in range(2):
        for _ in range(n):
            eye = rng.normal(0.5, 0.05, 2)
            head = rng.normal(p * shift * 0.01, 0.01, 5)
            g.append(tuple(eye) + tuple(head))
            grp.append(p)
    return np.array(g), np.array(grp)


def test_the_posture_shift_is_measured_on_the_head_features_only():
    g, grp = _passes(0.0)
    assert cq.head_shift(g, grp, FEAT) < 0.3
    g, grp = _passes(6.0)
    assert cq.head_shift(g, grp, FEAT) > 1.5
    g2 = g.copy()
    g2[grp == 1, 0] += 1.0                 # an eye feature moving is not posture
    g2[:, 2:] = g[grp == 0, 2:][np.arange(len(g)) % 200]
    assert cq.head_shift(g2, grp, FEAT) < 0.3


def test_no_posture_shift_without_head_features_or_passes():
    g, grp = _passes(6.0)
    assert cq.head_shift(g[:, :2], grp, "pe0-hp0-ht0-dg0") is None
    assert cq.head_shift(g, None, FEAT) is None
    assert cq.head_shift(g, np.zeros(len(g), dtype=int), FEAT) is None


def _report(err=90.0, x=(60.0, 0.95), y=(70.0, 0.9), posture=None):
    return {"err": err, "x": {"rmse": x[0], "slope": x[1]},
            "y": {"rmse": y[0], "slope": y[1]},
            "posture": None if posture is None else {"err": posture}}


def _keys(advice):
    return [k for k, _ in advice]


def test_a_good_calibration_gets_no_hints():
    assert cq.advice(_report(), 90.0, (2560, 1440), shift=2.0) == []


def test_poor_vertical_reach_names_the_axis_and_what_to_do():
    a = cq.advice(_report(y=(300.0, 0.23)), 160.0, (2560, 1440))
    keys = _keys(a)
    assert keys[0] == "hint_reach" and dict(a)["hint_reach"]["axis"] == "axis_y"
    for k in ("hint_head_still", "hint_light", "hint_camera"):
        assert k in keys
    a = cq.advice(_report(x=(300.0, 0.4), y=(300.0, 0.3)), 160.0, (2560, 1440))
    assert dict(a)["hint_reach"]["axis"] == "axis_xy"
    # exactly at the limit is not poor
    assert cq.advice(_report(y=(200.0, 0.5)), 150.0, (2560, 1440)) == []


def test_a_large_posture_shift_that_cost_accuracy_is_named():
    rep = _report(err=140.0, posture=300.0)
    a = cq.advice(rep, 140.0, (2560, 1440), shift=1.9)
    assert _keys(a) == ["hint_posture"]
    assert dict(a)["hint_posture"]["err"] == 300
    # the passes sat apart but the model coped: nothing to say
    assert cq.advice(_report(err=140.0, posture=160.0), 140.0, (2560, 1440),
                     shift=1.9) == []
    # the error after a posture change is bad, but posture did not change
    assert cq.advice(rep, 140.0, (2560, 1440), shift=0.4) == []
    assert cq.advice(rep, 140.0, (2560, 1440), shift=None) == []


def test_a_kotu_grade_says_so_first_and_names_the_key():
    a = cq.advice(_report(err=260.0), 260.0, (2560, 1440), key="F9")
    assert a[0] == ("bad", {"key": "F9"})
    assert cq.advice(None, 260.0, (2560, 1440), key="F9") == [("bad", {"key": "F9"})]
    assert cq.advice(None, None, (2560, 1440)) == []


def test_coverage_percent():
    assert cq.coverage_pct(0.23) == 23
    assert cq.coverage_pct(1.07) == 100
    assert cq.coverage_pct(-0.2) == 0
    assert cq.coverage_pct(None) is None


def test_summary_of_a_mapper():
    class M:
        err_cv = 260.0
        selection = _report(err=260.0, y=(300.0, 0.23))
        cal_samples = None
    s = cq.summary(M(), (2560, 1440), key="F9")
    assert s.label == "KOTU" and s.bad
    assert s.axes == (60, 300) and s.coverage == (95, 23)
    assert _keys(s.hints)[:2] == ["bad", "hint_reach"]

    class Old:                      # a fit without the per-axis selection
        err_cv = 90.0
    s = cq.summary(Old(), (2560, 1440))
    assert s.label == "IYI" and s.axes is None and s.coverage is None
    assert s.hints == []
