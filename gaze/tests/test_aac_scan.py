# gaze/tests/test_aac_scan.py
"""Switch scanning for the AAC grid: the pure state machine.

For when the eyes stop being reliable enough to aim. The grid lights up one
row after another; a press picks the row, then its tiles light up one by one;
a press picks the tile. Every pass ends on a GERI step so a wrong row is never
a trap. Nothing here touches a screen, a key or a clock of its own.
"""
import numpy as np
import pytest

from eye_gaze.scan import (Scanner, overlay_scan, scan_from_cfg,
                           scan_title)

T = 1.5


def _sc(count, mode="row-col", cycles=3, now=0.0):
    s = Scanner(seconds=T, cycles=cycles, mode=mode)
    s.start(count, now)
    return s


def _advance(s, steps, now):
    """Let `steps` highlight steps pass, one tick per step."""
    for _ in range(steps):
        now += T
        s.tick(now)
    return now


# --- row level ---------------------------------------------------------------

def test_scanning_starts_on_the_first_row():
    s = _sc(9)
    assert s.level == "row"
    assert s.highlight() == [0, 1, 2]


def test_rows_advance_one_step_per_interval():
    s = _sc(9)
    s.tick(T * 0.9)
    assert s.highlight() == [0, 1, 2], "erken ilerledi"
    s.tick(T)
    assert s.highlight() == [3, 4, 5]
    s.tick(2 * T)
    assert s.highlight() == [6, 7, 8]


def test_the_last_row_may_be_short():
    # 7 tiles -> 3x3 grid, last row holds one tile only.
    s = _sc(7)
    _advance(s, 2, 0.0)
    assert s.highlight() == [6]


def test_every_row_pass_ends_on_a_geri_step():
    s = _sc(9)
    _advance(s, 3, 0.0)
    assert s.on_back is True
    assert s.highlight() == []


def test_after_geri_the_rows_start_again():
    s = _sc(9)
    _advance(s, 4, 0.0)
    assert s.on_back is False
    assert s.highlight() == [0, 1, 2]


def test_a_long_stall_advances_only_one_step():
    """A frame stall (model load, GC) must not skip the item the user was
    waiting for: catching up would jump straight past it."""
    s = _sc(9)
    s.tick(10 * T)
    assert s.highlight() == [3, 4, 5]


# --- selecting ------------------------------------------------------------------

def test_press_on_a_row_starts_scanning_its_cells():
    s = _sc(9)
    now = _advance(s, 1, 0.0)            # row 1 lit
    assert s.press(now + 0.1) is None    # a row press says nothing yet
    assert s.level == "cell"
    assert s.highlight() == [3]


def test_cells_get_a_full_interval_after_the_row_press():
    s = _sc(9)
    s.press(1.4)                         # row 0, just before it moved on
    s.tick(1.4 + T * 0.9)
    assert s.highlight() == [0], "ilk hucre tam sure gosterilmedi"
    s.tick(1.4 + T)
    assert s.highlight() == [1]


def test_press_on_a_cell_selects_that_tile():
    s = _sc(9)
    s.press(0.2)                         # row 0
    now = _advance(s, 2, 0.2)            # cell 2 lit
    assert s.press(now + 0.1) == ("tile", 2)


def test_press_on_a_cell_in_a_later_row():
    s = _sc(9)
    now = _advance(s, 2, 0.0)            # row 2
    s.press(now + 0.1)
    now = _advance(s, 1, now + 0.1)      # cell 7
    assert s.press(now + 0.1) == ("tile", 7)


def test_cell_pass_ends_on_geri_which_returns_to_the_rows():
    """A wrong row must never be a trap."""
    s = _sc(9)
    s.press(0.2)
    now = _advance(s, 3, 0.2)            # past cells 0,1,2 -> GERI
    assert s.on_back is True
    assert s.press(now + 0.1) is None
    assert s.level == "row"
    assert s.highlight() == [0, 1, 2]


def test_geri_on_the_row_level_asks_to_go_back():
    s = _sc(9)
    now = _advance(s, 3, 0.0)
    assert s.on_back
    assert s.press(now + 0.1) == ("back", None)


def test_an_unanswered_cell_pass_falls_back_to_the_rows():
    s = _sc(9)
    s.press(0.2)
    _advance(s, 4, 0.2)                  # cells 0,1,2, GERI, then over
    assert s.level == "row"
    assert s.highlight() == [0, 1, 2]


# --- single row and linear --------------------------------------------------------

def test_a_single_row_board_skips_the_row_step():
    s = _sc(2)
    assert s.level == "cell"
    assert s.highlight() == [0]
    assert s.press(0.1) == ("tile", 0)


def test_a_single_row_geri_asks_to_go_back_not_to_rows():
    s = _sc(2)
    now = _advance(s, 2, 0.0)
    assert s.on_back
    assert s.press(now + 0.1) == ("back", None)


def test_linear_mode_lights_every_tile_in_turn():
    s = _sc(5, mode="linear")
    seen = [s.highlight()]
    now = 0.0
    for _ in range(4):
        now += T
        s.tick(now)
        seen.append(s.highlight())
    assert seen == [[0], [1], [2], [3], [4]]
    s.tick(now + T)
    assert s.on_back


def test_unknown_mode_falls_back_to_row_col():
    s = Scanner(seconds=T, cycles=3, mode="zigzag")
    assert s.mode == "row-col"


# --- giving up --------------------------------------------------------------------

def test_three_silent_cycles_close_the_grid():
    s = _sc(9, cycles=3)
    now = 0.0
    closed = []
    for _ in range(4 * 3):               # 3 rows + GERI, three times
        now += T
        closed.append(s.tick(now))
    assert closed[-1] is True
    assert not any(closed[:-1]), "erken kapandi"


def test_a_press_restarts_the_idle_count():
    s = _sc(9, cycles=2)
    now = _advance(s, 4, 0.0)            # one silent cycle
    now = _advance(s, 3, now)            # on GERI of the second
    s.press(now + 0.1)                   # deliberate GERI press
    now += 0.1
    for _ in range(4):                   # one full cycle after the press
        now += T
        assert s.tick(now) is False


def test_seconds_left_only_in_the_last_cycle():
    s = _sc(9, cycles=2)
    assert s.seconds_left(0.0) is None
    now = _advance(s, 4, 0.0)
    left = s.seconds_left(now)
    assert left == pytest.approx(4 * T)


# --- presses -----------------------------------------------------------------------

def test_a_bounce_right_after_a_press_is_ignored():
    """One press of a shaky switch can arrive as two; the second must not
    select the first cell of the row the first press just opened."""
    s = _sc(9)
    s.press(0.5)                          # row 0
    assert s.press(0.6) is None
    assert s.level == "cell"
    assert s.highlight() == [0]


def test_press_before_start_is_harmless():
    s = Scanner(seconds=T, cycles=3)
    assert s.press(1.0) is None
    assert s.tick(5.0) is False
    assert s.highlight() == []


def test_empty_board_only_offers_geri():
    s = _sc(0)
    assert s.on_back
    assert s.press(0.1) == ("back", None)


def test_restart_on_a_new_board_resets_level_and_count():
    s = _sc(9)
    s.press(0.2)
    s.start(4, 1.0)
    assert s.level == "row"
    assert s.highlight() == [0, 1]


# --- config -----------------------------------------------------------------------

def test_scanning_is_off_by_default():
    assert scan_from_cfg({}) is None
    assert scan_from_cfg({"aac_scan": False}) is None


def test_scan_from_cfg_reads_the_settings():
    s = scan_from_cfg({"aac_scan": True, "aac_scan_seconds": 2.0,
                       "aac_scan_mode": "linear", "aac_scan_cycles": 5})
    assert s.seconds == 2.0 and s.mode == "linear" and s.cycles == 5


def test_silly_values_are_clamped():
    s = scan_from_cfg({"aac_scan": True, "aac_scan_seconds": 0.01,
                       "aac_scan_cycles": 0})
    assert s.seconds >= 0.5
    assert s.cycles >= 1


def test_an_unknown_mode_is_named_on_the_console(capsys):
    scan_from_cfg({"aac_scan": True, "aac_scan_mode": "zigzag"})
    out = capsys.readouterr().out
    assert "zigzag" in out
    out.encode("ascii")


def test_scan_settings_are_known_defaulted_and_coerced(tmp_path, monkeypatch):
    from eye_gaze import config as config_mod
    monkeypatch.setattr(config_mod, "_report", lambda *a, **k: None)
    for k in ("aac_scan", "aac_scan_seconds", "aac_scan_mode",
              "aac_scan_cycles"):
        assert k in config_mod.KNOWN_KEYS, k
    d = config_mod.DEFAULTS
    assert d["aac_scan"] is False
    assert d["aac_scan_seconds"] == 1.5
    assert d["aac_scan_mode"] == "row-col"
    assert d["aac_scan_cycles"] == 3
    p = tmp_path / "c.yaml"
    p.write_text('left_click_key: space\naac_scan: true\n'
                 'aac_scan_seconds: "2,5"\naac_scan_cycles: "4"\n',
                 encoding="utf-8")
    cfg = config_mod.load_config(str(p))
    assert cfg["aac_scan_seconds"] == 2.5
    assert cfg["aac_scan_cycles"] == 4 and isinstance(cfg["aac_scan_cycles"], int)


# --- drawing ----------------------------------------------------------------------

_YELLOW = (0, 255, 255)


def test_overlay_marks_the_lit_row_and_leaves_the_base_alone():
    base = np.zeros((300, 400, 3), dtype=np.uint8)
    rects = [(20, 60, 100, 80), (140, 60, 100, 80), (20, 160, 100, 80)]
    s = Scanner(seconds=T, cycles=3)
    s.start(3, 0.0)                      # 3 tiles -> 2x2 grid, row 0 = 0,1
    out = overlay_scan(base, rects, s, "KAPAT")
    assert base.max() == 0, "taban goruntu boyandi"
    assert tuple(out[60, 20]) == _YELLOW
    assert tuple(out[160 + 40, 20]) != _YELLOW, "isiklanmayan satir boyandi"


def test_overlay_shows_the_geri_step_when_it_is_lit():
    base = np.zeros((300, 400, 3), dtype=np.uint8)
    s = Scanner(seconds=T, cycles=3)
    s.start(0, 0.0)                      # only GERI
    out = overlay_scan(base, [], s, "KAPAT")
    # The whole screen edge is framed so the step cannot be missed.
    assert tuple(out[0, 200]) == _YELLOW
    assert tuple(out[299, 200]) == _YELLOW


def test_scan_title_replaces_the_gaze_hint():
    t = scan_title("Ana Menu  (1/2)   -   bak + tikla = konus")
    assert "bak + tikla" not in t
    assert t.startswith("Ana Menu  (1/2)")


def test_a_press_read_in_the_frame_that_advanced_picks_the_lit_item():
    """The driver ticks, THEN reads the press - while the screen still shows
    the old highlight. Reproduced in the audit: painted row 0, selected 1."""
    s = _sc(9, mode="linear")
    now = _advance(s, 2, 0.0)              # cell 2 lit
    now += T
    s.tick(now)                            # advances to 3 (not painted yet)
    assert s.press(now + 0.01) == ("tile", 2)


def test_a_press_well_after_the_advance_picks_the_new_item():
    s = _sc(9, mode="linear")
    now = _advance(s, 3, 0.0)              # cell 3 lit
    assert s.press(now + 0.3) == ("tile", 3)
