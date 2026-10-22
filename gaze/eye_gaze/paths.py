# gaze/eye_gaze/paths.py
"""Every file location the program uses, for the three ways it can run.

  source     - run from the git checkout (sys.frozen unset). Everything stays
               in gaze/ exactly as before packaging existed: the development machine's
               calibration, config and models must not move, or the driver
               would quietly start from an uncalibrated data dir.
  portable   - a frozen build with config.yaml or a "tasinabilir" marker next
               to the exe (the zip / USB stick): data lives beside the exe.
  installed  - any other frozen build: per-user OS data folders.

GOZFARESI_DATA_DIR overrides the data (and, by default, model and cache)
folder in every mode, so tests and smokes never read or write a real install.
Several modules turn these paths into constants at import time (e.g.
gaze_driver._BASE_DIR, gaze._MODEL_PATH), so the variable must be set BEFORE
those modules are imported - in practice, in the environment of the process.

The *_dir() functions only compute paths: they never create anything and
never raise, because they run while the driver and the launcher are still
importing - a crash there would leave the user with no program at all, not
even its error message. Writers call ensure_dir() on the path they are about
to write into. Nothing is cached (except the one-off fallbacks below): each
call re-reads sys/env, so a test can simulate a mode with monkeypatch alone.
"""
import ntpath
import os
import stat
import sys
import tempfile

ENV_DATA_DIR = "GOZFARESI_DATA_DIR"
APP_NAME = "GozFaresi"          # Windows folder name
APP_NAME_LINUX = "gozfaresi"    # XDG folder name
PORTABLE_MARKERS = ("config.yaml", "tasinabilir")

# Where path-only C libraries get their files when the normal folder is not
# ASCII (measured on the development machine: mediapipe and espeak cannot open
# C:\Users\Örnek\...). Public is ASCII and writable without admin rights.
WINDOWS_ASCII_ROOT = r"C:\Users\Public\GozFaresi"

_GAZE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ensure_dir(): original path -> the folder used instead (logged once each).
_fallbacks = {}
# Private mkdtemp() root, made once per process if /tmp/gozfaresi-<uid> is
# not safe to use.
_tmp_fallback = None
# ascii_dir() found no safe ASCII root at all (said once).
_said_no_root = False


def _is_windows():
    return os.name == "nt"


def _frozen():
    return bool(getattr(sys, "frozen", False))


def mode():
    """"source" | "portable" | "installed" (see module docstring)."""
    if not _frozen():
        return "source"
    exe_dir = app_dir()
    if any(os.path.exists(os.path.join(exe_dir, m)) for m in PORTABLE_MARKERS):
        return "portable"
    return "installed"


def app_dir():
    """gaze/ in source; the folder holding the exe when frozen."""
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _GAZE_DIR


def _linux_xdg(var, default):
    return os.environ.get(var) or os.path.expanduser(default)


def _xdg_data_app():
    return os.path.join(
        _linux_xdg("XDG_DATA_HOME", os.path.join("~", ".local", "share")),
        APP_NAME_LINUX)


def _installed_linux():
    # XDG splits settings (config) from bulky data (share); the override and
    # every other mode keep everything under data_dir().
    return (not os.environ.get(ENV_DATA_DIR) and mode() == "installed"
            and not _is_windows())


def data_dir():
    """config.yaml, calibration, gaze.log, makine.json. Not created here."""
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return override
    m = mode()
    if m == "source":
        return _GAZE_DIR
    if m == "portable":
        return app_dir()
    if _is_windows():
        base = (os.environ.get("LOCALAPPDATA")
                or os.path.expanduser(os.path.join("~", "AppData", "Local")))
        return os.path.join(base, APP_NAME)
    return os.path.join(
        _linux_xdg("XDG_CONFIG_HOME", os.path.join("~", ".config")),
        APP_NAME_LINUX)


def model_dir():
    """Downloaded models (GPU libraries go to model_dir()/gpu). Not created."""
    if _installed_linux():
        return os.path.join(_xdg_data_app(), "models")
    return os.path.join(data_dir(), "models")


def cache_dir():
    """Regenerable files (e.g. cache/aac phrase audio). Not created."""
    if _installed_linux():
        return os.path.join(_xdg_data_app(), "cache")
    return os.path.join(data_dir(), "cache")


def resource_path(*parts):
    """Files shipped inside the package (fonts, AAC boards, bundled models)."""
    if _frozen():
        base = getattr(sys, "_MEIPASS", app_dir())
    else:
        base = _GAZE_DIR
    return os.path.join(base, "resources", *parts)


def bundled_model(name):
    """Path of a model shipped in the package, or None if this build has none."""
    p = resource_path("models", name)
    return p if os.path.isfile(p) else None


def _private_tmp():
    """A mkdtemp() folder (0700, ours by construction), made once; None if
    even that cannot be made (tried again on the next call)."""
    global _tmp_fallback
    if _tmp_fallback is None:
        try:
            _tmp_fallback = tempfile.mkdtemp(prefix="gozfaresi-")
        except OSError:
            return None
    return _tmp_fallback


def _safe_linux_root():
    """ASCII root on Linux that nobody else can plant files or links in."""
    rt = os.environ.get("XDG_RUNTIME_DIR")
    if rt and rt.isascii():
        # Per-user, 0700, owned by us - systemd/logind guarantee that.
        return os.path.join(rt, APP_NAME_LINUX)
    uid = os.getuid()
    root = f"/tmp/gozfaresi-{uid}"
    try:
        os.mkdir(root, 0o700)
    except FileExistsError:
        pass
    except OSError:
        return _private_tmp()
    # /tmp is shared: another user may have created this name first, as a
    # symlink into our files or as a folder they can write into.
    try:
        st = os.lstat(root)
    except OSError:
        return _private_tmp()
    if (stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode)
            or st.st_uid != uid or st.st_mode & 0o022):
        return _private_tmp()
    return root


def _nominal_ascii_root():
    """The ASCII root without touching the disk (import-time constants)."""
    if _is_windows():
        return WINDOWS_ASCII_ROOT
    rt = os.environ.get("XDG_RUNTIME_DIR")
    if rt and rt.isascii():
        return os.path.join(rt, APP_NAME_LINUX)
    getuid = getattr(os, "getuid", None)
    return f"/tmp/gozfaresi-{getuid() if getuid else 0}"


def _ascii_root():
    """The ASCII fallback root, or None if no safe one can be had. Costs disk
    I/O on Linux (mkdir/lstat in /tmp), so call it only when it is needed."""
    if _is_windows():
        return WINDOWS_ASCII_ROOT
    try:
        return _safe_linux_root()
    except Exception:       # e.g. no os.getuid: never let a path lookup crash
        return None


def _log_once(path, fallback, err):
    # Console is cp1254 and the path may hold Turkish letters: ascii().
    print("[yol] klasor olusturulamadi: %s (%s) - yerine %s kullaniliyor"
          % (ascii(path), err.__class__.__name__, ascii(fallback)),
          file=sys.stderr)


def ensure_dir(path):
    """Create `path` and return it; if that fails, create and return a
    fallback folder instead (said once on stderr). Never raises: an
    unwritable profile must not stop the driver from starting."""
    if path in _fallbacks:
        return _fallbacks[path]
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError as e:
        err = e
    leaf = os.path.basename(os.path.normpath(path)) or APP_NAME
    fallback = path
    try:
        root = _ascii_root()
        if root is None:
            raise OSError("no ASCII fallback root")
        cand = os.path.join(root, "yedek", leaf)
        os.makedirs(cand, exist_ok=True)
        fallback = cand
    except OSError:
        try:
            fallback = tempfile.mkdtemp(prefix="gozfaresi-")
        except OSError:
            pass   # nothing writable at all: the writer will report it
    _fallbacks[path] = fallback
    _log_once(path, fallback, err)
    return fallback


def _ascii_path(sub, root_fn):
    """model_dir()/sub if ASCII, else root_fn()/sub. root_fn is only called
    when needed: on Linux it touches /tmp, which the usual (ASCII) case must
    never do. None if root_fn() has no root to offer."""
    normal = os.path.join(model_dir(), sub)
    if normal.isascii():
        return normal
    root = root_fn()
    return None if root is None else os.path.join(root, sub)


def ascii_path(sub):
    """The folder ascii_dir(sub) normally gives, computed only: nothing is
    created or probed (for status queries)."""
    return _ascii_path(sub, _nominal_ascii_root)


def ascii_dir(sub):
    """An ASCII-only folder for `sub` (created), for libraries that only take
    a path: model_dir()/sub when that is ASCII, else the shared fallback.
    Never raises; with no safe ASCII root at all it says so once and returns
    the normal folder, so the library reports its own failure."""
    global _said_no_root
    p = _ascii_path(sub, _ascii_root)
    if p is None:
        p = os.path.join(model_dir(), sub)
        if not _said_no_root:
            _said_no_root = True
            print("[yol] ASCII yedek klasor olusturulamadi; %s kullaniliyor"
                  % ascii(p), file=sys.stderr)
    return ensure_dir(p)


def legacy_dir(windows_path):
    """The folder an older source install already populated on Windows
    (e.g. C:\\Users\\Public\\piper-voices), kept so the development machine's models and
    voices never move. Any other mode gets the same-named ASCII folder under
    the app's own dirs. Pure (used for import-time constants): nothing is
    created or checked here; callers already create the folder on first use.
    """
    if (mode() == "source" and _is_windows()
            and not os.environ.get(ENV_DATA_DIR)):
        return windows_path
    return _ascii_path(ntpath.basename(windows_path), _nominal_ascii_root)
