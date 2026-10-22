# gaze/eye_gaze/accel.py
"""Which hardware runs the onnxruntime models, and what GPU this machine has.

  providers(want)  onnxruntime execution providers, best first, for
                   want = "auto" | "gpu" | "cpu" ("cuda" is an alias of "gpu",
                   the value config.yaml has always documented).
                     Windows: DirectML (every GPU brand), then CPU.
                     Linux:   CUDA when every library onnxruntime's CUDA
                              provider links loads (preload_cuda(d,
                              "onnxruntime")), then CPU.
  preload_cuda(d, need, refresh)
                   loads the CUDA 12 libraries from the NVIDIA GPU pack
                   folder (or the system) with RTLD_GLOBAL, so onnxruntime-gpu
                   and CTranslate2 find them when they dlopen their CUDA code;
                   True when `need`'s set ("ctranslate2": cuBLAS + cuDNN;
                   "onnxruntime": also cudart, cufft, curand - Linux only,
                   Windows runs onnxruntime on DirectML/CPU) is loaded. The
                   answer is remembered per folder: after the wizard unpacks
                   the GPU pack mid-session, call it with refresh=True.
  usable(provs)    providers() narrowed to what the installed onnxruntime
                   really has (a source checkout's CPU-only wheel has no DML).
  gpu_info()       name / provider / VRAM, for the wizard and the log.
  cuda_available() a CUDA device usable from CTranslate2 or torch.

Nothing here touches the GPU itself: whether a provider then really runs on a
device is decided when a session opens (deep_onnx.OnnxDeepGaze reports it in
.device).
"""
import ctypes
import glob
import os
import subprocess
import sys

from . import paths

CPU = "CPUExecutionProvider"
DML = "DmlExecutionProvider"
CUDA = "CUDAExecutionProvider"

# What each consumer cannot start without. CTranslate2 (faster-whisper)
# needs CUDA 12 cuBLAS + cuDNN 9; onnxruntime-gpu's CUDA provider links
# cudart, cufft and curand as well - without them it quietly runs on the CPU.
# On Windows onnxruntime runs on DirectML or the CPU, never CUDA: it has no
# set there, and the Windows GPU pack carries only cuBLAS + cuDNN.
_CT2 = {
    "linux": ("libcublasLt.so.12", "libcublas.so.12", "libcudnn.so.9"),
    "win": ("cublasLt64_12.dll", "cublas64_12.dll", "cudnn64_9.dll"),
}
_REQUIRED = {
    "ctranslate2": _CT2,
    "onnxruntime": {"linux": _CT2["linux"] + (
        "libcudart.so.12", "libcufft.so.11", "libcurand.so.10")},
}
# Everything preload_cuda() loads on each OS: what any consumer there needs.
_WANTED = {
    "linux": _REQUIRED["onnxruntime"]["linux"],
    "win": _CT2["win"],
}
# Loaded too when the folder has them: cuDNN 9 dlopens its sub-libraries by
# name at run time - from a private folder the loader would not find them.
# The Linux GPU pack (models_manifest) also brings what its libraries pull in
# themselves: nvJitLink (cuFFT) and NVRTC (cuDNN's runtime-compiled engines).
_EXTRA = {
    "linux": ("libcudnn_*.so.9", "libnvJitLink.so.12", "libnvrtc.so.12",
              "libnvrtc-builtins.so.12.*"),
    "win": ("cudnn_*64_9.dll",),
}

_preloaded = {}     # libdir -> set of library names loaded from it
_handles = []       # loaded libraries (and Windows DLL-directory cookies)


def _is_windows():
    return os.name == "nt"


def normalize_want(want):
    """"auto" | "gpu" | "cpu". "cuda" means "gpu"; anything else is "auto",
    as an unknown deep_device always was."""
    w = str(want if want is not None else "auto").strip().lower()
    if w == "cuda":
        return "gpu"
    return w if w in ("auto", "gpu", "cpu") else "auto"


def gpu_lib_dir():
    """Where the optional NVIDIA GPU pack is unpacked."""
    return os.path.join(paths.model_dir(), "gpu")


def providers(want="auto"):
    """onnxruntime providers to try, best first (see module docstring)."""
    if normalize_want(want) == "cpu":
        return [CPU]
    if _is_windows():
        return [DML, CPU]
    if preload_cuda(gpu_lib_dir(), "onnxruntime"):
        return [CUDA, CPU]
    return [CPU]


def usable(provs):
    """`provs` without the ones this onnxruntime build lacks; CPU at worst.
    Asking onnxruntime for a provider it does not have only logs a warning
    and leaves .device claiming hardware that is not in use."""
    try:
        import onnxruntime as ort
        have = set(ort.get_available_providers())
    except Exception:
        return [CPU]
    out = [p for p in provs if p in have]
    return out or [CPU]


def _cdll(target):
    if _is_windows():
        return ctypes.CDLL(target)
    return ctypes.CDLL(target, mode=ctypes.RTLD_GLOBAL)


def _find(libdir, pattern):
    if not libdir or not os.path.isdir(libdir):
        return []
    return sorted(glob.glob(os.path.join(glob.escape(libdir), "**", pattern),
                            recursive=True))


def preload_cuda(libdir, need="ctranslate2", refresh=False):
    """True when `need`'s libraries ("ctranslate2" | "onnxruntime", see
    _REQUIRED) are loaded - from `libdir`, searched recursively, else from
    the system's own library path. Never raises. A consumer with no CUDA set
    on this OS (onnxruntime on Windows) is False without loading anything.

    What loaded is remembered per folder, so the gaze loop never re-probes;
    refresh=True probes again (the wizard downloaded the GPU pack after the
    first answer was "no"). Loading a library twice is harmless.
    """
    key = os.path.abspath(libdir) if libdir else ""
    osname = "win" if _is_windows() else "linux"
    required = _REQUIRED[need].get(osname)
    if required is None:
        return False
    if refresh or key not in _preloaded:
        _preloaded[key] = _load_all(libdir, key, osname)
    loaded = _preloaded[key]
    return all(name in loaded for name in required)


def _pack_ready(libdir):
    """The GPU pack folder is used only when the downloader's markers say
    every wheel was unpacked completely (a crash mid-unpack can leave the
    required names next to missing cuDNN engines)."""
    try:
        from . import downloader, models_manifest
        return downloader.pack_ready(
            models_manifest.gpu_pack(_is_windows()), libdir)
    except Exception as exc:
        # The caller then says "yarim ya da eksik" - true for the user, but a
        # frozen build missing a module (an import error here) would hide
        # behind it for good: name the real cause in the log.
        print("[gpu] GPU paketi denetlenemedi: %s: %s"
              % (exc.__class__.__name__, ascii(str(exc))[:160]),
              file=sys.stderr)
        return False


def _is_pack_dir(libdir):
    norm = lambda p: os.path.normcase(os.path.abspath(p))
    return norm(libdir) == norm(gpu_lib_dir())


def _load_all(libdir, key, osname):
    """Load every CUDA library a consumer on this OS wants; the names that
    loaded."""
    wanted = _WANTED[osname]
    if libdir and os.path.isdir(libdir) and _is_pack_dir(libdir) \
            and not _pack_ready(libdir):
        print("[gpu] GPU paketi yarim ya da eksik (%s); kullanilmiyor"
              % ascii(libdir), file=sys.stderr)
        libdir = key = None     # system-wide libraries only
    targets = {}    # library name -> what to hand the loader
    for pattern in wanted + _EXTRA[osname]:
        for f in _find(libdir, pattern):
            targets.setdefault(os.path.basename(f), f)
    for name in wanted:
        targets.setdefault(name, name)      # system-wide install
    if _is_windows() and key and os.path.isdir(key):
        try:
            for d in sorted({os.path.dirname(t) for t in targets.values()
                             if os.path.isabs(t)}):
                _handles.append(os.add_dll_directory(d))
        except (OSError, AttributeError):
            pass
    # A library whose dependency is not loaded yet fails; retry until a whole
    # pass loads nothing new instead of hard-coding the dependency order.
    loaded = set()
    progress = True
    while progress:
        progress = False
        for name, target in sorted(targets.items()):
            if name in loaded:
                continue
            try:
                _handles.append(_cdll(target))
            except OSError:
                continue
            loaded.add(name)
            progress = True
    return loaded


def _run(cmd):
    kw = {}
    if _is_windows():
        kw["creationflags"] = 0x08000000    # CREATE_NO_WINDOW
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                          **kw).stdout


def _nvidia_smi():
    """(name, total MB, free MB) of the first NVIDIA GPU, or None. NVML only:
    no CUDA context is created, so a busy GPU is not disturbed."""
    try:
        out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
                    "--format=csv,noheader,nounits"])
    except Exception:
        return None
    for line in (out or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[0]:
            try:
                return parts[0], int(float(parts[1])), int(float(parts[2]))
            except ValueError:
                return parts[0], None, None
    return None


_DISPLAY_CLASS = (r"SYSTEM\CurrentControlSet\Control\Class"
                  r"\{4d36e968-e325-11ce-bfc1-08002be10318}")


def _windows_adapter():
    """(name, total MB) of the display adapter with the most memory, from the
    registry (any brand; no WMI/PowerShell start-up cost), or None."""
    try:
        import winreg
    except ImportError:
        return None
    best = None
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS)
    except OSError:
        return None
    with root:
        for i in range(64):
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            try:
                with winreg.OpenKey(root, sub) as k:
                    name = winreg.QueryValueEx(k, "DriverDesc")[0]
                    mem = None
                    for value in ("HardwareInformation.qwMemorySize",
                                  "HardwareInformation.MemorySize"):
                        try:
                            raw = winreg.QueryValueEx(k, value)[0]
                        except OSError:
                            continue
                        if isinstance(raw, bytes):
                            raw = int.from_bytes(raw[:8], "little")
                        mem = int(raw) // (1024 * 1024)
                        break
            except (OSError, ValueError, TypeError):
                continue
            if not name or "Basic" in name:
                continue
            if best is None or (mem or 0) > (best[1] or 0):
                best = (str(name), mem)
    return best


def gpu_info():
    """{"name", "provider", "vram_mb", "vram_free_mb"}; unknown values are
    None. provider is the first one onnxruntime would really use."""
    info = {"name": None, "provider": usable(providers("auto"))[0],
            "vram_mb": None, "vram_free_mb": None}
    smi = _nvidia_smi()
    if smi:
        info["name"], info["vram_mb"], info["vram_free_mb"] = smi
    elif _is_windows():
        adapter = _windows_adapter()
        if adapter:
            info["name"], info["vram_mb"] = adapter
    return info


def cuda_available():
    """A CUDA device CTranslate2 (faster-whisper) or torch can use."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return True
    except Exception:
        pass
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False
