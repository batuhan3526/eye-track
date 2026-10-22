# gaze/tests/test_kalibrasyon_veri_driver.py
"""The driver glue for calibration samples and implicit correction.

The two modules are tested on their own; this is where they can come apart
from the driver: the calibration routines must hand their samples over, the
save path must write them without ever risking calibration.json, and the
magnifier must report its error signal exactly once per confirmed click -
and never let the report get between the user and the click.
"""
import json
import os

import numpy as np
import pytest

import gaze_driver as gd
from eye_gaze import caldata
from eye_gaze.implicit import ImplicitCorrector
from eye_gaze.mapping import GazeMapper

FEAT = "pe1-hp1-ht1-dg0"
OPTS = {"poly2": False, "reach": 0.25, "latency": "auto"}


def _gaze_of(x, y, sw=320, sh=240):
    gx = (x / sw - 0.5) * 0.4
    gy = (y / sh - 0.5) * 0.3
    return (gx + 0.1 * gx * gx, gy)


def _fitted():
    g, s = [], []
    for i in range(5):
        for j in range(5):
            x, y = 20 + i * 70, 20 + j * 50
            g.append(_gaze_of(x, y))
            s.append((x, y))
    m = GazeMapper(reach_margin=0.25)
    m.fit(g, s)
    m.cal_samples = caldata.CalSamples.dots(g, s, None, OPTS)
    return m


# --- saving ------------------------------------------------------------------


def test_saving_a_calibration_also_saves_its_samples(tmp_path):
    cal = tmp_path / "calibration.json"
    m = _fitted()
    assert gd._save_calibration(str(cal), m, feat=FEAT) is True
    cs = caldata.load_samples(caldata.samples_path(str(cal), FEAT))
    assert cs is not None
    assert cs.meta["sig"] == caldata.mapper_sig(m)


def test_a_samples_failure_never_costs_the_calibration(tmp_path, monkeypatch):
    cal = tmp_path / "calibration.json"

    def boom(*a, **k):
        raise OSError("disk dolu")
    monkeypatch.setattr(caldata, "save_samples", boom)
    assert gd._save_calibration(str(cal), _fitted(), feat=FEAT) is True
    assert json.loads(cal.read_text(encoding="utf-8"))["v"] == 2


def test_a_refused_calibration_write_saves_no_samples(tmp_path, monkeypatch):
    """Samples describe the calibration IN calibration.json - never one that
    did not make it there."""
    cal = tmp_path / "calibration.json"
    monkeypatch.setattr(gd, "_atomic_write_bytes", lambda p, d: False)
    gd._save_calibration(str(cal), _fitted(), feat=FEAT)
    assert not os.path.exists(caldata.samples_path(str(cal), FEAT))


def test_the_loaded_calibration_and_its_samples_share_a_signature(tmp_path):
    cal = tmp_path / "calibration.json"
    m = _fitted()
    gd._save_calibration(str(cal), m, feat=FEAT)
    loaded, problem, _ = gd._read_calibration(str(cal))
    assert problem is None
    assert caldata.find_samples(str(cal), FEAT, 2,
                                sig=caldata.mapper_sig(loaded)) is not None


# --- the calibration routines hand their samples over -----------------------


class FakeCv2:
    WINDOW_NORMAL = 0
    WND_PROP_FULLSCREEN = 1
    WINDOW_FULLSCREEN = 1
    FONT_HERSHEY_SIMPLEX = 0

    def __init__(self):
        self.dot = (0, 0)

    def namedWindow(self, *a, **k):
        pass

    def setWindowProperty(self, *a, **k):
        pass

    def circle(self, img, centre, r, colour, thick):
        if r > 10:
            self.dot = centre

    def putText(self, *a, **k):
        pass

    def imshow(self, *a, **k):
        pass

    def waitKey(self, ms):
        return -1

    def destroyWindow(self, *a):
        pass

    def flip(self, frame, code):
        return frame


class FakeHotkeys:
    quit_requested = False

    def feed_window_key(self, k):
        pass


class FakeCap:
    def read(self):
        return True, np.zeros((2, 2, 3), dtype=np.uint8)


class DotEstimator:
    """Looks exactly where the fake cv2 last drew the big dot."""
    openness = 1.0

    def __init__(self, cv):
        self.cv = cv

    def estimate(self, frame):
        return _gaze_of(*self.cv.dot)


def test_dot_calibration_attaches_its_samples(monkeypatch):
    cv = FakeCv2()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    pos = [(0.1 + 0.2 * i, 0.1 + 0.2 * j) for i in range(5) for j in range(5)]
    m = gd.calibrate(DotEstimator(cv), FakeCap(), 320, 240, pos, FakeHotkeys(),
                     3, 0.25, poly2=True, passes=1)
    assert m is not None
    cs = m.cal_samples
    assert cs.kind == "dots" and len(cs.gaze) == 25
    assert cs.meta["opts"] == {"poly2": True, "reach": 0.25, "latency": None}
    assert len(cs.t) == 25


def test_pursuit_calibration_attaches_its_samples(monkeypatch):
    cv = FakeCv2()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    clock = {"t": 1000.0}

    def fake_time():
        clock["t"] += 1 / 60.0
        return clock["t"]
    monkeypatch.setattr(gd.time, "time", fake_time)
    monkeypatch.setattr(gd.time, "monotonic", fake_time)

    class BallEstimator(DotEstimator):
        def estimate(self, frame):
            return _gaze_of(*self.cv.dot, sw=1280, sh=720)
    m = gd.calibrate_pursuit(BallEstimator(cv), FakeCap(), 1280, 720,
                             FakeHotkeys(), 0.25, poly2=False, passes=1,
                             speed=900.0, latency=0.06)
    assert m is not None
    cs = m.cal_samples
    assert cs.kind == "pursuit"
    assert cs.meta["latency_used"] == pytest.approx(0.06)
    assert cs.meta["opts"] == {"poly2": False, "reach": 0.25, "latency": 0.06}
    assert len(cs.gaze) > 80


def test_pursuit_without_a_face_stops_early(monkeypatch, capsys):
    """No face (user turned away, camera covered) used to run the ball for
    both full passes - ~45 s of following it - before failing."""
    cv = FakeCv2()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    clock = {"t": 1000.0}

    def fake_time():
        clock["t"] += 1 / 30.0
        return clock["t"]
    monkeypatch.setattr(gd.time, "time", fake_time)
    monkeypatch.setattr(gd.time, "monotonic", fake_time)

    class NoFace(DotEstimator):
        def estimate(self, frame):
            return None
    m = gd.calibrate_pursuit(NoFace(cv), FakeCap(), 1280, 720, FakeHotkeys(),
                             0.25, passes=2)
    assert m is None
    assert clock["t"] - 1000.0 < 2.0 + gd._PURSUIT_GIVE_UP + 1.0
    assert "durduruldu" in capsys.readouterr().out


# --- the magnifier reports its error once, after the click --------------------


@pytest.fixture
def zoom(monkeypatch):
    import eye_gaze.zoom as zm

    class Cv(FakeCv2):
        def resize(self, img, size, interpolation=None):
            return img
        INTER_LINEAR = 1
    monkeypatch.setattr(gd, "cv2", Cv())
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    monkeypatch.setattr(zm, "grab_region",
                        lambda x, y, w, h: np.zeros((h, w, 3), dtype=np.uint8))
    state = {"cur": (500, 300), "clicks": 0, "set": None}
    monkeypatch.setattr(gd.cursor, "get_cursor", lambda: state["cur"])

    def set_cursor(x, y, **k):
        state["set"] = (x, y)
        return x, y
    monkeypatch.setattr(gd.cursor, "set_cursor", set_cursor)

    def click():
        state["clicks"] += 1
    monkeypatch.setattr(gd.actions, "left_click", click)
    z = gd._ZoomClick(1280, 720, 0.25, 8)
    return z, state


def test_a_confirmed_magnifier_click_reports_pre_cursor_and_target(zoom):
    z, state = zoom
    seen = []
    z.on_confirm = lambda pre, tgt, reg: seen.append((pre, tgt, reg))
    z.begin()
    state["cur"] = (700, 400)              # gaze inside the magnified view
    z.confirm()
    assert state["clicks"] == 1
    assert len(seen) == 1
    pre, tgt, reg = seen[0]
    assert pre == (500, 300)
    assert tgt == state["set"]
    assert reg == (340, 210, 320, 180)


def test_a_snapped_cursor_does_not_teach_the_snap_offset(zoom):
    """Snap-to-target moved the cursor onto a button 60 px from the gaze.
    The magnifier must centre on, and implicit correction must learn from,
    where the EYE was - learning the snap offset as gaze error would shift
    every later cursor by it."""
    z, state = zoom
    seen = []
    z.on_confirm = lambda pre, tgt, reg: seen.append((pre, tgt, reg))
    state["cur"] = (560, 300)                      # snapped onto the button
    z.gaze_hint = ((500.0, 300.0), (560, 300))     # (raw gaze, placed)
    z.begin()
    z.confirm()
    pre, _, reg = seen[0]
    assert pre == (500, 300)
    assert reg == (340, 210, 320, 180)             # centred on the gaze


def test_a_hand_moved_mouse_is_taken_as_it_is(zoom):
    """A caregiver moved the real mouse: the cursor is no longer where the
    driver put it, so the old gaze hint says nothing about it."""
    z, state = zoom
    seen = []
    z.on_confirm = lambda pre, tgt, reg: seen.append((pre, tgt, reg))
    z.gaze_hint = ((100.0, 100.0), (120, 100))
    state["cur"] = (500, 300)
    z.begin()
    z.confirm()
    assert seen[0][0] == (500, 300)


def test_snap_runs_after_the_filters_and_before_the_cursor_moves():
    import inspect
    src = inspect.getsource(gd.main)
    stab = src.index("lx, ly = stabilizer.update(lx, ly)")
    snap_at = src.index("snapper.adjust(")
    move = src.index("px, py = cursor.set_cursor(lx, ly")
    hint = src.index("zoomer.gaze_hint = ")
    assert stab < snap_at < move < hint
    assert "make_target_snap(" in src


def test_a_cancelled_magnifier_teaches_nothing(zoom):
    z, state = zoom
    seen = []
    z.on_confirm = lambda *a: seen.append(a)
    z.begin()
    z.cancel()
    z.confirm()                            # no longer active: nothing
    assert seen == [] and state["clicks"] == 0


def test_a_failing_report_never_costs_or_repeats_the_click(zoom):
    z, state = zoom

    def boom(*a):
        raise RuntimeError("x")
    z.on_confirm = boom
    z.begin()
    z.confirm()
    assert state["clicks"] == 1
    assert not z.active


def test_without_a_listener_the_magnifier_works_as_before(zoom):
    z, state = zoom
    z.begin()
    z.confirm()
    assert state["clicks"] == 1


# --- construction from config -------------------------------------------------


def test_corrector_from_config(tmp_path):
    cal = str(tmp_path / "calibration.json")
    c = ImplicitCorrector.from_config(
        {"implicit_correction": True, "implicit_max_px": 70,
         "implicit_halflife_days": 2}, cal, "sig", 1280, 720)
    assert c.enabled and c._max == 70.0 and c._half == 2.0
    assert c._path == os.path.splitext(cal)[0] + "_implicit.json"
    assert c._sigma == pytest.approx(0.15 * 1280)


def test_corrector_from_config_off_and_without_a_file(tmp_path):
    c = ImplicitCorrector.from_config({"implicit_correction": False},
                                      str(tmp_path / "c.json"), "s", 1280, 720)
    assert not c.enabled
    # save_calibration off / defaults-only config: may not touch any file
    c2 = ImplicitCorrector.from_config({}, None, "s", 1280, 720)
    assert c2.enabled and c2._path is None


def test_corrector_from_config_survives_bad_values(tmp_path):
    c = ImplicitCorrector.from_config({"implicit_max_px": "cok"},
                                      str(tmp_path / "c.json"), "s", 1280, 720)
    assert c._max == 100.0


def test_safe_sig_never_raises():
    assert caldata.safe_sig(None) is None
    assert caldata.safe_sig(GazeMapper()) is None
    assert caldata.safe_sig(_fitted()) == caldata.mapper_sig(_fitted())


# --- config ---------------------------------------------------------------------


def test_new_settings_are_known_defaulted_and_coerced(tmp_path, monkeypatch):
    from eye_gaze import config as config_mod
    monkeypatch.setattr(config_mod, "_report", lambda m: None)
    for k in ("calibration_refit", "implicit_correction", "implicit_max_px",
              "implicit_halflife_days"):
        assert k in config_mod.KNOWN_KEYS, k
        assert k in config_mod.DEFAULTS, k
    assert config_mod.DEFAULTS["implicit_correction"] is True
    assert config_mod.DEFAULTS["calibration_refit"] is True
    p = tmp_path / "c.yaml"
    p.write_text('left_click_key: "space"\nimplicit_max_px: "80,5"\n'
                 'implicit_halflife_days: "2"\n', encoding="utf-8")
    cfg = config_mod.load_config(str(p))
    assert cfg["implicit_max_px"] == pytest.approx(80.5)
    assert cfg["implicit_halflife_days"] == pytest.approx(2.0)


def test_corrector_construction_never_kills_the_driver(tmp_path):
    """It runs at startup, outside the per-frame guard: a raise there would
    end the driver before the cursor ever moved."""
    class BadCfg(dict):
        def get(self, *a, **k):
            raise RuntimeError("x")
    c = ImplicitCorrector.from_config(BadCfg(), str(tmp_path / "c.json"), "s",
                                      1280, 720)
    assert not c.enabled
    assert c.apply(10.0, 20.0) == (10.0, 20.0)


def test_the_driver_tells_the_corrector_every_drift_it_uses():
    """The field is kept relative to the session's drift: F7 hands over the
    new drift, and a recalibration (drift back to 0) says so too."""
    import inspect
    src = inspect.getsource(gd.main)
    # Screen coordinates: the drift dot is the PRIMARY monitor's centre.
    assert "implicit.observe_drift((ox + sw // 2, oy + sh // 2)," in src
    reset = src.index("drift = (0.0, 0.0)", src.index("mapper = m"))
    assert "implicit.set_drift(drift)" in src[reset:reset + 200]


def test_the_magnifier_cuts_from_the_monitor_under_the_cursor(zoom,
                                                              monkeypatch):
    """With multi_monitor the region used to be clamped to the PRIMARY
    screen: F6 on a left-hand monitor magnified the primary's edge."""
    z, state = zoom
    z._multi = True
    monkeypatch.setattr(gd.cursor, "monitor_rect",
                        lambda x, y: (-1920, 0, 1920, 1080))
    state["cur"] = (-1900, 20)            # top-left corner, second monitor
    z.begin()
    x0, y0, zw, zh = z._region
    assert (x0, y0) == (-1920, 0) and (zw, zh) == (480, 270)
    state["cur"] = (0, 0)                 # top-left of the magnified view
    z.confirm()
    assert state["set"] == (-1920, 0)


def test_one_monitor_keeps_the_primary_behaviour(zoom, monkeypatch):
    z, state = zoom
    monkeypatch.setattr(gd.cursor, "monitor_rect",
                        lambda x, y: (_ for _ in ()).throw(AssertionError()))
    z.begin()                             # multi_monitor off: never asked
    assert z._region == (340, 210, 320, 180)
