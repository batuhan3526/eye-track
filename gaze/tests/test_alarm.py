# gaze/tests/test_alarm.py
"""Emergency alarm: hold a key (or the click switch, or a gamepad button) and
the machine calls for help - loudly, until the same hold stops it or it times
out. Every device is faked: no real hook, no real sound, no real clock."""
import io
import os
import wave
from pathlib import Path

import numpy as np
import pytest

from eye_gaze import alarm as al
from eye_gaze.switch import KeyPresses


# --- fakes -------------------------------------------------------------------

class _Ev:
    def __init__(self, kind):
        self.event_type = kind


class _FakeKeyboard:
    def __init__(self, fail=False):
        self.hooks = {}
        self.suppress = {}
        self.fail = fail

    def hook_key(self, key, callback, suppress=False):
        if self.fail:
            raise RuntimeError("hook yok")
        self.hooks[key] = callback
        self.suppress[key] = suppress
        return lambda: None

    def down(self, key):
        return self.hooks[key](_Ev("down"))

    def up(self, key):
        return self.hooks[key](_Ev("up"))


class _FakePlayer:
    def __init__(self, fail_start=False):
        self.calls = []
        self.voice = None
        self.fail_start = fail_start

    def start(self, now):
        self.calls.append(("start", now))
        if self.fail_start:
            raise RuntimeError("ses karti yok")

    def tick(self, now):
        self.calls.append(("tick", now))

    def stop(self):
        self.calls.append(("stop",))

    def set_voice(self, wav):
        self.voice = wav

    def close(self):
        self.calls.append(("close",))

    def kinds(self):
        return [c[0] for c in self.calls]


class _Held:
    """A source whose pressed state the test sets directly."""

    def __init__(self):
        self.down = False

    def __call__(self, now):
        return self.down


class _FakeSound:
    """Stands in for winsound."""
    SND_FILENAME = 0x20000
    SND_ASYNC = 0x1
    SND_NODEFAULT = 0x2
    SND_MEMORY = 0x4

    def __init__(self, fail=False):
        self.played = []
        self.beeps = []
        self.fail = fail

    def PlaySound(self, sound, flags):
        if self.fail and sound is not None:
            raise RuntimeError("Failed to play sound")
        self.played.append((sound, flags))

    def Beep(self, freq, ms):
        self.beeps.append((freq, ms))


class _SyncThread:
    """threading.Thread stand-in that does NOT run the target (tests drive it)."""
    made = []

    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args
        self.started = False
        _SyncThread.made.append(self)

    def start(self):
        self.started = True


class _RunNow(_SyncThread):
    def start(self):
        self.started = True
        self.target(*self.args)


def _log():
    lines = []
    return lines, lines.append


def _core(sources, player=None, max_seconds=120.0):
    lines, log = _log()
    player = player or _FakePlayer()
    core = al.AlarmCore(player, sources, max_seconds=max_seconds, log=log)
    return core, player, lines


def _hold(core, src, t0, seconds, step=0.05):
    """Hold `src` down from t0 for `seconds`, ticking every `step`; release."""
    t = t0
    src.down = True
    while t <= t0 + seconds + 1e-9:
        core.tick(t)
        t += step
    src.down = False
    core.tick(t)
    return t


# --- HoldDetector --------------------------------------------------------------

def test_hold_fires_once_after_the_hold_time():
    h = al.HoldDetector(2.0)
    assert h.update(True, 0.0) is False
    assert h.update(True, 1.9) is False
    assert h.update(True, 2.0) is True
    # Still held: never a second time in the same hold.
    assert h.update(True, 5.0) is False
    assert h.update(True, 60.0) is False


def test_hold_rearms_only_after_release():
    h = al.HoldDetector(2.0)
    h.update(True, 0.0)
    assert h.update(True, 2.1) is True
    assert h.update(False, 2.2) is False
    assert h.update(True, 3.0) is False
    assert h.update(True, 5.0) is True


def test_a_short_press_never_fires():
    h = al.HoldDetector(2.0)
    t = 0.0
    for _ in range(40):              # tapping for 12 s, 0.15 s each
        assert h.update(True, t) is False
        assert h.update(True, t + 0.15) is False
        assert h.update(False, t + 0.16) is False
        t += 0.3


# --- AlarmCore -----------------------------------------------------------------

def test_holding_starts_the_alarm_and_says_so():
    src = _Held()
    core, player, lines = _core([("f12", src, 2.0)])
    _hold(core, src, 0.0, 1.5)
    assert not core.ringing
    assert "start" not in player.kinds()
    _hold(core, src, 10.0, 2.2)
    assert core.ringing
    assert player.kinds().count("start") == 1
    text = " ".join(lines)
    assert "ALARM" in text and "f12" in text
    assert all(ord(c) < 128 for c in text), "konsol cp1254: yalniz ASCII"


def test_the_same_hold_again_stops_it():
    src = _Held()
    core, player, lines = _core([("f12", src, 2.0)])
    t = _hold(core, src, 0.0, 2.2)
    assert core.ringing
    # Keeping the key down after it started must NOT stop it again.
    assert "stop" not in player.kinds()
    _hold(core, src, t + 1.0, 2.2)
    assert not core.ringing
    assert player.kinds().count("stop") == 1
    assert any("durdu" in ln for ln in lines)


def test_one_long_hold_does_not_start_and_stop():
    src = _Held()
    core, player, _ = _core([("f12", src, 2.0)])
    _hold(core, src, 0.0, 30.0)
    assert core.ringing


def test_the_player_is_kept_going_while_ringing():
    src = _Held()
    core, player, _ = _core([("f12", src, 2.0)])
    t = _hold(core, src, 0.0, 2.2)
    n = player.kinds().count("tick")
    core.tick(t + 1.0)
    core.tick(t + 2.0)
    assert player.kinds().count("tick") >= n + 2


def test_it_times_out():
    src = _Held()
    core, player, lines = _core([("f12", src, 2.0)], max_seconds=120.0)
    _hold(core, src, 0.0, 2.1)
    core.tick(100.0)
    assert core.ringing
    core.tick(123.0)
    assert not core.ringing
    assert player.kinds()[-1] == "stop"
    assert any("120" in ln for ln in lines)


def test_zero_max_means_no_timeout():
    src = _Held()
    core, player, _ = _core([("f12", src, 2.0)], max_seconds=0)
    _hold(core, src, 0.0, 2.1)
    core.tick(10000.0)
    assert core.ringing


def test_any_source_can_stop_what_another_started():
    key, pad = _Held(), _Held()
    core, player, _ = _core([("f12", key, 2.0), ("START", pad, 2.0)])
    t = _hold(core, key, 0.0, 2.2)
    assert core.ringing
    _hold(core, pad, t, 2.2)
    assert not core.ringing


def test_a_broken_source_does_not_disable_the_others():
    def boom(now):
        raise OSError("xinput gitti")
    key = _Held()
    core, player, lines = _core([("pad", boom, 2.0), ("f12", key, 2.0)])
    _hold(core, key, 0.0, 2.2)
    assert core.ringing


def test_a_failing_player_still_counts_as_ringing_and_is_reported():
    """The person holding the key cannot see the console; but the alarm state
    must still be on, so the next hold STOPS rather than starts again."""
    src = _Held()
    core, player, lines = _core([("f12", src, 2.0)],
                                player=_FakePlayer(fail_start=True))
    _hold(core, src, 0.0, 2.2)
    assert core.ringing
    assert any("HATA" in ln or "calinamadi" in ln for ln in lines)


def test_close_stops_a_ringing_alarm():
    src = _Held()
    core, player, _ = _core([("f12", src, 2.0)])
    _hold(core, src, 0.0, 2.2)
    core.close()
    assert not core.ringing
    assert "stop" in player.kinds()


# --- auto-repeat through the real KeyPresses hub ----------------------------------

def test_keyboard_auto_repeat_is_a_hold_not_many_presses():
    kb = _FakeKeyboard()
    hub = KeyPresses(["f12"], kb=kb)
    core, player, _ = _core([("f12", lambda now: hub.held("f12"), 2.0)])
    t = 0.0
    kb.down("f12")
    while t < 2.5:                 # Windows repeats key-down ~30x/s
        kb.down("f12")
        core.tick(t)
        t += 1 / 30
    assert core.ringing
    assert player.kinds().count("start") == 1


def test_quick_repeated_taps_never_raise_the_alarm():
    kb = _FakeKeyboard()
    hub = KeyPresses(["f12"], kb=kb)
    core, player, _ = _core([("f12", lambda now: hub.held("f12"), 2.0)])
    t = 0.0
    while t < 20.0:
        kb.down("f12")
        core.tick(t)
        core.tick(t + 0.2)
        kb.up("f12")
        core.tick(t + 0.25)
        t += 0.5
    assert not core.ringing


# --- sound ---------------------------------------------------------------------

def _wav_info(data):
    with wave.open(io.BytesIO(data), "rb") as w:
        n = w.getnframes()
        frames = np.frombuffer(w.readframes(n), dtype="<i2")
        return w.getnchannels(), w.getsampwidth(), w.getframerate(), n, frames


def _voice_wav(rate=16000, seconds=0.8, channels=2, width=2):
    t = np.arange(int(rate * seconds)) / rate
    x = (0.2 * np.sin(2 * np.pi * 200 * t) * 32767).astype("<i2")
    if channels == 2:
        x = np.repeat(x, 2)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(x.tobytes())
    return buf.getvalue()


def test_the_siren_is_a_loud_valid_wav():
    data, seconds = al.build_cycle_wav(None, rate=22050)
    ch, width, rate, n, frames = _wav_info(data)
    assert (ch, width, rate) == (1, 2, 22050)
    assert seconds == pytest.approx(n / rate)
    assert 1.0 <= seconds <= 4.0
    assert np.abs(frames).max() > 0.8 * 32767, "alarm YUKSEK sesli olmali"


def test_the_voice_is_added_and_made_loud():
    plain, s0 = al.build_cycle_wav(None, rate=22050)
    data, s1 = al.build_cycle_wav(_voice_wav(16000, 0.8, channels=2), rate=22050)
    ch, width, rate, n, frames = _wav_info(data)
    assert (ch, width, rate) == (1, 2, 22050)
    assert s1 >= s0 + 0.8 - 0.01
    # The quiet (0.2) voice is normalised up, not left under the siren.
    voice_part = frames[int(s0 * rate):]
    assert np.abs(voice_part).max() > 0.8 * 32767


@pytest.mark.parametrize("junk", [b"", b"bu bir wav degil", b"RIFF\x00\x00"])
def test_a_broken_voice_falls_back_to_the_siren(junk):
    plain, s0 = al.build_cycle_wav(None, rate=22050)
    data, s1 = al.build_cycle_wav(junk, rate=22050)
    assert s1 == pytest.approx(s0)


def test_player_plays_async_from_a_file_and_repeats_each_cycle(tmp_path):
    snd = _FakeSound()
    p = al.TonePlayer(sound=snd, tmpdir=str(tmp_path))
    p.start(0.0)
    assert len(snd.played) == 1
    path, flags = snd.played[0]
    assert flags & snd.SND_ASYNC and flags & snd.SND_FILENAME
    assert Path(path).read_bytes()[:4] == b"RIFF"
    p.tick(0.5)
    assert len(snd.played) == 1          # mid-cycle: not restarted
    p.tick(p.cycle_seconds + 0.1)
    assert len(snd.played) == 2          # next cycle: played again (self-heals
                                         # if TTS/another sound cut it off)
    p.stop()
    assert snd.played[-1][0] is None     # PlaySound(None) silences it
    n = len(snd.played)
    p.tick(1000.0)
    assert len(snd.played) == n          # stopped means stopped
    p.close()


def test_player_uses_the_voice_once_it_arrives(tmp_path):
    snd = _FakeSound()
    p = al.TonePlayer(sound=snd, tmpdir=str(tmp_path))
    plain = p.cycle_seconds
    p.set_voice(_voice_wav(22050, 1.0, channels=1))
    assert p.cycle_seconds > plain + 0.9
    p.start(0.0)
    path = snd.played[-1][0]
    _, _, rate, n, _ = _wav_info(Path(path).read_bytes())
    assert n / rate == pytest.approx(p.cycle_seconds)
    p.close()


def test_player_falls_back_to_beeps_when_playsound_fails(tmp_path):
    snd = _FakeSound(fail=True)
    _SyncThread.made = []
    p = al.TonePlayer(sound=snd, tmpdir=str(tmp_path), thread=_SyncThread)
    p.start(0.0)                         # must not raise
    beeper = [t for t in _SyncThread.made if t.started]
    assert beeper, "PlaySound calismadiysa Beep yedegi baslamali"
    # Drive the beep loop: it beeps while ringing and ends after stop().
    calls = {"n": 0}

    def fake_beep(f, ms):
        calls["n"] += 1
        if calls["n"] >= 6:
            p.stop()
    snd.Beep = fake_beep
    beeper[0].target(*beeper[0].args)
    assert calls["n"] >= 6
    p.close()


def test_player_falls_back_to_beeps_when_the_file_cannot_be_written(tmp_path):
    snd = _FakeSound()
    _SyncThread.made = []
    p = al.TonePlayer(sound=snd, tmpdir=str(tmp_path / "yok" / "yok"),
                      thread=_SyncThread)
    p.start(0.0)
    assert any(t.started for t in _SyncThread.made)


class _AsyncFileSound(_FakeSound):
    """Like the real winsound: an ASYNC play of a missing file returns
    normally and is simply silent. Records what would actually sound."""

    def __init__(self):
        super().__init__()
        self.audible = []

    def PlaySound(self, sound, flags):
        super().PlaySound(sound, flags)
        if sound is None:
            return
        try:
            with open(sound, "rb") as f:
                data = f.read()
        except OSError:
            data = b""
        self.audible.append(data[:4] == b"RIFF" and len(data) > 1000)


@pytest.mark.parametrize("damage", ["deleted", "truncated"])
def test_a_temp_file_cleaned_away_between_alarms_is_written_again(
        tmp_path, damage):
    """Days of uptime: Storage Sense or a cleaner empties %TEMP% between two
    alarms. Async PlaySound of a missing file raises nothing, so neither the
    Beep fallback nor the console would tell anyone the siren is silent."""
    snd = _AsyncFileSound()
    p = al.TonePlayer(sound=snd, tmpdir=str(tmp_path))
    p.start(0.0)
    p.stop()
    path = snd.played[0][0]
    if damage == "deleted":
        os.remove(path)
    else:
        open(path, "wb").close()
    p.start(100.0)
    assert snd.audible == [True, True]
    p.tick(100.0 + p.cycle_seconds + 0.1)      # every cycle checks again
    assert snd.audible[-1] is True
    p.close()


def test_a_file_that_vanishes_mid_alarm_comes_back_next_cycle(tmp_path):
    snd = _AsyncFileSound()
    p = al.TonePlayer(sound=snd, tmpdir=str(tmp_path))
    p.start(0.0)
    os.remove(snd.played[0][0])
    p.tick(p.cycle_seconds + 0.1)
    assert snd.audible == [True, True]
    p.close()


def test_player_with_no_sound_module_is_silent_but_safe(tmp_path):
    p = al.TonePlayer(sound=None, tmpdir=str(tmp_path), thread=_SyncThread)
    p.start(0.0)
    p.tick(10.0)
    p.stop()
    p.close()


# --- watcher thread --------------------------------------------------------------

def test_the_watcher_survives_a_tick_that_raises():
    class Boom:
        ringing = False
        n = 0

        def tick(self, now):
            Boom.n += 1
            raise ValueError("bozuk")

        def close(self):
            pass

    lines, log = _log()
    clock = iter(range(1000))
    w = al.AlarmWatcher(Boom(), clock=lambda: float(next(clock)),
                        sleep=lambda s: None, log=log, thread=_SyncThread)
    steps = {"n": 0}

    def sleep(_s):
        steps["n"] += 1
        if steps["n"] >= 5:
            w.closed = True
    w._sleep = sleep
    w._loop()
    assert Boom.n == 5
    assert sum("HATA" in ln for ln in lines) == 1, "tekrar eden hata bir kez yazilir"


def test_attach_speaker_renders_the_phrase_off_the_loop():
    player = _FakePlayer()
    core = al.AlarmCore(player, [], log=lambda s: None)

    class Spk:
        ok = True

        def synth(self, text):
            Spk.text = text
            return b"RIFFwav"
    w = al.AlarmWatcher(core, log=lambda s: None, thread=_RunNow)
    w.attach_speaker(Spk())
    assert player.voice == b"RIFFwav"
    assert "Yard" in Spk.text


def test_attach_speaker_tolerates_none_and_failures():
    player = _FakePlayer()
    core = al.AlarmCore(player, [], log=lambda s: None)
    w = al.AlarmWatcher(core, log=lambda s: None, thread=_RunNow)
    w.attach_speaker(None)
    assert player.voice is None

    class Bad:
        ok = True

        def synth(self, text):
            raise RuntimeError("piper coktu")
    w.attach_speaker(Bad())
    assert player.voice is None


def test_a_voice_with_no_audio_says_the_alarm_is_siren_only():
    # spd-say speaks by itself: Speaker.synth() returns None for it.
    player = _FakePlayer()
    core = al.AlarmCore(player, [], log=lambda s: None)
    lines = []
    w = al.AlarmWatcher(core, log=lines.append, thread=_RunNow)

    class SelfSpeaking:
        ok = True

        def synth(self, text):
            return None
    w.attach_speaker(SelfSpeaking())
    assert player.voice is None
    assert len(lines) == 1 and "yalniz siren" in lines[0]
    assert lines[0].isascii()


# --- start_alarm: wiring from config ---------------------------------------------

def _start(cfg, kb=None, keys=None, gamepad=None, player=None, os_keys=None):
    lines, log = _log()
    kb = kb if kb is not None else _FakeKeyboard()
    player = player or _FakePlayer()
    # os_keys=None: never read the REAL keyboard state of the test machine.
    w = al.start_alarm(cfg, keys=keys, kb=kb, gamepad=gamepad, player=player,
                       log=log, thread=_SyncThread, os_keys=os_keys)
    return w, kb, player, lines


def test_default_config_hooks_f12_and_swallows_it():
    w, kb, player, lines = _start({})
    assert "f12" in kb.hooks
    assert kb.suppress["f12"] is True
    kb.down("f12")
    t = 0.0
    while t < 2.2:
        w.core.tick(t)
        t += 0.05
    assert w.core.ringing


def test_alarm_key_can_be_turned_off():
    w, kb, player, lines = _start({"alarm_key": ""})
    assert kb.hooks == {}


def test_an_alarm_key_that_is_already_a_driver_key_is_refused():
    """Space held 2 s would click AND alarm from the keyboard hook; the switch
    route is alarm_switch_hold, and the message says so."""
    w, kb, player, lines = _start({"alarm_key": "space",
                                   "left_click_key": "space"})
    assert "space" not in kb.hooks
    text = " ".join(lines)
    assert "alarm_switch_hold" in text
    assert all(ord(c) < 128 for c in text)


def test_switch_hold_raises_the_alarm_and_a_short_press_still_clicks_once():
    kb = _FakeKeyboard()
    keys = KeyPresses(["space"], kb=kb)
    w, _, player, lines = _start({"left_click_key": "space",
                                  "alarm_switch_hold": 3.0}, kb=kb, keys=keys)
    # A normal click: one press, one click, no alarm.
    kb.down("space")
    w.core.tick(0.0)
    w.core.tick(0.2)
    kb.up("space")
    w.core.tick(0.25)
    assert keys.take("space") is True
    assert keys.take("space") is False
    assert not w.core.ringing
    # Held for 3 s (with auto-repeat): alarm, and still only ONE click press.
    t = 1.0
    kb.down("space")
    while t < 4.2:
        kb.down("space")
        w.core.tick(t)
        t += 0.05
    assert w.core.ringing
    assert keys.take("space") is True
    assert keys.take("space") is False


def test_switch_hold_is_off_by_default():
    kb = _FakeKeyboard()
    keys = KeyPresses(["space"], kb=kb)
    w, _, player, _ = _start({"left_click_key": "space"}, kb=kb, keys=keys)
    kb.down("space")
    for i in range(200):
        w.core.tick(i * 0.05)
    assert not w.core.ringing


def test_a_too_short_switch_hold_is_raised_to_a_safe_minimum():
    kb = _FakeKeyboard()
    keys = KeyPresses(["space"], kb=kb)
    w, _, player, lines = _start({"left_click_key": "space",
                                  "alarm_switch_hold": 0.2}, kb=kb, keys=keys)
    kb.down("space")
    w.core.tick(0.0)
    w.core.tick(0.5)
    assert not w.core.ringing, "normal bir tik alarm calmamali"


class _Pad:
    def __init__(self):
        self.value = 0
        self.calls = 0

    def buttons(self, index=0):
        self.calls += 1
        return self.value


def test_gamepad_alarm_button_hold():
    pad = _Pad()
    w, kb, player, _ = _start({"gamepad_alarm": "START"}, gamepad=pad)
    pad.value = 0x0010
    t = 0.0
    while t < 2.2:
        w.core.tick(t)
        t += 0.05
    assert w.core.ringing


def test_gamepad_left_hold_counts_as_the_switch():
    pad = _Pad()
    w, kb, player, _ = _start({"gamepad_left": "A", "alarm_switch_hold": 3.0,
                               "left_click_key": ""}, gamepad=pad)
    pad.value = 0x1000
    t = 0.0
    while t < 3.2:
        w.core.tick(t)
        t += 0.05
    assert w.core.ringing


def test_an_unknown_gamepad_button_is_named():
    w, kb, player, lines = _start({"gamepad_alarm": "ZZ"}, gamepad=_Pad())
    assert any("gamepad_alarm" in ln for ln in lines)


def test_an_absent_controller_is_not_polled_every_tick():
    class Gone:
        calls = 0

        def buttons(self, index=0):
            Gone.calls += 1
            return None
    w, kb, player, _ = _start({"gamepad_alarm": "START"}, gamepad=Gone())
    for i in range(100):                 # 5 s at 20 Hz
        w.core.tick(i * 0.05)
    assert Gone.calls <= 6


def test_start_alarm_never_raises_even_if_the_hook_fails():
    w, kb, player, lines = _start({}, kb=_FakeKeyboard(fail=True))
    w.attach_speaker(None)
    w.close()


def test_start_alarm_never_raises_on_a_garbage_config():
    w, kb, player, lines = _start({"alarm_hold_seconds": "abc",
                                   "alarm_max_seconds": None,
                                   "alarm_switch_hold": "x"})
    w.close()


def test_startup_line_says_how_to_use_it():
    w, kb, player, lines = _start({"gamepad_alarm": "START",
                                   "alarm_switch_hold": 3}, gamepad=_Pad())
    text = " ".join(lines)
    assert "f12" in text and "START" in text
    assert all(ord(c) < 128 for c in text)


def test_close_stops_sound_and_is_idempotent():
    w, kb, player, _ = _start({})
    kb.down("f12")
    for i in range(50):
        w.core.tick(i * 0.05)
    assert w.core.ringing
    w.close()
    w.close()
    assert not w.core.ringing
    assert "stop" in player.kinds()


# --- config + driver wiring --------------------------------------------------------

def test_alarm_settings_are_known_and_numeric(monkeypatch, tmp_path):
    from eye_gaze import config as cf
    monkeypatch.setattr(cf, "_report", lambda m: None)
    for k in ("alarm_key", "alarm_hold_seconds", "alarm_max_seconds",
              "gamepad_alarm", "alarm_switch_hold"):
        assert k in cf.KNOWN_KEYS
    assert cf.DEFAULTS["alarm_key"] == "f12"
    assert cf.DEFAULTS["alarm_hold_seconds"] == 2.0
    assert cf.DEFAULTS["alarm_max_seconds"] == 120.0
    assert cf.DEFAULTS["gamepad_alarm"] == ""
    assert cf.DEFAULTS["alarm_switch_hold"] == 0.0
    p = tmp_path / "c.yaml"
    p.write_text('left_click_key: "space"\nalarm_hold_seconds: "2,5"\n'
                 'alarm_switch_hold: "3"\nalarm_max_seconds: 60\n',
                 encoding="utf-8")
    cfg = cf.load_config(str(p))
    assert cfg["alarm_hold_seconds"] == 2.5
    assert cfg["alarm_switch_hold"] == 3.0


def test_the_driver_starts_the_alarm_before_the_first_calibration():
    """It has to work while calibrating: the watcher runs on its own thread, so
    it only needs to exist before run_calibration() is first called."""
    import inspect
    import gaze_driver as gd
    src = inspect.getsource(gd.main)
    assert "start_alarm(" in src
    assert src.index("start_alarm(") < src.index("_calibrate_until_done(")
    assert "attach_speaker(" in src
    assert "alarm.close()" in src


def test_the_alarm_is_armed_before_the_camera_and_the_models():
    """No webcam (a video call holding it, unplugged, still enumerating at
    boot) used to `return 1` before start_alarm existed - and BASLAT.bat's
    restart loop then ran for as long as the camera stayed away with NO F12.
    The model loads (seconds of torch) also came first, on every start."""
    import inspect
    import gaze_driver as gd
    src = inspect.getsource(gd.main)
    armed = src.index("start_alarm(")
    assert armed < src.index("_ResilientCapture(")
    assert armed < src.index("GazeEstimator(")
    assert armed < src.index("_make_deep(")
    cam = src[src.index("webcam acilamadi"):]
    assert "return 1" not in cam[:cam.index("Kamera cozunurlugu")]


# --- the alarm survives Windows removing the keyboard hook ----------------------


def test_the_os_key_state_rings_the_alarm_when_the_hook_is_dead():
    """Windows silently removes a slow low-level hook; the hub then never
    sees F12 again. The physical key state (GetAsyncKeyState) still does."""
    down = set()
    w, kb, player, lines = _start({}, os_keys=lambda vk: vk in down)
    down.add(al.vk_of("f12"))              # held, but no hook event at all
    for i in range(50):
        w.core.tick(i * 0.05)
    assert w.core.ringing
    assert any("kancasi" in m for m in lines)


def test_a_live_hook_and_the_os_state_do_not_double_up():
    down = set()
    w, kb, player, lines = _start({}, os_keys=lambda vk: vk in down)
    kb.down("f12")
    down.add(al.vk_of("f12"))
    for i in range(50):
        w.core.tick(i * 0.05)
    assert w.core.ringing
    assert not any("kancasi" in m for m in lines)


def test_vk_codes_for_config_key_names():
    assert al.vk_of("f12") == 0x7B and al.vk_of("F1") == 0x70
    assert al.vk_of("space") == 0x20 and al.vk_of("a") == 0x41
    assert al.vk_of("scroll lock") == 0x91
    assert al.vk_of("") is None and al.vk_of("ctrl+x") is None
