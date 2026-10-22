# gaze/eye_gaze/tts_backends.py
"""Text-to-speech backends. Every backend returns (synth_to_wav, name) where
synth_to_wav(text) -> WAV bytes. Playback is the caller's job (see speech.py):
that split is what lets the AAC grid pre-render phrases to disk.

Order matters: piper is a local neural voice (fast, natural), onecore is the
Windows Tolga voice (always present, robotic), sapi is the last resort.
On Linux piper is followed by espeak-ng and then spd-say (speech-dispatcher,
present on most desktops for the screen reader) - see auto_order().
xtts is never auto-selected - it is the best Turkish voice here and the only
one that can clone the user's own recorded voice, but it costs ~2 GB of VRAM
and seconds per sentence, so it is only used when asked for by name.

Threading contract (Task 3 depends on this): a backend may be BUILT on one
thread and CALLED from another (Speaker's worker thread). Every backend here
supports that. onecore is agile (WinRT). COM is not - it has apartment affinity
- so the sapi backend keeps one voice object per calling thread.

Diagnostics: select_backend() never raises and never prints, but it does record
why each engine was rejected - see last_errors()/last_request(). Silence is a
bug: if the neural voice fails to come up, the caregiver has to be able to find
out why instead of just hearing a different voice.
"""
import io
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import wave

from . import paths

KNOWN = ("piper", "xtts", "onecore", "sapi", "espeak", "spd")
WINDOWS_AUTO_ORDER = ("piper", "onecore", "sapi")
LINUX_AUTO_ORDER = ("piper", "espeak", "spd")


def _is_windows():
    return os.name == "nt"


def auto_order():
    """Engines engine="auto" tries, best first, on this OS."""
    return WINDOWS_AUTO_ORDER if _is_windows() else LINUX_AUTO_ORDER


# This OS's order, for callers that read a constant.
AUTO_ORDER = auto_order()

# The folders the development machine's source install already filled (KUR-SES.bat):
# ASCII on purpose: a Turkish user name (e.g. "Örnek") makes the home dir non-ASCII, and
# non-ASCII paths already broke MediaPipe model loading in this project.
_LEGACY_PIPER_DIR = r"C:\Users\Public\piper-voices"
# Same rule, same reason, for the ~1.8 GB XTTS checkpoint. Left unset, coqui
# downloads it under the local AppData of the CURRENT user - which with a
# Turkish user name is C:\Users\Örnek\..., i.e. exactly the non-ASCII path that has
# already cost this project three separate failures. TTS_HOME is coqui's own
# override for that directory and is honoured before XDG_DATA_HOME/home.
_LEGACY_XTTS_DIR = r"C:\Users\Public\xtts-models"


def _legacy_source():
    """Source mode on Windows with no data override: the development machine as it was
    before packaging. Its voices and models stay exactly where they are."""
    return (paths.mode() == "source" and _is_windows()
            and not os.environ.get(paths.ENV_DATA_DIR))


def install_hint():
    """What the user can do about a missing voice or dictation model, for a
    console line (ASCII). A packaged build has no KUR-SES.bat: there the
    answer is the launcher's download page. A Linux checkout installs the
    Linux requirements."""
    if paths.mode() != "source":
        return "Ayarlar > Indirmeler bolumune bakin"
    if _is_windows():
        return "KUR-SES.bat calistirin"
    return "packaging/linux/requirements-linux.txt paketlerini kurun"


def _packaged_piper_dir():
    """The voice the program came with: the legacy folder in source mode on
    Windows, otherwise resources/piper (a frozen build's bundle; in a source
    checkout gaze/resources/piper). Computed only, nothing is created."""
    if _legacy_source():
        return _LEGACY_PIPER_DIR
    return paths.resource_path("piper")


def _xtts_dir(create):
    """Where coqui keeps the XTTS checkpoint (source mode only - xtts is
    never packaged). Windows: the legacy folder, else paths' checked ASCII
    folder (coqui loads through C libraries with the same non-ASCII
    weakness). Linux: model_dir() as it is (ruling R19: paths are UTF-8
    bytes there, and the ASCII fallback root is wiped at logout).
    create=False only computes (import-time constant, messages)."""
    if _legacy_source():
        return _LEGACY_XTTS_DIR
    if _is_windows():
        if create:
            return paths.ascii_dir("xtts-models")
        return (paths.ascii_path("xtts-models")
                or os.path.join(paths.model_dir(), "xtts-models"))
    return os.path.join(paths.model_dir(), "xtts-models")


def xtts_model_dir():
    """The XTTS model folder, created on Windows when it is the ASCII one."""
    return _xtts_dir(create=True)


# Import-time values (status lines, messages). What the backends really use
# is piper_voice_dir() / xtts_model_dir(), resolved at the moment of use.
PIPER_VOICE_DIR = _packaged_piper_dir()
XTTS_MODEL_DIR = _xtts_dir(create=False)
XTTS_MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"
# Used when no reference recording is configured. XTTS ships ~50 studio
# speakers; this one reads Turkish cleanly.
XTTS_BUILTIN_SPEAKER = "Ana Florence"
XTTS_SAMPLE_RATE = 24000
# The 17 languages the v2 checkpoint was trained on, exactly as the model card
# spells them (https://huggingface.co/coqui/XTTS-v2). Asking it for anything
# else does not fail - it just reads the text with the wrong phonetics, which
# is why the request is answered with WRONG_LANGUAGE_NOTE instead.
#
# Chinese is "zh-cn", NOT "zh": the checkpoint's language index has no "zh"
# entry, so passing one raises inside coqui on EVERY sentence - a permanent
# silence that this helper exists to prevent, not to cause.
XTTS_LANGUAGES = frozenset((
    "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru",
    "nl", "cs", "ar", "zh-cn", "hu", "ko", "ja", "hi"))

# Chinese is the only entry above that is not a bare ISO 639-1 pair, and
# _xtts_language() reduces a hint to its first two letters - so without this
# map a config saying "zh" (or "zh-CN", or "zh_CN") could never match.
XTTS_LANGUAGE_ALIASES = {"zh": "zh-cn"}

# Appended to a voice name when a voice was found but not in the language that
# was asked for - e.g. Turkish text about to be read out by an English voice.
# Turkish because the user sees it, ASCII because the console prints it.
WRONG_LANGUAGE_NOTE = " (istenen dil yok)"

# SpeechStreamFileMode.SSFMCreateForWrite: create/truncate a SAPI file stream.
_SSFM_CREATE_FOR_WRITE = 3

# Written by select_backend(); read through last_errors()/last_request().
_LAST = {"engine": "", "errors": {}}


def last_errors():
    """{engine: reason} for every engine rejected by the last select_backend().

    Kept even when a later engine succeeded, so the caller can report "piper
    failed, using onecore" instead of just quietly sounding different.
    """
    return dict(_LAST["errors"])


def last_request():
    """Engine name asked for in the last select_backend() call ('auto' etc.).

    With last_errors() this separates "the user explicitly asked for xtts and
    it is missing" from "every engine on the development machine is broken".
    """
    return _LAST["engine"]


def resolve_piper_voice(voice_hint, voice_dir, listdir=os.listdir):
    """Pick a .onnx voice file from voice_dir. Exact name wins, then the
    language prefix ('tr' -> tr_TR-*), then any substring, otherwise raise.

    The language tier sits above the substring tier on purpose: piper names
    voices <lang>_<REGION>-<speaker>-<quality>, so a bare substring search for
    'tr' also matches 'ru_RU-dmi<tr>i-medium' and, because 'ru' sorts before
    'tr', a Russian voice would win over a Turkish one that is sitting right
    there in the same folder.
    """
    try:
        names = [n for n in listdir(voice_dir) if n.endswith(".onnx")]
    except Exception as e:
        raise RuntimeError(f"piper ses klasoru okunamadi: {e}")
    if not names:
        raise RuntimeError(f"piper sesi yok: {voice_dir}")
    hint = (voice_hint or "tr").strip().lower()
    for n in names:
        if n[:-5].lower() == hint:
            return n
    for n in names:
        stem = n[:-5].lower()
        if stem.startswith(hint + "_") or stem.startswith(hint + "-"):
            return n
    for n in names:
        if hint in n.lower():
            return n
    raise RuntimeError(f"'{voice_hint}' esleseni yok: {voice_dir}")


def piper_voice_dir():
    """The folder _piper_backend reads.

    PIPER_VOICE_DIR when set (used as given; on Windows the ASCII guard in
    _piper_backend still refuses a non-ASCII one), else the voice the
    program came with (_packaged_piper_dir). A frozen Windows build whose
    bundle sits under a non-ASCII path - installed under C:\\Users\\Örnek
    - gets a verified ASCII copy of it instead (piper_copy): espeak's C code
    cannot open that path there and kills the process. Linux takes the
    folder as it is (ruling R19, measured: espeak-ng reads a UTF-8 data path
    and synthesizes the same audio from it).

    Raises RuntimeError (ASCII text) when the copy cannot be had, so
    select_backend() moves on to the next voice.
    """
    env = os.environ.get("PIPER_VOICE_DIR")
    if env:
        return env
    src = _packaged_piper_dir()
    if _is_windows() and not src.isascii():
        from . import piper_copy
        return piper_copy.ascii_voice_copy(src)
    return src


def downloaded_piper_dir():
    """Where the downloader puts piper voices (models_manifest VOICE_PACKS,
    e.g. the English voice): model_dir()/piper, or on Windows its ASCII
    twin when model_dir() is not ASCII (downloader._root_for). None when no
    voice is downloadable. Computed only, nothing is created."""
    try:
        from . import downloader, models_manifest
        names = list(models_manifest.VOICE_PACKS.values())
        if not names:
            return None
        item = models_manifest.items(names[0])[0]
        return os.path.dirname(downloader.local_path(item, create=False))
    except Exception:
        return None


def _find_piper_voice(voice_hint, voice_dir, extra_dir=None,
                      listdir=os.listdir):
    """(file name, folder) of the voice to load: the program's own voice
    folder and the downloaded voices' folder, best match first - an exact
    name, then a voice in the language asked for, then anything
    resolve_piper_voice() would take. On a tie the program's own folder
    wins, so a voice that came with the program is used exactly as before.
    Raises the program folder's RuntimeError when neither has a match."""
    best, first_error = None, None
    hint = (voice_hint or "tr").strip().lower()
    for rank_dir, folder in enumerate((voice_dir, extra_dir)):
        if not folder:
            continue
        if rank_dir and not os.path.isdir(folder):
            continue
        try:
            fname = resolve_piper_voice(voice_hint, folder, listdir)
        except RuntimeError as exc:
            if first_error is None:
                first_error = exc
            continue
        if fname[:-5].lower() == hint:
            score = 0
        elif not _piper_wrong_language(fname, voice_hint):
            score = 1
        else:
            score = 2
        if best is None or (score, rank_dir) < best[0]:
            best = ((score, rank_dir), fname, folder)
    if best is None:
        raise first_error or RuntimeError(f"piper sesi yok: {voice_dir}")
    return best[1], best[2]


def _language_prefix(voice_hint):
    """The bare two-letter language code a hint opens with, or "".

    'tr' -> 'tr', 'tr_TR-dfki-medium' -> 'tr', 'zh-cn' -> 'zh'. A hint that is
    not a language request at all ('dfki', 'Tolga') returns "" - the one rule
    every backend here shares about what counts as asking for a language.
    """
    hint = (voice_hint or "").strip().lower()
    want = hint.split("_", 1)[0].split("-", 1)[0]
    if len(want) != 2 or not want.isalpha():
        return ""
    return want


def _hint_tiers(voice_hint):
    """(hint, language_code) - the two tiers a voice hint is matched at.

    This is resolve_piper_voice()'s tier 1 -> tier 2 rule, lifted out so
    onecore and sapi can use it too. Without it those two only ever try the
    literal string, so a tts_voice left at a piper FILE name after someone
    switches tts_engine to onecore matches nothing and Windows keeps its
    English default - Turkish read in an English accent, on every sentence,
    forever.

    The two tiers are matched DIFFERENTLY, and that asymmetry is the point:

    - tier 1 (the whole hint) is a loose substring search over a voice's
      description, as it always has been - that is how a hint like 'Tolga' or
      'dfki' is meant to work.
    - tier 2 is a language code, and a language is matched STRICTLY against the
      voice's language field. A free substring there is not a weaker version of
      the same idea, it is a different and wrong one: 'en' sits inside
      'Hortense', so a loose tier 2 answers a request for English with a French
      voice and, because it counts as a match, suppresses the wrong-language
      note that would have exposed it.

    Either element is "" when there is nothing to try at that tier.
    """
    hint = (voice_hint or "").strip().lower()
    if not hint:
        return "", ""
    want = _language_prefix(hint)
    if want == hint:
        # The hint IS a bare language code, so it is a tier 2 request and only
        # that. Letting it through tier 1 as well would be a free substring
        # search for two letters over a whole description: 'es' and 'ce' both
        # occur in a SAPI token id (a registry path containing "Voices" and
        # "Tokens"), so a Spanish request would be answered by an English voice
        # AND counted as a match, suppressing the note that would expose it.
        return "", want
    return hint, want


def _piper_wrong_language(fname, voice_hint):
    """True when a language was asked for and the picked voice is not in it.

    Piper file names start with the language code (tr_TR-dfki-medium.onnx), so
    the comparison is on the part before the first underscore. A hint that is
    not a language request at all ('dfki') never counts as wrong. An empty hint
    counts as Turkish, because that is what resolve_piper_voice() defaults to.
    """
    want = _language_prefix(voice_hint or "tr")
    if not want:
        return False
    return fname.split("_", 1)[0].lower() != want


def _voice_worker_name():
    from . import ses_isci
    return ses_isci.WORKER_NAME


def _voice_worker_command():
    """None in a source install (piper runs in-process, as always); in a
    packaged build the voice worker's command, or [] when the build has no
    worker executable (piper is then unavailable - never loaded here)."""
    if not paths._frozen():
        return None
    from . import ses_isci
    return ses_isci.worker_command() or []


def _in_process_piper(PiperVoice, path, espeak_dir, speed, volume):
    """synth_to_wav for piper loaded into THIS process (source installs)."""
    holder = {}
    # The docstring's threading contract applies here too, and Task 14 makes
    # concurrent entry designed-in: PhraseCache.warm() calls synth() on a
    # background thread while the Speaker worker may be mid-speak(). The lock
    # spans the synthesis, not just the load: espeak-ng phonemizes through
    # process-global state (note the `global _ESPEAK_PHONEMIZER` in piper's
    # voice.py), and the price of being wrong about its re-entrancy is a dead
    # process, not a wrong answer. Serializing costs ~200 ms of waiting.
    lock = threading.Lock()

    # length_scale is TIME: bigger is slower. Field report - the phrases were
    # intelligible to Whisper but not comfortably to a person ("su istiyorum"
    # heard as "u itiyorum"), and the audio measured clean, so what was missing
    # was not fidelity but time. volume leaves headroom: piper normalises every
    # phrase to peak, and a signal sitting on 0 dBFS overshoots when it is
    # resampled and then Bluetooth-encoded, which lands on the sibilants first.
    syn_config = None
    if abs(speed - 1.0) > 1e-6 or abs(volume - 1.0) > 1e-6:
        from piper import SynthesisConfig
        syn_config = SynthesisConfig(length_scale=float(speed),
                                     volume=float(volume))

    def synth_to_wav(text):
        with lock:
            if "v" not in holder:
                holder["v"] = PiperVoice.load(path, espeak_data_dir=espeak_dir)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                holder["v"].synthesize_wav(text, wf, syn_config=syn_config)
            return buf.getvalue()

    return synth_to_wav


def _piper_backend(voice_hint, speaker_wav="", speed=1.0, volume=1.0):
    """Local neural TTS (ONNX). The model is loaded lazily on the first call
    so driver startup stays fast.

    speaker_wav is unused (see _onecore_backend).

    The three checks below all exist for one reason: piper can kill the
    process outright, and the only moment we can still refuse is here, at
    construction, while select_backend() is still able to fall through to
    Turkish OneCore/Tolga. Once construction returns, that window is shut.

    A packaged build never loads piper here: it runs in the voice worker
    (eye_gaze/ses_isci.py, Task 18b R44) - same checks, same name, same WAV;
    a dead or hung worker raises per sentence and the Speaker falls back.
    """
    worker = _voice_worker_command()
    if worker is None:
        from piper import PiperVoice  # ImportError here -> select_backend moves on

    voice_dir = piper_voice_dir()
    # CHECK 1 (uncatchable, Windows): espeak-ng's C extension silently
    # DISCARDS a non-ASCII espeak_data_dir, falls back to the path compiled
    # into the wheel ("D:/a/piper1-gpl/...", the build machine's) and aborts
    # the process. This is not theoretical here: this user's home directory
    # is C:\Users\Örnek, so pointing PIPER_VOICE_DIR at anything under
    # the profile - the most natural workaround there is - is the lethal one.
    # ascii() escapes the path so the message itself stays console-safe.
    # Linux is exempt (ruling R19): there the path reaches espeak as UTF-8
    # bytes and works - measured in Docker under /tmp/calisma with the
    # wheel's own espeak data hidden, bit-identical audio.
    if _is_windows() and not voice_dir.isascii():
        hint = (_LEGACY_PIPER_DIR if _legacy_source()
                else paths.ascii_path("piper") or "ASCII bir klasor")
        raise RuntimeError(
            "piper ses klasoru ASCII disi karakter iceriyor: "
            f"{ascii(voice_dir)} - espeak bu yolu sessizce yok sayar ve surec "
            f"oldurulur; sesleri {hint} altina koyun")
    # A downloaded voice (the English one) lives in its own folder; the
    # espeak data below always comes from the program's voice folder, which
    # the ASCII check above has just cleared.
    fname, model_dir = _find_piper_voice(voice_hint, voice_dir,
                                         downloaded_piper_dir())
    if _is_windows() and not model_dir.isascii():
        raise RuntimeError(
            "indirilen piper sesinin klasoru ASCII disi karakter iceriyor: "
            f"{ascii(model_dir)}")
    path = os.path.join(model_dir, fname)
    # CHECK 2 (uncatchable): with the espeak data missing, PiperVoice.load()
    # SUCCEEDS (the phonemizer is built lazily inside phonemize()) and the
    # process is then KILLED on the first synthesize() call - no exception, no
    # traceback, try/except cannot catch it. Smoke-testing load() gives a
    # false all-clear.
    # Test for phontab, not for the directory: KUR-SES.bat copies ~373 files
    # with copytree(dirs_exist_ok=True), and any interruption - Ctrl-C, full
    # disk, antivirus lock, reboot - leaves the directory in place but
    # incomplete. phontab sorts 88th of 125, after most of the *_dict payload,
    # so a truncated copy is more likely than not to have made the directory
    # without it. An isdir() check waves that state straight through.
    espeak_dir = os.path.join(voice_dir, "espeak-ng-data")
    if not os.path.isfile(os.path.join(espeak_dir, "phontab")):
        raise RuntimeError(f"espeak verisi yok/eksik: {espeak_dir} ({install_hint()})")
    # CHECK 3 (catchable, but just as mute): PiperVoice.load() reads the
    # model's .onnx.json sidecar. Without it load() raises - which the worker
    # thread survives, but only at the FIRST synthesis, long after
    # select_backend() committed to piper. The user would then press F4 and
    # hear nothing, forever, with Tolga sitting unused. Same window, so same
    # treatment.
    config_path = path + ".json"
    if not os.path.isfile(config_path):
        raise RuntimeError(f"piper ses ayar dosyasi yok: {config_path}")
    if worker is not None and not worker:
        raise RuntimeError("ses sureci (%s) bu pakette yok"
                           % _voice_worker_name())
    if worker:
        from . import ses_isci
        synth_to_wav = ses_isci.piper_synth(worker, path, espeak_dir,
                                            float(speed), float(volume))
    else:
        synth_to_wav = _in_process_piper(PiperVoice, path, espeak_dir, speed,
                                         volume)

    name = f"piper/{fname[:-5]}"
    # The phrase cache is keyed on engine+voice+text. Without the speed in the
    # NAME, slowing the voice down would leave every pre-rendered AAC phrase
    # playing at the old rate and the setting would look like it did nothing.
    # Volume likewise. Both only when changed, so existing caches stay valid.
    if abs(speed - 1.0) > 1e-6:
        name += f"@{speed:g}x"
    if abs(volume - 1.0) > 1e-6:
        name += f"@vol{volume:g}"
    # Say so out loud when the voice is not in the language that was asked
    # for, exactly as onecore and sapi do - Turkish text read by a Russian
    # voice otherwise looks like success.
    if _piper_wrong_language(fname, voice_hint):
        name += WRONG_LANGUAGE_NOTE
    return synth_to_wav, name


def _xtts_language(voice_hint):
    """(language_code, wrong_language) for an xtts request.

    Same rule as _piper_wrong_language(), for the same reason: only a bare
    two-letter code is a language request, so 'dfki' asks for a speaker and
    must not be flagged, and an empty hint means Turkish. The difference is
    that xtts has no voice files to choose from - one checkpoint speaks all 17
    languages - so the hint picks the synthesis language directly. A language
    the checkpoint does not know falls back to Turkish and says so, because
    XTTS would otherwise happily read the text with the wrong phonetics.
    """
    want = _language_prefix(voice_hint or "tr")
    if not want:
        return "tr", False
    # "zh" -> "zh-cn": the checkpoint knows only the long form, see the alias
    # table. Applied after the two-letter test so it cannot widen it.
    want = XTTS_LANGUAGE_ALIASES.get(want, want)
    if want in XTTS_LANGUAGES:
        return want, False
    return "tr", True


def _xtts_reference(speaker_wav):
    """Validate the voice-clone reference recording; returns "" when unset.

    This is user-supplied configuration (config.yaml: tts_speaker_wav) and it
    is checked HERE, at construction, rather than at synthesis time. Two
    reasons, and they pull the same way:

    - The whole point of setting it is to speak in the user's OWN voice.
      Quietly synthesizing with a built-in studio speaker when the path is
      wrong is precisely what select_backend()'s contract forbids - "if the
      user asked for xtts and it is missing they must be told, not handed a
      different voice" - and it is a substitution nobody would ever notice
      was a misconfiguration rather than a bad clone.
    - A typo in a path is made on setup day, with a caregiver present. The
      alternative is finding out on the day they press F4 in an emergency.

    Refusing does mean select_backend("xtts") returns (None, "") and the user
    has no voice until the config is fixed. That is not a regression: an
    explicitly named engine has never had a fall-through, which is why
    engine "auto" - not this path - is what guarantees the user can always
    speak. The failure is loud, at startup, and names the file.
    """
    ref = (speaker_wav or "").strip()
    if not ref:
        return ""
    # Checked before existence: coqui loads this through torchaudio/soundfile,
    # whose Windows backends have the same non-ASCII weakness as the rest of
    # this stack, and a path that decodes wrong reports as "missing" anyway.
    # ascii() escapes it so the message survives a cp1254 console.
    if not ref.isascii():
        raise RuntimeError(
            "referans ses dosyasi ASCII disi karakter iceriyor: "
            f"{ascii(ref)} - kaydi {XTTS_MODEL_DIR} gibi ASCII bir yola koyun")
    if not os.path.isfile(ref):
        raise RuntimeError(f"referans ses dosyasi yok: {ref}")
    try:
        with open(ref, "rb") as f:
            head = f.read(1)
    except OSError as exc:
        raise RuntimeError(
            f"referans ses dosyasi okunamadi: {ref} ({type(exc).__name__})")
    # An interrupted or still-recording capture leaves a zero-byte file behind,
    # which reads as a perfectly present file and dies inside coqui instead.
    if not head:
        raise RuntimeError(f"referans ses dosyasi bos: {ref}")
    return ref


def _xtts_backend(voice_hint, speaker_wav=""):
    """Coqui XTTS v2 - the highest-quality Turkish voice we have, and the one
    that can clone a person's own recorded voice. Heavy (~2 GB VRAM) and slower
    per sentence, so it is opt-in only and never in AUTO_ORDER.

    speaker_wav: 6-30 s of clean speech to clone. Empty -> built-in speaker.
    License: Coqui Public Model License (non-commercial); personal use is fine.

    NOT INSTALLED in the development setup by deliberate decision (voice
    cloning was deferred): coqui-tts is a heavy dependency with a plausible conflict against
    the working torch 2.5.1+cu121 stack, so the code is written and the package
    is not pulled in. Everything below the import is therefore unexercised -
    see the Task 5 report.
    """
    # Config validation runs BEFORE the import on purpose. It makes the
    # diagnostic name the real problem ("referans ses dosyasi yok") instead of
    # the missing package on a machine that has not installed coqui yet, and it
    # keeps these guards testable today, which is the only part of this backend
    # that can be tested at all.
    ref = _xtts_reference(speaker_wav)
    lang, wrong_language = _xtts_language(voice_hint)
    model_dir = os.environ.get("TTS_HOME") or xtts_model_dir()
    # Windows only, like piper's CHECK 1: on Linux the path reaches the C
    # libraries as UTF-8 bytes (ruling R19).
    if _is_windows() and not model_dir.isascii():
        raise RuntimeError(
            "xtts model klasoru ASCII disi karakter iceriyor: "
            f"{ascii(model_dir)} - TTS_HOME degiskenini {XTTS_MODEL_DIR} yapin")
    # Published back so coqui's downloader sees it: it reads TTS_HOME first and
    # only then falls back to the user profile. Set, not defaulted, so an
    # existing value keeps winning - it is the same value we just validated.
    os.environ["TTS_HOME"] = model_dir

    # ImportError here -> select_backend records it and returns (None, ""). It
    # comes first because it is the cheap failure: without coqui-tts there is
    # nothing to import torch for. Importing at construction rather than lazily
    # matches _piper_backend: an unusable install is then known at startup,
    # where the caregiver sees it reported, instead of at the first F4 press.
    from TTS.api import TTS
    import numpy as np
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    holder = {}
    # Model load stays lazy so Speaker.__init__ does not stall the gaze loop
    # (~1.8 GB off disk into VRAM), and the lock is the same contract piper
    # documents: Task 14's PhraseCache.warm() synthesizes on a background
    # thread while the Speaker worker may be mid-speak(), and two threads
    # racing this holder would load the checkpoint twice - 2 GB of VRAM the
    # machine does not have spare.
    lock = threading.Lock()

    def synth_to_wav(text):
        with lock:
            if "t" not in holder:
                os.makedirs(model_dir, exist_ok=True)
                holder["t"] = TTS(XTTS_MODEL).to(device)
            kw = {"text": text, "language": lang}
            if ref:
                kw["speaker_wav"] = ref
            else:
                kw["speaker"] = XTTS_BUILTIN_SPEAKER
            # tts() returns a plain list of floats in [-1, 1]; clipped because
            # a single sample over 1.0 wraps to full-scale negative in int16
            # and lands in the user's ear as a click.
            pcm = np.asarray(holder["t"].tts(**kw), dtype=np.float32)
            # NaN first: every comparison against NaN is False, so np.clip
            # passes it through untouched and (nan * 32767).astype("<i2") is
            # undefined behaviour plus a RuntimeWarning. XTTS does emit NaN
            # audio when the reference recording is poor - which is exactly the
            # input class this backend is built to accept.
            pcm = np.nan_to_num(pcm, nan=0.0, posinf=1.0, neginf=-1.0)
            pcm = np.clip(pcm, -1.0, 1.0)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(XTTS_SAMPLE_RATE)
                wf.writeframes((pcm * 32767.0).astype("<i2").tobytes())
            return buf.getvalue()

    name = "xtts/klon" if ref else "xtts/" + XTTS_BUILTIN_SPEAKER
    # Say so out loud when the language asked for is not one the checkpoint
    # speaks, exactly as the other three backends do.
    if wrong_language:
        name += WRONG_LANGUAGE_NOTE
    return synth_to_wav, name


def _onecore_backend(voice_hint, speaker_wav="", speed=1.0):
    """Windows OneCore voices via WinRT (Microsoft Tolga lives here).

    speaker_wav is unused here: it is the xtts reference-voice path, see
    _xtts_backend. Every backend takes the same arguments so make_backend can
    stay a plain dispatch - it is not dead code, do not delete it.
    """
    import asyncio
    from winsdk.windows.media.speechsynthesis import SpeechSynthesizer
    from winsdk.windows.storage.streams import DataReader

    synth = SpeechSynthesizer()
    hint, lang = _hint_tiers(voice_hint)
    matched = False
    for v in SpeechSynthesizer.all_voices:
        if hint and hint in (v.language + " " + v.display_name).lower():
            synth.voice = v
            matched = True
            break
    # Tier 2: the bare language code, compared against the language field only
    # (v.language is a BCP-47 tag like "tr-TR"). This retry is what turns a
    # tts_voice still holding a piper FILE name after an engine switch into
    # Turkish Tolga instead of the English system default.
    if not matched and lang:
        for v in SpeechSynthesizer.all_voices:
            if v.language.lower().split("-", 1)[0] == lang:
                synth.voice = v
                matched = True
                break

    # tts_speed is piper's length_scale (1.25 = 25 % slower). The user found
    # normal-rate speech hard to follow; a fallback to Tolga must not quietly
    # undo that. speaking_rate is the inverse (0.5 .. 6.0).
    slowed = False
    if abs(float(speed) - 1.0) > 1e-6:
        try:
            synth.options.speaking_rate = max(0.5, min(6.0, 1.0 / float(speed)))
            slowed = True
        except Exception:
            pass                    # very old Windows: normal rate

    async def _to_wav(text):
        stream = await synth.synthesize_text_to_stream_async(text)
        size = int(stream.size)
        reader = DataReader(stream.get_input_stream_at(0))
        await reader.load_async(size)
        buf = bytearray(size)
        reader.read_bytes(buf)
        return bytes(buf)

    def synth_to_wav(text):
        # asyncio.run() creates and disposes its own loop, and WinRT objects
        # are agile, so this is safe from whichever thread owns playback.
        return asyncio.run(_to_wav(text))

    # SpeechSynthesizer.voice is never None - there is always a system default.
    # Say so out loud when it is not the language that was asked for: an
    # English voice reading Turkish text otherwise looks like success.
    name = synth.voice.display_name
    # (hint or lang), not just hint: a bare language request has no tier 1, so
    # testing hint alone would silence the note for exactly those requests.
    if (hint or lang) and not matched:
        name += WRONG_LANGUAGE_NOTE
    if slowed:
        name += f"@{float(speed):g}x"     # part of the phrase-cache key
    return synth_to_wav, "onecore/" + name


def _sapi_backend(voice_hint, speaker_wav=""):
    """Classic SAPI. Cannot see OneCore Turkish voices - last resort only.

    speaker_wav is unused (see _onecore_backend).

    Drives SAPI through comtypes directly rather than through pyttsx3's Engine.
    That is deliberate: pyttsx3's save_to_file() never sets the driver busy
    flag, so on the second call the queued endLoop runs before startLoop is
    entered and startLoop then spins forever on an empty queue. That is a hang,
    not an exception - no try/except can recover it and it would wedge
    Speaker's worker thread for good. SpVoice.Speak() is synchronous and has no
    run loop to wedge.
    """
    import comtypes
    import comtypes.client

    def _new_voice():
        # Idempotent per thread: harmless when this thread already initialized
        # COM (S_FALSE), survivable when it chose a different apartment.
        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        tts = comtypes.client.CreateObject("SAPI.SpVoice")
        hint, lang = _hint_tiers(voice_hint)
        matched = False
        for token in tts.GetVoices():
            if hint and hint in (token.Id + " " + token.GetDescription()).lower():
                tts.Voice = token
                matched = True
                break
        # Tier 2, strict (see _hint_tiers): a SAPI token has no plain language
        # field - the code lives in its id, TTS_MS_TR-TR_TOLGA_11.0 - so the
        # language is matched as the delimited "_tr-" rather than as a free
        # substring, which would find "en" inside "Hortense".
        if not matched and lang:
            for token in tts.GetVoices():
                if ("_" + lang + "-") in token.Id.lower():
                    tts.Voice = token
                    matched = True
                    break
        return tts, matched

    # Build one now, so that an unusable SAPI raises here at construction time
    # where select_backend() can catch it and move on to the next engine.
    first_tts, first_matched = _new_voice()
    local = threading.local()
    local.tts = first_tts

    def _thread_voice():
        """One SpVoice per calling thread - COM has apartment affinity."""
        tts = getattr(local, "tts", None)
        if tts is None:
            tts, _ = _new_voice()
            local.tts = tts
        return tts

    def synth_to_wav(text):
        tts = _thread_voice()
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        cwd = os.getcwd()
        try:
            # CreateObject stays outside the try below: if it raises there is
            # no stream to close and `stream` would be unbound in the finally.
            stream = comtypes.client.CreateObject("SAPI.SpFileStream")
            try:
                stream.Open(path, _SSFM_CREATE_FOR_WRITE)
                previous = tts.AudioOutputStream
                try:
                    tts.AudioOutputStream = stream
                    tts.Speak(text)
                finally:
                    # Restore the speaker output before closing the file
                    # stream, so the next call is not left writing into a dead
                    # stream. Swallowed on failure so it can never skip the
                    # Close() below: an open SpFileStream holds a lock on the
                    # temp wav, os.remove() then fails and %TEMP% fills up
                    # with locked files the AAC pre-render path cannot reuse.
                    try:
                        tts.AudioOutputStream = previous
                    except Exception:
                        pass
            finally:
                # Close() releases the lock so the outer finally can delete
                # the file; it is guarded because closing a stream that never
                # opened must not replace the real error. The cwd is restored
                # because opening a SAPI file stream can move the process
                # working directory and this driver loads models by relative
                # path.
                try:
                    stream.Close()
                except Exception:
                    pass
                os.chdir(cwd)
            with open(path, "rb") as f:
                return f.read()
        finally:
            # Runs on success, on exception and on KeyboardInterrupt, so the
            # AAC pre-render path cannot litter %TEMP% with orphaned wavs.
            try:
                os.remove(path)
            except OSError:
                pass

    name = first_tts.Voice.GetDescription()
    if (voice_hint or "") and not first_matched:
        name += WRONG_LANGUAGE_NOTE
    return synth_to_wav, "sapi/" + name


# --- Linux fallbacks: external programs -------------------------------------
#
# Linux has no Tolga. espeak-ng (robotic, but Turkish and tiny) and spd-say
# (speech-dispatcher, which most desktops install for their screen reader)
# are the voices left when piper cannot come up. Both are programs, not
# libraries: found on PATH at construction, so a missing one is a refusal
# select_backend() records (and startup_report() says), never a silence.

ESPEAK_WPM = 175            # espeak-ng's own default rate, words per minute
_ESPEAK_TIMEOUT = 60.0      # s: rendering a sentence takes well under one
_SPD_TIMEOUT = 300.0        # s: spd-say -w returns when it has been SPOKEN

_which = shutil.which       # tests replace these two


def _run_tool(cmd, data, timeout):
    """(returncode, stdout, stderr) of one external voice program."""
    if data is None:
        # No stdin of ours: a launcher-started (console-less) process may
        # not have a usable one to inherit.
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=timeout)
    else:
        p = subprocess.run(cmd, input=data, capture_output=True,
                           timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def _tool_message(raw):
    text = (raw or b"").decode("utf-8", "replace").strip()[-200:]
    return text.encode("ascii", "backslashreplace").decode("ascii") or "-"


def _tool_language(voice_hint):
    """The language a hint asks for, Turkish when it names none (a piper
    file name such as tr_TR-dfki-medium gives "tr", "dfki" gives "tr")."""
    return _language_prefix(voice_hint or "tr") or "tr"


def _fixed_stream_wav(raw):
    """`espeak-ng --stdout` writes a WAV meant for a pipe: its RIFF and data
    sizes are 0x7ffff... placeholders, which the wave module (and with it
    playback and the phrase cache) takes literally. Rebuilt with the real
    sizes from the samples that actually came."""
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise RuntimeError("espeak-ng WAV vermedi")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        size = struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = pos + 8
        if cid == b"fmt " and size >= 16:
            fmt = struct.unpack("<HHIIHH", raw[body:body + 16])
        elif cid == b"data":
            data = raw[body:body + min(size, len(raw) - body)]
            break
        pos = body + size + (size & 1)
    if fmt is None or data is None or fmt[0] != 1 or fmt[5] % 8:
        raise RuntimeError("espeak-ng WAV cozulemedi")
    channels, rate, width = fmt[1], fmt[2], fmt[5] // 8
    block = max(1, channels * width)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(data[:len(data) - len(data) % block])
    return buf.getvalue()


def _espeak_backend(voice_hint, speaker_wav="", speed=1.0):
    """espeak-ng as a program: text on stdin (so a sentence starting with
    "-" is never read as an option), UTF-8 forced (-b 1) whatever the
    locale, WAV on stdout. speaker_wav is unused (see _onecore_backend)."""
    exe = _which("espeak-ng")
    if not exe:
        raise RuntimeError("espeak-ng bulunamadi (kurmak icin: espeak-ng"
                           " paketi)")
    lang = _tool_language(voice_hint)
    cmd = [exe, "-v", lang, "--stdout", "-b", "1"]
    slowed = abs(float(speed) - 1.0) > 1e-6
    if slowed:
        # tts_speed is piper's length_scale (bigger = slower); espeak counts
        # words per minute (80..450).
        wpm = int(round(ESPEAK_WPM / float(speed)))
        cmd += ["-s", str(max(80, min(450, wpm)))]

    def synth_to_wav(text):
        code, out, err = _run_tool(cmd, text.encode("utf-8"), _ESPEAK_TIMEOUT)
        if code != 0 or not out:
            raise RuntimeError("espeak-ng hata verdi (%s): %s"
                               % (code, _tool_message(err)))
        return _fixed_stream_wav(out)

    name = "espeak/" + lang
    if slowed:
        name += f"@{float(speed):g}x"     # part of the phrase-cache key
    return synth_to_wav, name


def _silent_wav(seconds=0.01, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * max(1, int(rate * seconds)))
    return buf.getvalue()


def _spd_backend(voice_hint, speaker_wav="", speed=1.0):
    """speech-dispatcher through spd-say. It cannot hand back audio - it
    SPEAKS, and -w makes it return once the sentence has been said - so
    synth_to_wav() speaks and returns a few milliseconds of silence for the
    player, and carries speaks_itself=True: Speaker.synth() (the AAC
    phrase cache) must never call it, or pre-rendering would read every
    phrase aloud and store silence for each. speaker_wav is unused."""
    exe = _which("spd-say")
    if not exe:
        raise RuntimeError("spd-say bulunamadi (kurmak icin:"
                           " speech-dispatcher paketi)")
    lang = _tool_language(voice_hint)
    cmd = [exe, "-l", lang, "-w"]
    slowed = abs(float(speed) - 1.0) > 1e-6
    if slowed:
        # spd-say's rate is -100..+100 around normal; length_scale 1.25
        # (25 % longer) becomes -20.
        rate = int(round((1.0 / float(speed) - 1.0) * 100))
        cmd += ["-r", str(max(-100, min(100, rate)))]
    silence = _silent_wav()

    def synth_to_wav(text):
        # "--": a sentence that starts with "-" is text, not an option.
        code, out, err = _run_tool(cmd + ["--", text], None, _SPD_TIMEOUT)
        if code != 0:
            raise RuntimeError("spd-say hata verdi (%s): %s"
                               % (code, _tool_message(err or out)))
        return silence

    def stop():
        """Speaker.stop() (F11): cancel what speech-dispatcher is saying.
        Never blocks or raises - it runs on a key press in the gaze loop.
        -C cancels every client's messages, the screen reader's included:
        the user asked for silence."""
        try:
            subprocess.Popen([exe, "-C"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            pass
    synth_to_wav.speaks_itself = True
    synth_to_wav.stop = stop

    name = "spd/" + lang
    if slowed:
        name += f"@{float(speed):g}x"
    return synth_to_wav, name


def speaks_itself(synth):
    """True for a backend that speaks instead of returning audio (spd)."""
    return bool(getattr(synth, "speaks_itself", False))


def make_backend(name, voice_hint="tr", speaker_wav="", speed=1.0, volume=1.0):
    """Build one backend by name; raises when it is unavailable.

    speed is honoured by piper, OneCore, espeak and spd; volume is piper's
    only - the other engines ignore what they cannot honour rather than
    pretend.
    """
    if name == "piper":
        return _piper_backend(voice_hint, speaker_wav, speed, volume)
    if name == "xtts":
        return _xtts_backend(voice_hint, speaker_wav)
    if name == "onecore":
        return _onecore_backend(voice_hint, speaker_wav, speed)
    if name == "sapi":
        return _sapi_backend(voice_hint, speaker_wav)
    if name == "espeak":
        return _espeak_backend(voice_hint, speaker_wav, speed)
    if name == "spd":
        return _spd_backend(voice_hint, speaker_wav, speed)
    raise RuntimeError(f"bilinmeyen backend: {name}")


# Recorded for an engine refused by strict selection (ASCII, console).
NOT_IN_LANGUAGE = "bu ses istenen dilde degil"


def select_backend(engine="auto", voice_hint="tr", speaker_wav="",
                   factory=make_backend, speed=1.0, volume=1.0,
                   strict=False):
    """Pick a backend. Returns (synth_to_wav, name), or (None, "") if none work.

    An explicitly named engine is never silently downgraded - if the user asked
    for xtts and it is missing they must be told, not handed a different voice.
    strict=True (a program language other than Turkish) also refuses a voice
    that came up in another language (its name carries WRONG_LANGUAGE_NOTE):
    English text read by the Turkish voice is not speech the listener can
    follow. Turkish keeps its old, lenient rule - the field-tested behaviour.
    Never raises and never prints; ask last_errors() for the reasons.
    """
    engine = (engine or "auto").strip().lower()
    order = auto_order() if engine not in KNOWN else (engine,)
    errors = {}
    for name in order:
        try:
            result = factory(name, voice_hint, speaker_wav, speed, volume)
        except Exception as exc:
            # Broad on purpose: a missing DLL, a bad model file and a COM error
            # all mean the same thing here - try the next voice. BaseException
            # stays uncaught so Ctrl-C still works. The reason is never lost.
            errors[name] = f"{type(exc).__name__}: {exc}"
            continue
        if strict and WRONG_LANGUAGE_NOTE in str(result[1]):
            errors[name] = "%s: %s" % (NOT_IN_LANGUAGE, result[1])
            continue
        _remember(engine, errors)
        return result
    _remember(engine, errors)
    return None, ""


def _remember(engine, errors):
    """Publish diagnostics with single assignments (atomic under the GIL)."""
    _LAST["errors"] = errors
    _LAST["engine"] = engine
