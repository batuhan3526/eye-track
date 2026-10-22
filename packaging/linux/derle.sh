#!/usr/bin/env bash
# packaging/linux/derle.sh - builds the Linux packages of Goz Faresi into
# dist/surum-<version>/:
#   GozFaresi-<v>-Linux-x86_64.AppImage   double-click package (appimagetool)
#   GozFaresi-<v>-Linux-x86_64.tar.gz     the same files as a plain folder
#   SHA256SUMS                            every file in the folder (rewritten)
#
#   bash packaging/linux/derle.sh
#
# Default: inside Docker, on the image packaging/linux/test.sh tests with
# (ubuntu:22.04 + Python 3.11 -> glibc 2.35, runs on Ubuntu 22.04+, Debian 12,
# Mint 21+, Fedora 36+). Works from Git Bash on Windows and from a Linux
# shell. The source is STREAMED into the container (tar over stdin) and the
# results are copied out with `docker cp`: Docker Desktop's translation of
# a non-ASCII profile path is not trusted (same as test.sh).
#
# Environment:
#   GOZFARESI_DOCKERSIZ=1   build on this machine instead (the CI job: an
#                           ubuntu-22.04 runner with the image's packages and
#                           requirements-linux.txt already installed, python
#                           = Python 3.11 on PATH)
#   GOZFARESI_LINUX_IMAGE   image tag (default gozfaresi-linux:22.04)
#   GOZFARESI_SKIP_BUILD=1  use the existing image, do not (re)build it
#
# Internal: `derle.sh --ic OUTDIR` runs the build steps themselves (in the
# container, or on the CI runner) and writes the two packages into OUTDIR.
#
# appimagetool and the AppImage runtime are pinned to fixed releases and
# checked by SHA-256 (values below, from GitHub's release digests); they are
# cached in packaging/out/indirme with the other build downloads. Nothing
# here touches a GPU (no --gpus, CUDA_VISIBLE_DEVICES empty).
set -euo pipefail

# Git Bash would rewrite /build/... arguments into C:/Program Files/Git/...
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'
export CUDA_VISIBLE_DEVICES=

APPIMAGETOOL_URL=https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage
APPIMAGETOOL_SHA=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
RUNTIME_URL=https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64
RUNTIME_SHA=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d
MAX_APPIMAGE_MB=700

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
cache_rel=packaging/out/indirme

say() { printf '[derle] %s\n' "$*"; }
die() { printf '[derle] HATA: %s\n' "$*" >&2; exit 1; }

sha_of() { sha256sum "$1" | cut -d' ' -f1; }

version() {
    local v
    v=$(sed -n 's/^__version__ = "\([0-9.]*\)".*/\1/p' \
        "$repo/gaze/eye_gaze/__init__.py" | tr -d '\r')
    [ -n "$v" ] || die "gaze/eye_gaze/__init__.py icinde __version__ yok"
    printf '%s' "$v"
}

# fetch URL SHA NAME: a verified copy in the download cache.
fetch() {
    local url=$1 sha=$2 dest="$repo/$cache_rel/$3"
    mkdir -p "$repo/$cache_rel"
    if [ -f "$dest" ] && [ "$(sha_of "$dest")" = "$sha" ]; then return 0; fi
    say "indiriliyor: $url"
    # Relative output name: Git Bash's curl cannot open a
    # non-ASCII absolute path.
    (cd "$repo/$cache_rel" && curl -fL --retry 3 -o "$3.part" "$url")
    [ "$(sha_of "$dest.part")" = "$sha" ] || {
        rm -f "$dest.part"; die "$3 SHA-256 uyusmuyor"; }
    mv "$dest.part" "$dest"
}

write_sums() {
    # GNU sha256sum on Git Bash marks binary mode with '*': normalise to the
    # two-space form derle.ps1 writes, which `sha256sum -c` reads everywhere.
    (cd "$1" && find . -maxdepth 1 -type f ! -name SHA256SUMS ! -name '*.part' \
        | sed 's|^\./||' | LC_ALL=C sort | while IFS= read -r f; do
            sha256sum "$f"; done | sed 's/ \*/  /' > SHA256SUMS)
    say "SHA256SUMS: $(wc -l < "$1/SHA256SUMS") dosya"
}

# --- the build itself (container or CI runner), cwd = source root ----------
inner() {
    local out=$1 ver work onedir name
    ver=$(version)
    work=${GOZFARESI_WORK:-$repo/packaging/out/linux-work}
    rm -rf "$work"
    mkdir -p "$work" "$out"
    cd "$repo"
    python -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version'

    # Deep gaze model: made with CPU torch only when missing or damaged.
    set +e; python packaging/hazirla.py onnx-denetle; rc=$?; set -e
    if [ "$rc" = 3 ]; then
        say "derin model yok ya da bozuk: CPU torch ile uretiliyor"
        python -m venv "$work/venv-onnx"
        "$work/venv-onnx/bin/pip" install torch==2.5.1 \
            --index-url https://download.pytorch.org/whl/cpu
        "$work/venv-onnx/bin/pip" install -r packaging/requirements-onnx.txt
        "$work/venv-onnx/bin/python" gaze/tools/derin_onnx_cevir.py
        python packaging/hazirla.py onnx-denetle
    elif [ "$rc" != 0 ]; then
        die "hazirla.py onnx-denetle ($rc)"
    fi

    python packaging/hazirla.py sahne --cikti "$work/stage" \
        --onbellek "$repo/$cache_rel"
    GOZFARESI_STAGE="$work/stage" python -m PyInstaller --noconfirm --clean \
        --log-level WARN --distpath "$work/dist" --workpath "$work/pyi" \
        packaging/gozfaresi.spec
    onedir="$work/dist/gozfaresi"
    [ -x "$onedir/gozfaresi" ] || die "PyInstaller cikti vermedi: $onedir"

    cp packaging/BENI-OKU-KURULUM.txt packaging/UCUNCU-TARAF-LISANSLAR.txt \
        "$onedir/"
    cp gaze/LICENSE "$onedir/LICENSE.txt"
    # pkexec runs it as root: LF endings (.gitattributes) and executable
    # (the guard below checks both).
    chmod 755 "$onedir/_internal/linux/izin-kur.sh"
    # Full licence texts of everything bundled (+ the Debian copyright files
    # of the system libraries PyInstaller copied).
    python packaging/hazirla.py lisanslar "$onedir"
    # The bundle guard: nothing of the build machine (calibration, logs,
    # caches, hardware profile, a config.yaml), everything a package needs,
    # lisanslar/ included.
    python packaging/hazirla.py denetle "$onedir"
    # Corresponding Source of the GPL-3.0 parts, next to the packages.
    python packaging/hazirla.py gpl-kaynak "$out" --onbellek "$repo/$cache_rel"

    name="GozFaresi-$ver-Linux-x86_64"
    tar -C "$work/dist" --owner=0 --group=0 --numeric-owner \
        -czf "$out/$name.tar.gz.part" gozfaresi
    mv "$out/$name.tar.gz.part" "$out/$name.tar.gz"
    say "tar.gz: $(du -m "$out/$name.tar.gz" | cut -f1) MB"

    local appdir="$work/AppDir"
    mkdir -p "$appdir/usr/lib" "$appdir/usr/share/applications" \
        "$appdir/usr/share/icons/hicolor/256x256/apps"
    cp -a "$onedir" "$appdir/usr/lib/gozfaresi"
    install -m 755 packaging/linux/AppRun "$appdir/AppRun"
    install -m 644 packaging/linux/gozfaresi.desktop "$appdir/gozfaresi.desktop"
    install -m 644 packaging/linux/gozfaresi.desktop \
        "$appdir/usr/share/applications/gozfaresi.desktop"
    install -m 644 gaze/resources/icon.png "$appdir/gozfaresi.png"
    install -m 644 gaze/resources/icon.png \
        "$appdir/usr/share/icons/hicolor/256x256/apps/gozfaresi.png"
    ln -s gozfaresi.png "$appdir/.DirIcon"

    local tool="$repo/$cache_rel/appimagetool-x86_64.AppImage"
    local runtime="$repo/$cache_rel/runtime-x86_64"
    [ "$(sha_of "$tool")" = "$APPIMAGETOOL_SHA" ] || die "appimagetool SHA"
    [ "$(sha_of "$runtime")" = "$RUNTIME_SHA" ] || die "AppImage runtime SHA"
    chmod +x "$tool"
    # No FUSE in a container: the tool unpacks itself and runs.
    ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$tool" --no-appstream \
        --runtime-file "$runtime" "$appdir" "$out/$name.AppImage.part"
    mv "$out/$name.AppImage.part" "$out/$name.AppImage"
    chmod 755 "$out/$name.AppImage"
    local mb
    mb=$(( $(stat -c %s "$out/$name.AppImage") / 1000000 ))
    say "AppImage: $mb MB"
    [ "$mb" -lt "$MAX_APPIMAGE_MB" ] || die "AppImage $MAX_APPIMAGE_MB MB sinirini asiyor"
}

if [ "${1:-}" = "--ic" ]; then
    inner "$2"
    exit 0
fi

ver=$(version)
dist="$repo/dist/surum-$ver"
mkdir -p "$dist"
say "surum $ver -> $dist"
fetch "$APPIMAGETOOL_URL" "$APPIMAGETOOL_SHA" appimagetool-x86_64.AppImage
fetch "$RUNTIME_URL" "$RUNTIME_SHA" runtime-x86_64

if [ "${GOZFARESI_DOCKERSIZ:-}" = "1" ]; then
    bash "$here/derle.sh" --ic "$dist"
    write_sums "$dist"
    exit 0
fi

image=${GOZFARESI_LINUX_IMAGE:-gozfaresi-linux:22.04}
if [ "${GOZFARESI_SKIP_BUILD:-}" != "1" ]; then
    tar -C "$here" -cf - Dockerfile requirements-linux.txt \
        | docker build -t "$image" -
fi

name="gozfaresi-derle-$$-$RANDOM"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# What goes in: the working tree as git sees it (tracked + new, not ignored)
# for gaze/ and packaging/, plus the built or downloaded files the build
# would otherwise make again: the ONNX model and its .sha256, MediaPipe's
# model, and the download cache (pinned tools, piper voice).
inner_cmd='
set -e
mkdir -p /build/src
tar -xf - -C /build/src
cd /build/src
GOZFARESI_WORK=/build/work bash packaging/linux/derle.sh --ic /build/out
'
(cd "$repo" \
    && { git ls-files -z --cached --others --exclude-standard -- gaze packaging
         for f in gaze/resources/models/eth-xgaze-resnet18.onnx \
                  gaze/resources/models/eth-xgaze-resnet18.onnx.sha256 \
                  gaze/models/face_landmarker.task; do
             if [ -f "$f" ]; then printf '%s\0' "$f"; fi
         done
         if [ -d "$cache_rel" ]; then
             find "$cache_rel" -maxdepth 1 -type f ! -name '*.part' -print0
         fi; } \
    | tar --null --ignore-failed-read -T - -cf -) \
  | docker run -i --init --name "$name" -e CUDA_VISIBLE_DEVICES= \
        "$image" bash -c "$inner_cmd"

# Out as a tar stream as well: docker.exe would read /c/Users/... (Git Bash,
# no path conversion) as C:\c\Users\....
docker cp "$name:/build/out/." - | tar -xf - -C "$dist"
write_sums "$dist"
say "bitti:"
ls -l "$dist"
