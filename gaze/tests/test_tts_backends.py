# gaze/tests/test_tts_backends.py
import ctypes
import glob
import importlib.util
import io
import os
import pathlib
import shutil
import struct
import sys
import tempfile
import threading
import time
import types
import wave

import pytest

import warnings

from eye_gaze.tts_backends import (
    AUTO_ORDER,
    KNOWN,
    PIPER_VOICE_DIR,
    WRONG_LANGUAGE_NOTE,
    XTTS_LANGUAGES,
    XTTS_MODEL_DIR,
    _hint_tiers,
    _language_prefix,
    _piper_wrong_language,
    _xtts_language,
    last_errors,
    last_request,
    make_backend,
    resolve_piper_voice,
    select_backend,
)

TEXT = "Merhaba, bu bir deneme cumlesidir."


def _factory(available, expect_hint="tr", expect_wav=""):
    """Fake make_backend: only names in `available` succeed.

    Also asserts the pass-through arguments, so swapping voice_hint and
    speaker_wav in select_backend cannot stay green.
    """
    def make(name, voice_hint="tr", speaker_wav="", speed=1.0, volume=1.0):
        assert voice_hint == expect_hint
        assert speaker_wav == expect_wav
        if name not in available:
            raise RuntimeError(f"{name} yok")
        return (lambda text: b"WAV:" + text.encode("utf-8"), name)
    return make


# --- selection order -------------------------------------------------------

def test_auto_prefers_piper_then_onecore_then_sapi():
    import eye_gaze.tts_backends as tb
    assert tb.WINDOWS_AUTO_ORDER == ("piper", "onecore", "sapi")
    assert AUTO_ORDER == tb.auto_order()     # this OS's order (Linux: espeak)
    assert "xtts" in KNOWN and "xtts" not in AUTO_ORDER
    assert "xtts" not in tb.LINUX_AUTO_ORDER


@pytest.fixture
def windows_order(monkeypatch):
    """These tests walk the Windows chain (onecore, sapi) with fake
    factories; on Linux auto is piper -> espeak -> spd (test_tts_paths.py)."""
    import eye_gaze.tts_backends as tb
    monkeypatch.setattr(tb, "_is_windows", lambda: True)


def test_auto_picks_first_available(windows_order):
    fn, name = select_backend("auto", factory=_factory({"onecore", "sapi"}))
    assert name == "onecore"
    assert fn("selam") == b"WAV:selam"


def test_auto_falls_through_to_sapi(windows_order):
    fn, name = select_backend("auto", factory=_factory({"sapi"}))
    assert name == "sapi"


def test_no_backend_available_returns_none_and_does_not_raise():
    fn, name = select_backend("auto", factory=_factory(set()))
    assert fn is None
    assert name == ""


def test_explicit_engine_is_not_downgraded():
    """Acikca 'xtts' istendiyse sessizce baska motora dusmemeli."""
    fn, name = select_backend("xtts", factory=_factory({"onecore"}))
    assert fn is None
    assert name == ""


def test_explicit_engine_used_when_available():
    fn, name = select_backend("piper", factory=_factory({"piper", "onecore"}))
    assert name == "piper"


def test_unknown_engine_falls_back_to_auto(windows_order):
    fn, name = select_backend("bilinmeyen", factory=_factory({"onecore"}))
    assert name == "onecore"


# --- argument pass-through (Finding 4) -------------------------------------

def test_factory_receives_voice_hint_and_speaker_wav_in_order():
    seen = []

    def factory(name, voice_hint="", speaker_wav="", speed=1.0, volume=1.0):
        seen.append((name, voice_hint, speaker_wav))
        return (lambda text: b""), name

    select_backend("piper", voice_hint="tr-TR", speaker_wav=r"C:\ref.wav",
                   factory=factory)
    assert seen == [("piper", "tr-TR", r"C:\ref.wav")]


def test_auto_passes_arguments_to_every_attempt(windows_order):
    fn, name = select_backend("auto", voice_hint="tr-TR", speaker_wav=r"C:\ref.wav",
                              factory=_factory({"sapi"}, "tr-TR", r"C:\ref.wav"))
    assert name == "sapi"


# --- diagnostics (Finding 3) -----------------------------------------------

def test_failure_reasons_are_recorded_not_swallowed():
    select_backend("auto", factory=_factory(set()))
    errors = last_errors()
    assert set(errors) == set(AUTO_ORDER)
    assert "piper yok" in errors["piper"]
    assert errors["piper"].startswith("RuntimeError")


def test_reasons_distinguish_explicit_engine_from_total_failure():
    select_backend("xtts", factory=_factory({"onecore"}))
    assert last_request() == "xtts"
    assert set(last_errors()) == {"xtts"}

    select_backend("auto", factory=_factory(set()))
    assert last_request() == "auto"
    assert set(last_errors()) == set(AUTO_ORDER)


def test_reasons_kept_for_engines_skipped_before_a_success(windows_order):
    fn, name = select_backend("auto", factory=_factory({"sapi"}))
    assert name == "sapi"
    errors = last_errors()
    assert set(errors) == {"piper", "onecore"}


def test_last_errors_returns_a_copy():
    select_backend("auto", factory=_factory(set()))
    last_errors()["piper"] = "degistirildi"
    assert last_errors()["piper"] != "degistirildi"


def test_select_backend_does_not_raise_on_unexpected_error_type():
    def factory(name, voice_hint="tr", speaker_wav="", speed=1.0,
                volume=1.0):
        raise MemoryError("kaynak yok")

    fn, name = select_backend("auto", factory=factory)
    assert (fn, name) == (None, "")
    assert "MemoryError" in last_errors()["piper"]


# --- real make_backend (Finding 4) -----------------------------------------

def test_make_backend_rejects_unknown_name():
    with pytest.raises(RuntimeError):
        make_backend("bilinmeyen")


def test_xtts_is_known_but_never_auto_selected():
    """xtts agir (~2 GB VRAM); yalnizca adiyla istendiginde yuklenmeli."""
    tried = []

    def make(name, voice_hint="tr", speaker_wav="", speed=1.0, volume=1.0):
        tried.append(name)
        raise RuntimeError("yok")

    select_backend("auto", factory=make)
    assert "xtts" not in tried
    assert tried == list(AUTO_ORDER)


def test_real_onecore_backend_synthesizes_wav():
    pytest.importorskip("winsdk")
    fn, name = make_backend("onecore", "tr")
    assert name.startswith("onecore/")
    wav = fn(TEXT)
    assert wav[:4] == b"RIFF"
    assert len(wav) > 10000


def test_real_onecore_marks_missing_language():
    pytest.importorskip("winsdk")
    fn, name = make_backend("onecore", "zzz-yok")
    assert WRONG_LANGUAGE_NOTE.strip() in name


def test_real_onecore_falls_back_to_the_hints_language_code():
    """The engine-switch trap, against the real voice list.

    Someone dislikes the piper voice and changes ONE line to
    tts_engine: onecore, leaving tts_voice at the piper FILE name. Matching
    only the literal string leaves Windows on its English default and the user
    hears Turkish read in an English accent on every sentence, forever.
    """
    pytest.importorskip("winsdk")
    fn, name = make_backend("onecore", "tr_TR-dfki-medium")
    assert WRONG_LANGUAGE_NOTE.strip() not in name, name
    plain = make_backend("onecore", "tr")[1]
    assert name == plain, f"{name!r} != {plain!r}"


def test_real_onecore_language_tier_does_not_substring_match_a_voice_name():
    """'en' sits inside 'Hortense'. A free substring tier 2 answered a request
    for English with a French voice AND, because it counted as a match,
    suppressed the wrong-language note that would have exposed it."""
    pytest.importorskip("winsdk")
    from winsdk.windows.media.speechsynthesis import SpeechSynthesizer
    languages = {v.language.lower().split("-", 1)[0]
                 for v in SpeechSynthesizer.all_voices}
    fn, name = make_backend("onecore", "en_US-ljspeech-medium")
    if "en" in languages:
        # An English voice exists: tier 2 must find it, and it must be one.
        assert WRONG_LANGUAGE_NOTE.strip() not in name, name
        picked = [v for v in SpeechSynthesizer.all_voices
                  if v.display_name in name]
        assert picked and picked[0].language.lower().startswith("en"), name
    else:
        assert WRONG_LANGUAGE_NOTE.strip() in name, name
    assert "hortense" not in name.lower(), f"dil katmani ad icinde eslesti: {name}"


def test_real_onecore_still_prefers_an_exact_voice_name_over_the_language():
    """Tier 1 must keep beating tier 2: a named voice is still honoured."""
    pytest.importorskip("winsdk")
    from winsdk.windows.media.speechsynthesis import SpeechSynthesizer
    names = [v.display_name for v in SpeechSynthesizer.all_voices
             if not v.language.lower().startswith("tr")]
    if not names:
        pytest.skip("Turkce disi OneCore sesi yok")
    fn, name = make_backend("onecore", names[0])
    assert names[0] in name


def _tempwavs():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "tmp*.wav")))


def test_real_sapi_bare_language_request_is_not_a_free_substring_search():
    """'es' and 'ce' both occur inside a SAPI token id - it is a registry path
    containing "Voices" and "Tokens". Matched loosely, a Spanish request came
    back as English AND counted as a match, so the note that would have exposed
    it was suppressed."""
    pytest.importorskip("comtypes")
    for hint in ("es", "ce"):
        fn, name = make_backend("sapi", hint)
        assert WRONG_LANGUAGE_NOTE.strip() in name, f"{hint} -> {name}"


def test_real_sapi_backend_synthesizes_twice_without_hanging():
    """Regression: pyttsx3's save_to_file path hung forever on the 2nd call."""
    pytest.importorskip("comtypes")
    before = _tempwavs()
    fn, name = make_backend("sapi", "tr")
    assert name.startswith("sapi/")

    first = fn(TEXT)
    second = fn(TEXT)
    for wav in (first, second):
        assert wav[:4] == b"RIFF"
        assert len(wav) > 10000
    assert not _tempwavs() - before, "gecici wav dosyasi silinmedi"


def test_real_sapi_backend_survives_being_called_from_another_thread():
    """Speaker builds on the main thread and calls from its worker thread."""
    pytest.importorskip("comtypes")
    fn, _ = make_backend("sapi", "tr")
    out, err = [], []

    def work():
        try:
            out.append(fn(TEXT))
            out.append(fn(TEXT))
        except BaseException as exc:  # noqa: BLE001
            err.append(f"{type(exc).__name__}: {exc}")

    # daemon: on a regression the assert below fails cleanly instead of
    # blocking the interpreter at exit and turning a failure into a hang.
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout=60)
    assert not t.is_alive(), "sapi backend baska is parcaciginda takildi"
    assert not err, err
    assert len(out) == 2
    assert all(wav[:4] == b"RIFF" for wav in out)


class _FakeToken:
    def GetDescription(self):
        return "Sahte Ses"


class _FakeStream:
    """Holds a REAL open handle on the temp file, like SpFileStream does.

    That is what makes the leak assertion below non-vacuous: on Windows an
    open handle blocks os.remove(), so if Close() is skipped the temp wav
    genuinely survives - exactly the production failure being guarded.
    """

    def __init__(self, log):
        self._log = log
        self._fh = None

    def Open(self, path, mode):
        self._log.append("open")
        self._fh = open(path, "wb")

    def Close(self):
        self._log.append("close")
        if self._fh is not None:
            self._fh.close()
            self._fh = None


class _FakeVoice:
    """SpVoice whose AudioOutputStream assignment always fails."""

    def __init__(self, log):
        self._log = log
        self.Voice = _FakeToken()

    @property
    def AudioOutputStream(self):
        return None

    @AudioOutputStream.setter
    def AudioOutputStream(self, value):
        raise RuntimeError("ses cikisi atanamadi")

    def GetVoices(self):
        return []

    def Speak(self, text):
        self._log.append("speak")


def test_sapi_closes_file_stream_even_when_output_assignment_fails(monkeypatch):
    """An open SpFileStream locks the temp wav; Close() must always run."""
    comtypes_client = pytest.importorskip("comtypes.client")
    log = []

    def fake_create(name, *a, **k):
        return _FakeVoice(log) if name == "SAPI.SpVoice" else _FakeStream(log)

    monkeypatch.setattr(comtypes_client, "CreateObject", fake_create)
    before = _tempwavs()
    fn, _ = make_backend("sapi", "tr")
    with pytest.raises(RuntimeError):
        fn(TEXT)
    assert "close" in log, "SpFileStream kapatilmadi - gecici wav kilitli kalir"
    assert "speak" not in log
    # Non-vacuous: _FakeStream.Open holds a real handle, so without the Close()
    # above this genuinely fails with the locked wav still on disk.
    assert not _tempwavs() - before, "gecici wav dosyasi silinmedi"


# --- piper voice resolution (Task 4) ---------------------------------------

_VOICES = ["tr_TR-dfki-medium.onnx", "tr_TR-dfki-medium.onnx.json",
           "tr_TR-fahrettin-medium.onnx", "tr_TR-fettah-medium.onnx",
           "en_US-ljspeech-medium.onnx"]

# Separate fixture, so _VOICES stays exactly as the brief specified it. This
# one contains a trap: a bare substring search for 'tr' also matches
# 'ru_RU-dmi(tr)i-medium', and 'ru' sorts first.
_VOICES_RU = ["ru_RU-dmitri-medium.onnx", "tr_TR-dfki-medium.onnx"]


def test_piper_voice_exact_name_wins():
    got = resolve_piper_voice("tr_TR-fahrettin-medium", "/v", listdir=lambda d: _VOICES)
    assert got == "tr_TR-fahrettin-medium.onnx"


def test_piper_voice_language_hint_picks_turkish():
    got = resolve_piper_voice("tr", "/v", listdir=lambda d: _VOICES)
    assert got.startswith("tr_TR-")


def test_piper_voice_language_hint_beats_a_substring_match_in_another_language():
    """Non-vacuous version of the test above: here 'tr' matches both names."""
    assert "tr" in "ru_RU-dmitri-medium.onnx"          # the trap is real
    got = resolve_piper_voice("tr", "/v", listdir=lambda d: _VOICES_RU)
    assert got == "tr_TR-dfki-medium.onnx"


def test_piper_voice_empty_hint_defaults_to_turkish_not_to_a_substring():
    got = resolve_piper_voice("", "/v", listdir=lambda d: _VOICES_RU)
    assert got == "tr_TR-dfki-medium.onnx"


def test_piper_voice_ignores_json_sidecar():
    got = resolve_piper_voice("dfki", "/v", listdir=lambda d: _VOICES)
    assert got == "tr_TR-dfki-medium.onnx"


def test_piper_voice_missing_raises():
    with pytest.raises(RuntimeError):
        resolve_piper_voice("tr", "/v", listdir=lambda d: ["en_US-ljspeech-medium.onnx"])


def test_piper_voice_empty_dir_raises():
    with pytest.raises(RuntimeError):
        resolve_piper_voice("tr", "/v", listdir=lambda d: [])


# --- the shared hint-matching rule ------------------------------------------


def test_language_prefix_only_accepts_a_bare_two_letter_code():
    assert _language_prefix("tr") == "tr"
    assert _language_prefix("tr_TR-dfki-medium") == "tr"
    assert _language_prefix("zh-cn") == "zh"
    assert _language_prefix("EN") == "en"
    assert _language_prefix("dfki") == ""          # a speaker, not a language
    assert _language_prefix("Tolga") == ""
    assert _language_prefix("") == ""


def test_hint_tiers_retry_the_language_only_when_it_adds_something():
    # Lower-cased: every candidate it is matched against is lower-cased too.
    assert _hint_tiers("tr_TR-dfki-medium") == ("tr_tr-dfki-medium", "tr")
    # A bare language code is a tier-2 request and ONLY that: run through the
    # loose tier 1 it becomes a two-letter substring search over a whole
    # description, which is how 'es' matches an English SAPI token id.
    assert _hint_tiers("tr") == ("", "tr")
    assert _hint_tiers("EN") == ("", "en")
    assert _hint_tiers("dfki") == ("dfki", "")     # not a language request
    assert _hint_tiers("") == ("", "")             # nothing asked for
    assert _hint_tiers(None) == ("", "")


# --- piper wrong-language reporting, as onecore/sapi already do ------------

def test_piper_marks_wrong_language():
    assert _piper_wrong_language("ru_RU-dmitri-medium.onnx", "tr")
    assert _piper_wrong_language("en_US-ljspeech-medium.onnx", "tr")


def test_piper_does_not_mark_the_requested_language():
    assert not _piper_wrong_language("tr_TR-dfki-medium.onnx", "tr")
    assert not _piper_wrong_language("tr_TR-dfki-medium.onnx", "tr_TR-dfki-medium")
    assert not _piper_wrong_language("en_US-ljspeech-medium.onnx", "en")


def test_piper_speaker_name_hint_is_not_a_language_request():
    """'dfki' asks for a speaker, not a language - must not be flagged."""
    assert not _piper_wrong_language("tr_TR-dfki-medium.onnx", "dfki")


def test_piper_empty_hint_is_a_turkish_request():
    """resolve_piper_voice() defaults an empty hint to 'tr', so the note must
    agree with it - otherwise a Russian voice picked for a blank hint would
    look like success."""
    assert _piper_wrong_language("ru_RU-dmitri-medium.onnx", "")


# --- piper construction-time guards ----------------------------------------
#
# Every one of these refuses BEFORE select_backend() commits, which is the
# only moment a fall-through to Turkish Tolga is still possible. None of them
# calls synthesize(), which is why they are safe to run inside pytest.

def _short_path(path):
    """Windows 8.3 form (C:\\Users\\Örnek Kişi\\... -> C:\\Users\\RNEKK~1\\...)."""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
        return buf.value if n else str(path)
    except Exception:
        return str(path)


@pytest.fixture
def ascii_tmp_dir():
    """A temp directory guaranteed to be ASCII.

    pytest's tmp_path is unusable for the guard tests below: with a Turkish
    user name it lives under C:\\Users\\Örnek, so the non-ASCII check would fire first
    and every other guard test would pass for the wrong reason - both messages
    mention espeak. Falls back to the 8.3 short path, and skips rather than
    lying if neither form is ASCII.
    """
    base = tempfile.gettempdir()
    if not base.isascii():
        base = _short_path(base)
    if not base.isascii():
        pytest.skip("ASCII gecici klasor bulunamadi")
    d = tempfile.mkdtemp(prefix="piper-test-", dir=base)
    if not d.isascii():
        shutil.rmtree(d, ignore_errors=True)
        pytest.skip("ASCII gecici klasor bulunamadi")
    try:
        yield pathlib.Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _fake_voice_dir(root, espeak=True, sidecar=True, phontab=True,
                    voice="tr_TR-sahte-medium"):
    """A structurally healthy piper voice folder (contents are not real)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{voice}.onnx").write_bytes(b"not a real model")
    if sidecar:
        (root / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")
    if espeak:
        d = root / "espeak-ng-data"
        d.mkdir()
        (d / "tr_dict").write_bytes(b"x")     # a partial copy has these...
        if phontab:
            (d / "phontab").write_bytes(b"x")  # ...but stops before this
    return root


def test_piper_backend_refuses_when_espeak_data_is_missing(ascii_tmp_dir, monkeypatch):
    """The crash guard, as a test.

    With espeak-ng-data absent PiperVoice.load() SUCCEEDS and the process is
    then killed on the first synthesize() - no exception, nothing catchable.
    So the backend has to refuse at construction time, where select_backend()
    can still fall through to onecore.
    """
    pytest.importorskip("piper")
    _fake_voice_dir(ascii_tmp_dir / "v", espeak=False)
    monkeypatch.setenv("PIPER_VOICE_DIR", str(ascii_tmp_dir / "v"))
    with pytest.raises(RuntimeError) as excinfo:
        make_backend("piper", "tr")
    assert "espeak verisi" in str(excinfo.value)
    # The non-ASCII message also says "espeak", so pin this to the espeak
    # check: otherwise a non-ASCII temp dir makes this pass for the wrong
    # reason without ever exercising the guard under test.
    assert "ASCII" not in str(excinfo.value)


def test_piper_backend_refuses_a_partially_copied_espeak_dir(ascii_tmp_dir, monkeypatch):
    """isdir() is not enough: KUR-SES.bat copies ~373 files and an interrupted
    copy (Ctrl-C, full disk, antivirus lock) leaves the directory there but
    incomplete. phontab sorts 88th of 125, so a truncated copy most likely has
    the directory and not phontab - and that state kills the process exactly
    like a missing directory does."""
    pytest.importorskip("piper")
    _fake_voice_dir(ascii_tmp_dir / "v", phontab=False)
    monkeypatch.setenv("PIPER_VOICE_DIR", str(ascii_tmp_dir / "v"))
    with pytest.raises(RuntimeError) as excinfo:
        make_backend("piper", "tr")
    assert "espeak verisi" in str(excinfo.value)
    # The non-ASCII message also says "espeak", so pin this to the espeak
    # check: otherwise a non-ASCII temp dir makes this pass for the wrong
    # reason without ever exercising the guard under test.
    assert "ASCII" not in str(excinfo.value)


def test_piper_backend_refuses_a_non_ascii_voice_dir(ascii_tmp_dir, monkeypatch):
    """espeak-ng's C extension silently discards a non-ASCII data dir, falls
    back to the path compiled into the wheel and aborts the process. A Turkish
    user's home is often C:\\Users\\Örnek, so this is one careless env var
    away."""
    pytest.importorskip("piper")
    _fake_voice_dir(ascii_tmp_dir / "sesler-uc")     # healthy in every other way
    bad = ascii_tmp_dir / "sesler-\u00fc\u00e7"
    (ascii_tmp_dir / "sesler-uc").rename(bad)
    monkeypatch.setenv("PIPER_VOICE_DIR", str(bad))
    # The Windows rule (Linux accepts the folder: see test_tts_paths.py).
    import eye_gaze.tts_backends as tb
    monkeypatch.setattr(tb, "_is_windows", lambda: True)
    with pytest.raises(RuntimeError) as excinfo:
        make_backend("piper", "tr")
    assert "ASCII" in str(excinfo.value)
    # The message has to survive a cp1254 console, so it must not smuggle the
    # raw path through - ascii() escapes it.
    assert str(excinfo.value).isascii()


def test_piper_backend_refuses_when_config_sidecar_is_missing(ascii_tmp_dir, monkeypatch):
    """Survivable but permanently mute: load() raises only at the FIRST
    synthesis, long after select_backend() committed to piper, so every F4
    press is silence and Tolga is never reached."""
    pytest.importorskip("piper")
    _fake_voice_dir(ascii_tmp_dir / "v", sidecar=False)
    monkeypatch.setenv("PIPER_VOICE_DIR", str(ascii_tmp_dir / "v"))
    with pytest.raises(RuntimeError) as excinfo:
        make_backend("piper", "tr")
    assert "ayar dosyasi yok" in str(excinfo.value)
    assert "ASCII" not in str(excinfo.value)


def test_piper_guards_let_select_backend_fall_through_to_onecore(ascii_tmp_dir, monkeypatch):
    """What the guards are FOR: the user still gets a Turkish voice."""
    pytest.importorskip("piper")
    pytest.importorskip("winsdk")
    _fake_voice_dir(ascii_tmp_dir / "v", phontab=False)
    monkeypatch.setenv("PIPER_VOICE_DIR", str(ascii_tmp_dir / "v"))
    fn, name = select_backend("auto", "tr")
    assert name.startswith("onecore/")
    assert "espeak verisi" in last_errors()["piper"]
    assert "ASCII" not in last_errors()["piper"]
    assert fn(TEXT)[:4] == b"RIFF"


class _CountingPiperVoice:
    """Counts loads and widens the race window that a missing lock loses."""

    loads = 0
    _guard = threading.Lock()

    @classmethod
    def load(cls, path, espeak_data_dir=None, **kw):
        with cls._guard:
            cls.loads += 1
        time.sleep(0.3)
        return cls()

    def synthesize_wav(self, text, wav_file, syn_config=None):
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\x00\x00" * 8000)


def test_piper_loads_the_model_once_under_concurrent_first_calls(ascii_tmp_dir, monkeypatch):
    """Task 14 makes this concurrency designed-in: PhraseCache.warm() calls
    synth() on a background thread while the Speaker worker may be mid-speak().
    Without the lock this loads three separate 63 MB ONNX sessions."""
    piper = pytest.importorskip("piper")
    monkeypatch.setattr(piper, "PiperVoice", _CountingPiperVoice)
    _CountingPiperVoice.loads = 0
    _fake_voice_dir(ascii_tmp_dir / "v")
    monkeypatch.setenv("PIPER_VOICE_DIR", str(ascii_tmp_dir / "v"))
    fn, _ = make_backend("piper", "tr")

    out, err = [], []

    def work():
        try:
            out.append(fn(TEXT))
        except BaseException as exc:  # noqa: BLE001
            err.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=work, daemon=True) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads)
    assert not err, err
    assert len(out) == 3
    assert all(wav[:4] == b"RIFF" for wav in out)
    assert _CountingPiperVoice.loads == 1, (
        f"model {_CountingPiperVoice.loads} kez yuklendi (beklenen 1)")


def _coqui_installed():
    return importlib.util.find_spec("TTS") is not None


# --- xtts (Task 5) ---------------------------------------------------------
#
# coqui-tts is deliberately NOT installed (voice cloning was deferred, and
# the package has a plausible conflict with the working torch 2.5.1+cu121
# stack). So the synthesis path is unexercised by design, and everything below
# tests the two things that still matter today: that xtts can never reach the
# user by accident, and that a misconfigured clone path is reported by name.


def test_xtts_language_follows_the_voice_hint():
    assert _xtts_language("tr") == ("tr", False)
    assert _xtts_language("") == ("tr", False)          # default is Turkish
    assert _xtts_language("en") == ("en", False)
    assert _xtts_language("tr_TR-dfki-medium") == ("tr", False)


def test_xtts_marks_a_language_the_checkpoint_cannot_speak():
    """XTTS v2 knows 17 languages. Finnish is not one - it would read the text
    with Turkish phonetics and look like success."""
    assert _xtts_language("fi") == ("tr", True)
    assert WRONG_LANGUAGE_NOTE                          # the note it feeds


def test_xtts_chinese_uses_the_code_the_checkpoint_actually_knows():
    """The v2 model card spells Chinese 'zh-cn'. With a bare 'zh' in the table
    a config saying zh would be judged SUPPORTED, get no wrong-language note,
    and then raise inside coqui on every single sentence - the permanent
    silence this helper exists to prevent, inverted."""
    assert "zh-cn" in XTTS_LANGUAGES
    assert "zh" not in XTTS_LANGUAGES
    assert _xtts_language("zh") == ("zh-cn", False)
    assert _xtts_language("zh-cn") == ("zh-cn", False)
    assert _xtts_language("zh_CN") == ("zh-cn", False)


def test_xtts_speaker_name_hint_is_not_a_language_request():
    """Same rule as piper: 'dfki' asks for a speaker, not a language."""
    assert _xtts_language("dfki") == ("tr", False)
    assert _xtts_language("zzz-yok") == ("tr", False)


def test_make_backend_xtts_fails_catchably_when_coqui_is_missing():
    """Not installed here -> an ImportError, which select_backend can catch.

    An uncatchable failure (piper's espeak class of bug) would take the driver
    down with it, and the driver is the user's only way to use the computer.
    """
    if _coqui_installed():
        pytest.skip("coqui-tts kurulu - bu test yoklugunu dogruluyor")
    with pytest.raises(ImportError):
        make_backend("xtts", "tr")


def test_explicit_xtts_is_not_downgraded_and_the_reason_is_kept():
    """select_backend must survive it, must NOT quietly hand over piper or
    Tolga instead, and must be able to say why."""
    if _coqui_installed():
        pytest.skip("coqui-tts kurulu - bu test yoklugunu dogruluyor")
    fn, name = select_backend("xtts", "tr")
    assert (fn, name) == (None, "")
    assert last_request() == "xtts"
    # Exactly one entry: piper/onecore/sapi were never even attempted, so no
    # other voice could have been substituted.
    assert set(last_errors()) == {"xtts"}
    assert "TTS" in last_errors()["xtts"], last_errors()["xtts"]


def test_auto_never_attempts_xtts_even_with_a_speaker_wav_configured():
    """A filled-in tts_speaker_wav must not drag the 2 GB engine into auto."""
    select_backend("auto", "tr", r"C:\ref.wav", factory=_factory(set(), "tr", r"C:\ref.wav"))
    assert "xtts" not in last_errors()
    assert set(last_errors()) == set(AUTO_ORDER)


# --- xtts reference-recording guards, at construction time -----------------
#
# tts_speaker_wav is user-supplied config. These all raise RuntimeError, not
# ImportError, which is itself the assertion that they run BEFORE the coqui
# import - i.e. that the caregiver is told the path is wrong rather than that
# a package is missing. test_xtts_accepts_a_valid_speaker_wav below is what
# keeps them non-vacuous.


def test_xtts_refuses_a_missing_speaker_wav_at_construction(ascii_tmp_dir):
    """Silently synthesizing with a built-in studio speaker instead would be
    the one substitution nobody could ever detect."""
    with pytest.raises(RuntimeError) as excinfo:
        make_backend("xtts", "tr", str(ascii_tmp_dir / "kayit-yok.wav"))
    assert "referans ses dosyasi yok" in str(excinfo.value)
    # Pin it to THIS guard: the non-ASCII message also names the same file.
    assert "ASCII" not in str(excinfo.value)


def test_xtts_refuses_a_non_ascii_speaker_wav(ascii_tmp_dir):
    """The file exists and is readable here - only the path is non-ASCII, so
    this cannot pass by accidentally hitting the missing-file guard."""
    bad = ascii_tmp_dir / "kayit-\u00fc\u00e7.wav"
    bad.write_bytes(b"RIFF" + b"\x00" * 64)
    assert bad.is_file()
    with pytest.raises(RuntimeError) as excinfo:
        make_backend("xtts", "tr", str(bad))
    assert "ASCII" in str(excinfo.value)
    assert "yok" not in str(excinfo.value)
    # Must survive a cp1254 console, so the raw path may not be smuggled out.
    assert str(excinfo.value).isascii()


def test_xtts_refuses_an_empty_speaker_wav(ascii_tmp_dir):
    """An interrupted or still-open recording is a zero-byte file that passes
    every isfile() check and dies inside coqui instead."""
    ref = ascii_tmp_dir / "kayit.wav"
    ref.write_bytes(b"")
    with pytest.raises(RuntimeError) as excinfo:
        make_backend("xtts", "tr", str(ref))
    assert "referans ses dosyasi bos" in str(excinfo.value)


def test_xtts_refuses_a_non_ascii_model_dir(ascii_tmp_dir, monkeypatch):
    """Left to itself coqui downloads 1.8 GB under C:\\Users\\Örnek\\..."""
    monkeypatch.setenv("TTS_HOME", str(ascii_tmp_dir / "model-\u00fc\u00e7"))
    # The Windows rule; on Linux the folder is used as it is (ruling R19).
    import eye_gaze.tts_backends as tb
    monkeypatch.setattr(tb, "_is_windows", lambda: True)
    with pytest.raises(RuntimeError) as excinfo:
        make_backend("xtts", "tr")
    assert "ASCII" in str(excinfo.value)
    assert XTTS_MODEL_DIR in str(excinfo.value)
    assert str(excinfo.value).isascii()


def test_xtts_accepts_a_valid_speaker_wav(ascii_tmp_dir):
    """Non-vacuity guard for the three refusals above: a good reference gets
    all the way past them and only then needs coqui-tts."""
    if _coqui_installed():
        pytest.skip("coqui-tts kurulu - bu test yoklugunu dogruluyor")
    ref = ascii_tmp_dir / "kayit.wav"
    ref.write_bytes(b"RIFF" + b"\x00" * 64)
    with pytest.raises(ImportError):
        make_backend("xtts", "tr", str(ref))


# --- xtts below the coqui boundary, against a stand-in --------------------
#
# coqui-tts is not installed, so the real synthesis path cannot be run. What
# CAN be run is everything on our side of that boundary - the PCM->WAV
# framing, the clipping, the backend name, the lazy load and its lock - which
# is the code the user actually hears and the only part we wrote. The fake
# below encodes our ASSUMPTION about coqui's API (TTS(model).to(device) and
# .tts(text=, language=, speaker=|speaker_wav=) -> list of floats in [-1, 1]).
# That assumption is itself unverified; see the Task 5 report.


class _FakeXtts:
    """Stand-in for TTS.api.TTS. Counts loads to expose a missing lock."""

    loads = 0
    instances = []
    _guard = threading.Lock()

    def __init__(self, model_name):
        with _FakeXtts._guard:
            _FakeXtts.loads += 1
            _FakeXtts.instances.append(self)
        self.model_name = model_name
        self.device = None
        self.calls = []
        time.sleep(0.2)          # widen the race a missing lock would lose

    def to(self, device):
        self.device = device
        return self

    def tts(self, **kw):
        self.calls.append(kw)
        # 2.0/-2.0 are deliberately out of range: int16 conversion WRAPS a
        # sample over 1.0 round to full-scale opposite sign, which the user
        # hears as a click, so the backend has to clip first.
        return [0.0, 0.5, -0.5, 2.0, -2.0]


@pytest.fixture
def fake_coqui(monkeypatch, ascii_tmp_dir):
    pytest.importorskip("torch")
    if _coqui_installed():
        pytest.skip("gercek coqui-tts kurulu - sahte modul enjekte edilmez")
    pkg = types.ModuleType("TTS")
    api = types.ModuleType("TTS.api")
    api.TTS = _FakeXtts
    pkg.api = api
    monkeypatch.setitem(sys.modules, "TTS", pkg)
    monkeypatch.setitem(sys.modules, "TTS.api", api)
    # Keep the 1.8 GB download root out of C:\Users\Public during tests.
    monkeypatch.setenv("TTS_HOME", str(ascii_tmp_dir / "models"))
    _FakeXtts.loads = 0
    _FakeXtts.instances = []
    return _FakeXtts


def test_xtts_produces_a_playable_wav(fake_coqui):
    """winsound.PlaySound(SND_MEMORY) needs a real RIFF header, not raw PCM."""
    fn, name = make_backend("xtts", "tr")
    assert name == "xtts/Ana Florence"
    wav = fn(TEXT)
    assert wav[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav)) as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 24000
        frames = wf.readframes(wf.getnframes())
    assert len(frames) == 5 * 2


def test_xtts_clips_instead_of_wrapping_loud_samples(fake_coqui):
    """Without the clip, 2.0 * 32767 wraps to -2 in int16 and the user hears a
    full-scale click in the middle of the sentence."""
    fn, _ = make_backend("xtts", "tr")
    with wave.open(io.BytesIO(fn(TEXT))) as wf:
        samples = struct.unpack("<5h", wf.readframes(wf.getnframes()))
    assert samples[0] == 0
    assert samples[3] == 32767, "tepe ornek sarmalandi (kirpilmadi)"
    assert samples[4] == -32767, "tepe ornek sarmalandi (kirpilmadi)"


def test_xtts_replaces_nan_samples_instead_of_writing_garbage(fake_coqui, monkeypatch):
    """np.clip passes NaN straight through (every comparison with NaN is
    False), so (nan * 32767).astype('<i2') is undefined plus a RuntimeWarning.
    XTTS emits NaN audio when the reference recording is poor - exactly the
    input class this backend accepts."""
    monkeypatch.setattr(
        _FakeXtts, "tts",
        lambda self, **kw: [float("nan"), 0.5, float("inf"), float("-inf")])
    fn, _ = make_backend("xtts", "tr")
    with warnings.catch_warnings():
        # Non-vacuity: without nan_to_num numpy warns here, and this turns
        # that warning into a failure instead of a silent pass.
        warnings.simplefilter("error", RuntimeWarning)
        wav = fn(TEXT)
    with wave.open(io.BytesIO(wav)) as wf:
        samples = struct.unpack("<4h", wf.readframes(wf.getnframes()))
    assert samples[0] == 0, "NaN ornek int16'ya cop olarak yazildi"
    assert samples[1] == 16383
    assert samples[2] == 32767
    assert samples[3] == -32767


def test_xtts_asks_for_turkish_and_the_builtin_speaker_when_not_cloning(fake_coqui):
    fn, _ = make_backend("xtts", "tr")
    fn(TEXT)
    kw = fake_coqui.instances[-1].calls[-1]
    assert kw["text"] == TEXT
    assert kw["language"] == "tr"
    assert kw["speaker"] == "Ana Florence"
    assert "speaker_wav" not in kw


def test_xtts_clone_passes_the_reference_recording_through(fake_coqui, ascii_tmp_dir):
    """The whole point of the feature: the user's own voice, not a studio one."""
    ref = ascii_tmp_dir / "kayit.wav"
    ref.write_bytes(b"RIFF" + b"\x00" * 64)
    fn, name = make_backend("xtts", "tr", str(ref))
    assert name == "xtts/klon"
    fn(TEXT)
    kw = fake_coqui.instances[-1].calls[-1]
    assert kw["speaker_wav"] == str(ref)
    assert "speaker" not in kw, "yerlesik ses klonun uzerine yaziliyor"


def test_xtts_marks_an_unsupported_language_in_the_backend_name(fake_coqui):
    fn, name = make_backend("xtts", "fi")          # Finnish: not in the 17
    assert name.endswith(WRONG_LANGUAGE_NOTE)
    fn(TEXT)
    assert fake_coqui.instances[-1].calls[-1]["language"] == "tr"


def test_xtts_defers_all_loading_until_the_first_synthesis(fake_coqui, ascii_tmp_dir):
    """Speaker.__init__ selects the backend on the main thread before the gaze
    loop starts, so construction must not touch the 1.8 GB checkpoint."""
    fn, _ = make_backend("xtts", "tr")
    assert fake_coqui.loads == 0
    assert not (ascii_tmp_dir / "models").exists()
    fn(TEXT)
    assert fake_coqui.loads == 1
    assert (ascii_tmp_dir / "models").is_dir()
    assert os.environ["TTS_HOME"] == str(ascii_tmp_dir / "models")


def test_xtts_loads_the_model_once_under_concurrent_first_calls(fake_coqui):
    """Same designed-in concurrency as piper (Task 14's PhraseCache.warm()
    against the Speaker worker) - except here a second load is ~2 GB of VRAM
    the machine does not have spare."""
    fn, _ = make_backend("xtts", "tr")
    out, err = [], []

    def work():
        try:
            out.append(fn(TEXT))
        except BaseException as exc:  # noqa: BLE001
            err.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=work, daemon=True) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads)
    assert not err, err
    assert len(out) == 3
    assert all(wav[:4] == b"RIFF" for wav in out)
    assert fake_coqui.loads == 1, f"model {fake_coqui.loads} kez yuklendi"


def test_real_piper_backend_synthesizes_wav_three_times():
    """Same lazy-holder reuse bug shape that hit sapi in Task 2."""
    pytest.importorskip("piper")
    # Read the SAME directory the backend will read. Checking the module
    # constant instead would let a PIPER_VOICE_DIR pointing at a partial or
    # non-ASCII folder wave this test through - and the run would then be
    # killed inside synthesize_wav(), taking pytest down with no traceback.
    voice_dir = os.environ.get("PIPER_VOICE_DIR", PIPER_VOICE_DIR)
    if not os.path.isfile(os.path.join(voice_dir, "espeak-ng-data", "phontab")):
        pytest.skip("piper sesleri kurulu degil")
    if not voice_dir.isascii():
        pytest.skip("piper ses klasoru ASCII disi")
    fn, name = make_backend("piper", "tr")
    assert name.startswith("piper/tr_TR-")
    for _ in range(3):
        wav = fn(TEXT)
        assert wav[:4] == b"RIFF"
        assert len(wav) > 10000


def test_real_onecore_marks_a_bare_language_code_that_has_no_voice():
    """The companion half of the tier fix, which had no test at all - it was
    mutated away during review and 89 tests stayed green.

    A bare two-letter hint is a TIER 2 request only, so `hint` is empty by
    construction. Testing `hint` alone therefore silences the wrong-language
    note for exactly the requests that are nothing but a language - the user
    asks for a language Windows does not have, gets the English default, and
    is told nothing.
    """
    pytest.importorskip("winsdk")
    fn, name = make_backend("onecore", "zz")     # no voice has language "zz"
    assert WRONG_LANGUAGE_NOTE.strip() in name, name


def test_real_onecore_does_not_mark_a_language_it_does_have():
    """Non-vacuity: the note must not be attached to every request."""
    pytest.importorskip("winsdk")
    from winsdk.windows.media.speechsynthesis import SpeechSynthesizer
    have = {v.language.lower().split("-", 1)[0]
            for v in SpeechSynthesizer.all_voices}
    lang = "tr" if "tr" in have else sorted(have)[0]
    fn, name = make_backend("onecore", lang)
    assert WRONG_LANGUAGE_NOTE.strip() not in name, name


# --- speech rate: "daha yavas ve anlasilir olsun" ---------------------------
#
# Field report: the AAC phrases were understandable to the machine but not
# comfortably to a person - "su istiyorum" heard as "u itiyorum". The audio
# itself measured clean (one single sample at full scale, so normalisation
# rather than clipping); what is missing is TIME. piper's length_scale is the
# knob: bigger is slower.


@pytest.fixture
def dfki_voice_dir(ascii_tmp_dir, monkeypatch):
    """A fake but structurally healthy tr_TR-dfki-medium voice folder.

    These tests only look at the engine NAME and the SynthesisConfig, never at
    real audio, yet they used to read the real voice folder - green on this
    machine because KUR-SES.bat had filled C:\\Users\\Public\\piper-voices,
    red on any machine without it (the Linux suite was the first such run).
    """
    d = _fake_voice_dir(ascii_tmp_dir / "v", voice="tr_TR-dfki-medium")
    monkeypatch.setenv("PIPER_VOICE_DIR", str(d))
    return d


def test_speed_reaches_the_piper_backend(dfki_voice_dir):
    pytest.importorskip("piper")
    seen = {}

    class FakeVoice:
        @staticmethod
        def load(path, espeak_data_dir=None):
            return FakeVoice()

        def synthesize_wav(self, text, wf, syn_config=None):
            seen["cfg"] = syn_config
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(b"\x00\x00" * 100)

    import piper
    import eye_gaze.tts_backends as tb
    real = piper.PiperVoice
    piper.PiperVoice = FakeVoice
    try:
        fn, name = tb.make_backend("piper", "tr_TR-dfki-medium", "", speed=1.4)
        fn("deneme")
    finally:
        piper.PiperVoice = real
    assert seen["cfg"] is not None, "SynthesisConfig hic verilmedi"
    assert abs(seen["cfg"].length_scale - 1.4) < 1e-6, seen["cfg"]


def test_a_changed_speed_changes_the_engine_name(dfki_voice_dir):
    """The phrase cache is keyed on engine+voice+text. Without this, slowing
    the voice down would leave every AAC phrase playing from a WAV rendered at
    the OLD speed - the setting would look like it did nothing."""
    pytest.importorskip("piper")
    fast = make_backend("piper", "tr_TR-dfki-medium", "", speed=1.0)[1]
    slow = make_backend("piper", "tr_TR-dfki-medium", "", speed=1.3)[1]
    assert fast != slow, (fast, slow)
    assert "1.3" in slow, slow


def test_the_default_speed_leaves_the_name_clean(dfki_voice_dir):
    """Non-vacuity: the suffix must not appear when nothing was changed."""
    pytest.importorskip("piper")
    assert make_backend("piper", "tr_TR-dfki-medium", "")[1] == \
        "piper/tr_TR-dfki-medium"


def test_select_backend_passes_speed_through():
    seen = {}

    def factory(name, voice_hint="tr", speaker_wav="", speed=1.0, volume=1.0):
        seen["speed"] = speed
        seen["volume"] = volume
        return (lambda t: b"WAV"), name

    select_backend("piper", "tr", "", factory=factory, speed=1.25, volume=0.8)
    assert seen == {"speed": 1.25, "volume": 0.8}


def test_a_changed_volume_changes_the_engine_name(dfki_voice_dir):
    """Same reason as speed: the phrase cache is keyed on the engine name, and
    after a tts_volume change the AAC phrases kept playing at the OLD volume."""
    pytest.importorskip("piper")
    loud = make_backend("piper", "tr_TR-dfki-medium", "", volume=1.0)[1]
    soft = make_backend("piper", "tr_TR-dfki-medium", "", volume=0.8)[1]
    assert loud != soft, (loud, soft)
    assert "0.8" in soft, soft
    assert loud == "piper/tr_TR-dfki-medium"     # default keeps old caches


def test_speed_and_volume_stamps_do_not_collide(dfki_voice_dir):
    pytest.importorskip("piper")
    a = make_backend("piper", "tr_TR-dfki-medium", "", speed=0.8)[1]
    b = make_backend("piper", "tr_TR-dfki-medium", "", volume=0.8)[1]
    assert a != b, (a, b)
