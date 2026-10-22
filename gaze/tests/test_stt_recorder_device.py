# gaze/tests/test_stt_recorder_device.py
"""Recorder vs. the audio device: first-press latency, cleanup on failure,
and keeping up when the video frame rate drops.
"""
import sys

import numpy as np

import eye_gaze.stt as stt
from eye_gaze.stt import Recorder


def _f(level, n=1600):
    return np.full(n, level, dtype=np.float32)


# --- PortAudio initialises at import, not at the first F3 -------------------


def test_the_audio_library_is_loaded_at_construction(monkeypatch):
    calls = []
    monkeypatch.setattr(stt, "_preload_audio", lambda: calls.append(1))
    Recorder()
    assert calls == [1]


def test_an_injected_stream_factory_skips_the_preload(monkeypatch):
    calls = []
    monkeypatch.setattr(stt, "_preload_audio", lambda: calls.append(1))
    Recorder(stream_factory=lambda **kw: None)
    assert calls == []


def test_a_missing_audio_library_does_not_break_construction(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)   # import -> error
    r = Recorder()
    assert r.active is False


def test_a_preload_that_raises_does_not_break_construction(monkeypatch):
    def boom():
        raise OSError("PortAudio yok")
    monkeypatch.setattr(stt, "_preload_audio", boom)
    Recorder()


# --- a stream that fails is still closed -----------------------------------


class Stream:
    def __init__(self, start_fails=False, stop_fails=False):
        self.start_fails = start_fails
        self.stop_fails = stop_fails
        self.closed = False

    def start(self):
        if self.start_fails:
            raise OSError("cihaz mesgul")

    def read(self, n):
        return np.zeros((n, 1), dtype=np.float32), False

    def stop(self):
        if self.stop_fails:
            raise OSError("cihaz cikarildi")

    def close(self):
        self.closed = True


def test_a_stream_that_fails_to_start_is_closed():
    """An opened-but-unstarted PortAudio stream holds the device."""
    s = Stream(start_fails=True)
    r = Recorder(stream_factory=lambda **kw: s)
    assert r.start() is False
    assert s.closed is True


def test_stop_closes_even_when_the_stream_refuses_to_stop():
    s = Stream(stop_fails=True)
    r = Recorder(stream_factory=lambda **kw: s)
    r.start()
    r.stop()
    assert s.closed is True
    assert r.active is False


# --- poll() must keep up when the gaze loop slows down ----------------------
#
# One block per poll() is 10 blocks/s only at >= 10 fps. A slower frame (face
# lost, GPU busy) let the PortAudio input buffer overflow and audio was lost.


class Backlog:
    """A device with `blocks` full blocks already waiting."""

    def __init__(self, blocks, level=0.001, block=1600):
        self.left = blocks * block
        self.block = block
        self.level = level
        self.reads = 0

    @property
    def read_available(self):
        return self.left

    def start(self):
        pass

    def read(self, n):
        self.reads += 1
        self.left -= n
        return _f(self.level, n).reshape(-1, 1), False

    def stop(self):
        pass

    def close(self):
        pass


def test_poll_drains_every_ready_block():
    s = Backlog(5)
    r = Recorder(stream_factory=lambda **kw: s)
    r.start()
    r.poll()
    assert s.reads == 5
    assert s.left == 0


def test_poll_drain_is_bounded():
    """A device that always claims more data must not trap the gaze loop."""
    s = Backlog(10_000)
    r = Recorder(max_seconds=10_000, stream_factory=lambda **kw: s)
    r.start()
    r.poll()
    assert 1 <= s.reads <= 64


def test_poll_stops_draining_once_the_recording_ends():
    """Blocks after the end belong to nobody: no reading past the stop."""
    s = Backlog(50, level=0.4)
    r = Recorder(silence_seconds=5.0, max_seconds=0.5,
                 stream_factory=lambda **kw: s)
    r.start()
    r.poll()
    assert r.active is False
    assert s.reads == 5
