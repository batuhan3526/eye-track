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
from eye_gaze import caldata, calquality, pursuit, textfit
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


def test_the_default_path_is_the_rows_and_grid_can_be_chosen(monkeypatch):
    m, _ = _run_pursuit(monkeypatch)
    rows = PursuitPath(1280, 720, 900.0, layout="rows")
    assert m.cal_samples.meta["path_pts"] == [list(p) for p in rows._pts]
    assert m.cal_samples.meta["guard"] == list(pursuit.TURN_GUARD)
    assert m.cal_samples.meta["screen"] == [1280, 720]
    m, _ = _run_pursuit(monkeypatch, layout="grid")
    grid = PursuitPath(1280, 720, 900.0, layout="grid")
    assert m.cal_samples.meta["path_pts"] == [list(p) for p in grid._pts]


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


class PlaceCv2(TextCv2):
    """Keeps where and how big every line was drawn."""

    def __init__(self):
        super().__init__()
        self.placed = []

    def putText(self, img, text, org, font, scale, colour, thick, *a, **k):
        self.texts.append(text)
        self.placed.append((text, org, scale, thick))


def _full_summary():
    """Every line the result screen can show: KOTU, both axes, reach, the
    posture hint and all the general advice."""
    return calquality.Summary(
        "KOTU", 260.0, True, (150, 320), (90, 20), 0.9,
        [("bad", {"key": "F9"}), ("hint_posture", {"err": 540}),
         ("hint_reach", {"axis": "axis_xy"}), ("hint_head_still", {}),
         ("hint_light", {}), ("hint_camera", {})])


def _shown_on(monkeypatch, sw, sh, q):
    import cv2 as real_cv2
    cv = PlaceCv2()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    monkeypatch.setattr(gd.calquality, "summary", lambda *a, **k: q)
    gd._show_cal_result("w", sw, sh, Keys(), _bad_mapper())
    boxes = []
    for text, (x, y), scale, thick in cv.placed:
        (w, h), base = real_cv2.getTextSize(
            text, real_cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
        boxes.append((text, x, y - h, x + w, y + base, scale))
    return cv, boxes


def _drawn(cv):
    return " ".join(" ".join(t.split()) for t in cv.texts)


SCREENS = [(1024, 600), (1024, 768), (1280, 720), (1366, 768), (1920, 1080),
           (2560, 1440)]


@pytest.mark.parametrize("lang", ["tr", "en"])
@pytest.mark.parametrize("screen", SCREENS)
def test_the_whole_result_fits_the_screen(monkeypatch, lang, screen):
    """1280x720 is a 1080p laptop at 150 % to this DPI-unaware process: the
    camera and light hints used to be drawn below the bottom edge there."""
    from eye_gaze import i18n
    i18n.set_language(lang)
    sw, sh = screen
    q = _full_summary()
    cv, boxes = _shown_on(monkeypatch, sw, sh, q)
    for text, x0, y0, x1, y1, _ in boxes:
        assert 0 <= x0 and x1 <= sw, (text, screen)
        assert 0 <= y0 and y1 <= sh, (text, screen)
    # nothing overlaps: every line starts below the one before it
    for a, b in zip(boxes, boxes[1:]):
        assert b[2] >= a[4] - 2, (a[0], b[0])
    drawn = _drawn(cv)
    for text, _, drop in gd._cal_result_lines(q):
        if drop is None:                    # grade, per-axis, KOTU + key
            assert " ".join(text.split()) in drawn, (text, screen)
    assert "F9" in drawn
    if screen[0] >= 1280:                   # room for everything there
        for text, _, _ in gd._cal_result_lines(q):
            assert " ".join(text.split()) in drawn, (text, screen)
    # readable: the body text never below textfit's smallest size
    body = [b[5] for b in boxes[1:]]
    assert min(body) >= textfit.BODY_SCALE * textfit.MIN_K - 1e-9


def test_a_large_screen_keeps_the_full_size_look(monkeypatch):
    cv, boxes = _shown_on(monkeypatch, 2560, 1440, _full_summary())
    assert boxes[0][5] == pytest.approx(textfit.TITLE_SCALE)
    assert all(b[5] == pytest.approx(textfit.BODY_SCALE) for b in boxes[1:])
    assert cv.placed[0][1] == (textfit.MARGIN_X, 1440 // 6)


@pytest.mark.parametrize("lang", ["tr", "en"])
def test_a_tiny_screen_drops_the_general_hints_first(monkeypatch, lang):
    from eye_gaze import i18n
    i18n.set_language(lang)
    q = _full_summary()
    lines = gd._cal_result_lines(q)
    cv, boxes = _shown_on(monkeypatch, 800, 360, q)
    drawn = _drawn(cv)
    shown = {d for text, _, d in lines if " ".join(text.split()) in drawn}
    for text, _, drop in lines:
        if drop is None:
            assert " ".join(text.split()) in drawn, text
    assert gd._RESULT_DROP["hint_camera"] not in shown
    ranks = sorted(v for v in gd._RESULT_DROP.values())
    gone = [r for r in ranks if r not in shown]
    assert gone == ranks[:len(gone)]       # the most general ones went
    for text, x0, y0, x1, y1, _ in boxes:
        assert 0 <= y0 and y1 <= 360, text


def test_the_layout_never_raises():
    placed, dropped = textfit.fit([("a b c", (1, 2, 3), None)], 10, 10,
                                  measure=lambda *a: 1 / 0)
    assert placed and placed[0][0] == "a b c" and dropped == []
    placed, _ = textfit.fit([("x " * 40, (0, 0, 0), None)] * 30, 300, 100)
    assert placed                           # forced onto the screen's top


def test_wrap_breaks_on_words_by_measured_width():
    width = lambda text, scale, thick: 10 * len(text)   # noqa: E731
    assert textfit.wrap("aa bb cc dd", 50, 1.0, measure=width) ==         ["aa bb", "cc dd"]
    assert textfit.wrap("toolongword x", 30, 1.0, measure=width) ==         ["toolongword", "x"]
    assert textfit.wrap("", 30, 1.0, measure=width) == [""]


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


# --- "press F9 twice to try again" works on the result screen -----------------


class KeyCv2(TextCv2):
    """Reports the window-mode recalibrate key: always, or only on the
    result screen (the only place that waits 100 ms per key)."""

    def __init__(self, char, only_result=False):
        super().__init__()
        self.char, self.only_result = char, only_result

    def waitKey(self, ms):
        if self.only_result and ms != 100:
            return -1
        return ord(self.char)


def _window_hotkeys(monkeypatch):
    from eye_gaze import hotkeys as hk_mod
    monkeypatch.setattr(hk_mod._platform, "keyboard_api",
                        lambda: (_ for _ in ()).throw(ImportError("none")))
    hk = hk_mod.Hotkeys("f10", "f9", "f8", interval=1.0, drift_key="f7")
    assert hk.mode == "window"
    return hk


def test_recal_on_the_result_screen_ends_it_and_asks_again(monkeypatch, capsys):
    hk = _window_hotkeys(monkeypatch)
    cv = KeyCv2(hk.recal_char)
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    hk.calibrating = True                       # an in-session F9
    gd._show_cal_result("w", 2560, 1440, hk, _bad_mapper())
    assert hk.again_requested and hk.result_showing is False
    assert not hk.quit_requested                # not a cancel
    assert hk.end_calibration() is False
    assert hk.take_recal() is True
    capsys.readouterr().out.encode("ascii")


def test_in_session_recal_on_the_result_screen_keeps_the_finished_one(monkeypatch):
    """The calibration just finished comes back (the save rules decide about
    it), and the next loop starts a fresh one."""
    hk = _window_hotkeys(monkeypatch)
    cv = KeyCv2(hk.recal_char, only_result=True)
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
    hk.calibrating = True
    m = gd.calibrate_pursuit(BallEstimator(cv), kv.FakeCap(), 1280, 720, hk,
                             0.25, passes=1, speed=900.0, latency=0.06)
    assert m is not None
    assert hk.end_calibration() is False
    assert hk.take_recal() is True


def test_recal_during_the_ball_still_cancels(monkeypatch):
    hk = _window_hotkeys(monkeypatch)
    cv = KeyCv2(hk.recal_char)
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    clock = {"t": 1000.0}

    def fake_time():
        clock["t"] += 1 / 60.0
        return clock["t"]
    monkeypatch.setattr(gd.time, "time", fake_time)
    monkeypatch.setattr(gd.time, "monotonic", fake_time)

    class BallEstimator(kv.DotEstimator):
        def estimate(self, frame):
            return kv._gaze_of(*self.cv.dot, sw=1280, sh=720)
    hk.calibrating = True
    m = gd.calibrate_pursuit(BallEstimator(cv), kv.FakeCap(), 1280, 720, hk,
                             0.25, passes=1, speed=900.0, latency=0.06)
    assert m is None
    assert hk.end_calibration() is True
    assert hk.take_recal() is False


def test_first_calibration_recal_on_the_result_screen_starts_another(monkeypatch):
    hk = _window_hotkeys(monkeypatch)
    monkeypatch.setattr(gd, "cv2", KeyCv2(hk.recal_char))
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    gd._show_cal_result("w", 2560, 1440, hk, _bad_mapper())
    gd._after_first_calibration(hk)
    assert hk.take_recal() is True


def test_first_calibration_recal_during_the_ball_is_still_dropped():
    class H:
        recal_requested = True          # F9 x2 while following the ball
        drift_requested = True
        again_requested = False

        def take_again(self):
            return False
    h = H()
    gd._after_first_calibration(h)
    assert h.recal_requested is False and h.drift_requested is False


# --- the click switch ends the result screen early, without a click ----------


class SwitchRig:
    """The REAL _Clicker behind Hotkeys.result_switch, as main() wires it,
    with a fake key hub and gamepad; presses arrive while the screen waits."""

    def __init__(self, monkeypatch, at=3, key=True):
        from tests.test_click_menu_routing import CFG, FakeKeys, FakePad
        from eye_gaze.switch import GAMEPAD_BUTTONS
        self.clicks = []
        for name in ("left_click", "right_click", "double_click",
                     "left_down", "left_up"):
            monkeypatch.setattr(gd.actions, name,
                                lambda name=name: self.clicks.append(name))
        self.keys, self.pad = FakeKeys(), FakePad()
        self.clicker = gd._Clicker(dict(CFG), keys=self.keys, gamepad=self.pad)
        self.hk = Keys()
        self.hk.result_switch = lambda drain=False: self.clicker.take_dismiss(
            0.0, drain)
        rig = self

        class Cv(TextCv2):
            waits = 0

            def waitKey(self, ms):
                Cv.waits += 1
                if Cv.waits == at:
                    if key:
                        rig.keys.press("space")
                    else:
                        rig.pad.mask = GAMEPAD_BUTTONS["A"]
                return -1
        self.cv = Cv()
        monkeypatch.setattr(gd, "cv2", self.cv)
        monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)


@pytest.mark.parametrize("key", [True, False])
def test_the_click_switch_ends_the_result_screen_without_a_click(monkeypatch,
                                                                 key):
    r = SwitchRig(monkeypatch, at=3, key=key)
    gd._show_cal_result("w", 2560, 1440, r.hk, _bad_mapper())
    assert r.hk.fed == 3                    # ended at the press, not after 8 s
    assert r.clicks == []                   # nothing reached the desktop
    assert r.keys.pending == [] and r.clicker.take("left") is False
    assert r.hk.result_showing is False if hasattr(r.hk, "result_showing") \
        else True
    # the still-held pad button is no new press for the live loop
    r.clicker.update(0.0)
    r.clicker.fire()
    assert r.clicks == []


def test_a_press_made_during_the_ball_does_not_skip_the_result(monkeypatch):
    r = SwitchRig(monkeypatch, at=10 ** 6)
    r.keys.press("space")                   # while following the ball
    gd._show_cal_result("w", 2560, 1440, r.hk, _bad_mapper())
    assert r.hk.fed == 80                   # shown for its full time
    assert r.clicks == [] and r.keys.pending == []


def test_other_switches_on_the_result_screen_neither_end_it_nor_click(
        monkeypatch):
    r = SwitchRig(monkeypatch, at=2)
    r.keys.press("f9")                      # not a click key of this rig
    r.clicker.inject("right")
    gd._show_cal_result("w", 2560, 1440, r.hk, _bad_mapper())
    assert r.hk.fed == 2 and r.clicks == []  # the space at the 2nd wait


def test_a_broken_switch_poll_never_costs_the_result_screen(monkeypatch):
    cv = TextCv2()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    keys = Keys()
    keys.result_switch = lambda drain=False: 1 / 0
    gd._show_cal_result("w", 2560, 1440, keys, _bad_mapper())
    assert keys.fed == 80


def test_main_wires_the_click_switch_to_the_result_screen():
    import inspect
    src = inspect.getsource(gd.main)
    assert "hotkeys.result_switch = " in src
    assert src.index("clicker = _Clicker(") < src.index("hotkeys.result_switch")
    assert src.index("hotkeys.result_switch") < src.index("def run_calibration")
    first = src[src.index("_after_first_calibration(hotkeys)"):]
    assert first.index("clicker.discard()") < first.index("eyes.use(mapper)")
    from eye_gaze import hotkeys as hk_mod
    assert "result_switch" in inspect.getsource(hk_mod.Hotkeys.__init__)
