# packaging/hazirla.py
"""Stage the files a package ships that are not in git, for gozfaresi.spec.

    python packaging/hazirla.py sahne [--cikti DIR] [--onbellek DIR]
    python packaging/hazirla.py onnx-denetle
    python packaging/hazirla.py tasinabilir-zip ONEDIR ZIP
    python packaging/hazirla.py sha256sums DIR
    python packaging/hazirla.py lisanslar ONEDIR
    python packaging/hazirla.py denetle ONEDIR
    python packaging/hazirla.py gpl-kaynak DIR [--onbellek DIR]

Run by derle.ps1 / derle.sh / the CI workflow with the BUILD interpreter
(the venv that also runs PyInstaller): espeak-ng-data is taken from that
interpreter's piper package, so the data always matches the espeak code that
gets frozen next to it.

sahne (stage) writes, after emptying DIR (default packaging/out/stage):
  models/face_landmarker.task   MediaPipe's model, pinned by SHA-256. The
                                checkout's gaze/models copy is used when it
                                matches, otherwise it is downloaded.
  piper/tr_TR-dfki-medium.onnx  the Turkish piper voice and its .json, from
  piper/...onnx.json            eye_gaze/models_manifest.py (Hugging Face,
                                fixed revision, SHA-256 checked) - the same
                                entry the program itself downloads from.
  piper/espeak-ng-data/         the build interpreter's piper wheel data.
Downloads are cached in --onbellek (default packaging/out/indirme) and every
file, cached or not, is checked against its SHA-256 before it is staged.

onnx-denetle (check the deep model) exits 0 when
gaze/resources/models/eth-xgaze-resnet18.onnx and its .sha256 sidecar exist
and agree AND the model is the one ONNX_SHA pins, 3 otherwise: the build
scripts then run tools/derin_onnx_cevir.py in a CPU torch venv and check
again, so a conversion that comes out different stops the build. The model is never staged: it already sits in
gaze/resources/models, which the spec collects whole.

tasinabilir-zip (portable zip) packs the PyInstaller onedir folder as
GozFaresi/ in ZIP and adds the "tasinabilir" marker next to the exe: with
it the program keeps its data beside itself (eye_gaze.paths, portable
mode). The installer gets the same folder WITHOUT the marker.

sha256sums writes DIR/SHA256SUMS for every other file in DIR, in
`sha256sum -c` format (LF, "<hash>  <name>"), so each build script can
rewrite it after adding its own files.

lisanslar (licences) fills ONEDIR/lisanslar/ - next to the exe, in the
installer, the zip, the tar.gz and the AppImage alike - with the full licence
texts of what the bundle holds:
  <dist>-<version>/  every licence/notice file the BUILD interpreter's
                     installed distributions carry (dist-info licences,
                     cv2/LICENSE-3RD-PARTY.txt with FFmpeg and Qt 5, ...).
                     A distribution that ships none gets the vendored copy
                     from packaging/lisans-metinleri (FALLBACK); one with
                     neither stops the build - a new dependency must never
                     ship without its licence.
  metinler/          the vendored full texts (GPL-3.0, LGPL-2.1/3.0,
                     Apache-2.0, CC BY-NC-SA 4.0, ...) the models, espeak-ng
                     and the bundled native libraries refer to.
  python/            the interpreter's own LICENSE (Windows: it also covers
                     Tcl/Tk, OpenSSL, SQLite, ... that ship with it).
  sistem/            Linux: the Debian copyright file of every system
                     library PyInstaller copied into _internal (dpkg).
Build-only tools (pip, setuptools, pytest ...) are left out.

denetle (check) is the bundle guard both build scripts run before packing:
no calibration*, *.npz, gaze.log, makine.json, config.yaml, config.son-iyi*,
cache/ or "tasinabilir" anywhere; every file a package needs present,
lisanslar/ (and its KAYNAKLAR.txt) included; on Linux izin-kur.sh
executable and LF-only; the copyleft native parts exactly the listed ones
(source_problems below); the voice worker present, piper only in its PYZ
and none of the program or the GPL-incompatible libraries there
(isolation_problems, Task 18b).

gpl-kaynak (GPL source) puts the exact source of every GPL / LGPL / MPL part
of THIS OS's package next to the packages (DIR = dist/surum-<v>) and lists
them in DIR/KAYNAKLAR-<OS>.txt (component, file, URL, SHA-256, how the
version was established): piper1-gpl and espeak-ng, PyInstaller, certifi,
tqdm and, on Linux, python-xlib, OpenCV's FFmpeg and Qt 5, libquadmath and
the LGPL Ubuntu libraries (tables below). All pinned by SHA-256 and cached
in --onbellek. `denetle` refuses a bundle whose (L)GPL native parts are not
the ones listed (and any DirectML, PyAV, ASIO or OpenCV FFmpeg plugin file).

Output is ASCII (the Windows console is cp1254). Nothing outside the
--cikti/--onbellek folders is written.
"""
import argparse
import hashlib
import importlib.util
import os
import shutil
import ssl
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GAZE = os.path.join(REPO, "gaze")
sys.path.insert(0, GAZE)

from eye_gaze import models_manifest  # noqa: E402  (stdlib-only module)

FACE_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task")
FACE_SHA = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
ONNX = os.path.join(GAZE, "resources", "models", "eth-xgaze-resnet18.onnx")
# The deep model every package has shipped so far (Task 9, verified against
# the torch model: <= 3e-7 rad, 0.001 px on the development machine's calibration).
# Converted by gaze/tools/derin_onnx_cevir.py with torch 2.5.1 (CPU), onnx
# 1.19.1, opset 17 - deterministic, the same bytes on three exports - from
# Hugging Face hysts/ptgaze-eth-xgaze-resnet18 at revision
# 5b6aebd02c7a612b50afb2e48c453f23533b8de3, model.safetensors sha256
# d1c91b2aa6a0c73856c16890d337afdecdb05563ed52182dfdb77742f1c856bc (the
# only snapshot in the development machine's HF cache; the same file as
# C:/Users/Public/ptgaze-models). Its own .sha256 sidecar is written by the
# converter, so it cannot tell a model made from other weights (the repo's
# main branch moving on, a CI machine): this pin does. Deep calibrations
# are fitted on this model's output - a different one must never ship
# silently (final review M-2).
ONNX_SHA = "add4976870f63efbf643af73c9884c9258f27e3a7812e4b8aa3ec390e63a7886"


def say(msg):
    print("[hazirla] " + msg.encode("ascii", "replace").decode("ascii"),
          flush=True)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url, sha, cache_dir, name):
    """A verified local copy of url (from the cache when it is intact)."""
    os.makedirs(cache_dir, exist_ok=True)
    dest = os.path.join(cache_dir, name)
    if os.path.isfile(dest) and sha256(dest) == sha:
        return dest
    say("indiriliyor: " + url)
    part = dest + ".part"
    with urllib.request.urlopen(url, context=_context(), timeout=60) as r, \
            open(part, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)
    got = sha256(part)
    if got != sha:
        os.remove(part)
        raise SystemExit("HATA: %s SHA-256 uyusmuyor (%s, beklenen %s)"
                         % (name, got, sha))
    os.replace(part, dest)
    return dest


def piper_espeak_dir():
    spec = importlib.util.find_spec("piper")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("HATA: bu yorumlayicida piper-tts kurulu degil")
    d = os.path.join(list(spec.submodule_search_locations)[0],
                     "espeak-ng-data")
    if not os.path.isfile(os.path.join(d, "phontab")):
        raise SystemExit("HATA: piper paketinde espeak-ng-data yok: " + d)
    return d


def stage(out, cache):
    if os.path.isdir(out):
        shutil.rmtree(out)
    models = os.path.join(out, "models")
    piper = os.path.join(out, "piper")
    os.makedirs(models)
    os.makedirs(piper)

    local = os.path.join(GAZE, "models", "face_landmarker.task")
    if os.path.isfile(local) and sha256(local) == FACE_SHA:
        src = local
    else:
        src = fetch(FACE_URL, FACE_SHA, cache, "face_landmarker.task")
    shutil.copyfile(src, os.path.join(models, "face_landmarker.task"))
    say("face_landmarker.task tamam")

    for item in models_manifest.MANIFEST["piper-tr-dfki-medium"]:
        name = os.path.basename(item.dest_rel)
        src = fetch(item.url, item.sha256, cache, name)
        shutil.copyfile(src, os.path.join(piper, name))
        say("piper: %s tamam" % name)

    shutil.copytree(piper_espeak_dir(), os.path.join(piper, "espeak-ng-data"))
    say("piper: espeak-ng-data tamam")


def onnx_ok():
    side = ONNX + ".sha256"
    if not (os.path.isfile(ONNX) and os.path.isfile(side)):
        say("derin model yok: " + os.path.relpath(ONNX, REPO))
        return False
    with open(side, encoding="ascii") as f:
        want = f.read().split()[0].lower()
    got = sha256(ONNX)
    if got != want:
        say("derin model .sha256 ile uyusmuyor")
        return False
    if got != ONNX_SHA:
        say("derin model sabitlenen model degil (sha256 %s, beklenen %s):"
            " baska agirliklardan ya da baska bir torch ile uretilmis;"
            " derin kalibrasyonlar bu modele gore - paketlenmez"
            % (got[:12], ONNX_SHA[:12]))
        return False
    say("derin model tamam (%s)" % want[:12])
    return True


PORTABLE_NOTE = (
    "Bu dosya burada durdukça Göz Faresi ayarlarını, kalibrasyonu ve "
    "indirdiği modelleri bu klasörde tutar (taşınabilir kullanım).\n"
    "Silerseniz program verilerini kullanıcı klasöründe arar ve bu "
    "klasördeki kalibrasyonu görmez.\n")


def portable_zip(onedir, dst):
    import zipfile
    exe = [n for n in os.listdir(onedir) if n.lower() in ("gozfaresi.exe",
                                                          "gozfaresi")]
    if not exe:
        raise SystemExit("HATA: %s icinde GozFaresi yok" % onedir)
    part = dst + ".part"
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=6) as z:
        for root, dirs, files in os.walk(onedir):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(root, name)
                rel = os.path.relpath(path, onedir).replace(os.sep, "/")
                z.write(path, "GozFaresi/" + rel)
        z.writestr("GozFaresi/tasinabilir", PORTABLE_NOTE.encode("utf-8"))
    os.replace(part, dst)
    say("zip: %s (%.1f MB)" % (os.path.basename(dst),
                               os.path.getsize(dst) / 1e6))


def write_sums(folder):
    names = sorted(n for n in os.listdir(folder)
                   if n != "SHA256SUMS" and not n.endswith(".part")
                   and os.path.isfile(os.path.join(folder, n)))
    lines = ["%s  %s\n" % (sha256(os.path.join(folder, n)), n)
             for n in names]
    with open(os.path.join(folder, "SHA256SUMS"), "w", encoding="ascii",
              newline="\n") as f:
        f.writelines(lines)
    say("SHA256SUMS: %d dosya" % len(names))


LICENCE_TEXTS = os.path.join(HERE, "lisans-metinleri")
# Distributions that carry no licence file in their wheel: the vendored copy.
FALLBACK = {
    "ctranslate2": "ctranslate2-4.8.3-LICENSE.txt",     # MIT (upstream LICENSE)
    "flatbuffers": "Apache-2.0.txt",
    "tokenizers": "Apache-2.0.txt",
}
# In the build venv only; never frozen into the bundle. "av" (PyAV) is a
# faster-whisper dependency the spec leaves out (Task 18, R41).
BUILD_ONLY = {"pip", "setuptools", "wheel", "pytest", "pluggy", "iniconfig",
              "pyinstaller-hooks-contrib", "altgraph", "pefile",
              "pywin32-ctypes", "macholib", "av"}
# Always present afterwards (the build scripts check the same names).
REQUIRED_TEXTS = ("GPL-3.0.txt", "GPL-2.0.txt", "LGPL-2.1.txt",
                  "LGPL-3.0.txt", "Apache-2.0.txt", "CC-BY-NC-SA-4.0.txt")
_LICENCE_NAME = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS",
                 "COPYRIGHT", "THIRDPARTYNOTICES", "LICENSE-3RD-PARTY")


def _is_licence_file(rel):
    rel = rel.replace("\\", "/")
    name = rel.rsplit("/", 1)[-1]
    if name.endswith((".py", ".pyc", ".pyi", ".xml", ".so", ".dll", ".pyd")):
        return False
    upper = name.upper().split(".")[0]
    if any(upper.startswith(n) for n in _LICENCE_NAME):
        return True
    return ".dist-info/licenses/" in rel


def _norm(name):
    return name.lower().replace("_", "-")


def _python_licence():
    """The interpreter's licence file(s): Windows LICENSE.txt, Debian's
    copyright file for deadsnakes' python3.11 on Linux."""
    cands = [os.path.join(sys.base_prefix, "LICENSE.txt"),
             os.path.join(sys.base_prefix, "LICENSE")]
    ver = "python%d.%d" % sys.version_info[:2]
    for pkg in (ver, ver + "-minimal", "lib" + ver + "-stdlib",
                "lib" + ver + "-minimal"):
        cands.append(os.path.join("/usr/share/doc", pkg, "copyright"))
    return [c for c in cands if os.path.isfile(c)]


def _system_copyrights(onedir, out):
    """Linux: /usr/share/doc/<pkg>/copyright of every system .so in
    _internal (the ones PyInstaller copied from the build image)."""
    import subprocess
    internal = os.path.join(onedir, "_internal")
    if not shutil.which("dpkg-query") or not os.path.isdir(internal):
        return 0
    pkgs = set()
    for name in sorted(os.listdir(internal)):
        p = os.path.join(internal, name)
        if ".so" not in name or os.path.islink(p) or not os.path.isfile(p):
            continue
        r = subprocess.run(["dpkg-query", "-S", "*/" + name],
                           capture_output=True, text=True)
        # "libx11-6:amd64, other:amd64: /usr/lib/.../libX11.so.6"
        for line in r.stdout.splitlines():
            owners = line.rsplit(": ", 1)[0]
            pkgs.update(x.strip().split(":")[0] for x in owners.split(","))
    n = 0
    for pkg in sorted(pkgs):
        src = os.path.join("/usr/share/doc", pkg, "copyright")
        if os.path.isfile(src):
            os.makedirs(os.path.join(out, "sistem"), exist_ok=True)
            shutil.copyfile(src, os.path.join(out, "sistem", pkg + ".copyright"))
            n += 1
    return n


def collect_licences(onedir):
    import importlib.metadata as md
    if not os.path.isdir(onedir):
        raise SystemExit("HATA: klasor yok: %s" % onedir)
    out = os.path.join(onedir, "lisanslar")
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    seen, missing, count = set(), [], 0
    for dist in sorted(md.distributions(),
                       key=lambda d: _norm(d.metadata["Name"] or "")):
        name = dist.metadata["Name"] or ""
        if not name or _norm(name) in BUILD_ONLY or _norm(name) in seen:
            continue
        seen.add(_norm(name))
        target = os.path.join(out, "%s-%s" % (_norm(name), dist.version))
        files = [f for f in (dist.files or []) if _is_licence_file(str(f))]
        copied = 0
        for f in files:
            src = str(dist.locate_file(f))
            if not os.path.isfile(src):
                continue
            rel = str(f).replace("\\", "/")
            flat = rel.split(".dist-info/", 1)[-1].replace("/", "_")
            os.makedirs(target, exist_ok=True)
            shutil.copyfile(src, os.path.join(target, flat))
            copied += 1
        if not copied and _norm(name) in FALLBACK:
            os.makedirs(target, exist_ok=True)
            shutil.copyfile(os.path.join(LICENCE_TEXTS, FALLBACK[_norm(name)]),
                            os.path.join(target, FALLBACK[_norm(name)]))
            copied = 1
        if not copied:
            missing.append("%s %s" % (name, dist.version))
        count += copied
    if missing:
        raise SystemExit("HATA: lisans metni olmayan paketler (packaging/"
                         "lisans-metinleri + hazirla.FALLBACK'e ekleyin): "
                         + ", ".join(missing))
    shutil.copytree(LICENCE_TEXTS, os.path.join(out, "metinler"))
    py = _python_licence()
    if not py:
        raise SystemExit("HATA: Python lisans dosyasi bulunamadi")
    os.makedirs(os.path.join(out, "python"))
    for i, src in enumerate(py):
        shutil.copyfile(src, os.path.join(
            out, "python", "%d-%s" % (i, os.path.basename(src))))
    nsys = _system_copyrights(onedir, out)
    write_sources_list(os.path.join(out, "KAYNAKLAR.txt"))
    for t in REQUIRED_TEXTS:
        if not os.path.isfile(os.path.join(out, "metinler", t)):
            raise SystemExit("HATA: lisans metni eksik: " + t)
    with open(os.path.join(out, "BENIOKU.txt"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write("Bu klasör, Göz Faresi paketindeki üçüncü taraf bileşenlerin "
                "TAM lisans metinlerini içerir.\nÖzet ve açıklama: bir üst "
                "klasördeki UCUNCU-TARAF-LISANSLAR.txt\n\n"
                "  <paket>-<sürüm>/  Python paketlerinin kendi lisans "
                "dosyaları\n  metinler/          tam lisans metinleri (GPL-3.0, "
                "LGPL-2.1, LGPL-3.0, Apache-2.0, CC BY-NC-SA 4.0, ...)\n"
                "  python/            Python ve onunla gelen kitaplıklar\n"
                "  sistem/            (Linux) pakete kopyalanan sistem "
                "kitaplıklarının telif dosyaları\n"
                "  KAYNAKLAR.txt      GPL / LGPL / MPL bileşenlerin paketlerle "
                "birlikte yayınlanan kaynak dosyaları\n")
    say("lisanslar: %d paket, %d dosya, %d sistem kitapligi"
        % (len(seen), count, nsys))


_FORBIDDEN_FILES = ("gaze.log", "makine.json", "config.yaml", "tasinabilir")


def check_bundle(onedir):
    """The bundle guard (both build scripts): nothing of the build machine
    in it, everything a package needs - licences included - present.
    Exits non-zero with every problem listed."""
    linux = os.path.isfile(os.path.join(onedir, "gozfaresi"))
    windows = os.path.isfile(os.path.join(onedir, "GozFaresi.exe"))
    if linux == windows:
        raise SystemExit("HATA: %s bir GozFaresi paketi degil" % onedir)
    bad = []
    for root, dirs, files in os.walk(onedir):
        for d in dirs:
            if d == "cache":
                bad.append(os.path.join(root, d))
        for f in files:
            if (f in _FORBIDDEN_FILES or f.startswith("calibration")
                    or f.endswith(".npz") or f.startswith("config.son-iyi")):
                bad.append(os.path.join(root, f))
    need = ["BENI-OKU-KURULUM.txt", "UCUNCU-TARAF-LISANSLAR.txt",
            "LICENSE.txt", "lisanslar/BENIOKU.txt", "lisanslar/KAYNAKLAR.txt",
            "_internal/config.default.yaml",
            "_internal/resources/models/eth-xgaze-resnet18.onnx",
            "_internal/resources/models/eth-xgaze-resnet18.onnx.sha256",
            "_internal/resources/models/face_landmarker.task",
            "_internal/resources/piper/tr_TR-dfki-medium.onnx",
            "_internal/resources/piper/tr_TR-dfki-medium.onnx.json",
            "_internal/resources/piper/espeak-ng-data/phontab",
            "_internal/resources/aac/turkce-aac-paketi.xml",
            "_internal/resources/aac/english-aac-pack.xml",
            "_internal/resources/lang/tr.yaml",
            "_internal/resources/lang/en.yaml",
            "_internal/resources/faces/benchmark.jpg",
            "_internal/resources/fonts/DejaVuSans.ttf",
            "_internal/linux/izin-kur.sh", "_internal/linux/99-gozfaresi.rules",
            "gozfaresi-ses" if linux else "GozFaresi-Ses.exe",
            "_internal/certifi/cacert.pem",
            "_internal/mediapipe/tasks/c/"
            + ("libmediapipe.so" if linux else "libmediapipe.dll")]
    need += ["lisanslar/metinler/" + t for t in REQUIRED_TEXTS]
    missing = [n for n in need
               if not os.path.isfile(os.path.join(onedir, *n.split("/")))]
    lic = os.path.join(onedir, "lisanslar")
    for prefix in ("piper-tts-", "python", "opencv-"):
        if not (os.path.isdir(lic) and any(
                n.startswith(prefix) for n in os.listdir(lic))):
            missing.append("lisanslar/%s*" % prefix)
    if linux:
        sh = os.path.join(onedir, "_internal", "linux", "izin-kur.sh")
        if os.path.isfile(sh):
            if not os.access(sh, os.X_OK):
                bad.append(sh + " (calistirilabilir degil)")
            with open(sh, "rb") as f:
                if b"\r" in f.read():
                    bad.append(sh + " (CRLF)")
    # (L)GPL native code only with its source listed; no DirectML, PyAV,
    # ASIO or OpenCV FFmpeg plugin at all (Task 18, R39/R41).
    bad += source_problems(onedir, linux)
    # piper/espeak-ng only in the voice worker, the incompatible libraries
    # never there (Task 18b, R44).
    bad += isolation_problems(onedir, linux)
    if bad or missing:
        raise SystemExit("HATA: paket denetimi basarisiz\n  olmamasi gereken: "
                         "%s\n  eksik: %s" % (bad or "-", missing or "-"))
    say("paket denetimi tamam (%s)" % ("Linux" if linux else "Windows"))


# --- Corresponding Source (Task 18, R41) -------------------------------------
# Every (L)GPL - and MPL-2.0 - part a package carries ships with its exact
# source NEXT TO the packages (dist/surum-<v>/, in SHA256SUMS), not only a
# link. `gpl-kaynak` downloads them (pinned by SHA-256, cached in
# --onbellek) and writes KAYNAKLAR-<OS>.txt there; `lisanslar` writes the
# same list into the bundle (lisanslar/KAYNAKLAR.txt); `denetle` refuses a
# bundle whose copyleft parts are not exactly the ones listed here.
#
# Left out of the bundle instead of sourced (gozfaresi.spec): PyAV (its
# FFmpeg with x264, x265, GnuTLS, GMP ...), OpenCV's Windows FFmpeg plugin,
# GNU readline, Berkeley DB (_dbm), glibc's libmvec, PortAudio's ASIO
# build, DirectML. Not sourced, by their own terms: the GCC runtime
# libraries (libgcc_s, libstdc++, libgfortran, libgomp: GPL-3.0 WITH the
# GCC Runtime Library Exception, which lets their object code ship under
# any terms).

class Src:
    """One source file: what it is the source of, its licence, the file
    name in dist/, where it comes from, its SHA-256 and how the exact
    version was established (`how`, written into KAYNAKLAR)."""

    def __init__(self, component, licence, name, url, sha, how):
        self.component, self.licence = component, licence
        self.name, self.url, self.sha, self.how = name, url, sha, how


_PYPI = "https://files.pythonhosted.org/packages/"
_LP = "https://launchpad.net/ubuntu/+archive/primary/+sourcefiles/"


def _pypi_sdist(dist, version, path, sha, licence):
    return Src("%s %s" % (dist, version), licence, path.rsplit("/", 1)[1],
               _PYPI + path, sha,
               "PyPI'deki %s %s kaynak dağıtımı (sdist); sürüm, derleme"
               " yorumlayıcısında kurulu olandır" % (dist, version))


# Python distributions under a copyleft licence, as installed in the BUILD
# interpreter -> their sources. A version not listed here stops the build:
# read the new version's sdist from PyPI (URL + sha256), never guess.
COPYLEFT_DISTS = ("piper-tts", "pyinstaller", "certifi", "tqdm",
                  "python-xlib")
PY_SOURCES = {
    # piper-tts 1.6.0 wheel: built by its CMakeLists.txt from espeak-ng at
    # commit 724808c (Task 14).
    ("piper-tts", "1.6.0"): (
        Src("piper-tts 1.6.0", "GPL-3.0-or-later", "piper1-gpl-v1.6.0.tar.gz",
            "https://github.com/OHF-voice/piper1-gpl/archive/refs/tags/"
            "v1.6.0.tar.gz",
            "171e27d9f5dc38048552155909f5760f781c08958e6208ea4b5d97525e1ad82b",
            "piper-tts 1.6.0 tekerleğinin üretildiği v1.6.0 etiketi"),
        Src("espeak-ng (piper-tts 1.6.0 içinde; resources/piper/"
            "espeak-ng-data)", "GPL-3.0-or-later",
            "espeak-ng-724808c5a83f9ef95fdd0db886ba7ba537ff224a.tar.gz",
            "https://github.com/espeak-ng/espeak-ng/archive/"
            "724808c5a83f9ef95fdd0db886ba7ba537ff224a.tar.gz",
            "7d8ebd78201923a443245362a928999977d1721c3eeba596eafc6ea8db7e243c",
            "piper1-gpl v1.6.0 CMakeLists.txt dosyasının sabitlediği commit"),
    ),
    ("pyinstaller", "6.22.3"): (_pypi_sdist(
        "pyinstaller", "6.22.3",
        "63/41/f90302845945abd4ed647933ff5ee7c6ac93983187be67f897b6cb613331/"
        "pyinstaller-6.22.3.tar.gz",
        "05eb2f5615503e72939a7224d68b4aff572c6b0438ee4a17d0a4b481f399362d",
        "GPL-2.0-or-later, önyükleyici istisnasıyla (GozFaresi.exe /"
        " gozfaresi önyükleyicisi)"),),
    ("certifi", "2026.6.17"): (_pypi_sdist(
        "certifi", "2026.6.17",
        "c9/c7/424b75da314c1045981bd9777432fad05a9e0c69daa4ed7e308bbaffe405/"
        "certifi-2026.6.17.tar.gz",
        "024c88eeec92ca068db80f02b8b07c9cef7b9fe261d1d535abfd5abd6f6af432",
        "MPL-2.0"),),
    ("certifi", "2026.10.19"): (_pypi_sdist(
        "certifi", "2026.10.19",
        "a6/bd/1436c50cc46699de0230fbaacc1d1de7bfec413176099949e9d5112e88f2/"
        "certifi-2026.10.19.tar.gz",
        "035c1e85572b141bf1c3a3eff0e33a8eb74917522f2559075ff8d2fea780a1ae",
        "MPL-2.0"),),
    ("tqdm", "4.70.1"): (_pypi_sdist(
        "tqdm", "4.70.1",
        "0d/ea/b2a5bd54b28a324dae8211928b2d730b6547500342c7e6c6dea08bd0a485/"
        "tqdm-4.70.1.tar.gz",
        "cefd0eca11b2a37a3aee776544d4f4ae913f02688135b5556b8788dfa474afc4",
        "MPL-2.0 ve MIT"),),
    ("python-xlib", "0.33"): (_pypi_sdist(
        "python-xlib", "0.33",
        "86/f5/8c0653e5bb54e0cbdfe27bf32d41f27bc4e12faa8742778c17f2a71be2c0/"
        "python-xlib-0.33.tar.gz",
        "55af7906a2c75ce6cb280a584776080602444f75815a7aff4d287bb2d7018b32",
        "LGPL-2.1-or-later"),),
}

# Linux: what the opencv-python 4.13.0.92 and numpy 2.4.6 wheels vendor.
_OPENCV_SCRIPTS = Src(
    "opencv-python 4.13.0.92 derleme betikleri (FFmpeg ve Qt'nin nasıl"
    " yapılandırılıp derlendiği: docker/manylinux_2_28/Dockerfile_x86_64)",
    "MIT", "opencv-python-92.tar.gz",
    "https://github.com/opencv/opencv-python/archive/refs/tags/92.tar.gz",
    "049293f56726a3ebb01bb7508b973e14e62752a4a0e067ac6af4e371d6aa30d3",
    "opencv-python deposunun 92 etiketi = 4.13.0.92 tekerlekleri")
_FFMPEG = Src(
    "FFmpeg 8.0.1 (OpenCV tekerleğinde: libavcodec, libavformat, libavutil,"
    " libavdevice, libavfilter, libswscale, libswresample)",
    "LGPL-2.1-or-later", "ffmpeg-8.0.1.tar.gz",
    "https://ffmpeg.org/releases/ffmpeg-8.0.1.tar.gz",
    "ed1cfade43aab7711c88937a0afc15b7b22efdc528c6ff074b4027c55a3c175c",
    "kitaplıktaki 'FFmpeg version 8.0.1' dizgisi ve yapılandırma satırı"
    " (--enable-openssl --enable-libvpx --enable-shared --enable-pic),"
    " opencv-python 92 Dockerfile'ındaki FFMPEG_VERSION=8.0.1 ve configure"
    " satırıyla aynı; Dockerfile tam bu dosyayı indirir. FFmpeg sürüm"
    " imzası (.asc) FFmpeg release signing key FCF986EA15E6E293 ile doğru")
_QT = Src(
    "Qt 5.15.18 (OpenCV tekerleğinde: QtCore, QtGui, QtWidgets, QtTest,"
    " QtXcbQpa ve cv2/qt/plugins/platforms/libqxcb.so)",
    "LGPL-3.0-only", "qt-everywhere-opensource-src-5.15.18.tar.xz",
    "https://download.qt.io/archive/qt/5.15/5.15.18/single/"
    "qt-everywhere-opensource-src-5.15.18.tar.xz",
    "cea1fbabf02455f3f0e8eaa839f5d6f45cdb56b62c8a83af5c1d00ac05f912ea",
    "libQt5Core 'Qt 5.15.18 ... by GCC 14.2.1' der; opencv-python 92"
    " Dockerfile'ı QT_VERSION=5.15.18 ile tam bu dosyayı indirir ve"
    " yapılandırma seçeneklerini verir (MD5 51d97438bd33f8f0042593cf6cc4490c,"
    " download.qt.io md5sums.txt ile aynı)")
_GCC8 = Src(
    "libquadmath (OpenCV tekerleğinde: libquadmath-2284e583.so.0.0.0)",
    "LGPL-2.1-or-later", "gcc-8.5.0-28.el8_10.alma.1.src.rpm",
    "https://vault.almalinux.org/8.10/BaseOS/Source/Packages/"
    "gcc-8.5.0-28.el8_10.alma.1.src.rpm",
    "c94dbbd2cd6d5d01a5a6822ea18f8ce34942c25a80e17b4682d547d5e652dbd0",
    "GNU build-id c1c5f8d61f17e5579c4e85718845b74e4b94116c: AlmaLinux 8"
    " libquadmath-8.5.0-28.el8_10.alma.1.x86_64.rpm ile aynı (8.5.0-20,"
    " -22, -23, -24, -26 karşılaştırıldı, uymadı); opencv-python"
    " manylinux_2_28 (AlmaLinux 8) imajında derlenir")
_GCC4 = Src(
    "libquadmath (NumPy tekerleğinde:"
    " libquadmath-96973f99-934c22de.so.0.0.0)",
    "LGPL-2.1-or-later", "gcc-4.8.5-44.el7.src.rpm",
    "https://vault.centos.org/7.9.2009/os/Source/SPackages/"
    "gcc-4.8.5-44.el7.src.rpm",
    "642c4085f6af9565e39583e54a9d7a5d9e6b93dd8c8e9474aec8a892d6c7aa36",
    "GNU build-id 549b4c82347785459571c79239872ad31509dcf4: CentOS 7"
    " libquadmath-4.8.5-44.el7.x86_64.rpm ile aynı; NumPy'nin OpenBLAS'ı"
    " (scipy-openblas64 0.3.31.188.0) manylinux2014 (CentOS 7) imajında"
    " derlenir")


def _ubuntu(src, version, files, licence, what):
    return tuple(Src("%s (Ubuntu kaynak paketi %s %s)" % (what, src, version),
                     licence, name, _LP + "%s/%s/%s" % (src, version, name),
                     sha, "derleme imajında dpkg-query: %s %s; .dsc ve"
                     " içindeki dosyalar, Launchpad arşivi" % (src, version))
                 for name, sha in files)


# Ubuntu 22.04 libraries under the LGPL that PyInstaller copies from the
# build image: source package -> (version, sources). `denetle` checks the
# image still has exactly this version.
UBUNTU_SOURCES = {
    "glib2.0": ("2.72.4-0ubuntu2.10", _ubuntu(
        "glib2.0", "2.72.4-0ubuntu2.10", (
            ("glib2.0_2.72.4-0ubuntu2.10.dsc",
             "29ad099fa2f59d77424dfc8bccb474df79e4c79f2d603228aef02a679a292ca9"),
            ("glib2.0_2.72.4.orig.tar.xz",
             "8848aba518ba2f4217d144307a1d6cb9afcc92b54e5c13ac1f8c4d4608e96f0e"),
            ("glib2.0_2.72.4-0ubuntu2.10.debian.tar.xz",
             "e81d6f72522eb2092ddec06760753d5fe7866eab553237a4c237b613e592df69"),
        ), "LGPL-2.1-or-later",
        "GLib (libglib-2.0, libgthread-2.0; Qt kullanır)")),
    "jackd2": ("1.9.20~dfsg-1", _ubuntu(
        "jackd2", "1.9.20~dfsg-1", (
            ("jackd2_1.9.20~dfsg-1.dsc",
             "79a4b3db1e84703e9d8ef0d4ecb64095496a42d4d61fc4974c50d2a5dad97332"),
            ("jackd2_1.9.20~dfsg.orig.tar.gz",
             "f87cd57838e258eec97d7de77cab68ac2a4c75907cccd9d2792354011007fe8a"),
            ("jackd2_1.9.20~dfsg-1.debian.tar.xz",
             "f3680739d718667fbdb13bb3e6edecbaf98a5ccd8533ae9d978bc2843d21975f"),
        ), "LGPL-2.1-or-later", "JACK (libjack; PortAudio kullanır)")),
    "alsa-lib": ("1.2.6.1-1ubuntu1.2", _ubuntu(
        "alsa-lib", "1.2.6.1-1ubuntu1.2", (
            ("alsa-lib_1.2.6.1-1ubuntu1.2.dsc",
             "e333bd3c3c60b8617dbc956f06f5b7dad0d862288542d272a267d360fd5c3b55"),
            ("alsa-lib_1.2.6.1.orig.tar.bz2",
             "ad582993d52cdb5fb159a0beab60a6ac57eab0cc1bdf85dc4db6d6197f02333f"),
            ("alsa-lib_1.2.6.1.orig.tar.bz2.asc",
             "6a1f4df007b3e1804c57162e9fef8f2b99c4d7ea48bd4def27d0b9b7dab7350c"),
            ("alsa-lib_1.2.6.1-1ubuntu1.2.debian.tar.xz",
             "fd30152719525f094a951c63811bfcb7410f5d6b8d63987e9f71a95deb2a95e7"),
        ), "LGPL-2.1-or-later", "ALSA (libasound; PortAudio kullanır)")),
}
# The other Ubuntu packages PyInstaller copies a library of: no licence
# that asks for source (full text in lisanslar/sistem/<pkg>.copyright). A
# package in neither table stops the build until someone classifies it.
UBUNTU_NO_SOURCE = {
    "blt": "MIT tarzı", "brotli": "MIT", "bzip2": "BSD tarzı",
    "expat": "MIT", "fontconfig": "MIT tarzı",
    "freetype": "FreeType License (FTL; GPL-2 seçeneği kullanılmaz)",
    "gcc-12": "GPL-3.0 + GCC Runtime Library Exception",
    "libbsd": "BSD/ISC", "libffi": "MIT", "libice": "MIT/X11",
    "libmd": "BSD/ISC", "libpng1.6": "libpng", "libsm": "MIT/X11",
    "libx11": "MIT/X11", "libxau": "MIT/X11", "libxcb": "MIT/X11",
    "libxdmcp": "MIT/X11", "libxext": "MIT/X11", "libxfixes": "MIT/X11",
    "libxrender": "MIT/X11", "libxss": "MIT/X11", "ncurses": "MIT/X11",
    "openssl": "Apache-2.0", "pcre3": "BSD", "portaudio19": "MIT",
    "python3.11": "PSF-2.0", "sqlite3": "kamu malı", "tcl8.6": "BSD tarzı",
    "tk8.6": "BSD tarzı", "util-linux": "BSD-3-Clause (libuuid)",
    "xft": "MIT/X11", "xz-utils": "kamu malı (liblzma)", "zlib": "zlib",
}

# (L)GPL native libraries in a Linux bundle, by EXACT file name -> their
# sources. auditwheel names a vendored library after a hash of its
# contents, so a new wheel build means a new name, which `denetle` refuses
# until this table and the sources are updated. Windows: none at all.
LINUX_NATIVE = {
    "libavcodec-156beeea.so.62.11.100": (_FFMPEG, _OPENCV_SCRIPTS),
    "libavdevice-13b3e407.so.62.1.100": (_FFMPEG, _OPENCV_SCRIPTS),
    "libavfilter-93b41299.so.11.4.100": (_FFMPEG, _OPENCV_SCRIPTS),
    "libavformat-8c8a026e.so.62.3.100": (_FFMPEG, _OPENCV_SCRIPTS),
    "libavutil-ec54c519.so.60.8.100": (_FFMPEG, _OPENCV_SCRIPTS),
    "libswresample-02b1114a.so.6.1.100": (_FFMPEG, _OPENCV_SCRIPTS),
    "libswscale-09114b18.so.9.1.100": (_FFMPEG, _OPENCV_SCRIPTS),
    "libQt5Core-195a14c9.so.5.15.18": (_QT, _OPENCV_SCRIPTS),
    "libQt5Gui-26aef175.so.5.15.18": (_QT, _OPENCV_SCRIPTS),
    "libQt5Test-0e30c269.so.5.15.18": (_QT, _OPENCV_SCRIPTS),
    "libQt5Widgets-da081f00.so.5.15.18": (_QT, _OPENCV_SCRIPTS),
    "libQt5XcbQpa-44a675a7.so.5.15.18": (_QT, _OPENCV_SCRIPTS),
    "libqxcb.so": (_QT, _OPENCV_SCRIPTS),
    "libquadmath-2284e583.so.0.0.0": (_GCC8,),
    "libquadmath-96973f99-934c22de.so.0.0.0": (_GCC4,),
}
# File-name prefixes (lower case) of (L)GPL or proprietary native code. A
# bundled file that starts with one must be in LINUX_NATIVE or be an Ubuntu
# library of UBUNTU_SOURCES (_UBUNTU_FILES, checked by package version) - on
# Windows none may be there at all.
_COPYLEFT_PREFIXES = (
    "libavcodec", "libavdevice", "libavfilter", "libavformat", "libavutil",
    "libswresample", "libswscale", "libpostproc", "avcodec", "avdevice",
    "avfilter", "avformat", "avutil", "swresample", "swscale", "postproc",
    "opencv_videoio_ffmpeg", "libqt5", "qt5", "libqxcb", "libquadmath",
    "libx264", "libx265", "libgnutls", "libgmp", "libnettle", "libhogweed",
    "libunistring", "libmp3lame", "libiconv", "libreadline", "libdb-",
    "libgdbm", "libmvec", "directml", "libglib-", "libgthread-", "libjack",
    "libasound")
_UBUNTU_FILES = ("libglib-2.0.so", "libgthread-2.0.so", "libjack.so.",
                 "libasound.so.")


def _linux():
    return sys.platform.startswith("linux")


def _installed(dist):
    import importlib.metadata as md
    try:
        return md.version(dist)
    except md.PackageNotFoundError:
        return None


def build_sources(linux=None, installed=_installed):
    """The Src list for a package built by THIS interpreter (installed:
    dist name -> version or None) on this OS, in a fixed order without
    duplicates. SystemExit names every copyleft distribution whose
    installed version has no sources listed."""
    linux = _linux() if linux is None else linux
    out, missing = [], []
    for dist in COPYLEFT_DISTS:
        version = installed(dist)
        if version is None:
            continue
        if (dist, version) not in PY_SOURCES:
            missing.append("%s %s" % (dist, version))
            continue
        out.extend(PY_SOURCES[(dist, version)])
    if missing:
        raise SystemExit("HATA: kaynagi listelenmemis copyleft paket(ler): %s"
                         " (hazirla.py PY_SOURCES: PyPI'deki sdist adresi ve"
                         " sha256)" % ", ".join(missing))
    if linux:
        for srcs in LINUX_NATIVE.values():
            out.extend(srcs)
        for _version, srcs in UBUNTU_SOURCES.values():
            out.extend(srcs)
    seen, uniq = set(), []
    for s in out:
        if s.name not in seen:
            seen.add(s.name)
            uniq.append(s)
    return uniq


def sources_text(srcs, linux):
    """The text of KAYNAKLAR-<OS>.txt and lisanslar/KAYNAKLAR.txt."""
    from eye_gaze import __version__ as ver
    lines = [
        "Göz Faresi %s - %s paketi: GPL / LGPL / MPL bileşenlerin kaynak"
        " kodu" % (ver, "Linux" if linux else "Windows"),
        "=" * 72, "",
        "Aşağıdaki dosyaların HER BİRİ paketlerle aynı yerde yayınlanır",
        "(GozFaresi-%s-... dosyalarının yanında; SHA-256 değerleri" % ver,
        "SHA256SUMS dosyasında da var). Adresler yalnız bilgi içindir.",
        "Göz Faresi'nin kendi kaynağı: GozFaresi-%s-kaynak.zip." % ver, ""]
    for s in srcs:
        lines += ["- " + s.component, "    lisans:  " + s.licence,
                  "    dosya:   " + s.name, "    adres:   " + s.url,
                  "    SHA-256: " + s.sha,
                  "    sürüm nasıl belirlendi: " + s.how, ""]
    lines += [
        "Kaynağı GEREKMEYEN, kendi lisansı izin verdiği için: GCC çalışma",
        "kitaplıkları (libgcc_s, libstdc++, libgfortran, libgomp), GPL-3.0 +",
        "GCC Runtime Library Exception.",
        "Bu sürümde pakete HİÇ KONMAYANLAR: PyAV ve içindeki FFmpeg / x264 /",
        "x265 / GnuTLS, OpenCV'nin Windows FFmpeg eklentisi, GNU readline,",
        "Berkeley DB, glibc'nin libmvec'i, PortAudio'nun ASIO derlemesi,",
        "DirectML.", ""]
    return "\n".join(lines)


def write_sources_list(path, linux=None):
    linux = _linux() if linux is None else linux
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(sources_text(build_sources(linux), linux))


def gpl_sources(dist, cache):
    """Every source the package built here needs, into `dist`, plus
    KAYNAKLAR-<OS>.txt."""
    os.makedirs(dist, exist_ok=True)
    linux = _linux()
    srcs = build_sources(linux)
    for s in srcs:
        src = fetch(s.url, s.sha, cache, s.name)
        dst = os.path.join(dist, s.name)
        if not (os.path.isfile(dst) and sha256(dst) == s.sha):
            shutil.copyfile(src, dst + ".part")
            os.replace(dst + ".part", dst)
        say("kaynak: %s" % s.name)
    write_sources_list(os.path.join(
        dist, "KAYNAKLAR-%s.txt" % ("Linux" if linux else "Windows")), linux)
    say("kaynaklar: %d dosya" % len(srcs))


def _ubuntu_packages(onedir):
    """{source package: version} of the Ubuntu packages that own a library
    in _internal (dpkg-query; {} where there is no dpkg)."""
    import subprocess
    internal = os.path.join(onedir, "_internal")
    if not shutil.which("dpkg-query") or not os.path.isdir(internal):
        return {}
    owners = set()
    for name in sorted(os.listdir(internal)):
        p = os.path.join(internal, name)
        if ".so" not in name or os.path.islink(p) or not os.path.isfile(p):
            continue
        r = subprocess.run(["dpkg-query", "-S", "*/" + name],
                           capture_output=True, text=True)
        for line in r.stdout.splitlines():
            owners.update(o.strip().split(":")[0]
                          for o in line.rsplit(": ", 1)[0].split(","))
    out = {}
    for pkg in sorted(owners):
        r = subprocess.run(["dpkg-query", "-W", "-f",
                            "${source:Package} ${source:Version}", pkg],
                           capture_output=True, text=True)
        parts = r.stdout.split()
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


def source_problems(onedir, linux, ubuntu=None):
    """Why the bundle's (L)GPL / proprietary native parts do not match the
    sources listed here; [] when they do. ubuntu: {source package: version}
    (default: _ubuntu_packages(onedir))."""
    bad = []
    for root, dirs, files in os.walk(onedir):
        rel_root = os.path.relpath(root, onedir)
        if rel_root == ".":
            # lisanslar/ holds licence texts named after the packages
            # (sistem/libjack-jackd2-0.copyright), not their code.
            dirs[:] = [d for d in dirs if d != "lisanslar"]
        for d in dirs:
            if d.lower() in ("av", "av.libs") and \
                    os.path.basename(root) == "_internal":
                bad.append(os.path.join(rel_root, d) + " (PyAV)")
        for f in files:
            low = f.lower()
            rel = os.path.join(rel_root, f)
            if low.endswith("-asio.dll"):
                bad.append(rel + " (ASIO)")
            elif low.startswith(_COPYLEFT_PREFIXES) and not (
                    linux and (f in LINUX_NATIVE
                               or f.startswith(_UBUNTU_FILES))):
                bad.append(rel + " (kaynagi listelenmemis)")
    if linux:
        if ubuntu is None:
            ubuntu = _ubuntu_packages(onedir)
        for pkg, version in sorted(ubuntu.items()):
            if pkg in UBUNTU_SOURCES:
                if UBUNTU_SOURCES[pkg][0] != version:
                    bad.append("Ubuntu %s %s (kaynagi %s icin listelenmis)"
                               % (pkg, version, UBUNTU_SOURCES[pkg][0]))
            elif pkg not in UBUNTU_NO_SOURCE:
                bad.append("Ubuntu %s %s (lisansi siniflandirilmamis:"
                           " UBUNTU_SOURCES ya da UBUNTU_NO_SOURCE)"
                           % (pkg, version))
    return bad



# --- the voice worker's isolation (Task 18b, R44) ----------------------------
# The main program (our code with CTranslate2/oneMKL, OpenCV/OpenSSL 1.1.1,
# ...) and the voice worker (piper + espeak-ng, GPL-3.0) are two executables
# with their own PYZ. What each may hold, by module name:
MAIN_NEVER = ("piper", "gozfaresi_ses")
# (Extension modules - _ssl, _hashlib, _tkinter - are native files, not
# PYZ entries: SES_NATIVE_NEVER below covers them.)
SES_NEVER = ("cv2", "ctranslate2", "faster_whisper", "mediapipe",
             "matplotlib", "tkinter", "PIL", "gaze_driver", "gozfaresi",
             "ssl", "sounddevice", "huggingface_hub", "tokenizers", "av")
# eye_gaze in the worker: the package marker and the pipe protocol only.
SES_EYE_GAZE = ("eye_gaze", "eye_gaze.ses_protokol")
SES_MUST = ("piper", "piper.voice", "eye_gaze.ses_protokol")


def pyz_modules(exe):
    """Module names in the PYZ embedded in a PyInstaller executable (the
    build interpreter's PyInstaller reads it)."""
    import tempfile
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    data = CArchiveReader(exe).extract("PYZ.pyz")
    fd, tmp = tempfile.mkstemp(suffix=".pyz")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return set(ZlibArchiveReader(tmp).toc)
    finally:
        os.remove(tmp)


def _under(name, roots):
    return any(name == r or name.startswith(r + ".") for r in roots)


def isolation_check(main_mods, ses_mods):
    """Problems with the two module sets ([] when the split is right)."""
    bad = ["ana program: %s (piper yalniz ses surecinde olmali)" % m
           for m in sorted(main_mods) if _under(m, MAIN_NEVER)]
    bad += ["ses sureci: %s (bu surece girmemeli)" % m
            for m in sorted(ses_mods)
            if _under(m, SES_NEVER) or (_under(m, ("eye_gaze",))
                                        and m not in SES_EYE_GAZE)]
    bad += ["ses sureci: %s eksik" % m for m in SES_MUST if m not in ses_mods]
    return bad


# Native files (by lower-case file-name prefix) that must never be in the
# voice worker's own binary set, and the one that must never be in the
# program's.
SES_NATIVE_NEVER = ("mkl", "libmkl", "libiomp", "iomp", "libcrypto",
                    "libssl", "_ssl.", "_hashlib.", "cv2", "opencv",
                    "ctranslate2", "libctranslate2", "libqt5", "qt5",
                    "libavcodec", "libavformat", "libavutil", "avcodec",
                    "libmediapipe", "cudnn", "cublas", "libcudnn", "libcublas",
                    "directml", "_tkinter.", "tcl", "tk86", "libtk", "libtcl")
MAIN_NATIVE_NEVER = ("espeakbridge",)


def native_isolation_check(main_bins, ses_bins):
    """Problems with the two executables' own native file sets."""
    def base(p):
        return p.replace("\\", "/").rsplit("/", 1)[-1].lower()
    bad = ["ana program ikilisi: %s (yalniz ses surecinde olmali)" % p
           for p in sorted(main_bins) if base(p).startswith(MAIN_NATIVE_NEVER)]
    bad += ["ses sureci ikilisi: %s (bu surece girmemeli)" % p
            for p in sorted(ses_bins) if base(p).startswith(SES_NATIVE_NEVER)]
    if not any(base(p).startswith("espeakbridge") for p in ses_bins):
        bad.append("ses sureci ikilisi: espeakbridge eksik")
    return bad


def binary_sets(onedir):
    """(program's, worker's) native file lists, written by gozfaresi.spec
    next to the onedir folder."""
    import json
    path = onedir.rstrip("/\\") + "-ikililer.json"
    with open(path, encoding="ascii") as f:
        data = json.load(f)
    return data["ana"], data["ses"]


def isolation_problems(onedir, linux):
    main = os.path.join(onedir, "gozfaresi" if linux else "GozFaresi.exe")
    ses = os.path.join(onedir, "gozfaresi-ses" if linux
                       else "GozFaresi-Ses.exe")
    if not (os.path.isfile(main) and os.path.isfile(ses)):
        return []          # the "need" list reports the missing executable
    try:
        bad = isolation_check(pyz_modules(main), pyz_modules(ses))
        bad += native_isolation_check(*binary_sets(onedir))
        return bad
    except Exception as exc:
        return ["ses sureci ayrimi denetlenemedi: %s: %s"
                % (type(exc).__name__, exc)]

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sahne")
    s.add_argument("--cikti", default=os.path.join(HERE, "out", "stage"))
    s.add_argument("--onbellek", default=os.path.join(HERE, "out", "indirme"))
    sub.add_parser("onnx-denetle")
    z = sub.add_parser("tasinabilir-zip")
    z.add_argument("onedir")
    z.add_argument("zip")
    m = sub.add_parser("sha256sums")
    m.add_argument("klasor")
    lc = sub.add_parser("lisanslar")
    lc.add_argument("onedir")
    g = sub.add_parser("gpl-kaynak")
    g.add_argument("klasor")
    g.add_argument("--onbellek", default=os.path.join(HERE, "out", "indirme"))
    dn = sub.add_parser("denetle")
    dn.add_argument("onedir")
    a = ap.parse_args(argv)
    if a.cmd == "denetle":
        check_bundle(os.path.abspath(a.onedir))
        return 0
    if a.cmd == "lisanslar":
        collect_licences(os.path.abspath(a.onedir))
        return 0
    if a.cmd == "gpl-kaynak":
        gpl_sources(os.path.abspath(a.klasor), os.path.abspath(a.onbellek))
        return 0
    if a.cmd == "onnx-denetle":
        return 0 if onnx_ok() else 3
    if a.cmd == "tasinabilir-zip":
        portable_zip(os.path.abspath(a.onedir), os.path.abspath(a.zip))
        return 0
    if a.cmd == "sha256sums":
        write_sums(os.path.abspath(a.klasor))
        return 0
    stage(os.path.abspath(a.cikti), os.path.abspath(a.onbellek))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
