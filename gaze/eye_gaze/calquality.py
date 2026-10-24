# gaze/eye_gaze/calquality.py
"""How good a calibration is, in terms of the screen it was made on, and
what to tell the user about it.

The grade bands used to be fixed pixels (held-out error under 100 px IYI,
200 px and over KOTU) - right for the 2560x1440 screen they were set on, but
a 1366x768 laptop is half that diagonal, so the same accuracy relative to
the screen graded twice as well there. They are now a fraction of the screen
diagonal, chosen so that 100/200 px at 2560x1440 mean exactly what they
meant; an unknown screen keeps the pixel values. The driver's grade, the
startup refit (caldata) and the save rule all read them from here.

Besides the grade, the per-axis report of the model selection (modelsel)
says how far the cursor can actually reach on each axis (the coverage
slope) and how it fares after a posture change. advice() turns those into
concrete hints - only where the numbers point at a cause the user can do
something about. Pure Python + numpy, no I/O, no UI.
"""
import math
from collections import namedtuple

import numpy as np

REF_SCREEN = (2560, 1440)
_REF_DIAG = math.hypot(*REF_SCREEN)
# fractions of the screen diagonal: held-out error (the grade) ...
_GOOD, _BAD = 100.0 / _REF_DIAG, 200.0 / _REF_DIAG
# ... and in-sample error, for a fit that could not be graded held-out
_FIT_GOOD, _FIT_BAD = 80.0 / _REF_DIAG, 160.0 / _REF_DIAG

WORSE = 1.25            # "clearly worse": a new error over this x the old one
POOR_COVERAGE = 0.5     # coverage slope under this: half the screen unreachable
SHIFT_LARGE = 1.0       # head features moved >= 1 SD (pooled) between passes
_MIN_PASS = 10          # samples a pass needs before its mean means anything

Summary = namedtuple("Summary", "label err bad axes coverage shift hints")


def _diag(screen):
    try:
        w, h = float(screen[0]), float(screen[1])
        d = math.hypot(w, h)
        if w > 0 and h > 0 and math.isfinite(d):
            return d
    except Exception:
        pass
    return _REF_DIAG


def bands(screen=None, held_out=True):
    """(IYI under, KOTU from) in px for a screen (w, h); None/invalid = the
    reference screen. held_out False: the stricter in-sample bands."""
    d = _diag(screen)
    good, bad = (_GOOD, _BAD) if held_out else (_FIT_GOOD, _FIT_BAD)
    return good * d, bad * d


def label(err, screen=None, held_out=True):
    """'IYI' / 'ORTA' / 'KOTU', or '?' without an error."""
    if not isinstance(err, (int, float)) or isinstance(err, bool):
        return "?"
    good, bad = bands(screen, held_out)
    return "IYI" if err < good else "ORTA" if err < bad else "KOTU"


def is_bad(err, screen=None):
    return label(err, screen) == "KOTU"


def coverage_pct(slope):
    """Coverage slope as a whole percent of the calibrated range, 0..100."""
    if not isinstance(slope, (int, float)) or not math.isfinite(slope):
        return None
    return int(round(100.0 * min(1.0, max(0.0, slope))))


def head_shift(gaze, groups, feat=None):
    """How far the head features moved between calibration passes: the
    largest difference of pass means, in standard deviations of the pooled
    feature, over the head rotation/position inputs. None without head
    features or without two passes of _MIN_PASS samples."""
    try:
        from .modelsel import feature_groups
        g = np.asarray(gaze, dtype=float)
        if g.ndim != 2 or groups is None or len(groups) != len(g):
            return None
        fg = feature_groups(g.shape[1], feat)
        cols = fg.get("headrot", []) + fg.get("headpos", [])
        if not cols:
            return None
        grp = np.asarray(groups)
        ok = np.isfinite(g[:, cols]).all(axis=1)
        labels = [v for v in np.unique(grp[ok]) if (grp[ok] == v).sum() >= _MIN_PASS]
        if len(labels) < 2:
            return None
        best = 0.0
        for j in cols:
            col = g[ok, j]
            sd = float(col.std())
            if sd <= 0:
                continue
            means = [float(col[grp[ok] == v].mean()) for v in labels]
            best = max(best, (max(means) - min(means)) / sd)
        return best
    except Exception:
        return None


def advice(report, err, screen=None, shift=None, key="F9"):
    """[(i18n key under driver.calq, format args)] - what to say after a
    calibration, most important first; [] when there is nothing to say.

    - KOTU: says so, and how to try again (`key` x2).
    - an axis reaches less than POOR_COVERAGE of the screen: which one, and
      the usual causes (head following the ball, light, glasses, camera).
    - the passes sat clearly apart (shift >= SHIFT_LARGE) AND the error
      after a posture change is KOTU and clearly worse than the grade: sit
      as usual, shift only slightly between passes. A shift alone is no
      fault - the user is asked to shift between passes - and a poor
      across-posture error without a shift has another cause.
    """
    out = []
    if is_bad(err, screen):
        out.append(("bad", {"key": key}))
    if not isinstance(report, dict):
        return out
    try:
        weak = []
        for ax in ("x", "y"):
            s = (report.get(ax) or {}).get("slope")
            if isinstance(s, (int, float)) and s < POOR_COVERAGE:
                weak.append(ax)
        post = report.get("posture")
        perr = post.get("err") if isinstance(post, dict) else None
        if (isinstance(shift, (int, float)) and shift >= SHIFT_LARGE
                and isinstance(perr, (int, float)) and is_bad(perr, screen)
                and isinstance(err, (int, float)) and perr >= WORSE * err):
            out.append(("hint_posture", {"err": int(round(perr))}))
        if weak:
            axis = "axis_xy" if len(weak) == 2 else "axis_" + weak[0]
            out.append(("hint_reach", {"axis": axis}))
            out.extend((k, {}) for k in ("hint_head_still", "hint_light",
                                         "hint_camera"))
    except Exception:
        pass
    return out


def summary(mapper, screen=None, feat=None, key="F9"):
    """Everything the calibration result screen and printout show, for a
    freshly fitted mapper. Never raises."""
    err = getattr(mapper, "err_cv", None)
    rep = getattr(mapper, "selection", None)
    axes = coverage = shift = None
    if isinstance(rep, dict):
        try:
            axes = tuple(int(round(rep[a]["rmse"])) for a in ("x", "y"))
            coverage = tuple(coverage_pct(rep[a].get("slope")) for a in ("x", "y"))
        except Exception:
            axes = coverage = None
        cs = getattr(mapper, "cal_samples", None)
        if cs is not None:
            try:
                shift = head_shift(cs.gaze, cs.groups(), feat or cs.meta.get("feat"))
            except Exception:
                shift = None
    return Summary(label(err, screen), err, is_bad(err, screen), axes, coverage,
                   shift, advice(rep, err, screen, shift, key))
