# gaze/eye_gaze/pursuit.py
"""Smooth-pursuit calibration path.

Instead of 16 static dots, a ball glides across the screen at constant speed
and the eyes follow it. Every frame yields a (gaze, ball position) training
pair, so a ~25 second sweep collects hundreds of samples - far more data,
far less tiring than staring at dots.

The path is a boustrophedon (zigzag) over four rows plus a return to center,
so all edges and corners are covered. Samples near waypoints are dropped by
the caller via near_turn(): the eye saccades at direction changes and those
frames would pair the wrong positions.
"""
import bisect
import math

# screen-fraction waypoints: 4-row zigzag + center finish
_WAYPOINTS = [(0.06, 0.08), (0.94, 0.08),
              (0.94, 0.37), (0.06, 0.37),
              (0.06, 0.63), (0.94, 0.63),
              (0.94, 0.92), (0.06, 0.92),
              (0.5, 0.5)]


class PursuitPath:
    def __init__(self, sw, sh, speed=480.0, waypoints=None):
        wp = waypoints if waypoints is not None else _WAYPOINTS
        self._pts = [(fx * sw, fy * sh) for (fx, fy) in wp]
        self.speed = float(speed)
        self._cum = [0.0]
        for a, b in zip(self._pts, self._pts[1:]):
            self._cum.append(self._cum[-1] + math.dist(a, b))
        self.length = self._cum[-1]
        self.duration = self.length / self.speed

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

    def near_turn(self, t, guard=0.35):
        """True within `guard` seconds of any waypoint (incl. start/end),
        where the eye is saccading rather than smoothly pursuing."""
        d = max(0.0, min(t, self.duration)) * self.speed
        g = guard * self.speed
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
