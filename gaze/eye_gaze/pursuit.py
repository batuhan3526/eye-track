# gaze/eye_gaze/pursuit.py
"""Smooth-pursuit calibration path.

Instead of 16 static dots, a ball glides across the screen at constant speed
and the eyes follow it. Every frame yields a (gaze, ball position) training
pair, so a ~25 second sweep collects hundreds of samples - far more data,
far less tiring than staring at dots.

Two layouts (config calibration_path):

- "rows" (default): the boustrophedon over four rows plus a return to
  center that every calibration so far was made with.
- "grid": the outer frame plus the middle row and the middle column -
  three full-width rows AND three full-height columns, the middle column
  swept twice, so the vertical model is trained on vertical motion over
  the whole height. Selectable, not the default: in simulation (synthetic
  eye through the real model selection, tests/test_pursuit_sim.py) it was
  no more accurate than the rows - four well-spread rows already pin a
  linear or quadratic vertical fit - and it has no field record yet.

Both cover the same 6%-94% (x) / 8%-92% (y) envelope, and the grid is
about as long as the rows (within 15% on landscape screens from 5:4 to 32:9),
so a pass takes about as long.

Samples near direction changes are dropped by the caller via near_turn():
the eye saccades at a turn and those frames would pair the wrong positions.
"""
import bisect
import math

# screen-fraction waypoints: 4-row zigzag + center finish
_ROWS = [(0.06, 0.08), (0.94, 0.08),
         (0.94, 0.37), (0.06, 0.37),
         (0.06, 0.63), (0.94, 0.63),
         (0.94, 0.92), (0.06, 0.92),
         (0.5, 0.5)]

# The frame, then the middle row and the middle column (down and back up):
# one continuous line through every edge of a 3x3 grid. It starts at the
# left middle because a line covering every edge once must start and end
# where an odd number of edges meet.
_X, _Y = (0.06, 0.5, 0.94), (0.08, 0.5, 0.92)
_GRID = [(_X[i], _Y[j]) for i, j in
         ((0, 1), (0, 0), (2, 0), (2, 2), (0, 2), (0, 1),
          (1, 1), (1, 0), (1, 2), (1, 1), (2, 1))]

LAYOUTS = {"grid": _GRID, "rows": _ROWS}
_ALIASES = {"izgara": "grid", "satir": "rows", "satirlar": "rows"}
DEFAULT_LAYOUT = "rows"      # also when config.yaml has no calibration_path

# Turn guard (s of ball time, i.e. after the eye lag is taken off): the eye
# keeps smooth pursuit up to the turn and needs a catch-up saccade after it,
# so little is dropped before a direction change and more after it (and
# after the start). On a short segment both are shrunk so that at least
# _MIN_KEEP of it survives - the guard is time, the segment is length/speed,
# so on a small screen or with a fast ball the old fixed guard dropped whole
# vertical segments.
TURN_GUARD = (0.10, 0.35)
LEGACY_GUARD = 0.35     # either side of every waypoint (samples from before)
_MIN_KEEP = 0.5
_STRAIGHT = 0.996       # cos of the angle below which a waypoint is no turn


def layout_of(value, say=print):
    """config calibration_path -> a LAYOUTS name; unknown values are named
    and the default is used."""
    if value is None:
        return DEFAULT_LAYOUT
    v = str(value).strip().lower()
    if not v:
        return DEFAULT_LAYOUT
    v = _ALIASES.get(v, v)
    if v in LAYOUTS:
        return v
    try:
        say("UYARI: calibration_path '%s' taninmadi; \"%s\" kullaniliyor"
            " (\"rows\" ya da \"grid\" yazin)."
            % (str(value).encode("ascii", "replace").decode("ascii"),
               DEFAULT_LAYOUT))
    except Exception:
        pass
    return DEFAULT_LAYOUT


class PursuitPath:
    """The ball's path in pixels.

    waypoints: screen fractions (default: LAYOUTS[layout]). guard: the turn
    guard (before, after) in seconds, or None for the legacy rule
    (LEGACY_GUARD either side of every waypoint) - what samples recorded
    without a stored guard were paired with.
    """

    def __init__(self, sw, sh, speed=480.0, waypoints=None, layout=DEFAULT_LAYOUT,
                 guard=TURN_GUARD):
        wp = waypoints if waypoints is not None else LAYOUTS[layout]
        self._pts = [(fx * sw, fy * sh) for (fx, fy) in wp]
        self.size = (sw, sh)
        self.speed = float(speed)
        self._cum = [0.0]
        for a, b in zip(self._pts, self._pts[1:]):
            self._cum.append(self._cum[-1] + math.dist(a, b))
        self.length = self._cum[-1]
        self.duration = self.length / self.speed
        self.guard = None if guard is None else (float(guard[0]), float(guard[1]))
        self._windows = [] if self.guard is None else self._turn_windows()

    def _turns(self):
        """Indices of the waypoints where the direction changes (the start
        and the end always count)."""
        n = len(self._pts)
        out = []
        for i in range(n):
            if i == 0 or i == n - 1:
                out.append(i)
                continue
            (ax, ay), (bx, by), (cx, cy) = self._pts[i - 1:i + 2]
            u, v = (bx - ax, by - ay), (cx - bx, cy - by)
            nu, nv = math.hypot(*u), math.hypot(*v)
            if nu <= 0 or nv <= 0 or (u[0] * v[0] + u[1] * v[1]) / (nu * nv) < _STRAIGHT:
                out.append(i)
        return out

    def _turn_windows(self):
        """[(from, to)] path distances dropped around the turns."""
        before, after = (g * self.speed for g in self.guard)
        turns = set(self._turns())
        cut = [[0.0, 0.0] for _ in self._pts]   # (before, after) per waypoint
        for i in turns:
            cut[i] = [before if i > 0 else 0.0,
                      after if i < len(self._pts) - 1 else 0.0]
        # Each segment loses its start's "after" and its end's "before";
        # together never more than (1 - _MIN_KEEP) of it.
        for k in range(len(self._pts) - 1):
            seg = self._cum[k + 1] - self._cum[k]
            lost = cut[k][1] + cut[k + 1][0]
            room = (1.0 - _MIN_KEEP) * seg
            if lost > room > 0:
                f = room / lost
                cut[k][1] *= f
                cut[k + 1][0] *= f
        return [(self._cum[i] - cut[i][0], self._cum[i] + cut[i][1])
                for i in sorted(turns)]

    def pos(self, t):
        """Ball position at time t (seconds from motion start), clamped."""
        d = max(0.0, min(t, self.duration)) * self.speed
        i = bisect.bisect_right(self._cum, d) - 1
        i = max(0, min(i, len(self._pts) - 2))
        seg = self._cum[i + 1] - self._cum[i]
        k = 0.0 if seg <= 0 else (d - self._cum[i]) / seg
        ax, ay = self._pts[i]
        bx, by = self._pts[i + 1]
        return (ax + (bx - ax) * k, ay + (by - ay) * k)

    def near_turn(self, t, guard=None):
        """True near a direction change (incl. start/end), where the eye is
        saccading rather than smoothly pursuing.

        guard (s) given: the legacy rule with that guard, either side of
        every waypoint. Otherwise this path's own rule: its turn guard, or
        the legacy rule when it has none."""
        d = max(0.0, min(t, self.duration)) * self.speed
        if guard is None and self.guard is not None:
            return any(lo <= d <= hi for lo, hi in self._windows)
        g = (LEGACY_GUARD if guard is None else guard) * self.speed
        return any(abs(d - c) <= g for c in self._cum)


def pursuit_keep(samples, path, lat):
    """(indices into samples, ball positions) of the frames that make
    training pairs for an assumed eye lag `lat`: frames outside the motion
    and near direction changes are dropped."""
    idx, s_pts = [], []
    for i, (_, t) in enumerate(samples):
        ts = t - lat
        if 0.0 <= ts <= path.duration and not path.near_turn(ts):
            idx.append(i)
            s_pts.append(path.pos(ts))
    return idx, s_pts


def pursuit_pairs(samples, path, lat):
    """(gaze, ball position) training pairs for an assumed eye lag `lat`,
    dropping frames outside the motion and near direction changes."""
    idx, s_pts = pursuit_keep(samples, path, lat)
    return [samples[i][0] for i in idx], s_pts


def best_latency_fit(samples, path, reach_margin=0.0, poly2=False,
                     candidates=(0.06, 0.09, 0.12, 0.15, 0.18, 0.22, 0.26, 0.30),
                     min_samples=60):
    """Pursuit eye-lag varies per person; the wrong assumed lag pairs gaze
    with the wrong ball positions and inflates every residual. Scan candidate
    latencies, fit each pairing, keep the best.

    samples: list of (gaze_features, t_seconds_from_motion_start).
    Returns (mapper, mean_error_px, latency) or (None, None, None)."""
    from .mapping import fit_with_rejection
    best = (None, None, None)
    for lat in candidates:
        g_pts, s_pts = pursuit_pairs(samples, path, lat)
        if len(g_pts) < min_samples:
            continue
        mapper, err = fit_with_rejection(g_pts, s_pts,
                                         reach_margin=reach_margin, poly2=poly2)
        if mapper is not None and (best[1] is None or err < best[1]):
            best = (mapper, err, lat)
    return best
