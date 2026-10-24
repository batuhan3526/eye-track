# gaze/tests/test_pursuit_sim.py
"""A synthetic eye following the ball on both paths, through the real model
selection (modelsel.select_pursuit).

The eye: gaze features are a linear function of where the ball was 0.2 s
earlier plus frame noise, the vertical signal half as strong as the
horizontal one. What this shows, at equal noise:

- about a third of the grid's training pairs come from vertical motion,
  twice the rows' share (only their short links between rows);
- the vertical model from the grid is as accurate as the one from the rows
  on a common set of fixations, and the cursor reaches as far.

It does not show the grid to be MORE accurate for such an eye: for a linear
or quadratic vertical response four well-spread rows already pin the fit
down (simulated with frame noise, slow drift and a lid-flattened response,
the two paths came out within a few percent of each other). That is why
the rows stay the default and the grid is only selectable.

The turn guard a new recording is cut with (short before a turn, longer
after it, never more than half a segment) is checked here too: with an eye
that overshoots every turn and catches up by saccade, it pairs more frames
than the old 0.35 s-either-side rule and fits the rows just as well.
"""
import numpy as np

from eye_gaze import modelsel
from eye_gaze.pursuit import TURN_GUARD, PursuitPath, pursuit_keep

SW, SH = 1920, 1080
LAG = 0.2
NOISE = 0.003


def _features(x, y, rng, noise):
    return ((x / SW - 0.5) * 0.10 + rng.normal(0, noise),
            (y / SH - 0.5) * 0.05 + rng.normal(0, noise))


def _record(layout, seed, passes=2):
    rng = np.random.default_rng(seed)
    path = PursuitPath(SW, SH, 480.0, layout=layout)
    samples, groups = [], []
    for p in range(passes):
        for t in np.arange(0.0, path.duration + 0.2, 1 / 30.0):
            x, y = path.pos(t - LAG)
            samples.append((_features(x, y, rng, NOISE), float(t)))
            groups.append(p)
    return samples, path, groups


def _common_test_y(mapper, seed, n=600):
    rng = np.random.default_rng(1000 + seed)
    xs = rng.uniform(0.06, 0.94, n) * SW
    ys = rng.uniform(0.08, 0.92, n) * SH
    p = np.array([mapper.predict(_features(x, y, rng, NOISE))
                  for x, y in zip(xs, ys)])
    return float(np.sqrt(np.mean((p[:, 1] - ys) ** 2)))


def _vertical_share(layout):
    samples, path, _ = _record(layout, 0, passes=1)
    idx, _ = pursuit_keep(samples, path, LAG)
    moving = 0
    for i in idx:
        ts = samples[i][1] - LAG
        moving += abs(path.pos(ts + 1e-3)[1] - path.pos(ts)[1]) > 1e-6
    return moving / len(idx)


def test_grid_trains_the_vertical_model_on_vertical_motion():
    # rows: only the short links between rows (what the guard leaves of them)
    assert _vertical_share("grid") >= 0.3
    assert _vertical_share("grid") >= 1.8 * _vertical_share("rows")


def test_grid_vertical_model_is_as_good_as_the_rows_at_equal_noise():
    lags = (0.18, 0.21)                   # around the true one: keeps it quick
    out = {}
    for layout in ("grid", "rows"):
        errs, slopes = [], []
        for seed in (0, 1):
            samples, path, groups = _record(layout, seed)
            sel, lat = modelsel.select_pursuit(samples, path, groups,
                                               reach=0.25, latencies=lags)
            assert sel is not None
            errs.append(_common_test_y(sel.mapper, seed))
            slopes.append(sel.report["y"]["slope"])
        out[layout] = (np.mean(errs), np.mean(slopes))
    grid, rows = out["grid"], out["rows"]
    assert grid[0] <= 1.15 * rows[0], out       # loose: not worse
    assert grid[1] >= 0.9 and grid[1] >= rows[1] - 0.05, out


# --- the turn guard on the rows, with an eye that reacts late at a turn --------


def _eye_at(path, tb, react):
    """Where the eye looks at ball time tb: on the ball, except that it
    slows just before a turn, carries on in the old direction for `react`
    seconds after it, then catches up (saccade)."""
    for k, c in enumerate(path._cum[1:-1]):
        tc = c / path.speed
        if tc <= tb < tc + react[k]:
            (ax, ay), (bx, by) = path.pos(tc - 0.05), path.pos(tc)
            return (bx + (bx - ax) / 0.05 * (tb - tc),
                    by + (by - ay) / 0.05 * (tb - tc))
        if tc - 0.06 <= tb < tc:
            return path.pos(tc - 0.06 + 0.5 * (tb - tc + 0.06))
    return path.pos(tb)


def _record_turns(seed, guard, passes=2):
    rng = np.random.default_rng(seed)
    path = PursuitPath(SW, SH, 480.0, layout="rows", guard=guard)
    samples, groups = [], []
    for p in range(passes):
        react = rng.uniform(0.10, 0.28, len(path._cum))
        for t in np.arange(0.0, path.duration + 0.2, 1 / 30.0):
            x, y = _eye_at(path, t - LAG, react)
            samples.append((_features(x, y, rng, NOISE), float(t)))
            groups.append(p)
    return samples, path, groups


def _common_test(mapper, seed, n=600):
    rng = np.random.default_rng(1000 + seed)
    xs = rng.uniform(0.06, 0.94, n) * SW
    ys = rng.uniform(0.08, 0.92, n) * SH
    p = np.array([mapper.predict(_features(x, y, rng, NOISE))
                  for x, y in zip(xs, ys)])
    return (float(np.sqrt(np.mean((p[:, 0] - xs) ** 2))),
            float(np.sqrt(np.mean((p[:, 1] - ys) ** 2))))


def test_the_turn_guard_fits_the_rows_as_well_as_the_old_rule():
    lags = (0.18, 0.21)
    out = {}
    for name, guard in (("old", None), ("new", TURN_GUARD)):
        res = []
        for seed in (0, 1):
            samples, path, groups = _record_turns(seed, guard)
            sel, _ = modelsel.select_pursuit(samples, path, groups,
                                             reach=0.25, latencies=lags)
            assert sel is not None
            res.append(_common_test(sel.mapper, seed) + (sel.report["n"],))
        out[name] = np.mean(res, axis=0)
    old, new = out["old"], out["new"]
    assert new[2] >= 1.1 * old[2], out          # more pairs kept
    assert new[0] <= 1.05 * old[0], out         # x: not worse
    assert new[1] <= 1.05 * old[1], out         # y: not worse
