# gaze/tests/test_actions_catalog.py
"""The list of jobs a key or a gamepad button can be given, and the check
that two jobs never share one."""
from pathlib import Path

import pytest
import yaml

from eye_gaze import keymap, switch
from eye_gaze.actions_catalog import ACTIONS, NOT_ACTIONS, by_key
from eye_gaze.config import DEFAULTS, KNOWN_KEYS, _BOOL_KEYS

TEMPLATE = Path(__file__).resolve().parent.parent / "config.default.yaml"


def _binding_settings():
    return {k for k in KNOWN_KEYS
            if k.endswith("_key") or k.startswith("gamepad_")}


def test_catalog_covers_every_key_setting():
    listed = {a.key for a in ACTIONS}
    missing = _binding_settings() - listed - NOT_ACTIONS
    assert not missing, sorted(missing)
    assert listed <= KNOWN_KEYS
    # The exclusions are on/off switches, not bindings - never a hidden
    # binding someone forgot to give a label.
    assert NOT_ACTIONS <= _BOOL_KEYS
    assert not NOT_ACTIONS & listed


def test_catalog_entries_are_well_formed():
    keys = [a.key for a in ACTIONS]
    labels = [a.label for a in ACTIONS]
    assert len(set(keys)) == len(keys)
    assert len(set(labels)) == len(labels)
    for a in ACTIONS:
        assert a.how in ("tek", "cift", "basili"), a
        assert a.kind in ("key", "pad"), a
        assert a.kind == ("pad" if a.key.startswith("gamepad_") else "key"), a
        assert a.label and a.group, a
        if a.kind == "pad" and a.default:
            assert switch.valid_button(a.default), a
    assert by_key("alarm_key").label == "Acil alarm"
    assert by_key("alarm_key").how == "basili"
    assert by_key("quit_key").how == "cift"


def test_catalog_defaults_match_the_default_template():
    tmpl = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    for a in ACTIONS:
        assert a.default == str(tmpl.get(a.key, "")), a.key


def test_the_catalog_uses_the_driver_s_own_default_names():
    # The labels in a conflict message must name the same jobs the driver
    # hooks, so the catalog's keys must be the ones keymap knows about.
    known = {k for k, _ in keymap.CORE} | {k for k, _ in keymap.NEWER}
    assert known <= {a.key for a in ACTIONS}
    assert set(keymap.PAD_CLICKS) | {"gamepad_alarm"} <= {a.key for a in ACTIONS}


# --- find_conflicts -----------------------------------------------------------

def test_find_conflicts_alarm_equals_click():
    found = keymap.find_conflicts({"left_click_key": "space",
                                   "alarm_key": "SPACE"})
    assert len(found) == 1
    off, owner, msg = found[0]
    assert (off, owner) == ("alarm_key", "left_click_key")
    assert "Acil alarm" in msg and "Sol tık" in msg
    assert "space" in msg


def test_find_conflicts_pad_alarm_on_a_click_button():
    found = keymap.find_conflicts({"gamepad_left": "A", "gamepad_alarm": "a"})
    assert [(a, b) for a, b, _ in found] == [("gamepad_alarm", "gamepad_left")]
    assert "Acil alarm (gamepad)" in found[0][2]
    assert "Sol tık (gamepad)" in found[0][2]


def test_find_conflicts_does_not_change_the_values():
    values = {"left_click_key": "space", "alarm_key": "space"}
    keymap.find_conflicts(values)
    assert values == {"left_click_key": "space", "alarm_key": "space"}


def test_the_default_template_has_no_conflicts():
    tmpl = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    assert keymap.find_conflicts(dict(DEFAULTS, **tmpl)) == []


@pytest.mark.parametrize("cfg", [
    {"left_click_key": "space", "alarm_key": "space"},
    {"alarm_key": "f2"},                                # vs click menu default
    {"click_menu_key": "f12", "speech_stop_key": "f12"},
    {"aac_key": "f5", "alarm_key": "f5", "click_menu_key": "f5",
     "speech_stop_key": "f5"},
    {"speech_stop_key": "q"},                           # vs quit default
    {"gamepad_drag": "X", "gamepad_alarm": "x"},
    {"gamepad_left": "A", "gamepad_alarm": "START"},
    {},
])
def test_find_conflicts_agrees_with_startup_resolution(cfg):
    found = keymap.find_conflicts(cfg)
    resolved = keymap.resolve_key_conflicts(dict(cfg), log=lambda m: None)
    assert [(a, b) for a, b, _ in found] == resolved
    for _, _, msg in found:
        assert "tek iş" in msg


# --- the settings screen: one key, one job, for EVERYTHING assigned ----------

def test_two_original_keys_on_one_key_is_a_conflict():
    # Startup leaves both hooked (one press = left AND right click); the
    # settings screen must not let it be saved.
    found = keymap.find_conflicts({"left_click_key": "space",
                                   "right_click_key": " Space "})
    assert [(a, b) for a, b, _ in found] == [("right_click_key",
                                              "left_click_key")]
    msg = found[0][2]
    assert "Sol tık" in msg and "Sağ tık" in msg and "tek iş" in msg


def test_keyboard_aliases_name_the_same_key():
    found = keymap.find_conflicts({"left_click_key": "space",
                                   "tts_key": "spacebar"})
    assert [(a, b) for a, b, _ in found] == [("tts_key", "left_click_key")]


def test_one_pad_button_for_two_click_jobs_is_a_conflict():
    found = keymap.find_conflicts({"gamepad_left": "A", "gamepad_drag": "a"})
    assert [(a, b) for a, b, _ in found] == [("gamepad_drag", "gamepad_left")]
    assert "Sol tık (gamepad)" in found[0][2]
    assert "Sürükle (gamepad)" in found[0][2]
    assert "düğme" in found[0][2]


def test_a_key_and_a_pad_button_with_the_same_name_do_not_collide():
    # "a" the letter key and "A" the gamepad button are different things.
    assert keymap.find_conflicts({"left_click_key": "a",
                                  "gamepad_left": "A"}) == []


def test_holding_the_click_switch_for_the_alarm_is_not_a_conflict():
    # alarm_switch_hold / click_menu_on_hold are a deliberate second use of
    # the SAME switch (a long hold), not a second assignment.
    cfg = {"left_click_key": "space", "gamepad_left": "A",
           "alarm_switch_hold": 3, "click_menu_on_hold": 0.8}
    assert keymap.find_conflicts(cfg) == []


def test_a_job_startup_already_turns_off_is_reported_once():
    found = keymap.find_conflicts({"left_click_key": "space",
                                   "right_click_key": "space",
                                   "alarm_key": "space"})
    assert [(a, b) for a, b, _ in found] == [
        ("alarm_key", "left_click_key"),          # startup's rule first
        ("right_click_key", "left_click_key")]


def test_startup_conflicts_come_first_and_unchanged():
    cfg = {"left_click_key": "f5", "aac_key": "f5", "alarm_key": "f5"}
    found = [(a, b) for a, b, _ in keymap.find_conflicts(cfg)]
    resolved = keymap.resolve_key_conflicts(dict(cfg), log=lambda m: None)
    assert found[:len(resolved)] == resolved
    assert found[len(resolved):] == [("aac_key", "left_click_key")]
