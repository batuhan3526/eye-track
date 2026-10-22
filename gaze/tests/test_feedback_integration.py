# gaze/tests/test_feedback_integration.py
"""Speaker + WavPlayer + Feedback together, on their real threads.

Only the audio DEVICE is fake (nothing is heard). Each part is green on its
own in its own file; this is the check that they keep their promises to each
other: an earcon never cuts a sentence, the stop key cuts it at once, and a
stop does not leave the channel locked for the next sound.
"""
import io
import threading
import time
import wave

import eye_gaze.speech as sp
from eye_gaze.feedback import Feedback
from eye_gaze.playback import WavPlayer
from eye_gaze.speech import Speaker


def _wav(seconds, rate=16000, fill=b"\x00\x00"):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(fill * int(rate * seconds))
    return buf.getvalue()


class Device:
    """Fake PlaySound: records what started and when it was stopped."""

    def __init__(self):
        self.log = []
        self._lock = threading.Lock()

    def start(self, wav):
        with self._lock:
            self.log.append(("start", wav, time.monotonic()))
        return True

    def stop(self):
        with self._lock:
            self.log.append(("stop", None, time.monotonic()))


def _until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return cond()


def _rig(monkeypatch, sentence):
    dev = Device()
    player = WavPlayer(backend=dev, fallback=lambda w: None, tail=0.0)
    monkeypatch.setattr(sp, "shared_player", lambda: player)
    monkeypatch.setattr(sp, "select_backend",
                        lambda *a, **k: (lambda t: sentence, "sahte"))
    monkeypatch.setattr(sp, "_report", lambda m: None)
    fb = Feedback(player)
    return dev, player, fb


def test_earcon_during_a_sentence_is_dropped_not_cutting_it(monkeypatch):
    sentence = _wav(0.6, fill=b"\x01\x00")
    dev, player, fb = _rig(monkeypatch, sentence)
    s = Speaker()
    s.speak("uzun bir cumle")
    assert _until(lambda: player.busy)
    fb.event("click")
    fb.event("aac_open")
    time.sleep(0.2)
    starts = [e for e in dev.log if e[0] == "start"]
    assert [e[1] for e in starts] == [sentence]      # nothing else started
    s.close()
    fb.close()


def test_stop_key_cuts_the_sentence_and_frees_the_channel(monkeypatch):
    sentence = _wav(5.0, fill=b"\x01\x00")
    dev, player, fb = _rig(monkeypatch, sentence)
    s = Speaker()
    s.speak("cok uzun bir cumle")
    assert _until(lambda: player.busy)
    t = time.monotonic()
    s.stop()
    assert _until(lambda: not player.busy, timeout=1.0)
    assert time.monotonic() - t < 0.5
    assert any(e[0] == "stop" for e in dev.log)
    # The channel is free again: the next earcon is heard.
    fb.event("cal_done")
    assert _until(lambda: any(e[1] == fb.sounds["cal_done"]
                              for e in dev.log if e[0] == "start"))
    s.close()
    fb.close()


def test_repeat_after_stop_plays_the_same_audio_again(monkeypatch):
    sentence = _wav(0.1, fill=b"\x01\x00")
    dev, player, fb = _rig(monkeypatch, sentence)
    s = Speaker()
    s.speak("su")
    assert _until(lambda: len([e for e in dev.log if e[0] == "start"]) == 1)
    s.stop()
    assert s.repeat() is True
    assert _until(lambda: len([e for e in dev.log if e[0] == "start"]) == 2)
    s.close()
    fb.close()


def test_speech_waits_for_an_earcon_instead_of_cutting_it(monkeypatch):
    sentence = _wav(0.05, fill=b"\x01\x00")
    dev, player, fb = _rig(monkeypatch, sentence)
    fb.event("camera_lost")            # ~0.5 s earcon
    assert _until(lambda: player.busy)
    s = Speaker()
    s.speak("yardim")
    s.close()                           # waits for the sentence
    starts = [e for e in dev.log if e[0] == "start"]
    assert [e[1] for e in starts] == [fb.sounds["camera_lost"], sentence]
    # and the sentence started only after the earcon had run its length
    assert starts[1][2] - starts[0][2] >= 0.4
    assert not any(e[0] == "stop" for e in dev.log)
    fb.close()
