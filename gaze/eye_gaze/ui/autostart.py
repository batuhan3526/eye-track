# gaze/eye_gaze/ui/autostart.py
""""Bilgisayar açılınca başlat" - only ever switched by the settings
checkbox (spec 4: no system setting changes without the user asking).

The ALS user cannot start the program themselves: after a reboot (an update,
a power cut) someone has to, unless this is on.

  Windows  a shortcut in the user's Startup folder, made by PowerShell's
           WScript.Shell exactly as OTOMATIK-BASLAT-KUR.bat makes its own -
           paths are handed over in environment variables, never pasted
           into the script, so a quote or a Turkish letter in a path cannot
           break the command. It starts the exe (frozen) or this checkout's
           gozfaresi.py under the current Python (source).
  Linux    ~/.config/autostart/gozfaresi.desktop (XDG_CONFIG_HOME
           respected), which every XDG desktop starts at login. An
           AppImage is started by its own path ($APPIMAGE): the exe path
           inside it is a mount point that changes on every run.
"""
import os
import subprocess
import sys

from eye_gaze.i18n import t

WINDOWS_LINK = "GozFaresi.lnk"
# Made by gaze/OTOMATIK-BASLAT-KUR.bat for the source checkout's BASLAT.bat.
OLD_WINDOWS_LINK = "Goz-Surucusu.lnk"
LINUX_ENTRY = "gozfaresi.desktop"

_GAZE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

_PS_SCRIPT = (
    "$s=(New-Object -ComObject WScript.Shell)"
    ".CreateShortcut($env:GOZFARESI_LNK);"
    " $s.TargetPath=$env:GOZFARESI_HEDEF;"
    " $s.Arguments=$env:GOZFARESI_ARGS;"
    " $s.WorkingDirectory=$env:GOZFARESI_KLASOR;"
    " $s.WindowStyle=7; $s.Description='Goz Faresi'; $s.Save()")


def _is_windows():
    return os.name == "nt"


def _frozen():
    return bool(getattr(sys, "frozen", False))


def launch_target():
    """(program, [arguments], working folder) that starts the launcher."""
    if _frozen():
        exe = sys.executable
        if not _is_windows() and os.environ.get("APPIMAGE"):
            exe = os.environ["APPIMAGE"]
        return exe, [], os.path.dirname(exe)
    script = os.path.join(_GAZE_DIR, "gozfaresi.py")
    exe = sys.executable
    if _is_windows():
        # pythonw: no console window at login (the launcher has its own).
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.basename(exe).lower() == "python.exe" and os.path.exists(w):
            exe = w
    return exe, [script], _GAZE_DIR


# --- Windows -----------------------------------------------------------------

def windows_startup_dir():
    appdata = os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup")


def windows_link_path():
    return os.path.join(windows_startup_dir(), WINDOWS_LINK)


def powershell_command(link, target, args, workdir):
    """(argv, env) that writes the shortcut. Built, not run."""
    env = dict(os.environ)
    env.update({"GOZFARESI_LNK": link, "GOZFARESI_HEDEF": target,
                "GOZFARESI_ARGS": subprocess.list2cmdline(args),
                "GOZFARESI_KLASOR": workdir})
    root = os.environ.get("SystemRoot")
    exe = (os.path.join(root, "System32", "WindowsPowerShell", "v1.0",
                        "powershell.exe") if root else "powershell")
    argv = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
            "Bypass", "-Command", _PS_SCRIPT]
    return argv, env


def _run(argv, env):
    """(exit code, stderr) of the PowerShell call; no console window."""
    flags = 0x08000000 if _is_windows() else 0      # CREATE_NO_WINDOW
    try:
        p = subprocess.run(argv, env=env, capture_output=True, timeout=30,
                           creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return p.returncode, (p.stderr or b"").decode("utf-8", "replace")


def _set_windows(on, run):
    link = windows_link_path()
    if not on:
        return _remove(link)
    old = os.path.join(windows_startup_dir(), OLD_WINDOWS_LINK)
    if os.path.exists(old):
        # Both would start at login: two drivers on one camera and one
        # keyboard. Removing the other one is the caregiver's decision.
        return False, t("autostart.old_link", name=OLD_WINDOWS_LINK)
    try:
        os.makedirs(os.path.dirname(link), exist_ok=True)
    except OSError as exc:
        return False, t("autostart.no_folder", error=exc)
    target, args, workdir = launch_target()
    argv, env = powershell_command(link, target, args, workdir)
    code, err = (run or _run)(argv, env)
    if code != 0:
        return False, t("autostart.link_failed",
                        error=err.strip() or t("autostart.code", code=code))
    return True, t("autostart.on_windows")


# --- Linux -------------------------------------------------------------------

def linux_entry_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.environ.get("HOME") or os.path.expanduser("~"), ".config")
    return os.path.join(base, "autostart", LINUX_ENTRY)


def _desktop_quote(arg):
    # Desktop Entry spec, "Exec key": quote an argument holding a reserved
    # character, and escape " ` $ \ inside the quotes.
    if arg and not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    out = arg
    for c in ("\\", '"', "`", "$"):
        out = out.replace(c, "\\" + c)
    return '"%s"' % out


def desktop_exec(argv):
    return " ".join(_desktop_quote(a) for a in argv)


def _set_linux(on):
    path = linux_entry_path()
    if not on:
        return _remove(path)
    target, args, _ = launch_target()
    text = ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Göz Faresi\n"
            "Comment=%s\n"
            "Exec=%s\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
            % (t("autostart.comment"), desktop_exec([target] + args)))
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, t("autostart.write_failed", error=exc)
    return True, t("autostart.on_linux")


# --- both --------------------------------------------------------------------

def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        return False, t("autostart.off_failed", error=exc)
    return True, t("autostart.turned_off")


def is_enabled():
    if _is_windows():
        return os.path.exists(windows_link_path())
    return os.path.exists(linux_entry_path())


def set_enabled(on, run=None):
    """(ok, Turkish sentence for the window). `run(argv, env) -> (code,
    stderr)` replaces the PowerShell call in tests."""
    if _is_windows():
        return _set_windows(bool(on), run)
    return _set_linux(bool(on))
