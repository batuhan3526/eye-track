# gaze/eye_gaze/oneeuro.py
"""One Euro Filter - low-latency jitter smoothing for cursor coordinates."""
import math


class _LowPass:
    def __init__(self, alpha: float):
        self.alpha = alpha
        self.s = None

    def __call__(self, value: float, alpha: float = None) -> float:
        if alpha is not None:
            self.alpha = alpha
        if self.s is None:
            self.s = value
        else:
            self.s = self.alpha * value + (1.0 - self.alpha) * self.s
        return self.s


class OneEuroFilter:
    def __init__(self, freq: float, mincutoff: float = 1.0, beta: float = 0.0, dcutoff: float = 1.0):
        if freq <= 0:
            raise ValueError("freq must be > 0")
        self.freq = float(freq)
        self.mincutoff = float(mincutoff)
        self.beta = float(beta)
        self.dcutoff = float(dcutoff)
        self._x = _LowPass(self._alpha(self.mincutoff))
        self._dx = _LowPass(self._alpha(self.dcutoff))
        self._last = None
        self._last_t = None

    def reset(self):
        """Forget all history: the next value passes straight through.
        Used when a saccade makes the old position meaningless."""
        self._x.s = None
        self._dx.s = None
        self._last = None
        self._last_t = None

    def _alpha(self, cutoff: float, te: float = None) -> float:
        if te is None:
            te = 1.0 / self.freq
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / te)

    def __call__(self, value: float, t: float = None) -> float:
        # A NaN/inf would stay in the filter state forever (every later
        # output NaN = frozen cursor). Skip it: hold the last output.
        if not math.isfinite(value):
            return self._x.s if self._x.s is not None else value
        # With a timestamp the real frame interval drives the smoothing, so
        # behaviour stays consistent when the camera frame rate varies.
        te = 1.0 / self.freq
        if t is not None:
            if self._last_t is not None and t > self._last_t:
                te = t - self._last_t
            self._last_t = t
        prev = self._last if self._last is not None else value
        dvalue = (value - prev) / te
        edvalue = self._dx(dvalue, self._alpha(self.dcutoff, te))
        cutoff = self.mincutoff + self.beta * abs(edvalue)
        result = self._x(value, self._alpha(cutoff, te))
        self._last = value
        return result
