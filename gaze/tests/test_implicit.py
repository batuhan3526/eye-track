# gaze/tests/test_implicit.py
"""Implicit accuracy correction from the clicks that carry a REAL error.

An F6 magnifier click is two measurements of the same intent: the user first
aims with the raw gaze cursor, then picks the exact point inside the
magnified view. The confirmed point minus the pre-zoom cursor IS the gaze
error at that place on the screen. An ordinary click carries no such signal
(nobody knows where the user meant), so only the magnifier and F7 feed this.

Every correction is slow (one observation moves the cursor a third of the
way), bounded (implicit_max_px), local (a Gaussian kernel), decays with age,
and belongs to ONE calibration.
"""
import json
import math
import os

import pytest

from eye_gaze.implicit import ImplicitCorrector, implicit_path

SIG = "abc123"
DAY = 86400.0
REGION = (300, 200, 320, 180)     # x0, y0, w, h of a 25% magnifier at 1280x720


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _corr(tmp_path=None, clock=None, **kw):
    path = None if tmp_path is None else str(tmp_path / "calibration_implicit.json")
    said = []
    c = ImplicitCorrector(path=path, sig=SIG, clock=clock or Clock(),
                          log=said.append, **kw)
    return c, said


def _zoom(c, pre, err, region=REGION):
    """Pre-zoom cursor at `pre`, the user confirms pre + err (inside REGION)."""
    tgt = (pre[0] + err[0], pre[1] + err[1])
    return c.observe_zoom(pre, tgt, region)


def _click_at(c, target, true_err):
    """The user aims at `target`; raw gaze lands true_err short of it, the
    corrector moves it, and the magnifier reveals what is left."""
    raw = (target[0] - true_err[0], target[1] - true_err[1])
    cur = c.apply(*raw)
    return c.observe_zoom(cur, target, None)


def test_without_observations_nothing_moves():
    c, _ = _corr()
    assert c.apply(400.0, 300.0) == (400.0, 300.0)


def test_one_magnifier_click_corrects_a_third_of_the_error_locally():
    c, _ = _corr()
    assert _zoom(c, (450, 290), (30, -12)) is True
    ox, oy = c.offset(450, 290)
    assert ox == pytest.approx(10.0, abs=0.01)
    assert oy == pytest.approx(-4.0, abs=0.01)
    far = c.offset(1250, 700)
    assert math.hypot(*far) < 0.5


def test_a_stationary_error_is_learned_over_repeated_clicks():
    c, _ = _corr()
    target = (460.0, 280.0)
    true_err = (40.0, -25.0)
    raw = (target[0] - true_err[0], target[1] - true_err[1])
    first = None
    for _ in range(12):
        cur = c.apply(*raw)
        miss = math.hypot(target[0] - cur[0], target[1] - cur[1])
        first = miss if first is None else first
        _zoom(c, cur, (target[0] - cur[0], target[1] - cur[1]))
    cur = c.apply(*raw)
    assert math.hypot(target[0] - cur[0], target[1] - cur[1]) < 0.25 * first


def test_the_correction_is_capped():
    c, _ = _corr(max_px=50.0)
    for _ in range(30):
        _zoom(c, (460, 290), (150, 0), None)
    assert math.hypot(*c.offset(460, 290)) <= 50.0 + 1e-6


def test_huge_errors_are_not_learned():
    c, said = _corr(outlier_px=200.0)
    assert c.observe_zoom((100, 100), (100 + 260, 100), None) is False
    assert c.offset(100, 100) == (0.0, 0.0)


def test_a_target_on_the_magnifier_edge_is_not_learned():
    """The cursor stops at the screen edge inside the magnified view, so a
    target ON the region's border says only 'further than this'."""
    c, _ = _corr()
    x0, y0, w, h = REGION
    assert c.observe_zoom((x0 + w // 2, y0 + h // 2), (x0 + w - 1, y0 + 50),
                          REGION) is False
    assert c.observe_zoom((x0 + w // 2, y0 + h // 2), (x0 + 100, y0),
                          REGION) is False
    assert c.offset(x0 + w // 2, y0 + h // 2) == (0.0, 0.0)


def test_old_observations_fade():
    clock = Clock()
    c, _ = _corr(clock=clock, halflife_days=3.0)
    _zoom(c, (450, 290), (30, 0))
    fresh = c.offset(450, 290)[0]
    clock.t += 3 * DAY
    assert c.offset(450, 290)[0] < fresh * 0.75


def test_corrections_survive_a_restart(tmp_path):
    c, _ = _corr(tmp_path)
    _zoom(c, (450, 290), (30, -12))
    before = c.offset(450, 290)
    c2, said = _corr(tmp_path)
    assert c2.offset(450, 290) == pytest.approx(before)


def test_corrections_of_another_calibration_are_dropped_on_load(tmp_path):
    c, _ = _corr(tmp_path)
    _zoom(c, (450, 290), (30, -12))
    path = str(tmp_path / "calibration_implicit.json")
    c2 = ImplicitCorrector(path=path, sig="other", clock=Clock(),
                           log=lambda m: None)
    assert c2.offset(450, 290) == (0.0, 0.0)


def test_a_new_calibration_resets_the_corrections(tmp_path):
    c, _ = _corr(tmp_path)
    _zoom(c, (450, 290), (30, -12))
    c.bind(SIG)                                  # same calibration: kept
    assert c.offset(450, 290) != (0.0, 0.0)
    c.bind("new-calibration")
    assert c.offset(450, 290) == (0.0, 0.0)


def test_a_rejected_recalibration_does_not_wipe_the_saved_corrections(tmp_path):
    """F9 graded KOTU is used for the session but NOT saved: the next start
    loads the old calibration, and its corrections must still be there."""
    c, _ = _corr(tmp_path)
    _zoom(c, (450, 290), (30, -12))
    c.bind("session-only")                       # nothing learned afterwards
    c2, _ = _corr(tmp_path)
    assert c2.offset(450, 290) != (0.0, 0.0)


def test_disabled_does_nothing_and_writes_nothing(tmp_path):
    c, _ = _corr(tmp_path, enabled=False)
    assert _zoom(c, (450, 290), (30, -12)) is False
    assert c.apply(450.0, 290.0) == (450.0, 290.0)
    assert not os.path.exists(str(tmp_path / "calibration_implicit.json"))


def test_apply_never_raises_and_never_moves_on_a_fault():
    c, _ = _corr()
    _zoom(c, (450, 290), (30, -12))
    c._entries = [["bozuk"]]
    assert c.apply(450.0, 290.0) == (450.0, 290.0)
    assert c.apply(float("nan"), 290.0)[1] == 290.0


def test_f7_moves_the_global_level_into_drift_and_keeps_the_shape():
    """F7 sets the global offset (drift). Whatever global level the field had
    learned must leave it, or the two would add up and overshoot; the LOCAL
    differences between areas stay."""
    c, _ = _corr(sigma=200.0)
    for _ in range(6):
        _click_at(c, (200, 360), (60, 0))     # left: the gaze is 60 px short
        _click_at(c, (1080, 360), (20, 0))    # right: 20 px short
    left0, right0 = c.offset(200, 360)[0], c.offset(1080, 360)[0]
    centre = (640, 360)
    c.observe_drift(centre, (40.0, 0.0))      # F7: 40 px short at the centre
    assert abs(c.offset(*centre)[0]) < 3.0
    left1, right1 = c.offset(200, 360)[0], c.offset(1080, 360)[0]
    assert (left1 - right1) == pytest.approx(left0 - right0, rel=0.35)
    assert left1 > right1


A, B, CENTRE = (300, 360), (1200, 650), (640, 360)


def _learn_a_left_error_and_an_accurate_right(c):
    for _ in range(3):
        _click_at(c, A, (60, 0))               # gaze 60 px short near A
        _click_at(c, B, (0, 0))                # accurate near B


def test_f7_that_finds_the_centre_accurate_leaves_the_field_alone():
    """The shift is the CHANGE in drift, not the field's local mean near the
    centre: an accurate F7 must not push an accurate area off, nor wipe the
    correction learned elsewhere."""
    c, _ = _corr(sigma=0.15 * 1280)
    _learn_a_left_error_and_an_accurate_right(c)
    a0 = c.offset(*A)[0]
    assert a0 > 25
    c.observe_drift(CENTRE, (0.0, 0.0))
    assert abs(c.offset(*B)[0]) < 3.0
    assert c.offset(*A)[0] > 0.8 * a0


def test_f7_moves_a_learned_global_error_into_drift_for_this_session():
    c, _ = _corr(sigma=0.15 * 1280)
    for p in (A, B, CENTRE, (1000, 150)):
        for _ in range(4):
            _click_at(c, p, (50, 0))           # 50 px short everywhere
    c.observe_drift(CENTRE, (50.0, 0.0))
    for p in (A, B, CENTRE):
        assert abs(c.offset(*p)[0]) < 6.0      # drift does it now: no overshoot


def test_f7_does_not_erase_the_learned_correction_for_the_next_session(tmp_path):
    """Drift lives for one session; the file must not keep a shift made
    against it. The next start (drift 0) still gets the learned level."""
    c, _ = _corr(tmp_path, sigma=0.15 * 1280)
    for p in (A, B, CENTRE, (1000, 150)):
        for _ in range(4):
            _click_at(c, p, (50, 0))
    before = [c.offset(*p)[0] for p in (A, B)]
    c.observe_drift(CENTRE, (50.0, 0.0))
    c2, _ = _corr(tmp_path, sigma=0.15 * 1280)
    after = [c2.offset(*p)[0] for p in (A, B)]
    assert after == pytest.approx(before, abs=6.0)


def test_a_magnifier_click_under_drift_is_kept_for_the_next_session(tmp_path):
    """Learned while drift was 30: the error it reveals is on top of the
    drift, and the next session starts without that drift."""
    c, _ = _corr(tmp_path)
    c.observe_drift(CENTRE, (30.0, 0.0))
    for _ in range(6):
        _zoom(c, (450, 290), (0, 0))           # accurate WITH the drift
    assert abs(c.offset(450, 290)[0]) < 3.0
    c2, _ = _corr(tmp_path)
    assert c2.offset(450, 290)[0] > 20.0


def test_a_recalibration_drops_the_drift_the_field_is_relative_to():
    c, _ = _corr(sigma=0.15 * 1280)
    _learn_a_left_error_and_an_accurate_right(c)
    a0 = c.offset(*A)[0]
    c.observe_drift(CENTRE, (40.0, 0.0))
    c.set_drift((0.0, 0.0))                    # F9: the driver zeroes drift
    assert c.offset(*A)[0] == pytest.approx(a0, abs=8.0)


def test_f7_is_remembered_as_an_anchor(tmp_path):
    c, _ = _corr(tmp_path)
    c.observe_drift((640, 360), (0.0, 0.0))
    with open(str(tmp_path / "calibration_implicit.json"), encoding="utf-8") as f:
        d = json.load(f)
    assert len(d["sets"][SIG]) == 1


def test_a_gpu_flap_does_not_wipe_the_other_calibrations_corrections(tmp_path):
    """deep_gaze: auto swaps between two calibrations (11 and 9 inputs) when
    the GPU comes and goes. Learning under one must not erase the other."""
    path = str(tmp_path / "calibration_implicit.json")
    a = ImplicitCorrector(path=path, sig="A", clock=Clock(), log=lambda m: None)
    _zoom(a, (450, 290), (30, -12))
    b = ImplicitCorrector(path=path, sig="B", clock=Clock(), log=lambda m: None)
    assert b.offset(450, 290) == (0.0, 0.0)
    _zoom(b, (450, 290), (-20, 5))
    a2 = ImplicitCorrector(path=path, sig="A", clock=Clock(), log=lambda m: None)
    assert a2.offset(450, 290) == pytest.approx(a.offset(450, 290))


def test_only_a_few_calibrations_are_remembered(tmp_path):
    path = str(tmp_path / "calibration_implicit.json")
    clock = Clock()
    for i in range(7):
        clock.t += 10
        c = ImplicitCorrector(path=path, sig="S%d" % i, clock=clock,
                              log=lambda m: None)
        _zoom(c, (450, 290), (10, 0))
    with open(path, encoding="utf-8") as f:
        sets = json.load(f)["sets"]
    assert len(sets) == 4 and "S6" in sets and "S0" not in sets


def test_entries_are_capped():
    c, _ = _corr(max_entries=20)
    for i in range(50):
        _zoom(c, (300 + i, 290), (5, 0), None)
    assert len(c._entries) == 20


def test_a_corrupt_file_starts_empty(tmp_path):
    p = tmp_path / "calibration_implicit.json"
    p.write_text("{bozuk", encoding="utf-8")
    c, said = _corr(tmp_path)
    assert c.offset(450, 290) == (0.0, 0.0)


def test_a_failed_write_is_not_fatal(tmp_path, monkeypatch):
    import eye_gaze.implicit as im
    c, said = _corr(tmp_path)

    def boom(*a, **k):
        raise OSError("disk")
    monkeypatch.setattr(im.os, "replace", boom)
    assert _zoom(c, (450, 290), (30, -12)) is True     # still learned in memory
    assert c.offset(450, 290) != (0.0, 0.0)
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".tmp")]


def test_messages_are_ascii(tmp_path):
    c, said = _corr(tmp_path)
    _zoom(c, (450, 290), (30, -12))
    c.observe_zoom((100, 100), (400, 100), None)
    c.observe_drift((640, 360), (0.0, 0.0))
    _corr(tmp_path)
    for m in said:
        m.encode("ascii")


def test_path_sits_beside_the_calibration(tmp_path):
    cal = str(tmp_path / "calibration.json")
    assert implicit_path(cal) == str(tmp_path / "calibration_implicit.json")
