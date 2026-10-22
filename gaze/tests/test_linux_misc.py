# gaze/tests/test_linux_misc.py
"""Linux helpers: clipboard, audio, on-screen keyboard, font, and the
driver's Linux paths (close signals, focused window, raising, priority).

Tests marked x11_clipboard use the REAL xclip / Tk under Xvfb (the Docker
run); everything else uses fakes and runs on Windows as well. No test opens
an audio device: sounddevice is always a fake here (conftest also refuses the
real one).
"""
import io
import os
import signal
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import pytest

from eye_gaze import alarm as al
from eye_gaze import aac
from eye_gaze import platform as pf
from eye_gaze import playback
from eye_gaze.platform import linux_misc as lm

LINUX = sys.platform.startswith("linux")
HAS_X = LINUX and bool(os.environ.get("DISPLAY"))
x11_clipboard = pytest.mark.skipif(
    not (HAS_X and lm.shutil.which("xclip")),
    reason="real X display + xclip (Docker/Xvfb)")
x11_tk = pytest.mark.skipif(not HAS_X, reason="real X display (Docker/Xvfb)")

TURKISH = "Merhaba dünya — şğüöçıİ ĞÜŞÖÇ"


def _wav(seconds, rate=22050, channels=1):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * seconds) * channels)
    return buf.getvalue()


@pytest.fixture
def x11(monkeypatch):
    """A clean X11 clipboard state: session x11, fresh sequence counter."""
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr(lm, "_seq", [0, None])
    return monkeypatch


# --- clipboard: which tool --------------------------------------------------

class _FakeRun:
    """subprocess.run stand-in: records argv + kwargs, serves a clipboard."""

    def __init__(self):
        self.calls = []
        self.board = b""

    def __call__(self, argv, **kw):
        self.calls.append((list(argv), kw))
        out = b""
        if "input" in kw:
            self.board = kw["input"]
        elif argv[0] in ("wl-paste", "xclip", "xsel"):
            out = self.board
        return subprocess.CompletedProcess(argv, 0, stdout=out)


def _tools(monkeypatch, present):
    """A Linux process (also on Windows) with exactly these tools."""
    monkeypatch.setattr(pf, "OS", "linux")
    monkeypatch.setattr(lm, "_which",
                        lambda t: "/usr/bin/" + t if t in present else None)


def test_clipboard_prefers_wl_copy_on_wayland(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("DISPLAY", ":0")          # XWayland is there too
    _tools(monkeypatch, {"wl-copy", "wl-paste", "xclip", "xsel"})
    run = _FakeRun()
    monkeypatch.setattr(lm, "_run", run)
    assert lm.clipboard_method() == "wl"
    assert lm.clipboard_set(TURKISH) is True
    argv, kw = run.calls[0]
    assert argv[0] == "wl-copy"
    assert kw["input"] == TURKISH.encode("utf-8")
    # The forked clipboard server must not inherit pipes (subprocess would
    # wait for their EOF forever) nor our session (a closed terminal).
    assert kw["stdout"] is subprocess.DEVNULL
    assert kw["stderr"] is subprocess.DEVNULL
    assert kw["start_new_session"] is True and kw["timeout"]
    # No read-back on Wayland: wl-paste may take the focus from the field.
    assert len(run.calls) == 1
    assert lm.clipboard_get() == TURKISH
    assert run.calls[-1][0][:2] == ["wl-paste", "--no-newline"]


def test_clipboard_order_on_x11(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    _tools(monkeypatch, {"xclip", "xsel", "wl-copy", "wl-paste"})
    assert lm.clipboard_method() == "xclip"
    _tools(monkeypatch, {"xsel"})
    assert lm.clipboard_method() == "xsel"
    _tools(monkeypatch, set())
    assert lm.clipboard_method() == "tk"


def test_wayland_without_any_clipboard_tool_says_what_to_install(monkeypatch):
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.delenv("DISPLAY", raising=False)
    _tools(monkeypatch, {"xclip"})
    assert lm.clipboard_get() is None
    assert "wl-clipboard" in lm.clipboard_problem()
    assert lm.clipboard_set("x") is False
    ok, why = lm.clipboard_status()
    assert ok is False and "wl-clipboard" in why


def test_an_x11_write_waits_until_the_clipboard_serves_it(x11):
    """xsel takes the selection in its forked child, after the parent we
    waited for exited: a Ctrl+V sent at once pasted the OLD clipboard."""
    x11.setenv("DISPLAY", ":0")
    _tools(x11, {"xsel"})
    run = _FakeRun()
    old = []

    def slow(argv, **kw):
        res = run(argv, **kw)
        if "input" in kw:
            old.append(b"eski")          # the child has not taken over yet
        elif old:
            old.pop()
            return subprocess.CompletedProcess(argv, 0, stdout=b"eski")
        return res
    x11.setattr(lm, "_run", slow)
    x11.setattr(lm.time, "sleep", lambda s: None)
    assert lm.clipboard_set("yeni") is True
    reads = [c for c in run.calls if "input" not in c[1]]
    assert len(reads) == 2               # the stale read, then the new text


def test_an_x11_write_that_never_lands_is_a_failure(x11):
    x11.setenv("DISPLAY", ":0")
    _tools(x11, {"xclip"})
    x11.setattr(lm, "_run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, stdout=b"baska"))
    x11.setattr(lm.time, "sleep", lambda s: None)
    t = [0.0]

    def clock():
        t[0] += 0.1
        return t[0]
    x11.setattr(lm, "_clock", clock)
    assert lm.clipboard_set("yeni") is False
    assert "geri okunamadi" in lm.clipboard_problem()


def test_a_hung_clipboard_owner_times_out_with_a_reason(x11):
    x11.setenv("DISPLAY", ":0")
    _tools(x11, {"xclip"})

    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
    x11.setattr(lm, "_run", hang)
    assert lm.clipboard_get() is None
    assert "yanit vermiyor" in lm.clipboard_problem()


# --- clipboard: the sequence number -------------------------------------------

def test_clipboard_seq_moves_when_the_content_changes(x11):
    x11.setenv("DISPLAY", ":0")
    _tools(x11, {"xclip"})
    run = _FakeRun()
    x11.setattr(lm, "_run", run)
    run.board = b"bir"
    a = lm.clipboard_seq()
    assert lm.clipboard_seq() == a              # nothing copied
    run.board = b"iki"                          # someone copied
    b = lm.clipboard_seq()
    assert b != a
    assert lm.clipboard_set("uc") is True       # our own write counts too
    c = lm.clipboard_seq()
    assert c != b
    assert lm.clipboard_seq() == c


def test_an_unreadable_clipboard_counts_as_changed(x11):
    """'Unknown' must not read as 'nothing copied': F4 then reads and says
    the real problem instead of 'select the text first'."""
    x11.setenv("DISPLAY", ":0")
    _tools(x11, {"xclip"})

    def broken(argv, **kw):
        raise OSError("xclip bozuk")
    x11.setattr(lm, "_run", broken)
    assert lm.clipboard_seq() != lm.clipboard_seq()


# --- speech routes through linux_misc on Linux ---------------------------------

def test_speech_clipboard_uses_linux_misc_without_win32(monkeypatch):
    import eye_gaze.speech as sp
    monkeypatch.setattr(sp, "_LINUX", True)
    monkeypatch.setattr(sp, "_win32", lambda: (None, None))
    board = {"t": None, "seq": 7}
    monkeypatch.setattr(lm, "clipboard_get", lambda: board["t"])
    monkeypatch.setattr(lm, "clipboard_problem",
                        lambda: "pano sahibi program yanit vermiyor (xclip)")

    def put(text):
        board["t"] = text
        return True
    monkeypatch.setattr(lm, "clipboard_set", put)
    monkeypatch.setattr(lm, "clipboard_seq", lambda: board["seq"])
    assert sp.get_clipboard_text() == ""
    assert "yanit vermiyor" in sp.clipboard_problem()
    assert sp.set_clipboard_text(TURKISH) is True
    assert sp.get_clipboard_text() == TURKISH
    assert sp.clipboard_problem() == ""
    board["t"] = ""
    assert sp.get_clipboard_text() == ""
    assert sp.clipboard_problem() == "panoda metin yok"
    assert sp.clipboard_sequence() == 7


def test_speech_clipboard_on_windows_without_win32_is_unchanged(monkeypatch):
    import eye_gaze.speech as sp
    monkeypatch.setattr(sp, "_LINUX", False)
    monkeypatch.setattr(sp, "_win32", lambda: (None, None))
    monkeypatch.setattr(lm, "clipboard_get", lambda: pytest.fail("linux"))
    assert sp.get_clipboard_text() == ""
    assert sp.clipboard_problem() == "Windows pano arabirimi acilamadi"
    assert sp.set_clipboard_text("x") is False
    assert sp.clipboard_sequence() is None


# --- clipboard: the real thing (Xvfb) ------------------------------------------

def _xclip_out():
    return subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                          capture_output=True, timeout=5).stdout.decode("utf-8")


@x11_clipboard
def test_clipboard_roundtrip_xclip(x11):
    assert lm.clipboard_method() == "xclip"
    before = lm.clipboard_seq()
    assert lm.clipboard_set(TURKISH) is True
    assert lm.clipboard_get() == TURKISH
    assert _xclip_out() == TURKISH               # another client sees it
    assert lm.clipboard_seq() != before
    import eye_gaze.speech as sp                 # and the driver's doors
    assert sp.set_clipboard_text("Ağrım var") is True
    assert sp.get_clipboard_text() == "Ağrım var"


@x11_clipboard
def test_the_clipboard_outlives_the_process_that_set_it(tmp_path):
    """A dictated sentence must still be pasteable after we exit - and the
    set must not hang on the forked xclip server holding our pipes."""
    gaze = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from eye_gaze.platform import linux_misc as lm\n"
            "sys.exit(0 if lm.clipboard_set(%r) else 3)\n" % (gaze, TURKISH))
    env = dict(os.environ, XDG_SESSION_TYPE="x11")
    t = time.monotonic()
    r = subprocess.run([sys.executable, "-c", code], env=env, timeout=20,
                       capture_output=True)
    assert r.returncode == 0, r.stderr
    assert time.monotonic() - t < 10
    assert _xclip_out() == TURKISH


@x11_tk
def test_the_tk_clipboard_serves_other_programs(x11):
    """Last resort without xclip/xsel: our hidden Tk root owns the
    clipboard and its event loop answers other programs' pastes."""
    _tools(x11, set())
    assert lm.clipboard_method() == "tk"
    assert lm.clipboard_set(TURKISH) is True
    assert lm.clipboard_get() == TURKISH
    if lm.shutil.which("xclip"):
        assert _xclip_out() == TURKISH


@x11_tk
def test_a_process_using_the_tk_clipboard_exits_cleanly():
    """Tcl aborts the process ("Tcl_AsyncDelete") when its interpreter is
    deleted by another thread - the keeper destroys it on its own."""
    gaze = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from eye_gaze.platform import linux_misc as lm\n"
            "lm._which = lambda t: None\n"
            "assert lm.clipboard_set('tk'), lm.clipboard_problem()\n"
            "assert lm.clipboard_get() == 'tk'\n" % gaze)
    env = dict(os.environ, XDG_SESSION_TYPE="x11")
    r = subprocess.run([sys.executable, "-c", code], env=env, timeout=30,
                       capture_output=True)
    assert r.returncode == 0, r.stderr
    assert b"Tcl_AsyncDelete" not in r.stderr


# --- on-screen keyboard -------------------------------------------------------------

def test_osk_none_found_returns_reason(monkeypatch):
    _tools(monkeypatch, set())
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.setattr(lm, "_popen", lambda *a, **k: pytest.fail("popen"))
    ok, why = lm.launch_osk()
    assert ok is False
    assert "onboard" in why and why.strip()
    assert lm.osk_status() == (False, why)


def test_osk_starts_onboard_detached(monkeypatch):
    _tools(monkeypatch, {"onboard", "gsettings"})
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    seen = []
    monkeypatch.setattr(lm, "_popen", lambda argv, **kw: seen.append(
        (argv, kw)))
    assert lm.launch_osk() == (True, "onboard")
    argv, kw = seen[0]
    assert argv == ["/usr/bin/onboard"]
    assert kw["start_new_session"] is True
    assert kw["stdout"] is subprocess.DEVNULL


def test_osk_on_gnome_switches_the_shell_keyboard_on(monkeypatch):
    _tools(monkeypatch, {"gsettings"})
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    run = _FakeRun()
    monkeypatch.setattr(lm, "_run", run)
    ok, why = lm.launch_osk()
    assert ok is True and "GNOME" in why
    assert run.calls[0][0][-2:] == ["screen-keyboard-enabled", "true"]
    monkeypatch.setattr(lm, "_run", lambda argv, **kw: (
        subprocess.CompletedProcess(argv, 1)))
    ok, why = lm.launch_osk()
    assert ok is False and "gsettings" in why


def test_osk_key_on_linux_says_the_reason_once(monkeypatch, capsys):
    import gaze_driver as gd
    monkeypatch.setattr(gd, "_OSK_SAID", [False])
    monkeypatch.setattr(lm, "launch_osk", lambda: (
        False, "Ekran klavyesi bulunamadı; 'onboard' paketini kurun"))
    assert gd._open_osk_linux() is False
    assert gd._open_osk_linux() is False
    out = capsys.readouterr().out
    out.encode("ascii")                            # cp1254 console
    assert out.count("onboard") == 1
    assert "nedeni yukarida" in out


# --- font ------------------------------------------------------------------------------

def test_aac_font_has_turkish_glyphs():
    from PIL import ImageFont
    path = lm.font_path()
    assert os.path.isfile(path), path
    font = ImageFont.truetype(path, 40)

    def ink(ch):
        mask = font.getmask(ch)
        return bytes(mask), mask.size
    # A code point no font has: FreeType draws glyph 0, the ".notdef" box.
    notdef = ink("\U0010FFFD")
    for ch in "ğüşıöçİĞÜŞÖÇ":
        drawn = ink(ch)
        assert any(drawn[0]), "%r is blank" % ch
        assert drawn != notdef, "%r is the .notdef box" % ch


def test_the_aac_grid_uses_dejavu_on_linux_and_keeps_windows_fonts(
        monkeypatch):
    monkeypatch.setattr(aac, "_WINDOWS", False)
    assert aac._font_paths() == (lm.font_path(),)
    font = aac._load_font(30)
    assert os.path.basename(font.path) == "DejaVuSans.ttf"
    monkeypatch.setattr(aac, "_WINDOWS", True)
    paths = aac._font_paths()
    assert paths[:3] == aac._FONT_CANDIDATES      # Windows look unchanged
    assert paths[-1] == lm.font_path()


def test_the_font_is_stored_as_binary():
    repo = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    try:
        r = subprocess.run(["git", "check-attr", "binary", "--",
                            "gaze/resources/fonts/DejaVuSans.ttf"],
                           cwd=repo, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    assert r.stdout.decode().strip().endswith("binary: set")
    lic = os.path.join(os.path.dirname(lm.font_path()), "LICENSE-DejaVu.txt")
    with open(lic, encoding="utf-8") as f:
        assert "Bitstream" in f.read()


# --- audio -------------------------------------------------------------------------------

class _FakeSD:
    """sounddevice stand-in. OutputStream feeds its callback from a thread
    in real time (blocks of 512 frames), as PortAudio does."""

    class CallbackStop(Exception):
        pass

    class PortAudioError(Exception):
        pass

    def __init__(self, fail=None):
        self.fail = fail
        self.streams = []
        sd = self

        class OutputStream:
            def __init__(self, samplerate, channels, dtype, callback):
                if sd.fail:
                    raise sd.PortAudioError(sd.fail)
                self.rate, self.channels, self.dtype = samplerate, channels, dtype
                self.cb = callback
                self.frames = 0
                self.aborted = False
                self.closed = False
                self._stop = threading.Event()
                sd.streams.append(self)

            def start(self):
                self._t = threading.Thread(target=self._feed, daemon=True)
                self._t.start()

            def _feed(self):
                block = 512
                while not self._stop.is_set():
                    out = np.zeros((block, self.channels), dtype=self.dtype)
                    try:
                        self.cb(out, block, None, None)
                    except sd.CallbackStop:
                        return
                    self.frames += block
                    self._stop.wait(block / float(self.rate))

            def abort(self):
                self.aborted = True
                self._stop.set()
                self._t.join(1.0)

            def close(self):
                self.closed = True
        self.OutputStream = OutputStream

    def query_devices(self, kind=None):
        if self.fail:
            raise self.PortAudioError(self.fail)
        return {"name": "fake", "max_output_channels": 2}


def test_sounddevice_backend_stop_is_immediate():
    sd = _FakeSD()
    be = playback._SoundDeviceBackend(sd=sd, log=lambda m: None)
    assert be.start(_wav(3.0)) is True
    time.sleep(0.1)
    s = sd.streams[0]
    assert s.frames > 0 and s.rate == 22050 and s.channels == 1
    t = time.perf_counter()
    be.stop()
    assert time.perf_counter() - t < 0.1
    assert s.aborted and s.closed
    after = s.frames
    time.sleep(0.1)
    assert s.frames == after                    # nothing sounds after stop


def test_the_player_stops_a_sentence_on_linux_within_a_tenth(monkeypatch):
    sd = _FakeSD()
    be = playback._SoundDeviceBackend(sd=sd, log=lambda m: None)
    p = playback.WavPlayer(backend=be, tail=0.0)
    res = {}
    th = threading.Thread(target=lambda: res.setdefault("r", p.play(_wav(5))))
    th.start()
    time.sleep(0.1)
    t = time.perf_counter()
    p.stop()
    th.join(2.0)
    assert res["r"] is False
    assert time.perf_counter() - t < 0.15
    assert sd.streams[0].aborted


def test_a_new_sound_replaces_the_one_playing():
    """PlaySound's one-channel rule, which hold()/release() rely on."""
    sd = _FakeSD()
    be = playback._SoundDeviceBackend(sd=sd, log=lambda m: None)
    be.start(_wav(3.0))
    be.start(_wav(0.2, channels=2))
    assert sd.streams[0].aborted and sd.streams[0].closed
    assert sd.streams[1].channels == 2
    be.stop()


def test_a_sound_plays_to_its_end_and_stops_itself():
    sd = _FakeSD()
    be = playback._SoundDeviceBackend(sd=sd, log=lambda m: None)
    be.start(_wav(0.05))
    time.sleep(0.3)
    s = sd.streams[0]
    assert 1024 <= s.frames <= 22050 * 0.05 + 512
    assert not s._t.is_alive()


def test_no_audio_device_is_said_once_and_reported(monkeypatch):
    sd = _FakeSD(fail="Error querying device -1")
    said = []
    be = playback._SoundDeviceBackend(sd=sd, log=said.append)
    monkeypatch.setattr(playback, "_SD_BACKEND", be)
    assert be.start(_wav(0.1)) is False
    assert be.start(_wav(0.1)) is False
    assert be.stop() is None                     # never raises
    assert len(said) == 1
    said[0].encode("ascii")
    assert "ALARM" in said[0] and "PortAudioError" in said[0]
    ok, why = playback.audio_status()
    assert ok is False and "alarm" in why
    assert be.probe()[0] is False
    assert len(said) == 1


def test_linux_player_falls_back_to_silence_not_to_winsound(monkeypatch):
    monkeypatch.setattr(playback, "_LINUX", True)
    sd = _FakeSD(fail="no device")
    monkeypatch.setattr(playback, "_SD_BACKEND",
                        playback._SoundDeviceBackend(sd=sd, log=lambda m: None))
    p = playback.WavPlayer(tail=0.0)
    assert p._backend is playback._SD_BACKEND
    assert p._fallback is playback._no_sound
    assert p.try_play(_wav(0.05)) is True       # silent, but never raises


def test_wav_pcm_reads_what_the_voices_write():
    data, rate = playback.wav_pcm(_wav(0.5, rate=16000, channels=2))
    assert rate == 16000 and data.shape == (8000, 2) and data.dtype == np.int16
    assert playback.wav_pcm(b"RIFF....WAVEjunk") is None
    assert playback.wav_pcm(None) is None
    siren, _ = al.build_cycle_wav(None)
    data, rate = playback.wav_pcm(siren)
    assert rate == al._RATE and data.shape[1] == 1


def test_capabilities_name_the_audio_state(monkeypatch):
    monkeypatch.setattr(pf, "OS", "windows")
    assert pf.capabilities()["audio"] == (True, "")
    assert "audio" in pf.CAPABILITY_KEYS


# --- the alarm on Linux ---------------------------------------------------------------

class _FakeAudio:
    def __init__(self, ok=True, tone_ok=True):
        self.ok = ok
        self.tone_ok = tone_ok
        self.started = []
        self.stops = 0
        self.tones = []

    def start(self, wav):
        self.started.append(wav)
        return self.ok

    def stop(self):
        self.stops += 1

    def tone(self, f, ms):
        self.tones.append(f)
        return self.tone_ok

    def probe(self):
        return (self.ok, "" if self.ok else "PortAudioError: yok")


class _Channel:
    def __init__(self):
        self.calls = []

    def hold(self):
        self.calls.append("hold")

    def release(self):
        self.calls.append("release")


def test_alarm_tone_player_linux_plays_via_playback(monkeypatch, tmp_path):
    monkeypatch.setattr(al, "_LINUX", True)
    monkeypatch.setitem(sys.modules, "winsound", None)     # no winsound
    audio = _FakeAudio()
    monkeypatch.setattr(playback, "audio_backend", lambda: audio)
    ch = _Channel()
    p = al.TonePlayer(tmpdir=str(tmp_path), channel=ch)
    assert p._snd is None and p._audio is audio
    p.start(0.0)
    assert ch.calls == ["hold"]
    assert len(audio.started) == 1 and audio.started[0][:4] == b"RIFF"
    assert playback.wav_duration(audio.started[0]) == pytest.approx(
        p.cycle_seconds)
    p.tick(0.5)
    assert len(audio.started) == 1                 # mid-cycle
    p.tick(p.cycle_seconds + 0.1)
    assert len(audio.started) == 2                 # re-issued each cycle
    p.stop()
    assert audio.stops == 1 and ch.calls == ["hold", "release"]
    p.tick(1000.0)
    assert len(audio.started) == 2
    p.close()
    assert os.listdir(str(tmp_path)) == []          # no temp files on Linux


@pytest.mark.skipif(not LINUX, reason="the real Linux default")
def test_the_real_linux_alarm_uses_the_shared_output():
    p = al.TonePlayer()
    assert p._audio is playback.audio_backend()
    assert p.probes_audio is True
    p.close()


def test_alarm_beeps_with_a_sine_when_the_siren_will_not_start(tmp_path):
    made = []

    class T:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            made.append(self)

        def start(self):
            pass
    audio = _FakeAudio(ok=False)
    p = al.TonePlayer(sound=None, audio=audio, tmpdir=str(tmp_path), thread=T)
    p.start(0.0)
    assert made, "the beep fallback must start"
    n = {"k": 0}

    def tone(f, ms):
        n["k"] += 1
        if n["k"] >= 4:
            p.stop()
        return True
    audio.tone = tone
    made[0].target()
    assert n["k"] >= 4


def test_alarm_beeps_do_not_spin_without_a_device(tmp_path):
    made = []

    class T:
        def __init__(self, target=None, args=(), daemon=None):
            self.target = target
            made.append(self)

        def start(self):
            pass
    audio = _FakeAudio(ok=False, tone_ok=False)
    p = al.TonePlayer(sound=None, audio=audio, tmpdir=str(tmp_path), thread=T)
    p.start(0.0)
    made[0].target()                     # returns at once instead of looping
    assert audio.tones == [960]
    p.stop()


def test_a_silent_alarm_is_said_at_start_up():
    lines = []

    class RunNow:
        def __init__(self, target=None, args=(), daemon=None):
            self.target, self.args = target, args
            self.loop = target.__name__ == "_loop"

        def start(self):
            if not self.loop:
                self.target(*self.args)
    p = al.TonePlayer(sound=None, audio=_FakeAudio(ok=False))
    w = al.AlarmWatcher(al.AlarmCore(p, [("f12", lambda now: False, 2.0)],
                                     log=lines.append),
                        log=lines.append, thread=RunNow)
    w.start()
    assert any("SESSIZ" in ln for ln in lines), lines
    lines.clear()
    p_ok = al.TonePlayer(sound=None, audio=_FakeAudio(ok=True))
    al.AlarmWatcher(al.AlarmCore(p_ok, [], log=lines.append),
                    log=lines.append, thread=RunNow).start()
    assert lines == []


def test_alarm_key_backup_reads_the_linux_key_state(monkeypatch):
    monkeypatch.setattr(al, "_LINUX", True)
    asked = []

    class KB:
        def is_pressed(self, name):
            asked.append(name)
            return name == "f12"
    monkeypatch.setattr(pf, "keyboard_api", lambda: KB())
    down = al._os_key_reader()
    assert down(al.vk_of("f12")) is True
    assert down(al.vk_of("scroll lock")) is False
    assert asked == ["f12", "scroll lock"]
    for name in ("f1", "f24", "space", "a", "7", "page down", "pause"):
        assert al.name_of_vk(al.vk_of(name)) == name


def test_the_linux_hook_warning_does_not_blame_windows(monkeypatch):
    monkeypatch.setattr(al, "_LINUX", True)
    said = []
    poll = al._held_with_backup(lambda: False, al.vk_of("f12"),
                                lambda vk: True, "f12", said.append)
    poll(0.0)
    poll(1.0)
    assert len(said) == 1 and "Windows" not in said[0]


# --- the driver's Linux paths ---------------------------------------------------------

@pytest.fixture
def driver_linux(monkeypatch):
    import gaze_driver as gd
    monkeypatch.setattr(gd._platform, "OS", "linux")
    saved = {}
    for name in ("SIGHUP", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            saved[sig] = signal.getsignal(sig)
    yield gd
    for sig, h in saved.items():
        signal.signal(sig, h)


class _Clicker:
    def __init__(self):
        self.released = 0

    def release(self):
        self.released += 1


class _LetGo:
    def __init__(self, log):
        self.log = log

    def let_go(self):
        self.log.append("evdev let go")


def _evdev_handler(log, prev):
    """The REAL EvdevBackend._on_signal, on a backend without devices."""
    from eye_gaze.platform import linux_evdev
    b = linux_evdev.EvdevBackend.__new__(linux_evdev.EvdevBackend)
    b.keyboard = _LetGo(log)
    b._prev_handlers = {signal.SIGTERM: prev}
    return b._on_signal


def test_close_guard_chains_to_the_evdev_handler(driver_linux):
    gd = driver_linux
    log = []
    sig = signal.SIGTERM

    def original(signum, frame):
        log.append("original")
    # evdev installed first (it chains to `original`), then the driver.
    signal.signal(sig, _evdev_handler(log, original))
    clicker = _Clicker()
    gd._close_guard(clicker)
    handler = signal.getsignal(sig)
    handler(sig, None)
    assert clicker.released == 1                 # the drag let go
    assert log == ["evdev let go", "original"]   # evdev's handler still ran


def test_evdev_installed_after_the_close_guard_chains_to_it(driver_linux):
    gd = driver_linux
    log = []
    sig = signal.SIGTERM
    signal.signal(sig, lambda s, f: log.append("original"))
    clicker = _Clicker()
    gd._close_guard(clicker)
    signal.signal(sig, _evdev_handler(log, signal.getsignal(sig)))
    signal.getsignal(sig)(sig, None)
    assert log == ["evdev let go", "original"] and clicker.released == 1


def test_close_guard_leaves_an_ignored_signal_alone(driver_linux):
    gd = driver_linux
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    gd._close_guard(_Clicker())
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_IGN


def test_close_guard_off_the_main_thread_says_so(driver_linux, capsys):
    gd = driver_linux
    th = threading.Thread(target=gd._close_guard, args=(_Clicker(),))
    th.start()
    th.join()
    out = capsys.readouterr().out
    assert "yakalanamadi" in out
    out.encode("ascii")


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signal")
def test_a_real_sighup_releases_the_drag(driver_linux):
    gd = driver_linux
    got = []
    signal.signal(signal.SIGHUP, lambda s, f: got.append(s))
    clicker = _Clicker()
    gd._close_guard(clicker)
    os.kill(os.getpid(), signal.SIGHUP)
    for _ in range(100):                 # the handler runs between bytecodes
        if got:
            break
        time.sleep(0.01)
    assert got == [signal.SIGHUP] and clicker.released == 1


def test_raise_process_priority_is_a_no_op_on_linux(driver_linux, monkeypatch):
    monkeypatch.setattr(os, "nice", lambda n: pytest.fail("os.nice"),
                        raising=False)
    driver_linux._raise_process_priority()


class _WinBackend:
    def __init__(self, pid):
        self.pid = pid
        self.raised = []

    def foreground_pid(self):
        return self.pid

    def raise_window(self, title):
        self.raised.append(title)
        return True


def test_foreground_is_ours_on_linux(driver_linux, monkeypatch):
    gd = driver_linux
    b = _WinBackend(os.getpid())
    monkeypatch.setattr(pf, "linux_backend", lambda: b)
    monkeypatch.setattr(lm, "_on_terminal", lambda: False)
    assert gd._foreground_is_ours() is True
    b.pid = 999999
    assert gd._foreground_is_ours() is False
    b.pid = None                         # the window manager does not say
    assert gd._foreground_is_ours() is False
    monkeypatch.setattr(pf, "linux_backend", lambda: pf.NullBackend())
    assert gd._foreground_is_ours() is False     # Wayland: unknown


def test_our_terminal_counts_as_ours(monkeypatch):
    """Ctrl+C into the terminal running the driver would SIGINT it: that
    window belongs to the terminal emulator, an ancestor of ours."""
    monkeypatch.setattr(lm, "_on_terminal", lambda: True)
    parents = {os.getppid(): 4321, 4321: 1}
    monkeypatch.setattr(lm, "_parent_pid", lambda pid: parents.get(pid))
    b = _WinBackend(4321)
    monkeypatch.setattr(pf, "linux_backend", lambda: b)
    assert lm.foreground_is_ours() is True
    monkeypatch.setattr(lm, "_on_terminal", lambda: False)
    assert lm.foreground_is_ours() is False


@pytest.mark.skipif(not LINUX, reason="/proc")
def test_parent_pid_reads_proc():
    assert lm._parent_pid(os.getpid()) == os.getppid()


def test_force_topmost_on_linux_raises_at_most_every_quarter_second(
        driver_linux, monkeypatch):
    gd = driver_linux
    b = _WinBackend(1)
    monkeypatch.setattr(pf, "linux_backend", lambda: b)
    monkeypatch.setattr(lm, "_raised", {})
    t = [100.0]
    monkeypatch.setattr(lm, "_clock", lambda: t[0])
    gd._force_topmost("Buyutec")
    gd._force_topmost("Buyutec")
    gd._force_topmost("AAC")
    assert b.raised == ["Buyutec", "AAC"]
    t[0] += 0.3
    gd._force_topmost("Buyutec")
    assert b.raised == ["Buyutec", "AAC", "Buyutec"]


def test_force_topmost_without_x_is_said_once(driver_linux, monkeypatch,
                                              capsys):
    monkeypatch.setattr(pf, "linux_backend", lambda: pf.NullBackend())
    monkeypatch.setattr(lm, "_said_no_raise", [False])
    driver_linux._force_topmost("Buyutec")
    driver_linux._force_topmost("Buyutec")
    out = capsys.readouterr().out
    assert out.count("[pencere]") == 1
    out.encode("ascii")


def test_evdev_backend_reaches_windows_through_its_display():
    from eye_gaze.platform import linux_evdev
    b = linux_evdev.EvdevBackend.__new__(linux_evdev.EvdevBackend)
    b._x = None
    assert b.foreground_pid() is None and b.raise_window("x") is False
    b._x = _WinBackend(77)
    assert b.foreground_pid() == 77 and b.raise_window("x") is True


def test_a_signal_inside_the_backend_lock_does_not_hang_the_exit(
        driver_linux, monkeypatch):
    """The handler interrupts the main thread between two bytecodes - maybe
    while it holds the input backend's lock (set_cursor, every frame).
    Releasing the drag right there would wait on that lock forever."""
    gd = driver_linux
    monkeypatch.setattr(gd, "_CLOSE_RELEASE_WAIT", 0.2)
    lock = threading.Lock()
    log = []

    class LockedClicker:
        def release(self):
            with lock:                    # what button() would do
                log.append("released")
    signal.signal(signal.SIGTERM, lambda s, f: log.append("original"))
    gd._close_guard(LockedClicker())
    with lock:                            # the frame the signal interrupted
        t = time.perf_counter()
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert time.perf_counter() - t < 1.0
        assert log == ["original"]        # chained on, did not hang
    for _ in range(100):                  # and the release still lands
        if "released" in log:
            break
        time.sleep(0.01)
    assert log == ["original", "released"]


# --- fix round 1 ---------------------------------------------------------------------

def test_a_dead_device_is_retried_once_per_cycle_not_every_tick(tmp_path):
    """Measured in review: 100 alarm ticks re-opened PortAudio 100 times and
    started 101 beep threads."""
    sd = _FakeSD(fail="Error querying device -1")
    opened = []
    real = sd.OutputStream

    def counting(*a, **k):
        opened.append(1)
        return real(*a, **k)
    sd.OutputStream = counting
    audio = playback._SoundDeviceBackend(sd=sd, log=lambda m: None)
    made = []

    class T:
        def __init__(self, target=None, args=(), daemon=None):
            made.append(target)

        def start(self):
            pass
    p = al.TonePlayer(sound=None, audio=audio, tmpdir=str(tmp_path), thread=T)
    p.start(0.0)
    for i in range(1, 101):              # 5 s of the 20 Hz alarm loop
        p.tick(i * 0.05)
    cycles = 5.0 / p.cycle_seconds
    assert len(opened) <= int(cycles) + 2, len(opened)
    assert len(opened) >= 2              # but it does try again (replug)
    assert made == []                    # no beep thread for a dead device
    p.stop()


def test_a_failed_sounddevice_import_is_not_retried(monkeypatch):
    calls = []

    def refuse():
        calls.append(1)
        raise OSError("PortAudio library not found")
    monkeypatch.setattr(playback, "_import_sounddevice", refuse)
    be = playback._SoundDeviceBackend(log=lambda m: None)
    for _ in range(5):
        assert be.start(_wav(0.1)) is False
    assert be.probe()[0] is False
    assert calls == [1]
    assert "PortAudio library" in be.error


def test_the_remembered_import_error_does_not_pile_up_tracebacks(
        monkeypatch):
    # Final review M-10: every re-raise stacked another traceback level on
    # the one stored exception (a leak per sentence, earcon, alarm cycle).
    import traceback

    def refuse():
        raise OSError("PortAudio library not found")
    monkeypatch.setattr(playback, "_import_sounddevice", refuse)
    be = playback._SoundDeviceBackend(log=lambda m: None)
    depths = []
    for _ in range(6):
        try:
            be._module()
        except OSError as e:
            depths.append(len(traceback.extract_tb(e.__traceback__)))
    assert len(set(depths[1:])) == 1, depths
    assert depths[-1] <= depths[0], depths
    for _ in range(3):
        assert be.start(_wav(0.1)) is False
    assert len(traceback.extract_tb(be._import_error.__traceback__))         <= depths[0]


def test_probe_waits_for_a_start_or_stop_in_progress():
    be = playback._SoundDeviceBackend(sd=_FakeSD(), log=lambda m: None)
    res = []
    with be._lock:
        th = threading.Thread(target=lambda: res.append(be.probe()))
        th.start()
        time.sleep(0.05)
        assert res == []                 # PortAudio is not thread-safe
    th.join(1.0)
    assert res == [(True, "")]


def test_a_keyboard_that_will_not_start_is_retried_every_few_seconds(
        monkeypatch):
    monkeypatch.setattr(al, "_LINUX", True)
    tries = []

    def broken():
        tries.append(1)
        raise RuntimeError("no backend")
    monkeypatch.setattr(pf, "keyboard_api", broken)
    t = [100.0]
    monkeypatch.setattr(al.time, "monotonic", lambda: t[0])
    down = al._os_key_reader()
    with pytest.raises(RuntimeError):
        down(al.vk_of("f12"))
    for _ in range(40):                  # two seconds at 20 Hz
        assert down(al.vk_of("f12")) is False
    assert tries == [1]
    t[0] += al._KB_RETRY + 0.1
    with pytest.raises(RuntimeError):
        down(al.vk_of("f12"))
    assert tries == [1, 1]


def test_gnome_osk_says_it_stays_on_and_how_to_turn_it_off(monkeypatch):
    _tools(monkeypatch, {"gsettings"})
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(lm, "_run", _FakeRun())
    ok, why = lm.launch_osk()
    assert ok and "açık kalır" in why and "Ayarlar" in why
    assert "Ayarlar" in lm.osk_status()[1]


def test_the_signal_handler_creates_no_thread(driver_linux, monkeypatch):
    gd = driver_linux
    signal.signal(signal.SIGTERM, lambda s, f: None)
    clicker = _Clicker()
    gd._close_guard(clicker)

    def no_thread(*a, **k):
        raise AssertionError("Thread() inside a signal handler")
    monkeypatch.setattr(gd.threading, "Thread", no_thread)
    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    assert clicker.released == 1
    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)   # second: no-op
    assert clicker.released == 1


# F4: clear first, so the same selection copied twice still speaks.

class _Board:
    def __init__(self):
        self.text = "eski dikte"
        self.selection = "Ağrım var"

    def clear(self):
        self.text = ""
        return True

    def copy(self):
        self.text = self.selection

    def read(self):
        return self.text


def test_two_f4s_on_the_same_selection_both_speak():
    import gaze_driver as gd
    b = _Board()

    def seq():                            # content-derived, as on Linux
        return lm._digest(b.text)
    for _ in range(2):
        text, note = gd._selection_text(False, b.copy, seq, b.read,
                                        wait=0, poll=0, clear=b.clear)
        assert text == "Ağrım var", note


def test_f4_with_nothing_selected_after_a_clear_finds_nothing():
    import gaze_driver as gd
    b = _Board()
    b.copy = lambda: None                 # the window ignored Ctrl+C
    text, _ = gd._selection_text(False, b.copy, lambda: 1, b.read,
                                 wait=0, poll=0, clear=b.clear)
    assert text == ""                     # never the old "eski dikte"


def test_f4_falls_back_to_the_sequence_when_the_clear_fails():
    import gaze_driver as gd
    b = _Board()
    b.clear = lambda: False
    b.copy = lambda: None
    text, note = gd._selection_text(False, b.copy, lambda: 1, b.read,
                                    wait=0, poll=0, tries=2, clear=b.clear)
    assert text is None and "SECIN" in note


def test_clipboard_clear_verifies_it_reads_empty(x11):
    x11.setenv("DISPLAY", ":0")
    _tools(x11, {"xsel"})
    run = _FakeRun()
    run.board = b"dolu"

    def serve(argv, **kw):
        if argv[-1] == "--clear":
            run.board = b""
        return run(argv, **kw)
    x11.setattr(lm, "_run", serve)
    assert lm.clipboard_clear() is True
    assert ["xsel", "--clipboard", "--clear"] in [c[0] for c in run.calls]
    x11.setattr(lm, "_run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 0, stdout=b"inatci"))
    x11.setattr(lm.time, "sleep", lambda s: None)
    t = [0.0]

    def clock():
        t[0] += 0.1
        return t[0]
    x11.setattr(lm, "_clock", clock)
    assert lm.clipboard_clear() is False


@x11_clipboard
def test_two_real_f4s_on_the_same_text_both_speak(x11):
    import gaze_driver as gd

    def app_copies():                     # what the focused app does on ^C
        subprocess.run(["xclip", "-selection", "clipboard", "-i"],
                       input="Su istiyorum".encode("utf-8"),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=5)
    for _ in range(2):
        text, note = gd._selection_text(False, app_copies, lm.clipboard_seq,
                                        lm.clipboard_get,
                                        clear=lm.clipboard_clear)
        assert text == "Su istiyorum", (note, lm.clipboard_problem())


# The terminal's Ctrl+C: a pty whose foreground group is the child.

_TTY_CHILD = "\n".join([
    "import os, sys, time",
    "sys.path.insert(0, %r)",
    "from eye_gaze.platform import linux_misc as lm",
    "if sys.argv[1] == 'guard':",
    "    with lm.interrupt_key_off(settle=0.3) as on:",
    "        os.write(1, b'READY %%d\\n' %% on)",
    "        time.sleep(0.6)",
    "else:",
    "    os.write(1, b'READY 0\\n')",
    "    time.sleep(0.6)",
    "os.write(1, b'SURVIVED\\n')",
])


def _run_on_a_pty(mode):
    import fcntl
    import pty
    import termios
    master, slave = pty.openpty()
    gaze = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def own_tty():
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    p = subprocess.Popen([sys.executable, "-c", _TTY_CHILD % gaze, mode],
                         stdin=slave, stdout=slave, stderr=slave,
                         start_new_session=True, preexec_fn=own_tty)
    os.close(slave)
    out = b""
    deadline = time.monotonic() + 20
    while b"READY" not in out and time.monotonic() < deadline:
        try:
            out += os.read(master, 1024)
        except OSError:
            break
    os.write(master, b"\x03")              # F4's Ctrl+C reaching the tty
    p.wait(20)
    try:
        while True:
            chunk = os.read(master, 1024)
            if not chunk:
                break
            out += chunk
    except OSError:
        pass
    os.close(master)
    return p.returncode, out


@pytest.mark.skipif(not LINUX, reason="POSIX pty")
def test_the_terminal_ctrl_c_does_not_kill_the_driver():
    code, out = _run_on_a_pty("guard")
    assert b"READY 1" in out, out
    assert code == 0 and b"SURVIVED" in out, (code, out)


@pytest.mark.skipif(not LINUX, reason="POSIX pty")
def test_without_the_guard_the_terminal_ctrl_c_kills_it():
    """Non-vacuity: the same ^C on the same pty ends an unguarded process."""
    code, out = _run_on_a_pty("plain")
    assert b"READY" in out, out
    assert code != 0 and b"SURVIVED" not in out, (code, out)


def test_interrupt_key_off_without_a_terminal_does_nothing(monkeypatch):
    monkeypatch.setattr(lm, "_foreground_tty", lambda: (None, False))
    with lm.interrupt_key_off(settle=0) as on:
        assert on is False
