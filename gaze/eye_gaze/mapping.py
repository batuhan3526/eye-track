# gaze/eye_gaze/mapping.py
"""Calibration mapping: gaze feature vector -> screen (px, py).

Affine (linear) least-squares fit over an arbitrary-length feature vector. The
feature vector is (gx, gy) for eye-only gaze, or (gx, gy, hx, hy) when head-pose
proxies are included so the fit can compensate for head movement. Affine keeps
the mapping stable (no wild extrapolation that flings the cursor to an edge).

Each screen axis may instead use its own subset of the inputs, its own
quadratic pair and its own ridge strength (`axes`), as chosen per person by
eye_gaze.modelsel. A file without them is read and predicts exactly as before.

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


def _axis_spec(spec):
    """(inputs, quad, lam) normalised: inputs a tuple of input indices, quad
    None or a pair of them, lam the ridge strength."""
    inputs, quad, lam = spec
    inputs = tuple(int(i) for i in inputs)
    quad = None if quad is None else (int(quad[0]), int(quad[1]))
    lam = float(lam)
    if not inputs or min(inputs) < 0 or lam < 0 or not math.isfinite(lam) \
            or (quad is not None and min(quad) < 0):
        raise ValueError("bad axis spec")
    return inputs, quad, lam


def axis_row(g, spec):
    """Feature row (bias first) of one axis for the raw gaze vector g."""
    inputs, quad, _ = spec
    f = [1.0] + [g[i] for i in inputs]
    if quad is not None:
        a, b = g[quad[0]], g[quad[1]]
        f += [a * a, b * b, a * b]
    return f


def axis_design(g, spec):
    """axis_row for every row of the (N, n) array g, as an (N, m) array."""
    inputs, quad, _ = spec
    g = np.asarray(g, dtype=float)
    cols = [np.ones(len(g))] + [g[:, i] for i in inputs]
    if quad is not None:
        a, b = g[:, quad[0]], g[:, quad[1]]
        cols += [a * a, b * b, a * b]
    return np.column_stack(cols)


def ridge(a, y, lam):
    """Ridge regression with unpenalised intercept, solved on standardised
    features but returned as raw-feature coefficients. a includes the bias
    column 0."""
    return ridge_path(a, y, (lam,))[0]


def ridge_path(a, y, lams):
    """ridge() for several strengths, sharing the standardisation."""
    f = a[:, 1:]
    mu = f.mean(axis=0)
    sd = f.std(axis=0)
    sd = np.where(sd < 1e-9, 1.0, sd)  # constant column -> weight becomes 0
    z = (f - mu) / sd
    ym = float(y.mean())
    n, m = z.shape
    zz, zy = z.T @ z, z.T @ (y - ym)
    out = []
    for lam in lams:
        w = np.linalg.solve(zz + lam * n * np.eye(m), zy)
        coefs = w / sd
        bias = ym - float(coefs @ mu)
        out.append(np.concatenate(([bias], coefs)))
    return out


class GazeMapper:
    def __init__(self, reach_margin=0.0, poly2=False, axes=None):
        self._reach = float(reach_margin)
        self._poly2 = bool(poly2)
        # ((inputs, quad, lam) for x, the same for y), or None: every input,
        # quadratic eye terms when poly2, the class ridge strength.
        self.axes = None if axes is None else tuple(_axis_spec(a) for a in axes)
        # What eye_gaze.modelsel measured when it chose `axes` (a JSON-safe
        # dict), or None.
        self.selection = None
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
        # The person's open-eye openness (pixel scale, eye_gaze.openness)
        # measured on this calibration's frames; None for older files.
        self.open_eye = None

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
        """ridge() at the class strength."""
        return ridge(a, y, cls._RIDGE)

    def fit(self, gaze_points, screen_points):
        if len(gaze_points) != len(screen_points):
            raise ValueError("gaze_points and screen_points length mismatch")
        if not gaze_points:
            raise ValueError("no calibration points")
        sx = np.array([p[0] for p in screen_points], dtype=float)
        sy = np.array([p[1] for p in screen_points], dtype=float)
        if self.axes is None:
            n_features = len(self._features(gaze_points[0]))
            if len(gaze_points) < n_features:
                raise ValueError("need at least %d calibration points" % n_features)
            a = np.array([self._features(g) for g in gaze_points], dtype=float)
            self._cx = self._ridge_fit(a, sx)
            self._cy = self._ridge_fit(a, sy)
        else:
            pts = np.array(gaze_points, dtype=float)
            for spec in self.axes:
                if max(spec[0] + (spec[1] or ())) >= pts.shape[1]:
                    raise ValueError("an axis uses an input the points lack")
                n_features = len(axis_row(pts[0], spec))
                if len(pts) < n_features:
                    raise ValueError("need at least %d calibration points"
                                     % n_features)
            spx, spy = self.axes
            self._cx = ridge(axis_design(pts, spx), sx, spx[2])
            self._cy = ridge(axis_design(pts, spy), sy, spy[2])
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
        if self.axes is None:
            f = np.array(self._features(tuple(g)), dtype=float)
            px, py = float(f @ self._cx), float(f @ self._cy)
        else:
            px = float(np.array(axis_row(g, self.axes[0])) @ self._cx)
            py = float(np.array(axis_row(g, self.axes[1])) @ self._cy)
        if getattr(self, "_slo", None) is not None:
            srng = self._shi - self._slo
            px = min(max(px, self._slo[0] - 0.3 * srng[0]), self._shi[0] + 0.3 * srng[0])
            py = min(max(py, self._slo[1] - 0.3 * srng[1]), self._shi[1] + 0.3 * srng[1])
        return px, py

    def predict_many(self, gaze):
        """predict() for every row of the (N, n) array `gaze`, as (N, 2):
        the same clamp, model and cap, at numpy speed (model selection
        validates thousands of held-out frames)."""
        if self._cx is None:
            raise RuntimeError("GazeMapper not fitted; call fit() first")
        g = np.asarray(gaze, dtype=float).reshape(len(gaze), -1)
        rng = self._hi - self._lo
        g = np.minimum(np.maximum(g, self._lo - self._reach * rng),
                       self._hi + self._reach * rng)
        if self.axes is None:
            spec = (tuple(range(g.shape[1])), (0, 1) if self._poly2 else None, 0)
            a = axis_design(g, spec)
            px, py = a @ self._cx, a @ self._cy
        else:
            px = axis_design(g, self.axes[0]) @ self._cx
            py = axis_design(g, self.axes[1]) @ self._cy
        if getattr(self, "_slo", None) is not None:
            srng = self._shi - self._slo
            px = np.clip(px, self._slo[0] - 0.3 * srng[0], self._shi[0] + 0.3 * srng[0])
            py = np.clip(py, self._slo[1] - 0.3 * srng[1], self._shi[1] + 0.3 * srng[1])
        return np.column_stack([px, py])

    def to_dict(self):
        if self._cx is None:
            raise RuntimeError("GazeMapper not fitted; nothing to save")
        # Format version: 2 = ridge, one model over every input; 3 = per-axis
        # models (`axes`). A 3 must never reach a loader that only knows 2:
        # 1.2.0 would ignore `axes`, predict with the wrong feature vector
        # and stop the cursor loop - it rejects the 3 and restores its own
        # backup instead.
        d = {"v": 3 if self.axes is not None else 2,
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
        if self.open_eye is not None:
            d["open_eye"] = float(self.open_eye)
        if self.axes is not None:
            d["axes"] = [{"in": list(inp), "quad": None if q is None else list(q),
                          "lam": lam} for inp, q, lam in self.axes]
        if isinstance(self.selection, dict):
            d["sel"] = self.selection
        return d

    @classmethod
    def from_dict(cls, d):
        axes = d.get("axes")
        if axes is not None:
            axes = [(a["in"], a.get("quad"), a["lam"]) for a in axes]
            if len(axes) != 2:
                raise ValueError("axes must describe x and y")
        m = cls(reach_margin=d.get("reach", 0.0), poly2=d.get("poly2", False),
                axes=axes)
        m._cx = np.array(d["cx"], dtype=float)
        m._cy = np.array(d["cy"], dtype=float)
        m._lo = np.array(d["lo"], dtype=float)
        m._hi = np.array(d["hi"], dtype=float)
        if m.axes is not None:
            # A hand-edited or truncated file must fail HERE (the loader then
            # falls back to a backup), not at the first predict() in the
            # cursor loop.
            for spec, c in zip(m.axes, (m._cx, m._cy)):
                if max(spec[0] + (spec[1] or ())) >= len(m._lo) \
                        or len(c) != len(axis_row(m._lo, spec)):
                    raise ValueError("axes do not match the coefficients")
        if isinstance(d.get("sel"), dict):
            m.selection = d["sel"]
        if "slo" in d:
            m._slo = np.array(d["slo"], dtype=float)
            m._shi = np.array(d["shi"], dtype=float)
        if _pair(d.get("screen")):
            m.screen = tuple(int(v) for v in d["screen"])
        if _pair(d.get("camera")):
            m.camera = tuple(int(v) for v in d["camera"])
        v = d.get("open_eye")
        if (isinstance(v, (int, float)) and not isinstance(v, bool)
                and math.isfinite(v) and v > 0):
            m.open_eye = float(v)
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
        m.selection = self.selection
        return m


def _pair(v):
    return (isinstance(v, (list, tuple)) and len(v) == 2
            and all(isinstance(x, (int, float)) and x > 0 for x in v))


def fit_with_rejection(gaze_points, screen_points, reach_margin=0.0, poly2=False,
                       min_keep=6, axes=None):
    """Fit, then drop gross outlier points (blink / glance-away during a
    calibration dot) and refit on the survivors.

    Returns (mapper, mean_error_px) or (None, None). poly2=True tries the
    quadratic fit first and falls back to affine when there are too few
    points for it. `axes` fits that per-axis model instead (no fallback)."""
    # A single NaN sample would make every ridge coefficient NaN.
    if isinstance(gaze_points, np.ndarray) and isinstance(screen_points, np.ndarray):
        ok = np.isfinite(gaze_points).all(axis=1) & np.isfinite(screen_points).all(axis=1)
        gaze_points, screen_points = list(gaze_points[ok]), list(screen_points[ok])
    else:
        pairs = [(g, s) for g, s in zip(gaze_points, screen_points)
                 if all(math.isfinite(v) for v in tuple(g) + tuple(s))]
        gaze_points = [g for g, _ in pairs]
        screen_points = [s for _, s in pairs]

    def try_fit(g, s, p2):
        m = GazeMapper(reach_margin=reach_margin, poly2=p2, axes=axes)
        try:
            m.fit(g, s)
            return m
        except ValueError:
            return None

    mapper, used_p2 = None, False
    for p2 in ([True, False] if (poly2 and axes is None) else [False]):
        mapper = try_fit(gaze_points, screen_points, p2)
        if mapper is not None:
            used_p2 = p2
            break
    if mapper is None:
        return None, None

    def dists(m, g, s):
        if axes is None:        # unchanged for the single model of old
            return [math.dist(m.predict(a), b) for a, b in zip(g, s)]
        d = m.predict_many(g) - np.asarray(s, dtype=float)
        return np.hypot(d[:, 0], d[:, 1]).tolist()

    errs = dists(mapper, gaze_points, screen_points)
    med = sorted(errs)[len(errs) // 2]
    threshold = max(2.5 * med, 60.0)  # px; generous so only gross outliers go
    keep = [i for i, e in enumerate(errs) if e <= threshold]
    if min_keep <= len(keep) < len(gaze_points):
        refit = try_fit([gaze_points[i] for i in keep],
                        [screen_points[i] for i in keep], used_p2)
        if refit is not None:
            mapper = refit
            errs = dists(mapper, [gaze_points[i] for i in keep],
                         [screen_points[i] for i in keep])
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
