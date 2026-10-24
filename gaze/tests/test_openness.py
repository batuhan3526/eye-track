# gaze/tests/test_openness.py
"""Eye openness in camera pixels and the per-person closed-eye threshold."""
import inspect
import json
from types import SimpleNamespace

import numpy as np
import pytest

from eye_gaze import openness as op
from eye_gaze.blink import BlinkClicker
from eye_gaze.gaze import eye_openness, landmark_openness
from eye_gaze.mapping import GazeMapper


# --- synthetic face: one eye pair in camera PIXELS ---------------------------

def _face_px(gap=12.0, width=40.0, cx=640.0, cy=400.0):
    """Landmark index -> (x, y) pixels for both eyes: lids `gap` apart,
    corners `width` apart (openness = gap / width)."""
    pts = {}
    for (outer, inner, upper, lower), dx in (((33, 133, 159, 145), -60.0),
                                             ((362, 263, 386, 374), 60.0)):
        ex = cx + dx
        pts[outer] = (ex - width / 2, cy)
        pts[inner] = (ex + width / 2, cy)
        pts[upper] = (ex, cy - gap / 2)
        pts[lower] = (ex, cy + gap / 2)
    return pts


def _landmarks(pts_px, w, h):
    """The same face as MediaPipe reports it for a w x h frame: x / w, y / h."""
    lm = [SimpleNamespace(x=0.5, y=0.5) for _ in range(478)]
    for i, (x, y) in pts_px.items():
        lm[i] = SimpleNamespace(x=x / w, y=y / h)
    return lm


def _old_normalised(lm):
    """v1.2.0: the ratio straight from the normalised coordinates."""
    def pt(i):
        return (lm[i].x, lm[i].y)
    return (eye_openness(pt(159), pt(145), pt(33), pt(133))
            + eye_openness(pt(386), pt(374), pt(362), pt(263))) / 2.0


def test_openness_is_the_same_on_a_4_3_and_a_16_9_camera():
    face = _face_px(gap=12.0, width=40.0)
    a = landmark_openness(_landmarks(face, 1280, 960), 1280, 960)
    b = landmark_openness(_landmarks(face, 1280, 720), 1280, 720)
    assert a == pytest.approx(0.3) and b == pytest.approx(0.3)
    # what it used to be: the same eye read 33% more open on 16:9
    old_a = _old_normalised(_landmarks(face, 1280, 960))
    old_b = _old_normalised(_landmarks(face, 1280, 720))
    assert old_b / old_a == pytest.approx(960 / 720)


def test_pixel_openness_is_the_old_value_times_the_frame_aspect():
    face = _face_px(gap=9.0, width=36.0)
    for w, h in ((1280, 960), (1280, 720), (640, 480), (1920, 1080)):
        lm = _landmarks(face, w, h)
        assert landmark_openness(lm, w, h) == pytest.approx(
            _old_normalised(lm) * h / w)


def test_a_degenerate_eye_is_closed_not_a_crash():
    lm = [SimpleNamespace(x=0.5, y=0.5) for _ in range(478)]
    assert landmark_openness(lm, 1280, 720) == 0.0


# --- the fraction: today's 0.18 on a 1280x960 camera ------------------------

def test_auto_on_a_typical_open_eye_equals_the_factory_value_at_1280x960():
    # MediaPipe's canonical face (geometry_pipeline_metadata in the
    # face_landmarker bundle), seen frontally: lid gap / eye width.
    assert op.CANONICAL_OPEN == pytest.approx(0.2586, abs=1e-4)
    eye = op.ClosedEye("auto")
    thr = eye.threshold()               # nothing learnt yet: the typical eye
    face = _face_px(gap=op.CANONICAL_OPEN * 40.0, width=40.0)
    lm = _landmarks(face, 1280, 960)
    # the same eye, the same decision as 0.18 on the old normalised scale
    assert thr / landmark_openness(lm, 1280, 960) == pytest.approx(
        0.18 / _old_normalised(lm), rel=1e-3)
    assert 0.5 < op.FRACTION < 0.55


def test_real_faces_get_an_unclamped_auto_threshold():
    """The public-domain portraits the deep model is tested on (MediaPipe
    landmarks in tests/data/faces/reference.json): their open eyes lie well
    inside the range "auto" follows without hitting the floor or ceiling."""
    import os
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", "faces", "reference.json")
    with open(here, encoding="ascii") as f:
        faces = json.load(f)["faces"]
    assert faces
    for face in faces.values():
        w, h = face["size"]
        lm = [SimpleNamespace(x=a[0], y=a[1]) for a in face["landmarks"]]
        v = landmark_openness(lm, w, h)
        assert op.FLOOR < op.FRACTION * v < op.CEILING
        eye = op.ClosedEye("auto")
        eye.use(SimpleNamespace(open_eye=v, camera=(w, h)))
        assert eye.threshold() == pytest.approx(op.FRACTION * v)


def test_a_pixel_scale_gate_is_fixed():
    eye = op.ClosedEye.fixed(0.1)
    assert not eye.auto
    _feed(eye, 0.4, 20.0)
    assert eye.threshold() == 0.1


# --- auto: learnt per person ---------------------------------------------------

def _feed(eye, value, seconds, t0=0.0, fps=30.0):
    n = int(seconds * fps)
    for i in range(n):
        eye.observe(value, t0 + i / fps)
    return t0 + n / fps


def test_auto_threshold_is_learnt_from_the_persons_open_eyes():
    eye = op.ClosedEye("auto")
    _feed(eye, 0.20, 20.0)
    assert eye.threshold() == pytest.approx(op.FRACTION * 0.20)
    wide = op.ClosedEye("auto")
    _feed(wide, 0.32, 20.0)
    assert wide.threshold() == pytest.approx(op.FRACTION * 0.32)


def test_closed_frames_do_not_drag_the_threshold_down():
    eye = op.ClosedEye("auto")
    t = _feed(eye, 0.26, 15.0)
    t = _feed(eye, 0.03, 30.0, t0=t)        # a long rest with the eyes shut
    assert eye.threshold() == pytest.approx(op.FRACTION * 0.26)


def test_runtime_tracking_is_slow_and_forgets_old_frames():
    eye = op.ClosedEye("auto")
    t = _feed(eye, 0.30, 60.0)
    t = _feed(eye, 0.22, 5.0, t0=t)         # a few seconds: no change yet
    assert eye.threshold() == pytest.approx(op.FRACTION * 0.30)
    _feed(eye, 0.22, 90.0, t0=t)            # a minute and more: followed
    assert eye.threshold() == pytest.approx(op.FRACTION * 0.22)


def test_calibration_value_is_used_until_enough_live_frames():
    eye = op.ClosedEye("auto")
    eye.use(SimpleNamespace(open_eye=0.20, camera=(1280, 720)))
    assert eye.threshold() == pytest.approx(op.FRACTION * 0.20)
    _feed(eye, 0.30, 3.0)                   # too little to override it
    assert eye.threshold() == pytest.approx(op.FRACTION * 0.20)


def test_a_calibration_without_the_value_uses_the_runtime_estimate():
    """v1.2.0 files carry no open-eye value: no crash, no recalibration."""
    old = GazeMapper.from_dict(json.loads(json.dumps(_V2)))
    eye = op.ClosedEye("auto")
    eye.use(old)
    assert eye.threshold() == pytest.approx(op.FRACTION * op.CANONICAL_OPEN)
    _feed(eye, 0.21, 20.0)
    assert eye.threshold() == pytest.approx(op.FRACTION * 0.21)


def test_auto_threshold_has_a_floor_and_a_ceiling():
    low = op.ClosedEye("auto")
    low.use(SimpleNamespace(open_eye=0.02, camera=None))
    assert low.threshold() == pytest.approx(op.FLOOR)
    high = op.ClosedEye("auto")
    high.use(SimpleNamespace(open_eye=1.5, camera=None))
    assert high.threshold() == pytest.approx(op.CEILING)
    assert op.FLOOR < op.FRACTION * op.CANONICAL_OPEN < op.CEILING


def test_nonsense_openness_is_ignored():
    eye = op.ClosedEye("auto")
    for i in range(900):
        eye.observe(float("nan"), i / 30.0)
        eye.observe(None, i / 30.0)
    assert eye.threshold() == pytest.approx(op.FRACTION * op.CANONICAL_OPEN)


# --- explicit values and the factory value -------------------------------------

@pytest.mark.parametrize("value", ["auto", " Auto ", None, 0.18, "0.18", "0,18"])
def test_auto_and_the_v120_factory_value_are_auto(value):
    eye = op.ClosedEye(value)
    assert eye.auto
    assert eye.threshold() == pytest.approx(op.FRACTION * op.CANONICAL_OPEN)


def test_an_explicit_value_is_honoured_in_the_calibrations_camera_aspect():
    eye = op.ClosedEye(0.25)
    assert not eye.auto
    eye.use(SimpleNamespace(open_eye=0.30, camera=(640, 480)))
    eye.set_frame((1280, 720))
    assert eye.threshold() == pytest.approx(0.25 * 480 / 640)
    eye.use(SimpleNamespace(open_eye=0.30, camera=(1280, 720)))
    assert eye.threshold() == pytest.approx(0.25 * 720 / 1280)
    _feed(eye, 0.40, 30.0)                  # never learnt over
    assert eye.threshold() == pytest.approx(0.25 * 720 / 1280)


def test_an_explicit_value_without_a_camera_stamp_uses_the_frame_aspect():
    eye = op.ClosedEye(0.12)
    eye.use(SimpleNamespace(open_eye=None, camera=None))
    eye.set_frame((1920, 1080))
    assert eye.threshold() == pytest.approx(0.12 * 1080 / 1920)


def test_describe_is_ascii():
    for v in ("auto", 0.25):
        line = op.ClosedEye(v).describe()
        assert line and line.isascii()


# --- calibration metadata ------------------------------------------------------

_V2 = {"v": 2, "reach": 0.25, "poly2": False,
       "cx": [1.0, 2.0, 3.0], "cy": [4.0, 5.0, 6.0],
       "lo": [0.1, 0.2], "hi": [0.9, 0.8],
       "screen": [1280, 720], "camera": [640, 480]}


def test_open_eye_value_travels_with_the_mapper():
    m = GazeMapper.from_dict(dict(_V2))
    assert m.open_eye is None
    assert "open_eye" not in m.to_dict()          # v2 files stay as they were
    m.open_eye = 0.2475
    d = json.loads(json.dumps(m.to_dict()))
    assert d["open_eye"] == 0.2475 and d["v"] == 2
    back = GazeMapper.from_dict(d)
    assert back.open_eye == 0.2475
    assert back.rescaled(2560, 1440).open_eye == 0.2475


@pytest.mark.parametrize("bad", [-1.0, 0.0, "x", None, float("nan"), [1]])
def test_a_bad_stored_open_eye_value_is_ignored(bad):
    d = dict(_V2, open_eye=bad)
    assert GazeMapper.from_dict(d).open_eye is None


def test_open_median_ignores_closed_frames_and_needs_enough():
    vals = [0.26] * 30 + [0.02] * 30
    assert op.open_median(vals, 0.135) == pytest.approx(0.26)
    assert op.open_median([0.26] * 3, 0.135) is None
    assert op.open_median([], 0.135) is None


def test_refit_from_samples_keeps_the_open_eye_value():
    from eye_gaze import caldata
    rnd = np.random.default_rng(3)
    g = [tuple(rnd.random(2)) for _ in range(30)]
    s = [(1000 * a + 100, 600 * b + 50) for a, b in g]
    cs = caldata.CalSamples.dots(g, s, list(range(30)),
                                 {"poly2": False, "reach": 0.25,
                                  "latency": None})
    cs.meta["open_eye"] = 0.23
    r = caldata.refit(cs, 0.25, False, None)
    assert r is not None and r.mapper.open_eye == pytest.approx(0.23)
    del cs.meta["open_eye"]
    r = caldata.refit(cs, 0.25, False, None)
    assert r is not None and r.mapper.open_eye is None


# --- consumers -----------------------------------------------------------------

def test_blink_clicker_takes_the_current_threshold():
    b = BlinkClicker(hold_seconds=0.5)
    assert not b.update(0.10, 0.0, 0.12)
    assert b.update(0.10, 0.6, 0.12)
    b2 = BlinkClicker(hold_seconds=0.5)
    assert not b2.update(0.10, 0.0, 0.08)
    assert not b2.update(0.10, 0.6, 0.08)       # open by this threshold


def test_every_live_consumer_reads_the_one_source():
    import gaze_driver as gd
    src = inspect.getsource(gd.main)
    assert "float(cfg.get(\"blink_closed_threshold\"" not in src
    assert "ClosedEye(cfg.get(\"blink_closed_threshold\"" in src
    assert "closed_thr = eyes.threshold()" in src
    assert "blinker.update(source.openness, now, closed_thr)" in src
    assert "eyes.observe(source.openness, now)" in src
    # the calibrations get the same object
    run = src[src.index("def run_calibration"):]
    run = run[:run.index("n_inputs = ")]
    assert run.count("eyes") >= 2


def _fake_clock(monkeypatch, gd, step=1 / 60.0):
    clock = {"t": 1000.0}

    def fake_time():
        clock["t"] += step
        return clock["t"]
    monkeypatch.setattr(gd.time, "time", fake_time)
    monkeypatch.setattr(gd.time, "monotonic", fake_time)


def _fakes(monkeypatch, gd, kv):
    cv = kv.FakeCv2()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    return cv


def test_pursuit_calibration_stores_the_open_eye_value(monkeypatch):
    import gaze_driver as gd
    from tests import test_kalibrasyon_veri_driver as kv
    cv = _fakes(monkeypatch, gd, kv)
    _fake_clock(monkeypatch, gd)

    class Ball(kv.DotEstimator):
        n = 0

        def estimate(self, frame):
            self.n += 1
            # every 10th frame a blink: kept out of the samples and the median
            self.openness = 0.02 if self.n % 10 == 0 else 0.24
            return kv._gaze_of(*self.cv.dot, sw=1280, sh=720)
    eye = op.ClosedEye("auto")
    m = gd.calibrate_pursuit(Ball(cv), kv.FakeCap(), 1280, 720,
                             kv.FakeHotkeys(), 0.25, poly2=False, passes=1,
                             speed=400.0, latency=0.06, closed_thr=eye)
    assert m is not None
    assert m.open_eye == pytest.approx(0.24)
    assert m.cal_samples.meta["open_eye"] == pytest.approx(0.24)
    # the gate learnt the person while they followed the ball
    assert eye.threshold() == pytest.approx(op.FRACTION * 0.24)


def test_dot_calibration_stores_the_open_eye_value(monkeypatch):
    import gaze_driver as gd
    from tests import test_kalibrasyon_veri_driver as kv
    cv = _fakes(monkeypatch, gd, kv)

    class Dot(kv.DotEstimator):
        openness = 0.31
    pos = [(0.1 + 0.2 * i, 0.1 + 0.2 * j) for i in range(5) for j in range(5)]
    m = gd.calibrate(Dot(cv), kv.FakeCap(), 320, 240, pos, kv.FakeHotkeys(),
                     3, 0.25, poly2=True, passes=1,
                     closed_thr=op.ClosedEye("auto"))
    assert m is not None
    assert m.open_eye == pytest.approx(0.31)
    assert m.cal_samples.meta["open_eye"] == pytest.approx(0.31)


def test_config_accepts_auto_and_numbers(tmp_path):
    from eye_gaze.config import load_config
    p = tmp_path / "c.yaml"
    for text, want in (('"auto"', "auto"), ("AUTO", "auto"), ("0.22", 0.22),
                       ('"0,22"', 0.22)):
        p.write_text("left_click_key: space\nblink_closed_threshold: %s\n"
                     % text, encoding="utf-8")
        assert load_config(str(p))["blink_closed_threshold"] == want


def test_shipped_config_says_auto():
    import os
    import yaml
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "config.default.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert cfg["blink_closed_threshold"] == "auto"
