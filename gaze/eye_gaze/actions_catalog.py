# gaze/eye_gaze/actions_catalog.py
"""Every job a key or a gamepad button can be given, as the settings screen
and the first-run wizard show it.

One entry per binding setting in config.yaml (test_catalog_covers_every_key_
setting keeps this in step with config.KNOWN_KEYS): the name the user
sees (from the language catalog), how the key is pressed for that job,
and the value a new install starts with (equal to config.default.yaml's,
which a test pins).

`how` matters because it is what the user must physically do, and what a
key-capture dialog must tell them: "tek" = one press, "cift" = two presses
within hotkey_double_interval (the driver's guard against a stray press
quitting or recalibrating), "basili" = hold for alarm_hold_seconds.
"""
from collections import namedtuple

from eye_gaze.i18n import t

_Fields = namedtuple("Action", "key how group_id default kind")


class Action(_Fields):
    """One job. `label` and `group` are read from the language catalog
    (actions.<key>, actions.group.<group_id>) each time, so a window built
    after a language change shows the new names."""
    __slots__ = ()

    @property
    def label(self):
        return t("actions." + self.key)

    @property
    def group(self):
        return t("actions.group." + self.group_id)


ACTIONS = [
    # --- clicking: the user's only way to press anything --------------------
    Action("left_click_key", "tek", "click", "space", "key"),
    Action("right_click_key", "tek", "click", "", "key"),
    Action("double_click_key", "tek", "click", "", "key"),
    Action("drag_key", "tek", "click", "", "key"),
    Action("click_menu_key", "tek", "click", "f2", "key"),
    Action("zoom_key", "tek", "click", "f6", "key"),
    Action("gamepad_left", "tek", "gamepad", "A", "pad"),
    Action("gamepad_right", "tek", "gamepad", "B", "pad"),
    Action("gamepad_double", "tek", "gamepad", "Y", "pad"),
    Action("gamepad_drag", "tek", "gamepad", "X", "pad"),
    # --- speaking and writing -----------------------------------------------
    Action("aac_key", "tek", "speech", "f5", "key"),
    Action("tts_key", "tek", "speech", "f4", "key"),
    Action("speech_stop_key", "tek", "speech", "f11", "key"),
    Action("stt_key", "tek", "writing", "f3", "key"),
    Action("osk_key", "cift", "writing", "scroll lock", "key"),
    # --- help ---------------------------------------------------------------
    Action("alarm_key", "basili", "emergency", "f12", "key"),
    Action("gamepad_alarm", "basili", "emergency", "", "pad"),
    # --- the program itself: two presses, so a stray one does nothing -------
    Action("pause_key", "cift", "program", "f8", "key"),
    Action("drift_key", "cift", "program", "f7", "key"),
    Action("recalibrate_key", "cift", "program", "f9", "key"),
    Action("quit_key", "cift", "program", "f10", "key"),
]

# Settings named like bindings that are not one: gamepad_enabled switches the
# whole gamepad on or off. Listed so the coverage test can tell "not a job"
# from "forgotten".
NOT_ACTIONS = frozenset({"gamepad_enabled"})

_BY_KEY = {a.key: a for a in ACTIONS}


def by_key(key):
    """The Action for setting `key`, or None."""
    return _BY_KEY.get(key)
