# gaze/tests/test_aac_parse.py
"""Reading OptiKey's dynamic-keyboard XML.

The phrases live in the same files OptiKey uses, so there is one place to edit
and two ways to speak. A broken file here must cost one board, never the
driver: this runs on a machine whose user cannot open a text editor to fix it.
"""
import pytest

from eye_gaze.aac import (Board, Phrase, hex_to_bgr, is_placeholder,
                          load_boards, parse_keyboard)

XML = """<?xml version="1.0" encoding="utf-8"?>
<Keyboard>
  <Name>Türkçe AAC - Ana Menü</Name>
  <Grid><Rows>4</Rows><Cols>3</Cols></Grid>
  <Content>
    <DynamicKey Row="0" Col="0" BackgroundColor="#C0392B" ForegroundColor="#FFFFFF">
      <Label>ACIL / SAGLIK</Label>
      <ChangeKeyboard BackReturnsHere="False">turkce-aac-acil-saglik</ChangeKeyboard>
    </DynamicKey>
    <DynamicKey Row="1" Col="0" BackgroundColor="#1A5276">
      <Label>Su istiyorum</Label>
      <Action>ClearScratchpad</Action>
      <Text>Lütfen biraz su verir misiniz?</Text>
      <Action>Speak</Action>
    </DynamicKey>
    <DynamicKey Row="1" Col="1">
      <Label>Ağrım var</Label>
    </DynamicKey>
    <DynamicKey Row="2" Col="0">
      <Label>[Aile Adı 1] gelsin.</Label>
      <Text>[Aile Adı 1] gelsin.</Text>
    </DynamicKey>
  </Content>
</Keyboard>
"""


def test_board_name_and_key():
    b = parse_keyboard(XML, "turkce-aac-paketi")
    assert isinstance(b, Board)
    assert b.name == "Türkçe AAC - Ana Menü"
    assert b.key == "turkce-aac-paketi"
    assert len(b.phrases) == 4


def test_category_key_carries_target():
    p = parse_keyboard(XML, "k").phrases[0]
    assert isinstance(p, Phrase)
    assert p.target == "turkce-aac-acil-saglik"
    assert p.label == "ACIL / SAGLIK"


def test_text_is_spoken_not_label():
    p = parse_keyboard(XML, "k").phrases[1]
    assert p.label == "Su istiyorum"
    assert p.text == "Lütfen biraz su verir misiniz?"
    assert p.target is None


def test_label_used_when_text_missing():
    p = parse_keyboard(XML, "k").phrases[2]
    assert p.text == "Ağrım var"


def test_placeholder_phrase_is_locked():
    p = parse_keyboard(XML, "k").phrases[3]
    assert p.locked is True


def test_normal_phrase_is_not_locked():
    assert parse_keyboard(XML, "k").phrases[1].locked is False


def test_is_placeholder_detects_brackets():
    assert is_placeholder("[Aile Adı 1] gelsin.") is True
    assert is_placeholder("[İlaç Adı] istiyorum") is True
    assert is_placeholder("Su istiyorum") is False


def test_is_placeholder_needs_a_real_bracket_pair():
    """A closing bracket before an opening one is not a template - and locking
    a phrase the user needs is not a harmless mistake."""
    assert is_placeholder("Kapat] sonra [ac") is False
    assert is_placeholder("[") is False
    assert is_placeholder("]") is False


def test_colors_parsed_to_bgr():
    p = parse_keyboard(XML, "k").phrases[0]
    assert p.bg == (43, 57, 192)        # #C0392B -> BGR
    assert p.fg == (255, 255, 255)


def test_hex_to_bgr_bad_value_uses_default():
    assert hex_to_bgr("mavi", (1, 2, 3)) == (1, 2, 3)
    assert hex_to_bgr("", (1, 2, 3)) == (1, 2, 3)
    assert hex_to_bgr("#FFF", (1, 2, 3)) == (1, 2, 3)


def test_hex_to_bgr_accepts_lowercase_and_spaces():
    assert hex_to_bgr("  #c0392b  ", (0, 0, 0)) == (43, 57, 192)


def test_broken_xml_raises():
    with pytest.raises(Exception):
        parse_keyboard("<Keyboard><oops", "k")


def test_load_boards_skips_broken_files(tmp_path):
    (tmp_path / "iyi.xml").write_text(XML, encoding="utf-8")
    (tmp_path / "bozuk.xml").write_text("<Keyboard><kirik", encoding="utf-8")
    (tmp_path / "not.txt").write_text("xml degil", encoding="utf-8")
    boards = load_boards(str(tmp_path))
    assert set(boards) == {"iyi"}
    assert boards["iyi"].name == "Türkçe AAC - Ana Menü"


def test_load_boards_missing_directory_returns_empty():
    assert load_boards("Z:/olmayan/klasor") == {}


# --- shapes the real files actually have -----------------------------------


def test_a_key_with_no_label_and_no_target_is_dropped():
    """OptiKey files contain layout-only entries; a blank tile the user can
    look at and select would speak nothing and teach them the grid lies."""
    xml = ("<Keyboard><Name>x</Name><Content>"
           "<DynamicKey Row='0' Col='0'><Action>Speak</Action></DynamicKey>"
           "<DynamicKey Row='0' Col='1'><Label>Evet</Label></DynamicKey>"
           "</Content></Keyboard>")
    b = parse_keyboard(xml, "k")
    assert [p.label for p in b.phrases] == ["Evet"]


def test_a_text_only_key_is_shown_by_its_text():
    xml = ("<Keyboard><Name>x</Name><Content>"
           "<DynamicKey Row='0' Col='0'><Text>Tesekkur ederim</Text>"
           "<Action>Speak</Action></DynamicKey></Content></Keyboard>")
    p = parse_keyboard(xml, "k").phrases[0]
    assert p.label == "Tesekkur ederim"
    assert p.text == "Tesekkur ederim"


def test_a_board_with_no_content_is_empty_not_an_error():
    b = parse_keyboard("<Keyboard><Name>Bos</Name></Keyboard>", "k")
    assert b.phrases == []
    assert b.name == "Bos"


def test_a_nameless_board_falls_back_to_its_key():
    b = parse_keyboard("<Keyboard><Content/></Keyboard>", "turkce-aac-paketi")
    assert b.name == "turkce-aac-paketi"


def test_optikey_only_keyboard_switches_are_dropped():
    """The shipped main board really contains

        <Label>KLAVYE</Label><Action>Alpha1Keyboard</Action>
        <Label>Ana Menü</Label><Action>MenuKeyboard</Action>

    - keys that switch OptiKey to its own alphabet or menu. This grid cannot do
    that, so kept as tiles they would sit there and, when selected, SPEAK the
    word "KLAVYE" out loud to whoever is in the room.
    """
    xml = ("<Keyboard><Name>x</Name><Content>"
           "<DynamicKey><Label>KLAVYE</Label>"
           "<Action>Alpha1Keyboard</Action></DynamicKey>"
           "<DynamicKey><Label>Ana Menü</Label>"
           "<Action>MenuKeyboard</Action></DynamicKey>"
           "<DynamicKey><Label>Evet.</Label><Text>Evet.</Text>"
           "<Action>Speak</Action></DynamicKey>"
           "</Content></Keyboard>")
    assert [p.label for p in parse_keyboard(xml, "k").phrases] == ["Evet."]


def test_a_key_that_speaks_is_kept_whatever_else_it_does():
    """Non-vacuity: the rule is 'cannot speak', not 'mentions a keyboard'."""
    xml = ("<Keyboard><Name>x</Name><Content><DynamicKey>"
           "<Label>Klavyeyi acar misin?</Label>"
           "<Text>Klavyeyi acar misin?</Text><Action>Speak</Action>"
           "<Action>Alpha1Keyboard</Action></DynamicKey></Content></Keyboard>")
    assert len(parse_keyboard(xml, "k").phrases) == 1


def test_a_label_only_phrase_is_still_kept():
    """Non-vacuity: dropping every key without <Text> would empty the sample
    boards this project ships."""
    xml = ("<Keyboard><Name>x</Name><Content><DynamicKey>"
           "<Label>Agrim var</Label></DynamicKey></Content></Keyboard>")
    assert len(parse_keyboard(xml, "k").phrases) == 1


def test_multiline_labels_are_collapsed():
    """OptiKey wraps long labels across lines; drawn as-is they arrive with a
    newline in the middle of a tile."""
    xml = ("<Keyboard><Name>x</Name><Content><DynamicKey>"
           "<Label>Lutfen\n     yardim edin</Label></DynamicKey></Content></Keyboard>")
    assert parse_keyboard(xml, "k").phrases[0].label == "Lutfen yardim edin"
