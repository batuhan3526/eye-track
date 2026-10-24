# gaze/eye_gaze/hardware.py
"""What this machine has, measured once, and the settings decided from it.

  probe_cameras(max_index, timeout_s, opener)  every camera that delivers
                   frames, every mode it really runs at (with and without
                   MJPG) and the one chosen for this machine;
  kept_mode(record, calibrated, size)   the mode an existing calibration
                   keeps;
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
                        name, fourcc, modes, native, legacy}; fps is the rate
                        asked for, fps_measured what arrived during the
                        probe, fourcc "MJPG" when the mode runs with it.
                        From 1.3.0 on: modes = every measured mode
                        {width, height, fps, fourcc, backend, request, ok,
                        got, cost_ms} (ok False: a stretched picture), native
                        = the camera's own size, legacy = the mode 1.2.0's
                        rule takes. A calibration in use keeps the mode it
                        was made in (kept_mode); this record's mode applies
                        to a new install and a recalibration
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

# Probe candidates: the 16:10, 16:9 and 4:3 families up to 1920 wide (spec
# 6.6 said 1280; a wide-angle webcam's face is ~190 px at 720p and ~285 px
# at 1080p, and FaceLandmarker / the deep model crop it to ~256 / 224 px -
# 4K adds little but decode time).
CANDIDATES = ((1920, 1200), (1920, 1080), (1280, 960), (1280, 800),
              (1280, 720), (960, 540), (640, 480))
MAX_WIDTH = 1920
# What 1.2.0 measured and how it chose: the mode a calibration WITHOUT a
# camera stamp was made in (legacy_mode).
LEGACY_CANDIDATES = ((1280, 960), (1280, 720), (640, 480))
LEGACY_MAX_WIDTH = 1280
WEAK_MAX_WIDTH = 1280   # a weak machine, or a frame budget no mode meets
WEAK_CORES = 4          # fewer CPU cores than this: a weak machine
MJPG = "MJPG"
MJPG_ABOVE = 640 * 480  # MJPG is asked for above this many pixels
MIN_FPS = 24.0          # below this the cursor visibly lags
REQUEST_FPS = 30        # what the probe (and then the driver) asks for
FULL_RATE = 0.9 * REQUEST_FPS   # at this rate MJPG cannot do better
MJPG_GAIN = 1.10        # MJPG beats the own format at a size only this much
                        # faster (it costs a decode on every frame)
READ_SECONDS = 1.0      # frames counted per candidate mode
WARMUP_FRAMES = 2       # first frames after a mode change come late
PROBE_TIMEOUT_S = 30    # per camera: up to ~14 modes of READ_SECONDS each
# One frame at MIN_FPS. MediaPipe + the deep model (gaze_ms, measured by
# benchmark_gaze) plus what a frame of that size costs besides them (decode,
# conversions, the status window; frame_cost_ms) must fit; without gaze_ms
# that cost alone may take a quarter of it.
FRAME_BUDGET_MS = 1000.0 / MIN_FPS
COST_SHARE = 0.25

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

# fourcc: "MJPG" when the chosen mode runs with it, "" for the camera's
# own format; modes: every measurement (mode dicts, see _measure); native:
# (w, h) the camera delivers when no size is asked for; legacy: (w, h)
# 1.2.0's rule picks from the same measurements.
CameraInfo = collections.namedtuple(
    "CameraInfo", "index backend width height fps_measured name fourcc modes"
    " native legacy", defaults=("", (), None, None))


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


def _set_mode(cap, width, height, fps, fourcc=None):
    """Ask for a mode. MJPG (fourcc) goes BEFORE the size: DirectShow and
    V4L2 offer the frame sizes and rates of the format in force when the
    size is set, and their uncompressed YUY2 runs 1280x720 at 5-10 fps on
    many webcams."""
    import cv2
    if fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    if width > 0 and height > 0:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    if fps > 0:
        cap.set(cv2.CAP_PROP_FPS, fps)


def fourcc_text(cap):
    """The capture's reported FOURCC as 4 ASCII letters/digits, "" when it
    reports none (or garbage). Only for the log: Media Foundation reports
    the format it converts to, not always the camera's."""
    try:
        import cv2
        v = int(cap.get(cv2.CAP_PROP_FOURCC))
        text = "".join(chr((v >> (8 * i)) & 0xFF) for i in range(4))
        return text if v and text.isalnum() and text.isascii() else ""
    except Exception:
        return ""


def open_capture(index, width, height, fps, backend=None, opener=None,
                 fourcc=None):
    """An opened capture in the requested mode, or the last unopened one (or
    None) when no backend opens it. `backend` (from makine.json) is tried
    first, then default_backends(); `fourcc` ("MJPG" when the probe measured
    the mode faster with it) is asked for before the size. Unsupported
    modes fall back to the camera's own, as cv2 always did. With
    GOZFARESI_FAKE_CAMERA set: the fake camera, whatever was asked."""
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
                _set_mode(cap, int(width), int(height), int(fps),
                          fourcc or None)
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


def frame_cost_ms(frame):
    """ms the driver spends on a frame of this size besides the models
    (median of 3): above 640x480 an MJPG decode of it (an MJPG mode pays it
    on every read, and Media Foundation decodes a compressed camera format
    inside read() too), the mirror flip, the RGB copy MediaPipe takes, a
    downscale like its detector's and the status window's fit. The
    size-dependent part of the per-frame cost - FaceLandmarker and the deep
    model then work on a face crop of fixed size."""
    import cv2
    from .camera_modes import window_size
    h, w = frame.shape[:2]
    jpg = None
    if w * h > MJPG_ABOVE:
        ok, buf = cv2.imencode(".jpg", frame)
        jpg = buf if ok else None
    win = window_size(w, h)
    times = []
    for _ in range(3):
        t = time.perf_counter()
        if jpg is not None:
            cv2.imdecode(jpg, cv2.IMREAD_COLOR)
        f = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
        cv2.resize(rgb, (256, 256), interpolation=cv2.INTER_LINEAR)
        cv2.resize(f, win, interpolation=cv2.INTER_LINEAR)
        times.append(time.perf_counter() - t)
    return statistics.median(times) * 1000.0


def _picture(frame):
    """(w, h) of a decoded colour frame; None for anything else (an MJPG
    buffer a backend hands out undecoded is one row of bytes)."""
    if frame is None or getattr(frame, "ndim", 0) != 3 \
            or frame.shape[2] != 3 or frame.shape[0] < 2 \
            or frame.shape[1] < 2:
        return None
    return int(frame.shape[1]), int(frame.shape[0])


def _measure(cap, width, height, read_seconds, clock, stop, fourcc=None,
             cost=None):
    """The mode the camera really delivers after being asked for width x
    height (0 x 0: nothing asked - the camera's own mode) with `fourcc`, as
    {"width", "height", "fps", "fourcc", "request", "got", "cost_ms"}; None
    when no picture came."""
    if width > 0 and height > 0:
        _set_mode(cap, width, height, REQUEST_FPS, fourcc)
    size = None
    last = None
    for _ in range(WARMUP_FRAMES):
        if stop.is_set():
            return None
        ok, frame = cap.read()
        if ok and _picture(frame):
            size, last = _picture(frame), frame
    stamps = []
    t0 = clock()
    while clock() - t0 < read_seconds and not stop.is_set():
        ok, frame = cap.read()
        if not ok or _picture(frame) is None:
            break
        size, last = _picture(frame), frame
        stamps.append(clock())
    if size is None:
        return None
    if len(stamps) >= 2 and stamps[-1] > stamps[0]:
        fps = (len(stamps) - 1) / (stamps[-1] - stamps[0])
    else:
        fps = 0.0
    try:
        ms = float((cost or frame_cost_ms)(last))
    except Exception:
        ms = 0.0
    # The frame itself is the truth: CAP_PROP_* echo the request on some
    # drivers.
    return {"width": size[0], "height": size[1], "fps": round(float(fps), 1),
            "fourcc": fourcc or "",
            "request": [int(width), int(height)] if width > 0 else None,
            "got": fourcc_text(cap), "cost_ms": round(ms, 2)}


def _area(m):
    return m["width"] * m["height"]


def _honest(m):
    """Delivered in the shape asked for (or nothing was asked)."""
    from .camera_modes import aspect, same_aspect
    req = m.get("request")
    return not req or same_aspect(aspect(m["width"], m["height"]),
                                  aspect(*req))


def native_size(modes):
    """(w, h) of the camera's own picture shape: the largest mode delivered
    as asked (or with nothing asked). Not the mode a capture opens in -
    DirectShow and V4L2 often open any sensor at 640x480 (4:3), and a
    16:10 or 16:9 camera judged by that lost every large mode. None for no
    modes."""
    honest = [m for m in modes or () if _honest(m)]
    if not honest:
        return None
    best = max(honest, key=_area)
    return best["width"], best["height"]


def judge_modes(modes):
    """Mark every mode ok / not ok (stretched): ok when its delivered aspect
    is within 2 % of what was asked or of the camera's own (native_size).
    Returns native_size."""
    from .camera_modes import aspect, same_aspect
    native = native_size(modes)
    nat = aspect(*native) if native else 0.0
    for m in modes or ():
        got = aspect(m["width"], m["height"])
        m["ok"] = bool(_honest(m) or same_aspect(got, nat))
    return native


def _probe_one(index, opener, read_seconds, clock, stop, out, cost=None):
    """Fill out["modes"] while measuring; always releases what it opened.

    First the camera's own mode (nothing asked), then every candidate in its
    own format, then - above 640x480, where its own format fell short of
    the full rate - the same with MJPG. In that order because a format once
    set cannot be unset on an open capture. probe_cameras judges the modes
    (judge_modes) once measuring is over."""
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
            modes = out["modes"] = []

            def add(m):
                if m is not None:
                    m["backend"] = backend
                    modes.append(m)
                return m
            add(_measure(cap, 0, 0, read_seconds, clock, stop, None, cost))
            plain = {}
            for w, h in CANDIDATES:
                if stop.is_set():
                    break
                plain[(w, h)] = add(_measure(cap, w, h, read_seconds, clock,
                                             stop, None, cost))
            for w, h in CANDIDATES:
                if stop.is_set():
                    break
                m = plain.get((w, h))
                # Delivered as asked at the full rate: MJPG cannot do better.
                # A size the own format does not offer at all may still
                # come with MJPG (some cameras offer 1080p only so).
                if w * h <= MJPG_ABOVE or (
                        m is not None and m["fps"] >= FULL_RATE
                        and (m["width"], m["height"]) == (w, h)):
                    continue
                add(_measure(cap, w, h, read_seconds, clock, stop, MJPG,
                             cost))
            if modes:
                return
            # Opened but not one frame: Media Foundation does that with some
            # cameras DirectShow drives fine. Try the next backend.
        except Exception:
            continue
        finally:
            # A probe that keeps the device open locks it for the driver.
            _release(cap)


def best_mode(modes, max_width=MAX_WIDTH):
    """(w, h, fps) tuples: the highest resolution measured at >= MIN_FPS (at
    most max_width wide); without one, the fastest mode."""
    if not modes:
        return None
    good = [m for m in modes if m[2] >= MIN_FPS and m[0] <= max_width]
    if good:
        return max(good, key=lambda m: (m[0] * m[1], m[2]))
    narrow = [m for m in modes if m[0] <= max_width] or list(modes)
    return max(narrow, key=lambda m: (m[2], m[0] * m[1]))


def _fastest(modes):
    return max(modes, key=lambda m: m["fps"]) if modes else None


def _by_size(modes):
    """One mode per delivered size. The camera's own format, unless MJPG
    there is at least MJPG_GAIN faster: MJPG costs a decode on every frame,
    and every calibration before 1.3.0 was made in the own format."""
    groups = {}
    for m in modes:
        groups.setdefault((m["width"], m["height"]), []).append(m)
    out = []
    for group in groups.values():
        plain = _fastest([m for m in group if not m.get("fourcc")])
        mjpg = _fastest([m for m in group if m.get("fourcc")])
        if plain is None or (mjpg is not None
                             and mjpg["fps"] >= plain["fps"] * MJPG_GAIN):
            out.append(mjpg)
        else:
            out.append(plain)
    return out


def mode_at(modes, size):
    """The measured mode to open at exactly `size` (accepted or not - a
    calibration made in it fits it), or None. The own format whenever it
    runs at >= MIN_FPS there: a calibration from before 1.3.0 was made in
    it."""
    try:
        want = (int(size[0]), int(size[1]))
        hits = [m for m in modes or () if isinstance(m, dict)
                and (m.get("width"), m.get("height")) == want]
    except Exception:
        return None
    plain = _fastest([m for m in hits if not m.get("fourcc")
                      and m.get("fps", 0) >= MIN_FPS])
    if plain is not None:
        return plain
    hits = _by_size(hits)
    return hits[0] if hits else None


def frame_budget(profile):
    """(max_width, ms a frame's own cost may take) on this machine.

    gaze_ms measured (benchmark_gaze): what is left of one frame at MIN_FPS
    after MediaPipe and the deep model. Not measured: a quarter of that
    frame, and a weak machine (fewer than WEAK_CORES cores) keeps to 1280
    wide."""
    profile = profile if isinstance(profile, dict) else {}
    ms = _number(profile.get("gaze_ms"))
    mach = profile.get("machine")
    cores = _number(mach.get("cpu_cores")) if isinstance(mach, dict) else None
    if ms is not None:
        return MAX_WIDTH, FRAME_BUDGET_MS - ms
    if cores is not None and cores < WEAK_CORES:
        return WEAK_MAX_WIDTH, FRAME_BUDGET_MS * COST_SHARE
    return MAX_WIDTH, FRAME_BUDGET_MS * COST_SHARE


def choose_mode(modes, profile=None):
    """The mode to run (a mode dict), or None for no modes.

    Among the accepted modes (judge_modes) at >= MIN_FPS: the largest whose
    frame cost (frame_cost_ms) fits the machine's budget (frame_budget) -
    when it fits none, the largest up to 1280 wide. Widest first (the iris
    resolution); at the same width the camera's own aspect (the largest
    accepted mode's), then the larger picture. Without a mode at MIN_FPS:
    the fastest, as 1.2.0 did."""
    from .camera_modes import aspect, same_aspect
    if not modes:
        return None
    max_w, cost_cap = frame_budget(profile)
    ok = _by_size([m for m in modes if m.get("ok", True)]) \
        or _by_size(list(modes))
    fast = [m for m in ok if m["fps"] >= MIN_FPS and m["width"] <= max_w]
    if fast:
        pool = [m for m in fast if m.get("cost_ms", 0.0) <= cost_cap] \
            or [m for m in fast if m["width"] <= WEAK_MAX_WIDTH] or fast
        big = max(ok, key=_area)
        nat = aspect(big["width"], big["height"])
        return max(pool, key=lambda m: (
            m["width"], same_aspect(aspect(m["width"], m["height"]), nat),
            _area(m), m["fps"]))
    narrow = [m for m in ok if m["width"] <= max_w] or ok
    return max(narrow, key=lambda m: (m["fps"], _area(m)))


def rechoose(record, profile):
    """makine.json's camera record with its mode chosen again from its own
    measured modes for `profile` (the wizard measures gaze_ms only after the
    camera step). A record without measured modes comes back as it was."""
    if not isinstance(record, dict) or not isinstance(record.get("modes"),
                                                      list):
        return record
    m = choose_mode(record["modes"], profile)
    if m is None:
        return record
    out = dict(record)
    out.update(width=int(m["width"]), height=int(m["height"]),
               fourcc=m.get("fourcc") or "",
               fps_measured=float(m.get("fps", 0.0)))
    return out


def legacy_mode(modes):
    """(w, h) 1.2.0's probe would have chosen from these measurements: the
    camera's own format, its three requests, 1280 wide at most."""
    old = []
    for m in modes or ():
        req = m.get("request")
        if m.get("fourcc") or not req or tuple(req) not in LEGACY_CANDIDATES:
            continue
        t = (m["width"], m["height"], m["fps"])
        if t not in old:
            old.append(t)
    best = best_mode(old, LEGACY_MAX_WIDTH)
    return (best[0], best[1]) if best else None


def _mode_line(m):
    """One measured mode for the console (ASCII)."""
    text = "%dx%d%s %.0f fps" % (m["width"], m["height"],
                                 " " + m["fourcc"] if m.get("fourcc") else "",
                                 m["fps"])
    req = m.get("request")
    if req is None:
        text += " (kendi modu)"
    elif not m.get("ok", True):
        text += " (GERILMIS: istenen %dx%d, kullanilmaz)" % tuple(req)
    elif tuple(req) != (m["width"], m["height"]):
        text += " (istenen %dx%d)" % tuple(req)
    return text


STOP_GRACE_S = 1.0      # after timeout_s: time a told-to-stop probe gets


def probe_cameras(max_index=6, timeout_s=PROBE_TIMEOUT_S, opener=None,
                  read_seconds=READ_SECONDS, clock=time.monotonic,
                  indices=None, profile=None, cost=None):
    """[CameraInfo] for every camera 0..max_index-1 (or in `indices`) that
    delivered frames, each in the mode choose_mode() picks for this machine
    (`profile`: makine.json's measurements; None = only this machine's
    cores). Every measured mode is logged and kept in .modes; `cost`
    replaces frame_cost_ms (tests).

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
            mode = {"width": cap.width, "height": cap.height,
                    "fps": float(cap.fps), "fourcc": "", "request": None,
                    "got": "", "cost_ms": 0.0, "backend": "fake", "ok": True}
            return [CameraInfo(0, "fake", cap.width, cap.height,
                               float(cap.fps), "fake", "", (mode,),
                               (cap.width, cap.height), None)]
        finally:
            cap.release()
    if profile is None:
        profile = {"machine": machine()}
    opener = opener or _cv_open
    found = []
    for index in (range(int(max_index)) if indices is None else indices):
        out = {"modes": [], "backend": None, "name": ""}
        stop = threading.Event()
        t = threading.Thread(target=_probe_one, daemon=True,
                             name="kamera-sondaj-%d" % index,
                             args=(index, opener, read_seconds, clock, stop,
                                   out, cost))
        t.start()
        t.join(timeout_s)
        stop.set()
        t.join(STOP_GRACE_S)
        if t.is_alive():
            _say("kamera %d takildi (surucusu yanit vermiyor); bu kamera"
                 " KULLANILMIYOR" % index)
            continue
        modes = list(out["modes"])
        native = judge_modes(modes)
        mode = choose_mode(modes, profile)
        if mode is None:
            continue
        _say("kamera %d (%s) olculen modlar: %s" % (
            index, out["backend"], "; ".join(_mode_line(m) for m in modes)))
        _say("kamera %d: secilen %s" % (index, _mode_line(mode)))
        found.append(CameraInfo(index, out["backend"], mode["width"],
                                mode["height"], round(mode["fps"], 1),
                                out["name"], mode.get("fourcc") or "",
                                tuple(modes), native, legacy_mode(modes)))
    return found


def camera_record(info):
    """makine.json's "camera" entry for a CameraInfo: the chosen mode and,
    from 1.3.0 on, every measured mode, the camera's own size and the mode
    1.2.0's rule takes (kept_mode reads them)."""
    rec = {"index": int(info.index), "backend": info.backend,
           "width": int(info.width), "height": int(info.height),
           "fps": REQUEST_FPS, "fps_measured": float(info.fps_measured),
           "name": info.name or "", "fourcc": info.fourcc or ""}
    if info.modes:
        rec["modes"] = [dict(m) for m in info.modes]
    if info.native:
        rec["native"] = [int(v) for v in info.native]
    if info.legacy:
        rec["legacy"] = [int(v) for v in info.legacy]
    return rec


def kept_mode(record, calibrated, cal_size):
    """The mode an existing calibration keeps instead of the record's own,
    as a mode dict (width, height, fourcc, fps), or None (open the record's
    mode).

    A calibration is fitted on the picture of one mode: another size or
    aspect moves every iris and head feature. So with a calibration in use
    (`calibrated`):
      - cal_size, its camera stamp (w, h): that mode, when the camera was
        measured delivering it;
      - no stamp (a calibration older than stamps), or a stamp this probe
        did not measure (a 1.2.0 stamp from a driver echoing the request, a
        probe cut short): the mode 1.2.0's rule picks (record["legacy"]) -
        the one that install has been running in;
      - neither measured: the record's mode (the driver warns about the
        mismatch as before);
      - a record without measured modes (1.2.0's makine.json): its own mode
        IS that install's mode - None.
    At the kept size the own format is used whenever it runs at >= MIN_FPS
    (mode_at): that is the format a calibration before 1.3.0 was made in.
    The probe's newer choice applies to a new install and a recalibration.
    """
    if not calibrated or not isinstance(record, dict):
        return None
    modes = record.get("modes")
    if not isinstance(modes, list) or not modes:
        return None
    try:
        own = (int(record.get("width")), int(record.get("height")),
               str(record.get("fourcc") or ""))
    except Exception:
        return None
    for size in (cal_size, record.get("legacy")):
        if not size:
            continue
        m = mode_at(modes, size)
        if m is None:
            continue
        if (m["width"], m["height"], m.get("fourcc") or "") == own:
            return None
        return m
    return None


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

    def getBackendName(self):
        return "fake"

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
