# gaze/tests/test_config_writer.py
"""Writing settings back into a caregiver's config.yaml.

The file is the user's: every comment in it was written for a caregiver who
will open it in Notepad, and every value was tuned in the field. The writer
may change the value it was asked to change and nothing else - and must never
leave behind a file the driver cannot start from.
"""
import os
from pathlib import Path

import pytest
import yaml

import eye_gaze.config as config_mod
import eye_gaze.config_writer as cw
from eye_gaze.config import load_config

GAZE = Path(__file__).resolve().parent.parent
TEMPLATE = GAZE / "config.default.yaml"
# The writer's tests edit a real, fully commented file: the shipped template
# (never a developer's local, untracked config.yaml).
SAMPLE = TEMPLATE


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(config_mod, "_report", lambda m: None)


def _copy_real(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(SAMPLE.read_bytes())
    return p


def _lines(data):
    return data.splitlines(keepends=True)


def _no_leftovers(p):
    assert not os.path.exists(str(p) + ".tmp")
    assert not os.path.exists(config_mod.backup_path(str(p)))


# --- the line the writer touches, and only that line ------------------------

def test_set_values_preserves_comments_and_alignment(tmp_path):
    p = _copy_real(tmp_path)
    before = p.read_bytes()
    cw.set_values(str(p), {"left_click_key": "f13"})
    after = p.read_bytes()
    old, new = _lines(before), _lines(after)
    assert len(old) == len(new)
    changed = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
    assert len(changed) == 1
    i = changed[0]
    assert old[i].startswith(b"left_click_key:")
    # The comment keeps its column, and the line its ending.
    assert old[i].index(b"#") == new[i].index(b"#")
    assert new[i].startswith(b'left_click_key: "f13"')
    assert new[i].split(b"#", 1)[1] == old[i].split(b"#", 1)[1]
    assert load_config(str(p))["left_click_key"] == "f13"


@pytest.mark.parametrize("nl", [b"\n", b"\r\n"])
@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
def test_line_endings_and_bom_survive(tmp_path, nl, bom):
    body = nl.join([b"# A\xc3\xa7\xc4\xb1klama: T\xc3\xbcrk\xc3\xa7e yorum",
                    b'left_click_key: "space"   # sol t\xc4\xb1k',
                    b"dwell_seconds: 1.0", b""])
    p = tmp_path / "config.yaml"
    p.write_bytes(bom + body)
    cw.set_values(str(p), {"dwell_seconds": 2.5})
    want = nl.join([b"# A\xc3\xa7\xc4\xb1klama: T\xc3\xbcrk\xc3\xa7e yorum",
                    b'left_click_key: "space"   # sol t\xc4\xb1k',
                    b"dwell_seconds: 2.5", b""])
    assert p.read_bytes() == bom + want
    _no_leftovers(p)


def test_a_longer_value_pushes_the_comment_but_keeps_its_gap(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "a"  # yorum\n')
    cw.set_values(str(p), {"left_click_key": "scroll lock"})
    assert p.read_bytes() == b'left_click_key: "scroll lock"  # yorum\n'


def test_a_hash_inside_quotes_is_not_the_comment(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "space"\n'
                  b'tts_voice: "a # b"      # ses adi\n')
    cw.set_values(str(p), {"tts_voice": "tr"})
    assert p.read_bytes() == (b'left_click_key: "space"\n'
                              b'tts_voice: "tr"         # ses adi\n')


def test_unquoted_and_single_quoted_values_are_replaced(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b"left_click_key: space # a\n"
                  b"tts_key: 'f4'\n")
    cw.set_values(str(p), {"left_click_key": "f13", "tts_key": "f14"})
    assert p.read_bytes() == (b'left_click_key: "f13" # a\n'
                              b'tts_key: "f14"\n')


def test_set_values_appends_missing_key(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "space"\r\n# son yorum\r\n')
    cw.set_values(str(p), {"deep_backend": "onnx"})
    assert p.read_bytes() == (b'left_click_key: "space"\r\n# son yorum\r\n'
                              b'deep_backend: "onnx"\r\n')
    assert load_config(str(p))["deep_backend"] == "onnx"


def test_appending_to_a_file_without_a_final_newline(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "space"')
    cw.set_values(str(p), {"dwell_click": True})
    assert p.read_bytes() == b'left_click_key: "space"\ndwell_click: true\n'


# --- values: how they are spelled in YAML -----------------------------------

def test_set_values_quotes_scroll_lock(tmp_path):
    p = _copy_real(tmp_path)
    cw.set_values(str(p), {"osk_key": "f13"})
    cw.set_values(str(p), {"osk_key": "scroll lock"})
    line = [ln for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.startswith("osk_key:")]
    assert line[0].startswith('osk_key: "scroll lock"')
    assert p.read_bytes() == SAMPLE.read_bytes()
    assert load_config(str(p))["osk_key"] == "scroll lock"


def test_typed_values_are_written_as_yaml_reads_them(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "space"\n')
    cw.set_values(str(p), {"dwell_click": False, "blink_click": True,
                           "dwell_seconds": 1.25, "camera_fps": 30,
                           "camera_index": "auto", "deep_gaze": "auto",
                           "tts_speaker_wav": "C:\\ses\\\"kayit\".wav",
                           "stt_language": "tr", "gamepad_alarm": ""})
    text = p.read_text(encoding="utf-8")
    assert "dwell_click: false\n" in text
    assert "blink_click: true\n" in text
    assert "dwell_seconds: 1.25\n" in text
    assert "camera_fps: 30\n" in text
    assert 'camera_index: "auto"\n' in text
    assert 'gamepad_alarm: ""\n' in text
    raw = yaml.safe_load(text)
    assert raw["tts_speaker_wav"] == "C:\\ses\\\"kayit\".wav"
    cfg = load_config(str(p))
    assert cfg["camera_index"] == "auto" and cfg["dwell_seconds"] == 1.25


def test_tiny_and_huge_floats_still_read_back_as_numbers(tmp_path):
    # PyYAML reads "1e-05" (no dot) as a STRING.
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "space"\n')
    # saccade_velocity_px_s: a float setting with no range (config.RANGES).
    cw.set_values(str(p), {"beta": 0.00001, "saccade_velocity_px_s": 1e20})
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert raw["beta"] == 0.00001 and raw["saccade_velocity_px_s"] == 1e20


@pytest.mark.parametrize("bad", [None, [1, 2], {"a": 1}, float("nan"),
                                 float("inf")])
def test_unwritable_value_types_are_refused_before_writing(tmp_path, bad):
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "space"\n')
    with pytest.raises((TypeError, ValueError)):
        cw.set_values(str(p), {"tts_voice": bad})
    assert p.read_bytes() == b'left_click_key: "space"\n'


# --- refusing to guess -------------------------------------------------------

def test_a_duplicate_key_is_refused(tmp_path):
    body = b'left_click_key: "space"\nleft_click_key: "f13"\n'
    p = tmp_path / "config.yaml"
    p.write_bytes(body)
    with pytest.raises(ValueError):
        cw.set_values(str(p), {"left_click_key": "f14"})
    assert p.read_bytes() == body


def test_a_nested_occurrence_is_refused(tmp_path):
    body = (b'left_click_key: "space"\naac_root: "x"\n'
            b'aac_dir:\n  tts_key: "f4"\n')
    p = tmp_path / "config.yaml"
    p.write_bytes(body)
    with pytest.raises(ValueError):
        cw.set_values(str(p), {"tts_key": "f14"})
    with pytest.raises(ValueError):
        cw.set_values(str(p), {"aac_dir": ""})
    assert p.read_bytes() == body


@pytest.mark.parametrize("body", [
    b'left_click_key: "space"\ntts_voice: |\n  tr\n',
    b'left_click_key: "space"\ntts_voice: tr\n  devam\n',
    b'left_click_key: "space"\ntts_voice: [a, b]\n',
    b'left_click_key: "space"\ntts_voice:\n',
    b'left_click_key: "space"\n"tts_voice": "tr"\n',
])
def test_values_the_writer_cannot_place_exactly_are_refused(tmp_path, body):
    p = tmp_path / "config.yaml"
    p.write_bytes(body)
    with pytest.raises(ValueError):
        cw.set_values(str(p), {"tts_voice": "x"})
    assert p.read_bytes() == body


def test_an_unknown_setting_is_refused(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "space"\n')
    with pytest.raises(ValueError):
        cw.set_values(str(p), {"left_click_ley": "f13"})
    assert p.read_bytes() == b'left_click_key: "space"\n'


def test_a_file_that_does_not_load_today_is_not_edited(tmp_path):
    body = b'left_click_key: "C:\\Users\\x"\n'
    p = tmp_path / "config.yaml"
    p.write_bytes(body)
    with pytest.raises(ValueError):
        cw.set_values(str(p), {"tts_key": "f4"})
    assert p.read_bytes() == body


# --- verify after writing, roll back on mismatch ------------------------------

def test_set_values_rolls_back_on_unparsable(tmp_path, monkeypatch):
    p = _copy_real(tmp_path)
    # A formatter bug that writes the YAML trap the config.yaml comments warn
    # about: a backslash path in double quotes is a ScannerError.
    monkeypatch.setattr(cw, "_format_value", lambda v: '"C:\\Users\\x"')
    with pytest.raises(ValueError):
        cw.set_values(str(p), {"tts_speaker_wav": "C:/x.wav"})
    assert p.read_bytes() == SAMPLE.read_bytes()
    _no_leftovers(p)


def test_a_value_the_driver_would_drop_is_rolled_back(tmp_path):
    p = _copy_real(tmp_path)
    with pytest.raises(ValueError):
        cw.set_values(str(p), {"camera_fps": "otuz"})
    assert p.read_bytes() == SAMPLE.read_bytes()
    _no_leftovers(p)


def test_a_bool_written_as_a_word_is_rolled_back(tmp_path):
    # "true" in quotes is a string; the loader turns it into True, which is
    # not what the caller asked to store.
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "space"\ndwell_click: false\n')
    with pytest.raises(ValueError):
        cw.set_values(str(p), {"dwell_click": "evet"})
    assert p.read_bytes() == b'left_click_key: "space"\ndwell_click: false\n'


def test_the_last_known_good_copy_is_never_touched(tmp_path):
    p = _copy_real(tmp_path)
    good = Path(config_mod.backup_path(str(p)))
    good.write_bytes(b"# eski yedek\n")
    cw.set_values(str(p), {"left_click_key": "f13"})
    assert good.read_bytes() == b"# eski yedek\n"
    with pytest.raises(ValueError):
        cw.set_values(str(p), {"camera_fps": "otuz"})
    assert good.read_bytes() == b"# eski yedek\n"


def test_nothing_to_write_leaves_the_file_alone(tmp_path):
    p = _copy_real(tmp_path)
    mtime = os.stat(p).st_mtime_ns
    cw.set_values(str(p), {})
    assert os.stat(p).st_mtime_ns == mtime


# --- a config with explicit values (an install the wizard already set up) ----

def test_a_config_with_explicit_values_loads_them(tmp_path):
    p = _copy_real(tmp_path)
    cw.set_values(str(p), {"camera_index": 0, "camera_width": 1280,
                           "camera_height": 960, "camera_fps": 30,
                           "stt_model": "medium"})
    cfg = load_config(str(p))
    assert (cfg["camera_index"], cfg["camera_width"],
            cfg["camera_height"], cfg["camera_fps"]) == (0, 1280, 960, 30)
    assert cfg["stt_model"] == "medium"


# --- "auto" values (spec 6.9) -------------------------------------------------

AUTO_KEYS = ("camera_index", "camera_width", "camera_height", "camera_fps",
             "deep_gaze", "stt_model", "stt_device", "deep_device")


def test_auto_is_kept_as_auto_not_coerced_or_dropped(tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(config_mod, "_report", said.append)
    p = tmp_path / "config.yaml"
    p.write_text('left_click_key: "space"\n'
                 + "".join('%s: "%s"\n' % (k, "AUTO" if i % 2 else "auto")
                           for i, k in enumerate(AUTO_KEYS)),
                 encoding="utf-8")
    cfg = load_config(str(p))
    for k in AUTO_KEYS:
        assert cfg[k] == "auto", k
    assert said == []
    # A clean load, so it is also kept as the last known good copy.
    assert os.path.exists(config_mod.backup_path(str(p)))


def test_a_template_restored_from_backup_keeps_auto(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(TEMPLATE.read_bytes())
    load_config(str(p))                       # leaves config.son-iyi.yaml
    p.write_bytes(b'left_click_key: "C:\\Users\\x"\n')   # broken edit
    cfg = load_config(str(p))
    assert config_mod.used_backup(cfg)
    assert cfg["camera_index"] == "auto" and cfg["stt_model"] == "auto"


def test_default_template_loads_and_is_generic(tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(config_mod, "_report", said.append)
    p = tmp_path / "config.yaml"
    p.write_bytes(TEMPLATE.read_bytes())
    cfg = load_config(str(p))
    assert not config_mod.used_defaults(cfg)
    assert not config_mod.used_backup(cfg)
    assert said == []                         # no unknown key, no bad value
    assert cfg["camera_index"] == "auto"
    text = TEMPLATE.read_text(encoding="utf-8")
    low = text.lower()
    assert "c:/users" not in low and "c:\\users" not in low
    for machine_specific in ("bu makine", "kullanici karari",
                             "public", "2026-", "kur-ses.bat"):
        assert machine_specific not in low, machine_specific


def test_default_template_leaves_the_machine_values_to_auto():
    tmpl = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    # Everything that depends on the computer is decided on it ("auto"),
    # the AAC boards are the bundled ones.
    for k in AUTO_KEYS:
        assert tmpl[k] == "auto", k
    assert tmpl["aac_dir"] == ""
    assert tmpl["calibration_file"] == "calibration.json"


def test_the_template_keeps_the_caregiver_comments():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert text.count("#") > 150
    assert "config.son-iyi.yaml" in text
    assert "DUZ EGIK CIZGIYLE" in text


# --- what the driver does with "auto" until the hardware profile (Task 13) ---

def test_the_driver_opens_the_old_default_camera_mode_for_auto():
    import gaze_driver as gd
    cfg = dict(config_mod.DEFAULTS, camera_index="auto", camera_width="auto",
               camera_height="auto", camera_fps="auto")
    assert gd._camera_request(cfg) == (0, 1280, 720, 60)


def test_explicit_camera_settings_are_used_as_before():
    import gaze_driver as gd
    cfg = dict(config_mod.DEFAULTS, camera_index=1, camera_width=1280,
               camera_height=960, camera_fps=30)
    assert gd._camera_request(cfg) == (1, 1280, 960, 30)
    assert gd._camera_request({}) == (0, 1280, 720, 60)


def test_the_driver_dictates_with_the_old_default_model_for_auto():
    import gaze_driver as gd
    assert gd._stt_model_name({"stt_model": "auto"}) == "medium"
    assert gd._stt_model_name({}) == "medium"
    assert gd._stt_model_name({"stt_model": "small"}) == "small"


def test_an_empty_aac_dir_means_the_bundled_boards():
    import gaze_driver as gd
    from eye_gaze import paths
    from eye_gaze.aac import load_boards
    bundled = paths.resource_path("aac")
    assert gd._aac_dir({"aac_dir": ""}) == bundled
    assert gd._aac_dir({}) == bundled
    assert gd._aac_dir({"aac_dir": "D:/AAC"}) == "D:/AAC"
    boards = load_boards(bundled)
    # Two packs side by side since the language setting: Turkish + English.
    assert "turkce-aac-paketi" in boards and "english-aac-pack" in boards
    assert len([k for k in boards if k.startswith("turkce-aac-")]) == 6
    assert len([k for k in boards if k.startswith("english-aac-")]) == 6


# --- fix round 1: click-route guard, escapes, Path, read-only, quiet ---------

ONLY_KEY = b'left_click_key: "space"\ngamepad_left: ""\ndwell_seconds: 2.0\n'


def test_clearing_the_only_click_route_is_refused(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(ONLY_KEY)
    with pytest.raises(ValueError, match="TIKLAMA YOLU"):
        cw.set_values(str(p), {"left_click_key": ""})
    assert p.read_bytes() == ONLY_KEY
    _no_leftovers(p)


def test_a_one_call_reassign_of_the_click_route_is_accepted(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(ONLY_KEY)
    cw.set_values(str(p), {"left_click_key": "", "gamepad_left": "A"})
    cfg = load_config(str(p))
    assert cfg["left_click_key"] == "" and cfg["gamepad_left"] == "A"
    cw.set_values(str(p), {"left_click_key": "f13", "gamepad_left": ""})
    assert load_config(str(p))["left_click_key"] == "f13"


def test_switching_dwell_to_blink_is_accepted(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: ""\ndwell_click: true\nblink_click: false\n')
    cw.set_values(str(p), {"dwell_click": False, "blink_click": True})
    cfg = load_config(str(p))
    assert cfg["dwell_click"] is False and cfg["blink_click"] is True


def test_a_file_that_already_had_no_route_can_still_be_edited(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: ""\ndwell_seconds: 2.0\n')
    cw.set_values(str(p), {"tts_key": "f4"})
    assert load_config(str(p))["tts_key"] == "f4"


def test_yaml_line_breaks_and_bom_in_a_value_are_escaped(tmp_path):
    src = (GAZE / "eye_gaze" / "config_writer.py").read_text(encoding="utf-8")
    for ch in "\u2028\u2029\ufeff":
        assert ch not in src          # escapes in the source, not literals
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "space"\n')
    value = "a\u2028b\u2029c\ufeffd"
    cw.set_values(str(p), {"tts_voice": value})
    text = p.read_bytes().decode("utf-8")
    assert not any(ch in text for ch in "\u2028\u2029\ufeff")
    assert yaml.safe_load(text)["tts_voice"] == value


def test_a_pathlib_path_is_accepted(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(b'left_click_key: "space"\n')
    cw.set_values(p, {"tts_key": "f4"})
    assert p.read_bytes() == b'left_click_key: "space"\ntts_key: "f4"\n'
    _no_leftovers(p)


def _is_root():
    return hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.mark.skipif(_is_root(), reason="root ignores file permissions")
def test_a_read_only_config_is_refused_clearly(tmp_path):
    import stat
    p = tmp_path / "config.yaml"
    p.write_bytes(ONLY_KEY)
    os.chmod(p, stat.S_IREAD)
    try:
        with pytest.raises(PermissionError, match="salt okunur"):
            cw.set_values(str(p), {"tts_key": "f4"})
        assert p.read_bytes() == ONLY_KEY
        assert not os.path.exists(str(p) + ".tmp")
    finally:
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)


def test_a_stale_read_only_tmp_is_cleared_and_none_is_left(tmp_path):
    import stat
    p = tmp_path / "config.yaml"
    p.write_bytes(ONLY_KEY)
    tmp = tmp_path / "config.yaml.tmp"
    tmp.write_bytes(b"crash leftover")
    os.chmod(tmp, stat.S_IREAD)
    cw.set_values(str(p), {"tts_key": "f4"})
    assert load_config(str(p))["tts_key"] == "f4"
    assert not tmp.exists()
    # The new file is writable again: the next save works too.
    cw.set_values(str(p), {"tts_key": "f14"})


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_file_mode_is_kept(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_bytes(ONLY_KEY)
    os.chmod(p, 0o600)
    cw.set_values(str(p), {"tts_key": "f4"})
    assert os.stat(p).st_mode & 0o777 == 0o600


def test_saving_prints_nothing_and_a_rejection_carries_the_loader_note(
        tmp_path, monkeypatch):
    said = []
    monkeypatch.setattr(config_mod, "_report", said.append)
    p = tmp_path / "config.yaml"
    body = b'left_click_key: "space"\ncamera_fps: "otuz"\n'
    p.write_bytes(body)
    cw.set_values(str(p), {"tts_key": "f4"})     # existing bad line untouched
    assert said == []
    with pytest.raises(ValueError, match="bir sayi degil"):
        cw.set_values(str(p), {"camera_width": "genis"})
    assert said == []
