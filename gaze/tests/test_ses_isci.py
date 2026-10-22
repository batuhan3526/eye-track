# gaze/tests/test_ses_isci.py
"""Task 18b (R44): piper/espeak-ng in their own process in packaged builds.

The protocol (eye_gaze/ses_protokol.py), the worker (gozfaresi_ses.py), the
program's side (eye_gaze/ses_isci.py) and how the piper backend picks it.
Real processes are a fake worker run by THIS interpreter (no piper needed)
and, when piper and its voice are installed, the real worker script."""
import io
import os
import subprocess
import sys
import textwrap
import time
import wave

import pytest

from eye_gaze import ses_isci, ses_protokol as sp

GAZE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER_SCRIPT = os.path.join(GAZE, "gozfaresi_ses.py")


# --- protocol -------------------------------------------------------------------

def test_a_frame_round_trips_and_end_of_file_is_none():
    buf = io.BytesIO()
    sp.write_frame(buf, {"op": "synth", "text": "Su istiyorum çğış"},
                   b"RIFF\x00\x01")
    sp.write_frame(buf, {"ok": True})
    buf.seek(0)
    assert sp.read_frame(buf) == ({"op": "synth",
                                   "text": "Su istiyorum çğış"},
                                  b"RIFF\x00\x01")
    assert sp.read_frame(buf) == ({"ok": True}, b"")
    assert sp.read_frame(buf) is None
    # Headers travel ASCII-only (JSON escapes), whatever the text.
    one = io.BytesIO()
    sp.write_frame(one, {"t": "çğış"})
    assert one.getvalue()[4:].isascii()


@pytest.mark.parametrize("raw", [
    b"\x00\x00",                                   # cut inside a length
    b"\x00\x00\x00\x05{\"a\"",                     # cut inside the header
    b"\x00\x00\x00\x02{}\x00\x00\x00\x09RIFF",     # cut inside the body
    b"\x00\x00\x00\x02[]\x00\x00\x00\x00",         # header not a dict
    b"\x00\x00\x00\x03{x}\x00\x00\x00\x00",        # header not JSON
    b"\x7f\xff\xff\xff",                           # absurd size
])
def test_a_broken_frame_is_a_protocol_error(raw):
    with pytest.raises(sp.ProtocolError):
        sp.read_frame(io.BytesIO(raw))


# --- the worker itself (in this process, fake piper) --------------------------

class _FakeVoice:
    loaded = []
    configs = []

    @classmethod
    def load(cls, path, espeak_data_dir=None):
        cls.loaded.append((path, espeak_data_dir))
        return cls()

    def synthesize_wav(self, text, wf, syn_config=None):
        _FakeVoice.configs.append(syn_config)
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x01\x00" * (10 * len(text)))


@pytest.fixture
def fake_piper(monkeypatch):
    import types
    mod = types.ModuleType("piper")
    mod.PiperVoice = _FakeVoice

    class SynthesisConfig:
        def __init__(self, length_scale=1.0, volume=1.0):
            self.length_scale, self.volume = length_scale, volume
    mod.SynthesisConfig = SynthesisConfig
    _FakeVoice.loaded, _FakeVoice.configs = [], []
    monkeypatch.setitem(sys.modules, "piper", mod)
    return mod


def _serve(*frames):
    import gozfaresi_ses
    ins, outs = io.BytesIO(), io.BytesIO()
    for f in frames:
        sp.write_frame(ins, f)
    ins.seek(0)
    rc = gozfaresi_ses.serve(ins, outs)
    outs.seek(0)
    replies = []
    while True:
        fr = sp.read_frame(outs)
        if fr is None:
            return rc, replies
        replies.append(fr)


def test_the_worker_loads_once_and_answers_each_sentence_with_a_wav(
        fake_piper):
    rc, replies = _serve(
        {"op": "ping"},
        {"op": "synth", "text": "erken"},
        {"op": "load", "model": "/v/tr.onnx", "espeak_dir": "/v/espeak",
         "length_scale": 1.25, "volume": 0.9},
        {"op": "synth", "text": "Su istiyorum"},
        {"op": "synth", "text": "Merhaba"},
        {"op": "nonsense"},
        {"op": "quit"},
        {"op": "synth", "text": "after quit: never answered"})
    assert rc == 0
    (ping, _), (early, _), (load, _), (s1, w1), (s2, w2), (bad, _), \
        (bye, _) = replies
    assert ping["ok"] and ping["pid"] == os.getpid()
    assert not early["ok"] and "yuklenmedi" in early["error"]
    assert load == {"ok": True, "id": None}
    assert _FakeVoice.loaded == [("/v/tr.onnx", "/v/espeak")]
    for wav, text in ((w1, "Su istiyorum"), (w2, "Merhaba")):
        with wave.open(io.BytesIO(wav)) as wf:
            assert wf.getnframes() == 10 * len(text)
    cfg = _FakeVoice.configs[0]
    assert (cfg.length_scale, cfg.volume) == (1.25, 0.9)
    assert not bad["ok"] and "bilinmeyen" in bad["error"]
    assert bye == {"ok": True, "id": None}


def test_every_answer_carries_its_request_id(fake_piper):
    _rc, replies = _serve({"op": "ping", "id": 7},
                          {"op": "load", "model": "m", "espeak_dir": "e",
                           "id": 8},
                          {"op": "synth", "text": "x", "id": 9},
                          {"op": "nope", "id": 10}, {"op": "quit", "id": 11})
    assert [h["id"] for h, _ in replies] == [7, 8, 9, 10, 11]


def test_the_worker_keeps_piper_defaults_when_speed_and_volume_are_1(
        fake_piper):
    _serve({"op": "load", "model": "m", "espeak_dir": "e"},
           {"op": "synth", "text": "x"})
    assert _FakeVoice.configs == [None]    # as the in-process backend


def test_the_worker_ends_at_end_of_file_and_on_a_broken_frame():
    import gozfaresi_ses
    assert gozfaresi_ses.serve(io.BytesIO(b""), io.BytesIO()) == 0
    assert gozfaresi_ses.serve(io.BytesIO(b"\x00\x00\x00"), io.BytesIO()) == 2


# --- the program's side, with a fake worker process ---------------------------

FAKE_WORKER = textwrap.dedent("""
    import os, sys, time
    sys.path.insert(0, %(gaze)r)
    from eye_gaze.ses_protokol import read_frame, write_frame
    mode = os.environ.get("SAHTE_MOD", "ok")
    marker = os.environ.get("SAHTE_IZ", "")
    first = bool(marker) and not os.path.exists(marker)
    if marker:
        open(marker, "a").close()
    ins, outs = sys.stdin.buffer, sys.stdout.buffer
    print("pid", os.getpid(), file=sys.stderr)
    if mode == "sagir":
        # ignores end of file: only a job object (Windows) can end it
        while True:
            time.sleep(1)
    while True:
        fr = read_frame(ins)
        if fr is None:
            sys.exit(0)
        h, _ = fr
        rid = h.get("id")
        if h["op"] == "load":
            if mode == "yukte_takilir":
                time.sleep(60)
            if mode == "yukte_olur" and (first or not marker):
                os._exit(4)
            write_frame(outs, {"ok": True, "id": rid})
        elif h["op"] == "ping":
            write_frame(outs, {"ok": True, "pid": os.getpid(), "id": rid})
        elif h["op"] == "synth":
            if mode == "olur" and first:
                os._exit(3)
            if mode == "takilir" and first:
                time.sleep(60)
            if mode == "yavas":
                time.sleep(float(os.environ.get("SAHTE_SURE", "1.5")))
            if mode == "reddeder":
                write_frame(outs, {"ok": False, "error": "X: bozuk",
                                   "id": rid})
                continue
            write_frame(outs, {"ok": True, "id": rid},
                        b"RIFF" + h["text"].encode())
""")


@pytest.fixture
def fake_worker(tmp_path, monkeypatch):
    script = tmp_path / "sahte_isci.py"
    script.write_text(FAKE_WORKER % {"gaze": GAZE}, encoding="utf-8")
    monkeypatch.setattr(ses_isci, "_workers", {})
    made = []

    def make(mode="ok", once=True):
        monkeypatch.setenv("SAHTE_MOD", mode)
        if once:
            monkeypatch.setenv("SAHTE_IZ", str(tmp_path / ("iz-" + mode)))
        else:
            monkeypatch.delenv("SAHTE_IZ", raising=False)
        w = ses_isci.VoiceWorker([sys.executable, str(script)],
                                 {"model": "m", "espeak_dir": "e"})
        made.append(w)
        return w
    yield make
    for w in made:
        w.close()


def test_a_sentence_goes_through_the_warm_worker(fake_worker, capsys):
    w = fake_worker()
    w.start()                                    # spawned + load sent now
    assert w.pid() is not None
    assert w.wait_ready(20)
    assert w.synth("Su istiyorum") == b"RIFFSu istiyorum"
    pid = w.pid()
    assert w.synth("ikinci") == b"RIFFikinci"
    assert w.pid() == pid and w.starts == 1      # kept warm, not restarted
    out = capsys.readouterr().out
    assert "ses sureci hazir" in out and out.isascii()


def test_a_worker_that_dies_raises_at_once_says_so_once_and_restarts(
        fake_worker, capsys):
    w = fake_worker("olur")
    assert w.wait_ready(20)
    t0 = time.monotonic()
    with pytest.raises(ses_isci.WorkerError) as e:
        w.synth("ilk")
    assert time.monotonic() - t0 < 3             # end of file, no timeout
    assert "kapandi" in str(e.value) and "None" not in str(e.value)
    out = capsys.readouterr().out
    assert out.count("[ses]") == 1 and "yedek" in out and out.isascii()
    assert w.starts == 2                         # restarted at once...
    assert w.wait_ready(20)
    assert w.synth("ikinci") == b"RIFFikinci"    # ...and warm again


def test_a_hung_worker_is_killed_after_the_timeout(fake_worker, monkeypatch):
    monkeypatch.setattr(ses_isci, "SYNTH_TIMEOUT_S", 0.5)
    monkeypatch.setattr(ses_isci, "SYNTH_TIMEOUT_PER_CHAR_S", 0.0)
    w = fake_worker("takilir")
    assert w.wait_ready(20)
    proc = w._life.proc
    t0 = time.monotonic()
    with pytest.raises(ses_isci.WorkerError) as e:
        w.synth("takilan")
    assert time.monotonic() - t0 < 5
    assert "yanit vermedi" in str(e.value)
    assert proc.poll() is not None               # killed, not left behind
    assert w.wait_ready(20)
    assert w.synth("sonra") == b"RIFFsonra"


def test_a_refused_sentence_raises_but_keeps_nothing_half_open(fake_worker):
    w = fake_worker("reddeder", once=False)
    assert w.wait_ready(20)
    pid = w.pid()
    with pytest.raises(ses_isci.WorkerError) as e:
        w.synth("x")
    assert "bozuk" in str(e.value)
    assert w.pid() == pid                        # alive and well: kept


def test_a_missing_worker_executable_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(ses_isci, "_workers", {})
    w = ses_isci.VoiceWorker([str(tmp_path / "yok.exe")], {"model": "m"})
    w.start()                                    # never raises
    with pytest.raises(ses_isci.WorkerError):
        w.synth("x")


def test_the_speaker_says_a_sentence_with_the_backup_when_the_worker_dies(
        fake_worker, monkeypatch):
    import eye_gaze.speech as speech
    import eye_gaze.tts_backends as tb
    w = fake_worker("olur")
    monkeypatch.setattr(tb, "_is_windows", lambda: True)

    def select(name="auto", *a, **k):
        if name in ("auto", "piper"):
            return w.synth, "piper/tr_TR-dfki-medium@1.25x"
        if name == "onecore":
            return (lambda t: b"TOLGA:" + t.encode()), "onecore/Tolga"
        return None, ""
    monkeypatch.setattr(speech, "select_backend", select)
    monkeypatch.setattr(speech, "_report", lambda m: None)
    assert w.wait_ready(20)
    s = speech.Speaker("tr")
    try:
        # The fallback says it - and says that it was the fallback, so the
        # phrase cache does not file it under piper's key (R46).
        assert s.synth_with_engine("Su istiyorum") == (
            b"TOLGA:Su istiyorum", "onecore/Tolga")
        assert w.wait_ready(20)
        assert s.synth_with_engine("Su istiyorum") == (
            b"RIFFSu istiyorum", "piper/tr_TR-dfki-medium@1.25x")
        assert s.engine.startswith("piper")
    finally:
        s.close()


def test_workers_of_the_same_voice_are_shared(monkeypatch):
    monkeypatch.setattr(ses_isci, "_workers", {})
    a = ses_isci.shared_worker(["x"], "m", "e", 1.25, 1.0)
    b = ses_isci.shared_worker(["x"], "m", "e", 1.25, 1.0)
    c = ses_isci.shared_worker(["x"], "m", "e", 1.0, 1.0)
    assert a is b and a is not c


# --- the worker never outlives the program ------------------------------------

def _gone(pid, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _running(pid):
            return True
        time.sleep(0.2)
    return False


def _running(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                             capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:        # a zombie of a dead parent's child counts as gone
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


PARENT = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, %(gaze)r)
    from eye_gaze import ses_isci
    w = ses_isci.VoiceWorker([sys.executable, %(script)r],
                             {"model": "m", "espeak_dir": "e"})
    w.start()
    w.wait_ready(20) if os.environ["SAHTE_MOD"] != "sagir" else None
    import time
    time.sleep(1.0)
    print(w.pid(), flush=True)
    os._exit(9)          # a crash: no atexit, no close()
""")


@pytest.mark.parametrize("mode", ["ok", pytest.param(
    "sagir", marks=pytest.mark.skipif(
        os.name != "nt", reason="the job object is Windows' guarantee"))])
def test_a_crashed_program_leaves_no_worker_behind(tmp_path, mode):
    # ok: the worker reads end of file on its stdin. sagir ("deaf", never
    # reads again): only the kill-on-close job object can end it.
    script = tmp_path / "sahte_isci.py"
    script.write_text(FAKE_WORKER % {"gaze": GAZE}, encoding="utf-8")
    parent = tmp_path / "ebeveyn.py"
    parent.write_text(PARENT % {"gaze": GAZE, "script": str(script)},
                      encoding="utf-8")
    env = dict(os.environ, SAHTE_MOD=mode)
    env.pop("SAHTE_IZ", None)
    r = subprocess.run([sys.executable, str(parent)], capture_output=True,
                       text=True, timeout=60, env=env)
    assert r.returncode == 9, r.stderr
    pid = int(r.stdout.split()[-1])
    assert _gone(pid), "voice worker %d outlived its program" % pid


def test_close_ends_the_worker(fake_worker):
    w = fake_worker()
    assert w.wait_ready(20)
    proc = w._life.proc
    w.close()
    assert proc.poll() is not None


# --- which piper the backend uses ----------------------------------------------

def _voice(root):
    root.mkdir(parents=True)
    (root / "tr_TR-dfki-medium.onnx").write_bytes(b"model")
    (root / "tr_TR-dfki-medium.onnx.json").write_text("{}")
    (root / "espeak-ng-data").mkdir()
    (root / "espeak-ng-data" / "phontab").write_bytes(b"x")
    return root


@pytest.fixture
def ascii_voice(tmp_path, monkeypatch):
    import tempfile
    base = tempfile.mkdtemp(prefix="gf-ses-")
    if not base.isascii():
        pytest.skip("no ASCII temp folder")
    d = _voice(__import__("pathlib").Path(base) / "v")
    monkeypatch.setenv("PIPER_VOICE_DIR", str(d))
    return d


def test_a_packaged_build_uses_the_worker_and_never_imports_piper(
        ascii_voice, monkeypatch):
    import eye_gaze.tts_backends as tb
    seen = {}

    def fake_piper_synth(command, model, espeak_dir, scale, volume):
        seen.update(command=command, model=model, espeak=espeak_dir,
                    scale=scale, volume=volume)
        return lambda text: b"RIFF"
    monkeypatch.setattr(tb, "_voice_worker_command", lambda: ["ses.exe"])
    monkeypatch.setattr(ses_isci, "piper_synth", fake_piper_synth)
    monkeypatch.setitem(sys.modules, "piper", None)   # import would fail
    fn, name = tb.make_backend("piper", "tr", speed=1.25, volume=0.9)
    assert name == "piper/tr_TR-dfki-medium@1.25x@vol0.9"   # cache key kept
    assert fn("x") == b"RIFF"
    assert seen["command"] == ["ses.exe"]
    assert seen["model"] == os.path.join(str(ascii_voice),
                                         "tr_TR-dfki-medium.onnx")
    assert seen["espeak"] == os.path.join(str(ascii_voice), "espeak-ng-data")
    assert (seen["scale"], seen["volume"]) == (1.25, 0.9)


def test_a_packaged_build_without_the_worker_falls_to_the_next_voice(
        ascii_voice, monkeypatch):
    import eye_gaze.tts_backends as tb
    monkeypatch.setattr(tb, "_voice_worker_command", lambda: [])
    monkeypatch.setitem(sys.modules, "piper", None)

    def make(name, *a, **k):
        if name == "piper":
            return tb.make_backend(name, *a, **k)
        return (lambda t: b"RIFF", name)
    monkeypatch.setattr(tb, "_is_windows", lambda: True)
    fn, name = tb.select_backend("auto", "tr", factory=make)
    assert name == "onecore"
    assert "ses sureci" in tb.last_errors()["piper"]


def test_a_source_install_keeps_piper_in_process(monkeypatch):
    import eye_gaze.tts_backends as tb
    from eye_gaze import paths
    monkeypatch.setattr(paths, "_frozen", lambda: False)
    assert tb._voice_worker_command() is None
    assert ses_isci.worker_command() is None


def test_the_worker_command_is_the_executable_next_to_the_program(
        tmp_path, monkeypatch):
    from eye_gaze import paths
    monkeypatch.setattr(paths, "_frozen", lambda: True)
    monkeypatch.setattr(paths, "app_dir", lambda: str(tmp_path))
    assert ses_isci.worker_command() is None             # not in this build
    (tmp_path / ses_isci.WORKER_NAME).write_bytes(b"")
    assert ses_isci.worker_command() == [
        str(tmp_path / ses_isci.WORKER_NAME)]


# --- the real worker script with real piper (when installed) -------------------

def test_the_real_worker_script_speaks_with_real_piper(monkeypatch):
    pytest.importorskip("piper")
    import eye_gaze.tts_backends as tb
    voice_dir = os.environ.get("PIPER_VOICE_DIR", tb.PIPER_VOICE_DIR)
    if not os.path.isfile(os.path.join(voice_dir, "espeak-ng-data",
                                       "phontab")) or not voice_dir.isascii():
        pytest.skip("piper sesleri kurulu degil")
    fname, model_dir = tb._find_piper_voice("tr", voice_dir)
    monkeypatch.setattr(ses_isci, "_workers", {})
    w = ses_isci.VoiceWorker(
        [sys.executable, WORKER_SCRIPT],
        {"model": os.path.join(model_dir, fname),
         "espeak_dir": os.path.join(voice_dir, "espeak-ng-data"),
         "length_scale": 1.25, "volume": 0.9})
    try:
        assert w.wait_ready(60)
        for _ in range(2):
            wav = w.synth("Su istiyorum")
            assert wav[:4] == b"RIFF" and len(wav) > 10000
        _h, _b = w.request({"op": "modules"}, 20)
        mods = set(_h["modules"])
        assert "piper" in mods
        for never in ("cv2", "ctranslate2", "faster_whisper", "mediapipe",
                      "gaze_driver", "eye_gaze.tts_backends", "_hashlib",
                      "_ssl"):
            assert never not in mods, never
    finally:
        w.close()


@pytest.mark.skipif(os.name == "nt", reason="Windows uses the job object")
def test_the_worker_exits_when_its_parent_is_gone():
    import gozfaresi_ses
    parents = iter([100, 100, 1])
    exited = []
    gozfaresi_ses._die_with_parent(poll_s=0.01,
                                   getppid=lambda: next(parents, 1),
                                   exit=exited.append)
    end = time.monotonic() + 5
    while not exited and time.monotonic() < end:
        time.sleep(0.02)
    assert exited == [0]


def test_the_worker_blocks_the_libraries_that_must_never_load_there():
    import gozfaresi_ses
    for name in ("cv2", "ctranslate2", "faster_whisper", "_ssl", "_hashlib",
                 "ssl", "gaze_driver"):
        assert name in gozfaresi_ses.NEVER, name


# --- R46: nobody waits out a hung worker -------------------------------------

def _speaker_on(worker, monkeypatch):
    import eye_gaze.speech as speech
    import eye_gaze.tts_backends as tb
    monkeypatch.setattr(tb, "_is_windows", lambda: True)
    def fn(text):
        return worker.synth(text)
    fn.cancel, fn.wait_ready, fn.close = (worker.cancel, worker.wait_ready,
                                          worker.close)

    def select(name="auto", *a, **k):
        if name in ("auto", "piper"):
            return fn, "piper/tr_TR-dfki-medium@1.25x"
        if name == "onecore":
            return (lambda t: b"TOLGA:" + t.encode()), "onecore/Tolga"
        return None, ""
    monkeypatch.setattr(speech, "select_backend", select)
    said = []
    monkeypatch.setattr(speech, "_report", said.append)
    return speech.Speaker("tr"), said


def test_a_second_caller_gets_the_fallback_at_once_while_the_worker_hangs(
        fake_worker, monkeypatch):
    # The reviewer's probe, scaled down: A's sentence hangs, B (F4) comes
    # 0.2 s later. B must not wait out A's timeout plus a cold restart.
    import threading
    monkeypatch.setattr(ses_isci, "SYNTH_TIMEOUT_S", 3.0)
    w = fake_worker("takilir")
    assert w.wait_ready(20)
    s, _said = _speaker_on(w, monkeypatch)
    try:
        a = threading.Thread(target=s.synth, args=("asili cumle",))
        a.start()
        time.sleep(0.2)
        t0 = time.monotonic()
        wav, by = s.synth_with_engine("F4 cumlesi")
        took = time.monotonic() - t0
        assert (wav, by) == (b"TOLGA:F4 cumlesi", "onecore/Tolga")
        assert took < ses_isci.LOCK_WAIT_S + 0.8, took
        a.join(10)
    finally:
        s.close()


def test_a_hung_sentence_is_dropped_after_a_few_seconds_not_twenty():
    assert ses_isci.SYNTH_TIMEOUT_S + 30 * ses_isci.SYNTH_TIMEOUT_PER_CHAR_S \
        <= 3.0
    assert ses_isci.LOCK_WAIT_S <= 1.0 and ses_isci.READY_WAIT_S <= 2.0


def test_stop_cuts_the_wait_on_a_hung_worker(fake_worker, monkeypatch):
    import threading
    w = fake_worker("takilir")
    assert w.wait_ready(20)
    got = {}

    def ask():
        t0 = time.monotonic()
        try:
            w.synth("asili")
        except Exception as exc:
            got["exc"] = exc
        got["took"] = time.monotonic() - t0
    th = threading.Thread(target=ask)
    th.start()
    time.sleep(0.3)
    w.cancel()
    th.join(5)
    assert isinstance(got["exc"], ses_isci.WorkerCancelled)
    assert got["took"] < 1.0
    assert w.pid() is not None                   # not a fault: not killed


def test_a_stopped_sentence_neither_falls_back_nor_complains(
        fake_worker, monkeypatch):
    w = fake_worker("takilir")
    assert w.wait_ready(20)
    s, said = _speaker_on(w, monkeypatch)
    played = []
    import eye_gaze.speech as speech
    monkeypatch.setattr(speech, "_play", played.append)
    try:
        s.speak("asili cumle")
        time.sleep(0.4)
        t0 = time.monotonic()
        s.stop()
        s.speak("sonraki")          # worker still busy: fallback, at once
        end = time.monotonic() + 8
        while not played and time.monotonic() < end:
            time.sleep(0.05)
        assert played == [b"TOLGA:sonraki"]
        assert time.monotonic() - t0 < 4
        assert not any("asili" in m for m in said)
    finally:
        s.close()


def test_a_cold_or_hung_load_gives_the_alarm_phrase_in_about_two_seconds(
        fake_worker, monkeypatch):
    w = fake_worker("yukte_takilir", once=False)
    w.start()
    s, _said = _speaker_on(w, monkeypatch)
    try:
        t0 = time.monotonic()
        wav, by = s.synth_with_engine("Yardim edin!")
        assert time.monotonic() - t0 < ses_isci.READY_WAIT_S + 1.0
        assert (wav, by) == (b"TOLGA:Yardim edin!", "onecore/Tolga")
    finally:
        s.close()


def test_the_alarm_takes_the_fallback_phrase_then_the_users_voice():
    from eye_gaze import alarm as al

    class Player:
        voices = []

        def set_voice(self, wav):
            Player.voices.append(wav)

    class Core:
        player = Player()

        def close(self):
            pass

    class Spk:
        ok = True
        renders_audio = True
        engine = "piper/tr_TR-dfki-medium@1.25x"
        calls = 0

        def synth_with_engine(self, text):
            Spk.calls += 1
            return ((b"TOLGA", "onecore/Tolga") if Spk.calls == 1
                    else (b"PIPER", Spk.engine))

        def wait_ready(self, timeout):
            return True

    class RunNow:
        def __init__(self, target, args=(), daemon=None):
            self.t, self.a = target, args

        def start(self):
            self.t(*self.a)
    w = al.AlarmWatcher(Core(), log=lambda s: None, thread=RunNow)
    w.attach_speaker(Spk())
    assert Player.voices == [b"TOLGA", b"PIPER"]


def test_a_fallback_rendering_is_never_cached_under_the_main_voice(tmp_path):
    from eye_gaze.phrase_cache import PhraseCache

    class Spk:
        engine = voice = "piper/tr_TR-dfki-medium@1.25x"
        language = "tr"
        pending = 0
        speaker_wav = ""

        def wait_ready(self, timeout):
            return True

        def synth_with_engine(self, text):
            if text == "bozuk":
                return b"TOLGA", "onecore/Tolga"   # the worker failed once
            return b"PIPER", self.engine
    c = PhraseCache(Spk(), str(tmp_path), defer_seconds=0)
    c.warm(["bozuk", "iyi"])
    c.wait(20)
    assert not os.path.exists(c._path("bozuk"))
    with open(c._path("iyi"), "rb") as f:
        assert f.read() == b"PIPER"


def test_a_stop_does_not_cut_another_caller_s_request(fake_worker):
    import threading
    w = fake_worker("yavas", once=False)
    assert w.wait_ready(20)
    got = {}

    def warmer():
        got["wav"] = w.synth("hazirlanan")
    th = threading.Thread(target=warmer)
    th.start()
    time.sleep(0.3)
    w.cancel(threading.current_thread())     # the Speaker thread's stop
    th.join(10)
    assert got["wav"] == b"RIFFhazirlanan"


def test_a_worker_found_dead_between_sentences_falls_back_at_once(
        fake_worker, capsys):
    w = fake_worker()
    assert w.wait_ready(20)
    w._life.proc.kill()
    w._life.proc.wait(5)
    time.sleep(0.3)                              # reader saw end of file
    t0 = time.monotonic()
    with pytest.raises(ses_isci.WorkerError) as e:
        w.synth("sonraki")
    assert time.monotonic() - t0 < 0.5           # no cold-load wait
    assert "kapandi" in str(e.value)
    assert "[ses]" in capsys.readouterr().out    # said
    assert w.wait_ready(20)                      # restarted in background
    assert w.synth("sonraki") == b"RIFFsonraki"


# --- R46 fix round 2 ---------------------------------------------------------

def _chain_speaker(monkeypatch, main_fn, backup=None, backup_name="espeak/tr"):
    """A real Speaker on the Linux-like chain: main_fn as the packaged piper,
    `backup` (None, an audio voice or a speaks-itself voice) after it."""
    import eye_gaze.speech as speech
    import eye_gaze.tts_backends as tb
    monkeypatch.setattr(tb, "_is_windows", lambda: False)

    def select(name="auto", *a, **k):
        if name in ("auto", "piper"):
            return main_fn, "piper/tr_TR-dfki-medium@1.25x"
        if backup is not None and name in ("espeak", "spd"):
            return backup, backup_name
        return None, ""
    monkeypatch.setattr(speech, "select_backend", select)
    said = []
    monkeypatch.setattr(speech, "_report", said.append)
    return speech.Speaker("tr"), said


class _SlowLoad:
    """A packaged piper whose voice is not ready until `ready` is set."""

    def __init__(self, transient=ses_isci._NotReady):
        import threading
        self.ready = threading.Event()
        self.calls = 0
        self.transient = transient

    def __call__(self, text):
        self.calls += 1
        if not self.ready.is_set():
            raise self.transient("ses sureci hazir degil")
        return b"PIPER:" + text.encode()

    def wait_ready(self, timeout):
        return self.ready.wait(timeout)


@pytest.mark.parametrize("transient", [ses_isci._NotReady, ses_isci._Busy])
def test_a_slow_or_busy_worker_never_costs_the_session_its_piper_voice(
        monkeypatch, transient):
    # N-1: four fallbacks while the voice loads (or the worker is busy)
    # are not voice failures; piper speaks as soon as it is ready.
    piper = _SlowLoad(transient)
    s, said = _chain_speaker(monkeypatch, piper,
                             lambda t: b"ESPEAK:" + t.encode())
    try:
        for i in range(4):
            assert s.synth_with_engine("cumle %d" % i) == (
                b"ESPEAK:cumle %d" % i, "espeak/tr")
        assert s.engine.startswith("piper")
        assert not any("bu oturumda" in m for m in said)
        piper.ready.set()
        assert s.synth_with_engine("simdi") == (
            b"PIPER:simdi", "piper/tr_TR-dfki-medium@1.25x")
    finally:
        s.close()


def test_real_faults_still_switch_the_session(monkeypatch):
    def broken(text):
        raise ses_isci.WorkerError("ses sureci kapandi (cikis kodu 3)")
    s, said = _chain_speaker(monkeypatch, broken, lambda t: b"E")
    try:
        for _ in range(s.SWITCH_AFTER):
            s.synth("x")
        assert s.engine == "espeak/tr"
    finally:
        s.close()


def test_the_worker_close_after_a_switch_does_not_hold_the_sentence(
        monkeypatch):
    # N-3: closing waits for the worker; the sentence that caused the
    # switch must not wait with it.
    def broken(text):
        raise ses_isci.WorkerError("ses sureci yanit vermedi")
    broken.close = lambda: time.sleep(3)
    s, _said = _chain_speaker(monkeypatch, broken, lambda t: b"E")
    try:
        for _ in range(s.SWITCH_AFTER - 1):
            s.synth("x")
        t0 = time.monotonic()
        assert s.synth("ucuncu") == b"E"
        assert time.monotonic() - t0 < 0.5
        assert s.engine == "espeak/tr"
    finally:
        s.close()


class _Player:
    def __init__(self):
        self.voices = []
        self.has_voice = False

    def set_voice(self, wav):
        self.voices.append(wav)
        self.has_voice = wav is not None

    def start(self, now):
        pass

    def tick(self, now):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def _watcher(monkeypatch):
    from eye_gaze import alarm as al
    monkeypatch.setattr(al.AlarmWatcher, "UPGRADE_WAIT_S", 0.2)
    monkeypatch.setattr(al.AlarmWatcher, "UPGRADE_BACKOFF_S", (0.1, 0.2))
    player = _Player()
    core = al.AlarmCore(player, [], log=lambda m: None)
    lines = []
    import threading
    w = al.AlarmWatcher(core, log=lines.append, thread=threading.Thread)
    return w, core, player, lines


def _spd_like():
    spoken = []

    def spd(text):
        spoken.append(text)
        return b""
    spd.speaks_itself = True
    spd.stop = lambda: spoken.append("<stop>")
    return spd, spoken


@pytest.mark.parametrize("backup", ["spd", "none"])
def test_the_alarm_phrase_is_rendered_eventually_and_the_alarm_speaks(
        monkeypatch, backup):
    # N-2/N-5: the worker is not ready at startup and the fallback cannot
    # return audio (spd-say only) or there is none. The alarm says the
    # phrase live meanwhile where it can, and the phrase gets rendered in
    # the user's voice once the worker is ready - never given up.
    piper = _SlowLoad()
    spd, spoken = _spd_like()
    s, _said = _chain_speaker(monkeypatch, piper,
                              spd if backup == "spd" else None, "spd/tr")
    w, core, player, lines = _watcher(monkeypatch)
    try:
        w.attach_speaker(s)
        time.sleep(1.0)                      # several failed tries
        assert player.voices == []
        assert any("tekrar denenecek" in m for m in lines)
        core._start(0.0, "f12", 2.0)         # the alarm fires now
        if backup == "spd":
            end = time.monotonic() + 5
            while not any("Yard" in str(x) for x in spoken) \
                    and time.monotonic() < end:
                time.sleep(0.05)
            assert any("Yard" in str(x) for x in spoken)   # said live
        core.stop("test")
        piper.ready.set()                    # the worker finally loads
        end = time.monotonic() + 5
        while not player.voices and time.monotonic() < end:
            time.sleep(0.05)
        assert player.voices and player.voices[-1].startswith(b"PIPER:Yard")
        assert core.live_phrase is None
        assert any("artik hazir" in m for m in lines)
    finally:
        w.closed = True
        s.close()


def test_a_stopped_long_sentence_does_not_make_the_next_one_look_hung(
        fake_worker, monkeypatch):
    # N-4: the worker is still rendering the stopped sentence; the next
    # one is not timed against it (no false hang, no kill), it takes the
    # fallback at once, and the late answer is never paired with it.
    import threading
    monkeypatch.setenv("SAHTE_SURE", "1.5")
    w = fake_worker("yavas", once=False)
    assert w.wait_ready(20)
    pid = w.pid()
    got = {}

    def long_one():
        try:
            w.synth("uzun cumle")
        except Exception as exc:
            got["a"] = exc
    th = threading.Thread(target=long_one)
    th.start()
    time.sleep(0.3)
    w.cancel(th)
    th.join(5)
    assert isinstance(got["a"], ses_isci.WorkerCancelled)
    t0 = time.monotonic()
    with pytest.raises(ses_isci.WorkerError) as e:
        w.synth("hemen")
    assert getattr(e.value, "transient", False)
    assert time.monotonic() - t0 < 0.5
    time.sleep(1.6)                          # the stopped one finishes
    assert w.synth("sonra") == b"RIFFsonra"  # its own answer, not "uzun"
    assert w.pid() == pid                    # never taken for a hang


def test_a_worker_dying_while_loading_is_retried_with_a_back_off(
        fake_worker, monkeypatch):
    monkeypatch.setattr(ses_isci, "RESPAWN_BACKOFF_S", (0.0, 1.0))
    w = fake_worker("yukte_olur", once=False)    # dies at every load
    t0 = time.monotonic()
    assert w.wait_ready(2.5) is False
    # 0 s, then 1 s, then 1 s ...: three or four spawns, not dozens
    assert 2 <= w.starts <= 4, w.starts
    with pytest.raises(ses_isci.WorkerError):
        w.synth("x")
    assert time.monotonic() - t0 < 5


def test_wait_ready_follows_a_restart_after_a_death_while_loading(
        fake_worker, monkeypatch):
    monkeypatch.setattr(ses_isci, "RESPAWN_BACKOFF_S", (0.0, 0.5))
    w = fake_worker("yukte_olur")                # only the first life dies
    assert w.wait_ready(20) is True
    assert w.starts == 2
    assert w.synth("x") == b"RIFFx"


def test_a_closed_worker_is_never_started_again(fake_worker):
    w = fake_worker()
    assert w.wait_ready(20)
    w.close()
    assert w.wait_ready(1) is False
    with pytest.raises(ses_isci.WorkerError):
        w.synth("x")
    assert w.pid() is None


def test_the_warmer_stops_when_the_voice_is_gone(tmp_path):
    # Seen in the packaged smoke: at shutdown the worker is closed and the
    # warmer went on printing a failure per phrase while Python finalised
    # (fatal error, exit 127).
    from eye_gaze.phrase_cache import PhraseCache

    class Spk:
        engine = voice = "piper/tr_TR-dfki-medium@1.25x"
        language = "tr"
        pending = 0
        speaker_wav = ""
        calls = 0

        def wait_ready(self, timeout):
            return False                       # closed / never back

        def synth_with_engine(self, text):
            Spk.calls += 1
            return None, None
    c = PhraseCache(Spk(), str(tmp_path), defer_seconds=0)
    c.warm(["a", "b", "c"])
    c.wait(20)
    assert Spk.calls == 0
