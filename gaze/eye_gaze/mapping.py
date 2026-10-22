# gaze/eye_gaze/mapping.py
"""Calibration mapping: gaze feature vector -> screen (px, py).

Affine (linear) least-squares fit over an arbitrary-length feature vector. The
feature vector is (gx, gy) for eye-only gaze, or (gx, gy, hx, hy) when head-pose
proxies are included so the fit can compensate for head movement. Affine keeps
the mapping stable (no wild extrapolation that flings the cursor to an edge).

Incoming features are clamped to the calibrated range expanded by `reach_margin`
so the user can reach the very edges/corners by looking slightly beyond
calibration. to_dict/from_dict let a good calibration be saved and reloaded.

fit_with_rejection() adds outlier rejection: calibration points corrupted by a
blink or a glance away are detected via fit residuals and dropped, then the
mapping is refitted on the clean points - standard practice in eye-tracking
research and a large real-world accuracy win.
"""
import math

import numpy as np


class GazeMapper:
    def __init__(self, reach_margin=0.0, poly2=False):
        self._reach = float(reach_margin)
        self._poly2 = bool(poly2)
        self._cx = None
        self._cy = None
        self._lo = None
        self._hi = None
        self._slo = None
        self._shi = None
        # (width, height) of the screen and of the camera image this was
        # calibrated on; None for files from before they were recorded.
        self.screen = None
        self.camera = None

    def _features(self, g):
        f = [1.0] + list(g)
        if self._poly2:
            # quadratic terms in the eye-gaze components: the iris-to-screen
            # relation is not perfectly affine, especially near the edges.
            gx, gy = f[1], f[2]
            f += [gx * gx, gy * gy, gx * gy]
        return f

    @property
    def n_inputs(self):
        """Length of the RAW gaze vector this mapper was fitted for (2
        eye-only, 4 with head pose) - not the expanded feature count;
        None before fit()."""
        return None if self._lo is None else len(self._lo)

    # Ridge strength (on standardised features). Keeps coefficients sane when
    # a feature barely varies during calibration (e.g. head position while the
    # user holds still) - plain least squares can then assign huge weights
    # that fling predictions thousands of px off screen in live use.
    _RIDGE = 1e-3

    @classmethod
    def _ridge_fit(cls, a, y):
        """Ridge regression with unpenalised intercept, solved on
        standardised features but returned as raw-feature coefficients so
        predict()/to_dict() stay unchanged. a includes the bias column 0."""
        f = a[:, 1:]
        mu = f.mean(axis=0)
        sd = f.std(axis=0)
        sd = np.where(sd < 1e-9, 1.0, sd)  # constant column -> weight becomes 0
        z = (f - mu) / sd
        ym = float(y.mean())
        n, m = z.shape
        gram = z.T @ z + cls._RIDGE * n * np.eye(m)
        w = np.linalg.solve(gram, z.T @ (y - ym))
        coefs = w / sd
        bias = ym - float(coefs @ mu)
        return np.concatenate(([bias], coefs))

    def fit(self, gaze_points, screen_points):
        if len(gaze_points) != len(screen_points):
            raise ValueError("gaze_points and screen_points length mismatch")
        if not gaze_points:
            raise ValueError("no calibration points")
        n_features = len(self._features(gaze_points[0]))
        if len(gaze_points) < n_features:
            raise ValueError("need at least %d calibration points" % n_features)
        a = np.array([self._features(g) for g in gaze_points], dtype=float)
        sx = np.array([p[0] for p in screen_points], dtype=float)
        sy = np.array([p[1] for p in screen_points], dtype=float)
        self._cx = self._ridge_fit(a, sx)
        self._cy = self._ridge_fit(a, sy)
        pts = np.array(gaze_points, dtype=float)
        self._lo = pts.min(axis=0)
        self._hi = pts.max(axis=0)
        # calibrated screen envelope: predictions are capped to +-30% beyond
        # it so a noisy fit can never fling the cursor thousands of px away
        self._slo = np.array([sx.min(), sy.min()])
        self._shi = np.array([sx.max(), sy.max()])

    def predict(self, gaze):
        if self._cx is None:
            raise RuntimeError("GazeMapper not fitted; call fit() first")
        rng = self._hi - self._lo
        lo = self._lo - self._reach * rng
        hi = self._hi + self._reach * rng
        g = np.minimum(np.maximum(np.array(gaze, dtype=float), lo), hi)
        f = np.array(self._features(tuple(g)), dtype=float)
        px, py = float(f @ self._cx), float(f @ self._cy)
        if getattr(self, "_slo", None) is not None:
            srng = self._shi - self._slo
            px = min(max(px, self._slo[0] - 0.3 * srng[0]), self._shi[0] + 0.3 * srng[0])
            py = min(max(py, self._slo[1] - 0.3 * srng[1]), self._shi[1] + 0.3 * srng[1])
        return px, py

    def to_dict(self):
        if self._cx is None:
            raise RuntimeError("GazeMapper not fitted; nothing to save")
        d = {"v": 2,  # fit-algorithm version (2 = ridge); loaders reject older
             "cx": self._cx.tolist(), "cy": self._cy.tolist(),
             "lo": self._lo.tolist(), "hi": self._hi.tolist(),
             "reach": self._reach, "poly2": self._poly2}
        if self._slo is not None:
            d["slo"] = self._slo.tolist()
            d["shi"] = self._shi.tolist()
        if self.screen:
            d["screen"] = [int(v) for v in self.screen]
        if self.camera:
            d["camera"] = [int(v) for v in self.camera]
        return d

    @classmethod
    def from_dict(cls, d):
        m = cls(reach_margin=d.get("reach", 0.0), poly2=d.get("poly2", False))
        m._cx = np.array(d["cx"], dtype=float)
        m._cy = np.array(d["cy"], dtype=float)
        m._lo = np.array(d["lo"], dtype=float)
        m._hi = np.array(d["hi"], dtype=float)
        if "slo" in d:
            m._slo = np.array(d["slo"], dtype=float)
            m._shi = np.array(d["shi"], dtype=float)
        if _pair(d.get("screen")):
            m.screen = tuple(int(v) for v in d["screen"])
        if _pair(d.get("camera")):
            m.camera = tuple(int(v) for v in d["camera"])
        return m

    def rescaled(self, sw, sh):
        """This calibration for a screen of sw x sh, or self when unchanged
        or unknown.

        The prediction is linear in the coefficients, and every calibration
        target is a FRACTION of the screen, so a different Windows scaling or
        resolution on the same monitor is an exact rescale - not a new
        calibration. (A different monitor is only approximately so.)
        """
        if self._cx is None or not self.screen:
            return self
        sw0, sh0 = self.screen
        if (int(sw0), int(sh0)) == (int(sw), int(sh)) or sw0 <= 0 or sh0 <= 0:
            return self
        kx, ky = float(sw) / sw0, float(sh) / sh0
        m = GazeMapper.from_dict(self.to_dict())
        m._cx = self._cx * kx
        m._cy = self._cy * ky
        if self._slo is not None:
            m._slo = self._slo * np.array([kx, ky])
            m._shi = self._shi * np.array([kx, ky])
        m.screen = (int(sw), int(sh))
        m.err_cv = getattr(self, "err_cv", None)
        return m


def _pair(v):
    return (isinstance(v, (list, tuple)) and len(v) == 2
            and all(isinstance(x, (int, float)) and x > 0 for x in v))


def fit_with_rejection(gaze_points, screen_points, reach_margin=0.0, poly2=False,
                       min_keep=6):
    """Fit, then drop gross outlier points (blink / glance-away during a
    calibration dot) and refit on the survivors.

    Returns (mapper, mean_error_px) or (None, None). poly2=True tries the
    quadratic fit first and falls back to affine when there are too few
    points for it."""
    # A single NaN sample would make every ridge coefficient NaN.
    pairs = [(g, s) for g, s in zip(gaze_points, screen_points)
             if all(math.isfinite(v) for v in tuple(g) + tuple(s))]
    gaze_points = [g for g, _ in pairs]
    screen_points = [s for _, s in pairs]

    def try_fit(g, s, p2):
        m = GazeMapper(reach_margin=reach_margin, poly2=p2)
        try:
            m.fit(g, s)
            return m
        except ValueError:
            return None

    mapper, used_p2 = None, False
    for p2 in ([True, False] if poly2 else [False]):
        mapper = try_fit(gaze_points, screen_points, p2)
        if mapper is not None:
            used_p2 = p2
            break
    if mapper is None:
        return None, None

    errs = [math.dist(mapper.predict(g), s)
            for g, s in zip(gaze_points, screen_points)]
    med = sorted(errs)[len(errs) // 2]
    threshold = max(2.5 * med, 60.0)  # px; generous so only gross outliers go
    keep = [i for i, e in enumerate(errs) if e <= threshold]
    if min_keep <= len(keep) < len(gaze_points):
        refit = try_fit([gaze_points[i] for i in keep],
                        [screen_points[i] for i in keep], used_p2)
        if refit is not None:
            mapper = refit
            errs = [math.dist(mapper.predict(gaze_points[i]), screen_points[i])
                    for i in keep]
    return mapper, sum(errs) / len(errs)


def holdout_error(gaze_points, screen_points, reach_margin=0.0, poly2=False,
                  blocks=None):
    """Mean error on points the fit did NOT see - the honest quality number.

    blocks=None: leave-one-out (dots: few, independent points).
    blocks=k: k contiguous blocks (pursuit: consecutive frames are near
    duplicates, so an interleaved split would grade each sample against its
    own neighbour and flatter just like the in-sample error does).
    Gross outliers (blinks, glances) are dropped from the score the same way
    fit_with_rejection drops them from the fit. None when it cannot be
    measured.
    """
    n = len(gaze_points)
    if n < 8:
        return None
    k = n if blocks is None else max(2, min(int(blocks), n))
    edges = [round(i * n / k) for i in range(k + 1)]
    errs = []
    for a, b in zip(edges, edges[1:]):
        if b <= a:
            continue
        tr_g = gaze_points[:a] + gaze_points[b:]
        tr_s = screen_points[:a] + screen_points[b:]
        m, _ = fit_with_rejection(list(tr_g), list(tr_s),
                                  reach_margin=reach_margin, poly2=poly2)
        if m is None:
            continue
        errs += [math.dist(m.predict(g), s)
                 for g, s in zip(gaze_points[a:b], screen_points[a:b])]
    errs = [e for e in errs if math.isfinite(e)]
    if not errs:
        return None
    med = sorted(errs)[len(errs) // 2]
    kept = [e for e in errs if e <= max(2.5 * med, 60.0)]
    return sum(kept) / len(kept)
