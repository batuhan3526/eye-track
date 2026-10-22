# gaze/tests/test_stt_insert.py
"""Putting the dictated text into whatever field has focus.

The clipboard route is not an optimisation: keyboard.write() sends scancodes
through the active keyboard LAYOUT, so "s", "g", "i" come out as the wrong
letters - or nothing - on a machine whose layout is not Turkish. The clipboard
carries Unicode and does not care.
"""
from eye_gaze.stt import insert_outcome, insert_report, insert_text


def test_pastes_turkish_text_via_clipboard():
    pasted = []
    ok = insert_text("Şu an biraz ağrım var, ilaç ister misiniz diye sordular.",
                     paster=lambda t: pasted.append(t) or True,
                     writer=lambda t: (_ for _ in ()).throw(
                         AssertionError("yazma kullanilmamali")))
    assert ok is True
    assert pasted == ["Şu an biraz ağrım var, ilaç ister misiniz diye sordular."]


def test_falls_back_to_typing_when_clipboard_fails():
    typed = []
    ok = insert_text("agrim var", paster=lambda t: False,
                     writer=lambda t: typed.append(t) or True)
    assert ok is True
    assert typed == ["agrim var"]


def test_falls_back_to_typing_when_the_clipboard_raises():
    """OpenClipboard fails while another program holds it - a raise, not a
    False."""
    typed = []
    ok = insert_text("agrim var",
                     paster=lambda t: (_ for _ in ()).throw(OSError("mesgul")),
                     writer=lambda t: typed.append(t) or True)
    assert ok is True
    assert typed == ["agrim var"]


def test_empty_text_does_nothing():
    ok = insert_text("   ", paster=lambda t: True, writer=lambda t: True)
    assert ok is False


def test_both_paths_failing_returns_false():
    ok = insert_text("bir sey", paster=lambda t: False, writer=lambda t: False)
    assert ok is False


def test_a_typing_fallback_that_raises_is_not_fatal():
    ok = insert_text("bir sey", paster=lambda t: False,
                     writer=lambda t: (_ for _ in ()).throw(RuntimeError("yok")))
    assert ok is False


# --- what the console may claim ---------------------------------------------------

def test_outcome_says_what_happened():
    assert insert_outcome("bir", paster=lambda t: True,
                          writer=lambda t: True) == "pasted"
    assert insert_outcome("bir", paster=lambda t: "pasted",
                          writer=lambda t: True) == "pasted"
    assert insert_outcome("bir", paster=lambda t: False,
                          writer=lambda t: True) == "typed"

    def refuse(t):
        raise ValueError("no keymap")      # Linux without an X keymap
    assert insert_outcome("bir", paster=lambda t: "clipboard",
                          writer=refuse) == "clipboard"
    assert insert_outcome("bir", paster=lambda t: False,
                          writer=refuse) == ""
    assert insert_text("bir", paster=lambda t: "clipboard",
                       writer=refuse) is False


def test_report_claims_only_what_happened():
    said = insert_report("pasted", "Şu an ağrım var")
    assert said.startswith("Yazildi") and said.isascii()
    said = insert_report("clipboard", "Şu an")
    assert "panoda duruyor" in said and "Yazildi" not in said
    said = insert_report("", "Şu an ağrım var")
    assert "panoda duruyor" not in said and "Yazildi" not in said
    assert "panoya da konamadi" in said and said.isascii()
    assert "u an a" in said            # the words stay on the console


def test_clipboard_paste_reports_a_failed_ctrl_v(monkeypatch):
    from eye_gaze import platform as pf
    from eye_gaze import speech, stt
    monkeypatch.setattr(speech, "set_clipboard_text", lambda t: True)

    class KB:
        def send(self, combo):
            raise RuntimeError("no keyboard")
    monkeypatch.setattr(pf, "keyboard_api", lambda: KB())
    assert stt._clipboard_paste("bir") == "clipboard"
    monkeypatch.setattr(speech, "set_clipboard_text", lambda t: False)
    assert stt._clipboard_paste("bir") is False
