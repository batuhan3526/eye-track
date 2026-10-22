# gaze/eye_gaze/phrase_cache.py
"""Pre-rendered AAC phrases.

Fixed phrases never change, so we synthesise them once, write the WAV to disk
and replay it. An AAC user asking for help should not wait for a neural vocoder
- the phrase has to come out the instant they look at it.
"""
import hashlib
import os
import threading
import time


def cache_key(engine, voice, text, extra="", language=""):
    """Filesystem-safe key. Includes engine and voice so switching either one
    invalidates the cache instead of replaying the old voice. `extra` is
    anything else that shapes the audio (the clone reference); it is left out
    of the hash when empty so caches made before it existed stay valid.

    `language` is the program language the phrase was rendered for: one
    voice name can read two languages' text (an OS voice, espeak), and
    Turkish audio must never answer an English phrase or the other way round.
    Turkish - every cache made before languages existed - keeps the key
    without it, so the development machine's cache stays valid."""
    raw = f"{engine}\x00{voice}\x00{text}"
    if extra:
        raw += f"\x00{extra}"
    lang = language.strip() if isinstance(language, str) else ""
    if lang and lang != "tr":
        raw += f"\x00lang={lang}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _reference_stamp(speaker):
    """Path + mtime of the voice-clone reference, '' when there is none.

    Every clone is named "xtts/klon", so without this re-recording the
    reference (same path, new file) or pointing at another one would leave the
    AAC phrases in the OLD voice. Never raises: a missing file just stamps as
    such - xtts itself refuses it at startup.
    """
    ref = str(getattr(speaker, "speaker_wav", "") or "").strip()
    if not ref:
        return ""
    try:
        mtime = os.path.getmtime(ref)
    except Exception:
        mtime = "?"
    return f"{ref}|{mtime}"


# How long the warmer waits for a voice that is still loading.
WAIT_READY_S = 90.0


class PhraseCache:
    """Disk cache of synthesised phrases, warmed on a background thread."""

    def __init__(self, speaker, cache_dir, defer_seconds=1.0):
        self._speaker = speaker
        self._dir = cache_dir
        self._threads = []
        self._defer = float(defer_seconds)
        self._last_user = None
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            pass

    def _path(self, text):
        key = cache_key(getattr(self._speaker, "engine", ""),
                        getattr(self._speaker, "voice", ""), text,
                        _reference_stamp(self._speaker),
                        getattr(self._speaker, "language", ""))
        return os.path.join(self._dir, key + ".wav")

    def warm(self, texts):
        """Synthesise any missing phrases in the background. Never blocks."""
        todo, seen = [], set()
        for t in texts:
            t = (t or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            if not os.path.isfile(self._path(t)):
                todo.append(t)
        if not todo:
            return
        # The warm thread is a daemon, so quitting the driver early kills it
        # mid-write and leaves its .tmp behind. Each such start adds one more,
        # for ever; they are never read, only accumulated.
        # Only while none of our own warm threads is running, though: one of
        # those .tmp files may be the phrase it is writing right now. Windows
        # refuses to delete an open file, which hid this; on Linux the unlink
        # succeeds, the os.replace() after it fails and the phrase is lost.
        self._threads = [t for t in self._threads if t.is_alive()]
        if not self._threads:
            self._sweep_orphans()
        th = threading.Thread(target=self._warm_all, args=(todo,), daemon=True)
        # Kept in a list, not a single slot: warming a second board while the
        # first is still going would otherwise orphan that thread, and wait()
        # would return with a phrase still half-written.
        self._threads.append(th)
        th.start()

    def _sweep_orphans(self):
        """Delete .tmp files left by a warm thread killed mid-write."""
        try:
            for name in os.listdir(self._dir):
                if name.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(self._dir, name))
                    except Exception:
                        pass
        except Exception:
            pass

    def _busy(self):
        """Is the user's own speech in flight right now?

        Two ways in. This cache's speak() is one; the other is F4, which goes
        straight to Speaker.speak() and never touches this object at all - so
        `pending` (the Speaker's own queue) has to be asked as well, or a warm
        loop keeps taking piper's lock in front of the sentence the user just
        asked to have read out.
        """
        if getattr(self._speaker, "pending", 0):
            return True
        return (self._last_user is not None
                and time.monotonic() - self._last_user < self._defer)

    def _yield_to_user(self):
        """Block while the user is being spoken to.

        piper serialises every synthesis on one lock (measured in Task 4), so a
        warm loop grinding through forty-eight phrases can hold that lock just
        as the user asks for a sentence - making them wait for exactly the
        delay this cache exists to remove. The warmer is never the urgent one.

        Bounded, because this is a courtesy and not a lock: a Speaker wedged
        with a queued item must not mean the phrases are never prepared at all.
        """
        deadline = time.monotonic() + max(self._defer, 0.0) + 2.0
        while self._busy() and time.monotonic() < deadline:
            time.sleep(0.02)

    def _warm_all(self, texts):
        # Never urgent: a packaged piper's worker may still be loading its
        # voice; rendering now would only get the fallback voice (R46).
        wait = getattr(self._speaker, "wait_ready", None)
        if wait is not None:
            try:
                wait(WAIT_READY_S)
            except Exception:
                pass
        for t in texts:
            self._yield_to_user()
            if wait is not None:
                # Again before every phrase: a worker that died and is being
                # restarted is waited for, not met with the fallback voice
                # phrase after phrase (instant when ready). A voice that is
                # not back within WAIT_READY_S - or was closed, at the end of
                # the session - ends the warming: the phrases are said live
                # when asked for, and a warmer still printing while Python
                # shuts down aborts the process (seen: exit 127).
                try:
                    if not wait(WAIT_READY_S):
                        return
                except Exception:
                    return
            try:
                render = getattr(self._speaker, "synth_with_engine", None)
                if render is not None:
                    wav, made_by = render(t)
                else:
                    wav, made_by = self._speaker.synth(t), None
                if not wav:
                    continue
                if made_by is not None and made_by != getattr(
                        self._speaker, "engine", made_by):
                    # A fallback voice said it (the main voice failed once):
                    # filed under the main voice's key it would play in the
                    # wrong voice for good. Not cached; asked live later.
                    continue
                # Written aside and renamed: warm() skips anything that already
                # exists, so a WAV truncated by a crash or a full disk would
                # never be repaired - that phrase would make a broken noise for
                # the life of the machine and nothing would retry it.
                path = self._path(t)
                tmp = path + ".tmp"
                try:
                    with open(tmp, "wb") as f:
                        f.write(wav)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(tmp, path)
                except Exception:
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
            except Exception:
                continue          # a phrase that will not render is not fatal

    def wait(self, timeout=120.0):
        """Block until warming finishes. For tests and shutdown only."""
        for th in list(self._threads):
            th.join(timeout)

    def speak(self, text):
        """Speak `text`. True when it came from cache (instant), False when it
        had to be synthesised live."""
        text = (text or "").strip()
        if not text:
            return False
        self._last_user = time.monotonic()
        try:
            with open(self._path(text), "rb") as f:
                self._speaker.play_wav(f.read())
            return True
        except Exception:
            self._speaker.speak(text)
            return False
