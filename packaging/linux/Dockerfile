# packaging/linux/Dockerfile
# One image for the Linux test suite (packaging/linux/test.sh) and, later, the
# AppImage build. ubuntu:22.04 = the oldest glibc we promise to run on, so a
# binary built here also starts on newer distributions.
#
# Tag: gozfaresi-linux:22.04 (test.sh builds it; the layer cache makes rebuilds
# cheap). No GPU: run it without --gpus; onnxruntime-gpu then uses the CPU.
# Pinned by digest (the 22.04 tag of 2026-10-21) so a release build gets the
# same base as the tested one; bump it on purpose, never by accident.
FROM ubuntu:22.04@sha256:0a9887931b0e38af2e3487ed39e29436b0535c4f3f52e57745baaf79d1898956

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Etc/UTC \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CUDA_VISIBLE_DEVICES=""

# 22.04 ships Python 3.10; the program is 3.11 everywhere, so deadsnakes.
#   libgl1 + libglib2.0-0  opencv / mediapipe shared-library deps
#   libportaudio2          sounddevice
#   xvfb + xauth           xvfb-run (Tk and the X11 backend need a display)
#   xclip, xdotool         X11 clipboard / window tools used by the Linux backend
#   build-essential + python3.11-dev   evdev has no wheel, it is compiled
#   libegl1 + libgles2     mediapipe's FaceLandmarker (GL context, even on CPU)
#   libxcb1 -randr0 -render0 -shm0 -xfixes0
#                          mss 10 (the magnifier's X11 grab) dlopens them at
#                          its first grab, not at import; every desktop has
#                          them, this minimal image did not (render was
#                          missing and every grab failed)
#   file                   appimagetool refuses to run without it (derle.sh)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates gpg-agent software-properties-common \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        python3.11 python3.11-tk python3.11-venv python3.11-dev \
        build-essential \
        libgl1 libglib2.0-0 libportaudio2 \
        libegl1 libgles2 \
        libxcb1 libxcb-randr0 libxcb-render0 libxcb-shm0 libxcb-xfixes0 \
        xvfb xauth xclip xdotool file \
 && rm -rf /var/lib/apt/lists/*

RUN python3.11 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

COPY requirements-linux.txt /tmp/requirements-linux.txt
# faster-whisper and piper-tts depend on the CPU "onnxruntime" wheel, which
# installs into the same onnxruntime/ folder as onnxruntime-gpu: whichever is
# written last wins, and uninstalling either later deletes files of the other.
# So: install everything, drop the CPU wheel, then lay the GPU wheel down
# again on its own.
# LATER TASKS: any further `pip install` (here or in a build script) can pull
# the CPU "onnxruntime" back in as a dependency. Repeat this uninstall +
# --force-reinstall --no-deps step after it, and re-run the
# CPUExecutionProvider assertion below - otherwise onnxruntime may be half
# of each wheel and fail only at runtime.
RUN pip install --upgrade pip \
 && pip install -r /tmp/requirements-linux.txt \
 && pip uninstall -y onnxruntime \
 && pip install --force-reinstall --no-deps \
        "$(grep -E '^onnxruntime-gpu==' /tmp/requirements-linux.txt)" \
 && python -c "import onnxruntime as o; assert 'CPUExecutionProvider' in o.get_available_providers(), o.get_available_providers()" \
 && python -c "import cv2, mediapipe, numpy, yaml, PIL, tkinter, faster_whisper, piper, sounddevice, Xlib, evdev, mss, PyInstaller, pytest"

# The X11 grab libraries above must all resolve (gaze/tests/
# test_overlays_linux.py grabs for real), and so must mediapipe's GL ones.
RUN python -c "import ctypes.util as u; missing = [n for n in ('xcb', 'xcb-randr', 'xcb-render', 'xcb-shm', 'xcb-xfixes', 'EGL', 'GLESv2') if not u.find_library(n)]; assert not missing, missing"

# Tests run as an ordinary user: the source tree is made read-only for it, so
# a test that writes into gaze/ (on Windows: the user's live calibration
# folder) fails here instead of passing silently.
RUN useradd --create-home --shell /bin/bash tester
WORKDIR /home/tester
