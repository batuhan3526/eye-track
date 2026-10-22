# gaze/tests/test_feedback_config.py
"""Settings for the earcons and the stop/repeat speech key."""

import yaml

import eye_gaze.config as config_mod
from eye_gaze.config import DEFAULTS, load_config

_KEYS = ("feedback_sounds", "feedback_volume", "speech_stop_key")


def _shipped(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_new_settings_are_known_and_defaulted():
    for k in _KEYS:
        assert k in config_mod.KNOWN_KEYS, k
    assert DEFAULTS["feedback_sounds"] is True
    assert DEFAULTS["feedback_volume"] == 0.3
    assert DEFAULTS["speech_stop_key"] == "f11"


def test_shipped_config_turns_them_on(shipped_config):
    cfg = _shipped(shipped_config)
    assert cfg["feedback_sounds"] is True
    assert cfg["feedback_volume"] == 0.3
    assert cfg["speech_stop_key"] == "f11"


def test_shipped_config_block_is_ascii(shipped_config):
    """The comments are read on a cp1254 console by the caregiver; the rest
    of the file is ASCII-only Turkish and this block must match."""
    with open(shipped_config, "r", encoding="utf-8") as f:
        lines = [ln for ln in f if any(k in ln for k in _KEYS)]
    assert len(lines) >= 3
    assert all(ln.isascii() for ln in lines), lines


def test_volume_written_with_a_turkish_comma(monkeypatch, tmp_path):
    monkeypatch.setattr(config_mod, "_report", lambda m: None)
    p = tmp_path / "c.yaml"
    p.write_text('left_click_key: "space"\nfeedback_volume: "0,5"\n',
                 encoding="utf-8")
    assert load_config(str(p))["feedback_volume"] == 0.5


def test_stop_key_does_not_collide_with_an_existing_key(shipped_config):
    cfg = _shipped(shipped_config)
    taken = {str(cfg.get(k, "") or "").lower() for k in (
        "left_click_key", "right_click_key", "double_click_key", "drag_key",
        "zoom_key", "aac_key", "osk_key", "tts_key", "stt_key", "quit_key",
        "recalibrate_key", "pause_key", "drift_key")}
    assert cfg["speech_stop_key"].lower() not in taken
