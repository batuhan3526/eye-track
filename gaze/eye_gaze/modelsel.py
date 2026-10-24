# gaze/eye_gaze/modelsel.py
"""Which inputs each screen axis of a calibration should use - chosen from
the person's own calibration, on data the fit did not see.

One fixed model (every feature, quadratic eye terms) fits the calibration
frames well and can still fail the moment the user sits differently: a head
position feature that only carries the posture of one pass looks like signal
inside that pass, and after a posture change it moves the cursor somewhere
the user is not looking. Stronger ridge does not cure that - the coefficients
are not too large, they are on the wrong inputs.

So, per axis, a small FIXED family of candidates is compared - a nested
ladder from the primary gaze pair (the deep model when it is on, else the
eye) up to every input, each linear or with quadratic terms of the primary
pair, at three ridge strengths - on two held-out measures:

- within posture: contiguous time blocks held out, the same honest grading
  the calibration has always reported (and the number the grade bands mean);
- across posture: when the calibration had several passes (the user is asked
  to shift posture between them), every pass held out once and predicted
  from the others.

A candidate is admissible only when its across-posture error is within
_POSTURE_GUARD of the best across-posture error: a feature that flings the
cursor after a posture change cannot win, however well it fits inside one.
Among the admissible ones the simplest within one standard error of the best
within-posture error is taken (the family is small and the rule leans
simple, so the choice itself does not overfit). The final model is fitted
on all pairs exactly as the live calibration fits (ridge + gross-outlier
rejection), and what was measured travels with it as a plain dict - the
grade, per-axis error, how much of the screen the cursor actually spans (the
coverage slope), and the across-posture error ("posture").

Pure numpy, no I/O.
"""
import itertools
import math
from collections import namedtuple

import numpy as np

from .mapping import axis_design, fit_with_rejection, ridge_path
from .pursuit import pursuit_keep

_VERSION = 1

# Eye-lag candidates for pursuit (s), each graded on held-out pairs; within
# _LAG_TIE (relative) of a smaller lag counts as a tie and the smaller wins.
LATENCIES = tuple(round(0.03 * i, 2) for i in range(17))
_LAG_TIE = 0.01

_LAMBDAS = (1e-1, 1e-2, 1e-3)    # larger first: the simpler model on a tie
_BLOCKS = 10                     # same blocks as the pursuit grade always had
_POSTURE_GUARD = 1.25            # admissible: across-posture error <= this x best
_MIN_PAIRS = 8                   # same floor as mapping.holdout_error
_FLOOR = 60.0                    # px; mapping.holdout_error's outlier floor
_SE_CHUNKS = 10

Selection = namedtuple("Selection", "mapper err err_cv report")


# --- the feature vector --------------------------------------------------------


def layout(per_eye, use_head, use_tr, deep):
    """[(group, size)] in the order the driver builds the gaze vector:
    eye (gx, gy), per-eye differences, head rotation, head position (only
    with head rotation), deep model (appended last)."""
    out = [("eye", 2)]
    if per_eye:
        out.append(("eyediff", 2))
    if use_head:
        out.append(("headrot", 2))
        if use_tr:
            out.append(("headpos", 3))
    if deep:
        out.append(("deep", 2))
    return out


def input_count(per_eye, use_head, use_tr, deep):
    return sum(n for _, n in layout(per_eye, use_head, use_tr, deep))


def parse_stamp(stamp):
    """Feature stamp "pe1-hp1-ht1-dg0" -> (per_eye, use_head, use_tr, deep),
    or None when it is not one."""
    try:
        parts = {}
        for part in str(stamp).split("-"):
            parts[part[:2]] = part[2:]
        vals = tuple(parts[k] for k in ("pe", "hp", "ht", "dg"))
        if any(v not in ("0", "1") for v in vals):
            return None
        return tuple(v == "1" for v in vals)
    except Exception:
        return None


def _indexed(lay):
    out, i = {}, 0
    for name, n in lay:
        out[name] = list(range(i, i + n))
        i += n
    return out


def _combos():
    for pe, hp, ht, dg in itertools.product((False, True), repeat=4):
        if ht and not hp:
            continue        # translation only counts with head pose
        yield pe, hp, ht, dg


def feature_groups(n_inputs, feat=None):
    """{group: [input indices]} for a gaze vector of n_inputs.

    From the feature stamp when it is known and matches the count. Without
    one, only a count that a single setting combination produces is
    trusted; otherwise everything after the eye pair is one unnamed group
    ("rest") - the ladder then only chooses between eye and all.
    """
    n = int(n_inputs)
    flags = parse_stamp(feat) if feat else None
    if flags is not None and input_count(*flags) == n:
        return _indexed(layout(*flags))
    hits = {tuple(layout(*c)) for c in _combos() if input_count(*c) == n}
    if len(hits) == 1:
        return _indexed(list(hits.pop()))
    if n <= 2:
        return {"eye": list(range(n))}
    return {"eye": [0, 1], "rest": list(range(2, n))}


def _primary(groups):
    return "deep" if "deep" in groups else "eye"


def primary_pair(groups):
    g = groups[_primary(groups)]
    return (g[0], g[1]) if len(g) >= 2 else None


def ladder(groups):
    """[(group names, input indices)] from simplest to richest, nested:
    primary, eye + deep, + eye differences, + head rotation, all."""
    order = list(groups)
    steps = [(_primary(groups),), ("eye", "deep"), ("eyediff",),
             ("headrot",), tuple(order)]
    names, out = set(), []
    for step in steps:
        names |= {s for s in step if s in groups}
        idx = tuple(sorted(i for s in names for i in groups[s]))
        if not out or idx != out[-1][1]:
            out.append((tuple(s for s in order if s in names), idx))
    return out


# --- validation folds ------------------------------------------------------------


def groups_from_t(t):
    """Pass index per pursuit sample: t restarts from ~0 on every pass."""
    t = np.asarray(t, dtype=float)
    g = np.zeros(len(t), dtype=int)
    if len(t) > 1:
        g[1:] = np.cumsum(np.diff(t) < 0)
    return g


def _pass_folds(n, groups):
    """Held-out index arrays, one per pass; None with fewer than two."""
    if groups is None or len(groups) != n:
        return None
    g = np.asarray(groups)
    labels = np.unique(g)
    if len(labels) < 2:
        return None
    return [np.flatnonzero(g == v) for v in labels]


def _block_folds(n, blocks=_BLOCKS):
    """Held-out index arrays: contiguous blocks (consecutive frames are near
    duplicates, so interleaved folds would grade a frame by its neighbour)."""
    k = max(2, min(int(blocks), n))
    edges = [round(i * n / k) for i in range(k + 1)]
    return [np.arange(a, b) for a, b in zip(edges, edges[1:]) if b > a]


def _kept(e):
    """mapping.holdout_error's gross-outlier rule (blinks, glances)."""
    med = np.sort(e)[len(e) // 2]
    return e <= max(2.5 * med, _FLOOR)


def _slope(y, p):
    """Held-out prediction spread over true spread: 1.0 reaches the whole
    calibrated range, 0.5 only half of it."""
    v = float(np.var(y))
    if v <= 0:
        return None
    return float(np.mean((y - y.mean()) * (p - p.mean())) / v)


def _axis_score(pred, y):
    """(trimmed RMSE, its standard error, slope).

    Consecutive pursuit frames are near duplicates, so the standard error is
    taken over contiguous chunks of the (time ordered) errors, not over
    frames - per frame it would claim a precision the data does not have.
    """
    e = np.abs(pred - y)
    keep = _kept(e)
    sq = e[keep] ** 2
    rmse = math.sqrt(float(sq.mean()))
    chunks = [c.mean() for c in np.array_split(sq, min(_SE_CHUNKS, len(sq)))
              if len(c)]
    se = 0.0
    if len(chunks) > 1 and rmse > 0:
        se = float(np.std(chunks, ddof=1)) / math.sqrt(len(chunks)) / (2 * rmse)
    return rmse, se, _slope(y[keep], pred[keep])


def _axis_heldout(X, y, folds, inputs, quad, reach):
    """Held-out predictions of one axis candidate at every _LAMBDAS
    strength, with the live mapper's input clamp and +-30% output cap; None
    when a fold is too small."""
    spec = (inputs, quad, 0.0)
    d = axis_design(X, spec)
    m = d.shape[1]
    pred = np.empty((len(_LAMBDAS), len(y)))
    for te in folds:
        tr = np.ones(len(y), dtype=bool)
        tr[te] = False
        if tr.sum() < m:
            return None
        lo, hi = X[tr].min(axis=0), X[tr].max(axis=0)
        rng = hi - lo
        dt = axis_design(np.clip(X[te], lo - reach * rng, hi + reach * rng), spec)
        ylo, yhi = y[tr].min(), y[tr].max()
        for k, c in enumerate(ridge_path(d[tr], y[tr], _LAMBDAS)):
            pred[k, te] = np.clip(dt @ c, ylo - 0.3 * (yhi - ylo),
                                  yhi + 0.3 * (yhi - ylo))
    return pred


def _choose_axis(X, y, folds, pfolds, steps, pair, reach, allow_quad,
                 guard=None):
    """(names, spec, within-posture RMSE) of the chosen candidate, or None.

    folds: within-posture blocks; pfolds: whole passes, or None (one pass).
    """
    guard = _POSTURE_GUARD if guard is None else guard
    cands = []
    quads = (None, pair) if (allow_quad and pair is not None) else (None,)
    for names, idx in steps:          # simplicity order: ladder,
        for quad in quads:            # linear before quadratic,
            preds = _axis_heldout(X, y, folds, idx, quad, reach)
            if preds is None:
                continue
            ppreds = (None if pfolds is None
                      else _axis_heldout(X, y, pfolds, idx, quad, reach))
            for k, lam in enumerate(_LAMBDAS):       # stronger ridge first
                rmse, se, _ = _axis_score(preds[k], y)
                prmse = None if ppreds is None else _axis_score(ppreds[k], y)[0]
                if math.isfinite(rmse):
                    cands.append((rmse, se, prmse, names, (idx, quad, lam)))
    checked = [c[2] for c in cands if c[2] is not None and math.isfinite(c[2])]
    if checked:
        # A candidate that could not be tried across posture (a pass too
        # small to train on) cannot prove it survives one.
        limit = guard * min(checked)
        cands = [c for c in cands if c[2] is not None and c[2] <= limit]
    if not cands:
        return None
    best = min(cands, key=lambda c: c[0])
    limit = best[0] + best[1]
    return next((c[3], c[4], c[0]) for c in cands if c[0] <= limit)


def _clean(gaze, screen, groups):
    """(X, Y, groups) arrays of the finite pairs only (one NaN poisons a
    ridge fit), groups kept aligned; X is (0, 0) when unusable."""
    try:
        x = np.asarray(gaze, dtype=float)
        y = np.asarray(screen, dtype=float)
    except Exception:
        x = y = np.zeros((0, 0))
    if x.ndim != 2 or y.ndim != 2 or len(x) != len(y) or y.shape[1] != 2:
        return np.zeros((0, 0)), np.zeros((0, 2)), None
    ok = np.isfinite(x).all(axis=1) & np.isfinite(y).all(axis=1)
    grp = None
    if groups is not None and len(groups) == len(x):
        grp = np.asarray(groups)[ok]
    return x[ok], y[ok], grp


def _report(pred, screen, kind, k):
    y = np.asarray(screen, dtype=float)
    eu = np.hypot(pred[:, 0] - y[:, 0], pred[:, 1] - y[:, 1])
    rep = {"v": _VERSION, "cv": kind, "k": int(k), "n": int(len(y)),
           "err": float(eu[_kept(eu)].mean())}
    for ax, j in (("x", 0), ("y", 1)):
        rmse, _, slope = _axis_score(pred[:, j], y[:, j])
        rep[ax] = {"rmse": rmse, "slope": slope}
    return rep


def validate(gaze, screen, groups=None, reach=0.0, poly2=False, axes=None,
             blocks=_BLOCKS, posture=False):
    """Held-out report of ONE fixed model, trained exactly as the live
    calibration trains (mapping.fit_with_rejection). posture=False:
    contiguous blocks (the grade); True: whole passes held out (None with
    fewer than two). `axes` None = the single all-inputs model of earlier
    versions (poly2 as given). None when not measurable."""
    x, y, grp = _clean(gaze, screen, groups)
    n = len(x)
    if n < _MIN_PAIRS:
        return None
    if posture:
        folds = _pass_folds(n, grp)
        if folds is None:
            return None
    else:
        folds = _block_folds(n, blocks)
    pred = np.empty((n, 2))
    for te in folds:
        tr = np.ones(n, dtype=bool)
        tr[te] = False
        m, _ = fit_with_rejection(x[tr], y[tr], reach_margin=reach,
                                  poly2=poly2, axes=axes)
        if m is None:
            return None
        pred[te] = m.predict_many(x[te])
    if not np.all(np.isfinite(pred)):
        return None
    return _report(pred, y, "pass" if posture else "blocks", len(folds))


def _choose(X, Y, grp, feat, reach, allow_quad, blocks, guard):
    """(chosen [(names, spec, rmse)] per axis, within-posture report of
    the pair trained the live way - its "err" is the grade) or None."""
    if len(X) < _MIN_PAIRS:
        return None
    fg = feature_groups(X.shape[1], feat)
    steps, pair = ladder(fg), primary_pair(fg)
    folds, pfolds = _block_folds(len(X), blocks), _pass_folds(len(X), grp)
    chosen = []
    for j in (0, 1):
        c = _choose_axis(X, Y[:, j], folds, pfolds, steps, pair, reach,
                         allow_quad, guard)
        if c is None:
            return None
        chosen.append(c)
    axes = tuple(spec for _, spec, _ in chosen)
    rep = validate(X, Y, grp, reach, axes=axes, blocks=blocks)
    return None if rep is None else (chosen, rep)


def _finish(X, Y, grp, reach, chosen, rep):
    """The final mapper on all pairs, with the report completed; or None."""
    axes = tuple(spec for _, spec, _ in chosen)
    mapper, err = fit_with_rejection(X, Y, reach_margin=reach, axes=axes)
    if mapper is None:
        return None
    for ax, (names, (idx, quad, lam), _) in zip(("x", "y"), chosen):
        rep[ax].update({"groups": list(names), "in": list(idx),
                        "quad": None if quad is None else list(quad),
                        "lam": lam})
    # Posture robustness: the same model with whole passes held out.
    rep["posture"] = validate(X, Y, grp, reach, axes=axes, posture=True)
    mapper.selection = rep
    mapper.err_cv = rep["err"]
    return Selection(mapper, err, rep["err"], rep)


def select(gaze, screen, groups=None, feat=None, reach=0.0, allow_quad=True,
           blocks=_BLOCKS, guard=None):
    """Choose and fit the per-axis model for these training pairs.

    groups: pass index per pair, or None. feat: the feature stamp (names the
    inputs; see feature_groups). allow_quad False keeps every candidate
    linear. Returns Selection(mapper, in-sample error, held-out error,
    report) or None when nothing could be validated.
    """
    X, Y, grp = _clean(gaze, screen, groups)
    got = _choose(X, Y, grp, feat, reach, allow_quad, blocks, guard)
    return None if got is None else _finish(X, Y, grp, reach, *got)


def select_pursuit(samples, path, groups=None, feat=None, reach=0.0,
                   allow_quad=True, latencies=LATENCIES, min_pairs=60,
                   guard=None):
    """select() for a pursuit calibration, with the eye lag chosen by the
    held-out (within-posture) grade of the model chosen at that lag - not
    the in-sample error, which favours whichever lag lets the richest model
    memorise the most.

    samples: [(gaze, t)]; groups: pass index per SAMPLE, or None to read the
    passes from the t restarts. Returns (Selection, latency) or (None, None).
    """
    if groups is None or len(groups) != len(samples):
        groups = groups_from_t([t for _, t in samples])
    best, best_lat = None, None
    for lat in latencies:
        idx, targets = pursuit_keep(samples, path, lat)
        if len(idx) < min_pairs:
            continue
        X, Y, grp = _clean([samples[i][0] for i in idx], targets,
                           [groups[i] for i in idx])
        got = _choose(X, Y, grp, feat, reach, allow_quad, _BLOCKS, guard)
        if got is None:
            continue
        if best is None or got[1]["err"] < best[1][1]["err"] * (1.0 - _LAG_TIE):
            best, best_lat = ((X, Y, grp), got), float(lat)
    # Only the winning lag is fitted on all pairs and tried across posture.
    sel = None if best is None else _finish(*best[0], reach, *best[1])
    if sel is None:
        return None, None
    sel.report["latency"] = best_lat
    return sel, best_lat
