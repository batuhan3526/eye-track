# gaze/eye_gaze/ses_isci.py
"""The program's side of the voice worker (Task 18b, R44; bounded waits R46).

In a packaged build piper-tts and espeak-ng (GPL-3.0) never load into the
program's own process: that process also holds libraries that are not
GPL-compatible (Intel oneMKL in CTranslate2, OpenSSL 1.1.1 in OpenCV on
Linux). They run in GozFaresi-Ses.exe / gozfaresi-ses next to the main
executable (gaze/gozfaresi_ses.py), started once and kept warm, over a pipe
(eye_gaze/ses_protokol.py). A source install keeps piper in-process.

piper_synth() returns what tts_backends' piper backend returns in-process: a
synth_to_wav(text) -> WAV bytes. Nobody ever waits long on the worker - F4
and the AAC grid are this user's voice. synth_to_wav RAISES, and
speech.Speaker says that sentence with the next voice of the chain
(onecore/sapi, espeak/spd) at once, when:
  - another caller holds the worker for more than LOCK_WAIT_S (busy);
  - the worker is starting or restarting and its voice is not loaded
    within READY_WAIT_S (it keeps loading in the background);
  - it died (end of file: at once) or does not answer within
    SYNTH_TIMEOUT_S + SYNTH_TIMEOUT_PER_CHAR_S * len(text) - a few seconds,
    scaled to the measured ~0.1-0.5 s per sentence; it is then killed,
    said once, and a fresh worker starts loading right away;
  - cancel() (Speaker.stop()) ends the wait: the stopped sentence is
    dropped, its late answer discarded by request id.
After three failures in a row the Speaker switches voices for the session
(the existing rule) and closes the worker. The phrase-cache warmer, which is
never urgent, first waits for the voice to be ready (wait_ready()).

The worker cannot outlive the program: it exits at end of file on its
stdin, on Linux it watches its parent and exits the moment the parent is
gone, and on Windows it sits in a job object that kills it when the
program's last handle to the job closes - also when the program crashes.
"""
import atexit
import itertools
import os
import queue
import subprocess
import sys
import threading
import time

from . import paths
from .ses_protokol import ProtocolError, read_frame, write_frame

WORKER_NAME = "GozFaresi-Ses.exe" if os.name == "nt" else "gozfaresi-ses"

# A caller that finds the worker busy (another sentence, the warmer) takes
# the fallback voice after this long instead of queueing behind it.
LOCK_WAIT_S = 1.0
# A live sentence waits at most this long for a (re)starting worker's voice
# to load; the load goes on in the background for the next sentence.
READY_WAIT_S = 2.0
# Measured: process start + voice load 1.6-4 s (6 s under full CPU load). A
# load that is not done by this is a hung worker: killed and restarted.
LOAD_TIMEOUT_S = 60.0
# One sentence: measured 0.1 s steady (0.5 s with the driver's deep model
# sharing the CPU). An answer this late means a hung worker.
SYNTH_TIMEOUT_S = 2.5
SYNTH_TIMEOUT_PER_CHAR_S = 0.01
_POLL_S = 0.05
# A worker that dies while loading its voice again and again is retried
# after these pauses (then every last one), never in a tight loop beside
# the gaze tracking - and never given up (R46 N-2).
RESPAWN_BACKOFF_S = (0.0, 10.0, 20.0, 30.0)


class WorkerError(RuntimeError):
    """The worker is missing, busy, not ready, died, hung or refused."""


class WorkerCancelled(WorkerError):
    """cancel() ended the wait (the user stopped the speech). Not a fault:
    no fallback voice, nothing said."""
    cancelled = True


class _Transient(WorkerError):
    """The worker is fine but cannot take this sentence NOW (still loading,
    busy, waiting to be restarted): the sentence goes to the fallback voice
    at once, and it is NOT a voice failure - speech.Speaker does not count
    it toward switching the session to another voice (R46 N-1)."""
    transient = True


class _NotReady(_Transient):
    """The voice is still loading (or a restart is backing off)."""


class _Busy(_Transient):
    """Another request (or a stopped sentence) holds the worker."""


class _Refused(WorkerError):
    """The worker answered with an error (it is alive and well)."""


def _say(msg):
    try:
        print("[ses] " + str(msg).encode("ascii", "replace").decode("ascii"),
              flush=True)
    except Exception:
        pass


def worker_command():
    """[path] of the voice worker executable of THIS frozen build, or None
    (a source install, or a build without the worker)."""
    if not paths._frozen():
        return None
    exe = os.path.join(paths.app_dir(), WORKER_NAME)
    return [exe] if os.path.isfile(exe) else None


# --- Windows: kill-on-close job ----------------------------------------------

_job = {}


def _windows_job():
    """One job object for every worker of this process, created once and
    never closed by us: when the program ends - normally or not - Windows
    closes the handle and kills what is still in the job. None if it cannot
    be made (the pipe's end of file still ends the worker)."""
    if "h" in _job:
        return _job["h"]
    _job["h"] = None
    try:
        import ctypes
        from ctypes import wintypes

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount",
                "WriteTransferCount", "OtherTransferCount")]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC),
                        ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = k32.CreateJobObjectW
        create.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        create.restype = wintypes.HANDLE
        setinfo = k32.SetInformationJobObject
        setinfo.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                            wintypes.DWORD]
        setinfo.restype = wintypes.BOOL
        assign = k32.AssignProcessToJobObject
        assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assign.restype = wintypes.BOOL
        h = create(None, None)
        if not h:
            return None
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000   # KILL_ON_JOB_CLOSE
        if not setinfo(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            return None                                  # 9 = Extended...
        _job["h"] = h
        _job["assign"] = assign
    except Exception:
        return None
    return _job["h"]


def _into_job(proc):
    h = _windows_job()
    if not h:
        return False
    try:
        from ctypes import wintypes
        return bool(_job["assign"](h, wintypes.HANDLE(int(proc._handle))))
    except Exception:
        return False


# --- the worker ----------------------------------------------------------------

class _Life:
    """One worker process: its pipe, its reader thread, its voice load."""

    def __init__(self, proc, load_id):
        self.proc = proc
        self.load_id = load_id
        self.replies = queue.Queue()
        self.ready = threading.Event()      # the voice is loaded
        self.dead = threading.Event()       # end of file / broken pipe
        self.load_error = None
        self.started = time.monotonic()
        self.loaded_at = None
        self.said = False                   # its failure was said
        # Requests whose wait was cancelled (stop()) but which the worker is
        # still rendering: id -> deadline. Their answers are dropped here.
        self.orphans = {}

    def read(self):
        try:
            while True:
                frame = read_frame(self.proc.stdout)
                if frame is None:
                    break
                header, body = frame
                rid = header.get("id")
                if rid in self.orphans:
                    self.orphans.pop(rid, None)
                    continue
                if rid == self.load_id:
                    if header.get("ok"):
                        self.loaded_at = time.monotonic()
                        self.ready.set()
                    else:
                        self.load_error = header.get("error", "?")
                        self.dead.set()
                    continue
                self.replies.put((header, body))
        except (ProtocolError, OSError, ValueError) as exc:
            self.load_error = self.load_error or "bozuk yanit: %s" % exc
        self.dead.set()

    def exit_code(self):
        try:
            return self.proc.wait(timeout=1.0)
        except Exception:
            return self.proc.poll()


class VoiceWorker:
    """One worker process holding one loaded voice. Thread-safe; every wait
    is bounded (module docstring)."""

    def __init__(self, command, load):
        self.command = list(command)
        self.load_request = dict(load, op="load")
        self._lock = threading.Lock()
        self._life = None
        self._ids = itertools.count(1)
        self._cancel = threading.Event()
        self._asker = None          # thread of the request being waited on
        self._load_deaths = 0       # lives in a row that died before ready
        self._next_spawn = 0.0
        self._closed = False        # closed for good: never respawned
        self.starts = 0
        self.timings = {}

    # -- process ----------------------------------------------------------

    def _spawn_locked(self):
        if self._closed:
            raise WorkerError("ses sureci kapatildi")
        if time.monotonic() < self._next_spawn:
            raise _NotReady("ses sureci yeniden baslatilmayi bekliyor")
        flags = 0x08000000 if os.name == "nt" else 0     # CREATE_NO_WINDOW
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        proc = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, close_fds=True,
                                creationflags=flags, env=env)
        if os.name == "nt":
            _into_job(proc)
        life = _Life(proc, next(self._ids))
        threading.Thread(target=life.read, daemon=True,
                         name="ses-isci-okur").start()
        self.starts += 1
        self._life = life
        write_frame(proc.stdin, dict(self.load_request, id=life.load_id))

    def _alive_locked(self):
        life = self._life
        return (life is not None and not life.dead.is_set()
                and life.proc.poll() is None)

    def _reap_locked(self):
        """Forget a life that ended; one that never got its voice loaded
        sets the back-off before the next spawn."""
        life = self._life
        if life is None or self._alive_locked():
            return
        if life.ready.is_set():
            self._load_deaths = 0
        else:
            self._load_deaths += 1
            pause = RESPAWN_BACKOFF_S[min(self._load_deaths - 1,
                                          len(RESPAWN_BACKOFF_S) - 1)]
            self._next_spawn = time.monotonic() + pause
        self._stop_life_locked()

    def _ensure_started_locked(self):
        """A live worker, starting one (load sent, not waited for) if
        needed. Raises _NotReady while a restart backs off, WorkerError
        when the executable cannot be started."""
        if not self._alive_locked():
            self._reap_locked()
            try:
                self._spawn_locked()
            except _NotReady:
                raise
            except Exception as exc:
                self._life = None
                raise WorkerError("ses sureci baslatilamadi: %s" % exc)

    def _stop_life_locked(self):
        life, self._life = self._life, None
        if life is None:
            return
        try:
            life.proc.stdin.close()
        except Exception:
            pass
        try:
            life.proc.kill()
        except Exception:
            pass
        try:
            life.proc.wait(timeout=5)
        except Exception:
            pass

    def start(self):
        """Start the worker and send the voice load now, without waiting."""
        if self._closed:
            return
        if not self._lock.acquire(timeout=LOCK_WAIT_S):
            return
        try:
            self._check_load_locked()
            self._ensure_started_locked()
        except _NotReady:
            pass                        # backing off; tried again later
        except WorkerError as exc:
            _say("ses sureci baslatilamadi: %s" % exc)
        finally:
            self._lock.release()

    def _check_load_locked(self):
        """A load running past LOAD_TIMEOUT_S is a hung worker."""
        life = self._life
        if (life is not None and self._alive_locked()
                and not life.ready.is_set()
                and time.monotonic() - life.started > LOAD_TIMEOUT_S):
            self._fail_locked(WorkerError(
                "ses sureci %.0f sn icinde sesi yukleyemedi"
                % LOAD_TIMEOUT_S))

    def wait_ready(self, timeout):
        """True once the voice is loaded, within `timeout` (for callers that
        are never urgent: the phrase-cache warmer, the alarm phrase). It
        follows restarts: a life that dies while loading is replaced (after
        the back-off) and waited on in turn."""
        end = time.monotonic() + float(timeout)
        while True:
            self.start()
            life = self._life
            if life is not None and life.ready.is_set():
                self._load_deaths = 0
                return True
            left = end - time.monotonic()
            if left <= 0 or self._closed:
                return False
            if life is None or life.dead.is_set():
                time.sleep(min(0.5, left))
                continue
            life.ready.wait(min(0.5, left))

    def cancel(self, thread=None):
        """End the wait of a pending request at once (Speaker.stop()).
        thread: only when that thread asked (the Speaker's own worker) - a
        stop must not cut the phrase-cache warmer's or the alarm phrase's
        request short."""
        if thread is not None and self._asker is not thread:
            return
        self._cancel.set()

    def _fail_locked(self, exc, restart=True):
        """A dead or hung worker: say it once, kill it, start a fresh one
        loading for the next sentence."""
        life = self._life
        if life is not None and not life.said:
            life.said = True
            _say("ses sureci (piper) kullanilamadi: %s - bu cumle yedek"
                 " sesle soylenir; ses sureci yeniden baslatiliyor" % (exc,))
        if self._alive_locked():
            # hung or answering nonsense: it is not coming back by itself
            life.dead.set()
            try:
                life.proc.kill()
            except Exception:
                pass
        self._reap_locked()
        if restart:
            try:
                self._spawn_locked()
            except Exception:
                self._life = None

    def _wait_ready_locked(self, life):
        end = time.monotonic() + READY_WAIT_S
        while True:
            if life.ready.is_set():
                self._load_deaths = 0
                return
            if life.dead.is_set():
                raise WorkerError("ses sureci kapandi (cikis kodu %s%s)" % (
                    life.exit_code(), "; " + life.load_error
                    if life.load_error else ""))
            if self._cancel.is_set():
                raise WorkerCancelled("durduruldu")
            if time.monotonic() - life.started > LOAD_TIMEOUT_S:
                raise WorkerError("ses sureci %.0f sn icinde sesi yukleyemedi"
                                  % LOAD_TIMEOUT_S)
            if time.monotonic() >= end:
                # Not a fault yet: it goes on loading for the next sentence.
                raise _NotReady("ses sureci hazir degil")
            life.ready.wait(_POLL_S)

    def _answer_locked(self, life, rid, timeout):
        end = time.monotonic() + timeout
        while True:
            if self._cancel.is_set():
                raise WorkerCancelled("durduruldu")
            try:
                header, body = life.replies.get(timeout=_POLL_S)
            except queue.Empty:
                if life.dead.is_set() and life.replies.empty():
                    raise WorkerError("ses sureci kapandi (cikis kodu %s)"
                                      % life.exit_code())
                if time.monotonic() >= end:
                    raise WorkerError("ses sureci %.1f sn icinde yanit"
                                      " vermedi" % timeout)
                continue
            if header.get("id") != rid:
                continue                    # a stopped sentence's late answer
            if not header.get("ok"):
                raise _Refused("ses sureci: %s" % header.get("error", "?"))
            return header, body

    def request(self, header, timeout, lock_timeout=LOCK_WAIT_S):
        """The worker's answer (header, body) to one request. Raises
        WorkerError (busy, not ready, dead, hung, refused) or
        WorkerCancelled; a dead or hung worker is said once, killed and
        restarted in the background."""
        if not self._lock.acquire(timeout=lock_timeout):
            raise _Busy("ses sureci mesgul")
        try:
            self._cancel.clear()
            self._asker = threading.current_thread()
            life = self._life
            if life is not None and not self._alive_locked():
                # Died since the last sentence (crash, antivirus, killed):
                # this sentence goes to the fallback NOW; a fresh worker
                # starts loading for the next one.
                exc = WorkerError("ses sureci kapandi (cikis kodu %s)"
                                  % life.exit_code())
                self._fail_locked(exc)
                raise exc
            self._ensure_started_locked()
            life = self._life
            rid = None
            try:
                self._wait_ready_locked(life)
                if life.orphans:
                    # Still rendering a sentence the user stopped (N-4):
                    # past its own deadline that is a hang, otherwise this
                    # one goes to the fallback without waiting behind it.
                    if time.monotonic() > max(life.orphans.values()):
                        raise WorkerError("ses sureci durdurulan cumleye"
                                          " yanit vermedi")
                    raise _Busy("ses sureci durdurulan cumleyi bitiriyor")
                rid = next(self._ids)
                write_frame(life.proc.stdin, dict(header, id=rid))
                return self._answer_locked(life, rid, timeout)
            except WorkerCancelled:
                if rid is not None:
                    life.orphans[rid] = time.monotonic() + timeout
                raise
            except (_Transient, _Refused):
                raise
            except WorkerError as exc:
                self._fail_locked(exc)
                raise
            except Exception as exc:        # broken pipe on write
                err = WorkerError("%s: %s" % (type(exc).__name__, exc))
                self._fail_locked(err)
                raise err
        finally:
            self._asker = None
            self._lock.release()

    def synth(self, text):
        text = str(text)
        t0 = time.monotonic()
        _header, body = self.request(
            {"op": "synth", "text": text},
            SYNTH_TIMEOUT_S + SYNTH_TIMEOUT_PER_CHAR_S * len(text))
        if not body:
            raise WorkerError("ses sureci bos ses dondurdu")
        self._timing(time.monotonic() - t0)
        return body

    def _timing(self, seconds):
        """Two lines a session, for the record: the worker's start-to-ready
        time with the first sentence, and the mean of the next ten."""
        t = self.timings
        if "first_s" not in t:
            t["first_s"] = seconds
            life = self._life
            load = (life.loaded_at - life.started) if (
                life is not None and life.loaded_at) else 0.0
            _say("ses sureci hazir: acilis + ses yukleme %.0f ms, ilk cumle"
                 " %.0f ms" % (1000 * load, 1000 * seconds))
            return
        steady = t.setdefault("steady", [])
        if len(steady) < 10:
            steady.append(seconds)
            if len(steady) == 10:
                _say("ses sureci: cumle basina ortalama %.0f ms (10 cumle)"
                     % (100 * sum(steady)))

    def pid(self):
        life = self._life
        return life.proc.pid if life is not None else None

    def close(self):
        """End the worker for good (the session gave this voice up, or the
        program ends): end of file first, kill if it lingers."""
        self._closed = True
        self.cancel()
        with self._lock:
            life, self._life = self._life, None
            if life is None:
                return
            try:
                life.proc.stdin.close()
            except Exception:
                pass
            try:
                life.proc.wait(timeout=2)
            except Exception:
                try:
                    life.proc.kill()
                    life.proc.wait(timeout=2)
                except Exception:
                    pass


# One worker per loaded voice (model + espeak data + speed + volume): the
# driver's Speakers (live speech, AAC grid, phrase-cache warmer) share it.
_workers = {}
_workers_lock = threading.Lock()


def shared_worker(command, model, espeak_dir, length_scale=1.0, volume=1.0):
    key = (tuple(command), model, espeak_dir, float(length_scale),
           float(volume))
    with _workers_lock:
        w = _workers.get(key)
        if w is None:
            w = VoiceWorker(command, {
                "model": model, "espeak_dir": espeak_dir,
                "length_scale": float(length_scale),
                "volume": float(volume)})
            _workers[key] = w
    return w


def close_all():
    with _workers_lock:
        workers = list(_workers.values())
        _workers.clear()
    for w in workers:
        try:
            w.close()
        except Exception:
            pass


atexit.register(close_all)


def piper_synth(command, model, espeak_dir, length_scale=1.0, volume=1.0):
    """synth_to_wav(text) backed by the worker, started (and its voice
    loaded) right away in the background. Carries cancel(), wait_ready()
    and close() for speech.Speaker."""
    worker = shared_worker(command, model, espeak_dir, length_scale, volume)
    worker.start()

    def synth_to_wav(text):
        return worker.synth(text)

    synth_to_wav.worker = worker
    synth_to_wav.cancel = worker.cancel
    synth_to_wav.wait_ready = worker.wait_ready
    synth_to_wav.close = worker.close
    return synth_to_wav
