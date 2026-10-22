"""The single-switch click-type menu (F2), tested without a screen.

The user aims with the eyes and has ONE switch. To right-click, double-click,
drag or scroll they open this menu, look at the option, press the switch - and
the action lands where they were looking when the menu OPENED, not on the menu
tile. Getting that point wrong, firing twice, or leaving a button held are all
things this user cannot undo by reaching for the mouse.
"""
import pytest

from eye_gaze import click_menu as cm
from eye_gaze.aac import cell_at


# --- layout ---------------------------------------------------------------

SCREENS = [(1280, 720), (1920, 1080), (1366, 768), (2560, 1440)]


def _inside(rect, sw, sh):
    x, y, w, h = rect
    return x >= 0 and y >= 0 and x + w <= sw and y + h <= sh


@pytest.mark.parametrize("sw,sh", SCREENS)
@pytest.mark.parametrize("fx,fy", [(0.5, 0.5), (0.02, 0.02), (0.98, 0.98),
                                   (0.02, 0.98), (0.98, 0.02), (0.3, 0.7),
                                   (0.7, 0.3)])
def test_menu_never_covers_the_target_and_stays_on_screen(sw, sh, fx, fy):
    tx, ty = int(sw * fx), int(sh * fy)
    panel, rects = cm.layout(tx, ty, sw, sh)
    assert len(rects) == len(cm.ITEMS)
    assert _inside(panel, sw, sh)
    for r in rects:
        assert _inside(r, sw, sh)
    px, py, pw, ph = panel
    # The target and a ring around it stay visible: the user must SEE where
    # the action is going to land.
    assert not (px - 30 <= tx < px + pw + 30 and py - 30 <= ty < py + ph + 30)
    assert cell_at(tx, ty, rects) is None


@pytest.mark.parametrize("sw,sh", SCREENS)
def test_targets_are_big_enough_for_a_100px_gaze_error(sw, sh):
    # Measured calibration error is 100-134 px; tiny targets are guesswork.
    _, rects = cm.layout(sw // 2, sh // 2, sw, sh)
    for (_, _, w, h) in rects:
        assert w >= 180
        assert h >= 110


def test_tiles_do_not_overlap():
    _, rects = cm.layout(640, 360, 1280, 720)
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            ax, ay, aw, ah = a
            bx, by, bw, bh = b
            assert (ax + aw <= bx or bx + bw <= ax or ay + ah <= by
                    or by + bh <= ay)


def test_menu_opens_on_the_side_with_more_room():
    panel, _ = cm.layout(200, 360, 1280, 720)
    assert panel[0] > 200
    panel, _ = cm.layout(1100, 360, 1280, 720)
    assert panel[0] + panel[2] < 1100


def test_items_and_labels():
    assert cm.ITEMS == ("left", "right", "double", "drag", "scroll_up",
                        "scroll_down", "cancel")
    assert cm.item_label("left") == "Sol tik"
    assert cm.item_label("right") == "Sag tik"
    assert cm.item_label("double") == "Cift tik"
    assert cm.item_label("drag", dragging=False) == "Surukle baslat"
    assert cm.item_label("drag", dragging=True) == "Surukle birak"
    assert cm.item_label("scroll_up") == "Kaydir yukari"
    assert cm.item_label("scroll_down") == "Kaydir asagi"
    assert cm.item_label("cancel") == "Iptal"
    for k in cm.ITEMS:                      # drawn with Hershey: ASCII only
        cm.item_label(k).encode("ascii")


def test_render_draws_a_full_screen_image_with_or_without_screenshot():
    import numpy as np
    panel, rects = cm.layout(300, 300, 1280, 720)
    labels = [cm.item_label(k) for k in cm.ITEMS]
    img = cm.render(None, 1280, 720, panel, rects, labels, (300, 300))
    assert img.shape == (720, 1280, 3)
    shot = np.full((720, 1280, 3), 200, dtype=np.uint8)
    img2 = cm.render(shot, 1280, 720, panel, rects, labels, (300, 300))
    assert img2.shape == (720, 1280, 3)
    # Background outside the panel is dimmed, so the menu stands out.
    assert img2[700, 5].max() < 200
    # A screenshot of the wrong size must not crash the menu.
    bad = np.zeros((10, 10, 3), dtype=np.uint8)
    assert cm.render(bad, 1280, 720, panel, rects, labels,
                     (300, 300)).shape == (720, 1280, 3)


# --- hold gate -------------------------------------------------------------

def test_short_press_is_one_click_at_the_press_point():
    g = cm.HoldGate(0.6)
    assert g.update(0.0, True, True, (100, 100)) == []
    assert g.update(0.1, False, True, (105, 100)) == []
    assert g.update(0.2, False, False, (120, 100)) == [("click", (100, 100))]
    assert g.update(0.3, False, False, (120, 100)) == []


def test_tap_shorter_than_a_frame_still_clicks():
    g = cm.HoldGate(0.6)
    assert g.update(0.0, True, False, (50, 60)) == [("click", (50, 60))]


def test_long_hold_opens_the_menu_once_and_the_release_does_nothing():
    g = cm.HoldGate(0.6)
    g.update(0.0, True, True, (100, 100))
    assert g.update(0.3, False, True, (300, 300)) == []
    assert g.update(0.65, False, True, (300, 300)) == [("menu", (100, 100))]
    assert g.update(0.9, False, True, (300, 300)) == []
    assert g.update(1.0, False, False, (300, 300)) == []
    assert g.update(1.1, False, False, (300, 300)) == []


def test_release_and_repress_between_frames_counts_both():
    g = cm.HoldGate(0.6)
    g.update(0.0, True, True, (1, 1))
    assert g.update(0.03, True, True, (2, 2)) == [("click", (1, 1))]
    assert g.update(0.06, False, False, (3, 3)) == [("click", (2, 2))]


def test_a_gap_in_updates_drops_a_half_finished_press():
    # Another overlay took over (or a calibration ran) while the switch was
    # down: the release seen afterwards must not click whatever is in front.
    g = cm.HoldGate(0.6)
    g.update(0.0, True, True, (1, 1))
    assert g.update(5.0, False, False, (9, 9)) == []


# --- the menu itself, with every side effect faked --------------------------

class FakeClicker:
    def __init__(self, log):
        self.log = log
        self.pending = set()
        self.dragging = False
        self.muted = 0
        self.held = False

    def take(self, kind):
        if kind in self.pending:
            self.pending.discard(kind)
            return True
        return False

    def discard(self):
        self.pending.clear()

    def mute(self, now, seconds=1.5):
        self.muted += 1
        self.pending.clear()

    def inject(self, kind):
        self.pending.add(kind)

    def fire(self):
        for k in sorted(self.pending):
            self.log.append(("fire", k))
            if k == "drag":
                self.dragging = not self.dragging
        self.pending.clear()

    def left_held(self):
        return self.held


class FakeKeys:
    def __init__(self):
        self.pending = []

    def press(self, key):
        self.pending.append(key)

    def take(self, key):
        if key in self.pending:
            self.pending.remove(key)
            return True
        return False


class FakeUI:
    def __init__(self, log):
        self.log = log
        self.shown = 0
        self.closed = 0
        self.fail = False

    def show(self, img):
        if self.fail:
            raise RuntimeError("imshow patladi")
        self.shown += 1

    def close(self):
        self.closed += 1
        self.log.append(("close",))


class Rig:
    def __init__(self, hold=0.0, timeout=10.0, key="f2", on_undo=None):
        self.log = []
        self.cur = [400, 300]
        self.clicker = FakeClicker(self.log)
        self.keys = FakeKeys()
        self.ui = FakeUI(self.log)
        self.printed = []
        self.menu = cm.ClickMenu(
            1280, 720, self.clicker, key=key, timeout=timeout, hold=hold,
            scroll_notches=5, ui=self.ui, grab=lambda: None,
            get_cursor=lambda: tuple(self.cur),
            set_cursor=lambda x, y: self.log.append(("move", x, y)),
            scroll=lambda amt: self.log.append(("scroll", amt)),
            log=self.printed.append, on_undo=on_undo)

    def look_at(self, kind):
        x, y, w, h = self.menu.rects[self.menu.items.index(kind)]
        self.cur[:] = [x + w // 2, y + h // 2]

    def route(self, now):
        return self.menu.route(now, self.keys, ("f5", "f6"))


def test_menu_key_opens_the_menu_at_the_gaze_point():
    r = Rig()
    r.keys.press("f2")
    assert r.route(0.0) is True
    assert r.menu.active
    assert r.menu.target == (400, 300)
    assert r.ui.shown >= 1
    assert r.log == []                  # opening does nothing to the app


@pytest.mark.parametrize("kind", ["left", "right", "double", "drag"])
def test_choice_is_applied_at_the_target_not_on_the_tile(kind):
    r = Rig()
    r.keys.press("f2")
    r.route(0.0)
    r.look_at(kind)
    r.clicker.pending.add("left")       # the switch press that chooses
    assert r.route(0.1) is True
    assert not r.menu.active
    # Window closed FIRST, then the cursor goes back to the target, then ONE
    # action - and never a left click at the tile.
    assert r.log == [("close",), ("move", 400, 300), ("fire", kind)]


@pytest.mark.parametrize("kind,amount", [("scroll_up", 600),
                                         ("scroll_down", -600)])
def test_scroll_choices_scroll_at_the_target(kind, amount):
    r = Rig()
    r.keys.press("f2")
    r.route(0.0)
    r.look_at(kind)
    r.clicker.pending.add("left")
    r.route(0.1)
    assert r.log == [("close",), ("move", 400, 300), ("scroll", amount)]


def test_cancel_tile_closes_without_any_action():
    r = Rig()
    r.keys.press("f2")
    r.route(0.0)
    r.look_at("cancel")
    r.clicker.pending.add("left")
    r.route(0.1)
    assert not r.menu.active
    assert r.log == [("close",)]


def test_press_between_tiles_selects_nothing_and_keeps_the_menu():
    r = Rig()
    r.keys.press("f2")
    r.route(0.0)
    r.cur[:] = list(r.menu.target)      # looking back at the target itself
    r.clicker.pending.add("left")
    assert r.route(0.1) is True
    assert r.menu.active
    assert r.log == []


def test_menu_key_again_closes_it():
    r = Rig()
    r.keys.press("f2")
    r.route(0.0)
    r.keys.press("f2")
    assert r.route(0.1) is True
    assert not r.menu.active
    assert r.log == [("close",)]


def test_other_layer_keys_and_buttons_are_dropped_while_open():
    r = Rig()
    r.keys.press("f2")
    r.route(0.0)
    r.keys.press("f5")
    r.keys.press("f6")
    r.clicker.pending.update({"right", "drag"})
    r.route(0.1)
    assert r.keys.pending == []
    assert r.clicker.pending == set()
    assert r.log == []


def test_choice_does_not_also_fire_other_pending_buttons():
    r = Rig()
    r.keys.press("f2")
    r.route(0.0)
    r.look_at("double")
    r.clicker.pending.update({"left", "right"})
    r.route(0.1)
    assert [e for e in r.log if e[0] == "fire"] == [("fire", "double")]


def test_timeout_closes_and_mutes_the_next_press():
    r = Rig(timeout=10.0)
    r.keys.press("f2")
    r.route(0.0)
    assert r.route(5.0) is True and r.menu.active
    assert r.route(10.5) is True
    assert not r.menu.active
    assert r.clicker.muted == 1
    assert r.log == [("close",)]


def test_moving_between_tiles_keeps_the_menu_open():
    r = Rig(timeout=10.0)
    r.keys.press("f2")
    r.route(0.0)
    r.look_at("left")
    r.route(1.0)
    r.look_at("right")
    r.route(8.0)                        # reading = activity: clock restarts
    r.route(15.0)
    assert r.menu.active
    assert r.route(18.5) is True
    assert not r.menu.active


def test_seconds_left_counts_down():
    r = Rig(timeout=10.0)
    r.keys.press("f2")
    r.route(0.0)
    assert r.menu.seconds_left(7.2) == pytest.approx(2.8)


@pytest.mark.parametrize("sw,sh", SCREENS)
@pytest.mark.parametrize("fx,fy", [(0.5, 0.5), (0.0, 0.0), (0.999, 0.999),
                                   (0.01, 0.99), (0.99, 0.01)])
def test_countdown_is_always_on_screen(sw, sh, fx, fy):
    tx, ty = int(sw * fx), int(sh * fy)
    panel, _ = cm.layout(tx, ty, sw, sh)
    x, y = cm.countdown_origin((tx, ty), panel, sw, sh)
    assert 0 <= x and x + 250 <= sw
    assert 30 <= y <= sh - 10


def test_countdown_is_painted_without_error_near_a_corner():
    r = Rig(timeout=10.0)
    r.cur[:] = [1279, 719]
    r.keys.press("f2")
    r.route(0.0)
    shown = r.ui.shown
    assert r.route(7.0) is True
    assert r.ui.shown == shown + 1 and r.menu.active


def test_drag_label_follows_the_shared_drag_state():
    r = Rig()
    r.clicker.dragging = True
    r.keys.press("f2")
    r.route(0.0)
    assert r.menu.labels[cm.ITEMS.index("drag")] == "Surukle birak"


def test_drag_start_then_release_goes_through_the_clicker():
    r = Rig()
    for t0, expect in ((0.0, True), (1.0, False)):
        r.keys.press("f2")
        r.route(t0)
        r.look_at("drag")
        r.clicker.pending.add("left")
        r.route(t0 + 0.1)
        assert r.clicker.dragging is expect


def test_inactive_menu_leaves_presses_to_the_caller():
    r = Rig()
    r.clicker.pending.add("left")
    assert r.route(0.0) is False
    assert r.clicker.pending == {"left"}


def test_a_failing_window_closes_the_menu_and_never_raises():
    r = Rig()
    r.keys.press("f2")
    r.route(0.0)
    r.ui.fail = True
    r.clicker.pending.add("left")
    assert r.route(0.1) is True         # this one frame is swallowed
    assert not r.menu.active
    assert r.clicker.pending == set()   # no click leaks behind
    assert any("Tik menusu" in p for p in r.printed)
    r.ui.fail = False
    r.clicker.pending.add("left")
    assert r.route(0.2) is False        # normal clicking is back


def test_a_window_that_cannot_open_does_not_swallow_clicking():
    r = Rig()
    r.ui.fail = True
    r.keys.press("f2")
    assert r.route(0.0) is False
    assert not r.menu.active
    for p in r.printed:
        p.encode("ascii")


# --- hold-to-open, through route() -------------------------------------------

def test_hold_mode_short_press_clicks_once_where_it_was_pressed():
    r = Rig(hold=0.6)
    r.clicker.pending.add("left")
    r.clicker.held = True
    assert r.route(0.0) is False
    assert r.clicker.pending == set()   # not fired yet: might become a hold
    r.cur[:] = [450, 310]
    r.clicker.held = False
    assert r.route(0.15) is False
    assert r.log == [("move", 400, 300)]
    assert r.clicker.pending == {"left"}    # the caller's fire() clicks it


def test_hold_mode_long_press_opens_the_menu_without_clicking():
    r = Rig(hold=0.6)
    r.clicker.pending.add("left")
    r.clicker.held = True
    r.route(0.0)
    r.cur[:] = [700, 500]
    assert r.route(0.3) is False
    assert r.route(0.7) is True
    assert r.menu.active
    assert r.menu.target == (400, 300)
    r.clicker.held = False
    r.route(0.8)
    r.route(0.9)
    assert r.menu.active
    assert r.clicker.pending == set()
    assert r.log == []


def test_hold_mode_off_by_default():
    r = Rig()
    r.clicker.pending.add("left")
    r.clicker.held = True
    assert r.route(0.0) is False
    assert r.clicker.pending == {"left"}


# --- construction from config -------------------------------------------------

def test_from_config_disabled_when_no_key_and_no_hold():
    assert cm.from_config({"click_menu_key": "", "click_menu_on_hold": 0},
                          1280, 720, FakeClicker([]), log=lambda s: None) is None


def test_from_config_reads_settings():
    lines = []
    m = cm.from_config({"click_menu_key": "F2", "click_menu_on_hold": 0.8,
                        "click_menu_timeout": 12, "click_menu_scroll": 3},
                       1280, 720, FakeClicker([]), log=lines.append)
    assert m.key == "f2"
    assert m.hold == pytest.approx(0.8)
    assert m.timeout == pytest.approx(12)
    assert m.scroll_notches == 3
    assert lines and all(s.encode("ascii") for s in lines)


def test_from_config_defaults():
    m = cm.from_config({}, 1280, 720, FakeClicker([]), log=lambda s: None)
    assert m.key == "f2"
    assert m.hold == 0
    assert m.timeout == pytest.approx(10)
    assert m.scroll_notches == 5


def test_a_hold_too_short_to_tell_from_a_click_is_raised():
    m = cm.from_config({"click_menu_on_hold": 0.05}, 1280, 720,
                       FakeClicker([]), log=lambda s: None)
    assert m.hold >= 0.3


def test_from_config_never_raises_on_garbage():
    # Built at startup, outside the per-frame guard: a throw here would stop
    # the driver before the cursor ever moved.
    lines = []
    m = cm.from_config({"click_menu_key": "f2", "click_menu_timeout": "abc",
                        "click_menu_scroll": [1], "click_menu_on_hold": "x"},
                       1280, 720, FakeClicker([]), log=lines.append)
    assert m is not None
    assert m.timeout == pytest.approx(10)
    assert m.scroll_notches == 5
    assert m.hold == 0


# --- config.yaml settings -------------------------------------------------------

def test_click_menu_settings_are_known_with_defaults():
    from eye_gaze import config as c
    assert c.DEFAULTS["click_menu_key"] == "f2"
    assert c.DEFAULTS["click_menu_on_hold"] == 0
    assert c.DEFAULTS["click_menu_timeout"] == 10
    assert c.DEFAULTS["click_menu_scroll"] == 5
    for k in ("click_menu_key", "click_menu_on_hold", "click_menu_timeout",
              "click_menu_scroll"):
        assert k in c.KNOWN_KEYS


def test_click_menu_numbers_typed_the_turkish_way(monkeypatch, tmp_path):
    from eye_gaze import config as c
    monkeypatch.setattr(c, "_report", lambda m: None)
    p = tmp_path / "c.yaml"
    p.write_text("left_click_key: space\nclick_menu_on_hold: '0,8'\n"
                 "click_menu_timeout: '12'\nclick_menu_scroll: '3.0'\n",
                 encoding="utf-8")
    cfg = c.load_config(str(p))
    assert cfg["click_menu_on_hold"] == pytest.approx(0.8)
    assert cfg["click_menu_timeout"] == pytest.approx(12.0)
    assert cfg["click_menu_scroll"] == 3 and isinstance(
        cfg["click_menu_scroll"], int)


def test_shipped_config_enables_the_menu_on_f2(shipped_config):
    import yaml
    with open(shipped_config, "r", encoding="utf-8") as f:
        shipped = yaml.safe_load(f)
    assert shipped["click_menu_key"] == "f2"
    assert shipped["click_menu_on_hold"] == 0


def test_the_undo_item_is_only_offered_with_a_handler():
    assert "undo" not in cm.ITEMS
    assert cm.ITEMS_UNDO[-2:] == ("undo", "cancel")


def test_undo_and_cancel_get_full_rows_and_nothing_overlaps():
    sw, sh = 2560, 1440
    panel, rects = cm.layout(400, 700, sw, sh, items=cm.ITEMS_UNDO)
    assert len(rects) == len(cm.ITEMS_UNDO)
    wide = rects[0][2] * 2
    assert rects[-1][2] > wide and rects[-2][2] > wide
    for i, (x, y, w, h) in enumerate(rects):
        for x2, y2, w2, h2 in rects[i + 1:]:
            assert x + w <= x2 or x2 + w2 <= x or y + h <= y2 or y2 + h2 <= y
        assert 0 <= x and x + w <= sw and 0 <= y and y + h <= sh


def test_choosing_undo_calls_the_driver_and_touches_nothing_else():
    undos = []
    r = Rig(on_undo=lambda: undos.append(1))
    r.keys.press("f2")
    r.route(0.0)
    r.look_at("undo")
    r.clicker.pending.add("left")
    r.route(0.1)
    assert undos == [1]
    assert not r.menu.active
    assert [e for e in r.log if e[0] != "close"] == []   # no move, no click
