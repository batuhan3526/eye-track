# gaze/tests/test_packaging_scripts.py
"""The build and installer scripts under packaging/ (repo root), read as
text: only what can be checked without building. Skipped where packaging/
is not next to gaze/ (an export of gaze/ alone)."""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PACKAGING = os.path.join(ROOT, "packaging")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(PACKAGING), reason="packaging/ is not in this tree")


def _iss_section(name):
    with open(os.path.join(PACKAGING, "windows", "kurulum.iss"),
              encoding="utf-8-sig") as f:
        text = f.read()
    m = re.search(r"^\[%s\]\s*$(.*?)(?=^\[)" % re.escape(name), text,
                  re.M | re.S)
    assert m, name
    return [ln.strip() for ln in m.group(1).splitlines()
            if ln.strip() and not ln.strip().startswith(";")]


def test_an_upgrade_removes_only_the_two_program_folders_first():
    # Final review M-3: stale _internal/*.dist-info and licence folders of
    # an older version must go; {app} itself may hold portable-mode data
    # (config.yaml, "tasinabilir") and is never emptied.
    assert _iss_section("InstallDelete") == [
        'Type: filesandordirs; Name: "{app}\\_internal"',
        'Type: filesandordirs; Name: "{app}\\lisanslar"',
    ]


def _hazirla():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hazirla_under_test", os.path.join(PACKAGING, "hazirla.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_deep_model_must_be_the_pinned_one_not_only_match_its_sidecar(
        tmp_path, monkeypatch, capsys):
    # Final review M-2: the converter writes the sidecar itself, so a model
    # made from other weights "agrees" with it. Deep calibrations are fitted
    # on the shipped model's output.
    import hashlib
    hz = _hazirla()
    assert hz.ONNX_SHA == ("add4976870f63efbf643af73c9884c9258f27e3a7"
                           "812e4b8aa3ec390e63a7886")
    model = tmp_path / "eth-xgaze-resnet18.onnx"
    model.write_bytes(b"another conversion")
    digest = hashlib.sha256(b"another conversion").hexdigest()
    (tmp_path / "eth-xgaze-resnet18.onnx.sha256").write_text(
        digest + "  eth-xgaze-resnet18.onnx\n", encoding="ascii")
    monkeypatch.setattr(hz, "ONNX", str(model))
    assert hz.onnx_ok() is False
    out = capsys.readouterr().out
    assert "sabitlenen model degil" in out and out.isascii()
    # The pinned bytes pass (pin moved to this file's hash for the test).
    monkeypatch.setattr(hz, "ONNX_SHA", digest)
    assert hz.onnx_ok() is True
    # A sidecar that disagrees still fails first.
    (tmp_path / "eth-xgaze-resnet18.onnx.sha256").write_text(
        "0" * 64 + "  x\n", encoding="ascii")
    assert hz.onnx_ok() is False


# --- Task 18: legally safest release set ------------------------------------

def _tree(root, *rels):
    for rel in rels:
        path = root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    return root


def test_the_windows_bundle_may_hold_no_directml_pyav_asio_or_ffmpeg(
        tmp_path):
    # R39: DirectML.dll (Microsoft's own terms) must not ship next to the
    # GPL-3.0 piper/espeak-ng; R41: PyAV's FFmpeg/x264/x265, OpenCV's FFmpeg
    # plugin and PortAudio's ASIO build are left out, not sourced.
    hz = _hazirla()
    clean = _tree(tmp_path / "temiz", "GozFaresi.exe",
                  "_internal/onnxruntime/capi/onnxruntime.dll",
                  "_internal/cv2/cv2.pyd",
                  "_internal/_sounddevice_data/portaudio-binaries/"
                  "libportaudio64bit.dll",
                  "_internal/numpy.libs/libscipy_openblas64_-63c8.dll",
                  "_internal/resources/aac/x.xml")
    assert hz.source_problems(str(clean), linux=False) == []
    dirty = _tree(tmp_path / "kirli", "GozFaresi.exe",
                  "_internal/onnxruntime/capi/DirectML.dll",
                  "_internal/cv2/opencv_videoio_ffmpeg4130_64.dll",
                  "_internal/_sounddevice_data/portaudio-binaries/"
                  "libportaudio64bit-asio.dll",
                  "_internal/av/__init__.py",
                  "_internal/av.libs/libx264-165-f3a9.dll",
                  "_internal/av.libs/avcodec-62-984d.dll")
    bad = " ".join(hz.source_problems(str(dirty), linux=False))
    for name in ("DirectML.dll", "opencv_videoio_ffmpeg4130_64.dll",
                 "libportaudio64bit-asio.dll", "av (PyAV)",
                 "av.libs (PyAV)", "libx264-165-f3a9.dll",
                 "avcodec-62-984d.dll"):
        assert name in bad, name
    # Not even a sourced Linux library is allowed on Windows.
    _tree(tmp_path / "w2", "_internal/libQt5Core-195a14c9.so.5.15.18")
    assert hz.source_problems(str(tmp_path / "w2"), linux=False)


def test_linux_copyleft_libraries_must_be_exactly_the_sourced_ones(tmp_path):
    hz = _hazirla()
    names = sorted(hz.LINUX_NATIVE)
    ok = _tree(tmp_path / "ok", "gozfaresi",
               *["_internal/" + n for n in names],
               "_internal/opencv_contrib_python.libs/" + names[0],
               "_internal/libglib-2.0.so.0", "_internal/libjack.so.0",
               "_internal/libasound.so.2", "_internal/libavif-a883.so.16",
               # licence texts named after the packages are not their code
               "lisanslar/sistem/libjack-jackd2-0.copyright",
               "lisanslar/libqt5-notice.txt")
    ubuntu = {pkg: v for pkg, (v, _s) in hz.UBUNTU_SOURCES.items()}
    ubuntu["libx11"] = "2:1.7.5-1ubuntu0.4"
    assert hz.source_problems(str(ok), True, ubuntu=ubuntu) == []
    # Another wheel build (new auditwheel hash), PyAV's FFmpeg, glibc's
    # libmvec, GNU readline, Berkeley DB: refused.
    bad_tree = _tree(tmp_path / "bad", "gozfaresi",
                     "_internal/libavcodec-5e6b81c2.so.62.28.102",
                     "_internal/libQt5Core-00000000.so.5.15.19",
                     "_internal/libmvec.so.1", "_internal/libreadline.so.8",
                     "_internal/libdb-5.3.so", "_internal/libx265-f538.so.216",
                     "_internal/av/_core.abi3.so")
    bad = " ".join(hz.source_problems(str(bad_tree), True, ubuntu={}))
    for name in ("libavcodec-5e6b81c2", "libQt5Core-00000000", "libmvec",
                 "libreadline", "libdb-5.3", "libx265", "av (PyAV)"):
        assert name in bad, name
    # The Ubuntu libraries are tied to the exact source package version,
    # and an unclassified package stops the build.
    moved = dict(ubuntu, **{"glib2.0": "2.72.4-0ubuntu2.11",
                            "newlib": "1.0-1"})
    bad = " ".join(hz.source_problems(str(ok), True, ubuntu=moved))
    assert "glib2.0 2.72.4-0ubuntu2.11" in bad and "newlib 1.0-1" in bad


def test_each_build_lists_the_exact_sources_of_its_copyleft_parts():
    hz = _hazirla()
    win = {"piper-tts": "1.6.0", "pyinstaller": "6.22.3",
           "certifi": "2026.6.17", "tqdm": "4.70.1"}
    names = [s.name for s in hz.build_sources(False, installed=win.get)]
    assert names == ["piper1-gpl-v1.6.0.tar.gz",
                     "espeak-ng-724808c5a83f9ef95fdd0db886ba7ba537ff224a"
                     ".tar.gz", "pyinstaller-6.22.3.tar.gz",
                     "certifi-2026.6.17.tar.gz", "tqdm-4.70.1.tar.gz"]
    lin = dict(win, certifi="2026.10.19", **{"python-xlib": "0.33"})
    srcs = hz.build_sources(True, installed=lin.get)
    names = [s.name for s in srcs]
    assert len(names) == len(set(names))
    for want in ("python-xlib-0.33.tar.gz", "ffmpeg-8.0.1.tar.gz",
                 "qt-everywhere-opensource-src-5.15.18.tar.xz",
                 "opencv-python-92.tar.gz",
                 "gcc-8.5.0-28.el8_10.alma.1.src.rpm",
                 "gcc-4.8.5-44.el7.src.rpm",
                 "glib2.0_2.72.4.orig.tar.xz", "jackd2_1.9.20~dfsg-1.dsc",
                 "alsa-lib_1.2.6.1-1ubuntu1.2.debian.tar.xz",
                 "certifi-2026.10.19.tar.gz"):
        assert want in names, want
    for s in srcs:
        assert re.fullmatch(r"[0-9a-f]{64}", s.sha), s.name
        assert s.url.startswith("https://") and s.how and s.licence
    # A copyleft distribution at a version nobody read the source of: the
    # build stops instead of shipping it without.
    with pytest.raises(SystemExit) as e:
        hz.build_sources(False, installed=dict(win, tqdm="4.71.0").get)
    assert "tqdm 4.71.0" in str(e.value) and str(e.value).isascii()
    text = hz.sources_text(srcs, True)
    assert "ffmpeg-8.0.1.tar.gz" in text and "Linux" in text
    assert "c1c5f8d61f17e5579c4e85718845b74e4b94116c" in text  # how: build-id


def test_the_licence_summary_says_the_sources_ship_with_the_packages():
    with open(os.path.join(PACKAGING, "UCUNCU-TARAF-LISANSLAR.txt"),
              encoding="utf-8") as f:
        text = f.read()
    assert "KAYNAKLAR" in text
    for part in ("FFmpeg 8.0.1", "Qt 5.15.18", "python-xlib", "libquadmath",
                 "GLib", "JACK", "ALSA", "PyInstaller", "certifi", "tqdm",
                 "piper1-gpl-v1.6.0.tar.gz", "en_US-ljspeech-medium"):
        assert part in text, part
    # Never again "only a link", DirectML or the lessac English voice.
    for gone in ("yalnız bağlantı", "DirectML.dll", "en_US-lessac"):
        assert gone not in text, gone


def test_the_windows_build_uses_the_cpu_onnxruntime_only():
    with open(os.path.join(PACKAGING, "windows", "requirements-win.txt"),
              encoding="utf-8") as f:
        reqs = [ln.split("#")[0].strip() for ln in f]
    reqs = [r for r in reqs if r]
    assert "onnxruntime==1.24.4" in reqs
    assert not any(r.startswith(("onnxruntime-directml", "onnxruntime-gpu"))
                   for r in reqs)
    with open(os.path.join(PACKAGING, "windows", "derle.ps1"),
              encoding="ascii") as f:
        ps = f.read()
    assert "'DmlExecutionProvider' not in p" in ps
    assert "onnxruntime-directml==" not in ps


def test_the_spec_leaves_pyav_readline_and_dbm_out():
    with open(os.path.join(PACKAGING, "gozfaresi.spec"),
              encoding="utf-8") as f:
        spec = f.read()
    assert 'excludes += ["av", "readline", "_dbm"]' in spec
    assert '"pyi_rth_av_yok.py"' in spec
    for name in ("opencv_videoio_ffmpeg", "-asio.dll", "libmvec.so.1",
                 '"libdb-"', '"_dbm."'):
        assert name in spec, name


_STUB_CHECK = r"""
import importlib.util, runpy, sys
runpy.run_path(sys.argv[1])
import av
assert sys.modules["av"] is av and importlib.util.find_spec("av")
try:
    av.open
except AttributeError as e:
    assert "PyAV" in str(e) and str(e).isascii(), e
else:
    raise SystemExit("no error")
assert not hasattr(av, "audio")
if importlib.util.find_spec("faster_whisper") is not None:
    import numpy as np, faster_whisper
    from faster_whisper.audio import pad_or_trim
    assert pad_or_trim(np.zeros(10, np.float32), 16).shape == (16,)
    try:
        faster_whisper.decode_audio("yok.wav")
    except AttributeError as e:
        assert "PyAV" in str(e)
    else:
        raise SystemExit("decoded without PyAV")
    print("faster_whisper ok")
print("stub ok")
"""


def test_the_pyav_stub_lets_faster_whisper_import_and_names_the_reason():
    # The frozen build has no PyAV; faster_whisper/audio.py imports it at the
    # top but only decode_audio() (files, streams) uses it. The program
    # feeds numpy audio, so import + pad_or_trim must work and a file decode
    # must fail loudly.
    import subprocess
    import sys
    hook = os.path.join(PACKAGING, "pyi_rth_av_yok.py")
    r = subprocess.run([sys.executable, "-c", _STUB_CHECK, hook],
                       capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, CUDA_VISIBLE_DEVICES=""))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "stub ok" in r.stdout


# --- Task 18b: the voice worker's isolation ----------------------------------

def test_piper_may_only_be_in_the_voice_worker_and_nothing_else_there():
    hz = _hazirla()
    main = {"gaze_driver", "eye_gaze", "eye_gaze.ses_isci", "cv2",
            "ctranslate2", "onnxruntime", "numpy"}
    ses = {"piper", "piper.voice", "piper.config", "numpy", "onnxruntime",
           "eye_gaze", "eye_gaze.ses_protokol", "json", "wave"}
    assert hz.isolation_check(main, ses) == []
    bad = " ".join(hz.isolation_check(main | {"piper.voice"}, ses))
    assert "ana program: piper.voice" in bad
    for intruder in ("cv2", "ctranslate2.models", "ssl",
                     "eye_gaze.tts_backends", "gaze_driver", "PIL.Image"):
        bad = " ".join(hz.isolation_check(main, ses | {intruder}))
        assert "ses sureci: %s" % intruder in bad, intruder
    assert "piper.voice eksik" in " ".join(
        hz.isolation_check(main, ses - {"piper.voice"}))


def test_the_bundle_guard_requires_the_voice_worker():
    with open(os.path.join(PACKAGING, "hazirla.py"), encoding="utf-8") as f:
        text = f.read()
    assert '"gozfaresi-ses" if linux else "GozFaresi-Ses.exe"' in text
    assert "bad += isolation_problems(onedir, linux)" in text
    with open(os.path.join(PACKAGING, "gozfaresi.spec"),
              encoding="utf-8") as f:
        spec = f.read()
    assert 'excludes += ["piper"]' in spec
    assert "gozfaresi_ses.py" in spec and "ses_exe," in spec


def test_the_section_7_permission_is_stated_word_for_word():
    canon = ("As an additional permission under section 7 of the GNU GPL "
             "version 3, the copyright holders of Göz Faresi give you "
             "permission to combine Göz Faresi with, and convey the result "
             "together with, the following libraries that are not "
             "GPL-compatible, in unmodified form: Intel oneMKL and Intel "
             "OpenMP runtime (as used by CTranslate2), OpenSSL 1.1.x, NVIDIA "
             "CUDA/cuDNN/cuBLAS runtime libraries. The corresponding source "
             "of Göz Faresi itself must still be provided under the GPL. This "
             "permission does not extend to other GPL-licensed works "
             "distributed with Göz Faresi (such as piper-tts and espeak-ng), "
             "which run as a separate program.")
    for rel in (("packaging", "UCUNCU-TARAF-LISANSLAR.txt"),
                ("gaze", "README.md")):
        with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
            flat = " ".join(f.read().split())
        assert canon in flat, rel
    # Not inside the GPL text itself.
    with open(os.path.join(ROOT, "gaze", "LICENSE"), encoding="utf-8") as f:
        assert "additional permission under section 7 of the GNU GPL" \
            not in f.read()


def test_each_executable_s_native_files_are_checked_for_the_split():
    hz = _hazirla()
    main = ["ctranslate2/libiomp5md.dll", "ctranslate2/ctranslate2.dll",
            "cv2/cv2.pyd", "_ssl.pyd", "libcrypto-3.dll", "onnxruntime.dll"]
    ses = ["piper/espeakbridge.pyd", "onnxruntime/capi/onnxruntime.dll",
           "numpy.libs/libscipy_openblas64_-63c8.dll", "_ctypes.pyd"]
    assert hz.native_isolation_check(main, ses) == []
    bad = " ".join(hz.native_isolation_check(
        main + ["piper/espeakbridge.pyd"], ses))
    assert "ana program ikilisi: piper/espeakbridge.pyd" in bad
    for intruder in ("ctranslate2/libiomp5md.dll", "_hashlib.pyd",
                     "libssl-60250281.so.1.1.1k", "cv2/cv2.abi3.so",
                     "libctranslate2-bdb19cbc.so.4.8.3", "mkl_core.2.dll",
                     "libQt5Core-195a14c9.so.5.15.18", "_ssl.cpython-311.so"):
        bad = " ".join(hz.native_isolation_check(main, ses + [intruder]))
        assert "ses sureci ikilisi: %s" % intruder in bad, intruder
    assert "espeakbridge eksik" in " ".join(
        hz.native_isolation_check(main, ses[1:]))


def test_the_spec_writes_both_executables_native_file_lists():
    with open(os.path.join(PACKAGING, "gozfaresi.spec"),
              encoding="utf-8") as f:
        spec = f.read()
    assert '"-ikililer.json"' in spec and '"ses": sorted(' in spec


def test_the_turkish_voice_s_lessac_parentage_is_an_open_question():
    # R46 / I-3: the shipped dfki voice is fine-tuned from lessac, whose
    # Blizzard 2013 data terms made us drop the English lessac voice.
    url = ("https://www.cstr.ed.ac.uk/projects/blizzard/2013/"
           "lessac_blizzard2013/license.html")
    for rel in (("packaging", "UCUNCU-TARAF-LISANSLAR.txt"),
                ("gaze", "README.md")):
        with open(os.path.join(ROOT, *rel), encoding="utf-8") as f:
            text = f.read()
        assert url in text, rel
