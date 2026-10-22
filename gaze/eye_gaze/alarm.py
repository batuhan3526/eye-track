# gaze/eye_gaze/alarm.py
"""Emergency alarm: the user holds a key, the click switch or a gamepad button
and the machine calls for help - a loud siren with "Yardim edin!" spoken over
it - until the same hold stops it or alarm_max_seconds runs out.

Why its own thread: the gaze loop is not always running. The first calibration,
F9 recalibration and F7 drift correction all block it, the camera can drop out,
a per-frame fault can repeat for minutes, and the user can be paused. None of
those may be the moment the alarm stops working. So the alarm reads HELD state
(the keyboard hook thread and XInput keep that up to date on their own) from a
daemon thread, never consumes a press the driver needs, and never touches the
mouse - a fault in here cannot freeze the cursor or hold a button down.

Everything that touches a device is injected (player, keyboard, gamepad,
clock, thread), so the logic is tested without a hook, a speaker or a clock.
"""
import io
import os
import tempfile
import threading
import time
import wave

import numpy as np

from eye_gaze.i18n import t as _t
from eye_gaze.switch import (GAMEPAD_BUTTONS, KeyPresses, button_names_text,
                             valid_button)

# The Turkish sentence; the one spoken is alarm_text(), in the language of
# the session (rendered once per session, see AlarmCore.attach_speaker).
ALARM_TEXT = "Yardım edin!"


def alarm_text():
    """The alarm's sentence in the session's language. A catalog that cannot
    be read gives back the key itself - never something to say out loud in
    an emergency - so the built-in Turkish sentence stands in."""
    try:
        text = _t("alarm.phrase")
    except Exception:
        text = ""
    if not text or text == "alarm.phrase":
        return ALARM_TEXT
    return text

_MIN_HOLD = 0.5          # s; below this a hold is a press
_MIN_SWITCH_HOLD = 1.0   # s; the click switch's ordinary press must stay a click
_PAD_RETRY = 1.0         # s between polls while no pad/joystick answers
_RATE = 22050
# Linux plays through playback (no winsound) and reads key state from its
# backend. Module-level so tests can take either path on either system.
_LINUX = os.name != "nt"


def _ascii(text):
    """Console-safe text: the console is cp1254."""
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def _console(message):
    """Default log: timestamped console line (the driver mirrors stdout into
    gaze.log, so this is also the after-the-fact record). Never raises."""
    try:
        print("[ALARM %s] %s" % (time.strftime("%H:%M:%S"), _ascii(message)))
    except Exception:
        pass


class HoldDetector:
    """update(pressed, now) is True ONCE when `pressed` has stayed true for
    `seconds`; it re-arms only after a release. A key held for a minute is one
    hold, so the hold that starts the alarm can never also stop it."""

    def __init__(self, seconds):
        self.seconds = float(seconds)
        self._since = None
        self._fired = False

    def update(self, pressed, now):
        if not pressed:
            self._since = None
            self._fired = False
            return False
        if self._since is None:
            self._since = now
        if not self._fired and now - self._since >= self.seconds - 1e-9:
            self._fired = True
            return True
        return False


# --- sound -----------------------------------------------------------------------

def _siren(rate, seconds=2.0, hi=960.0, lo=770.0, step=0.25, peak=0.9):
    """Hi-lo two-tone siren, float in [-peak, peak]. A third harmonic makes it
    carry further than a pure sine at the same peak; short fades stop the
    switch between tones from clicking."""
    seg = int(rate * step)
    fade = max(1, int(rate * 0.005))
    env = np.ones(seg)
    env[:fade] = np.linspace(0.0, 1.0, fade)
    env[-fade:] = np.linspace(1.0, 0.0, fade)
    t = np.arange(seg) / rate
    parts = []
    for i in range(max(1, int(round(seconds / step)))):
        f = hi if i % 2 == 0 else lo
        x = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 3 * f * t)
        parts.append(x * env)
    y = np.concatenate(parts)
    return y / np.abs(y).max() * peak


def _decode(wav_bytes, rate):
    """WAV bytes -> mono float at `rate`, or None if it is not usable audio."""
    if not wav_bytes:
        return None
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            ch, width, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
            raw = w.readframes(w.getnframes())
        if width == 1:
            x = (np.frombuffer(raw, dtype=np.uint8).astype(float) - 128) / 128
        elif width == 2:
            x = np.frombuffer(raw, dtype="<i2").astype(float) / 32768
        elif width == 4:
            x = np.frombuffer(raw, dtype="<i4").astype(float) / 2147483648
        else:
            return None
        if ch > 1:
            x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
        if len(x) == 0 or fr <= 0:
            return None
        if fr != rate:
            n = max(1, int(len(x) * rate / fr))
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
        return x
    except Exception:
        return None


def build_cycle_wav(voice_wav=None, rate=_RATE):
    """One alarm cycle as (16-bit mono WAV bytes, seconds): siren, then the
    spoken phrase normalised up to siren loudness, then a short gap. A missing
    or unreadable voice gives the siren alone - the alarm never depends on TTS.
    """
    parts = [_siren(rate)]
    voice = _decode(voice_wav, rate)
    if voice is not None and np.abs(voice).max() > 1e-4:
        voice = voice / np.abs(voice).max() * 0.9
        parts += [np.zeros(int(rate * 0.25)), voice, np.zeros(int(rate * 0.35))]
    else:
        parts.append(np.zeros(int(rate * 0.2)))
    y = np.concatenate(parts)
    pcm = np.clip(np.round(y * 32767), -32768, 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue(), len(pcm) / rate


class TonePlayer:
    """Plays the alarm cycle through winsound, asynchronously, from a temp file
    (winsound cannot play from memory asynchronously).

    Re-issued once per cycle rather than SND_LOOP: winsound is one sound per
    process. While ringing it holds `channel` (the shared speech/earcon
    player), so speech and click beeps do not start at all; re-issuing is the
    backstop for anything that still slips through, bringing the siren back
    within a cycle. It also means a dead watcher thread leaves at most one
    cycle of sound, never an endless one.

    When PlaySound cannot be used (file not writable, no audio device answer)
    it falls back to winsound.Beep on its own thread.

    Linux has no winsound: the cycle is played from memory through `audio`,
    playback's shared PortAudio output (the one speech and earcons use, so a
    new sound replaces the other exactly as PlaySound does), and the Beep
    fallback becomes a sine on that same output. No audio device there means
    no siren at all - playback says so once, and probe_audio() says it at
    start-up, before anyone needs the alarm.
    """

    def __init__(self, sound="auto", tmpdir=None, rate=_RATE,
                 thread=threading.Thread, channel=None, audio="auto"):
        # channel: the speech/earcon player (playback.WavPlayer). Held while
        # the alarm rings so no click beep or sentence cuts the siren off.
        self._channel = channel
        auto = sound == "auto"
        if auto:
            try:
                import winsound as sound
            except Exception:
                sound = None
        self._snd = sound
        if audio == "auto":
            audio = None
            if auto and sound is None and _LINUX:
                try:
                    from eye_gaze.playback import audio_backend
                    audio = audio_backend()
                except Exception:
                    audio = None
        self._audio = audio
        self._tmpdir = tmpdir or tempfile.gettempdir()
        self._rate = rate
        self._thread = thread
        self._lock = threading.Lock()
        self._wav, self.cycle_seconds = build_cycle_wav(None, rate)
        self.has_voice = False          # "Yardim edin!" is in the cycle
        self._path = None
        self._files = []
        self._n = 0
        self._ringing = False
        self._beeping = False
        self._next = 0.0

    def set_voice(self, wav):
        data, seconds = build_cycle_wav(wav, self._rate)
        with self._lock:
            self._wav, self.cycle_seconds = data, seconds
            self._path = None            # written afresh on the next play
            self.has_voice = wav is not None

    def _file(self):
        with self._lock:
            if self._path is not None and not self._intact(self._path):
                # Async PlaySound of a missing file raises NOTHING and plays
                # nothing: a %TEMP% cleaner (Storage Sense, Disk Cleanup) that
                # ran between alarms would leave the siren silent with no
                # Beep fallback and a console saying it rings. Checked on
                # every cycle; a failing rewrite raises -> Beep fallback.
                self._path = None
            if self._path is None:
                self._n += 1        # a fresh name: never rewrite one in use
                path = os.path.join(self._tmpdir, "gaze_alarm_%d_%d.wav"
                                    % (os.getpid(), self._n))
                with open(path, "wb") as f:
                    f.write(self._wav)
                self._files.append(path)
                self._path = path
            return self._path

    def _intact(self, path):
        try:
            return os.path.getsize(path) == len(self._wav)
        except OSError:
            return False

    def _play(self, now):
        if self._audio is not None:
            self._play_audio(now)
            return
        if self._snd is None:
            return
        try:
            flags = (self._snd.SND_FILENAME | self._snd.SND_ASYNC
                     | self._snd.SND_NODEFAULT)
            self._snd.PlaySound(self._file(), flags)
            self._next = now + max(0.5, self.cycle_seconds - 0.15)
        except Exception:
            self._start_beeps()

    def _play_audio(self, now):
        try:
            with self._lock:
                wav = self._wav
            ok = bool(self._audio.start(wav))
        except Exception:
            ok = False
        if ok:
            self._next = now + max(0.5, self.cycle_seconds - 0.15)
            return
        # Tried again once per cycle (a device plugged back in brings the
        # siren back), never at the alarm loop's 20 Hz: each try re-opens
        # PortAudio. A DEVICE failure gets no beep fallback either - the
        # sine would go to the same dead output; only a sound the device
        # refused for another reason does.
        self._next = now + max(0.5, self.cycle_seconds)
        if not getattr(self._audio, "error", None):
            self._start_beeps()

    @property
    def probes_audio(self):
        """True where the siren's device can be checked ahead (Linux)."""
        return self._audio is not None

    def probe_audio(self):
        """(ok, ASCII reason) - can the siren sound at all? Only the Linux
        output can say; Windows (winsound) is taken as able. Blocking
        (PortAudio initialises): call it off the alarm loop."""
        if self._audio is None:
            return True, ""
        try:
            return self._audio.probe()
        except Exception as exc:
            return False, type(exc).__name__

    def _start_beeps(self):
        if self._beeping or (self._snd is None and self._audio is None):
            return
        self._beeping = True
        try:
            self._thread(target=self._beep_loop, daemon=True).start()
        except Exception:
            self._beeping = False

    def _beep_loop(self):
        try:
            while self._ringing:
                for f in (960, 770):
                    if not self._ringing:
                        break
                    if self._snd is not None:
                        self._snd.Beep(f, 250)
                    elif not self._audio.tone(f, 250):
                        # No device: tone() returns at once - looping on it
                        # would only spin a core. Already said by playback.
                        return
        except Exception:
            pass
        finally:
            self._beeping = False

    def _hold_channel(self, on):
        if self._channel is None:
            return
        try:
            if on:
                self._channel.hold()
            else:
                self._channel.release()
        except Exception:
            pass

    def start(self, now):
        self._ringing = True
        self._hold_channel(True)        # BEFORE the siren: nothing may cut it
        self._play(now)

    def tick(self, now):
        if self._ringing and not self._beeping and now >= self._next:
            self._play(now)

    def stop(self):
        self._ringing = False
        if self._snd is not None:
            try:
                self._snd.PlaySound(None, 0)
            except Exception:
                pass
        if self._audio is not None:
            try:
                self._audio.stop()
            except Exception:
                pass
        self._hold_channel(False)       # AFTER the silence: speech may resume

    def close(self):
        self.stop()
        for p in self._files:
            try:
                os.remove(p)
            except Exception:
                pass
        self._files = []


# --- logic --------------------------------------------------------------------------

class AlarmCore:
    """Pure alarm state. sources: (label, poll(now) -> held, hold_seconds).
    A completed hold on ANY source toggles the alarm; while ringing the player
    is kept going and the timeout is enforced. tick() never raises."""

    def __init__(self, player, sources, max_seconds=120.0, log=None):
        self.player = player
        self._srcs = [(str(lbl), poll, HoldDetector(sec), float(sec))
                      for (lbl, poll, sec) in sources]
        self.max_seconds = float(max_seconds or 0)
        self._log = log or _console
        self.ringing = False
        self._started = None
        self._bad = set()
        # Set by AlarmWatcher when no rendered phrase exists yet: said live
        # once when the alarm fires (R46 N-2), stopped with the alarm.
        self.live_phrase = None
        self.live_stop = None

    def _say(self, msg):
        try:
            self._log(_ascii(msg))
        except Exception:
            pass

    def tick(self, now):
        fired = None
        for label, poll, det, sec in self._srcs:
            try:
                down = bool(poll(now))
            except Exception as exc:
                if label not in self._bad:
                    self._bad.add(label)
                    self._say("UYARI: alarm girisi '%s' okunamadi (%s);"
                              " digerleri calisiyor." % (label, type(exc).__name__))
                down = False
            if det.update(down, now) and fired is None:
                fired = (label, sec)
        if fired is not None:
            if self.ringing:
                self.stop("'%s' basili tutuldu" % fired[0])
            else:
                self._start(now, *fired)
            return
        if not self.ringing:
            return
        if self.max_seconds > 0 and now - self._started >= self.max_seconds:
            self.stop("zaman asimi: %g sn doldu" % self.max_seconds)
            return
        try:
            self.player.tick(now)
        except Exception:
            pass

    def _start(self, now, label, sec):
        self.ringing = True
        self._started = now
        limit = ("en fazla %g sn calar" % self.max_seconds
                 if self.max_seconds > 0 else "sure siniri yok")
        # Sound FIRST, log after: a console write can block (a stray click
        # puts a classic console into QuickEdit selection), and a siren that
        # waits behind a print is no siren.
        try:
            self.player.start(now)
        except Exception as exc:
            self._say("HATA: alarm sesi calinamadi (%s: %s)"
                      % (type(exc).__name__, exc))
        live = self.live_phrase
        if live is not None and not getattr(self.player, "has_voice", True):
            try:
                live()
            except Exception:
                pass
        self._say("!!! ACIL ALARM CALIYOR ('%s' basili tutuldu) - durdurmak"
                  " icin '%s' tekrar %g sn basili tutun; %s. !!!"
                  % (label, label, sec, limit))

    def stop(self, why):
        if not self.ringing:
            return
        self.ringing = False
        try:
            self.player.stop()
        except Exception:
            pass
        stop = self.live_stop
        if stop is not None and self.live_phrase is not None:
            try:
                stop()
            except Exception:
                pass
        self._say("ACIL ALARM durdu (%s)." % why)

    def close(self):
        self.stop("program kapaniyor")
        try:
            self.player.close()
        except Exception:
            pass


class AlarmWatcher:
    """Runs AlarmCore.tick() on a daemon thread, independent of the gaze loop."""

    def __init__(self, core, clock=time.monotonic, sleep=time.sleep,
                 period=0.05, log=None, thread=threading.Thread, closers=()):
        self.core = core
        self.closed = False
        self._clock = clock
        self._sleep = sleep
        self._period = period
        self._log = log or _console
        self._thread = thread
        self._closers = list(closers)
        self._errs = set()

    def start(self):
        self._thread(target=self._loop, daemon=True).start()
        if getattr(self.core.player, "probes_audio", False):
            try:
                self._thread(target=self._probe_audio, daemon=True).start()
            except Exception:
                pass

    def _probe_audio(self):
        """Say at start-up when the siren cannot sound (Linux without an
        audio device): found at the first emergency it would be too late.
        Its own thread - PortAudio may take a while to answer, and the alarm
        loop must be watching the keys meanwhile."""
        try:
            ok, why = self.core.player.probe_audio()
            if not ok:
                self._log("UYARI: ses aygiti yok (%s) - acil alarm SESSIZ"
                          " calacak (yalniz ekranda ve kayitta gorunur)."
                          % _ascii(why))
        except Exception:
            pass

    def _loop(self):
        while not self.closed:
            try:
                self.core.tick(self._clock())
            except Exception as exc:
                kind = type(exc).__name__
                if kind not in self._errs:
                    self._errs.add(kind)
                    try:
                        self._log("HATA: alarm izleyicisi (%s): %s"
                                  % (kind, _ascii(exc)))
                    except Exception:
                        pass
            try:
                self._sleep(self._period)
            except Exception:
                pass

    def attach_speaker(self, speaker):
        """Render "Yardim edin!" with the user's own voice, off every loop;
        until (or unless) it arrives the alarm is the siren alone."""
        if speaker is None or not getattr(speaker, "ok", True):
            return
        # No language gate here: a Speaker of any language but Turkish is
        # built strictly (speech.Speaker) and never holds a voice of another
        # language, so "Help me!" can only be said in an English voice. A
        # Turkish Speaker that fell back to a foreign voice still says
        # "Yardim edin!" beside the siren, accent and all - startup_report()
        # warns about that voice, and a sentence someone understands beats
        # the siren alone.
        try:
            self._thread(target=self._render, args=(speaker,),
                         daemon=True).start()
        except Exception:
            pass

    # The phrase must end up said in the user's own voice whatever happens
    # at startup (R46 N-2/N-5): a packaged piper's worker may still be
    # loading (the fallback voice gives the phrase within ~2 s), may die and
    # be restarted, or there may be no fallback that can return audio
    # (spd-say only, or none). Then the render is retried with a bounded
    # back-off until the user's voice renders it or the session ends.
    UPGRADE_WAIT_S = 30.0
    UPGRADE_BACKOFF_S = (10.0, 20.0, 30.0)

    def _render(self, speaker):
        try:
            render = getattr(speaker, "synth_with_engine", None)
            if render is not None:
                wav, made_by = render(alarm_text())
            else:
                wav, made_by = speaker.synth(alarm_text()), None
            if wav:
                self.core.player.set_voice(wav)
            main = getattr(speaker, "engine", None)
            if wav and (made_by is None or made_by == main):
                return                          # the user's own voice: done
            live = getattr(speaker, "speak", None)
            if not wav and live is not None:
                # Until a rendering exists, the alarm says the phrase live
                # when it fires, with whatever voice the chain can use then
                # (spd-say speaks by itself, over the siren, and is stopped
                # with the alarm) - a voice beats the siren alone.
                self.core.live_phrase = lambda: live(alarm_text())
                self.core.live_stop = getattr(speaker, "stop", None)
            if getattr(speaker, "renders_audio", False):
                if not wav:
                    self._log("UYARI: alarm cumlesi henuz hazirlanamadi;"
                              " hazirlanana kadar alarm calinca (bir yedek"
                              " ses varsa) canli soylenecek; siren her"
                              " durumda calar.")
                self._upgrade(speaker)
            elif not wav:
                self._log("UYARI: alarm cumlesi bu sesle hazirlanamadi;"
                          " alarmda yalniz siren calacak."
                          if live is None else
                          "UYARI: alarm cumlesi bu sesle onceden"
                          " hazirlanamaz; alarm calinca canli soylenecek.")
        except BaseException as exc:      # noqa: BLE001 - a worker must not die loud
            try:
                self._log("UYARI: alarm cumlesi seslendirilemedi (%s);"
                          " yalniz siren calacak." % type(exc).__name__)
            except Exception:
                pass

    def _pause(self, seconds):
        end = time.monotonic() + seconds
        while not self.closed and time.monotonic() < end:
            time.sleep(min(0.5, max(0.0, end - time.monotonic())))

    def _upgrade(self, speaker):
        """Render the phrase in the user's own voice: wait for it to be
        ready, try, and on failure try again after UPGRADE_BACKOFF_S (the
        last pause repeats) - until it works or the session ends. Said once
        if the first try fails. Never raises."""
        tries = 0
        said = False
        while not self.closed:
            try:
                if speaker.wait_ready(self.UPGRADE_WAIT_S):
                    wav, made_by = speaker.synth_with_engine(alarm_text())
                    if (wav and made_by == getattr(speaker, "engine", None)
                            and not self.closed):
                        self.core.player.set_voice(wav)
                        self.core.live_phrase = None
                        self._log("Alarm cumlesi artik hazir ('%s')."
                                  % _ascii(made_by))
                        return
            except BaseException:      # noqa: BLE001 - try again later
                pass
            if not said:
                said = True
                self._log("UYARI: alarm cumlesi kullanicinin sesiyle henuz"
                          " hazirlanamadi; tekrar denenecek.")
            self._pause(self.UPGRADE_BACKOFF_S[
                min(tries, len(self.UPGRADE_BACKOFF_S) - 1)])
            tries += 1

    def close(self):
        """Stop the sound and unhook. Idempotent, never raises."""
        self.closed = True
        try:
            self.core.close()
        except Exception:
            pass
        for c in self._closers:
            try:
                c()
            except Exception:
                pass
        self._closers = []


class _PadPoll:
    """One gamepad read per tick shared by every gamepad source, backing off
    while no pad or joystick answers (polling an empty slot is expensive)."""

    def __init__(self, pad, retry=_PAD_RETRY):
        self._pad = pad
        self._retry = retry
        self._now = None
        self._next = 0.0
        self._val = 0

    def pressed(self, name, now):
        if now != self._now:
            self._now = now
            if now < self._next:
                self._val = 0
            else:
                b = self._pad.buttons()
                if b is None:
                    self._next = now + self._retry
                    self._val = 0
                else:
                    self._val = int(b)
        return bool(self._val & GAMEPAD_BUTTONS.get(str(name).upper(), 0))


def _num(cfg, key, default):
    try:
        v = cfg.get(key, default)
        if v is None or isinstance(v, bool):
            return default
        if isinstance(v, str):
            v = v.strip().replace(",", ".")
        f = float(v)
        return f if f == f else default
    except Exception:
        return default


# Keys the driver already owns, with the driver's own defaults.
_DRIVER_KEYS = (("left_click_key", ""), ("right_click_key", ""),
                ("double_click_key", ""), ("drag_key", ""), ("zoom_key", "f6"),
                ("aac_key", ""), ("osk_key", ""), ("tts_key", ""),
                ("stt_key", ""), ("quit_key", "q"), ("recalibrate_key", "c"),
                ("pause_key", "f8"), ("drift_key", "f7"))


def _key_name(v):
    return str(v or "").strip().lower()


# --- a second way to see the key: the OS key state ---------------------------
# Windows silently REMOVES a low-level keyboard hook whose callback is late
# (LowLevelHooksTimeout) - a GIL held by a model load is enough. From then on
# the hook-fed hub never sees F12 again: no alarm, and nothing says so. The
# physical key state from GetAsyncKeyState needs no hook. While the hook is
# alive and swallowing the key the OS state stays "up" (a blocked key never
# reaches it), so the two never double up; once the hook is gone the key is
# no longer swallowed and the OS state carries the alarm.

_VK_NAMES = {"space": 0x20, "enter": 0x0D, "tab": 0x09, "pause": 0x13,
             "scroll lock": 0x91, "insert": 0x2D, "home": 0x24, "end": 0x23,
             "page up": 0x21, "page down": 0x22}


def vk_of(name):
    """Windows virtual-key code for a config key name, or None."""
    n = _key_name(name)
    if n in _VK_NAMES:
        return _VK_NAMES[n]
    if n[:1] == "f" and n[1:].isdigit() and 1 <= int(n[1:]) <= 24:
        return 0x6F + int(n[1:])
    if len(n) == 1 and n.isascii() and n.isalnum():
        return ord(n.upper())
    return None


_VK_TO_NAME = {v: k for k, v in _VK_NAMES.items()}


def name_of_vk(vk):
    """vk_of()'s inverse: the config key name for a virtual-key code."""
    if vk in _VK_TO_NAME:
        return _VK_TO_NAME[vk]
    if 0x70 <= vk <= 0x87:
        return "f%d" % (vk - 0x6F)
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    return None


_KB_RETRY = 5.0          # s between attempts to get a failed Linux keyboard


def _linux_key_reader():
    """vk -> physically down? from the Linux backend's own key state
    (XQueryKeymap on X11, the kernel's EVIOCGKEY with evdev). Both see the
    key while it is swallowed too - harmless: the poll ORs the two views,
    and the "hook is gone" warning needs the OS down AND the hook up for
    HOOK_DEAD_AFTER. The keyboard is fetched at the first read, not here:
    the alarm is built before anything else and must not wait on it."""
    kb = []
    failed = [None]          # when keyboard_api() last failed (monotonic)

    def down(vk):
        name = name_of_vk(vk)
        if name is None:
            return False
        if not kb:
            # A backend that cannot start is retried every few seconds, not
            # at the alarm loop's 20 Hz (each try may open devices / X).
            if (failed[0] is not None
                    and time.monotonic() - failed[0] < _KB_RETRY):
                return False
            try:
                from eye_gaze import platform
                kb.append(platform.keyboard_api())
            except Exception:
                failed[0] = time.monotonic()
                raise
        return bool(kb[0].is_pressed(name))
    return down


def _os_key_reader():
    """vk -> physically down? via GetAsyncKeyState (Windows) or the Linux
    backend's key state; None when neither can be had."""
    if _LINUX:
        return _linux_key_reader()
    try:
        import ctypes
        f = ctypes.WinDLL("user32").GetAsyncKeyState   # own handle: no
        f.argtypes = [ctypes.c_int]                   # shared prototypes
        f.restype = ctypes.c_short
        return lambda vk: bool(f(vk) & 0x8000)
    except Exception:
        return None


# Down on the OS but not on the hook this long = the hook is gone.
HOOK_DEAD_AFTER = 0.5


def _held_with_backup(hub_held, vk, os_down, label, warn):
    """poll(now) for one key: the hook's view OR the OS key state."""
    seen = {"since": None, "warned": False}

    def poll(now):
        h = bool(hub_held())
        if vk is None or os_down is None:
            return h
        try:
            d = os_down(vk)
        except Exception:
            d = False
        if d and not h:
            if seen["since"] is None:
                seen["since"] = now
            elif (not seen["warned"]
                  and now - seen["since"] >= HOOK_DEAD_AFTER):
                seen["warned"] = True
                if not _LINUX:
                    warn("UYARI: klavye kancasi '%s' tusunu gormuyor (Windows"
                         " kancayi kaldirmis olabilir). Alarm YEDEK yoldan"
                         " calisiyor; surucu tuslari icin programi yeniden"
                         " baslatin." % label)
                else:
                    warn("UYARI: klavye kancasi '%s' tusunu gormuyor (tus"
                         " dinleyicisi durmus ya da yeniden kuruluyor"
                         " olabilir). Alarm YEDEK yoldan calisiyor; surucu"
                         " tuslari icin programi yeniden baslatin." % label)
        else:
            seen["since"] = None
        return h or d
    return poll


def start_alarm(cfg, keys=None, kb=None, gamepad="auto", player=None,
                log=None, thread=threading.Thread, clock=time.monotonic,
                os_keys="auto"):
    """Build and start the alarm from config. NEVER raises.

    keys: the driver's KeyPresses hub, read (held) - never taken from - for the
    click-switch hold. The alarm key gets a hub of its own so the driver's key
    list and per-frame handling are untouched.
    """
    say = log or _console

    def warn(msg):
        try:
            say(_ascii(msg))
        except Exception:
            pass

    sources, closers, parts = [], [], []
    try:
        if os_keys == "auto":
            os_keys = _os_key_reader()
        hold = max(_MIN_HOLD, _num(cfg, "alarm_hold_seconds", 2.0))
        max_s = _num(cfg, "alarm_max_seconds", 120.0)
        sw_hold = _num(cfg, "alarm_switch_hold", 0.0)
        if 0 < sw_hold < _MIN_SWITCH_HOLD:
            warn("NOT: alarm_switch_hold %g sn cok kisa (normal bir tik alarm"
                 " calardi); %g sn kullaniliyor." % (sw_hold, _MIN_SWITCH_HOLD))
            sw_hold = _MIN_SWITCH_HOLD

        akey = _key_name(cfg.get("alarm_key", "f12"))
        if akey:
            owner = [k for k, d in _DRIVER_KEYS
                     if _key_name(cfg.get(k, d)) == akey]
            if owner:
                warn("UYARI: alarm_key '%s' zaten '%s' icin kullaniliyor -"
                     " klavye alarmi KAPALI. Tik switch'i ile alarm icin"
                     " alarm_switch_hold kullanin." % (akey, owner[0]))
            else:
                hub = KeyPresses([akey], suppress=bool(cfg.get("suppress_keys",
                                                               True)), kb=kb)
                closers.append(hub.close)
                if hub.ok and not hub.bad_keys:
                    def hub_held(hub=hub, k=akey):
                        hub.take(k)          # presses are not used; keep it empty
                        return hub.held(k)
                    sources.append((akey, _held_with_backup(
                        hub_held, vk_of(akey), os_keys, akey, warn), hold))
                    parts.append("'%s' %g sn basili tut" % (akey, hold))
                else:
                    warn("UYARI: alarm tusu '%s' baglanamadi - klavye alarmi"
                         " CALISMAYACAK." % akey)

        if sw_hold > 0:
            left = _key_name(cfg.get("left_click_key", ""))
            if left and keys is not None:
                sources.append((left, _held_with_backup(
                    lambda k=left: keys.held(k), vk_of(left), os_keys, left,
                    warn), sw_hold))
                parts.append("tik switch'i '%s' %g sn" % (left, sw_hold))

        pad_names = []
        galarm = str(cfg.get("gamepad_alarm", "") or "").strip()
        if galarm:
            if valid_button(galarm):
                pad_names.append((galarm.upper(), hold))
            else:
                warn("UYARI: gamepad_alarm: '%s' bilinen bir gamepad dugmesi"
                     " degil. Gecerli adlar: %s" % (galarm, button_names_text()))
        gleft = str(cfg.get("gamepad_left", "") or "").strip()
        if sw_hold > 0 and valid_button(gleft):
            pad_names.append((gleft.upper(), sw_hold))
        if pad_names and cfg.get("gamepad_enabled", True):
            if gamepad == "auto":
                from eye_gaze.switch import GamepadReader
                gamepad = GamepadReader()     # its own: not shared across threads
            if gamepad is not None:
                pp = _PadPoll(gamepad)
                for name, sec in pad_names:
                    sources.append(("gamepad " + name,
                                    lambda now, n=name: pp.pressed(n, now), sec))
                    parts.append("gamepad %s %g sn" % (name, sec))

        if player is None:
            channel = None
            try:
                from eye_gaze.playback import shared_player
                channel = shared_player()
            except Exception:
                pass                    # the siren still plays; it may be cut
            player = TonePlayer(channel=channel)
        core = AlarmCore(player, sources, max_seconds=max_s, log=say)
        w = AlarmWatcher(core, clock=clock, log=say, thread=thread,
                         closers=closers)
        if sources:
            w.start()
            warn("  ACIL ALARM: " + " | ".join(parts)
                 + " (cal / durdur) | "
                 + ("en fazla %g sn" % max_s if max_s > 0 else "sure siniri yok"))
        else:
            warn("  ACIL ALARM: KAPALI (alarm_key bos ya da kullanilamiyor)")
        return w
    except Exception as exc:
        warn("HATA: acil alarm kurulamadi (%s: %s) - surucu calismaya devam"
             " ediyor." % (type(exc).__name__, exc))
        for c in closers:
            try:
                c()
            except Exception:
                pass
        return AlarmWatcher(AlarmCore(_NullPlayer(), [], log=say), log=say,
                            thread=thread)


class _NullPlayer:
    def start(self, now):
        pass

    def tick(self, now):
        pass

    def stop(self):
        pass

    def set_voice(self, wav):
        pass

    def close(self):
        pass
