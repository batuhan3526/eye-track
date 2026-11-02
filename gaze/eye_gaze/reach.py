# gaze/eye_gaze/reach.py
"""Full reach of the screen edges.

A tester's 1.3.2 cursor did not get to the far left and right of a
2560x1440 screen, and the calibration had measured why: its held-out
predictions spanned 93% of the calibrated range (eye_gaze.modelsel's
coverage slope, 0.93). Any least-squares mapping pulls its guesses towards
the middle - it predicts the average target for what it sees, and noisy
inputs make that average sit inward. Looking straight at an edge then put
the cursor ~90 px short of it, before any smoothing.

Two things, both from what the person's own calibration measured:

- predict(): each axis is stretched back around the centre of the
  calibrated range by 1 / slope, so that on average the cursor lands where
  the person looks, edges included. The stretch also scales the noise, so
  it is capped at 1 / MIN_SLOPE (1.25x): a calibration that reaches less
  than 80% needs a new calibration, not a magnifying glass on its errors.
  An axis that already reaches (slope >= 1) or a calibration without a
  report (older files, dots, a refit without selection) is left as it is.
- edge(): a narrow band along each screen edge carries the cursor onto the
  edge - its outer half is the edge itself, its inner half moves twice as
  fast, so there is no jump and every point stays reachable. The edges are
  where the scroll bars, the taskbar and a maximised window's close button
  are. The band is a quarter of the axis' measured error, at most 2% of the
  screen (1% without a report).

The setting `edge_reach` (resolve()): a config.yaml that says it wins;
without the line it is on for a 1.3.x config (one with cursor_sensitivity)
and off for a v1.2.0 one, whose cursor keeps the feel its user validated.
Pure Python, no I/O.
"""
import math

from eye_gaze import calquality

KEY = "edge_reach"
MIN_SLOPE = 0.8             # stretch at most 1 / 0.8 = 1.25x
EDGE_ERROR_FRACTION = 0.25  # band = a quarter of the axis' held-out error ...
EDGE_MAX_FRACTION = 0.02    # ... at most 2% of the screen axis
EDGE_DEFAULT_FRACTION = 0.01  # without a measured error


def _slope(report, axis):
    try:
        v = report[axis]["slope"]
    except Exception:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) and v > 0 else None


def _rmse(report, axis):
    try:
        v = report[axis]["rmse"]
    except Exception:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) and v >= 0 else None


def factor(slope):
    """The stretch for a coverage slope: 1 / slope, between 1 and
    1 / MIN_SLOPE; 1 for an unusable slope."""
    if slope is None:
        return 1.0
    return 1.0 / min(1.0, max(MIN_SLOPE, slope))


def warp(u, lo, hi, band):
    """u carried onto the nearer edge of [lo, hi] when within `band` of it
    (the outer half is the edge, the inner half moves twice as fast);
    continuous and never decreasing. A band wider than a quarter of the
    axis is narrowed to it."""
    band = min(float(band), (hi - lo) / 4.0)
    if band <= 0:
        return u
    a, h = hi - 2.0 * band, hi - band
    if u >= h:
        return hi
    if u > a:
        return a + 2.0 * (u - a)
    a, h = lo + 2.0 * band, lo + band
    if u <= h:
        return lo
    if u < a:
        return a - 2.0 * (a - u)
    return u


class _View:
    """predict() of one mapper through a Reach (measure_drift and the
    saccade detector take an object with predict())."""

    def __init__(self, owner, mapper):
        self._owner = owner
        self._mapper = mapper

    def predict(self, gaze):
        return self._owner.predict(self._mapper, gaze)


class Reach:
    """The live cursor's reach correction; Reach(False) changes nothing."""

    def __init__(self, enabled, log=print):
        self.enabled = bool(enabled)
        self._log = log
        self._for = None            # the mapper the cached values belong to
        self._stretch = None        # ((kx, ky), (cx, cy)) or None
        self._view = None

    def _cached(self, mapper):
        if mapper is not self._for:
            self._stretch = self._measure(mapper)
            self._for = mapper
        return self._stretch

    def _fault(self, exc):
        """Like the saccade jump and target snapping, the reach never stops
        the cursor loop: a fault switches it off for the session, said once."""
        self.enabled = False
        try:
            self._log("UYARI: kenarlara tam ulasim hata verdi ve bu oturum icin"
                      " KAPATILDI (" + type(exc).__name__ + ": "
                      + ascii(str(exc)) + "). Imlec 1.3.2'deki gibi calisiyor.")
        except Exception:
            pass

    @staticmethod
    def _measure(mapper):
        rep = getattr(mapper, "selection", None)
        rng = getattr(mapper, "calibrated_range", None)
        if not isinstance(rep, dict) or rng is None:
            return None
        (x0, y0), (x1, y1) = rng
        k = (factor(_slope(rep, "x")), factor(_slope(rep, "y")))
        if k == (1.0, 1.0):
            return None
        return k, ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    def predict(self, mapper, gaze):
        """mapper.predict(gaze), stretched back by the measured shrink."""
        px, py = mapper.predict(gaze)
        if not self.enabled:
            return px, py
        try:
            st = self._cached(mapper)
            if st is None:
                return px, py
            (kx, ky), (cx, cy) = st
            return cx + (px - cx) * kx, cy + (py - cy) * ky
        except Exception as exc:
            self._fault(exc)
            return px, py

    def view(self, mapper):
        """An object whose predict(gaze) is self.predict(mapper, gaze)."""
        if self._view is None or self._view._mapper is not mapper:
            self._view = _View(self, mapper)
        return self._view

    def bands(self, mapper, width, height):
        """(x band, y band) in px for a screen area of width x height."""
        rep = getattr(mapper, "selection", None)
        out = []
        for axis, size in (("x", width), ("y", height)):
            size = max(0.0, float(size))
            e = _rmse(rep, axis) if isinstance(rep, dict) else None
            if e is None:
                out.append(EDGE_DEFAULT_FRACTION * size)
            else:
                out.append(min(EDGE_ERROR_FRACTION * e, EDGE_MAX_FRACTION * size))
        return tuple(out)

    def edge(self, mapper, x, y, limits):
        """(x, y) with the edge band applied inside limits = (x0, y0, x1,
        y1), the inclusive pixel limits the cursor is clamped to."""
        if not self.enabled:
            return x, y
        try:
            x0, y0, x1, y1 = limits
            bx, by = self.bands(mapper, x1 - x0 + 1, y1 - y0 + 1)
            return warp(x, x0, x1, bx), warp(y, y0, y1, by)
        except Exception as exc:
            self._fault(exc)
            return x, y

    def describe(self, mapper, width, height):
        """One ASCII line for gaze.log: on/off, the stretch per axis and the
        edge bands on a width x height screen."""
        if not self.enabled:
            return ("Kenarlara tam ulasim: kapali (config.yaml: edge_reach:"
                    " true ile acilir)")
        try:
            st = self._cached(mapper)
            bx, by = self.bands(mapper, width, height)
        except Exception as exc:
            self._fault(exc)
            return "Kenarlara tam ulasim: hata, kapatildi"
        if st is None:
            how = "kalibrasyon daralma olcmedi, imlec oldugu gibi"
        else:
            how = "imlec x %.2f, y %.2f kat acilir (kalibrasyonun olctugu" \
                  " daralma)" % st[0]
        return ("Kenarlara tam ulasim: acik - %s; kenar payi %.0f / %.0f px"
                % (how, bx, by))

    def coverage(self, mapper):
        """(x %, y %) of the calibrated range the live cursor reaches, or
        None per axis without a measured slope (the calibration result)."""
        rep = getattr(mapper, "selection", None)
        out = []
        for axis in ("x", "y"):
            s = _slope(rep, axis) if isinstance(rep, dict) else None
            if s is None:
                out.append(None)
                continue
            if self.enabled:
                s = s * factor(s)
            out.append(calquality.coverage_pct(s))
        return tuple(out)


def resolve(cfg, user):
    """cfg[KEY]: the file's own value when it has the line; otherwise on for
    a 1.3.x config (it has cursor_sensitivity), off for a v1.2.0 one."""
    from eye_gaze import sensitivity
    user = user if isinstance(user, dict) else {}
    if KEY in user and KEY in cfg:
        cfg[KEY] = cfg[KEY] is True
    else:
        cfg[KEY] = sensitivity.KEY in user
    return cfg
