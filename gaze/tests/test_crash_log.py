# gaze/tests/test_crash_log.py
"""What cokme.log and gaze.log say about each run.

A tester's 1.3.2 cokme.log had "Windows fatal exception: code 0x80040155"
and "0x80010108" dumps under the driver - yet the driver ran on for minutes
each time: Windows raises some COM errors as exceptions and handles them
itself, and faulthandler writes every one (first chance). The log looked
like crashes that never happened. And which version wrote a log had to be
asked: the first report of that week came from an older install. Now:

- every cokme.log header and every gaze.log run header names the version;
- a process that ends normally after such dumps says so under them.
"""
import os
import subprocess
import sys

import pytest

import eye_gaze

GAZE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(tmp_path, body, role="--surucu"):
    code = ("import sys\n"
            "sys.argv = ['gozfaresi', %r]\n"
            "import gozfaresi\n"
            "f = gozfaresi.crash_log()\n"
            "assert f is not None\n" % role) + body
    env = dict(os.environ, GOZFARESI_DATA_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", code], cwd=GAZE_DIR, env=env,
                       capture_output=True, timeout=60)
    with open(tmp_path / "cokme.log", encoding="utf-8",
              errors="replace") as f:
        return r, f.read()


def test_the_header_names_the_role_and_the_version(tmp_path):
    _, text = _run(tmp_path, "gozfaresi.crash_log_end(f, 0)\n")
    first = text.splitlines()[0]
    assert "--surucu" in first and eye_gaze.__version__ in first


def test_a_quiet_normal_end_adds_nothing(tmp_path):
    r, text = _run(tmp_path, "gozfaresi.crash_log_end(f, 0)\n")
    assert r.returncode == 0
    assert len(text.splitlines()) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows structured exceptions")
def test_a_normal_end_after_a_handled_windows_exception_says_so(tmp_path):
    # ctypes handles the exception RaiseException throws (it becomes
    # OSError) - but faulthandler has already written its dump, exactly
    # like the COM errors in the tester's log.
    body = ("import ctypes\n"
            "try:\n"
            "    ctypes.windll.kernel32.RaiseException(0x80040155, 0, 0, None)\n"
            "except OSError:\n"
            "    pass\n"
            "gozfaresi.crash_log_end(f, 0)\n")
    r, text = _run(tmp_path, body)
    assert r.returncode == 0, r.stderr
    assert "Windows fatal exception: code 0x80040155" in text
    last = text.strip().splitlines()[-1]
    assert "--surucu" in last and "kod 0" in last
    assert "DURDURMADI" in last
    assert text.index("0x80040155") < text.index(last)


@pytest.mark.skipif(os.name != "nt", reason="Windows structured exceptions")
def test_a_real_crash_gets_no_all_clear(tmp_path):
    body = ("import ctypes, threading\n"
            "def kamera():\n"
            "    ctypes.c_int.from_address(0).value\n"
            "t = threading.Thread(target=kamera)\n"
            "t.start(); t.join()\n"
            "gozfaresi.crash_log_end(f, 0)\n")
    r, text = _run(tmp_path, body)
    assert r.returncode != 0
    assert "DURDURMADI" not in text


def test_gaze_log_names_the_version_of_each_run(tmp_path, monkeypatch):
    import gaze_driver as gd
    monkeypatch.setattr(gd, "_BASE_DIR", str(tmp_path))
    out, err = sys.stdout, sys.stderr
    try:
        gd._setup_logging()
    finally:
        tee = sys.stdout
        sys.stdout, sys.stderr = out, err
    for stream in getattr(tee, "_streams", ()):
        if stream not in (out, err):
            stream.close()
    with open(tmp_path / "gaze.log", encoding="utf-8") as f:
        text = f.read()
    assert "yeni calisma" in text and eye_gaze.__version__ in text
