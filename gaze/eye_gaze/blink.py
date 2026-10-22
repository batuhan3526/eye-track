# gaze/eye_gaze/blink.py
"""Long-blink -> click detector.

A natural blink is brief; only eyes held closed for >= hold_seconds fires once.
This avoids false clicks from ordinary blinking while still letting a user with
no usable finger trigger clicks hands-free.
"""


class BlinkClicker:
    def __init__(self, closed_threshold=0.18, hold_seconds=0.5):
        self.closed_threshold = closed_threshold
        self.hold_seconds = hold_seconds
        self._closed_since = None
        self._fired = False

    def update(self, openness, t):
        """openness: eye-openness ratio (low = closed). Returns True exactly
        once, when a sustained closure reaches hold_seconds."""
        if openness <= self.closed_threshold:
            if self._closed_since is None:
                self._closed_since = t
                self._fired = False
            elif not self._fired and (t - self._closed_since) >= self.hold_seconds:
                self._fired = True
                return True
        else:
            self._closed_since = None
            self._fired = False
        return False
