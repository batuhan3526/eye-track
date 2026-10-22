# gaze/tests/test_accel.py
"""Provider selection and GPU discovery, with fakes only: no test here loads a
CUDA library, opens a DirectML device or runs nvidia-smi."""
import os
import sys
import types

import pytest

from eye_gaze import accel


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(accel, "_preloaded", {})
    monkeypatch.setattr(accel, "_handles", [])


def _platform(monkeypatch, windows):
    monkeypatch.setattr(accel, "_is_windows", lambda: windows)


def _loader(monkeypatch, loadable):
    """accel._cdll stand-in: loads names/paths whose basename is in
    `loadable`, records every attempt."""
    tried = []

    def cdll(target):
        tried.append(target)
        if os.path.basename(target) not in loadable:
            raise OSError("cannot open " + target)
        return object()
    monkeypatch.setattr(accel, "_cdll", cdll)
    return tried


@pytest.mark.parametrize("windows", [True, False])
@pytest.mark.parametrize("want", ["cpu", "CPU", " cpu "])
def test_providers_cpu_only_when_asked(monkeypatch, windows, want):
    _platform(monkeypatch, windows)
    tried = _loader(monkeypatch, set(accel._REQUIRED["onnxruntime"]["linux"]))
    assert accel.providers(want) == ["CPUExecutionProvider"]
    assert tried == [], "deep_device: cpu still loaded CUDA libraries"


def test_providers_windows_is_directml_then_cpu(monkeypatch):
    _platform(monkeypatch, True)
    for want in ("auto", "gpu", "cuda"):
        assert accel.providers(want) == ["DmlExecutionProvider",
                                         "CPUExecutionProvider"]


def test_providers_linux_without_cuda_libs_is_cpu(monkeypatch, tmp_path):
    """onnxruntime-gpu without the GPU pack: no CUDA provider is asked for."""
    _platform(monkeypatch, False)
    monkeypatch.setattr(accel, "gpu_lib_dir", lambda: str(tmp_path))
    tried = _loader(monkeypatch, set())
    assert accel.providers("auto") == ["CPUExecutionProvider"]
    assert accel.providers("gpu") == ["CPUExecutionProvider"]
    assert "libcublas.so.12" in tried     # it did look (system path)


def test_providers_linux_with_cuda_libs_is_cuda_first(monkeypatch, tmp_path):
    _platform(monkeypatch, False)
    monkeypatch.setattr(accel, "gpu_lib_dir", lambda: str(tmp_path))
    _loader(monkeypatch, set(accel._REQUIRED["onnxruntime"]["linux"]))
    assert accel.providers("auto") == ["CUDAExecutionProvider",
                                       "CPUExecutionProvider"]


def test_preload_cuda_loads_the_pack_folder_with_dependencies_in_any_order(
        monkeypatch, tmp_path):
    """cuDNN's sub-libraries need libcudnn_graph first; the loader retries
    instead of knowing the order. Files are found in nested wheel folders."""
    _platform(monkeypatch, False)
    for rel in ("cublas/lib/libcublas.so.12", "cublas/lib/libcublasLt.so.12",
                "cudnn/lib/libcudnn.so.9", "cudnn/lib/libcudnn_graph.so.9",
                "cudnn/lib/libcudnn_engines_precompiled.so.9"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
    loaded = []

    def cdll(target):
        name = os.path.basename(target)
        if name == "libcudnn_engines_precompiled.so.9" and \
                "libcudnn_graph.so.9" not in loaded:
            raise OSError("libcudnn_graph.so.9: cannot open shared object")
        if not os.path.isabs(target):
            raise OSError("not on the system path")
        loaded.append(name)
        return object()
    monkeypatch.setattr(accel, "_cdll", cdll)
    assert accel.preload_cuda(str(tmp_path)) is True
    assert set(loaded) == {"libcublas.so.12", "libcublasLt.so.12",
                           "libcudnn.so.9", "libcudnn_graph.so.9",
                           "libcudnn_engines_precompiled.so.9"}


def test_preload_cuda_without_cudnn_is_false(monkeypatch, tmp_path):
    _platform(monkeypatch, False)
    _loader(monkeypatch, {"libcublas.so.12", "libcublasLt.so.12"})
    assert accel.preload_cuda(str(tmp_path)) is False


def test_preload_cuda_uses_rtld_global(monkeypatch):
    """onnxruntime's CUDA provider dlopens libcublas by name: only a
    globally loaded copy satisfies that."""
    _platform(monkeypatch, False)
    modes = []
    monkeypatch.setattr(accel.ctypes, "CDLL",
                        lambda target, mode=None: modes.append(mode))
    accel._cdll("libcublas.so.12")
    assert modes == [accel.ctypes.RTLD_GLOBAL]


def test_preload_cuda_is_remembered(monkeypatch, tmp_path):
    _platform(monkeypatch, False)
    tried = _loader(monkeypatch, set(accel._REQUIRED["onnxruntime"]["linux"]))
    assert accel.preload_cuda(str(tmp_path)) is True
    n = len(tried)
    assert accel.preload_cuda(str(tmp_path)) is True
    assert len(tried) == n


def test_usable_drops_providers_this_onnxruntime_lacks(monkeypatch):
    """A source checkout's CPU onnxruntime has no DirectML: asking for it
    would leave .device claiming a GPU that is not in use."""
    fake = types.SimpleNamespace(
        get_available_providers=lambda: ["AzureExecutionProvider",
                                         "CPUExecutionProvider"])
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    assert accel.usable(["DmlExecutionProvider", "CPUExecutionProvider"]) == [
        "CPUExecutionProvider"]
    fake.get_available_providers = lambda: ["DmlExecutionProvider",
                                            "CPUExecutionProvider"]
    assert accel.usable(["DmlExecutionProvider", "CPUExecutionProvider"]) == [
        "DmlExecutionProvider", "CPUExecutionProvider"]


def test_usable_without_onnxruntime_is_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    assert accel.usable(["DmlExecutionProvider"]) == ["CPUExecutionProvider"]


@pytest.mark.parametrize("value, want", [
    ("auto", "auto"), ("gpu", "gpu"), ("cuda", "gpu"), ("CUDA", "gpu"),
    ("cpu", "cpu"), (None, "auto"), ("bilinmeyen", "auto"),
])
def test_deep_device_values(value, want):
    assert accel.normalize_want(value) == want


def test_gpu_info_from_nvidia_smi(monkeypatch):
    _platform(monkeypatch, False)
    monkeypatch.setattr(accel, "_run",
                        lambda cmd: "NVIDIA Test GPU, 24576, 20000\n"
                                    "NVIDIA Test GPU, 24576, 24000\n")
    monkeypatch.setattr(accel, "providers", lambda want="auto": ["CPUExecutionProvider"])
    info = accel.gpu_info()
    assert info == {"name": "NVIDIA Test GPU",
                    "provider": "CPUExecutionProvider",
                    "vram_mb": 24576, "vram_free_mb": 20000}


def test_gpu_info_windows_without_nvidia_reads_the_adapter(monkeypatch):
    _platform(monkeypatch, True)

    def no_smi(cmd):
        raise FileNotFoundError("nvidia-smi")
    monkeypatch.setattr(accel, "_run", no_smi)
    monkeypatch.setattr(accel, "_windows_adapter",
                        lambda: ("AMD Radeon RX 6600", 8192))
    monkeypatch.setattr(accel, "usable", lambda provs: list(provs))
    info = accel.gpu_info()
    assert info["name"] == "AMD Radeon RX 6600" and info["vram_mb"] == 8192
    assert info["vram_free_mb"] is None
    assert info["provider"] == "DmlExecutionProvider"


def test_gpu_info_knows_nothing_says_none(monkeypatch):
    _platform(monkeypatch, False)
    monkeypatch.setattr(accel, "_run", lambda cmd: "")
    monkeypatch.setattr(accel, "providers", lambda want="auto": ["CPUExecutionProvider"])
    info = accel.gpu_info()
    assert info["name"] is None and info["vram_mb"] is None


def test_cuda_available_via_ctranslate2(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(
        get_cuda_device_count=lambda: 1))
    monkeypatch.setitem(sys.modules, "torch", None)
    assert accel.cuda_available() is True


def test_cuda_available_via_torch(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: True)))
    assert accel.cuda_available() is True


def test_cuda_available_neither(monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(
        get_cuda_device_count=lambda: 0))
    monkeypatch.setitem(sys.modules, "torch", None)
    assert accel.cuda_available() is False


def test_onnxruntime_needs_cudart_cufft_curand_too(monkeypatch, tmp_path):
    """cuBLAS + cuDNN alone are enough for CTranslate2 but not for
    onnxruntime's CUDA provider, which would quietly run on the CPU: CUDA is
    not offered to it then."""
    _platform(monkeypatch, False)
    monkeypatch.setattr(accel, "gpu_lib_dir", lambda: str(tmp_path))
    _loader(monkeypatch, set(accel._REQUIRED["ctranslate2"]["linux"]))
    assert accel.preload_cuda(str(tmp_path), "ctranslate2") is True
    assert accel.preload_cuda(str(tmp_path), "onnxruntime") is False
    assert accel.providers("auto") == ["CPUExecutionProvider"]
    monkeypatch.setattr(accel, "usable", lambda provs: list(provs))
    monkeypatch.setattr(accel, "_run", lambda cmd: "")
    assert accel.gpu_info()["provider"] == "CPUExecutionProvider"


def test_refresh_picks_up_a_gpu_pack_unpacked_mid_session(monkeypatch, tmp_path):
    """The wizard downloads the GPU pack after the first probe said no: the
    remembered "no" must not outlive it."""
    _platform(monkeypatch, False)
    have = set()
    _loader(monkeypatch, have)
    assert accel.preload_cuda(str(tmp_path), "onnxruntime") is False
    have.update(accel._REQUIRED["onnxruntime"]["linux"])
    assert accel.preload_cuda(str(tmp_path), "onnxruntime") is False  # cached
    assert accel.preload_cuda(str(tmp_path), "onnxruntime", refresh=True) is True


@pytest.mark.parametrize("windows,need,extra,skipped", [
    (False, "onnxruntime", ["libnvJitLink.so.12", "libnvrtc.so.12",
                            "libnvrtc-builtins.so.12.8"],
     ["libnvrtc.alt.so.12", "libnvrtc-builtins.alt.so.12.8",
      "libcufftw.so.11", "libnvblas.so.12"]),
    # Windows: CTranslate2 is the only CUDA consumer; files of the Linux set
    # in the folder are not loaded.
    (True, "ctranslate2", ["cudnn_ops64_9.dll", "cudnn_graph64_9.dll"],
     ["cudart64_12.dll", "cufft64_11.dll", "curand64_10.dll",
      "nvJitLink_120_0.dll", "nvrtc64_120_0.dll", "nvblas64_12.dll"]),
])
def test_gpu_pack_dependencies_are_preloaded(monkeypatch, tmp_path, windows,
                                             need, extra, skipped):
    """The flat gpu/ folder the downloader unpacks also holds what the
    libraries load by name (cuDNN's sub-libraries; on Linux nvJitLink for
    cuFFT and NVRTC for cuDNN's runtime-compiled engines): those are loaded
    too, the unused variants are not."""
    _platform(monkeypatch, windows)
    osname = "win" if windows else "linux"
    for name in accel._REQUIRED[need][osname] + tuple(extra) + \
            tuple(skipped):
        (tmp_path / name).write_bytes(b"")
    loaded = []

    def cdll(target):
        if not os.path.isabs(target):
            raise OSError("not on the system path")
        loaded.append(os.path.basename(target))
    monkeypatch.setattr(accel, "_cdll", cdll)
    monkeypatch.setattr(accel.os, "add_dll_directory", lambda d: None,
                        raising=False)
    assert accel.preload_cuda(str(tmp_path), need) is True
    assert set(loaded) == set(accel._REQUIRED[need][osname]) | set(extra)


def test_windows_never_asks_for_the_onnxruntime_cuda_set(monkeypatch,
                                                         tmp_path):
    """onnxruntime on Windows is DirectML/CPU: no CUDA library is probed
    for it, and the CUDA answer for it is always no."""
    _platform(monkeypatch, True)
    tried = _loader(monkeypatch, set())
    assert accel.preload_cuda(str(tmp_path), "onnxruntime") is False
    assert tried == []
    assert "win" not in accel._REQUIRED["onnxruntime"]


def test_pack_check_failure_names_its_cause(monkeypatch, capsys):
    """A frozen build missing the downloader (an import error) must not hide
    behind "GPU paketi yarim": the real exception reaches the log."""
    import eye_gaze.downloader as dl

    def boom(*a, **k):
        raise ImportError("No module named 'xyz'")
    monkeypatch.setattr(dl, "pack_ready", boom)
    assert accel._pack_ready("/nowhere") is False
    err = capsys.readouterr().err
    assert "GPU paketi denetlenemedi" in err
    assert "ImportError" in err and "xyz" in err
