# gaze/eye_gaze/stabilizer.py
"""Fixation stabilizer ("rope" deadband).

While the user fixates, gaze estimates still jitter by a few px - enough to
make small targets shimmer and dwell-clicking fiddly. The stabilizer locks
the cursor while the target stays within deadzone_px of the locked point,
and once the target moves beyond it, the lock is dragged along like a rope
with slack: output trails the target by exactly the deadzone radius, so
there is no jump and no jitter, and fixations are rock solid.

The rope alone left the cursor exactly `deadzone` px SHORT of the target for
the whole fixation (it stops where the slack runs out). So once the target
has stayed inside the deadzone for a few frames, the lock is eased onto the
MEAN of those recent positions - the fixation point, not any one noisy
sample - and then holds still again (a small hysteresis keeps it calm).
"""
import math
from collections import deque


class FixationStabilizer:
    def __init__(self, deadzone_px=20, settle_frames=5, settle_eps=4.0,
                 settle_rate=0.25):
        self.deadzone = float(deadzone_px)
        self._settle_frames = int(settle_frames)
        self._settle_eps = float(settle_eps)
        self._settle_rate = float(settle_rate)
        self._inside = deque(maxlen=12)
        self._lock = None

    def reset(self):
        self._lock = None
        self._inside.clear()

    def update(self, x, y):
        if self.deadzone <= 0:
            return (x, y)
        if self._lock is None:
            self._lock = (float(x), float(y))
            return self._lock
        dx, dy = x - self._lock[0], y - self._lock[1]
        d = math.hypot(dx, dy)
        if d <= self.deadzone:
            self._inside.append((float(x), float(y)))
            if len(self._inside) >= self._settle_frames:
                mx = sum(p[0] for p in self._inside) / len(self._inside)
                my = sum(p[1] for p in self._inside) / len(self._inside)
                ex, ey = mx - self._lock[0], my - self._lock[1]
                if math.hypot(ex, ey) > self._settle_eps:
                    r = self._settle_rate
                    self._lock = (self._lock[0] + ex * r, self._lock[1] + ey * r)
            return self._lock
        self._inside.clear()
        k = (d - self.deadzone) / d
        self._lock = (self._lock[0] + dx * k, self._lock[1] + dy * k)
        return self._lock
