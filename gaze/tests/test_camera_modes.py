# gaze/tests/test_camera_modes.py
"""Cameras of every kind: MJPG, 16:9 or 4:3 only, drivers that stretch, the
mode an existing calibration was made in, the status window that never
stretches the picture, and the focal length the deep model assumes.

No real camera is opened: every capture here is a fake.
"""
import json
import math
import threading

import cv2
import numpy as np
import pytest

import gaze_driver as gd
from eye_gaze import camera_modes as cm
from eye_gaze import config as config_mod
from eye_gaze import hardware as hw


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("GOZFARESI_DATA_DIR", str(d))
    monkeypatch.delenv(hw.FAKE_CAMERA_ENV, raising=False)
    return d


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


MJPG_CODE = cv2.VideoWriter_fourcc(*"MJPG")


class ModeCam:
    """A camera described by what it DELIVERS.

    native: the (w, h) it runs at before any size is asked for.
    table: {(req_w, req_h): (w, h, fps)} without MJPG; a request not in the
           table keeps the current mode (what drivers do).
    mjpg:  the same with MJPG requested; None = the camera refuses MJPG
           (set() returns False and nothing changes). "garbage" = frames
           arrive as an undecoded byte buffer.
    """

    def __init__(self, native, table, clock, mjpg=None, native_fps=30.0,
                 fallback=None):
        self.native = (native[0], native[1], native_fps)
        # A request in no table: the mode `fallback` answers (callable
        # req -> (w, h, fps)), else the current mode stays.
        self.fallback = fallback
        self.table = dict(table)
        self.mjpg = mjpg
        self.clock = clock
        self.req = [0, 0]
        self.fourcc = None
        self.cur = self.native
        self.calls = []
        self.released = threading.Event()

    def isOpened(self):
        return True

    def set(self, prop, value):
        self.calls.append((prop, value))
        if prop == cv2.CAP_PROP_FOURCC:
            if self.mjpg is None:
                return False
            self.fourcc = "MJPG"
            return True
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            self.req[0] = int(value)
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
            self.req[1] = int(value)
            req = tuple(self.req)
            src = self.table
            if self.fourcc == "MJPG" and isinstance(self.mjpg, dict):
                src = self.mjpg
            if req in src:
                self.cur = src[req]
            elif self.fallback is not None:
                self.cur = self.fallback(req)
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_FOURCC:
            return float(MJPG_CODE) if self.fourcc else 0.0
        return 0.0

    def read(self):
        w, h, fps = self.cur
        self.clock.t += 1.0 / fps
        if self.fourcc == "MJPG" and self.mjpg == "garbage":
            return True, np.zeros((1, w * h // 10), np.uint8)
        return True, np.zeros((h, w, 3), np.uint8)

    def release(self):
        self.released.set()


def _probe(cam_factory, clock, **kw):
    kw.setdefault("profile", {})
    kw.setdefault("cost", lambda frame: 1.0)
    return hw.probe_cameras(max_index=1, timeout_s=10,
                            opener=lambda i, b: cam_factory(),
                            read_seconds=0.5, clock=clock, **kw)


def _same(sizes):
    return {(w, h): (w, h, 30.0) for w, h in sizes}


# --- MJPG -------------------------------------------------------------------

def test_mjpg_is_kept_where_it_delivers_more_frames():
    """DirectShow / V4L2 with YUY2: 1280x720 at 10 fps uncompressed, 30 with
    MJPG. The mode is chosen at 30 fps WITH MJPG and recorded so."""
    clock = Clock()
    plain = {(1280, 720): (1280, 720, 10.0), (640, 480): (640, 480, 30.0)}
    mjpg = {(1280, 720): (1280, 720, 30.0), (640, 480): (640, 480, 30.0)}
    found = _probe(lambda: ModeCam((640, 480), plain, clock, mjpg=mjpg),
                   clock)
    cam = found[0]
    assert (cam.width, cam.height, cam.fourcc) == (1280, 720, "MJPG")
    assert cam.fps_measured == pytest.approx(30.0, abs=0.5)
    rec = hw.camera_record(cam)
    assert rec["fourcc"] == "MJPG"
    # Every measurement is kept, with and without MJPG.
    got = {(m["width"], m["height"], m["fourcc"]): m["fps"]
           for m in rec["modes"]}
    assert got[(1280, 720, "")] == pytest.approx(10.0, abs=0.5)
    assert got[(1280, 720, "MJPG")] == pytest.approx(30.0, abs=0.5)
    assert all(m["backend"] == cam.backend for m in rec["modes"])
    json.dumps(rec)                     # makine.json can hold it


def test_mjpg_is_asked_for_before_the_size_and_only_above_640x480():
    clock = Clock()
    plain = {(1280, 720): (1280, 720, 10.0)}
    made = []

    def factory():
        c = ModeCam((640, 480), plain, clock, mjpg=dict(plain))
        made.append(c)
        return c
    _probe(factory, clock)
    calls = made[0].calls
    for i, (prop, value) in enumerate(calls):
        if prop == cv2.CAP_PROP_FOURCC:
            assert calls[i + 1][0] == cv2.CAP_PROP_FRAME_WIDTH
            w = calls[i + 1][1]
            h = calls[i + 2][1]
            assert w * h > 640 * 480
    assert any(p == cv2.CAP_PROP_FOURCC for p, _ in calls)


def test_a_camera_that_refuses_mjpg_keeps_its_own_format():
    clock = Clock()
    plain = _same([(1280, 720), (640, 480)])
    plain[(1280, 720)] = (1280, 720, 15.0)
    found = _probe(lambda: ModeCam((640, 480), plain, clock, mjpg=None),
                   clock)
    cam = found[0]
    assert cam.fourcc == ""
    assert (cam.width, cam.height) == (640, 480)    # 1280x720 is 15 fps


def test_mjpg_frames_that_arrive_undecoded_are_not_a_mode():
    clock = Clock()
    plain = {(1280, 720): (1280, 720, 15.0), (640, 480): (640, 480, 30.0)}
    found = _probe(lambda: ModeCam((640, 480), plain, clock,
                                   mjpg="garbage"), clock)
    cam = found[0]
    assert (cam.width, cam.height, cam.fourcc) == (640, 480, "")
    assert all(m["fourcc"] == "" for m in cam.modes)


def test_a_mode_at_full_rate_is_not_measured_again_with_mjpg():
    """Media Foundation usually picks a compressed format by itself: MJPG
    cannot deliver more than the 30 fps asked for, so it is not tried."""
    clock = Clock()
    made = []

    def factory():
        c = ModeCam((640, 480), _same([(1920, 1080), (1280, 720)]), clock,
                    mjpg=_same([(1920, 1080), (1280, 720)]))
        made.append(c)
        return c
    found = _probe(factory, clock)
    assert found[0].fourcc == ""
    calls = made[0].calls
    with_mjpg = {(calls[i + 1][1], calls[i + 2][1])
                 for i, (p, _) in enumerate(calls)
                 if p == cv2.CAP_PROP_FOURCC}
    # Delivered as asked at 30 fps: not again. Sizes the camera's own
    # format does not offer (it answered with another one) are still
    # tried with MJPG - some cameras offer their large modes only so.
    assert (1920, 1080) not in with_mjpg and (1280, 720) not in with_mjpg
    assert (1280, 960) in with_mjpg


# --- aspect -----------------------------------------------------------------

def test_a_stretched_mode_is_rejected():
    """Asked for 1280x960 the driver hands out 1280x800 - neither what was
    asked nor the camera's own 4:3 shape: a squeezed picture."""
    clock = Clock()
    table = {(1280, 960): (1280, 800, 30.0), (640, 480): (640, 480, 30.0)}
    found = _probe(lambda: ModeCam((640, 480), table, clock,
                                   fallback=lambda req: (640, 480, 30.0)),
                   clock)
    cam = found[0]
    assert (cam.width, cam.height) == (640, 480)
    bad = [m for m in cam.modes if (m["width"], m["height"]) == (1280, 800)]
    assert bad and not any(m["ok"] for m in bad)
    assert bad[0]["request"] == [1280, 960]


def test_a_16_9_only_camera_runs_at_1080p():
    clock = Clock()
    table = _same([(1920, 1080), (1280, 720), (960, 540)])
    found = _probe(lambda: ModeCam((1280, 720), table, clock), clock)
    cam = found[0]
    assert (cam.width, cam.height) == (1920, 1080)
    # Its own shape is its largest honest mode, not the mode it opened in.
    assert cam.native == (1920, 1080)
    # The 4:3 requests fell back to a 16:9 mode: the camera's own shape,
    # so not "stretched".
    assert all(m["ok"] for m in cam.modes)


def test_a_4_3_only_camera_runs_at_1280x960():
    clock = Clock()
    table = _same([(1280, 960), (640, 480)])
    found = _probe(lambda: ModeCam((640, 480), table, clock), clock)
    cam = found[0]
    assert (cam.width, cam.height) == (1280, 960)


def test_a_4k_camera_is_capped_at_1920_wide():
    clock = Clock()
    table = _same([(1920, 1080), (1280, 720)])
    found = _probe(lambda: ModeCam((3840, 2160), table, clock), clock)
    assert (found[0].width, found[0].height) == (1920, 1080)


def test_the_probe_logs_every_mode(capsys):
    clock = Clock()
    table = {(1280, 960): (1280, 800, 30.0), (1280, 720): (1280, 720, 30.0)}
    _probe(lambda: ModeCam((640, 480), table, clock), clock)
    out = capsys.readouterr().out
    assert "1280x720" in out and "1280x800" in out and "640x480" in out
    assert out.isascii()


# --- choice: fps first, then size, within the per-frame budget ---------------

def _m(w, h, fps, fourcc="", cost=1.0, ok=True):
    return {"width": w, "height": h, "fps": fps, "fourcc": fourcc,
            "backend": "v4l2", "request": [w, h], "ok": ok, "cost_ms": cost}


def test_choice_takes_the_largest_mode_at_full_rate():
    modes = [_m(1920, 1080, 15.0), _m(1280, 720, 30.0), _m(640, 480, 30.0)]
    assert hw.choose_mode(modes, {})["width"] == 1280


def test_choice_on_a_weak_machine_stays_at_1280_wide():
    modes = [_m(1920, 1080, 30.0), _m(1280, 720, 30.0)]
    weak = {"machine": {"cpu_cores": 2, "ram_gb": 4.0}}
    assert hw.choose_mode(modes, weak)["width"] == 1280
    strong = {"machine": {"cpu_cores": 8, "ram_gb": 16.0}}
    assert hw.choose_mode(modes, strong)["width"] == 1920


def test_choice_keeps_the_frame_within_the_measured_gaze_time():
    """gaze_ms (benchmark_gaze) + what a frame of that size costs must fit
    in one frame at MIN_FPS."""
    budget = 1000.0 / hw.MIN_FPS
    modes = [_m(1920, 1080, 30.0, cost=9.0), _m(1280, 720, 30.0, cost=4.0)]
    slow = {"gaze_ms": budget - 6.0}
    assert hw.choose_mode(modes, slow)["width"] == 1280
    fast = {"gaze_ms": 10.0}
    assert hw.choose_mode(modes, fast)["width"] == 1920


def test_choice_without_a_full_rate_mode_takes_the_fastest():
    modes = [_m(1280, 960, 10.0), _m(1280, 720, 15.0), _m(640, 480, 20.0)]
    assert hw.choose_mode(modes, {})["width"] == 640


def test_choice_prefers_the_plain_format_at_equal_rate():
    modes = [_m(1280, 720, 30.0, "MJPG"), _m(1280, 720, 30.0, "")]
    assert hw.choose_mode(modes, {})["fourcc"] == ""


def test_choice_skips_rejected_modes():
    """A 4:3 camera that answers 1280x960 with a 1280x800 picture: its own
    shape is 4:3 (its largest mode delivered as asked), so 1280x800 is a
    squeezed picture and never chosen."""
    native = dict(_m(640, 480, 30.0), request=None)
    squeezed = dict(_m(1280, 800, 30.0), request=[1280, 960])
    modes = [native, squeezed, _m(640, 480, 30.0)]
    assert hw.judge_modes(modes) == (640, 480)
    assert squeezed["ok"] is False
    assert hw.choose_mode(modes, {})["width"] == 640


def test_a_16_10_camera_on_a_640x480_default_backend_runs_at_1920x1200():
    """DirectShow / V4L2 open it at 640x480: judged by that, every 16:10
    mode looked stretched and the camera fell to 640x480."""
    clock = Clock()
    table = {(1920, 1200): (1920, 1200, 30.0), (1280, 800): (1280, 800, 30.0),
             (640, 480): (640, 480, 30.0)}

    def nearest(req):
        return (1920, 1200, 30.0) if req[0] >= 1920 else (1280, 800, 30.0)
    found = _probe(lambda: ModeCam((640, 480), table, clock,
                                   fallback=nearest), clock)
    cam = found[0]
    assert (cam.width, cam.height) == (1920, 1200)
    assert cam.native == (1920, 1200)
    # 1280x720 answered with 1280x800: the camera's own shape, not a stretch.
    alt = [m for m in cam.modes if m["request"] == [1280, 720]]
    assert alt and alt[0]["ok"]


def test_a_16_9_camera_on_a_640x480_default_backend_runs_at_1080p():
    clock = Clock()
    table = _same([(1920, 1080), (1280, 720), (640, 480)])
    found = _probe(lambda: ModeCam((640, 480), table, clock,
                                   fallback=lambda req: (1280, 720, 30.0)),
                   clock)
    cam = found[0]
    assert (cam.width, cam.height) == (1920, 1080)
    assert cam.native == (1920, 1080)


def test_choice_prefers_the_cameras_own_aspect():
    modes = [_m(1280, 960, 30.0), _m(1280, 720, 30.0), _m(1920, 1080, 15.0)]
    weak = {"machine": {"cpu_cores": 2}}
    # Largest accepted mode 1920x1080 is 16:9: of the two 1280-wide modes
    # the 16:9 one, though 1280x960 has more pixels.
    assert (hw.choose_mode(modes, weak)["height"]) == 720


def test_mjpg_needs_ten_percent_more_frames_to_win():
    near = [_m(1280, 720, 26.0, ""), _m(1280, 720, 28.0, "MJPG")]
    assert hw.choose_mode(near, {})["fourcc"] == ""
    far = [_m(1280, 720, 26.0, ""), _m(1280, 720, 30.0, "MJPG")]
    assert hw.choose_mode(far, {})["fourcc"] == "MJPG"


def test_legacy_mode_is_what_1_2_0_would_have_picked():
    """Plain-format measurements of the old three requests, 1280 wide at
    most: the mode a calibration without a camera stamp was made in."""
    modes = [_m(1920, 1080, 30.0), _m(1280, 960, 15.0),
             _m(1280, 960, 30.0, "MJPG"), _m(1280, 720, 30.0),
             _m(640, 480, 30.0)]
    assert hw.legacy_mode(modes) == (1280, 720)
    assert hw.legacy_mode([]) is None


# --- an existing calibration keeps its camera mode ----------------------------

def _record(w, h, modes=None, legacy=None, fourcc=""):
    rec = {"index": 0, "backend": "msmf", "width": w, "height": h,
           "fps": 30, "fps_measured": 30.0, "name": "", "fourcc": fourcc}
    if modes is not None:
        rec["modes"] = modes
    if legacy is not None:
        rec["legacy"] = list(legacy)
    return rec


NEW = [_m(1920, 1080, 30.0, "MJPG"), _m(1280, 960, 15.0),
       _m(1280, 960, 30.0, "MJPG"), _m(1280, 720, 30.0), _m(640, 480, 30.0)]


def test_a_calibration_keeps_the_mode_it_was_made_in():
    rec = _record(1920, 1080, NEW, legacy=(1280, 720), fourcc="MJPG")
    kept = hw.kept_mode(rec, True, (1280, 960))
    assert (kept["width"], kept["height"], kept["fourcc"]) == \
        (1280, 960, "MJPG")


def test_no_calibration_takes_the_new_choice():
    rec = _record(1920, 1080, NEW, legacy=(1280, 720))
    assert hw.kept_mode(rec, False, None) is None


def test_a_mode_the_camera_no_longer_delivers_is_not_forced():
    rec = _record(1920, 1080, NEW)
    assert hw.kept_mode(rec, True, (800, 600)) is None


def test_a_1_2_0_record_is_opened_as_it_always_was():
    """makine.json from 1.2.0: no measured modes - its own mode is the one
    that install has been calibrated and running in."""
    rec = _record(1280, 960)
    assert hw.kept_mode(rec, True, (1280, 720)) is None
    assert hw.kept_mode(rec, True, None) is None


def test_a_calibration_without_a_stamp_keeps_the_old_rule():
    rec = _record(1920, 1080, NEW, legacy=(1280, 720), fourcc="MJPG")
    kept = hw.kept_mode(rec, True, None)
    assert (kept["width"], kept["height"]) == (1280, 720)


def test_calibration_stamp_reads_the_saved_camera_size(tmp_path,
                                                      monkeypatch):
    class M:
        camera = (1280, 960)
    p = tmp_path / "calibration.json"
    monkeypatch.setattr(gd, "_read_calibration",
                        lambda path: (M(), None, {}) if path == str(p)
                        else (None, "dosya yok", None))
    assert gd._calibration_stamp(str(p), True) == (True, (1280, 960))
    assert gd._calibration_stamp(str(tmp_path / "none.json"), True) == \
        (False, None)
    assert gd._calibration_stamp(str(p), False) == (False, None)


AUTO = {"camera_index": "auto", "camera_width": "auto",
        "camera_height": "auto", "camera_fps": "auto"}


def test_driver_opens_the_calibrated_mode_after_a_new_probe():
    """Settings > "Kamera ara" (or the wizard) wrote the new choice; the
    calibration in use was taken at 1280x960: that mode is opened, and the
    new choice is kept for the next calibration."""
    rec = _record(1920, 1080, NEW, legacy=(1280, 720), fourcc="MJPG")
    hw.save_profile({"camera": rec})
    cfg, _, cam = gd._resolve_machine(dict(AUTO), say=lambda m: None,
                                      probe=lambda **k: pytest.fail("p"))
    lines = []
    cfg2, cam2 = gd._keep_calibration_mode(cfg, True, cam,
                                           (True, (1280, 960)),
                                           say=lines.append)
    assert (cfg2["camera_width"], cfg2["camera_height"]) == (1280, 960)
    assert (cam2["width"], cam2["height"], cam2["fourcc"]) == \
        (1280, 960, "MJPG")
    assert cam2["choice"] == {"width": 1920, "height": 1080,
                              "fourcc": "MJPG"}
    assert lines and all(m.isascii() for m in lines)
    # The profile itself still holds the new choice.
    assert hw.load_profile()["camera"]["width"] == 1920


def test_driver_keeps_explicit_camera_settings():
    rec = _record(1920, 1080, NEW)
    cfg = {"camera_index": 0, "camera_width": 640, "camera_height": 480}
    out, cam = gd._keep_calibration_mode(cfg, False, rec,
                                         (True, (1280, 960)),
                                         say=lambda m: None)
    assert out == cfg and cam == dict(rec, fourcc="")


def test_driver_without_calibration_uses_the_new_choice():
    rec = _record(1920, 1080, NEW, fourcc="MJPG")
    cfg = dict(AUTO, camera_width=1920, camera_height=1080)
    out, cam = gd._keep_calibration_mode(cfg, True, rec, (False, None),
                                         say=lambda m: None)
    assert (out["camera_width"], cam.get("choice")) == (1920, None)


def test_the_probed_opener_asks_for_the_recorded_format(monkeypatch):
    seen = []

    def fake_open(index, w, h, fps, backend=None, opener=None, fourcc=None):
        seen.append((index, w, h, fps, backend, fourcc))
        return None
    monkeypatch.setattr(hw, "open_capture", fake_open)
    gd._camera_opener(0, 1280, 720, 30, _record(1280, 720, fourcc="MJPG"))()
    gd._camera_opener(0, 640, 480, 30, {"index": 0, "backend": "dshow"})()
    assert seen == [(0, 1280, 720, 30, "msmf", "MJPG"),
                    (0, 640, 480, 30, "dshow", None)]


def test_open_capture_sets_mjpg_before_the_size():
    calls = []

    class Cap:
        def isOpened(self):
            return True

        def set(self, prop, value):
            calls.append(prop)
            return True
    hw.open_capture(0, 1280, 720, 30, backend="dshow",
                    opener=lambda i, b: Cap(), fourcc="MJPG")
    assert calls == [cv2.CAP_PROP_FOURCC, cv2.CAP_PROP_FRAME_WIDTH,
                     cv2.CAP_PROP_FRAME_HEIGHT, cv2.CAP_PROP_FPS]
    calls.clear()
    hw.open_capture(0, 1280, 720, 30, backend="dshow",
                    opener=lambda i, b: Cap())
    assert cv2.CAP_PROP_FOURCC not in calls


# --- switching mode for a recalibration --------------------------------------

class SwitchCap:
    """_ResilientCapture's reopen()/frame_size, recorded."""

    def __init__(self, fail=()):
        self.opened = []
        self.fail = set(fail)
        self.frame_size = None

    def reopen(self, opener):
        mode = opener()
        if mode in self.fail:
            return False
        self.opened.append(mode)
        return True


def _switch(cap, rec, say=None):
    return cm.ModeSwitch(cap, rec, lambda w, h, f: (lambda: (w, h, f)),
                         say=say or (lambda m: None))


def test_a_recalibration_adopts_the_new_choice_and_a_cancel_goes_back():
    cap = SwitchCap()
    rec = dict(_record(1280, 960, NEW), fourcc="MJPG",
               choice={"width": 1920, "height": 1080, "fourcc": "MJPG"})
    sw = _switch(cap, rec)
    prev = sw.for_calibration()
    assert cap.opened == [(1920, 1080, "MJPG")]
    assert (rec["width"], rec["height"]) == (1920, 1080)
    sw.back(prev)
    assert cap.opened[-1] == (1280, 960, "MJPG")
    assert (rec["width"], rec["height"]) == (1280, 960)


def test_nothing_switches_without_a_pending_choice():
    cap = SwitchCap()
    sw = _switch(cap, _record(1280, 720, NEW))
    assert sw.for_calibration() is None and cap.opened == []
    sw.back(None)
    assert cap.opened == []
    assert cm.ModeSwitch(cap, None, None).for_calibration() is None


def test_a_new_mode_that_does_not_open_keeps_the_old_one():
    cap = SwitchCap(fail={(1920, 1080, "MJPG")})
    rec = dict(_record(1280, 960, NEW), fourcc="MJPG",
               choice={"width": 1920, "height": 1080, "fourcc": "MJPG"})
    lines = []
    sw = _switch(cap, rec, lines.append)
    assert sw.for_calibration() is None
    assert cap.opened == [(1280, 960, "MJPG")]
    assert (rec["width"], rec["height"]) == (1280, 960)
    assert lines and all(m.isascii() for m in lines)


def test_an_undone_calibration_brings_its_mode_back():
    cap = SwitchCap()
    rec = dict(_record(1920, 1080, NEW), fourcc="MJPG")
    sw = _switch(cap, rec)
    sw.follow((1280, 960))
    assert cap.opened == [(1280, 960, "MJPG")]
    sw.follow((1280, 960))                      # already there
    sw.follow((800, 600))                       # not a measured mode
    sw.follow(None)
    assert len(cap.opened) == 1


class FlakyOpen:
    def __init__(self, frames):
        self.frames = frames

    def isOpened(self):
        return True

    def read(self):
        return True, self.frames

    def release(self):
        pass

    def get(self, prop):
        return 0.0


def test_resilient_capture_knows_the_real_frame_size_and_reopens():
    cap = gd._ResilientCapture(lambda: FlakyOpen(np.zeros((720, 1280, 3),
                                                          np.uint8)))
    assert cap.frame_size is None
    cap.read()
    assert cap.frame_size == (1280, 720)
    assert gd._camera_size(cap) == (1280, 720)
    assert cap.reopen(lambda: FlakyOpen(np.zeros((480, 640, 3), np.uint8)))
    assert cap.frame_size is None
    cap.read()
    assert cap.frame_size == (640, 480)


# --- the status window keeps the picture's shape -----------------------------

def test_window_size_keeps_the_aspect():
    for fw, fh in ((1920, 1080), (1280, 960), (640, 480), (1280, 720)):
        w, h = cm.window_size(fw, fh)
        assert w <= cm.WINDOW_MAX[0] and h <= cm.WINDOW_MAX[1]
        assert abs(w / h - fw / fh) < 0.01
    assert cm.window_size(640, 480) == (640, 480)


def test_fit_to_window_letterboxes_instead_of_stretching():
    frame = np.full((720, 1280, 3), 255, np.uint8)
    out = cm.fit_to_window(frame, 600, 600)
    assert out.shape == (600, 600, 3)
    rows = np.where(out[:, 300, 0] > 0)[0]
    content_h = rows.max() - rows.min() + 1
    assert abs(600 / content_h - 1280 / 720) < 0.02
    assert out[0, 300, 0] == 0                  # black band, not picture
    # No window size known: the frame as it is.
    assert cm.fit_to_window(frame, 0, 0) is frame


def test_status_window_keeps_its_ratio():
    import inspect
    src = inspect.getsource(gd.main)
    assert "WINDOW_KEEPRATIO" in src
    assert "fit_to_window(" in src


# --- focal length ------------------------------------------------------------

def test_auto_focal_is_the_image_width_exactly():
    assert cm.focal_px(1280, None) == 1280
    assert cm.focal_px(1920, None) == 1920
    f = cm.focal_px(1280, 90.0)
    assert f == pytest.approx(640.0)
    assert cm.focal_px(1280, 2 * math.degrees(math.atan(0.5))) == \
        pytest.approx(1280.0)


def test_hfov_setting_accepts_auto_and_degrees():
    assert cm.hfov_setting("auto") is None
    assert cm.hfov_setting(None) is None
    assert cm.hfov_setting(78) == 78.0
    lines = []
    assert cm.hfov_setting(400, say=lines.append) is None
    assert cm.hfov_setting("genis", say=lines.append) is None
    assert len(lines) == 2 and all(m.isascii() for m in lines)


def test_a_numeric_fov_is_part_of_the_feature_stamp():
    base = gd._feature_id(True, True, True, True)
    assert base == "pe1-hp1-ht1-dg1"
    assert gd._feature_id(True, True, True, True, hfov=None) == base
    assert gd._feature_id(True, True, True, True, hfov=78.0) == \
        "pe1-hp1-ht1-dg1-fv78"
    # Without the deep model the focal length is not used at all.
    assert gd._feature_id(True, True, True, False, hfov=78.0) == \
        "pe1-hp1-ht1-dg0"
    from eye_gaze import modelsel
    assert modelsel.parse_stamp("pe1-hp1-ht1-dg1-fv78") == \
        (True, True, True, True)
    diff = gd._feature_diff("pe1-hp1-ht1-dg1", "pe1-hp1-ht1-dg1-fv78")
    assert diff and "camera_hfov_deg" in diff[0]


def test_onnx_camera_matrix_follows_the_frame_and_the_fov():
    from eye_gaze import deep_onnx
    m = deep_onnx.dummy_camera_matrix(1280, 720)
    assert m[0, 0] == 1280 and m[1, 1] == 1280 and m[0, 2] == 640
    m2 = deep_onnx.dummy_camera_matrix(1280, 720, focal=640.0)
    assert m2[0, 0] == 640.0 and m2[1, 2] == 360
    g = deep_onnx.OnnxDeepGaze.__new__(deep_onnx.OnnxDeepGaze)
    g.hfov_deg = None
    g._camera_size = None
    assert g._camera_for(1920, 1080)[0, 0] == 1920
    assert g._camera_for(640, 480)[0, 0] == 640
    g.hfov_deg = 90.0
    g._camera_size = None
    assert g._camera_for(1280, 720)[0, 0] == pytest.approx(640.0)


def test_config_knows_camera_hfov_deg(tmp_path):
    assert "camera_hfov_deg" in config_mod.KNOWN_KEYS
    p = tmp_path / "config.yaml"
    p.write_text('camera_hfov_deg: "auto"\nleft_click_key: "space"\n',
                 encoding="utf-8")
    assert config_mod.load_config(str(p))["camera_hfov_deg"] == "auto"
    p.write_text('camera_hfov_deg: "78,5"\nleft_click_key: "space"\n',
                 encoding="utf-8")
    assert config_mod.load_config(str(p))["camera_hfov_deg"] == 78.5
    from pathlib import Path
    shipped = (Path(gd.__file__).parent / "config.default.yaml").read_text(
        encoding="utf-8")
    assert 'camera_hfov_deg: "auto"' in shipped


# --- the smoke camera ---------------------------------------------------------

def test_the_fake_camera_probe_has_its_one_mode(monkeypatch):
    from pathlib import Path
    face = Path(gd.__file__).parent / "resources" / "faces" / "benchmark.jpg"
    monkeypatch.setenv(hw.FAKE_CAMERA_ENV, str(face))
    found = hw.probe_cameras()
    cam = found[0]
    assert cam.backend == "fake" and len(cam.modes) == 1
    assert cam.modes[0]["width"] == cam.width
    rec = hw.camera_record(cam)
    assert hw.kept_mode(rec, True, (cam.width, cam.height)) is None


def test_torch_camera_follows_the_frame_in_place():
    """ptgaze's Camera is frozen and shared with its head-pose normaliser:
    the matrix itself is rewritten."""
    from eye_gaze import deep

    class Cam:
        camera_matrix = None
    g = deep.DeepGaze.__new__(deep.DeepGaze)
    cam = Cam()
    cam.camera_matrix = np.array([[1280, 0, 640], [0, 1280, 480], [0, 0, 1]],
                                 dtype=float)
    shared = cam.camera_matrix
    g._est = type("E", (), {"camera": cam})()
    g.hfov_deg = None
    g._camera_size = (1280, 960)
    g._fit_camera(1280, 960)
    assert shared[0, 0] == 1280                 # same size: untouched
    g._fit_camera(1920, 1080)
    assert shared is g._est.camera.camera_matrix
    assert (shared[0, 0], shared[0, 2], shared[1, 2]) == (1920, 960, 540)
    g.hfov_deg = 90.0
    g._camera_size = None
    g._fit_camera(1920, 1080)
    assert shared[0, 0] == pytest.approx(960.0)


def test_make_deep_gaze_hands_the_fov_only_when_set(monkeypatch):
    from eye_gaze import deep, deep_onnx
    built = []

    class Onnx:
        backend = "onnx"
        device = "cpu"

        def __init__(self, w, h, providers, **kw):
            built.append(kw)
    monkeypatch.setattr(deep, "select_backend", lambda b="auto": ("onnx",
                                                                  False))
    monkeypatch.setattr(deep_onnx, "OnnxDeepGaze", Onnx)
    deep.make_deep_gaze(1280, 720, "cpu")
    deep.make_deep_gaze(1280, 720, "cpu", hfov_deg=78)
    assert built == [{}, {"hfov_deg": 78.0}]


def test_the_startup_report_warns_about_another_shape():
    class Cap:
        def get(self, prop):
            return 30.0 if prop == cv2.CAP_PROP_FPS else 0.0

        def backend_name(self):
            return "DSHOW"
    lines = gd._camera_report(Cap(), 1280, 720, 30, (1280, 720))
    assert len(lines) == 1 and "DSHOW" in lines[0]
    assert "istenen 1280x720" in lines[0]
    lines = gd._camera_report(Cap(), 1280, 720, 30, (640, 480))
    assert len(lines) == 2 and "UYARI" in lines[1]
    assert all(m.isascii() for m in lines)


def test_main_switches_the_mode_around_a_recalibration():
    import inspect
    src = inspect.getsource(gd.main)
    assert "_keep_calibration_mode(" in src
    assert src.index("_keep_calibration_mode(") < src.index("_camera_request(cfg)")
    recal = src[src.index("hotkeys.take_recal()"):]
    assert recal.index("_switch_for_calibration(") < \
        recal.index("run_calibration()") < recal.index("modes.back(")


# --- fix round 1 ------------------------------------------------------------

def test_a_stamp_this_probe_did_not_measure_falls_back_to_the_old_rule():
    """A 1.2.0 stamp from a driver echoing the request, or a probe cut short:
    not the new choice for a calibrated user, but the mode 1.2.0 ran."""
    rec = _record(1920, 1080, NEW, legacy=(1280, 720), fourcc="MJPG")
    kept = hw.kept_mode(rec, True, (800, 600))
    assert (kept["width"], kept["height"], kept["fourcc"]) == (1280, 720, "")


def test_the_kept_size_prefers_the_own_format_at_full_rate():
    modes = [_m(1920, 1080, 30.0, "MJPG"), _m(1280, 960, 26.0),
             _m(1280, 960, 30.0, "MJPG")]
    rec = _record(1920, 1080, modes, fourcc="MJPG")
    kept = hw.kept_mode(rec, True, (1280, 960))
    assert (kept["width"], kept["fourcc"]) == (1280, "")
    # Below MIN_FPS in its own format: MJPG keeps the calibrated size usable.
    slow = [_m(1920, 1080, 30.0, "MJPG"), _m(1280, 960, 15.0),
            _m(1280, 960, 30.0, "MJPG")]
    kept = hw.kept_mode(_record(1920, 1080, slow, fourcc="MJPG"), True,
                        (1280, 960))
    assert kept["fourcc"] == "MJPG"


def test_the_record_size_in_mjpg_goes_plain_for_a_calibration():
    modes = [_m(1280, 720, 26.0), _m(1280, 720, 30.0, "MJPG")]
    rec = _record(1280, 720, modes, fourcc="MJPG")
    kept = hw.kept_mode(rec, True, (1280, 720))
    assert (kept["width"], kept["fourcc"]) == (1280, "")
    assert hw.kept_mode(dict(rec, fourcc=""), True, (1280, 720)) is None


def test_explicit_sizes_get_no_measured_format_and_never_switch(monkeypatch):
    rec = dict(_record(1920, 1080, NEW, fourcc="MJPG"))
    cfg = {"camera_index": "auto", "camera_width": 1280,
           "camera_height": 960}
    out, cam = gd._keep_calibration_mode(cfg, False, rec,
                                         (True, (1280, 960)),
                                         say=lambda m: None)
    assert out == cfg and cam["fourcc"] == "" and "choice" not in cam
    seen = []

    def fake_open(index, w, h, fps, backend=None, opener=None, fourcc=None):
        seen.append((w, h, fourcc))
    monkeypatch.setattr(hw, "open_capture", fake_open)
    gd._camera_opener(0, 1280, 960, 30, cam)()
    assert seen == [(1280, 960, None)]
    # main hands ModeSwitch no record for explicit sizes: follow() after a
    # load or an undo can then never reopen at the calibration's size.
    cap = SwitchCap()
    sw = cm.ModeSwitch(cap, None, lambda w, h, f: (lambda: (w, h, f)))
    assert sw.follow((1920, 1080)) is False and cap.opened == []
    import inspect
    src = inspect.getsource(gd.main)
    assert "machine_cam if mode_auto else None" in src


def test_mjpg_that_brings_no_frame_is_reopened_in_the_own_format():
    cap = SwitchCap()
    rec = dict(_record(1920, 1080, NEW), fourcc="MJPG",
               choice={"width": 1920, "height": 1080, "fourcc": "MJPG"})
    lines = []
    sw = _switch(cap, rec, lines.append)
    assert sw.ensure_frames()
    assert cap.opened == [(1920, 1080, "")]
    assert rec["fourcc"] == "" and rec["choice"]["fourcc"] == ""
    assert lines and all(m.isascii() for m in lines)
    assert not sw.ensure_frames()               # own format now
    cap.frame_size = (1920, 1080)
    rec["fourcc"] = "MJPG"
    assert not sw.ensure_frames()               # frames arrive: keep MJPG


def test_main_settles_every_switch_and_goes_back_even_when_calibration_raises():
    import inspect
    src = inspect.getsource(gd.main)
    assert src.index("modes.ensure_frames()") < src.index("_camera_report(")
    recal = src[src.index("hotkeys.take_recal()"):]
    fin = recal.index("finally:")
    assert recal.index("_switch_for_calibration(modes, cap)") < \
        recal.index("run_calibration()") < fin < recal.index("modes.back(")
    assert recal.index("modes.back(") < recal.index("keys.clear()")


def test_frame_cost_counts_a_decode_above_640x480(monkeypatch):
    calls = []
    real = cv2.imdecode

    def counting(buf, flags):
        calls.append(1)
        return real(buf, flags)
    monkeypatch.setattr(cv2, "imdecode", counting)
    hw.frame_cost_ms(np.zeros((480, 640, 3), np.uint8))
    assert calls == []
    hw.frame_cost_ms(np.zeros((1080, 1920, 3), np.uint8))
    assert len(calls) == 3


def test_the_wizard_chooses_again_once_gaze_time_is_known():
    rec = _record(1920, 1080, [_m(1920, 1080, 30.0, cost=9.0),
                               _m(1280, 720, 30.0, cost=4.0)])
    slow = {"gaze_ms": 1000.0 / hw.MIN_FPS - 6.0}
    out = hw.rechoose(rec, slow)
    assert (out["width"], out["height"]) == (1280, 720)
    assert rec["width"] == 1920                 # a copy
    assert hw.rechoose(_record(1280, 960), slow)["width"] == 1280
    import inspect
    from eye_gaze.ui import wizard
    assert "hardware.rechoose(" in inspect.getsource(wizard.Wizard._write)


def test_a_window_of_the_pictures_shape_is_shown_as_is():
    frame = np.zeros((1080, 1920, 3), np.uint8)
    assert cm.fit_to_window(frame, 800, 450) is frame
