# gaze/eye_gaze/dwell.py
"""Dwell click: the cursor parked inside a small radius for dwell_seconds
fires a click. Hands-free alternative to the finger switch / long blink.

After firing, the cursor must leave the radius before it can fire again
(unless repeat=True, which refires every dwell_seconds while parked - useful
for pressing the same button repeatedly, e.g. a scroll arrow).
"""
import math


class DwellClicker:
    def __init__(self, radius_px=40, dwell_seconds=1.0, repeat=False):
        self.radius = float(radius_px)
        self.dwell = float(dwell_seconds)
        self.repeat = bool(repeat)
        self._anchor = None
        self._since = None
        self._fired = False
        # Read-only for the progress ring (eye_gaze.dwell_ring): where and
        # when the dweller was last fed, and how many clicks it has fired.
        self.last_t = None
        self.last_pos = None
        self.fires = 0
        self._restart = False

    # Longer than a blink (closed-eye frames are not fed), shorter than a
    # pause (F8 x2 twice), a calibration or a scan-grid session.
    MAX_GAP = 2.0

    def update(self, x, y, t):
        """Feed the current cursor position. Returns True when a dwell
        completes and a click should fire."""
        if self.last_t is not None and t - self.last_t > self.MAX_GAP:
            # Not fed for a while (paused, calibrating, scan grid open): the
            # time since the old anchor was not a dwell the user was doing.
            # Without this the first frame after a pause clicked at once.
            # Only the countdown restarts: a dwell that already fired keeps
            # _fired, so the gaze must still leave the radius before the
            # same spot can be clicked again.
            self._since = t
        if self._restart:
            self._restart = False
            self._since = t
        self.last_t = t
        self.last_pos = (x, y)
        if self._fire_check(x, y, t):
            self.fires += 1
            return True
        return False

    def restart(self):
        """The time since now is not a dwell: the eyes were closed or the face
        lost (those frames are not fed, and the 2 s MAX_GAP is far longer
        than a rest), or an overlay just opened/closed under a still cursor.
        Without this, fixate 0.3 s + rest the eyes 0.8 s clicked on the first
        open frame - with blink_click on, a second click after the blink's.
        Like MAX_GAP, only the countdown restarts; a fired dwell stays fired.
        """
        self._restart = True

    def _fire_check(self, x, y, t):
        if self._anchor is None or math.dist((x, y), self._anchor) > self.radius:
            self._anchor = (x, y)
            self._since = t
            self._fired = False
            return False
        if not self._fired and (t - self._since) >= self.dwell:
            if self.repeat:
                self._since = t
            else:
                self._fired = True
            return True
        return False

    def progress_at(self, t):
        """Fraction of the dwell completed at time t, in [0, 1]."""
        if self._since is None or self._fired or self.dwell <= 0:
            return 0.0
        return max(0.0, min(1.0, (t - self._since) / self.dwell))
