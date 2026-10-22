# gaze/eye_gaze/hardware.py
"""What this machine has, measured once, and the settings decided from it.

  probe_cameras(max_index, timeout_s, opener)  every camera that delivers
                   frames, with the best mode it really runs at;
  screens(), scale()     monitor rectangles and the Windows scaling;
  machine()              CPU cores and RAM;
  benchmark_gaze(seconds, providers)  ms per frame of the whole gaze path
                   (MediaPipe + ONNX deep model) on a shipped face image;
  decide(profile)        deep model on/off and the Whisper size;
  load_profile() / save_profile(d)    data_dir()/makine.json;
  resolve_auto(cfg, profile)          config.yaml's "auto" lines filled in.

Only "auto" is ever resolved: a value written in config.yaml always wins
(spec 6.9) - the development machine's config.yaml names its camera mode, Whisper size
and so on explicitly, so nothing here changes how it runs.

makine.json (spec 6.6; every field optional - a missing one is simply not
decided, and the driver keeps its old behaviour for that setting):
  version              PROFILE_VERSION; another version is ignored (re-probe)
  camera               {index, backend, width, height, fps, fps_measured,
                        name}; fps is the rate asked for, fps_measured what
                        arrived during the probe
  calibration_camera   camera_identity() {index, name, width, height} of the
                       camera the calibration in use was taken with, or
                       "unknown" (a calibration older than this file) -
                       written by the driver, never by a probe
  camera_missing       {index, at}: a probe for that camera_index ("auto" =
                       any) found nothing at time.time() `at`; not probed
                       again for NO_CAMERA_RETRY_S (the wizard probes on
                       request whatever this says)
  screens, scale       [[x, y, w, h], ...] and the physical/logical ratio
  machine              {cpu_cores, ram_gb}
  gpu                  {name, provider, vram_mb, vram_free_mb, cuda}
  gaze_ms, gaze_provider   benchmark_gaze() result and the provider it ran on
  decisions            what decide() gave when the wizard measured
  sihirbaz             "atlandi": the launcher offered an unfinished wizard
                       again and the user declined (not asked again)

GPU rule: nothing in this module starts a GPU by itself. benchmark_gaze()
runs on the providers it is handed; the wizard asks the user before it hands
it a GPU one (spec 6.8), and the tests only ever hand it the CPU.

The smoke hook GOZFARESI_FAKE_CAMERA=<jpg> replaces every camera with that
picture at 30 fps (open_capture() and probe_cameras()); without the variable
that code path never runs.
"""
import collections
import json
import os
import statistics
import threading
import time

# Media Foundation's hardware transforms made opening a common USB webcam take
# ~130 s (measured); off, ~1 s. The probe opens every camera several times, so
# it needs this as much as the driver does. OpenCV reads it at import time,
# and the launcher may import this module before anything else imports cv2.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

from . import paths  # noqa: E402

PROFILE_NAME = "makine.json"
PROFILE_VERSION = 1
FAKE_CAMERA_ENV = "GOZFARESI_FAKE_CAMERA"

# Probe candidates, best first. 1280 wide at most (spec 6.6): a wider mode
# costs MediaPipe time on every frame and gives the iris nothing it needs.
CANDIDATES = ((1280, 960), (1280, 720), (640, 480))
MAX_WIDTH = 1280
MIN_FPS = 24.0          # below this the cursor visibly lags
REQUEST_FPS = 30        # what the probe (and then the driver) asks for
READ_SECONDS = 1.5      # frames counted per candidate mode
WARMUP_FRAMES = 2       # first frames after a mode change come late

NO_CAMERA_RETRY_S = 24 * 3600   # a failed probe is repeated once a day


def wall_clock():
    """time.time() for stamps kept in makine.json (camera_missing.at). Wall
    time on purpose: the stamp must survive a reboot, which a monotonic
    clock does not. A clock step only moves the next probe by that much;
    a stamp in the future (clock set back) counts as stale."""
    return time.time()

DEEP_MAX_MS = 45.0      # gaze path per frame at which the deep model stays on
STT_MEDIUM_VRAM_MB = 4096
STT_SMALL_RAM_GB = 8.0
STT_SMALL_CORES = 4

CameraInfo = collections.namedtuple(
    "CameraInfo", "index backend width height fps_measured name")


def _is_windows():
    return os.name == "nt"


def _say(message):
    """Console line (Turkish, ASCII only - the console is cp1254)."""
    try:
        from .platform import ascii_text
        print("[donanim] " + ascii_text(message))
    except Exception:
        pass


# --- cameras ----------------------------------------------------------------

def default_backends():
    """Capture backends to try, in order: Media Foundation, then DirectShow
    on Windows; V4L2 on Linux."""
    return ("msmf", "dshow") if _is_windows() else ("v4l2",)


def _cv_open(index, backend):
    import cv2
    api = {"msmf": cv2.CAP_MSMF, "dshow": cv2.CAP_DSHOW,
           "v4l2": cv2.CAP_V4L2}.get(backend, cv2.CAP_ANY)
    return cv2.VideoCapture(int(index), api)


def _release(cap):
    if cap is None:
        return
    try:
        cap.release()
    except Exception:
        pass


def _set_mode(cap, width, height, fps):
    import cv2
    if width > 0 and height > 0:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if fps > 0:
        cap.set(cv2.CAP_PROP_FPS, fps)


def open_capture(index, width, height, fps, backend=None, opener=None):
    """An opened capture in the requested mode, or the last unopened one (or
    None) when no backend opens it. `backend` (from makine.json) is tried
    first, then default_backends(). Unsupported modes fall back to the
    camera's own, as cv2 always did. With GOZFARESI_FAKE_CAMERA set: the
    fake camera, whatever was asked."""
    fake = os.environ.get(FAKE_CAMERA_ENV)
    if fake:
        return _FakeCapture(fake)
    opener = opener or _cv_open
    order = list(default_backends())
    if backend:
        order = [backend] + [b for b in order if b != backend]
    last = None
    for b in order:
        _release(last)
        last = None
        try:
            cap = opener(index, b)
        except Exception:
            cap = None
        if cap is None:
            continue
        try:
            if cap.isOpened():
                _set_mode(cap, int(width), int(height), int(fps))
                return cap
        except Exception:
            pass
        last = cap
    return last


def _camera_name(index):
    """The device's own name where the OS gives it cheaply (Linux sysfs);
    "" otherwise."""
    if _is_windows():
        return ""
    try:
        with open("/sys/class/video4linux/video%d/name" % int(index),
                  encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except Exception:
        return ""


def _measure(cap, width, height, read_seconds, clock, stop):
    """(width, height, fps) the camera really delivers after being asked for
    width x height, or None when no frame came."""
    _set_mode(cap, width, height, REQUEST_FPS)
    shape = None
    for _ in range(WARMUP_FRAMES):
        if stop.is_set():
            return None
        ok, frame = cap.read()
        if ok and frame is not None:
            shape = frame.shape
    stamps = []
    t0 = clock()
    while clock() - t0 < read_seconds and not stop.is_set():
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        shape = frame.shape
        stamps.append(clock())
    if shape is None:
        return None
    if len(stamps) >= 2 and stamps[-1] > stamps[0]:
        fps = (len(stamps) - 1) / (stamps[-1] - stamps[0])
    else:
        fps = 0.0
    # The frame itself is the truth: CAP_PROP_* echo the request on some
    # drivers.
    return (int(shape[1]), int(shape[0]), float(fps))


def _probe_one(index, opener, read_seconds, clock, stop, out):
    """Fill out["modes"] while measuring; always releases what it opened."""
    for backend in default_backends():
        if stop.is_set():
            return
        try:
            cap = opener(index, backend)
        except Exception:
            cap = None
        try:
            if cap is None or not cap.isOpened():
                continue
            out["backend"] = backend
            out["name"] = _camera_name(index)
            for w, h in CANDIDATES:
                if stop.is_set():
                    break
                mode = _measure(cap, w, h, read_seconds, clock, stop)
                if mode is not None and mode not in out["modes"]:
                    out["modes"].append(mode)
            if out["modes"]:
                return
            # Opened but not one frame: Media Foundation does that with some
            # cameras DirectShow drives fine. Try the next backend.
        except Exception:
            continue
        finally:
            # A probe that keeps the device open locks it for the driver.
            _release(cap)


def best_mode(modes):
    """The highest resolution measured at >= MIN_FPS (at most MAX_WIDTH
    wide); without one, the fastest mode."""
    if not modes:
        return None
    good = [m for m in modes if m[2] >= MIN_FPS and m[0] <= MAX_WIDTH]
    if good:
        return max(good, key=lambda m: (m[0] * m[1], m[2]))
    narrow = [m for m in modes if m[0] <= MAX_WIDTH] or list(modes)
    return max(narrow, key=lambda m: (m[2], m[0] * m[1]))


STOP_GRACE_S = 1.0      # after timeout_s: time a told-to-stop probe gets


def probe_cameras(max_index=6, timeout_s=8, opener=None,
                  read_seconds=READ_SECONDS, clock=time.monotonic,
                  indices=None):
    """[CameraInfo] for every camera 0..max_index-1 (or in `indices`) that
    delivered frames.

    Each camera is probed in its own thread for at most timeout_s seconds,
    then told to stop and given STOP_GRACE_S more. A probe that stops in
    time (a slow camera) keeps the modes it measured: it has released the
    device. One still stuck (a driver hung in a mode change) is skipped -
    it still HOLDS the device, and a camera recorded in that state would
    leave the driver waiting for it forever. Its thread releases the camera
    whenever the driver call returns. A hanging camera costs at most
    timeout_s + STOP_GRACE_S.
    """
    fake = os.environ.get(FAKE_CAMERA_ENV)
    if fake:
        cap = _FakeCapture(fake)
        try:
            if not cap.isOpened():
                return []
            return [CameraInfo(0, "fake", cap.width, cap.height,
                               float(cap.fps), "fake")]
        finally:
            cap.release()
    opener = opener or _cv_open
    found = []
    for index in (range(int(max_index)) if indices is None else indices):
        out = {"modes": [], "backend": None, "name": ""}
        stop = threading.Event()
        t = threading.Thread(target=_probe_one, daemon=True,
                             name="kamera-sondaj-%d" % index,
                             args=(index, opener, read_seconds, clock, stop,
                                   out))
        t.start()
        t.join(timeout_s)
        stop.set()
        t.join(STOP_GRACE_S)
        if t.is_alive():
            _say("kamera %d takildi (surucusu yanit vermiyor); bu kamera"
                 " KULLANILMIYOR" % index)
            continue
        mode = best_mode(list(out["modes"]))
        if mode is None:
            continue
        found.append(CameraInfo(index, out["backend"], mode[0], mode[1],
                                round(mode[2], 1), out["name"]))
    return found


def camera_record(info):
    """makine.json's "camera" entry for a CameraInfo."""
    return {"index": int(info.index), "backend": info.backend,
            "width": int(info.width), "height": int(info.height),
            "fps": REQUEST_FPS, "fps_measured": float(info.fps_measured),
            "name": info.name or ""}


CAMERA_IDENTITY = ("index", "name", "width", "height")


def camera_identity(record):
    """What a calibration depends on: which device, in which mode. Not the
    backend - the same camera re-probed through DirectShow after a one-off
    Media Foundation failure is the same picture."""
    return {k: record.get(k) for k in CAMERA_IDENTITY}


def same_camera(a, b):
    """Whether two camera records name the same device in the same mode."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    return all(a.get(k) == b.get(k) for k in CAMERA_IDENTITY)


def is_fake(record):
    """A record of the smoke camera (GOZFARESI_FAKE_CAMERA)."""
    return isinstance(record, dict) and (record.get("backend") == "fake"
                                         or record.get("fake") is True)


class _FakeCapture:
    """GOZFARESI_FAKE_CAMERA's camera: the same picture at 30 fps, paced, so
    the driver loop runs as with a webcam. The cv2.VideoCapture subset the
    driver uses."""

    def __init__(self, path, fps=30.0):
        import cv2
        import numpy as np
        self.fps = float(fps)
        self._frame = None
        try:
            # np.fromfile: the path may be non-ASCII (cv2.imread cannot).
            self._frame = cv2.imdecode(np.fromfile(path, np.uint8),
                                       cv2.IMREAD_COLOR)
        except Exception:
            self._frame = None
        if self._frame is not None:
            self.height, self.width = self._frame.shape[:2]
        else:
            self.width = self.height = 0
            _say("GOZFARESI_FAKE_CAMERA resmi okunamadi: %s" % ascii(path))
        self._next = time.monotonic()

    def isOpened(self):
        return self._frame is not None

    def read(self):
        if self._frame is None:
            return False, None
        now = time.monotonic()
        if now < self._next:
            time.sleep(self._next - now)
        self._next = max(now, self._next) + 1.0 / self.fps
        return True, self._frame.copy()

    def get(self, prop):
        import cv2
        return {cv2.CAP_PROP_FRAME_WIDTH: float(self.width),
                cv2.CAP_PROP_FRAME_HEIGHT: float(self.height),
                cv2.CAP_PROP_FPS: self.fps}.get(prop, 0.0)

    def set(self, prop, value):
        return False

    def release(self):
        pass


# --- screens and machine ----------------------------------------------------

def _windows_screens():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32")

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC,
                                           ctypes.POINTER(wintypes.RECT),
                                           proc, wintypes.LPARAM]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR,
                                       ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    found = []

    def each(hmon, _hdc, _rect, _data):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            if r.right > r.left and r.bottom > r.top:
                found.append((bool(mi.dwFlags & 1),
                              (r.left, r.top, r.right - r.left,
                               r.bottom - r.top)))
        return True

    user32.EnumDisplayMonitors(None, None, proc(each), 0)
    # Primary first, as on Linux.
    found.sort(key=lambda p: not p[0])
    return [rect for _, rect in found]


def screens():
    """[(x, y, w, h)] of every monitor, primary first, in this process's
    (logical, DPI-unaware) coordinates. Never raises."""
    try:
        if _is_windows():
            rects = _windows_screens()
        else:
            from . import platform
            backend = platform.linux_backend()
            primary = tuple(backend.primary())
            rects = [primary] + [tuple(r) for r in backend.screens()
                                 if tuple(r) != primary]
        if rects:
            return [tuple(int(v) for v in r) for r in rects]
    except Exception:
        pass
    try:
        from . import cursor
        w, h = cursor.screen_size()
        return [(0, 0, int(w), int(h))]
    except Exception:
        return [(0, 0, 1920, 1080)]


def scale():
    """Physical / logical pixels of the primary screen (1.5 at 150 %)."""
    try:
        from . import zoom
        return float(zoom.screen_scale())
    except Exception:
        return 1.0


def _ram_bytes():
    if _is_windows():
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD),
                        ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_uint64),
                        ("ullAvailPhys", ctypes.c_uint64),
                        ("ullTotalPageFile", ctypes.c_uint64),
                        ("ullAvailPageFile", ctypes.c_uint64),
                        ("ullTotalVirtual", ctypes.c_uint64),
                        ("ullAvailVirtual", ctypes.c_uint64),
                        ("ullAvailExtendedVirtual", ctypes.c_uint64)]
        k32 = ctypes.WinDLL("kernel32")
        k32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
        k32.GlobalMemoryStatusEx.restype = wintypes.BOOL
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not k32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return int(st.ullTotalPhys)
    return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))


def machine():
    """{"cpu_cores": int | None, "ram_gb": float | None}. Never raises."""
    try:
        ram = _ram_bytes()
    except Exception:
        ram = None
    return {"cpu_cores": os.cpu_count(),
            "ram_gb": round(ram / 1024.0 ** 3, 1) if ram else None}


# --- gaze benchmark ---------------------------------------------------------

def benchmark_image_path():
    """The face picture shipped for the benchmark (a public-domain portrait,
    copied from tests/data/faces)."""
    return paths.resource_path("faces", "benchmark.jpg")


def _benchmark_frame():
    import cv2
    import numpy as np
    img = cv2.imdecode(np.fromfile(benchmark_image_path(), np.uint8),
                       cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(benchmark_image_path())
    return img


def _default_estimator():
    from .gaze import GazeEstimator
    # The driver's own feature set (config.yaml defaults: per-eye, head pose
    # and translation), so the time is the time the driver will spend.
    return GazeEstimator(use_head_pose=True, head_translation=True,
                         per_eye=True)


def _default_deep(width, height, providers):
    from . import deep_onnx
    return deep_onnx.OnnxDeepGaze(width, height, providers)


def benchmark_gaze(seconds=3, providers=None, image=None,
                   estimator_factory=None, deep_factory=None,
                   clock=time.perf_counter):
    """Median ms per frame of MediaPipe + the ONNX deep model on `providers`
    (None: what "auto" would use - may be a GPU; the wizard asks first).
    Raises when either model cannot run or no face is found: a failed
    measurement must not read as a fast machine."""
    if providers is None:
        from . import accel
        providers = accel.usable(accel.providers("auto"))
    frame = _benchmark_frame() if image is None else image
    h, w = frame.shape[:2]
    est = (estimator_factory or _default_estimator)()
    deep = None
    try:
        deep = (deep_factory or _default_deep)(w, h, list(providers))

        def one():
            t = clock()
            if est.estimate(frame) is None:
                raise RuntimeError("benchmark: yuz bulunamadi")
            deep.estimate_from_landmarks(frame, est.landmarks)
            return clock() - t

        one()           # first run pays for lazy initialisation
        times = []
        end = clock() + float(seconds)
        while clock() < end or len(times) < 3:
            times.append(one())
        return statistics.median(times) * 1000.0
    finally:
        for obj in (deep, est):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass


# --- decisions --------------------------------------------------------------

def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value else None


def decide(profile):
    """The settings this profile's measurements decide; a setting without
    its measurement is left out (it then keeps the driver's old behaviour).

      deep_gaze    gaze path <= DEEP_MAX_MS per frame
      deep_device  "cpu" when that was measured on the CPU (no need to wake
                   a GPU), else "auto" (GPU, CPU when it is gone)
      stt_model    CUDA + >= 4 GB FREE VRAM: medium; else RAM >= 8 GB and
      stt_device   >= 4 cores: small; else base. Not on the GPU without
                   CUDA. VRAM that could not be read is not 4 GB.
    """
    profile = profile if isinstance(profile, dict) else {}
    out = {}
    ms = _number(profile.get("gaze_ms"))
    if ms is not None:
        out["deep_gaze"] = ms <= DEEP_MAX_MS
        if out["deep_gaze"]:
            on_cpu = profile.get("gaze_provider") == "CPUExecutionProvider"
            out["deep_device"] = "cpu" if on_cpu else "auto"
    mach = profile.get("machine")
    if isinstance(mach, dict):
        gpu = profile.get("gpu") if isinstance(profile.get("gpu"), dict) else {}
        free = _number(gpu.get("vram_free_mb"))
        ram = _number(mach.get("ram_gb"))
        cores = _number(mach.get("cpu_cores"))
        if gpu.get("cuda") is True and free is not None \
                and free >= STT_MEDIUM_VRAM_MB:
            out["stt_model"], out["stt_device"] = "medium", "cuda"
        elif ram is not None and cores is not None \
                and ram >= STT_SMALL_RAM_GB and cores >= STT_SMALL_CORES:
            out["stt_model"], out["stt_device"] = "small", "cpu"
        else:
            out["stt_model"], out["stt_device"] = "base", "cpu"
    return out


def _is_auto(value):
    return isinstance(value, str) and value.strip().lower() == "auto"


def resolve_auto(cfg, profile):
    """A copy of cfg with its "auto" camera / deep-model / dictation lines
    replaced by this profile's values. Explicit values never change; an
    "auto" the profile has no value for stays "auto".

    The camera mode is taken only for the camera it was measured on: an
    explicit camera_index: 1 with camera_width: "auto" must not get camera
    0's resolution."""
    out = dict(cfg)
    profile = profile if isinstance(profile, dict) else {}
    cam = profile.get("camera")
    if isinstance(cam, dict) and isinstance(cam.get("index"), int):
        if _is_auto(out.get("camera_index")):
            out["camera_index"] = cam["index"]
        if out.get("camera_index") == cam["index"]:
            for key, field in (("camera_width", "width"),
                               ("camera_height", "height"),
                               ("camera_fps", "fps")):
                v = cam.get(field)
                if _is_auto(out.get(key)) and isinstance(v, int) and v > 0:
                    out[key] = v
    dec = decide(profile)
    saved = profile.get("decisions")
    if isinstance(saved, dict):
        dec.update({k: v for k, v in saved.items() if k in
                    ("deep_gaze", "deep_device", "stt_model", "stt_device")})
    for key in ("deep_gaze", "deep_device", "stt_model", "stt_device"):
        if key in dec and _is_auto(out.get(key)):
            out[key] = dec[key]
    return out


# --- makine.json ------------------------------------------------------------

def profile_path():
    return os.path.join(paths.data_dir(), PROFILE_NAME)


def load_profile(path=None):
    """makine.json as a dict; {} when it is missing, unreadable, not ours or
    of another version (the caller re-probes). Never raises."""
    path = path or profile_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        _say("%s okunamadi (%s); donanim yeniden olculecek."
             % (PROFILE_NAME, type(exc).__name__))
        return {}
    if not isinstance(data, dict) or data.get("version") != PROFILE_VERSION:
        _say("%s eski ya da tanimsiz bicimde; donanim yeniden olculecek."
             % PROFILE_NAME)
        return {}
    return data


def save_profile(profile, path=None):
    """Write makine.json atomically (temp file + fsync + os.replace): a crash
    mid-write leaves the old file, never half of one. True when written;
    never raises - a profile that could not be saved is measured again."""
    path = path or profile_path()
    data = dict(profile)
    data["version"] = PROFILE_VERSION
    # The smoke camera is never remembered: a later real start would look
    # for a camera that does not exist.
    for key in ("camera", "calibration_camera"):
        if is_fake(data.get(key)):
            data.pop(key)
    tmp = path + ".tmp"
    try:
        paths.ensure_dir(os.path.dirname(path) or ".")
        text = json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True)
        with open(tmp, "w", encoding="ascii") as f:
            f.write(text + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return True
    except Exception as exc:
        try:
            os.remove(tmp)
        except Exception:
            pass
        _say("%s yazilamadi (%s); bir sonraki acilista yeniden olculecek."
             % (PROFILE_NAME, type(exc).__name__))
        return False
