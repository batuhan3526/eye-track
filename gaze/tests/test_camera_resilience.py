# gaze/tests/test_camera_resilience.py
"""A camera that drops out (sleep/resume, USB hiccup, a video call taking it)
used to end the driver after ~5 s. Nobody was there to restart it: the user
was left without a cursor and without a way to ask for help. Now the capture
reconnects on its own and the loop keeps running."""
import gaze_driver as gd


class FakeCap:
    def __init__(self, frames=None, opened=True):
        self.frames = list(frames or [])
        self.opened = opened
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        if self.frames:
            return True, self.frames.pop(0)
        return False, None

    def get(self, prop):
        return 640.0

    def release(self):
        self.released = True


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_frames_pass_through():
    cap = FakeCap(["f1", "f2"])
    rc = gd._ResilientCapture(lambda: cap, clock=Clock())
    assert rc.read() == (True, "f1")
    assert rc.read() == (True, "f2")


def test_reconnects_after_the_stream_dies():
    dead = FakeCap([])
    alive = FakeCap(["yeni"])
    caps = [dead, alive]
    clock = Clock()
    rc = gd._ResilientCapture(lambda: caps.pop(0), clock=clock,
                              fail_limit=5, retry_every=2.0)
    for _ in range(10):
        assert rc.read()[0] is False
    clock.t = 3.0
    ok, frame = rc.read()
    assert dead.released
    assert (ok, frame) == (True, "yeni")


def test_keeps_retrying_while_no_camera_opens():
    clock = Clock()
    attempts = []

    def opener():
        attempts.append(clock.t)
        return FakeCap(opened=False)
    rc = gd._ResilientCapture(opener, clock=clock, fail_limit=1, retry_every=2.0)
    for i in range(50):                 # 10 s of polling at 5 Hz
        clock.t = i * 0.2
        assert rc.read()[0] is False
    # not hammering the driver every frame, but not giving up either
    assert 4 <= len(attempts) <= 7


def test_an_opener_that_raises_is_survived():
    clock = Clock()
    state = {"n": 0}

    def opener():
        state["n"] += 1
        if state["n"] == 1:
            return FakeCap([])
        raise RuntimeError("MSMF patladi")
    rc = gd._ResilientCapture(opener, clock=clock, fail_limit=1, retry_every=1.0)
    rc.read()
    clock.t = 5.0
    assert rc.read()[0] is False        # no exception escapes


def test_lost_and_back_are_announced_once(capsys):
    dead, alive = FakeCap([]), FakeCap(["a", "b"])
    caps = [dead, alive]
    clock = Clock()
    rc = gd._ResilientCapture(lambda: caps.pop(0), clock=clock,
                              fail_limit=3, retry_every=1.0)
    for _ in range(10):
        rc.read()
    clock.t = 2.0
    rc.read()
    rc.read()
    out = capsys.readouterr().out
    assert out.count("KAMERA") == 2     # one "lost", one "back"


def test_get_and_release_delegate():
    cap = FakeCap()
    rc = gd._ResilientCapture(lambda: cap, clock=Clock())
    assert rc.get(3) == 640.0
    rc.release()
    assert cap.released


# --- a failed first calibration used to end the program ---------------------


class HK:
    def __init__(self):
        self.quit_requested = False


def test_failed_calibration_is_retried_not_fatal(capsys):
    results = [None, None, "mapper"]
    hk = HK()
    m = gd._calibrate_until_done(lambda: results.pop(0), hk, wait=lambda s: None)
    assert m == "mapper"
    assert "tekrar" in capsys.readouterr().out.lower()


def test_quit_during_calibration_stops_retrying():
    hk = HK()
    calls = []

    def run():
        calls.append(1)
        hk.quit_requested = True
        return None
    assert gd._calibrate_until_done(run, hk, wait=lambda s: None) is None
    assert len(calls) == 1


def test_quit_while_waiting_between_attempts_stops():
    hk = HK()

    def wait(s):
        hk.quit_requested = True
    calls = []
    assert gd._calibrate_until_done(lambda: calls.append(1), hk, wait=wait) is None
    assert len(calls) == 1


def test_a_dots_plan_too_small_for_the_features_gets_more_passes(capsys):
    """9 dots x 1 pass with 11 features: the fit needs 12 points, so every
    attempt failed and the first calibration retried forever, silently."""
    assert gd._dot_passes(9, 1, 11) == 2
    assert "yetersiz" in capsys.readouterr().out
    assert gd._dot_passes(16, 1, 11) == 1
    assert gd._dot_passes(16, 2, 11) == 2


def test_the_driver_times_things_with_a_monotonic_clock():
    """A Windows time sync (right after resume - exactly when the camera
    reconnects) stepped the wall clock: reconnects stalled by the step,
    overlays closed early, a blink could fire a click. Only the sample
    stamps written to disk keep wall time."""
    import inspect
    src = inspect.getsource(gd)
    lines = [ln for ln in src.splitlines() if "time.time()" in ln]
    assert len(lines) == 1 and "stamps.append" in lines[0], lines
    assert gd._ResilientCapture.__init__.__defaults__[0] is gd.time.monotonic
