# gaze/tests/test_input_routing.py
"""Where a finger-switch press goes: the AAC grid, the magnifier, or a real
mouse click. A press that lands in two of those places is a click the user
did not ask for and cannot undo."""
import pytest

import gaze_driver as gd
from eye_gaze.switch import GAMEPAD_BUTTONS


class FakeKeys:
    """KeyPresses stand-in: presses queued by the test, taken by the driver."""

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
        self.cancels = 0
        self.begins = 0
        self.auto_closed = False

    def tick(self, now):
        if self.auto_closed:
            self.active = False
            return True
        return False

    def confirm(self):
        self.confirms += 1
        self.active = False

    def cancel(self):
        self.cancels += 1
        self.active = False

    def begin(self):
        self.begins += 1
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
       "gamepad_right": "B", "gamepad_double": "Y", "gamepad_drag": "X"}


def _setup(**cfg):
    keys, pad = FakeKeys(), FakePad()
    c = dict(CFG)
    c.update(cfg)
    return gd._Clicker(c, keys=keys, gamepad=pad), keys, pad


def _frame(now, clicker, keys, grid, zoom):
    clicker.update(now)
    return gd._route_clicks(now, clicker, keys, grid, zoom, "f5", "f6")


def test_grid_confirm_with_held_gamepad_does_not_click_behind(clicks):
    # The reported bug: the grid closes on the press, the button is still down
    # one frame later, and that frame saw a "new" press -> real click.
    clicker, keys, pad = _setup()
    grid, zoom = FakeOverlay(active=True), FakeOverlay()
    pad.mask = GAMEPAD_BUTTONS["A"]
    _frame(0.00, clicker, keys, grid, zoom)
    _frame(0.033, clicker, keys, grid, zoom)
    _frame(0.066, clicker, keys, grid, zoom)
    assert grid.confirms == 1
    assert clicks == []


def test_zoom_confirm_with_key_does_not_click_twice(clicks):
    clicker, keys, pad = _setup()
    grid, zoom = FakeOverlay(), FakeOverlay(active=True)
    keys.press("space")
    _frame(0.00, clicker, keys, grid, zoom)
    _frame(0.033, clicker, keys, grid, zoom)
    assert zoom.confirms == 1
    assert clicks == []                 # zoom.confirm itself does the click


def test_normal_press_is_one_real_click(clicks):
    clicker, keys, pad = _setup()
    grid, zoom = FakeOverlay(), FakeOverlay()
    pad.mask = GAMEPAD_BUTTONS["A"]
    for i in range(5):
        _frame(i * 0.033, clicker, keys, grid, zoom)
    assert clicks == ["left_click"]


def test_press_right_after_auto_close_is_swallowed(clicks):
    # The grid timed out while the user was still reading; their next press
    # was meant for a phrase and must not click the app behind.
    clicker, keys, pad = _setup()
    grid, zoom = FakeOverlay(active=True), FakeOverlay()
    grid.auto_closed = True
    _frame(0.0, clicker, keys, grid, zoom)
    keys.press("space")
    _frame(0.5, clicker, keys, grid, zoom)
    assert clicks == []
    keys.press("space")
    _frame(3.0, clicker, keys, grid, zoom)      # well after: works again
    assert clicks == ["left_click"]


def test_other_buttons_do_nothing_while_overlay_open(clicks):
    clicker, keys, pad = _setup()
    grid, zoom = FakeOverlay(active=True), FakeOverlay()
    pad.mask = GAMEPAD_BUTTONS["B"] | GAMEPAD_BUTTONS["X"]
    _frame(0.0, clicker, keys, grid, zoom)
    grid.active = False
    _frame(0.033, clicker, keys, grid, zoom)    # still held after it closed
    assert clicks == []


def test_overlay_key_opens_grid_and_releases_drag(clicks):
    clicker, keys, pad = _setup()
    grid, zoom = FakeOverlay(), FakeOverlay()
    pad.mask = GAMEPAD_BUTTONS["X"]
    _frame(0.0, clicker, keys, grid, zoom)      # drag starts
    pad.mask = 0
    keys.press("f5")
    _frame(0.033, clicker, keys, grid, zoom)
    assert grid.begins == 1
    assert clicks == ["left_down", "escape", "left_up"]   # Esc cancels the drag


def test_click_during_drag_ends_the_drag_state(clicks):
    # A click is down+up: after it the button is up, so the drag flag must
    # not stay set and swallow the next drag press.
    clicker, keys, pad = _setup()
    grid, zoom = FakeOverlay(), FakeOverlay()
    pad.mask = GAMEPAD_BUTTONS["X"]
    _frame(0.0, clicker, keys, grid, zoom)
    pad.mask = 0
    _frame(0.033, clicker, keys, grid, zoom)
    keys.press("space")
    _frame(0.066, clicker, keys, grid, zoom)
    pad.mask = GAMEPAD_BUTTONS["X"]
    _frame(0.1, clicker, keys, grid, zoom)
    assert clicks == ["left_down", "left_click", "left_down"]


def test_disconnected_gamepad_is_not_polled_every_frame():
    class CountingPad(FakePad):
        def __init__(self):
            super().__init__()
            self.calls = 0
            self.connected = False

        def buttons(self, index=0):
            self.calls += 1
            return None if not self.connected else self.mask
    pad = CountingPad()
    clicker = gd._Clicker(dict(CFG), keys=FakeKeys(), gamepad=pad)
    for i in range(30):                 # one second of frames
        clicker.update(i / 30)
    assert pad.calls <= 2


def test_closing_the_console_releases_a_held_drag(clicks):
    # The X button does not raise KeyboardInterrupt: Windows calls a console
    # control handler and then kills the process, skipping every finally.
    clicker, keys, pad = _setup()
    pad.mask = GAMEPAD_BUTTONS["X"]
    clicker.update(0.0)
    clicker.fire()
    handler = gd._close_guard(clicker)
    assert handler(2) is False          # CTRL_CLOSE_EVENT: let Windows go on
    assert clicks == ["left_down", "escape", "left_up"]


def test_close_guard_without_a_drag_does_nothing(clicks):
    clicker, keys, pad = _setup()
    gd._close_guard(clicker)(2)
    assert clicks == []


def test_zoom_key_pressed_while_grid_open_does_not_fire_later(clicks):
    # Found by the whole-driver smoke run: F6 during the AAC grid was neither
    # used nor dropped, so the magnifier popped up whenever the grid closed.
    clicker, keys, pad = _setup()
    grid, zoom = FakeOverlay(active=True), FakeOverlay()
    keys.press("f6")
    _frame(0.0, clicker, keys, grid, zoom)
    grid.active = False
    _frame(0.033, clicker, keys, grid, zoom)
    assert zoom.begins == 0


def test_grid_key_pressed_while_zoom_open_does_not_fire_later(clicks):
    clicker, keys, pad = _setup()
    grid, zoom = FakeOverlay(), FakeOverlay(active=True)
    keys.press("f5")
    _frame(0.0, clicker, keys, grid, zoom)
    zoom.active = False
    _frame(0.033, clicker, keys, grid, zoom)
    assert grid.begins == 0


# --- a bouncing switch after a selection closed the overlay -----------------
#
# KeyPresses counts the bounce as a second press; _Clicker takes one per
# frame, so the bounce surfaces on the NEXT frame - when the grid (or the
# magnifier) the press was aimed at is already gone.

class StayOpenOverlay(FakeOverlay):
    """A selection that keeps the overlay open (a category, a scan step)."""

    def confirm(self):
        self.confirms += 1


@pytest.mark.parametrize("which", ["grid", "zoom"])
def test_switch_bounce_after_a_closing_selection_does_not_click_behind(
        clicks, which):
    clicker, keys, pad = _setup()
    grid, zoom = FakeOverlay(), FakeOverlay()
    (grid if which == "grid" else zoom).active = True
    keys.press("space")
    keys.press("space")                 # the bounce, ~40 ms later
    _frame(0.00, clicker, keys, grid, zoom)
    _frame(0.033, clicker, keys, grid, zoom)
    _frame(0.066, clicker, keys, grid, zoom)
    assert (grid.confirms, zoom.confirms) == ((1, 0) if which == "grid"
                                              else (0, 1))
    assert clicks == []
    keys.press("space")                 # a deliberate press later: works
    _frame(2.0, clicker, keys, grid, zoom)
    assert clicks == ["left_click"]


def test_a_selection_that_keeps_the_overlay_open_does_not_mute_it(clicks):
    clicker, keys, pad = _setup()
    grid, zoom = StayOpenOverlay(active=True), FakeOverlay()
    keys.press("space")
    _frame(0.00, clicker, keys, grid, zoom)
    keys.press("space")
    _frame(0.1, clicker, keys, grid, zoom)
    assert grid.confirms == 2
    assert clicks == []
