# gaze/eye_gaze/openness.py
"""Closed-eye threshold: one source for every consumer.

Eye openness (eye_gaze.gaze) is the lid gap over the eye width measured in
camera PIXELS. It used to be measured on MediaPipe's normalised coordinates
(x / frame width, y / frame height), so the same eye read 33% more open on a
16:9 picture than on a 4:3 one, and the fixed `blink_closed_threshold: 0.18`
meant something different on every camera mode. In pixels it is
(old value) x height / width.

`blink_closed_threshold: "auto"` makes the threshold a FRACTION of the
person's own open-eye openness: measured on the calibration's frames (kept in
the calibration file as `open_eye`), then tracked slowly while the program runs
(median of the open frames of the last minute). The calibration loops, the
cursor freeze, the dwell restart and the blink click all ask the same
ClosedEye object.

The fraction. "A typical open eye" is MediaPipe's canonical face model (the
geometry_pipeline_metadata mesh shipped in face_landmarker.task) seen from the
front: lid gap 159-145 / corners 33-133 (the same for 386-374 / 362-263) =
0.2586. On a 1280x960 camera that eye read 0.2586 / 0.75 = 0.345 on the old
normalised scale, where 0.18 was the factory threshold, i.e. 52% of it.
FRACTION = 0.18 x (960 / 1280) / 0.2586 = 0.522: on that camera "auto" decides
exactly like 0.18 did for the typical eye, and on any other camera, and for any
other eye, the threshold scales with the person instead.

An explicit number other than 0.18 (the v1.2.0 factory value, which counts as
"auto") was written on the old normalised scale; it is honoured, converted to
pixels with the aspect of the camera the calibration in use was made with (its
`camera` stamp; v1.2.0 files have it), else the current frame's.
"""
import math
import statistics
from collections import deque

FACTORY = 0.18                 # v1.2.0 factory value (normalised scale)
REF_ASPECT = 960.0 / 1280.0    # the camera the fraction is matched on (h / w)
CANONICAL_OPEN = 0.2586        # canonical face, frontal: lid gap / eye width
FRACTION = FACTORY * REF_ASPECT / CANONICAL_OPEN
# "auto" never goes outside a quarter / three quarters of the typical eye: a
# median learnt from a squint or a face half out of the picture cannot make
# every frame "closed" (cursor frozen) or none (blink click dead).
FLOOR = 0.25 * CANONICAL_OPEN
CEILING = 0.75 * CANONICAL_OPEN

WINDOW = 60.0         # s of open frames the live median looks back over
MIN_SPAN = 10.0       # s of them before the live median replaces the calibration's
MIN_FRAMES = 30
REFRESH = 1.0         # s between median recomputations
MIN_CAL = 10          # open frames a calibration needs for its own median


def _num(value):
    """value as a finite float, accepting a decimal comma; None otherwise."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse(setting):
    """The explicit threshold on the old normalised scale, or None for
    "auto" (also: missing, unreadable, or the v1.2.0 factory 0.18)."""
    if isinstance(setting, str) and setting.strip().lower() == "auto":
        return None
    f = _num(setting)
    if f is None or abs(f - FACTORY) < 1e-9:
        return None
    return f


def valid_open(value):
    """A stored open-eye openness, or None when it is not a plausible one."""
    f = _num(value)
    return f if f is not None and 0.0 < f < 5.0 else None


def open_median(values, threshold):
    """Median openness of the frames above threshold (the open ones), or
    None with fewer than MIN_CAL of them."""
    kept = [float(v) for v in values
            if _num(v) is not None and float(v) > threshold]
    return statistics.median(kept) if len(kept) >= MIN_CAL else None


def _aspect(size):
    try:
        w, h = float(size[0]), float(size[1])
    except (TypeError, ValueError, IndexError):
        return None
    return h / w if w > 0 and h > 0 else None


_UNSET = object()


class ClosedEye:
    """The closed-eye threshold in pixel-space openness. Never raises."""

    def __init__(self, setting="auto"):
        self._explicit = parse(setting)
        self._pixels = None        # a threshold given on the pixel scale
        self._mapper = _UNSET
        self._cal = None           # open-eye openness the calibration measured
        self._cal_aspect = None    # h / w of the camera it was made with
        self._frame_aspect = None
        self._win = deque(maxlen=int(WINDOW * 120))   # (t, openness)
        self._live = None          # median of the window, once it is enough
        self._next = None

    @classmethod
    def fixed(cls, value):
        """A threshold given directly on the pixel scale (tests, tools)."""
        eye = cls("auto")
        eye._pixels = float(value)
        return eye

    @property
    def auto(self):
        return self._explicit is None and self._pixels is None

    def use(self, mapper):
        """The calibration in use (its open_eye and camera stamp); cheap to
        call every frame."""
        if mapper is self._mapper:
            return
        self._mapper = mapper
        self._cal = valid_open(getattr(mapper, "open_eye", None))
        self._cal_aspect = _aspect(getattr(mapper, "camera", None))

    def set_frame(self, size):
        """(w, h) of the camera picture: the explicit value's fallback aspect."""
        a = _aspect(size)
        if a is not None:
            self._frame_aspect = a

    def observe(self, openness, now):
        """One frame with a face. Only open frames enter the window."""
        if not self.auto:
            return
        v, t = _num(openness), _num(now)
        if v is None or t is None:
            return
        if v > self.threshold():
            self._win.append((t, v))
        while self._win and self._win[0][0] < t - WINDOW:
            self._win.popleft()
        if self._next is None or t >= self._next or t < self._next - 2 * REFRESH:
            self._next = t + REFRESH
            w = self._win
            if len(w) >= MIN_FRAMES and w[-1][0] - w[0][0] >= MIN_SPAN:
                self._live = statistics.median(v for _, v in w)
            elif not w:
                self._live = None

    def open_eye(self):
        """The open-eye openness the auto threshold is a fraction of."""
        return self._live or self._cal or CANONICAL_OPEN

    def threshold(self):
        if self._pixels is not None:
            return self._pixels
        if self._explicit is not None:
            a = self._cal_aspect or self._frame_aspect or REF_ASPECT
            return self._explicit * a
        return min(max(FRACTION * self.open_eye(), FLOOR), CEILING)

    def describe(self):
        """One ASCII console line."""
        if self._pixels is not None:
            return "Goz kapali esigi: %.3f (kamera pikseli)" % self._pixels
        if self._explicit is not None:
            return ("Goz kapali esigi: %g (eski olcek) -> %.3f (kamera"
                    " pikseli)" % (self._explicit, self.threshold()))
        src = ("kalibrasyondan" if self._cal and not self._live else
               "canli olcumden" if self._live else "tipik goz, ogrenilecek")
        return ("Goz kapali esigi: otomatik, acik goz degerinin %%%d kadari"
                " (%s): %.3f" % (round(100 * FRACTION), src, self.threshold()))
