# gaze/tests/test_stt_hallucination.py
"""Whisper's own non-speech verdicts, its well-known subtitle hallucinations,
and the CUDA -> CPU retry on model load.

Anything that gets past here is pasted into the focused field AND read back
aloud, so a phantom "Izlediginiz icin tesekkurler" is not a cosmetic bug.
"""
import numpy as np
import pytest

import eye_gaze.stt as stt
from eye_gaze.stt import Transcriber

AUDIO = np.zeros(16000, dtype=np.float32)


class Model:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        return self.result


def _t(result):
    return Transcriber(loader=lambda n, d: Model(result))


def _seg(text, nsp=0.0, lp=-0.2):
    return {"text": text, "no_speech_prob": nsp, "avg_logprob": lp}


# --- (a) segments whisper itself marks as non-speech -----------------------


def test_a_segment_whisper_calls_non_speech_is_dropped():
    res = {"text": " Su istiyorum. Altyazi M.K.",
           "segments": [_seg(" Su istiyorum."),
                        _seg(" Bunu duymadim.", nsp=0.9, lp=-1.4)]}
    assert _t(res).transcribe(AUDIO) == "Su istiyorum."


def test_a_confident_segment_with_high_no_speech_prob_is_kept():
    """Both conditions, not either: a short loud word can score a high
    no_speech_prob and still be decoded confidently."""
    res = {"text": " Evet.", "segments": [_seg(" Evet.", nsp=0.8, lp=-0.3)]}
    assert _t(res).transcribe(AUDIO) == "Evet."


def test_all_segments_non_speech_gives_empty_text():
    res = {"text": " Hmm.", "segments": [_seg(" Hmm.", nsp=0.95, lp=-1.5)]}
    assert _t(res).transcribe(AUDIO) == ""


def test_a_result_without_segments_falls_back_to_text():
    assert _t({"text": " Merhaba. "}).transcribe(AUDIO) == "Merhaba."


# --- (b) known whole-text hallucinations ------------------------------------


@pytest.mark.parametrize("text", [
    " Altyazı M.K.",
    "altyazi m.k.",
    " ALTYAZI M.K. ",
    " İzlediğiniz için teşekkürler.",
    "Izlediginiz icin tesekkurler!",
    " Abone olmayı unutmayın.",
    "abone olmayi unutmayin",
    " Altyazılar Amara.org topluluğu tarafından",
    "altyazilar amara.org toplulugu tarafindan",
])
def test_a_known_hallucination_is_discarded(text):
    t = _t({"text": text})
    assert t.transcribe(AUDIO) == ""
    assert t.ok is True                   # not a failure, just nothing said


def test_a_real_sentence_containing_thanks_is_kept():
    """Whole-text matches only."""
    out = _t({"text": " Yardımın için teşekkürler."}).transcribe(AUDIO)
    assert out == "Yardımın için teşekkürler."


# --- CUDA load failure retries on the CPU -----------------------------------


def test_a_cuda_load_failure_retries_once_on_the_cpu(monkeypatch):
    monkeypatch.setattr(stt, "_cuda_available", lambda: True)
    tried = []
    m = Model({"text": " Merhaba."})

    def loader(name, device):
        tried.append(device)
        if device == "cuda":
            raise RuntimeError("CUDA out of memory")
        return m

    t = Transcriber(device="auto", loader=loader)
    assert t.transcribe(AUDIO) == "Merhaba."
    assert tried == ["cuda", "cpu"]
    assert t.device == "cpu"
    assert t.ok is True
    assert m.calls[0]["fp16"] is False


def test_a_failure_on_both_devices_gives_up(monkeypatch):
    monkeypatch.setattr(stt, "_cuda_available", lambda: True)
    tried = []

    def loader(name, device):
        tried.append(device)
        raise RuntimeError("whisper kurulu degil")

    t = Transcriber(device="cuda", loader=loader)
    assert t.transcribe(AUDIO) == ""
    assert tried == ["cuda", "cpu"]
    assert t.ok is False
    assert "whisper" in t.last_error


def test_an_explicit_cpu_failure_is_not_retried(monkeypatch):
    monkeypatch.setattr(stt, "_cuda_available", lambda: True)
    tried = []

    def loader(name, device):
        tried.append(device)
        raise RuntimeError("yok")

    t = Transcriber(device="cpu", loader=loader)
    assert t.transcribe(AUDIO) == ""
    assert tried == ["cpu"]
    assert t.ok is False
