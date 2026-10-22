# gaze/tests/test_aac_draw.py
"""Drawing the grid.

OpenCV's putText uses Hershey fonts and cannot draw Turkish letters at all -
"Ağrım var" comes out as "A?r?m var" - so the tiles are rendered with Pillow.
"""
import numpy as np

from eye_gaze.aac import (Phrase, ascii_fold, board_rects, draw_board,
                          wrap_lines)


def test_ascii_fold_maps_turkish_letters():
    assert ascii_fold("Ağrım var, şu an çok üşüyorum. İyi geceler.") == \
        "Agrim var, su an cok usuyorum. Iyi geceler."


def test_ascii_fold_leaves_plain_text_alone():
    assert ascii_fold("Su istiyorum") == "Su istiyorum"


def test_wrap_lines_breaks_on_words():
    lines = wrap_lines("Lütfen biraz su verir misiniz", 12)
    assert all(len(l) <= 12 for l in lines)
    assert " ".join(lines) == "Lütfen biraz su verir misiniz"


def test_wrap_lines_handles_one_long_word():
    lines = wrap_lines("antidisestablishmentarianism", 10)
    assert len(lines) >= 1


def test_a_word_longer_than_the_tile_is_split_not_overflowed():
    """Left whole it runs straight out of the tile and over its neighbour."""
    lines = wrap_lines("antidisestablishmentarianism", 10)
    assert all(len(l) <= 10 for l in lines), lines
    assert "".join(lines) == "antidisestablishmentarianism"


def test_wrap_lines_empty_text():
    assert wrap_lines("", 10) == []


def test_draw_board_returns_bgr_image_of_right_size():
    tiles = [Phrase("Su istiyorum", "Lütfen su verir misiniz?", bg=(120, 40, 20)),
             Phrase("Ağrım var", "Ağrım var")]
    img = draw_board(tiles, 800, 600, "Türkçe AAC")
    assert isinstance(img, np.ndarray)
    assert img.shape == (600, 800, 3)
    assert img.dtype == np.uint8


def test_draw_board_paints_tile_colors():
    tiles = [Phrase("Tek", "Tek", bg=(10, 200, 30))]
    img = draw_board(tiles, 400, 300, "T")
    assert (img == np.array([10, 200, 30], dtype=np.uint8)).all(axis=2).any()


def test_draw_board_survives_empty_board():
    img = draw_board([], 400, 300, "Bos")
    assert img.shape == (300, 400, 3)


def test_locked_tiles_are_drawn_grey():
    normal = draw_board([Phrase("Selam", "Selam", bg=(10, 200, 30))], 400, 300, "T")
    locked = draw_board([Phrase("[Ad] gelsin", "[Ad] gelsin", bg=(10, 200, 30))],
                        400, 300, "T")
    assert not np.array_equal(normal, locked)


# --- one source of truth for where the tiles are ---------------------------


def test_the_tile_a_user_looks_at_is_the_tile_that_was_painted():
    """board_rects decides both where a tile is DRAWN and which phrase a glance
    selects. If drawing offset the tiles to clear the title bar and hit testing
    did not, every look would speak the neighbouring phrase - and nothing would
    look wrong on screen.
    """
    tiles = [Phrase(f"c{i}", f"c{i}", bg=(20 * i + 10, 30, 40)) for i in range(6)]
    img = draw_board(tiles, 800, 600, "Baslik")
    rects = board_rects(len(tiles), 800, 600)
    assert len(rects) == len(tiles)
    for i, (x, y, w, h) in enumerate(rects):
        px = tuple(int(v) for v in img[y + 4, x + 4])
        assert px == tiles[i].bg, f"{i}. kutu {px}, beklenen {tiles[i].bg}"


def test_the_title_is_actually_visible():
    """It was drawn first and then painted over by the top row of tiles."""
    img = draw_board([Phrase("Tek", "Tek", bg=(0, 0, 0))], 800, 600, "BASLIK")
    header = img[:board_rects(1, 800, 600)[0][1]]
    assert header.max() > 30, "baslik seridi bos"


def test_tiles_never_start_above_the_title():
    for n in range(1, 10):
        top = min(r[1] for r in board_rects(n, 1920, 1080))
        assert top >= 40, n


def test_board_rects_stay_on_screen():
    for n in range(1, 10):
        for x, y, w, h in board_rects(n, 1920, 1080):
            assert x >= 0 and y >= 0 and x + w <= 1920 and y + h <= 1080


def test_a_pillow_failure_is_reported_once(monkeypatch, capsys):
    """Falling back to Hershey silently means Turkish text degrades to ASCII
    and nobody ever finds out why."""
    import eye_gaze.aac as aac
    monkeypatch.setattr(aac, "_draw_pillow",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("yok")))
    monkeypatch.setattr(aac, "_PILLOW_WARNED", False, raising=False)
    tiles = [Phrase("Ağrım var", "Ağrım var")]
    first = draw_board(tiles, 400, 300, "T")
    out1 = capsys.readouterr().out
    draw_board(tiles, 400, 300, "T")
    out2 = capsys.readouterr().out
    assert "Pillow" in out1, out1
    assert out2 == "", "uyari her karede tekrarlaniyor"
    assert first.shape == (300, 400, 3)


# --- the grid has to show WHERE you are pointing ---------------------------
#
# Reported from the field: "F5'e basiyorum yeni ekran geliyor ama orada ne
# yapacagimi bilmiyorum, maus ile isaretlenmedi." The board was drawn and
# nothing showed which tile the cursor was on, so there was no way to know what
# pressing the click key would say out loud. For an AAC grid that is not a
# missing nicety - it is the difference between choosing a sentence and
# guessing one.


def _blank(w=800, h=600):
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_the_tile_under_the_pointer_is_marked():
    from eye_gaze.aac import board_rects, overlay_pointer
    rects = board_rects(4, 800, 600)
    img = _blank()
    out = overlay_pointer(img, rects, 1, 0, 0)
    x, y, w, h = rects[1]
    # the border of tile 1 must have been painted
    assert out[y + 2, x + w // 2].any(), "secili kutunun cercevesi cizilmedi"
    x0, y0, w0, h0 = rects[0]
    assert not out[y0 + 2, x0 + w0 // 2].any(), "yanlis kutu isaretlendi"


def test_the_pointer_itself_is_drawn():
    from eye_gaze.aac import board_rects, overlay_pointer
    out = overlay_pointer(_blank(), board_rects(4, 800, 600), None, 400, 300)
    assert out[300, 400].any() or out[300 - 6, 400].any(), "imlec isareti yok"


def test_the_original_image_is_not_modified():
    """The board is rendered once and overlaid every frame; painting into the
    cached copy would smear every pointer position onto the next one."""
    from eye_gaze.aac import board_rects, overlay_pointer
    img = _blank()
    before = img.copy()
    overlay_pointer(img, board_rects(4, 800, 600), 0, 100, 100)
    assert np.array_equal(img, before)


def test_no_tile_is_marked_when_the_pointer_is_between_them():
    from eye_gaze.aac import board_rects, overlay_pointer
    rects = board_rects(4, 800, 600)
    out = overlay_pointer(_blank(), rects, None, 0, 0)
    for x, y, w, h in rects:
        assert not out[y + 2, x + w // 2].any()


def test_a_locked_tile_is_marked_differently():
    """Selecting it will do nothing, and the user needs to know that BEFORE
    they spend a dwell on it."""
    from eye_gaze.aac import board_rects, overlay_pointer
    rects = board_rects(2, 800, 600)
    normal = overlay_pointer(_blank(), rects, 0, 0, 0, locked=False)
    locked = overlay_pointer(_blank(), rects, 0, 0, 0, locked=True)
    assert not np.array_equal(normal, locked)


def test_overlaying_out_of_range_is_harmless():
    from eye_gaze.aac import board_rects, overlay_pointer
    overlay_pointer(_blank(), board_rects(2, 800, 600), 9, 10, 10)
