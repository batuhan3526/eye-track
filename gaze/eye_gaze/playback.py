# gaze/eye_gaze/playback.py
"""The process's one audio channel: stoppable speech, earcons that never cut it.

PlaySound has exactly ONE channel per process - starting a sound replaces the
one that is playing. Two consequences shape this module:

* Speech has to be STOPPABLE (the speech_stop_key). winsound.PlaySound with
  SND_MEMORY is synchronous and cannot be interrupted from another thread:
  measured on the development machine, PlaySound(None, 0) called from a second thread
  simply blocked until the 3 s sound had played out. Python refuses
  SND_MEMORY|SND_ASYNC (it cannot keep the buffer alive), so the async call is
  made through ctypes here and this module keeps the buffer alive itself. The
  playing thread then waits on an Event for the sound's length; stop() sets
  the Event and silences the device.

* An earcon must never cut a sentence. Everything that sounds holds one lock;
  speech waits for it (an earcon is a tenth of a second), earcons use
  try_play() and are simply DROPPED when the channel is busy - a beep that
  waited would arrive late and mean nothing.

Nothing here may take the gaze loop down: every device call is guarded, and a
machine with no async audio falls back to the old blocking PlaySound.

On Linux the device is PortAudio through sounddevice (_SoundDeviceBackend):
one process-wide output that the speech/earcon player and the alarm share,
with PlaySound's "a new sound replaces the one playing" rule kept, so the
hold/release logic below works unchanged. No audio device is not an error
that may stop anything: it is said once and every sound is then silent.
"""
import ctypes
import os
import struct
import sys
import threading
import time

from eye_gaze.i18n import t as _t

_SND_ASYNC = 0x0001
_SND_NODEFAULT = 0x0002
_SND_MEMORY = 0x0004


def wav_duration(wav):
    """Length in seconds of a RIFF/WAVE byte string, or None if unreadable.

    Parsed by hand rather than with the wave module: wave in Python 3.11
    refuses WAVE_FORMAT_EXTENSIBLE headers, and only the byte rate and the
    data size are needed. Never raises.
    """
    try:
        b = bytes(wav)
        if len(b) < 12 or b[:4] != b"RIFF" or b[8:12] != b"WAVE":
            return None
        pos, byte_rate = 12, None
        while pos + 8 <= len(b):
            cid = b[pos:pos + 4]
            size = struct.unpack_from("<I", b, pos + 4)[0]
            body = pos + 8
            if cid == b"fmt " and size >= 16 and body + 12 <= len(b):
                byte_rate = struct.unpack_from("<I", b, body + 8)[0]
            elif cid == b"data":
                if not byte_rate:
                    return None
                # Streaming writers leave 0xFFFFFFFF here; trust the bytes.
                return min(size, len(b) - body) / float(byte_rate)
            pos = body + size + (size & 1)
        return None
    except Exception:
        return None


def _blocking_play(wav):
    """The old path: synchronous, not stoppable, but works everywhere."""
    import winsound
    winsound.PlaySound(wav, winsound.SND_MEMORY)


def wav_pcm(wav):
    """(samples, rate) of a PCM / float RIFF WAVE byte string, samples a
    numpy array shaped (frames, channels) of int16 or float32; None if it is
    not one. Hand-parsed for the reason wav_duration() is. Never raises."""
    try:
        import numpy as np
        b = bytes(wav)
        if len(b) < 12 or b[:4] != b"RIFF" or b[8:12] != b"WAVE":
            return None
        pos, fmt = 12, None
        while pos + 8 <= len(b):
            cid = b[pos:pos + 4]
            size = struct.unpack_from("<I", b, pos + 4)[0]
            body = pos + 8
            if cid == b"fmt " and size >= 16 and body + 16 <= len(b):
                tag, ch, rate = struct.unpack_from("<HHI", b, body)
                bits = struct.unpack_from("<H", b, body + 14)[0]
                if tag == 0xFFFE and size >= 40 and body + 26 <= len(b):
                    tag = struct.unpack_from("<H", b, body + 24)[0]
                fmt = (tag, ch, rate, bits)
            elif cid == b"data":
                if fmt is None:
                    return None
                tag, ch, rate, bits = fmt
                raw = b[body:body + min(size, len(b) - body)]
                if tag == 1 and bits == 16:
                    x = np.frombuffer(raw[:len(raw) // 2 * 2], dtype="<i2")
                elif tag == 1 and bits == 8:
                    x = ((np.frombuffer(raw, dtype=np.uint8).astype(np.int16)
                          - 128) << 8)
                elif tag == 1 and bits == 32:
                    x = (np.frombuffer(raw[:len(raw) // 4 * 4], dtype="<i4")
                         >> 16).astype(np.int16)
                elif tag == 3 and bits == 32:
                    x = np.frombuffer(raw[:len(raw) // 4 * 4], dtype="<f4")
                else:
                    return None
                if ch < 1 or rate <= 0:
                    return None
                x = x[:len(x) // ch * ch].reshape(-1, ch)
                return np.ascontiguousarray(x), int(rate)
            pos = body + size + (size & 1)
        return None
    except Exception:
        return None


def _import_sounddevice():
    """The sounddevice module (importing it initialises PortAudio). A seam:
    the test suite replaces it so no test opens a real audio device."""
    import sounddevice
    return sounddevice


def _console(message):
    """One console line, ASCII (cp1254 console). Never raises."""
    try:
        print(message.encode("ascii", "replace").decode("ascii"))
    except Exception:
        pass


class _SoundDeviceBackend:
    """PortAudio output (sounddevice), with _WinmmBackend's contract:
    start(wav) -> bool returns at once while the sound plays on PortAudio's
    thread; a new start() replaces what is playing; stop() is immediate.

    stop() ABORTS the stream - Pa_AbortStream drops the buffers already
    queued, where a plain stop would let them play out first (a stopped
    siren that keeps sounding for a buffer's length is not stopped). One
    stream per sound: opened by start(), closed by the next start() or
    stop(). PortAudio failures (no audio device, a device that vanished)
    return False and are said ONCE on the console, with what they cost:
    no speech, no earcons and - which the caregiver must know - no alarm
    siren. audio_status() then reports it for capabilities()."""

    def __init__(self, sd=None, log=_console):
        self._sd = sd
        self._log = log
        self._lock = threading.Lock()
        self._stream = None
        self._said = False
        self._import_error = None
        self.error = None

    def _module(self):
        """sounddevice, imported once. A failed import (no PortAudio library,
        an ImportError) is remembered for good and re-raised: the library
        cannot appear while we run, and every retry would re-run PortAudio's
        loader - the alarm alone would do that each cycle. A missing DEVICE
        is different: start() tries it again each time it is asked."""
        if self._sd is None:
            if self._import_error is not None:
                # Its old traceback dropped: re-raised as it was, the one
                # stored exception grew by a level per sound, each holding
                # start()'s frame and the sound's PCM (final review M-10).
                raise self._import_error.with_traceback(None)
            try:
                self._sd = _import_sounddevice()
            except Exception as exc:
                self._import_error = exc
                raise
        return self._sd

    def _fail(self, exc):
        self.error = "%s: %s" % (type(exc).__name__, exc)
        if not self._said:
            self._said = True
            self._log("[ses] UYARI: ses aygiti acilamadi (%s) - konusma,"
                      " tik sesleri ve ACIL ALARM SIRENI DUYULMAYACAK."
                      " Hoparlor/ses kartini kontrol edin." % self.error)

    def start(self, wav):
        pcm = wav_pcm(wav)
        if pcm is None:
            return False
        data, rate = pcm
        with self._lock:
            self._close_locked()
            try:
                sd = self._module()
                pos = [0]

                def callback(out, frames, _time, _status):
                    n = min(frames, len(data) - pos[0])
                    out[:n] = data[pos[0]:pos[0] + n]
                    pos[0] += n
                    if n < frames:
                        out[n:] = 0
                        raise sd.CallbackStop()

                stream = sd.OutputStream(samplerate=rate,
                                         channels=data.shape[1],
                                         dtype=data.dtype.name,
                                         callback=callback)
                stream.start()
            except Exception as exc:
                self._fail(exc)
                return False
            self._stream = stream
            self.error = None
            return True

    def stop(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        s, self._stream = self._stream, None
        if s is None:
            return
        try:
            s.abort()
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass

    def tone(self, freq, ms, rate=22050):
        """A sine for `ms` milliseconds, blocking (the alarm's Beep stand-in
        on its own thread). False at once when it cannot sound."""
        import numpy as np
        t = np.arange(int(rate * ms / 1000.0)) / float(rate)
        y = (np.sin(2 * np.pi * float(freq) * t) * 0.9 * 32767).astype("<i2")
        hdr = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + 2 * len(y),
                          b"WAVE", b"fmt ", 16, 1, 1, rate, rate * 2, 2, 16,
                          b"data", 2 * len(y))
        if not self.start(hdr + y.tobytes()):
            return False
        time.sleep(ms / 1000.0)
        return True

    def probe(self):
        """(ok, ASCII reason): is there a default output device? Imports
        PortAudio - call it off every loop. A failure is said (once)."""
        # Under the lock: PortAudio is not thread-safe, and a start() or
        # stop() may run at the same moment on the alarm or speech thread.
        with self._lock:
            try:
                sd = self._module()
                sd.query_devices(kind="output")
            except Exception as exc:
                self._fail(exc)
                return False, self.error
        return True, ""


_LINUX = os.name != "nt"     # tests flip it to take the other path
_SD_BACKEND = None
_SD_LOCK = threading.Lock()


def audio_backend():
    """Linux: the ONE process-wide PortAudio output, shared by the speech /
    earcon player and the alarm - two would each think they owned it."""
    global _SD_BACKEND
    with _SD_LOCK:
        if _SD_BACKEND is None:
            _SD_BACKEND = _SoundDeviceBackend()
        return _SD_BACKEND


def audio_status():
    """(ok, Turkish reason) for platform.capabilities() on Linux. Cheap: it
    reports what playback has already found, it never opens a device."""
    be = _SD_BACKEND
    if be is not None and be.error:
        return False, _t("playback.device_failed", error=be.error)
    if "sounddevice" not in sys.modules:
        import importlib.util
        try:
            found = importlib.util.find_spec("sounddevice") is not None
        except Exception:
            found = False
        if not found:
            return False, _t("playback.no_sounddevice")
    return True, "PortAudio (sounddevice)"


def _no_sound(wav):
    """Linux's fallback when the device would not start: nothing to fall
    back to. The backend has already said why (once)."""
    return None


def _default_backend():
    if not _LINUX:
        return _WinmmBackend()
    return audio_backend()


def _default_fallback():
    return _no_sound if _LINUX else _blocking_play


class _WinmmBackend:
    """PlaySoundW(SND_MEMORY | SND_ASYNC) through ctypes.

    The buffer is kept referenced until the NEXT sound has replaced it:
    winmm reads it while playing, and freeing it under the device would be a
    use-after-free inside the audio stack.
    """

    def __init__(self):
        self._fn = None
        self._tried = False
        self._buf = None

    def _api(self):
        if not self._tried:
            self._tried = True
            try:
                from ctypes import wintypes
                fn = ctypes.windll.winmm.PlaySoundW
                fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
                fn.restype = wintypes.BOOL
                self._fn = fn
            except Exception:
                self._fn = None
        return self._fn

    def start(self, wav):
        fn = self._api()
        if fn is None:
            return False
        buf = ctypes.create_string_buffer(bytes(wav), len(wav))
        ok = fn(ctypes.addressof(buf), None,
                _SND_MEMORY | _SND_ASYNC | _SND_NODEFAULT)
        if ok:
            self._buf = buf
        return bool(ok)

    def stop(self):
        fn = self._api()
        if fn is not None:
            fn(None, None, 0)


class WavPlayer:
    """One audio channel, shared by speech (play) and earcons (try_play)."""

    def __init__(self, backend=None, fallback=None, tail=0.15):
        self._backend = backend if backend is not None else _default_backend()
        self._fallback = (fallback if fallback is not None
                          else _default_fallback())
        # PlaySound returns once the sound is QUEUED, so the header length
        # ends a little before the speaker does. Without the tail the next
        # sound (an earcon) would clip the last syllable.
        self._tail = float(tail)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._held = threading.Event()
        # Bumped by every stop(). The Event alone is not enough: a sentence
        # blocked on the lock behind an earcon would find it already used
        # up by the earcon's wait, clear it and play in full.
        self._stops = 0

    @property
    def busy(self):
        """True while something is sounding (or about to)."""
        return self._lock.locked()

    # --- the emergency alarm owns the channel while it rings -----------------
    # The alarm plays through winsound on this same per-process channel, so
    # any earcon (every click beeps) or sentence would cut the siren off until
    # its next cycle. While held, nothing here starts a sound, and stop() no
    # longer sends PlaySound(NULL) - that would silence the siren, not speech.

    def hold(self):
        """Give the channel to the alarm. Never blocks, never raises. A
        sentence already playing stops waiting (the siren has replaced it on
        the device anyway) without silencing the device."""
        self._held.set()
        self._stop.set()

    def release(self):
        self._held.clear()

    @property
    def held(self):
        return self._held.is_set()

    def play(self, wav):
        """Play and block until finished (True) or stopped (False). Speech
        uses this from its worker thread, never from the gaze loop. False
        at once, nothing heard, while the alarm holds the channel."""
        if self._held.is_set():
            return False
        asked = self._stops
        with self._lock:
            if self._stops != asked:
                return False            # stopped while waiting for the channel
            return self._sound(wav)

    def try_play(self, wav):
        """Play only if the channel is free; False = skipped, nothing heard."""
        if self._held.is_set():
            return False
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self._sound(wav)
            return True
        finally:
            self._lock.release()

    def stop(self):
        """Silence whatever is playing now, and any sentence already waiting
        for the channel. Safe from any thread, any time."""
        self._stops += 1
        self._stop.set()

    def _sound(self, wav):
        # Cleared here, inside the lock: a stop() pressed while NOTHING was
        # playing must not swallow the next sentence the user asks for.
        self._stop.clear()
        if self._held.is_set():
            return False                # the alarm took the channel meanwhile
        dur = wav_duration(wav)
        started = False
        if dur is not None:
            try:
                started = bool(self._backend.start(wav))
            except Exception:
                started = False
        if not started:
            # Better a sentence that cannot be stopped than no sentence.
            self._fallback(wav)
            return True
        if self._stop.wait(dur + self._tail):
            if not self._held.is_set():
                try:
                    self._backend.stop()
                except Exception:
                    pass
            return False
        return True


_SHARED = None
_SHARED_LOCK = threading.Lock()


def shared_player():
    """The process-wide player. Speech and earcons MUST share it - two
    players would each think the channel was free."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = WavPlayer()
        return _SHARED
