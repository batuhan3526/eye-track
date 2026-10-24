# gaze/gaze_driver.py
"""Webcam gaze -> Windows cursor driver, with clicking.

Eyes move the cursor; a finger switch (keyboard key and/or XInput gamepad) or a
long blink performs clicks, so the system can be used WITHOUT OptiKey for mouse
control. OptiKey is still used for typing + Turkish speech.

Usage:  python gaze_driver.py  (or double-click BASLAT.bat)
Global double-press hotkeys (work in any window): quit / recalibrate / pause.
Defaults f10 / f9 / f8; all configurable in config.yaml.
"""
import sys
import os
import json
import math
import time
import threading
import statistics
from collections import deque

# Silence MediaPipe/TensorFlow C++ INFO/WARNING chatter (XNNPACK, feedback
# manager, telemetry). Must be set before cv2/mediapipe are imported.
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
# Media Foundation's hardware transforms made opening a common USB webcam take
# ~130 s (measured); off, the same 1280x960 stream opens in ~1 s. That delay hit every
# start and every camera reconnect. Read by OpenCV at import time.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2
import numpy as np

from eye_gaze.config import is_auto, load_config, used_backup, used_defaults
from eye_gaze.gaze import GazeEstimator
from eye_gaze.mapping import GazeMapper, fit_with_rejection, holdout_error
from eye_gaze.oneeuro import OneEuroFilter
from eye_gaze.limiter import SpeedLimiter
from eye_gaze.stabilizer import FixationStabilizer
from eye_gaze.saccade import make_saccade_jump
from eye_gaze.snap import make_target_snap
from eye_gaze.hotkeys import Hotkeys, DoublePress
from eye_gaze.switch import (EdgeDetector, GamepadReader, KeyPresses,
                             valid_button, button_names_text, GAMEPAD_BUTTONS)
from eye_gaze.blink import BlinkClicker
from eye_gaze.dwell import DwellClicker
from eye_gaze.dwell_ring import attach as attach_dwell_ring, guard_capture
from eye_gaze.pursuit import PursuitPath, best_latency_fit, pursuit_pairs
from eye_gaze import cursor, actions
from eye_gaze import caldata
from eye_gaze import modelsel
from eye_gaze.implicit import ImplicitCorrector
from eye_gaze import click_menu
from eye_gaze import paths
from eye_gaze import hardware
from eye_gaze import i18n
from eye_gaze.i18n import t_ascii
from eye_gaze import platform as _platform

# config/calibration/log/cache folder: gaze/ in source (independent of the
# process cwd, so running from another directory still finds them), the
# portable or per-user folder when frozen. Tests monkeypatch this name.
# Only computed here (never created): an import must not fail on a bad
# profile; main() creates it. GOZFARESI_DATA_DIR must be set before import.
_BASE_DIR = paths.data_dir()


class _Tee:
    """Mirror writes to several streams (console + log file)."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, s):
        for st in self._streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass

    def flush(self):
        for st in self._streams:
            try:
                st.flush()
            except Exception:
                pass


def _setup_logging():
    """Mirror all console output (and tracebacks) into gaze.log so problems
    can be diagnosed after the console window is gone."""
    path = os.path.join(_BASE_DIR, "gaze.log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            os.remove(path)
        f = open(path, "a", encoding="utf-8", errors="replace")
    except Exception:
        return
    import datetime
    f.write("\n===== yeni calisma: %s =====\n"
            % datetime.datetime.now().isoformat(timespec="seconds"))
    f.flush()
    sys.stdout = _Tee(sys.stdout, f)
    sys.stderr = _Tee(sys.stderr, f)

# Calibration dots pushed close to the edges so the full screen is reachable.
_CAL_POSITIONS_9 = [(0.08, 0.08), (0.5, 0.08), (0.92, 0.08),
                    (0.08, 0.5), (0.5, 0.5), (0.92, 0.5),
                    (0.08, 0.92), (0.5, 0.92), (0.92, 0.92)]


def _cal_positions(n):
    """9-point grid by default; 16+ requests a denser 4x4 grid (better fit,
    required for a reliable quadratic mapping)."""
    if n >= 16:
        vals = (0.06, 0.37, 0.63, 0.94)
        return [(x, y) for y in vals for x in vals]
    return _CAL_POSITIONS_9[:n]


_FG_API = None


def _fg_api():
    """(user32, kernel32) for the foreground-window check, or (None, None)."""
    global _FG_API
    if _FG_API is not None:
        return _FG_API
    try:
        import ctypes
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = ctypes.c_void_p
        u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p,
                                               ctypes.POINTER(ctypes.c_uint)]
        u.GetWindowThreadProcessId.restype = ctypes.c_uint
        k.GetCurrentProcessId.argtypes = []
        k.GetCurrentProcessId.restype = ctypes.c_uint
    except Exception:
        _FG_API = (None, None)
        return _FG_API
    _FG_API = (u, k)
    return _FG_API


def _foreground_is_ours():
    """Does the focused window belong to THIS process?

    F4 copies the user's selection with a synthetic Ctrl+C, and a synthetic key
    goes wherever the focus is. Our own console is a window too: land Ctrl+C
    there and cmd.exe asks "Terminate batch job (Y/N)?" while Python takes a
    KeyboardInterrupt and exits - so answering N changes nothing, the driver is
    already gone. It was reported from the field, pressing F4 right after F3.

    Anything uncertain - no foreground window, an API that will not answer -
    counts as ours. The risks are not symmetric: guessing "not ours" wrongly
    ends the session, guessing "ours" wrongly only means F4 speaks whatever is
    already on the clipboard.

    Linux asks the X server (platform.linux_misc.foreground_is_ours), and
    there "unknown" is False: Wayland never says which window has the focus,
    and True would switch F4 off for good.
    """
    if _platform.OS != "windows":
        from eye_gaze.platform import linux_misc
        return linux_misc.foreground_is_ours()
    u, k = _fg_api()
    if u is None:
        return True
    try:
        import ctypes
        hwnd = u.GetForegroundWindow()
        if not hwnd:
            return True
        pid = ctypes.c_uint(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        return pid.value == k.GetCurrentProcessId()
    except Exception:
        return True


_TOPMOST_API = None


def _topmost_api():
    """user32 with the window prototypes declared, or None.

    Same reasoning as eye_gaze.zoom._gdi(): HWND is documented to fit in 32
    bits, so this is not the truncation that crashed the clipboard - but this
    runs thirty times a second whenever the magnifier or the AAC grid is open,
    and "probably the right window" is not a thing to leave to chance when
    getting it wrong means shoving somebody else's window to the front.
    """
    global _TOPMOST_API
    if _TOPMOST_API is not None:
        return _TOPMOST_API[0]
    try:
        import ctypes
        u = ctypes.windll.user32
        u.FindWindowW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        u.FindWindowW.restype = ctypes.c_void_p
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_uint]
        u.SetWindowPos.restype = ctypes.c_int
    except Exception:
        _TOPMOST_API = (None,)
        return None
    _TOPMOST_API = (u,)
    return u


def _raise_process_priority():
    """Above-normal priority keeps cursor updates smooth when other apps
    (browser, OptiKey, games) load the machine.

    Not on Linux: raising priority there (a negative os.nice) needs root,
    and the only change an ordinary user may make is to LOWER it."""
    if _platform.OS != "windows":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k.SetPriorityClass.restype = ctypes.c_int
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00008000)  # ABOVE_NORMAL
    except Exception:
        pass


def _force_topmost(title):
    """Push the named window above other always-on-top apps (e.g. OptiKey).
    cv2's WND_PROP_TOPMOST loses to apps that re-assert their own topmost
    state, so this is called repeatedly with the Win32 API instead. Linux:
    the X11 "above" state (linux_misc.raise_window, never raises)."""
    if _platform.OS != "windows":
        from eye_gaze.platform import linux_misc
        linux_misc.raise_window(title)
        return
    try:
        import ctypes
        u = _topmost_api()
        if u is None:
            return
        hwnd = u.FindWindowW(None, title)
        if hwnd:
            # HWND_TOPMOST, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            u.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0,
                           0x0001 | 0x0002 | 0x0010)
    except Exception:
        pass


# --- the primary monitor --------------------------------------------------
#
# Calibration, the magnifier, the AAC grid, the click menu and the drift dot
# are fullscreen windows laid out in primary-screen pixels (0..sw, 0..sh), and
# the calibration maps the gaze into those same pixels. On Windows the
# primary monitor's top-left IS (0, 0) - the virtual screen is defined that
# way - so nothing there is offset. An X11 primary is often not: a monitor to
# its left puts it at (1920, 0), and the driver used to draw the calibration
# on whichever monitor the window manager chose and steer the cursor over the
# wrong one.

# (x, y) of the primary monitor, set once by main() (tests set it too). None:
# windows are left where they open - what every caller got before.
_SCREEN_ORIGIN = None


def _primary_rect():
    """(x, y, w, h) of the primary monitor in cursor coordinates."""
    if _platform.OS == "windows":
        sw, sh = cursor.screen_size()
        return (0, 0, sw, sh)
    x, y, w, h = cursor.primary_rect()
    return (int(x), int(y), int(w), int(h))


def _gaze_bounds(multi_monitor, primary):
    """Where the gaze cursor may go: the whole virtual desktop, or the
    primary monitor - at its real position, not at (0, 0)."""
    if multi_monitor:
        return cursor.virtual_screen()
    return tuple(primary)


def _place_on_primary(win, origin=None):
    """Move a just-created window onto the primary monitor, so that going
    fullscreen fills THAT monitor. Not on Windows (origin (0, 0): the
    window opens there already, and its behaviour stays as it was)."""
    origin = _SCREEN_ORIGIN if origin is None else origin
    if origin is None or _platform.OS == "windows":
        return
    try:
        cv2.moveWindow(win, int(origin[0]), int(origin[1]))
    except Exception:
        pass


def _fullscreen(win):
    """A fullscreen OpenCV window on the primary monitor."""
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    _place_on_primary(win)
    cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)


# --- what this session cannot do ------------------------------------------

_caps_said = [False]


def _print_missing_capabilities(caps=None, say=print):
    """Name, once at start, every capability this session lacks (Linux:
    Wayland without permission, no mss, no X display...), with its reason.
    A key that does nothing must never be a silent mystery. Never raises."""
    if _caps_said[0]:
        return
    _caps_said[0] = True
    try:
        caps = _platform.capabilities() if caps is None else caps
        missing = [(k, v[1]) for k, v in caps.items() if not v[0]]
    except Exception as exc:
        say("UYARI: bu oturumun ozellikleri denetlenemedi (%s)."
            % type(exc).__name__)
        return
    if not missing:
        return
    say("Bu oturumda CALISMAYAN ozellikler:")
    for key, why in missing:
        say("  - %s: %s" % (key, _platform.ascii_text(why or "sebep bilinmiyor")))


# --- makine.json: "auto" settings ------------------------------------------

_CAMERA_KEYS = ("camera_index", "camera_width", "camera_height", "camera_fps")


def _resolve_machine(cfg, probe=None, say=print, now=None):
    """(cfg, profile, camera) - config.yaml's "auto" lines filled from
    makine.json (spec 6.6/6.9). Never raises: on any fault cfg comes back as
    it was, and "auto" keeps the driver's old defaults.

    An "auto" camera with no camera in the profile is probed for and the
    result saved, so the next start opens it at once. A probe that finds
    nothing is remembered too (camera_missing), so a start without the
    camera does not pay for the probe again until a day later: "auto" then
    keeps the old code defaults. The smoke camera is used, never saved. An
    explicit camera_index (with an "auto" mode) probes only that camera.
    The screens are
    re-read into an existing profile; a changed screen needs nothing more
    here (_fit_setup rescales the calibration). `camera` is the profile's
    camera record when this session uses it, else None.
    """
    try:
        # Wall time (hardware.wall_clock): the stamp outlives the process.
        now = hardware.wall_clock() if now is None else now
        profile = hardware.load_profile()
        changed = False
        want_cam = any(is_auto(cfg.get(k)) for k in _CAMERA_KEYS)
        cam = profile.get("camera")
        index_auto = is_auto(cfg.get("camera_index"))
        want_index = "auto" if index_auto else cfg.get("camera_index")
        need = want_cam and (not isinstance(cam, dict) or (
            not index_auto and cam.get("index") != want_index))
        miss = profile.get("camera_missing")
        recent_miss = (isinstance(miss, dict)
                       and miss.get("index") == want_index
                       and isinstance(miss.get("at"), (int, float))
                       and 0 <= now - miss["at"] < hardware.NO_CAMERA_RETRY_S)
        if need and recent_miss:
            say("Kamera son aramada bulunamamisti; bugun yeniden aranmiyor"
                " (varsayilan kamera ayarlari kullaniliyor).")
        elif need:
            say("Kamera araniyor (kayitli kamera yok) - birkac saniye"
                " surebilir...")
            found = (probe or hardware.probe_cameras)(
                **({} if index_auto else {"indices": [want_index]}))
            pick = None
            for info in found:
                if index_auto or info.index == want_index:
                    pick = info
                    break
            if pick is not None:
                rec = hardware.camera_record(pick)
                profile["camera"] = rec
                profile.pop("camera_missing", None)
                # The smoke camera: this session only (save_profile drops it).
                changed = not hardware.is_fake(rec)
                say("Kamera bulundu: %d (%s) %dx%d, olculen %.0f fps."
                    % (pick.index, pick.backend, pick.width, pick.height,
                       pick.fps_measured))
            else:
                profile["camera_missing"] = {"index": want_index, "at": now}
                changed = True
                say("Kamera bulunamadi; varsayilan kamera ayarlariyla"
                    " bekleniyor.")
        if profile or changed:
            now = [list(r) for r in hardware.screens()]
            sc = round(hardware.scale(), 3)
            if profile.get("screens") != now or profile.get("scale") != sc:
                profile["screens"], profile["scale"] = now, sc
                changed = True
        if changed:
            hardware.save_profile(profile)
        out = hardware.resolve_auto(cfg, profile)
        cam = profile.get("camera")
        used = (dict(cam) if want_cam and isinstance(cam, dict)
                and out.get("camera_index") == cam.get("index") else None)
        return out, profile, used
    except Exception as exc:
        say("UYARI: makine.json kullanilamadi (%s); \"auto\" ayarlar"
            " varsayilan degerlerle calisiyor." % type(exc).__name__)
        return cfg, {}, None


def _calibration_camera_warning(profile, camera):
    """The line saying the loaded calibration was taken with another camera
    (makine.json's calibration_camera), or None. Only a warning: the image
    may have another aspect ratio or field of view, and the user decides -
    nothing is recalibrated by itself."""
    try:
        calcam = profile.get("calibration_camera")
        if (not camera or not isinstance(calcam, dict)
                or hardware.same_camera(calcam, camera)):
            return None
        return ("DIKKAT: kalibrasyon bu kamerayla yapilmadi; F7 ya da F9"
                " onerilir (kalibrasyon: kamera %s %sx%s, su an: kamera"
                " %s %sx%s)."
                % (calcam.get("index"), calcam.get("width"),
                   calcam.get("height"), camera.get("index"),
                   camera.get("width"), camera.get("height")))
    except Exception:
        return None


def _note_calibration_camera(profile, camera, fresh, save=None):
    """Remember in makine.json which camera the calibration in use was taken
    with - the evidence _calibration_camera_warning warns from.

    A new (saved) calibration records the camera it was taken with. A loaded
    calibration with nothing recorded (makine.json lost, or a calibration
    older than it) is recorded as "unknown": claiming today's camera would
    be false confidence, and warning would nag about a camera that may well
    be right - so nothing is said. A recorded camera is never overwritten by
    a loaded calibration: a mismatch keeps warning until the user
    recalibrates. The smoke camera is never recorded. Never touches
    calibration files; never raises."""
    if not camera or hardware.is_fake(camera):
        return False
    try:
        have = profile.get("calibration_camera")
        if fresh:
            new = hardware.camera_identity(camera)
        elif have is None:
            new = "unknown"
        else:
            return False
        if have == new:
            return False
        profile["calibration_camera"] = new
        return bool((save or hardware.save_profile)(profile))
    except Exception:
        return False


def _keep_deep_for_calibration(cfg, was_auto, cal_path, base_inputs,
                               say=print):
    """makine.json turning deep_gaze off must not orphan a calibration taken
    WITH the deep model: that would force a tiring recalibration. Then
    deep_gaze goes back to "auto", whose own rule (_make_deep: the model
    opens, on the CPU if need be) keeps the calibration valid."""
    if not was_auto or _deep_mode(cfg) not in _DEEP_OFF:
        return cfg
    if not _deep_expected({"deep_gaze": "auto"}, cal_path, base_inputs):
        return cfg
    say("makine.json derin modeli kapali diyor, ama kayitli kalibrasyon"
        " derin modelle alinmis: derin model ACIK tutuluyor (kalibrasyon"
        " gecerli kalir).")
    out = dict(cfg)
    out["deep_gaze"] = "auto"
    return out


def _auto_or(cfg, key, default):
    """cfg[key], or `default` when it is missing or "auto".

    A new install's template writes the machine-specific settings as "auto"
    (spec 6.9). Until something resolves them from the machine's profile, the
    driver must run on its old code defaults - an "auto" reaching
    cv2.VideoCapture or int() would mean no camera, or no start at all.
    """
    v = cfg.get(key, default)
    return default if is_auto(v) else v


def _camera_request(cfg):
    """(index, width, height, fps) the camera is opened with."""
    return (_auto_or(cfg, "camera_index", 0),
            int(_auto_or(cfg, "camera_width", 1280)),
            int(_auto_or(cfg, "camera_height", 720)),
            int(_auto_or(cfg, "camera_fps", 60)))


def _stt_model_name(cfg):
    return str(_auto_or(cfg, "stt_model", "medium"))


def _aac_dir(cfg):
    """The AAC board folder: aac_dir, or the boards shipped with the program
    when it is empty or missing."""
    d = str(cfg.get("aac_dir", "") or "").strip()
    return d or paths.resource_path("aac")


# --- language-bound settings (i18n.setting: the language's default when the
# key says "auto", is missing, or - in another language than Turkish - still
# holds the Turkish factory value; with Turkish, exactly as before) ----------

def _tts_voice(cfg):
    """The voice hint the Speaker is built with."""
    return str(i18n.setting(cfg, "tts_voice", "tr"))


def _stt_language(cfg):
    """Whisper's dictation language."""
    return str(i18n.setting(cfg, "stt_language", "tr"))


def _aac_root(cfg):
    """The AAC main menu (board file name without .xml)."""
    return str(i18n.setting(cfg, "aac_root", "turkce-aac-paketi"))


def _voice_download_note(voice, speaker):
    """One console line (ASCII) when the language's piper voice is one the
    program downloads (the English one) and it is not here yet, so another
    voice speaks: said once, at start. None otherwise."""
    try:
        from eye_gaze import downloader, models_manifest
        pack = models_manifest.voice_pack(voice)
        if not pack or downloader.status(pack) == "ok":
            return None
        engine = getattr(speaker, "engine", "") if speaker is not None else ""
        if speaker is not None and getattr(speaker, "ok", False):
            return ("NOT: '%s' sesi henuz indirilmedi; simdilik bilgisayarin"
                    " kendi sesi konusuyor (%s). Indirmek icin: sihirbazin"
                    " Indirmeler adimi." % (voice, i18n.ascii_fold(engine)))
        return ("NOT: '%s' sesi henuz indirilmedi ve bu dilde baska ses de"
                " yok; seslendirme kapali, alarmda yalniz siren calar."
                " Indirmek icin: sihirbazin Indirmeler adimi." % voice)
    except Exception:
        return None


def _camera_opener(index, width, height, fps, machine_cam=None):
    """The function _ResilientCapture opens (and reopens) the camera with.

    A camera named in config.yaml (machine_cam None) opens EXACTLY as it
    always did: cv2.VideoCapture(index) with no backend argument, then the
    mode. That is the long-tested start (~1 s with MSMF hardware
    transforms off), and nothing about it may change.
    Only a camera found by the probe (makine.json, "auto" settings) goes
    through hardware.open_capture with the backend that worked in the probe
    (Windows MSMF, else DSHOW; Linux V4L2). GOZFARESI_FAKE_CAMERA (smokes
    only) replaces either.
    """
    if machine_cam is not None or os.environ.get(hardware.FAKE_CAMERA_ENV):
        backend = machine_cam.get("backend") if machine_cam else None

        def open_probed():
            return hardware.open_capture(index, width, height, fps,
                                         backend=backend)
        return open_probed

    def open_camera():
        c = cv2.VideoCapture(index)
        # Higher capture resolution = finer iris measurement = better accuracy.
        # Unsupported values silently fall back to the camera's own resolution.
        # Reopening after a dropout requests the SAME mode, so the features
        # the calibration was fitted on keep their scale.
        if c.isOpened():
            if width > 0 and height > 0:
                c.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                c.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            if fps > 0:
                c.set(cv2.CAP_PROP_FPS, fps)
        return c
    return open_camera


class _ResilientCapture:
    """cv2.VideoCapture that reconnects instead of dying.

    The loop used to exit after ~100 failed reads: sleep/resume, a USB
    hiccup or a video call grabbing the webcam ended the driver, and nobody
    was there to start it again - no cursor, no way to ask for help. Now a
    dead stream is released and reopened every `retry_every` seconds for as
    long as it takes; read() simply reports no frame meanwhile.
    """

    def __init__(self, opener, clock=time.monotonic, fail_limit=60, retry_every=2.0):
        self._opener = opener
        self._clock = clock
        self._fail_limit = int(fail_limit)
        self._retry_every = float(retry_every)
        self._fails = 0
        self._next_try = 0.0
        self._lost = False
        self.cap = self._try_open()

    def _try_open(self):
        try:
            cap = self._opener()
        except Exception:
            return None
        try:
            if cap is not None and cap.isOpened():
                return cap
            if cap is not None:
                cap.release()
        except Exception:
            pass
        return None

    def isOpened(self):
        return self.cap is not None

    def read(self):
        if self.cap is not None:
            try:
                ok, frame = self.cap.read()
            except Exception:
                ok, frame = False, None
            if ok:
                if self._lost:
                    self._lost = False
                    print("KAMERA yeniden baglandi; surucu devam ediyor.")
                self._fails = 0
                return True, frame
            self._fails += 1
            if self._fails < self._fail_limit:
                return False, None
        # Dead (or never opened): reconnect, but not on every frame - and not
        # at once either: a device coming back from sleep needs a moment.
        now = self._clock()
        if not self._lost:
            self._lost = True
            self._next_try = now + self._retry_every
            print("KAMERA goruntusu kesildi - yeniden baglaniliyor (surucu"
                  " KAPANMIYOR; kamerayi kullanan baska program varsa kapatin).")
        if now < self._next_try:
            return False, None
        self._next_try = now + self._retry_every
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = self._try_open()
        self._fails = 0
        if self.cap is None:
            return False, None
        return self.read()

    def get(self, prop):
        try:
            return self.cap.get(prop) if self.cap is not None else 0.0
        except Exception:
            return 0.0

    def release(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass


def _component_median(feats):
    return tuple(statistics.median(c) for c in zip(*feats))


def _collect_gaze(estimator, cap, hotkeys, samples, max_attempts=900, topmost_title=None):
    acc = []
    attempts = 0
    while len(acc) < samples and attempts < max_attempts:
        attempts += 1
        if topmost_title and attempts % 10 == 1:
            _force_topmost(topmost_title)
        ok, frame = cap.read()
        hotkeys.feed_window_key(cv2.waitKey(1) & 0xFF)
        if hotkeys.quit_requested:
            return None
        if not ok:
            continue
        frame = cv2.flip(frame, 1)
        g = estimator.estimate(frame)
        if g is not None:
            acc.append(g)
    if len(acc) < max(3, samples // 3):
        return None
    return _component_median(acc)


def calibrate(estimator, cap, sw, sh, positions, hotkeys, samples, reach_margin,
              poly2=False, passes=1, feat=None):
    win = t_ascii("driver.cal.window")
    _fullscreen(win)
    gaze_pts, screen_pts = [], []
    stamps = []  # capture time per point, kept with the samples (caldata)
    pass_of = []  # pass index per point: the model is validated across passes
    # Repeating the grid in a slightly different posture gives the head
    # compensation features real variance to learn from - a single-posture
    # calibration leaves them near-constant and unlearnable.
    seq = []
    for p in range(max(1, int(passes))):
        seq.extend((p, fxp, fyp) for (fxp, fyp) in positions)
    total = len(positions)
    last_pass = -1
    try:
        for i0, (pass_i, fxp, fyp) in enumerate(seq):
            i = (i0 % total) + 1
            if pass_i != last_pass and pass_i > 0:
                if not _pass_change_screen(win, sw, sh, hotkeys, pass_i, passes):
                    return None
            last_pass = pass_i
            canvas = np.zeros((sh, sw, 3), dtype=np.uint8)
            px, py = int(fxp * sw), int(fyp * sh)
            cv2.circle(canvas, (px, py), 22, (0, 255, 0), -1)
            cv2.circle(canvas, (px, py), 6, (0, 0, 0), -1)
            cv2.putText(canvas, t_ascii("driver.cal.point", i=i, total=total),
                        (max(10, px - 150), max(45, py - 45)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            cv2.imshow(win, canvas)
            _force_topmost(win)
            for _ in range(11):
                hotkeys.feed_window_key(cv2.waitKey(100) & 0xFF)
                if hotkeys.quit_requested:
                    return None
            _force_topmost(win)
            g = _collect_gaze(estimator, cap, hotkeys, samples, topmost_title=win)
            if hotkeys.quit_requested:
                return None
            if g is not None:
                gaze_pts.append(g)
                screen_pts.append((px, py))
                stamps.append(time.time())  # wall time: kept in the samples file
                pass_of.append(pass_i)
    finally:
        cv2.destroyWindow(win)
    if len(gaze_pts) < 6:
        return None
    opts = {"poly2": bool(poly2), "reach": float(reach_margin), "latency": None}
    cs = caldata.CalSamples.dots(gaze_pts, screen_pts, stamps, opts,
                                 groups=pass_of if int(passes) > 1 else None)
    # Same fit as a refit from the saved samples: per-axis model selection,
    # graded on the pass (or block) it did not see. Never raises.
    r = caldata.refit(cs, reach_margin, poly2, None, feat) if cs else None
    if r is not None:
        mapper, err = r.mapper, r.err
    else:
        mapper, err = fit_with_rejection(gaze_pts, screen_pts,
                                         reach_margin=reach_margin, poly2=poly2)
        if mapper is None:
            return None
        mapper.err_cv = holdout_error(gaze_pts, screen_pts,
                                      reach_margin=reach_margin, poly2=poly2)
        mapper.cal_samples = cs
    _stamp_setup(mapper, sw, sh, cap)
    _print_cal_quality(err, mapper.err_cv)
    _print_model(mapper)
    return mapper


def _dot_passes(n_points, passes, n_inputs):
    """Passes needed for a dots calibration to be fittable at all.

    The affine fit needs n_inputs + 1 points; with 11 features the config's
    own "9 = eski hizli mod" + "calibration_passes: 1" gave 9 - every attempt
    failed, and _calibrate_until_done retried it forever with no reason given.
    A few spare points cover dots lost to a blink or a turned head.
    """
    need = n_inputs + 4
    if n_points <= 0 or n_points * passes >= need:
        return passes
    fixed = -(-need // n_points)
    print("UYARI: %d nokta x %d tur, %d ozellikli kalibrasyon icin yetersiz;"
          " %d tur gosterilecek." % (n_points, passes, n_inputs, fixed))
    return fixed


def _camera_size(cap):
    try:
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return (w, h) if w > 0 and h > 0 else None
    except Exception:
        return None


def _stamp_setup(mapper, sw, sh, cap):
    """Record the screen and camera size this calibration was made on."""
    mapper.screen = (int(sw), int(sh))
    mapper.camera = _camera_size(cap)


def _fit_setup(mapper, sw, sh, cam, say=print):
    """The loaded calibration made fit for today's screen; never raises.

    A caregiver changing Windows scaling or resolution (or plugging in a TV)
    left the cursor predicting in the old pixel space - part of the screen
    unreachable, and nothing said. Now the screen is rescaled exactly and
    announced; a different camera image size cannot be rescaled (the iris and
    head features move with it), so that is announced as a warning."""
    if mapper is None:
        return None
    try:
        old_cam = getattr(mapper, "camera", None)
        if old_cam and cam and tuple(old_cam) != tuple(cam):
            say("DIKKAT: kalibrasyon %dx%d kamera goruntusuyle alinmis, su an"
                " %dx%d. Imlec kayabilir; kayarsa F7 x2, olmazsa F9 x2."
                % (old_cam[0], old_cam[1], cam[0], cam[1]))
        old = getattr(mapper, "screen", None)
        m = mapper.rescaled(sw, sh)
        if m is not mapper:
            say("Ekran boyutu degismis (%dx%d -> %dx%d): kalibrasyon yeni"
                " boyuta OLCEKLENDI - yeniden kalibrasyon gerekmedi."
                % (old[0], old[1], sw, sh))
        return m
    except Exception:
        return mapper


def _calibrate_until_done(run, hotkeys, wait, pause=5.0):
    """Run the first calibration until it succeeds or the user quits.

    A failed first calibration (face lost, bad light, the user resting) used
    to end the program - and with it any way for the user to try again.
    """
    while True:
        m = run()
        if m is not None or hotkeys.quit_requested:
            return m
        print("Kalibrasyon tamamlanamadi - %d sn sonra TEKRAR baslayacak"
              " (vazgecmek icin cikis tusuna iki kez basin)." % int(pause))
        wait(pause)
        if hotkeys.quit_requested:
            return None


_CV_GOOD, _CV_BAD = 100.0, 200.0     # px, on held-out points


def _cal_label(err_cv):
    return ("IYI" if err_cv < _CV_GOOD else
            "ORTA" if err_cv < _CV_BAD else "KOTU")


def _print_cal_quality(err, err_cv=None):
    """The label is graded on HELD-OUT points. The in-sample residual it used
    to be graded on flatters (simulated 73 px reported vs 141 px real), and
    the field log agreed: F7 corrections of 300-970 px after 'ORTA'."""
    if err_cv is None:
        quality = "IYI" if err < 80 else ("ORTA" if err < 160 else "KOTU")
        print(f"Kalibrasyon ortalama hatasi: {err:.0f} px [{quality}]"
              + ("  -> tekrar kalibre etmeniz onerilir" if quality == "KOTU" else ""))
        return
    quality = _cal_label(err_cv)
    print(f"Kalibrasyon hatasi: {err_cv:.0f} px [{quality}] (gormedigi noktalarda"
          f" olculdu; egitim noktalarinda {err:.0f} px)"
          + ("  -> tekrar kalibre etmeniz onerilir" if quality == "KOTU" else ""))


def _print_model(mapper):
    """The per-axis line under the grade (inputs, error, screen coverage)."""
    line = caldata.describe(getattr(mapper, "selection", None))
    if line:
        print(line)


def _pass_change_screen(win, sw, sh, hotkeys, pass_i, total_passes):
    """Between calibration passes: prompt the user to shift posture slightly."""
    msg = np.zeros((sh, sw, 3), dtype=np.uint8)
    cv2.putText(msg, t_ascii("driver.cal.pass_title", n=pass_i + 1,
                             total=total_passes),
                (sw // 2 - 350, sh // 2 - 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 2)
    cv2.putText(msg, t_ascii("driver.cal.pass_hint"),
                (sw // 2 - 380, sh // 2 + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.imshow(win, msg)
    _force_topmost(win)
    for _ in range(30):
        hotkeys.feed_window_key(cv2.waitKey(100) & 0xFF)
        if hotkeys.quit_requested:
            return False
    return True


_PURSUIT_GIVE_UP = 8      # s into a pass ...
_PURSUIT_MIN_EARLY = 15   # ... with fewer usable frames than this: stop


def calibrate_pursuit(source, cap, sw, sh, hotkeys, reach_margin, poly2=False,
                      passes=1, speed=480.0, latency="auto", closed_thr=0.18,
                      feat=None):
    """Smooth-pursuit calibration: the user follows a gliding ball; every
    frame contributes a (gaze, ball position) pair. The user's eye lag is
    LEARNED from the data (latency='auto': several candidate lags are fitted
    and the best kept) or fixed to a number. Frames near direction changes
    (saccades) or with closed eyes are dropped."""
    win = t_ascii("driver.pursuit.window")
    _fullscreen(win)
    canvas = np.zeros((sh, sw, 3), dtype=np.uint8)
    samples = []  # (gaze_features, t) - latency pairing happens at fit time
    pass_of = []  # pass index per sample: the model is validated across passes
    path = PursuitPath(sw, sh, speed)
    try:
        for p in range(max(1, int(passes))):
            if p and not _pass_change_screen(win, sw, sh, hotkeys, p, passes):
                return None
            # Monotonic: a clock step (time sync after resume) mid-pass would
            # pair gaze with the wrong ball positions.
            t0 = time.monotonic() + 2.0  # 2 sn hazirlik: top basta bekler
            loop_i = 0
            got = 0
            while True:
                loop_i += 1
                t = time.monotonic() - t0
                if t >= path.duration + 0.2:
                    break
                if t >= _PURSUIT_GIVE_UP and got < _PURSUIT_MIN_EARLY:
                    # No face / no camera: following the ball for the rest of
                    # both passes (~45 s, physically taxing) cannot succeed.
                    print("Kalibrasyon durduruldu: yuz/kamera %d sn boyunca"
                          " neredeyse hic gorulmedi (%d ornek). Kameraya"
                          " donun, isigi kontrol edin." % (_PURSUIT_GIVE_UP, got))
                    return None
                x, y = path.pos(t)
                canvas[:] = 0
                cv2.circle(canvas, (int(x), int(y)), 26, (0, 255, 0), -1)
                cv2.circle(canvas, (int(x), int(y)), 7, (0, 0, 0), -1)
                pct = int(100 * max(0.0, min(t, path.duration)) / path.duration)
                cv2.putText(canvas, t_ascii("driver.pursuit.follow", pct=pct,
                                            n=p + 1, total=passes),
                            (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
                cv2.imshow(win, canvas)
                if loop_i % 15 == 1:
                    _force_topmost(win)
                hotkeys.feed_window_key(cv2.waitKey(1) & 0xFF)
                if hotkeys.quit_requested:
                    return None
                ok, frame = cap.read()
                if not ok:
                    continue
                frame = cv2.flip(frame, 1)
                g = source.estimate(frame)
                if g is not None and source.openness > closed_thr and t >= 0.0:
                    samples.append((g, t))
                    pass_of.append(p)
                    got += 1
    finally:
        cv2.destroyWindow(win)
    if len(samples) < 80:
        print(f"Takip kalibrasyonu icin yeterli ornek toplanamadi ({len(samples)}).")
        return None
    opts = {"poly2": bool(poly2), "reach": float(reach_margin), "latency": latency}
    # Same fit as a refit from the saved samples: per-axis model selection,
    # the eye lag chosen on the pass it did not see. Never raises.
    cs = caldata.CalSamples.pursuit(samples, path, 0.0, opts, groups=pass_of)
    r = caldata.refit(cs, reach_margin, poly2, latency, feat) if cs else None
    if r is not None:
        mapper, err, lat = r.mapper, r.err, r.latency
    else:
        if isinstance(latency, str) and latency.strip().lower() == "auto":
            mapper, err, lat = best_latency_fit(samples, path,
                                                reach_margin=reach_margin,
                                                poly2=poly2)
        else:
            lat = float(latency)
            mapper, err, lat = best_latency_fit(samples, path,
                                                reach_margin=reach_margin,
                                                poly2=poly2, candidates=(lat,))
        if mapper is None:
            return None
        g_pts, s_pts = pursuit_pairs(samples, path, lat)
        mapper.err_cv = holdout_error(g_pts, s_pts, reach_margin=reach_margin,
                                      poly2=poly2, blocks=10)
        mapper.cal_samples = caldata.CalSamples.pursuit(samples, path, lat, opts,
                                                        groups=pass_of)
    print(f"Takip kalibrasyonu: {len(samples)} ornek; ogrenilen goz gecikmesi: {lat:.2f} sn.")
    _stamp_setup(mapper, sw, sh, cap)
    _print_cal_quality(err, mapper.err_cv)
    _print_model(mapper)
    return mapper


def measure_drift(estimator, cap, sw, sh, hotkeys, samples, mapper):
    """One-point drift correction: the user fixates a single center dot for
    ~2 seconds; the offset between where the mapper thinks they look and the
    dot is returned. Much faster than a full recalibration and fixes the
    gradual accuracy drift that accumulates within a session."""
    win = t_ascii("driver.drift.window")
    _fullscreen(win)
    try:
        canvas = np.zeros((sh, sw, 3), dtype=np.uint8)
        cx, cy = sw // 2, sh // 2
        cv2.circle(canvas, (cx, cy), 22, (0, 255, 255), -1)
        cv2.circle(canvas, (cx, cy), 6, (0, 0, 0), -1)
        cv2.putText(canvas, t_ascii("driver.drift.look"), (cx - 170, cy - 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.imshow(win, canvas)
        _force_topmost(win)
        for _ in range(8):
            hotkeys.feed_window_key(cv2.waitKey(100) & 0xFF)
            if hotkeys.quit_requested:
                return None
        g = _collect_gaze(estimator, cap, hotkeys, samples, topmost_title=win)
        if g is None:
            print("Hizli duzeltme: yuz/goz yakalanamadi (isik? kamera onunde engel?).")
            return None
        px, py = mapper.predict(g)
        off = (cx - px, cy - py)
        # Large offsets are usually REAL (the user leaned / posture changed
        # since calibration), so clamp instead of rejecting - an applied
        # correction beats a discarded one.
        max_x, max_y = 0.6 * sw, 0.6 * sh
        off = (min(max(off[0], -max_x), max_x), min(max(off[1], -max_y), max_y))
        if abs(off[0]) >= max_x or abs(off[1]) >= max_y:
            print("Hizli duzeltme sinirda kirpildi; mumkun oldugunda F9 x2 ile "
                  "gunluk oturusunuzda tam kalibrasyon yapin.")
        return off
    finally:
        cv2.destroyWindow(win)


# --- calibration files -----------------------------------------------------
#
# calibration.json is the most expensive thing this system produces: it costs a
# two-pass pursuit calibration, which is physically taxing for this user. It
# must therefore survive every route by which the driver might decide the file
# "does not fit" - and the feature count it is keyed on can change WITHOUT the
# user touching anything (deep_gaze: "auto" silently drops 2 features the day
# the GPU is busy, updating or gone).
#
# So backups are keyed BY FEATURE COUNT - calibration.json.11.bak next to
# calibration.json.9.bak - rather than by time. That is what makes the loss
# non-destructive instead of merely reversible:
#   * a 9-input save can never evict the 11-input backup, so the GPU-gone /
#     GPU-back ping-pong (recalibrate, overwrite, recalibrate again) cannot
#     grind the tuned file away, however many times it repeats;
#   * the slot is self-describing, so _restore_calibration can find the right
#     file on its own and the user does not have to recalibrate at all.
# One previous generation is kept per slot (.bak.onceki), so a bad
# recalibration done twice in a row with the SAME settings is still recoverable
# - and so an interrupted rotation cannot hide the slot. Restore tries it too.


def _calibration_backup(path, n_inputs):
    """Backup slot for a calibration with n_inputs raw features.

    Falls back to a single unnumbered slot when the count is unknown (an
    unparseable file), so garbage never occupies - and never evicts - a real
    feature-count slot.
    """
    return "%s.%d.bak" % (path, int(n_inputs)) if n_inputs else path + ".bak"


def _best_slot(path, n_inputs):
    """The calibration that is never rotated away: per feature count, the
    best graded one ever saved. Two .bak generations meant three mediocre
    recalibrations in a row (150, 170, 190 px - each accepted, none KOTU)
    pushed a 60 px calibration off the disk for good."""
    return "%s.%d.en-iyi" % (path, int(n_inputs))


def _keep_best(path, data):
    """Copy the calibration bytes just saved into the best slot when they
    are better. Graded beats graded by err_cv; an ungraded file (everything
    from before err_cv) only fills an EMPTY slot and gives way to a clearly
    good (IYI) graded one. Never raises."""
    try:
        d = json.loads(data.decode("utf-8"))
        n = len(d["lo"])
        new = d.get("err_cv")
        best = _best_slot(path, n)
        _, _, old_d = _read_calibration(best)
        if old_d is not None:
            old = old_d.get("err_cv")
            if not isinstance(new, (int, float)):
                return
            if isinstance(old, (int, float)):
                if new >= old:
                    return
            elif new >= _CV_GOOD:
                return
        _atomic_write_bytes(best, data)
    except Exception:
        pass


def _same_mapper(a, b):
    try:
        da, db = a.to_dict(), b.to_dict()
        return (np.allclose(da["cx"], db["cx"]) and np.allclose(da["cy"], db["cy"])
                and np.allclose(da["lo"], db["lo"]) and np.allclose(da["hi"], db["hi"]))
    except Exception:
        return False


def _undo_calibration(path, mapper, n_inputs, feat=None, screen=None):
    """(mapper, message) for "Kalibrasyonu geri al"; mapper None = nothing
    to go back to. Never raises.

    1. The calibration in use was NOT saved (refused as KOTU): go back to the
       one on disk, which is what the user had before it.
    2. Otherwise SWAP calibration.json with its backup slot - so choosing it
       again swaps back, and a mistaken choice costs nothing.
    """
    def fit(m):
        return m.rescaled(*screen) if (m is not None and screen) else m
    try:
        cur, _, _ = _read_calibration(path)
        cur = fit(cur)
        if (cur is not None and cur.n_inputs == n_inputs
                and mapper is not None and not _same_mapper(mapper, cur)):
            return cur, "Kayitli (onceki) kalibrasyona donuldu."
        slot = _calibration_backup(path, n_inputs)
        for bak in (slot, slot + ".onceki"):
            m, _, d = _read_calibration(bak)
            if m is None or m.n_inputs != n_inputs:
                continue
            stamp = (d or {}).get("feat")
            if feat and stamp and stamp != feat:
                continue
            with open(bak, "rb") as f:
                prev = f.read()
            now_bytes = None
            try:
                with open(path, "rb") as f:
                    now_bytes = f.read()
            except Exception:
                pass
            if not _atomic_write_bytes(path, prev):
                return None, "Geri alinamadi: kalibrasyon dosyasi yazilamadi."
            if now_bytes:
                _atomic_write_bytes(bak, now_bytes)
            return fit(m), ("Onceki kalibrasyon geri yuklendi (tekrar"
                            " secerseniz yenisine donulur).")
        return None, "Geri alinacak onceki kalibrasyon yok."
    except Exception as exc:
        return None, "Geri alinamadi (%s)." % type(exc).__name__


def _feature_id(per_eye, use_head, use_tr, deep_on):
    """Which features a calibration was fitted for, as a short stamp.

    The feature COUNT is not a fingerprint: 9 inputs is reached both by
    per_eye+head+translation with no deep model AND by head+translation+deep
    with per-eye off, and 4 inputs by five different combinations. Nothing in
    the saved file recorded which one, so a backup taken under one set could be
    restored under another - feeding ETH-XGaze outputs into coefficients fitted
    for per-eye differences. The cursor still moves, plausibly and wrongly,
    while the driver announces in capitals that no recalibration is needed.
    That is worse than an honest "please recalibrate".

    Translation only counts when head pose is on, matching how base_inputs is
    computed, so the stamp describes the vector rather than the YAML.
    """
    return "pe%d-hp%d-ht%d-dg%d" % (bool(per_eye), bool(use_head),
                                    bool(use_head and use_tr), bool(deep_on))


def _read_calibration(path):
    """(mapper, problem, raw dict); problem is None on success.

    Never raises: every caller is on a path that must keep the driver alive.
    The raw dict comes back too because the feature stamp lives beside the
    coefficients and GazeMapper deliberately knows nothing about it.
    """
    if not path or not os.path.exists(path):
        return None, "dosya yok", None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as exc:
        return None, "okunamadi (%s)" % type(exc).__name__, None
    if not isinstance(d, dict) or d.get("v") != 2:
        return None, "eski algoritmayla alinmis", None
    try:
        m = GazeMapper.from_dict(d)
    except Exception as exc:
        return None, "bozuk (%s)" % type(exc).__name__, None
    # Travels with the mapper so a restore/self-heal re-save keeps the grade
    # _should_save() compares against.
    m.err_cv = d.get("err_cv")
    return m, None, d


def _write_flushed(path, data):
    """Write and fsync, or clean up after itself and report False."""
    try:
        with open(path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass
        return False


def _atomic_write_bytes(path, data):
    """Replace `path` with `data` in one step, or leave it exactly as it was.

    A plain open(path, "w") truncates first, so a full disk or a crash between
    truncate and write leaves a HALF calibration - destroying the file this
    whole module exists to protect, on the very operation meant to save it.
    """
    tmp = path + ".tmp"
    if not _write_flushed(tmp, data):
        return False
    # Defender, the search indexer or a sync client can hold the target for
    # a moment; one failed rename used to drop a just-paid-for calibration.
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return True
        except Exception:
            if attempt < 4:
                time.sleep(0.2)
    try:
        os.remove(tmp)
    except Exception:
        pass
    return False


def _stored_n_inputs(raw):
    """Feature count recorded in raw calibration JSON bytes, or None."""
    try:
        d = json.loads(raw.decode("utf-8"))
        if d.get("v") == 2 and isinstance(d.get("lo"), list):
            return len(d["lo"])
    except Exception:
        pass
    return None


def _backup_failed(bak):
    print("UYARI: eski kalibrasyonun YEDEGI ALINAMADI (%s)." % bak)
    print("  Yeni kalibrasyon yine de kaydediliyor, ama eskisi geri"
          " getirilemeyecek. Disk dolu ya da klasor yazma korumali olabilir.")
    return False


def _backup_calibration(path, old):
    """Preserve the bytes currently in `path` before they are overwritten."""
    bak = _calibration_backup(path, _stored_n_inputs(old))
    # Already preserved, byte for byte. Rotating anyway would spend the slot's
    # ONE generation of history on a no-op - and that is not a rare corner: a
    # GPU that flaps takes this path on every restart (restore 11 -> save -> the
    # 9-input file backed up unchanged, and back again), so after two restarts
    # both generations held the same copy and the guarantee .onceki exists for
    # was gone without the user doing anything at all.
    try:
        with open(bak, "rb") as f:
            if f.read() == old:
                return True
    except Exception:
        pass
    tmp = bak + ".tmp"
    # The replacement is written and flushed BEFORE the existing slot is moved
    # aside. Rotating first - which is what this did at review time - meant a
    # full disk left the SLOT ITSELF gone while a perfectly good copy sat in
    # .onceki, and _restore_calibration was only looking at the slot: the fix
    # for silent loss would have been a new way to lose the file silently.
    if not _write_flushed(tmp, old):
        return _backup_failed(bak)
    try:
        if os.path.exists(bak):
            os.replace(bak, bak + ".onceki")
    except Exception:
        pass  # one generation lost; the slot itself is written below
    try:
        os.replace(tmp, bak)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        return _backup_failed(bak)


def _should_save(path, err_cv, n_inputs=None, ref=None):
    """(ok, reason): may a fresh calibration replace the file at `path`?

    Everything used to be saved, so one bad recalibration (tired, poor light,
    a sneeze) silently became tomorrow's calibration while the good one sat
    in a backup nothing ever restores. Refused only in the clear case: the new
    one grades KOTU on held-out points AND the stored one was clearly better.

    A stored file WITHOUT a grade (every calibration saved before err_cv
    existed - including the one this user has been living on since July) is
    kept against a KOTU newcomer too, as long as it is still usable with
    today's feature count (`n_inputs`). Overwriting it was the old rule, and
    it meant the first tired F9 silently replaced the field-proven file.
    An unusable one (other feature count, unreadable) never blocks a save.

    `ref`: the stored calibration's error measured the SAME way as err_cv
    (a startup refit re-measures it), used instead of the grade in the file,
    which may come from an earlier, more lenient validation.
    """
    if err_cv is None or err_cv < _CV_BAD:
        return True, ""
    if isinstance(ref, (int, float)) and not isinstance(ref, bool):
        if err_cv <= 1.25 * ref:
            return True, ""
        return False, ("Yeni kalibrasyon KOTU (%.0f px); kayitli olan (%.0f px)"
                       " daha iyiydi - USTUNE YAZILMADI." % (err_cv, ref))
    m, _, d = _read_calibration(path)
    old = (d or {}).get("err_cv")
    if not isinstance(old, (int, float)):
        if (m is not None and n_inputs is not None
                and m.n_inputs == n_inputs):
            return False, ("Yeni kalibrasyon KOTU (%.0f px); kayitli (calisan)"
                           " kalibrasyonun USTUNE YAZILMADI." % err_cv)
        return True, ""
    if err_cv <= 1.25 * old:
        return True, ""
    return False, ("Yeni kalibrasyon KOTU (%.0f px); kayitli olan (%.0f px)"
                   " daha iyiydi - USTUNE YAZILMADI." % (err_cv, old))


def _save_calibration(path, mapper, feat=None, err_cv=None):
    """Write the calibration, keeping the previous one in its feature slot.

    `feat` is the feature stamp (_feature_id) this mapper was fitted for. It is
    omitted rather than guessed when the caller does not know - a restored
    pre-stamp backup must not come back out of here looking like a verified
    match for today's settings.

    The overwrite is deliberately NOT refused when the backup fails. The mapper
    in hand was just paid for with a full pursuit calibration; refusing to
    persist it guarantees the user pays that price again next session, whereas
    the file being replaced is - in the case this guard exists for - about to
    be recoverable from its own count slot anyway. Failure is announced instead.
    """
    try:
        d = mapper.to_dict()
        if feat:
            d["feat"] = feat
        if err_cv is None:
            err_cv = getattr(mapper, "err_cv", None)
        if isinstance(err_cv, (int, float)):
            d["err_cv"] = int(round(err_cv))
        data = json.dumps(d).encode("utf-8")
    except Exception as exc:
        print("UYARI: kalibrasyon kaydedilemedi (%s)." % type(exc).__name__)
        return False
    old = None
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                old = f.read()
        except Exception:
            # Exists but unreadable - an antivirus or a sync client holding it
            # open. Skipping the backup SILENTLY here would be the same silent
            # loss this function exists to close, just one layer down.
            print("UYARI: mevcut kalibrasyon dosyasi okunamadi, YEDEGI"
                  " ALINAMIYOR (%s)." % path)
            print("  Uzerine yazilacak. Dosya baska bir program tarafindan"
                  " kilitli olabilir.")
    if old == data:
        caldata.save_beside(path, mapper, feat)  # never raises
        return True  # identical: nothing to preserve, nothing to write
    if old:
        _backup_calibration(path, old)
        _keep_best(path, old)       # an ungraded field file fills an empty slot
    if _atomic_write_bytes(path, data):
        # Raw samples beside it, ONLY once the calibration itself is on disk.
        caldata.save_beside(path, mapper, feat)  # never raises
        _keep_best(path, data)                   # never raises
        return True
    print("UYARI: kalibrasyon KAYDEDILEMEDI (%s)." % path)
    print("  Bu oturumda calisir, ama program kapaninca kaybolur ve yeniden"
          " kalibrasyon gerekir.")
    return False


_FEATURE_NAMES = {"pe": "per_eye_features", "hp": "use_head_pose",
                  "ht": "head_translation", "dg": "derin bakis modeli"}


def _feature_diff(stored_feat, feat):
    """Which settings differ between two stamps, named as in config.yaml.

    Empty when either stamp is missing (every calibration in the field predates
    them) or when they are equal, so callers can fall back to the heuristic
    without asking twice.
    """
    if not stored_feat or not feat or stored_feat == feat:
        return []
    def parse(s):
        out = {}
        for part in str(s).split("-"):
            if len(part) >= 3:
                out[part[:2]] = part[2:] == "1"
        return out
    a, b = parse(stored_feat), parse(feat)
    return ["%s (kalibrasyonda %s, su an %s)"
            % (_FEATURE_NAMES.get(k, k), _onoff(a[k]), _onoff(b[k]))
            for k in ("pe", "hp", "ht", "dg")
            if k in a and k in b and a[k] != b[k]]


def _mismatch_report(stored, current, deep_on, note="", stored_feat=None,
                     feat=None):
    """Why a saved calibration was rejected, in the user's language.

    Naming the actual numbers matters: "farkli bir use_head_pose ayariyla
    alinmis" blamed a setting the user had usually never touched, and gave no
    way to tell an edited config from a GPU that vanished overnight.

    When both files carry a feature stamp the cause is not guessed at all - the
    exact settings that moved are named. The heuristic below is what remains
    for files saved before the stamp existed.
    """
    lines = ["DIKKAT: kayitli kalibrasyon SU ANKI AYARLARLA UYUSMUYOR"
             " (kayitli: %s ozellik, su an: %s ozellik)." % (stored, current)]
    diff = _feature_diff(stored_feat, feat)
    if diff:
        lines.append("  SEBEP: su ayar(lar) degismis: " + ", ".join(diff) + ".")
        if note:
            lines.append("  Su anki ayarlar: %s" % note)
        return lines
    # deep_gaze: "auto" is the only one of the four that can change on its own -
    # a GPU driver update, another program holding the VRAM, a transient CUDA
    # failure. The other three only move when someone edits config.yaml.
    if stored == current + 2 and not deep_on:
        lines.append("  EN OLASI SEBEP: derin bakis modeli kalibrasyon"
                     " sirasinda ACIKTI, bu oturumda ACILAMADI (GPU yok,"
                     " mesgul ya da surucusu degismis olabilir).")
    elif stored == current - 2 and deep_on:
        lines.append("  EN OLASI SEBEP: derin bakis modeli bu oturumda ACILDI,"
                     " kalibrasyon sirasinda KAPALIYDI.")
    else:
        lines.append("  EN OLASI SEBEP: config.yaml icinde ozellik sayisini"
                     " degistiren bir ayar degismis: per_eye_features,"
                     " use_head_pose, head_translation ya da deep_gaze.")
    if note:
        lines.append("  Su anki ayarlar: %s" % note)
    return lines


def _restore_calibration(path, n_inputs, feat=None, quiet=False):
    """Last chance before forcing a recalibration: the matching backup slot.

    The previous generation is tried too. It is not only there for a bad
    recalibration done twice: it is also what makes an interrupted rotation
    survivable, so no single failed write can hide a good calibration.

    `quiet` suppresses the "no backup either" line for the case where nothing
    was wrong in the first place - a first ever run has no calibration and no
    backup, and must not be greeted with a warning about it.
    """
    slot = _calibration_backup(path, n_inputs)
    other = False
    for bak in (slot, slot + ".onceki", _best_slot(path, n_inputs)):
        m, _, d = _read_calibration(bak)
        if m is None or m.n_inputs != n_inputs:
            continue
        stamp = (d or {}).get("feat")
        if feat and stamp and stamp != feat:
            other = True
            continue
        print("  Bu ayarlara UYAN yedek bulundu: %s" % bak)
        if stamp or not feat:
            print("  Yedek geri yuklendi - YENIDEN KALIBRASYON GEREKMIYOR.")
        else:
            # A backup taken before the stamp existed - which every calibration
            # already in the field is. Restoring still beats charging the user a
            # full pursuit calibration, but the count alone cannot prove it was
            # fitted for these features, and the message may not claim it was.
            print("  Yedek geri yuklendi, ama hangi ayarlarla alindigi kayitli"
                  " degil (bu surumden onceki bir yedek).")
            print("  Imlec belirgin sekilde kayiyorsa '%s' tusuna iki kez"
                  " basip yeniden kalibre edin." % "F9")
        # Self-heal so this is announced once rather than every startup. The
        # backup is untouched by the write, so a failure here costs only the
        # message repeating next time. The stamp is carried over as it was
        # found: writing today's stamp onto an unverified file would turn a
        # guess into a recorded certainty.
        _save_calibration(path, m, feat=stamp)
        return m
    if other:
        print("  Ayni ozellik sayisinda bir yedek var, ama baska ayarlarla"
              " alinmis; KULLANILMADI.")
        print("  (Ayni sayida ozellik farkli ayarlardan da cikabilir; yanlis"
              " yerde duran bir imlec yeniden kalibrasyondan daha kotudur.)")
    if not quiet:
        print("  Bu ayarlara UYAN kalibrasyon yedegi de yok; yeniden"
              " kalibrasyon yapilacak.")
    return None


def _load_calibration(path, n_inputs, deep_on=False, note="", feat=None):
    m, problem, d = _read_calibration(path)
    if problem == "dosya yok":
        # A DELETED calibration.json is the one case where the backup is
        # CERTAINLY still valid - nothing about the settings changed - and it
        # was the one case that never reached it. Quietly, because "no file and
        # no backup" is also what a first ever run looks like.
        return _restore_calibration(path, n_inputs, feat, quiet=True)
    if problem is not None:
        print("Kayitli kalibrasyon kullanilamadi (%s)." % problem)
        return _restore_calibration(path, n_inputs, feat)
    if m.n_inputs != n_inputs:
        for line in _mismatch_report(m.n_inputs, n_inputs, deep_on, note,
                                     (d or {}).get("feat"), feat):
            print(line)
        return _restore_calibration(path, n_inputs, feat)
    stamp = (d or {}).get("feat")
    if feat and stamp and stamp != feat:
        # Same count, different features. Unstamped files (everything saved
        # before this version) fall through and are accepted on the count as
        # before: refusing them would charge a full pursuit calibration for an
        # upgrade the user did not ask for.
        print("DIKKAT: kayitli kalibrasyon BASKA AYARLARLA alinmis (kayitli:"
              " %s, su an: %s) - ozellik sayisi ayni oldugu icin fark"
              " yalnizca bu kayittan anlasiliyor." % (stamp, feat))
        if note:
            print("  Su anki ayarlar: %s" % note)
        return _restore_calibration(path, n_inputs, feat)
    return m


class _FeatureSource:
    """Combines the geometric estimator with the optional deep model into a
    single feature vector; the ridge calibration learns the weighting."""

    # Consecutive failing frames (~1 s) before the deep model is rebuilt on
    # the CPU. A GPU reset (TDR, a driver update under Windows Update) makes
    # every later CUDA call fail: each frame returned None and the cursor
    # stayed frozen for the rest of the session.
    CPU_FALLBACK_AFTER = 30

    def __init__(self, estimator, deep=None, rebuild_on_cpu=None):
        self._e = estimator
        self._d = deep
        self._rebuild = rebuild_on_cpu
        self.deep_errors = 0
        self._streak = 0

    @property
    def openness(self):
        return self._e.openness

    def estimate(self, frame):
        g = self._e.estimate(frame)
        if g is None:
            return None
        if self._d is not None:
            lm = getattr(self._e, "landmarks", None)
            try:
                try:
                    d = (self._d.estimate_from_landmarks(frame, lm)
                         if lm is not None else self._d.estimate(frame))
                except Exception:
                    d = self._d.estimate(frame)
            except Exception as exc:
                # The fallback raising (a transient CUDA fault) used to escape
                # out of calibrate_pursuit and end the driver mid-calibration.
                # One dropped frame is the right price; say so only once.
                self.deep_errors += 1
                self._streak += 1
                if self.deep_errors == 1:
                    print("UYARI: derin bakis modeli hata verdi (" + ascii(exc)[:80]
                          + "); o kareler atlaniyor.")
                if self._streak == self.CPU_FALLBACK_AFTER:
                    self._fall_back_to_cpu()
                return None
            self._streak = 0
            if d is None:
                return None
            g = tuple(g) + d
        # One NaN/inf feature poisons everything downstream: the 1-Euro state
        # stays NaN for the rest of the session (cursor frozen), and a single
        # NaN calibration sample turns the whole ridge fit into NaN.
        if not all(math.isfinite(v) for v in g):
            return None
        return g

    def _fall_back_to_cpu(self):
        """Same model, same features (the calibration stays valid) - only
        slower. Never raises."""
        if self._rebuild is None or getattr(self._d, "device", "cpu") == "cpu":
            print("UYARI: derin bakis modeli SUREKLI hata veriyor; imlec"
                  " durmus olabilir. Programi yeniden baslatin.")
            return
        try:
            new = self._rebuild()
        except Exception as exc:
            print("UYARI: GPU hata veriyor ve derin model CPU'da da acilamadi"
                  " (" + ascii(exc)[:80] + "). Programi yeniden baslatin.")
            return
        old, self._d, self._streak = self._d, new, 0
        try:
            old.close()
        except Exception:
            pass
        print("UYARI: GPU hata verdi - derin bakis modeli CPU'ya alindi"
              " (imlec calisir ama daha yavas). Bilgisayari yeniden baslatmak"
              " GPU'yu geri getirir.")

    def close(self):
        self._e.close()
        if self._d is not None:
            self._d.close()


_DEEP_OFF = ("false", "0", "off", "none", "")


def _deep_mode(cfg):
    return str(cfg.get("deep_gaze", False)).strip().lower()


def _deep_expected(cfg, cal_path, base_inputs):
    """Was the deep model supposed to be running this session?

    deep_gaze: "auto" on a machine that never had a GPU is a normal, intended
    state and must not shout. On a machine that HAS been using the deep model
    it is a silent downgrade of both accuracy and calibration compatibility.
    The stored calibration's own feature count is the evidence: base_inputs + 2
    can only have been produced with the deep model running.

    Backup slots count as evidence too. After one GPU-less session the main
    file has already been rewritten with base_inputs features, and only
    calibration.json.<base+2>.bak still remembers that deep was ever in use.
    """
    mode = _deep_mode(cfg)
    if mode not in ("auto",):
        return True  # an explicit deep_gaze: true that did not load
    want = base_inputs + 2
    for p in (cal_path, _calibration_backup(cal_path, want)):
        m, _, _ = _read_calibration(p)
        if m is not None and m.n_inputs == want:
            return True
    return False


def _deep_report(cfg, deep, cal_path, base_inputs):
    """Startup lines about a deep model that is configured but not running."""
    if deep is not None or _deep_mode(cfg) in _DEEP_OFF:
        return []
    if not _deep_expected(cfg, cal_path, base_inputs):
        return ["Derin bakis modeli kapali (deep_gaze: auto, uygun GPU yok);"
                " geometrik mod ile devam ediliyor."]
    return [
        "DIKKAT: DERIN BAKIS MODELI BU OTURUMDA ACILAMADI.",
        "  Bu bilgisayarda daha once derin model KULLANILIYORDU (kayitli"
        " kalibrasyon %d ozellikle alinmis, su an %d ozellik var)."
        % (base_inputs + 2, base_inputs),
        "  Sonuc: isabet DUSER ve o kalibrasyon bu oturumda kullanilamaz.",
        "  Olasi sebep: NVIDIA ekran karti gorunmuyor - surucu guncellemesi,"
        " baska bir program VRAM'i tutuyor ya da gecici bir CUDA hatasi.",
        "  Eski kalibrasyonunuz YEDEKTE KORUNUYOR: GPU geri geldiginde"
        " kendiliginden geri yuklenir, yeniden kalibrasyon gerekmez.",
        "  Kalici olarak geometrik modda calisacaksaniz config.yaml icinde"
        " deep_gaze: false yapin.",
    ]


def _make_deep(cfg, cap, expected=False):
    """Create the deep gaze model if configured and available; never fatal.

    expected: _deep_expected() - the stored calibration was taken WITH the
    deep model. Then a missing or busy GPU (another program holding the VRAM)
    must not drop the model: that would make the calibration unusable and push
    the user into a tiring F9. The CPU gives the same features (measured
    identical), only slower - the same trade _FeatureSource makes mid-session.

    Backend (deep.select_backend): torch + ptgaze + CUDA keeps the torch path
    the development machine always ran; otherwise the ONNX model (packages have no
    torch). deep_backend: torch|onnx forces one. Both give the same features,
    so the choice never touches the calibration.
    """
    mode = _deep_mode(cfg)
    if mode in _DEEP_OFF:
        return None
    from eye_gaze import accel, deep as deep_mod
    want_backend = deep_mod.normalize_backend(cfg.get("deep_backend", "auto"))
    backend, gpu = deep_mod.select_backend(want_backend)
    if backend is None:
        if want_backend == "onnx":
            print("deep_backend: onnx ama ONNX derin modeli ya da onnxruntime"
                  " yok (resources/models/eth-xgaze-resnet18.onnx)."
                  " Geometrik mod ile devam.")
        else:
            print("deep_gaze acik ama derin model calistirilamiyor"
                  " (onnxruntime + ONNX modeli ya da PyTorch + ptgaze gerekir -"
                  " kaynak kurulumda KUR-DERIN.bat). Geometrik mod ile devam.")
        return None
    if mode == "auto" and not gpu and not expected:
        # Deep model on CPU is too slow for daily use. Returning None quietly
        # is right HERE - whether it deserves an alarm depends on evidence this
        # function does not have; _deep_report() decides and speaks.
        return None
    device_cfg = accel.normalize_want(cfg.get("deep_device", "auto"))
    # Whether the first attempt is on a GPU, i.e. whether a CPU retry is a
    # different attempt at all. ONNX already retries on the CPU inside
    # OnnxDeepGaze: a failure that gets out is the model file itself.
    on_gpu = backend == "torch" and (
        device_cfg == "gpu" or (device_cfg == "auto" and gpu))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    try:
        dg = deep_mod.make_deep_gaze(w, h, device_cfg, backend=want_backend)
    except Exception as e:
        if backend == "onnx":
            print("Derin model (ONNX) acilamadi - model dosyasi eksik ya da"
                  " bozuk olabilir (%s); geometrik mod ile devam." % ascii(e)[:120])
            return None
        if not on_gpu or not expected:
            print(f"Derin model yuklenemedi ({e}); geometrik mod ile devam.")
            return None
        print(f"Derin model GPU'da yuklenemedi ({e}); CPU deneniyor...")
        try:
            dg = deep_mod.make_deep_gaze(w, h, "cpu", backend="torch")
        except Exception as e2:
            print(f"Derin model CPU'da da yuklenemedi ({e2}); geometrik mod ile devam.")
            return None
    if (dg.device == "cpu" and mode == "auto" and not expected
            and device_cfg != "cpu"):
        # onnxruntime fell back to the CPU by itself (DirectML/CUDA listed but
        # no usable device): the same "too slow, not needed" case as above.
        try:
            dg.close()
        except Exception:
            pass
        return None
    if dg.device == "cpu" and expected and device_cfg != "cpu":
        # gpu (select_backend): a GPU was seen at all. Seen but not usable
        # (VRAM held by another program): "restart once it is free" is the
        # advice. None at all (deep_gaze: true on a GPU-less machine): that
        # advice would leave the user waiting for a GPU that never comes.
        print(t_ascii("driver.deep.cpu_gpu_failed" if gpu
                      else "driver.deep.cpu_no_gpu"))
    else:
        print(f"Derin bakis modeli ACIK (ETH-XGaze, cihaz: {dg.device}"
              f"{', ONNX' if getattr(dg, 'backend', '') == 'onnx' else ''}).")
    return dg


def _deep_cpu_rebuilder(deep, cap):
    """What _FeatureSource calls after 30 failing GPU frames: the same backend
    on the CPU (same features, the calibration stays valid)."""
    backend = getattr(deep, "backend", "torch")

    def rebuild():
        from eye_gaze import deep as deep_mod
        return deep_mod.make_deep_gaze(int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                       int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                                       "cpu", backend=backend)
    return rebuild


class _AacGrid:
    """Fullscreen phrase grid: look at a tile, press the click key, it speaks.

    Mirrors _ZoomClick's window handling (fullscreen + forced topmost) because
    that is what already survives OptiKey stealing the foreground. All of the
    decision-making lives in AacNavigator, which is tested without a screen;
    what is left here is the window and the clock.
    """

    def __init__(self, sw, sh, cfg, speaker, origin=(0, 0)):
        from eye_gaze.aac import AacNavigator, load_boards
        self._sw, self._sh = sw, sh
        # The grid fills the primary monitor; tiles are laid out from its
        # top-left, the cursor is in screen coordinates.
        self._ox, self._oy = int(origin[0]), int(origin[1])
        self._win = t_ascii("driver.aac.window")
        self._timeout = float(cfg.get("aac_timeout", 20))
        self._speaker = speaker
        self._cache = None
        self._base = None
        self._t0 = None
        self._open = False
        self._hover = None
        from eye_gaze.scan import scan_from_cfg
        self._scan = scan_from_cfg(cfg)     # aac_scan: tarama (None = bakis)
        aac_dir = _aac_dir(cfg)
        boards = load_boards(aac_dir)
        root = _aac_root(cfg)
        bundled = paths.resource_path("aac")
        if (root not in boards and i18n.language() != i18n.DEFAULT
                and i18n.is_default(cfg, "aac_root")
                and os.path.normcase(os.path.abspath(aac_dir))
                != os.path.normcase(os.path.abspath(bundled))):
            # The language's own pack (e.g. English) is not in the user's
            # folder (an OptiKey folder with the Turkish boards): the pack
            # that came with the program, rather than phrases of another
            # language read by this language's voice.
            shipped = load_boards(bundled)
            if root in shipped:
                print("AAC: '%s' klasorunde '%s' yok; programla gelen paket"
                      " kullaniliyor." % (i18n.ascii_fold(aac_dir), root))
                aac_dir, boards = bundled, shipped
        if root not in boards and boards:
            # Never silently: the caregiver named a board and got another one,
            # and every category tile they expect is now somewhere else.
            # The language's own main menu first (the folder may hold more
            # than one language's pack: the shipped one does), else the
            # first board as before.
            own = i18n.language_default("aac_root")
            pick = own if own in boards else sorted(boards)[0]
            print("AAC: ana menu '%s' bulunamadi; '%s' kullaniliyor"
                  " (aac_root ayarina bakin)." % (root, pick))
            root = pick
        self._nav = AacNavigator(boards, root)
        self.ready = bool(boards)
        if not self.ready:
            print("AAC: cumle dosyasi bulunamadi (%s); F5 izgarasi kapali."
                  % aac_dir)
            print("  config.yaml icinde aac_dir ayarini kontrol edin.")
            return
        # The boards this grid can reach: the shipped folder holds the
        # English pack beside the Turkish one, and counting it would turn
        # the Turkish "6 klavye" into 12.
        print("AAC izgarasi hazir: %d klavye, ana menu '%s'"
              % (len(self._nav.reachable()), root))
        if self._scan is not None:
            print("  TARAMA modu ACIK: satirlar sirayla yanar, tiklama tusu"
                  " secer (%.1f sn/adim, %s)." % (self._scan.seconds,
                                                  self._scan.mode))
        if speaker is None:
            # The Speaker is built before the grid and can come back as None -
            # no piper, no Tolga, nothing. The grid would still open, still
            # draw, still close on a selection, and say NOTHING. For someone
            # whose only way to speak this is, a silent success is the worst
            # outcome there is; startup_report() has already printed why.
            print("  DIKKAT: SES YOK - izgara acilir ama secilen cumle"
                  " SESLENDIRILEMEZ.")
            from eye_gaze.tts_backends import install_hint
            print("  Yukaridaki seslendirme satirlarina bakin; "
                  + ("KUR-SES.bat calistirmaniz gerekebilir."
                     if install_hint() == "KUR-SES.bat calistirin"
                     else install_hint() + "."))
        if bool(cfg.get("aac_cache", True)) and speaker is not None:
            from eye_gaze.phrase_cache import PhraseCache
            self._cache = PhraseCache(
                speaker, os.path.join(paths.ensure_dir(paths.cache_dir()),
                                      "aac"))
            self._cache.warm(self._nav.all_phrases())

    @property
    def active(self):
        return self._open

    def begin(self):
        if not self.ready or self._open:
            return
        self._nav.reset()
        _fullscreen(self._win)
        self._open = True
        self._t0 = time.monotonic()
        self._hover = None
        self._redraw()

    def _redraw(self):
        """Render the board itself. ~36 ms, so only on navigation."""
        if self._scan is not None:
            return self._scan_redraw()
        from eye_gaze.aac import draw_board
        self._base = draw_board(self._nav.tiles(), self._sw, self._sh,
                                self._nav.title())
        self._paint()

    def _paint(self, now=None):
        """Show the board with the pointer on top. Cheap: runs every frame."""
        if self._scan is not None:
            return self._scan_paint(now)
        from eye_gaze.aac import cell_at, overlay_pointer
        if self._base is None:
            return
        px, py = self._pointer()
        rects = self._nav.rects(self._sw, self._sh)
        idx = cell_at(px, py, rects)
        if now is not None and idx is not None and idx != self._hover:
            # Reading from tile to tile is activity: only a gaze parked on one
            # tile (or off the board) runs the clock down.
            if self._hover is not None:
                self._t0 = now
            self._hover = idx
        tiles = self._nav.tiles()
        locked = idx is not None and idx < len(tiles) and tiles[idx].locked
        img = overlay_pointer(self._base, rects, idx, px, py, locked)
        left = self._timeout - ((now if now is not None else time.monotonic())
                                - (self._t0 or 0))
        if 0 < left <= 5 and hasattr(cv2, "putText"):
            # The close used to come with no warning at all.
            cv2.putText(img, t_ascii("common.closing",
                                     n="%d" % math.ceil(left)),
                        (self._sw - 260, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                        (0, 0, 255), 3)
        cv2.imshow(self._win, img)
        _force_topmost(self._win)

    def tick(self, now):
        """True only on the frame the grid closed itself (timeout)."""
        if self._scan is not None:
            return self._scan_tick(now)
        if self._open:
            # Follow the pointer every frame. Without this the grid showed
            # nothing about where the user was aiming, so there was no way to
            # tell what the click key would say.
            try:
                self._paint(now)
            except Exception as exc:
                # Same rule as _scan_tick: a paint that throws every frame
                # left a frozen fullscreen topmost board over the desktop,
                # and the exception skipped the switch routing behind it.
                print("AAC izgara hatasi (" + ascii(exc)[:80] + "); izgara"
                      " kapatildi. Yeniden acmak icin AAC tusu.")
                self.cancel()
                return True
            if now - self._t0 > self._timeout:
                self.cancel()
                return True
        return False

    def confirm(self, dwell=False):
        """Speak (or navigate to) whatever tile the gaze cursor is on."""
        if self._scan is not None:
            return self._scan_confirm(dwell)
        if not self._open:
            return
        from eye_gaze.aac import cell_at
        px, py = self._pointer()
        idx = cell_at(px, py, self._nav.rects(self._sw, self._sh))
        if idx is None:
            return                      # a glance between tiles selects neither
        text = self._nav.choose(idx)
        self._t0 = time.monotonic()
        if text is None:
            self._redraw()
            return
        self.cancel()
        if self._cache is not None:
            self._cache.speak(text)
        elif self._speaker is not None:
            self._speaker.speak(text)
        else:
            print("AAC: cumle SESLENDIRILEMEDI (ses motoru yok): "
                  + ascii(text[:70]))
            return
        print("AAC: " + ascii(text[:70]))

    def _pointer(self):
        """The cursor in grid pixels."""
        x, y = cursor.get_cursor()
        return (x - self._ox, y - self._oy)

    def cancel(self):
        if self._open:
            try:
                cv2.destroyWindow(self._win)
            except Exception:
                pass
            self._open = False

    # --- switch scanning (aac_scan: true) ---------------------------------
    # For when the gaze no longer aims well enough: rows light up in turn and
    # the click switch picks one, then its tiles, then the tile. The decisions
    # live in eye_gaze.scan.Scanner; these methods are the window and clock.

    @property
    def scanning(self):
        return self._scan is not None

    def _scan_redraw(self):
        from eye_gaze.aac import draw_board
        from eye_gaze.scan import scan_title
        tiles = self._nav.tiles()
        self._base = draw_board(tiles, self._sw, self._sh,
                                scan_title(self._nav.title()))
        now = time.monotonic()
        self._scan.start(len(tiles), now)    # a new board scans from the top
        self._scan_paint(now)

    def _scan_paint(self, now=None):
        from eye_gaze.scan import overlay_scan
        if self._base is None:
            return
        now = time.monotonic() if now is None else now
        tiles = self._nav.tiles()
        locked = [i for i, t in enumerate(tiles) if t.locked]
        from eye_gaze.aac import back_label
        back = t_ascii("driver.aac.close") if self._nav.at_root else back_label()
        img = overlay_scan(self._base, self._nav.rects(self._sw, self._sh),
                           self._scan, back, locked)
        left = self._scan.seconds_left(now)
        if left is not None and 0 < left <= 5 and hasattr(cv2, "putText"):
            cv2.putText(img, t_ascii("common.closing",
                                     n="%d" % math.ceil(left)),
                        (20, self._sh - 30), cv2.FONT_HERSHEY_SIMPLEX, 1.2,
                        (0, 0, 255), 3)
        cv2.imshow(self._win, img)
        _force_topmost(self._win)

    def _scan_tick(self, now):
        """Advance the scan; True when the grid closed itself.

        A scan that throws closes the grid (and returns True, so the switch is
        muted as after a timeout) instead of leaving a frozen fullscreen board
        over the desktop that nothing can move on from.
        """
        if not self._open:
            return False
        try:
            self._scan_paint(now)
            closed = self._scan.tick(now)
        except Exception as exc:
            print("AAC tarama hatasi (" + ascii(exc)[:80] + "); izgara"
                  " kapatildi. F5 ile yeniden acilabilir.")
            closed = True
        if closed:
            self.cancel()
            return True
        return False

    def _scan_confirm(self, dwell):
        # Dwell turns a STILL gaze into a click, and watching the scan is
        # exactly that: in scan mode only the switch (or a blink) may select.
        if not self._open or dwell:
            return
        try:
            act = self._scan.press(time.monotonic())
        except Exception as exc:
            print("AAC tarama hatasi (" + ascii(exc)[:80] + "); izgara"
                  " kapatildi.")
            self.cancel()
            return
        if act is None:
            return
        kind, idx = act
        if kind == "back":
            if self._nav.at_root:
                self.cancel()
                print("AAC: izgara kapatildi (tarama KAPAT).")
                return
            self._nav.reset()
            self._redraw()
            return
        text = self._nav.choose(idx)
        if text is None:
            self._redraw()                  # category, page, GERI or locked
            return
        self.cancel()
        if self._cache is not None:
            self._cache.speak(text)
        elif self._speaker is not None:
            self._speaker.speak(text)
        else:
            print("AAC: cumle SESLENDIRILEMEDI (ses motoru yok): "
                  + ascii(text[:70]))
            return
        print("AAC: " + ascii(text[:70]))


def _zoom_unavailable():
    """Why this session can take no magnifier screenshot (ASCII), or None.

    Linux only (Wayland, no X display, mss missing). On Windows None: a GDI
    grab fails only for the moment (UAC prompt, locked session)."""
    if _platform.OS == "windows":
        return None
    ok, why = _platform.overlay_status("zoom_capture")
    return None if ok else _platform.ascii_text(why)


def _zoom_notice(zoom_key):
    """The start-up line saying the magnifier key cannot work here, or None.

    Said once at start, so that F6 doing nothing is never a silent mystery;
    each press then repeats the reason after its own failure line."""
    if not zoom_key:
        return None
    why = _zoom_unavailable()
    if why is None:
        return None
    return ("UYARI: buyutec (" + _platform.ascii_text(zoom_key).upper()
            + ") bu oturumda calismiyor: " + why)


class _ZoomClick:
    """In-driver magnifier: freeze-frame a region around the cursor, show it
    fullscreen, aim by gaze inside it, click maps back to the real point."""

    def __init__(self, sw, sh, scale=0.25, timeout=8.0, multi_monitor=False,
                 origin=(0, 0)):
        self._sw, self._sh = sw, sh
        # The primary monitor's top-left: the magnified view is shown there,
        # and one-monitor regions are cut from there (see _primary_rect).
        self._ox, self._oy = int(origin[0]), int(origin[1])
        # With multi_monitor the region is cut from the monitor UNDER the
        # cursor; it used to be clamped to the primary screen, so F6 on a
        # second monitor magnified the edge of the first one instead.
        self._multi = bool(multi_monitor)
        self._scale = min(max(float(scale), 0.10), 0.90)
        self._timeout = float(timeout)
        self._win = t_ascii("driver.zoom.window")
        self._region = None
        self._t0 = None
        self._pre = None
        # on_confirm(pre_cursor, target, region): the gaze error signal for
        # implicit correction. Called AFTER the click; its faults are dropped.
        self.on_confirm = None
        # ((raw gaze x, y), (placed cursor x, y)) from the gaze loop. With
        # snap-to-target the cursor sits on a button, not where the eye is;
        # the magnifier centres on the eye and teaches implicit correction
        # the EYE's error, not the snap offset.
        self.gaze_hint = None

    @property
    def active(self):
        return self._region is not None

    def begin(self):
        from eye_gaze.zoom import grab_region
        cx, cy = cursor.get_cursor()
        hint = self.gaze_hint
        if hint is not None:
            (gx, gy), (hx, hy) = hint
            # Only while the cursor is still where the gaze loop put it: a
            # hand-moved mouse is taken as it is.
            if abs(hx - cx) <= 2 and abs(hy - cy) <= 2:
                cx, cy = int(round(gx)), int(round(gy))
        mx, my, mw, mh = (cursor.monitor_rect(cx, cy) if self._multi
                          else (self._ox, self._oy, self._sw, self._sh))
        zw, zh = int(mw * self._scale), int(mh * self._scale)
        x0 = min(max(cx - zw // 2, mx), mx + mw - zw)
        y0 = min(max(cy - zh // 2, my), my + mh - zh)
        img = grab_region(x0, y0, zw, zh)
        if img is None:
            why = _zoom_unavailable()
            print("Buyutec: ekran goruntusu alinamadi."
                  + ("" if why is None else " " + why))
            return
        big = cv2.resize(img, (self._sw, self._sh), interpolation=cv2.INTER_LINEAR)
        _fullscreen(self._win)
        cv2.imshow(self._win, big)
        _force_topmost(self._win)
        self._region = (x0, y0, zw, zh)
        self._pre = (cx, cy)
        self._t0 = time.monotonic()

    def tick(self, now):
        """True only on the frame the magnifier closed itself (timeout)."""
        if self.active:
            _force_topmost(self._win)
            if now - self._t0 > self._timeout:
                self.cancel()
                return True
        return False

    def confirm(self):
        """Map the current (gaze-driven) cursor position back to the real
        screen point, close the magnifier and click there."""
        if not self.active:
            return
        from eye_gaze.zoom import map_zoom_point
        px, py = cursor.get_cursor()
        x0, y0, zw, zh = self._region
        # The view fills the primary monitor: its pixel 0 is the origin.
        rx, ry = map_zoom_point(px - self._ox, py - self._oy, x0, y0, zw, zh,
                                self._sw, self._sh)
        self.cancel()
        cursor.set_cursor(rx, ry, virtual=self._multi)
        actions.left_click()
        if self.on_confirm is not None:
            try:
                self.on_confirm(self._pre, (rx, ry), (x0, y0, zw, zh))
            except Exception:
                pass

    def cancel(self):
        if self.active:
            try:
                cv2.destroyWindow(self._win)
            except Exception:
                pass
            self._region = None


def _onoff(flag):
    return "acik" if flag else "kapali"


def _has_click_method(cfg, gamepad, dweller, blinker):
    """True when at least one click route is actually BOUND to something.

    Testing `clicker.gamepad is not None` on its own was dead code: _Clicker
    builds a GamepadReader whenever gamepad_enabled is true (the default), with
    or without a controller attached, so the "no click method" warning could
    never fire in any shipped configuration - including the one it was written
    for. A safety warning that cannot fire is worse than none, because it reads
    as protection that is not there. The BINDING is what decides.
    """
    if str(cfg.get("left_click_key", "") or ""):
        return True
    if gamepad is not None and str(cfg.get("gamepad_left", "") or ""):
        return True
    return dweller is not None or blinker is not None


def _selection_text(ours, copy, sequence, read, wait=0.15, poll=0.05,
                    tries=8, clear=None):
    """(text or None, note) for F4: the user's CURRENT selection, never an old
    clipboard.

    clear (Linux): empty the clipboard first -> True once it reads empty.
    Linux has no change counter, only one derived from the content, and the
    second F4 on the same selection copies the SAME text: the counter does
    not move and the user was told to select what they had selected. After
    a verified clear, any text that turns up was just copied. A clear that
    fails falls back to the sequence number.

    The clipboard sequence number is read before and after the synthetic
    Ctrl+C. If it did not move, nothing was copied - nothing selected, or a
    window that ignores Ctrl+C - and what the clipboard holds is stale: often
    the last dictation, which F4 then used to read aloud as if just chosen.
    An unknown sequence number (API unavailable) falls back to reading.
    Runs on a worker thread: the waits here used to freeze the cursor.
    """
    if ours:
        # Ctrl+C would land on OUR console: cmd.exe asks "Terminate batch job
        # (Y/N)?" and Python takes a KeyboardInterrupt - reported from the field.
        return None, ("Kopyalama atlandi: su an SURUCUNUN kendi penceresi"
                      " onde. Metnin oldugu pencereye tiklayip tekrar deneyin.")
    if clear is not None and _cleared(clear):
        copy()
        time.sleep(wait)
        text = ""
        for i in range(max(1, tries)):
            text = read()
            if text:
                break
            if i < tries - 1:
                time.sleep(poll)
        # "" here: nothing came (or the read failed - the caller says which,
        # from clipboard_problem()).
        return text, ""
    before = sequence()
    copy()
    time.sleep(wait)
    if before is not None:
        for _ in range(max(1, tries)):
            after = sequence()
            if after is None or after != before:
                break
            time.sleep(poll)
        else:
            return None, ("Seslendirilecek metin yok - once metni SECIN"
                          " (surukle) sonra tusa basin.")
    return read(), ""


def _cleared(clear):
    try:
        return bool(clear())
    except Exception:
        return False


def _stt_action(recorder, busy):
    """What the dictation key should do right now.

    'winh'  - no local recorder: fall back to Windows' own dictation
    'wait'  - the previous recording is still being transcribed
    'stop'  - end the recording the user is in the middle of
    'start' - begin one

    A free function rather than four branches inside the gaze loop, because the
    ordering carries a real invariant - busy BEFORE active - and inside main()
    nothing about it could be tested.
    """
    if recorder is None:
        return "winh"
    if busy:
        return "wait"
    if recorder.active:
        return "stop"
    return "start"


def _stt_fix_hint():
    """The console line after "SESLE YAZMA CALISMIYOR". KUR-SES.bat exists
    only in the development machine's source checkout and Win+H only on Windows; a
    packaged build points at its download page instead."""
    from eye_gaze.tts_backends import install_hint
    hint = install_hint()
    if hint == "KUR-SES.bat calistirin":
        return ("  KUR-SES.bat calistirin; duzelene kadar config.yaml"
                " icinde stt_engine: windows yapip Win+H kullanabilirsiniz.")
    if _platform.OS == "windows":
        return ("  " + hint + "; duzelene kadar config.yaml icinde"
                " stt_engine: windows yapip Win+H kullanabilirsiniz.")
    return "  " + hint + "."


def _stt_model_gate(transcriber, say, report=print, alert=None):
    """May a recording start now? False while the faster-whisper model is
    missing (packages: the wizard's download was skipped or failed): the
    press starts the download in the background instead and the user is
    told to wait - recording first would hold their sentence until a
    1.5 GB download ends, then paste it into whatever has the focus then.
    Never blocks: model_ready() is a few stat calls."""
    if transcriber is None or transcriber.model_ready():
        return True
    if transcriber.downloading:
        note = transcriber.download_note()
        report("Dikte modeli hala indiriliyor%s - bekleyin."
               % (" (" + note + ")" if note else ""))
        return False
    transcriber.start_download(say=say, report=report, alert=alert)
    return False


_STUCK_AFTER = 300      # ~10 s of consecutive identical frame failures


def _frame_error(state, exc):
    """Keep the gaze loop alive through anything one frame can throw.

    Before this, an exception anywhere in the per-frame work propagated out of
    main() and ended the process. It happened for real: a clipboard read hit an
    access violation while handling F4 and the driver closed. Losing settings,
    speech or dictation for a session is survivable; losing the CURSOR is not -
    it is this user's only way to operate the machine, including their only way
    to ask someone for help.

    Repeats of the same fault are counted, not reprinted: a failure that recurs
    at 30 fps would otherwise bury every other line in the console.
    """
    kind = type(exc).__name__
    state["n"] = state.get("n", 0) + 1
    if kind == state.get("last"):
        state["rep"] = state.get("rep", 0) + 1
        if state["rep"] == _STUCK_AFTER:
            # A fault that fires EVERY frame is not a hiccup the user can wait
            # out: whatever comes after it in the frame - possibly the cursor
            # update itself - never runs again. One quiet line at the start
            # would leave them staring at a driver that is alive and useless.
            print("UYARI: ayni hata %d karedir araliksiz tekrarliyor -"
                  " surucu TAKILMIS olabilir." % _STUCK_AFTER)
            print("  Imlec de durduysa cikis tusuna IKI KEZ basip programi"
                  " yeniden baslatin. Ayrinti: gaze.log")
        return False
    state["last"] = kind
    state["rep"] = 0
    # ascii(): the console is cp1254 and a UnicodeEncodeError raised HERE would
    # do precisely what this guard exists to prevent.
    print("KARE HATASI (" + kind + "): " + ascii(exc))
    # Deliberately NOT "imlec calismaya devam ediyor": whether the cursor
    # survives depends on where in the frame the fault happened, and promising
    # something that may not be true is how a user stops believing the console.
    print("  Surucu CALISMAYA DEVAM EDIYOR. Ayni hata tekrarlanirsa bir daha"
          " yazilmaz.")
    return True


def _stt_poll(recorder):
    """One frame of recording: (audio or None, message to print or "").

    Recorder.poll() closes the stream and hands back whatever it had when the
    device fails - an unplugged USB microphone, a driver reset. If it had
    nothing, it returns None and simply stops being active: the loop quietly
    stops polling while the user is still waiting to be transcribed, with
    nothing on the console to say the recording is over.
    """
    audio = recorder.poll()
    if audio is not None:
        return audio, ""
    if not recorder.active:
        return None, _stt_empty_note(recorder, ended=True)
    return None, ""


def _stt_empty_note(recorder, ended=False):
    """Why a recording produced nothing. Silence is NOT handed to Whisper any
    more (it invents sentences from it), so 'no speech heard' and 'no audio
    at all' are different problems with different fixes."""
    if getattr(recorder, "no_speech", False):
        return ("Konusma duyulmadi - hicbir sey yazilmadi. Tekrar deneyin;"
                " biraz daha yuksek sesle ya da daha yakindan konusun.")
    if ended:
        return ("Kayit kesildi - hicbir ses alinamadi (mikrofon"
                " cikarilmis ya da baska bir program almis olabilir).")
    return "Kayit bos - hicbir ses alinmadi."


def _persistent_warnings(cfg, has_click):
    """Warnings that must stay visible for the whole session.

    The [ayar] lines and the startup banner are printed before the camera is
    even open, and the console scrolls; the OpenCV preview window is the one
    thing the user actually looks at from then on. Two states are wrong for
    long enough to belong there:

      * running on the last-known-good config, because the risk of that feature
        is precisely a caregiver concluding their edit did nothing;
      * no click route bound, because the cursor moving perfectly makes it look
        like everything works.

    Short lines: this is drawn over a webcam frame with cv2.putText, which has
    no wrapping and no Turkish characters.
    """
    lines = []
    if used_backup(cfg):
        lines.append(t_ascii("driver.warn.old_settings"))
    if not has_click:
        lines.append(t_ascii("driver.warn.no_click"))
    return lines


class _Clicker:
    """Finger-switch clicks: keyboard keys (KeyPresses, hooked + swallowed)
    and gamepad/joystick buttons. Left/right/double click + drag toggle.

    update() runs ONCE per frame whatever is on screen, and turns presses into
    pending actions; the caller then either take()s the left press for an open
    overlay or fire()s the actions as real mouse events. Edges used to be
    computed only in the branch that happened to run, so the frame after the
    AAC grid or magnifier closed saw the still-held switch as a NEW press and
    clicked whatever was behind - a click the user never asked for.
    """

    KINDS = ("left", "right", "double", "drag")
    _PAD_RETRY = 1.0      # s between polls while no pad/joystick answers

    def __init__(self, cfg, keys=None, gamepad="auto"):
        self._cfg = cfg
        self._keys = keys
        if gamepad == "auto":
            gamepad = GamepadReader() if cfg.get("gamepad_enabled", True) else None
        self.gamepad = gamepad
        self._ed = {k: EdgeDetector() for k in self.KINDS}
        self._pending = set()
        self._pad_next = 0.0
        self._mute_until = 0.0
        self._dragging = False
        # Called with the fired kinds after real mouse events went out (the
        # click earcon). Set by main(); None = silent.
        self.on_fire = None
        self._btn = 0           # last pad buttons: left_held() for the click menu
        if self.gamepad is not None:
            for k in ("gamepad_left", "gamepad_right", "gamepad_double", "gamepad_drag"):
                name = cfg.get(k, "")
                if name and not valid_button(name):
                    print(f"UYARI: config'de {k}: '{name}' bilinen bir gamepad dugmesi degil. "
                          f"Gecerli adlar: {button_names_text()}")

    def _pad_buttons(self, now):
        if self.gamepad is None or now < self._pad_next:
            return 0
        b = self.gamepad.buttons()
        if b is None:
            self._pad_next = now + self._PAD_RETRY
            return 0
        return b

    def update(self, now):
        btn = self._pad_buttons(now)
        self._btn = btn
        for kind in self.KINDS:
            pad = str(self._cfg.get("gamepad_" + kind, "") or "")
            down = bool(pad) and bool(btn & GAMEPAD_BUTTONS.get(pad.upper(), 0))
            if self._ed[kind].update(down):
                self._pending.add(kind)
            key = str(self._cfg.get(
                {"left": "left_click_key", "right": "right_click_key",
                 "double": "double_click_key", "drag": "drag_key"}[kind], "") or "")
            if key and self._keys is not None and self._keys.take(key):
                self._pending.add(kind)
        if now < self._mute_until:
            self._pending.clear()

    def take(self, kind):
        if kind in self._pending:
            self._pending.discard(kind)
            return True
        return False

    def discard(self):
        self._pending.clear()

    def mute(self, now, seconds=1.5):
        """Drop presses for a moment - after an overlay closed by itself the
        next press was aimed at it, not at the window that is now in front."""
        self._mute_until = now + seconds
        self._pending.clear()

    def fire(self):
        p, self._pending = self._pending, set()
        if "left" in p:
            actions.left_click()
            self._dragging = False      # a click is down+up: the button is up
        if "right" in p:
            actions.right_click()
        if "double" in p:
            actions.double_click()
            self._dragging = False
        if "drag" in p:
            if self._dragging:
                actions.left_up()
                self._dragging = False
            else:
                actions.left_down()
                self._dragging = True
        if p and self.on_fire is not None:
            try:
                self.on_fire(p)
            except Exception:
                pass                    # a sound must never cost the click

    def release(self):
        """Forced end of a drag (pause, an overlay, calibration, camera lost,
        exit) - never the user's own drop, which goes through fire(). Esc
        first: a bare button-up dropped a dragged file into whatever folder
        the eyes were on, which this user cannot undo."""
        if self._dragging:
            try:
                actions.escape()
            except Exception:
                pass
            actions.left_up()
            self._dragging = False

    # --- for the click menu (eye_gaze/click_menu.py) ---
    def inject(self, kind):
        """Queue one action as if its switch had been pressed. The click
        menu applies its choice through here so a drag it starts is THIS drag,
        the one release() lets go of on pause, exit and console close."""
        if kind in self.KINDS:
            self._pending.add(kind)

    @property
    def dragging(self):
        return self._dragging

    def left_held(self):
        """True while the left click switch (key or pad button) is down."""
        key = str(self._cfg.get("left_click_key", "") or "")
        held = getattr(self._keys, "held", None)
        if key and held is not None and held(key):
            return True
        pad = str(self._cfg.get("gamepad_left", "") or "")
        return bool(pad) and bool(self._btn & GAMEPAD_BUTTONS.get(pad.upper(), 0))


_CLOSE_HANDLER = None    # kept alive: ctypes would free it under Windows


def _close_guard(clicker):
    """Console control handler that lets go of a held drag.

    Closing the console with its X button, logging off or shutting down does
    not raise KeyboardInterrupt: Windows calls the handler and then ends the
    process, skipping main()'s finally - and a left button held by the drag
    toggle stays down system-wide, with no cursor left to release it.
    Returns the handler (installed when the Win32 API is available).

    Linux: the same events arrive as signals - SIGHUP when the terminal is
    closed, SIGTERM at logout, shutdown or a plain kill (_close_signals).
    """
    global _CLOSE_HANDLER

    def handler(ctrl_type):
        if ctrl_type in (2, 5, 6):      # CLOSE, LOGOFF, SHUTDOWN
            try:
                clicker.release()
            except Exception:
                pass
        return False                    # let the default handling continue
    if _platform.OS != "windows":
        _close_signals(handler)
        return handler
    try:
        import ctypes
        proto = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
        cb = proto(lambda t: int(bool(handler(t))))
        k = ctypes.windll.kernel32
        k.SetConsoleCtrlHandler.argtypes = [proto, ctypes.c_int]
        k.SetConsoleCtrlHandler.restype = ctypes.c_int
        k.SetConsoleCtrlHandler(cb, 1)
        _CLOSE_HANDLER = cb
    except Exception:
        pass
    return handler


_CLOSE_SIGNALS = (("SIGHUP", 2), ("SIGTERM", 6))   # -> CLOSE, SHUTDOWN
# How long a signal waits for the drag release (see _close_signals).
_CLOSE_RELEASE_WAIT = 1.0


def _close_signals(handler):
    """Run the close handler on SIGHUP / SIGTERM, CHAINED to whatever was
    installed before (the evdev backend's handler lets go of the grabbed
    keyboard - replacing it would leave the keyboard dead after we exit),
    then let the signal do what it would have done: the previous Python
    handler, or the default action (terminate) re-raised. An ignored signal
    (nohup) or a C-level handler is left alone, as the evdev backend does.
    The evdev backend may also install its handler AFTER this one; it
    chains to ours the same way. Main thread only (signal.signal's rule):
    elsewhere it is said and skipped. Never raises.

    The release runs on a thread of its own, waited for at most
    _CLOSE_RELEASE_WAIT - as Windows runs its console handler. A Python
    signal handler interrupts the main thread between two bytecodes, and
    that may be INSIDE the input backend's lock (the cursor is moved every
    frame under it): releasing the button on the main thread would then
    wait for a lock its own frame holds - a hang at logout instead of an
    exit. Off-thread, the worst case is a release that comes too late.
    That thread is started HERE and parks on an Event: the signal handler
    only sets it and waits, nothing is created inside the handler."""
    import signal
    if threading.current_thread() is not threading.main_thread():
        print("UYARI: kapanis sinyalleri yakalanamadi (ana is parcacigi"
              " degil); terminal kapanirsa basili surukleme"
              " birakilmayabilir.")
        return
    go, done = threading.Event(), threading.Event()
    fired = [2]

    def release():
        go.wait()
        try:
            handler(fired[0])
        except Exception:
            pass
        finally:
            done.set()
    parked = False
    for name, ctrl in _CLOSE_SIGNALS:
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            prev = signal.getsignal(sig)
            if prev is signal.SIG_IGN or prev is None:
                continue
            if not parked:
                threading.Thread(target=release, daemon=True,
                                 name="close-guard").start()
                parked = True

            def on_signal(signum, frame, prev=prev, ctrl=ctrl):
                if not go.is_set():
                    fired[0] = ctrl
                    go.set()
                done.wait(_CLOSE_RELEASE_WAIT)
                if callable(prev):
                    return prev(signum, frame)
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)
            signal.signal(sig, on_signal)
        except ValueError:
            print("UYARI: kapanis sinyali (%s) yakalanamadi (ana is"
                  " parcacigi degil); terminal kapanirsa basili surukleme"
                  " birakilmayabilir." % name)
            return
        except Exception:
            continue


_OSK_SAID = [False]


def _open_osk_linux():
    """The OSK key on Linux: onboard / GNOME's keyboard (linux_misc). When
    there is none the reason is said in full once, briefly after that."""
    from eye_gaze.platform import linux_misc
    ok, why = linux_misc.launch_osk()
    if ok:
        print("Ekran klavyesi acildi: " + _platform.ascii_text(why))
    elif not _OSK_SAID[0]:
        _OSK_SAID[0] = True
        print("Ekran klavyesi acilamadi: " + _platform.ascii_text(why))
    else:
        print("Ekran klavyesi acilamadi (nedeni yukarida yazildi).")
    return ok


def _quiet_layers(zoomer, aacgrid, clickmenu, dring):
    """Close every overlay and hide the dwell ring before a calibration or a
    drift measurement takes the screen. The loop stops ticking there, so an
    open magnifier, grid or click menu would otherwise sit (topmost) under
    the dots and come back stale - and timed out, muting the next press -
    once it ends. Never raises: each layer is tried on its own."""
    for layer in (zoomer, aacgrid, clickmenu):
        if layer is None:
            continue
        try:
            layer.cancel()
        except Exception:
            pass
    if dring is not None:
        try:
            dring.hide()
        except Exception:
            pass


def _ring_off(dring):
    """Hide the dwell ring when no frame is being processed (camera gone, a
    frame that throws). It hides itself only from tick(), at the end of a
    normal frame, so it would otherwise sit frozen on top of everything
    showing a click about to happen that nothing is measuring. Never raises."""
    if dring is None:
        return
    try:
        dring.hide()
    except Exception:
        pass


def _dwell_live(aacgrid):
    """Whether the dweller is fed this frame. Not while the AAC grid is open
    in SCAN mode: there only the switch selects (dwell is ignored), and a
    ring filling up and flashing 'clicked' would tell the user something
    that did not happen."""
    return not (aacgrid is not None and aacgrid.active
                and getattr(aacgrid, "scanning", False))


def _route_clicks(now, clicker, keys, aacgrid, zoomer, aac_key, zoom_key,
                  menu=None):
    """One frame of switch routing. True when an overlay owns the input (the
    caller must then not act on any other key this frame).

    While the AAC grid or the magnifier is open the left press confirms it
    and every other pending action is dropped: a right-click or drag toggle
    fired from under a fullscreen window is something this user cannot undo.
    """
    for ov in (aacgrid, zoomer):
        if ov is None or not ov.active:
            continue
        own, other = ((aac_key, zoom_key) if ov is aacgrid
                      else (zoom_key, aac_key))
        if ov.tick(now):
            clicker.mute(now)           # closed by its own timeout
        elif clicker.take("left"):
            ov.confirm()
            if not ov.active:
                # Closed by this selection: a switch bounce counted as a
                # second press would surface next frame as a real click on
                # the app behind. Scan's PRESS_GUARD only covers an open grid.
                clicker.mute(now, click_menu.SELECT_MUTE)
        elif own and keys.take(own):
            ov.cancel()
        # The other overlay's key is dropped, not saved up: it used to open
        # the magnifier the moment the grid closed (found by the smoke run).
        if other:
            keys.take(other)
        if menu is not None and menu.key:
            keys.take(menu.key)         # click menu key: dropped, not saved up
        clicker.discard()
        return True
    # Click menu (F2 / hold): owns the input while open, see click_menu.route.
    if menu is not None and menu.route(now, keys, (aac_key, zoom_key)):
        return True
    clicker.fire()
    if aacgrid is not None and aac_key and keys.take(aac_key):
        clicker.release()
        aacgrid.begin()
        return True
    if zoom_key and keys.take(zoom_key):
        clicker.release()
        zoomer.begin()
        return True
    return False


def _late_key_grabs():
    """True on Linux with the X11 key backend (synchronous grabs): main()
    then grabs only the alarm early and the other keys after the models."""
    if _platform.OS == "windows":
        return False
    try:
        return getattr(_platform.linux_backend(), "name", "") == "x11"
    except Exception:
        return False


def _alarm_uses_switch(cfg):
    """alarm_switch_hold > 0 with a click key: holding the click switch is
    an alarm, so that key is the alarm's grab too."""
    try:
        hold = float(cfg.get("alarm_switch_hold", 0) or 0)
    except (TypeError, ValueError):
        return False
    return hold > 0 and bool(str(cfg.get("left_click_key", "") or "").strip())


def main():
    global _BASE_DIR, _SCREEN_ORIGIN
    # First run of an installed build: the per-user folder does not exist yet
    # (and if it cannot be made, ensure_dir says so and hands back a fallback,
    # so the log and the "no config" message still reach someone).
    _BASE_DIR = paths.ensure_dir(_BASE_DIR)
    _setup_logging()
    cfg = load_config(os.path.join(_BASE_DIR, "config.yaml"))
    # Every screen, menu and spoken sentence from here on is in the chosen
    # language (Turkish when config.yaml has no `language` line).
    i18n.setup(cfg)
    if used_defaults(cfg):
        # Refuse to start, rather than demand a calibration that can neither be
        # used nor kept. In this state DEFAULTS supplies NO click method at all
        # - left_click_key, gamepad_left, dwell_click and blink_click are all
        # absent or off (test_defaults_really_have_no_way_to_click pins that) -
        # so the session cannot be used for anything even once it is running.
        # Getting there would first cost a full pursuit calibration, which is
        # physically taxing for this user, and its result is then discarded
        # because saving is forced off. Cancelling that calibration already
        # exits with 1, so continuing was never a working session: only a more
        # expensive route to the same place. Stopping here also means the
        # camera is never opened and calibration.json is never touched.
        print("Surucu BASLATILMADI: gecerli bir ayar dosyasi olmadan TIKLAMA"
              " yapilamaz ve kalibrasyon kaydedilemez.", file=sys.stderr)
        print("Yukaridaki [ayar] satirlarina bakin, config.yaml dosyasini"
              " duzeltin ve programi yeniden baslatin.", file=sys.stderr)
        # 2 = "do not restart me": BASLAT.bat restarts on any other failure,
        # but restarting cannot fix a config file.
        return 2
    if used_backup(cfg):
        # load_config already said this via [ayar] lines, but those scroll past
        # before the camera is even open. Repeat it here, where the startup
        # banner is, so the caregiver cannot conclude their edit silently did
        # nothing.
        print("*** ESKI (son calisan) AYARLARLA CALISIYOR - config.yaml"
              " okunamadi. Yaptiginiz son degisiklikler GECERLI DEGIL. ***",
              file=sys.stderr)
    # One key, one job: a newer key (alarm, click menu, speech stop) given a
    # key another hub already hooks is turned off here, before any hook.
    from eye_gaze.keymap import resolve_key_conflicts
    resolve_key_conflicts(cfg)
    quit_key = cfg["quit_key"]
    recal_key = cfg["recalibrate_key"]
    pause_key = cfg.get("pause_key", "f8")
    drift_key = cfg.get("drift_key", "f7")
    interval = float(cfg.get("hotkey_double_interval", 1.0))
    margin = int(cfg["screen_margin"])
    reach_margin = float(cfg.get("reach_margin", 0.25))
    use_head = bool(cfg.get("use_head_pose", True))
    use_tr = bool(cfg.get("head_translation", True))
    win_n = max(1, int(cfg.get("gaze_median_window", 5)))
    cal_samples = int(cfg.get("calibration_samples", 25))
    save_cal = bool(cfg.get("save_calibration", True))
    # Defence in depth behind the early return above: a defaults-only config
    # may not touch calibration.json AT ALL. Without the user's feature flags
    # n_inputs drops from 11 to 9, so the saved 11-input file is rejected on
    # load, a full recalibration is forced, and the result would OVERWRITE the
    # tuned file with a degraded one - costing a second full recalibration once
    # the YAML is repaired. Calibration is physically taxing for this user and
    # it is the most expensive thing they produce.
    #
    # One flag, so it stops the LOAD as well as the two saves. That is
    # deliberate: "this config may not touch the calibration file" is a simpler
    # invariant to keep true than "it may read but not write", and the read has
    # nothing to offer here - a mismatched file is rejected anyway, and its
    # rejection line blames use_head_pose, a setting the user never changed.
    if used_defaults(cfg):
        save_cal = False
    cal_file = cfg.get("calibration_file", "calibration.json")
    if cal_file and not os.path.isabs(cal_file):
        cal_file = os.path.join(_BASE_DIR, cal_file)
    gaze_scroll = bool(cfg.get("gaze_scroll", False))
    scroll_zone = float(cfg.get("scroll_zone", 0.08))
    scroll_speed = int(cfg.get("scroll_speed", 1))
    blink_on = bool(cfg.get("blink_click", False))
    closed_thr = float(cfg.get("blink_closed_threshold", 0.18))
    dwell_on = bool(cfg.get("dwell_click", False))
    multi_mon = bool(cfg.get("multi_monitor", False))

    # Keys and the emergency alarm come up FIRST - before the camera, the
    # face model and the GPU model. They used to come after, so a webcam
    # held by a video call (or unplugged) meant `return 1` before F12
    # existed, and BASLAT.bat's restart loop ran with no alarm at all.
    # The driver's keys are swallowed so the app in front never also gets them
    # (Space typing a space, F5 reloading the browser, F3 opening its find
    # bar). suppress_keys: false turns that off; pause turns it off live.
    suppress_keys = bool(cfg.get("suppress_keys", True))
    # Keys typed BY the on-screen keyboard / OptiKey belong to the app, not
    # to us (a swallowed OSK Space became a click on that same key: a loop).
    from eye_gaze import injected
    injected.enabled = bool(cfg.get("ignore_injected_keys", True))
    # Linux X11 only: the alarm is the ONLY early grab, the other keys are
    # grabbed once the face and deep models are loaded (_arm_late_keys).
    # X11 grabs are synchronous - a held grabbed key freezes the keyboard
    # until this process answers - and the watchdog that cuts a stall is a
    # thread of this same process: a GIL stall (importing and building the
    # models) longer than the X server's auto-repeat delay is not covered,
    # and an auto-repeat into a frozen keyboard can crash the X server
    # (eye_gaze/platform/linux_x11.py, Safety). The alarm stays first, as
    # above; the click switch is grabbed early too when alarm_switch_hold
    # makes it an alarm. Windows and evdev: everything early, as before.
    late_keys = _late_key_grabs()
    hotkeys = Hotkeys(quit_key, recal_key, pause_key, interval, drift_key=drift_key,
                      suppress=suppress_keys, defer=late_keys)
    zoom_key = str(cfg.get("zoom_key", "f6") or "")
    aac_key = str(cfg.get("aac_key", "") or "")
    osk_key = str(cfg.get("osk_key", "") or "")
    tts_key = str(cfg.get("tts_key", "") or "")
    stt_key = str(cfg.get("stt_key", "") or "")
    menu_key = str(cfg.get("click_menu_key", "f2") or "")
    keys = KeyPresses([cfg.get(k, "") for k in ("left_click_key", "right_click_key",
                                                "double_click_key", "drag_key")]
                      + [zoom_key, aac_key, osk_key, tts_key, stt_key]
                      + [menu_key],
                      suppress=suppress_keys, defer=late_keys)
    # The click switch is an alarm grab too with alarm_switch_hold (the
    # alarm key itself has its own hub, inside start_alarm).
    alarm_grabs = ([cfg.get("left_click_key", "")]
                   if _alarm_uses_switch(cfg) else [])
    if late_keys and alarm_grabs:
        keys.arm(alarm_grabs)
    late_said = []

    def _arm_late_keys():
        if not late_keys:
            return
        hotkeys.arm()               # both idempotent; again after a
        keys.arm()                  # _disarm_late_keys()
        if late_said:
            return
        late_said.append(True)
        for bad in keys.bad_keys:
            print(f"UYARI: config'deki tus adi '{bad}' taninmadi; bu tus CALISMAYACAK.")
        print("Surucu tuslari baglandi (X11: modeller yuklendikten sonra).")

    def _disarm_late_keys():
        # X11, camera wait over: the keys armed for the wait are ungrabbed
        # again before the models load, so the alarm is the only grab held
        # through that GIL stall (final review I-1). The camera usually
        # comes back while the user is pressing the switch to get the
        # cursor back - exactly the key whose auto-repeat into a frozen
        # keyboard can crash the X server. _arm_late_keys() re-grabs them.
        if not late_keys:
            return
        hotkeys.disarm()
        keys.disarm(keep=alarm_grabs)
        print("X11: modeller yuklenirken yalniz acil alarm tusu bagli;"
              " surucu tuslari sonra yeniden baglanacak.")
    if late_keys:
        print("X11: surucu tuslari modeller yuklenince baglanacak; acil alarm"
              " simdiden CALISIYOR.")
    else:
        for bad in keys.bad_keys:
            print(f"UYARI: config'deki tus adi '{bad}' taninmadi; bu tus CALISMAYACAK.")
    clicker = _Clicker(cfg, keys=keys)
    _close_guard(clicker)
    # Emergency alarm (F12 held): its own thread, so it also works while
    # calibrating, paused or with no face. start_alarm() never raises.
    from eye_gaze.alarm import start_alarm
    alarm = start_alarm(cfg, keys)
    _print_missing_capabilities()
    # "auto" camera / deep model / dictation lines from makine.json. After
    # the alarm: a first-start camera probe takes a few seconds.
    deep_was_auto = is_auto(cfg.get("deep_gaze"))
    cfg, machine_profile, machine_cam = _resolve_machine(cfg)
    sw, sh = cursor.screen_size()
    primary = _primary_rect()
    ox, oy = primary[0], primary[1]     # (0, 0) on Windows
    _SCREEN_ORIGIN = (ox, oy)
    # cursor bounds: primary monitor, or the whole virtual desktop
    bx, by, bw, bh = _gaze_bounds(multi_mon, primary)
    cam_index, cam_w, cam_h, cam_fps = _camera_request(cfg)
    open_camera = _camera_opener(cam_index, cam_w, cam_h, cam_fps, machine_cam)
    cap = _ResilientCapture(open_camera)
    if not cap.isOpened():
        print("HATA: webcam acilamadi (camera_index dogru mu? baska bir program"
              " kamerayi kullaniyor olabilir)", file=sys.stderr)
        print("  Kamera BEKLENIYOR (birkac saniyede bir yeniden denenir)."
              " Acil alarm CALISIYOR; cikmak icin '%s' x2." % quit_key)
        # Nothing to click with yet: let the caregiver's keyboard type.
        keys.suppressing = hotkeys.suppressing = False
        # The quit key must work while we wait (X11: no models load here).
        _arm_late_keys()
        while not cap.isOpened():
            if hotkeys.quit_requested:
                print("Kamera beklenirken cikildi.")
                clicker.release()
                keys.close()
                hotkeys.close()
                alarm.close()
                cap.release()
                return 0
            cap.read()          # reconnects on its own schedule; never raises
            time.sleep(0.1)
        keys.suppressing = hotkeys.suppressing = suppress_keys
        keys.clear()            # typing done while waiting is not clicks
        print("KAMERA acildi; surucu baslatiliyor.")
        _disarm_late_keys()     # X11: only the alarm grabbed while loading
    print(f"Kamera cozunurlugu: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
          f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}"
          f" @ {cap.get(cv2.CAP_PROP_FPS):.0f} fps (istenen {cam_fps})")
    _raise_process_priority()
    per_eye = bool(cfg.get("per_eye_features", True))
    estimator = GazeEstimator(use_head_pose=use_head, head_translation=use_tr,
                              per_eye=per_eye)
    # Feature count WITHOUT the deep model: both the calibration key and the
    # evidence _deep_report() reads the stored calibration against.
    base_inputs = modelsel.input_count(per_eye, use_head, use_tr, False)
    cfg = _keep_deep_for_calibration(cfg, deep_was_auto, cal_file, base_inputs)
    deep = _make_deep(cfg, cap,
                      expected=_deep_expected(cfg, cal_file, base_inputs))
    for line in _deep_report(cfg, deep, cal_file, base_inputs):
        print(line)
    source = _FeatureSource(estimator, deep,
                            rebuild_on_cpu=_deep_cpu_rebuilder(deep, cap))
    _arm_late_keys()            # X11: the models are loaded (see above)
    blinker = (BlinkClicker(closed_thr, float(cfg.get("blink_hold_seconds", 0.5)))
               if blink_on else None)
    dweller = (DwellClicker(float(cfg.get("dwell_radius", 40)),
                            float(cfg.get("dwell_seconds", 1.0)),
                            bool(cfg.get("dwell_repeat", False)))
               if dwell_on else None)
    positions = _cal_positions(int(cfg["calibration_points"]))
    poly2 = bool(cfg.get("calibration_poly2", True))
    cal_passes = max(1, int(cfg.get("calibration_passes", 2)))
    cal_mode = str(cfg.get("calibration_mode", "pursuit")).strip().lower()
    pursuit_speed = float(cfg.get("pursuit_speed", 480))
    pursuit_latency = cfg.get("pursuit_latency", "auto")

    print(f"Hotkey modu: {hotkeys.mode}")
    if hotkeys.mode == "window":
        print("  UYARI: 'keyboard' kutuphanesi yok; kisayollar SADECE kamera "
              "penceresi seciliyken calisir:")
        print(f"  CIKIS '{hotkeys.quit_char}'x2 | KALIBRE '{hotkeys.recal_char}'x2 "
              f"| DURAKLAT '{hotkeys.pause_char}'x2 | DUZELT '{hotkeys.drift_char}'x2")
    else:
        print(f"  CIKIS '{quit_key}'x2 | KALIBRE '{recal_key}'x2 | DURAKLAT '{pause_key}'x2 "
              f"| HIZLI DUZELT '{drift_key}'x2")
    click_key = str(cfg.get("left_click_key", "") or "")
    # The reader object exists whenever gamepad_enabled is on, controller or
    # not, so it says nothing about whether a gamepad can click. The BINDING
    # does - same test _has_click_method() uses, so the line and the warning
    # below can never contradict each other.
    pad_click = clicker.gamepad is not None and bool(cfg.get("gamepad_left", ""))
    print(f"  Sol tik tusu: '{click_key or 'YOK'}'  gamepad: {_onoff(pad_click)}")
    print(f"  Bakarak tikla (dwell): {'acik' if dweller else 'kapali'} | "
          f"Kirpma-tik: {'acik' if blinker else 'kapali'} | "
          f"Coklu monitor: {'acik' if multi_mon else 'kapali'}")
    # 'None' printed above used to be the only sign that nothing could click,
    # and by the time it mattered the line had scrolled away. Say what is wrong
    # and what to type instead.
    can_click = _has_click_method(cfg, clicker.gamepad, dweller, blinker)
    if not can_click:
        print("  UYARI: HICBIR TIKLAMA YONTEMI ACIK DEGIL - imlec oynar ama"
              " hicbir sey tiklanamaz.")
        print("  config.yaml icinde left_click_key: \"space\" yazin (parmak"
              " switch'i icin) ya da dwell_click: true yapin (bakarak tikla).")
    warn_lines = _persistent_warnings(cfg, can_click)
    # Earcons (eye_gaze/feedback.py). Built before calibration so the first
    # one can chime; build_feedback never raises, a dead audio stack only
    # costs the beeps.
    from eye_gaze.feedback import build_feedback, build_speech_control
    feedback = build_feedback(cfg)
    clicker.on_fire = lambda kinds: feedback.event("click")
    print(f"  Ses geri bildirimi: {_onoff(feedback.enabled)}")

    def run_calibration():
        if cal_mode.startswith("dot") or cal_mode.startswith("nokta"):
            return calibrate(source, cap, sw, sh, positions, hotkeys, cal_samples,
                             reach_margin, poly2, cal_passes, feat=feat_id)
        return calibrate_pursuit(source, cap, sw, sh, hotkeys, reach_margin, poly2,
                                 cal_passes, pursuit_speed, pursuit_latency, closed_thr,
                                 feat=feat_id)

    n_inputs = base_inputs + (2 if deep is not None else 0)
    if cal_mode.startswith("dot") or cal_mode.startswith("nokta"):
        cal_passes = _dot_passes(len(positions), cal_passes, n_inputs)
    feat_note = ("per_eye_features=%s, use_head_pose=%s, head_translation=%s,"
                 " derin model=%s"
                 % (_onoff(per_eye), _onoff(use_head), _onoff(use_tr),
                    _onoff(deep is not None)))
    feat_id = _feature_id(per_eye, use_head, use_tr, deep is not None)
    mapper = (_load_calibration(cal_file, n_inputs, deep is not None, feat_note,
                                feat=feat_id)
              if save_cal else None)
    if save_cal and bool(cfg.get("calibration_refit", True)):
        # A changed mapping option, or no usable calibration.json: refit from
        # the saved raw samples instead of a full calibration (never raises).
        mapper = caldata.reconcile(
            cal_file, mapper, feat_id, n_inputs,
            {"poly2": poly2, "reach": reach_margin, "latency": pursuit_latency},
            lambda e, ref=None: _should_save(cal_file, e, n_inputs, ref=ref),
            lambda m: _save_calibration(cal_file, m, feat=feat_id))
    mapper = _fit_setup(mapper, sw, sh, _camera_size(cap))
    if mapper is not None:
        print(f"Kayitli kalibrasyon yuklendi. Yeniden kalibrasyon: '{recal_key}' x2.")
        line = _calibration_camera_warning(machine_profile, machine_cam)
        if line:
            print(line)
        _note_calibration_camera(machine_profile, machine_cam, fresh=False)
    else:
        if cal_mode.startswith("dot") or cal_mode.startswith("nokta"):
            print("Kalibrasyon: yesil noktalara SABIT bakin (basinizi degil gozunuzu oynatin).")
        else:
            print("Kalibrasyon: ekranda suzulen yesil TOPU gozunuzle takip edin (~25 sn/tur).")
        print("ONEMLI: GUNLUK oturusunuzdeyken kalibre olun; sonradan yaslanirsaniz F7 x2 duzeltir.")
        def _wait(sec):
            end = time.monotonic() + sec
            while time.monotonic() < end and not hotkeys.quit_requested:
                hotkeys.feed_window_key(cv2.waitKey(100) & 0xFF)
        mapper = _calibrate_until_done(run_calibration, hotkeys, _wait)
        if mapper is None:
            # Only reachable by the user's own quit request: exit code 0 so
            # BASLAT.bat does not start the driver straight back up.
            print("Kalibrasyon iptal edildi; cikiliyor.", file=sys.stderr)
            keys.close()
            hotkeys.close()
            alarm.close()
            cap.release()
            estimator.close()
            cv2.destroyAllWindows()
            return 0
        if save_cal and _save_calibration(cal_file, mapper, feat=feat_id):
            _note_calibration_camera(machine_profile, machine_cam, fresh=True)
        feedback.event("cal_done")
        # An F9 x2 pressed during the first calibration must not start a
        # second full one the moment it ends.
        hotkeys.recal_requested = False
        hotkeys.drift_requested = False

    fx = OneEuroFilter(cfg["freq"], cfg["mincutoff"], cfg["beta"], cfg["dcutoff"])
    fy = OneEuroFilter(cfg["freq"], cfg["mincutoff"], cfg["beta"], cfg["dcutoff"])
    limiter = SpeedLimiter(float(cfg.get("max_cursor_speed", 0)))
    stabilizer = FixationStabilizer(float(cfg.get("deadzone", 20)))
    saccader = make_saccade_jump(cfg)   # None unless saccade_jump: true
    zoomer = _ZoomClick(sw, sh, float(cfg.get("zoom_scale", 0.25)),
                        float(cfg.get("zoom_timeout", 8)), multi_mon,
                        origin=(ox, oy))
    notice = _zoom_notice(zoom_key)
    if notice is not None:
        print(notice)
    # Dwell progress ring; hides itself before every magnifier grab. None when
    # dwell_click is off, no window could be made, or the session allows no
    # overlay window (Wayland) - each said once, never fatal.
    dring = attach_dwell_ring(cfg, dweller, zoomer)
    # Snap-to-target: the cursor goes ONTO the nearest button/link/list item
    # (Windows UI Automation, on a worker thread). None when off; a fault in
    # it only ever means the plain gaze cursor.
    snapper = make_target_snap(cfg, sw, sh)
    # Implicit correction: learns ONLY from magnifier clicks and F7, never
    # from ordinary clicks. No file at all when calibration saving is off.
    implicit = ImplicitCorrector.from_config(
        cfg, cal_file if save_cal else None, caldata.safe_sig(mapper), sw, sh)
    zoomer.on_confirm = (lambda pre, tgt, reg: (not hotkeys.paused)
                         and implicit.observe_zoom(pre, tgt, reg))
    osk_dp = DoublePress(interval)
    tts_state = {"busy": False}
    speaker = None
    # aac_key is Task 16's grid: it must be able to speak even with F4 disabled.
    if tts_key or str(cfg.get("aac_key", "") or ""):
        # select_backend() never raises, so this try/except is not what catches
        # a missing voice - startup_report() below is. It is kept for the two
        # statements that CAN still raise: the import itself, and the worker
        # thread start inside Speaker.__init__. Either one propagating would
        # take down the gaze loop, which is this user's only way to use the
        # computer, over a feature they can live without for one session.
        try:
            from eye_gaze.speech import Speaker, startup_report
            voice = _tts_voice(cfg)
            speaker = Speaker(voice,
                              engine=str(cfg.get("tts_engine", "auto")),
                              speaker_wav=str(cfg.get("tts_speaker_wav", "") or ""),
                              speed=float(cfg.get("tts_speed", 1.0)),
                              volume=float(cfg.get("tts_volume", 1.0)),
                              language=i18n.language())
            for line in startup_report(speaker):
                print(line)
            note = _voice_download_note(voice, speaker)
            if note:
                print(note)
            if not speaker.ok:
                # Stop the (already started) worker thread before dropping the
                # reference, so an unusable Speaker does not leave a thread
                # parked on its queue for the whole session.
                speaker.close()
                speaker = None
        except Exception as e:
            # ascii(): the console is cp1254, and a UnicodeEncodeError raised
            # HERE - inside the handler whose whole job is to keep the gaze
            # loop alive - would propagate out of main() and do exactly what
            # this guard exists to prevent.
            print("TTS baslatilamadi (" + ascii(e) + "); seslendirme kapali.")
            # startup_report() and close() run AFTER the binding above, so a
            # failure in either would otherwise leave a live, unusable Speaker
            # in scope for the rest of the session.
            speaker = None
    alarm.attach_speaker(speaker)       # "Yardim edin!" in the user's voice

    stt_engine = str(cfg.get("stt_engine", "whisper") or "whisper").lower()
    recorder = None
    transcriber = None
    stt_state = {"busy": False}
    if stt_key and stt_engine == "whisper":
        try:
            from eye_gaze.stt import Recorder, Transcriber
            recorder = Recorder(
                silence_seconds=float(cfg.get("stt_silence_seconds", 1.5)),
                max_seconds=float(cfg.get("stt_max_seconds", 30)),
                lead_in_seconds=feedback.capture_lead_in())
            stt_model = _stt_model_name(cfg)
            transcriber = Transcriber(model=stt_model,
                                      device=str(cfg.get("stt_device", "auto")),
                                      language=_stt_language(cfg))
            print(f"Sesle yazma: yerel Whisper ({stt_model})"
                  f" - '{stt_key}' ile kayit")
            # The first press pays for loading a multi-gigabyte model. Without
            # this line that shows up as the program hanging on the one feature
            # the user just tried for the first time.
            print("  NOT: ILK kullanimda model yuklenir, 10-30 sn surebilir.")
            if not transcriber.model_ready():
                print(f"  Dikte modeli henuz indirilmedi: ilk '{stt_key}'"
                      " basisinda indirilir (internet gerekir).")
        except Exception as e:
            print("Yerel dikte kurulamadi (" + ascii(e) + ")"
                  + ("; Win+H kullanilacak." if _platform.OS == "windows"
                     else "; sesle yazma kapali."))
            recorder = None
            transcriber = None

    def _finish_stt(audio):
        """Transcribe off the gaze loop, then paste + optionally read back."""
        try:
            from eye_gaze.stt import insert_outcome, insert_report
            text = transcriber.transcribe(audio) if transcriber is not None else ""
            if text:
                # Only what happened: "panoda duruyor" when the clipboard
                # really holds it, "Yazildi" when it really landed.
                print(insert_report(insert_outcome(text), text))
                if bool(cfg.get("stt_readback", True)) and speaker is not None:
                    speaker.speak(text)
            elif transcriber is not None and not transcriber.ok:
                # "Konusma anlasilamadi, tekrar deneyin" for a missing whisper
                # install is an instruction to keep retrying something that can
                # never work. Say which of the two it is.
                print("SESLE YAZMA CALISMIYOR: " + ascii(transcriber.last_error))
                print(_stt_fix_hint())
            elif transcriber is not None and transcriber.last_error:
                print("Cozumleme hatasi: " + ascii(transcriber.last_error))
            else:
                print(f"Konusma anlasilamadi - tekrar deneyin ({stt_key}).")
        except BaseException as exc:      # noqa: BLE001 - see below
            # busy is cleared in the finally, but a thread that dies in silence
            # would leave the user pressing F3 at a HUD stuck on COZULUYOR with
            # nothing in the console to explain it.
            print("Dikte is parcaciği hatasi: " + ascii(exc))
        finally:
            # WITHOUT this, one raise anywhere above leaves busy True forever:
            # dictation is dead for the rest of the session and the HUD says
            # COZULUYOR the whole time.
            stt_state["busy"] = False

    def _start_stt_worker(audio):
        stt_state["busy"] = True
        threading.Thread(target=_finish_stt, args=(audio,), daemon=True).start()

    def _speak_selection(ours):
        """F4 off the gaze loop: copy, wait for the clipboard, speak."""
        try:
            from eye_gaze import speech as _sp

            clear = None
            if _platform.OS != "windows":
                from eye_gaze.platform import linux_misc

                def _copy():
                    # The focus may be our own terminal (Wayland does not
                    # say, tmux/ssh hide it): its Ctrl+C must not SIGINT us.
                    with linux_misc.interrupt_key_off():
                        _platform.keyboard_api().send("ctrl+c")
                clear = linux_misc.clipboard_clear
            else:
                def _copy():
                    _platform.keyboard_api().send("ctrl+c")
            seq = getattr(_sp, "clipboard_sequence", lambda: None)
            txt, note = _selection_text(ours, _copy, seq, _sp.get_clipboard_text,
                                        clear=clear)
            if txt is not None and txt.strip():
                speaker.speak(txt)
                print("Seslendiriliyor (" + speaker.voice + "): " + ascii(txt[:60]))
            elif txt is not None:
                # Say WHICH it was. "Once metni SECIN" for a clipboard another
                # program was holding sends someone who cannot easily repeat an
                # action off to redo the one thing they already did right.
                why = _sp.clipboard_problem()
                if why and why != "panoda metin yok":
                    print("Seslendirilemedi - " + why + ". Tekrar deneyin.")
                else:
                    print("Seslendirilecek metin yok - once metni SECIN"
                          " (surukle) sonra tusa basin.")
            else:
                print(note)
        except BaseException as exc:      # noqa: BLE001 - a worker must not die silently
            print("Seslendirme hatasi: " + ascii(exc))
        finally:
            tts_state["busy"] = False

    def _start_tts_worker(ours):
        tts_state["busy"] = True
        threading.Thread(target=_speak_selection, args=(ours,), daemon=True).start()

    # Built after the Speaker exists: the grid warms its phrase cache through it.
    aacgrid = (_AacGrid(sw, sh, cfg, speaker, origin=(ox, oy))
               if aac_key else None)
    # speech_stop_key (F11): its own hook, None without a Speaker.
    speech_ctl = build_speech_control(cfg, speaker, suppress_keys)
    if speech_ctl is not None:
        print(f"  KONUSMAYI DURDUR '{speech_ctl.key}' | SON CUMLEYI TEKRARLA"
              f" '{speech_ctl.key}' x2")
    undo_req = []           # set by the click menu, handled by the loop
    clickmenu = click_menu.from_config(
        cfg, sw, sh, clicker, topmost=_force_topmost,
        on_undo=(lambda: undo_req.append(True)) if save_cal else None,
        origin=(ox, oy), place=_place_on_primary)
    if clickmenu is not None and dring is not None:
        # The menu's background is a screenshot, like the magnifier's.
        guard_capture(clickmenu, dring)

    def fire_left_click(dwell=False):
        # Dwell and blink clicks come through here too, so the grid has to be
        # first: otherwise looking at a phrase and dwelling would fire a real
        # mouse click into whatever window is behind the fullscreen grid.
        if aacgrid is not None and aacgrid.active:
            aacgrid.confirm(dwell=dwell)    # scan mode ignores dwell
        elif zoomer.active:
            zoomer.confirm()
        elif clickmenu is not None and clickmenu.active:
            clickmenu.confirm()
        else:
            actions.left_click()
            feedback.event("click")
    hist = deque(maxlen=win_n)
    drift = (0.0, 0.0)  # one-point correction offset (px), reset on recalibration

    if aacgrid is not None and aacgrid.ready:
        print(f"  AAC IZGARASI '{aac_key}' | SESLENDIR '{tts_key}'"
              f" | SESLE YAZ '{stt_key}'")
    print("Surucu calisiyor. Imleci gozunuzle surun; parmakla tikla.")
    status = "gaze_driver"
    cv2.namedWindow(status, cv2.WINDOW_NORMAL)
    loop_i = 0
    prev_paused = False
    prev_layers = None
    cam_down = False
    fps_ema = 0.0
    prev_t = None
    frame_errs = {}
    try:
        while True:
            if hotkeys.quit_requested:
                break
            # Nothing one frame does may end the process. An exception
            # here used to propagate out of main() and close the driver -
            # it really happened, from a clipboard read during F4 - and
            # that takes the CURSOR down, which is this user's only way to
            # operate the machine or ask anyone for help.
            try:
                # Pause is the caregiver's mode: their keyboard types again.
                keys.suppressing = hotkeys.suppressing = not hotkeys.paused
                if hotkeys.paused:
                    clicker.release()       # never leave a drag held while frozen
                if undo_req:
                    del undo_req[:]
                    clicker.release()
                    m, msg = _undo_calibration(cal_file, mapper, n_inputs,
                                               feat_id, screen=(sw, sh))
                    print(msg)
                    if m is not None:
                        feedback.event("cal_done")
                        mapper = m
                        hist.clear()
                        fx = OneEuroFilter(cfg["freq"], cfg["mincutoff"], cfg["beta"], cfg["dcutoff"])
                        fy = OneEuroFilter(cfg["freq"], cfg["mincutoff"], cfg["beta"], cfg["dcutoff"])
                        limiter.reset()
                        stabilizer.reset()
                        drift = (0.0, 0.0)
                        implicit.set_drift(drift)
                        implicit.bind(caldata.safe_sig(mapper))
                    else:
                        feedback.event("error")
                    continue
                if hotkeys.take_recal():
                    clicker.release()
                    _quiet_layers(zoomer, aacgrid, clickmenu, dring)
                    print("Yeniden kalibrasyon basladi. VAZGECMEK icin '%s' x2"
                          " (onceki kalibrasyon korunur)." % recal_key)
                    hotkeys.calibrating = True
                    try:
                        m = run_calibration()
                    finally:
                        cancelled = hotkeys.end_calibration()
                    keys.clear()            # presses made during it are stale
                    clicker.discard()
                    if m is None and cancelled:
                        print("Kalibrasyon IPTAL edildi; onceki kalibrasyon"
                              " kullanilmaya devam ediyor.")
                    if m is not None:
                        feedback.event("cal_done")
                        mapper = m
                        hist.clear()
                        fx = OneEuroFilter(cfg["freq"], cfg["mincutoff"], cfg["beta"], cfg["dcutoff"])
                        fy = OneEuroFilter(cfg["freq"], cfg["mincutoff"], cfg["beta"], cfg["dcutoff"])
                        limiter.reset()
                        stabilizer.reset()
                        drift = (0.0, 0.0)
                        implicit.set_drift(drift)  # the field is relative to it
                        implicit.bind(caldata.safe_sig(mapper))  # new calibration: start over
                        if save_cal:
                            ok_save, why = _should_save(
                                cal_file, getattr(mapper, "err_cv", None),
                                n_inputs)
                            if ok_save:
                                if _save_calibration(cal_file, mapper,
                                                     feat=feat_id):
                                    _note_calibration_camera(
                                        machine_profile, machine_cam,
                                        fresh=True)
                            else:
                                print(why)
                                print("  Bu oturumda yeni kalibrasyon kullaniliyor;"
                                      " program yeniden acilinca kayitli (iyi) olan"
                                      " yuklenecek. Tekrar denemek icin: '%s' x2."
                                      % recal_key)
                    continue
                if hotkeys.take_drift():
                    clicker.release()
                    _quiet_layers(zoomer, aacgrid, clickmenu, dring)
                    hotkeys.calibrating = True
                    try:
                        off = measure_drift(source, cap, sw, sh, hotkeys,
                                            max(10, cal_samples // 2), mapper)
                    finally:
                        if hotkeys.end_calibration():
                            off = None
                    if off is not None:
                        drift = off
                        implicit.observe_drift((ox + sw // 2, oy + sh // 2),
                                               off)
                        print(f"Hizli duzeltme uygulandi: ({off[0]:+.0f}, {off[1]:+.0f}) px")
                    else:
                        print("Hizli duzeltme alinamadi; onceki ayar korundu.")
                    keys.clear()
                    clicker.discard()
                    continue
                ok, frame = cap.read()
                # Earcon edges + stop/repeat key, BEFORE the no-frame branch
                # so both keep working while the camera is gone. Neither raises.
                feedback.update(paused=hotkeys.paused,
                                aac=aacgrid is not None and aacgrid.active,
                                recording=recorder is not None and recorder.active,
                                camera_lost=getattr(cap, "_lost", False))
                if speech_ctl is not None:
                    speech_ctl.poll(time.monotonic(), not hotkeys.paused)
                if not ok:
                    # _ResilientCapture reconnects on its own; the loop stays
                    # up so hotkeys (and the user's way out) keep working.
                    if getattr(cap, "_lost", False) and not cam_down:
                        # Really gone (not one dropped frame): nothing ticks
                        # the overlays from here, so a fullscreen grid or
                        # magnifier would sit topmost for the whole outage,
                        # and a drag would hold the button system-wide.
                        cam_down = True
                        clicker.release()
                        _quiet_layers(zoomer, aacgrid, clickmenu, dring)
                    hotkeys.feed_window_key(cv2.waitKey(50) & 0xFF)
                    keys.clear()
                    clicker.discard()
                    _ring_off(dring)
                    continue
                cam_down = False
                loop_i += 1
                now = time.monotonic()
                if prev_t is not None and now > prev_t:
                    inst = 1.0 / (now - prev_t)
                    fps_ema = inst if fps_ema == 0.0 else 0.9 * fps_ema + 0.1 * inst
                prev_t = now
                frame = cv2.flip(frame, 1)
                g = source.estimate(frame)
                if dweller is not None:
                    # Eyes closed / face lost / an overlay opened or closed
                    # under a still cursor: none of that time is a dwell.
                    layers = tuple(bool(o is not None and o.active)
                                   for o in (aacgrid, zoomer, clickmenu))
                    if (g is None or source.openness <= closed_thr
                            or layers != prev_layers):
                        dweller.restart()
                    prev_layers = layers
                if g is not None and not hotkeys.paused:
                    # closed eyes give garbage iris/lid ratios: freeze the cursor so
                    # a long-blink click lands where the user was last looking.
                    if source.openness > closed_thr:
                        hist.append(g)
                        if saccader is not None:   # never raises; off on a fault
                            saccader.step(mapper.predict, g, drift, now, hist,
                                          (fx, fy, limiter, stabilizer))
                        feat = _component_median(list(hist))
                        sx, sy = mapper.predict(feat)
                        # Calibration pixels are primary-monitor pixels;
                        # from here on everything is screen coordinates.
                        sx += ox + drift[0]
                        sy += oy + drift[1]
                        sx, sy = implicit.apply(sx, sy)  # never raises
                        sx = min(max(sx, bx + margin), bx + bw - 1 - margin)
                        sy = min(max(sy, by + margin), by + bh - 1 - margin)
                        lx, ly = limiter.update(fx(sx, now), fy(sy, now), now)
                        lx, ly = stabilizer.update(lx, ly)
                        raw_gaze = (lx, ly)
                        if snapper is not None:   # never raises
                            # Our own fullscreen layers are not Windows
                            # targets: the gaze aims inside them as it is.
                            lx, ly = snapper.adjust(
                                lx, ly, now, active=not any(
                                    o is not None and o.active
                                    for o in (aacgrid, zoomer, clickmenu)))
                        px, py = cursor.set_cursor(lx, ly, virtual=multi_mon)
                        zoomer.gaze_hint = (raw_gaze, (px, py))
                        if gaze_scroll and (loop_i % 4 == 0):
                            yr = (sy - by) / max(1, bh - 1)
                            if yr <= scroll_zone:
                                actions.scroll(scroll_speed * 120)
                            elif yr >= 1.0 - scroll_zone:
                                actions.scroll(-scroll_speed * 120)
                        if (dweller is not None and _dwell_live(aacgrid)
                                and dweller.update(px, py, now)):
                            fire_left_click(dwell=True)
                    if blinker is not None and blinker.update(source.openness, now):
                        fire_left_click()
                # finger-switch clicks are deliberate -> active even when paused
                clicker.update(now)
                if hotkeys.paused:
                    # Caregiver mode: their F5/F6/F2/F3/F4 belong to the app.
                    # They used to ALSO open the fullscreen grid (the next
                    # Space then spoke a tile), start dictation, send Ctrl+C.
                    for k in (aac_key, zoom_key, osk_key, tts_key, stt_key,
                              getattr(clickmenu, "key", "")):
                        if k:
                            keys.take(k)
                if _route_clicks(now, clicker, keys, aacgrid, zoomer,
                                 aac_key, zoom_key, clickmenu):
                    # An overlay owns the input: presses of the other keys
                    # are dropped, not saved up to fire after it closes.
                    for k in (osk_key, tts_key, stt_key):
                        keys.take(k)
                else:
                    if osk_key and keys.take(osk_key) and osk_dp.press(now):
                        if _platform.OS != "windows":
                            _open_osk_linux()
                        else:
                            try:
                                import subprocess
                                subprocess.Popen("osk.exe", shell=True)
                                print("Windows ekran klavyesi acildi.")
                            except Exception:
                                print("Ekran klavyesi acilamadi.")
                    if tts_key and keys.take(tts_key) and speaker is not None:
                        if tts_state["busy"]:
                            print("Onceki seslendirme hazirlaniyor - bekleyin.")
                        else:
                            _start_tts_worker(_foreground_is_ours())
                    if stt_key and keys.take(stt_key):
                        act = _stt_action(recorder, stt_state["busy"])
                        if act == "winh" and _platform.OS != "windows":
                            # No system dictation to fall back to on Linux.
                            print("Sesle yazma kullanilamiyor: yerel Whisper"
                                  " diktesi yuklenemedi (Win+H yalnizca"
                                  " Windows'ta var).")
                        elif act == "winh":
                            try:
                                _platform.keyboard_api().send("windows+h")
                                print("Sesle yazma (Win+H) ac/kapa gonderildi.")
                            except Exception:
                                print("Win+H gonderilemedi.")
                        elif act == "wait":
                            print("Onceki kayit hala cozuluyor - bekleyin.")
                        elif act == "stop":
                            audio = recorder.stop()      # elle bitir
                            if audio is not None:
                                _start_stt_worker(audio)
                            else:
                                print(_stt_empty_note(recorder))
                        elif not _stt_model_gate(
                                transcriber,
                                speaker.speak if speaker is not None else None,
                                print, lambda: feedback.event("error")):
                            pass        # fetching the model; said above
                        elif recorder.start():
                            print("KAYIT basladi - konusun; susunca kendi biter.")
                        else:
                            print("Mikrofon bulunamadi; sesle yazma kullanilamiyor.")
                if recorder is not None and recorder.active:
                    audio, note = _stt_poll(recorder)
                    if audio is not None:
                        _start_stt_worker(audio)
                    elif note:
                        print(note)
                if dring is not None:
                    dring.tick(now)         # after the overlays: stays on top
                if hotkeys.paused != prev_paused:
                    prev_paused = hotkeys.paused
                    print(f"DURAKLATILDI - devam: '{pause_key}' x2" if prev_paused
                          else "Devam ediliyor.")
                if hotkeys.paused:
                    fh, fw = frame.shape[:2]
                    cv2.rectangle(frame, (0, 0), (fw - 1, fh - 1), (0, 0, 255), 8)
                    cv2.putText(frame, t_ascii("driver.hud.paused", key=pause_key),
                                (14, 42),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
                # Session-long faults, on the only surface the user keeps looking at.
                wy = 78 if hotkeys.paused else 42
                for i, ln in enumerate(warn_lines):
                    cv2.putText(frame, ln, (14, wy + 26 * i),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 2)
                # Dictation HUD along the BOTTOM: the top-left corner already
                # carries the pause banner and the session-long warnings, and this
                # is the one thing the user watches while waiting for their words.
                hud_y = frame.shape[0] - 46
                if recorder is not None and recorder.active:
                    cv2.circle(frame, (28, hud_y), 11, (0, 0, 255), -1)
                    cv2.putText(frame, t_ascii("driver.hud.recording"), (48, hud_y + 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                    bar = int(200 * recorder.level)
                    cv2.rectangle(frame, (150, hud_y - 8), (150 + bar, hud_y + 6),
                                  (0, 200, 0), -1)
                elif stt_state["busy"]:
                    cv2.putText(frame, t_ascii("driver.hud.transcribing"), (24, hud_y + 8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
                cv2.putText(frame, f"FPS {fps_ema:.0f}", (14, frame.shape[0] - 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                cv2.imshow(status, frame)
                hotkeys.feed_window_key(cv2.waitKey(1) & 0xFF)
            except Exception as exc:
                if _frame_error(frame_errs, exc):
                    feedback.event("error")     # new kind of fault only
                _ring_off(dring)                # never raises
                # A fault that repeats every frame would otherwise spin
                # this loop as fast as the CPU allows.
                time.sleep(0.01)
    except KeyboardInterrupt:
        pass  # console Ctrl+C / window close: fall through to cleanup
    finally:
        if frame_errs.get("n"):
            # Repeats are not printed as they happen, so without this a fault
            # that fired thousands of times would leave one early line and look
            # like a one-off.
            print("NOT: oturum boyunca %d kare hatasi olustu (son tur: %s)."
                  % (frame_errs["n"], frame_errs.get("last")))
            print("  Ayrinti icin gaze.log dosyasina bakin.")
        zoomer.cancel()
        if aacgrid is not None:
            aacgrid.cancel()
        if dring is not None:
            dring.close()
        if snapper is not None:
            snapper.close()
        if clickmenu is not None:
            clickmenu.cancel()
        # Release the mouse button and unhook the keyboard FIRST. speaker.close()
        # waits up to 5 s for queued speech to finish; doing it earlier would
        # leave the left button physically held down (drag toggle) and the
        # global hook installed for those seconds, with the gaze cursor already
        # dead - and this user cannot reach over and click the mouse.
        clicker.release()
        keys.close()
        hotkeys.close()
        alarm.close()
        if speech_ctl is not None:
            speech_ctl.close()
        feedback.close()                # bounded (<= 1 s), after the releases
        if recorder is not None and recorder.active:
            # An input stream left open holds the microphone against every
            # other program on the machine until this process is killed.
            recorder.stop()
        if speaker is not None:
            speaker.close()
        try:
            cap.release()
            source.close()
            cv2.destroyAllWindows()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    # Started by the launcher (source mode): its "Durdur" closes our stdin,
    # which ends main() as Ctrl+C would, cleanup included. BASLAT.bat and
    # every other start never set GOZFARESI_SUPERVISED and import nothing.
    if os.environ.get("GOZFARESI_SUPERVISED") == "1":
        try:
            from eye_gaze.ui.supervisor import watch_supervisor_pipe
            watch_supervisor_pipe()
        except Exception as _exc:
            print("UYARI: baslatici borusu izlenemiyor (%s); Durdur surucuyu"
                  " zorla kapatir." % ascii(str(_exc)))
    try:
        _code = main()
    except KeyboardInterrupt:
        _code = 0
    except Exception:
        import traceback
        traceback.print_exc()
        _code = 1
    raise SystemExit(_code)
