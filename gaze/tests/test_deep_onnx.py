# gaze/tests/test_deep_onnx.py
"""The torch-free deep gaze model (eye_gaze.deep_onnx) and the backend choice
(eye_gaze.deep.make_deep_gaze, gaze_driver._make_deep).

Equivalence is what keeps a calibration valid across backends: the same face
must give the same two features (within 1e-3 rad) from torch + ptgaze and
from onnxruntime. Every run here is on the CPU (CPUExecutionProvider,
DeepGaze(device="cpu")): no test touches a GPU.

The ONNX model is not committed (tools/derin_onnx_cevir.py builds it); tests
that need it skip with that reason when it is absent. Face images and their
recorded torch outputs are in tests/data/faces (see SOURCES.txt).
"""
import json
import math
import os
import sys
import types

import numpy as np
import pytest

import gaze_driver as gd
from eye_gaze import accel, deep, deep_onnx

FACES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "faces")
TOL_RAD = 1e-3


def _reference():
    with open(os.path.join(FACES, "reference.json"), encoding="ascii") as f:
        return json.load(f)["faces"]


def _image(name):
    cv2 = pytest.importorskip("cv2")
    img = cv2.imdecode(np.fromfile(os.path.join(FACES, name), np.uint8),
                       cv2.IMREAD_COLOR)
    assert img is not None, name
    return img


def _landmarks(points):
    return [types.SimpleNamespace(x=x, y=y) for x, y in points]


def _angle(a, b):
    """Angle in rad between two camera-frame gaze directions given as their
    (x, y): the gaze points back at the camera, so z = -sqrt(1 - x^2 - y^2)."""
    def vec(g):
        x, y = g
        return np.array([x, y, -math.sqrt(max(0.0, 1.0 - x * x - y * y))])
    u, v = vec(a), vec(b)
    return math.atan2(np.linalg.norm(np.cross(u, v)), float(np.dot(u, v)))


def _onnx_model_or_skip():
    pytest.importorskip("onnxruntime")
    p = deep_onnx.model_path()
    if p is None:
        pytest.skip("ONNX modeli bu checkout'ta yok "
                    "(resources/models/%s; tools/derin_onnx_cevir.py uretir)"
                    % deep_onnx.MODEL_NAME)
    return p


def _onnx_cpu(w, h):
    return deep_onnx.OnnxDeepGaze(w, h, ["CPUExecutionProvider"])


# --- torch-free: these run in the Linux Docker suite too --------------------


def test_reference_faces_are_distinct():
    """Non-vacuity for the equivalence tests: three different gazes."""
    ref = _reference()
    assert len(ref) == 3
    outs = [tuple(r["torch_cpu"]) for r in ref.values()]
    for i in range(3):
        for j in range(i + 1, 3):
            assert _angle(outs[i], outs[j]) > 0.02


def test_onnx_model_matches_its_sha256():
    p = _onnx_model_or_skip()
    assert os.path.isfile(p + ".sha256")
    deep_onnx.load_model_bytes(p)       # raises on a mismatch


def test_a_damaged_model_is_refused(tmp_path):
    p = tmp_path / "m.onnx"
    p.write_bytes(b"bozuk")
    (tmp_path / "m.onnx.sha256").write_text("0" * 64 + "  m.onnx\n")
    with pytest.raises(ValueError, match="damaged"):
        deep_onnx.load_model_bytes(str(p))


def test_a_frozen_build_refuses_a_model_without_its_sha256(tmp_path,
                                                           monkeypatch):
    """Packages always ship the sidecar: its absence there means a damaged
    install, and an unverifiable model must not drive the cursor."""
    p = tmp_path / "m.onnx"
    p.write_bytes(b"model")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(ValueError, match="no .sha256"):
        deep_onnx.load_model_bytes(str(p))


def test_a_source_checkout_may_load_a_model_without_its_sha256(tmp_path,
                                                               monkeypatch):
    p = tmp_path / "m.onnx"
    p.write_bytes(b"model")
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert deep_onnx.load_model_bytes(str(p)) == b"model"


def test_a_frozen_build_loads_a_model_whose_sha256_matches(tmp_path,
                                                           monkeypatch):
    import hashlib
    p = tmp_path / "m.onnx"
    p.write_bytes(b"model")
    (tmp_path / "m.onnx.sha256").write_text(
        hashlib.sha256(b"model").hexdigest() + "  m.onnx" + chr(10))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert deep_onnx.load_model_bytes(str(p)) == b"model"


def test_onnx_matches_recorded_torch_outputs():
    """The recorded torch + ptgaze CPU answers for these exact landmarks
    (reference.json): checks the whole ONNX path - head pose, normalisation,
    transform, network, denormalisation - where torch is not installed."""
    _onnx_model_or_skip()
    worst = 0.0
    for name, r in _reference().items():
        w, h = r["size"]
        dg = _onnx_cpu(w, h)
        try:
            assert dg.device == "cpu" and dg.backend == "onnx"
            got = dg.estimate_from_landmarks(_image(name), _landmarks(r["landmarks"]))
        finally:
            dg.close()
        worst = max(worst, _angle(got, r["torch_cpu"]))
    assert worst < TOL_RAD, worst


def test_onnx_full_frame_estimate_finds_the_face():
    """estimate(frame) - the fallback with its own face detection - gives the
    same answer as the landmark path (same MediaPipe model, IMAGE mode)."""
    _onnx_model_or_skip()
    pytest.importorskip("mediapipe")
    from eye_gaze import gaze
    if not (os.path.isfile(gaze._MODEL_PATH)
            or deep_onnx.paths.bundled_model("face_landmarker.task")):
        pytest.skip("face_landmarker.task yok (testte indirme yapilmaz)")
    r = _reference()["ramon.jpg"]
    dg = _onnx_cpu(*r["size"])
    try:
        got = dg.estimate(_image("ramon.jpg"))
        blank = dg.estimate(np.zeros((480, 640, 3), np.uint8))
    finally:
        dg.close()
    assert blank is None
    assert _angle(got, r["torch_cpu"]) < 0.02


def test_gaze_angles_to_vector_looks_back_at_the_camera():
    v = deep_onnx.gaze_angles_to_vector((0.0, 0.0))
    assert np.allclose(v, [0.0, 0.0, -1.0])
    v = deep_onnx.gaze_angles_to_vector((0.3, -0.2))
    assert math.isclose(np.linalg.norm(v), 1.0)
    assert v[1] < 0 and v[0] > 0        # pitch up -> -y, yaw left -> +x


def test_input_transform_is_rgb_mean_std():
    patch = np.zeros((224, 224, 3), np.uint8)
    patch[..., 0] = 255                 # blue in BGR
    x = deep_onnx.to_input(patch)
    assert x.shape == (1, 3, 224, 224) and x.dtype == np.float32
    # channel 2 (B after BGR -> RGB) is full, R and G are empty
    assert np.allclose(x[0, 2], (1.0 - 0.406) / 0.225, atol=1e-6)
    assert np.allclose(x[0, 0], (0.0 - 0.485) / 0.229, atol=1e-6)


def test_dummy_camera_matches_ptgaze_create_dummy():
    k = deep_onnx.dummy_camera_matrix(1281, 961)
    assert k.tolist() == [[1281, 0, 640], [0, 1281, 480], [0, 0, 1]]


_NO_SCIPY_CHILD = """
import sys

class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('scipy', 'torch', 'ptgaze'):
            raise ImportError('blocked ' + name)

sys.meta_path.insert(0, Block())
import cv2
import numpy as np
from eye_gaze import accel, deep, deep_onnx as d

k = d.dummy_camera_matrix(640, 480)
rv, tv = np.array([0.1, -0.2, 0.05]), np.array([0.02, 0.01, 0.6])
pts = cv2.projectPoints(d._LANDMARKS_3D, rv, tv, k, None)[0][:, 0]
rot, t, m3 = d.estimate_head_pose(pts, k)
assert np.allclose(rot, cv2.Rodrigues(rv)[0], atol=1e-6), rot
assert np.allclose(t, tv, atol=1e-6), t
img = np.full((480, 640, 3), 128, np.uint8)
patch, nrot = d.normalize_face(img, k, rot, d.face_center(m3))
assert patch.shape == (224, 224, 3)
assert np.allclose(nrot @ nrot.T, np.eye(3), atol=1e-12)
assert d.to_input(patch).shape == (1, 3, 224, 224)
assert not [m for m in sys.modules
            if m.split('.')[0] in ('scipy', 'torch', 'ptgaze')]
print('ok')
"""


def test_deep_onnx_needs_no_scipy_and_no_torch():
    """The package ships neither: the whole preprocessing runs with scipy,
    torch and ptgaze made unimportable, and a head turned by a known
    rotation is recovered by solvePnP + cv2.Rodrigues."""
    import subprocess
    pytest.importorskip("cv2")
    code = _NO_SCIPY_CHILD
    gaze_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-c", code], cwd=gaze_dir,
                         capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "ok", out.stderr


# --- against ptgaze itself (Windows dev machine: torch + ptgaze) ------------


def _ptgaze_or_skip():
    pytest.importorskip("torch")
    pytest.importorskip("ptgaze")
    pytest.importorskip("scipy")


def test_normalizer_matches_ptgaze():
    """224x224 normalised face patch: ptgaze's FaceModelMediaPipe +
    HeadPoseNormalizer vs the port (cv2.Rodrigues instead of scipy)."""
    _ptgaze_or_skip()
    from ptgaze.common import Camera
    from ptgaze.common.face_model_mediapipe import FaceModelMediaPipe
    from ptgaze.head_pose_estimation import HeadPoseNormalizer
    from ptgaze.mode import GazeEstimationMode
    pkg = os.path.dirname(sys.modules["ptgaze"].__file__)
    norm_cam = Camera.from_file(os.path.join(
        pkg, "data", "normalized_camera_params", "eth-xgaze.yaml"))
    fm = FaceModelMediaPipe()
    for name, r in _reference().items():
        w, h = r["size"]
        img = _image(name)
        pts = np.array([(x * w, y * h) for x, y in r["landmarks"]])
        cam = Camera.create_dummy(w, h)
        hp = fm.estimate_head_pose(pts, cam)
        centers = fm.compute_centers(hp.model3d, GazeEstimationMode.ETH_XGAZE)
        ref = HeadPoseNormalizer(cam, norm_cam, 0.6).normalize(
            img, hp.rot, centers.face)

        k = deep_onnx.dummy_camera_matrix(w, h)
        assert np.array_equal(k, cam.camera_matrix)
        rot, tvec, model3d = deep_onnx.estimate_head_pose(pts, k)
        assert np.allclose(rot, hp.rot.as_matrix(), atol=1e-9)
        assert np.allclose(tvec, hp.position, atol=1e-9)
        center = deep_onnx.face_center(model3d)
        assert np.allclose(center, centers.face, atol=1e-9)
        patch, nrot = deep_onnx.normalize_face(img, k, rot, center)
        assert np.allclose(nrot, ref.normalizing_rot.as_matrix(), atol=1e-9)
        assert patch.shape == ref.image.shape == (224, 224, 3)
        diff = np.abs(patch.astype(float) - ref.image.astype(float))
        assert diff.mean() < 1.0, (name, diff.mean())
        assert patch.std() > 20, "bos yama: karsilastirma bos gecti"


def _readonly_deep_inputs(monkeypatch):
    """DeepGaze's model files, found read-only: never copied, never
    downloaded (the real resolvers copy into C:\\Users\\Public on a miss)."""
    ckpt = os.path.join(deep._ASCII_MODEL_DIR, deep.CHECKPOINT_NAME)
    task = os.path.join(deep._ASCII_MODEL_DIR, "face_landmarker.task")
    if not (os.path.isfile(ckpt) and os.path.isfile(task)):
        pytest.skip("ptgaze model dosyalari bu makinede yok")
    monkeypatch.setattr(deep, "ethxgaze_checkpoint", lambda: ckpt)
    monkeypatch.setattr(deep, "_ascii_mediapipe_model", lambda: task)


def test_onnx_matches_ptgaze_end_to_end(monkeypatch):
    """DeepGaze(cpu) vs OnnxDeepGaze(cpu), same landmarks, same frames."""
    _ptgaze_or_skip()
    _onnx_model_or_skip()
    _readonly_deep_inputs(monkeypatch)
    worst = 0.0
    for name, r in _reference().items():
        w, h = r["size"]
        img = _image(name)
        lm = _landmarks(r["landmarks"])
        tg = deep.DeepGaze(w, h, device="cpu")
        og = _onnx_cpu(w, h)
        try:
            a = tg.estimate_from_landmarks(img, lm)
            b = og.estimate_from_landmarks(img, lm)
        finally:
            tg.close()
            og.close()
        assert _angle(a, r["torch_cpu"]) < 1e-6, "reference.json eskimis"
        worst = max(worst, _angle(a, b))
    assert worst < TOL_RAD, worst


# --- which backend make_deep_gaze() builds -----------------------------------


@pytest.fixture
def backends(monkeypatch):
    """torch / ptgaze / onnxruntime availability as `state` says; both model
    classes replaced by recorders. Nothing real is loaded."""
    state = {"torch": True, "cuda": True, "ptgaze": True, "onnx": True}
    built = []
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: state["cuda"])

    def install():
        monkeypatch.setitem(sys.modules, "torch", torch if state["torch"] else None)
        monkeypatch.setitem(sys.modules, "ptgaze",
                            types.ModuleType("ptgaze") if state["ptgaze"] else None)
    install()
    monkeypatch.setattr(deep, "_onnx_ready", lambda: state["onnx"])
    monkeypatch.setattr(deep, "_onnx_gpu", lambda: True)

    class FakeTorchGaze:
        backend = "torch"

        def __init__(self, w, h, device=None):
            self.device = device
            built.append(("torch", device))

        def close(self):
            pass

    class FakeOnnxGaze:
        backend = "onnx"

        def __init__(self, w, h, providers):
            self.device = "dml" if providers[0] == accel.DML else "cpu"
            built.append(("onnx", list(providers)))

        def close(self):
            pass

    monkeypatch.setattr(deep, "DeepGaze", FakeTorchGaze)
    monkeypatch.setattr(deep_onnx, "OnnxDeepGaze", FakeOnnxGaze)
    monkeypatch.setattr(accel, "_is_windows", lambda: True)
    return state, built, install


def test_make_deep_prefers_torch_in_source_when_cuda(backends):
    """The development machine (torch + ptgaze + CUDA, and an ONNX model built in the
    checkout): today's torch path - the user's calibration was taken with it."""
    state, built, _ = backends
    dg = deep.make_deep_gaze(1280, 960, "auto")
    assert dg.backend == "torch" and built == [("torch", "cuda")]
    assert deep.select_backend("auto") == ("torch", True)


def test_make_deep_uses_onnx_when_no_torch(backends):
    """A package: no torch. DirectML first on Windows, then CPU."""
    state, built, install = backends
    state["torch"] = False
    install()
    dg = deep.make_deep_gaze(1280, 960, "auto")
    assert dg.backend == "onnx"
    assert built == [("onnx", [accel.DML, accel.CPU])]


def test_make_deep_uses_onnx_when_torch_has_no_cuda(backends):
    state, built, _ = backends
    state["cuda"] = False
    assert deep.make_deep_gaze(1280, 960, "auto").backend == "onnx"


def test_make_deep_uses_onnx_when_ptgaze_is_missing(backends):
    state, built, install = backends
    state["ptgaze"] = False
    install()
    assert deep.make_deep_gaze(1280, 960, "auto").backend == "onnx"


def test_torch_on_cpu_only_without_an_onnx_model(backends):
    """A source checkout that never built the ONNX model keeps today's CPU
    fallback rather than losing the deep model."""
    state, built, _ = backends
    state["cuda"] = False
    state["onnx"] = False
    dg = deep.make_deep_gaze(1280, 960, "auto")
    assert dg.backend == "torch" and built == [("torch", "cpu")]


def test_nothing_to_run_it_raises(backends):
    state, built, install = backends
    state["torch"] = False
    state["onnx"] = False
    install()
    assert deep.select_backend("auto") == (None, False)
    with pytest.raises(RuntimeError):
        deep.make_deep_gaze(1280, 960, "auto")


def test_deep_backend_onnx_is_honoured_with_torch_present(backends):
    state, built, _ = backends
    assert deep.make_deep_gaze(1280, 960, "auto", backend="onnx").backend == "onnx"


def test_deep_device_cuda_alias(backends):
    """deep_device: cuda (config.yaml's documented value) = gpu, both backends."""
    state, built, install = backends
    deep.make_deep_gaze(1280, 960, "cuda")
    deep.make_deep_gaze(1280, 960, "gpu")
    assert built == [("torch", "cuda"), ("torch", "cuda")]
    state["torch"] = False
    install()
    built.clear()
    deep.make_deep_gaze(1280, 960, "cuda")
    deep.make_deep_gaze(1280, 960, "cpu")
    assert built == [("onnx", [accel.DML, accel.CPU]), ("onnx", [accel.CPU])]


class _Cap:
    def get(self, prop):
        return {gd.cv2.CAP_PROP_FRAME_WIDTH: 1280,
                gd.cv2.CAP_PROP_FRAME_HEIGHT: 960}.get(prop, 0)


def test_make_deep_driver_cuda_alias_and_gpu(backends):
    state, built, _ = backends
    for value in ("cuda", "gpu"):
        dg = gd._make_deep({"deep_gaze": "auto", "deep_device": value}, _Cap(),
                           expected=True)
        assert dg.device == "cuda"
    assert built == [("torch", "cuda"), ("torch", "cuda")]


def test_make_deep_driver_onnx_cpu_fallback_keeps_a_deep_calibration(
        backends, capsys, monkeypatch):
    """Package on a PC whose DirectML could not open a device: the ONNX
    session runs on the CPU. A calibration taken with the deep model needs it,
    so it stays - and the user is told why the cursor is slower."""
    state, built, install = backends
    state["torch"] = False
    install()

    class CpuOnly(deep_onnx.OnnxDeepGaze):
        def __init__(self, w, h, providers):
            super().__init__(w, h, providers)
            self.device = "cpu"
    monkeypatch.setattr(deep_onnx, "OnnxDeepGaze", CpuOnly)
    dg = gd._make_deep({"deep_gaze": "auto"}, _Cap(), expected=True)
    assert dg is not None and dg.device == "cpu"
    assert "CPU'da ACILDI" in capsys.readouterr().out


def test_make_deep_driver_onnx_on_cpu_without_need_stays_geometric(
        backends, monkeypatch):
    """Same PC, no deep calibration: deep on the CPU is only slow."""
    state, built, install = backends
    state["torch"] = False
    install()

    class CpuOnly(deep_onnx.OnnxDeepGaze):
        def __init__(self, w, h, providers):
            super().__init__(w, h, providers)
            self.device = "cpu"
    monkeypatch.setattr(deep_onnx, "OnnxDeepGaze", CpuOnly)
    assert gd._make_deep({"deep_gaze": "auto"}, _Cap(), expected=False) is None
    dg = gd._make_deep({"deep_gaze": True}, _Cap(), expected=False)
    assert dg is not None and dg.device == "cpu"


def test_make_deep_driver_without_any_runtime_says_so(backends, capsys):
    state, built, install = backends
    state["torch"] = False
    state["onnx"] = False
    install()
    assert gd._make_deep({"deep_gaze": True}, _Cap()) is None
    assert "Geometrik mod" in capsys.readouterr().out


def test_cpu_rebuild_keeps_the_backend(backends):
    """30 failing GPU frames rebuild the model on the CPU - with the backend
    the session started with, so the features (and calibration) stay."""
    state, built, _ = backends
    onnx_gpu = types.SimpleNamespace(backend="onnx", device="dml")
    new = gd._deep_cpu_rebuilder(onnx_gpu, _Cap())()
    assert new.backend == "onnx" and built == [("onnx", [accel.CPU])]
    built.clear()
    torch_gpu = types.SimpleNamespace(backend="torch", device="cuda")
    new = gd._deep_cpu_rebuilder(torch_gpu, _Cap())()
    assert new.backend == "torch" and built == [("torch", "cpu")]


# --- review round 1: fallbacks and what gets said ----------------------------


class _FakeOrt(types.ModuleType):
    """onnxruntime stand-in: `fail` lists providers whose session creation
    raises; `silent_cpu` makes every session end up CPU-only, as
    onnxruntime-gpu does when the CUDA libraries are missing."""

    def __init__(self, available, fail=(), silent_cpu=False):
        super().__init__("onnxruntime")
        self.available, self.fail, self.silent_cpu = available, set(fail), silent_cpu
        self.created = []
        ort = self

        class SessionOptions:
            enable_mem_pattern = True
            execution_mode = None

        class Session:
            def __init__(self, data, sess_options=None, providers=()):
                ort.created.append((list(providers), sess_options))
                if providers and providers[0] in ort.fail:
                    raise RuntimeError("no device for " + providers[0])
                self._p = ["CPUExecutionProvider"] if ort.silent_cpu else list(providers)

            def get_providers(self):
                return self._p

            def get_inputs(self):
                return [types.SimpleNamespace(name="image")]

        self.SessionOptions = SessionOptions
        self.InferenceSession = Session
        self.ExecutionMode = types.SimpleNamespace(ORT_SEQUENTIAL="seq")

    def get_available_providers(self):
        return list(self.available)


def _fake_model(tmp_path):
    p = tmp_path / "m.onnx"
    p.write_bytes(b"model")
    return str(p)


@pytest.mark.parametrize("provider, device", [
    (accel.DML, "dml"), (accel.CUDA, "cuda"), (accel.CPU, "cpu")])
def test_device_names_what_the_session_really_runs_on(monkeypatch, tmp_path,
                                                      provider, device):
    ort = _FakeOrt([provider, accel.CPU])
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    dg = deep_onnx.OnnxDeepGaze(640, 480, [provider, accel.CPU],
                                model_file=_fake_model(tmp_path))
    assert dg.device == device
    if provider == accel.DML:
        # DirectML supports neither memory patterns nor parallel execution.
        so = ort.created[0][1]
        assert so.enable_mem_pattern is False and so.execution_mode == "seq"


def test_a_directml_session_that_fails_is_retried_on_the_cpu(
        monkeypatch, tmp_path, capsys):
    ort = _FakeOrt([accel.DML, accel.CPU], fail=[accel.DML])
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    dg = deep_onnx.OnnxDeepGaze(640, 480, [accel.DML, accel.CPU],
                                model_file=_fake_model(tmp_path))
    assert dg.device == "cpu"
    assert [c[0] for c in ort.created] == [[accel.DML, accel.CPU], [accel.CPU]]
    assert "CPU deneniyor" in capsys.readouterr().out


def test_a_session_that_silently_ends_on_the_cpu_says_cpu(monkeypatch, tmp_path):
    """onnxruntime-gpu without its CUDA libraries opens fine - on the CPU.
    .device must not claim the GPU (that decides the CPU warnings)."""
    ort = _FakeOrt([accel.CUDA, accel.CPU], silent_cpu=True)
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    dg = deep_onnx.OnnxDeepGaze(640, 480, [accel.CUDA, accel.CPU],
                                model_file=_fake_model(tmp_path))
    assert dg.device == "cpu"


def test_a_cpu_session_that_fails_raises(monkeypatch, tmp_path):
    ort = _FakeOrt([accel.CPU], fail=[accel.CPU])
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    with pytest.raises(RuntimeError):
        deep_onnx.OnnxDeepGaze(640, 480, [accel.CPU], model_file=_fake_model(tmp_path))
    assert len(ort.created) == 1


def test_unknown_deep_backend_is_said_once(monkeypatch, capsys):
    monkeypatch.setattr(deep, "_said_unknown", set())
    assert deep.normalize_backend("tensorrt") == "auto"
    assert deep.normalize_backend("tensorrt") == "auto"
    out = capsys.readouterr().out
    assert out.count("deep_backend: tensorrt") == 1 and out.isascii()
    assert deep.normalize_backend(" ONNX ") == "onnx"


def _broken_onnx(monkeypatch):
    class Broken:
        backend = "onnx"

        def __init__(self, w, h, providers):
            raise ValueError("eth-xgaze-resnet18.onnx is damaged")
    monkeypatch.setattr(deep_onnx, "OnnxDeepGaze", Broken)


def test_auto_falls_back_to_torch_cpu_when_the_onnx_model_is_damaged(
        backends, monkeypatch, capsys):
    state, built, _ = backends
    state["cuda"] = False           # auto picks ONNX
    _broken_onnx(monkeypatch)
    dg = deep.make_deep_gaze(1280, 960, "auto")
    assert dg.backend == "torch" and built == [("torch", "cpu")]
    assert "PyTorch ile CPU'da" in capsys.readouterr().out


def test_forced_onnx_does_not_fall_back(backends, monkeypatch):
    state, built, _ = backends
    _broken_onnx(monkeypatch)
    with pytest.raises(ValueError):
        deep.make_deep_gaze(1280, 960, "auto", backend="onnx")
    assert built == []


def test_driver_does_not_retry_a_broken_onnx_model_and_says_why(
        backends, monkeypatch, capsys):
    """OnnxDeepGaze already retries on the CPU; what gets out is the model
    file, and a second attempt would only repeat the same error."""
    state, built, install = backends
    state["torch"] = False
    install()
    tried = []

    class Broken:
        def __init__(self, w, h, providers):
            tried.append(providers)
            raise ValueError("eth-xgaze-resnet18.onnx is damaged")
    monkeypatch.setattr(deep_onnx, "OnnxDeepGaze", Broken)
    assert gd._make_deep({"deep_gaze": "auto"}, _Cap(), expected=True) is None
    out = capsys.readouterr().out
    assert len(tried) == 1
    assert "bozuk" in out and "CPU deneniyor" not in out


def test_driver_forced_onnx_without_the_model_says_so(backends, capsys):
    state, built, _ = backends
    state["onnx"] = False
    assert gd._make_deep({"deep_gaze": True, "deep_backend": "onnx"}, _Cap()) is None
    assert "deep_backend: onnx" in capsys.readouterr().out
