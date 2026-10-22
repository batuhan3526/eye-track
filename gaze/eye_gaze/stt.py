# gaze/eye_gaze/stt.py
"""Local speech-to-text: microphone capture, end-of-speech detection and
Whisper transcription.

The user has ALS and cannot comfortably press a key twice, so recording ends
by itself once they stop talking. The silence threshold adapts to the room's
noise floor - no manual tuning between a quiet bedroom and a noisy one.
"""
import sys
import threading

import numpy as np

from eye_gaze.i18n import t as _t

SAMPLE_RATE = 16000

# Below this a frame is digital silence (a zeroed buffer at stream start, an
# AGC dip), not a measurement of the room. No real microphone in a real room
# reads this low.
_MIN_ROOM_LEVEL = 1e-4


def rms(frame):
    """Root-mean-square level of one audio frame (float32, -1..1)."""
    a = np.asarray(frame, dtype=np.float32)
    if a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(a * a)))


class SilenceDetector:
    """Decides when the user has finished speaking.

    Feed it fixed-length frames; update() returns True once speech has been
    heard and then `silence_seconds` of quiet has followed.
    """

    def __init__(self, silence_seconds=1.5, frame_seconds=0.1,
                 factor=2.5, floor=0.005, calib_frames=3):
        self._need = max(1, int(round(silence_seconds / max(1e-6, frame_seconds))))
        self._factor = float(factor)
        self._floor = float(floor)
        self._calib_frames = max(1, int(calib_frames))
        self.reset()

    def reset(self):
        self.threshold = None
        self.spoke = False
        self._noise = []
        self._base = None
        self._quiet = 0

    def _set_threshold(self, base):
        self._base = float(base)
        self.threshold = max(self._base * self._factor, self._floor)

    def update(self, frame):
        level = rms(frame)
        if self.threshold is None:
            self._noise.append(level)
            if len(self._noise) >= self._calib_frames:
                self._set_threshold(float(np.mean(self._noise)))
            return False
        # The calibration frames are simply whatever the microphone heard in the
        # first fraction of a second. A user who starts talking immediately -
        # the natural thing to do - has their own VOICE measured as the room's
        # noise floor, which puts the threshold above anything they can produce:
        # no frame ever counts as speech and the recording can only end on its
        # time cap. Tracking the quietest level actually observed repairs that
        # as soon as they draw breath. It can only ever LOWER the floor, so a
        # genuinely noisy room keeps the high threshold it needs.
        # Digital-zero frames are ignored: one of them would drop the threshold
        # to the floor for the whole recording, and with a fan or BiPAP in the
        # room every frame would then count as speech until the 30 s cap.
        if _MIN_ROOM_LEVEL <= level < self._base:
            self._set_threshold(level)
        if level > self.threshold:
            self.spoke = True
            self._quiet = 0
            return False
        if not self.spoke:
            return False           # not started yet: silence means nothing
        self._quiet += 1
        return self._quiet >= self._need


def _default_stream(**kw):
    import sounddevice as sd
    return sd.InputStream(**kw)


def _preload_audio():
    """Import sounddevice now. Its import initialises PortAudio, which takes
    long enough to freeze the cursor if it happens on the first F3 press."""
    import sounddevice  # noqa: F401

# A slow gaze-loop frame can leave several blocks waiting; poll() drains them
# but stops here so a device that always reports more cannot trap the loop.
_MAX_BLOCKS_PER_POLL = 32


class Recorder:
    """Microphone capture that ends itself when the user stops talking.

    poll() is called from the gaze loop once per video frame, on the same
    thread that moves the cursor, and must never block: see the read_available
    guard below.
    """

    def __init__(self, silence_seconds=1.5, max_seconds=30.0, block=1600,
                 stream_factory=None, lead_in_seconds=0.0):
        self._block = int(block)
        # Audio thrown away at the start of every recording: that is where
        # the "recording started" earcon sounds (feedback.capture_lead_in()).
        # Rounded UP to whole blocks - a beep half-kept is a beep kept.
        block_s = self._block / float(SAMPLE_RATE)
        try:
            lead = max(0.0, float(lead_in_seconds))
        except (TypeError, ValueError):
            lead = 0.0
        self._lead_blocks = (int(np.ceil(lead / block_s - 1e-6))
                             if lead > 0 else 0)
        self._skip = 0
        self._max_blocks = max(1, int(round(
            max_seconds / (self._block / float(SAMPLE_RATE)))))
        self._silence = SilenceDetector(
            silence_seconds=silence_seconds,
            frame_seconds=self._block / float(SAMPLE_RATE))
        self._factory = stream_factory or _default_stream
        if stream_factory is None:
            # Best effort: a missing or broken audio stack is reported by
            # start() returning False, never by the constructor raising.
            try:
                _preload_audio()
            except Exception:
                pass
        self._stream = None
        self._chunks = []
        self.active = False
        self.level = 0.0
        # True when the last recording ended without the silence detector ever
        # hearing speech. Its audio is withheld: Whisper fed a quiet room does
        # not return "", it invents subtitle credits ("Altyazi M.K.") that would
        # be pasted and read back. False after a dead device, so the driver can
        # tell "nothing was said" from "the microphone went away".
        self.no_speech = False

    def start(self):
        """Open the microphone. False means no usable input device."""
        if self.active:
            return True
        try:
            self._stream = self._factory(samplerate=SAMPLE_RATE, channels=1,
                                         dtype="float32", blocksize=self._block)
            self._stream.start()
        except Exception:
            # A stream that was opened but would not start still holds the
            # device against every other program on the machine.
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
            self._stream = None
            return False
        self._chunks = []
        self.level = 0.0
        self.no_speech = False
        self._silence.reset()
        self._skip = self._lead_blocks
        self.active = True
        return True

    def poll(self):
        """Read every block that is ready. Returns the finished recording, or
        None while it is still going (and when it ended with no speech - see
        no_speech).

        InputStream.read(n) BLOCKS until n frames exist - a tenth of a second
        per call, once per video frame, would hold the gaze loop at 10 fps for
        the whole dictation. The cursor is this user's only way to operate the
        machine, so a block is only read once the device says it is there.

        All ready blocks are drained, not just one: at under 10 fps (face lost,
        GPU busy) one block per frame falls behind the microphone and the
        PortAudio input buffer overflows. A stream that cannot say what is
        ready (no read_available) gets one block per call, as before.
        """
        if not self.active or self._stream is None:
            return None
        known = hasattr(self._stream, "read_available")
        for _ in range(_MAX_BLOCKS_PER_POLL):
            avail = getattr(self._stream, "read_available", self._block)
            if avail is not None and avail < self._block:
                return None
            try:
                data, _ = self._stream.read(self._block)
            except Exception:
                return self.stop()
            frame = np.asarray(data, dtype=np.float32).reshape(-1)
            if self._skip > 0:
                # Lead-in: neither kept nor shown to the silence detector.
                self._skip -= 1
                if not known:
                    break
                continue
            self._chunks.append(frame)
            self.level = min(1.0, rms(frame) * 4.0)
            if (self._silence.update(frame)
                    or len(self._chunks) >= self._max_blocks):
                return self.stop()
            if not known:
                break
        return None

    def stop(self):
        """Close the microphone and return the audio (float32 mono 16 kHz).
        None when nothing was recorded, or nothing was SAID (see no_speech)."""
        if self._stream is not None:
            # Separate tries: a device yanked out mid-recording fails stop(),
            # and skipping close() then would leave the handle open for good.
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self.active = False
        self.level = 0.0
        if not self._chunks:
            return None
        if not self._silence.spoke:
            self._chunks = []
            self.no_speech = True
            return None
        return np.concatenate(self._chunks).astype(np.float32)


def pick_device(requested, cuda_available):
    """'auto'/'cuda'/'cpu' -> the device we can actually use."""
    req = (requested or "auto").strip().lower()
    if req == "cpu":
        return "cpu"
    return "cuda" if cuda_available else "cpu"


def _cuda_available():
    # CTranslate2 first (faster-whisper, the packages), then torch (the
    # openai-whisper source install) - see accel.cuda_available().
    from eye_gaze import accel
    return accel.cuda_available()


# Whisper's own "this was not speech" verdict, per segment: the thresholds
# whisper.transcribe() itself uses to skip a silent window.
_NO_SPEECH_PROB = 0.6
_LOW_LOGPROB = -1.0

# What Whisper says to a quiet room. Trained on subtitled video, it fills
# silence with subtitle credits and YouTube sign-offs; pasted and read back,
# they put words in the user's mouth. Matched against the WHOLE text only, in
# _fold() form, so a real sentence that contains "tesekkurler" is kept.
_HALLUCINATIONS_RAW = (
    "altyazi m.k.",
    "altyazı m.k.",
    "izlediginiz icin tesekkurler",
    "izlediğiniz için teşekkürler",
    "izlediginiz icin tesekkur ederim",
    "abone olmayi unutmayin",
    "abone olmayı unutmayın",
    "altyazilar amara.org toplulugu tarafindan",
    "altyazılar amara.org topluluğu tarafından",
)

_TR_FOLD = str.maketrans({"ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s",
                          "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u", "ö": "o",
                          "Ö": "o", "ç": "c", "Ç": "c"})


def _fold(text):
    """Case-, punctuation- and Turkish-diacritic-insensitive form.

    The Turkish letters are mapped BEFORE casefold(): "İ".lower() is "i" plus
    a combining dot, which would never match a plain "i"."""
    t = str(text).translate(_TR_FOLD).casefold()
    t = "".join(ch if ch.isalnum() or ch.isspace() else "" for ch in t)
    return " ".join(t.split())


_HALLUCINATIONS = frozenset(_fold(h) for h in _HALLUCINATIONS_RAW)

# The same for English dictation (Whisper's best-known English sign-offs).
# "Thank you." alone is NOT here: an AAC user says it, and it is a real
# sentence far more often than a hallucination.
_HALLUCINATIONS_EN_RAW = (
    "thanks for watching",
    "thank you for watching",
    "thank you for watching and see you next time",
    "please subscribe",
    "subtitles by the amara.org community",
)


def _plain_fold(text):
    """_fold() without the Turkish letter table, for every other language:
    there "I" and "ı" are not one letter's two cases."""
    t = str(text).casefold()
    t = "".join(ch if ch.isalnum() or ch.isspace() else "" for ch in t)
    return " ".join(t.split())


_HALLUCINATIONS_EN = frozenset(_plain_fold(h) for h in _HALLUCINATIONS_EN_RAW)


def _is_hallucination(text, language="tr"):
    """The whole text is one of Whisper's sign-offs for `language` (the
    Turkish list and folding for Turkish, the English ones for English,
    nothing filtered for a language without a list)."""
    lang = str(language or "tr").strip().lower()
    # Whisper takes a code or a language name ("tr" / "turkish"), and a
    # region suffix is harmless: Turkish in every form keeps its filter.
    base = lang.replace("-", "_").split("_", 1)[0]
    if base in ("tr", "turkish"):
        return _fold(text) in _HALLUCINATIONS
    if base in ("en", "english"):
        return _plain_fold(text) in _HALLUCINATIONS_EN
    return False


def _speech_text(res, language="tr"):
    """The text of `res`, minus segments Whisper itself judged non-speech."""
    segs = res.get("segments")
    if isinstance(segs, list) and segs:
        kept = [str(sg.get("text", "")) for sg in segs
                if not (float(sg.get("no_speech_prob", 0.0) or 0.0) > _NO_SPEECH_PROB
                        and float(sg.get("avg_logprob", 0.0) or 0.0) < _LOW_LOGPROB)]
        text = "".join(kept).strip()
    else:
        text = str(res.get("text", "")).strip()
    if _is_hallucination(text, language):
        return ""
    return text


class ModelUnavailable(RuntimeError):
    """The faster-whisper model folder is not on disk and could not be
    fetched. str() is the Turkish sentence for the screen. Not a device
    problem, so it is never retried on the CPU."""


def _openai_whisper_installed():
    """openai-whisper importable? Asked WITHOUT importing it: importing
    whisper imports torch, seconds of work, and model_ready() runs on an F3
    press in the gaze loop."""
    if sys.modules.get("whisper") is not None:
        return True
    import importlib.util
    try:
        return importlib.util.find_spec("whisper") is not None
    except (ImportError, ValueError):
        return False


def _whisper_model_class():
    from faster_whisper import WhisperModel
    return WhisperModel


# CTranslate2 compute types. float16 on the GPU is what openai-whisper's
# fp16 did there; int8 on the CPU is several times faster than float32 for
# the same weights, which is what makes "small" usable on a laptop.
_COMPUTE_TYPE = {"cuda": "float16", "cpu": "int8"}

# Decoding options, pinned rather than left to faster-whisper's defaults
# (which can move between releases): openai-whisper's transcribe() rules -
# the same temperature fallback ladder and the same three thresholds that
# decide when a window is retried or skipped - with beam search 5, which is
# what openai-whisper's command line uses and faster-whisper defaults to.
# Beam 5 costs roughly 1.5-2x the decoding time of greedy on a CPU; dictation
# is a sentence at a time, and accuracy is what this user pays for in
# corrections. No VAD: the recorder already ends on silence and hands over
# speech only.
FASTER_OPTIONS = {
    "beam_size": 5,
    "vad_filter": False,
    "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
    "condition_on_previous_text": True,
    "compression_ratio_threshold": 2.4,
    "log_prob_threshold": -1.0,
    "no_speech_threshold": 0.6,
}


def _ascii_text(text):
    """Console-safe (cp1254) form of a Turkish sentence, without the quotes
    ascii() would add."""
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def _say_line(message):
    print("[dikte] " + _ascii_text(message))


# Makes Transcriber.start_download()'s check-and-set of `downloading` one
# step, so two callers can never start two downloads of the same model.
_FETCH_GUARD = threading.Lock()


class _FasterWhisper:
    """faster_whisper.WhisperModel behind openai-whisper's interface:
    transcribe() returns {"text", "segments": [{"text", "no_speech_prob",
    "avg_logprob"}]}, so _speech_text() filters both engines identically."""

    def __init__(self, model_dir, device):
        self._dir = model_dir
        self.device = device
        # Set when the CPU rebuild after a CUDA failure failed too: nothing
        # is retried after that, on either device.
        self.failed = ""
        self._model = self._build(device)

    def _build(self, device):
        return _whisper_model_class()(self._dir, device=device,
                                      compute_type=_COMPUTE_TYPE[device])

    def _run(self, audio, language):
        segments, _info = self._model.transcribe(audio, language=language,
                                                  **FASTER_OPTIONS)
        # segments is a generator: decoding (and any CUDA fault) happens
        # while it is consumed, inside the caller's try.
        segs = [{"text": str(s.text),
                 "no_speech_prob": float(s.no_speech_prob),
                 "avg_logprob": float(s.avg_logprob)} for s in segments]
        return {"text": "".join(sg["text"] for sg in segs), "segments": segs}

    def transcribe(self, audio, language="tr", fp16=None):
        """fp16 is accepted and ignored: the compute type is the device's."""
        if self._model is None:
            raise RuntimeError(self.failed)
        try:
            return self._run(audio, language)
        except Exception as exc:
            if self.device != "cuda":
                raise
            # CTranslate2 loads cuBLAS/cuDNN at the first sentence, not when
            # the model is built: "Library cublas64_12.dll is not found" only
            # shows up here. Slow on the CPU beats not at all - once: the
            # broken CUDA model is dropped first, so whatever happens next,
            # CUDA is never tried again.
            _say_line("GPU'da cozumlenemedi (%s); bundan sonra CPU'da (int8)"
                      " calisiyor, daha yavas." % ascii(str(exc)[:120]))
            self._model = None
            self.device = "cpu"
            try:
                self._model = self._build("cpu")
            except Exception as cpu_exc:
                self.failed = "CPU'da da acilamadi: %s: %s" % (
                    type(cpu_exc).__name__, cpu_exc)
                raise RuntimeError(self.failed)
            return self._run(audio, language)


def _faster_model_dir(name, progress=None):
    """The whisper-<name> folder, downloaded when missing. Raises
    ModelUnavailable with the Turkish reason."""
    from eye_gaze import downloader, models_manifest
    key = "whisper-" + str(name)
    if key not in models_manifest.MANIFEST:
        sizes = ", ".join(k.split("-", 1)[1] for k in models_manifest.MANIFEST
                          if k.startswith("whisper-"))
        raise ModelUnavailable("Whisper modeli bilinmiyor: %s (olanlar: %s)."
                               % (name, sizes))
    path = downloader.ensure(key, progress=progress)
    if path is None:
        raise ModelUnavailable("Whisper modeli indirilemedi: %s"
                               % (downloader.last_error or key))
    return path


def _faster_loader(name, device):
    model_dir = _faster_model_dir(name)
    if device == "cuda":
        from eye_gaze import accel
        # The optional NVIDIA GPU pack (model_dir()/gpu) has to be loaded
        # before CTranslate2 looks for cuBLAS/cuDNN. Without them a model
        # "on CUDA" builds fine and dies at the first sentence, so this
        # raises here, where Transcriber retries on the CPU at once.
        if not accel.preload_cuda(accel.gpu_lib_dir(), "ctranslate2"):
            raise RuntimeError("CUDA kitapliklari (cuBLAS/cuDNN) yuklenemedi"
                               " - GPU paketi kurulu degil")
    return _FasterWhisper(model_dir, device)


def _default_loader(name, device):
    """openai-whisper when it is installed (source mode on the development machine:
    the same "medium" weights, unchanged), faster-whisper otherwise (the
    packages, which carry no torch)."""
    if _openai_whisper_installed():
        import whisper
        return whisper.load_model(name, device=device)
    return _faster_loader(name, device)


class Transcriber:
    """Whisper wrapper. The model is loaded on first use (it is several GB)
    and a failure never propagates - the driver keeps running without STT.

    `last_error` exists because the alternative is a driver that can only say
    "konusma anlasilamadi, tekrar deneyin" to somebody whose whisper install is
    missing - an instruction to keep retrying something that cannot work.
    """

    def __init__(self, model="medium", device="auto", language="tr", loader=None):
        self._name = str(model)
        self._device_req = device
        self._language = str(language or "tr")
        self._loader = loader or _default_loader
        self._model = None
        self._tried = False
        self.device = ""
        self.ok = True
        self.last_error = ""
        self.downloading = False
        self._progress = None
        self._fetch = None

    def _device(self):
        return pick_device(self._device_req, _cuda_available())

    # --- the faster-whisper model folder (packages only) -------------------
    #
    # The wizard offers the download; skipped or failed there, it happens on
    # the first F3 press instead. Not inside transcribe(): a 1.5 GB download
    # behind the user's first sentence would paste that sentence minutes
    # later into whatever window has the focus by then. So the driver asks
    # model_ready() before recording and, when it is not, start_download()
    # fetches the model on its own thread while the user is told to wait.

    def _model_key(self):
        return "whisper-" + self._name

    def model_ready(self):
        """True when recording may start: openai-whisper (source mode), an
        injected loader, or the faster-whisper model folder complete on disk.
        Cheap (no import, a few stat calls): it runs on an F3 press."""
        if self._loader is not _default_loader or _openai_whisper_installed():
            return True
        from eye_gaze import downloader, models_manifest
        key = self._model_key()
        if key not in models_manifest.MANIFEST:
            return False        # start_download() then names the problem
        return downloader.status(key) == "ok"

    def download_note(self):
        """"%NN" of the running download ("" before its first byte)."""
        prog = self._progress
        if not prog or not prog[1]:
            return ""
        return "%%%d" % int(100 * prog[0] / prog[1])

    def wait_download(self, timeout=None):
        """Block until a running download ends (tests, shutdown). True when
        none is running any more."""
        th = self._fetch
        if th is not None:
            th.join(timeout)
            return not th.is_alive()
        return True

    def start_download(self, say=None, report=print, alert=None):
        """Fetch the model on a daemon thread; never blocks. False when one
        is already running. say(text) speaks Turkish, report(line) prints an
        ASCII console line, alert() sounds the error earcon."""
        with _FETCH_GUARD:
            if self.downloading:
                return False
            self.downloading = True
            self._progress = (0, 0)
        say = say or (lambda text: None)
        alert = alert or (lambda: None)
        self._fetch = threading.Thread(
            target=self._download, args=(say, report, alert), daemon=True,
            name="whisper-indir")
        self._fetch.start()
        return True

    def _download(self, say, report, alert):
        key = self._model_key()
        known = True
        try:
            from eye_gaze import models_manifest
            if key not in models_manifest.MANIFEST:
                known = False
                _faster_model_dir(self._name)       # raises, naming the sizes
            total = sum(it.size for it in models_manifest.items(key))
            report("Dikte modeli indiriliyor: %s (%d MB). Bitince haber"
                   " verilecek; bu sirada sesle yazma kapali."
                   % (key, total // (1024 * 1024)))
            say(_t("stt.say.downloading"))
            step = [0]

            def progress(done, size):
                self._progress = (done, size)
                pct = int(100 * done / size) if size else 0
                if pct >= step[0] + 10:
                    step[0] = pct - pct % 10
                    report("  dikte modeli: %%%d" % step[0])
            _faster_model_dir(self._name, progress)
        except Exception as exc:
            reason = (str(exc) if isinstance(exc, ModelUnavailable)
                      else "Whisper modeli indirilemedi: %s: %s"
                      % (type(exc).__name__, exc))
            self.last_error = reason
            report("SESLE YAZMA HAZIR DEGIL: " + _ascii_text(reason))
            alert()
            if known:
                say(_t("stt.say.download_failed"))
            else:
                say(_t("stt.say.unknown_size"))
        else:
            self.last_error = ""
            report("Dikte modeli hazir - simdi konusabilirsiniz.")
            say(_t("stt.say.ready"))
        finally:
            self.downloading = False

    def transcribe(self, audio):
        """Turkish text for the recorded audio, '' when nothing usable."""
        a = np.asarray(audio, dtype=np.float32).reshape(-1)
        if a.size == 0:
            return ""
        if self._model is None:
            if self._tried:
                # Loading a model that is not there costs seconds of blocked
                # thread and produces the same failure every time.
                return ""
            self._tried = True
            self.device = self._device()
            try:
                self._model = self._loader(self._name, self.device)
            except ModelUnavailable as exc:
                # No model folder (offline first F3, unknown size): the same
                # download would fail on the CPU too. Not latched - back
                # online, the next sentence tries again.
                self.last_error = str(exc)
                self.ok = False
                self._tried = False
                return ""
            except Exception as exc:
                self.last_error = "%s: %s" % (type(exc).__name__, exc)
            if self._model is None and self.device == "cuda":
                # A GPU that is present but out of VRAM (the gaze model lives
                # there too) or a broken CUDA runtime must not cost the user
                # dictation outright: slow on the CPU beats not at all. Once.
                _say_line("GPU'da acilamadi (%s); CPU'da calisiyor, daha"
                          " yavas." % ascii(self.last_error[:120]))
                self.device = "cpu"
                try:
                    self._model = self._loader(self._name, self.device)
                except Exception as exc:
                    self.last_error = "%s: %s" % (type(exc).__name__, exc)
            if self._model is None:
                self.ok = False
                return ""
            self.ok = True
        try:
            # fp16 belongs to the DEVICE: leaving it off on CUDA halves the
            # speed, turning it on for a CPU only earns a warning per run.
            res = self._model.transcribe(a, language=self._language,
                                         fp16=(self.device == "cuda"))
            text = _speech_text(res, self._language)
        except Exception as exc:
            # .get() is inside the try on purpose: a fork that returns a list
            # instead of a dict would otherwise raise straight into the caller.
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self._sync_device()
            return ""
        self._sync_device()
        self.last_error = ""
        return text

    def _sync_device(self):
        """The faster-whisper adapter may have moved itself to the CPU during
        the sentence; device (the label, and fp16 for openai-whisper) follows
        it. A model that cannot run on either device ends dictation for the
        session, saying why (ok False), instead of failing every sentence."""
        if isinstance(self._model, _FasterWhisper):
            self.device = self._model.device
            if self._model.failed:
                self.ok = False


def _clipboard_paste(text):
    """Clipboard + Ctrl+V. Layout-independent, so Turkish characters survive.

    This does overwrite whatever the user had on the clipboard. That is the
    accepted cost: keyboard.write() sends scancodes through the active keyboard
    LAYOUT, and on a machine that is not set to Turkish the dictated sentence
    arrives mangled or half-empty - which is worse than a lost clipboard.

    Returns "pasted" (on the clipboard, Ctrl+V sent), "clipboard" (on the
    clipboard, but Ctrl+V could not be sent) or False (not even that).
    """
    from eye_gaze import platform as _platform
    from eye_gaze.speech import set_clipboard_text
    if not set_clipboard_text(text):
        return False
    try:
        _platform.keyboard_api().send("ctrl+v")
    except Exception:
        return "clipboard"
    return "pasted"


def _type_text(text):
    from eye_gaze import platform as _platform
    _platform.keyboard_api().write(text)
    return True


def insert_outcome(text, writer=None, paster=None):
    """Put `text` into whatever field has focus. Clipboard first (keeps
    Turkish characters), typing as a fallback. What happened:
    "pasted" or "typed" (it landed), "clipboard" (it did not, but it is on
    the clipboard) or "" (neither - only the console line keeps it)."""
    text = (text or "").strip()
    if not text:
        return ""
    paster = paster or _clipboard_paste
    writer = writer or _type_text
    on_clipboard = False
    try:
        r = paster(text)
        if r is True or r == "pasted":
            return "pasted"
        on_clipboard = r == "clipboard"
    except Exception:
        pass
    try:
        if writer(text):
            return "typed"
    except Exception:
        pass
    return "clipboard" if on_clipboard else ""


def insert_text(text, writer=None, paster=None):
    """insert_outcome(), True when the text landed."""
    return insert_outcome(text, writer, paster) in ("pasted", "typed")


def insert_report(outcome, text):
    """The console line for insert_outcome() (ASCII): it claims only what
    happened - "on the clipboard" only when it is, "written" only when it
    landed."""
    if outcome in ("pasted", "typed"):
        return "Yazildi: %s" % ascii(text[:80])
    if outcome == "clipboard":
        return ("Metin kutuya yazilamadi (once yazacaginiz yere tiklayin),"
                " panoda duruyor.")
    return ("Metin kutuya yazilamadi ve panoya da konamadi; soylenen: %s"
            % ascii(text[:200]))
