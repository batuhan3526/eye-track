# gaze/tests/test_hotkeys.py

from eye_gaze.hotkeys import DoublePress, Hotkeys


def _use_keyboard(monkeypatch, kb):
    """Stand in for the platform keyboard API (the `keyboard` module on
    Windows, the backend's on Linux). kb=None: no keyboard library at all."""
    from eye_gaze import platform

    def missing():
        raise ImportError("keyboard")
    monkeypatch.setattr(platform, "keyboard_api",
                        missing if kb is None else (lambda: kb))


def test_double_press_within_interval_triggers():
    dp = DoublePress(interval=1.0)
    assert dp.press(0.0) is False
    assert dp.press(0.5) is True


def test_two_far_presses_do_not_trigger():
    dp = DoublePress(interval=1.0)
    assert dp.press(0.0) is False
    assert dp.press(2.0) is False  # too late: counts as a new first press


def test_resets_after_trigger():
    dp = DoublePress(interval=1.0)
    dp.press(0.0)
    assert dp.press(0.3) is True
    # after a trigger the next press is a fresh first press, not a double
    assert dp.press(0.4) is False
    assert dp.press(0.6) is True


def test_exactly_at_interval_triggers():
    dp = DoublePress(interval=1.0)
    dp.press(0.0)
    assert dp.press(1.0) is True


def _window_hotkeys(monkeypatch, quit_key, recal_key, pause_key):
    # no keyboard API forces the cv2-window fallback path
    _use_keyboard(monkeypatch, None)
    return Hotkeys(quit_key, recal_key, pause_key, interval=1.0)


def test_window_mode_function_keys_fall_back_to_chars(monkeypatch):
    # cv2.waitKey can only ever report single characters, so "f10"-style
    # names must fall back to q/c/p — otherwise quitting is impossible.
    hk = _window_hotkeys(monkeypatch, "f10", "f9", "f8")
    assert hk.mode == "window"
    assert (hk.quit_char, hk.recal_char, hk.pause_char) == ("q", "c", "p")
    hk.feed_window_key(ord("q"))
    hk.feed_window_key(ord("q"))
    assert hk.quit_requested


def test_window_mode_single_char_keys_used_directly(monkeypatch):
    hk = _window_hotkeys(monkeypatch, "x", "r", "z")
    assert (hk.quit_char, hk.recal_char, hk.pause_char) == ("x", "r", "z")
    hk.feed_window_key(ord("r"))
    hk.feed_window_key(ord("r"))
    assert hk.take_recal()
    hk.feed_window_key(ord("z"))
    hk.feed_window_key(ord("z"))
    assert hk.paused


def test_drift_key_double_press_and_consume(monkeypatch):
    _use_keyboard(monkeypatch, None)
    hk = Hotkeys("f10", "f9", "f8", interval=1.0, drift_key="f7")
    assert hk.drift_char == "d"  # window-mode fallback for "f7"
    assert hk.take_drift() is False
    hk.feed_window_key(ord("d"))
    hk.feed_window_key(ord("d"))
    assert hk.take_drift() is True
    assert hk.take_drift() is False  # consumed


class _Ev:
    def __init__(self, kind):
        self.event_type = kind


class _FakeKb:
    """Records hook_key callbacks so events can be played through them."""

    def __init__(self):
        self.hooks = {}
        self.suppress = {}

    def hook_key(self, key, cb, suppress=False):
        self.hooks[key] = cb
        self.suppress[key] = suppress
        return lambda: None

    def on_press_key(self, key, cb, suppress=False):
        # the old registration path: fires on every key-down, repeats included
        return self.hook_key(key, lambda e: e.event_type == "up" or cb(e), suppress)

    def unhook_all(self):
        pass

    def ev(self, key, kind):
        return self.hooks[key](_Ev(kind))


def _global_hotkeys(monkeypatch, **kw):
    kb = _FakeKb()
    _use_keyboard(monkeypatch, kb)
    hk = Hotkeys("f10", "f9", "f8", interval=1.0, drift_key="f7", **kw)
    assert hk.mode == "global"
    return hk, kb


def test_holding_quit_key_does_not_quit(monkeypatch):
    # Windows auto-repeats a held key ~30x/s; each repeat used to count as a
    # press, so resting a finger on F10 for half a second ended the session.
    hk, kb = _global_hotkeys(monkeypatch)
    for _ in range(30):
        kb.ev("f10", "down")
    assert not hk.quit_requested
    kb.ev("f10", "up")
    kb.ev("f10", "down")
    assert hk.quit_requested            # a real second press still works


def test_holding_pause_key_does_not_toggle(monkeypatch):
    hk, kb = _global_hotkeys(monkeypatch)
    for _ in range(31):              # odd: old code ended on a toggle
        kb.ev("f8", "down")
    assert hk.paused is False


def test_holding_recal_key_does_not_start_calibration(monkeypatch):
    hk, kb = _global_hotkeys(monkeypatch)
    for _ in range(30):
        kb.ev("f9", "down")
    assert hk.take_recal() is False


def test_hotkeys_are_swallowed(monkeypatch):
    # F7 twice used to open Chrome's caret-browsing prompt behind the
    # drift-correction screen.
    hk, kb = _global_hotkeys(monkeypatch)
    assert kb.suppress["f7"] is True
    assert kb.ev("f7", "down") is False


def test_hotkeys_pass_through_when_not_suppressing(monkeypatch):
    hk, kb = _global_hotkeys(monkeypatch, suppress=False)
    assert kb.ev("f7", "down") is True


def _press(kb, key):
    kb.ev(key, "down")
    kb.ev(key, "up")


def test_recal_key_twice_during_a_recalibration_cancels_it(monkeypatch):
    """An accidental F9 x2 used to have two exits: a full ~45 s two-pass
    calibration, or F10 x2 - which quit with code 0, which BASLAT.bat does
    not restart: no cursor until a caregiver came."""
    hk, kb = _global_hotkeys(monkeypatch)
    hk.calibrating = True
    _press(kb, "f9")
    _press(kb, "f9")
    assert hk.quit_requested            # every calibration loop stops on it
    assert hk.take_recal() is False     # NOT a request for another one
    assert hk.end_calibration() is True
    assert hk.quit_requested is False and hk.calibrating is False


def test_quit_during_a_recalibration_cancels_instead_of_exiting(monkeypatch):
    hk, kb = _global_hotkeys(monkeypatch)
    hk.calibrating = True
    _press(kb, "f10")
    _press(kb, "f10")
    assert hk.end_calibration() is True
    assert hk.quit_requested is False
    _press(kb, "f10")
    _press(kb, "f10")
    assert hk.quit_requested            # outside a calibration it quits


def test_requests_latched_during_a_calibration_are_dropped(monkeypatch):
    hk, kb = _global_hotkeys(monkeypatch)
    hk.calibrating = True
    _press(kb, "f7")
    _press(kb, "f7")
    assert hk.end_calibration() is False   # finished normally
    assert hk.take_drift() is False and hk.take_recal() is False


def test_an_injected_hotkey_passes_to_the_app_and_does_nothing(monkeypatch):
    from eye_gaze import injected
    monkeypatch.setattr(injected, "enabled", True)
    hk, kb = _global_hotkeys(monkeypatch)

    class Inj(_Ev):
        injected = True
    for _ in range(2):
        assert kb.hooks["f10"](Inj("down")) is True
        kb.hooks["f10"](Inj("up"))
    assert not hk.quit_requested


def test_drift_key_while_paused_belongs_to_the_app(monkeypatch):
    hk, kb = _global_hotkeys(monkeypatch)
    hk.paused = True
    _press(kb, "f7")
    _press(kb, "f7")
    assert hk.take_drift() is False
    hk.paused = False
    _press(kb, "f7")
    _press(kb, "f7")
    assert hk.take_drift() is True


def test_recalibrate_key_while_paused_belongs_to_the_app(monkeypatch):
    """Seen in real use: F9 x2 while paused started a full two-pass
    calibration. Paused is caregiver mode - their F9 (Outlook, Word) must
    not start a tiring calibration the user did not ask for. Same rule as
    F7 above; the user un-pauses (F8 x2) first."""
    hk, kb = _global_hotkeys(monkeypatch)
    hk.paused = True
    _press(kb, "f9")
    _press(kb, "f9")
    assert hk.take_recal() is False
    hk.paused = False
    _press(kb, "f9")
    _press(kb, "f9")
    assert hk.take_recal() is True


def test_recalibrate_key_still_cancels_a_running_calibration(monkeypatch):
    hk, kb = _global_hotkeys(monkeypatch)
    hk.calibrating = True
    _press(kb, "f9")
    _press(kb, "f9")
    assert hk.cancel_requested


class _RecordingKeyboard:
    def __init__(self):
        self.hooked = []

    def hook_key(self, key, callback, suppress=False):
        self.hooked.append(key)
        return lambda: None


def test_deferred_hotkeys_hook_only_when_armed(monkeypatch):
    # Linux X11: the driver's keys are grabbed after the models load.
    kb = _RecordingKeyboard()
    _use_keyboard(monkeypatch, kb)
    hk = Hotkeys("f10", "f9", "f8", 1.0, drift_key="f7", defer=True)
    assert kb.hooked == [] and hk.mode == "window"
    hk.arm()
    hk.arm()                                    # idempotent
    assert kb.hooked == ["f10", "f9", "f8", "f7"]
    assert hk.mode == "global"


def test_hotkeys_hook_at_once_by_default(monkeypatch):
    kb = _RecordingKeyboard()
    _use_keyboard(monkeypatch, kb)
    hk = Hotkeys("f10", "f9", "f8", 1.0)
    assert kb.hooked == ["f10", "f9", "f8"] and hk.mode == "global"


def test_disarmed_hotkeys_unhook_and_arm_again(monkeypatch):
    # Linux X11 after a camera wait: unhooked while the models load,
    # hooked again after (gaze_driver.main, final review I-1).
    removed = []

    class Kb(_RecordingKeyboard):
        def hook_key(self, key, callback, suppress=False):
            self.hooked.append(key)
            return lambda: removed.append(key)
    kb = Kb()
    _use_keyboard(monkeypatch, kb)
    hk = Hotkeys("f10", "f9", "f8", 1.0, drift_key="f7", defer=True)
    hk.arm()
    hk.disarm()
    assert removed == ["f10", "f9", "f8", "f7"]
    hk.disarm()                                 # nothing left to remove
    assert len(removed) == 4
    hk.arm()
    assert kb.hooked == ["f10", "f9", "f8", "f7"] * 2
