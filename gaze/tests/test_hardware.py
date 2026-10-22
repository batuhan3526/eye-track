# gaze/tests/test_hardware.py
"""The hardware profile (makine.json) and the "auto" settings decided from it.

No test here opens a real camera (the user may be using it) or starts a GPU:
the probe runs on fake openers, the benchmark on the CPU provider only - the
DirectML/CUDA path is exercised with fakes.
"""
import json
import os
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import pytest

import gaze_driver as gd
from eye_gaze import config as config_mod
from eye_gaze import hardware as hw

GAZE = Path(__file__).resolve().parent.parent
FACE = GAZE / "resources" / "faces" / "benchmark.jpg"
CPU = ["CPUExecutionProvider"]


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    """makine.json goes to a temp folder, never to gaze/ (the user's install
    in source mode); no smoke camera unless a test sets one."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("GOZFARESI_DATA_DIR", str(d))
    monkeypatch.delenv(hw.FAKE_CAMERA_ENV, raising=False)
    return d


# --- fake cameras -----------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeCam:
    """A camera with fixed modes {(w, h): fps}; reading advances the fake
    clock by one frame time, so the probe measures exactly that fps."""

    def __init__(self, modes, clock, opened=True, hang=None):
        self.modes = dict(modes)
        self.clock = clock
        self.opened = opened
        self.hang = hang
        self.req = [0, 0]
        self.mode = next(iter(self.modes)) if self.modes else (0, 0)
        self.released = threading.Event()
        self.reads = 0

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        import cv2
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            self.req[0] = int(value)
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
            self.req[1] = int(value)
            if tuple(self.req) in self.modes:
                self.mode = tuple(self.req)
        return True

    def get(self, prop):
        return 0.0

    def read(self):
        if self.hang is not None:
            self.hang.wait()
            return False, None
        self.reads += 1
        self.clock.t += 1.0 / self.modes[self.mode]
        w, h = self.mode
        return True, np.zeros((h, w, 3), np.uint8)

    def release(self):
        self.released.set()


class Opener:
    """opener(index, backend) handing out FakeCams; records every one."""

    def __init__(self, cams):
        self.cams = cams          # {(index, backend) or index: factory}
        self.made = []

    def __call__(self, index, backend):
        f = self.cams.get((index, backend), self.cams.get(index))
        cam = f() if f is not None else FakeCam({}, Clock(), opened=False)
        self.made.append((index, backend, cam))
        return cam


def _probe(opener, **kw):
    kw.setdefault("read_seconds", 0.5)
    kw.setdefault("timeout_s", 5)
    return hw.probe_cameras(max_index=kw.pop("max_index", 3), opener=opener,
                            **kw)


# --- probe ------------------------------------------------------------------

def test_probe_picks_highest_res_at_24fps():
    clock = Clock()
    modes = {(1280, 960): 15.0, (1280, 720): 30.0, (640, 480): 30.0}
    op = Opener({0: lambda: FakeCam(modes, clock)})
    found = _probe(op, clock=clock)
    assert len(found) == 1
    cam = found[0]
    assert (cam.index, cam.width, cam.height) == (0, 1280, 720)
    assert cam.fps_measured == pytest.approx(30.0, abs=0.5)
    assert cam.backend == hw.default_backends()[0]
    # Every capture the probe opened (or tried) was released: a camera left
    # open stays locked for the driver.
    assert all(c.released.is_set() for _, _, c in op.made)


def test_probe_without_a_fast_mode_takes_the_fastest():
    clock = Clock()
    modes = {(1280, 960): 10.0, (1280, 720): 15.0, (640, 480): 20.0}
    found = _probe(Opener({0: lambda: FakeCam(modes, clock)}), clock=clock)
    assert (found[0].width, found[0].height) == (640, 480)


def test_best_mode_caps_the_width_at_1280():
    assert hw.best_mode([(1920, 1080, 30.0), (1280, 720, 30.0)]) == \
        (1280, 720, 30.0)
    assert hw.best_mode([]) is None


def test_probe_falls_back_to_directshow_on_windows(monkeypatch):
    monkeypatch.setattr(hw, "_is_windows", lambda: True)
    clock = Clock()
    modes = {(640, 480): 30.0}
    op = Opener({(0, "msmf"): lambda: FakeCam({}, clock, opened=False),
                 (0, "dshow"): lambda: FakeCam(modes, clock)})
    found = _probe(op, clock=clock)
    assert [(c.index, c.backend) for c in found] == [(0, "dshow")]
    assert all(c.released.is_set() for _, _, c in op.made)


def test_linux_probes_v4l2(monkeypatch):
    monkeypatch.setattr(hw, "_is_windows", lambda: False)
    assert hw.default_backends() == ("v4l2",)
    monkeypatch.setattr(hw, "_is_windows", lambda: True)
    assert hw.default_backends() == ("msmf", "dshow")


def test_probe_skips_hanging_camera():
    hang = threading.Event()
    clock = Clock()
    stuck = FakeCam({(1280, 720): 30.0}, clock, hang=hang)
    good_clock = Clock()
    op = Opener({0: lambda: stuck,
                 1: lambda: FakeCam({(640, 480): 30.0}, good_clock)})
    timeout = 1.0
    t0 = time.monotonic()
    found = hw.probe_cameras(max_index=2, timeout_s=timeout, opener=op,
                             read_seconds=0.2, clock=good_clock)
    elapsed = time.monotonic() - t0
    try:
        # timeout_s, then STOP_GRACE_S for the stuck one to let go.
        assert elapsed < timeout + hw.STOP_GRACE_S + 0.5
        assert [c.index for c in found] == [1]
    finally:
        hang.set()
    # The abandoned thread still releases what it opened once the driver
    # call returns.
    assert stuck.released.wait(5.0)


class HangsOnSecondMode(FakeCam):
    """Delivers its first mode, then the driver hangs in the mode change."""

    def set(self, prop, value):
        super().set(prop, value)
        import cv2
        if prop == cv2.CAP_PROP_FRAME_HEIGHT and self.reads:
            self.block.wait()


def test_a_camera_hanging_on_its_second_mode_is_not_kept():
    """Its first mode measured fine, but the thread still HOLDS the device:
    recorded, the driver would wait for that camera forever."""
    hang = threading.Event()
    clock = Clock()
    cam = HangsOnSecondMode({(1280, 960): 30.0, (1280, 720): 30.0}, clock)
    cam.block = hang
    op = Opener({0: lambda: cam})
    t0 = time.monotonic()
    try:
        found = hw.probe_cameras(max_index=1, timeout_s=1.0, opener=op,
                                 read_seconds=0.2, clock=clock)
        assert time.monotonic() - t0 < 1.0 + hw.STOP_GRACE_S + 0.5
        assert cam.reads > 0                # its first mode WAS measured
        assert found == []
    finally:
        hang.set()
    assert cam.released.wait(5.0)


class Slow(FakeCam):
    """Real time passes on every read: the probe overruns timeout_s, but
    stops between reads when told to."""

    def read(self):
        time.sleep(0.05)
        return super().read()


def test_a_slow_camera_that_stops_in_time_keeps_what_it_measured():
    clock = Clock()
    cam = Slow({(1280, 960): 30.0}, clock)
    found = hw.probe_cameras(max_index=1, timeout_s=0.5,
                             opener=Opener({0: lambda: cam}),
                             read_seconds=60.0, clock=clock)
    assert [(c.width, c.height) for c in found] == [(1280, 960)]
    assert cam.released.is_set()            # let go before it was recorded


class NoFrames(FakeCam):
    def read(self):
        return False, None


def test_msmf_without_frames_falls_back_to_directshow(monkeypatch):
    monkeypatch.setattr(hw, "_is_windows", lambda: True)
    clock = Clock()
    op = Opener({(0, "msmf"): lambda: NoFrames({(640, 480): 30.0}, clock),
                 (0, "dshow"): lambda: FakeCam({(640, 480): 30.0}, clock)})
    found = hw.probe_cameras(max_index=1, timeout_s=5, opener=op,
                             read_seconds=0.2, clock=clock)
    assert [(c.index, c.backend) for c in found] == [(0, "dshow")]
    assert all(c.released.is_set() for _, _, c in op.made)


def test_probe_can_be_limited_to_one_camera():
    clock = Clock()
    op = Opener({3: lambda: FakeCam({(640, 480): 30.0}, clock)})
    found = hw.probe_cameras(indices=[3], timeout_s=5, opener=op,
                             read_seconds=0.2, clock=clock)
    assert [c.index for c in found] == [3]
    assert {i for i, _, _ in op.made} == {3}


def test_probe_skips_a_camera_whose_open_never_returns():
    hang = threading.Event()

    def blocked():
        hang.wait()
        return FakeCam({}, Clock(), opened=False)
    clock = Clock()
    op = Opener({0: blocked, 1: lambda: FakeCam({(640, 480): 30.0}, clock)})
    t0 = time.monotonic()
    try:
        found = hw.probe_cameras(max_index=2, timeout_s=1.0, opener=op,
                                 read_seconds=0.2, clock=clock)
        assert time.monotonic() - t0 < 1.0 + hw.STOP_GRACE_S + 0.5
        assert [c.index for c in found] == [1]
    finally:
        hang.set()


def test_probe_no_camera_returns_empty():
    op = Opener({})
    assert hw.probe_cameras(max_index=4, timeout_s=2, opener=op) == []
    assert len(op.made) >= 4
    assert all(c.released.is_set() for _, _, c in op.made)


def test_probe_opener_that_raises_is_no_camera():
    def boom(index, backend):
        raise RuntimeError("driver")
    assert hw.probe_cameras(max_index=2, timeout_s=2, opener=boom) == []


def test_probe_sets_the_msmf_flag_before_cv2():
    src = (GAZE / "eye_gaze" / "hardware.py").read_text(encoding="utf-8")
    flag = src.index("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS")
    assert flag < src.index("import cv2")
    assert os.environ["OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS"] == "0"


# --- opening the driver's camera -------------------------------------------

def test_open_capture_tries_the_recorded_backend_first(monkeypatch):
    monkeypatch.setattr(hw, "_is_windows", lambda: True)
    clock = Clock()
    seen = []

    def opener(index, backend):
        seen.append(backend)
        return FakeCam({(1280, 960): 30.0}, clock, opened=(backend == "dshow"))
    cap = hw.open_capture(0, 1280, 960, 30, backend="dshow", opener=opener)
    assert seen == ["dshow"] and cap.isOpened()
    assert cap.mode == (1280, 960)
    seen.clear()
    cap = hw.open_capture(0, 1280, 960, 30, opener=opener)
    assert seen == ["msmf", "dshow"] and cap.isOpened()


def test_open_capture_returns_an_unopened_capture_when_nothing_opens():
    made = []

    def opener(index, backend):
        made.append(FakeCam({}, Clock(), opened=False))
        return made[-1]
    cap = hw.open_capture(0, 1280, 720, 30, opener=opener)
    assert cap is made[-1] and not cap.isOpened()
    assert all(c.released.is_set() for c in made[:-1])


def test_fake_camera_only_with_env(monkeypatch):
    calls = []

    def opener(index, backend):
        calls.append((index, backend))
        return FakeCam({}, Clock(), opened=False)
    # Without the variable: the real path (the opener), never the fake.
    hw.open_capture(0, 1280, 720, 30, opener=opener)
    hw.probe_cameras(max_index=1, timeout_s=2, opener=opener)
    assert calls and all(b != "fake" for _, b in calls)
    # With it: the picture, whatever is asked; no opener at all.
    monkeypatch.setenv(hw.FAKE_CAMERA_ENV, str(FACE))
    calls.clear()
    cap = hw.open_capture(3, 1280, 960, 60, opener=opener)
    assert isinstance(cap, hw._FakeCapture) and cap.isOpened()
    ok, frame = cap.read()
    assert ok and frame.shape == (480, 640, 3)
    found = hw.probe_cameras(opener=opener)
    assert found == [hw.CameraInfo(0, "fake", 640, 480, 30.0, "fake")]
    assert calls == []


def test_fake_camera_runs_at_30_fps(monkeypatch):
    cap = hw._FakeCapture(str(FACE))
    import cv2
    assert cap.get(cv2.CAP_PROP_FRAME_WIDTH) == 640
    assert cap.get(cv2.CAP_PROP_FPS) == 30.0
    t0 = time.monotonic()
    for _ in range(7):
        assert cap.read()[0]
    assert time.monotonic() - t0 >= 6 / 30.0 - 0.02


def test_fake_camera_with_a_bad_picture_is_not_opened(tmp_path):
    bad = tmp_path / "yok.jpg"
    bad.write_bytes(b"not a jpeg")
    cap = hw._FakeCapture(str(bad))
    assert not cap.isOpened() and cap.read() == (False, None)


# --- screens, machine, benchmark --------------------------------------------

def test_screens_machine_and_scale_are_sane():
    rects = hw.screens()
    assert rects and all(len(r) == 4 and r[2] > 0 and r[3] > 0 for r in rects)
    assert 1.0 <= hw.scale() <= 4.0
    m = hw.machine()
    assert m["cpu_cores"] >= 1
    assert m["ram_gb"] is None or m["ram_gb"] > 0


def test_linux_screens_put_the_primary_first(monkeypatch):
    from eye_gaze import platform

    class Backend:
        def primary(self):
            return (1920, 0, 2560, 1440)

        def screens(self):
            return [(0, 180, 1920, 1080), (1920, 0, 2560, 1440)]
    monkeypatch.setattr(hw, "_is_windows", lambda: False)
    monkeypatch.setattr(platform, "linux_backend", lambda: Backend())
    assert hw.screens() == [(1920, 0, 2560, 1440), (0, 180, 1920, 1080)]


def test_benchmark_image_is_the_public_domain_test_face():
    assert FACE.read_bytes() == (GAZE / "tests" / "data" / "faces"
                                 / "chawla.jpg").read_bytes()
    assert hw.benchmark_image_path() == str(FACE)
    assert (FACE.parent / "SOURCES.txt").is_file()


class FakeEst:
    def __init__(self, face=True):
        self.face = face
        self.landmarks = object()
        self.closed = False

    def estimate(self, frame):
        return (0.5, 0.5) if self.face else None

    def close(self):
        self.closed = True


def test_benchmark_hands_the_providers_to_the_deep_model():
    """The GPU path, with fakes only: what the wizard measures DirectML
    with. Nothing here touches a GPU."""
    est = FakeEst()
    seen = {}
    clock = Clock()

    class Deep:
        closed = False

        def estimate_from_landmarks(self, frame, lm):
            clock.t += 0.020
            return (0.1, 0.2)

        def close(self):
            Deep.closed = True

    def deep_factory(w, h, providers):
        seen["args"] = (w, h, providers)
        return Deep()
    ms = hw.benchmark_gaze(0.5, ["DmlExecutionProvider", "CPUExecutionProvider"],
                           image=np.zeros((480, 640, 3), np.uint8),
                           estimator_factory=lambda: est,
                           deep_factory=deep_factory, clock=clock)
    assert seen["args"] == (640, 480, ["DmlExecutionProvider",
                                       "CPUExecutionProvider"])
    assert ms == pytest.approx(20.0)
    assert est.closed and Deep.closed


def test_benchmark_without_a_face_raises_and_closes():
    est = FakeEst(face=False)
    with pytest.raises(RuntimeError):
        hw.benchmark_gaze(0.1, CPU, image=np.zeros((48, 64, 3), np.uint8),
                          estimator_factory=lambda: est,
                          deep_factory=lambda w, h, p: FakeEst())
    assert est.closed


def test_benchmark_gaze_on_cpu():
    """The real MediaPipe + ONNX path on the shipped face, CPU only."""
    pytest.importorskip("mediapipe")
    pytest.importorskip("onnxruntime")
    from eye_gaze import deep_onnx, gaze
    if deep_onnx.model_path() is None:
        pytest.skip("ONNX derin modeli bu checkout'ta yok")
    if not (os.path.exists(gaze._MODEL_PATH)
            or gaze.paths.bundled_model("face_landmarker.task")):
        pytest.skip("face_landmarker.task yok (indirilmesi gerekir)")
    ms = hw.benchmark_gaze(seconds=0.3, providers=CPU)
    assert 0.0 < ms < 5000.0


# --- decisions ---------------------------------------------------------------

def test_decide_cpu_slow_turns_deep_off():
    assert hw.decide({"gaze_ms": 80.0,
                      "gaze_provider": "CPUExecutionProvider"}) \
        == {"deep_gaze": False}
    fast_cpu = hw.decide({"gaze_ms": 30.0,
                          "gaze_provider": "CPUExecutionProvider"})
    assert fast_cpu == {"deep_gaze": True, "deep_device": "cpu"}
    gpu = hw.decide({"gaze_ms": 12.0, "gaze_provider": "DmlExecutionProvider"})
    assert gpu == {"deep_gaze": True, "deep_device": "auto"}
    assert hw.decide({"gaze_ms": 45.0})["deep_gaze"] is True
    assert hw.decide({"gaze_ms": 45.1})["deep_gaze"] is False
    # Nothing measured: nothing decided (the driver keeps its old rule).
    assert hw.decide({}) == {}
    assert hw.decide(None) == {}


@pytest.mark.parametrize("profile,expected", [
    # CUDA with 4+ GB free VRAM: medium on the GPU.
    ({"machine": {"cpu_cores": 2, "ram_gb": 4.0},
      "gpu": {"cuda": True, "vram_mb": 24576, "vram_free_mb": 6000}},
     ("medium", "cuda")),
    # 8 GB RAM, 4 cores, no CUDA: small on the CPU.
    ({"machine": {"cpu_cores": 4, "ram_gb": 8.0},
      "gpu": {"cuda": False, "vram_free_mb": 8000}},
     ("small", "cpu")),
    # Weak machine: base.
    ({"machine": {"cpu_cores": 2, "ram_gb": 4.0}}, ("base", "cpu")),
])
def test_decide_whisper_sizes(profile, expected):
    d = hw.decide(profile)
    assert (d["stt_model"], d["stt_device"]) == expected


def test_unknown_vram_is_not_four_gigabytes():
    d = hw.decide({"machine": {"cpu_cores": 8, "ram_gb": 16.0},
                   "gpu": {"cuda": True, "vram_mb": 24576,
                           "vram_free_mb": None}})
    assert d["stt_model"] == "small"
    d = hw.decide({"machine": {"cpu_cores": 8, "ram_gb": 16.0},
                   "gpu": {"cuda": True, "vram_free_mb": 3000}})
    assert d["stt_model"] == "small"


# --- resolve_auto -------------------------------------------------------------

FULL = {
    "camera": {"index": 2, "backend": "dshow", "width": 640, "height": 480,
               "fps": 30, "fps_measured": 29.8, "name": "x"},
    "gaze_ms": 90.0, "gaze_provider": "CPUExecutionProvider",
    "machine": {"cpu_cores": 2, "ram_gb": 4.0},
}


def _load_copy(src, tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "_report", lambda m: None)
    p = tmp_path / "config.yaml"
    shutil.copyfile(src, p)
    return config_mod.load_config(str(p))


@pytest.fixture
def explicit_config(tmp_path):
    """The template with its camera mode and Whisper size written out, as
    in an install set up by hand (no makine.json behind it)."""
    from eye_gaze import config_writer
    p = tmp_path / "explicit" / "config.yaml"
    p.parent.mkdir()
    shutil.copyfile(GAZE / "config.default.yaml", p)
    config_writer.set_values(str(p), {
        "camera_index": 0, "camera_width": 1280, "camera_height": 960,
        "camera_fps": 30, "stt_model": "medium"})
    return p


def test_resolve_auto_keeps_explicit_values(tmp_path, monkeypatch,
                                            explicit_config):
    """A config with explicit values: its camera mode, Whisper size and
    every other written value survive a profile that would decide all of
    them differently. And with no makine.json nothing changes at all."""
    cfg = _load_copy(explicit_config, tmp_path, monkeypatch)
    assert (cfg["camera_index"], cfg["camera_width"], cfg["camera_height"],
            cfg["camera_fps"], cfg["stt_model"]) == (0, 1280, 960, 30,
                                                     "medium")
    assert hw.load_profile() == {}
    assert hw.resolve_auto(cfg, hw.load_profile()) == cfg
    out = hw.resolve_auto(cfg, FULL)
    for key, value in cfg.items():
        if not config_mod.is_auto(value):
            assert out[key] == value, key
    # Only lines that literally say "auto" may take a measured value.
    changed = {k for k in cfg if out[k] != cfg[k]}
    assert changed <= {k for k, v in cfg.items() if config_mod.is_auto(v)}


def test_resolve_auto_fills_the_new_install_template(tmp_path, monkeypatch):
    cfg = _load_copy(GAZE / "config.default.yaml", tmp_path, monkeypatch)
    out = hw.resolve_auto(cfg, FULL)
    assert (out["camera_index"], out["camera_width"], out["camera_height"],
            out["camera_fps"]) == (2, 640, 480, 30)
    assert out["deep_gaze"] is False
    assert (out["stt_model"], out["stt_device"]) == ("base", "cpu")
    assert out["deep_device"] == "auto"      # deep off: nothing to decide
    assert out["pursuit_latency"] == "auto"  # learnt in calibration, not here
    assert cfg["camera_index"] == "auto"     # a copy: the input is untouched
    # Nothing in the profile: every "auto" stays "auto" (old defaults).
    assert hw.resolve_auto(cfg, {}) == cfg


def test_a_camera_mode_is_only_used_for_its_own_camera():
    cfg = {"camera_index": 1, "camera_width": "auto", "camera_height": "auto",
           "camera_fps": "auto"}
    out = hw.resolve_auto(cfg, FULL)             # FULL measured camera 2
    assert out == cfg
    cfg["camera_index"] = 2
    assert hw.resolve_auto(cfg, FULL)["camera_width"] == 640


def test_saved_decisions_are_what_the_wizard_decided():
    prof = dict(FULL, decisions={"deep_gaze": True, "stt_model": "small"})
    out = hw.resolve_auto({"deep_gaze": "auto", "stt_model": "auto",
                           "stt_device": "auto"}, prof)
    assert out == {"deep_gaze": True, "stt_model": "small",
                   "stt_device": "cpu"}


# --- makine.json ----------------------------------------------------------------

def test_profile_round_trip_is_atomic(_data_dir):
    assert hw.save_profile(FULL)
    path = _data_dir / "makine.json"
    assert hw.profile_path() == str(path)
    data = hw.load_profile()
    assert data["version"] == hw.PROFILE_VERSION
    assert data["camera"] == FULL["camera"]
    assert os.listdir(_data_dir) == ["makine.json"]     # no .tmp left
    path.read_bytes().decode("ascii")


@pytest.mark.parametrize("content", [
    b"{ yarim", b"[1, 2]", b'{"version": 0, "camera": {"index": 0}}',
    b'{"camera": {"index": 0}}', b"\xff\xfe",
])
def test_a_corrupt_or_old_profile_is_ignored(_data_dir, content, capsys):
    (_data_dir / "makine.json").write_bytes(content)
    assert hw.load_profile() == {}
    out = capsys.readouterr().out
    assert "yeniden olculecek" in out and out.isascii()


def test_a_failed_save_keeps_the_old_profile(_data_dir, monkeypatch, capsys):
    assert hw.save_profile({"gaze_ms": 10.0})
    before = (_data_dir / "makine.json").read_bytes()

    def boom(a, b):
        raise OSError("disk full")
    monkeypatch.setattr(hw.os, "replace", boom)
    assert hw.save_profile(FULL) is False
    assert (_data_dir / "makine.json").read_bytes() == before
    assert os.listdir(_data_dir) == ["makine.json"]
    assert "yazilamadi" in capsys.readouterr().out


# --- the driver ------------------------------------------------------------------

def test_driver_prints_missing_capabilities_once(monkeypatch):
    monkeypatch.setattr(gd, "_caps_said", [False])
    caps = {"cursor": (True, ""),
            "zoom_capture": (False, "Wayland bu oturumda ekran görüntüsüne "
                                    "izin vermiyor"),
            "snap": (False, "Hedefe yapışma yalnızca Windows'ta var")}
    monkeypatch.setattr(gd._platform, "capabilities", lambda: caps)
    lines = []
    gd._print_missing_capabilities(say=lines.append)
    gd._print_missing_capabilities(say=lines.append)
    text = "\n".join(lines)
    assert text.count("zoom_capture") == 1 and text.count("snap") == 1
    assert "cursor" not in text
    assert "goruntusune" in text and "yapisma" in text
    assert text.isascii()


def test_a_session_with_everything_prints_nothing(monkeypatch):
    monkeypatch.setattr(gd, "_caps_said", [False])
    lines = []
    gd._print_missing_capabilities(caps={"cursor": (True, "")},
                                   say=lines.append)
    assert lines == []


def test_the_driver_start_wires_the_profile():
    import inspect
    src = inspect.getsource(gd.main)
    alarm = src.index("start_alarm(")
    caps = src.index("_print_missing_capabilities()")
    resolve = src.index("_resolve_machine(cfg)")
    assert alarm < caps < src.index("_ResilientCapture(")
    assert alarm < resolve < src.index("_camera_request(cfg)")
    assert ("_camera_opener(cam_index, cam_w, cam_h, cam_fps, machine_cam)"
            in src)
    assert "_ResilientCapture(open_camera)" in src
    assert src.index("_keep_deep_for_calibration(") < src.index("_make_deep(")


CAM0 = hw.CameraInfo(0, "msmf", 1280, 960, 29.9, "")


def test_driver_probes_once_and_remembers_the_camera(_data_dir):
    cfg = {"camera_index": "auto", "camera_width": "auto",
           "camera_height": "auto", "camera_fps": "auto"}
    probes = []

    def probe(**kw):
        probes.append(kw)
        return [CAM0]
    out, profile, cam = gd._resolve_machine(cfg, probe=probe,
                                            say=lambda m: None)
    assert probes == [{}]                     # "auto": every camera
    assert (out["camera_index"], out["camera_width"], out["camera_height"],
            out["camera_fps"]) == (0, 1280, 960, 30)
    assert cam["backend"] == "msmf"
    saved = json.loads((_data_dir / "makine.json").read_text("ascii"))
    assert saved["camera"]["width"] == 1280 and saved["screens"]
    # Next start: from makine.json, no probe.
    out2, _, _ = gd._resolve_machine(
        cfg, probe=lambda **k: pytest.fail("probed again"),
        say=lambda m: None)
    assert out2 == out


def test_a_corrupt_profile_is_probed_again(_data_dir):
    (_data_dir / "makine.json").write_bytes(b"\x00garbage")
    cfg = {"camera_index": "auto"}
    out, _, _ = gd._resolve_machine(cfg, probe=lambda **k: [CAM0],
                                    say=lambda m: None)
    assert out["camera_index"] == 0
    assert hw.load_profile()["camera"]["index"] == 0


def test_no_camera_found_keeps_auto_and_the_old_default(_data_dir):
    cfg = {"camera_index": "auto", "camera_width": "auto"}
    lines = []
    out, profile, cam = gd._resolve_machine(cfg, probe=lambda **k: [],
                                            say=lines.append, now=1000.0)
    assert out == cfg and cam is None
    assert gd._camera_request(out)[0] == 0
    assert any("bulunamadi" in m for m in lines)
    assert hw.load_profile()["camera_missing"] == {"index": "auto",
                                                   "at": 1000.0}


def test_a_missing_camera_is_not_probed_again_for_a_day(_data_dir):
    """camera_index: 1 with an "auto" mode and no camera 1: only camera 1
    is probed, and not again on every start (up to 6 x 8 s each time)."""
    cfg = {"camera_index": 1, "camera_width": "auto",
           "camera_height": "auto", "camera_fps": "auto"}
    calls = []

    def probe(**kw):
        calls.append(kw)
        return []
    out, _, cam = gd._resolve_machine(cfg, probe=probe, say=lambda m: None,
                                      now=1000.0)
    assert calls == [{"indices": [1]}]
    assert out == cfg and cam is None
    assert gd._camera_request(out) == (1, 1280, 720, 60)   # old defaults
    lines = []
    gd._resolve_machine(cfg, probe=probe, say=lines.append,
                        now=1000.0 + hw.NO_CAMERA_RETRY_S - 1)
    assert len(calls) == 1 and lines and lines[0].isascii()
    # A day later it is looked for again - and found.
    found = hw.CameraInfo(1, "msmf", 640, 480, 30.0, "")
    out, _, cam = gd._resolve_machine(
        cfg, probe=lambda **k: [found], say=lambda m: None,
        now=1000.0 + hw.NO_CAMERA_RETRY_S + 1)
    assert (out["camera_width"], cam["index"]) == (640, 1)
    assert "camera_missing" not in hw.load_profile()
    # A failed search for ANOTHER setting is not this one's.
    hw.save_profile({"camera_missing": {"index": "auto", "at": 1000.0}})
    calls.clear()
    gd._resolve_machine(cfg, probe=probe, say=lambda m: None, now=1001.0)
    assert calls == [{"indices": [1]}]


def test_the_smoke_camera_is_used_but_never_saved(_data_dir, monkeypatch):
    monkeypatch.setenv(hw.FAKE_CAMERA_ENV, str(FACE))
    cfg = {"camera_index": "auto", "camera_width": "auto",
           "camera_height": "auto", "camera_fps": "auto"}
    out, profile, cam = gd._resolve_machine(cfg, say=lambda m: None)
    assert (out["camera_index"], out["camera_width"]) == (0, 640)
    assert cam["backend"] == "fake"
    saved = hw.load_profile()
    assert "camera" not in saved and "calibration_camera" not in saved
    assert not gd._note_calibration_camera(profile, cam, fresh=True,
                                           save=hw.save_profile)
    # Even handed to save_profile directly, a fake record is dropped.
    hw.save_profile({"camera": dict(cam),
                     "calibration_camera": dict(cam, fake=True),
                     "gaze_ms": 10.0})
    assert hw.load_profile() == {"gaze_ms": 10.0,
                                 "version": hw.PROFILE_VERSION}


def test_explicit_camera_settings_never_probe_or_write(_data_dir, tmp_path,
                                                       monkeypatch,
                                                       explicit_config):
    cfg = _load_copy(explicit_config, tmp_path, monkeypatch)
    out, profile, cam = gd._resolve_machine(
        cfg, probe=lambda **k: pytest.fail("probed"), say=lambda m: None)
    assert out == cfg and profile == {} and cam is None
    assert not (_data_dir / "makine.json").exists()


def test_a_broken_profile_step_never_stops_the_driver(monkeypatch):
    monkeypatch.setattr(hw, "load_profile",
                        lambda: (_ for _ in ()).throw(RuntimeError("x")))
    cfg = {"camera_index": "auto"}
    lines = []
    out, profile, cam = gd._resolve_machine(cfg, say=lines.append)
    assert out == cfg and profile == {} and cam is None
    assert lines and all(m.isascii() for m in lines)


def test_screens_are_rerecorded_in_an_existing_profile(_data_dir, monkeypatch):
    hw.save_profile({"screens": [[0, 0, 800, 600]], "scale": 1.0})
    monkeypatch.setattr(hw, "screens", lambda: [(0, 0, 2560, 1440)])
    monkeypatch.setattr(hw, "scale", lambda: 1.5)
    gd._resolve_machine({"camera_index": 0}, say=lambda m: None)
    p = hw.load_profile()
    assert p["screens"] == [[0, 0, 2560, 1440]] and p["scale"] == 1.5


def test_another_camera_warns_and_never_recalibrates(_data_dir):
    old = hw.camera_identity(hw.camera_record(CAM0))
    new = hw.camera_record(hw.CameraInfo(1, "msmf", 640, 480, 30.0, ""))
    profile = {"camera": new, "calibration_camera": old}
    line = gd._calibration_camera_warning(profile, new)
    assert "kalibrasyon bu kamerayla yapilmadi; F7 ya da F9 onerilir" in line
    assert line.isascii()
    assert gd._calibration_camera_warning(
        profile, hw.camera_record(CAM0)) is None
    assert gd._calibration_camera_warning({}, new) is None
    assert gd._calibration_camera_warning(profile, None) is None
    saves = []
    # A loaded calibration does not overwrite the evidence: the warning
    # repeats until the user recalibrates.
    assert not gd._note_calibration_camera(profile, new, fresh=False,
                                           save=saves.append)
    assert profile["calibration_camera"] == old and saves == []
    # A new, saved calibration records the camera it was taken with: its
    # identity, without the backend.
    gd._note_calibration_camera(profile, new, fresh=True, save=saves.append)
    assert profile["calibration_camera"] == {"index": 1, "name": "",
                                             "width": 640, "height": 480}
    assert len(saves) == 1
    # A camera not from makine.json (explicit config): nothing recorded.
    assert not gd._note_calibration_camera({}, None, fresh=True,
                                           save=saves.append)


def test_the_same_camera_through_another_backend_does_not_nag():
    """MSMF failed once and the probe found the same device via DirectShow:
    same picture, same calibration - no warning on every start."""
    via_msmf = hw.camera_record(CAM0)
    via_dshow = dict(via_msmf, backend="dshow")
    profile = {"calibration_camera": hw.camera_identity(via_msmf)}
    assert hw.same_camera(via_msmf, via_dshow)
    assert gd._calibration_camera_warning(profile, via_dshow) is None


def test_a_calibration_older_than_makine_json_is_unknown_not_adopted():
    """makine.json lost (or never there) while a calibration exists: which
    camera took it is not known. Neither claim today's camera nor nag."""
    cam = hw.camera_record(CAM0)
    profile = {"camera": cam}
    saves = []

    def save(p):
        saves.append(dict(p))
        return True
    assert gd._note_calibration_camera(profile, cam, fresh=False, save=save)
    assert profile["calibration_camera"] == "unknown" and len(saves) == 1
    assert gd._calibration_camera_warning(profile, cam) is None
    other = hw.camera_record(hw.CameraInfo(1, "msmf", 640, 480, 30.0, ""))
    assert gd._calibration_camera_warning(profile, other) is None
    # Recorded once; later starts write nothing.
    assert not gd._note_calibration_camera(profile, cam, fresh=False,
                                           save=save)
    assert len(saves) == 1
    # A new calibration replaces "unknown" with the real camera.
    gd._note_calibration_camera(profile, cam, fresh=True, save=save)
    assert profile["calibration_camera"] == hw.camera_identity(cam)


def test_main_records_the_camera_only_after_a_saved_calibration():
    import inspect
    src = inspect.getsource(gd.main)
    assert src.count("_note_calibration_camera(") == 3
    assert "if save_cal and _save_calibration(cal_file, mapper, feat=feat_id):" \
        in src
    assert "_calibration_camera_warning(machine_profile, machine_cam)" in src


def test_profile_turning_deep_off_keeps_a_deep_calibration(monkeypatch):
    asked = []

    def expected(cfg, path, base):
        asked.append((cfg["deep_gaze"], path, base))
        return True
    monkeypatch.setattr(gd, "_deep_expected", expected)
    lines = []
    out = gd._keep_deep_for_calibration({"deep_gaze": False}, True,
                                        "cal.json", 9, say=lines.append)
    assert out["deep_gaze"] == "auto" and asked == [("auto", "cal.json", 9)]
    assert lines and lines[0].isascii()
    # Explicit false in config.yaml: the user's choice, untouched.
    assert gd._keep_deep_for_calibration({"deep_gaze": False}, False,
                                         "cal.json", 9) == {"deep_gaze": False}
    # The profile turned it ON: nothing to protect.
    assert gd._keep_deep_for_calibration({"deep_gaze": True}, True,
                                         "cal.json", 9) == {"deep_gaze": True}
    monkeypatch.setattr(gd, "_deep_expected", lambda *a: False)
    assert gd._keep_deep_for_calibration({"deep_gaze": False}, True,
                                         "cal.json", 9) == {"deep_gaze": False}


def test_resolved_dictation_reaches_the_driver():
    out = hw.resolve_auto({"stt_model": "auto", "stt_device": "auto"},
                          {"machine": {"cpu_cores": 8, "ram_gb": 16.0}})
    assert gd._stt_model_name(out) == "small"
    assert out["stt_device"] == "cpu"


# --- explicit camera: opened exactly as before (controller ruling) -------------

class RecVideoCapture:
    """Fake cv2.VideoCapture: records how it was constructed and set."""
    made = []

    def __init__(self, *args):
        self.args = args
        self.sets = []
        self.opened = True
        RecVideoCapture.made.append(self)

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        self.sets.append((prop, value))
        return True

    def release(self):
        pass


@pytest.fixture
def rec_cv2(monkeypatch):
    import cv2
    RecVideoCapture.made = []
    monkeypatch.setattr(cv2, "VideoCapture", RecVideoCapture)
    return cv2


def test_an_explicit_camera_opens_exactly_as_before(rec_cv2):
    """config.yaml names the camera (the development machine): cv2.VideoCapture(index)
    with NO backend argument and the same three set() calls - also on every
    _ResilientCapture reconnect."""
    cv2 = rec_cv2
    opener = gd._camera_opener(0, 1280, 960, 30, None)
    cap = gd._ResilientCapture(opener)
    assert cap.isOpened()
    first = RecVideoCapture.made[0]
    assert first.args == (0,)
    assert first.sets == [(cv2.CAP_PROP_FRAME_WIDTH, 1280),
                          (cv2.CAP_PROP_FRAME_HEIGHT, 960),
                          (cv2.CAP_PROP_FPS, 30)]
    # The reconnect path calls the same opener.
    again = cap._opener()
    assert again.args == (0,) and again.sets == first.sets
    assert [c.args for c in RecVideoCapture.made] == [(0,), (0,)]


def test_an_explicit_camera_that_does_not_open_is_not_set(rec_cv2):
    class Closed(RecVideoCapture):
        def __init__(self, *args):
            super().__init__(*args)
            self.opened = False
    import cv2
    cv2.VideoCapture = Closed
    c = gd._camera_opener(1, 1280, 720, 60, None)()
    assert c.args == (1,) and c.sets == []


def test_a_probed_camera_opens_with_its_recorded_backend(rec_cv2):
    cv2 = rec_cv2
    cam = hw.camera_record(hw.CameraInfo(2, "dshow", 640, 480, 30.0, ""))
    c = gd._camera_opener(2, 640, 480, 30, cam)()
    assert c.args == (2, cv2.CAP_DSHOW)
    assert c.sets == [(cv2.CAP_PROP_FRAME_WIDTH, 640),
                      (cv2.CAP_PROP_FRAME_HEIGHT, 480),
                      (cv2.CAP_PROP_FPS, 30)]


def test_a_probed_camera_without_a_backend_uses_the_os_order(rec_cv2,
                                                            monkeypatch):
    cv2 = rec_cv2
    monkeypatch.setattr(hw, "_is_windows", lambda: False)
    c = gd._camera_opener(0, 640, 480, 30, {"index": 0})()
    assert c.args == (0, cv2.CAP_V4L2)


def test_the_smoke_camera_replaces_an_explicit_one(rec_cv2, monkeypatch):
    monkeypatch.setenv(hw.FAKE_CAMERA_ENV, str(FACE))
    c = gd._camera_opener(0, 1280, 960, 30, None)()
    assert isinstance(c, hw._FakeCapture) and RecVideoCapture.made == []
