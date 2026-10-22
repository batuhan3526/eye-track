# gaze/tests/test_feedback_driver.py
"""Driver side of the earcons and the stop/repeat key.

The click earcon hangs off _Clicker.fire(): the one place a finger-switch
press becomes a real mouse event. It must sound for a real click only (never
for a press an overlay consumed) and a failing sound must never cost the click.
"""
import inspect

import pytest

import gaze_driver as gd
from eye_gaze.switch import GAMEPAD_BUTTONS


class FakeKeys:
    def __init__(self):
        self.pending = []
        self.suppressing = True

    def press(self, key):
        self.pending.append(key)

    def take(self, key):
        if key in self.pending:
            self.pending.remove(key)
            return True
        return False

    def clear(self):
        self.pending = []


class FakePad:
    def __init__(self):
        self.mask = 0

    def buttons(self, index=0):
        return self.mask


class FakeOverlay:
    def __init__(self, active=False):
        self.active = active
        self.confirms = 0

    def tick(self, now):
        return False

    def confirm(self):
        self.confirms += 1
        self.active = False

    def cancel(self):
        self.active = False

    def begin(self):
        self.active = True


@pytest.fixture
def clicks(monkeypatch):
    log = []
    for name in ("left_click", "right_click", "double_click", "left_down",
                 "left_up", "escape"):
        monkeypatch.setattr(gd.actions, name,
                            lambda name=name: log.append(name))
    return log


CFG = {"left_click_key": "space", "right_click_key": "", "double_click_key": "",
       "drag_key": "", "gamepad_enabled": True, "gamepad_left": "A",
       "gamepad_right": "B", "gamepad_double": "", "gamepad_drag": ""}


def _setup():
    keys, pad = FakeKeys(), FakePad()
    return gd._Clicker(dict(CFG), keys=keys, gamepad=pad), keys, pad


def _frame(now, clicker, keys, grid, zoom):
    clicker.update(now)
    return gd._route_clicks(now, clicker, keys, grid, zoom, "f5", "f6")


def test_a_real_click_sounds_once_after_the_click(clicks):
    clicker, keys, _ = _setup()
    heard = []
    clicker.on_fire = lambda kinds: heard.append((sorted(kinds), list(clicks)))
    keys.press("space")
    _frame(0.0, clicker, keys, None, FakeOverlay())
    _frame(0.033, clicker, keys, None, FakeOverlay())
    assert clicks == ["left_click"]
    # Called once, and AFTER the click went out - a slow sound must not delay it.
    assert heard == [(["left"], ["left_click"])]


def test_nothing_pressed_means_no_sound(clicks):
    clicker, keys, _ = _setup()
    heard = []
    clicker.on_fire = heard.append
    for i in range(5):
        _frame(i * 0.033, clicker, keys, None, FakeOverlay())
    assert heard == []


def test_a_press_an_overlay_consumed_is_not_a_click_sound(clicks):
    clicker, keys, pad = _setup()
    heard = []
    clicker.on_fire = heard.append
    grid = FakeOverlay(active=True)
    pad.mask = GAMEPAD_BUTTONS["A"]
    _frame(0.0, clicker, keys, grid, FakeOverlay())
    assert grid.confirms == 1
    assert heard == [] and clicks == []


def test_a_broken_sound_never_costs_the_click(clicks):
    clicker, keys, _ = _setup()

    def boom(kinds):
        raise RuntimeError("ses yok")
    clicker.on_fire = boom
    keys.press("space")
    _frame(0.0, clicker, keys, None, FakeOverlay())     # must not raise
    assert clicks == ["left_click"]


def test_clicker_without_a_hook_still_works(clicks):
    clicker, keys, _ = _setup()
    assert clicker.on_fire is None
    keys.press("space")
    _frame(0.0, clicker, keys, None, FakeOverlay())
    assert clicks == ["left_click"]


def test_main_wires_every_piece():
    """Each part is tested on its own above and in test_feedback.py; this
    pins that main() actually connects them (an integrator merging seven
    branches into this function could silently drop one line)."""
    src = inspect.getsource(gd.main)
    for needle in ("build_feedback(", "feedback.update(", "build_speech_control(",
                   "speech_ctl.poll(", "lead_in_seconds=feedback.capture_lead_in()",
                   "clicker.on_fire", 'feedback.event("cal_done")',
                   'feedback.event("error")', "feedback.close()"):
        assert needle in src, needle
