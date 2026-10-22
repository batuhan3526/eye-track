# gaze/tests/test_ui_supervisor.py
"""The launcher's watch over the driver process (ui/supervisor.py).

Fake processes and a fake clock: nothing here starts the real driver. One
test runs a real child Python to prove the stop pipe works end to end."""
import os
import subprocess
import sys
import threading
import time

import pytest

from eye_gaze.ui import supervisor as sv


class FakeStdin:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeProc:
    def __init__(self, exits_on_stdin_close=True):
        self.code = None
        self.stdin = FakeStdin()
        self.terminated = False
        self.killed = False
        self.exits_on_stdin_close = exits_on_stdin_close

    def finish(self, code):
        self.code = code

    def poll(self):
        if self.code is None and self.stdin.closed \
                and self.exits_on_stdin_close:
            self.code = 0
        return self.code

    def wait(self, timeout=None):
        if self.poll() is None:
            raise subprocess.TimeoutExpired("driver", timeout)
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = 1 if self.code is None else self.code

    def kill(self):
        self.killed = True
        self.code = -9 if self.code is None else self.code


class Popen:
    """Records every spawn; refuses one while an earlier child still runs
    (the launcher must never have two drivers at once)."""

    def __init__(self, **proc_kw):
        self.procs = []
        self.calls = []
        self.proc_kw = proc_kw

    def __call__(self, cmd, **kw):
        for p in self.procs:
            assert p.poll() is not None, "a second driver was started"
        self.calls.append((list(cmd), kw))
        p = FakeProc(**self.proc_kw)
        self.procs.append(p)
        return p

    @property
    def last(self):
        return self.procs[-1]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(popen=None, clock=None, **kw):
    states = []
    sup = sv.Supervisor(["driver"], states.append, popen=popen or Popen(),
                        clock=clock or Clock(), **kw)
    return sup, states


def test_supervisor_exit_codes():
    popen, clock = Popen(), Clock()
    sup, states = make(popen, clock)
    sup.start()
    assert states == ["calisiyor"] and len(popen.procs) == 1

    popen.last.finish(1)                    # a crash
    sup.poll()
    assert states[-1] == "yeniden-baslatiliyor"
    clock.t += sv.RESTART_DELAY_S - 0.1
    sup.poll()
    assert len(popen.procs) == 1            # not before 5 s
    clock.t += 0.2
    sup.poll()
    assert states[-1] == "calisiyor" and len(popen.procs) == 2

    popen.last.finish(2)                    # config error: never restarted
    sup.poll()
    assert states[-1] == "ayar-hatasi" and sup.exit_code == 2
    clock.t += 60
    sup.poll()
    assert len(popen.procs) == 2

    sup.start()
    popen.last.finish(0)                    # F10 x2: the user quit
    sup.poll()
    assert states[-1] == "kapandi" and sup.reason == "cikis"
    clock.t += 60
    sup.poll()
    assert len(popen.procs) == 3
    assert states == ["calisiyor", "yeniden-baslatiliyor", "calisiyor",
                      "ayar-hatasi", "calisiyor", "kapandi"]


def _crash_loop(popen, clock, sup):
    """CRASH_LIMIT crashes in a row; returns right after the last one."""
    sup.start()
    for i in range(sv.CRASH_LIMIT):
        if i:
            clock.t += sv.RESTART_DELAY_S + 1
            sup.poll()
        popen.last.finish(1)
        sup.poll()
    assert len(popen.procs) == sv.CRASH_LIMIT


def _wait_for_try(popen, clock, sup, seconds):
    """No new driver before `seconds`, one at `seconds`."""
    n = len(popen.procs)
    clock.t += seconds - 1
    sup.poll()
    assert len(popen.procs) == n, seconds
    clock.t += 1
    sup.poll()
    assert len(popen.procs) == n + 1, seconds


def test_a_crash_loop_backs_off_but_never_gives_up():
    # R36 (final review M-9): the old hard stop after CRASH_LIMIT crashes
    # left the ALS user without the alarm until someone noticed.
    popen, clock = Popen(), Clock()
    sup, states = make(popen, clock)
    _crash_loop(popen, clock, sup)
    assert states[-1] == "yeniden-baslatiliyor" and sup.reason == "cokuyor"
    assert "sürekli çöküyor" in sup.message
    assert "günlüğü gönderin" in sup.message
    assert "60 sn sonra yeniden denenecek" in sup.message
    assert sup.pending_restart()
    assert sv.BACKOFF_S == (60.0, 120.0, 300.0)
    waits = []
    for _ in range(5):
        wait = int(sup.message.split(" sn sonra")[0].rsplit(" ", 1)[1])
        waits.append(wait)
        _wait_for_try(popen, clock, sup, wait)
        assert states[-1] == "calisiyor"
        clock.t += 2                      # dies again at once
        popen.last.finish(1)
        sup.poll()
        assert states[-1] == "yeniden-baslatiliyor"
    assert waits == [60, 120, 300, 300, 300]


def test_stop_ends_the_back_off():
    popen, clock = Popen(), Clock()
    sup, states = make(popen, clock)
    _crash_loop(popen, clock, sup)
    assert sup.pending_restart()
    sup.stop()                            # Durdur
    assert states[-1] == "kapandi" and sup.reason == "durduruldu"
    assert not sup.pending_restart()
    clock.t += 3600
    sup.poll()
    assert len(popen.procs) == sv.CRASH_LIMIT


def test_the_user_s_quit_during_the_back_off_stops_for_good():
    popen, clock = Popen(), Clock()
    sup, states = make(popen, clock)
    _crash_loop(popen, clock, sup)
    _wait_for_try(popen, clock, sup, 60)
    popen.last.finish(0)                  # F10 x2
    sup.poll()
    assert states[-1] == "kapandi" and sup.reason == "cikis"
    clock.t += 3600
    sup.poll()
    assert len(popen.procs) == sv.CRASH_LIMIT + 1


def test_a_try_that_runs_for_a_minute_ends_the_back_off():
    popen, clock = Popen(), Clock()
    sup, states = make(popen, clock)
    _crash_loop(popen, clock, sup)
    _wait_for_try(popen, clock, sup, 60)
    clock.t += sv.CRASH_WINDOW_S          # ran fine for a minute
    popen.last.finish(1)
    sup.poll()
    assert states[-1] == "yeniden-baslatiliyor" and sup.reason == "cokme"
    _wait_for_try(popen, clock, sup, sv.RESTART_DELAY_S)


def test_a_driver_that_cannot_be_started_backs_off_too():
    clock = Clock()

    def refuse(cmd, **kw):
        raise OSError("no such exe")
    sup, states = make(refuse, clock)
    sup.start()
    for _ in range(sv.CRASH_LIMIT - 1):
        clock.t += sv.RESTART_DELAY_S
        sup.poll()
    assert sup.reason == "cokuyor" and states[-1] == "yeniden-baslatiliyor"
    assert "no such exe" in sup.message and "60 sn" in sup.message


def test_crashes_spread_over_more_than_a_minute_keep_restarting():
    popen, clock = Popen(), Clock()
    sup, states = make(popen, clock)
    sup.start()
    for i in range(sv.CRASH_LIMIT * 2):
        popen.last.finish(3)
        sup.poll()
        clock.t += 20                       # one crash every ~20 s
        sup.poll()
    assert states[-1] == "calisiyor"
    assert len(popen.procs) == sv.CRASH_LIMIT * 2 + 1


def test_an_explicit_start_forgets_old_crashes():
    popen, clock = Popen(), Clock()
    sup, states = make(popen, clock)
    sup.start()
    for i in range(sv.CRASH_LIMIT):
        popen.last.finish(1)
        sup.poll()
        clock.t += sv.RESTART_DELAY_S + 1
        sup.poll()
    assert sup.reason == "cokuyor"
    sup.start()                              # the caregiver tries again
    popen.last.finish(1)
    sup.poll()
    assert states[-1] == "yeniden-baslatiliyor" and sup.reason == "cokme"
    assert "5 sn" not in sup.message


def test_stop_closes_the_pipe_first_and_kills_only_if_needed():
    popen = Popen()
    sup, states = make(popen)
    sup.start()
    proc = popen.last
    sup.stop()
    assert proc.stdin.closed and not proc.terminated and not proc.killed
    assert states[-1] == "kapandi" and sup.reason == "durduruldu"

    stubborn = Popen(exits_on_stdin_close=False)
    sup, states = make(stubborn, grace_s=0.0)
    sup.start()
    sup.stop()
    assert stubborn.last.stdin.closed and stubborn.last.terminated
    assert not sup.running()


def test_stop_cancels_a_pending_restart():
    popen, clock = Popen(), Clock()
    sup, states = make(popen, clock)
    sup.start()
    popen.last.finish(1)
    sup.poll()
    sup.stop()
    clock.t += 30
    sup.poll()
    assert len(popen.procs) == 1 and states[-1] == "kapandi"


def test_restart_ends_the_old_driver_before_starting_the_new_one():
    popen = Popen()
    sup, states = make(popen)
    sup.start()
    sup.restart()           # Popen asserts the first one is gone
    assert len(popen.procs) == 2 and sup.running()
    assert states[-1] == "calisiyor"


def test_a_restart_stop_is_never_read_as_the_user_quitting():
    # The driver ends with exit 0 on the launcher's own pipe stop, the code
    # F10 x2 gives too - and exit 0 + reason "cikis" closes the launcher.
    # A restart (Settings -> Kaydet, a language change, the wizard's pause)
    # must never be that (Task 16 carry): stop() lets go of the process
    # before it ends, so its exit is not polled as the user's quit.
    popen = Popen()
    seen = []
    sup = sv.Supervisor(["driver"], lambda st: seen.append((st, sup.reason)),
                        popen=popen, clock=Clock())
    sup.start()
    sup.restart()
    sup.poll()
    sup.pause()
    sup.resume()
    sup.poll()
    assert all(p.code == 0 for p in popen.procs[:-1])     # all ended with 0
    assert ("kapandi", "cikis") not in seen
    assert sup.state == "calisiyor" and len(popen.procs) == 3


def test_start_twice_runs_one_driver():
    popen = Popen()
    sup, _ = make(popen)
    sup.start()
    sup.start()
    assert len(popen.procs) == 1


def test_the_child_gets_a_pipe_and_the_supervised_flag():
    popen = Popen()
    sup, _ = make(popen)
    sup.start()
    cmd, kw = popen.calls[0]
    assert cmd == ["driver"]
    assert kw["stdin"] == subprocess.PIPE
    assert kw["env"][sv.SUPERVISED_ENV] == "1"


def test_a_driver_that_cannot_start_is_said():
    def broken(cmd, **kw):
        raise FileNotFoundError("no such exe")
    clock = Clock()
    sup, states = make(broken, clock)
    sup.start()
    assert states[-1] == "yeniden-baslatiliyor"
    assert "başlatılamadı" in sup.message


def test_polling_goes_through_the_scheduler():
    scheduled = []
    popen = Popen()
    sup, states = make(popen, schedule=lambda ms, fn: scheduled.append(
        (ms, fn)) or len(scheduled), cancel=lambda token: None)
    sup.start()
    assert scheduled and scheduled[-1][0] == sv.POLL_MS
    popen.last.finish(0)
    scheduled[-1][1]()
    assert states[-1] == "kapandi"


def test_driver_command_frozen_and_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = sv.driver_command()
    assert cmd[0] == sys.executable
    assert os.path.basename(cmd[1]) == "gaze_driver.py"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert sv.driver_command() == [sys.executable, "--surucu"]


# --- the stop pipe, driver side ---------------------------------------------

def test_the_pipe_watcher_interrupts_only_on_a_clean_end():
    hits = []
    env = {sv.SUPERVISED_ENV: "1"}
    t = sv.watch_supervisor_pipe(read=lambda fd, n: b"", environ=env,
                                 interrupt=lambda: hits.append("int"))
    t.join(2)
    assert hits == ["int"]

    def broken(fd, n):
        raise OSError("no pipe")
    t = sv.watch_supervisor_pipe(read=broken, environ=env,
                                 interrupt=lambda: hits.append("bad"))
    t.join(2)
    assert hits == ["int"]          # no pipe: never a stop
    assert sv.watch_supervisor_pipe(read=broken, environ={},
                                    interrupt=lambda: None) is None


def test_windows_pipe_read_polls_and_never_blocks_in_a_read():
    """Bytes are read only once PeekNamedPipe says they are there; the
    pipe's end is b"" (the stop); a non-pipe stdin is an OSError."""
    seq = iter([0, 0, 3])
    reads, sleeps = [], []
    got = sv._windows_pipe_read(
        0, 64, peek=lambda h: next(seq), get_handle=lambda fd: 7,
        sleep=sleeps.append, os_read=lambda fd, n: reads.append(n) or b"abc")
    assert got == b"abc" and reads == [3] and len(sleeps) == 2

    seq = iter([0, None])
    assert sv._windows_pipe_read(0, 64, peek=lambda h: next(seq),
                                 get_handle=lambda fd: 7,
                                 sleep=lambda s: None,
                                 os_read=lambda fd, n: 1 / 0) == b""

    def not_a_pipe(h):
        raise OSError(1, "PeekNamedPipe")
    with pytest.raises(OSError):
        sv._windows_pipe_read(0, 64, peek=not_a_pipe, get_handle=lambda fd: 7)


def test_a_watched_child_can_still_load_native_modules(tmp_path):
    """Task 15 field finding: a watcher blocked in ReadFile on stdin froze
    every DLL load that inspects the standard handles - `import numpy` in
    the frozen driver hung until the launcher closed the pipe. With the
    watcher running and the pipe open, the import must finish."""
    gaze_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    marker = tmp_path / "ithal.txt"
    script = (
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from eye_gaze.ui import supervisor as sv\n"
        "sv.watch_supervisor_pipe()\n"
        "time.sleep(0.5)\n"
        "import numpy\n"
        "open(%r, 'w').write('ok')\n"
        "try:\n"
        "    time.sleep(30)\n"
        "except KeyboardInterrupt:\n"
        "    sys.exit(0)\n" % (gaze_dir, str(marker)))
    sup = sv.Supervisor([sys.executable, "-c", script], lambda s: None,
                        grace_s=10.0)
    sup.start()
    try:
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert marker.exists(), "import numpy hung behind the pipe watcher"
    finally:
        sup.stop()


def test_a_real_child_stops_cleanly_when_the_pipe_closes(tmp_path):
    """End to end: the child installs the watcher, the supervisor closes
    the pipe, the child's main thread gets KeyboardInterrupt and exits 0."""
    gaze_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    marker = tmp_path / "temiz.txt"
    script = (
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from eye_gaze.ui import supervisor as sv\n"
        "sv.watch_supervisor_pipe()\n"
        "try:\n"
        "    time.sleep(30)\n"
        "except KeyboardInterrupt:\n"
        "    open(%r, 'w').write('ok')\n"
        "    sys.exit(0)\n"
        "sys.exit(5)\n" % (gaze_dir, str(marker)))
    states = []
    sup = sv.Supervisor([sys.executable, "-c", script], states.append,
                        grace_s=10.0)
    sup.start()
    time.sleep(0.5)
    t0 = time.monotonic()
    sup.stop()
    assert time.monotonic() - t0 < 9
    assert marker.read_text() == "ok"
    assert states[-1] == "kapandi"


# --- one launcher ----------------------------------------------------------

def test_only_one_launcher_holds_the_lock(tmp_path):
    first = sv.InstanceLock(str(tmp_path / "baslatici.lock"))
    second = sv.InstanceLock(str(tmp_path / "baslatici.lock"))
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire()
    second.release()


def test_a_second_launcher_brings_the_first_to_front(tmp_path):
    path = str(tmp_path / "baslatici.lock")
    first = sv.InstanceLock(path)
    assert first.acquire()
    got = []
    seen = threading.Event()
    first.serve(lambda msg: (got.append(msg), seen.set()))
    try:
        assert sv.InstanceLock(path).send("ayarlar")
        assert seen.wait(5)
        assert got == ["ayarlar"]
    finally:
        first.release()
    assert not sv.InstanceLock(path).send("goster")


def test_the_default_lock_follows_the_data_dir_override(monkeypatch,
                                                        tmp_path):
    monkeypatch.setenv("GOZFARESI_DATA_DIR", str(tmp_path))
    assert os.path.dirname(sv.lock_path()) == str(tmp_path)


# --- gozfaresi.py: one exe, two roles ----------------------------------------

@pytest.fixture
def gozfaresi(monkeypatch):
    import gozfaresi as gf
    calls = {}

    class FakeDriver:
        @staticmethod
        def main():
            calls["argv"] = list(sys.argv[1:])
            result = calls.get("result", 0)
            if isinstance(result, BaseException):
                raise result
            return result
    monkeypatch.setitem(sys.modules, "gaze_driver", FakeDriver)
    monkeypatch.delenv(sv.SUPERVISED_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["gozfaresi"])
    return gf, calls


@pytest.mark.parametrize("result, code", [
    (0, 0), (2, 2), (1, 1), (KeyboardInterrupt(), 0),
    (RuntimeError("boom"), 1)])
def test_driver_role_keeps_the_exit_code(gozfaresi, result, code):
    gf, calls = gozfaresi
    calls["result"] = result
    assert gf.main(["--surucu", "--x"]) == code
    assert calls["argv"] == ["--x"]


def test_launcher_role_is_the_default(gozfaresi, monkeypatch):
    gf, calls = gozfaresi
    from eye_gaze import ui
    seen = []
    monkeypatch.setattr(ui, "main", lambda argv: seen.append(argv) or 0)
    assert gf.main([]) == 0 and gf.main(["--ayarlar"]) == 0
    assert seen == [[], ["--ayarlar"]] and "argv" not in calls


def test_the_driver_watches_the_pipe_only_when_supervised():
    gaze_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(gaze_dir, "gaze_driver.py"),
              encoding="utf-8") as f:
        src = f.read()
    tail = src[src.index('if __name__ == "__main__":'):]
    assert 'os.environ.get("GOZFARESI_SUPERVISED") == "1"' in tail
    assert tail.index("watch_supervisor_pipe()") < tail.index("_code = main()")


# --- pause / resume (key capture, camera search, wizard) ---------------------

def test_pause_holds_a_pending_crash_restart():
    popen, clock = Popen(), Clock()
    sup, states = make(popen, clock)
    sup.start()
    popen.last.finish(1)
    sup.poll()
    assert sup.pending_restart()
    sup.pause()
    assert not sup.pending_restart()
    clock.t += 30
    sup.poll()
    assert len(popen.procs) == 1            # not restarted mid-capture
    sup.resume()
    assert len(popen.procs) == 2 and sup.running()


def test_pause_nests_and_only_the_last_resume_starts():
    popen = Popen()
    sup, _ = make(popen)
    sup.start()
    sup.pause()
    sup.pause()
    assert not sup.running()
    sup.resume()
    assert not sup.running() and len(popen.procs) == 1
    sup.start()                             # while paused: remembered only
    assert len(popen.procs) == 1
    sup.resume()
    assert sup.running() and len(popen.procs) == 2
    sup.resume()                            # unbalanced: harmless
    assert len(popen.procs) == 2


def test_pause_of_a_stopped_driver_does_not_start_it():
    popen = Popen()
    sup, _ = make(popen)
    sup.pause()
    sup.resume()
    assert popen.procs == []
    sup.start()
    sup.pause()
    sup.resume(restart=False)               # wizard left with "Şimdi değil"
    assert not sup.running() and len(popen.procs) == 1


def test_stop_says_it_is_stopping_before_it_waits():
    popen = Popen()
    seen = []
    sup, states = make(popen, on_stopping=lambda: seen.append(
        popen.last.poll()))
    sup.start()
    sup.stop()
    assert seen == [None]                   # called while still running
    sup.stop()                              # nothing to stop: not called
    assert seen == [None]
