# gaze/tests/test_stt_lead_in.py
"""The recorder discards its first fraction of a second when asked to.

That is where the "recording started" earcon sounds. Kept, a beep there is
heard by the silence detector (as the room's noise floor, or as speech) and by
Whisper - which, handed a beep and a quiet room, invents a sentence and pastes
it into whatever box has the focus.
"""
import numpy as np

from eye_gaze.stt import Recorder


class FakeStream:
    def __init__(self, frames, read_available=True):
        self._frames = list(frames)
        if read_available:
            self.read_available = 10 ** 6

    def start(self):
        pass

    def read(self, n):
        if not self._frames:
            return np.zeros((n, 1), dtype=np.float32), False
        return self._frames.pop(0).reshape(-1, 1), False

    def stop(self):
        pass

    def close(self):
        pass


def _f(level, n=1600):
    return np.full(n, level, dtype=np.float32)


def _record(frames, lead_in, read_available=True):
    r = Recorder(silence_seconds=0.3, lead_in_seconds=lead_in,
                 stream_factory=lambda **kw: FakeStream(frames, read_available))
    assert r.start()
    for _ in range(200):
        audio = r.poll()
        if audio is not None or not r.active:
            return r, audio
    raise AssertionError("kayit bitmedi")


def test_the_beep_blocks_are_not_in_the_audio():
    beep = 0.25
    frames = ([_f(beep)] * 4 + [_f(0.001)] * 3 + [_f(0.4)] * 5
              + [_f(0.001)] * 10)
    _, audio = _record(frames, lead_in=0.4)
    assert audio is not None
    assert not np.any(np.isclose(audio, beep))


def test_a_beep_then_silence_is_no_speech_not_a_transcription():
    """The hallucination path: without the lead-in a loud first block is
    'speech' and the quiet room after it goes to Whisper."""
    frames = [_f(0.001)] * 3 + [_f(0.3)] + [_f(0.001)] * 40
    r, audio = _record(frames, lead_in=0.4)
    assert audio is None
    assert r.no_speech is True


def test_lead_in_rounds_up_to_whole_blocks():
    # 0.35 s at 1600-sample (0.1 s) blocks = 4 blocks, never 3.
    frames = [_f(0.2)] * 4 + [_f(0.001)] * 3 + [_f(0.5)] * 3 + [_f(0.001)] * 10
    _, audio = _record(frames, lead_in=0.35)
    assert audio is not None
    assert not np.any(np.isclose(audio, 0.2))


def test_lead_in_is_rearmed_on_every_start():
    frames = [_f(0.2)] * 2 + [_f(0.001)] * 3 + [_f(0.5)] * 3 + [_f(0.001)] * 10
    r = Recorder(silence_seconds=0.3, lead_in_seconds=0.2,
                 stream_factory=lambda **kw: FakeStream(frames))
    for _ in range(2):
        assert r.start()
        audio = None
        for _ in range(200):
            audio = r.poll()
            if audio is not None or not r.active:
                break
        assert audio is not None
        assert not np.any(np.isclose(audio, 0.2))


def test_default_is_no_lead_in():
    frames = [_f(0.001)] * 3 + [_f(0.4)] * 5 + [_f(0.001)] * 10
    _, audio = _record(frames, lead_in=0.0)
    assert audio is not None
    assert audio.size == 11 * 1600          # 3 quiet + 5 speech + 3 quiet


def test_lead_in_with_a_stream_that_cannot_report_readiness():
    """One block per poll for such streams - the skip must keep that rule,
    or a blocking read() would hold the gaze loop."""
    frames = [_f(0.25)] * 4 + [_f(0.001)] * 3 + [_f(0.4)] * 5 + [_f(0.001)] * 10
    stream = FakeStream(frames, read_available=False)
    reads = []
    real_read = stream.read

    def counted(n):
        reads.append(1)
        return real_read(n)
    stream.read = counted
    r = Recorder(silence_seconds=0.3, lead_in_seconds=0.4,
                 stream_factory=lambda **kw: stream)
    assert r.start()
    audio = None
    for _ in range(200):
        before = len(reads)
        audio = r.poll()
        assert len(reads) - before <= 1
        if audio is not None or not r.active:
            break
    assert audio is not None
    assert not np.any(np.isclose(audio, 0.25))
