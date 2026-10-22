# gaze/eye_gaze/models_manifest.py
"""Everything the program may download, pinned: URL + size + SHA-256.

Nothing here is fetched or hosted by us (spec 6.9). The values were read once
from the sources' own metadata - PyPI's JSON API for the NVIDIA wheels, the
Hugging Face API (`lfs.sha256` for LFS files) for the models; the few small
non-LFS files (config.json, tokenizer.json, vocabulary.txt, the voice's
.onnx.json) were fetched and hashed, and their git blob ids checked against
the API. Changing a version means re-reading those values, never editing a
hash by hand.

  whisper-tiny|base|small|medium
      Systran/faster-whisper-<size> at a fixed commit: exactly the files
      faster-whisper's own download_model() takes for these repos
      (config.json, model.bin, tokenizer.json, vocabulary.*; they have no
      preprocessor_config.json). kind "hf_dir": the files form one folder,
      which is what WhisperModel() is given.
  nvidia-gpu-win / nvidia-gpu-linux
      The optional NVIDIA GPU pack: CUDA 12 + cuDNN 9 libraries taken out of
      the PyPI wheels (kind "wheel_libs") into model_dir()/gpu, the folder
      accel.preload_cuda() loads. Each OS gets what its CUDA consumers need:
        Windows: only CTranslate2 (faster-whisper) runs on CUDA - the deep
                 model is DirectML - so cuBLAS + cuDNN, 1.26 GB. Their DLLs
                 import nothing else at load time (read from the wheels' PE
                 import tables: cublas64_12 -> cublasLt64_12, cudnn_* ->
                 each other, otherwise KERNEL32 only; cudart is static), and
                 the cudnn wheel's only requirement is the cublas wheel.
        Linux:   CTranslate2 and onnxruntime-gpu's CUDA provider: also
                 cudart, cuFFT, cuRAND, nvJitLink, NVRTC, 1.69 GB.
      Versions are the CUDA 12.8 set PyTorch 2.8 (cu128) pins together, so
      they are known to work side by side:
        cuBLAS 12.8.4.1, cuDNN 9.10.2.21, cudart 12.8.90, cuFFT 11.3.3.83,
        cuRAND 10.3.9.90, nvJitLink 12.8.93, NVRTC 12.8.93.
      Why these:
        - CTranslate2 4.x wheels (faster-whisper) need CUDA 12 cuBLAS +
          cuDNN 9 (cuDNN 8 support ended in 4.5.0); 4.6.3+ is built with
          CUDA 12.8, so older 12.x libraries would be behind the build.
        - onnxruntime-gpu 1.21-1.26 are the CUDA 12 / cuDNN 9 builds (1.27+
          moved to CUDA 13; packaging/linux/requirements-linux.txt pins
          1.26.0); their own [cuda,cudnn] extras list cudart, cuFFT 11,
          cuRAND 10, NVRTC and cuDNN 9, i.e. the file names accel._REQUIRED
          looks for.
        - CUDA 12.8 is the first 12.x with Blackwell (RTX 50) kernels and
          still runs on R525+ drivers (minor-version compatibility).
        - nvJitLink: nvidia-cufft-cu12 11.3 declares it as a dependency
          (cuFFT links it); NVRTC: cuDNN 9's runtime-compiled engines load
          it by name, and onnxruntime's [cuda] extra lists it.
  piper-tr-dfki-medium
      rhasspy/piper-voices tr/tr_TR/dfki/medium at a fixed commit. Only for
      the case the license check (Task 15) moves the voice out of the
      package; nothing uses this entry yet.
  piper-en-us-ljspeech-medium
      rhasspy/piper-voices en/en_US/ljspeech/medium at the same commit: the
      English voice (language: en). Not in the package - offered by the
      wizard's download step when English is chosen; until it is there the
      OS's English voice speaks. Read 2026-10-21 from the HF paths-info API
      (lfs.sha256 of the .onnx; the .onnx.json fetched, hashed and its git
      blob id 73ecdec4f6ab17a437936652dfe3a925bb19e098 checked).
      Why this voice (Task 18, R40): its MODEL_CARD (blob 324d7470) names
      the LJ Speech dataset, "License: public domain", and says it was
      trained from scratch - so neither its data nor a parent voice carries
      other terms. It replaced en_US-lessac-medium, whose Blizzard 2013
      data licence could not be confirmed for redistribution.
"""
import os
from dataclasses import dataclass

KINDS = ("file", "wheel_libs", "hf_dir")


@dataclass(frozen=True)
class Item:
    """One downloadable file. dest_rel is relative to the download root and
    uses "/" on every OS. kind:
      file        the file itself is the result;
      hf_dir      one file of a model folder (the folder is the result);
      wheel_libs  a .whl whose nvidia/*/lib|bin libraries are unpacked next
                  to it, after which the wheel itself is deleted."""
    name: str
    url: str
    size: int
    sha256: str
    dest_rel: str
    kind: str


def _wheel(pkg, version, url, size, sha256):
    return Item(name="%s==%s" % (pkg, version), url=url, size=size,
                sha256=sha256,
                dest_rel="gpu/" + url.rsplit("/", 1)[1], kind="wheel_libs")


_GPU_WIN = [
    _wheel("nvidia-cublas-cu12", "12.8.4.1",
           "https://files.pythonhosted.org/packages/70/61/7d7b3c70186fb651d0fbd35b01dbfc8e755f69fd58f817f3d0f642df20c3/nvidia_cublas_cu12-12.8.4.1-py3-none-win_amd64.whl",
           567544208,
           "47e9b82132fa8d2b4944e708049229601448aaad7e6f296f630f2d1a32de35af"),
    _wheel("nvidia-cudnn-cu12", "9.10.2.21",
           "https://files.pythonhosted.org/packages/3d/90/0bd6e586701b3a890fd38aa71c387dab4883d619d6e5ad912ccbd05bfd67/nvidia_cudnn_cu12-9.10.2.21-py3-none-win_amd64.whl",
           692992268,
           "c6288de7d63e6cf62988f0923f96dc339cea362decb1bf5b3141883392a7d65e"),
]
_GPU_LINUX = [
    _wheel("nvidia-cublas-cu12", "12.8.4.1",
           "https://files.pythonhosted.org/packages/dc/61/e24b560ab2e2eaeb3c839129175fb330dfcfc29e5203196e5541a4c44682/nvidia_cublas_cu12-12.8.4.1-py3-none-manylinux_2_27_x86_64.whl",
           594346921,
           "8ac4e771d5a348c551b2a426eda6193c19aa630236b418086020df5ba9667142"),
    _wheel("nvidia-cudnn-cu12", "9.10.2.21",
           "https://files.pythonhosted.org/packages/ba/51/e123d997aa098c61d029f76663dedbfb9bc8dcf8c60cbd6adbe42f76d049/nvidia_cudnn_cu12-9.10.2.21-py3-none-manylinux_2_27_x86_64.whl",
           706758467,
           "949452be657fa16687d0930933f032835951ef0892b37d2d53824d1a84dc97a8"),
    _wheel("nvidia-cuda-runtime-cu12", "12.8.90",
           "https://files.pythonhosted.org/packages/0d/9b/a997b638fcd068ad6e4d53b8551a7d30fe8b404d6f1804abf1df69838932/nvidia_cuda_runtime_cu12-12.8.90-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
           954765,
           "adade8dcbd0edf427b7204d480d6066d33902cab2a4707dcfc48a2d0fd44ab90"),
    _wheel("nvidia-cufft-cu12", "11.3.3.83",
           "https://files.pythonhosted.org/packages/1f/13/ee4e00f30e676b66ae65b4f08cb5bcbb8392c03f54f2d5413ea99a5d1c80/nvidia_cufft_cu12-11.3.3.83-py3-none-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
           193118695,
           "4d2dd21ec0b88cf61b62e6b43564355e5222e4a3fb394cac0db101f2dd0d4f74"),
    _wheel("nvidia-curand-cu12", "10.3.9.90",
           "https://files.pythonhosted.org/packages/fb/aa/6584b56dc84ebe9cf93226a5cde4d99080c8e90ab40f0c27bda7a0f29aa1/nvidia_curand_cu12-10.3.9.90-py3-none-manylinux_2_27_x86_64.whl",
           63619976,
           "b32331d4f4df5d6eefa0554c565b626c7216f87a06a4f56fab27c3b68a830ec9"),
    _wheel("nvidia-nvjitlink-cu12", "12.8.93",
           "https://files.pythonhosted.org/packages/f6/74/86a07f1d0f42998ca31312f998bd3b9a7eff7f52378f4f270c8679c77fb9/nvidia_nvjitlink_cu12-12.8.93-py3-none-manylinux2010_x86_64.manylinux_2_12_x86_64.whl",
           39254836,
           "81ff63371a7ebd6e6451970684f916be2eab07321b73c9d244dc2b4da7f73b88"),
    _wheel("nvidia-cuda-nvrtc-cu12", "12.8.93",
           "https://files.pythonhosted.org/packages/05/6b/32f747947df2da6994e999492ab306a903659555dddc0fbdeb9d71f75e52/nvidia_cuda_nvrtc_cu12-12.8.93-py3-none-manylinux2010_x86_64.manylinux_2_12_x86_64.whl",
           88040029,
           "a7756528852ef889772a84c6cd89d41dfa74667e24cca16bb31f8f061e3e9994"),
]

# size -> (revision, {file: (bytes, sha256)})
_WHISPER = {
    "tiny": ("d90ca5fe260221311c53c58e660288d3deb8d356", {
        "config.json": (2249,
                       "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8"),
        "model.bin": (75538270,
                       "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1"),
        "tokenizer.json": (2203239,
                       "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
        "vocabulary.txt": (459861,
                       "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    }),
    "base": ("ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66", {
        "config.json": (2309,
                       "56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a"),
        "model.bin": (145217532,
                       "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9"),
        "tokenizer.json": (2203239,
                       "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
        "vocabulary.txt": (459861,
                       "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    }),
    "small": ("536b0662742c02347bc0e980a01041f333bce120", {
        "config.json": (2370,
                       "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
        "model.bin": (483546902,
                       "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
        "tokenizer.json": (2203239,
                       "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
        "vocabulary.txt": (459861,
                       "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    }),
    "medium": ("08e178d48790749d25932bbc082711ddcfdfbc4f", {
        "config.json": (2257,
                       "3622a2ddc41ec0e0fd4e68c13c6830f03b90c38d89aaad184de02c8c642cf807"),
        "model.bin": (1527906378,
                       "9b45e1009dcc4ab601eff815b61d80e60ce3fd8c74c1a14f4a282258286b51ae"),
        "tokenizer.json": (2203239,
                       "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
        "vocabulary.txt": (459861,
                       "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    }),
}
_PIPER_REV = "b1cb611417f8c6d0841becaa6c876255d9f4617c"


def _whisper_items(size):
    rev, files = _WHISPER[size]
    repo = "Systran/faster-whisper-" + size
    return [Item(name="whisper-%s/%s" % (size, fname),
                 url="https://huggingface.co/%s/resolve/%s/%s"
                     % (repo, rev, fname),
                 size=nbytes, sha256=sha,
                 dest_rel="whisper/faster-whisper-%s/%s" % (size, fname),
                 kind="hf_dir")
            for fname, (nbytes, sha) in files.items()]


_PIPER_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/%s"
              "/tr/tr_TR/dfki/medium/" % _PIPER_REV)
_PIPER = [
    Item(name="piper-tr-dfki-medium/tr_TR-dfki-medium.onnx",
         url=_PIPER_URL + "tr_TR-dfki-medium.onnx", size=63201294,
         sha256="2844717f524ab965d3fe86e60562cbb601d3e456836efcc2196cc3a14112a8fb",
         dest_rel="piper/tr_TR-dfki-medium.onnx", kind="file"),
    Item(name="piper-tr-dfki-medium/tr_TR-dfki-medium.onnx.json",
         url=_PIPER_URL + "tr_TR-dfki-medium.onnx.json", size=4960,
         sha256="13ebd7810f1b61b5027583cf3131a0a233b6ea81c38f2200ebc4ff41c3cca039",
         dest_rel="piper/tr_TR-dfki-medium.onnx.json", kind="file"),
]

_PIPER_EN_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/%s"
                 "/en/en_US/ljspeech/medium/" % _PIPER_REV)
_PIPER_EN = [
    Item(name="piper-en-us-ljspeech-medium/en_US-ljspeech-medium.onnx",
         url=_PIPER_EN_URL + "en_US-ljspeech-medium.onnx", size=63531379,
         sha256="6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22",
         dest_rel="piper/en_US-ljspeech-medium.onnx", kind="file"),
    Item(name="piper-en-us-ljspeech-medium/en_US-ljspeech-medium.onnx.json",
         url=_PIPER_EN_URL + "en_US-ljspeech-medium.onnx.json", size=4972,
         sha256="141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860",
         dest_rel="piper/en_US-ljspeech-medium.onnx.json", kind="file"),
]

MANIFEST = {
    **{"whisper-" + s: _whisper_items(s) for s in _WHISPER},
    "nvidia-gpu-win": _GPU_WIN,
    "nvidia-gpu-linux": _GPU_LINUX,
    "piper-tr-dfki-medium": _PIPER,
    "piper-en-us-ljspeech-medium": _PIPER_EN,
}

# piper voices the program downloads on demand (not in the package), by
# voice name (tts_voice / a catalog's _defaults) -> MANIFEST entry. The
# Turkish voice is not here: it comes with the program.
VOICE_PACKS = {
    "en_US-ljspeech-medium": "piper-en-us-ljspeech-medium",
}


def items(name):
    """MANIFEST[name] as a list (a single Item becomes [item])."""
    entry = MANIFEST[name]
    return list(entry) if isinstance(entry, (list, tuple)) else [entry]


def voice_pack(voice):
    """The MANIFEST entry that downloads piper voice `voice`, or None (a
    voice that comes with the program, an OS voice, a bare language code)."""
    return VOICE_PACKS.get(str(voice or "").strip())


def gpu_pack(windows=None):
    """The NVIDIA GPU pack entry for this OS (or for windows=True/False)."""
    if windows is None:
        windows = os.name == "nt"
    return "nvidia-gpu-win" if windows else "nvidia-gpu-linux"
