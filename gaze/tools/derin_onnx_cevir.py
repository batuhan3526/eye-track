# gaze/tools/derin_onnx_cevir.py
"""Build resources/models/eth-xgaze-resnet18.onnx from the ETH-XGaze weights.

    CUDA_VISIBLE_DEVICES="" python tools/derin_onnx_cevir.py [--girdi W] [--cikti O]

The packaged program has no torch: its deep gaze model is this ONNX export of
the same resnet18 weights ptgaze loads (hysts/ptgaze-eth-xgaze-resnet18 on the
Hugging Face Hub). Run at build time, on the CPU only - the GPU may be busy
with someone else's work, and the export does not need it.

Input, first found: --girdi, the copy beside the ASCII MediaPipe model
(deep.ethxgaze_checkpoint's folder), the Hub cache, a Hub download. Nothing is
written anywhere except --cikti and its .sha256.

Before writing, torch CPU and onnxruntime CPU are compared on 20 synthetic
inputs and on the normalised faces of tests/data/faces/*.jpg; the file is only
written if they agree within 1e-4 rad. The export is deterministic: the same
weights and torch version give the same bytes (and .sha256).

Build-only dependencies: torch (CPU is enough), timm, safetensors, onnx (the
exporter imports it), onnxruntime, numpy, opencv, mediapipe (for the faces).
"""
import argparse
import glob
import hashlib
import io
import os
import sys

# Before torch is imported: this tool never touches a GPU.
os.environ["CUDA_VISIBLE_DEVICES"] = ""

_GAZE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GAZE_DIR not in sys.path:
    sys.path.insert(0, _GAZE_DIR)

from eye_gaze import deep, deep_onnx  # noqa: E402

DEFAULT_OUT = os.path.join(_GAZE_DIR, "resources", "models", deep_onnx.MODEL_NAME)
FACES_DIR = os.path.join(_GAZE_DIR, "tests", "data", "faces")
OPSET = 17
TOLERANCE = 1e-4        # rad, torch CPU vs onnxruntime CPU
N_SYNTHETIC = 20


def find_weights(given=None):
    """Path of the safetensors weights; only reads, never copies."""
    if given:
        return given
    local = os.path.join(deep._ASCII_MODEL_DIR, deep.CHECKPOINT_NAME)
    if os.path.isfile(local):
        return local
    try:
        cached = deep._hf_cached()
    except Exception:
        cached = None
    return cached or str(deep._hf_download())


def build_torch_model(weights):
    """ptgaze's ETH-XGaze network: timm resnet18 with a 2-way head."""
    import timm
    from safetensors.torch import load_file
    model = timm.create_model("resnet18", num_classes=2, pretrained=False)
    model.load_state_dict(load_file(weights))
    model.eval()
    return model


def export_onnx(model):
    import torch
    x = torch.zeros(1, 3, deep_onnx.INPUT_SIZE, deep_onnx.INPUT_SIZE)
    buf = io.BytesIO()
    kw = dict(opset_version=OPSET, input_names=["image"], output_names=["gaze"],
              do_constant_folding=True)
    with torch.no_grad():
        try:
            torch.onnx.export(model, x, buf, dynamo=False, **kw)
        except TypeError:       # torch < 2.5 has no dynamo= and no other exporter
            torch.onnx.export(model, x, buf, **kw)
    return buf.getvalue()


def detect_faces(faces_dir=FACES_DIR):
    """[(file name, BGR image, FaceLandmarker landmarks)] for the face images
    MediaPipe finds a face in (said for each one it does not)."""
    import cv2
    import numpy as np
    files = sorted(glob.glob(os.path.join(glob.escape(faces_dir), "*.jpg")))
    if not files:
        return []
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    from eye_gaze import gaze
    with open(gaze.ensure_model(), "rb") as f:
        model = f.read()
    lm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_buffer=model),
        running_mode=vision.RunningMode.IMAGE, num_faces=1))
    out = []
    try:
        for p in files:
            img = cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)
            res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                     data=cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))
            if not res.face_landmarks:
                print("UYARI: yuz bulunamadi: %s" % ascii(os.path.basename(p)))
                continue
            out.append((os.path.basename(p), img, res.face_landmarks[0]))
    finally:
        lm.close()
    return out


def face_inputs(faces):
    """(name, model input) for each detected face, normalised exactly as
    OnnxDeepGaze does at run time."""
    out = []
    for name, img, landmarks in faces:
        h, w = img.shape[:2]
        cam = deep_onnx.dummy_camera_matrix(w, h)
        pts = deep_onnx.landmarks_to_pixels(landmarks, w, h)
        rot, _, model3d = deep_onnx.estimate_head_pose(pts, cam)
        patch, _ = deep_onnx.normalize_face(img, cam, rot,
                                            deep_onnx.face_center(model3d))
        out.append((name, deep_onnx.to_input(patch)))
    return out


def write_reference(weights, faces, path):
    """tests/data/faces/reference.json: each face's landmarks (rounded to 6
    decimals) and what torch + ptgaze on the CPU answers for exactly those
    landmarks - so the ONNX path can be checked where torch is not installed.
    Needs ptgaze."""
    import json
    import types
    deep.ethxgaze_checkpoint = lambda: weights      # these weights, no copy
    ref = {}
    for name, img, landmarks in faces:
        h, w = img.shape[:2]
        pts = [[round(p.x, 6), round(p.y, 6)]
               for p in landmarks[:deep_onnx.NUM_LANDMARKS]]
        dg = deep.DeepGaze(w, h, device="cpu")
        try:
            g = dg.estimate_from_landmarks(
                img, [types.SimpleNamespace(x=x, y=y) for x, y in pts])
        finally:
            dg.close()
        ref[name] = {"size": [w, h], "torch_cpu": [g[0], g[1]], "landmarks": pts}
        print("  %-12s torch CPU %.6f %.6f" % (name, g[0], g[1]))
    write_atomic(path, json.dumps({"faces": ref}, separators=(",", ":"))
                 .encode("ascii"))
    print("Yazildi: %s" % ascii(path))


def max_difference(model, onnx_bytes, faces):
    """Largest |torch - onnxruntime| over all inputs, in rad."""
    import numpy as np
    import onnxruntime as ort
    import torch
    sess = ort.InferenceSession(onnx_bytes, providers=["CPUExecutionProvider"])
    rng = np.random.default_rng(0)
    size = deep_onnx.INPUT_SIZE
    cases = [("sentetik-%02d" % i,
              rng.standard_normal((1, 3, size, size)).astype(np.float32))
             for i in range(N_SYNTHETIC)] + list(faces)
    worst = 0.0
    with torch.no_grad():
        for name, x in cases:
            ref = model(torch.from_numpy(x)).numpy()
            got = sess.run(None, {"image": x})[0]
            d = float(np.abs(ref - got).max())
            worst = max(worst, d)
            if not name.startswith("sentetik"):
                print("  %-12s torch %s  onnx %s  fark %.2e"
                      % (name, np.round(ref[0], 5), np.round(got[0], 5), d))
    return worst, len(cases)


def write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--girdi", help="model.safetensors (ETH-XGaze resnet18)")
    ap.add_argument("--cikti", default=DEFAULT_OUT, help="ONNX output path")
    ap.add_argument("--yuzler", default=FACES_DIR, help="folder of face .jpg")
    ap.add_argument("--referans", action="store_true",
                    help="only (re)write <yuzler>/reference.json (needs ptgaze)")
    args = ap.parse_args(argv)

    if not args.referans:
        try:
            import onnx  # noqa: F401  (torch's exporter imports it)
        except ImportError:
            print("HATA: 'onnx' paketi yok; torch'un ONNX disa aktaricisi onu"
                  " kullanir. Bir derleme venv'ine kurun (ortak sistem"
                  " Python'una degil): pip install onnx")
            return 2
    weights = find_weights(args.girdi)
    print("Agirliklar: %s" % ascii(weights))
    detected = detect_faces(args.yuzler)
    if args.referans:
        write_reference(weights, detected,
                        os.path.join(args.yuzler, "reference.json"))
        return 0
    model = build_torch_model(weights)
    data = export_onnx(model)
    faces = face_inputs(detected)
    worst, n = max_difference(model, data, faces)
    print("torch CPU <-> onnxruntime CPU: %d girdi (%d yuz), max fark %.2e rad"
          % (n, len(faces), worst))
    if not worst < TOLERANCE:
        print("HATA: fark %.0e rad sinirini asiyor; dosya YAZILMADI." % TOLERANCE)
        return 1
    digest = hashlib.sha256(data).hexdigest()
    write_atomic(args.cikti, data)
    write_atomic(args.cikti + ".sha256",
                 ("%s  %s\n" % (digest, os.path.basename(args.cikti))).encode("ascii"))
    print("Yazildi: %s (%d bayt, opset %d)" % (ascii(args.cikti), len(data), OPSET))
    print("sha256: %s" % digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
