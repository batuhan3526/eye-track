# gaze/eye_gaze/gaze.py
"""Webcam gaze estimation via MediaPipe Tasks FaceLandmarker (478-point model,
includes iris 468-477).

Produces a gaze feature: (gx, gy) eye-only, or (gx, gy, hx, hy) with head-pose
proxies so the calibration mapping can compensate for head movement (important
when the user has no neck control). estimate() also updates `self.openness`
(eye-openness ratio, measured in camera pixels) for blink detection. mediapipe
and cv2 are imported lazily so the pure helpers and unit tests run without the
heavy deps installed.
"""
import math
import os
import urllib.request

from . import paths

# FaceLandmarker 478-point indices (iris 468-477).
# NOTE: _LEFT/_RIGHT are subject-relative groupings; MediaPipe's own convention
# labels the 468-472 group as the anatomical right eye. Each triple below
# (iris + corners + lids) belongs to one consistent eye, and both eyes' ratios
# are averaged, so the naming choice does not affect the output.
_LEFT_IRIS = [468, 469, 470, 471, 472]
_RIGHT_IRIS = [473, 474, 475, 476, 477]
_LEFT_CORNERS = (33, 133)     # outer, inner
_RIGHT_CORNERS = (362, 263)
_LEFT_LIDS = (159, 145)       # upper, lower
_RIGHT_LIDS = (386, 374)

_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
              "face_landmarker/float16/1/face_landmarker.task")
# In source mode model_dir() is gaze/models - the file this checkout has
# always used. Frozen, it is a writable portable/per-user folder, never the
# temporary _MEIPASS.
_MODEL_PATH = os.path.join(paths.model_dir(), "face_landmarker.task")


def ensure_model(path=None):
    """Return a path to face_landmarker.task: the copy shipped in the package,
    else the downloaded one, downloading it once if missing."""
    if path is None:
        bundled = paths.bundled_model("face_landmarker.task")
        if bundled:
            return bundled
    target = path or _MODEL_PATH
    if not os.path.exists(target):
        # ensure_dir, not makedirs: an unwritable model dir falls back
        # (said once) instead of failing the camera start.
        target = os.path.join(paths.ensure_dir(os.path.dirname(target)),
                              os.path.basename(target))
        urllib.request.urlretrieve(_MODEL_URL, target)
    return target


def iris_ratio(iris_pt, corner1, corner2):
    """Position of iris_pt projected onto the corner1->corner2 axis, in [0,1].
    Returns 0.5 when the two corners coincide (degenerate eye). Because it is a
    ratio *within* the eye, it is largely invariant to head translation."""
    ax, ay = corner1
    bx, by = corner2
    vx, vy = (bx - ax), (by - ay)
    denom = vx * vx + vy * vy
    if denom == 0:
        return 0.5
    px, py = iris_pt
    t = ((px - ax) * vx + (py - ay) * vy) / denom
    return max(0.0, min(1.0, t))


def head_proxy_from_matrix(matrix):
    """Two head-pose proxies from a 4x4 facial transformation matrix: the x and
    y components of the rotated forward axis (rotation column 2). The exact
    sign/convention is irrelevant — these are just consistent, head-pose-
    correlated regression inputs that let the mapping compensate for head turns."""
    return (float(matrix[0][2]), float(matrix[1][2]))


def head_translation_proxies(nose, corner_left, corner_right):
    """Head-translation and distance proxies: nose position in the frame plus
    the inter-ocular width (shrinks as the user moves away from the camera).
    Iris *ratios* are invariant to head translation, but the gaze->screen
    geometry is not - feeding these to the calibration regression lets it
    compensate for involuntary sliding/bobbing of the head."""
    return (float(nose[0]), float(nose[1]),
            float(math.dist(corner_left, corner_right)))


def eye_openness(upper, lower, corner1, corner2):
    """Eye-openness ratio: vertical lid gap / horizontal eye width. Low when the
    eye is closed. Normalised by width so it is scale/distance invariant."""
    w = math.dist(corner1, corner2)
    if w <= 0:
        return 0.0
    return math.dist(upper, lower) / w


def landmark_openness(lm, width, height):
    """Both eyes' openness (mean) with the landmarks in camera PIXELS.

    MediaPipe's coordinates are x / width and y / height, so a ratio taken on
    them grows with the frame's width / height: the same eye read 33% more
    open on 16:9 than on 4:3, and one fixed threshold meant something else on
    every camera mode. Only openness moves to pixels; the regression features
    keep the normalised coordinates their calibrations were fitted on."""
    def px(i):
        return (lm[i].x * width, lm[i].y * height)
    op_l = eye_openness(px(_LEFT_LIDS[0]), px(_LEFT_LIDS[1]),
                        px(_LEFT_CORNERS[0]), px(_LEFT_CORNERS[1]))
    op_r = eye_openness(px(_RIGHT_LIDS[0]), px(_RIGHT_LIDS[1]),
                        px(_RIGHT_CORNERS[0]), px(_RIGHT_CORNERS[1]))
    return (op_l + op_r) / 2.0


def _mean(points):
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


class GazeEstimator:
    """Wraps MediaPipe Tasks FaceLandmarker.
    estimate(frame_bgr) -> (gx, gy) or (gx, gy, hx, hy) or None.
    After each call with a face, `self.openness` holds the eye-openness ratio
    (in pixels: the same on any camera aspect; eye_gaze.openness)."""

    def __init__(self, refine=True, model_path=None, use_head_pose=True,
                 head_translation=True, per_eye=False):
        # `refine` kept for backward compatibility; the 478-point task model
        # already includes the iris landmarks, so it is implicit now.
        import mediapipe as mp  # lazy
        from mediapipe.tasks import python  # lazy
        from mediapipe.tasks.python import vision  # lazy
        self._mp = mp
        self._use_head_pose = use_head_pose
        self._head_translation = head_translation
        self._per_eye = per_eye
        # raw landmark list of the last frame with a face (for downstream
        # consumers like the deep gaze model, to avoid a second detection)
        self.landmarks = None
        self.openness = 1.0
        model = ensure_model(model_path)
        # Read the model in Python (Unicode-safe) and pass it as a buffer.
        # MediaPipe's C++ loader cannot open non-ASCII paths on Windows
        # (e.g. a user folder like "Örnek"), so model_asset_path would fail.
        with open(model, "rb") as f:
            model_bytes = f.read()
        options = vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_buffer=model_bytes),
            running_mode=vision.RunningMode.IMAGE,
            num_faces=1,
            output_facial_transformation_matrixes=use_head_pose,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)

    def estimate(self, frame):
        import cv2  # lazy
        mp = self._mp
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect(image)
        if not result.face_landmarks:
            self.landmarks = None
            return None
        lm = result.face_landmarks[0]
        self.landmarks = lm

        def pt(i):
            return (lm[i].x, lm[i].y)

        gx_l = iris_ratio(_mean([pt(i) for i in _LEFT_IRIS]), pt(_LEFT_CORNERS[0]), pt(_LEFT_CORNERS[1]))
        gx_r = iris_ratio(_mean([pt(i) for i in _RIGHT_IRIS]), pt(_RIGHT_CORNERS[0]), pt(_RIGHT_CORNERS[1]))
        gy_l = iris_ratio(_mean([pt(i) for i in _LEFT_IRIS]), pt(_LEFT_LIDS[0]), pt(_LEFT_LIDS[1]))
        gy_r = iris_ratio(_mean([pt(i) for i in _RIGHT_IRIS]), pt(_RIGHT_LIDS[0]), pt(_RIGHT_LIDS[1]))
        gx = (gx_l + gx_r) / 2.0
        gy = (gy_l + gy_r) / 2.0
        feats = [gx, gy]
        if self._per_eye:
            # left/right eye DIFFERENCES: lets the calibration weight an
            # asymmetric pair (ptosis, one weaker eye) instead of trusting a
            # blind average. Mean stays first so poly2 terms keep meaning.
            feats += [gx_l - gx_r, gy_l - gy_r]

        self.openness = landmark_openness(lm, frame.shape[1], frame.shape[0])

        if not self._use_head_pose:
            return tuple(feats)
        mats = getattr(result, "facial_transformation_matrixes", None)
        if mats:
            hx, hy = head_proxy_from_matrix(mats[0])
        else:
            hx, hy = 0.0, 0.0
        feats += [hx, hy]
        if self._head_translation:
            # nose tip = landmark 1; widest eye corners 33 (left) / 263 (right)
            nx, ny, s = head_translation_proxies(pt(1), pt(33), pt(263))
            feats += [nx, ny, s]
        return tuple(feats)

    def close(self):
        self._landmarker.close()
