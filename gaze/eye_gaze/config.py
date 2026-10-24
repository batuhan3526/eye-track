# gaze/eye_gaze/config.py
"""Config loader with defaults; user config.yaml overrides keys."""
import difflib
import os

import yaml

from eye_gaze.i18n import t

DEFAULTS = {
    "camera_index": 0,
    "freq": 30.0,
    "mincutoff": 1.0,
    "beta": 0.007,
    "dcutoff": 1.0,
    "calibration_points": 9,
    "screen_margin": 0,
    "recalibrate_key": "c",
    "quit_key": "q",
    # Saccade-aware smoothing (eye_gaze/saccade.py). OFF by default: the
    # current cursor feel is field-approved; the user opts in to try it.
    "saccade_jump": False,
    "saccade_velocity_px_s": 1500.0,
    "saccade_min_px": 120.0,
    "dwell_ring": True,       # dwell progress ring (only drawn when dwell_click is on)
    # Snap-to-target (eye_gaze/snap.py). OFF in the code defaults: a config
    # that lost the line keeps the field-tested plain cursor; config.yaml
    # turns it on.
    "snap_to_targets": False,
    "snap_radius": 120.0,
    "snap_max_target": 0.4,
    "dwell_ring_px": 40,
    # kalibrasyon verisi + ortuk duzeltme (eye_gaze/caldata.py, implicit.py)
    "calibration_refit": True,
    "implicit_correction": True,
    "implicit_max_px": 100.0,
    "implicit_halflife_days": 3.0,
    # Emergency alarm (eye_gaze/alarm.py).
    "alarm_key": "f12",
    "alarm_hold_seconds": 2.0,
    "alarm_max_seconds": 120.0,
    "gamepad_alarm": "",
    "alarm_switch_hold": 0.0,
    # Earcons + stop/repeat speech (feedback.py). None of them clicks, so a
    # defaults-only config still cannot (test_defaults_really_have_no_way_to_click).
    "feedback_sounds": True,
    "feedback_volume": 0.3,
    "speech_stop_key": "f11",
    "aac_scan": False,
    "aac_scan_seconds": 1.5,
    "aac_scan_mode": "row-col",
    "aac_scan_cycles": 3,
    # Click-type menu (eye_gaze/click_menu.py)
    "click_menu_key": "f2",
    "click_menu_on_hold": 0,
    "click_menu_timeout": 10,
    "click_menu_scroll": 5,
}


# Marks a config that came back as bare DEFAULTS because the user's file could
# not be read. Carried IN the returned dict on purpose: cfg is already the
# object every caller passes around, so the flag cannot get separated from the
# config it describes, and there is no module-level state to be overwritten by
# a second load (the mistake tts_backends' last_errors() has to work around).
# A leading underscore keeps it out of the user's namespace, and load_config
# strips it from user data so it can only ever mean "load_config fell back".
DEFAULTS_ONLY_KEY = "_defaults_only"

# Same carrier, different meaning: the user's config.yaml was unusable but the
# LAST KNOWN GOOD copy of it was, so the driver is running on yesterday's real
# settings. Kept separate from DEFAULTS_ONLY_KEY because the consequences are
# opposite - a defaults-only config cannot click and must not be started, a
# backed-up config is a complete working configuration.
BACKUP_KEY = "_from_backup"

# Every setting the driver actually reads. A key that is not here is a typo,
# and a typo is silent today: `left_click_ley: "space"` is valid YAML, differs
# from DEFAULTS, and leaves a machine whose cursor moves and whose buttons do
# nothing. test_every_setting_in_the_shipped_config_is_known keeps this in step
# with config.yaml as later tasks add settings.
KNOWN_KEYS = frozenset("""
camera_index camera_width camera_height camera_fps
freq mincutoff beta dcutoff max_cursor_speed deadzone
calibration_mode calibration_points calibration_poly2 calibration_passes
calibration_samples calibration_file save_calibration
pursuit_speed pursuit_latency calibration_path reach_margin screen_margin
per_eye_features use_head_pose head_translation gaze_median_window
recalibrate_key quit_key pause_key drift_key hotkey_double_interval
suppress_keys ignore_injected_keys left_click_key right_click_key double_click_key drag_key
gamepad_enabled gamepad_left gamepad_right gamepad_double gamepad_drag
gaze_scroll scroll_zone scroll_speed
blink_click blink_closed_threshold blink_hold_seconds
dwell_click dwell_radius dwell_seconds dwell_repeat
dwell_ring dwell_ring_px
snap_to_targets snap_radius snap_max_target
multi_monitor zoom_key zoom_scale zoom_timeout osk_key
tts_key tts_engine tts_voice tts_speaker_wav tts_speed tts_volume
aac_key aac_dir aac_root aac_timeout aac_cache
stt_key stt_engine stt_model stt_device stt_language
stt_silence_seconds stt_max_seconds stt_readback
deep_gaze deep_device deep_backend
saccade_jump saccade_velocity_px_s saccade_min_px
calibration_refit implicit_correction implicit_max_px implicit_halflife_days
alarm_key alarm_hold_seconds alarm_max_seconds gamepad_alarm alarm_switch_hold
feedback_sounds feedback_volume speech_stop_key
aac_scan aac_scan_seconds aac_scan_mode aac_scan_cycles
click_menu_key click_menu_on_hold click_menu_timeout click_menu_scroll
language
""".split())


# Settings the driver feeds to int()/float(). YAML hands a Turkish-written
# "0,15" over as the STRING '0,15', and float('0,15') raises - at startup for
# most of these, but for pursuit_latency only after the user had finished a
# full two-pass calibration. Coerced here, once, before anything reads them.
_INT_KEYS = frozenset("""
camera_index camera_width camera_height camera_fps calibration_points
calibration_passes calibration_samples screen_margin gaze_median_window
scroll_speed
dwell_ring_px
aac_scan_cycles
click_menu_scroll
""".split())
_FLOAT_KEYS = frozenset("""
freq mincutoff beta dcutoff max_cursor_speed deadzone pursuit_speed
reach_margin hotkey_double_interval scroll_zone blink_closed_threshold
blink_hold_seconds dwell_radius dwell_seconds zoom_scale zoom_timeout
aac_timeout tts_speed tts_volume stt_silence_seconds stt_max_seconds
saccade_velocity_px_s saccade_min_px
snap_radius snap_max_target
implicit_max_px implicit_halflife_days
alarm_hold_seconds alarm_max_seconds alarm_switch_hold
feedback_volume
aac_scan_seconds
click_menu_on_hold click_menu_timeout
""".split())


# On/off settings the driver reads with bool(). A quoted "false" or a Turkish
# "hayir" is a non-empty STRING, and bool() of it is True - so
# `dwell_click: "hayir"` used to switch dwell clicking ON.
_BOOL_KEYS = frozenset("""
per_eye_features suppress_keys ignore_injected_keys use_head_pose head_translation gamepad_enabled
save_calibration gaze_scroll blink_click dwell_click dwell_repeat multi_monitor
aac_cache stt_readback saccade_jump dwell_ring calibration_poly2
calibration_refit implicit_correction feedback_sounds aac_scan
snap_to_targets
""".split())
_TRUE_WORDS = frozenset("true yes on 1 evet acik açık".split())
_FALSE_WORDS = frozenset("false no off 0 hayir hayır kapali kapalı".split())

# Settings that raise (or divide by zero) when not strictly positive - and
# they raise AFTER the calibration loaded, on every restart, so BASLAT.bat's
# watchdog would loop forever with no cursor.
_POSITIVE_KEYS = frozenset("freq mincutoff dcutoff pursuit_speed".split())
_MAX_LATENCY = 0.5   # s; pursuit_latency above this is ms written as seconds

# Settings that may say "auto" instead of a value. The camera, deep-model and
# dictation lines depend on the machine, so a new install's template
# (config.default.yaml, spec 6.9) ships them as "auto" and the driver picks
# the value; pursuit_latency's "auto" is learnt during calibration. Without
# this, camera_index: "auto" is "not a number": dropped with a warning on
# every start of every fresh install.
_AUTO_KEYS = frozenset("""
camera_index camera_width camera_height camera_fps
deep_gaze deep_device stt_model stt_device
pursuit_latency
""".split())


def is_auto(value):
    """True when a setting says "auto" (any case, any surrounding spaces)."""
    return isinstance(value, str) and value.strip().lower() == "auto"


def _as_bool(value):
    """value as a bool, accepting Turkish/English words; raises ValueError."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        w = value.strip().lower()
        if w in _TRUE_WORDS:
            return True
        if w in _FALSE_WORDS:
            return False
    raise ValueError(value)


def _as_number(value, kind):
    """value as `kind`, accepting a decimal comma; raises ValueError."""
    if isinstance(value, bool) or value is None:
        raise ValueError(value)
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
    f = float(value)
    if f != f or f in (float("inf"), float("-inf")):
        raise ValueError(value)
    return int(round(f)) if kind is int else f


def _coerce_numbers(cfg, user, report=None):
    """Make every numeric setting the user wrote a real number, in place.

    A value that cannot be one is DROPPED - the driver then uses its own
    default (or DEFAULTS') - and named on the console. Refusing the whole file
    over one bad number would cost every other setting in it.
    `report` replaces the console line (None = _report).
    """
    say = report or _report
    for key in list(user):
        if key in _AUTO_KEYS and is_auto(cfg[key]):
            cfg[key] = "auto"
            continue
        if key in _BOOL_KEYS:
            try:
                cfg[key] = _as_bool(cfg[key])
            except (TypeError, ValueError):
                say(t("config.not_bool", key=key, value=user[key]))
                if key in DEFAULTS:
                    cfg[key] = DEFAULTS[key]
                else:
                    cfg.pop(key, None)
            continue
        if key == "pursuit_latency":
            kind = float
        elif key in _INT_KEYS:
            kind = int
        elif key in _FLOAT_KEYS:
            kind = float
        else:
            continue
        try:
            cfg[key] = _as_number(cfg[key], kind)
        except (TypeError, ValueError):
            say(t("config.not_number", key=key, value=user[key]))
            if key in DEFAULTS:
                cfg[key] = DEFAULTS[key]
            else:
                cfg.pop(key, None)
            continue
        v = cfg[key]
        if key in _POSITIVE_KEYS and v <= 0:
            say(t("config.not_positive", key=key, value=user[key]))
            if key in DEFAULTS:
                cfg[key] = DEFAULTS[key]
            else:
                cfg.pop(key, None)
        elif key == "pursuit_latency" and not 0 <= v <= _MAX_LATENCY:
            say(t("config.bad_latency", value=user[key],
                  max="%s" % _MAX_LATENCY))
            cfg[key] = "auto"


def _can_click(cfg):
    """True when this config binds at least one way to click.

    The same test gaze_driver._has_click_method makes, minus the live gamepad
    reader, which does not exist yet at load time. A config that fails it is
    not a working configuration: the cursor moves and nothing can be pressed.
    """
    if str(cfg.get("left_click_key", "") or ""):
        return True
    if cfg.get("gamepad_enabled", True) and str(cfg.get("gamepad_left", "") or ""):
        return True
    return bool(cfg.get("dwell_click", False)) or bool(cfg.get("blink_click", False))


def used_defaults(cfg):
    """True when this config is bare DEFAULTS after a failed load.

    Callers must treat that as "the user's settings are NOT in effect", not as
    "a working configuration": DEFAULTS has no click key and no feature flags,
    so the machine is not usable as-is and nothing tuned may be persisted from
    it. See gaze_driver's save_calibration guard.
    """
    return bool(cfg.get(DEFAULTS_ONLY_KEY, False))


def used_backup(cfg):
    """True when config.yaml was unusable and the last-known-good copy is in
    effect instead. The settings ARE the user's own (feature flags, click key,
    calibration flags all real), so the driver starts normally - but the
    caregiver's most recent edit to config.yaml is NOT running, and saying so
    is the whole point of this flag.
    """
    return bool(cfg.get(BACKUP_KEY, False))


def backup_path(path):
    """Where the last-known-good copy of `path` lives.

    Sits beside config.yaml as config.son-iyi.yaml: a caregiver who wants to
    recover by hand only has to rename it, and it keeps the .yaml extension so
    it opens in the same editor as the original.
    """
    return os.path.splitext(path)[0] + ".son-iyi.yaml"


def _report(message):
    """Console line about a bad config (Turkish text, ASCII only).

    Module-level so tests can capture it, and it never raises: this runs on the
    path whose whole job is to keep the driver alive.
    """
    try:
        print("[ayar] " + str(message).encode("ascii", "backslashreplace")
              .decode("ascii"))
    except Exception:
        pass


def _report_unknown_keys(user, path):
    """Name settings the driver does not recognise, and what they look like.

    A mistyped key is the quietest way to break this program: YAML accepts it,
    the loader accepts it, and the setting the caregiver believes they changed
    keeps its old value with nothing said. Where the typo is the click key the
    result is a machine that boots, tracks the eyes perfectly and cannot press
    anything.
    """
    for k in sorted(str(k) for k in user
                    if k not in KNOWN_KEYS
                    and k not in (DEFAULTS_ONLY_KEY, BACKUP_KEY)):
        near = difflib.get_close_matches(k, sorted(KNOWN_KEYS), n=1, cutoff=0.75)
        if near:
            _report(f"UYARI: {path} icinde '{k}' diye bir ayar YOK -"
                    f" '{near[0]}' mi demek istediniz? Bu satir GORMEZDEN"
                    " GELINIYOR.")
        else:
            _report(f"UYARI: {path} icinde '{k}' diye bir ayar YOK; bu satir"
                    " GORMEZDEN GELINIYOR.")


def _read_into_defaults(path, announce=False, report=None):
    """Read one YAML file over DEFAULTS.

    Returns (cfg, problem, headline). `problem` is None only when the file
    supplied at least one setting that differs from DEFAULTS; otherwise it is
    the Turkish detail line and `headline` the banner for _fell_back().

    Never raises - it is called on the keep-the-driver-alive path, and it is
    also how the BACKUP is read, where a second failure must degrade rather
    than propagate.
    """
    cfg = dict(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            user = yaml.safe_load(f)
    except Exception as exc:
        # Broad on purpose: a YAML syntax error, a missing file, a locked file
        # and a bad encoding all mean the same thing here - carry on rather
        # than leave the user with no cursor. BaseException is left uncaught so
        # Ctrl-C still works.
        return (cfg, t("config.unreadable", path=path,
                       error=type(exc).__name__, detail=exc),
                "DIKKAT: config.yaml OKUNAMADI")
    if user is not None and not isinstance(user, dict):
        return (cfg, t("config.not_a_map", path=path),
                "DIKKAT: config.yaml BEKLENEN BICIMDE DEGIL")
    if user:
        if announce:
            _report_unknown_keys(user, path)
        cfg.update(user)
        _coerce_numbers(cfg, user, report)
        # Only load_config may set these: a user file that happens to contain
        # either key must not be able to claim it fell back or was restored.
        cfg.pop(DEFAULTS_ONLY_KEY, None)
        cfg.pop(BACKUP_KEY, None)
    # The trigger is the STATE, not the exception. A zero-byte config.yaml (an
    # interrupted save, a full disk, an antivirus lock), a comment-only one and
    # a bare "{}" all parse perfectly and all leave exactly DEFAULTS - the same
    # unusable state _fell_back() describes, but reached in silence, which is
    # worse than the loud one. A DELETED config.yaml was already flagged; a
    # ZEROED one has to be too, and the only condition that covers both is the
    # effective config itself.
    #
    # The reason line may NOT claim the file is empty: a visibly non-empty file
    # whose every key happens to hold its default value (camera_index: 0,
    # quit_key: "q", screen_margin: 0) lands here too, and a caregiver who
    # opens the file and sees settings in it stops believing the next message
    # this program prints.
    if cfg == DEFAULTS:
        return (cfg, t("config.only_defaults", path=path),
                "DIKKAT: config.yaml GECERLI AYAR ICERMIYOR")
    return cfg, None, None


def read_config(path, report=None):
    """(cfg, problem) for one file, merged and coerced exactly as load_config
    would use it - but with no fallback, and without writing
    config.son-iyi.yaml (keeping that copy is the driver's job: only a file the
    driver actually started from is "last known good").

    `problem` is None when load_config would run on this file as it is;
    otherwise it is the Turkish reason it would fall back. A value the loader
    drops (e.g. a word where a number belongs) is reported the way
    load_config reports it - on the console, or to `report(message)` when
    given (a settings window must not print a line on every save).
    """
    cfg, problem, _ = _read_into_defaults(path, report=report)
    return cfg, problem


def can_click(cfg):
    """Public form of _can_click for callers that write settings."""
    return _can_click(cfg)


def _remember_good(path):
    """Keep a byte-for-byte copy of a config.yaml that just loaded cleanly.

    Raw bytes, not a re-dump of the parsed dict: the copy stays a file the
    caregiver can read, edit and rename back, comments and all.

    Written via a temp file + os.replace so a crash or a full disk halfway
    through cannot leave a HALF-WRITTEN backup - the thing that is supposed to
    be the last line of defence is the last thing that may be corrupt. An
    unchanged config is not rewritten at all, so the good copy is not put at
    risk once per startup for nothing.
    """
    bak = backup_path(path)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return
    try:
        with open(bak, "rb") as f:
            if f.read() == data:
                return
    except Exception:
        pass
    tmp = bak + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, bak)
    except Exception as exc:
        try:
            os.remove(tmp)
        except Exception:
            pass
        # Not fatal - today's config is fine, only tomorrow's safety net is
        # missing - but silence here would mean the net is quietly absent for
        # months and only discovered on the day it was needed.
        _report(f"UYARI: son calisan ayarlarin yedegi alinamadi ({bak}):"
                f" {type(exc).__name__}. Ayar dosyasi bozulursa geri donulecek"
                " bir kopya OLMAYACAK.")


def load_config(path=None):
    """Defaults merged with the user's YAML. NEVER raises.

    A malformed config.yaml used to propagate straight out of gaze_driver's
    main() and kill the process before the camera was even opened - taking the
    CURSOR down with it, this user's only means of operating the computer, over
    one mistyped setting. Losing a session's settings is survivable; losing the
    cursor is not.

    Three outcomes, in order of preference:
      1. config.yaml loads      -> it is used, and copied to config.son-iyi.yaml
      2. it does not, but the copy does -> LAST KNOWN GOOD settings are used and
         the driver starts normally; a loud warning says the edit is not live
      3. neither                -> bare DEFAULTS, flagged, and gaze_driver
         refuses to start (DEFAULTS cannot click).

    The likeliest way in is a Windows path pasted into a double-quoted scalar:
    to YAML, "C:\\Users\\..." opens a \\U escape that needs 8 hex digits and
    raises a ScannerError. Hence the forward-slash instruction in config.yaml.
    """
    if not path:
        # An explicit "give me the defaults" call, not a failed load.
        return dict(DEFAULTS)
    cfg, problem, headline = _read_into_defaults(path, announce=True)
    if problem is None:
        # "Last known good" is loaded later as the settings that were WORKING,
        # and the driver starts on it without asking anything else. A file that
        # merely parses is not that: left_click_ley: "space" is valid YAML and
        # produces a machine with no way to click. Copying it into the safety
        # net would turn the next real failure into a locked machine presented
        # as a recovery - so the net keeps the older copy that could be used.
        if _can_click(cfg):
            _remember_good(path)
        else:
            _report(f"UYARI: {path} icinde HICBIR TIKLAMA YONTEMI yok"
                    " (left_click_key, gamepad_left, dwell_click, blink_click"
                    " hepsi bos ya da kapali).")
            _report("Bu dosya 'son calisan ayarlar' yedegi olarak"
                    " KAYDEDILMEDI: boyle bir yedek geri yuklendiginde imlec"
                    " oynar ama hicbir sey tiklanamaz.")
        return cfg
    _report(problem)
    bak = backup_path(path)
    bcfg, bproblem, _ = _read_into_defaults(bak)
    if bproblem is None:
        bcfg[BACKUP_KEY] = True
        _announce_backup(path, bak)
        return bcfg
    # No usable backup either: the pre-existing refuse-to-start path.
    _report(f"Son calisan ayar yedegi de kullanilamadi ({bak}): {bproblem}")
    return _fell_back(dict(DEFAULTS), headline)


def _announce_backup(path, bak):
    """Say - unmissably - that the file the caregiver just edited is NOT live.

    This is the one real risk of last-known-good: a caregiver whose edit broke
    the YAML sees a driver that starts and works, and concludes their change
    simply had no effect. So the message must not read as an aside. It names
    the broken file, says in as many words that today's edit is not running,
    and points at the copy that is.
    """
    _report("DIKKAT: config.yaml KULLANILAMADI - program ESKI (son calisan)"
            " ayarlarla acildi.")
    _report("BU YUZDEN: config.yaml icinde EN SON YAPTIGINIZ DEGISIKLIKLER"
            " SU AN GECERLI DEGIL. Degisiklik ise yaramadi sanmayin -"
            " dosya kullanilamadi (sebebi yukaridaki satirda).")
    _report(f"Su an yururlukte olan ayarlar: {bak}")
    _report(f"DUZELTILECEK DOSYA: {path} - yukaridaki hata satirina bakip"
            " duzeltin ve programi yeniden baslatin.")
    _report("Windows yollarini duz egik cizgiyle yazin, or."
            " C:/Users/Public/ses.wav")


def _fell_back(cfg, headline):
    """Flag and announce a defaults-only config.

    The message must NOT reassure. DEFAULTS carries no click key
    (left_click_key, gamepad_left, dwell_click, blink_click are all absent or
    off), so the cursor moves and nothing can be clicked - for someone whose
    only input is this driver that is a locked machine, not a degraded mode.
    Saying "imlec calisir" here would describe it as working.
    """
    cfg[DEFAULTS_ONLY_KEY] = True
    _report(headline + " - program varsayilan ayarlarla acildi, sizin"
            " ayarlariniz GECERLI DEGIL.")
    _report("Bu haliyle TIKLAMA TUSU TANIMSIZ: imlec hareket eder ama"
            " TIKLAMA YAPILAMAZ. Bilgisayar bu sekilde kullanilamaz.")
    _report("Kayitli kalibrasyon KORUNUYOR (uzerine yazilmayacak);"
            " kalibrasyon ayarlariniz da gecerli degil.")
    _report("config.yaml dosyasini duzeltip programi yeniden baslatin."
            " Windows yollarini duz egik cizgiyle yazin, or."
            " C:/Users/Public/ses.wav")
    return cfg
