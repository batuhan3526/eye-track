# gaze/tests/test_stt_recorder.py
"""Microphone capture driven from the gaze loop.

poll() is called once per video frame, from the same thread that moves the
cursor. Anything it blocks on, the cursor blocks on too - so the tests below
care as much about what it must NOT do as about the audio it returns.
"""
import numpy as np
import pytest

from eye_gaze.stt import Recorder


class FakeStream:
    """Stand-in for sounddevice.InputStream: hands out canned frames."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False

    def start(self):
        pass

    def read(self, n):
        if not self._frames:
            return np.zeros((n, 1), dtype=np.float32), False
        return self._frames.pop(0).reshape(-1, 1), False

    def stop(self):
        pass

    def close(self):
        self.closed = True


def _f(level, n=1600):
    return np.full(n, level, dtype=np.float32)


def test_start_returns_false_when_no_microphone():
    def boom(**kw):
        raise OSError("mikrofon yok")
    r = Recorder(stream_factory=boom)
    assert r.start() is False
    assert r.active is False


def test_records_until_silence_and_returns_audio():
    frames = [_f(0.001)] * 3 + [_f(0.4)] * 5 + [_f(0.001)] * 20
    r = Recorder(silence_seconds=0.3, stream_factory=lambda **kw: FakeStream(frames))
    assert r.start() is True
    audio = None
    for _ in range(40):
        audio = r.poll()
        if audio is not None:
            break
    assert audio is not None
    assert audio.dtype == np.float32
    assert audio.size > 0
    assert r.active is False


def test_max_seconds_stops_endless_recording():
    # Quiet calibration first: speech the detector never recognised is now
    # withheld as no_speech, and this test is about the cap, not about that.
    frames = [_f(0.001)] * 3 + [_f(0.4)] * 200   # hic susmuyor
    r = Recorder(silence_seconds=5.0, max_seconds=0.5,
                 stream_factory=lambda **kw: FakeStream(frames))
    r.start()
    audio = None
    for _ in range(50):
        audio = r.poll()
        if audio is not None:
            break
    assert audio is not None
    assert audio.size <= int(16000 * 0.6)


def test_manual_stop_returns_what_was_recorded():
    frames = [_f(0.001)] * 3 + [_f(0.4)] * 10
    r = Recorder(stream_factory=lambda **kw: FakeStream(frames))
    r.start()
    for _ in range(5):
        r.poll()
    audio = r.stop()
    assert audio is not None and audio.size > 0
    assert r.active is False


def test_level_tracks_loudness_for_the_hud():
    r = Recorder(stream_factory=lambda **kw: FakeStream([_f(0.5)] * 5))
    r.start()
    r.poll()
    assert r.level > 0.1


def test_poll_when_inactive_returns_none():
    r = Recorder(stream_factory=lambda **kw: FakeStream([]))
    assert r.poll() is None


# --- poll() runs INSIDE the gaze loop --------------------------------------
#
# InputStream.read(n) blocks until n frames exist. One block is 0.1 s; the gaze
# loop runs at 30-60 fps. Reading unconditionally would therefore drop the
# cursor to 10 fps for the whole dictation - and the cursor is this user's only
# way to operate the machine. read_available says what is ready right now.


class EmptyStream(FakeStream):
    read_available = 0

    def read(self, n):
        raise AssertionError("bos akistan okundu: goz dongusu bloklanirdi")


def test_poll_does_not_read_a_stream_with_nothing_ready():
    r = Recorder(stream_factory=lambda **kw: EmptyStream([]))
    assert r.start() is True
    assert r.poll() is None
    assert r.active is True, "veri beklerken kayit iptal edildi"


class HalfStream(FakeStream):
    read_available = 800          # half a block

    def read(self, n):
        raise AssertionError("tam blok hazir degilken okundu")


def test_poll_waits_for_a_whole_block():
    r = Recorder(block=1600, stream_factory=lambda **kw: HalfStream([]))
    r.start()
    assert r.poll() is None


class ReadyStream(FakeStream):
    read_available = 16000

    def read(self, n):
        return _f(0.4, n).reshape(-1, 1), False


def test_poll_reads_when_a_block_is_ready():
    """Non-vacuity for the two above: the guard must let real audio through."""
    r = Recorder(stream_factory=lambda **kw: ReadyStream([]))
    r.start()
    r.poll()
    assert r.level > 0.1


def test_a_stream_that_dies_mid_recording_returns_what_it_had():
    class Dying(FakeStream):
        def read(self, n):
            if not self._frames:
                raise OSError("USB mikrofon cikarildi")
            return super().read(n)

    frames = [_f(0.001)] * 3 + [_f(0.4)] * 2   # calibration, then speech
    r = Recorder(stream_factory=lambda **kw: Dying(frames))
    r.start()
    for _ in range(5):
        assert r.poll() is None
    assert r.poll() is not None
    assert r.active is False


def test_the_microphone_is_closed_even_when_nothing_was_recorded():
    """An open input stream left behind holds the device against every other
    program on the machine."""
    stream = FakeStream([])
    r = Recorder(stream_factory=lambda **kw: stream)
    r.start()
    assert r.stop() is None
    assert stream.closed is True


@pytest.mark.parametrize("second", [True, False])
def test_start_twice_does_not_open_a_second_microphone(second):
    opened = []

    def factory(**kw):
        s = FakeStream([_f(0.4)] * 5)
        opened.append(s)
        return s

    r = Recorder(stream_factory=factory)
    r.start()
    if second:
        r.start()
    assert len(opened) == 1


# --- F3 pressed, nothing said ----------------------------------------------
#
# Whisper fed a recording of a quiet room does not return "": it hallucinates
# subtitle credits ("Altyazi M.K.", "Izlediginiz icin tesekkurler") which were
# then pasted into the focused field and read back. A recording in which the
# silence detector never heard speech is therefore not audio to transcribe;
# `no_speech` lets the driver say so instead of saying nothing.


def _run(r, polls=400):
    for _ in range(polls):
        audio = r.poll()
        if audio is not None or not r.active:
            return audio
    return None


def test_a_recording_with_no_speech_returns_none_and_says_so():
    frames = [_f(0.001)] * 400               # quiet room, nobody talks
    r = Recorder(silence_seconds=0.3, max_seconds=1.0,
                 stream_factory=lambda **kw: FakeStream(frames))
    r.start()
    assert _run(r) is None
    assert r.active is False, "kayit kendi kendine bitmedi"
    assert r.no_speech is True


def test_a_manual_stop_before_any_speech_returns_none_and_says_so():
    r = Recorder(stream_factory=lambda **kw: FakeStream([_f(0.001)] * 10))
    r.start()
    for _ in range(5):
        r.poll()
    assert r.stop() is None
    assert r.no_speech is True


def test_a_recording_with_speech_is_not_flagged():
    """Non-vacuity: a normal dictation still returns its audio."""
    frames = [_f(0.001)] * 3 + [_f(0.4)] * 5 + [_f(0.001)] * 20
    r = Recorder(silence_seconds=0.3, stream_factory=lambda **kw: FakeStream(frames))
    r.start()
    audio = _run(r)
    assert audio is not None and audio.size > 0
    assert r.no_speech is False


def test_no_speech_is_cleared_by_the_next_start():
    r = Recorder(stream_factory=lambda **kw: FakeStream([_f(0.001)] * 10))
    r.start()
    r.poll()
    r.stop()
    assert r.no_speech is True
    r.start()
    assert r.no_speech is False


def test_a_dead_device_with_no_audio_is_not_reported_as_no_speech():
    """"Mikrofon koptu" and "sizi duymadim" need different answers."""
    class Dying(FakeStream):
        def read(self, n):
            raise OSError("USB mikrofon cikarildi")

    r = Recorder(stream_factory=lambda **kw: Dying([]))
    r.start()
    assert r.poll() is None
    assert r.active is False
    assert r.no_speech is False
