"""Saccade-aware smoothing: jump on a saccade, smooth during fixation.

The cursor chain (median -> 1-Euro -> speed limiter -> fixation stabilizer)
is tuned for a calm cursor while the user FIXATES, and every stage of it
also slows down a big gaze jump: the median waits for half its window, the
1-Euro trails, the limiter glides at max_cursor_speed and the stabilizer
drags behind by its deadzone. On a 700 px jump that is most of a second.

The eye does not glide - it jumps (a saccade, < 100 ms) and then holds. So
the detector watches the RAW per-frame estimate (before any smoothing) and,
once the eye has demonstrably LANDED somewhere new, the smoothing state is
dropped: the cursor goes straight there and the filters then settle around
the new fixation exactly as they do today.

What counts as a saccade (all of them):
  * amplitude: the sample is >= min_px from the fixation anchor (the median
    of the last few accepted samples - one noisy frame cannot move it);
  * velocity: the eye covered that distance since the last accepted sample
    at >= velocity_px_s (slow drift and smooth pursuit stay with the filters);
  * confirmation: confirm_frames consecutive samples land in the SAME new
    place. A single-frame spike (glasses glint) returns to the anchor and is
    thrown away; lids closing before a blink drag the estimate steadily, so
    their samples never agree with each other;
  * no time gap: a gap > max_gap_s (blink frames are not fed, pause,
    recalibration) carries no velocity information - the next sample simply
    becomes the new anchor. Conservative on purpose: a jump that happens
    during a blink is left to the normal filters, as today.
Hysteresis: after firing, the detector re-arms only once the eye has settled
(rearm_frames samples within min_px/2 of the new anchor), so the landing
itself and a quick look back can never fire twice.

The frame counts were tuned at 30 fps; set_frame_rate() turns them into the
same durations at the loop's measured rate (exactly today's counts at 30 fps).
A confirmation always takes at least two samples, whatever the rate: one
sample is a glint, not a landing.

SaccadeJump is the driver glue. It never raises: a fault switches the
feature off for the session and the cursor carries on exactly as before.
"""
import math
import statistics
from collections import deque

from eye_gaze import timebase

DEFAULT_VELOCITY = 1500.0   # px/s (logical px - the driver is DPI-unaware)
DEFAULT_MIN_PX = 120.0

_FIX, _CAND, _SETTLE = "fix", "cand", "settle"


def _median_xy(pts):
    return (statistics.median(p[0] for p in pts),
            statistics.median(p[1] for p in pts))


def _mean_xy(pts):
    n = float(len(pts))
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _positive(value, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    if v != v or v <= 0 or v == float("inf"):
        return float(default)
    return v


class SaccadeDetector:
    def __init__(self, velocity_px_s=DEFAULT_VELOCITY, min_px=DEFAULT_MIN_PX,
                 confirm_frames=2, anchor_frames=5, rearm_frames=2,
                 max_gap_s=0.25):
        self.velocity = _positive(velocity_px_s, DEFAULT_VELOCITY)
        self.min_px = _positive(min_px, DEFAULT_MIN_PX)
        self.confirm_frames = max(1, int(confirm_frames))
        self.rearm_frames = max(1, int(rearm_frames))
        self.max_gap = _positive(max_gap_s, 0.25)
        self._fix = deque(maxlen=max(1, int(anchor_frames)))
        # The counts above mean frames at timebase.REF_FPS.
        self._base = (self.confirm_frames, self._fix.maxlen, self.rearm_frames)
        self.target = None          # where the eye landed (on the firing frame)
        self.reset()

    def set_frame_rate(self, fps):
        """Re-express confirm/anchor/rearm counts at `fps` (same durations)."""
        confirm, anchor, rearm = self._base
        fps = float(fps)
        if fps != timebase.REF_FPS:
            sec = timebase.ref_seconds
            confirm = timebase.frames(sec(confirm), fps, minimum=min(2, confirm))
            anchor = timebase.frames(sec(anchor), fps, minimum=min(3, anchor))
            rearm = timebase.frames(sec(rearm), fps)
        self.confirm_frames, self.rearm_frames = confirm, rearm
        if anchor != self._fix.maxlen:
            self._fix = deque(self._fix, maxlen=anchor)

    def reset(self):
        self._state = _FIX
        self._fix.clear()
        self._last_fix = None       # (x, y, t) of the last accepted sample
        self._prev_t = None
        self._cand = []
        self._cand_anchor = None
        self._calm = 0

    def _anchor_at(self, p, t):
        """Start over from p: no history, no velocity, nothing pending."""
        self._state = _FIX
        self._fix.clear()
        self._cand = []
        self._accept(p, t)

    def _accept(self, p, t):
        self._fix.append(p)
        self._last_fix = (p[0], p[1], t)

    def _onset(self, p, t, anchor):
        """p is far enough from anchor, and got there fast enough."""
        if _dist(p, anchor) < self.min_px:
            return False
        lx, ly, lt = self._last_fix
        dt = t - lt
        if dt <= 0 or dt > self.max_gap:
            return False
        return _dist(p, (lx, ly)) / dt >= self.velocity

    def update(self, x, y, t):
        """Feed one raw sample; True exactly on the frame a saccade is confirmed."""
        p = (float(x), float(y))
        t = float(t)
        prev_t, self._prev_t = self._prev_t, t
        if prev_t is None or self._last_fix is None or t - prev_t > self.max_gap \
                or t < prev_t:
            self._anchor_at(p, t)
            return False

        if self._state == _SETTLE:
            self._accept(p, t)
            if _dist(p, _median_xy(self._fix)) <= self.min_px / 2.0:
                self._calm += 1
                if self._calm >= self.rearm_frames:
                    self._state = _FIX
            else:
                self._calm = 0
            return False

        if self._state == _CAND:
            anchor = self._cand_anchor
            m = _mean_xy(self._cand)
            coh = max(self.min_px / 2.0, 0.25 * _dist(m, anchor))
            if _dist(p, anchor) >= self.min_px and _dist(p, m) <= coh:
                self._cand.append(p)
            elif self._onset(p, t, anchor):
                self._cand = [p]                # still moving: restart
            else:
                # Back at the anchor (a spike) or wandering: the candidate
                # samples were noise and never touch the anchor.
                self._state = _FIX
                self._cand = []
                self._accept(p, t)
                return False
            if len(self._cand) >= self.confirm_frames:
                return self._fire()
            return False

        anchor = _median_xy(self._fix)
        if self._onset(p, t, anchor):
            self._state = _CAND
            self._cand = [p]
            self._cand_anchor = anchor
            if len(self._cand) >= self.confirm_frames:
                return self._fire()
            return False
        self._accept(p, t)
        return False

    def _fire(self):
        landed = list(self._cand)
        self.target = _mean_xy(landed)
        self._state = _SETTLE
        self._calm = 0
        self._cand = []
        self._fix.clear()
        for q in landed:
            self._fix.append(q)
        lx, ly = landed[-1]
        self._last_fix = (lx, ly, self._prev_t)
        return True


class SaccadeJump:
    """Per-frame driver glue around a SaccadeDetector."""

    def __init__(self, detector, log=print):
        self.detector = detector
        self.enabled = True
        self.count = 0
        self._log = log

    def step(self, predict, g, drift, now, hist, filters):
        """One open-eye frame. predict(g) -> raw screen (x, y) before any
        smoothing; drift is added like the driver adds it. On a saccade the
        median history keeps only the landing samples and every filter is
        reset(). Returns True when it jumped; never raises."""
        if not self.enabled:
            return False
        try:
            x, y = predict(g)
            x = float(x) + float(drift[0])
            y = float(y) + float(drift[1])
            if not self.detector.update(x, y, now):
                return False
            keep = self.detector.confirm_frames
            while len(hist) > keep:
                hist.popleft()
            for f in filters:
                f.reset()
            self.count += 1
            return True
        except Exception as exc:
            self.enabled = False
            try:
                self._log("UYARI: sakkad atlama hata verdi ve bu oturum icin"
                          " KAPATILDI (" + type(exc).__name__ + ": "
                          + ascii(str(exc)) + "). Imlec eskisi gibi calisiyor.")
            except Exception:
                pass
            return False


def make_saccade_jump(cfg, log=print):
    """SaccadeJump from config, or None when saccade_jump is off (default).
    Never raises: unusable numbers fall back to the defaults."""
    try:
        if not cfg.get("saccade_jump", False) is True:
            return None
        det = SaccadeDetector(
            velocity_px_s=cfg.get("saccade_velocity_px_s", DEFAULT_VELOCITY),
            min_px=cfg.get("saccade_min_px", DEFAULT_MIN_PX))
        log("Sakkad atlama ACIK: hiz >= %.0f px/sn ve mesafe >= %.0f px olan"
            " bakis sicramalarinda imlec beklemeden yeni yere gecer."
            % (det.velocity, det.min_px))
        return SaccadeJump(det, log=log)
    except Exception as exc:
        try:
            log("UYARI: sakkad atlama baslatilamadi (" + ascii(str(exc))
                + "); imlec eskisi gibi calisiyor.")
        except Exception:
            pass
        return None
