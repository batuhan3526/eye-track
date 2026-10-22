# gaze/tests/test_linux_x11_watchdog.py
"""The X11 key watchdog's decisions, as pure functions with made-up
timestamps, and the driver's late grabs on X11. No X server: runs on
Windows too (the real-server tests are in test_linux_x11.py)."""
import inspect

import pytest

import gaze_driver as gd
from eye_gaze.platform import linux_x11 as lx

B = 0.33                      # the budget at Xorg's default 660 ms delay
P = lx._watchdog_period(B)


def _verdict(now, last, busy=None, poll=None, waiting=False):
    asked = []

    def events_waiting():
        asked.append(1)
        return waiting
    v = lx._watchdog_verdict(now, last, B, busy, poll, events_waiting)
    return v, bool(asked)


def test_budget_is_half_the_repeat_delay_capped_at_the_server_default():
    assert lx._freeze_budget(0.66) == pytest.approx(0.33)
    assert lx._freeze_budget(0.5) == pytest.approx(0.25)     # GNOME
    assert lx._freeze_budget(None) == pytest.approx(0.33)
    assert lx._freeze_budget(5.0) == pytest.approx(0.33)
    assert lx._freeze_budget(0.06) == lx._WATCHDOG_MIN == 0.05


def test_a_cut_always_comes_before_the_first_auto_repeat():
    for delay in (0.66, 0.5, 0.25, 0.2, 0.12):
        b = lx._freeze_budget(delay)
        assert b + lx._watchdog_period(b) < delay, delay


def test_idle_and_short_handling_are_ok():
    assert _verdict(10.0, 10.0 - P)[0] == "ok"
    assert _verdict(10.0, 10.0 - P, busy=10.0 - B + 0.01)[0] == "ok"


def test_a_key_event_handled_too_long_is_cut():
    assert _verdict(10.0, 10.0 - P, busy=10.0 - B - 0.01) == ("cut", False)


def test_a_slow_release_callback_is_cut_only_when_events_wait():
    assert _verdict(10.0, 10.0 - P, poll=9.0, waiting=False) == ("ok", True)
    assert _verdict(10.0, 10.0 - P, poll=9.0, waiting=True) == ("cut", True)
    # Under budget: the events are not even looked at.
    assert _verdict(10.0, 10.0 - P, poll=9.9, waiting=True) == ("ok", False)


def test_a_key_event_under_budget_still_lets_the_poll_path_decide():
    assert _verdict(10.0, 10.0 - P, busy=9.9, poll=9.0,
                    waiting=True) == ("cut", True)


def test_a_starved_watchdog_looks_again_instead_of_cutting():
    # The watchdog itself woke 0.5 s late: the whole process stalled (one C
    # call holding the GIL), the key thread with it. It may answer now.
    late = 10.0 - (P + B / 2.0) - 0.01
    assert _verdict(10.0, late, busy=9.0) == ("recheck", False)
    # One period later, still frozen: cut.
    assert _verdict(10.0 + P, 10.0, busy=9.0) == ("cut", False)
    # Just inside the slack it is not starvation.
    near = 10.0 - (P + B / 2.0) + 0.01
    assert _verdict(10.0, near, busy=9.0) == ("cut", False)


def test_stalls_in_a_row_never_become_cuts():
    # 14b review: each cut counts toward the permanent give-up (_REARM_MAX
    # re-arms in _REARM_WINDOW). A slow machine stalling again and again
    # must not lose its keys for good: the late wake of every new stall is
    # a recheck, also on the poll path, however many stalls come in a row.
    last = 10.0
    seen = lx._Rechecks()
    for k in range(1, 4 * lx._REARM_MAX):
        now = 10.0 + k * 0.5
        busy = now - 0.4                     # a new stall each time
        v = lx._watchdog_verdict(now, last, B, busy, None, lambda: False,
                                 seen.for_stall((busy, None)))
        seen.note(v)
        assert v == "recheck", k
        assert _verdict(now, last, poll=busy, waiting=True)[0] == "recheck", k
        last = now


@pytest.mark.parametrize("path", ["busy", "poll"])
def test_one_stall_gets_a_few_rechecks_then_it_is_cut(path):
    # Final review M-1: a callback that really blocks while the watchdog
    # keeps being starved (back-to-back GIL holders) used to be rechecked
    # for ever. One stall: _MAX_RECHECKS looks, then the cut.
    assert lx._MAX_RECHECKS == 3
    seen = lx._Rechecks()
    stall = (9.0, None) if path == "busy" else (None, 9.0)
    last, verdicts = 10.0, []
    for k in range(1, 6):
        now = 10.0 + k * 0.5                 # every wake is late
        v = lx._watchdog_verdict(now, last, B, stall[0], stall[1],
                                 lambda: True, seen.for_stall(stall))
        seen.note(v)
        verdicts.append(v)
        last = now
        if v == "cut":
            break
    assert verdicts == ["recheck"] * 3 + ["cut"]
    # The next stall starts from zero again.
    assert seen.for_stall((12.9, None)) == 0


def test_the_first_check_has_no_previous_wake():
    assert _verdict(10.0, None, busy=9.0) == ("cut", False)


def test_a_watchdog_cut_comes_back_sooner_than_a_death():
    assert lx._CUT_COOLDOWN < lx._REARM_COOLDOWN
    assert lx._CUT_COOLDOWN == 0.75


# --- the driver: on X11 the alarm is the only grab before the models ---------

def test_late_grabs_only_on_the_x11_backend(monkeypatch):
    class Backend:
        name = "x11"
    monkeypatch.setattr(gd._platform, "OS", "linux")
    monkeypatch.setattr(gd._platform, "linux_backend", lambda: Backend())
    assert gd._late_key_grabs() is True
    Backend.name = "evdev"
    assert gd._late_key_grabs() is False
    monkeypatch.setattr(gd._platform, "OS", "windows")
    Backend.name = "x11"
    assert gd._late_key_grabs() is False


def test_late_grabs_never_raise(monkeypatch):
    def boom():
        raise RuntimeError("no display")
    monkeypatch.setattr(gd._platform, "OS", "linux")
    monkeypatch.setattr(gd._platform, "linux_backend", boom)
    assert gd._late_key_grabs() is False


def test_the_click_switch_is_an_alarm_grab_only_with_a_switch_hold():
    assert gd._alarm_uses_switch({"alarm_switch_hold": 3,
                                  "left_click_key": "space"}) is True
    assert gd._alarm_uses_switch({"alarm_switch_hold": 0,
                                  "left_click_key": "space"}) is False
    assert gd._alarm_uses_switch({"alarm_switch_hold": 3,
                                  "left_click_key": ""}) is False
    assert gd._alarm_uses_switch({"alarm_switch_hold": "x"}) is False


def test_main_grabs_the_other_keys_after_the_models_on_x11():
    src = inspect.getsource(gd.main)
    assert "defer=late_keys" in src
    assert src.count("defer=late_keys") == 2          # Hotkeys and KeyPresses
    # The alarm still comes before the camera and the models...
    assert src.index("start_alarm(") < src.index("_ResilientCapture(")
    # ...the other keys only once the face and deep models are built.
    arm = src.index("_arm_late_keys()            # X11: the models are loaded")
    assert src.index("GazeEstimator(") < arm
    assert src.index("_make_deep(cfg, cap") < arm
    assert arm < src.index("run_calibration")
    # Waiting for a camera, the quit key must work: armed there too.
    wait = src.index("while not cap.isOpened():")
    assert src.index("_arm_late_keys()", src.index("if not cap.isOpened():")) \
        < wait


def test_a_shorter_repeat_delay_is_taken_live_and_said_once(monkeypatch,
                                                            capsys):
    kb = lx.X11Keyboard(":0")             # never connects in this test
    assert kb.freeze_budget == pytest.approx(0.33)
    delays = iter([0.5, 0.15, 0.4, 0.1])
    monkeypatch.setattr(lx, "_repeat_delay", lambda name: next(delays))
    kb._reread_repeat_delay()                    # 0.5 s: shorter, taken
    assert kb.freeze_budget == pytest.approx(0.25)
    assert capsys.readouterr().out == ""         # 0.5 s is not short
    kb._reread_repeat_delay()                    # 0.15 s: taken, said
    assert kb.freeze_budget == pytest.approx(0.075)
    out = capsys.readouterr().out
    assert out.count("[x11]") == 1 and "150 ms" in out, out
    out.encode("ascii")
    kb._reread_repeat_delay()                    # 0.4 s: longer, ignored
    assert kb.freeze_budget == pytest.approx(0.075)
    kb._reread_repeat_delay()                    # 0.1 s: taken, not said again
    assert kb.freeze_budget == pytest.approx(0.05)
    assert capsys.readouterr().out == ""


# --- final review I-1: a camera wait must not leave every key grabbed ------
# while the models load. main() is run with fakes up to the calibration and
# the set of hooked keys is read at each step.

class _Stop(Exception):
    pass


class _GrabKeyboard:
    """A keyboard backend that knows which keys hold a hook right now."""

    def __init__(self):
        self.active = set()

    def hook_key(self, name, callback, suppress=False):
        n = str(name).lower()
        self.active.add(n)
        return lambda: self.active.discard(n)

    def unhook_all(self):
        self.active.clear()

    def is_pressed(self, name):
        return False


class _LateCamera:
    """Not open at start (held by a video call); open after one read."""
    opened_at_start = False

    def __init__(self, opener):
        self._open = self.opened_at_start

    def isOpened(self):
        return self._open

    def read(self):
        self._open = True
        return False, None

    def get(self, prop):
        return 0.0

    def release(self):
        pass


def _run_main_until_calibration(monkeypatch, tmp_path, extra_cfg,
                                camera_late=True):
    from eye_gaze import alarm as alarm_mod
    from eye_gaze import injected
    kb = _GrabKeyboard()
    seen = {}
    (tmp_path / "config.yaml").write_text(
        "camera_index: 0\nquit_key: \"f10\"\nrecalibrate_key: \"f9\"\n"
        "left_click_key: \"space\"\nalarm_key: \"f12\"\n"
        "gamepad_enabled: false\ndeep_gaze: false\n" + extra_cfg,
        encoding="utf-8")
    monkeypatch.setattr(gd, "_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(gd, "_setup_logging", lambda: None)
    monkeypatch.setattr(gd.i18n, "setup", lambda cfg: None)
    monkeypatch.setattr(injected, "enabled", injected.enabled)
    monkeypatch.setattr(gd, "_late_key_grabs", lambda: True)
    monkeypatch.setattr(gd._platform, "keyboard_api", lambda: kb)

    class Alarm:
        def close(self):
            pass

    def start_alarm(cfg, keys=None):
        kb.hook_key(cfg["alarm_key"], lambda e: True, suppress=True)
        return Alarm()
    monkeypatch.setattr(alarm_mod, "start_alarm", start_alarm)

    class Clicker:
        gamepad = None

        def __init__(self, cfg, keys=None):
            pass

        def release(self):
            pass
    monkeypatch.setattr(gd, "_Clicker", Clicker)
    monkeypatch.setattr(gd, "_close_guard", lambda c: None)
    monkeypatch.setattr(gd, "_print_missing_capabilities", lambda: None)
    monkeypatch.setattr(gd, "_resolve_machine", lambda c: (c, None, None))
    monkeypatch.setattr(gd.cursor, "screen_size", lambda: (1920, 1080))
    monkeypatch.setattr(gd, "_primary_rect", lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(gd, "_gaze_bounds", lambda m, p: p)
    monkeypatch.setattr(gd, "_camera_opener", lambda *a, **k: None)
    cam = type("Cam", (_LateCamera,), {"opened_at_start": not camera_late})

    def capture(opener):
        c = cam(opener)
        seen.setdefault("wait", None)
        if not c.isOpened():
            # the wait loop runs read() once: look at the grabs there
            real_read = c.read

            def read():
                seen["wait"] = set(kb.active)
                return real_read()
            c.read = read
        return c
    monkeypatch.setattr(gd, "_ResilientCapture", capture)
    monkeypatch.setattr(gd, "_raise_process_priority", lambda: None)

    def estimator(**kw):
        seen["face_model"] = set(kb.active)
        return object()
    monkeypatch.setattr(gd, "GazeEstimator", estimator)
    monkeypatch.setattr(gd, "_keep_deep_for_calibration",
                        lambda cfg, *a, **k: cfg)

    def make_deep(cfg, cap, expected=None):
        seen["deep_model"] = set(kb.active)
        return None
    monkeypatch.setattr(gd, "_make_deep", make_deep)
    monkeypatch.setattr(gd, "_deep_report", lambda *a: [])
    monkeypatch.setattr(gd, "_FeatureSource", lambda *a, **k: object())
    monkeypatch.setattr(gd, "_deep_cpu_rebuilder", lambda *a: None)

    def positions(n):
        seen["after"] = set(kb.active)
        raise _Stop()
    monkeypatch.setattr(gd, "_cal_positions", positions)
    with pytest.raises(_Stop):
        gd.main()
    return seen


def test_after_a_camera_wait_only_the_alarm_is_grabbed_while_models_load(
        monkeypatch, tmp_path, capsys):
    seen = _run_main_until_calibration(monkeypatch, tmp_path, "")
    # Waiting: the quit key works (and the other keys with it).
    assert {"f12", "f10", "space"} <= seen["wait"]
    # Loading the face and the deep model: the alarm alone.
    assert seen["face_model"] == {"f12"}
    assert seen["deep_model"] == {"f12"}
    # Loaded: every key back.
    assert {"f12", "f10", "f9", "f8", "f7", "space", "f2"} <= seen["after"]
    out = capsys.readouterr().out
    out.encode("ascii")
    assert "yalniz acil alarm tusu bagli" in out


def test_the_click_switch_stays_grabbed_when_it_is_an_alarm(
        monkeypatch, tmp_path):
    seen = _run_main_until_calibration(monkeypatch, tmp_path,
                                       "alarm_switch_hold: 3\n")
    assert seen["face_model"] == {"f12", "space"}
    assert seen["deep_model"] == {"f12", "space"}
    assert {"f10", "space", "f2"} <= seen["after"]


def test_a_camera_open_at_once_loads_with_the_alarm_alone_as_before(
        monkeypatch, tmp_path):
    seen = _run_main_until_calibration(monkeypatch, tmp_path, "",
                                       camera_late=False)
    assert seen["wait"] is None
    assert seen["deep_model"] == {"f12"}
    assert {"f10", "space"} <= seen["after"]
