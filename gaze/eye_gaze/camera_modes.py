# gaze/eye_gaze/camera_modes.py
"""The camera picture's geometry, wherever it is used.

  aspect() / same_aspect()   width/height and the 2 % tolerance the probe
                             and the driver judge a delivered frame by;
  focal_px(width, hfov)      the focal length the deep model assumes;
  hfov_setting(value)        config.yaml's camera_hfov_deg read;
  window_size() / fit_to_window()   the status window: sized to the
                             picture's shape and letterboxed, so resizing it
                             never stretches the picture;
  ModeSwitch                 an existing calibration keeps the camera mode it
                             was made in; the probe's newer choice is opened
                             for a recalibration, and given back up when that
                             recalibration is cancelled.
"""
import math

ASPECT_TOL = 0.02       # 2 %: rounding (848x480 is 1.767, 16:9 is 1.778)

# The deep model's focal length has always been the image width (ptgaze's
# dummy camera), a horizontal field of view of 2*atan(0.5) = 53.13 deg.
# "auto" keeps exactly that: every deep calibration in the field was fitted
# on it.
AUTO_HFOV_DEG = 2.0 * math.degrees(math.atan(0.5))
HFOV_RANGE = (30.0, 150.0)  # degrees a webcam lens can have (fisheye incl.)

WINDOW_MAX = (800, 600)     # the status window's first size, at most
FIT_TOL = 0.005             # window this close to the picture's shape: as is


def aspect(w, h):
    return float(w) / float(h) if w and h else 0.0


def same_aspect(a, b, tol=ASPECT_TOL):
    """Whether two width/height ratios agree within tol (relative)."""
    return a > 0 and b > 0 and abs(a / b - 1.0) <= tol


# --- focal length -----------------------------------------------------------

def focal_px(width, hfov_deg=None):
    """Focal length in pixels for an image `width` wide: the width itself
    for "auto" (None), else from the horizontal field of view."""
    if hfov_deg is None:
        return width
    return (float(width) / 2.0) / math.tan(math.radians(float(hfov_deg)) / 2.0)


def hfov_setting(value, say=print):
    """camera_hfov_deg as degrees, or None for "auto" (also missing). A value
    no lens has is said once (ASCII) and treated as "auto"."""
    if value is None or (isinstance(value, str)
                         and value.strip().lower() == "auto"):
        return None
    try:
        if isinstance(value, bool):
            raise ValueError(value)
        v = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        say("UYARI: camera_hfov_deg: %s bir aci degil; \"auto\" kullaniliyor."
            % ascii(value))
        return None
    if not HFOV_RANGE[0] <= v <= HFOV_RANGE[1]:
        say("UYARI: camera_hfov_deg: %g derece olamaz (%g-%g); \"auto\""
            " kullaniliyor." % (v, HFOV_RANGE[0], HFOV_RANGE[1]))
        return None
    return v


# --- the status window -------------------------------------------------------

def window_size(fw, fh, max_size=WINDOW_MAX):
    """(w, h) of a window showing an fw x fh picture at its own shape, no
    larger than max_size and never enlarged."""
    if fw <= 0 or fh <= 0:
        return max_size
    s = min(1.0, max_size[0] / float(fw), max_size[1] / float(fh))
    return max(1, int(round(fw * s))), max(1, int(round(fh * s)))


def shown_size(win, fallback):
    """(w, h) of a highgui window's picture area as it is now (the user may
    have resized it), else `fallback`. Never raises."""
    try:
        import cv2
        _x, _y, w, h = cv2.getWindowImageRect(win)
        if w > 0 and h > 0:
            return int(w), int(h)
    except Exception:
        pass
    return fallback


def fit_to_window(frame, win_w, win_h):
    """`frame` scaled to fit win_w x win_h at its own aspect, centred on
    black: shown in a window of that size it is never stretched, however
    the window was resized. The frame itself when the window size is
    unknown."""
    if win_w <= 0 or win_h <= 0:
        return frame
    import cv2
    import numpy as np
    fh, fw = frame.shape[:2]
    if (fw, fh) == (win_w, win_h) or same_aspect(
            aspect(fw, fh), aspect(win_w, win_h), FIT_TOL):
        # The window already has the picture's shape (the size it was
        # given): highgui's own scaling cannot stretch it - no work here.
        return frame
    s = min(win_w / float(fw), win_h / float(fh))
    w = max(1, min(win_w, int(round(fw * s))))
    h = max(1, min(win_h, int(round(fh * s))))
    small = cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR)
    out = np.zeros((win_h, win_w) + frame.shape[2:], frame.dtype)
    x, y = (win_w - w) // 2, (win_h - h) // 2
    out[y:y + h, x:x + w] = small
    return out


# --- which mode a session runs in ---------------------------------------------

def _mode(rec):
    return (int(rec["width"]), int(rec["height"]), str(rec.get("fourcc") or ""))


class ModeSwitch:
    """The camera mode this session runs in, and the one a new calibration
    is made in.

    `record` is the session's camera record (makine.json's "camera", with
    the calibration's mode filled in by the driver; "choice" = the probe's
    newer mode, waiting for a recalibration). It is updated in place, so
    the calibration-camera notes always name the mode really open.
    make_opener(w, h, fourcc) -> the opener _ResilientCapture uses.
    Without a record (camera named in config.yaml) nothing ever switches.
    Never raises.
    """

    def __init__(self, cap, record, make_opener, say=print):
        self.cap = cap
        self.record = record if isinstance(record, dict) else None
        self.make_opener = make_opener
        self.say = say

    def _current(self):
        return _mode(self.record)

    def _choice(self):
        c = self.record.get("choice") if self.record else None
        if not isinstance(c, dict):
            return None
        try:
            return _mode(c)
        except Exception:
            return None

    def pending(self):
        try:
            c = self._choice()
            return c is not None and c != self._current()
        except Exception:
            return False

    def for_calibration(self):
        """Switch to the probe's choice before a calibration. The mode to go
        back() to when the calibration does not take, or None (nothing
        switched)."""
        if not self.pending():
            return None
        prev = self._current()
        return prev if self._switch(self._choice()) else None

    def back(self, prev):
        """Return to `prev` (for_calibration's result; None: nothing)."""
        if prev is not None and self.record is not None:
            self._switch(prev)

    def follow(self, size):
        """The calibration now in use was made at `size` (its camera stamp,
        e.g. after an undo): open that mode when the camera has it. True
        when the camera was switched."""
        if self.record is None or not size:
            return False
        try:
            from . import hardware
            if tuple(int(v) for v in size) == self._current()[:2]:
                return False
            m = hardware.mode_at(self.record.get("modes"), size)
            if m is not None:
                return self._switch(_mode(m))
        except Exception:
            pass
        return False

    def ensure_frames(self):
        """A mode opened with MJPG that has brought no frame (the caller
        waited for one): reopen it in the camera's own format, so the
        reconnects do not retry a dead format forever. Said once per case
        (ASCII). True when it switched."""
        try:
            if (self.record is None or not self.record.get("fourcc")
                    or getattr(self.cap, "frame_size", None)):
                return False
            w, h, fourcc = self._current()
            self.say("UYARI: kamera %dx%d %s ile goruntu vermedi; kendi"
                     " bicimiyle yeniden aciliyor." % (w, h, fourcc))
            choice = self.record.get("choice")
            if isinstance(choice, dict) and (choice.get("width"),
                                             choice.get("height")) == (w, h):
                choice["fourcc"] = ""   # the same dead format: not again
            return self._switch((w, h, ""))
        except Exception:
            return False

    def _switch(self, mode):
        try:
            old = self._current()
            w, h, fourcc = mode
            if self.cap.reopen(self.make_opener(w, h, fourcc)):
                self.record.update(width=w, height=h, fourcc=fourcc)
                self.say("Kamera %dx%d%s moduna gecti." % (
                    w, h, " " + fourcc if fourcc else ""))
                return True
            self.say("UYARI: kamera %dx%d modunda acilamadi; %dx%d ile devam."
                     % (w, h, old[0], old[1]))
            self.cap.reopen(self.make_opener(*old))
        except Exception as exc:
            self.say("UYARI: kamera modu degistirilemedi (%s)."
                     % type(exc).__name__)
        return False
