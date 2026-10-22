# gaze/tests/test_i18n_resources.py
"""Language-bound resources (Task 15c, part 2): the voice, the dictation
language and the AAC pack follow `language` through each catalog's
`_defaults` (explicit values win; no `language` line = exactly as before),
a voice of another language is never used to read this language's text,
the phrase cache is keyed on the language, the English voice is a pinned
download offered by the wizard, and the English AAC pack mirrors the
Turkish one. No network, no real voice, no audio device."""
import os
import re
import threading
import xml.etree.ElementTree as ET

import pytest

from eye_gaze import i18n

GAZE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AAC = os.path.join(GAZE, "resources", "aac")

TURKISH_LETTERS = set("çğıöşüÇĞİÖŞÜ")


@pytest.fixture(autouse=True)
def _fresh_language():
    i18n.reset()
    yield
    i18n.reset()


# --- the defaults rule (ruling R31) -----------------------------------------------

FACTORY = {"tts_voice": "tr_TR-dfki-medium", "stt_language": "tr",
           "aac_root": "turkce-aac-paketi"}


def test_each_catalog_names_its_defaults_and_turkish_is_the_factory():
    assert {k: i18n.language_default(k, "tr") for k in FACTORY} == FACTORY
    assert i18n.FACTORY == FACTORY
    assert i18n.language_default("tts_voice", "en") == "en_US-ljspeech-medium"
    assert i18n.language_default("stt_language", "en") == "en"
    assert i18n.language_default("aac_root", "en") == "english-aac-pack"


def test_turkish_uses_every_value_exactly_as_before():
    # The development machine: config.yaml has no `language` line.
    assert i18n.language() == "tr"
    assert i18n.setting({}, "tts_voice", "tr") == "tr"          # old default
    assert i18n.setting({}, "stt_language", "tr") == "tr"
    assert i18n.setting({}, "aac_root", "turkce-aac-paketi") == \
        "turkce-aac-paketi"
    cfg = dict(FACTORY)
    for k, v in FACTORY.items():
        assert i18n.setting(cfg, k, "?") == v
        assert not i18n.is_default(cfg, k)
    assert i18n.setting({"tts_voice": "Tolga"}, "tts_voice", "tr") == "Tolga"
    # "auto" / empty: the Turkish default.
    assert i18n.setting({"tts_voice": "auto"}, "tts_voice", "tr") == \
        "tr_TR-dfki-medium"
    assert i18n.setting({"aac_root": ""}, "aac_root", "x") == \
        "turkce-aac-paketi"


def test_english_takes_its_defaults_unless_a_value_was_chosen():
    i18n.set_language("en")
    template = dict(FACTORY, language="en")     # what an install holds
    assert i18n.setting(template, "tts_voice", "tr") == "en_US-ljspeech-medium"
    assert i18n.setting(template, "stt_language", "tr") == "en"
    assert i18n.setting(template, "aac_root", "x") == "english-aac-pack"
    for value in ("auto", "AUTO", "", None):
        assert i18n.setting({"stt_language": value}, "stt_language",
                            "tr") == "en"
    assert i18n.setting({}, "tts_voice", "tr") == "en_US-ljspeech-medium"
    # Anything else was chosen on purpose and wins.
    assert i18n.setting({"tts_voice": "en_GB-alan-medium"}, "tts_voice",
                        "tr") == "en_GB-alan-medium"
    assert i18n.setting({"stt_language": "de"}, "stt_language", "tr") == "de"
    assert i18n.setting({"aac_root": "my-board"}, "aac_root", "x") == \
        "my-board"


def test_the_bare_turkish_code_is_a_factory_value_too():
    # config.yaml's comment says writing "tr" is enough for the voice.
    cfg = {"tts_voice": "tr", "stt_language": "TR"}
    assert i18n.setting(cfg, "tts_voice", "x") == "tr"           # Turkish
    assert i18n.setting(cfg, "stt_language", "x") == "TR"
    i18n.set_language("en")
    assert i18n.setting(cfg, "tts_voice", "x") == "en_US-ljspeech-medium"
    assert i18n.setting(cfg, "stt_language", "x") == "en"
    assert i18n.setting({"tts_voice": " tr "}, "tts_voice",
                        "x") == "en_US-ljspeech-medium"
    # Other values were chosen and still win.
    assert i18n.setting({"tts_voice": "tr_TR-fahrettin-medium"},
                        "tts_voice", "x") == "tr_TR-fahrettin-medium"
    assert i18n.setting({"stt_language": "turkish"}, "stt_language",
                        "x") == "turkish"
    assert i18n.setting({"aac_root": "tr"}, "aac_root", "x") == "tr"


def test_a_language_without_defaults_speaks_by_its_code(tmp_path,
                                                        monkeypatch):
    for code in ("tr", "en"):
        with open(os.path.join(GAZE, "resources", "lang", code + ".yaml"),
                  "rb") as src:
            (tmp_path / (code + ".yaml")).write_bytes(src.read())
    (tmp_path / "pt_BR.yaml").write_text('_name: "Português"\n',
                                         encoding="utf-8")
    monkeypatch.setattr(i18n, "lang_dir", lambda: str(tmp_path))
    i18n.reset()
    assert i18n.set_language("pt_BR") == "pt_BR"
    # Never the Turkish (or English) voice for Portuguese text.
    assert i18n.setting(FACTORY, "tts_voice", "tr") == "pt"
    assert i18n.setting(FACTORY, "stt_language", "tr") == "pt"
    assert i18n.setting(FACTORY, "aac_root", "x") == "english-aac-pack"


# --- R32 a-c: codes, validation, the bilingual notice ------------------------------

def test_a_region_coded_catalog_is_found_whatever_the_case(tmp_path,
                                                           monkeypatch):
    (tmp_path / "tr.yaml").write_text('_name: "Türkçe"\na: "A"\n',
                                      encoding="utf-8")
    (tmp_path / "pt_BR.yaml").write_text('_name: "Português"\na: "Á"\n',
                                         encoding="utf-8")
    monkeypatch.setattr(i18n, "lang_dir", lambda: str(tmp_path))
    i18n.reset()
    # Linux file systems are case-sensitive: "pt_br.yaml" does not open
    # pt_BR.yaml there, so the code is matched against the files found.
    for typed in ("pt-BR", "pt_br", "PT-br", " pt_BR "):
        assert i18n.normalize(typed) == "pt_BR"
    assert i18n.set_language("pt-br") == "pt_BR"
    assert i18n.t("a") == "Á"
    assert i18n.normalize("EN") == "en" and i18n.normalize(None) == "tr"


def test_a_code_that_is_not_a_language_code_opens_no_file(monkeypatch,
                                                          capsys):
    opened = []
    real = i18n._load
    monkeypatch.setattr(i18n, "_load",
                        lambda code: opened.append(code) or real(code))
    for bad in ("../../config", "C:/x", "tr/../en", "a" * 30):
        assert i18n.set_language(bad) == "tr"
        assert i18n.language() == "tr"
    assert not [c for c in opened if c not in ("tr", "en")], opened
    assert capsys.readouterr().err.isascii()


def test_the_unknown_language_notice_is_in_turkish_and_english():
    assert i18n.set_language("xx") == "tr"
    lines = i18n.notice().splitlines()
    assert len(lines) == 2
    assert "Türkçe açıldı" in lines[0] and "'xx'" in lines[0]
    assert lines[1] == ("Language file not found: 'xx'. The program opened"
                        " in Turkish.")


def test_the_alarm_sentence_never_becomes_its_key(monkeypatch):
    from eye_gaze import alarm
    assert alarm.alarm_text() == alarm.ALARM_TEXT == "Yardım edin!"
    i18n.set_language("en")
    assert alarm.alarm_text() == "Help me!"
    # A catalog that cannot be read gives the key back: the built-in
    # sentence stands in (an emergency is no time for "alarm.phrase").
    monkeypatch.setattr(alarm, "_t", lambda key, **kw: key)
    assert alarm.alarm_text() == "Yardım edin!"
    monkeypatch.setattr(alarm, "_t", lambda key, **kw: "")
    assert alarm.alarm_text() == "Yardım edin!"


# --- voices: never the wrong language ------------------------------------------

def _factory(names, made=None):
    """A make_backend stand-in: engine -> voice name (None = unavailable)."""
    def factory(name, hint, ref, speed, volume):
        if made is not None:
            made.append((name, hint))
        voice = names.get(name)
        if voice is None:
            raise RuntimeError("%s yok" % name)
        return (lambda text: b"RIFF" + name.encode()), voice
    return factory


def test_strict_selection_refuses_a_voice_of_another_language():
    from eye_gaze import tts_backends as tb
    wrong = "onecore/Microsoft Tolga" + tb.WRONG_LANGUAGE_NOTE
    names = {"onecore": wrong, "sapi": "sapi/Microsoft Zira",
             "espeak": "espeak/en", "spd": "spd/en"}
    fn, name = tb.select_backend("auto", "en_US-ljspeech-medium",
                                 factory=_factory(names), strict=True)
    assert name in ("sapi/Microsoft Zira", "espeak/en")
    if tb._is_windows():
        assert tb.NOT_IN_LANGUAGE in tb.last_errors()["onecore"]
    # Turkish keeps the old, lenient rule.
    fn, name = tb.select_backend("onecore", "tr", factory=_factory(names))
    assert name == wrong
    # Strict and nothing in the language: no voice rather than the wrong one.
    fn, name = tb.select_backend("onecore", "en", factory=_factory(names),
                                 strict=True)
    assert fn is None and name == ""


def test_the_speaker_selects_strictly_for_every_language_but_turkish(
        monkeypatch):
    from eye_gaze import speech
    seen = []

    def fake_select(engine, hint, ref, speed=1.0, volume=1.0, strict=False):
        seen.append(strict)
        return (lambda text: b"RIFF"), "fake/voice"
    monkeypatch.setattr(speech, "select_backend", fake_select)
    for lang, strict in ((None, False), ("tr", False), ("en", True),
                         ("pt_BR", True)):
        sp = speech.Speaker("x", language=lang)
        sp.close()
        assert seen[-1] is strict
        assert sp.language == (lang or "tr")


def test_speaks_language():
    from eye_gaze import speech, tts_backends as tb

    class S:
        ok = True
        engine = "piper/en_US-ljspeech-medium"
    assert speech.speaks_language(S())
    S.engine = "onecore/Microsoft Tolga" + tb.WRONG_LANGUAGE_NOTE
    assert not speech.speaks_language(S())
    assert not speech.speaks_language(None)


def _voice_files(folder, *stems):
    os.makedirs(folder, exist_ok=True)
    for stem in stems:
        for ext in (".onnx", ".onnx.json"):
            with open(os.path.join(folder, stem + ext), "wb") as f:
                f.write(b"x")


def test_a_downloaded_voice_is_found_beside_the_program_s_own(tmp_path):
    from eye_gaze import tts_backends as tb
    own, got = str(tmp_path / "piper"), str(tmp_path / "models" / "piper")
    _voice_files(own, "tr_TR-dfki-medium")
    _voice_files(got, "en_US-ljspeech-medium")
    assert tb._find_piper_voice("en_US-ljspeech-medium", own, got) == \
        ("en_US-ljspeech-medium.onnx", got)
    assert tb._find_piper_voice("en", own, got) == \
        ("en_US-ljspeech-medium.onnx", got)
    # The Turkish voice that came with the program: exactly as before.
    assert tb._find_piper_voice("tr_TR-dfki-medium", own, got) == \
        ("tr_TR-dfki-medium.onnx", own)
    assert tb._find_piper_voice("tr", own, got) == \
        ("tr_TR-dfki-medium.onnx", own)
    assert tb._find_piper_voice("tr", own, None) == \
        ("tr_TR-dfki-medium.onnx", own)
    # Not downloaded yet: refused, so select_backend moves on to the OS
    # voice of that language.
    with pytest.raises(RuntimeError, match="esleseni yok"):
        tb._find_piper_voice("en_US-ljspeech-medium", own,
                             str(tmp_path / "nothing"))


def test_the_downloaded_voice_folder_is_where_the_downloader_puts_it(
        monkeypatch, tmp_path):
    from eye_gaze import downloader, models_manifest as mm, paths
    from eye_gaze import tts_backends as tb
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "veri"))
    item = mm.items("piper-en-us-ljspeech-medium")[0]
    want = os.path.dirname(downloader.local_path(item))
    # The folder can be the SHARED ASCII fallback (C:\Users\Public\
    # GozFaresi when the data dir is not ASCII), which a packaged copy run
    # on the development machine fills for real (Task 16 smoke): "computed only" means
    # unchanged by the call, not absent.
    existed = os.path.exists(want)
    assert tb.downloaded_piper_dir() == want
    assert os.path.exists(want) == existed     # computed only
    if os.name == "nt" and not want.isascii():
        pytest.fail("a Windows voice folder must be ASCII: %r" % want)


def test_piper_voices_go_to_an_ascii_folder_on_windows(monkeypatch):
    from eye_gaze import downloader, models_manifest as mm, paths
    monkeypatch.setattr(downloader, "_is_windows", lambda: True)
    monkeypatch.setattr(paths, "model_dir",
                        lambda: r"C:\Users\Örnek\AppData\Local\GozFaresi\models")
    monkeypatch.setattr(paths, "ascii_path",
                        lambda sub: "C:/Users/Public/GozFaresi/models/" + sub)
    item = mm.items("piper-en-us-ljspeech-medium")[0]
    root = downloader._root_for(item, create=False)
    assert root.isascii()
    # A plain file elsewhere still goes to model_dir() as before.
    gpu = mm.items("nvidia-gpu-win")[0]
    assert not downloader._root_for(gpu, create=False).isascii()
    # No ASCII root can be named at all: a status query gets model_dir(),
    # not a TypeError from os.path.dirname(None).
    monkeypatch.setattr(paths, "ascii_path", lambda sub: None)
    assert downloader._root_for(item, create=False) == paths.model_dir()


def test_the_voice_note_is_said_only_while_the_voice_is_missing(
        monkeypatch):
    import gaze_driver as gd
    from eye_gaze import downloader
    state = {"s": "missing"}
    monkeypatch.setattr(downloader, "status", lambda name: state["s"])

    class Spk:
        ok = True
        engine = "onecore/Microsoft David"
    note = gd._voice_download_note("en_US-ljspeech-medium", Spk())
    assert note and note.isascii() and "indirilmedi" in note
    assert "Microsoft David" in note
    Spk.ok = False
    assert "yalniz siren" in gd._voice_download_note("en_US-ljspeech-medium",
                                                     Spk())
    state["s"] = "ok"
    assert gd._voice_download_note("en_US-ljspeech-medium", Spk()) is None
    state["s"] = "missing"
    # The Turkish voice comes with the program: nothing to say.
    assert gd._voice_download_note("tr_TR-dfki-medium", Spk()) is None
    assert gd._voice_download_note("tr", Spk()) is None


def test_the_driver_resolves_voice_dictation_and_board_per_language():
    import gaze_driver as gd
    template = dict(FACTORY)
    assert gd._tts_voice(template) == "tr_TR-dfki-medium"
    assert gd._stt_language(template) == "tr"
    assert gd._aac_root(template) == "turkce-aac-paketi"
    assert (gd._tts_voice({}), gd._stt_language({})) == ("tr", "tr")
    i18n.set_language("en")
    assert gd._tts_voice(template) == "en_US-ljspeech-medium"
    assert gd._stt_language(template) == "en"
    assert gd._aac_root(template) == "english-aac-pack"
    assert gd._stt_language({"stt_language": "de"}) == "de"


# --- the alarm: its sentence in a voice of its language, or the siren alone -----

def test_the_alarm_keeps_its_sentence_in_a_degraded_turkish_voice():
    """A Turkish Speaker that fell back to a foreign voice (piper broken, no
    Tolga) still says "Yardim edin!" beside the siren: an accented sentence
    beats the siren alone, and startup_report() already warns about it.
    A non-Turkish Speaker is strict, so its voice is in its language."""
    from eye_gaze import alarm as al, tts_backends as tb
    from tests.test_alarm import _FakePlayer, _RunNow
    player = _FakePlayer()
    core = al.AlarmCore(player, [], log=lambda s: None)
    lines = []
    w = al.AlarmWatcher(core, log=lines.append, thread=_RunNow)

    class Foreign:
        ok = True
        engine = "sapi/Microsoft Zira" + tb.WRONG_LANGUAGE_NOTE
        asked = []

        def synth(self, text):
            type(self).asked.append(text)
            return b"RIFFwav"
    w.attach_speaker(Foreign())
    assert player.voice == b"RIFFwav" and Foreign.asked == ["Yardım edin!"]
    assert lines == []

    i18n.set_language("en")

    class English(Foreign):
        engine = "onecore/Microsoft David"
        asked = []
    w.attach_speaker(English())
    assert English.asked == ["Help me!"]


def test_the_alarm_module_does_not_import_the_speech_code():
    # alarm.py is imported outside any try, before the camera opens: it
    # must not depend on the TTS modules' import-time code.
    import ast
    with open(os.path.join(GAZE, "eye_gaze", "alarm.py"),
              encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    assert not {n for n in names
                if "tts_backends" in n or n.endswith("speech")}, names


# --- the phrase cache: language + voice -------------------------------------------

def test_the_phrase_cache_key_includes_the_language():
    from eye_gaze.phrase_cache import cache_key
    tr = cache_key("espeak/en", "espeak/en", "Yes.")
    assert cache_key("espeak/en", "espeak/en", "Yes.", "", "tr") == tr
    en = cache_key("espeak/en", "espeak/en", "Yes.", "", "en")
    assert en != tr
    assert cache_key("espeak/en", "espeak/en", "Yes.", "", "de") not in (tr,
                                                                        en)


def test_one_voice_caches_each_language_apart(tmp_path):
    from eye_gaze.phrase_cache import PhraseCache

    class Spk:
        engine = voice = "onecore/Microsoft David"
        speaker_wav = ""
        pending = 0

        def __init__(self, language):
            self.language = language
    tr = PhraseCache(Spk("tr"), str(tmp_path))
    en = PhraseCache(Spk("en"), str(tmp_path))
    assert tr._path("OK") != en._path("OK")

    class Old:          # a Speaker from before languages existed
        engine = voice = "onecore/Microsoft David"
        speaker_wav = ""
        pending = 0
    assert PhraseCache(Old(), str(tmp_path))._path("OK") == tr._path("OK")


# --- the English voice in the manifest -----------------------------------------

def test_the_english_voice_is_pinned_like_the_turkish_one():
    # LJ Speech (public domain, trained from scratch: Task 18 R40), never
    # the lessac voice whose data licence could not be confirmed.
    from eye_gaze import models_manifest as mm
    its = mm.items("piper-en-us-ljspeech-medium")
    assert [i.dest_rel for i in its] == [
        "piper/en_US-ljspeech-medium.onnx",
        "piper/en_US-ljspeech-medium.onnx.json"]
    rev = mm._PIPER_REV
    assert re.fullmatch(r"[0-9a-f]{40}", rev)
    for it in its:
        assert it.kind == "file"
        assert it.url == ("https://huggingface.co/rhasspy/piper-voices/"
                          "resolve/%s/en/en_US/ljspeech/medium/%s"
                          % (rev, it.dest_rel.split("/")[1]))
        assert re.fullmatch(r"[0-9a-f]{64}", it.sha256)
    assert [i.size for i in its] == [63531379, 4972]
    assert its[0].sha256 == ("6f52a751e2349abe7a76735eb09dc1875298c77ea2342"
                             "ffd2fef79ff81b87f22")
    assert its[1].sha256 == ("141d612cc0a95ed7efc1ca936b845c2364967f2e9217"
                             "c5dbfcf69fc4d6c65860")
    assert mm.voice_pack("en_US-ljspeech-medium") == "piper-en-us-ljspeech-medium"
    assert mm.voice_pack("en_US-lessac-medium") is None
    assert not any("lessac" in i.url for n in mm.MANIFEST
                   for i in mm.items(n))
    # The Turkish voice comes with the program; nothing else downloads.
    assert mm.voice_pack("tr_TR-dfki-medium") is None
    assert mm.voice_pack("en") is None and mm.voice_pack("") is None
    # Every catalog's own voice is either downloadable or a bundled one.
    for code in i18n.codes():
        voice = i18n.language_default("tts_voice", code)
        assert voice == "tr_TR-dfki-medium" or mm.voice_pack(voice) or \
            len(voice) <= 3, (code, voice)


# --- the English AAC pack --------------------------------------------------------

_PACKS = {
    "turkce-aac-paketi": "english-aac-pack",
    "turkce-aac-acil-saglik": "english-aac-urgent-health",
    "turkce-aac-temel-ihtiyac": "english-aac-basic-needs",
    "turkce-aac-konfor-bakim": "english-aac-comfort-care",
    "turkce-aac-sosyal-duygu": "english-aac-social-feelings",
    "turkce-aac-kisisel": "english-aac-personal",
}


def _shape(path, rename):
    root = ET.parse(path).getroot()
    grid = (root.findtext("Grid/Rows"), root.findtext("Grid/Cols"))
    keys = []
    for k in root.find("Content"):
        change = k.findtext("ChangeKeyboard")
        keys.append((k.get("Row"), k.get("Col"), k.get("BackgroundColor"),
                     k.get("ForegroundColor"),
                     [a.text for a in k.findall("Action")],
                     k.find("Text") is not None,
                     rename.get(change, change) if change else None))
    return grid, keys


def test_the_english_pack_mirrors_the_turkish_one():
    for tr, en in _PACKS.items():
        tr_path = os.path.join(AAC, tr + ".xml")
        en_path = os.path.join(AAC, en + ".xml")
        assert _shape(tr_path, _PACKS) == _shape(en_path, {}), en
        with open(en_path, "r", encoding="utf-8") as f:
            text = f.read()
        assert not (set(text) & TURKISH_LETTERS), en
        assert "turkce-aac" not in text.split("-->", 1)[1], en


def test_the_english_pack_is_complete_and_says_english():
    from eye_gaze.aac import AacNavigator, load_boards
    boards = load_boards(AAC)
    nav = AacNavigator(boards, "english-aac-pack")
    assert [b.key for b in nav.reachable()] == sorted(_PACKS.values())
    phrases = nav.all_phrases()
    assert len(phrases) == len(set(phrases)) + 3     # 3 shared, as Turkish
    assert "This is an emergency, call an ambulance." in phrases
    assert "I'm sorry." in phrases and "I'm feeling low." in phrases
    assert "I'm having trouble breathing." in phrases
    assert all(p.isascii() for p in phrases)
    turkish = AacNavigator(boards, "turkce-aac-paketi").all_phrases()
    assert len(turkish) == len(phrases)
    # Two packs in one folder: each grid warms only its own.
    assert not set(turkish) & set(phrases)
    assert "Acil durum, 112'yi arayın." in turkish
    assert nav.title().startswith("English AAC - Main Menu")


def test_an_english_grid_falls_back_to_the_shipped_english_pack(tmp_path,
                                                                capsys):
    import gaze_driver as gd
    folder = tmp_path / "optikey"
    folder.mkdir()
    for name in ("turkce-aac-paketi", "turkce-aac-acil-saglik"):
        with open(os.path.join(AAC, name + ".xml"), "rb") as f:
            (folder / (name + ".xml")).write_bytes(f.read())
    cfg = dict(FACTORY, aac_dir=str(folder), aac_cache=False)
    g = gd._AacGrid(1920, 1080, cfg, None)
    assert g._nav.board.key == "turkce-aac-paketi"            # Turkish
    capsys.readouterr()
    # The shipped folder holds both packs: the Turkish console still counts
    # the six Turkish boards.
    gd._AacGrid(1920, 1080, dict(FACTORY, aac_dir="", aac_cache=False), None)
    out = capsys.readouterr().out
    assert "AAC izgarasi hazir: 6 klavye, ana menu 'turkce-aac-paketi'" in out
    i18n.set_language("en")
    g = gd._AacGrid(1920, 1080, cfg, None)
    assert g._nav.board.key == "english-aac-pack"
    assert "programla gelen paket" in capsys.readouterr().out
    # A board the caregiver named is never swapped for another language's.
    g = gd._AacGrid(1920, 1080, dict(cfg, aac_root="turkce-aac-acil-saglik"),
                    None)
    assert g._nav.board.key == "turkce-aac-acil-saglik"


# --- dictation -------------------------------------------------------------------

def test_whisper_s_sign_offs_are_dropped_per_language():
    from eye_gaze import stt
    res = {"text": " Thanks for watching!"}
    assert stt._speech_text(res, "en") == ""
    assert stt._speech_text(res, "tr") == "Thanks for watching!"
    assert stt._speech_text({"text": "Thank you."}, "en") == "Thank you."
    tr = {"text": "İzlediğiniz için teşekkürler."}
    assert stt._speech_text(tr, "tr") == ""
    assert stt._speech_text(tr) == ""                 # default: Turkish
    # Whisper also takes the language's name; Turkish in every form.
    for form in ("turkish", "Turkish", "TR", "tr_TR", "tr-TR"):
        assert stt._speech_text(tr, form) == "", form
    assert stt._speech_text(res, "english") == ""
    assert stt._speech_text(tr, "en") == "İzlediğiniz için teşekkürler."


def test_the_transcriber_dictates_in_the_resolved_language():
    from eye_gaze import stt
    calls = []

    class Model:
        def transcribe(self, audio, language=None, fp16=None):
            calls.append(language)
            return {"text": "Thanks for watching"}
    tr = stt.Transcriber(language="en", device="cpu",
                         loader=lambda n, d: Model())
    import numpy as np
    assert tr.transcribe(np.zeros(16000, dtype=np.float32)) == ""
    assert calls == ["en"]


# --- no Turkish letters outside Turkish -----------------------------------------

def test_turkish_letters_appear_only_in_turkish_resources():
    """The program draws no on-screen keyboard of its own (osk.exe, onboard
    and GNOME's keyboard follow the desktop's layout); what it does own -
    the English catalog and the English phrase pack - carries no Turkish
    letter, and the Turkish ones keep theirs."""
    with open(os.path.join(GAZE, "resources", "lang", "en.yaml"),
              encoding="utf-8") as f:
        body = [ln for ln in f if not ln.lstrip().startswith("#")]
    # "Göz Faresi" is the program's name and stays as it is.
    text = "".join(body).replace("Göz Faresi", "").replace("Türkçe", "")
    assert not set(text) & TURKISH_LETTERS
    with open(os.path.join(AAC, "turkce-aac-temel-ihtiyac.xml"),
              encoding="utf-8") as f:
        assert set(f.read()) & TURKISH_LETTERS


# --- the wizard ------------------------------------------------------------------

tk = pytest.importorskip("tkinter")

from tests.test_ui_wizard_flow import (  # noqa: E402,F401  (fixtures)
    _shared_root, data_dir, goto, installed, make_services, make_wizard,
    root)


def test_the_wizard_offers_the_english_voice_only_for_english(
        root, data_dir, installed):
    services = make_services(
        gpu={"name": "Intel UHD", "provider": "DmlExecutionProvider",
             "vram_mb": None, "vram_free_mb": None})
    w, _ = make_wizard(root, services)
    goto(w, "indirmeler")
    assert list(w.current.rows) == ["whisper-small"]
    w.back()
    w.back()
    w.back()
    assert w.current.name == "hosgeldiniz"
    w.set_language("en")
    goto(w, "indirmeler")
    rows = w.current.rows
    assert list(rows) == ["whisper-small", "piper-en-us-ljspeech-medium"]
    rows["piper-en-us-ljspeech-medium"].start()
    assert ("download", "piper-en-us-ljspeech-medium") in services.log


def test_a_language_switch_during_a_download_raises_nothing(
        root, data_dir, installed):
    w, _ = make_wizard(root, make_services())
    goto(w, "indirmeler")
    row = w.current.rows["whisper-small"]
    row.running = True
    row.cancel_event = threading.Event()
    w.set_language("en")                 # every step is built again
    row._progress(5, 10)                 # late reports from the worker
    row._done(("/m/whisper-small", None), row.cancel_event)
    row._done((None, "boom"), row.cancel_event)
    assert w.current.name == "indirmeler"
    assert not row._alive()


def test_the_camera_step_stays_screen_sized_without_a_frame(
        root, data_dir, installed):
    services = make_services()               # open_camera -> None
    w, _ = make_wizard(root, services)
    goto(w, "kamera")
    w.win.update_idletasks()
    step = w.current
    # Seen in the field: 7271x10144 px, every button off the screen.
    assert w.win.winfo_reqwidth() < 2000 and w.win.winfo_reqheight() < 1600
    assert step.image_box.winfo_reqwidth() == step.PREVIEW_W
    assert step.image_box.winfo_reqheight() == step.PREVIEW_H
    # No camera at all: the same.
    services.cameras = []
    step.retry()
    w.win.update_idletasks()
    assert w.win.winfo_reqwidth() < 2000 and w.win.winfo_reqheight() < 1600
