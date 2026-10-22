# gaze/eye_gaze/deep.py
"""Deep appearance-based gaze estimation (ETH-XGaze resnet18).

Produces a camera-frame gaze direction (x, y) from the full face appearance -
a much richer signal than iris geometry alone, especially on the vertical
axis. The output is APPENDED to the geometric feature vector and the ridge
calibration learns how much to trust each source.

Two backends give the same features (within 1e-3 rad, tests/test_deep_onnx.py),
so a calibration taken with one stays valid with the other:
  torch  DeepGaze, via ptgaze: torch (+CUDA for real-time), ptgaze,
         safetensors, huggingface_hub. The model (~45 MB) downloads once from
         the Hugging Face Hub. What a source checkout with KUR-DERIN.bat runs.
  onnx   deep_onnx.OnnxDeepGaze: onnxruntime + the bundled ONNX export -
         DirectML (any GPU brand) on Windows, CUDA or CPU on Linux. What the
         packages run: they contain no torch.
make_deep_gaze() picks one. VRAM use is well under 1 GB. On CPU it runs at
~5-15 fps - usable for testing, not recommended for daily use.

The MediaPipe face model is loaded from an ASCII-only path because
MediaPipe's C++ loader cannot open non-ASCII paths on Windows.
"""
import os
import pathlib
import shutil

from . import accel, paths

_ASCII_MODEL_DIR = paths.legacy_dir(r"C:\Users\Public\ptgaze-models")


def _ascii_mediapipe_model():
    """Return an ASCII-only path to face_landmarker.task, copying our
    existing model there if needed."""
    target = os.path.join(_ASCII_MODEL_DIR, "face_landmarker.task")
    if os.path.exists(target):
        return target
    os.makedirs(_ASCII_MODEL_DIR, exist_ok=True)
    from . import gaze as _g
    src = _g.ensure_model()
    shutil.copyfile(src, target)
    return target


CHECKPOINT_NAME = "eth-xgaze-resnet18.safetensors"
_HF_REPO, _HF_FILE = "hysts/ptgaze-eth-xgaze-resnet18", "model.safetensors"


def _hf_cached():
    from huggingface_hub import try_to_load_from_cache
    p = try_to_load_from_cache(_HF_REPO, _HF_FILE)
    return p if isinstance(p, str) and os.path.isfile(p) else None


def _hf_download():
    from huggingface_hub import hf_hub_download
    return hf_hub_download(_HF_REPO, _HF_FILE)


def ethxgaze_checkpoint(local_dir=_ASCII_MODEL_DIR, cached=_hf_cached,
                        download=_hf_download):
    """Path to the ETH-XGaze weights, touching the network only as a last
    resort.

    ptgaze's download_ethxgaze_model() asked the Hub on every start: a captive
    network stalled startup ~14 s, and a wiped HF cache meant no deep model,
    the 11-feature calibration rejected and a forced recalibration. A copy
    beside the ASCII MediaPipe model is used first; a Hub-cache hit is copied
    there; only then is anything downloaded. A failed copy is not fatal.
    """
    local = os.path.join(local_dir, CHECKPOINT_NAME)
    if os.path.isfile(local):
        return local
    src = None
    try:
        src = cached()
    except Exception:
        src = None
    if not src:
        src = download()
    src = str(src)
    try:
        os.makedirs(local_dir, exist_ok=True)
        shutil.copyfile(src, local + ".tmp")
        os.replace(local + ".tmp", local)
        return local
    except Exception:
        return src


class DeepGaze:
    """estimate(frame_bgr) -> (gaze_x, gaze_y) camera-frame direction, or
    None when no face is found."""

    def __init__(self, frame_width, frame_height, device=None):
        import torch
        import ptgaze
        from omegaconf import OmegaConf
        from ptgaze.gaze_estimator import GazeEstimator as _PtGazeEstimator

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.backend = "torch"

        pkg = pathlib.Path(ptgaze.__file__).parent
        cfg = OmegaConf.load(pkg / "data" / "configs" / "eth-xgaze.yaml")
        OmegaConf.set_struct(cfg, False)
        cfg.PACKAGE_ROOT = pkg.resolve().as_posix()
        cfg.device = device
        cfg.face_detector.mediapipe_model_path = _ascii_mediapipe_model()
        cfg.face_detector.mediapipe_max_num_faces = 1
        cfg.gaze_estimator.checkpoint = pathlib.Path(
            ethxgaze_checkpoint()).as_posix()
        cfg.gaze_estimator.use_dummy_camera_params = True
        cfg.gaze_estimator.dummy_camera_size = [int(frame_width), int(frame_height)]
        self._est = _PtGazeEstimator(cfg)

    def estimate(self, frame_bgr):
        faces = self._est.detect_faces(frame_bgr)
        if not faces:
            return None
        face = faces[0]
        self._est.estimate_gaze(frame_bgr, face)
        v = face.gaze_vector
        return (float(v[0]), float(v[1]))

    def estimate_from_landmarks(self, frame_bgr, landmarks):
        """Fast path: reuse MediaPipe landmarks already computed by the
        geometric estimator instead of running face detection a second time
        (roughly halves the per-frame cost of the deep model)."""
        import numpy as np
        from ptgaze.common import Face
        from ptgaze.head_pose_estimation.face_landmark_estimator import LandmarkEstimator
        n = getattr(LandmarkEstimator, "_MEDIAPIPE_NUM_LANDMARKS", 468)
        h, w = frame_bgr.shape[:2]
        pts = np.array([(p.x * w, p.y * h) for p in landmarks[:n]], dtype=np.float64)
        bbox = np.round(np.vstack([pts.min(axis=0), pts.max(axis=0)])).astype(np.int32)
        face = Face(bbox, pts)
        self._est.estimate_gaze(frame_bgr, face)
        v = face.gaze_vector
        return (float(v[0]), float(v[1]))

    def close(self):
        try:
            self._est.close()
        except Exception:
            pass


def _torch():
    """torch, or None when it is missing or broken."""
    try:
        import torch
        return torch
    except Exception:
        return None


def _has_ptgaze():
    try:
        import ptgaze  # noqa: F401
        return True
    except Exception:
        return False


def _onnx_ready():
    """onnxruntime importable and the ONNX model in this build."""
    try:
        import onnxruntime  # noqa: F401
    except Exception:
        return False
    from . import deep_onnx
    return deep_onnx.model_path() is not None


_BACKENDS = ("auto", "torch", "onnx")
_said_unknown = set()


def normalize_backend(backend):
    """"auto" | "torch" | "onnx". Anything else is "auto" - said once, not
    silently: a typo would otherwise look like the setting being ignored."""
    b = str(backend if backend is not None else "auto").strip().lower()
    if b in _BACKENDS:
        return b
    if b not in _said_unknown:
        _said_unknown.add(b)
        print("UYARI: deep_backend: %s taninmiyor (auto | torch | onnx);"
              " auto kullaniliyor." % ascii(b)[1:-1])
    return "auto"


def select_backend(backend="auto"):
    """(backend, gpu): which deep model make_deep_gaze() would build -
    "torch", "onnx" or None (nothing can run it) - and whether a GPU can run
    it (the caller's "is the deep model worth it" question).

    "auto": torch + ptgaze + CUDA keeps the torch path - calibrations
    taken with it stay valid; otherwise ONNX; torch on the CPU only when there is no
    ONNX model either (a source checkout that never built one).
    """
    backend = normalize_backend(backend)
    if backend == "onnx":
        return ("onnx" if _onnx_ready() else None), _onnx_gpu()
    torch = _torch()
    if backend == "torch":
        ok = torch is not None and _has_ptgaze()
        return ("torch" if ok else None), bool(ok and _cuda(torch))
    if torch is not None and _cuda(torch) and _has_ptgaze():
        return "torch", True
    if _onnx_ready():
        return "onnx", _onnx_gpu()
    if torch is not None and _has_ptgaze():
        return "torch", False
    return None, False


def _cuda(torch):
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _onnx_gpu():
    return accel.usable(accel.providers("auto"))[0] != accel.CPU


def make_deep_gaze(frame_w, frame_h, device_req="auto", backend="auto"):
    """The deep gaze model for device_req "auto" | "gpu" | "cpu" ("cuda" =
    "gpu"); raises when nothing can run it or the load fails. .device tells
    where it really runs ("cuda" | "dml" | "cpu"), .backend which one it is.

    backend "auto" that picked ONNX falls back to torch on the CPU, once and
    said, when the ONNX model will not open (damaged file) but torch + ptgaze
    are there: same features, so the calibration stays usable.
    """
    kind, _ = select_backend(backend)
    if kind is None:
        raise RuntimeError("derin model calistiricisi yok (onnxruntime + model"
                           " ya da torch + ptgaze gerekir)")
    want = accel.normalize_want(device_req)
    if kind == "torch":
        if want == "cpu":
            device = "cpu"
        elif want == "gpu":
            device = "cuda"     # as deep_device: cuda always did: no silent CPU
        else:
            device = "cuda" if _cuda(_torch()) else "cpu"
        return DeepGaze(frame_w, frame_h, device=device)
    from . import deep_onnx
    try:
        return deep_onnx.OnnxDeepGaze(frame_w, frame_h, accel.providers(want))
    except Exception as exc:
        if normalize_backend(backend) != "auto" or                 _torch() is None or not _has_ptgaze():
            raise
        print("UYARI: ONNX derin modeli acilamadi (%s); PyTorch ile CPU'da"
              " aciliyor." % ascii(exc)[:100])
    return DeepGaze(frame_w, frame_h, device="cpu")
