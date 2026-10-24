# gaze/eye_gaze/timebase.py
"""Seconds, not frames: the live loop's frame counts follow the real rate.

The cursor chain used to count in FRAMES - the live median window (7), the
stabilizer's settle (5 frames, 25 % per frame), the saccade detector's
confirm/anchor/rearm counts, the gaze-scroll period (every 4th frame). All of
them were tuned on a ~30 fps camera, so a 15 fps camera (dark room, slow CPU,
deep model on the CPU) made every one of them twice as slow, and a 60 fps
camera twice as nervous.

Each is now a duration (its old frame count at REF_FPS) converted back to
frames with the loop's MEASURED rate. At 30 fps every count is exactly what it
was; while the rate is still being measured (the first second or so) the
counts are the 30 fps ones.

FrameRate is the robust running estimate: the median of the recent frame
intervals, so one slow frame (a model load, a dialog) or a pause cannot move
it, and with hysteresis, so a rate wobbling around a boundary does not resize
the median window every frame.
"""
from collections import deque

REF_FPS = 30.0          # the rate every old frame count was tuned at

# The rate a frame count can be converted with. Below 5 fps the cursor is
# not usable anyway; above 120 the camera is lying about its timestamps.
MIN_FPS = 5.0
MAX_FPS = 120.0

SCROLL_PERIOD_S = 4.0 / REF_FPS   # gaze scroll: one notch every 4th frame at 30 fps


def frames(seconds, fps, minimum=1):
    """`seconds` as a whole number of frames at `fps` (at least `minimum`)."""
    return max(int(minimum), int(round(float(seconds) * float(fps))))


def ref_seconds(n_frames):
    """An old frame count as the duration it meant at REF_FPS."""
    return float(n_frames) / REF_FPS


def per_frame_rate(rate_at_ref, fps):
    """A per-frame easing fraction tuned at REF_FPS, at `fps` instead.

    Easing by r per frame for n frames leaves (1 - r)^n of the distance; the
    same remaining distance after the same TIME needs 1 - (1 - r)^(REF/fps).
    Exactly `rate_at_ref` at REF_FPS.
    """
    if fps == REF_FPS:
        return float(rate_at_ref)
    return 1.0 - (1.0 - float(rate_at_ref)) ** (REF_FPS / float(fps))


class FrameRate:
    """Median-of-intervals frame rate with hysteresis. Never raises.

    tick(now) once per loop frame; fps is REF_FPS until `min_samples`
    intervals were seen, then the median rate, which is only republished when
    it moved more than `hysteresis` (a fraction) from the published one.
    Intervals longer than `max_gap` (a pause, a camera hiccup, a modal
    dialog) are not frame intervals and are ignored.
    """

    def __init__(self, nominal=REF_FPS, window=60, min_samples=15,
                 hysteresis=0.1, max_gap=0.5):
        self.fps = self._clamp(nominal)
        self._dts = deque(maxlen=max(3, int(window)))
        self._min = max(1, int(min_samples))
        self._hyst = float(hysteresis)
        self._max_gap = float(max_gap)
        self._prev = None

    @staticmethod
    def _clamp(fps):
        try:
            fps = float(fps)
        except (TypeError, ValueError):
            return REF_FPS
        if fps != fps:
            return REF_FPS
        return min(MAX_FPS, max(MIN_FPS, fps))

    def measured(self):
        """The current median rate, or None before `min_samples` intervals."""
        if len(self._dts) < self._min:
            return None
        s = sorted(self._dts)
        mid = len(s) // 2
        med = s[mid] if len(s) % 2 else 0.5 * (s[mid - 1] + s[mid])
        return self._clamp(1.0 / med) if med > 0 else None

    def tick(self, now):
        """Feed one frame time; True when `fps` changed."""
        try:
            now = float(now)
            prev, self._prev = self._prev, now
            if prev is None or not 0.0 < now - prev <= self._max_gap:
                return False
            self._dts.append(now - prev)
            m = self.measured()
            if m is None or abs(m - self.fps) <= self._hyst * self.fps:
                return False
            self.fps = m
            return True
        except Exception:
            return False


class LoopTiming:
    """The live loop's frame counts, kept in step with a FrameRate.

    `median_seconds` is the live median window as a duration; `targets` are
    objects with set_frame_rate(fps) (the stabilizer, the saccade detector).
    tick(now) returns True when the counts changed; the caller then rebuilds
    its median history with resize(). Never raises.
    """

    def __init__(self, median_seconds, targets=(), rate=None, log=None):
        self.median_seconds = float(median_seconds)
        self.targets = [t for t in targets if t is not None]
        self.rate = rate if rate is not None else FrameRate()
        self._log = log
        self._apply(self.rate.fps)

    def _apply(self, fps):
        self.fps = float(fps)
        self.median_frames = frames(self.median_seconds, fps)
        self.scroll_frames = frames(SCROLL_PERIOD_S, fps)
        for t in self.targets:
            try:
                t.set_frame_rate(fps)
            except Exception:
                pass

    def tick(self, now):
        if not self.rate.tick(now):
            return False
        old = (self.median_frames, self.scroll_frames)
        try:
            self._apply(self.rate.fps)
        except Exception:
            return False
        if self._log is not None:
            try:
                self._log("Kare hizi %.0f fps: medyan %d kare (%.2f sn)."
                          % (self.fps, self.median_frames,
                             self.median_frames / self.fps))
            except Exception:
                pass
        return (self.median_frames, self.scroll_frames) != old

    def resize(self, hist):
        """`hist` as a deque of the current median length (newest kept)."""
        if hist.maxlen == self.median_frames:
            return hist
        return deque(hist, maxlen=self.median_frames)
