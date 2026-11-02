# gaze/tests/test_cursor_advice.py
"""Cursor settings that defeat the cursor (eye_gaze.cursor_advice).

Field report (1.3.2): to calm a shaky cursor a tester set the top speed to
200 px/s, the steady zone to 60 px and the snap distance to 300 px. The
cursor then took over 6 s to cross half his 2560 px screen (it "never got to
the edges"), could not make a correction under 60 px, and was dragged onto
desktop icons. Nothing said so. These are the settings the driver and the
settings window now warn about - with the numbers that make each one bad.
"""
import pytest

from eye_gaze import cursor_advice as ca
from eye_gaze.i18n import t


def _keys(out):
    return [k for k, _, _ in out]


FIELD = {"max_cursor_speed": 200.0, "deadzone": 60.0, "snap_to_targets": True,
         "snap_radius": 300.0}
LEVEL_1 = {"max_cursor_speed": 1200.0, "deadzone": 20.0,
           "snap_to_targets": True, "snap_radius": 120.0}


def test_the_field_settings_get_all_three_warnings():
    out = ca.cautions(FIELD, 2560)
    assert _keys(out) == ["max_cursor_speed", "deadzone", "snap_radius"]
    speed = dict((k, p) for k, _, p in out)["max_cursor_speed"]
    assert speed["seconds"] == "6,4"          # 1280 px at 200 px/s, Turkish


def test_the_shipped_levels_and_defaults_get_none():
    assert ca.cautions(LEVEL_1, 2560) == []
    assert ca.cautions({"max_cursor_speed": 2600.0, "deadzone": 10.0,
                        "snap_to_targets": True, "snap_radius": 120.0},
                       3840) == []


@pytest.mark.parametrize("speed,width,warn", [
    (640.0, 2560, False),       # exactly 2 s for half the screen
    (630.0, 2560, True),
    (480.0, 1920, False),
    (470.0, 1920, True),
    (0.0, 2560, False),         # 0 = no limit at all
    (700.0, None, False),       # unknown screen: 2560 assumed
    (600.0, None, True),
])
def test_the_speed_warning_is_about_crossing_half_the_screen(speed, width,
                                                            warn):
    out = ca.cautions({"max_cursor_speed": speed}, width)
    assert ("max_cursor_speed" in _keys(out)) is warn


@pytest.mark.parametrize("dz,warn", [(40.0, False), (41.0, True), (0.0, False)])
def test_the_steady_zone_warning(dz, warn):
    assert ("deadzone" in _keys(ca.cautions({"deadzone": dz}, 2560))) is warn


def test_a_big_snap_distance_matters_only_with_snapping_on():
    assert _keys(ca.cautions({"snap_to_targets": True, "snap_radius": 201.0},
                             2560)) == ["snap_radius"]
    assert ca.cautions({"snap_to_targets": True, "snap_radius": 200.0},
                       2560) == []
    assert ca.cautions({"snap_to_targets": False, "snap_radius": 300.0},
                       2560) == []


@pytest.mark.parametrize("cfg", [
    {"max_cursor_speed": "auto", "deadzone": None, "snap_radius": "abc",
     "snap_to_targets": True},
    {"max_cursor_speed": True, "deadzone": float("nan")}, {}])
def test_values_that_are_not_numbers_are_skipped(cfg):
    assert ca.cautions(cfg, 2560) == []


def test_every_warning_has_its_text_in_both_languages():
    from eye_gaze import i18n
    was = i18n.language()
    for lang in ("tr", "en"):
        i18n.set_language(lang)
        try:
            for key, msg, params in ca.cautions(FIELD, 2560):
                text = t(msg, **params)
                assert "{" not in text and text != msg, (lang, key)
        finally:
            i18n.set_language(was)


def test_the_log_lines_are_ascii_and_name_the_setting():
    lines = ca.log_lines(FIELD, 2560)
    assert len(lines) == 3
    assert all(line.isascii() for line in lines)
    assert "max_cursor_speed" in lines[0] and "200" in lines[0]
    assert "6,4" in lines[0]
    assert "deadzone" in lines[1] and "snap_radius" in lines[2]
    assert ca.log_lines(LEVEL_1, 2560) == []


def test_the_driver_logs_them_after_the_pixel_scaling():
    """gaze.log is what comes back from a remote tester: the driver prints
    the cautions on the values it really runs with (after physical scaling)."""
    import inspect
    import gaze_driver as gd
    src = inspect.getsource(gd.main)
    i_scale = src.index("cfg = physical.scaled_config(cfg")
    i_log = src.index("cursor_advice.log_lines(cfg, primary[2])")
    assert i_scale < i_log
