# gaze/eye_gaze/speech.py
"""Speech output + clipboard helpers, so the driver can talk without OptiKey.

Backends live in tts_backends.py and only produce WAV bytes; this module owns
the queue and the playback. Synthesis runs on a daemon thread, so speak()
never blocks the gaze loop. synth() is the synchronous door used by the AAC
phrase cache.
"""
import ctypes
import os
import queue
import threading
import time

# Nothing in this module plays through winsound any more (playback.py does,
# importing it where it is used); the name stays as an attribute for callers
# or tests that patch speech.winsound. Guarded because winsound only exists
# on Windows: unguarded, this module - and everything importing it - could
# not even be imported on Linux.
try:
    import winsound  # noqa: F401
except ImportError:
    winsound = None

from eye_gaze.playback import shared_player
from eye_gaze.tts_backends import (
    KNOWN,
    WRONG_LANGUAGE_NOTE,
    last_errors,
    last_request,
    select_backend,
    speaks_itself,
)

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


_WIN32 = None


def _win32():
    """(user32, kernel32) with every prototype DECLARED, or (None, None).

    This matters more than it looks. With no argtypes/restype, ctypes treats a
    return value as a C int - 32 bits - and Windows hands out clipboard and
    global handles well above 4 GB on the development machine (measured: 0x1c1849e0008).
    Every handle was therefore being TRUNCATED:

      * GetClipboardData came back 0, so F4 said "seslendirilecek metin yok"
        with perfectly good text sitting on the clipboard;
      * when the truncated value was not 0 it was a pointer into unmapped
        memory, and reading it raised an access violation that killed the
        WHOLE driver - the cursor with it, which for this user is the one
        thing that must never happen.

    Cached: the declarations are process-wide and only need doing once.
    """
    global _WIN32
    if _WIN32 is not None:
        return _WIN32
    try:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        k.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        k.GlobalAlloc.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = [ctypes.c_void_p]
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]
        k.GlobalUnlock.restype = ctypes.c_int
        k.GlobalSize.argtypes = [ctypes.c_void_p]
        k.GlobalSize.restype = ctypes.c_size_t
        k.GlobalFree.argtypes = [ctypes.c_void_p]
        k.GlobalFree.restype = ctypes.c_void_p
        u.GetClipboardSequenceNumber.argtypes = []
        u.GetClipboardSequenceNumber.restype = ctypes.c_uint
        u.OpenClipboard.argtypes = [ctypes.c_void_p]
        u.OpenClipboard.restype = ctypes.c_int
        u.CloseClipboard.argtypes = []
        u.CloseClipboard.restype = ctypes.c_int
        u.EmptyClipboard.argtypes = []
        u.EmptyClipboard.restype = ctypes.c_int
        u.GetClipboardData.argtypes = [ctypes.c_uint]
        u.GetClipboardData.restype = ctypes.c_void_p
        u.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
        u.SetClipboardData.restype = ctypes.c_void_p
    except Exception:
        _WIN32 = (None, None)
        return _WIN32
    _WIN32 = (u, k)
    return _WIN32


_CLIP_PROBLEM = ""


def clipboard_problem():
    """Why the last get_clipboard_text() came back empty ('' = it did not).

    Without this the F4 handler can only say "once metni SECIN" - blaming the
    user for not selecting anything - when the real answer may be that another
    program was holding the clipboard. That message sends someone who cannot
    easily repeat an action off to redo the one thing they already did right.
    """
    return _CLIP_PROBLEM


def _read_clipboard_once(u, k):
    """One attempt at the clipboard text. '' means "not this time"."""
    global _CLIP_PROBLEM
    if not u.OpenClipboard(None):
        _CLIP_PROBLEM = "pano baska bir program tarafindan tutuluyor"
        return ""
    try:
        h = u.GetClipboardData(_CF_UNICODETEXT)
        if not h:
            _CLIP_PROBLEM = "panoda metin yok"
            return ""
        p = k.GlobalLock(h)
        if not p:
            _CLIP_PROBLEM = "pano verisi kilitlenemedi"
            return ""
        try:
            # Bounded by the block's own size rather than trusting a NUL: the
            # buffer comes from whichever program last copied something, and a
            # missing terminator would walk this read off the end of the block.
            n = int(k.GlobalSize(h)) // 2
            if n <= 0:
                return ""
            return ctypes.wstring_at(p, n).split("\x00", 1)[0]
        finally:
            k.GlobalUnlock(h)
    finally:
        try:
            u.CloseClipboard()
        except Exception:
            pass


def get_clipboard_text():
    """Current clipboard text ('' when empty/unavailable). NEVER raises.

    Retries the WHOLE read, not just the open. Measured on the development machine:
    OpenClipboard succeeds and GetClipboardData still comes back NULL for a
    moment right after something else writes to the clipboard - Windows'
    clipboard history and cloud-clipboard both do this. Retrying only the open
    made F4 answer "seslendirilecek metin yok" with the text sitting right
    there; the next press, a second later, worked. Nothing looked broken, so
    the user just learns the button is unreliable.

    Callers are the F4 handler inside the gaze loop and the AAC grid; an
    exception escaping here stops the cursor, so nothing may propagate.
    """
    global _CLIP_PROBLEM
    u, k = _win32()
    if u is None:
        if _LINUX:
            return _linux_get()
        _CLIP_PROBLEM = "Windows pano arabirimi acilamadi"
        return ""
    _CLIP_PROBLEM = ""
    for attempt in range(10):
        try:
            text = _read_clipboard_once(u, k)
        except Exception as exc:
            _CLIP_PROBLEM = "pano okunamadi (%s)" % type(exc).__name__
            text = ""
        if text:
            _CLIP_PROBLEM = ""
            return text
        if attempt < 9:
            # 40 ms x 10 = 0.4 s worst case, and only on the failing path. F4 is
            # a deliberate press; waiting is cheaper than telling this user to
            # go and select the text they already selected.
            time.sleep(0.04)
    return ""


def set_clipboard_text(text):
    """Put text on the clipboard; returns True on success. NEVER raises."""
    u, k = _win32()
    if u is None:
        if _LINUX:
            return _linux_set(text)
        return False
    h = None
    owned = False
    try:
        # Sized from the UTF-16 buffer itself: an emoji is ONE Python char
        # but TWO UTF-16 units, so (len + 1) * 2 cut the end of the text and
        # left the block without its NUL terminator.
        buf = ctypes.create_unicode_buffer(str(text))
        n = ctypes.sizeof(buf)
        h = k.GlobalAlloc(_GMEM_MOVEABLE, n)
        if not h:
            return False
        p = k.GlobalLock(h)
        if not p:
            return False
        ctypes.memmove(p, buf, n)
        k.GlobalUnlock(h)
        # Retried for the same reason the read side is: clipboard history and
        # cloud clipboard hold the clipboard for a moment after every write.
        opened = False
        for attempt in range(10):
            if u.OpenClipboard(None):
                opened = True
                break
            if attempt < 9:
                time.sleep(0.04)
        if not opened:
            return False
        try:
            u.EmptyClipboard()
            # SetClipboardData takes ownership of h on success - and ONLY then.
            if u.SetClipboardData(_CF_UNICODETEXT, h):
                owned = True
                return True
            return False
        finally:
            u.CloseClipboard()
    except Exception:
        return False
    finally:
        # Every other exit leaves the block ours; dictation runs this per
        # sentence, so a leak here grows for the life of the process.
        if h and not owned:
            try:
                k.GlobalFree(h)
            except Exception:
                pass


def clipboard_sequence():
    """Windows' clipboard change counter, or None. NEVER raises.

    The F4 handler reads it before and after sending Ctrl+C: unchanged means
    nothing was copied, and reading the clipboard anyway would speak whatever
    sentence happened to be on it from before. On Linux a counter derived
    from the content (linux_misc.clipboard_seq).
    """
    u, _ = _win32()
    if u is None:
        if _LINUX:
            return _linux_seq()
        return None
    try:
        return int(u.GetClipboardSequenceNumber())
    except Exception:
        return None


# --- Linux: the same three doors, through platform.linux_misc ----------------
# Taken only when the Win32 API is missing (_win32() -> (None, None)), so the
# tests that hand _win32 a fake keep exercising the Windows code everywhere.

_LINUX = os.name != "nt"


def _linux_get():
    global _CLIP_PROBLEM
    try:
        from eye_gaze.platform import linux_misc
        text = linux_misc.clipboard_get()
        if text is None:
            _CLIP_PROBLEM = linux_misc.clipboard_problem() or "pano okunamadi"
            return ""
        _CLIP_PROBLEM = "" if text else "panoda metin yok"
        return text
    except Exception as exc:
        _CLIP_PROBLEM = "pano okunamadi (%s)" % type(exc).__name__
        return ""


def _linux_set(text):
    try:
        from eye_gaze.platform import linux_misc
        return bool(linux_misc.clipboard_set(text))
    except Exception:
        return False


def _linux_seq():
    try:
        from eye_gaze.platform import linux_misc
        return int(linux_misc.clipboard_seq())
    except Exception:
        return None


def _play(wav):
    """Play WAV bytes. Module-level so tests can replace it.

    Through the process-wide player, not winsound directly: a synchronous
    PlaySound cannot be stopped from another thread (speech_stop_key), and
    the earcons share this one channel - see playback.py.
    """
    p = shared_player()
    if getattr(p, "held", False):
        # The alarm owns the channel; the sentence is dropped. Saying so
        # beats a console that logged "AAC: ..." as if it had been heard.
        _report("ALARM CALIYOR - cumle SOYLENMEDI. Alarm durunca tekrar"
                " soyletmek icin konusma durdurma tusuna iki kez basin.")
        return
    p.play(wav)


def _stop_playback():
    """Silence the sentence playing now. Module-level so tests can replace it."""
    shared_player().stop()


def _report(message):
    """Announce a speech failure on the console (Turkish text, ASCII only).

    Module-level like _play so tests can capture it. Never raises: a broken
    stdout must not take down the worker thread it is reporting for.
    """
    try:
        print("[ses] " + message)
    except Exception:
        pass


def _ascii(text):
    """Console-safe form of `text`.

    The console here is cp1254 and a UnicodeEncodeError raised while REPORTING
    a failure would hide that failure - which is the exact bug startup_report()
    exists to end. Voice names and exception texts both come from outside.
    """
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def startup_report(speaker):
    """Console lines (Turkish, ASCII) naming the TTS engine that came up - or,
    when none did, why. Returned rather than printed so the driver owns stdout
    and this stays testable.

    Silence is the bug being paid off here. Before this, a Speaker that could
    select no backend at all was still built and stored, and the only symptom
    was that F4 did nothing - forever, with no line anywhere naming a reason.
    Every construction-time guard in tts_backends.py (the espeak phontab
    sentinel, the ASCII path checks, the .onnx.json sidecar, the xtts reference
    recording) refuses on the promise that its refusal surfaces HERE. Without
    that, those guards cost the user speech and return no diagnosis.

    Reads the diagnostics captured on the Speaker, not the module-level
    last_errors(), so a second Speaker built later cannot rewrite this one's
    reasons.
    """
    engine = _ascii(getattr(speaker, "engine", "") or "")
    errors = dict(getattr(speaker, "errors", None) or {})
    request = _ascii(getattr(speaker, "request", "") or "auto")
    # A tts_engine value that is neither "auto" nor a known engine is treated
    # as auto by select_backend(). Silently: so "xtts2" or "XTTS v2" alongside
    # a filled-in tts_speaker_wav runs auto, wins with piper, never validates
    # the reference recording - every Task 5 guard bypassed - and reports
    # success. The typo has to be named.
    unknown = []
    if request != "auto" and request not in KNOWN:
        unknown = ["UYARI: tts_engine degeri taninmadi: '" + request + "' -"
                   " auto olarak calisildi.",
                   "  Gecerli degerler: auto | " + " | ".join(KNOWN) + "."
                   " auto modunda ses klonlama (tts_speaker_wav) KULLANILMAZ."]
    # Where the user can get a missing voice: KUR-SES.bat only exists in this
    # machine's source checkout, Tolga only on Windows.
    from eye_gaze import tts_backends as _tb
    windows = _tb._is_windows()
    hint = _tb.install_hint()
    kur_ses = hint == "KUR-SES.bat calistirin"
    if getattr(speaker, "ok", False):
        lines = ["Seslendirme motoru: " + engine] + unknown
        if engine.startswith("spd/"):
            lines.append("  NOT: ses speech-dispatcher (spd-say) ile"
                         " soyleniyor: AAC cumleleri onceden hazirlanamaz,"
                         " alarmda yalniz siren calar; durdurma tusu tum"
                         " konusmayi iptal eder.")
        # An engine that came up in the WRONG language is not success: this
        # machine has no Turkish SAPI voice at all, so a fall-through there
        # means Turkish text read aloud with an English accent.
        if WRONG_LANGUAGE_NOTE.strip() in engine:
            lines.append("UYARI: bu ses istenen DILDE DEGIL - Turkce metin"
                         " yabanci aksanla okunur.")
            if not windows:
                lines.append("  Turkce icin: tts_engine: piper +"
                             " tts_voice: tr_TR-dfki-medium (" + hint + "),"
                             " ya da tts_engine: espeak (espeak-ng paketi).")
            else:
                lines.append("  Turkce icin: tts_engine: piper +"
                             " tts_voice: tr_TR-dfki-medium ("
                             + ("KUR-SES.bat" if kur_ses else hint) + "),"
                             " ya da tts_engine: onecore + tts_voice: tr"
                             " (Tolga).")
        # Say why the better voices were skipped: a silent downgrade from the
        # neural voice to robotic Tolga otherwise just sounds like a bad day.
        for name, reason in errors.items():
            lines.append("  " + _ascii(name) + " kullanilamadi: "
                         + _ascii(reason))
        return lines
    lines = ["TTS motoru bulunamadi; seslendirme KAPALI"
             " (" + hint + ").",
             "  istenen motor: " + request] + unknown
    for name, reason in errors.items():
        lines.append("  " + _ascii(name) + " kullanilamadi: " + _ascii(reason))
    if not errors:
        # Cannot happen through select_backend(), which always records a reason
        # per attempt - but an empty report would be the original bug again.
        lines.append("  sebep kaydedilmedi")
    if request in KNOWN:
        # An explicitly named engine is never downgraded (select_backend's
        # contract), so the user has no voice at all until the config is fixed.
        note = ("  NOT: tts_engine: " + request + " acikca istendi, bu yuzden"
                " baska sese DUSULMEZ.")
        if not windows:
            note += (" config.yaml icinde tts_engine: auto yaparsaniz piper,"
                     " espeak-ng ve spd-say sirayla denenir.")
            if request in ("onecore", "sapi"):
                note += " (" + request + " yalnizca Windows'ta var.)"
        elif request == "onecore":
            # Tolga is what auto falls back TO, and it is what just failed, so
            # auto would land on sapi - which on the development machine reads Turkish
            # with an English accent. Do not sell that as a fix.
            note += (" tts_engine: auto yaparsaniz once piper denenir; o da"
                     " yoksa bu makinede Turkce ses KALMAZ - "
                     + ("KUR-SES.bat piper Turkce sesini kurar." if kur_ses
                        else hint + "."))
        else:
            note += (" config.yaml icinde tts_engine: auto yaparsaniz Turkce"
                     " Tolga sesi denenir.")
        lines.append(note)
    return lines


def speaks_language(speaker):
    """True when `speaker` speaks in the language it was asked for: a voice
    is there and its name carries no WRONG_LANGUAGE_NOTE. The alarm asks
    this before it puts a sentence beside its siren - better the siren alone
    than "Help me!" read in a voice the listener cannot follow."""
    if speaker is None or not getattr(speaker, "ok", True):
        return False
    return WRONG_LANGUAGE_NOTE not in str(getattr(speaker, "engine", "") or "")


class Speaker:
    """Queued, non-blocking speech. speak() enqueues text, play_wav() enqueues
    already-rendered audio (used by the AAC cache), synth() is synchronous."""

    def __init__(self, voice_hint="tr", engine="auto", speaker_wav="",
                 speed=1.0, volume=1.0, language=None):
        # language: the program's language (i18n). Any but Turkish selects
        # strictly - a voice of another language is refused, never used to
        # read this language's text (select_backend). None / "tr": the old
        # rule, unchanged.
        self.language = str(language or "tr")
        self._strict = self.language != "tr"
        # Backend selection is synchronous (not on the worker) so that synth()
        # works from the first call. Construction must stay cheap: any model
        # loading belongs inside the backend, on first synthesis.
        self._synth, name = select_backend(engine, voice_hint, speaker_wav,
                                           speed=speed, volume=volume,
                                           strict=self._strict)
        self._select = (voice_hint, speaker_wav, speed, volume)
        self._auto = str(engine or "auto").strip().lower() == "auto"
        self._fails = 0
        self._backup = None
        self.engine = name
        self.voice = name          # kept for backwards compatibility
        # Read by the phrase cache: every clone is named "xtts/klon", so the
        # recording itself has to be part of the cache key.
        self.speaker_wav = str(speaker_wav or "").strip()
        self.ok = self._synth is not None
        # Snapshot the selection diagnostics NOW. last_errors()/last_request()
        # are module state that the next select_backend() call overwrites (Task
        # 16 builds a second Speaker for the AAC grid), and a startup report
        # naming another engine's failure is worse than no report at all.
        self.request = last_request()
        self.errors = last_errors()
        self._closed = False
        self._last_synth_error = None
        self._q = queue.Queue()
        # Stop/repeat (speech_stop_key). Every queued item carries the
        # generation it was asked for in; stop() bumps it, so a sentence that
        # was mid-synthesis when the user pressed stop is dropped instead of
        # coming out a second later.
        self._gen = 0
        self._last_req = None      # the item the user most recently asked for
        self._last_audio = None    # (that item, its wav) once synthesised
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                try:
                    kind, payload, gen = item
                    if gen != self._gen:
                        continue           # stopped before it was reached
                    wav = self._render(payload) if kind == "text" else payload
                    if wav and gen == self._gen:
                        if item is self._last_req:
                            # One assignment, so repeat() never pairs this
                            # request with the previous sentence's audio.
                            self._last_audio = (item, wav)
                        _play(wav)
                except BaseException as exc:      # noqa: BLE001
                    if getattr(exc, "cancelled", False):
                        continue           # stop() cut its wait: no fault
                    # Broad on purpose, BaseException included: this thread is
                    # the user's voice and nobody restarts it, so one bad
                    # sentence must not end it. Nothing legitimate is swallowed
                    # - KeyboardInterrupt and SystemExit are never delivered to
                    # a non-main thread (unlike select_backend, where
                    # BaseException is left uncaught to preserve Ctrl-C).
                    # Native backends (piper/xtts) can abort in ways that are
                    # not Exception subclasses.
                    _report(f"cumle seslendirilemedi: {type(exc).__name__}")
        except BaseException as exc:              # noqa: BLE001
            # The loop itself died. Never let that be silent: speak() would go
            # on filling a queue nobody reads and close() would join a corpse
            # instantly, so the user would press the key and hear nothing,
            # forever, with no clue why. ok=False makes it observable.
            self.ok = False
            _report(f"seslendirme is parcacigi durdu: {type(exc).__name__}")

    def speak(self, text):
        text = (text or "").strip()
        if text and self.ok and not self._closed:
            self._ask(("text", text[:2000], self._gen))

    def play_wav(self, wav):
        if wav and not self._closed:
            self._ask(("wav", wav, self._gen))

    def _ask(self, item):
        self._last_req = item
        self._q.put(item)

    def stop(self):
        """Silence speech NOW: the sentence playing, the one being prepared
        and everything queued behind them. Never raises, never blocks - it is
        called from the gaze loop on a key press."""
        self._gen += 1
        try:
            while True:
                item = self._q.get_nowait()
                if item is None:
                    # close() is waiting on this sentinel; dropping it would
                    # leave the worker parked on an empty queue for good.
                    self._q.put(None)
                    break
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            _stop_playback()
        except Exception:
            pass
        # A sentence waiting on the voice worker (packaged piper) stops
        # waiting now, so the next one is not queued behind it (R46).
        # Only this Speaker's own sentence: the phrase-cache warmer and the
        # alarm phrase share the worker and must not be cut short.
        cancel = getattr(self._synth, "cancel", None)
        if cancel is not None:
            try:
                cancel(self._t)
            except Exception:
                pass
        # A voice that speaks by itself (spd-say) is not on our player.
        for synth in (self._synth, (self._backup or (None,))[0]):
            if speaks_itself(synth):
                try:
                    synth.stop()
                except Exception:
                    pass

    def repeat(self):
        """Say the last thing asked for again. False when there is none.

        Re-uses the audio when it was already rendered (instant, and no piper
        lock), otherwise asks for the text again - which is what happens when
        the user stopped a sentence before it was ever heard.
        """
        if self._closed:
            return False
        req = self._last_req
        if req is None:
            return False
        audio = self._last_audio
        if audio is not None and audio[0] is req:
            self._q.put(("wav", audio[1], self._gen))
        else:
            self._ask((req[0], req[1], self._gen))
        return True

    @property
    def pending(self):
        """How much speech is still queued.

        Read by the phrase-cache warmer so it can stand aside: a sentence the
        user asked for with F4 never passes through the cache, and without this
        the warmer would go on taking piper's lock in front of it.
        """
        return self._q.qsize()

    # The auto chain used to run ONCE, at startup: piper that loads fine and
    # then fails on every sentence (a damaged model, an onnxruntime fault)
    # left the user mute with Tolga installed and unused. Now a failed
    # sentence is said at once with the next voice of the chain - a lost "su
    # istiyorum" is worse than a different voice - and after SWITCH_AFTER
    # failures in a row the next voice takes over for the session.
    SWITCH_AFTER = 3

    def _render(self, text, live=True, used=None):
        """WAV for text; falls back as above. Raises when nothing works.
        live=False (pre-rendering for the phrase cache) never falls back to
        a voice that speaks instead of returning audio (spd-say): the
        warmer would read its whole board aloud. used (a list) gets the
        name of the voice that really rendered it: the phrase cache must
        never file a fallback voice's audio under the main voice (R46)."""
        engine = self.engine
        try:
            wav = self._synth(text)
            if not wav:
                raise RuntimeError("bos ses")
            self._fails = 0
            if used is not None:
                used.append(engine)
            return wav
        except Exception as exc:
            if getattr(exc, "cancelled", False):
                raise              # stopped by the user: no fallback either
            # A voice still loading or busy (the packaged piper's worker) is
            # not a broken voice: that sentence takes the fallback, but it
            # never counts toward giving the voice up for the session (R46
            # N-1). A death, a hang or a bad answer does.
            if not getattr(exc, "transient", False):
                self._fails += 1
            backup = self._backup_voice()
            if backup is None or (not live and speaks_itself(backup[0])):
                raise
            fn, name = backup
            if self._fails >= self.SWITCH_AFTER:
                _report("'%s' sesi art arda %d kez hata verdi - bu oturumda"
                        " '%s' sesi kullaniliyor."
                        % (_ascii(self.engine), self._fails, _ascii(name)))
                old = self._synth
                self._synth, self.engine, self.voice = fn, name, name
                self._fails = 0
                self._backup = None
                # The voice worker of a voice given up on is not kept alive
                # idle for the rest of the session.
                # Off this thread: closing waits for the worker to exit,
                # and the sentence that triggered the switch is said next.
                close = getattr(old, "close", None)
                if close is not None:
                    try:
                        threading.Thread(target=close, daemon=True,
                                         name="ses-isci-kapat").start()
                    except Exception:
                        pass
            else:
                _report("'%s' sesi hata verdi - cumle '%s' sesiyle soyleniyor."
                        % (_ascii(self.engine), _ascii(name)))
            wav = fn(text)
            if used is not None:
                used.append(name)
            return wav

    def _backup_voice(self):
        """(synth, name) of the next working voice after the current one in
        the auto chain, built once; None when there is none. Only for
        "auto": an engine the user named is never swapped for another."""
        if not self._auto:
            return None
        if getattr(self, "_backup", None) is not None:
            return self._backup
        from eye_gaze.tts_backends import auto_order
        order = auto_order()
        cur = str(self.engine).split("/", 1)[0]
        later = order[order.index(cur) + 1:] if cur in order else ()
        hint, ref, speed, volume = self._select
        for nxt in later:
            fn, name = select_backend(nxt, hint, ref, speed=speed,
                                      volume=volume,
                                      strict=getattr(self, "_strict", False))
            if fn is not None:
                self._backup = (fn, name)
                return self._backup
        return None

    @property
    def renders_audio(self):
        """True when this voice gives audio to keep (not spd-say)."""
        return bool(self.ok and self._synth is not None
                    and not speaks_itself(self._synth))

    def wait_ready(self, timeout):
        """True when the voice is ready to render (a packaged piper's worker
        has loaded its voice); True at once for every other voice."""
        fn = getattr(self._synth, "wait_ready", None)
        if fn is None:
            return True
        try:
            return bool(fn(timeout))
        except Exception:
            return False

    def synth(self, text):
        """WAV bytes for `text`, or None. Synchronous - callers are the cache
        warmer (background thread), the alarm phrase and tests, never the
        gaze loop."""
        return self.synth_with_engine(text)[0]

    def synth_with_engine(self, text):
        """(WAV bytes or None, name of the voice that rendered it)."""
        text = (text or "").strip()
        if not text or not self.ok:
            return None, None
        if speaks_itself(self._synth):
            # spd-say has no audio to give: "rendering" would say the phrase
            # out loud now. Uncached, the AAC grid speaks it live instead.
            return None, None
        used = []
        try:
            wav = self._render(text[:2000], live=False, used=used)
            return wav, (used[-1] if used else self.engine)
        except Exception as exc:
            # Returning None in silence was the last of the quiet paths. The
            # caller is the phrase-cache warmer, and warm() decides what to do
            # by asking whether the file EXISTS - so a phrase that will not
            # render is skipped for good. Without a line here the user simply
            # finds that one sentence is always slow, and nothing anywhere
            # explains it. Repeats of the same fault are dropped: the warmer
            # walks dozens of phrases and would otherwise bury the console.
            kind = type(exc).__name__
            if kind != self._last_synth_error:
                self._last_synth_error = kind
                _report("cumle onceden hazirlanamadi (%s): %s"
                        % (kind, _ascii(text[:40])))
            return None, None

    def close(self):
        """Drain and stop. Idempotent, and bounded: it waits at most 5 s for
        audio already playing rather than cutting a sentence off mid-word.
        Callers releasing input devices must do so BEFORE calling this."""
        if not self._closed:
            # Only the first call enqueues the stop sentinel; a second one
            # would sit in the queue forever once the worker has exited.
            self._closed = True
            self._q.put(None)
        self._t.join(timeout=5.0)
