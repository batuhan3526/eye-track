#!/usr/bin/env bash
# packaging/kaynak.sh - the source package: dist/surum-<v>/GozFaresi-<v>-kaynak.zip
#
#   bash packaging/kaynak.sh [tree-ish]      (default HEAD)
#
# `git archive` of a COMMITTED tree, so only what git tracks goes in: this
# machine's calibration, calibration samples (.npz), gaze.log, cache/,
# makine.json and the downloaded/built models are all git-ignored and can
# never reach the zip. The listing is checked for them anyway, and the zip is
# refused if one appears (a future .gitignore slip must not publish a
# user's calibration). Uncommitted changes are NOT in the zip: said loudly.
# Afterwards SHA256SUMS of the folder is rewritten.
set -euo pipefail
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/.." && pwd)
rev=${1:-HEAD}
cd "$repo"

say() { printf '[kaynak] %s\n' "$*"; }
die() { printf '[kaynak] HATA: %s\n' "$*" >&2; exit 1; }

ver=$(git show "$rev:gaze/eye_gaze/__init__.py" | tr -d '\r' \
      | sed -n 's/^__version__ = "\([0-9.]*\)".*/\1/p')
[ -n "$ver" ] || die "$rev icinde gaze/eye_gaze/__init__.py __version__ yok"
dist="$repo/dist/surum-$ver"
zip="$dist/GozFaresi-$ver-kaynak.zip"
mkdir -p "$dist"

if [ "$rev" = HEAD ] && [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    say "UYARI: commit edilmemis degisiklikler var; zip yalniz $rev'i icerir"
fi

# Relative output path (cwd is the repo): Git for Windows' git.exe cannot
# open Git Bash's /c/Users/... form of a non-ASCII profile path.
git archive --format=zip --prefix="GozFaresi-$ver-kaynak/" \
    -o "dist/surum-$ver/GozFaresi-$ver-kaynak.zip.part" "$rev"

# Nothing of a user's machine: calibration (json, samples, implicit,
# backups), .npz, logs, caches, the hardware profile, models, and this
# machine's gaze/config.yaml (export-ignore in .gitattributes, R29) - the
# template config.default.yaml must be there instead.
listing=$(unzip -Z1 "$zip.part")
bad=$(printf '%s\n' "$listing" | grep -E -i \
    '(^|/)calibration[^/]*$|\.npz$|(^|/)gaze\.log$|(^|/)cache/|(^|/)models/|(^|/)makine\.json|\.onnx$|\.task$|\.safetensors$|(^|/)config\.son-iyi|(^|/)gaze/config\.yaml$' \
    || true)
if ! printf '%s\n' "$listing" | grep -q '/gaze/config\.default\.yaml$'; then
    bad="$bad gaze/config.default.yaml EKSIK"
fi
if [ -n "$bad" ]; then
    rm -f "$zip.part"
    die "zip'te olmamasi gereken dosyalar:"$'\n'"$bad"
fi
mv "$zip.part" "$zip"
say "$(basename "$zip"): $(printf '%s\n' "$listing" | wc -l) girdi," \
    "$(( $(stat -c %s "$zip") / 1000 )) kB; yasak dosya yok"

(cd "$dist" && find . -maxdepth 1 -type f ! -name SHA256SUMS ! -name '*.part' \
    | sed 's|^\./||' | LC_ALL=C sort | while IFS= read -r f; do
        sha256sum "$f"; done | sed 's/ \*/  /' > SHA256SUMS)
say "SHA256SUMS: $(wc -l < "$dist/SHA256SUMS") dosya"
