#!/usr/bin/env bash
# packaging/linux/test.sh [pytest args...]
#
# Runs the gaze/ test suite on Linux: Docker ubuntu:22.04, Python 3.11, under
# Xvfb, without GPU and without network. Works from Git Bash on Windows and
# from a Linux shell. Extra arguments go to pytest, e.g.
#     bash packaging/linux/test.sh -k linux_x11
#
# The source is STREAMED into the container (tar over stdin), not bind-mounted:
# Docker Desktop's translation of a non-ASCII profile path
# (a Turkish user name such as "Örnek") is not trustworthy. What is sent is the
# working tree as git sees it - tracked files plus new, not-ignored ones - so
# uncommitted changes are tested too, while caches, calibration files and
# downloaded models (all git-ignored) stay home - except the build outputs
# listed in `built` below.
#
# Environment:
#   GOZFARESI_LINUX_IMAGE  image tag (default gozfaresi-linux:22.04)
#   GOZFARESI_SKIP_BUILD=1 use the existing image, do not (re)build it
#   GOZFARESI_TEST_OUT=DIR also write junit.xml and copy it to DIR
set -euo pipefail

# Git Bash would rewrite /home/... arguments into C:/Program Files/Git/...
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
image=${GOZFARESI_LINUX_IMAGE:-gozfaresi-linux:22.04}
out=${GOZFARESI_TEST_OUT:-}

if [ "${GOZFARESI_SKIP_BUILD:-}" != "1" ]; then
    # The build context is streamed too (same path concern as above).
    tar -C "$here" -cf - Dockerfile requirements-linux.txt \
        | docker build -t "$image" -
fi

name="gozfaresi-test-$$-$RANDOM"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

junit=()
if [ -n "$out" ]; then
    junit=(--junitxml=/home/tester/out/junit.xml)
fi

# Inside: unpack, make the tree read-only (tests must not write into gaze/ -
# on Windows that folder holds the user's calibration), run pytest on a
# virtual screen. -rs lists every skip with its reason.
inner='
set -e
mkdir -p src out
tar -xf - -C src
chmod -R a-w src
cd src/gaze
xvfb-run -a -s "-screen 0 1280x1024x24" \
    python -m pytest -q -rs -p no:cacheprovider "$@"
'

# --init: bash execs xvfb-run as its last command, and as PID 1 xvfb-run
# never sees Xvfb's "ready" signal - it hung forever with pytest not started.
#
# Built, git-ignored files the tests use when present: the ONNX deep gaze
# model (gaze/tools/derin_onnx_cevir.py) - without it the ONNX inference
# tests skip on Linux too.
built=(gaze/resources/models/eth-xgaze-resnet18.onnx
       gaze/resources/models/eth-xgaze-resnet18.onnx.sha256)
status=0
(cd "$repo" \
    && { git ls-files -z --cached --others --exclude-standard -- gaze
         for f in "${built[@]}"; do
             if [ -f "$f" ]; then printf '%s\0' "$f"; fi
         done; } \
    | tar --null --ignore-failed-read -T - -cf -) \
  | docker run -i --init --name "$name" \
        --network none \
        --user tester \
        -e CUDA_VISIBLE_DEVICES= \
        "$image" \
        bash -c "$inner" bash "${junit[@]}" "$@" \
  || status=$?

if [ -n "$out" ]; then
    mkdir -p "$out"
    docker cp "$name:/home/tester/out/." "$out" || true
fi
exit "$status"
