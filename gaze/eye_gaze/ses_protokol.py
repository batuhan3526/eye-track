# gaze/eye_gaze/ses_protokol.py
"""The pipe protocol between the program and its voice worker (Task 18b, R44).

A packaged build runs piper-tts and espeak-ng (GPL-3.0) in their own small
executable, gozfaresi_ses.py frozen as GozFaresi-Ses.exe / gozfaresi-ses, so
the program's own process - which also holds CTranslate2 with Intel oneMKL,
OpenCV with OpenSSL 1.1.1 and other libraries that are not GPL-compatible -
never loads them. They talk over the worker's stdin/stdout:

  frame = 4-byte big-endian length + header (JSON, UTF-8)
        + 4-byte big-endian length + body (bytes, may be empty)

Requests (header "op"):
  ping                        -> {"ok": true, "pid": ...}
  load  model, espeak_dir,    -> {"ok": true}; loads the piper voice once
        length_scale, volume     (1.0 / 1.0 = piper's own defaults)
  synth text                  -> {"ok": true} + body = a complete WAV file
  modules                     -> {"ok": true, "modules": [...]} (sys.modules,
                                 for the isolation checks of the smokes)
  quit                        -> {"ok": true}, then the worker exits
A failure answers {"ok": false, "error": "<type>: <message>"}; end of file
on stdin (the program closed the pipe or died) ends the worker.

Standard library only: both sides import it, and the worker must not pull
anything of the program in.
"""
import json
import struct

_LEN = struct.Struct(">I")
MAX_HEADER = 1 << 20
MAX_BODY = 256 << 20


class ProtocolError(Exception):
    """A frame that cannot be read (truncated, oversized, not JSON)."""


def _read_exact(stream, n):
    chunks = []
    left = n
    while left:
        part = stream.read(left)
        if not part:
            return b"".join(chunks), False
        chunks.append(part)
        left -= len(part)
    return b"".join(chunks), True


def _read_len(stream, at_start, limit):
    raw, full = _read_exact(stream, _LEN.size)
    if not full:
        if at_start and not raw:
            return None
        raise ProtocolError("cerceve yarida kesildi")
    (n,) = _LEN.unpack(raw)
    if n > limit:
        raise ProtocolError("cerceve cok buyuk (%d bayt)" % n)
    return n


def read_frame(stream):
    """(header dict, body bytes), or None at a clean end of file."""
    n = _read_len(stream, True, MAX_HEADER)
    if n is None:
        return None
    raw, full = _read_exact(stream, n)
    if not full:
        raise ProtocolError("cerceve yarida kesildi")
    try:
        header = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ProtocolError("baslik JSON degil: %s" % exc)
    if not isinstance(header, dict):
        raise ProtocolError("baslik bir sozluk degil")
    m = _read_len(stream, False, MAX_BODY)
    body, full = _read_exact(stream, m)
    if not full:
        raise ProtocolError("cerceve yarida kesildi")
    return header, body


def write_frame(stream, header, body=b""):
    """One frame, flushed. The header is sent ASCII-safe (ensure_ascii)."""
    raw = json.dumps(header, ensure_ascii=True).encode("ascii")
    body = bytes(body or b"")
    stream.write(_LEN.pack(len(raw)) + raw + _LEN.pack(len(body)) + body)
    stream.flush()
