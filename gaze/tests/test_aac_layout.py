# gaze/tests/test_aac_layout.py
"""Grid layout and hit mapping.

The measured calibration error of a webcam setup is 100-134 px, so the tiles
are deliberately enormous and few. A tile the user cannot reliably hit is not a
smaller feature - it is a phrase they cannot say.
"""
from eye_gaze.aac import (BACK, NEXT, PER_PAGE, Board, Phrase, cell_at,
                          cell_rects, grid_shape, page_tiles, paginate)


def _p(label):
    return Phrase(label=label, text=label)


def test_grid_shape_scales_with_count():
    assert grid_shape(1) == (1, 1)
    assert grid_shape(2) == (1, 2)
    assert grid_shape(4) == (2, 2)
    assert grid_shape(6) == (2, 3)
    assert grid_shape(9) == (3, 3)


def test_grid_shape_never_exceeds_three_by_three():
    assert grid_shape(50) == (3, 3)


def test_grid_shape_of_zero_is_safe():
    assert grid_shape(0) == (1, 1)


def test_cells_are_large_enough_for_our_gaze_error():
    """Olculen kalibrasyon hatasi 100-134 px; kutular bundan cok buyuk olmali."""
    rects = cell_rects(9, 1920, 1080)
    assert len(rects) == 9
    for _, _, w, h in rects:
        assert w > 400 and h > 250


def test_cells_do_not_overlap_and_stay_on_screen():
    rects = cell_rects(9, 1920, 1080)
    for x, y, w, h in rects:
        assert x >= 0 and y >= 0
        assert x + w <= 1920 and y + h <= 1080
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            apart = (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0]
                     or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])
            assert apart


def test_cell_at_finds_the_tile_under_the_gaze():
    rects = cell_rects(4, 1920, 1080)
    x, y, w, h = rects[2]
    assert cell_at(x + w // 2, y + h // 2, rects) == 2


def test_cell_at_returns_none_in_the_gap():
    """Kutular arasi bosluga bakmak yanlislikla komsu kutuyu secmemeli."""
    rects = cell_rects(4, 1920, 1080)
    assert cell_at(0, 0, rects) is None          # sol ust kenar payi
    assert cell_at(-50, -50, rects) is None
    assert cell_at(1919, 1079, rects) is None    # sag alt kenar payi


def test_paginate_splits_long_boards():
    pages = paginate([_p(f"c{i}") for i in range(20)], per_page=9)
    assert len(pages) == 3
    assert len(pages[0]) == 9 and len(pages[2]) == 2


def test_paginate_empty_gives_one_empty_page():
    assert paginate([], per_page=9) == [[]]


def test_root_page_has_no_back_tile():
    b = Board("k", "Ana", [_p("a"), _p("b")])
    tiles = page_tiles(b, page=0, pages=1, is_root=True)
    assert all(t.target != BACK for t in tiles)
    assert len(tiles) == 2


def test_subboard_page_has_back_tile_last():
    b = Board("k", "Alt", [_p("a")])
    tiles = page_tiles(b, page=0, pages=1, is_root=False)
    assert tiles[-1].target == BACK
    # ASCII arrows, not U+25C0/U+25B6: the tiles are drawn with whatever font
    # the machine has, and a missing glyph shows the user an empty box where
    # their way back should be.
    assert tiles[-1].label == "<< GERI"


def test_multipage_adds_next_tile():
    b = Board("k", "Uzun", [_p(f"c{i}") for i in range(8)])
    tiles = page_tiles(b, page=0, pages=2, is_root=True)
    assert any(t.target == NEXT for t in tiles)


# --- the boards carry their own back key -----------------------------------
#
# Every shipped sub-board ends with <Label>Geri / Ana Menü</Label> and a
# ChangeKeyboard back to the main menu. Drawn alongside our own GERI tile the
# user gets two ways back, in two different places, and one of the nine huge
# tiles is spent saying the same thing twice. With eye control the position of
# a tile is how it is found, so there must be exactly one, always in the same
# corner.


def test_the_boards_own_back_key_is_replaced_by_ours():
    b = Board("alt", "Alt", [_p("a"), Phrase("Geri / Ana Menü", "", target="ana")])
    tiles = page_tiles(b, 0, 1, is_root=False, root_key="ana")
    assert [t.label for t in tiles] == ["a", "<< GERI"]


def test_a_link_to_another_board_is_kept():
    """Non-vacuity: only the link back to the ROOT is redundant."""
    b = Board("alt", "Alt", [Phrase("KISISEL", "", target="kisisel")])
    tiles = page_tiles(b, 0, 1, is_root=False, root_key="ana")
    assert [t.label for t in tiles] == ["KISISEL", "<< GERI"]


def test_the_root_board_keeps_all_of_its_links():
    b = Board("ana", "Ana", [Phrase("KISISEL", "", target="kisisel")])
    assert len(page_tiles(b, 0, 1, is_root=True, root_key="ana")) == 1


def test_dropping_the_duplicate_happens_before_pagination():
    """Otherwise the removed tile leaves a hole and the last page can end up
    empty - a page of nothing but a GERI box."""
    phrases = [_p(f"c{i}") for i in range(7)]
    phrases.append(Phrase("Geri", "", target="ana"))
    b = Board("alt", "Alt", phrases)
    assert len(paginate([p for p in b.phrases if p.target != "ana"])) == 1
    tiles = page_tiles(b, 0, 1, is_root=False, root_key="ana")
    assert len(tiles) == 8
    assert all(t.label != "Geri" for t in tiles)


# --- the invariant the whole layout rests on -------------------------------
#
# cell_rects caps at 9 tiles and silently drops the rest. If page_tiles could
# ever produce ten, the tenth phrase would be laid out nowhere: invisible,
# unhittable, and nothing in the code would say so.


def test_a_full_page_with_both_navigation_tiles_still_fits():
    b = Board("k", "Alt", [_p(f"c{i}") for i in range(30)])
    tiles = page_tiles(b, page=0, pages=5, is_root=False)
    assert len(tiles) <= 9
    assert len(cell_rects(len(tiles), 1920, 1080)) == len(tiles)


def test_the_page_size_leaves_room_for_navigation():
    assert PER_PAGE + 2 <= 9


def test_no_next_tile_on_a_single_page():
    """Non-vacuity for test_multipage_adds_next_tile."""
    b = Board("k", "Kisa", [_p("a")])
    assert all(t.target != NEXT for t in page_tiles(b, 0, 1, True))


def test_a_page_beyond_the_end_is_empty_not_an_error():
    b = Board("k", "Kisa", [_p("a")])
    assert page_tiles(b, page=7, pages=1, is_root=True) == []


def test_every_tile_of_a_real_sized_board_gets_a_rectangle():
    b = Board("k", "Ana", [_p(f"c{i}") for i in range(10)])
    for page in range(2):
        tiles = page_tiles(b, page, 2, is_root=False)
        assert len(cell_rects(len(tiles), 1920, 1080)) == len(tiles)


def test_a_small_window_still_produces_usable_rectangles():
    """Nothing negative, nothing zero-sized: cv2 raises on those and the grid
    is opened from the gaze loop."""
    for w, h in ((640, 480), (1280, 720), (3840, 2160)):
        for n in range(1, 10):
            for x, y, cw, ch in cell_rects(n, w, h):
                assert cw > 0 and ch > 0
                assert 0 <= x and 0 <= y and x + cw <= w and y + ch <= h
