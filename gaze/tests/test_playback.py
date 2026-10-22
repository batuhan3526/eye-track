# gaze/tests/test_playback.py
"""The process has ONE audio channel (PlaySound) and every sound shares it.

Two things this file pins down:

* speech must be stoppable mid-sentence (F11). winsound's synchronous
  PlaySound cannot be interrupted from another thread - measured: the stop call
  itself blocked until the 3 s sound had finished - so playback is async with a
  wait that stop() can cut short.
* an earcon must never cut speech. PlaySound replaces whatever is playing, so
  a beep started during a sentence would chop the sentence. try_play() skips
  instead of waiting or interrupting.

No test here makes a sound: the backend is a fake.
"""
import io
import threading
import time
import wave

from eye_gaze.playback import WavPlayer, wav_duration


def _wav(seconds, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class FakeBackend:
    def __init__(self, ok=True):
        self.ok = ok
        self.started = []
        self.stops = 0

    def start(self, wav):
        self.started.append(wav)
        return self.ok

    def stop(self):
        self.stops += 1


def test_wav_duration_reads_the_header():
    assert abs(wav_duration(_wav(1.0)) - 1.0) < 1e-6
    assert abs(wav_duration(_wav(0.25, rate=22050)) - 0.25) < 1e-3


def test_wav_duration_of_garbage_is_none():
    assert wav_duration(b"") is None
    assert wav_duration(b"RIFF....WAVEjunk") is None
    assert wav_duration(None) is None


def test_play_waits_for_the_sound_and_reports_completion():
    be = FakeBackend()
    p = WavPlayer(backend=be, tail=0.0)
    t = time.perf_counter()
    assert p.play(_wav(0.15)) is True
    took = time.perf_counter() - t
    assert be.started and took >= 0.14
    assert p.busy is False


def test_stop_cuts_a_long_sentence_short_from_another_thread():
    be = FakeBackend()
    p = WavPlayer(backend=be, tail=0.0)
    res = {}

    def run():
        res["done"] = p.play(_wav(5.0))
    th = threading.Thread(target=run)
    t = time.perf_counter()
    th.start()
    time.sleep(0.05)
    assert p.busy is True
    p.stop()
    th.join(2.0)
    assert not th.is_alive()
    assert time.perf_counter() - t < 1.5
    assert res["done"] is False          # interrupted, not finished
    assert be.stops >= 1                 # the device was really told to stop


def test_try_play_skips_while_something_is_sounding():
    """An earcon arriving mid-sentence is dropped - it must not cut speech
    and it must not queue up to play late, after the sentence."""
    be = FakeBackend()
    p = WavPlayer(backend=be, tail=0.0)
    th = threading.Thread(target=p.play, args=(_wav(0.4),))
    th.start()
    time.sleep(0.05)
    assert p.try_play(_wav(0.05)) is False
    assert len(be.started) == 1
    p.stop()
    th.join(2.0)
    assert p.try_play(_wav(0.02)) is True
    assert len(be.started) == 2


def test_failed_async_start_falls_back_to_blocking_play():
    """No async device: still speak, just not stoppably."""
    fell = []
    p = WavPlayer(backend=FakeBackend(ok=False), fallback=fell.append, tail=0.0)
    w = _wav(0.01)
    assert p.play(w) is True
    assert fell == [w]


def test_unreadable_header_falls_back_instead_of_guessing_a_length():
    fell = []
    be = FakeBackend()
    p = WavPlayer(backend=be, fallback=fell.append, tail=0.0)
    assert p.play(b"RIFFnot-a-real-wav") is True
    assert fell == [b"RIFFnot-a-real-wav"]
    assert be.started == []


def test_stop_when_idle_does_not_eat_the_next_sound():
    be = FakeBackend()
    p = WavPlayer(backend=be, tail=0.0)
    p.stop()
    t = time.perf_counter()
    assert p.play(_wav(0.1)) is True
    assert time.perf_counter() - t >= 0.09


def test_a_broken_backend_never_leaves_the_channel_locked():
    class Boom(FakeBackend):
        def start(self, wav):
            raise OSError("ses aygiti yok")
    p = WavPlayer(backend=Boom(), fallback=lambda w: None, tail=0.0)
    p.play(_wav(0.01))
    assert p.busy is False
    assert p.try_play(_wav(0.01)) is True


def test_stop_also_cancels_a_sentence_waiting_behind_an_earcon():
    """F11 while an earcon holds the channel and a sentence waits for it:
    the earcon's wait used up the stop and the sentence then cleared it and
    played in full - the very sentence the user had just stopped."""
    be = FakeBackend()
    p = WavPlayer(backend=be, tail=0.0)
    res = {}
    ear = threading.Thread(target=lambda: res.__setitem__(
        "earcon", p.try_play(_wav(0.6))))
    ear.start()
    time.sleep(0.05)
    say = threading.Thread(target=lambda: res.__setitem__(
        "sentence", p.play(_wav(0.3))))
    say.start()
    time.sleep(0.1)                     # the sentence is blocked on the lock
    p.stop()
    ear.join(2.0)
    say.join(2.0)
    assert res["sentence"] is False
    assert len(be.started) == 1         # only the earcon ever started
    assert p.play(_wav(0.05)) is True   # the NEXT sentence is not eaten
