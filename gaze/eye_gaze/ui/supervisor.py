# gaze/eye_gaze/ui/supervisor.py
"""The launcher's watch over the driver process, and "one launcher only".

BASLAT.bat's watchdog loop, moved into the launcher (spec 6.2):

  exit 0  the user quit (F10 twice)        -> "kapandi" (cikis)
  exit 2  config.yaml must be fixed; a restart cannot help
                                           -> "ayar-hatasi"
  other   a crash, or no camera            -> "yeniden-baslatiliyor",
                                              started again in 5 s
  CRASH_LIMIT crashes within CRASH_WINDOW_S -> "yeniden-baslatiliyor"
          (cokuyor), on a slow back-off: BACKOFF_S (60 s, 120 s, then every
          300 s). A driver that dies on every start would otherwise flash
          the camera light and grab the keyboard every 5 s, and nobody would
          read the log - so the window says "send the log". It never stops
          for good: each try arms the emergency alarm again for as long as
          the driver lives, and an ALS user must not be left with no alarm
          because of a crash loop until someone notices (ruling R36; the
          plan's hard stop took the alarm with it). A retry that runs for
          CRASH_WINDOW_S ends the back-off. Durdur, the user's quit (exit 0)
          and a config error (exit 2) still stop it.

The ALS user cannot restart the driver themselves - that is why a crash is
restarted at all, and why the launcher must never leave two drivers running
(both would hold the camera and hook the same keys: every click twice).

Nothing here blocks the Tk main loop: the child is polled through the
`schedule` hook (root.after), never waited for - except in stop(), which
must not return while the old driver still holds the camera and the global
key hooks (the key catcher and a restart need both).

Stopping: the child gets a pipe as stdin and GOZFARESI_SUPERVISED=1. The
driver side (watch_supervisor_pipe) turns the end of that pipe into a
KeyboardInterrupt in the driver's main thread, so main()'s finally runs:
a held drag is let go, the camera and the hooks are released. The pipe
also ends when the launcher itself dies - however it dies - so a driver is
never orphaned. Only a driver that does not stop within grace_s is
terminated (SIGTERM on Linux: the driver's close guard still runs; on
Windows TerminateProcess), then killed.
"""
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

from eye_gaze import i18n

STATES = ("calisiyor", "yeniden-baslatiliyor", "ayar-hatasi", "kapandi")
RESTART_DELAY_S = 5.0
CRASH_WINDOW_S = 60.0
CRASH_LIMIT = 5
# Seconds between tries once the driver keeps crashing; the last one repeats.
BACKOFF_S = (60.0, 120.0, 300.0)
STOP_GRACE_S = 5.0      # the driver's own cleanup (feedback <= 1 s, speech)
KILL_WAIT_S = 3.0
POLL_MS = 500

SUPERVISED_ENV = "GOZFARESI_SUPERVISED"
# The Turkish text (the window shows supervisor.crash_loop in its language).
CRASH_LOOP_MESSAGE = ("Sürücü sürekli çöküyor, günlüğü gönderin. (1 dakikada"
                      " {limit} kez kapandı; {seconds} sn sonra yeniden"
                      " denenecek.)")

_GAZE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _say(message):
    """One console line; ASCII only (the console is cp1254)."""
    try:
        from eye_gaze.platform import ascii_text
        print("[baslatici] " + ascii_text(message))
    except Exception:
        pass


def driver_command():
    """argv of the driver: the same exe in its driver role when frozen,
    gaze_driver.py under this interpreter in a source checkout."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--surucu"]
    return [sys.executable, os.path.join(_GAZE_DIR, "gaze_driver.py")]


def _creationflags():
    # A windowless launcher (pythonw, or the frozen console=False exe)
    # starting python.exe would open a console window over the user's
    # screen; the driver's output goes to gaze.log anyway.
    if os.name == "nt" and sys.stdout is None:
        return 0x08000000       # CREATE_NO_WINDOW
    return 0


class Supervisor:
    """Runs `cmd` and keeps it running by the rules above. on_state(state)
    is called on every change, from start/stop/restart/poll (i.e. on the Tk
    thread when poll is scheduled through root.after).

    Attributes for the status window: state, reason ("cikis" | "durduruldu"
    | "cokuyor" | "ayar" | "cokme" | None), exit_code, message (Turkish).

    on_stopping() runs right before stop() starts waiting for a live
    driver (the wait can take seconds): the window says "Durduruluyor…".

    pause() / resume() bracket anything that needs the camera or the
    keyboard to itself (key capture, camera search, the wizard). They nest:
    the driver - running OR waiting to be restarted after a crash - is
    stopped by the first pause() and started again only when the last
    resume() ends, and only if it was running or pending then. While
    paused, start() only remembers that a start is wanted."""

    def __init__(self, cmd, on_state, schedule=None, cancel=None, popen=None,
                 clock=None, grace_s=STOP_GRACE_S, kill_wait_s=KILL_WAIT_S,
                 env=None, cwd=None, on_stopping=None):
        self.cmd = list(cmd)
        self._on_state = on_state
        self._on_stopping = on_stopping
        self._pauses = 0
        self._resume_wanted = False
        self._schedule = schedule
        self._cancel = cancel
        self._popen = popen or subprocess.Popen
        self._clock = clock or time.monotonic
        self._grace = float(grace_s)
        self._kill_wait = float(kill_wait_s)
        self._env = env
        self._cwd = cwd
        self._proc = None
        self._timer = None
        self._restart_at = None
        self._crashes = []
        self._backoff = None        # index into BACKOFF_S while backing off
        self._spawned_at = None
        self.state = "kapandi"
        self.reason = None
        self.exit_code = None
        self.message = ""

    # --- public ------------------------------------------------------------

    def running(self):
        return self._proc is not None and self._proc.poll() is None

    def pending_restart(self):
        return self._restart_at is not None

    @property
    def paused(self):
        return self._pauses > 0

    def pause(self):
        if self._pauses == 0:
            self._resume_wanted = self.running() or self.pending_restart()
            if self._resume_wanted:
                self.stop()
        self._pauses += 1

    def resume(self, restart=True):
        """End one pause(); restart=False drops the wish to start again
        (e.g. the wizard was left with "Şimdi değil")."""
        if self._pauses == 0:
            return
        if not restart:
            self._resume_wanted = False
        self._pauses -= 1
        if self._pauses == 0 and self._resume_wanted:
            self._resume_wanted = False
            self.start()

    def start(self):
        """Start the driver unless it runs. An explicit start forgets the
        crash count: the caregiver is trying again on purpose."""
        if self._pauses:
            self._resume_wanted = True
            return
        if self.running():
            return
        self._crashes = []
        self._backoff = None
        self._restart_at = None
        self._spawn()

    def stop(self):
        """End the driver (and any pending restart); returns once it is
        gone."""
        self._disarm()
        self._restart_at = None
        proc, self._proc = self._proc, None
        if proc is not None:
            if self._on_stopping is not None and proc.poll() is None:
                try:
                    self._on_stopping()
                except Exception as exc:
                    _say("durum bildirimi hatasi: %s" % ascii(exc))
            self._end(proc)
        self.reason = "durduruldu"
        self.message = ""
        self._set("kapandi")

    def restart(self):
        self.stop()
        self.start()

    def poll(self):
        """Look at the child once; scheduled every POLL_MS while needed."""
        self._timer = None
        if self._proc is not None:
            code = self._proc.poll()
            if code is None:
                self._arm()
                return
            self._close_pipe(self._proc)
            self._proc = None
            self._exited(code)
            return
        if self._restart_at is not None:
            if self._clock() >= self._restart_at:
                self._restart_at = None
                self._spawn()
            else:
                self._arm()

    # --- internals -----------------------------------------------------------

    def _set(self, state):
        self.state = state
        try:
            self._on_state(state)
        except Exception as exc:
            _say("durum bildirimi hatasi: %r" % (exc,))

    def _arm(self):
        if self._schedule is None or self._timer is not None:
            return
        self._timer = self._schedule(POLL_MS, self.poll)

    def _disarm(self):
        if self._timer is not None and self._cancel is not None:
            try:
                self._cancel(self._timer)
            except Exception:
                pass
        self._timer = None

    def _spawn(self):
        env = dict(os.environ if self._env is None else self._env)
        env[SUPERVISED_ENV] = "1"
        self._spawned_at = self._clock()
        try:
            self._proc = self._popen(
                self.cmd, stdin=subprocess.PIPE, env=env, cwd=self._cwd,
                creationflags=_creationflags())
        except Exception as exc:
            self._proc = None
            _say("surucu baslatilamadi: %r" % (exc,))
            self._crashed(None, i18n.t("supervisor.start_failed", error=exc))
            return
        self.exit_code = None
        self.reason = None
        self.message = ""
        self._set("calisiyor")
        self._arm()

    def _exited(self, code):
        self.exit_code = code
        if code == 0:
            self.reason = "cikis"
            self.message = ""
            self._set("kapandi")
        elif code == 2:
            self.reason = "ayar"
            self.message = ""
            _say("surucu ayar hatasi (kod 2): yeniden baslatilmiyor")
            self._set("ayar-hatasi")
        else:
            self._crashed(code, "")

    def _crashed(self, code, message):
        now = self._clock()
        ran = 0.0 if self._spawned_at is None else now - self._spawned_at
        if self._backoff is not None and ran >= CRASH_WINDOW_S:
            # This try ran for a minute: no longer a crash loop.
            self._backoff = None
            self._crashes = []
        self._crashes = [t for t in self._crashes
                         if now - t < CRASH_WINDOW_S] + [now]
        if self._backoff is not None or len(self._crashes) >= CRASH_LIMIT:
            self._backoff = (0 if self._backoff is None
                             else min(self._backoff + 1, len(BACKOFF_S) - 1))
            delay = BACKOFF_S[self._backoff]
            self.reason = "cokuyor"
            self.message = i18n.t("supervisor.crash_loop",
                                  limit="%d" % CRASH_LIMIT,
                                  seconds="%d" % delay)
            if message:
                self.message += "\n" + message
            _say("surucu surekli kapaniyor (1 dakikada %d kez); %d sn sonra"
                 " yeniden denenecek" % (CRASH_LIMIT, delay))
            self._restart_at = now + delay
            self._set("yeniden-baslatiliyor")
            self._arm()
            return
        self.reason = "cokme"
        self.message = message
        _say("surucu kapandi (kod %s); %d sn sonra yeniden baslatiliyor"
             % (code, RESTART_DELAY_S))
        self._restart_at = now + RESTART_DELAY_S
        self._set("yeniden-baslatiliyor")
        self._arm()

    @staticmethod
    def _close_pipe(proc):
        stdin = getattr(proc, "stdin", None)
        if stdin is not None:
            try:
                stdin.close()
            except Exception:
                pass

    def _end(self, proc):
        """Pipe closed (the driver stops itself), then SIGTERM/terminate,
        then kill - each only if the one before did not end it."""
        self._close_pipe(proc)
        for step, wait in ((None, self._grace), (proc.terminate,
                                                 self._kill_wait),
                           (proc.kill, self._kill_wait)):
            if step is not None:
                if proc.poll() is not None:
                    return
                _say("surucu durmadi; %s" % ("sonlandiriliyor"
                                             if step == proc.terminate
                                             else "zorla kapatiliyor"))
                try:
                    step()
                except Exception:
                    pass
            try:
                proc.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                return


# --- driver side of the stop pipe -------------------------------------------

def _interrupt_main():
    """Ctrl+C for the main thread, from another thread. _thread.interrupt_
    main() alone only sets Python's flag: a main thread blocked in a sleep
    or a wait would not notice it until that returns (measured: a 30 s
    sleep ran to its end). A real SIGINT wakes it - on Windows raise()
    runs Python's handler, which also signals the event sleeps wait on; on
    Linux pthread_kill aims it at the main thread, whose system call then
    returns EINTR. Python's own (or a chained) SIGINT handler turns it into
    KeyboardInterrupt."""
    import signal
    try:
        if os.name == "nt":
            signal.raise_signal(signal.SIGINT)
        else:
            signal.pthread_kill(threading.main_thread().ident,
                                signal.SIGINT)
        return
    except Exception:
        pass
    import _thread
    _thread.interrupt_main()


PIPE_POLL_S = 0.25
_ERROR_BROKEN_PIPE = 109


def _peek_named_pipe(handle):
    """Bytes waiting in the pipe, None once its writer has closed it;
    OSError when the handle is not a pipe."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    peek = k32.PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD),
                     ctypes.POINTER(wintypes.DWORD)]
    peek.restype = wintypes.BOOL
    avail = wintypes.DWORD(0)
    if not peek(handle, None, 0, None, ctypes.byref(avail), None):
        err = ctypes.get_last_error()
        if err == _ERROR_BROKEN_PIPE:
            return None
        raise OSError(err, "PeekNamedPipe")
    return avail.value


def _windows_pipe_read(fd, n, peek=None, get_handle=None, sleep=time.sleep,
                       os_read=os.read):
    """os.read(fd, n) for the stop pipe on Windows, without ever leaving a
    read pending: polls PeekNamedPipe and reads only bytes already there.

    A thread blocked in a synchronous ReadFile on stdin freezes every other
    thread that touches the same handle - and loading a DLL whose C runtime
    inspects the standard handles at start-up does (measured, Task 15: with
    the watcher reading, `import numpy` hung until the launcher closed the
    pipe; the frozen driver never got past its imports). b"" on the pipe's
    end; OSError when stdin is not a pipe at all (the watcher then gives up
    without stopping anything)."""
    peek = peek or _peek_named_pipe
    if get_handle is None:
        import msvcrt
        get_handle = msvcrt.get_osfhandle
    handle = get_handle(fd)
    while True:
        avail = peek(handle)
        if avail is None:
            return b""
        if avail:
            return os_read(fd, min(n, avail))
        sleep(PIPE_POLL_S)


def watch_supervisor_pipe(read=None, interrupt=None, environ=None):
    """In the driver: when started by the launcher (GOZFARESI_SUPERVISED=1),
    end the driver as Ctrl+C would once the launcher closes our stdin - on
    Durdur, on a restart, and when the launcher dies. Only a clean end of
    the pipe stops anything; a missing or unreadable stdin never does.
    Returns the watching thread, or None when not supervised."""
    environ = os.environ if environ is None else environ
    if environ.get(SUPERVISED_ENV) != "1":
        return None
    read = read or (_windows_pipe_read if os.name == "nt" else os.read)
    interrupt = interrupt or _interrupt_main

    def run():
        try:
            while True:
                if not read(0, 64):
                    break
        except Exception:
            return
        _say("baslatici durdurma istedi; surucu kapaniyor")
        interrupt()

    t = threading.Thread(target=run, name="baslatici-borusu", daemon=True)
    t.start()
    return t


# --- one launcher ------------------------------------------------------------

def lock_path():
    """The launcher lock: per user (one launcher, hence one driver, per
    session), or inside GOZFARESI_DATA_DIR when that is set, so a smoke or a
    test never collides with a real launcher."""
    override = os.environ.get("GOZFARESI_DATA_DIR")
    if override:
        return os.path.join(override, "baslatici.lock")
    if os.name == "nt":
        return os.path.join(tempfile.gettempdir(), "GozFaresi-baslatici.lock")
    rt = os.environ.get("XDG_RUNTIME_DIR")
    if rt and os.path.isdir(rt):
        return os.path.join(rt, "gozfaresi-baslatici.lock")
    return "/tmp/gozfaresi-%d-baslatici.lock" % os.getuid()


class InstanceLock:
    """acquire() -> True for the first launcher only (an OS file lock: a
    crashed launcher leaves nothing that blocks). The first one serve()s a
    localhost socket; a second launcher send()s it "goster" / "ayarlar" /
    "sihirbaz" and exits, so a double click on the icon brings the open
    window to the front instead of starting a second driver."""

    def __init__(self, path=None):
        self.path = path or lock_path()
        self._lock = None
        self._server = None

    @property
    def _port_file(self):
        return self.path + ".port"

    def acquire(self):
        from eye_gaze import downloader, paths
        paths.ensure_dir(os.path.dirname(self.path) or ".")
        lock = downloader._ProcessLock(self.path)
        try:
            if not lock.try_acquire():
                return False
        except OSError as exc:
            # A lock that cannot even be created must not lock the user
            # out of their only input device: run, and say so.
            _say("baslatici kilidi olusturulamadi (%r); tek ornek denetimi"
                 " yok" % (exc,))
            return True
        self._lock = lock
        return True

    def serve(self, on_message):
        """Accept messages from later launchers; on_message(text) runs on a
        background thread (the caller marshals it to Tk)."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(4)
        self._server = srv
        port = srv.getsockname()[1]
        try:
            with open(self._port_file, "w", encoding="ascii") as f:
                f.write(str(port))
        except OSError as exc:
            _say("baslatici port dosyasi yazilamadi: %r" % (exc,))

        def loop():
            while True:
                try:
                    conn, _ = srv.accept()
                except OSError:
                    return
                try:
                    conn.settimeout(2.0)
                    data = conn.recv(64).decode("ascii", "replace").strip()
                    conn.sendall(b"ok\n")
                except OSError:
                    data = ""
                finally:
                    conn.close()
                if data:
                    try:
                        on_message(data)
                    except Exception as exc:
                        _say("ileti islenemedi: %r" % (exc,))

        threading.Thread(target=loop, name="baslatici-ileti",
                         daemon=True).start()

    def send(self, message):
        """True when a running launcher took `message`."""
        try:
            with open(self._port_file, encoding="ascii") as f:
                port = int(f.read().strip())
            with socket.create_connection(("127.0.0.1", port),
                                          timeout=2.0) as c:
                c.sendall(message.encode("ascii") + b"\n")
                return c.recv(8).startswith(b"ok")
        except (OSError, ValueError):
            return False

    def release(self):
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
            try:
                os.remove(self._port_file)
            except OSError:
                pass
        if self._lock is not None:
            self._lock.release()
            self._lock = None
