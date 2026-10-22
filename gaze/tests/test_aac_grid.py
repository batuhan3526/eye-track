# gaze/tests/test_aac_grid.py
"""Where the user is in the phrase tree.

Pure navigation, no window and no drawing, so every path a user can take
through the boards is testable without a screen.
"""
from eye_gaze.aac import AacNavigator, Board, Phrase, board_rects


def _boards():
    root = Board("ana", "Ana Menü", [
        Phrase("ACIL", "", target="acil"),
        Phrase("Su istiyorum", "Lütfen biraz su verir misiniz?"),
        Phrase("[Ad] gelsin", "[Ad] gelsin"),
    ])
    acil = Board("acil", "Acil", [
        Phrase("Nefes alamıyorum", "Nefes alamıyorum, yardım edin!"),
    ])
    return {"ana": root, "acil": acil}


def test_starts_on_root_board():
    n = AacNavigator(_boards(), "ana")
    assert n.title().startswith("Ana Menü")
    assert n.tiles()[0].label == "ACIL"


def test_root_has_no_back_tile():
    n = AacNavigator(_boards(), "ana")
    assert all(t.label != "<< GERI" for t in n.tiles())


def test_choosing_a_phrase_returns_its_text():
    n = AacNavigator(_boards(), "ana")
    assert n.choose(1) == "Lütfen biraz su verir misiniz?"


def test_choosing_a_category_navigates_and_returns_none():
    n = AacNavigator(_boards(), "ana")
    assert n.choose(0) is None
    assert n.title().startswith("Acil")
    assert n.tiles()[0].label == "Nefes alamıyorum"


def test_back_returns_to_root():
    n = AacNavigator(_boards(), "ana")
    n.choose(0)
    back = len(n.tiles()) - 1
    assert n.tiles()[back].label == "<< GERI"
    assert n.choose(back) is None
    assert n.title().startswith("Ana Menü")


def test_locked_phrase_cannot_be_spoken():
    n = AacNavigator(_boards(), "ana")
    assert n.choose(2) is None


def test_out_of_range_index_is_safe():
    n = AacNavigator(_boards(), "ana")
    assert n.choose(99) is None
    assert n.choose(-1) is None


def test_missing_target_board_does_not_navigate():
    boards = _boards()
    boards["ana"].phrases[0] = Phrase("YOK", "", target="olmayan")
    n = AacNavigator(boards, "ana")
    assert n.choose(0) is None
    assert n.title().startswith("Ana Menü")


def test_next_page_cycles_through_pages():
    long_board = Board("ana", "Uzun",
                       [Phrase(f"c{i}", f"cumle {i}") for i in range(15)])
    n = AacNavigator({"ana": long_board}, "ana")
    labels_p0 = [t.label for t in n.tiles()]
    nxt = next(i for i, t in enumerate(n.tiles()) if t.label == "DEVAM >>")
    assert n.choose(nxt) is None
    assert [t.label for t in n.tiles()] != labels_p0


def test_paging_past_the_end_wraps_to_the_first_page():
    """A user who keeps choosing DEVAM must never reach a dead end - there is
    no other way off the last page except GERI, and the root has no GERI."""
    b = Board("ana", "Uzun", [Phrase(f"c{i}", f"cumle {i}") for i in range(15)])
    n = AacNavigator({"ana": b}, "ana")
    first = [t.label for t in n.tiles()]
    for _ in range(3):
        nxt = next(i for i, t in enumerate(n.tiles()) if t.label == "DEVAM >>")
        n.choose(nxt)
    assert [t.label for t in n.tiles()] == first


def test_reset_returns_to_root_page_zero():
    n = AacNavigator(_boards(), "ana")
    n.choose(0)
    n.reset()
    assert n.title().startswith("Ana Menü")


def test_missing_root_board_gives_empty_grid():
    n = AacNavigator({}, "ana")
    assert n.tiles() == []
    assert n.choose(0) is None


def test_all_phrases_lists_only_speakable_text():
    n = AacNavigator(_boards(), "ana")
    texts = n.all_phrases()
    assert "Lütfen biraz su verir misiniz?" in texts
    assert "Nefes alamıyorum, yardım edin!" in texts
    assert "[Ad] gelsin" not in texts       # kilitli cumle onbellege girmez


# --- the geometry has to be the SAME geometry ------------------------------


def test_hit_testing_uses_the_rectangles_the_tiles_were_drawn_in():
    """draw_board reserves a header strip for the title. If the navigator
    handed out the raw grid instead, every glance would select the phrase one
    row above the one being looked at - and the screen would look correct.
    """
    n = AacNavigator(_boards(), "ana")
    assert n.rects(1920, 1080) == board_rects(len(n.tiles()), 1920, 1080)


def test_a_board_whose_own_back_key_was_dropped_has_one_page():
    """The page COUNT must be computed from the tiles that are actually shown.
    Counting the dropped back key would advertise a DEVAM tile leading to a
    page holding nothing but GERI."""
    phrases = [Phrase(f"c{i}", f"cumle {i}") for i in range(7)]
    phrases.append(Phrase("Geri / Ana Menü", "", target="ana"))
    boards = {"ana": Board("ana", "Ana", [Phrase("ALT", "", target="alt")]),
              "alt": Board("alt", "Alt", phrases)}
    n = AacNavigator(boards, "ana")
    n.choose(0)
    labels = [t.label for t in n.tiles()]
    assert "DEVAM >>" not in labels, labels
    assert labels[-1] == "<< GERI"
    assert len(labels) == 8


def test_the_title_shows_the_page_number_when_there_is_more_than_one():
    b = Board("ana", "Uzun", [Phrase(f"c{i}", f"cumle {i}") for i in range(15)])
    n = AacNavigator({"ana": b}, "ana")
    assert "1/3" in n.title(), n.title()


def test_a_single_page_title_has_no_page_number():
    n = AacNavigator(_boards(), "ana")
    assert "/" not in n.title().split("-")[0]
