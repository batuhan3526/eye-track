# gaze/tests/test_paths.py
"""Where every file lives, in each of the three ways the program can run.

The source checkout on the development machine holds the user's calibration - it must keep
resolving to exactly today's folders, or a packaging change would silently
start a fresh (uncalibrated) data dir. Frozen builds must never write next to
_MEIPASS (wiped on exit) and must survive a non-ASCII profile ("Örnek").
"""
import os
import shutil
import stat
import subprocess
import sys
import uuid

import pytest

import gaze_driver as gd
from eye_gaze import deep, gaze, paths, tts_backends


GAZE_DIR = os.path.dirname(os.path.abspath(gd.__file__))


@pytest.fixture
def clean_env(monkeypatch):
    """No override, not frozen - each test opts into what it simulates."""
    monkeypatch.delenv(paths.ENV_DATA_DIR, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(paths, "_fallbacks", {})
    monkeypatch.setattr(paths, "_tmp_fallback", None)
    monkeypatch.setattr(paths, "_said_no_root", False, raising=False)
    return monkeypatch


def _freeze(monkeypatch, exe_dir, meipass=None):
    os.makedirs(exe_dir, exist_ok=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable",
                        os.path.join(str(exe_dir), "GozFaresi.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass or exe_dir),
                        raising=False)


def test_source_mode_uses_gaze_dir(clean_env):
    assert paths.mode() == "source"
    assert paths.app_dir() == GAZE_DIR
    assert paths.data_dir() == GAZE_DIR
    assert paths.model_dir() == os.path.join(GAZE_DIR, "models")
    assert paths.cache_dir() == os.path.join(GAZE_DIR, "cache")
    assert paths.resource_path("fonts", "a.ttf") == os.path.join(
        GAZE_DIR, "resources", "fonts", "a.ttf")
    # The driver's own constant (tests monkeypatch it) comes from paths.
    assert gd._BASE_DIR == GAZE_DIR


def test_frozen_with_config_next_to_exe_is_portable(clean_env, tmp_path):
    exe_dir = tmp_path / "zip"
    _freeze(clean_env, exe_dir, meipass=tmp_path / "_MEI123")
    (exe_dir / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    assert paths.mode() == "portable"
    assert paths.app_dir() == str(exe_dir)
    assert paths.data_dir() == str(exe_dir)
    assert paths.model_dir() == os.path.join(str(exe_dir), "models")
    assert paths.cache_dir() == os.path.join(str(exe_dir), "cache")
    assert paths.resource_path("aac", "x.xml") == os.path.join(
        str(tmp_path / "_MEI123"), "resources", "aac", "x.xml")


def test_frozen_with_marker_file_is_portable(clean_env, tmp_path):
    exe_dir = tmp_path / "usb"
    _freeze(clean_env, exe_dir)
    (exe_dir / "tasinabilir").write_text("", encoding="utf-8")
    assert paths.mode() == "portable"
    assert paths.data_dir() == str(exe_dir)


def test_frozen_without_config_is_installed_windows(clean_env, tmp_path):
    clean_env.setattr(paths, "_is_windows", lambda: True)
    _freeze(clean_env, tmp_path / "Programs" / "GozFaresi")
    clean_env.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert paths.mode() == "installed"
    want = os.path.join(str(tmp_path / "Local"), "GozFaresi")
    assert paths.data_dir() == want
    assert paths.model_dir() == os.path.join(want, "models")
    assert paths.cache_dir() == os.path.join(want, "cache")
    # Computing never creates (it runs at import); the writer creates.
    assert not os.path.exists(want)
    assert paths.ensure_dir(paths.data_dir()) == want
    assert os.path.isdir(want)          # first run: log/config can be written


def test_frozen_installed_linux_uses_xdg(clean_env, tmp_path):
    clean_env.setattr(paths, "_is_windows", lambda: False)
    _freeze(clean_env, tmp_path / "opt")
    clean_env.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    clean_env.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert paths.mode() == "installed"
    assert paths.data_dir() == os.path.join(str(tmp_path / "cfg"), "gozfaresi")
    assert paths.model_dir() == os.path.join(
        str(tmp_path / "share"), "gozfaresi", "models")
    # Spec 6.3: models AND cache in XDG_DATA, settings in XDG_CONFIG.
    assert paths.cache_dir() == os.path.join(
        str(tmp_path / "share"), "gozfaresi", "cache")


def test_frozen_installed_linux_xdg_defaults(clean_env, tmp_path):
    clean_env.setattr(paths, "_is_windows", lambda: False)
    _freeze(clean_env, tmp_path / "opt")
    clean_env.delenv("XDG_CONFIG_HOME", raising=False)
    clean_env.delenv("XDG_DATA_HOME", raising=False)
    clean_env.setattr(os.path, "expanduser",
                      lambda p: p.replace("~", str(tmp_path / "home"), 1))
    assert paths.data_dir() == os.path.join(
        str(tmp_path / "home"), ".config", "gozfaresi")
    assert paths.model_dir() == os.path.join(
        str(tmp_path / "home"), ".local", "share", "gozfaresi", "models")
    assert paths.cache_dir() == os.path.join(
        str(tmp_path / "home"), ".local", "share", "gozfaresi", "cache")


def test_env_override_wins(clean_env, tmp_path):
    # Even over a portable marker: smokes must never touch a real data dir.
    exe_dir = tmp_path / "zip"
    _freeze(clean_env, exe_dir)
    (exe_dir / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    clean_env.setenv(paths.ENV_DATA_DIR, str(tmp_path / "smoke"))
    assert paths.data_dir() == str(tmp_path / "smoke")
    assert paths.model_dir() == os.path.join(str(tmp_path / "smoke"), "models")
    assert paths.cache_dir() == os.path.join(str(tmp_path / "smoke"), "cache")
    assert not os.path.exists(str(tmp_path / "smoke"))


def test_env_override_wins_over_installed_linux_xdg(clean_env, tmp_path):
    clean_env.setattr(paths, "_is_windows", lambda: False)
    _freeze(clean_env, tmp_path / "opt")
    clean_env.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    clean_env.setenv(paths.ENV_DATA_DIR, str(tmp_path / "smoke"))
    assert paths.model_dir() == os.path.join(str(tmp_path / "smoke"), "models")
    assert paths.cache_dir() == os.path.join(str(tmp_path / "smoke"), "cache")


def test_env_override_wins_in_source_mode(clean_env, tmp_path):
    clean_env.setenv(paths.ENV_DATA_DIR, str(tmp_path / "smoke"))
    assert paths.mode() == "source"
    assert paths.data_dir() == str(tmp_path / "smoke")


# -- unwritable profile: never crash at import, fall back when writing -------

def test_import_and_data_dir_survive_unwritable_profile(tmp_path):
    """A fresh process that imports the whole driver, frozen-installed, with
    LOCALAPPDATA / XDG_* pointing at a FILE (so no folder can be made there).
    """
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    exe_dir = tmp_path / "Programs"
    exe_dir.mkdir()
    code = (
        "import os, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "sys.frozen = True\n"
        "sys.executable = os.path.join(sys.argv[2], 'GozFaresi.exe')\n"
        "import gaze_driver\n"
        "from eye_gaze import paths, gaze, deep, tts_backends\n"
        "print('MODE', paths.mode())\n"
        "print('DATA', ascii(paths.data_dir()))\n"
        "print('MODEL', ascii(paths.model_dir()))\n"
        "print('CACHE', ascii(paths.cache_dir()))\n"
    )
    env = dict(os.environ)
    env.pop(paths.ENV_DATA_DIR, None)
    env.update(LOCALAPPDATA=str(blocker), XDG_CONFIG_HOME=str(blocker),
               XDG_DATA_HOME=str(blocker), CUDA_VISIBLE_DEVICES="",
               PYTHONIOENCODING="ascii")
    r = subprocess.run([sys.executable, "-c", code, GAZE_DIR, str(exe_dir)],
                       env=env, capture_output=True, text=True,
                       encoding="ascii", errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr
    assert "MODE installed" in r.stdout, r.stdout
    assert "DATA " in r.stdout and "CACHE " in r.stdout, r.stdout


def test_ensure_dir_falls_back_once_when_unwritable(clean_env, tmp_path,
                                                    capsys):
    clean_env.setattr(paths, "_is_windows", lambda: True)
    _freeze(clean_env, tmp_path / "Programs")
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    clean_env.setenv("LOCALAPPDATA", str(blocker))
    clean_env.setattr(paths, "_ascii_root", lambda: str(tmp_path / "root"))
    want = os.path.join(str(tmp_path / "root"), "yedek", "GozFaresi")
    assert paths.ensure_dir(paths.data_dir()) == want
    assert os.path.isdir(want)
    assert paths.ensure_dir(paths.data_dir()) == want      # stable
    err = capsys.readouterr().err
    assert err.count("[yol]") == 1, err                    # said once
    assert err.isascii()                                   # cp1254 console


def test_ensure_dir_uses_mkdtemp_when_fallback_root_fails(clean_env,
                                                          tmp_path, capsys):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    clean_env.setattr(paths, "_ascii_root",
                      lambda: str(blocker / "also-blocked"))
    made = str(tmp_path / "mkdtemp")
    clean_env.setattr(paths.tempfile, "mkdtemp", lambda prefix: made)
    assert paths.ensure_dir(str(blocker / "data")) == made
    assert "[yol]" in capsys.readouterr().err


def test_ensure_dir_never_raises_when_nothing_is_writable(clean_env,
                                                          tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    clean_env.setattr(paths, "_ascii_root", lambda: str(blocker / "r"))

    def no_tmp(prefix):
        raise PermissionError("no tmp")
    clean_env.setattr(paths.tempfile, "mkdtemp", no_tmp)
    target = str(blocker / "data")
    assert paths.ensure_dir(target) == target    # writer reports the error


# -- ASCII fallback folders ----------------------------------------------------

def _public_test_dir():
    """A unique ASCII dir the test can create and remove (Windows)."""
    return os.path.join(r"C:\Users\Public", "gozfaresi-test-" + uuid.uuid4().hex)


@pytest.mark.skipif(os.name != "nt", reason="Windows fallback root")
def test_ascii_dir_falls_back_for_non_ascii(clean_env, tmp_path):
    # A real non-ASCII folder, whatever tmp_path happens to be on this host.
    data = tmp_path / "\u00d6rnek"
    clean_env.setenv(paths.ENV_DATA_DIR, str(data))
    assert not paths.model_dir().isascii()
    sub = "pytest-" + uuid.uuid4().hex
    got = paths.ascii_dir(sub)
    try:
        assert got == os.path.join(r"C:\Users\Public\GozFaresi", sub)
        assert got.isascii()
        assert os.path.isdir(got)
    finally:
        shutil.rmtree(got, ignore_errors=True)
        try:
            os.rmdir(r"C:\Users\Public\GozFaresi")   # only if we made it empty
        except OSError:
            pass


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="Linux fallback root")
def test_ascii_dir_falls_back_for_non_ascii_linux(clean_env, tmp_path):
    data = tmp_path / "\u00e7a\u011fr\u0131"
    clean_env.setenv(paths.ENV_DATA_DIR, str(data))
    clean_env.delenv("XDG_RUNTIME_DIR", raising=False)
    sub = "pytest-" + uuid.uuid4().hex
    got = paths.ascii_dir(sub)
    try:
        assert got == os.path.join(f"/tmp/gozfaresi-{os.getuid()}", sub)
        assert os.path.isdir(got)
    finally:
        shutil.rmtree(got, ignore_errors=True)


def test_ascii_dir_uses_model_dir_when_ascii(clean_env, tmp_path):
    base = _public_test_dir() if os.name == "nt" else str(tmp_path / "m")
    clean_env.setattr(paths, "model_dir", lambda: base)
    try:
        got = paths.ascii_dir("piper")
        assert got == os.path.join(base, "piper")
        assert os.path.isdir(got)
    finally:
        shutil.rmtree(base, ignore_errors=True)


TMP_ROOT = "/tmp/gozfaresi-1000"


@pytest.fixture
def fake_linux_tmp(clean_env, tmp_path):
    """Linux /tmp fallback simulated on any host: os.mkdir/os.lstat answer for
    TMP_ROOT only (everything else goes to the real functions), uid 1000."""
    clean_env.setattr(paths, "_is_windows", lambda: False)
    clean_env.delenv("XDG_RUNTIME_DIR", raising=False)
    clean_env.setattr(os, "getuid", lambda: 1000, raising=False)
    state = {"exists": False, "mode": stat.S_IFDIR | 0o700, "uid": 1000,
             "mkdir": [], "mkdir_calls": 0, "mkdtemp": 0,
             "mkdir_error": None, "mkdtemp_error": None}
    real_mkdir, real_lstat = os.mkdir, os.lstat

    def mkdir(path, mode=0o777, *a, **k):
        if path != TMP_ROOT:
            return real_mkdir(path, mode, *a, **k)
        state["mkdir_calls"] += 1
        if state["mkdir_error"]:
            raise state["mkdir_error"]
        if state["exists"]:
            raise FileExistsError(path)
        state["mkdir"].append(mode)
        state["exists"] = True

    def lstat(path, *a, **k):
        if path != TMP_ROOT:
            return real_lstat(path, *a, **k)
        return os.stat_result((state["mode"], 0, 0, 1, state["uid"], 0,
                               0, 0, 0, 0))

    def mkdtemp(prefix=None, **k):
        state["mkdtemp"] += 1
        assert prefix == "gozfaresi-"
        if state["mkdtemp_error"]:
            raise state["mkdtemp_error"]
        return str(tmp_path / ("private%d" % state["mkdtemp"]))

    clean_env.setattr(os, "mkdir", mkdir)
    clean_env.setattr(os, "lstat", lstat)
    clean_env.setattr(paths.tempfile, "mkdtemp", mkdtemp)
    return state


def test_linux_root_prefers_ascii_runtime_dir(fake_linux_tmp, clean_env):
    clean_env.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    assert paths._ascii_root() == os.path.join("/run/user/1000", "gozfaresi")
    assert fake_linux_tmp["mkdir"] == []


def test_linux_root_skips_non_ascii_runtime_dir(fake_linux_tmp, clean_env):
    clean_env.setenv("XDG_RUNTIME_DIR", "/run/\u00e7a\u011fr\u0131")
    assert paths._ascii_root() == TMP_ROOT


def test_linux_root_creates_private_tmp_dir(fake_linux_tmp):
    assert paths._ascii_root() == TMP_ROOT
    assert fake_linux_tmp["mkdir"] == [0o700]
    assert fake_linux_tmp["mkdtemp"] == 0


def test_linux_root_reuses_own_existing_tmp_dir(fake_linux_tmp):
    fake_linux_tmp["exists"] = True
    assert paths._ascii_root() == TMP_ROOT
    assert fake_linux_tmp["mkdtemp"] == 0


@pytest.mark.parametrize("st_mode, uid", [
    (stat.S_IFLNK | 0o777, 1000),          # symlink planted by someone else
    (stat.S_IFDIR | 0o700, 1001),          # folder owned by another user
    (stat.S_IFDIR | 0o770, 1000),          # group-writable
    (stat.S_IFDIR | 0o707, 1000),          # other-writable
    (stat.S_IFREG | 0o600, 1000),          # not a folder at all
])
def test_linux_root_refuses_unsafe_existing_tmp_dir(fake_linux_tmp, tmp_path,
                                                    st_mode, uid):
    fake_linux_tmp.update(exists=True, mode=st_mode, uid=uid)
    got = paths._ascii_root()
    assert got == str(tmp_path / "private1")
    assert paths._ascii_root() == got               # one mkdtemp per process
    assert fake_linux_tmp["mkdtemp"] == 1


def test_ascii_dir_does_not_touch_tmp_when_model_dir_is_ascii(fake_linux_tmp,
                                                              clean_env,
                                                              tmp_path):
    # The usual Linux case: the model dir is ASCII, so /tmp must not even be
    # looked at (no mkdir there, no leaked mkdtemp folder).
    base = _public_test_dir() if os.name == "nt" else str(tmp_path / "m")
    clean_env.setattr(paths, "model_dir", lambda: base)
    try:
        assert paths.ascii_dir("piper") == os.path.join(base, "piper")
        assert os.path.isdir(os.path.join(base, "piper"))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    assert fake_linux_tmp["mkdir_calls"] == 0
    assert fake_linux_tmp["mkdtemp"] == 0


def test_ascii_dir_never_raises_without_any_ascii_root(fake_linux_tmp,
                                                       clean_env, tmp_path,
                                                       capsys):
    # Non-ASCII model dir, /tmp unwritable AND mkdtemp failing: still no
    # exception - the normal folder comes back and the problem is said once.
    data = tmp_path / "çağrı"
    clean_env.setenv(paths.ENV_DATA_DIR, str(data))
    clean_env.setattr(paths, "_said_no_root", False)
    fake_linux_tmp["mkdir_error"] = PermissionError("ro /tmp")
    fake_linux_tmp["mkdtemp_error"] = PermissionError("ro /tmp")
    want = os.path.join(str(data), "models", "piper")
    assert paths.ascii_dir("piper") == want
    assert paths.ascii_dir("piper") == want
    assert os.path.isdir(want)
    err = capsys.readouterr().err
    assert err.count("[yol]") == 1, err
    assert err.isascii()


def test_ascii_root_never_raises(fake_linux_tmp, clean_env):
    clean_env.delattr(os, "getuid", raising=False)
    assert paths._ascii_root() is None


# -- bundled / legacy model locations ------------------------------------------

def test_bundled_model_only_when_file_exists(clean_env, tmp_path):
    meipass = tmp_path / "_MEI1"
    _freeze(clean_env, tmp_path / "zip", meipass=meipass)
    assert paths.bundled_model("face_landmarker.task") is None
    (meipass / "resources" / "models").mkdir(parents=True)
    f = meipass / "resources" / "models" / "face_landmarker.task"
    f.write_bytes(b"model")
    assert paths.bundled_model("face_landmarker.task") == str(f)


def test_ensure_model_prefers_bundled(clean_env, monkeypatch, tmp_path):
    bundled = tmp_path / "face_landmarker.task"
    bundled.write_bytes(b"m")
    monkeypatch.setattr(paths, "bundled_model",
                        lambda name: str(bundled)
                        if name == "face_landmarker.task" else None)
    monkeypatch.setattr(gaze.urllib.request, "urlretrieve",
                        lambda *a: pytest.fail("must not download"))
    assert gaze.ensure_model() == str(bundled)


def test_ensure_model_falls_back_to_legacy_path(clean_env, monkeypatch,
                                                tmp_path):
    legacy = tmp_path / "models" / "face_landmarker.task"
    legacy.parent.mkdir()
    legacy.write_bytes(b"m")
    monkeypatch.setattr(paths, "bundled_model", lambda name: None)
    monkeypatch.setattr(gaze, "_MODEL_PATH", str(legacy))
    monkeypatch.setattr(gaze.urllib.request, "urlretrieve",
                        lambda *a: pytest.fail("must not download"))
    assert gaze.ensure_model() == str(legacy)


def test_ensure_model_downloads_when_nothing_present(clean_env, monkeypatch,
                                                     tmp_path):
    target = tmp_path / "dl" / "face_landmarker.task"
    got = []
    monkeypatch.setattr(paths, "bundled_model", lambda name: None)
    monkeypatch.setattr(gaze, "_MODEL_PATH", str(target))
    monkeypatch.setattr(gaze.urllib.request, "urlretrieve",
                        lambda url, dst: got.append((url, dst)))
    assert gaze.ensure_model() == str(target)
    assert got == [(gaze._MODEL_URL, str(target))]


@pytest.mark.skipif(os.name != "nt", reason="Windows legacy folders")
def test_source_mode_keeps_legacy_model_paths(clean_env):
    # Import-time constants: the development machine's models/voices stay where they are.
    assert deep._ASCII_MODEL_DIR == r"C:\Users\Public\ptgaze-models"
    assert tts_backends.PIPER_VOICE_DIR == r"C:\Users\Public\piper-voices"
    assert tts_backends.XTTS_MODEL_DIR == r"C:\Users\Public\xtts-models"
    assert gaze._MODEL_PATH == os.path.join(GAZE_DIR, "models",
                                            "face_landmarker.task")
    # And the helper itself, evaluated now under the source-mode env.
    assert (paths.legacy_dir(r"C:\Users\Public\piper-voices")
            == r"C:\Users\Public\piper-voices")


def test_legacy_dir_moves_into_app_dirs_when_frozen(clean_env, tmp_path):
    clean_env.setattr(paths, "_is_windows", lambda: True)
    _freeze(clean_env, tmp_path / "Programs")
    local = _public_test_dir() if os.name == "nt" else str(tmp_path / "L")
    clean_env.setenv("LOCALAPPDATA", local)
    try:
        got = paths.legacy_dir(r"C:\Users\Public\ptgaze-models")
        assert got == os.path.join(local, "GozFaresi", "models",
                                   "ptgaze-models")
        assert not os.path.exists(local)     # pure: import-time constant
    finally:
        shutil.rmtree(local, ignore_errors=True)


def test_legacy_dir_is_ascii_for_non_ascii_installed_profile(clean_env,
                                                             tmp_path):
    # The folders behind these constants are opened by C code (mediapipe,
    # espeak) that cannot take a non-ASCII path.
    clean_env.setattr(paths, "_is_windows", lambda: True)
    _freeze(clean_env, tmp_path / "Programs")
    clean_env.setenv("LOCALAPPDATA", str(tmp_path / "\u00d6rnek"))
    got = paths.legacy_dir(r"C:\Users\Public\piper-voices")
    assert got == os.path.join(r"C:\Users\Public\GozFaresi", "piper-voices")

