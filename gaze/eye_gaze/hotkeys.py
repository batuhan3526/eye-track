# gaze/eye_gaze/hotkeys.py
"""Global double-press hotkeys: quit, recalibrate, and pause/resume.

These must work even when the OpenCV window is NOT focused (e.g. while gaming or
typing). We use the `keyboard` library for a system-wide hook; if it is
unavailable we fall back to window-focused keys. A *double* press within a short
interval triggers the action, so a single press during normal typing/gaming is
ignored.
"""
from eye_gaze import injected
from eye_gaze import platform as _platform


class DoublePress:
    """Pure double-press detector. press(t) returns True when this press
    completes a double-press within `interval` seconds of the previous one."""

    def __init__(self, interval=1.0):
        self.interval = interval
        self._last = None

    def press(self, t):
        if self._last is not None and (t - self._last) <= self.interval:
            self._last = None
            return True
        self._last = t
        return False


class Hotkeys:
    """Tracks quit/recalibrate/pause via a global hook (preferred) or, as a
    fallback, via cv2 window keys fed through feed_window_key()."""

    def __init__(self, quit_key, recal_key, pause_key=None, interval=1.0,
                 drift_key=None, suppress=True, defer=False):
        import time
        self._time = time
        self.quit_requested = False
        self.recal_requested = False
        self.drift_requested = False
        self.paused = False
        self._quit_key = str(quit_key).lower()
        self._recal_key = str(recal_key).lower()
        self._pause_key = str(pause_key).lower() if pause_key else None
        self._drift_key = str(drift_key).lower() if drift_key else None
        # cv2.waitKey only reports single characters, so multi-char key names
        # ("f10") can never match in window mode; fall back to q/c/p/d there.
        self.quit_char = self._quit_key if len(self._quit_key) == 1 else "q"
        self.recal_char = self._recal_key if len(self._recal_key) == 1 else "c"
        self.pause_char = ((self._pause_key if len(self._pause_key) == 1 else "p")
                           if self._pause_key else None)
        self.drift_char = ((self._drift_key if len(self._drift_key) == 1 else "d")
                           if self._drift_key else None)
        self._quit_dp = DoublePress(interval)
        self._recal_dp = DoublePress(interval)
        self._pause_dp = DoublePress(interval)
        self._drift_dp = DoublePress(interval)
        self._keyboard = None
        self.mode = "window"
        # Keys currently held down. Windows auto-repeats a held key ~30x/s and
        # every repeat arrives as another key-down: counting those made a
        # finger resting on F10 quit, on F9 recalibrate, on F8 flicker pause.
        self._held = set()
        self._suppress = bool(suppress)
        # Live switch, flipped off while paused (caregiver mode) by the driver.
        self.suppressing = self._suppress
        # Set by the driver around an in-session F9/F7. The recalibrate key
        # pressed twice then CANCELS it: every calibration loop already stops
        # on quit_requested, and the driver turns that back into "keep the
        # old calibration" when cancel_requested is set.
        self.calibrating = False
        self.cancel_requested = False
        # Set by the driver while a finished calibration's result is on
        # screen. The recalibrate key pressed twice there asks for ANOTHER
        # calibration - the KOTU screen tells the user to do exactly that -
        # instead of cancelling the one just finished (kept; the save rules
        # decide about it) or being dropped as stale.
        self.result_showing = False
        self.again_requested = False
        self._armed = False
        self._removers = []
        if not defer:
            self.arm()

    def arm(self):
        """Hook the keys - done by the constructor unless defer=True (the
        driver's late grabs on Linux X11, see gaze_driver.main); until then
        only the window-key fallback works. Idempotent."""
        if self._armed:
            return
        self._armed = True
        try:
            keyboard = _platform.keyboard_api()
            injected.install()
            self._hook(keyboard, self._quit_key, self._fire_quit)
            self._hook(keyboard, self._recal_key, self._fire_recal)
            if self._pause_key:
                self._hook(keyboard, self._pause_key, self._fire_pause)
            if self._drift_key:
                self._hook(keyboard, self._drift_key, self._fire_drift)
            self._keyboard = keyboard
            self.mode = "global"
        except Exception:
            self._keyboard = None
            self.mode = "window"

    def disarm(self):
        """Drop the hooks arm() made, so a later arm() hooks them again.
        Linux X11 only: the driver holds no grab of these keys while its
        models load after a camera wait (gaze_driver.main). A remover that
        fails is ignored, as in close()."""
        removers, self._removers = self._removers, []
        for rm in removers:
            try:
                rm()
            except Exception:
                pass
        self._held.clear()
        self._armed = False

    def _hook(self, kb, key, fire):
        """One physical press -> one fire(); the key is swallowed (F7 must not
        also open Chrome's caret-browsing prompt, F10 the menu bar)."""
        def on_event(e):
            if injected.is_injected(e):
                return True     # OSK / OptiKey typing: the app's, not ours
            if getattr(e, "event_type", "") == "down":
                if key not in self._held:
                    self._held.add(key)
                    fire()
            else:
                self._held.discard(key)
            # The pause key itself is never let through: it is ours in both
            # modes, and a stray F8 has no use to the app behind.
            return not (self._suppress and (self.suppressing
                                            or key == self._pause_key))
        rm = kb.hook_key(key, on_event, suppress=self._suppress)
        if callable(rm):
            self._removers.append(rm)

    def _fire_quit(self):
        if self._quit_dp.press(self._time.time()):
            self.quit_requested = True

    def _fire_recal(self):
        if self._recal_dp.press(self._time.time()):
            if self.result_showing:
                self.again_requested = True
            elif self.calibrating:
                self.cancel_requested = True
                self.quit_requested = True
            elif not self.paused:
                # Paused, F9 belongs to the app (as F7 below): a caregiver's
                # F9 x2 started a full calibration (seen in real use).
                self.recal_requested = True

    def _fire_pause(self):
        if self._pause_dp.press(self._time.time()):
            self.paused = not self.paused

    def _fire_drift(self):
        if self._drift_dp.press(self._time.time()):
            # Paused, F7 reaches the app (Word's spell check): a caregiver's
            # F7 x2 measured "drift" while the user was not looking at the
            # dot, and the bogus offset was saved into the implicit field.
            if not self.paused:
                self.drift_requested = True

    def feed_window_key(self, cv2_key):
        """Window-focused fallback: feed the result of cv2.waitKey() each loop.
        No-op in global mode."""
        if self.mode != "window" or cv2_key is None or cv2_key in (255, -1):
            return
        if not (0 <= cv2_key < 256):
            return
        ch = chr(cv2_key).lower()
        if ch == self.quit_char:
            self._fire_quit()
        elif ch == self.recal_char:
            self._fire_recal()
        elif self.pause_char and ch == self.pause_char:
            self._fire_pause()
        elif self.drift_char and ch == self.drift_char:
            self._fire_drift()

    def take_recal(self):
        """Consume a pending recalibrate request."""
        if self.recal_requested:
            self.recal_requested = False
            return True
        return False

    def take_again(self):
        """Consume a recalibrate request made on a result screen."""
        if self.again_requested:
            self.again_requested = False
            return True
        return False

    def end_calibration(self):
        """After an in-session F9/F7: True when it was cancelled (by the
        recalibrate key or the quit key). Either way the driver keeps
        running - quitting there used to exit with code 0, which BASLAT.bat
        does not restart, leaving the user with no cursor. Requests latched
        during it are stale and dropped - except a recalibrate asked for on
        the result screen, which becomes the next recalibration."""
        self.calibrating = False
        self.result_showing = False
        cancelled = self.cancel_requested or self.quit_requested
        self.cancel_requested = False
        self.quit_requested = False
        self.recal_requested = self.take_again()
        self.drift_requested = False
        return cancelled

    def take_drift(self):
        """Consume a pending one-point drift-correction request."""
        if self.drift_requested:
            self.drift_requested = False
            return True
        return False

    def close(self):
        if self._keyboard is not None:
            try:
                self._keyboard.unhook_all()
            except Exception:
                pass
