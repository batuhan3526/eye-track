# gaze/tests/test_feedback.py
"""Earcons: short, quiet, distinct sounds that tell an eyes-only user what
just happened without them having to look at the console.

The rules that matter more than the sounds themselves:
  * nothing here may block the gaze loop or raise into it;
  * an earcon never cuts speech (it is skipped while the channel is busy);
  * while the microphone is recording, only the start/stop markers sound, and
    the start marker is skipped rather than played LATE - a late beep would
    land inside the recording that Whisper is about to transcribe.

No test makes a real sound: the player is a fake.
"""
import io
import struct
import threading
import time
import wave

import pytest

from eye_gaze.feedback import (
    EVENTS,
    Feedback,
    SpeechControl,
    build_feedback,
    build_speech_control,
    render,
)
from eye_gaze.playback import wav_duration


class FakePlayer:
    def __init__(self, busy=False):
        self.busy = busy
        self.played = []

    def try_play(self, wav):
        if self.busy:
            return False
        self.played.append(wav)
        return True


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def _samples(wav):
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        raw = w.readframes(w.getnframes())
    return struct.unpack("<%dh" % (len(raw) // 2), raw)


def _names(fb, player):
    inv = {v: k for k, v in fb.sounds.items()}
    return [inv[w] for w in player.played]


def _fb(player=None, clock=None, **kw):
    return Feedback(player or FakePlayer(), clock=clock or Clock(),
                    start_worker=False, **kw)


# --- the sounds ---------------------------------------------------------------

def test_every_event_has_its_own_distinct_sound():
    wavs = {name: render(name, 0.3) for name in EVENTS}
    assert len(set(wavs.values())) == len(EVENTS)
    for name, w in wavs.items():
        d = wav_duration(w)
        assert d is not None and 0.02 <= d <= 0.6, (name, d)


def test_volume_sets_the_peak_and_is_clamped():
    quiet = max(abs(s) for s in _samples(render("cal_done", 0.3)))
    assert 0.25 * 32767 <= quiet <= 0.3 * 32767 + 1
    loud = max(abs(s) for s in _samples(render("cal_done", 7.0)))
    assert loud <= 32767
    assert max(abs(s) for s in _samples(render("cal_done", -1))) == 0


def test_sounds_fade_in_and_out_so_they_do_not_click():
    s = _samples(render("error", 0.3))
    assert abs(s[0]) < 200 and abs(s[-1]) < 200


def test_unknown_sound_name_raises_keyerror_in_render_only():
    with pytest.raises(KeyError):
        render("yok-boyle-ses", 0.3)


# --- events -------------------------------------------------------------------

def test_event_is_played_by_the_worker_not_the_caller():
    p = FakePlayer()
    fb = _fb(p)
    assert fb.event("click") is True
    assert p.played == []            # the gaze loop never waits for audio
    fb.drain()
    assert _names(fb, p) == ["click"]


def test_disabled_feedback_is_silent():
    p = FakePlayer()
    fb = _fb(p, enabled=False)
    assert fb.event("click") is False
    fb.drain()
    assert p.played == []
    assert fb.capture_lead_in() == 0.0


def test_unknown_event_is_ignored_not_raised():
    fb = _fb()
    assert fb.event("bilinmeyen") is False
    assert fb.event(None) is False


def test_earcon_is_skipped_while_speech_holds_the_channel():
    p = FakePlayer(busy=True)
    fb = _fb(p)
    fb.event("click")
    fb.drain()                       # must not raise, must not wait
    assert p.played == []


def test_a_stale_earcon_is_dropped_instead_of_played_late():
    p, clk = FakePlayer(), Clock()
    fb = _fb(p, clock=clk)
    fb.event("aac_open")
    clk.t += 5.0
    fb.drain()
    assert p.played == []


def test_full_queue_never_blocks_the_caller():
    fb = _fb()
    t = time.perf_counter()
    results = [fb.event("click") for _ in range(100)]
    assert time.perf_counter() - t < 0.5
    assert results.count(False) > 0


def test_a_player_that_raises_does_not_escape():
    class Boom:
        busy = False

        def try_play(self, wav):
            raise RuntimeError("ses aygiti yok")
    fb = _fb(Boom())
    fb.event("click")
    fb.drain()                       # swallowed


# --- state edges (one call per frame from the driver) --------------------------

def test_first_update_is_only_a_baseline():
    p = FakePlayer()
    fb = _fb(p)
    fb.update(paused=True, aac=True, recording=False, camera_lost=True)
    fb.drain()
    assert p.played == []


def test_edges_map_to_their_sounds():
    p, clk = FakePlayer(), Clock()
    fb = _fb(p, clock=clk)
    fb.update(paused=False, aac=False, recording=False, camera_lost=False)
    steps = [dict(paused=True), dict(paused=False), dict(aac=True),
             dict(aac=False), dict(camera_lost=True), dict(camera_lost=False)]
    for st in steps:
        clk.t += 0.03
        fb.update(**st)
        fb.drain()
    assert _names(fb, p) == ["pause", "resume", "aac_open", "aac_close",
                             "camera_lost", "camera_back"]


def test_none_means_unknown_and_changes_nothing():
    p = FakePlayer()
    fb = _fb(p)
    fb.update(paused=False)
    fb.update(paused=None)
    fb.update(paused=False)
    fb.drain()
    assert p.played == []


def test_recording_markers_sound_and_everything_else_is_muted_meanwhile():
    p, clk = FakePlayer(), Clock()
    fb = _fb(p, clock=clk)
    fb.update(recording=False, paused=False)
    clk.t += 0.03
    fb.update(recording=True)
    fb.drain()
    fb.event("click")                # would be captured by the microphone
    clk.t += 0.03
    fb.update(paused=True)           # likewise
    clk.t += 0.03
    fb.update(recording=False)
    fb.drain()
    assert _names(fb, p) == ["stt_start", "stt_stop"]
    fb.event("click")                # recording over: sounds again
    fb.drain()
    assert _names(fb, p)[-1] == "click"


def test_start_marker_is_skipped_when_the_frame_gap_makes_it_late():
    """At a few fps the recording may have started a third of a second ago:
    a beep now would land inside the audio Whisper transcribes."""
    p, clk = FakePlayer(), Clock()
    fb = _fb(p, clock=clk)
    fb.update(recording=False)
    clk.t += 0.5
    fb.update(recording=True)
    fb.drain()
    assert p.played == []


def test_start_marker_is_skipped_when_it_waited_in_the_queue():
    p, clk = FakePlayer(), Clock()
    fb = _fb(p, clock=clk)
    fb.update(recording=False)
    clk.t += 0.03
    fb.update(recording=True)
    clk.t += 0.2                     # worker was slow to pick it up
    fb.drain()
    assert p.played == []


def test_lead_in_covers_the_whole_start_marker_budget():
    """The recorder throws away this much audio at the start. It must cover
    the latest moment the start marker can still be sounding."""
    fb = _fb()
    d = wav_duration(fb.sounds["stt_start"])
    lead = fb.capture_lead_in()
    assert lead >= d + fb.EDGE_BUDGET + fb.QUEUE_BUDGET
    assert lead <= 0.6               # more would eat the user's first word


def test_update_survives_garbage():
    fb = _fb()
    fb.update(paused="evet", aac=object(), recording=1, camera_lost=[])
    fb.drain()


def test_real_worker_thread_plays_and_close_stops_it():
    p = FakePlayer()
    fb = Feedback(p, start_worker=True)
    fb.event("cal_done")
    end = time.monotonic() + 2.0
    while not p.played and time.monotonic() < end:
        time.sleep(0.01)
    assert p.played
    fb.close()
    assert not fb._t.is_alive()


# --- config --------------------------------------------------------------------

def test_build_feedback_reads_the_config():
    fb = build_feedback({"feedback_sounds": True, "feedback_volume": 0.5},
                        player=FakePlayer(), start_worker=False)
    assert fb.enabled is True
    peak = max(abs(s) for s in _samples(fb.sounds["click"]))
    assert peak > 0.4 * 32767
    off = build_feedback({"feedback_sounds": False}, player=FakePlayer(),
                         start_worker=False)
    assert off.enabled is False


def test_build_feedback_never_raises_on_a_bad_value():
    fb = build_feedback({"feedback_volume": "cok"}, player=FakePlayer(),
                        start_worker=False)
    assert fb.enabled is True        # default volume, still useful
    fb.event("click")


def test_build_feedback_defaults_are_on_and_quiet():
    fb = build_feedback({}, player=FakePlayer(), start_worker=False)
    assert fb.enabled is True
    assert fb.volume == pytest.approx(0.3)


# --- stop / repeat key ----------------------------------------------------------

class FakeKeys:
    def __init__(self):
        self.pending = 0
        self.suppressing = True
        self.closed = False

    def take(self, key):
        if self.pending:
            self.pending -= 1
            return True
        return False

    def close(self):
        self.closed = True


class FakeSpeaker:
    def __init__(self, has_last=True):
        self.calls = []
        self.has_last = has_last

    def stop(self):
        self.calls.append("stop")

    def repeat(self):
        self.calls.append("repeat")
        return self.has_last


def _ctl(spk=None, interval=1.0):
    said = []
    keys = FakeKeys()
    c = SpeechControl(spk or FakeSpeaker(), "f11", keys, interval=interval,
                      report=said.append)
    return c, keys, said


def test_single_press_stops_double_press_repeats():
    spk = FakeSpeaker()
    c, keys, said = _ctl(spk)
    c.press(10.0)
    assert spk.calls == ["stop"]
    c.press(10.4)
    assert spk.calls == ["stop", "repeat"]
    c.press(10.6)                    # a third press is a new single: stop
    assert spk.calls == ["stop", "repeat", "stop"]
    assert said and all(s.isascii() for s in said)


def test_presses_far_apart_are_two_stops():
    spk = FakeSpeaker()
    c, _, _ = _ctl(spk)
    c.press(10.0)
    c.press(12.5)
    assert spk.calls == ["stop", "stop"]


def test_one_press_is_one_action():
    """The field bug class: one physical press must never do two things."""
    spk = FakeSpeaker()
    c, keys, _ = _ctl(spk)
    keys.pending = 1
    c.poll(10.0)
    c.poll(10.01)
    c.poll(10.02)
    assert spk.calls == ["stop"]


def test_poll_follows_the_pause_suppression_switch():
    c, keys, _ = _ctl()
    c.poll(1.0, suppressing=False)
    assert keys.suppressing is False
    c.poll(1.1, suppressing=True)
    assert keys.suppressing is True


def test_repeat_with_nothing_said_tells_the_user():
    c, _, said = _ctl(FakeSpeaker(has_last=False))
    c.press(1.0)
    c.press(1.2)
    assert any("yok" in s for s in said)


def test_a_speaker_that_raises_never_reaches_the_gaze_loop():
    class Bad:
        def stop(self):
            raise RuntimeError("x")

        def repeat(self):
            raise RuntimeError("y")
    c, keys, _ = _ctl(Bad())
    keys.pending = 2
    c.poll(1.0)
    c.poll(1.1)


def test_build_speech_control_hooks_only_the_stop_key():
    hooked = []

    class FakeKb:
        def hook_key(self, name, cb, suppress=False):
            hooked.append((name, suppress))
            return lambda: None
    c = build_speech_control({"speech_stop_key": "f11"}, FakeSpeaker(),
                             suppress=True, kb=FakeKb())
    assert c is not None
    assert hooked == [("f11", True)]


def test_build_speech_control_default_key_is_f11():
    hooked = []

    class FakeKb:
        def hook_key(self, name, cb, suppress=False):
            hooked.append(name)
            return lambda: None
    build_speech_control({}, FakeSpeaker(), suppress=True, kb=FakeKb())
    assert hooked == ["f11"]


def test_no_speaker_or_empty_key_means_no_hook():
    """F11 is the browser's fullscreen key: never swallow it for nothing."""
    hooked = []

    class FakeKb:
        def hook_key(self, name, cb, suppress=False):
            hooked.append(name)
            return lambda: None
    assert build_speech_control({"speech_stop_key": "f11"}, None,
                                suppress=True, kb=FakeKb()) is None
    assert build_speech_control({"speech_stop_key": ""}, FakeSpeaker(),
                                suppress=True, kb=FakeKb()) is None
    assert hooked == []


def test_a_bad_key_name_is_reported_and_disabled():
    said = []

    class FakeKb:
        def hook_key(self, name, cb, suppress=False):
            raise ValueError(name)
    c = build_speech_control({"speech_stop_key": "f99"}, FakeSpeaker(),
                             suppress=True, kb=FakeKb(), report=said.append)
    assert c is None
    assert said and "f99" in said[0] and said[0].isascii()
