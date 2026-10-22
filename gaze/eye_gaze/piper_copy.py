# gaze/eye_gaze/piper_copy.py
"""The ASCII copy of the packaged piper voice, for a frozen Windows build.

espeak-ng's C code on Windows cannot open a non-ASCII data folder: it falls
back to the path compiled into the wheel and aborts the process - nothing
catchable. A build installed under C:\\Users\\Örnek therefore copies its
voice (the .onnx files and espeak-ng-data) once into paths.ascii_dir("piper").

  ascii_voice_copy(src) -> folder
      ascii_dir("piper")/<stamp>, where <stamp> is a short hash of every
      source file's relative path and size plus the SHA-256 of espeak's two
      binary tables (phontab, phondata). An update that ships different
      espeak data gets a new stamp, so a new folder: a stale copy is never
      reused. An existing folder is trusted only after its files have the
      source sizes and its phontab/phondata hash-match the source - a folder
      somebody else planted under the shared C:\\Users\\Public root, or one
      damaged since, is passed over for a sibling <stamp>-1, -2, ... Nothing
      already there is ever deleted or overwritten.
      Raises RuntimeError (ASCII text): select_backend() then moves on to
      the next voice.

The copy is atomic: espeak-ng-data is written into espeak-ng-data.tmp,
phontab last, and renamed into place only when complete; voice files go
through <name>.tmp + os.replace. A copy cut short (power cut, full disk)
therefore never looks complete and is redone at the next start.

Two processes (the launcher and the driver) may start at once: the copy is
done under the downloader's cross-process lock, waited for at most
COPY_LOCK_WAIT seconds - the voice is built on the driver's start-up path,
so a long wait would hold up the cursor; the next voice is used instead and
piper comes up at the next start.
"""
import hashlib
import os
import shutil
import time

from . import paths

ESPEAK_DATA = "espeak-ng-data"
PHONTAB = "phontab"
# espeak's binary tables: a fault in them kills the process, so their
# content - not just their size - decides whether a copy is trusted.
CHECKED = (PHONTAB, "phondata")
COPY_LOCK_WAIT = 3.0        # s; see the module docstring
_LOCK_POLL = 0.1
_MAX_SIBLINGS = 20


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _inventory(src):
    """{relative path with "/": size} of every file under src."""
    out = {}
    for root, dirs, files in os.walk(src):
        dirs.sort()
        for name in files:
            p = os.path.join(root, name)
            out[os.path.relpath(p, src).replace(os.sep, "/")] = \
                os.path.getsize(p)
    return out


def _source(src):
    """(inventory, {table: sha256}, stamp) of the packaged voice. Raises
    RuntimeError when it is missing or incomplete."""
    try:
        inv = _inventory(src) if os.path.isdir(src) else {}
        sums = {n: _sha256(os.path.join(src, ESPEAK_DATA, n))
                for n in CHECKED}
    except OSError:
        inv, sums = {}, None
    if not sums or not any(r.endswith(".onnx") and "/" not in r for r in inv):
        raise RuntimeError("pakette piper sesi yok ya da eksik: %s"
                           % ascii(src))
    h = hashlib.sha256()
    for rel in sorted(inv):
        h.update(("%s\0%d\n" % (rel, inv[rel])).encode("utf-8"))
    for name in CHECKED:
        h.update(("%s\0%s\n" % (name, sums[name])).encode("ascii"))
    return inv, sums, h.hexdigest()[:12]


def _verified(dst, inv, sums):
    """dst holds every source file at its source size, and espeak's tables
    hash-match the source."""
    try:
        for rel, size in inv.items():
            if os.path.getsize(os.path.join(dst, *rel.split("/"))) != size:
                return False
        return all(_sha256(os.path.join(dst, ESPEAK_DATA, n)) == s
                   for n, s in sums.items())
    except OSError:
        return False


def _resumable(dst, inv):
    """A folder our own earlier copy left unfinished: no espeak-ng-data yet
    (that only ever arrives by one rename, complete) and nothing in it but
    voice files of their source size and our .tmp leftovers. Anything else
    is not ours, and is left alone."""
    if not os.path.isdir(dst) or os.path.lexists(os.path.join(dst,
                                                               ESPEAK_DATA)):
        return False
    try:
        for name in os.listdir(dst):
            if name.endswith(".tmp"):
                continue
            p = os.path.join(dst, name)
            if not os.path.isfile(p) or inv.get(name) != os.path.getsize(p):
                return False
    except OSError:
        return False
    return True


def _copy_file(src, dst):
    """Every byte the copy writes goes through here (tests count and break
    it)."""
    shutil.copyfile(src, dst)


def _copy_in(src, dst):
    """src -> dst through dst + ".tmp" and os.replace: a cut-short copy never
    leaves a half file under the real name."""
    tmp = dst + ".tmp"
    try:
        _copy_file(src, tmp)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _copy_tree(src, dst):
    """Every file of src into the new folder dst; phontab (top level) last:
    it is what every check takes to mean "the espeak data is complete"."""
    for root, dirs, files in os.walk(src):
        dirs.sort()
        rel = os.path.relpath(root, src)
        out = dst if rel == os.curdir else os.path.join(dst, rel)
        os.makedirs(out, exist_ok=True)
        for name in sorted(files):
            if rel == os.curdir and name == PHONTAB:
                continue
            _copy_in(os.path.join(root, name), os.path.join(out, name))
    _copy_in(os.path.join(src, PHONTAB), os.path.join(dst, PHONTAB))


def _copy_into(dst, src, inv, sums):
    os.makedirs(dst, exist_ok=True)
    for rel in sorted(inv):
        if "/" in rel:
            continue                        # espeak data: below, at once
        target = os.path.join(dst, rel)
        if not os.path.exists(target):
            _copy_in(os.path.join(src, rel), target)
    final = os.path.join(dst, ESPEAK_DATA)
    tmp = final + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)  # a cut-short copy; ours (locked)
    _copy_tree(os.path.join(src, ESPEAK_DATA), tmp)
    os.rename(tmp, final)
    if not _verified(dst, inv, sums):
        raise OSError("kopya dogrulanamadi: %s" % ascii(dst))


class _CopyLock:
    """The downloader's cross-process lock, waited for at most
    COPY_LOCK_WAIT seconds."""

    def __init__(self, folder):
        from .downloader import _ProcessLock
        self._path = os.path.join(folder, ".kopya.lock")
        self._lock = _ProcessLock(self._path)

    def __enter__(self):
        deadline = time.monotonic() + COPY_LOCK_WAIT
        while True:
            try:
                if self._lock.try_acquire():
                    return self
            except PermissionError:
                # Windows: a lock file its holder just deleted is "delete
                # pending" for a moment - busy, not forbidden. A denial that
                # lasts ends at the deadline below.
                pass
            except OSError as e:
                raise RuntimeError("piper sesi kopyalanamadi (%s): %s"
                                   % (type(e).__name__, ascii(self._path)))
            if time.monotonic() >= deadline:
                raise RuntimeError("piper sesi baska bir islemde kopyalaniyor"
                                   " - bu acilista yedek ses kullaniliyor,"
                                   " piper bir sonraki acilista gelir: %s"
                                   % ascii(self._path))
            time.sleep(_LOCK_POLL)

    def __exit__(self, *exc):
        self._lock.release()
        return False


def _candidates(root, stamp):
    yield os.path.join(root, stamp)
    for i in range(1, _MAX_SIBLINGS):
        yield os.path.join(root, "%s-%d" % (stamp, i))


def ascii_voice_copy(src):
    """See the module docstring."""
    inv, sums, stamp = _source(src)     # before any folder is created
    root = paths.ascii_dir("piper")
    if not root.isascii():
        # ascii_dir found no ASCII root at all and returned the normal one.
        raise RuntimeError("piper sesi icin ASCII klasor bulunamadi: %s"
                           % ascii(root))
    for cand in _candidates(root, stamp):          # usual case: no lock
        if not os.path.lexists(cand):
            break
        if _verified(cand, inv, sums):
            return cand
    with _CopyLock(root):
        for cand in _candidates(root, stamp):
            if _verified(cand, inv, sums):
                return cand                 # another process finished it
            if os.path.lexists(cand) and not _resumable(cand, inv):
                continue                    # not ours, or damaged: kept
            try:
                _copy_into(cand, src, inv, sums)
            except OSError as e:
                raise RuntimeError("piper sesi ASCII klasore kopyalanamadi"
                                   " (%s): %s" % (type(e).__name__,
                                                  ascii(cand)))
            return cand
    raise RuntimeError("piper sesi icin kullanilabilir klasor kalmadi: %s"
                       % ascii(root))
