# gaze/eye_gaze/limiter.py
"""Cursor speed limiter.

Raw gaze mapping teleports the cursor on every saccade, which feels wildly
fast and makes small targets impossible. Capping speed (px/s) turns jumps
into a controlled glide toward where the user is looking: large moves still
arrive quickly, but jitter and overshoot near the target shrink dramatically.
"""
import math

# dt is capped so a pause/freeze doesn't grant a huge one-frame budget.
_MAX_DT = 0.1


class SpeedLimiter:
    def __init__(self, max_speed):
        self.max_speed = float(max_speed)  # px/s; <= 0 disables limiting
        self._pos = None
        self._t = None

    def reset(self):
        self._pos = None
        self._t = None

    def update(self, x, y, t):
        """Return the position the cursor may actually move to at time t."""
        if self.max_speed <= 0 or self._pos is None:
            self._pos, self._t = (x, y), t
            return (x, y)
        dt = min(max(t - self._t, 1e-3), _MAX_DT)
        budget = self.max_speed * dt
        dx, dy = x - self._pos[0], y - self._pos[1]
        dist = math.hypot(dx, dy)
        if dist > budget:
            k = budget / dist
            x = self._pos[0] + dx * k
            y = self._pos[1] + dy * k
        self._pos, self._t = (x, y), t
        return (x, y)
