"""The click menu as one more layer of the driver's input routing, with the
REAL _Clicker and _route_clicks: one press = one action, other layer keys are
dropped while it is open, and nothing leaks to the app behind."""
import pytest

import gaze_driver as gd
from eye_gaze import click_menu as cm
from eye_gaze.switch import GAMEPAD_BUTTONS


class FakeKeys:
    def __init__(self):
        self.pending = []
        self.down = set()
        self.suppressing = True

    def press(self, key):
        self.pending.append(key)

    def take(self, key):
        if key in self.pending:
            self.pending.remove(key)
            return True
        return False

    def held(self, key):
        return key in self.down

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
        self.begins = 0

    def tick(self, now):
        return False

    def confirm(self):
        self.confirms += 1
        self.active = False

    def cancel(self):
        self.active = False

    def begin(self):
        self.begins += 1
        self.active = True


class FakeUI:
    def show(self, img):
        pass

    def close(self):
        pass


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


class Rig:
    def __init__(self, clicks, hold=0.0):
        self.clicks = clicks
        self.keys, self.pad = FakeKeys(), FakePad()
        self.clicker = gd._Clicker(dict(CFG), keys=self.keys, gamepad=self.pad)
        self.grid, self.zoom = FakeOverlay(), FakeOverlay()
        self.cur = [400, 300]
        self.menu = cm.ClickMenu(
            1280, 720, self.clicker, key="f2", timeout=10, hold=hold,
            ui=FakeUI(), grab=lambda: None,
            get_cursor=lambda: tuple(self.cur),
            set_cursor=lambda x, y: clicks.append(("move", x, y)),
            scroll=lambda a: clicks.append(("scroll", a)),
            log=lambda s: None)

    def frame(self, now):
        self.clicker.update(now)
        return gd._route_clicks(now, self.clicker, self.keys, self.grid,
                                self.zoom, "f5", "f6", self.menu)

    def look_at(self, kind):
        x, y, w, h = self.menu.rects[cm.ITEMS.index(kind)]
        self.cur[:] = [x + w // 2, y + h // 2]


def test_right_click_from_the_menu_is_one_right_click_at_the_target(clicks):
    r = Rig(clicks)
    r.keys.press("f2")
    assert r.frame(0.0) is True
    r.look_at("right")
    r.keys.press("space")
    r.frame(0.1)
    r.frame(0.2)
    assert clicks == [("move", 400, 300), "right_click"]
    assert not r.menu.active


def test_held_gamepad_after_choosing_does_not_click_behind(clicks):
    r = Rig(clicks)
    r.keys.press("f2")
    r.frame(0.0)
    r.look_at("double")
    r.pad.mask = GAMEPAD_BUTTONS["A"]
    for i in range(5):
        r.frame(0.1 + i * 0.033)
    assert clicks == [("move", 400, 300), "double_click"]


def test_drag_started_from_the_menu_is_released_by_the_clicker(clicks):
    # The same drag state release() lets go of on pause, exit, console close.
    r = Rig(clicks)
    r.keys.press("f2")
    r.frame(0.0)
    r.look_at("drag")
    r.keys.press("space")
    r.frame(0.1)
    assert clicks[-1] == "left_down"
    assert r.clicker.dragging
    r.clicker.release()
    assert clicks[-1] == "left_up"


def test_aac_and_zoom_keys_are_dropped_while_the_menu_is_open(clicks):
    r = Rig(clicks)
    r.keys.press("f2")
    r.frame(0.0)
    r.keys.press("f5")
    r.keys.press("f6")
    r.frame(0.1)
    r.keys.press("f2")                  # close it
    r.frame(0.2)
    r.frame(0.3)
    assert r.grid.begins == 0 and r.zoom.begins == 0
    assert clicks == []


def test_menu_key_while_the_aac_grid_is_open_does_not_fire_later(clicks):
    r = Rig(clicks)
    r.grid.active = True
    r.keys.press("f2")
    r.frame(0.0)
    r.grid.active = False
    r.frame(0.1)
    assert not r.menu.active


def test_left_press_while_aac_is_open_goes_to_the_grid_not_the_menu(clicks):
    r = Rig(clicks, hold=0.6)
    r.grid.active = True
    r.keys.press("space")
    r.frame(0.0)
    assert r.grid.confirms == 1
    assert clicks == []


def test_hold_mode_short_press_is_exactly_one_click(clicks):
    r = Rig(clicks, hold=0.6)
    r.keys.press("space")
    r.keys.down.add("space")
    r.frame(0.0)
    r.frame(0.033)
    assert clicks == []
    r.keys.down.discard("space")
    r.frame(0.1)
    r.frame(0.2)
    assert clicks == [("move", 400, 300), "left_click"]


def test_hold_mode_long_gamepad_hold_opens_the_menu_and_never_clicks(clicks):
    r = Rig(clicks, hold=0.6)
    r.pad.mask = GAMEPAD_BUTTONS["A"]
    t = 0.0
    while t < 1.0:
        r.frame(t)
        t += 0.033
    assert r.menu.active
    r.pad.mask = 0
    r.frame(1.1)
    r.frame(1.2)
    assert clicks == []
    assert r.menu.active
    r.look_at("left")
    r.pad.mask = GAMEPAD_BUTTONS["A"]   # a NEW press chooses
    r.frame(1.3)
    assert clicks == [("move", 400, 300), "left_click"]


def test_without_a_menu_routing_is_unchanged(clicks):
    keys, pad = FakeKeys(), FakePad()
    clicker = gd._Clicker(dict(CFG), keys=keys, gamepad=pad)
    keys.press("space")
    clicker.update(0.0)
    assert gd._route_clicks(0.0, clicker, keys, FakeOverlay(), FakeOverlay(),
                            "f5", "f6") is False
    assert clicks == ["left_click"]


def test_clicker_reports_left_held_from_key_or_pad():
    keys, pad = FakeKeys(), FakePad()
    clicker = gd._Clicker(dict(CFG), keys=keys, gamepad=pad)
    clicker.update(0.0)
    assert clicker.left_held() is False
    keys.down.add("space")
    assert clicker.left_held() is True
    keys.down.clear()
    pad.mask = GAMEPAD_BUTTONS["A"]
    clicker.update(0.1)
    assert clicker.left_held() is True


def test_switch_bounce_after_choosing_does_not_click_behind(clicks):
    """The menu closes on the choice; the bounce surfaces a frame later, over
    the context menu the choice just opened at the target."""
    r = Rig(clicks)
    r.keys.press("f2")
    r.frame(0.0)
    r.look_at("right")
    r.keys.press("space")
    r.keys.press("space")               # the bounce
    r.frame(0.1)
    r.frame(0.133)
    r.frame(0.166)
    assert clicks == [("move", 400, 300), "right_click"]
    r.keys.press("space")               # a deliberate press later: works
    r.frame(2.0)
    assert clicks[-1] == "left_click"


def test_a_press_between_tiles_does_not_mute_the_menu(clicks):
    r = Rig(clicks)
    r.keys.press("f2")
    r.frame(0.0)
    r.cur[:] = [1, 1]                   # off every tile: chooses nothing
    r.keys.press("space")
    r.frame(0.1)
    assert r.menu.active
    r.look_at("double")
    r.keys.press("space")
    r.frame(0.2)
    assert clicks == [("move", 400, 300), "double_click"]


def test_a_forced_release_cancels_the_drag_before_letting_go(clicks):
    """Pause / an overlay / camera loss used to send a bare button-up: the
    dragged file was DROPPED wherever the eyes were. Esc first cancels it."""
    r = Rig(clicks)
    r.keys.press("f2")
    r.frame(0.0)
    r.look_at("drag")
    r.keys.press("space")
    r.frame(0.1)
    r.clicker.release()
    assert clicks[-2:] == ["escape", "left_up"]
    r.clicker.release()                       # nothing held: nothing sent
    assert clicks[-2:] == ["escape", "left_up"]
