# gaze/tests/test_speech.py
import importlib.util
import sys

import pytest

from eye_gaze.speech import (
    Speaker,
    get_clipboard_text,
    set_clipboard_text,
    startup_report,
)
from eye_gaze.tts_backends import WRONG_LANGUAGE_NOTE

# Tests that talk to the REAL Win32 clipboard (user32/kernel32). The fake-
# backed tests in this file run everywhere.
win32_clipboard = pytest.mark.skipif(
    sys.platform != "win32", reason="Win32 clipboard (user32/kernel32)")


def test_clipboard_roundtrip_turkish_text():
    ok = set_clipboard_text("Merhaba dünya — şğüöçı testi")
    if not ok:
        pytest.skip("pano bu ortamda kullanilamiyor")
    assert get_clipboard_text() == "Merhaba dünya — şğüöçı testi"


def test_speaker_queues_without_blocking():
    s = Speaker(voice_hint="tr")
    s.speak("")          # empty: ignored
    s.speak(None)        # None: ignored
    s.close()            # must not raise / must not hang


def test_speaker_reports_engine_and_survives_no_backend(monkeypatch):
    import eye_gaze.speech as sp
    monkeypatch.setattr(sp, "select_backend", lambda *a, **k: (None, ""))
    s = Speaker()
    assert s.ok is False
    s.speak("bir sey")   # must not raise
    assert s.synth("bir sey") is None
    s.close()


def test_speaker_synth_returns_backend_bytes(monkeypatch):
    import eye_gaze.speech as sp
    monkeypatch.setattr(
        sp, "select_backend",
        lambda *a, **k: (lambda t: b"WAV:" + t.encode("utf-8"), "sahte"))
    s = Speaker()
    assert s.ok is True
    assert s.engine == "sahte"
    assert s.voice == "sahte"
    assert s.synth("agrim var") == b"WAV:agrim var"
    assert s.synth("") is None
    s.close()


def test_speaker_play_wav_does_not_synthesize(monkeypatch):
    import eye_gaze.speech as sp
    calls = []

    def fake_synth(t):
        calls.append(t)
        return b"WAV"

    monkeypatch.setattr(sp, "select_backend", lambda *a, **k: (fake_synth, "sahte"))
    played = []
    monkeypatch.setattr(sp, "_play", lambda wav: played.append(wav))
    s = Speaker()
    s.play_wav(b"HAZIR")
    s.close()          # close() drains the queue before returning
    assert played == [b"HAZIR"]
    assert calls == []


class _NativeAbort(BaseException):
    """A native runtime abort (piper/xtts): NOT an Exception subclass."""


def _wire(monkeypatch, synth):
    """Speaker with a fake backend; returns (played, reported) sinks."""
    import eye_gaze.speech as sp
    monkeypatch.setattr(sp, "select_backend", lambda *a, **k: (synth, "sahte"))
    played, reported = [], []
    monkeypatch.setattr(sp, "_play", lambda wav: played.append(wav))
    monkeypatch.setattr(sp, "_report", lambda msg: reported.append(msg))
    return played, reported


def test_speaker_speak_plays_the_synthesized_bytes(monkeypatch):
    """The primary path: text in, synthesized audio out of the speakers."""
    played, _ = _wire(monkeypatch, lambda t: b"WAV:" + t.encode("utf-8"))
    s = Speaker()
    s.speak("agrim var")
    s.close()
    assert played == [b"WAV:agrim var"]


def test_speaker_keeps_talking_after_a_failed_sentence(monkeypatch):
    """A failed sentence must never kill the worker - it is the user's voice.

    Covers BaseException too: a native backend can abort without raising an
    Exception subclass, and that used to kill the thread silently.
    """
    def flaky(text):
        if text == "patla":
            raise RuntimeError("sentez hatasi")
        if text == "coz":
            raise _NativeAbort("yerel cokme")
        return b"WAV:" + text.encode("utf-8")

    played, reported = _wire(monkeypatch, flaky)
    s = Speaker()
    s.speak("patla")
    s.speak("coz")
    s.speak("iyi")
    s.close()
    assert played == [b"WAV:iyi"], "basarisiz cumle worker'i oldurdu"
    assert s.ok is True
    assert len(reported) == 2, reported


def test_speaker_malformed_queue_item_does_not_kill_the_worker(monkeypatch):
    played, reported = _wire(monkeypatch, lambda t: b"WAV:" + t.encode("utf-8"))
    s = Speaker()
    s._q.put("bozuk")          # not a (kind, payload) pair
    s.speak("iyi")
    s.close()
    assert played == [b"WAV:iyi"]
    assert len(reported) == 1, reported


class _BrokenQueue:
    def get(self):
        raise _NativeAbort("kuyruk coktu")

    def put(self, item):
        raise AssertionError("olen worker'a is eklenmemeli")


def test_speaker_dying_worker_loop_is_not_silent(monkeypatch):
    """A dead worker used to leave ok=True: speak() filled a queue nobody read
    and close() joined a corpse instantly, so the user just heard nothing."""
    _, reported = _wire(monkeypatch, lambda t: b"WAV")
    s = Speaker()
    s.close()                  # stop the real worker, then drive _run by hand
    s._q = _BrokenQueue()
    s._run()                   # must return, not raise
    assert s.ok is False, "olen worker sessizce ok=True kaldi"
    assert reported and "durdu" in reported[-1], reported


def test_speaker_ignores_work_queued_after_close(monkeypatch):
    """Later tasks (AAC grid) may hold a Speaker past teardown."""
    played, _ = _wire(monkeypatch, lambda t: b"WAV:" + t.encode("utf-8"))
    s = Speaker()
    s.close()
    s.speak("gec kalan")
    s.play_wav(b"GEC")
    s.close()                  # still idempotent
    assert s._q.qsize() == 0, "kapandiktan sonra kuyruk buyuyor"
    assert played == []


# --- startup report --------------------------------------------------------
#
# The driver used to build a Speaker and never look at .ok: with no backend
# available the user pressed F4 and heard nothing, forever, with no line
# anywhere naming a reason. Every construction-time guard in tts_backends.py
# refuses on the promise that its refusal surfaces here.


class _Stub:
    """Just the attributes startup_report() reads."""

    def __init__(self, ok, engine="", request="auto", errors=None):
        self.ok = ok
        self.engine = engine
        self.request = request
        self.errors = errors or {}


def test_startup_report_names_the_engine_that_won():
    lines = startup_report(_Stub(True, "piper/tr_TR-dfki-medium", "auto"))
    assert lines[0] == "Seslendirme motoru: piper/tr_TR-dfki-medium"


@pytest.fixture
def windows_source(monkeypatch):
    """The development machine's source checkout: KUR-SES.bat and Tolga exist."""
    import sys as _sys
    import eye_gaze.tts_backends as tb
    from eye_gaze import paths
    monkeypatch.setattr(tb, "_is_windows", lambda: True)
    monkeypatch.delattr(_sys, "frozen", raising=False)
    monkeypatch.delenv(paths.ENV_DATA_DIR, raising=False)


@pytest.fixture
def packaged(monkeypatch, tmp_path):
    """A frozen (installed) build: no KUR-SES.bat next to it."""
    import sys as _sys
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_sys, "executable", str(tmp_path / "GozFaresi.exe"))


def test_startup_report_names_the_real_reason_when_nothing_came_up(
        windows_source):
    """The whole point: a named cause, not just 'kapali'."""
    lines = startup_report(_Stub(
        False, "", "auto",
        {"piper": "RuntimeError: espeak verisi yok/eksik",
         "onecore": "ImportError: winsdk yok",
         "sapi": "OSError: COM baslatilamadi"}))
    text = "\n".join(lines)
    assert "seslendirme KAPALI" in lines[0]
    assert "KUR-SES.bat" in lines[0]
    assert "espeak verisi yok/eksik" in text
    assert "winsdk yok" in text
    assert "COM baslatilamadi" in text


def test_startup_report_reports_the_engine_that_was_actually_requested():
    """An explicitly named engine is never downgraded, so the caregiver has to
    be told that no other voice was even tried."""
    lines = startup_report(_Stub(
        False, "", "xtts", {"xtts": "ImportError: No module named 'TTS'"}))
    text = "\n".join(lines)
    assert "istenen motor: xtts" in text
    assert "No module named 'TTS'" in text
    assert "tts_engine: auto" in text, "duzeltme yolu soylenmiyor"


def test_startup_report_does_not_promise_a_fallback_for_auto():
    """'auto' already tried everything - telling the user to set auto would be
    advice that changes nothing."""
    lines = startup_report(_Stub(False, "", "auto", {"piper": "RuntimeError: yok"}))
    assert not any("tts_engine: auto yaparsaniz" in ln for ln in lines)


def test_startup_report_warns_when_the_voice_is_in_the_wrong_language(
        windows_source):
    """The development machine has NO Turkish SAPI voice: falling through means Turkish
    text read aloud in an English accent, which otherwise looks like success."""
    lines = startup_report(_Stub(
        True, "sapi/Microsoft Zira Desktop" + WRONG_LANGUAGE_NOTE, "auto"))
    text = "\n".join(lines)
    assert "DILDE DEGIL" in text
    assert "tr_TR-dfki-medium" in text or "onecore" in text


def test_startup_report_says_why_a_better_engine_was_skipped():
    """A silent downgrade from the neural voice to robotic Tolga just sounds
    like a bad day unless the reason is printed."""
    lines = startup_report(_Stub(
        True, "onecore/Microsoft Tolga", "auto",
        {"piper": "RuntimeError: piper ses ayar dosyasi yok"}))
    text = "\n".join(lines)
    assert text.startswith("Seslendirme motoru: onecore/Microsoft Tolga")
    assert "piper ses ayar dosyasi yok" in text


def test_startup_report_names_an_unrecognised_engine_value():
    """A typo'd tts_engine ('xtts2', 'XTTS v2') is silently treated as auto by
    select_backend. Alongside a filled-in tts_speaker_wav that means auto runs,
    piper wins, the reference recording is never validated - every Task 5 guard
    bypassed - and the console reports plain success."""
    lines = startup_report(_Stub(True, "piper/tr_TR-dfki-medium", "xtts2"))
    text = "\n".join(lines)
    assert text.startswith("Seslendirme motoru: piper/tr_TR-dfki-medium")
    assert "taninmadi: 'xtts2'" in text
    assert "tts_speaker_wav" in text, "klonlamanin atlandigi soylenmiyor"


def test_startup_report_does_not_cry_wolf_over_a_valid_engine():
    for request in ("auto", "piper", "xtts", "onecore", "sapi"):
        text = "\n".join(startup_report(_Stub(True, "piper/x", request)))
        assert "taninmadi" not in text, request


def test_startup_report_flags_an_unrecognised_engine_on_failure_too():
    lines = startup_report(_Stub(False, "", "pipr", {"piper": "RuntimeError: yok"}))
    assert any("taninmadi: 'pipr'" in ln for ln in lines)


def test_startup_report_does_not_offer_tolga_when_tolga_is_what_failed(
        windows_source):
    """auto's Turkish fallback IS onecore. If onecore is what failed, auto
    lands on sapi - English-accented Turkish on the development machine."""
    text = "\n".join(startup_report(
        _Stub(False, "", "onecore", {"onecore": "ImportError: winsdk yok"})))
    assert "Tolga sesi denenir" not in text, "calismayan cozum oneriliyor"
    assert "KUR-SES.bat" in text
    text = "\n".join(startup_report(
        _Stub(False, "", "piper", {"piper": "RuntimeError: yok"})))
    assert "Tolga sesi denenir" in text, "piper icin oneri dogru ve kalmali"


def test_startup_report_is_ascii_even_with_a_non_ascii_voice_name():
    """The console here is cp1254; a UnicodeEncodeError while reporting the
    failure would hide the failure."""
    lines = startup_report(_Stub(True, "sapi/Türkçe Ses", "auto"))
    assert all(ln.isascii() for ln in lines), lines
    lines = startup_report(_Stub(False, "", "piper",
                                 {"piper": "RuntimeError: şu yok"}))
    assert all(ln.isascii() for ln in lines), lines


def test_startup_report_is_never_empty_when_no_reason_was_recorded():
    lines = startup_report(_Stub(False, "", "auto", {}))
    assert len(lines) >= 2
    assert any("sebep" in ln for ln in lines)


def test_startup_report_survives_a_speaker_without_diagnostics():
    """Backwards compatible with anything holding an older Speaker-like."""
    class _Old:
        ok = False
    assert startup_report(_Old())


def _coqui_installed():
    return importlib.util.find_spec("TTS") is not None


def test_speaker_captures_its_own_selection_diagnostics():
    """startup_report() reads the Speaker, not the module-level last_errors(),
    because a Speaker built later (Task 16's AAC grid) overwrites that state -
    and a report naming another engine's failure is worse than none."""
    if _coqui_installed():
        pytest.skip("coqui-tts kurulu - bu test yoklugunu dogruluyor")
    first = Speaker(engine="xtts")          # coqui absent -> cannot come up
    first.close()
    assert first.ok is False
    assert first.request == "xtts"
    assert set(first.errors) == {"xtts"}

    second = Speaker(engine="auto")         # rewrites the module-level state
    second.close()
    assert first.request == "xtts", "teshis baska bir Speaker tarafindan ezildi"
    assert set(first.errors) == {"xtts"}


def test_speaker_reports_a_named_reason_when_the_engine_cannot_come_up():
    """End to end, the exact path the driver takes: config asks for xtts,
    coqui is not installed, and the console gets a cause instead of silence."""
    if _coqui_installed():
        pytest.skip("coqui-tts kurulu - bu test yoklugunu dogruluyor")
    s = Speaker(voice_hint="tr", engine="xtts", speaker_wav="")
    s.close()
    text = "\n".join(startup_report(s))
    assert "istenen motor: xtts" in text
    # NOT just "TTS" in text: that matches the "TTS motoru bulunamadi" header
    # and would pass whatever the reason was. Pin it to the reason itself.
    assert "xtts kullanilamadi:" in text, text
    assert "No module named" in text, text
    assert text.isascii()


# --- synth() failures were the last silent path -----------------------------
#
# Task 3 moved the silent-failure handling out of the worker thread and left it
# here, deferred to the task that would actually use synth(): the phrase-cache
# warmer. A phrase that never renders is skipped for good - warm() only looks at
# whether the file exists - so the user finds that one sentence is slow forever
# and nothing anywhere says why.


def _speaker_with(monkeypatch, synth):
    import eye_gaze.speech as sp
    monkeypatch.setattr(sp, "select_backend", lambda *a, **k: (synth, "sahte"))
    return sp.Speaker("tr")


def test_a_failed_synth_is_reported(monkeypatch):
    import eye_gaze.speech as sp
    said = []
    monkeypatch.setattr(sp, "_report", lambda m: said.append(m))

    def boom(text):
        raise RuntimeError("model yok")

    s = _speaker_with(monkeypatch, boom)
    assert s.synth("Su istiyorum") is None
    s.close()
    assert any("Su istiyorum" in m or "RuntimeError" in m for m in said), said


def test_a_working_synth_reports_nothing(monkeypatch):
    """Non-vacuity: the report must not be the answer to every call."""
    import eye_gaze.speech as sp
    said = []
    monkeypatch.setattr(sp, "_report", lambda m: said.append(m))
    s = _speaker_with(monkeypatch, lambda text: b"WAV")
    assert s.synth("Selam") == b"WAV"
    s.close()
    assert said == []


def test_the_same_failure_is_not_repeated_for_every_phrase(monkeypatch):
    """The warmer runs through dozens of phrases; one broken backend would
    otherwise fill the console with the same line and bury everything else."""
    import eye_gaze.speech as sp
    said = []
    monkeypatch.setattr(sp, "_report", lambda m: said.append(m))

    def boom(text):
        raise RuntimeError("model yok")

    s = _speaker_with(monkeypatch, boom)
    for i in range(20):
        s.synth(f"cumle {i}")
    s.close()
    assert len(said) == 1, said


# --- Win32 handles are 64-bit; ctypes assumes 32 ----------------------------
#
# Without argtypes/restype ctypes treats every handle and pointer as a C int.
# Windows hands out clipboard/global handles well above 4 GB on the development machine
# (measured: 0x1c1849e0008), so they were being TRUNCATED. GetClipboardData
# came back 0 - "seslendirilecek metin yok" - and the truncated value that did
# survive pointed into unmapped memory: an access violation that killed the
# whole driver, taking the cursor with it. F3 dictation only exposed it: it is
# the first thing that puts our OWN handle on the clipboard.


@win32_clipboard
def test_the_clipboard_prototypes_are_declared():
    import ctypes

    import eye_gaze.speech as sp
    u, k = sp._win32()
    assert u is not None and k is not None
    for fn in (u.GetClipboardData, u.SetClipboardData, k.GlobalAlloc,
               k.GlobalLock):
        assert fn.restype is ctypes.c_void_p, fn


@win32_clipboard
def test_a_long_turkish_text_survives_the_clipboard_round_trip():
    """A large allocation is what pushes the handle above 4 GB, which is what
    made the truncation visible in the first place."""
    from eye_gaze.speech import get_clipboard_text, set_clipboard_text
    metin = ("Ağrım var, İlacımı istiyorum. Şu an çok üşüyorum. " * 4000)
    assert set_clipboard_text(metin) is True
    assert get_clipboard_text() == metin


@win32_clipboard
def test_a_short_text_round_trips_too():
    from eye_gaze.speech import get_clipboard_text, set_clipboard_text
    set_clipboard_text("Merhaba")
    assert get_clipboard_text() == "Merhaba"


# The guard under test is ctypes' Windows SEH translation: reading unmapped
# memory raises OSError there. On Linux the same read is a SIGSEGV that kills
# pytest itself (measured in Docker), so there is nothing to test.
@pytest.mark.skipif(sys.platform != "win32",
                    reason="access violation -> OSError is Windows SEH only")
def test_reading_a_bad_clipboard_pointer_returns_empty_instead_of_raising(
        monkeypatch):
    """The exact crash: whatever another program left on the clipboard, this
    function's contract is '' - never an exception into the gaze loop."""
    import eye_gaze.speech as sp

    class FakeKernel:
        def GlobalLock(self, h):
            return 0x1A1C910          # the address from the real traceback

        def GlobalUnlock(self, h):
            return 1

        def GlobalSize(self, h):
            return 4096

    class FakeUser:
        def OpenClipboard(self, h):
            return 1

        def CloseClipboard(self):
            return 1

        def GetClipboardData(self, fmt):
            return 0x1C1849E0008

    monkeypatch.setattr(sp, "_win32", lambda: (FakeUser(), FakeKernel()))
    # The access violation is deliberate and caught; keep faulthandler from
    # dumping six fake "Windows fatal exception" stacks that hide real ones.
    import faulthandler
    was_enabled = faulthandler.is_enabled()
    faulthandler.disable()
    try:
        assert sp.get_clipboard_text() == ""
    finally:
        if was_enabled:
            faulthandler.enable()


def test_an_empty_clipboard_reads_as_empty(monkeypatch):
    """Non-vacuity: '' must not come from the guard swallowing everything."""
    import eye_gaze.speech as sp

    class FakeUser:
        def OpenClipboard(self, h):
            return 1

        def CloseClipboard(self):
            return 1

        def GetClipboardData(self, fmt):
            return None

    monkeypatch.setattr(sp, "_win32", lambda: (FakeUser(), object()))
    assert sp.get_clipboard_text() == ""


def test_the_clipboard_reports_why_it_was_empty(monkeypatch):
    """The F4 handler needs the difference: 'nothing selected' is the user's
    doing, 'another program is holding it' is not."""
    import eye_gaze.speech as sp

    class Held:
        def OpenClipboard(self, h):
            return 0

    monkeypatch.setattr(sp, "_win32", lambda: (Held(), object()))
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    assert sp.get_clipboard_text() == ""
    assert "tutuluyor" in sp.clipboard_problem(), sp.clipboard_problem()


def test_an_empty_clipboard_is_reported_as_empty(monkeypatch):
    import eye_gaze.speech as sp

    class Empty:
        def OpenClipboard(self, h):
            return 1

        def CloseClipboard(self):
            return 1

        def GetClipboardData(self, fmt):
            return None

    monkeypatch.setattr(sp, "_win32", lambda: (Empty(), object()))
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    sp.get_clipboard_text()
    assert sp.clipboard_problem() == "panoda metin yok"


@win32_clipboard
def test_a_successful_read_clears_the_problem():
    """Non-vacuity: a stale reason would make every later failure a lie."""
    from eye_gaze.speech import (clipboard_problem, get_clipboard_text,
                                 set_clipboard_text)
    set_clipboard_text("Merhaba")
    assert get_clipboard_text() == "Merhaba"
    assert clipboard_problem() == ""


def test_a_transient_failure_is_retried(monkeypatch):
    """Measured on the development machine: OpenClipboard succeeds and GetClipboardData
    still returns NULL for a moment after something else writes. One attempt
    made F4 unreliable in a way that looked like the user's fault."""
    import eye_gaze.speech as sp
    calls = {"n": 0}

    class Flaky:
        def OpenClipboard(self, h):
            return 1

        def CloseClipboard(self):
            return 1

        def GetClipboardData(self, fmt):
            calls["n"] += 1
            return None if calls["n"] < 4 else 0x1234

    class Kernel:
        def GlobalLock(self, h):
            return 0x5678

        def GlobalUnlock(self, h):
            return 1

        def GlobalSize(self, h):
            return 8

    monkeypatch.setattr(sp, "_win32", lambda: (Flaky(), Kernel()))
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    monkeypatch.setattr(sp.ctypes, "wstring_at", lambda p, n: "Gecti\x00\x00\x00")
    assert sp.get_clipboard_text() == "Gecti"
    assert calls["n"] == 4


def test_speaker_remembers_its_reference_recording(monkeypatch):
    """The phrase cache reads it to key clones by recording, not by name."""
    import eye_gaze.speech as sp
    monkeypatch.setattr(sp, "select_backend",
                        lambda *a, **k: (lambda t: b"WAV", "xtts/klon"))
    s = Speaker(engine="xtts", speaker_wav="  C:/ses/ben.wav ")
    assert s.speaker_wav == "C:/ses/ben.wav"
    s.close()


# --- piper failing at runtime falls back to Tolga instead of going mute ------


def _chain(monkeypatch, piper, onecore=None, engine="auto"):
    import eye_gaze.speech as sp
    import eye_gaze.tts_backends as tb
    # The Windows chain (piper -> onecore); Linux's is piper -> espeak -> spd.
    monkeypatch.setattr(tb, "_is_windows", lambda: True)
    made = []

    def select(name="auto", *a, **k):
        made.append(name)
        if name in ("auto", "piper"):
            return piper, "piper/tr_TR-dfki-medium@1.25x"
        if name == "onecore" and onecore is not None:
            return onecore, "onecore/Microsoft Tolga@1.25x"
        return None, ""
    monkeypatch.setattr(sp, "select_backend", select)
    said = []
    monkeypatch.setattr(sp, "_report", lambda m: said.append(m))
    return sp.Speaker("tr", engine=engine), made, said


def test_a_failing_piper_sentence_is_said_with_tolga_at_once(monkeypatch):
    def piper(text):
        raise RuntimeError("onnxruntime")
    s, made, said = _chain(monkeypatch, piper, lambda t: b"TOLGA:" + t.encode())
    assert s.synth("Su istiyorum") == b"TOLGA:Su istiyorum"
    assert s.engine.startswith("piper")          # one failure: not switched
    s.close()
    assert any("Tolga" in m for m in said)


def test_piper_failing_again_and_again_switches_for_the_session(monkeypatch):
    calls = {"piper": 0}

    def piper(text):
        calls["piper"] += 1
        raise RuntimeError("bozuk model")
    s, made, said = _chain(monkeypatch, piper, lambda t: b"T")
    for _ in range(s.SWITCH_AFTER):
        assert s.synth("Merhaba") == b"T"
    assert s.engine.startswith("onecore")
    n = calls["piper"]
    assert s.synth("Merhaba") == b"T" and calls["piper"] == n   # piper left
    assert made.count("onecore") == 1                            # built once
    s.close()


def test_a_named_engine_is_never_swapped(monkeypatch):
    def piper(text):
        raise RuntimeError("x")
    s, made, said = _chain(monkeypatch, piper, lambda t: b"T", engine="piper")
    assert s.synth("Merhaba") is None
    assert "onecore" not in made
    s.close()


def test_a_working_piper_resets_the_failure_count(monkeypatch):
    seq = iter([RuntimeError("1"), b"OK", RuntimeError("2"), b"OK",
                RuntimeError("3")])

    def piper(text):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v
    s, made, said = _chain(monkeypatch, piper, lambda t: b"T")
    for _ in range(5):
        s.synth("a")
    assert s.engine.startswith("piper")
    s.close()


# --- packaged builds and Linux: no KUR-SES.bat, no Tolga ----------------------


def _texts(*stubs):
    return ["\n".join(startup_report(st)) for st in stubs]


_FAILURES = (
    _Stub(False, "", "auto", {"piper": "RuntimeError: yok"}),
    _Stub(False, "", "onecore", {"onecore": "ImportError: winsdk yok"}),
    _Stub(False, "", "piper", {"piper": "RuntimeError: yok"}),
    _Stub(True, "sapi/Microsoft Zira Desktop" + WRONG_LANGUAGE_NOTE, "auto"),
)


def test_a_packaged_build_points_at_the_download_page(packaged, monkeypatch):
    import eye_gaze.tts_backends as tb
    for windows in (True, False):
        monkeypatch.setattr(tb, "_is_windows", lambda: windows)
        for text in _texts(*_FAILURES):
            assert "KUR-SES" not in text, text
            assert all(line.isascii() for line in text.splitlines())
        assert "Ayarlar > Indirmeler" in _texts(_FAILURES[0])[0]


def test_linux_reports_never_offer_tolga(monkeypatch):
    import eye_gaze.tts_backends as tb
    monkeypatch.setattr(tb, "_is_windows", lambda: False)
    for text in _texts(*_FAILURES):
        assert "Tolga" not in text and "KUR-SES" not in text, text
    assert "espeak" in _texts(_FAILURES[3])[0]
    assert "spd-say" in _texts(_FAILURES[2])[0]


def test_startup_report_says_what_spd_say_cannot_do():
    text = "\n".join(startup_report(_Stub(True, "spd/tr", "auto")))
    assert "spd-say" in text and "siren" in text
    assert text.isascii()


def test_stop_also_cancels_a_voice_that_speaks_by_itself(monkeypatch):
    import eye_gaze.speech as sp
    stopped = []

    def fn(text):
        return b"RIFF"
    fn.speaks_itself = True
    fn.stop = lambda: stopped.append(1)
    monkeypatch.setattr(sp, "select_backend", lambda *a, **k: (fn, "spd/tr"))
    monkeypatch.setattr(sp, "_stop_playback", lambda: None)
    s = sp.Speaker("tr")
    try:
        s.stop()
        assert stopped == [1]
    finally:
        s.close()
