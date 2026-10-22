# gaze/eye_gaze/i18n.py
"""The program's language: every text a person sees or hears, by key.

The texts live in resources/lang/<code>.yaml (UTF-8), one file per language;
a new language is one new file, no code change. tr.yaml holds the exact
Turkish the program has always shown, so a Turkish install (the default, and
the development machine's source install, whose config.yaml has no `language` line)
looks and sounds byte for byte as before.

File shape: nested maps grouped by module ("wizard: camera: title: ...");
a text's key is its dotted path ("wizard.camera.title"). Top-level keys that
start with "_" are not texts but facts about the language: `_name` (its own
name, for the pickers: "Türkçe", "English"), `_decimal` (the decimal mark
numbers are shown with) and `_defaults` (the voice, dictation language and
AAC pack that suit it - see setting()).

t(key, **fmt) looks the key up in the chosen language, then English, then
Turkish; a key missing in the chosen language is logged once (ASCII). It
never raises: a missing or broken text must not take a window - or the
driver, this user's only way to operate the computer - down with it. The
worst case is the key itself on screen.

The language is set once at start (set_language, from config.yaml's
`language`) by the launcher and by the driver; the launcher changes it again
when the wizard's or the settings window's choice changes. An unknown code
falls back to Turkish and is said once (log, plus notice() for a window).

Not translated, by ruling: console / gaze.log lines (an ASCII developer log)
and config.yaml's comments.
"""
import os
import re
import sys
import threading
import unicodedata

import yaml

from eye_gaze import paths

DEFAULT = "tr"
# Where a key missing in the chosen language is looked for next.
FALLBACKS = ("en", "tr")
# The languages listed first in a picker; the rest follow by code.
_ORDER = ("tr", "en")

_CODE = re.compile(r"^[a-z]{2,3}([_-][A-Za-z0-9]{2,8})?$")

_lock = threading.RLock()
_catalogs = {}          # code -> (texts, meta) | None (unreadable)
_current = [DEFAULT]
_said = set()           # log lines already written (each said once)
_notice = [""]


def lang_dir():
    """resources/lang in source, portable, installed and frozen builds."""
    return paths.resource_path("lang")


def _log(line):
    """One ASCII line on stderr, once per distinct text. Never raises."""
    with _lock:
        if line in _said:
            return
        _said.add(line)
    try:
        print("[dil] " + ascii_fold(line), file=sys.stderr)
    except Exception:
        pass


def _flatten(tree, prefix, out):
    for k, v in tree.items():
        key = prefix + str(k)
        if isinstance(v, dict):
            _flatten(v, key + ".", out)
        elif v is not None:
            out[key] = v if isinstance(v, str) else str(v)


def _load(code):
    """(texts, meta) of one catalog file, cached; None if it is unusable."""
    with _lock:
        if code in _catalogs:
            return _catalogs[code]
        entry = None
        path = os.path.join(lang_dir(), code + ".yaml")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except FileNotFoundError:
            data = None
        except Exception as exc:
            _log("dil dosyasi okunamadi: %s (%s)"
                 % (path, type(exc).__name__))
            data = None
        if isinstance(data, dict):
            meta = {str(k): v for k, v in data.items()
                    if str(k).startswith("_")}
            texts = {}
            _flatten({k: v for k, v in data.items()
                      if not str(k).startswith("_")}, "", texts)
            entry = (texts, meta)
        elif data is not None:
            _log("dil dosyasi beklenen bicimde degil: %s" % path)
        _catalogs[code] = entry
        return entry


def codes():
    """Codes of the catalog files found now: Turkish, English, then the
    rest by code."""
    try:
        names = os.listdir(lang_dir())
    except OSError:
        names = []
    found = sorted(n[:-5] for n in names
                   if n.endswith(".yaml") and _CODE.match(n[:-5]))
    head = [c for c in _ORDER if c in found]
    return head + [c for c in found if c not in head]


def name(code):
    """The language's own name ("Türkçe", "English"), or its code."""
    cat = _load(code)
    own = cat[1].get("_name") if cat else None
    return str(own) if own else code


def available():
    """[(code, own name)] for a picker - only languages whose file loads."""
    return [(c, name(c)) for c in codes() if _load(c) is not None]


def normalize(code):
    """config.yaml's value as a catalog code ("" / None mean the default).

    Matched against the files found, ignoring case: "pt-BR", "pt_br" and
    "PT_BR" all mean pt_BR.yaml - which on Linux (a case-sensitive file
    system) opens only under its own spelling. A code with no file is
    returned lower-cased (set_language then says it is unknown)."""
    c = str(code if code is not None else "").strip().replace("-", "_")
    if not c:
        return DEFAULT
    low = c.lower()
    if low in (DEFAULT, "en"):
        return low
    for found in codes():
        if found.lower() == low:
            return found
    return low


def set_language(code):
    """Make `code` the language of every later t(); returns the code really
    used. Unknown or unreadable: Turkish, said once (log and notice())."""
    want = normalize(code)
    # Checked before any file is opened: the code becomes a file name, and
    # "../x" or "C:/y" must never reach open().
    if want != DEFAULT and (not _CODE.match(want) or _load(want) is None):
        shown = ascii_fold(want)[:40]
        _log("'%s' dili bulunamadi (resources/lang/%s.yaml yok ya da"
             " okunamiyor); Turkce kullaniliyor." % (shown, shown))
        with _lock:
            _current[0] = DEFAULT
        _notice[0] = _unknown_notice(shown)
        return DEFAULT
    with _lock:
        _current[0] = want
    _notice[0] = ""
    return want


def _unknown_notice(code):
    """The unknown-language sentence in Turkish AND English: the person who
    wrote a language nobody has a file for may read neither the program's
    Turkish nor the code they typed - English is the likeliest bridge."""
    lines = []
    for c in (DEFAULT, "en"):
        text = text_in(c, "i18n.unknown_language", code=code)
        if text and text not in lines:
            lines.append(text)
    return "\n".join(lines)


def text_in(code, key, /, **fmt):
    """The text of `key` in exactly the language `code`, formatted; None
    when that catalog has no such text (no fallback - for the few places
    that need one particular language, e.g. a bilingual notice)."""
    try:
        cat = _load(code)
        text = cat[0].get(key) if cat else None
        if text is None:
            return None
        return text.format(**fmt) if fmt else text
    except Exception:
        return None


def setup(cfg):
    """set_language from a config dict (its `language`, default Turkish)."""
    try:
        return set_language((cfg or {}).get("language", DEFAULT))
    except Exception as exc:          # never stop a start over a language
        _log("dil secilemedi (%s); Turkce kullaniliyor."
             % type(exc).__name__)
        with _lock:
            _current[0] = DEFAULT
        return DEFAULT


def language():
    return _current[0]


def notice():
    """The unknown-language sentence for a window ("" when all is well)."""
    return _notice[0]


def meta(key, default=None, code=None):
    """A "_" fact of the current (or `code`'s) language, e.g. "_decimal"."""
    for c in _chain(code or _current[0]):
        cat = _load(c)
        if cat and key in cat[1]:
            return cat[1][key]
    return default


# --- language-bound settings ---------------------------------------------------
#
# Three settings only make sense in the language the program speaks: the
# piper voice (tts_voice), Whisper's dictation language (stt_language) and
# the AAC phrase pack (aac_root). Each catalog names its own in `_defaults:`.
#
# config.default.yaml (and the development machine's config.yaml, which must not change)
# hold the Turkish values, and the template's keys must equal config.yaml's -
# so "auto" cannot be written there. The rule (ruling R31) is therefore:
#   - no `language` line (Turkish): every value is used exactly as before;
#     only "auto" / "" mean the Turkish default;
#   - another language: "auto", "", a missing key AND a value still equal to
#     the Turkish factory value (for tts_voice / stt_language also the bare
#     code "tr", any case) mean that language's default; any other value
#     was chosen on purpose and wins.
FACTORY = {"tts_voice": "tr_TR-dfki-medium",
           "stt_language": "tr",
           "aac_root": "turkce-aac-paketi"}
AUTO = "auto"
# Settings whose value may be a bare language code ("tr").
_CODE_KEYS = ("tts_voice", "stt_language")


def language_default(key, code=None):
    """`key`'s default for `code` (default: the current language), from
    that catalog's own `_defaults` - never another language's voice or
    dictation language. A catalog without one speaks and dictates by its
    code (the OS voice / Whisper's code for it) and shows the English
    phrase pack (the one more people can read than the Turkish)."""
    code = code or _current[0]
    cat = _load(code)
    table = cat[1].get("_defaults") if cat else None
    if isinstance(table, dict):
        value = table.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    if code == DEFAULT:
        return FACTORY.get(key, "")
    if key in ("tts_voice", "stt_language"):
        return code.split("_", 1)[0].lower()
    if key == "aac_root":
        return language_default(key, "en") if code != "en" else ""
    return ""


def is_default(cfg, key):
    """True when `key` takes the language's default (see the rule above)
    rather than a value somebody chose."""
    raw = (cfg or {}).get(key)
    if raw is None and _current[0] == DEFAULT:
        return False            # Turkish: a missing key keeps its old value
    s = "" if raw is None else str(raw).strip()
    if s.lower() in ("", AUTO):
        return True
    if _current[0] == DEFAULT:
        return False
    # The bare Turkish code counts as the factory value too (ruling R33):
    # config.yaml's own comment says writing "tr" is enough for the voice,
    # and under English that would read English text with the Turkish voice.
    return s == FACTORY.get(key) or (key in _CODE_KEYS
                                     and s.lower() == DEFAULT)


def setting(cfg, key, legacy):
    """The value of a language-bound setting as the program uses it.
    `legacy` is what the caller has always used for a missing key: with
    Turkish (no `language` line) a missing key still gives exactly that."""
    cfg = cfg or {}
    if _current[0] == DEFAULT and key not in cfg:
        return legacy
    if is_default(cfg, key):
        return language_default(key)
    value = str(cfg.get(key))
    return value if _current[0] == DEFAULT else value.strip()


def _chain(code):
    out = []
    for c in (code,) + FALLBACKS:
        if c not in out:
            out.append(c)
    return out


def _lookup(key, code):
    """(text, code it came from) or (None, None)."""
    for c in _chain(code):
        cat = _load(c)
        if cat and key in cat[0]:
            return cat[0][key], c
    return None, None


def t(key, /, **fmt):
    """The text for `key` in the current language, formatted with `fmt`
    ({name} placeholders). Never raises."""
    code = _current[0]
    try:
        text, got = _lookup(key, code)
    except Exception:
        text, got = None, None
    if text is None:
        _log("metin anahtari hicbir dilde yok: %s" % key)
        return key
    if got != code:
        _log("'%s' dilinde eksik metin: %s (%s kullaniliyor)"
             % (code, key, got))
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except Exception as exc:
        _log("metin bicimlenemedi: %s [%s] (%s)"
             % (key, got, type(exc).__name__))
    # The chosen language's text is broken: the Turkish one, then the raw.
    if got != DEFAULT:
        cat = _load(DEFAULT)
        base = cat[0].get(key) if cat else None
        if base is not None:
            try:
                return base.format(**fmt)
            except Exception:
                pass
    return text


_TR_ASCII = str.maketrans({"ı": "i", "İ": "I"})


def ascii_fold(text):
    """`text` with letters' marks dropped and anything else non-ASCII as
    '?' (the cp1254 console; OpenCV's Hershey font)."""
    s = unicodedata.normalize("NFKD", str(text).translate(_TR_ASCII))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.encode("ascii", "replace").decode("ascii")


def t_ascii(key, /, **fmt):
    """t() for text drawn with cv2.putText (ASCII only). The Turkish and
    English catalogs already write these keys in ASCII, so for them this is
    t(); a later language with other letters is folded instead of drawn as
    '???'."""
    return ascii_fold(t(key, **fmt))


def decimal(x):
    """A number as people of this language write it ("%g" form):
    "1,5" in Turkish, "1.5" in English."""
    s = "%g" % x
    mark = str(meta("_decimal", ",") or ",")
    return s.replace(".", mark)


def reset():
    """Forget loaded files and go back to Turkish (tests)."""
    with _lock:
        _catalogs.clear()
        _said.clear()
        _current[0] = DEFAULT
    _notice[0] = ""
