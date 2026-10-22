# gaze/eye_gaze/feedback.py
"""Audible feedback (earcons) and the stop / repeat speech key.

Earcons tell an eyes-only user what just happened - the click went through,
the microphone is listening, the driver paused, the camera dropped out -
without their having to find the console with their eyes. They are generated
tones (no asset files), rendered once into memory, quiet by default, and
played on a worker thread through the shared audio channel (playback.py).

Rules, in the order they matter:

1. Nothing here may block or raise into the gaze loop. event() and update()
   only enqueue, and swallow everything.
2. An earcon never cuts speech: WavPlayer.try_play() SKIPS while a sentence
   holds the channel. A beep that waited would arrive late and mean nothing.
3. While the microphone records, only the two dictation markers sound. Any
   other beep would be captured and handed to Whisper. The START marker is
   also skipped whenever it could no longer finish inside the recorder's
   lead-in (capture_lead_in(): the audio the recorder throws away) - a slow
   frame or a busy worker would otherwise put the beep inside the dictation,
   where it can count as "speech" and make Whisper invent a sentence.

SpeechControl is the speech_stop_key (F11 by default): one press silences
whatever is being said, a second press within hotkey_double_interval says the
last sentence again. Every press does exactly ONE of those - the first half of
a double press stops, the second repeats - so a press is never both.
"""
import functools
import math
import queue
import struct
import threading
import time

from eye_gaze.hotkeys import DoublePress
from eye_gaze.playback import wav_duration

RATE = 22050
_FADE = 0.006          # s; ramps in/out so a tone does not start with a click

# (frequency Hz, milliseconds); frequency 0 is a gap. Rising = something
# starts or comes back, falling = something stops or goes away. Pause/resume
# are slower and lower than the AAC grid so the two pairs are not confused.
SOUNDS = {
    "click": [(1500, 30)],
    "stt_start": [(660, 45), (0, 10), (990, 45)],
    "stt_stop": [(990, 45), (0, 10), (660, 45)],
    "pause": [(587, 110), (0, 30), (392, 160)],
    "resume": [(392, 110), (0, 30), (587, 160)],
    "cal_done": [(523, 90), (659, 90), (784, 160)],
    "error": [(220, 140), (0, 70), (220, 140)],
    "camera_lost": [(330, 120), (0, 40), (247, 120), (0, 40), (196, 200)],
    "camera_back": [(196, 100), (247, 100), (330, 150)],
    "aac_open": [(784, 60), (1047, 90)],
    "aac_close": [(1047, 60), (784, 90)],
}
EVENTS = tuple(SOUNDS)
MARKERS = frozenset(("stt_start", "stt_stop"))

DEFAULT_VOLUME = 0.3


def _clamp_volume(volume):
    try:
        v = float(str(volume).strip().replace(",", "."))
    except Exception:
        return DEFAULT_VOLUME
    if v != v:
        return DEFAULT_VOLUME
    return min(max(v, 0.0), 1.0)


@functools.lru_cache(maxsize=64)
def _render(name, volume):
    amp = volume * 32767.0
    fade = max(1, int(RATE * _FADE))
    samples = []
    for freq, ms in SOUNDS[name]:
        n = int(RATE * ms / 1000.0)
        if freq <= 0:
            samples.extend([0] * n)
            continue
        w = 2.0 * math.pi * freq / RATE
        for i in range(n):
            env = min(1.0, i / fade, (n - 1 - i) / fade)
            samples.append(int(round(amp * env * math.sin(w * i))))
    pcm = struct.pack("<%dh" % len(samples), *samples)
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE",
                         b"fmt ", 16, 1, 1, RATE, RATE * 2, 2, 16,
                         b"data", len(pcm))
    return header + pcm


def render(name, volume=DEFAULT_VOLUME):
    """WAV bytes (16-bit mono) for one earcon. KeyError for an unknown name."""
    if name not in SOUNDS:
        raise KeyError(name)
    return _render(name, _clamp_volume(volume))


class Feedback:
    """Earcon player. event(name) for one-off happenings, update(...) once
    per frame with the driver's state so edges are found here, not spread
    through the driver."""

    # Worst case before the start marker begins: the edge is noticed one frame
    # late (EDGE_BUDGET) and waits in the queue (QUEUE_BUDGET); past either,
    # it is skipped. OUTPUT_LATENCY is the audio stack's delay to the speaker.
    EDGE_BUDGET = 0.10
    QUEUE_BUDGET = 0.05
    OUTPUT_LATENCY = 0.10

    def __init__(self, player, volume=DEFAULT_VOLUME, enabled=True,
                 clock=time.monotonic, max_age=0.6, start_worker=True):
        self._player = player
        self._clock = clock
        self._max_age = float(max_age)
        self.volume = _clamp_volume(volume)
        self.enabled = bool(enabled) and self.volume > 0.0
        self.sounds = {name: render(name, self.volume) for name in EVENTS}
        self._q = queue.Queue(maxsize=8)
        self._state = {}
        self._recording = False
        self._last_update = None
        self._t = None
        if start_worker:
            self._t = threading.Thread(target=self._run, daemon=True,
                                       name="earcons")
            self._t.start()

    # --- called from the gaze loop: enqueue only, never raise ---------------

    def event(self, name):
        """Queue one earcon. True when queued. Never blocks, never raises."""
        try:
            if not self.enabled or name not in self.sounds:
                return False
            if self._recording and name not in MARKERS:
                return False
            self._q.put_nowait((name, self._clock()))
            return True
        except Exception:
            return False

    def update(self, paused=None, aac=None, recording=None, camera_lost=None):
        """Once per frame. None = "not known this frame" and changes nothing.
        The first known value of each is a baseline, not an edge."""
        try:
            now = self._clock()
            gap = None if self._last_update is None else now - self._last_update
            self._last_update = now
            # recording first: it decides whether the others may sound.
            for key, val, on, off in (
                    ("recording", recording, "stt_start", "stt_stop"),
                    ("paused", paused, "pause", "resume"),
                    ("aac", aac, "aac_open", "aac_close"),
                    ("camera_lost", camera_lost, "camera_lost", "camera_back")):
                if val is None:
                    continue
                val = bool(val)
                prev = self._state.get(key)
                self._state[key] = val
                if key == "recording":
                    self._recording = val
                if prev is None or prev == val:
                    continue
                name = on if val else off
                if name == "stt_start" and (gap is None
                                            or gap > self.EDGE_BUDGET):
                    continue        # too late to finish inside the lead-in
                self.event(name)
        except Exception:
            pass

    def capture_lead_in(self):
        """Seconds of microphone audio the recorder must discard so the start
        marker is never transcribed. 0 when earcons are off."""
        if not self.enabled:
            return 0.0
        d = wav_duration(self.sounds["stt_start"]) or 0.0
        return self.EDGE_BUDGET + self.QUEUE_BUDGET + d + self.OUTPUT_LATENCY

    # --- worker side -----------------------------------------------------------

    def _handle(self, item):
        try:
            name, t = item
            limit = self.QUEUE_BUDGET if name == "stt_start" else self._max_age
            if self._clock() - t > limit:
                return
            if self._recording and name not in MARKERS:
                return
            self._player.try_play(self.sounds[name])
        except Exception:
            pass

    def _run(self):
        while True:
            try:
                item = self._q.get()
            except Exception:
                return
            if item is None:
                return
            self._handle(item)

    def drain(self):
        """Play everything queued, on THIS thread. For tests."""
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return
            if item is None:
                return
            self._handle(item)

    def close(self):
        """Stop the worker. Idempotent, bounded, never raises."""
        self.enabled = False
        try:
            while True:
                self._q.get_nowait()
        except Exception:
            pass
        try:
            self._q.put_nowait(None)
        except Exception:
            pass
        if self._t is not None:
            self._t.join(timeout=1.0)


def build_feedback(cfg, player=None, start_worker=True):
    """Feedback from config (feedback_sounds, feedback_volume). Never raises:
    a broken audio stack must cost the beeps, never the driver."""
    try:
        if player is None:
            from eye_gaze.playback import shared_player
            player = shared_player()
        return Feedback(player,
                        volume=cfg.get("feedback_volume", DEFAULT_VOLUME),
                        enabled=bool(cfg.get("feedback_sounds", True)),
                        start_worker=start_worker)
    except Exception:
        return Feedback(_NullPlayer(), enabled=False, start_worker=False)


class _NullPlayer:
    busy = False

    def try_play(self, wav):
        return False


class SpeechControl:
    """speech_stop_key: single press = stop, double press = repeat last."""

    def __init__(self, speaker, key, keys, interval=1.0, report=print):
        self._speaker = speaker
        self.key = str(key)
        self._keys = keys
        self._dp = DoublePress(float(interval))
        self._report = report

    def poll(self, now, suppressing=None):
        """Once per frame. Takes at most one press. Never raises."""
        try:
            if suppressing is not None:
                self._keys.suppressing = bool(suppressing)
            if self._keys.take(self.key):
                self.press(now)
        except Exception:
            pass

    def press(self, now):
        try:
            if self._dp.press(now):
                if self._speaker.repeat():
                    self._say("Son cumle tekrar soyleniyor.")
                else:
                    self._say("Tekrar edilecek cumle yok.")
            else:
                self._speaker.stop()
                self._say("Konusma durduruldu (tekrar icin '%s' x2)."
                          % self.key)
        except Exception:
            pass

    def _say(self, msg):
        try:
            self._report(msg)
        except Exception:
            pass

    def close(self):
        try:
            self._keys.close()
        except Exception:
            pass


def build_speech_control(cfg, speaker, suppress=True, kb=None, report=print):
    """SpeechControl with its own hooked key, or None.

    None when there is nothing to stop (no Speaker) or the key is off: F11 is
    the browser's fullscreen key and must not be swallowed for nothing.
    Its own KeyPresses rather than a slot in the driver's: it is an action
    that is safe with an overlay open (stopping speech never clicks), so it
    does not belong to the overlay routing at all.
    """
    try:
        key = str(cfg.get("speech_stop_key", "f11") or "").strip().lower()
        if not key or speaker is None:
            return None
        from eye_gaze.switch import KeyPresses
        keys = KeyPresses([key], suppress=suppress, kb=kb)
        if key in keys.bad_keys or not keys.ok:
            try:
                report("UYARI: speech_stop_key '%s' kullanilamiyor; konusmayi"
                       " durdurma tusu CALISMAYACAK."
                       % key.encode("ascii", "replace").decode("ascii"))
            except Exception:
                pass
            keys.close()
            return None
        interval = cfg.get("hotkey_double_interval", 1.0)
        try:
            interval = float(interval)
        except Exception:
            interval = 1.0
        return SpeechControl(speaker, key, keys, interval=interval,
                             report=report)
    except Exception:
        return None
