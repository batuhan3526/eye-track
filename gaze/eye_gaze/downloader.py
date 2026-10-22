# gaze/eye_gaze/downloader.py
"""Resumable, verified downloads of the models_manifest items.

  download(item, root, progress, cancel) -> path
      Fetches into <dest>.part, continuing with "Range: bytes=<n>-" from
      whatever an earlier try left. When the size is reached the SHA-256 is
      checked: a match is moved into place with os.replace, so the program
      never sees a half or corrupt file under the real name; a mismatch
      deletes the .part and raises DownloadError (code "bozuk"). A server
      that ignores Range (200 + whole body) restarts the file from zero.
      A cancel (threading.Event) or a cut connection keeps the .part for
      next time. wheel_libs items are unpacked after the check: only
      nvidia/*/lib|bin libraries, flat into the wheel's folder
      (model_dir()/gpu, where accel.preload_cuda looks), then the wheel goes.
      The libraries accel requires come out last and a per-wheel marker
      after them; pack_ready() (what accel trusts) is true only with every
      marker in place, so a crash mid-unpack never offers half a CUDA set.
  ensure(name, progress=None, cancel=None) -> path | None
      Every file of MANIFEST[name], one progress bar over all of them. The
      path (the file, or the entry's folder) or None with `last_error` set
      to a Turkish sentence for the screen. Never raises.
  status(name) -> "ok" | "missing" | "partial"   (creates nothing)

Every failure is a DownloadError whose str() is the Turkish sentence for the
user and whose .code says which kind; one ASCII line goes to the console
(cp1254). Proxies come from urllib.request.getproxies() (env vars, and the
Windows registry); certificates are the system's merged with certifi's
bundle when certifi is importable (a frozen Linux build's OpenSSL may not
find the distribution's store at all).

Concurrency: one thread lock per destination file and one per ensure()
name, so the wizard and a first F3 asking for the same model cannot write
the same .part at once - the second waits (a cancel ends the wait) and then
finds the file in place.
Across processes (two program instances, or the launcher and the driver)
the same holds through an OS lock on <dest>.lock: fcntl.flock on Linux,
msvcrt.locking on Windows. The OS drops it when its holder exits or
crashes, so a leftover lock file never blocks anyone; a live holder ends
within TIMEOUT of a stalled transfer, so waiting on it cannot hang either.
"""
import errno
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

from . import accel, paths
from .i18n import t
from .models_manifest import items

if os.name == "nt":
    import msvcrt
else:
    import fcntl

TIMEOUT = 30.0              # seconds, per connect and per read: never hang
CHUNK = 1 << 16
PROGRESS_INTERVAL = 0.25    # seconds between progress callbacks
LOCK_POLL = 0.2             # seconds between tries for a held lock
LOCK_DENIED_POLLS = 25      # Windows: "access denied" polls taken as busy
ATTEMPTS = 3                # ensure(): tries per file, while each one gains
USER_AGENT = "GozFaresi-indirici/1"
_MB = 1024 * 1024

# Windows: ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL.
_DISK_FULL_WINERRORS = (39, 112)

# A wheel member we unpack: nvidia/<package>/lib|bin/<library>.
_LIB_MEMBER = re.compile(r"nvidia/[^/]+/(?:lib|bin)/([^/]+)")
_LIB_NAME = re.compile(r".+(?:\.dll|\.so(?:\.[0-9]+)*)")
# What accel requires to call a set loadable: unpacked after every other
# library of its wheel (their dependencies), so a crash leaves them absent.
_ENTRY_LIBS = frozenset(name for per_os in accel._REQUIRED.values()
                        for names in per_os.values() for name in names)

last_error = None           # Turkish reason of the last failed ensure()

_ssl_ctx = None
_locks = {}
_locks_guard = threading.Lock()


class DownloadError(Exception):
    """str(e): Turkish sentence for the user. code: "bozuk" | "kesildi" |
    "zamanasimi" | "agyok" | "sertifika" | "sunucu" | "disk" | "izin" |
    "paket" | "iptal"."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class DownloadCancelled(DownloadError):
    pass


def _fail(item, code, message, detail="", cls=DownloadError):
    # Console is cp1254 and may get paths with Turkish letters: ascii().
    print("[indir] %s: %s %s" % (code, item.name, ascii(str(detail))
                                 if detail else ""), file=sys.stderr)
    return cls(message, code)


def _fname(item):
    return item.dest_rel.rsplit("/", 1)[-1]


def _is_windows():
    return os.name == "nt"


def _lock(key):
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.Lock()
        return lock


def _cancelled(item):
    return _fail(item, "iptal", t("downloader.cancelled", name=_fname(item)),
                 cls=DownloadCancelled)


class _waited:
    """with _waited(lock, item, cancel): a thread lock whose wait a cancel
    ends (DownloadCancelled) - the wizard's "Atla" must not sit behind
    another thread's gigabyte download."""

    def __init__(self, lock, item, cancel):
        self.lock, self.item, self.cancel = lock, item, cancel

    def __enter__(self):
        while not self.lock.acquire(timeout=LOCK_POLL):
            if self.cancel is not None and self.cancel.is_set():
                raise _cancelled(self.item)
        return self

    def __exit__(self, *exc):
        self.lock.release()
        return False


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return -1


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _open_part(path, mode):
    """Every file this module writes is opened here (tests inject a full
    disk or a refused permission)."""
    return open(path, mode)


# ------------------------------------------------------------ transport ---

def _ssl_context():
    """System trust store, plus certifi's bundle when it is importable. Read
    as text (cadata): the bundle may sit under a non-ASCII path."""
    global _ssl_ctx
    if _ssl_ctx is None:
        ctx = ssl.create_default_context()
        try:
            import certifi
            with open(certifi.where(), encoding="ascii") as f:
                ctx.load_verify_locations(cadata=f.read())
        except Exception:
            pass        # the default context alone still verifies
        _ssl_ctx = ctx
    return _ssl_ctx


def _opener(url):
    handlers = [urllib.request.ProxyHandler(urllib.request.getproxies())]
    if url.lower().startswith("https:"):
        # Building the context reads the whole trust store: only for HTTPS.
        handlers.append(urllib.request.HTTPSHandler(context=_ssl_context()))
    return urllib.request.build_opener(*handlers)


def _open_error(item, e):
    """DownloadError for a failure before the body started."""
    name = _fname(item)
    if isinstance(e, urllib.error.HTTPError):
        return _fail(item, "sunucu", t("downloader.http", code="%d" % e.code,
                                       name=name), e)
    reason = getattr(e, "reason", e)
    if isinstance(reason, ssl.SSLCertVerificationError) or \
            isinstance(e, ssl.SSLCertVerificationError):
        return _fail(item, "sertifika", t("downloader.certificate",
                                          name=name), e)
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return _fail(item, "zamanasimi", t("downloader.timeout", name=name),
                     e)
    return _fail(item, "agyok", t("downloader.offline", name=name), e)


def _os_error(item, e, folder):
    """DownloadError for a local write failure (disk, permission)."""
    if e.errno == errno.ENOSPC or \
            getattr(e, "winerror", None) in _DISK_FULL_WINERRORS:
        return _fail(item, "disk", t("downloader.disk_full",
                                     name=_fname(item), folder=folder), e)
    if isinstance(e, PermissionError) or e.errno in (errno.EACCES,
                                                     errno.EPERM):
        return _fail(item, "izin", t("downloader.no_permission",
                                     folder=folder), e)
    return _fail(item, "disk", t("downloader.write_failed",
                                 name=_fname(item), folder=folder), e)


def _check_space(item, folder, have):
    """Refuse up front rather than fill the disk. A wheel also needs room
    for its unpacked libraries (measured about 1.5x the wheel; 2x keeps
    a margin)."""
    need = item.size - max(have, 0)
    if item.kind == "wheel_libs":
        need += 2 * item.size
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return
    if free < need:
        raise _fail(item, "disk", t("downloader.no_space", name=_fname(item),
                                    need="%d" % (need // _MB + 1),
                                    free="%d" % (free // _MB),
                                    folder=folder),
                    "need=%d free=%d" % (need, free))


class _Reporter:
    """progress(done, total), at most every PROGRESS_INTERVAL seconds; the
    first and last calls always go through."""

    def __init__(self, progress, total):
        self.progress, self.total, self.last = progress, total, None

    def __call__(self, done, force=False):
        if self.progress is None:
            return
        now = time.monotonic()
        if force or self.last is None or \
                now - self.last >= PROGRESS_INTERVAL:
            self.last = now
            self.progress(done, self.total)


def _range_start(header):
    m = re.match(r"bytes\s+(\d+)-", header or "")
    return int(m.group(1)) if m else None


def _transfer(item, part, have, report, cancel):
    """Bring part up to item.size bytes; returns the size reached."""
    folder = os.path.dirname(part)
    for _ in range(2):          # second round: restart from zero
        req = urllib.request.Request(item.url, headers={
            "User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
        if have > 0:
            req.add_header("Range", "bytes=%d-" % have)
        try:
            resp = _opener(item.url).open(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            e.close()       # it holds the connection
            if e.code == 416 and have > 0:      # the .part is not this file
                _remove(part)
                have = 0
                continue
            raise _open_error(item, e)
        except (urllib.error.URLError, OSError, http.client.HTTPException) \
                as e:
            raise _open_error(item, e)
        with resp:
            if have > 0 and resp.status == 206 and \
                    _range_start(resp.headers.get("Content-Range")) != have:
                have = 0        # answered some other range: start over
                continue
            if resp.status != 206:
                have = 0        # 200: the whole file, from byte 0
            return _stream(item, resp, part, have, report, cancel, folder)
    raise _fail(item, "sunucu", t("downloader.no_resume", name=_fname(item)))


def _stream(item, resp, part, have, report, cancel, folder):
    done = have
    report(done, force=True)
    try:
        f = _open_part(part, "ab" if have > 0 else "wb")
    except OSError as e:
        raise _os_error(item, e, folder)
    failure = None
    with f:
        while True:
            if cancel is not None and cancel.is_set():
                failure = _cancelled(item)
                break
            try:
                chunk = resp.read(CHUNK)
            except (socket.timeout, TimeoutError) as e:
                failure = _fail(item, "zamanasimi",
                                t("downloader.stalled", name=_fname(item)), e)
                break
            except (http.client.HTTPException, OSError) as e:
                failure = e     # cut mid-body: reported as "kesildi" below
                break
            if not chunk:
                break
            if done + len(chunk) > item.size:
                failure = "too-big"
                break
            try:
                f.write(chunk)
            except OSError as e:
                failure = _os_error(item, e, folder)
                break
            done += len(chunk)
            report(done)
    if isinstance(failure, DownloadError):
        raise failure
    if failure == "too-big":
        _remove(part)
        raise _fail(item, "bozuk", t("downloader.too_big", name=_fname(item)))
    if done < item.size:
        raise _fail(item, "kesildi", t("downloader.cut",
                                       done="%d" % (done // _MB),
                                       total="%d" % (item.size // _MB),
                                       name=_fname(item)),
                    failure or "short body")
    report(done, force=True)
    return done


def _verify(item, part, cancel):
    """SHA-256 of the finished .part; a mismatch deletes it."""
    h = hashlib.sha256()
    try:
        with _open_part(part, "rb") as f:
            while True:
                if cancel is not None and cancel.is_set():
                    raise _cancelled(item)
                b = f.read(1 << 20)
                if not b:
                    break
                h.update(b)
    except OSError as e:
        raise _os_error(item, e, os.path.dirname(part))
    if h.hexdigest() != item.sha256:
        _remove(part)
        raise _fail(item, "bozuk", t("downloader.bad_sha", name=_fname(item)),
                    "sha256=%s" % h.hexdigest())


def _fetch(item, dest, progress, cancel):
    folder = os.path.dirname(dest)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise _os_error(item, e, folder)
    part = dest + ".part"
    have = _size(part)
    if have > item.size:
        _remove(part)
        have = -1
    _check_space(item, folder, have)
    report = _Reporter(progress, item.size)
    if have < item.size:
        _transfer(item, part, max(have, 0), report, cancel)
    _verify(item, part, cancel)
    try:
        os.replace(part, dest)
    except OSError as e:
        raise _os_error(item, e, folder)


# ----------------------------------------------------------- wheel libs ---

def _marker(whl):
    return os.path.join(os.path.dirname(whl),
                        "." + os.path.basename(whl) + ".tamam")


def _wheel_done(item, whl):
    """The libraries of exactly this wheel are unpacked next to it - the
    only "ok" signal for a wheel_libs item."""
    try:
        with open(_marker(whl), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return False
    folder = os.path.dirname(whl)
    return (isinstance(m, dict) and m.get("sha256") == item.sha256
            and all(os.path.isfile(os.path.join(folder, n))
                    for n in m.get("files", [])))


def _unsafe(member):
    """Absolute, drive-qualified or climbing member names are never
    unpacked - the wheel's hash is checked, but a bad manifest must still
    not write outside gpu/."""
    name = member.replace("\\", "/")
    return (name.startswith("/") or re.match(r"[A-Za-z]:", name) is not None
            or ".." in name.split("/"))


def _extract_libs(item, whl, cancel):
    folder = os.path.dirname(whl)
    bad = None
    try:
        with zipfile.ZipFile(whl) as z:
            infos = z.infolist()
            bad = next((i.filename for i in infos if _unsafe(i.filename)),
                       None)
            picked = {}
            for info in infos if bad is None else ():
                m = _LIB_MEMBER.fullmatch(info.filename)
                if m and _LIB_NAME.fullmatch(m.group(1)):
                    picked[m.group(1)] = info
            # Entry libraries last: never one whose dependencies are not out.
            order = sorted(picked, key=lambda n: (n in _ENTRY_LIBS, n))
            for name in order:
                if cancel is not None and cancel.is_set():
                    raise _cancelled(item)
                # Hidden temporaries: accel's globs (e.g. libnvrtc-builtins
                # .so.12.*) must never match a half-written file.
                tmp = os.path.join(folder, "." + name + ".tmp")
                try:
                    with z.open(picked[name]) as src, \
                            _open_part(tmp, "wb") as dst:
                        shutil.copyfileobj(src, dst, CHUNK)
                    os.replace(tmp, os.path.join(folder, name))
                except BaseException:
                    _remove(tmp)
                    raise
            if bad is None:
                tmp = _marker(whl) + ".tmp"
                try:
                    with _open_part(tmp, "w") as f:
                        json.dump({"sha256": item.sha256,
                                   "files": sorted(picked)}, f)
                    os.replace(tmp, _marker(whl))
                except BaseException:
                    _remove(tmp)
                    raise
    except zipfile.BadZipFile as e:
        _remove(whl)
        raise _fail(item, "bozuk", t("downloader.bad_zip", name=_fname(item)),
                    e)
    except OSError as e:
        raise _os_error(item, e, folder)
    _remove(whl)      # ~GB; its libraries are out and recorded (or refused)
    if bad is not None:
        raise _fail(item, "paket", t("downloader.unsafe_zip",
                                     name=_fname(item)), bad)


# -------------------------------------------------------- process lock ---

class _ProcessLock:
    """Exclusive lock on one lock file, held by an OS lock (not by the
    file's existence), so a crashed holder leaves nothing that blocks."""

    def __init__(self, path):
        self.path = path
        self.fd = None

    def try_acquire(self):
        """True if taken now; False if another process holds it. OSError if
        the lock file cannot be created at all."""
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # The holder unlinks the file on release; if we locked a file
                # that was unlinked meanwhile, the name is not ours: retry.
                if os.fstat(fd).st_ino != os.stat(self.path).st_ino:
                    raise OSError("lock file replaced")
        except OSError:
            os.close(fd)
            return False
        self.fd = fd
        return True

    def release(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        if os.name == "nt":
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            os.close(fd)
            # Fails while another process has it open (it then reuses it).
            _remove(self.path)
        else:
            _remove(self.path)      # while still held: see try_acquire
            os.close(fd)


class _held:
    """with _held(item, dest, progress, cancel): owns <dest>.lock; waits
    (reporting the other process' .part as progress) while another process
    has it. A cancel while waiting raises DownloadCancelled."""

    def __init__(self, item, dest, progress, cancel):
        self.item, self.dest = item, dest
        self.progress, self.cancel = progress, cancel
        self.lock = _ProcessLock(dest + ".lock")

    def __enter__(self):
        folder = os.path.dirname(self.dest)
        report = _Reporter(self.progress, self.item.size)
        said = False
        denied = 0
        while True:
            try:
                os.makedirs(folder, exist_ok=True)
                if self.lock.try_acquire():
                    return self
            except PermissionError as e:
                # Windows: a lock file the holder just deleted stays "delete
                # pending" until its last handle closes, and opening it fails
                # with access denied meanwhile - busy, not forbidden. Only a
                # denial that lasts is a real permission problem.
                if not _is_windows() or denied >= LOCK_DENIED_POLLS:
                    raise _os_error(self.item, e, folder)
                denied += 1
            except OSError as e:
                raise _os_error(self.item, e, folder)
            if not said:
                said = True
                print("[indir] bekleniyor: %s baska bir islemde indiriliyor"
                      % self.item.name, file=sys.stderr)
            if self.cancel is not None and self.cancel.is_set():
                raise _cancelled(self.item)
            report(max(_size(self.dest + ".part"), 0))
            time.sleep(LOCK_POLL)

    def __exit__(self, *exc):
        self.lock.release()
        return False


# ------------------------------------------------------------------ API ---

def _dest(item, root):
    return os.path.join(root, *item.dest_rel.split("/"))


def download(item, root, progress=None, cancel=None):
    """Download one Item under `root`; see the module docstring. Returns
    the file path, or the library folder for a wheel_libs item."""
    dest = _dest(item, root)
    # In place already: no lock file is written (the folder may be
    # read-only, e.g. a portable copy on a locked stick).
    if item_status(item, root) == "ok":
        return os.path.dirname(dest) if item.kind == "wheel_libs" else dest
    with _waited(_lock(os.path.normcase(os.path.abspath(dest))), item,
                 cancel), _held(item, dest, progress, cancel):
        if item.kind == "wheel_libs":
            if _wheel_done(item, dest):
                return os.path.dirname(dest)
            if _size(dest) != item.size:
                _fetch(item, dest, progress, cancel)
            _extract_libs(item, dest, cancel)
            return os.path.dirname(dest)
        if _size(dest) != item.size:
            _fetch(item, dest, progress, cancel)
        return dest


def item_status(item, root):
    """"ok" | "partial" | "missing" for one Item under `root`. A file only
    ever gets its real name after its hash matched, so its size suffices."""
    dest = _dest(item, root)
    if item.kind == "wheel_libs" and _wheel_done(item, dest):
        return "ok"
    if item.kind != "wheel_libs" and _size(dest) == item.size:
        return "ok"
    if _size(dest) >= 0 or _size(dest + ".part") >= 0:
        return "partial"
    return "missing"


def pack_ready(name, libdir):
    """Every wheel of MANIFEST[name] is unpacked into libdir: its marker is
    there and the files it lists exist. accel trusts the GPU pack folder
    only then (a crash mid-unpack leaves a marker missing)."""
    return all(_wheel_done(it, os.path.join(libdir, _fname(it)))
               for it in items(name))


def _root_for(item, create=True):
    """Where an Item goes: model_dir(), persistent. The exceptions are a
    model folder (hf_dir) and a piper voice (piper/...) on Windows when
    model_dir() is not ASCII: CTranslate2 opens its folder with narrow-char
    C APIs there, and the voice is loaded next to espeak-ng, whose C code has
    already killed this process over a non-ASCII path - so both go to paths'
    ASCII folder. Linux paths are UTF-8 bytes to every library, and its
    ASCII fallback (XDG_RUNTIME_DIR, /tmp) is wiped at logout - never used
    for downloads. create=False (status queries) makes no folder."""
    root = paths.model_dir()
    top = item.dest_rel.split("/")[0]
    if ((item.kind == "hf_dir" or top == "piper") and _is_windows()
            and not root.isascii()):
        sub = paths.ascii_dir(top) if create else paths.ascii_path(top)
        return os.path.dirname(sub) if sub else root   # None: no ASCII root
    return root


def local_path(item, create=False):
    """Where `item` is (or would be) on this machine. create=False makes no
    folder - for readers such as the piper backend looking for a voice."""
    return _dest(item, _root_for(item, create))


def _result(its, roots):
    if len(its) == 1 and its[0].kind == "file":
        return _dest(its[0], roots[0])
    return os.path.dirname(_dest(its[0], roots[0]))


def status(name):
    """"ok" | "missing" | "partial" for MANIFEST[name]."""
    states = {item_status(it, _root_for(it, create=False))
              for it in items(name)}
    if states == {"ok"}:
        return "ok"
    if states == {"missing"}:
        return "missing"
    return "partial"


def _download_retrying(item, root, progress, cancel):
    """A dropped connection is tried again from where it stopped, as long as
    each try gets further (flaky Wi-Fi); anything else fails at once."""
    part = _dest(item, root) + ".part"
    for attempt in range(ATTEMPTS):
        before = _size(part)
        try:
            return download(item, root, progress, cancel)
        except DownloadError as e:
            if (e.code not in ("kesildi", "zamanasimi")
                    or attempt == ATTEMPTS - 1 or _size(part) <= before):
                raise


def ensure(name, progress=None, cancel=None):
    """Path of MANIFEST[name], downloading what is missing; None (and
    last_error) if that fails. One progress(done, total) over all files."""
    global last_error
    its = items(name)
    try:
        with _waited(_lock("ensure:" + name), its[0], cancel):
            roots = [_root_for(it) for it in its]
            total = sum(it.size for it in its)
            base = 0
            for it, root in zip(its, roots):
                each = None
                if progress is not None:
                    each = (lambda d, t, b=base: progress(b + d, total))
                _download_retrying(it, root, each, cancel)
                base += it.size
    except DownloadError as e:
        last_error = str(e)
        return None
    except Exception as e:      # a bug must not take the wizard down
        print("[indir] beklenmeyen hata: %s %s"
              % (name, ascii(repr(e))), file=sys.stderr)
        last_error = t("downloader.unexpected", name=name)
        return None
    last_error = None
    return _result(its, roots)
