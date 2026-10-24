# gaze/tests/test_config.py
import os
from pathlib import Path

import pytest

import eye_gaze.config as config_mod
from eye_gaze.config import (BACKUP_KEY, DEFAULTS, DEFAULTS_ONLY_KEY,
                             backup_path, load_config, used_backup,
                             used_defaults)


def test_defaults_when_no_path():
    cfg = load_config(None)
    assert cfg["camera_index"] == DEFAULTS["camera_index"]
    assert cfg["beta"] == DEFAULTS["beta"]


def test_user_file_overrides(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("camera_index: 2\nbeta: 0.1\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg["camera_index"] == 2
    assert cfg["beta"] == 0.1
    assert cfg["freq"] == DEFAULTS["freq"]


# --- a bad config.yaml must never cost the user the cursor -----------------
#
# load_config() runs at the very top of gaze_driver.main(). Anything it raises
# kills the process before the camera opens, so the person with ALS is left
# with no cursor AND no speech over one mistyped setting. Settings are
# recoverable; the cursor is the only way they can operate the machine at all.


def _capture(monkeypatch):
    said = []
    monkeypatch.setattr(config_mod, "_report", lambda m: said.append(m))
    return said


def test_windows_path_in_a_quoted_scalar_does_not_kill_the_driver(monkeypatch, tmp_path):
    r"""The exact value the config.yaml comment invites a caregiver to type.

    In a YAML double-quoted scalar \U opens a unicode escape needing 8 hex
    digits, so this is a ScannerError - and it used to propagate out of main().
    """
    said = _capture(monkeypatch)
    p = tmp_path / "c.yaml"
    p.write_text('tts_speaker_wav: "C:\\Users\\Public\\ses.wav"\n',
                 encoding="utf-8")
    cfg = load_config(str(p))                     # must not raise
    assert cfg["camera_index"] == DEFAULTS["camera_index"]
    assert cfg["quit_key"] == DEFAULTS["quit_key"]
    assert said, "bozuk config sessizce yutuldu"
    assert any("okunamadi" in m for m in said), said
    assert any(str(p) in m for m in said), "hangi dosya oldugu soylenmiyor"


def test_forward_slash_path_the_comment_now_recommends_parses(tmp_path):
    """Non-vacuity for the test above: the recommended form must actually work."""
    p = tmp_path / "c.yaml"
    p.write_text('tts_speaker_wav: "C:/Users/Public/ses.wav"\n', encoding="utf-8")
    assert load_config(str(p))["tts_speaker_wav"] == "C:/Users/Public/ses.wav"


def test_missing_config_file_falls_back_to_defaults(monkeypatch, tmp_path):
    said = _capture(monkeypatch)
    cfg = load_config(str(tmp_path / "yok.yaml"))
    assert used_defaults(cfg)
    assert {k: v for k, v in cfg.items() if k != DEFAULTS_ONLY_KEY} == DEFAULTS
    assert said


@pytest.mark.parametrize("body, adi", [
    ("", "sifir baytlik dosya"),
    ("# yalniz yorum\n# baska bir yorum\n", "yalniz yorum satirlari"),
    ("{}\n", "bos eslesme"),
])
def test_an_effectively_empty_config_is_flagged_like_a_broken_one(
        monkeypatch, tmp_path, body, adi):
    """These parse perfectly and leave EXACTLY defaults - the same unusable
    state, reached in silence. An interrupted save, a full disk or an AV lock
    zeroes config.yaml; a DELETED file was already flagged, so a ZEROED one
    must be too, or the calibration is destroyed with no warning at all."""
    said = _capture(monkeypatch)
    p = tmp_path / "c.yaml"
    p.write_text(body, encoding="utf-8")
    cfg = load_config(str(p))
    assert {k: v for k, v in cfg.items() if k != DEFAULTS_ONLY_KEY} == DEFAULTS
    assert used_defaults(cfg) is True, adi
    assert said, f"{adi}: sessiz kalindi"
    assert any("TIKLAMA YAPILAMAZ" in m for m in said), said


def test_config_that_is_not_a_mapping_falls_back_to_defaults(monkeypatch, tmp_path):
    said = _capture(monkeypatch)
    p = tmp_path / "c.yaml"
    p.write_text("- bir\n- iki\n", encoding="utf-8")
    cfg = load_config(str(p))                    # no AttributeError from update()
    assert used_defaults(cfg)
    assert said


# --- the fallback must not pretend to be a working machine ------------------
#
# DEFAULTS has 9 keys and NONE of them is a click key: left_click_key,
# gamepad_left, dwell_click and blink_click are all absent or off, so the
# cursor moves and nothing can be clicked. For someone whose only input is this
# driver that is a locked machine, not a degraded mode - and the saved
# calibration must survive it untouched.


def test_defaults_only_config_is_flagged(monkeypatch, tmp_path):
    _capture(monkeypatch)
    p = tmp_path / "c.yaml"
    p.write_text('x: "C:\\Users\\Public\\ses.wav"\n', encoding="utf-8")
    assert used_defaults(load_config(str(p))) is True


def test_a_good_config_is_never_flagged(monkeypatch, tmp_path):
    _capture(monkeypatch)
    p = tmp_path / "c.yaml"
    p.write_text("camera_index: 2\n", encoding="utf-8")
    assert used_defaults(load_config(str(p))) is False
    # load_config(None) is an explicit "give me the defaults" API call, not a
    # failed load; the driver never uses it.
    assert used_defaults(load_config(None)) is False
    # A file that sets ONE real key is a real config, however small.
    tiny = tmp_path / "t.yaml"
    tiny.write_text('left_click_key: "space"\n', encoding="utf-8")
    assert used_defaults(load_config(str(tiny))) is False


def test_a_user_file_cannot_claim_it_fell_back(monkeypatch, tmp_path):
    """Only load_config may set the flag - otherwise a stray key in the user's
    YAML would silently disable calibration saving forever."""
    _capture(monkeypatch)
    p = tmp_path / "c.yaml"
    p.write_text(f"{DEFAULTS_ONLY_KEY}: true\ncamera_index: 3\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert used_defaults(cfg) is False
    assert cfg["camera_index"] == 3


def test_defaults_really_have_no_way_to_click():
    """Non-vacuity for the message below: if this ever stops being true the
    warning is wrong and must be rewritten."""
    for key in ("left_click_key", "gamepad_left", "dwell_click", "blink_click"):
        assert not DEFAULTS.get(key), key


def test_fallback_message_does_not_claim_the_cursor_works(monkeypatch, tmp_path):
    said = _capture(monkeypatch)
    p = tmp_path / "c.yaml"
    p.write_text("{bozuk\n", encoding="utf-8")
    load_config(str(p))
    text = " ".join(said)
    assert "imlec calisir" not in text, "kullanilamaz durum calisiyor gibi anlatiliyor"
    assert "TIKLAMA YAPILAMAZ" in text, text
    assert "kullanilamaz" in text, text
    assert "KORUNUYOR" in text, "kalibrasyonun korundugu soylenmiyor"
    assert "yeniden baslatin" in text, "ne yapilacagi soylenmiyor"


def test_report_escapes_non_ascii_for_a_cp1254_console(capsys):
    """The config path this reports on is C:\\Users\\Örnek\\... - a raw
    print of it can raise UnicodeEncodeError on the console it is printing to,
    which would turn the recovery into the crash it was avoiding."""
    config_mod._report("yol: C:\\Users\\\u00d6rnek\\config.yaml")
    out = capsys.readouterr().out
    assert out.isascii(), out
    assert "[ayar]" in out
    assert "rnek" in out


def test_report_never_raises_on_a_broken_stdout(monkeypatch):
    """The reporter runs on the keep-the-driver-alive path."""
    def boom(*a, **k):
        raise UnicodeEncodeError("cp1254", "x", 0, 1, "bozuk")
    monkeypatch.setattr("builtins.print", boom)
    config_mod._report("deneme")                 # must not raise


# --- the stated reason must be true ----------------------------------------


def test_the_defaults_only_reason_does_not_claim_an_empty_file(monkeypatch,
                                                               tmp_path):
    """A visibly non-empty config whose every key holds its default value lands
    on the same halt. The halt is right; "dosya bos" was not - and a caregiver
    who opens the file and sees settings in it stops believing this program.
    """
    said = _capture(monkeypatch)
    p = tmp_path / "c.yaml"
    body = 'camera_index: 0\nquit_key: "q"\nscreen_margin: 0\n'
    p.write_text(body, encoding="utf-8")
    cfg = load_config(str(p))
    # Non-vacuity: the file really is non-empty and really does yield DEFAULTS.
    assert p.read_text(encoding="utf-8").strip() == body.strip()
    assert used_defaults(cfg) is True
    reason = next(m for m in said if str(p) in m)
    assert "dosya bos ya da yalniz yorum satirlari iceriyor" not in reason, reason
    assert "varsayilandan FARKLI hicbir ayar bulunamadi" in reason, reason
    assert "zaten varsayilan degerdedir" in reason, reason


# --- last known good: a bad edit must not cost the cursor -------------------
#
# Task 6 made a malformed or effectively-empty config.yaml exit the driver.
# Honest, but an antivirus lock or a caregiver's typo then leaves the person
# with NO cursor until somebody else intervenes. Yesterday's settings are a
# complete working configuration; DEFAULTS is not.


def _good(path, body='left_click_key: "space"\ncamera_index: 4\n'):
    """Load a valid config once so the last-known-good copy exists."""
    path.write_text(body, encoding="utf-8")
    load_config(str(path))
    assert backup_path(str(path)) != str(path)
    return backup_path(str(path))


def test_a_successful_load_keeps_a_byte_identical_copy(monkeypatch, tmp_path):
    _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    body = '# yorum korunmali\nleft_click_key: "space"\ncamera_index: 4\n'
    bak = _good(p, body)
    assert Path(bak).read_bytes() == p.read_bytes(), \
        "yedek ham kopya degil"
    assert b"# yorum korunmali" in p.read_bytes(), "yorum satiri korunmadi"


@pytest.mark.parametrize("body, adi", [
    ('tts_speaker_wav: "C:\\Users\\Public\\ses.wav"\n', "bozuk YAML"),
    ("", "sifir baytlik dosya"),
    ("# yalniz yorum\n", "yalniz yorum"),
    ("- bir\n- iki\n", "eslesme degil"),
])
def test_a_broken_config_falls_back_to_the_last_known_good_copy(
        monkeypatch, tmp_path, body, adi):
    _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    _good(p)
    said = _capture(monkeypatch)
    p.write_text(body, encoding="utf-8")
    cfg = load_config(str(p))
    assert used_defaults(cfg) is False, f"{adi}: surucu baslatilmayacak"
    assert used_backup(cfg) is True, adi
    assert cfg["camera_index"] == 4, f"{adi}: eski ayarlar yururlukte degil"
    assert cfg["left_click_key"] == "space", f"{adi}: tiklama tusu yok"
    assert any("son calisan" in m for m in said), said


def test_the_backup_warning_is_unmissable_and_names_the_file(monkeypatch,
                                                             tmp_path):
    """The one real risk of last-known-good: a caregiver whose edit broke the
    YAML sees a working driver and concludes the change simply did nothing."""
    _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    bak = _good(p)
    said = _capture(monkeypatch)
    p.write_text("{bozuk\n", encoding="utf-8")
    load_config(str(p))
    text = " ".join(said)
    assert "DIKKAT" in text, text
    assert "GECERLI DEGIL" in text, "degisikligin gecmedigi soylenmiyor"
    assert "Degisiklik ise yaramadi sanmayin" in text, text
    assert str(p) in text, "duzeltilecek dosya adi verilmiyor"
    assert bak in text, "yururlukteki yedek dosya adi verilmiyor"
    assert "yeniden baslatin" in text, text


def test_without_a_usable_backup_the_refuse_to_start_path_still_applies(
        monkeypatch, tmp_path):
    """Task 6's halt is kept, not replaced: DEFAULTS cannot click."""
    said = _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text("{bozuk\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert used_defaults(cfg) is True
    assert used_backup(cfg) is False
    assert any("TIKLAMA YAPILAMAZ" in m for m in said), said


def test_a_corrupt_backup_falls_through_to_the_halt(monkeypatch, tmp_path):
    """The safety net's own failure mode: a half-written or shredded backup
    must not be loaded as if it were the user's settings."""
    said = _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text("{bozuk\n", encoding="utf-8")
    with open(backup_path(str(p)), "w", encoding="utf-8") as f:
        f.write("{bu da bozuk\n")
    cfg = load_config(str(p))
    assert used_defaults(cfg) is True
    assert used_backup(cfg) is False
    assert any("Son calisan ayar yedegi de kullanilamadi" in m for m in said), said


def test_a_broken_config_never_overwrites_the_good_copy(monkeypatch, tmp_path):
    """Otherwise the second start after a bad edit would have nothing to fall
    back to - the deeper hole this whole mechanism could dig."""
    _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    bak = _good(p)
    before = Path(bak).read_bytes()
    p.write_text("{bozuk\n", encoding="utf-8")
    for _ in range(3):
        assert used_backup(load_config(str(p))) is True
    assert Path(bak).read_bytes() == before


def test_a_repaired_config_takes_over_again(monkeypatch, tmp_path):
    """Non-vacuity: the backup must not become sticky."""
    _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    _good(p)
    p.write_text("{bozuk\n", encoding="utf-8")
    assert used_backup(load_config(str(p))) is True
    p.write_text('left_click_key: "space"\ncamera_index: 9\n', encoding="utf-8")
    cfg = load_config(str(p))
    assert used_backup(cfg) is False
    assert cfg["camera_index"] == 9


def test_a_user_file_cannot_claim_it_came_from_the_backup(monkeypatch, tmp_path):
    _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text(f"{BACKUP_KEY}: true\ncamera_index: 3\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert used_backup(cfg) is False
    assert cfg["camera_index"] == 3


def test_an_unwritable_backup_is_reported_but_not_fatal(monkeypatch, tmp_path):
    said = _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    # A click route is part of the fixture now: a config that cannot click is
    # no longer copied at all, so without one there would be no write to fail.
    p.write_text('left_click_key: "space"\ncamera_index: 4\n', encoding="utf-8")
    real = open

    def boom(path, mode="r", *a, **k):
        if "w" in mode or "b" in mode and "w" in mode:
            raise OSError("yazma korumali")
        return real(path, mode, *a, **k)

    monkeypatch.setattr(config_mod, "open", boom, raising=False)
    cfg = load_config(str(p))                      # must not raise
    assert cfg["camera_index"] == 4
    assert used_defaults(cfg) is False
    assert any("yedegi alinamadi" in m for m in said), said


# --- "last known good" has to mean WORKING, not merely PARSEABLE ------------
#
# config.son-iyi.yaml is loaded as "the settings that were working" and the
# driver starts on it without further checks. A file that parses is not the
# same thing: `left_click_ley: "space"` is valid YAML, differs from DEFAULTS,
# and leaves the machine with no way to click at all. Copying that into the
# safety net turns the next real failure into a locked machine presented as a
# recovery.


def test_a_config_that_cannot_click_is_not_kept_as_the_good_copy(monkeypatch,
                                                                 tmp_path):
    said = _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text('left_click_ley: "space"\ncamera_index: 4\n', encoding="utf-8")
    load_config(str(p))
    assert not os.path.exists(backup_path(str(p))), \
        "tiklanamayan ayar dosyasi 'son calisan' yedegi olarak kaydedildi"
    assert any("TIKLAMA" in m for m in said), said


def test_an_earlier_working_copy_survives_a_click_less_edit(monkeypatch,
                                                            tmp_path):
    """The consequence that matters: the good copy must still be the one that
    could actually be used."""
    _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    bak = _good(p)
    p.write_text('left_click_ley: "space"\ncamera_index: 9\n', encoding="utf-8")
    load_config(str(p))
    p.write_text("{bozuk\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert used_backup(cfg) is True
    assert cfg["left_click_key"] == "space", "tiklanamayan ayarlar geri geldi"
    assert cfg["camera_index"] == 4
    assert os.path.exists(bak)


@pytest.mark.parametrize("body", [
    'dwell_click: true\ncamera_index: 4\n',
    'blink_click: true\ncamera_index: 4\n',
    'gamepad_left: "A"\ncamera_index: 4\n',
])
def test_any_bound_click_route_is_good_enough_to_keep(monkeypatch, tmp_path, body):
    """Non-vacuity: the gate tests for a click ROUTE, not for left_click_key."""
    _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text(body, encoding="utf-8")
    load_config(str(p))
    assert os.path.exists(backup_path(str(p))), body


# --- a mistyped key is silent today ----------------------------------------


def test_a_mistyped_key_is_named_with_the_key_it_resembles(monkeypatch, tmp_path):
    said = _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text('left_click_ley: "space"\n', encoding="utf-8")
    load_config(str(p))
    text = " ".join(said)
    assert "left_click_ley" in text, text
    assert "left_click_key" in text, "dogru anahtar onerilmiyor"


def test_known_keys_are_not_reported(monkeypatch, tmp_path):
    said = _capture(monkeypatch)
    p = tmp_path / "config.yaml"
    p.write_text('left_click_key: "space"\ndwell_seconds: 1.5\n', encoding="utf-8")
    load_config(str(p))
    assert not any("bilinmiyor" in m for m in said), said


def test_every_setting_in_the_shipped_config_is_known(shipped_config):
    """Keeps KNOWN_KEYS honest as later tasks add settings: a new key in
    config.yaml that nobody added here would be reported as a typo to every
    user."""
    import yaml as _yaml
    with open(shipped_config, "r", encoding="utf-8") as f:
        shipped = _yaml.safe_load(f)
    unknown = sorted(set(shipped) - set(config_mod.KNOWN_KEYS))
    assert unknown == [], unknown


# --- numbers typed the Turkish way must not crash the driver ----------------
#
# "0,15" is how a Turkish caregiver writes 0.15. YAML reads it as the STRING
# '0,15', and the driver's float() on it raised - for pursuit_latency only
# AFTER the user had finished a full two-pass calibration, throwing it away.


def _load(monkeypatch, tmp_path, body):
    said = _capture(monkeypatch)
    p = tmp_path / "c.yaml"
    p.write_text("left_click_key: space\n" + body, encoding="utf-8")
    return load_config(str(p)), said


def test_decimal_comma_is_read_as_a_number(monkeypatch, tmp_path):
    cfg, said = _load(monkeypatch, tmp_path,
                      "pursuit_latency: 0,15\nmincutoff: 0,2\ntts_speed: '1,4'\n")
    assert cfg["pursuit_latency"] == pytest.approx(0.15)
    assert cfg["mincutoff"] == pytest.approx(0.2)
    assert cfg["tts_speed"] == pytest.approx(1.4)


def test_integer_settings_come_back_as_int(monkeypatch, tmp_path):
    cfg, _ = _load(monkeypatch, tmp_path,
                   "deadzone: '20'\ncalibration_points: 16.0\ncamera_index: '1'\n")
    assert cfg["deadzone"] == 20 and isinstance(cfg["calibration_points"], int)
    assert cfg["camera_index"] == 1


def test_a_non_number_is_dropped_named_and_not_fatal(monkeypatch, tmp_path):
    cfg, said = _load(monkeypatch, tmp_path,
                      "max_cursor_speed: hizli\nmincutoff: 0.3\n")
    assert "max_cursor_speed" not in cfg          # driver default applies
    assert cfg["mincutoff"] == 0.3
    assert any("max_cursor_speed" in m and "hizli" in m for m in said)


def test_a_bad_number_for_a_default_key_falls_back_to_the_default(monkeypatch,
                                                                  tmp_path):
    cfg, said = _load(monkeypatch, tmp_path, "freq: yok\n")
    assert cfg["freq"] == DEFAULTS["freq"]
    assert any("freq" in m for m in said)


def test_pursuit_latency_auto_stays_auto(monkeypatch, tmp_path):
    cfg, said = _load(monkeypatch, tmp_path, "pursuit_latency: Auto\n")
    assert cfg["pursuit_latency"] == "auto"
    assert not any("pursuit_latency" in m for m in said)


def test_a_boolean_is_not_a_number(monkeypatch, tmp_path):
    cfg, said = _load(monkeypatch, tmp_path, "deadzone: true\n")
    assert "deadzone" not in cfg
    assert any("deadzone" in m for m in said)


def test_suppress_keys_is_a_known_setting():
    assert "suppress_keys" in config_mod.KNOWN_KEYS


# --- on/off written as a quoted word must not flip a feature ON ------------
#
# bool("hayir") and bool("false") are True. `dwell_click: "hayir"` switched
# dwell clicking ON - stray clicks wherever the user rested their eyes.


@pytest.mark.parametrize("word", ['"false"', '"hayir"', "'Hayır'", "kapali",
                                  '"KAPALI"', "no", "off", "0"])
def test_off_words_switch_a_feature_off(monkeypatch, tmp_path, word):
    cfg, said = _load(monkeypatch, tmp_path, f"dwell_click: {word}\n")
    assert cfg["dwell_click"] is False
    assert not said


@pytest.mark.parametrize("word", ['"true"', '"evet"', "'Açık'", "acik", "yes",
                                  "1"])
def test_on_words_switch_a_feature_on(monkeypatch, tmp_path, word):
    cfg, _ = _load(monkeypatch, tmp_path, f"blink_click: {word}\n")
    assert cfg["blink_click"] is True


def test_an_unknown_on_off_word_falls_back_to_the_default_and_is_named(
        monkeypatch, tmp_path):
    cfg, said = _load(monkeypatch, tmp_path, "saccade_jump: belki\n")
    assert cfg["saccade_jump"] is DEFAULTS["saccade_jump"]
    assert any("saccade_jump" in m and "belki" in m for m in said)


def test_a_mode_string_setting_is_not_treated_as_on_off(monkeypatch, tmp_path):
    cfg, said = _load(monkeypatch, tmp_path, 'deep_gaze: "auto"\n')
    assert cfg["deep_gaze"] == "auto" and not said


# --- values that crash the filter after the calibration loaded -------------
#
# freq/mincutoff/dcutoff of 0 raise inside OneEuroFilter AFTER calibration,
# on every restart: BASLAT.bat's watchdog would loop with no cursor, and the
# file would also have been copied as the "last good" config.


@pytest.mark.parametrize("key", ["freq", "mincutoff", "dcutoff"])
def test_a_zero_filter_setting_falls_back_to_the_default(monkeypatch, tmp_path,
                                                         key):
    from eye_gaze.oneeuro import OneEuroFilter
    cfg, said = _load(monkeypatch, tmp_path, f"{key}: 0\n")
    assert cfg[key] == DEFAULTS[key]
    assert any(key in m for m in said)
    OneEuroFilter(cfg["freq"], cfg["mincutoff"], cfg["beta"], cfg["dcutoff"])


def test_a_negative_pursuit_speed_is_dropped(monkeypatch, tmp_path):
    cfg, said = _load(monkeypatch, tmp_path, "pursuit_speed: -480\n")
    assert "pursuit_speed" not in cfg
    assert any("pursuit_speed" in m for m in said)


def test_pursuit_latency_written_in_milliseconds_becomes_auto(monkeypatch,
                                                              tmp_path):
    cfg, said = _load(monkeypatch, tmp_path, "pursuit_latency: 150\n")
    assert cfg["pursuit_latency"] == "auto"
    assert any("pursuit_latency" in m for m in said)


def test_a_sane_pursuit_latency_is_kept(monkeypatch, tmp_path):
    cfg, said = _load(monkeypatch, tmp_path, "pursuit_latency: 0.18\n")
    assert cfg["pursuit_latency"] == pytest.approx(0.18) and not said


# --- ranges: what the settings window offers, checked on every load too ---------

@pytest.mark.parametrize("body,key,want", [
    ("deadzone: -5\n", "deadzone", 0.0),
    ("max_cursor_speed: 99999\n", "max_cursor_speed", None),
    ("dwell_seconds: 0.01\n", "dwell_seconds", None),
    ("zoom_scale: 5\n", "zoom_scale", 0.9),
    ("feedback_volume: 3\n", "feedback_volume", 1.0),
    ("calibration_passes: 0\n", "calibration_passes", 1),
    ("blink_closed_threshold: 2\n", "blink_closed_threshold", None),
])
def test_a_number_outside_its_range_is_clamped_and_named(monkeypatch,
                                                         tmp_path, body, key,
                                                         want):
    cfg, said = _load(monkeypatch, tmp_path, body)
    lo, hi = config_mod.RANGES[key]
    assert lo <= cfg[key] <= hi
    if want is not None:
        assert cfg[key] == pytest.approx(want)
    assert any(key in m for m in said)
    assert all(m.isascii() for m in said)


def test_a_field_of_view_no_lens_has_becomes_auto(monkeypatch, tmp_path):
    cfg, said = _load(monkeypatch, tmp_path, "camera_hfov_deg: 500\n")
    assert cfg["camera_hfov_deg"] == "auto"
    assert any("camera_hfov_deg" in m for m in said)


def test_the_range_ends_themselves_are_kept_quietly(monkeypatch, tmp_path):
    body = "".join("%s: %s\n" % (k, v) for k, (lo, hi) in
                   config_mod.RANGES.items() for v in (lo,))
    cfg, said = _load(monkeypatch, tmp_path, body)
    assert not said
    body = "".join("%s: %s\n" % (k, hi) for k, (lo, hi) in
                   config_mod.RANGES.items())
    cfg, said = _load(monkeypatch, tmp_path, body)
    assert not said


def test_the_shipped_template_is_inside_every_range(shipped_config):
    # read_config, not load_config: no last-known-good copy beside the
    # template in the source tree.
    said = []
    _, problem = config_mod.read_config(str(shipped_config),
                                        report=said.append)
    assert problem is None and not said
