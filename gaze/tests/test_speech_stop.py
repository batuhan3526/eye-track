# gaze/tests/test_speech_stop.py
"""Stop and repeat (speech_stop_key, F11 by default).

An AAC user who picked the wrong phrase, or who hears a long F4 read-out going
on, has no other way to make the machine stop talking. And someone who was
not heard needs the last sentence again without navigating back to it.
"""
import threading
import time

import eye_gaze.speech as sp
from eye_gaze.speech import Speaker


def _wire(monkeypatch, synth):
    monkeypatch.setattr(sp, "select_backend", lambda *a, **k: (synth, "sahte"))
    played, stops = [], []
    monkeypatch.setattr(sp, "_play", lambda wav: played.append(wav))
    monkeypatch.setattr(sp, "_stop_playback", lambda: stops.append(1))
    monkeypatch.setattr(sp, "_report", lambda msg: None)
    return played, stops


def _until(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def test_stop_drops_queued_and_in_flight_sentences(monkeypatch):
    gate = threading.Event()
    entered = threading.Event()

    def slow(text):
        entered.set()
        gate.wait(2.0)
        return b"WAV:" + text.encode()
    played, stops = _wire(monkeypatch, slow)
    s = Speaker()
    s.speak("bir")
    s.speak("iki")
    s.speak("uc")
    assert entered.wait(2.0)        # "bir" is being synthesised right now
    s.stop()
    gate.set()
    s.close()
    assert played == []             # nothing after the press may come out
    assert stops                    # and the device was told to go quiet


def test_stop_does_not_break_close(monkeypatch):
    played, _ = _wire(monkeypatch, lambda t: b"W")
    s = Speaker()
    s.stop()
    s.close()                       # must not hang on a lost sentinel
    assert not s._t.is_alive()


def test_speech_after_a_stop_is_heard(monkeypatch):
    played, _ = _wire(monkeypatch, lambda t: b"WAV:" + t.encode())
    s = Speaker()
    s.stop()
    s.speak("yeni")
    s.close()
    assert played == [b"WAV:yeni"]


def test_repeat_replays_the_last_sentence_without_resynthesising(monkeypatch):
    calls = []

    def synth(t):
        calls.append(t)
        return b"WAV:" + t.encode()
    played, _ = _wire(monkeypatch, synth)
    s = Speaker()
    s.speak("su istiyorum")
    assert _until(lambda: len(played) == 1)
    assert s.repeat() is True
    assert _until(lambda: len(played) == 2)
    s.close()
    assert played == [b"WAV:su istiyorum"] * 2
    assert calls == ["su istiyorum"]


def test_repeat_of_a_cached_aac_phrase(monkeypatch):
    played, _ = _wire(monkeypatch, lambda t: b"X")
    s = Speaker()
    s.play_wav(b"HAZIR")
    assert _until(lambda: len(played) == 1)
    assert s.repeat() is True
    s.close()
    assert played == [b"HAZIR", b"HAZIR"]


def test_repeat_with_nothing_said_yet(monkeypatch):
    _wire(monkeypatch, lambda t: b"X")
    s = Speaker()
    assert s.repeat() is False
    s.close()


def test_repeat_after_a_stop_says_the_STOPPED_sentence_not_the_one_before(monkeypatch):
    """The user stopped 'b' before it was ever heard; repeat means 'b'."""
    gate = threading.Event()

    def synth(t):
        if t == "b":
            gate.wait(2.0)
        return b"WAV:" + t.encode()
    played, _ = _wire(monkeypatch, synth)
    s = Speaker()
    s.speak("a")
    assert _until(lambda: len(played) == 1)
    s.speak("b")
    time.sleep(0.05)
    s.stop()
    gate.set()
    time.sleep(0.05)
    assert s.repeat() is True
    s.close()
    assert played == [b"WAV:a", b"WAV:b"]


def test_default_play_and_stop_share_one_player(monkeypatch):
    """Speech and earcons must use the SAME channel object, or an earcon
    would think the channel was free and cut a sentence."""
    class FakePlayer:
        def __init__(self):
            self.played, self.stops = [], 0

        def play(self, wav):
            self.played.append(wav)
            return True

        def stop(self):
            self.stops += 1
    fp = FakePlayer()
    monkeypatch.setattr(sp, "shared_player", lambda: fp)
    sp._play(b"W")
    sp._stop_playback()
    assert fp.played == [b"W"]
    assert fp.stops == 1


def test_a_sentence_dropped_by_the_alarm_is_announced(monkeypatch):
    """While the alarm holds the one sound channel, play() returns False at
    once. The console still said "AAC: ..." - the user believed they had
    spoken (an emergency-board sentence, even) and nobody heard it."""
    import eye_gaze.speech as sp

    class HeldPlayer:
        held = True

        def play(self, wav):
            raise AssertionError("must not even try while the alarm rings")
    said = []
    monkeypatch.setattr(sp, "shared_player", lambda: HeldPlayer())
    monkeypatch.setattr(sp, "_report", lambda m: said.append(m))
    sp._play(b"W")
    assert said and "SOYLENMEDI" in said[0]
