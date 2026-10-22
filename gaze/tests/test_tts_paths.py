# gaze/tests/test_tts_paths.py
"""Where the piper voice comes from, the ASCII copy for a frozen Windows
build, and the Linux fallback voices (espeak-ng, spd-say).

Order of the voice folder: PIPER_VOICE_DIR, then the legacy source-mode
C:\\Users\\Public\\piper-voices, then the voice shipped in the package
(resources/piper). espeak-ng's C code on Windows cannot open a non-ASCII
folder (it aborts the process), so a frozen build installed under
C:\\Users\\Örnek copies the voice once into an ASCII folder. On Linux the
same folder works as it is (measured in Docker, see the Task 11 report).
"""
import io
import os
import pathlib
import shutil
import struct
import sys
import tempfile
import time
import wave

import pytest

import eye_gaze.tts_backends as tb
from eye_gaze import paths, piper_copy

VOICE = "tr_TR-dfki-medium"


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv(paths.ENV_DATA_DIR, raising=False)
    monkeypatch.delenv("PIPER_VOICE_DIR", raising=False)
    monkeypatch.delenv("TTS_HOME", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(paths, "_fallbacks", {})
    return monkeypatch


def _ascii_base():
    base = tempfile.gettempdir()
    if not base.isascii() and os.name == "nt":
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        f = ctypes.windll.kernel32.GetShortPathNameW
        f.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        f.restype = ctypes.c_uint
        if f(base, buf, 1024):
            base = buf.value
    return base


@pytest.fixture
def ascii_dir():
    base = _ascii_base()
    if not base.isascii():
        pytest.skip("ASCII gecici klasor bulunamadi")
    d = tempfile.mkdtemp(prefix="tts-paths-", dir=base)
    try:
        yield pathlib.Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _voice(root, voice=VOICE, onnx=b"model", phontab=b"phontab",
           phondata=b"phondata"):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{voice}.onnx").write_bytes(onnx)
    (root / f"{voice}.onnx.json").write_text("{}", encoding="utf-8")
    d = root / "espeak-ng-data"
    (d / "voices" / "trk").mkdir(parents=True, exist_ok=True)
    (d / "tr_dict").write_bytes(b"dict")
    (d / "voices" / "trk" / "tr").write_bytes(b"voice")
    (d / "phondata").write_bytes(phondata)
    if phontab is not None:
        (d / "phontab").write_bytes(phontab)
    return root


def _frozen(monkeypatch, meipass, windows):
    """A frozen build whose bundle (sys._MEIPASS) is `meipass`."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable", str(meipass / "GozFaresi.exe"))
    monkeypatch.setattr(tb, "_is_windows", lambda: windows)
    monkeypatch.setattr(paths, "_is_windows", lambda: windows)


@pytest.fixture
def frozen_windows_non_ascii(clean_env, tmp_path, ascii_dir):
    """Installed under a non-ASCII profile; paths.ascii_dir -> ascii_dir."""
    bundle = tmp_path / "\u00d6rnek" / "_internal"
    src = _voice(bundle / "resources" / "piper")
    _frozen(clean_env, bundle, windows=True)
    made = []
    root = ascii_dir / "GozFaresi" / "piper"

    def fake_ascii_dir(sub):
        made.append(sub)
        p = ascii_dir / "GozFaresi" / sub
        p.mkdir(parents=True, exist_ok=True)
        return str(p)
    clean_env.setattr(paths, "ascii_dir", fake_ascii_dir)
    copies = []
    real = piper_copy._copy_file

    def counting(s, d):
        name = os.path.basename(d)
        copies.append(name[:-4] if name.endswith(".tmp") else name)
        real(s, d)
    clean_env.setattr(piper_copy, "_copy_file", counting)
    return types_ns(bundle=bundle, src=src, root=root, made=made,
                    copies=copies,
                    stamp=lambda: piper_copy._source(str(src))[2])


class types_ns:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# --- the voice folder -------------------------------------------------------


def test_env_var_wins(clean_env, ascii_dir):
    clean_env.setenv("PIPER_VOICE_DIR", str(ascii_dir))
    assert tb.piper_voice_dir() == str(ascii_dir)


def test_source_mode_on_windows_keeps_the_public_voice_dir(clean_env):
    # The development machine: KUR-SES.bat filled C:\Users\Public\piper-voices.
    clean_env.setattr(tb, "_is_windows", lambda: True)
    clean_env.setattr(paths, "_is_windows", lambda: True)
    clean_env.setattr(paths, "ascii_dir",
                      lambda sub: pytest.fail("no ASCII copy in source mode"))
    assert tb.piper_voice_dir() == r"C:\Users\Public\piper-voices"


@pytest.mark.skipif(os.name != "nt", reason="Windows import-time constant")
def test_source_mode_constants_are_unchanged():
    assert tb.PIPER_VOICE_DIR == r"C:\Users\Public\piper-voices"
    assert tb.XTTS_MODEL_DIR == r"C:\Users\Public\xtts-models"


def test_frozen_ascii_bundle_is_used_as_it_is(clean_env, ascii_dir):
    bundle = ascii_dir / "GozFaresi" / "_internal"
    _voice(bundle / "resources" / "piper")
    _frozen(clean_env, bundle, windows=True)
    clean_env.setattr(paths, "ascii_dir",
                      lambda sub: pytest.fail("ASCII bundle needs no copy"))
    assert tb.piper_voice_dir() == str(bundle / "resources" / "piper")


def test_piper_dir_frozen_copies_to_ascii_once(frozen_windows_non_ascii):
    f = frozen_windows_non_ascii
    got = tb.piper_voice_dir()
    dst = f.root / f.stamp()
    assert got == str(dst) and got.isascii()
    assert f.made == ["piper"]
    assert (dst / f"{VOICE}.onnx").read_bytes() == b"model"
    assert (dst / f"{VOICE}.onnx.json").is_file()
    assert (dst / "espeak-ng-data" / "phontab").read_bytes() == b"phontab"
    assert (dst / "espeak-ng-data" / "phondata").read_bytes() == b"phondata"
    assert (dst / "espeak-ng-data" / "voices" / "trk" / "tr").is_file()
    # phontab is written last: it is what every check takes as "complete".
    assert f.copies[-1] == "phontab"
    assert not any(n.endswith(".tmp") for n in os.listdir(dst))
    n = len(f.copies)
    assert tb.piper_voice_dir() == str(dst)
    assert len(f.copies) == n            # once: nothing copied again


def test_piper_copy_cut_short_has_no_phontab_and_is_redone(
        frozen_windows_non_ascii, monkeypatch):
    f = frozen_windows_non_ascii
    real = piper_copy._copy_file
    done = []

    def dies_midway(s, d):
        if len(done) == 4:
            raise OSError(28, "No space left on device")
        done.append(d)
        real(s, d)
    monkeypatch.setattr(piper_copy, "_copy_file", dies_midway)
    with pytest.raises(RuntimeError) as e:
        tb.piper_voice_dir()
    assert str(e.value).isascii()
    dst = f.root / f.stamp()
    # No folder that could pass for a complete voice was left behind.
    assert not (dst / "espeak-ng-data").exists()
    monkeypatch.setattr(piper_copy, "_copy_file", real)
    # Resumed in the same folder (it is recognisably our own leftover).
    assert tb.piper_voice_dir() == str(dst)
    assert (dst / "espeak-ng-data" / "phontab").read_bytes() == b"phontab"
    assert (dst / "espeak-ng-data" / "tr_dict").read_bytes() == b"dict"
    assert not any(n.endswith(".tmp") for n in os.listdir(dst))


def test_an_update_with_new_espeak_data_gets_a_new_folder(
        frozen_windows_non_ascii):
    # The old copy must not be reused: espeak faults in stale data kill the
    # process. Nor deleted: it is somebody's working voice folder.
    f = frozen_windows_non_ascii
    old = tb.piper_voice_dir()
    (f.src / "espeak-ng-data" / "phondata").write_bytes(b"phondata v2!")
    new = tb.piper_voice_dir()
    assert new != old and os.path.dirname(new) == str(f.root)
    assert (pathlib.Path(new) / "espeak-ng-data" / "phondata").read_bytes() \
        == b"phondata v2!"
    assert (pathlib.Path(old) / "espeak-ng-data" / "phondata").read_bytes() \
        == b"phondata"


def test_a_planted_folder_with_a_wrong_phontab_is_not_used(
        frozen_windows_non_ascii):
    # C:\Users\Public is writable by every local user: a complete-looking
    # folder under the expected name is checked, not trusted.
    f = frozen_windows_non_ascii
    planted = f.root / f.stamp()
    _voice(planted, phontab=b"PHONTAB")          # same size, other content
    got = tb.piper_voice_dir()
    assert got == str(f.root / (f.stamp() + "-1"))
    assert (pathlib.Path(got) / "espeak-ng-data" / "phontab").read_bytes() \
        == b"phontab"
    # The planted folder itself is left exactly as it was.
    assert (planted / "espeak-ng-data" / "phontab").read_bytes() == b"PHONTAB"


def test_a_damaged_copy_is_passed_over_never_rewritten(
        frozen_windows_non_ascii):
    f = frozen_windows_non_ascii
    first = pathlib.Path(tb.piper_voice_dir())
    (first / "espeak-ng-data" / "tr_dict").write_bytes(b"dic")   # size differs
    got = pathlib.Path(tb.piper_voice_dir())
    assert got != first
    assert (first / "espeak-ng-data" / "tr_dict").read_bytes() == b"dic"
    assert (got / "espeak-ng-data" / "tr_dict").read_bytes() == b"dict"


def test_piper_copy_with_no_packaged_voice_says_so(clean_env, tmp_path,
                                                   ascii_dir):
    bundle = tmp_path / "\u00d6rnek" / "_internal"
    bundle.mkdir(parents=True)
    _frozen(clean_env, bundle, windows=True)
    made = []
    clean_env.setattr(paths, "ascii_dir",
                      lambda sub: made.append(sub) or str(ascii_dir / sub))
    with pytest.raises(RuntimeError) as e:
        tb.piper_voice_dir()
    assert "piper" in str(e.value) and str(e.value).isascii()
    # Checked before the ASCII folder is made: no empty folder left behind.
    assert made == []
    assert not (ascii_dir / "piper").exists()


def test_a_busy_copy_lock_falls_through_within_seconds(
        frozen_windows_non_ascii, monkeypatch):
    # The voice is built on the driver's start-up path: another process
    # copying must not hold the cursor up. Bounded, then the next voice.
    pytest.importorskip("piper")
    from eye_gaze import downloader
    f = frozen_windows_non_ascii
    assert piper_copy.COPY_LOCK_WAIT <= 3.0
    monkeypatch.setattr(piper_copy, "COPY_LOCK_WAIT", 0.3)
    f.root.mkdir(parents=True, exist_ok=True)
    other = downloader._ProcessLock(str(f.root / ".kopya.lock"))
    assert other.try_acquire()
    try:
        t0 = time.monotonic()
        with pytest.raises(RuntimeError) as e:
            tb.piper_voice_dir()
        assert time.monotonic() - t0 < 2.0
        assert "baska bir islemde" in str(e.value)
        tried = []

        def make(name, *a, **k):
            tried.append(name)
            if name == "piper":
                return tb.make_backend(name, *a, **k)
            return (lambda text: b"RIFF", name)
        fn, name = tb.select_backend("auto", "tr", factory=make)
        assert name == "onecore" and tried == ["piper", "onecore"]
        assert "baska bir islemde" in tb.last_errors()["piper"]
    finally:
        other.release()


def test_linux_uses_a_non_ascii_bundle_directly(clean_env, tmp_path):
    # Ruling R19 for piper: espeak on Linux takes the UTF-8 bytes of the
    # path - measured in Docker, bit-identical audio from /tmp/calisma.
    bundle = tmp_path / "\u00e7a\u011fr\u0131" / "gozfaresi"
    _voice(bundle / "resources" / "piper")
    _frozen(clean_env, bundle, windows=False)
    clean_env.setattr(paths, "ascii_dir",
                      lambda sub: pytest.fail("no ASCII detour on Linux"))
    assert tb.piper_voice_dir() == str(bundle / "resources" / "piper")


def test_linux_piper_backend_accepts_a_non_ascii_dir(clean_env, tmp_path):
    pytest.importorskip("piper")
    d = _voice(tmp_path / "sesler-\u00fc\u00e7")
    clean_env.setenv("PIPER_VOICE_DIR", str(d))
    clean_env.setattr(tb, "_is_windows", lambda: False)
    fn, name = tb.make_backend("piper", "tr")
    assert name == "piper/" + VOICE


def test_windows_piper_backend_still_refuses_a_non_ascii_env_dir(clean_env,
                                                                 tmp_path):
    pytest.importorskip("piper")
    d = _voice(tmp_path / "sesler-\u00fc\u00e7")
    clean_env.setenv("PIPER_VOICE_DIR", str(d))
    clean_env.setattr(tb, "_is_windows", lambda: True)
    with pytest.raises(RuntimeError) as e:
        tb.make_backend("piper", "tr")
    assert "ASCII" in str(e.value) and str(e.value).isascii()


# --- xtts model folder (source only, never packaged) --------------------------


def test_xtts_dir_source_windows_is_the_public_folder(clean_env):
    clean_env.setattr(tb, "_is_windows", lambda: True)
    clean_env.setattr(paths, "_is_windows", lambda: True)
    clean_env.setattr(paths, "ascii_dir", lambda sub: pytest.fail("no copy"))
    assert tb.xtts_model_dir() == r"C:\Users\Public\xtts-models"


def test_xtts_dir_on_windows_otherwise_goes_through_ascii_dir(clean_env,
                                                              tmp_path):
    clean_env.setenv(paths.ENV_DATA_DIR, str(tmp_path / "veri"))
    clean_env.setattr(tb, "_is_windows", lambda: True)
    clean_env.setattr(paths, "_is_windows", lambda: True)
    clean_env.setattr(paths, "ascii_dir", lambda sub: "C:\\ascii\\" + sub)
    assert tb.xtts_model_dir() == "C:\\ascii\\xtts-models"


def test_xtts_dir_on_linux_is_the_model_dir(clean_env, tmp_path):
    clean_env.setenv(paths.ENV_DATA_DIR, str(tmp_path / "\u00e7a\u011fr\u0131"))
    clean_env.setattr(tb, "_is_windows", lambda: False)
    clean_env.setattr(paths, "_is_windows", lambda: False)
    clean_env.setattr(paths, "ascii_dir", lambda sub: pytest.fail("no copy"))
    assert tb.xtts_model_dir() == os.path.join(paths.model_dir(),
                                               "xtts-models")


# --- Linux voice order ---------------------------------------------------------


def _factory(available, tried):
    def make(name, voice_hint="tr", speaker_wav="", speed=1.0, volume=1.0):
        tried.append(name)
        if name not in available:
            raise RuntimeError(f"{name} yok")
        return (lambda text: b"RIFF", name)
    return make


def test_linux_auto_order(monkeypatch):
    monkeypatch.setattr(tb, "_is_windows", lambda: False)
    assert tb.auto_order() == ("piper", "espeak", "spd")
    tried = []
    fn, name = tb.select_backend("auto", "tr",
                                 factory=_factory({"spd"}, tried))
    assert tried == ["piper", "espeak", "spd"]
    assert name == "spd"
    assert set(tb.last_errors()) == {"piper", "espeak"}


def test_windows_auto_order_is_unchanged(monkeypatch):
    monkeypatch.setattr(tb, "_is_windows", lambda: True)
    assert tb.auto_order() == ("piper", "onecore", "sapi")
    tried = []
    tb.select_backend("auto", "tr", factory=_factory(set(), tried))
    assert tried == ["piper", "onecore", "sapi"]


def test_linux_voices_can_be_named():
    assert "espeak" in tb.KNOWN and "spd" in tb.KNOWN


# --- espeak-ng --------------------------------------------------------------


def _stream_wav(frames, rate=22050):
    """What `espeak-ng --stdout` writes: a WAV header whose sizes are the
    0x7ffff... placeholders of a stream, then the samples."""
    hdr = (b"RIFF" + struct.pack("<I", 0x7FFFF024) + b"WAVE"
           + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
           + b"data" + struct.pack("<I", 0x7FFFF000))
    return hdr + b"\x01\x00" * frames


class _Run:
    def __init__(self, stdout=b"", returncode=0, stderr=b""):
        self.calls = []
        self.result = (returncode, stdout, stderr)

    def __call__(self, cmd, data, timeout):
        self.calls.append((cmd, data, timeout))
        return self.result


def test_espeak_backend_reads_turkish_from_stdin(monkeypatch):
    monkeypatch.setattr(tb, "_which",
                        lambda n: "/usr/bin/" + n if n == "espeak-ng" else None)
    run = _Run(_stream_wav(1000))
    monkeypatch.setattr(tb, "_run_tool", run)
    fn, name = tb.make_backend("espeak", "tr")
    assert name == "espeak/tr"
    wav = fn("-5 derece, ağrım var.")
    cmd, data, timeout = run.calls[0]
    assert cmd[:4] == ["/usr/bin/espeak-ng", "-v", "tr", "--stdout"]
    assert "-b" in cmd and cmd[cmd.index("-b") + 1] == "1"     # UTF-8 input
    # The text goes in on stdin, never as an argument ("-5" is no option).
    assert data == "-5 derece, ağrım var.".encode("utf-8")
    assert not any("derece" in c for c in cmd)
    assert timeout and timeout > 0
    # The stream placeholders are replaced by the real sizes.
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnframes() == 1000
        assert w.getframerate() == 22050


def test_espeak_backend_speed_is_slower_words_per_minute(monkeypatch):
    monkeypatch.setattr(tb, "_which", lambda n: "/usr/bin/" + n)
    run = _Run(_stream_wav(10))
    monkeypatch.setattr(tb, "_run_tool", run)
    fn, name = tb.make_backend("espeak", "tr", speed=1.25)
    fn("deneme")
    cmd = run.calls[0][0]
    assert cmd[cmd.index("-s") + 1] == "140"         # 175 / 1.25
    assert name.endswith("@1.25x")                    # phrase-cache key


def test_espeak_backend_missing_is_named(monkeypatch):
    monkeypatch.setattr(tb, "_which", lambda n: None)
    with pytest.raises(RuntimeError) as e:
        tb.make_backend("espeak", "tr")
    assert "espeak-ng" in str(e.value)


def test_espeak_failure_raises_with_its_message(monkeypatch):
    monkeypatch.setattr(tb, "_which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(tb, "_run_tool", _Run(b"", 1, b"Error: voice"))
    fn, _ = tb.make_backend("espeak", "tr")
    with pytest.raises(RuntimeError) as e:
        fn("deneme")
    assert "voice" in str(e.value)


def test_espeak_non_language_hint_means_turkish(monkeypatch):
    monkeypatch.setattr(tb, "_which", lambda n: "/usr/bin/" + n)
    run = _Run(_stream_wav(10))
    monkeypatch.setattr(tb, "_run_tool", run)
    fn, name = tb.make_backend("espeak", VOICE)      # a piper file name
    fn("deneme")
    assert run.calls[0][0][2] == "tr"


# --- spd-say ----------------------------------------------------------------


def test_spd_backend_speaks_itself_and_waits(monkeypatch):
    monkeypatch.setattr(tb, "_which",
                        lambda n: "/usr/bin/" + n if n == "spd-say" else None)
    run = _Run()
    monkeypatch.setattr(tb, "_run_tool", run)
    fn, name = tb.make_backend("spd", "tr")
    assert name == "spd/tr"
    assert getattr(fn, "speaks_itself", False) is True
    wav = fn("-5 derece")
    cmd = run.calls[0][0]
    assert cmd[:4] == ["/usr/bin/spd-say", "-l", "tr", "-w"]
    assert cmd[-2:] == ["--", "-5 derece"]       # never read as an option
    with wave.open(io.BytesIO(wav)) as w:          # a valid (silent) WAV
        assert w.getnframes() > 0


def test_spd_backend_speed_maps_to_rate(monkeypatch):
    monkeypatch.setattr(tb, "_which", lambda n: "/usr/bin/" + n)
    run = _Run()
    monkeypatch.setattr(tb, "_run_tool", run)
    fn, name = tb.make_backend("spd", "tr", speed=1.25)
    fn("deneme")
    cmd = run.calls[0][0]
    assert cmd[cmd.index("-r") + 1] == "-20"
    assert name.endswith("@1.25x")


def test_spd_missing_and_failure_are_named(monkeypatch):
    monkeypatch.setattr(tb, "_which", lambda n: None)
    with pytest.raises(RuntimeError) as e:
        tb.make_backend("spd", "tr")
    assert "spd-say" in str(e.value)
    monkeypatch.setattr(tb, "_which", lambda n: "/usr/bin/" + n)
    monkeypatch.setattr(tb, "_run_tool",
                        _Run(b"", 1, b"Failed to connect to Speech Dispatcher"))
    fn, _ = tb.make_backend("spd", "tr")
    with pytest.raises(RuntimeError) as e:
        fn("deneme")
    assert "Speech Dispatcher" in str(e.value)


def test_a_speaking_backend_is_never_used_to_pre_render(monkeypatch):
    # The AAC phrase cache warms dozens of phrases through Speaker.synth():
    # through spd-say that would read every one of them aloud and store
    # silence for each.
    from eye_gaze import speech
    spoken = []

    def fn(text):
        spoken.append(text)
        return b"RIFF"
    fn.speaks_itself = True
    monkeypatch.setattr(speech, "select_backend",
                        lambda *a, **k: (fn, "spd/tr"))
    s = speech.Speaker("tr")
    try:
        assert s.synth("su istiyorum") is None
        assert spoken == []
    finally:
        s.close()


def test_pre_rendering_never_falls_back_to_a_speaking_backend(monkeypatch):
    from eye_gaze import speech
    spoken = []

    def broken(text):
        raise RuntimeError("espeak-ng cikti vermedi")

    def spd(text):
        spoken.append(text)
        return b"RIFF"
    spd.speaks_itself = True

    def select(engine, *a, **k):
        return (broken, "espeak/tr") if engine == "auto" else (spd, "spd/tr")
    monkeypatch.setattr(speech, "select_backend", select)
    monkeypatch.setattr(tb, "_is_windows", lambda: False)
    monkeypatch.setattr(speech, "_report", lambda m: None)
    s = speech.Speaker("tr")
    try:
        assert s.synth("su istiyorum") is None
        assert spoken == []
    finally:
        s.close()


def test_spd_stop_cancels_speech_dispatcher_without_waiting(monkeypatch):
    monkeypatch.setattr(tb, "_which", lambda n: "/usr/bin/" + n)
    started = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            started.append((cmd, kw))
    monkeypatch.setattr(tb.subprocess, "Popen", FakePopen)
    fn, _ = tb.make_backend("spd", "tr")
    fn.stop()
    assert started[0][0] == ["/usr/bin/spd-say", "-C"]
    assert started[0][1]["stdin"] is tb.subprocess.DEVNULL

    def broken(cmd, **kw):
        raise OSError("gone")
    monkeypatch.setattr(tb.subprocess, "Popen", broken)
    fn.stop()                               # never raises into the gaze loop


def test_install_hint_per_mode(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(tb, "_is_windows", lambda: True)
    assert tb.install_hint() == "KUR-SES.bat calistirin"
    monkeypatch.setattr(tb, "_is_windows", lambda: False)
    assert "KUR-SES" not in tb.install_hint()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "GozFaresi.exe"))
    for windows in (True, False):
        monkeypatch.setattr(tb, "_is_windows", lambda: windows)
        assert tb.install_hint().startswith("Ayarlar > Indirmeler")
        assert tb.install_hint().isascii()
