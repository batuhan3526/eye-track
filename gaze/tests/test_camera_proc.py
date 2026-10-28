# gaze/tests/test_camera_proc.py
"""Cameras in a process of their own (eye_gaze.camera_proc): a driver fault
ends the child, never the wizard - and the search goes on without the mode
that faulted.

The children here are real processes (tests/kamera_cocugu.py) running the
real child code on fake cameras; the faults are real ones
(faulthandler._sigsegv: an access violation on Windows, SIGSEGV on Linux).
No real camera is opened (the user may be using it).
"""
import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from eye_gaze import camera_proc, hardware as hw

GAZE = Path(__file__).resolve().parent.parent
HELPER = str(Path(__file__).resolve().parent / "kamera_cocugu.py")
FAST = 100      # fps of the fake cameras: a short read window still counts


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("GOZFARESI_DATA_DIR", str(d))
    monkeypatch.delenv(hw.FAKE_CAMERA_ENV, raising=False)
    return d


def _crash_log(data_dir):
    p = data_dir / hw.CRASH_LOG
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _child(scenario, role=camera_proc.PROBE_ROLE):
    return [sys.executable, HELPER, json.dumps(scenario), role]


def _cam(backends=("dshow",), modes=((640, 480), (1280, 720), (1920, 1080)),
         crash=()):
    return {"backends": list(backends),
            "modes": [[w, h, FAST] for w, h in modes],
            "crash": [list(c) for c in crash]}


def _probe(scenario, indices=(0,), **kw):
    kw.setdefault("read_seconds", 0.1)
    kw.setdefault("timeout_s", 30)
    return hw.probe_cameras(indices=list(indices), child=_child(scenario),
                            profile={"machine": {"cpu_cores": 8}}, **kw)


@pytest.fixture
def no_pause(monkeypatch):
    monkeypatch.setattr(hw, "CHILD_PAUSE_S", 0.0)


# --- the search -------------------------------------------------------------

def test_the_search_in_a_child_finds_what_the_in_process_one_does(no_pause):
    scenario = {"backends": ["msmf", "dshow"],
                "cams": {"0": _cam(backends=("msmf", "dshow"))}}
    found = _probe(scenario, indices=(0, 1))
    assert [(c.index, c.backend) for c in found] == [(0, "msmf")]
    asked = {tuple(m["request"] or ()) for m in found[0].modes}
    assert {(), (1920, 1200), (1920, 1080), (1280, 720)} <= asked
    # (Which one is chosen depends on this machine's frame cost.)
    assert (found[0].width, found[0].height) in {
        (m["width"], m["height"]) for m in found[0].modes}


def test_a_driver_fault_ends_only_the_child_and_its_mode_is_skipped(
        no_pause, _data_dir):
    """The tester's 1.3.1 crash: no Media Foundation picture, so DirectShow,
    and the driver faulted asked for 1920x1200 (cokme.log 2026-10-28). The
    search survives it, leaves that mode out and measures the rest."""
    scenario = {"backends": ["msmf", "dshow"],
                "cams": {"0": _cam(crash=[("dshow", 1920, 1200, "")])}}
    found = _probe(scenario)
    assert [(c.index, c.backend) for c in found] == [(0, "dshow")]
    asked = {(tuple(m["request"] or ()), m["fourcc"])
             for m in found[0].modes}
    assert ((1920, 1200), "") not in asked
    assert {((1920, 1080), ""), ((1280, 720), ""),
            ((1920, 1200), "MJPG")} <= asked
    log = _crash_log(_data_dir)
    assert "kamera 0: dshow 1920x1200 olculurken surucu coktu" in log
    assert "atlanip" in log


def test_a_camera_that_keeps_faulting_is_given_up_the_others_are_found(
        no_pause, _data_dir):
    faults = [("dshow", w, h, "") for w, h in hw.CANDIDATES]
    scenario = {"backends": ["msmf", "dshow"],
                "cams": {"0": _cam(backends=("msmf",)),
                         "1": _cam(crash=faults)}}
    t0 = time.monotonic()
    found = _probe(scenario, indices=(0, 1, 2))
    assert time.monotonic() - t0 < 60
    by_index = {c.index: c for c in found}
    assert set(by_index) == {0, 1}
    # Camera 1 measured only its own mode before every asked one faulted:
    # that is what it keeps.
    assert [(m["width"], m["height"], m["request"])
            for m in by_index[1].modes] == [(640, 480, None)]
    lines = [l for l in _crash_log(_data_dir).splitlines()
             if "kamera 1:" in l]
    assert len(lines) == hw.CHILD_CRASHES
    assert "birakildi" in lines[-1]


def test_a_fault_while_measuring_its_own_mode_still_measures_the_rest(
        no_pause):
    scenario = {"backends": ["msmf"],
                "cams": {"0": _cam(backends=("msmf",),
                                   crash=[("msmf", 0, 0, "")])}}
    found = _probe(scenario)
    assert len(found) == 1
    assert all(m["request"] is not None for m in found[0].modes)


def test_a_child_that_cannot_start_leaves_the_search_to_the_caller():
    assert hw._probe_in_child([0], 1, 0.1,
                              cmd=[str(GAZE / "yok" / "olmayan.exe")]) is None


def test_a_child_that_dies_before_its_first_line_is_no_camera_fault(
        _data_dir):
    cmd = [sys.executable, "-c", "import sys; sys.exit(1)"]
    assert hw._probe_in_child([0], 1, 0.1, cmd=cmd) is None
    assert _crash_log(_data_dir) == ""


def test_a_child_that_never_answers_is_killed_in_time(monkeypatch):
    monkeypatch.setattr(hw, "CHILD_START_S", 0.0)
    monkeypatch.setattr(hw, "STOP_GRACE_S", 0.5)
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    t0 = time.monotonic()
    assert hw._probe_in_child([0], 0.5, 0.1, cmd=cmd) is None
    assert time.monotonic() - t0 < 10


def test_probe_child_reports_every_step_as_it_goes(monkeypatch):
    from tests import kamera_cocugu
    monkeypatch.setattr(hw, "default_backends", lambda: ("msmf", "dshow"))
    spec = _cam(backends=("msmf",))
    out = io.StringIO()
    request = {"indices": [0], "timeout_s": 30, "read_seconds": 0.05,
               "known": [[0, "msmf", 0, 0, "", None]],
               "skip": [[0, "msmf", 1920, 1200, ""]]}
    code = hw.probe_child([json.dumps(request)], out=out,
                          opener=lambda i, b: kamera_cocugu.Cam(spec, b))
    assert code == 0
    events = [json.loads(l) for l in out.getvalue().splitlines()]
    tries = [tuple(e["k"]) for e in events if e["ev"] == "try"]
    gots = [tuple(e["k"]) for e in events if e["ev"] == "got"]
    assert tries == gots
    # The known own mode is reused, the skipped size never asked.
    assert ("msmf", 0, 0, "") not in tries
    assert ("msmf", 1920, 1200, "") not in tries
    assert ("msmf", 1920, 1080, "") in tries
    done = [e for e in events if e["ev"] == "done"]
    assert [d["i"] for d in done] == [0]
    assert done[0]["out"]["backend"] == "msmf"
    assert events[-1] == {"ev": "end"}


def test_gozfaresi_starts_the_search_role_without_a_window():
    """The packaged exe runs the same entry point (gozfaresi.py)."""
    env = dict(os.environ)
    r = subprocess.run([sys.executable, str(GAZE / "gozfaresi.py"),
                        camera_proc.PROBE_ROLE, json.dumps({"indices": []})],
                       capture_output=True, timeout=120, env=env,
                       creationflags=camera_proc.creationflags())
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.decode().splitlines()[-1]) == {"ev": "end"}


def test_exit_codes_read_as_the_fault():
    assert camera_proc.exit_text(3221225477) == "0xC0000005"
    assert camera_proc.exit_text(-11) == "sinyal 11"
    assert camera_proc.exit_text(1) == "1"
    assert camera_proc.exit_text(None) == "?"


# --- the preview ------------------------------------------------------------

def _capture(scenario, **kw):
    return camera_proc.ChildCapture(0, 640, 480, 30, "msmf",
                                    cmd=_child(scenario,
                                               camera_proc.CAPTURE_ROLE),
                                    **kw)


def test_a_preview_capture_hands_out_the_childs_frames():
    cap = _capture({"cams": {"0": _cam(backends=("msmf",))}})
    try:
        assert cap.isOpened()
        for _ in range(3):
            ok, frame = cap.read()
            assert ok and frame.shape == (480, 640, 3)
    finally:
        cap.release()
    assert cap._proc.poll() is not None     # the child let go and ended


def test_a_camera_that_does_not_open_is_reported_closed():
    cap = _capture({"cams": {}})
    try:
        assert not cap.isOpened()
    finally:
        cap.release()


def test_a_driver_fault_in_the_preview_is_an_error_not_a_crash():
    cap = _capture({"cams": {"0": _cam(backends=("msmf",))}, "frames": 2})
    try:
        assert cap.isOpened()
        with pytest.raises(camera_proc.CameraLost) as err:
            for _ in range(10):
                cap.read()
        assert ("0xC0000005" in str(err.value)
                or "sinyal 11" in str(err.value))
    finally:
        cap.release()


def test_a_preview_stuck_in_its_open_can_be_killed():
    cap = _capture({"cams": {"0": _cam(backends=("msmf",))},
                    "hang_open": True})
    answer = []
    t = threading.Thread(target=lambda: answer.append(cap.isOpened()))
    t.start()
    time.sleep(1.0)
    assert t.is_alive()                     # waiting on the driver
    cap.kill()
    t.join(10)
    assert answer == [False]


# --- the launcher never opens a camera itself --------------------------------

def test_the_launcher_opens_cameras_only_through_a_child():
    for path in (GAZE / "eye_gaze" / "ui").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for call in ("open_capture(", "VideoCapture(", "_cv_open("):
            assert call not in text, "%s calls %s" % (path.name, call)


def test_the_wizard_preview_opens_a_child_capture(monkeypatch):
    from eye_gaze import ui
    started = []

    class Popen:
        def __init__(self, argv, **kw):
            started.append(argv)
            self.stdout = io.BytesIO(camera_proc.CLOSED + b"\n")

        def wait(self, timeout=None):
            return 0
    info = hw.CameraInfo(1, "dshow", 1280, 720, 30.0, "", "MJPG")
    real = camera_proc.ChildCapture.__init__

    def init(self, *a, **kw):
        kw["popen"] = Popen
        real(self, *a, **kw)
    monkeypatch.setattr(camera_proc.ChildCapture, "__init__", init)
    cap = ui.Services().open_camera(info)
    assert isinstance(cap, camera_proc.ChildCapture)
    assert not cap.isOpened()
    assert started[0][-2] == camera_proc.CAPTURE_ROLE
    assert json.loads(started[0][-1]) == {
        "index": 1, "width": 1280, "height": 720, "fps": hw.REQUEST_FPS,
        "backend": "dshow", "fourcc": "MJPG"}
