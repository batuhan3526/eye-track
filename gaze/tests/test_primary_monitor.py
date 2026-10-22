# gaze/tests/test_primary_monitor.py
"""The primary monitor is not always at (0, 0) (Linux, ruling R15).

An X11 desktop with a monitor LEFT of the primary puts the primary at
(1920, 0). The driver used to assume (0, 0): the calibration window opened on
whichever monitor the window manager chose, the gaze was clamped to the
wrong rectangle and the overlays cut and mapped the wrong region. Fake
backend here: primary (1920, 0, 2560, 1440) + secondary (0, 180, 1920, 1080).
Windows keeps its behaviour: there the primary's top-left IS (0, 0).
"""
import inspect
import os

import numpy as np
import pytest

import gaze_driver as gd
from eye_gaze import click_menu as cm
from eye_gaze import cursor

PRIMARY = (1920, 0, 2560, 1440)
SECOND = (0, 180, 1920, 1080)


class Backend:
    name = "fake"

    def __init__(self):
        self.cur = (2000, 100)
        self.moved = []

    def primary(self):
        return PRIMARY

    def screens(self):
        return [SECOND, PRIMARY]

    def get_cursor(self):
        return self.cur

    def set_cursor(self, x, y):
        self.moved.append((x, y))
        self.cur = (x, y)


@pytest.fixture
def linux(monkeypatch):
    b = Backend()
    monkeypatch.setattr(cursor, "_user32", None)
    monkeypatch.setattr(cursor, "_backend", lambda: b)
    monkeypatch.setattr(gd._platform, "OS", "linux")
    monkeypatch.setattr(gd, "_SCREEN_ORIGIN", None)
    return b


class RecCv:
    WINDOW_NORMAL = 0
    WND_PROP_FULLSCREEN = 1
    WINDOW_FULLSCREEN = 1
    INTER_LINEAR = 1

    def __init__(self):
        self.calls = []

    def namedWindow(self, win, flag):
        self.calls.append(("named", win))

    def moveWindow(self, win, x, y):
        self.calls.append(("move", win, x, y))

    def setWindowProperty(self, win, prop, value):
        self.calls.append(("fullscreen", win))

    def imshow(self, *a):
        pass

    def resize(self, img, size, interpolation=None):
        return np.zeros((size[1], size[0], 3), np.uint8)

    def destroyWindow(self, *a):
        pass


def test_the_linux_primary_is_where_the_backend_says(linux):
    assert cursor.primary_rect() == PRIMARY
    assert gd._primary_rect() == PRIMARY
    assert cursor.screen_size() == (2560, 1440)


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_the_windows_primary_starts_at_zero():
    x, y, w, h = gd._primary_rect()
    assert (x, y) == (0, 0) and (w, h) == cursor.screen_size()
    assert cursor.primary_rect()[:2] == (0, 0)


def test_the_gaze_is_clamped_to_the_real_primary(linux):
    assert gd._gaze_bounds(False, PRIMARY) == PRIMARY
    # multi_monitor: the whole desktop, both monitors.
    assert gd._gaze_bounds(True, PRIMARY) == (0, 0, 4480, 1440)


def test_main_offsets_the_gaze_and_the_drift_by_the_primary_origin():
    src = inspect.getsource(gd.main)
    assert "bx, by, bw, bh = _gaze_bounds(multi_mon, primary)" in src
    assert "sx += ox + drift[0]" in src and "sy += oy + drift[1]" in src
    assert src.index("sx += ox + drift[0]") < src.index(
        "sx = min(max(sx, bx + margin)")
    assert "_SCREEN_ORIGIN = (ox, oy)" in src
    assert "origin=(ox, oy)" in src


def test_fullscreen_windows_move_onto_the_primary_first(linux, monkeypatch):
    cv = RecCv()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd, "_SCREEN_ORIGIN", (1920, 0))
    gd._fullscreen("Kalibrasyon")
    assert cv.calls == [("named", "Kalibrasyon"),
                        ("move", "Kalibrasyon", 1920, 0),
                        ("fullscreen", "Kalibrasyon")]


def test_windows_windows_are_not_moved(monkeypatch):
    cv = RecCv()
    monkeypatch.setattr(gd, "cv2", cv)
    monkeypatch.setattr(gd._platform, "OS", "windows")
    monkeypatch.setattr(gd, "_SCREEN_ORIGIN", (0, 0))
    gd._fullscreen("Kalibrasyon")
    assert [c[0] for c in cv.calls] == ["named", "fullscreen"]


def test_every_driver_fullscreen_window_goes_through_the_placement():
    src = inspect.getsource(gd)
    body = inspect.getsource(gd._fullscreen)
    assert src.count("WND_PROP_FULLSCREEN") == body.count("WND_PROP_FULLSCREEN")
    for fn in (gd.calibrate, gd.calibrate_pursuit, gd.measure_drift,
               gd._AacGrid.begin, gd._ZoomClick.begin):
        assert "_fullscreen(" in inspect.getsource(fn), fn.__name__


def test_the_click_menu_window_is_placed_before_fullscreen(monkeypatch):
    import cv2
    calls = []
    monkeypatch.setattr(cv2, "namedWindow", lambda w, f: calls.append("named"))
    monkeypatch.setattr(cv2, "setWindowProperty",
                        lambda *a: calls.append("fullscreen"))
    monkeypatch.setattr(cv2, "imshow", lambda *a: calls.append("show"))
    w = cm.CvWindow(place=lambda title: calls.append("place"))
    w.show(np.zeros((4, 4, 3), np.uint8))
    assert calls == ["named", "place", "fullscreen", "show"]


# --- the magnifier ---------------------------------------------------------

@pytest.fixture
def zoom(linux, monkeypatch):
    import eye_gaze.zoom as zm
    monkeypatch.setattr(gd, "cv2", RecCv())
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    grabs = []

    def grab(x, y, w, h):
        grabs.append((x, y, w, h))
        return np.zeros((h, w, 3), np.uint8)
    monkeypatch.setattr(zm, "grab_region", grab)
    clicks = []
    monkeypatch.setattr(gd.actions, "left_click", lambda: clicks.append(1))
    monkeypatch.setattr(gd, "_SCREEN_ORIGIN", (1920, 0))
    z = gd._ZoomClick(2560, 1440, 0.25, 8, False, origin=(1920, 0))
    return z, linux, grabs, clicks


def test_one_monitor_magnifier_cuts_from_the_primary(zoom):
    z, b, grabs, _ = zoom
    b.cur = (1925, 5)                    # top-left corner of the primary
    z.begin()
    assert grabs == [(1920, 0, 640, 360)]
    assert z._region == (1920, 0, 640, 360)


def test_a_magnifier_click_lands_on_the_primary(zoom):
    z, b, _, clicks = zoom
    b.cur = (1925, 5)
    z.begin()
    b.cur = (1920, 0)                    # top-left of the magnified view
    z.confirm()
    assert b.moved[-1] == (1920, 0) and clicks == [1]
    b.cur = (1925, 5)
    z.begin()
    b.cur = (1920 + 2559, 1439)          # bottom-right of the view
    z.confirm()
    assert b.moved[-1] == (1920 + 639, 359)


def test_windows_magnifier_is_unchanged(monkeypatch):
    """Origin (0, 0): the same region and point as before R15."""
    import eye_gaze.zoom as zm
    monkeypatch.setattr(gd, "cv2", RecCv())
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    monkeypatch.setattr(zm, "grab_region",
                        lambda x, y, w, h: np.zeros((h, w, 3), np.uint8))
    state = {"cur": (500, 300), "set": None}
    monkeypatch.setattr(gd.cursor, "get_cursor", lambda: state["cur"])
    monkeypatch.setattr(gd.cursor, "set_cursor",
                        lambda x, y, **k: state.__setitem__("set", (x, y)))
    monkeypatch.setattr(gd.actions, "left_click", lambda: None)
    z = gd._ZoomClick(1280, 720, 0.25, 8)
    z.begin()
    assert z._region == (340, 210, 320, 180)
    state["cur"] = (0, 0)
    z.confirm()
    assert state["set"] == (340, 210)


# --- the AAC grid ----------------------------------------------------------

def test_the_aac_grid_reads_the_pointer_in_its_own_pixels(linux):
    grid = gd._AacGrid.__new__(gd._AacGrid)
    grid._ox, grid._oy = 1920, 0
    linux.cur = (1920 + 100, 50)
    assert grid._pointer() == (100, 50)


def test_main_gives_the_overlays_the_primary_origin():
    src = inspect.getsource(gd.main)
    assert "_AacGrid(sw, sh, cfg, speaker, origin=(ox, oy))" in src
    assert "origin=(ox, oy), place=_place_on_primary)" in src
    zoom = src[src.index("zoomer = _ZoomClick("):]
    assert "origin=(ox, oy)" in zoom[:zoom.index(")\n")]


# --- the click menu --------------------------------------------------------

class Clicker:
    def __init__(self):
        self.fired = []
        self.pending = set()
        self.dragging = False

    def take(self, kind):
        return False

    def discard(self):
        self.pending.clear()

    def mute(self, now, seconds=1.5):
        pass

    def inject(self, kind):
        self.pending.add(kind)

    def fire(self):
        self.fired.extend(sorted(self.pending))
        self.pending.clear()

    def left_held(self):
        return False


class UI:
    def show(self, img):
        pass

    def close(self):
        pass


def test_the_click_menu_on_a_primary_right_of_another_monitor():
    cur = [(1920 + 100, 100)]
    moved = []
    grabs = []

    def grab():
        grabs.append(1)
        return None
    clicker = Clicker()
    menu = cm.ClickMenu(2560, 1440, clicker, ui=UI(), grab=grab,
                        get_cursor=lambda: cur[0],
                        set_cursor=lambda x, y: moved.append((x, y)),
                        scroll=lambda n: None, log=lambda s: None,
                        origin=(1920, 0))
    menu.begin(cur[0], 0.0)
    # Laid out around the target IN THE MENU'S pixels.
    panel_l, rects_l = cm.layout(100, 100, 2560, 1440)
    assert menu.panel == panel_l and menu.rects == rects_l
    # Look at "Sag tik" (screen point = tile centre + origin), press.
    i = menu.items.index("right")
    x, y, w, h = menu.rects[i]
    cur[0] = (1920 + x + w // 2, y + h // 2)
    menu.confirm()
    assert moved == [(2020, 100)]            # the real point, on the primary
    assert clicker.fired == ["right"]


def test_the_click_menu_screenshot_is_the_primary(monkeypatch):
    import eye_gaze.zoom as zm
    grabs = []
    monkeypatch.setattr(zm, "grab_region",
                        lambda x, y, w, h: grabs.append((x, y, w, h)) or
                        np.zeros((h, w, 3), np.uint8))
    img = cm.grab_screen(2560, 1440, (1920, 0))
    assert grabs == [(1920, 0, 2560, 1440)] and img.shape == (1440, 2560, 3)
    grabs.clear()
    cm.grab_screen(1280, 720)
    assert grabs == [(0, 0, 1280, 720)]
