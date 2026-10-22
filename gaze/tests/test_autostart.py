# gaze/tests/test_autostart.py
""""Bilgisayar açılınca başlat" (ui/autostart.py). The Windows shortcut is
only ever built as a PowerShell command here, never run; the Linux entry
goes to a temporary HOME."""
import os
import sys

import pytest

from eye_gaze.ui import autostart


@pytest.fixture
def linux_home(monkeypatch, tmp_path):
    home = tmp_path / "ev çağrı"
    home.mkdir()
    monkeypatch.setattr(autostart, "_is_windows", lambda: False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPIMAGE", raising=False)
    return home


def test_autostart_roundtrip_linux(linux_home, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/Göz Faresi/gozfaresi")
    assert not autostart.is_enabled()
    ok, msg = autostart.set_enabled(True)
    assert ok, msg
    path = linux_home / ".config" / "autostart" / "gozfaresi.desktop"
    text = path.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in text and "Type=Application" in text
    assert 'Exec="/opt/Göz Faresi/gozfaresi"\n' in text
    assert "Name=Göz Faresi" in text
    assert autostart.is_enabled()
    ok, msg = autostart.set_enabled(False)
    assert ok and not path.exists() and not autostart.is_enabled()


def test_linux_autostart_starts_the_appimage_not_its_mount(linux_home,
                                                            monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/tmp/.mount_GozXYZ/gozfaresi")
    monkeypatch.setenv("APPIMAGE", "/home/u/GozFaresi-1.2.0.AppImage")
    autostart.set_enabled(True)
    text = autostart.linux_entry_path()
    with open(text, encoding="utf-8") as f:
        assert "Exec=/home/u/GozFaresi-1.2.0.AppImage\n" in f.read()


def test_linux_autostart_respects_xdg_config_home(linux_home, monkeypatch,
                                                  tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert autostart.linux_entry_path() == str(
        tmp_path / "xdg" / "autostart" / "gozfaresi.desktop")


def test_desktop_exec_quoting():
    assert autostart.desktop_exec(["/a b/py", "/x/g$z.py"]) == \
        '"/a b/py" "/x/g\\$z.py"'
    assert autostart.desktop_exec(["/usr/bin/gozfaresi"]) == \
        "/usr/bin/gozfaresi"


@pytest.fixture
def windows_env(monkeypatch, tmp_path):
    appdata = tmp_path / "AppData Roaming çğ"
    monkeypatch.setattr(autostart, "_is_windows", lambda: True)
    monkeypatch.setenv("APPDATA", str(appdata))
    return appdata


def test_autostart_windows_lnk_command(windows_env, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe = r"C:\Users\Örnek\AppData\Local\Programs\GozFaresi\GozFaresi.exe"
    monkeypatch.setattr(sys, "executable", exe)
    runs = []

    def fake_run(argv, env):
        runs.append((argv, env))
        return 0, ""
    ok, msg = autostart.set_enabled(True, run=fake_run)
    assert ok, msg
    (argv, env), = runs
    assert argv[0].lower().endswith("powershell.exe") or argv[0] == \
        "powershell"
    assert "-NoProfile" in argv
    script = argv[-1]
    assert "WScript.Shell" in script and "CreateShortcut" in script
    assert "$env:GOZFARESI_LNK" in script and "$s.Save()" in script
    # Paths travel in the environment, never inside the script text:
    # quotes or Turkish letters in them cannot break the command.
    assert exe not in script
    assert env["GOZFARESI_HEDEF"] == exe
    assert env["GOZFARESI_LNK"] == os.path.join(
        str(windows_env), "Microsoft", "Windows", "Start Menu", "Programs",
        "Startup", "GozFaresi.lnk")
    assert env["GOZFARESI_KLASOR"] == os.path.dirname(exe)
    assert env["GOZFARESI_ARGS"] == ""


def test_windows_source_shortcut_runs_the_launcher_script(windows_env,
                                                          monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    runs = []
    autostart.set_enabled(True, run=lambda a, e: runs.append(e) or (0, ""))
    env = runs[0]
    # python.exe -> pythonw.exe beside it (no console window at login).
    assert os.path.dirname(env["GOZFARESI_HEDEF"]) == os.path.dirname(
        sys.executable)
    assert os.path.basename(env["GOZFARESI_HEDEF"]).lower() in (
        os.path.basename(sys.executable).lower(), "pythonw.exe")
    assert env["GOZFARESI_ARGS"].rstrip('"').endswith("gozfaresi.py")


def test_windows_shortcut_failure_is_said(windows_env):
    ok, msg = autostart.set_enabled(
        True, run=lambda a, e: (1, "erisim engellendi"))
    assert not ok and "kısayol" in msg.lower()


def test_old_bat_autostart_blocks_a_second_one(windows_env):
    startup = autostart.windows_startup_dir()
    os.makedirs(startup)
    with open(os.path.join(startup, autostart.OLD_WINDOWS_LINK), "w"):
        pass
    runs = []
    ok, msg = autostart.set_enabled(True, run=lambda a, e: runs.append(a)
                                    or (0, ""))
    assert not ok and runs == []
    assert "Goz-Surucusu" in msg and "iki" in msg


def test_windows_disable_removes_the_shortcut(windows_env):
    startup = autostart.windows_startup_dir()
    os.makedirs(startup)
    lnk = os.path.join(startup, autostart.WINDOWS_LINK)
    with open(lnk, "w"):
        pass
    assert autostart.is_enabled()
    ok, msg = autostart.set_enabled(False)
    assert ok and not os.path.exists(lnk)
