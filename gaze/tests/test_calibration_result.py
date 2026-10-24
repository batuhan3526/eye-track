# gaze/tests/test_calibration_result.py
"""The end of a calibration: what the user sees and what is kept.

The model selection takes a few seconds after the last point; the window
now says it is calculating instead of vanishing. The result - grade, error
per axis, how far the cursor reaches, and concrete hints - is shown on the
calibration window (the user cannot see the console) and printed. A KOTU
grade says so in red with the key to try again.
"""
import numpy as np
import pytest

import gaze_driver as gd
from eye_gaze import caldata, calquality, pursuit
from eye_gaze.pursuit import PursuitPath

from tests import test_kalibrasyon_veri_driver as kv


class TextCv2(kv.FakeCv2):
    def __init__(self):
        super().__init__()
        self.texts = []
        self.shown = 0

    def putText(self, img, text, *a, **k):
        self.texts.append(text)

    def imshow(self, *a, **k):
        self.shown += 1


class Keys(kv.FakeHotkeys):
    def __init__(self):
        self.fed = 0

    def feed_window_key(self, k):
        self.fed += 1


def _run_pursuit(monkeypatch, **kw):
    cv = TextCv2()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    monkeypatch.setattr(gd, "_pass_change_screen", lambda *a, **k: True)
    clock = {"t": 1000.0}

    def fake_time():
        clock["t"] += 1 / 60.0
        return clock["t"]
    monkeypatch.setattr(gd.time, "time", fake_time)
    monkeypatch.setattr(gd.time, "monotonic", fake_time)

    class BallEstimator(kv.DotEstimator):
        def estimate(self, frame):
            return kv._gaze_of(*self.cv.dot, sw=1280, sh=720)
    m = gd.calibrate_pursuit(BallEstimator(cv), kv.FakeCap(), 1280, 720,
                             Keys(), 0.25, poly2=False, passes=2,
                             speed=900.0, latency=0.06, **kw)
    return m, cv


def test_the_window_says_it_is_calculating_then_shows_the_result(monkeypatch,
                                                                 capsys):
    m, cv = _run_pursuit(monkeypatch)
    assert m is not None
    computing = gd.t_ascii("driver.calq.computing")
    assert computing in cv.texts
    i = cv.texts.index(computing)
    after = cv.texts[i + 1:]
    q = calquality.summary(m, (1280, 720))
    assert after[0] == gd.t_ascii(
        "driver.calq.result",
        grade=gd.t_ascii(gd._GRADE_KEY[q.label]),
        err="%d" % round(m.err_cv))
    assert any("px" in t and "/" not in t for t in after[1:])
    out = capsys.readouterr().out
    out.encode("ascii")
    # computing is said before the result, the per-axis lines after it
    assert out.index(computing) < out.index("Kalibrasyon hatasi")
    assert gd.t_ascii("driver.calq.axes", x=q.axes[0], y=q.axes[1]) in out
    assert gd.t_ascii("driver.calq.coverage", x=q.coverage[0],
                      y=q.coverage[1]) in out


def test_the_default_path_is_the_grid_and_rows_can_be_chosen(monkeypatch):
    m, _ = _run_pursuit(monkeypatch)
    grid = PursuitPath(1280, 720, 900.0)
    assert m.cal_samples.meta["path_pts"] == [list(p) for p in grid._pts]
    assert m.cal_samples.meta["guard"] == list(pursuit.TURN_GUARD)
    assert m.cal_samples.meta["screen"] == [1280, 720]
    m, _ = _run_pursuit(monkeypatch, layout="rows")
    rows = PursuitPath(1280, 720, 900.0, layout="rows")
    assert m.cal_samples.meta["path_pts"] == [list(p) for p in rows._pts]


def _bad_mapper():
    class M:
        err_cv = 260.0
        selection = {"err": 260.0, "x": {"rmse": 150.0, "slope": 0.9},
                     "y": {"rmse": 320.0, "slope": 0.2}, "posture": None}
        cal_samples = None
    return M()


def test_a_kotu_result_says_so_with_the_key_and_the_hints(monkeypatch, capsys):
    cv = TextCv2()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    keys = Keys()
    gd._show_cal_result("w", 2560, 1440, keys, _bad_mapper(), recal_key="f9")
    shown = " ".join(cv.texts)
    assert gd.t_ascii("driver.calq.grade_kotu") in cv.texts[0]
    assert "F9" in shown
    for k in ("hint_head_still", "hint_light", "hint_camera"):
        assert gd.t_ascii("driver.calq." + k)[:20] in shown
    assert gd.t_ascii("driver.calq.axis_y") in shown
    out = capsys.readouterr().out
    assert "F9" in out and gd.t_ascii("driver.calq.hint_light") in out
    out.encode("ascii")
    assert keys.fed >= 50                 # long enough to read
    assert cv.shown == 1


def test_the_result_screen_wraps_long_lines_to_the_window(monkeypatch):
    cv = TextCv2()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    gd._show_cal_result("w", 1024, 768, Keys(), _bad_mapper())
    assert max(len(t) for t in cv.texts) <= (1024 - 80) / 18


def test_a_quit_ends_the_result_screen(monkeypatch):
    cv = TextCv2()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    keys = Keys()
    keys.quit_requested = True
    gd._show_cal_result("w", 2560, 1440, keys, _bad_mapper())
    assert keys.fed == 1


def test_a_result_screen_fault_never_costs_the_calibration(monkeypatch):
    class Boom(TextCv2):
        def imshow(self, *a, **k):
            raise RuntimeError("no window")
    monkeypatch.setattr(gd, "cv2", Boom())
    gd._show_cal_result("w", 2560, 1440, Keys(), _bad_mapper())   # no raise
    gd._show_computing("w", 2560, 1440)


# --- samples remember the turn guard -------------------------------------------


def test_new_samples_round_trip_their_turn_guard(tmp_path):
    path = PursuitPath(1280, 720, 480.0)
    samples = [((float(i), 0.0), i / 30.0) for i in range(300)]
    cs = caldata.CalSamples.pursuit(samples, path, 0.15, kv.OPTS)
    cal = str(tmp_path / "calibration.json")
    caldata.save_samples(cal, cs, kv._fitted(), feat="x")
    back = caldata.load_samples(caldata.samples_path(cal, "x"))
    assert back.path().guard == pytest.approx(pursuit.TURN_GUARD)
    assert back.screen_size == [1280, 720]
    assert back.pairs()[1] == cs.pairs()[1]


def test_old_samples_without_a_guard_pair_exactly_as_before(tmp_path):
    """A 1.2.0 samples file: the rows path, no stored guard. Its pairs must
    be the ones the 0.35 s rule cut, so a refit sees what it always saw."""
    path = PursuitPath(1280, 720, 480.0, layout="rows")
    samples = [((float(i), 0.0), i / 30.0) for i in range(int(path.duration * 30))]
    cs = caldata.CalSamples.pursuit(samples, path, 0.15, kv.OPTS)
    del cs.meta["guard"], cs.meta["screen"]
    assert cs.path().guard is None
    assert cs.screen_size is None
    want = [s for s in samples
            if 0 <= s[1] - 0.15 <= path.duration
            and not any(abs((s[1] - 0.15) * 480.0 - c) <= 0.35 * 480.0
                        for c in path._cum)]
    assert cs.pairs()[0] == [g for g, _ in want]


def test_a_broken_stored_guard_falls_back_to_the_legacy_rule():
    for bad in ("x", [1], [float("nan"), 1.0], [-1, 0.3], None):
        assert caldata._guard(bad) is None
    assert caldata._guard([0.1, 0.35]) == (0.1, 0.35)
