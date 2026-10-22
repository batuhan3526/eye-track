# gaze/tests/test_aac_scan_driver.py
"""The driver's AAC grid in switch-scanning mode (aac_scan: true).

The Scanner is tested on its own; this is the glue: a press of the click
switch must reach the scanner and not the gaze cursor, the picked tile must be
the one that was lit, a closed grid must close the way its timeout does, and
nothing here may need the eyes to aim.
"""
import pytest

import gaze_driver as gd
from eye_gaze.switch import KeyPresses

XML_ANA = """<?xml version="1.0" encoding="utf-8"?>
<Keyboard><Name>Ana Menu</Name><Content>
  <DynamicKey><Label>ACIL</Label><ChangeKeyboard>acil</ChangeKeyboard></DynamicKey>
  <DynamicKey><Label>Su istiyorum</Label><Text>Su verir misiniz?</Text>
    <Action>Speak</Action></DynamicKey>
  <DynamicKey><Label>[Ad] gelsin</Label><Text>[Ad] gelsin</Text>
    <Action>Speak</Action></DynamicKey>
</Content></Keyboard>
"""

XML_ACIL = """<?xml version="1.0" encoding="utf-8"?>
<Keyboard><Name>Acil</Name><Content>
  <DynamicKey><Label>Nefes alamiyorum</Label><Text>Nefes alamiyorum!</Text>
    <Action>Speak</Action></DynamicKey>
  <DynamicKey><Label>Geri</Label><ChangeKeyboard>ana</ChangeKeyboard></DynamicKey>
</Content></Keyboard>
"""

T = 1.5


class FakeSpeaker:
    engine = voice = "piper/tr"
    ok = True

    def __init__(self):
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


class FakeCv2:
    WINDOW_NORMAL = 0
    WND_PROP_FULLSCREEN = 1
    WINDOW_FULLSCREEN = 1

    def __init__(self):
        self.shown = []
        self.destroyed = []

    def namedWindow(self, *a, **k):
        pass

    def setWindowProperty(self, *a, **k):
        pass

    def imshow(self, win, img):
        self.shown.append(img)

    def destroyWindow(self, win):
        self.destroyed.append(win)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _make(tmp_path, monkeypatch, **extra):
    boards = tmp_path / "aac"
    boards.mkdir()
    (boards / "ana.xml").write_text(XML_ANA, encoding="utf-8")
    (boards / "acil.xml").write_text(XML_ACIL, encoding="utf-8")
    fake = FakeCv2()
    clock = Clock()
    monkeypatch.setattr(gd, "cv2", fake)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    monkeypatch.setattr(gd, "_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(gd.time, "monotonic", clock)
    # The scan must not depend on where the gaze cursor is: park it in the gap
    # between tiles, where gaze mode would select nothing at all.
    monkeypatch.setattr(gd.cursor, "get_cursor", lambda: (0, 0))
    speaker = FakeSpeaker()
    cfg = {"aac_dir": str(boards), "aac_root": "ana", "aac_timeout": 20,
           "aac_cache": False, "aac_scan": True, "aac_scan_seconds": T,
           "aac_scan_cycles": 3, "aac_scan_mode": "row-col"}
    cfg.update(extra)
    g = gd._AacGrid(1280, 720, cfg, speaker)
    return g, fake, speaker, clock


@pytest.fixture
def scan(tmp_path, monkeypatch):
    return _make(tmp_path, monkeypatch)


def _wait(g, clock, steps):
    for _ in range(steps):
        clock.t += T
        g.tick(clock.t)


def _press(g, clock, dt=0.1):
    clock.t += dt
    g.confirm()


def test_scan_off_keeps_the_gaze_grid(tmp_path, monkeypatch):
    g, _, _, _ = _make(tmp_path, monkeypatch, aac_scan=False)
    assert g.scanning is False


def test_scan_on_is_reported_on_the_console(tmp_path, monkeypatch, capsys):
    g, _, _, _ = _make(tmp_path, monkeypatch)
    assert g.scanning is True
    out = capsys.readouterr().out
    assert "TARAMA" in out
    out.encode("ascii")


def test_two_presses_speak_the_lit_phrase_without_aiming(scan):
    g, fake, speaker, clock = scan
    g.begin()
    # 3 tiles -> 2x2: row 0 = ACIL, Su istiyorum.
    _press(g, clock)                      # open row 0 -> ACIL lit
    assert g.active
    _wait(g, clock, 1)                    # -> "Su istiyorum" lit
    _press(g, clock)
    assert speaker.spoken == ["Su verir misiniz?"]
    assert g.active is False


def test_picking_a_category_opens_it_and_restarts_the_scan(scan):
    g, fake, speaker, clock = scan
    g.begin()
    _press(g, clock)                      # row 0
    _press(g, clock, dt=0.5)              # ACIL (cell 0)
    assert g.active is True
    assert g._nav.title().startswith("Acil")
    assert speaker.spoken == []
    # Acil: "Nefes" + GERI tile -> one row: scanning starts on the first tile.
    assert g._scan.highlight() == [0]
    _press(g, clock, dt=0.5)
    assert speaker.spoken == ["Nefes alamiyorum!"]


def test_the_title_says_how_to_use_the_scan(scan, monkeypatch):
    """'bak + tikla' on a board that does not follow the gaze would send the
    user looking for a pointer that is not there."""
    import eye_gaze.aac as aac
    g, _, _, _ = scan
    titles = []
    real = aac.draw_board

    def spy(tiles, w, h, title):
        titles.append(title)
        return real(tiles, w, h, title)
    monkeypatch.setattr(aac, "draw_board", spy)
    g.begin()
    assert "isik gelince" in titles[-1]
    assert "bak + tikla" not in titles[-1]


def test_geri_step_on_a_sub_board_goes_back_to_the_menu(scan):
    g, _, speaker, clock = scan
    g.begin()
    _press(g, clock)
    _press(g, clock, dt=0.5)              # into Acil
    _wait(g, clock, 2)                    # Nefes, GERI tile, then GERI step
    assert g._scan.on_back
    _press(g, clock)
    assert g.active is True
    assert g._nav.title().startswith("Ana Menu")
    assert speaker.spoken == []


def test_geri_step_on_the_main_menu_closes_the_grid(scan):
    g, _, speaker, clock = scan
    g.begin()
    _wait(g, clock, 2)                    # row 0, row 1, GERI
    assert g._scan.on_back
    _press(g, clock)
    assert g.active is False
    assert speaker.spoken == []


def test_a_locked_phrase_says_nothing_and_keeps_scanning(scan):
    g, _, speaker, clock = scan
    g.begin()
    _wait(g, clock, 1)                    # row 1: the locked "[Ad] gelsin"
    _press(g, clock)
    _press(g, clock, dt=0.5)
    assert speaker.spoken == []
    assert g.active is True
    assert g._scan.level == "row"


def test_unanswered_cycles_close_the_grid_like_the_timeout(scan):
    g, _, _, clock = scan
    g.begin()
    closed = []
    for _ in range(3 * 3):                # 2 rows + GERI, three cycles
        clock.t += T
        closed.append(g.tick(clock.t))
    assert closed[-1] is True, "tick() kapanisi bildirmedi (tik yutulmaz)"
    assert not any(closed[:-1])
    assert g.active is False


def test_scanning_ignores_the_gaze_timeout(tmp_path, monkeypatch):
    """aac_timeout is a gaze idea - a parked gaze. A scan is never idle while
    it is still offering rows; it has its own cycles limit."""
    g, _, _, clock = _make(tmp_path, monkeypatch, aac_timeout=5,
                           aac_scan_cycles=10)
    g.begin()
    for _ in range(8):
        clock.t += T
        assert g.tick(clock.t) is False
    assert g.active is True


def test_dwell_does_not_press_the_scan(scan):
    """Watching the scan means holding the gaze still - exactly what dwell
    turns into a click. In scan mode only a deliberate switch may select."""
    g, _, speaker, clock = scan
    g.begin()
    clock.t += 0.1
    g.confirm(dwell=True)
    assert g._scan.level == "row"
    assert g.active is True


def test_dwell_still_confirms_in_gaze_mode(tmp_path, monkeypatch):
    g, _, speaker, _ = _make(tmp_path, monkeypatch, aac_scan=False)
    g.begin()
    x, y, w, h = g._nav.rects(1280, 720)[1]
    monkeypatch.setattr(gd.cursor, "get_cursor", lambda: (x + w // 2, y + h // 2))
    g.confirm(dwell=True)
    assert speaker.spoken == ["Su verir misiniz?"]


def test_frames_are_painted_with_the_scan_highlight(scan):
    g, fake, _, clock = scan
    g.begin()
    n = len(fake.shown)
    clock.t += 0.2
    g.tick(clock.t)
    assert len(fake.shown) == n + 1
    img = fake.shown[-1]
    rects = g._nav.rects(1280, 720)
    x, y = rects[0][0], rects[0][1]
    assert tuple(img[y, x]) == (0, 255, 255)


def test_a_failing_scan_never_leaves_the_grid_stuck_open(scan, monkeypatch):
    """If the scan code throws, the grid closes rather than staying up over
    the desktop with nothing moving - a fullscreen window the user cannot get
    rid of would cost them the whole machine."""
    g, _, _, clock = scan
    g.begin()

    def boom(now):
        raise RuntimeError("bozuk")
    monkeypatch.setattr(g._scan, "tick", boom)
    clock.t += T
    assert g.tick(clock.t) is True
    assert g.active is False


# --- through the real switch path ---------------------------------------------

class _Ev:
    def __init__(self, kind):
        self.event_type = kind


class _FakeKb:
    def __init__(self):
        self.hooks = {}

    def hook_key(self, key, cb, suppress=False):
        self.hooks[key] = cb
        return lambda: None

    def send(self, key, kind):
        return self.hooks[key](_Ev(kind))


def test_holding_the_switch_is_one_scan_press(scan, monkeypatch):
    """Holding the switch makes Windows auto-repeat key-down; a scan that
    took each repeat as a press would open a row and pick its first tile in
    one push."""
    g, _, speaker, clock = scan
    for name in ("left_click", "right_click", "double_click", "left_down",
                 "left_up", "escape"):
        monkeypatch.setattr(gd.actions, name, lambda: None)
    kb = _FakeKb()
    keys = KeyPresses(["space", "f5"], kb=kb)
    cfg = {"left_click_key": "space", "gamepad_enabled": False}
    clicker = gd._Clicker(cfg, keys=keys, gamepad=None)
    g.begin()
    for i in range(30):                   # one second of a held switch
        kb.send("space", "down")
        clock.t += 0.033
        clicker.update(clock.t)
        gd._route_clicks(clock.t, clicker, keys, g, None, "f5", "f6")
    assert g._scan.level == "cell"        # the row opened ...
    assert g._scan.highlight() == [0]     # ... and nothing in it was picked
    assert speaker.spoken == []
    assert g.active is True


def test_the_dwell_click_is_wired_as_dwell():
    """main() is not unit-testable, so pin the one wiring line it needs: the
    dwell route must say it is dwell, or a still gaze presses the scan. Kept
    as a source check so a merge that drops it fails here, not in the chair."""
    import inspect
    src = inspect.getsource(gd.main)
    assert "fire_left_click(dwell=True)" in src
    assert "aacgrid.confirm(dwell=dwell)" in src
