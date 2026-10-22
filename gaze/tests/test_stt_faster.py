# gaze/tests/test_stt_faster.py
"""The faster-whisper (CTranslate2) dictation path of the packaged builds.

openai-whisper needs torch, which the packages do not carry, so there the
model is faster-whisper's: a folder the downloader fetches (whisper-<size>),
opened with WhisperModel(dir, device, compute_type). Source mode on the
user's machine keeps openai-whisper exactly as before. No test touches a GPU:
CUDA is always simulated.
"""
import sys
import threading
import types

import numpy as np
import pytest

import eye_gaze.stt as stt
from eye_gaze import accel, downloader
from eye_gaze.stt import Transcriber

AUDIO = np.full(16000, 0.05, dtype=np.float32)


class _Seg:
    def __init__(self, text, no_speech_prob=0.05, avg_logprob=-0.2):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


class FakeWhisperModel:
    """Stands in for faster_whisper.WhisperModel."""

    built = []              # (model_dir, device, compute_type)
    segments = [_Seg(" Merhaba.")]
    fail_build_on = ()      # devices whose construction raises
    fail_run_on = ()        # devices whose transcription raises
    runs = []

    def __init__(self, model_dir, device="auto", compute_type="default"):
        FakeWhisperModel.built.append((model_dir, device, compute_type))
        if device in FakeWhisperModel.fail_build_on:
            raise RuntimeError("CUDA failed with error out of memory")
        self.device = device

    def transcribe(self, audio, language=None, **kw):
        FakeWhisperModel.runs.append((self.device, language))
        FakeWhisperModel.options = kw
        if self.device in FakeWhisperModel.fail_run_on:
            raise RuntimeError("Library cublas64_12.dll is not found")

        def gen():
            # Lazy like the real one: decoding errors surface while iterating.
            for s in FakeWhisperModel.segments:
                yield s
        return gen(), types.SimpleNamespace(language=language)


@pytest.fixture
def faster(monkeypatch):
    """No openai-whisper, a fake WhisperModel, a model folder in place."""
    FakeWhisperModel.built = []
    FakeWhisperModel.runs = []
    FakeWhisperModel.segments = [_Seg(" Merhaba.")]
    FakeWhisperModel.fail_build_on = ()
    FakeWhisperModel.fail_run_on = ()
    monkeypatch.setattr(stt, "_openai_whisper_installed", lambda: False)
    monkeypatch.setattr(stt, "_whisper_model_class", lambda: FakeWhisperModel)
    monkeypatch.setattr(stt, "_cuda_available", lambda: False)
    calls = []

    def ensure(name, progress=None, cancel=None):
        calls.append(name)
        return "/modeller/" + name
    monkeypatch.setattr(downloader, "ensure", ensure)
    monkeypatch.setattr(downloader, "status", lambda name: "ok")
    preloads = []

    def preload(libdir, need="ctranslate2", refresh=False):
        preloads.append((libdir, need))
        return True
    monkeypatch.setattr(accel, "preload_cuda", preload)
    return types.SimpleNamespace(ensure_calls=calls, preloads=preloads)


def test_faster_adapter_maps_segments(faster):
    # Whisper's own non-speech verdict reaches _speech_text unchanged: the
    # second segment (no_speech_prob 0.9, avg_logprob -2) is dropped.
    FakeWhisperModel.segments = [_Seg(" Su istiyorum."),
                                 _Seg(" Altyazi M.K.", 0.9, -2.0)]
    t = Transcriber(model="medium", device="cpu")
    assert t.transcribe(AUDIO) == "Su istiyorum."
    assert faster.ensure_calls == ["whisper-medium"]
    assert FakeWhisperModel.built == [("/modeller/whisper-medium", "cpu",
                                       "int8")]
    assert FakeWhisperModel.runs == [("cpu", "tr")]
    assert t.ok is True and t.last_error == ""


def test_faster_adapter_drops_a_lone_hallucination(faster):
    FakeWhisperModel.segments = [_Seg(" Altyazi M.K.", 0.9, -2.0)]
    t = Transcriber(model="small", device="cpu")
    assert t.transcribe(AUDIO) == ""


def test_openai_whisper_preferred_when_installed(monkeypatch):
    # Source mode on the development machine: openai-whisper is installed and stays
    # the engine - same name ("medium"), same device, same fp16 rule.
    loads = []
    fake = types.ModuleType("whisper")

    class _M:
        def transcribe(self, audio, **kw):
            loads.append(("run", kw))
            return {"text": " Merhaba.", "segments": [
                {"text": " Merhaba.", "no_speech_prob": 0.1,
                 "avg_logprob": -0.2}]}

    def load_model(name, device=None):
        loads.append(("load", name, device))
        return _M()
    fake.load_model = load_model
    monkeypatch.setitem(sys.modules, "whisper", fake)
    monkeypatch.setattr(stt, "_cuda_available", lambda: False)

    def no_faster():
        raise AssertionError("faster-whisper must not be used")
    monkeypatch.setattr(stt, "_whisper_model_class", no_faster)
    monkeypatch.setattr(downloader, "ensure",
                        lambda *a, **k: pytest.fail("no download"))
    monkeypatch.setattr(downloader, "status",
                        lambda *a, **k: pytest.fail("no status query"))
    t = Transcriber(model="medium", device="auto")
    assert t.model_ready() is True
    assert t.transcribe(AUDIO) == "Merhaba."
    assert loads[0] == ("load", "medium", "cpu")
    assert loads[1][1] == {"language": "tr", "fp16": False}


def test_openai_whisper_detection_without_importing_it(monkeypatch):
    # The check runs on an F3 press in the gaze loop: importing whisper (and
    # torch with it) there would freeze the cursor for seconds.
    monkeypatch.delitem(sys.modules, "whisper", raising=False)
    import importlib.util as iu
    seen = []
    monkeypatch.setattr(iu, "find_spec",
                        lambda name: seen.append(name) or object())
    assert stt._openai_whisper_installed() is True
    assert seen == ["whisper"]
    assert "whisper" not in sys.modules
    monkeypatch.setattr(iu, "find_spec", lambda name: None)
    assert stt._openai_whisper_installed() is False


def test_offline_model_sets_turkish_error(faster, monkeypatch):
    def offline(name, progress=None, cancel=None):
        faster.ensure_calls.append(name)
        downloader.last_error = ("İnternete bağlanılamadı: model.bin "
                                 "indirilemedi.")
        return None
    monkeypatch.setattr(downloader, "ensure", offline)
    monkeypatch.setattr(downloader, "last_error", None)
    monkeypatch.setattr(stt, "_cuda_available", lambda: True)
    t = Transcriber(model="medium", device="auto")
    assert t.transcribe(AUDIO) == ""
    assert t.ok is False
    assert t.last_error.startswith("Whisper modeli indirilemedi: ")
    assert "İnternete bağlanılamadı" in t.last_error
    # A missing model is not retried on the CPU (same download, same
    # failure), and nothing was built.
    assert faster.ensure_calls == ["whisper-medium"]
    assert FakeWhisperModel.built == []
    # Not latched for the session: back online, the next try works.
    monkeypatch.setattr(downloader, "ensure",
                        lambda name, progress=None, cancel=None:
                        "/modeller/" + name)
    assert t.transcribe(AUDIO) == "Merhaba."
    assert t.ok is True and t.last_error == ""


def test_unknown_model_size_is_named(faster):
    t = Transcriber(model="large-v3", device="cpu")
    assert t.model_ready() is False
    assert t.transcribe(AUDIO) == ""
    assert "large-v3" in t.last_error
    assert faster.ensure_calls == []


def test_faster_cuda_uses_float16_after_loading_the_gpu_pack(faster,
                                                             monkeypatch):
    monkeypatch.setattr(stt, "_cuda_available", lambda: True)
    t = Transcriber(model="medium", device="auto")
    assert t.transcribe(AUDIO) == "Merhaba."
    assert faster.preloads == [(accel.gpu_lib_dir(), "ctranslate2")]
    assert FakeWhisperModel.built == [("/modeller/whisper-medium", "cuda",
                                       "float16")]
    assert t.device == "cuda"


def test_faster_cuda_build_failure_falls_back_to_cpu_int8_once(
        faster, monkeypatch, capsys):
    monkeypatch.setattr(stt, "_cuda_available", lambda: True)
    FakeWhisperModel.fail_build_on = ("cuda",)
    t = Transcriber(model="medium", device="auto")
    assert t.transcribe(AUDIO) == "Merhaba."
    assert [b[1:] for b in FakeWhisperModel.built] == [("cuda", "float16"),
                                                       ("cpu", "int8")]
    assert t.device == "cpu"
    out = capsys.readouterr().out
    assert "CPU" in out and out.isascii()
    # Once: the next sentence does not rebuild anything.
    assert t.transcribe(AUDIO) == "Merhaba."
    assert len(FakeWhisperModel.built) == 2


def test_faster_missing_gpu_libraries_go_straight_to_cpu(faster, monkeypatch,
                                                         capsys):
    # CTranslate2 loads cuBLAS lazily: a model built "on CUDA" without the
    # libraries only fails at the first sentence. No libraries -> CPU now.
    monkeypatch.setattr(stt, "_cuda_available", lambda: True)
    monkeypatch.setattr(accel, "preload_cuda", lambda *a, **k: False)
    t = Transcriber(model="medium", device="auto")
    assert t.transcribe(AUDIO) == "Merhaba."
    assert [b[1:] for b in FakeWhisperModel.built] == [("cpu", "int8")]
    out = capsys.readouterr().out
    assert "CPU" in out and out.isascii()


def test_faster_cuda_failure_at_first_sentence_retries_on_cpu(faster,
                                                              monkeypatch,
                                                              capsys):
    monkeypatch.setattr(stt, "_cuda_available", lambda: True)
    FakeWhisperModel.fail_run_on = ("cuda",)
    t = Transcriber(model="medium", device="auto")
    assert t.transcribe(AUDIO) == "Merhaba."
    assert [b[1:] for b in FakeWhisperModel.built] == [("cuda", "float16"),
                                                       ("cpu", "int8")]
    assert [r[0] for r in FakeWhisperModel.runs] == ["cuda", "cpu"]
    out = capsys.readouterr().out
    assert "CPU" in out and out.isascii()


def test_faster_cpu_failure_is_reported_not_retried(faster):
    FakeWhisperModel.fail_run_on = ("cpu",)
    t = Transcriber(model="medium", device="cpu")
    assert t.transcribe(AUDIO) == ""
    assert "cublas" in t.last_error
    assert len(FakeWhisperModel.built) == 1


# --- first F3 with no model: download in the background ---------------------


def test_model_ready_reads_the_download_status(faster, monkeypatch):
    states = {"whisper-small": "missing"}
    monkeypatch.setattr(downloader, "status", lambda name: states[name])
    t = Transcriber(model="small")
    assert t.model_ready() is False
    states["whisper-small"] = "partial"
    assert t.model_ready() is False
    states["whisper-small"] = "ok"
    assert t.model_ready() is True


def test_a_custom_loader_needs_no_download(monkeypatch):
    monkeypatch.setattr(stt, "_openai_whisper_installed", lambda: False)
    monkeypatch.setattr(downloader, "status",
                        lambda *a: pytest.fail("no status query"))
    assert Transcriber(loader=lambda n, d: object()).model_ready() is True


def _collect():
    said, lines, alerts = [], [], []
    return (said, lines, alerts, said.append, lines.append,
            lambda: alerts.append(1))


def test_download_runs_in_the_background_and_says_so(faster, monkeypatch):
    gate = threading.Event()
    progressed = threading.Event()

    def slow(name, progress=None, cancel=None):
        progress(0, 1000)
        progress(500, 1000)
        progressed.set()
        gate.wait(10)
        progress(1000, 1000)
        return "/modeller/" + name
    monkeypatch.setattr(downloader, "ensure", slow)
    monkeypatch.setattr(downloader, "status", lambda name: "missing")
    said, lines, alerts, say, report, alert = _collect()
    t = Transcriber(model="medium")
    assert t.start_download(say=say, report=report, alert=alert) is True
    assert progressed.wait(10)
    # Returned at once (the gaze loop called it), still running.
    assert t.downloading is True
    assert t.start_download(say=say, report=report, alert=alert) is False
    assert "%50" in t.download_note()
    gate.set()
    assert t.wait_download(10)
    assert t.downloading is False
    assert any("indiriliyor" in s for s in said)
    assert any("hazır" in s for s in said)
    assert alerts == []
    assert lines and all(line.isascii() for line in lines)
    assert any("whisper-medium" in line for line in lines)


def test_download_failure_is_said_and_can_be_retried(faster, monkeypatch):
    def offline(name, progress=None, cancel=None):
        downloader.last_error = "İnternete bağlanılamadı: model.bin indirilemedi."
        return None
    monkeypatch.setattr(downloader, "ensure", offline)
    monkeypatch.setattr(downloader, "last_error", None)
    said, lines, alerts, say, report, alert = _collect()
    t = Transcriber(model="medium")
    assert t.start_download(say=say, report=report, alert=alert) is True
    assert t.wait_download(10)
    assert alerts == [1]
    assert any("indirilemedi" in s for s in said)
    assert all(line.isascii() for line in lines)
    assert any("indirilemedi" in line for line in lines)
    assert t.last_error.startswith("Whisper modeli indirilemedi: ")
    # The next press may try again.
    assert t.start_download(say=say, report=report, alert=alert) is True
    assert t.wait_download(10)


def test_download_of_an_unknown_size_fails_at_once(faster):
    said, lines, alerts, say, report, alert = _collect()
    t = Transcriber(model="large-v3")
    assert t.start_download(say=say, report=report, alert=alert) is True
    assert t.wait_download(10)
    assert alerts == [1] and said
    assert faster.ensure_calls == []
    assert all(line.isascii() for line in lines)


# --- the driver's F3 gate -----------------------------------------------------


def test_driver_gate_lets_recording_start_when_the_model_is_there(faster):
    import gaze_driver as gd
    said, lines, alerts, say, report, alert = _collect()
    t = Transcriber(model="medium")
    assert gd._stt_model_gate(t, say, report, alert) is True
    assert said == [] and lines == []
    assert gd._stt_model_gate(None, say, report, alert) is True


def test_driver_gate_starts_the_download_instead_of_recording(faster,
                                                              monkeypatch):
    import gaze_driver as gd
    gate = threading.Event()

    def slow(name, progress=None, cancel=None):
        gate.wait(10)
        return "/modeller/" + name
    monkeypatch.setattr(downloader, "ensure", slow)
    monkeypatch.setattr(downloader, "status", lambda name: "missing")
    said, lines, alerts, say, report, alert = _collect()
    t = Transcriber(model="medium")
    assert gd._stt_model_gate(t, say, report, alert) is False
    assert t.downloading is True
    n = len(lines)
    # A second press while it downloads: refused, said on the console.
    assert gd._stt_model_gate(t, say, report, alert) is False
    assert len(lines) == n + 1 and "bekleyin" in lines[-1]
    assert lines[-1].isascii()
    gate.set()
    assert t.wait_download(10)


def test_faster_decoding_options_are_pinned(faster):
    # openai-whisper's transcribe() rules, beam 5, no VAD - not whatever
    # the installed faster-whisper release defaults to.
    t = Transcriber(model="medium", device="cpu")
    t.transcribe(AUDIO)
    assert FakeWhisperModel.options == {
        "beam_size": 5,
        "vad_filter": False,
        "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        "condition_on_previous_text": True,
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
    }


def test_device_label_follows_a_mid_session_move_to_the_cpu(faster,
                                                           monkeypatch):
    monkeypatch.setattr(stt, "_cuda_available", lambda: True)
    FakeWhisperModel.fail_run_on = ("cuda",)
    t = Transcriber(model="medium", device="auto")
    assert t.transcribe(AUDIO) == "Merhaba."
    assert t.device == "cpu"


def test_a_cpu_rebuild_that_fails_too_ends_dictation_once(faster,
                                                         monkeypatch):
    monkeypatch.setattr(stt, "_cuda_available", lambda: True)
    FakeWhisperModel.fail_run_on = ("cuda",)
    t = Transcriber(model="medium", device="auto")
    FakeWhisperModel.fail_build_on = ("cpu",)
    assert t.transcribe(AUDIO) == ""
    assert t.ok is False and t.device == "cpu"
    assert "CPU" in t.last_error
    built = len(FakeWhisperModel.built)
    runs = len(FakeWhisperModel.runs)
    # Next sentence: nothing rebuilt, CUDA never tried again.
    assert t.transcribe(AUDIO) == ""
    assert len(FakeWhisperModel.built) == built
    assert len(FakeWhisperModel.runs) == runs
    assert t.ok is False


def test_stt_fix_hint_never_sends_a_package_user_to_kur_ses(monkeypatch,
                                                            tmp_path):
    import gaze_driver as gd
    import eye_gaze.tts_backends as tb
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(tb, "_is_windows", lambda: True)
    assert "KUR-SES.bat" in gd._stt_fix_hint()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GozFaresi.exe"))
    for os_name, windows in (("windows", True), ("linux", False)):
        monkeypatch.setattr(tb, "_is_windows", lambda: windows)
        monkeypatch.setattr(gd._platform, "OS", os_name)
        line = gd._stt_fix_hint()
        assert "KUR-SES" not in line and "Ayarlar > Indirmeler" in line
        assert ("Win+H" in line) == windows
        assert line.isascii()
