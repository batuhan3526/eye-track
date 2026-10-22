# gaze/tests/test_stt_transcriber.py
"""Whisper wrapper.

The model is several gigabytes and is loaded on first use, on a background
thread. Nothing here may propagate: a missing whisper install must cost the
user dictation, never the cursor.
"""
import numpy as np

import eye_gaze.stt as stt
from eye_gaze.stt import Transcriber, pick_device


class FakeModel:
    def __init__(self, text=" Merhaba, biraz su istiyorum. ", raises=False):
        self._text = text
        self._raises = raises
        self.calls = []

    def transcribe(self, audio, **kw):
        if self._raises:
            raise RuntimeError("model patladi")
        self.calls.append(kw)
        return {"text": self._text}


def test_pick_device_auto_uses_cuda_when_available():
    assert pick_device("auto", True) == "cuda"
    assert pick_device("auto", False) == "cpu"


def test_pick_device_falls_back_when_cuda_requested_but_missing():
    assert pick_device("cuda", False) == "cpu"


def test_pick_device_respects_explicit_cpu():
    assert pick_device("cpu", True) == "cpu"


def test_transcribe_strips_and_returns_text():
    m = FakeModel()
    t = Transcriber(loader=lambda name, device: m)
    out = t.transcribe(np.zeros(16000, dtype=np.float32))
    assert out == "Merhaba, biraz su istiyorum."


def test_transcribe_passes_turkish_language():
    m = FakeModel()
    t = Transcriber(language="tr", loader=lambda name, device: m)
    t.transcribe(np.zeros(16000, dtype=np.float32))
    assert m.calls[0]["language"] == "tr"


def test_model_is_loaded_lazily_once():
    loaded = []

    def loader(name, device):
        loaded.append(name)
        return FakeModel()

    t = Transcriber(model="small", loader=loader)
    assert loaded == []                       # henuz yuklenmedi
    t.transcribe(np.zeros(16000, dtype=np.float32))
    t.transcribe(np.zeros(16000, dtype=np.float32))
    assert loaded == ["small"]                # tek sefer


def test_empty_audio_returns_empty_string_without_loading():
    loaded = []
    t = Transcriber(loader=lambda n, d: loaded.append(n) or FakeModel())
    assert t.transcribe(np.zeros(0, dtype=np.float32)) == ""
    assert loaded == []


def test_model_failure_returns_empty_string():
    t = Transcriber(loader=lambda n, d: FakeModel(raises=True))
    assert t.transcribe(np.zeros(16000, dtype=np.float32)) == ""


def test_loader_failure_marks_not_ok():
    def boom(name, device):
        raise RuntimeError("whisper kurulu degil")
    t = Transcriber(loader=boom)
    assert t.transcribe(np.zeros(16000, dtype=np.float32)) == ""
    assert t.ok is False


# --- fp16 is not a detail: it is half the speed on a GPU --------------------


def _dev(monkeypatch, cuda):
    monkeypatch.setattr(stt, "_cuda_available", lambda: cuda)


def test_fp16_is_used_on_the_gpu(monkeypatch):
    _dev(monkeypatch, True)
    m = FakeModel()
    t = Transcriber(device="auto", loader=lambda n, d: m)
    t.transcribe(np.zeros(16000, dtype=np.float32))
    assert m.calls[0]["fp16"] is True


def test_fp16_is_off_on_the_cpu(monkeypatch):
    """Whisper prints a warning and falls back to FP32 anyway; asking for it on
    a CPU only produces noise the caregiver has to learn to ignore."""
    _dev(monkeypatch, False)
    m = FakeModel()
    t = Transcriber(device="auto", loader=lambda n, d: m)
    t.transcribe(np.zeros(16000, dtype=np.float32))
    assert m.calls[0]["fp16"] is False


def test_the_chosen_device_is_visible(monkeypatch):
    _dev(monkeypatch, False)
    t = Transcriber(device="auto", loader=lambda n, d: FakeModel())
    t.transcribe(np.zeros(16000, dtype=np.float32))
    assert t.device == "cpu"


# --- WHY it failed, not just that it did -----------------------------------
#
# Without this the driver can only say "konusma anlasilamadi - tekrar deneyin"
# after every attempt, and a user with no whisper install keeps retrying
# something that can never work.


def test_a_failed_load_records_the_reason():
    def boom(name, device):
        raise RuntimeError("No module named 'whisper'")
    t = Transcriber(loader=boom)
    t.transcribe(np.zeros(16000, dtype=np.float32))
    assert "whisper" in t.last_error, t.last_error


def test_a_failed_transcription_records_the_reason():
    t = Transcriber(loader=lambda n, d: FakeModel(raises=True))
    t.transcribe(np.zeros(16000, dtype=np.float32))
    assert "model patladi" in t.last_error, t.last_error


def test_a_successful_run_leaves_no_error():
    """Non-vacuity: last_error must not be the answer to everything."""
    t = Transcriber(loader=lambda n, d: FakeModel())
    t.transcribe(np.zeros(16000, dtype=np.float32))
    assert t.last_error == ""
    assert t.ok is True


def test_a_result_that_is_not_a_dict_does_not_raise():
    """Some whisper forks return an object. Reaching the gaze loop with an
    AttributeError from here would kill the cursor."""
    class Odd:
        def transcribe(self, audio, **kw):
            return ["bu bir sozluk degil"]

    t = Transcriber(loader=lambda n, d: Odd())
    assert t.transcribe(np.zeros(16000, dtype=np.float32)) == ""
    assert t.last_error != ""


def test_a_load_failure_is_not_retried_on_every_press():
    """Loading a missing model costs seconds of blocked thread and the same
    traceback each time."""
    tries = []

    def boom(name, device):
        tries.append(name)
        raise RuntimeError("yok")

    t = Transcriber(loader=boom)
    t.transcribe(np.zeros(16000, dtype=np.float32))
    first = len(tries)          # 1, or 2 when a CUDA failure retried on CPU
    assert first in (1, 2)
    for _ in range(2):
        t.transcribe(np.zeros(16000, dtype=np.float32))
    assert len(tries) == first
