# -*- mode: python ; coding: utf-8 -*-
# packaging/gozfaresi.spec
#
# One onedir build of gaze/gozfaresi.py for the OS it runs on (Windows or
# Linux): the launcher and, as `GozFaresi --surucu`, the driver.
#
#   pyinstaller --noconfirm --clean packaging/gozfaresi.spec
#
# Run by packaging/windows/derle.ps1 and packaging/linux/derle.sh, after
# packaging/hazirla.py staged the files that are not in git (MediaPipe's
# model, the piper voice + espeak-ng-data) into GOZFARESI_STAGE (default
# packaging/out/stage).
#
# Why the choices (spec 6.1, 6.9, 7):
#  - onedir, never onefile: no unpacking of hundreds of MB at every start,
#    fewer antivirus complaints. UPX off for the same reason.
#  - console=False: the launcher is a Tk window; the driver is its child and
#    its output goes to gaze.log (gaze_driver._setup_logging), which the
#    launcher's "Gunlugu ac" shows.
#  - The default PyInstaller manifest, no DPI-awareness flag: the process
#    must stay DPI-UNAWARE, calibrations are in logical pixels.
#  - No torch / ptgaze / openai-whisper / coqui: a package always runs the
#    ONNX deep model and faster-whisper (spec 6.9).
#  - matplotlib is NOT excluded although the plan listed it: mediapipe
#    0.10.35 imports matplotlib.pyplot at `import mediapipe.tasks.python.
#    vision` (vision/drawing_utils.py, top level), so excluding it would
#    stop the camera path at its first import.
import os
import re
import sys

from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs,
                                     collect_submodules)

IS_WIN = sys.platform == "win32"
# GozFaresi.exe on Windows; lowercase on Linux, the convention there (the
# AppImage's AppRun and the tar.gz start "gozfaresi").
NAME = "GozFaresi" if IS_WIN else "gozfaresi"
ROOT = os.path.dirname(os.path.abspath(SPECPATH))
if os.path.basename(os.path.abspath(SPECPATH)) != "packaging":
    raise SystemExit("gozfaresi.spec must stay in packaging/")
GAZE = os.path.join(ROOT, "gaze")
STAGE = os.environ.get("GOZFARESI_STAGE") or os.path.join(
    ROOT, "packaging", "out", "stage")

with open(os.path.join(GAZE, "eye_gaze", "__init__.py"), encoding="utf-8") as f:
    VERSION = re.search(r'^__version__\s*=\s*"([0-9.]+)"', f.read(), re.M).group(1)


def _need(path, why):
    if not os.path.exists(path):
        raise SystemExit("eksik: %s (%s)" % (path, why))
    return path


# --- data ---------------------------------------------------------------------
# resources/** file by file: only what git (or the ONNX build) put there, never
# a stray cache or __pycache__. The ONNX model's .sha256 sidecar is mandatory:
# a frozen build refuses a model without it (deep_onnx.load_model_bytes).
_ONNX = os.path.join(GAZE, "resources", "models", "eth-xgaze-resnet18.onnx")
_need(_ONNX, "tools/derin_onnx_cevir.py")
_need(_ONNX + ".sha256", "tools/derin_onnx_cevir.py")
_SKIP_DIRS = {"__pycache__"}
datas = []
for root, dirs, files in os.walk(os.path.join(GAZE, "resources")):
    dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
    rel = os.path.relpath(root, GAZE)
    for name in sorted(files):
        if name.endswith((".pyc", ".tmp", ".part", ".lock")):
            continue
        datas.append((os.path.join(root, name), rel))

# Staged by hazirla.py (not in git).
datas.append((_need(os.path.join(STAGE, "models", "face_landmarker.task"),
                    "hazirla.py sahne"), os.path.join("resources", "models")))
_PIPER = _need(os.path.join(STAGE, "piper"), "hazirla.py sahne")
for root, dirs, files in os.walk(_PIPER):
    dirs.sort()
    rel = os.path.join("resources", "piper", os.path.relpath(root, _PIPER))
    for name in sorted(files):
        datas.append((os.path.join(root, name), os.path.normpath(rel)))
_need(os.path.join(_PIPER, "espeak-ng-data", "phontab"), "hazirla.py sahne")

# The launcher copies it into the data folder at the first start
# (ui.default_config_path(): the bundle root).
datas.append((_need(os.path.join(GAZE, "config.default.yaml"), "git"), "."))
# Linux permission installer and its udev rule (linux_evdev looks in
# _MEIPASS/linux). Shipped on Windows too: two small text files, and the
# portable zip then holds the same tree as the Linux tar.gz.
for name in ("izin-kur.sh", "99-gozfaresi.rules"):
    datas.append((_need(os.path.join(GAZE, "linux", name), "git"), "linux"))

datas += collect_data_files("mediapipe")
# libmediapipe.so / .dll, which mediapipe opens with ctypes (no hook covers
# it: on Linux it was missing and FaceLandmarker could not start).
binaries = collect_dynamic_libs("mediapipe")
# faster-whisper's Silero VAD model (assets/*.onnx).
datas += collect_data_files("faster_whisper")
# certifi's cacert.pem: the downloader merges it with the system store.
datas += collect_data_files("certifi")

# --- code ---------------------------------------------------------------------
# Every eye_gaze module: the platform backends and many features are imported
# lazily (inside functions or through a name table), which the analysis
# cannot always follow.
hiddenimports = ["gaze_driver"] + collect_submodules("eye_gaze")
# mediapipe finds libmediapipe.so/.dll through importlib.resources.files(
# "mediapipe.tasks.c"), a package nothing imports: without it the frozen
# Linux driver died at FaceLandmarker creation (ModuleNotFoundError).
hiddenimports += ["mediapipe.tasks.c"]
if IS_WIN:
    # derle.ps1 generates the UIAutomation and SAPI wrappers into the build
    # venv's comtypes/gen first; a frozen comtypes cannot write them later.
    hiddenimports += collect_submodules("comtypes.gen") + [
        "comtypes.client", "comtypes.stream"]
    hiddenimports += collect_submodules("winsdk.windows.media.speechsynthesis")
    hiddenimports += collect_submodules("winsdk.windows.storage.streams")
else:
    hiddenimports += ["evdev", "evdev.ecodes", "evdev.uinput"]
    # python-xlib loads its extensions and keysym tables by name.
    hiddenimports += collect_submodules("Xlib")
    hiddenimports += collect_submodules("mss")

excludes = ["torch", "torchvision", "torchaudio", "ptgaze", "whisper", "TTS",
            "scipy", "timm", "omegaconf", "onnx", "pytest", "IPython"]
# Left out so their (L)GPL native code never ships (Task 18, R41):
#  - av (PyAV): its own FFmpeg with libx264/libx265, GnuTLS, GMP ...;
#    faster-whisper only needs it to decode files, the program feeds it numpy
#    audio. pyi_rth_av_yok.py puts a stub in its place for `import av`.
#  - readline (GNU readline, GPL-3.0) and _dbm (Berkeley DB, Sleepycat): stdlib
#    extensions for an interactive prompt and dbm files, never used here;
#    every importer treats them as optional.
excludes += ["av", "readline", "_dbm"]
# piper-tts and its espeak-ng bridge (GPL-3.0) never load into this process:
# it also holds CTranslate2 (Intel oneMKL, Intel OpenMP) and, on Linux,
# OpenCV's OpenSSL 1.1.1 - not GPL-compatible. They run in the voice worker
# below (Task 18b, R44); eye_gaze/ses_isci.py talks to it over a pipe.
excludes += ["piper"]

a = Analysis(
    [os.path.join(GAZE, "gozfaresi.py")],
    pathex=[GAZE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(ROOT, "packaging", "pyi_rth_av_yok.py")],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
# matplotlib's hook brings its sample data (.npz arrays): never used, and
# the build scripts refuse any .npz in a package (calibration samples are).
a.datas = [d for d in a.datas
           if not d[0].replace("\\", "/").startswith(
               "matplotlib/mpl-data/sample_data/")]
# Binaries the program never loads, dropped for licence reasons (R39/R41;
# hazirla.py denetle refuses a bundle that still has them):
#  - cv2/opencv_videoio_ffmpeg*.dll (Windows): OpenCV's optional FFmpeg
#    plugin (LGPL), loaded only to open video FILES or streams; cameras open
#    through MSMF / DirectShow, which are built into cv2.
#  - libportaudio64bit-asio.dll (Windows): PortAudio built with Steinberg's
#    ASIO SDK (proprietary terms); sounddevice loads it only when
#    SD_ENABLE_ASIO is set, the plain libportaudio64bit.dll otherwise.
#  - libmvec.so.1 (Linux): a part of glibc (LGPL), present on every system
#    the package runs on (glibc >= 2.35) and never bundled from the build
#    image, like the rest of glibc. (PyAV was what pulled it in.)
#  - _dbm + libdb-5.3.so (Linux): Berkeley DB under the Sleepycat licence,
#    kept by the analysis despite the "_dbm" exclude (Task 18 build); the
#    dbm package falls back to dbm.dumb without it, and nothing here uses
#    dbm at all.
def _dropped(dest):
    name = dest.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return (name.startswith(("opencv_videoio_ffmpeg", "libdb-", "_dbm."))
            or name.endswith("-asio.dll") or name == "libmvec.so.1")


a.binaries = [b for b in a.binaries if not _dropped(b[0])]
a.datas = [d for d in a.datas if not _dropped(d[0])]
pyz = PYZ(a.pure)

_version_file = None
if IS_WIN:
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo)
    _v = tuple(int(x) for x in (VERSION.split(".") + ["0"] * 4)[:4])
    _version_file = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_v, prodvers=_v, mask=0x3F, flags=0x0,
                          OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable("041F04B0", [
                StringStruct("CompanyName", "eye-track project"),
                StringStruct("FileDescription", "Göz Faresi - göz ile fare"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "GozFaresi"),
                # Same wording as LICENSE / UCUNCU-TARAF-LISANSLAR.txt: our
                # code and the frozen bundle are both GPL-3.0-or-later (R35).
                StringStruct("LegalCopyright",
                             "Copyright (C) 2026 Göz Faresi (eye-track) "
                             "projesi. GPL-3.0-or-later (LICENSE.txt)"),
                StringStruct("OriginalFilename", "GozFaresi.exe"),
                StringStruct("ProductName", "Göz Faresi"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x041F, 1200])]),
        ])

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(GAZE, "resources", "icon.ico") if IS_WIN else None,
    version=_version_file,
    uac_admin=False,
)
# --- the voice worker (Task 18b, R44) ----------------------------------------
# gozfaresi_ses.py from an Analysis of its own: piper, its espeak bridge,
# onnxruntime (CPU provider only), numpy and the pipe protocol - nothing of
# the program and none of the libraries that are not GPL-compatible. Its
# PYZ is its own (embedded in its executable); its native files land in the
# same _internal, where only what it imports is ever loaded.
# hazirla.py denetle checks both PYZ sets and refuses a mix.
SES_NAME = "GozFaresi-Ses" if IS_WIN else "gozfaresi-ses"
SES_EXCLUDES = excludes + [
    "cv2", "ctranslate2", "faster_whisper", "mediapipe", "matplotlib",
    "tkinter", "_tkinter", "PIL", "sounddevice", "keyboard", "comtypes",
    "winsdk", "Xlib", "evdev", "mss", "yaml", "certifi", "huggingface_hub",
    "tokenizers", "gaze_driver",
    # no OpenSSL of any version in the voice process (hashlib falls back to
    # its built-in hashes without _hashlib)
    "ssl", "_ssl", "_hashlib"]
SES_EXCLUDES = [m for m in SES_EXCLUDES if m != "piper"]
ses = Analysis(
    [os.path.join(GAZE, "gozfaresi_ses.py")],
    pathex=[GAZE],
    binaries=[],
    datas=[],
    hiddenimports=["piper", "piper.voice", "piper.config",
                   "piper.phonemize_espeak", "piper.espeakbridge"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=SES_EXCLUDES,
    noarchive=False,
    optimize=0,
)
ses.binaries = [b for b in ses.binaries if not _dropped(b[0])]
ses.datas = [d for d in ses.datas if not _dropped(d[0])]
ses_pyz = PYZ(ses.pure)

# Each executable's own native files (the analysis' binary dependency set,
# extension modules included), for hazirla.py denetle: the voice worker's
# set must hold no MKL / Intel OpenMP / OpenSSL / OpenCV / CTranslate2, the
# program's no espeak bridge. Written next to the onedir folder, never into
# a package.
import json
os.makedirs(DISTPATH, exist_ok=True)
with open(os.path.join(DISTPATH, NAME + "-ikililer.json"), "w",
          encoding="ascii") as _f:
    json.dump({"ana": sorted(b[0].replace("\\", "/") for b in a.binaries),
               "ses": sorted(b[0].replace("\\", "/") for b in ses.binaries)},
              _f, indent=1)
ses_exe = EXE(
    ses_pyz,
    ses.scripts,
    [],
    exclude_binaries=True,
    name=SES_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A console program on purpose: its stdin/stdout ARE the pipe. The
    # program starts it with CREATE_NO_WINDOW, so no window ever shows.
    console=True,
    icon=os.path.join(GAZE, "resources", "icon.ico") if IS_WIN else None,
    version=_version_file,
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    ses_exe,
    ses.binaries,
    ses.datas,
    strip=False,
    upx=False,
    name=NAME,
)
