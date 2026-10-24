# gaze/tests/test_sensitivity.py
"""cursor_sensitivity presets (eye_gaze.sensitivity) and how config loads them.

The binding rule: a config.yaml WITHOUT the key (every v1.2.0 install) runs
exactly as before; the template for new installs says "auto" (= 3). A filter
line explicitly set to something other than the v1.2.0 factory value wins over
the preset.
"""
import math
import random
import statistics
from collections import deque

import pytest
import yaml

from eye_gaze import config, sensitivity, timebase
from eye_gaze.limiter import SpeedLimiter
from eye_gaze.oneeuro import OneEuroFilter
from eye_gaze.stabilizer import FixationStabilizer

# v1.2.0's config.default.yaml, the five lines the presets drive.
V120 = {"mincutoff": 0.2, "beta": 0.002, "max_cursor_speed": 1200.0,
        "deadzone": 20.0, "gaze_median_seconds": 7 / 30.0}

V120_FILE = """\
freq: 30.0
mincutoff: 0.2
beta: 0.002
dcutoff: 1.0
max_cursor_speed: 1200
deadzone: 20
gaze_median_window: 7
left_click_key: "space"
"""


def _write(tmp_path, text, name="config.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


@pytest.fixture
def said(monkeypatch):
    lines = []
    monkeypatch.setattr(config, "_report", lines.append)
    return lines


# --- the table ------------------------------------------------------------------

def test_preset_1_is_exactly_v120():
    assert sensitivity.preset(1) == V120
    assert sensitivity.FACTORY == V120


def test_presets_go_from_calm_to_fast():
    rows = [sensitivity.preset(n) for n in sensitivity.LEVELS]
    assert list(sensitivity.LEVELS) == [1, 2, 3, 4, 5]
    for a, b in zip(rows, rows[1:]):
        assert b["mincutoff"] > a["mincutoff"]
        assert b["beta"] > a["beta"]
        assert b["max_cursor_speed"] > a["max_cursor_speed"]
        assert b["deadzone"] < a["deadzone"]
        assert b["gaze_median_seconds"] < a["gaze_median_seconds"]
    assert sensitivity.AUTO_LEVEL == 3


def test_table_is_a_copy():
    t = sensitivity.table()
    t[1]["deadzone"] = 99
    assert sensitivity.preset(1)["deadzone"] == 20.0
    assert set(t) == {1, 2, 3, 4, 5}


@pytest.mark.parametrize("value,level", [
    ("auto", 3), (" AUTO ", 3), (1, 1), (5, 5), ("2", 2), (4.0, 4), ("3,0", 3),
])
def test_level_of(value, level):
    assert sensitivity.level_of(value) == level


@pytest.mark.parametrize("value", [None, "hizli", True, 0, 6, 2.5, float("nan")])
def test_level_of_rejects(value):
    assert sensitivity.level_of(value) is None


# --- old configs: exactly as before ----------------------------------------------

def test_v120_config_without_the_key_is_untouched(tmp_path, said):
    cfg = config.load_config(_write(tmp_path, V120_FILE))
    assert cfg["mincutoff"] == 0.2 and cfg["beta"] == 0.002
    assert cfg["max_cursor_speed"] == 1200 and cfg["deadzone"] == 20
    assert cfg["gaze_median_window"] == 7
    assert "gaze_median_seconds" not in cfg
    assert "cursor_sensitivity" not in cfg
    assert sensitivity.median_seconds(cfg) == pytest.approx(7 / 30.0)
    assert timebase.frames(sensitivity.median_seconds(cfg), 30.0) == 7


def test_old_config_without_filter_lines_keeps_the_old_code_defaults(tmp_path, said):
    cfg = config.load_config(_write(tmp_path, 'left_click_key: "space"\n'))
    # DEFAULTS and the driver's own fallbacks, as in v1.2.0
    assert cfg["mincutoff"] == 1.0 and cfg["beta"] == 0.007
    assert "deadzone" not in cfg and "max_cursor_speed" not in cfg
    assert timebase.frames(sensitivity.median_seconds(cfg), 30.0) == 5


def test_old_config_values_other_than_factory_are_untouched(tmp_path, said):
    cfg = config.load_config(_write(
        tmp_path, V120_FILE.replace("deadzone: 20", "deadzone: 35")))
    assert cfg["deadzone"] == 35


# --- new configs ------------------------------------------------------------------

def test_auto_is_preset_3_even_with_factory_lines(tmp_path, said):
    cfg = config.load_config(_write(
        tmp_path, V120_FILE + 'cursor_sensitivity: "auto"\n'))
    p3 = sensitivity.preset(3)
    for key in ("mincutoff", "beta", "max_cursor_speed", "deadzone"):
        assert cfg[key] == p3[key], key
    assert sensitivity.median_seconds(cfg) == p3["gaze_median_seconds"]
    assert cfg["cursor_sensitivity"] == "auto"
    assert not said


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
def test_each_level(tmp_path, said, level):
    cfg = config.load_config(_write(
        tmp_path, 'left_click_key: "space"\ncursor_sensitivity: %d\n' % level))
    want = sensitivity.preset(level)
    for key in sensitivity.KEYS:
        assert cfg[key] == want[key], key
    assert cfg["cursor_sensitivity"] == level


def test_explicit_non_factory_line_wins(tmp_path, said):
    text = (V120_FILE.replace("deadzone: 20", "deadzone: 30")
            .replace("max_cursor_speed: 1200", "max_cursor_speed: 0")
            + "cursor_sensitivity: 5\n")
    cfg = config.load_config(_write(tmp_path, text))
    assert cfg["deadzone"] == 30
    assert cfg["max_cursor_speed"] == 0          # limiter off on purpose
    assert cfg["mincutoff"] == sensitivity.preset(5)["mincutoff"]
    assert sorted(sensitivity.overrides(cfg)) == ["deadzone", "max_cursor_speed"]


def test_auto_filter_lines_follow_the_preset(tmp_path, said):
    cfg = config.load_config(_write(
        tmp_path, 'left_click_key: "space"\ncursor_sensitivity: 4\n'
        'deadzone: "auto"\nmincutoff: "auto"\ngaze_median_seconds: "auto"\n'))
    p4 = sensitivity.preset(4)
    assert cfg["deadzone"] == p4["deadzone"]
    assert cfg["mincutoff"] == p4["mincutoff"]
    assert cfg["gaze_median_seconds"] == p4["gaze_median_seconds"]
    assert not said


def test_auto_filter_lines_without_the_key_mean_factory(tmp_path, said):
    cfg = config.load_config(_write(
        tmp_path, 'left_click_key: "space"\ndeadzone: "auto"\n'))
    assert cfg["deadzone"] == 20.0
    assert "mincutoff" in cfg and cfg["mincutoff"] == 1.0   # untouched


def test_median_window_and_seconds(tmp_path, said):
    base = 'left_click_key: "space"\ncursor_sensitivity: 3\n'
    cfg = config.load_config(_write(tmp_path, base + "gaze_median_window: 9\n"))
    assert sensitivity.median_seconds(cfg) == pytest.approx(9 / 30.0)
    cfg = config.load_config(_write(
        tmp_path, base + "gaze_median_window: 9\ngaze_median_seconds: 0,4\n"))
    assert sensitivity.median_seconds(cfg) == pytest.approx(0.4)
    cfg = config.load_config(_write(tmp_path, base + "gaze_median_window: 7\n"))
    assert sensitivity.median_seconds(cfg) == sensitivity.preset(3)["gaze_median_seconds"]


def test_median_seconds_without_the_key(tmp_path, said):
    cfg = config.load_config(_write(
        tmp_path, 'left_click_key: "space"\ngaze_median_seconds: 0.3\n'))
    assert sensitivity.median_seconds(cfg) == pytest.approx(0.3)
    assert sensitivity.median_seconds({"gaze_median_seconds": -1,
                                       "gaze_median_window": 7}) == pytest.approx(7 / 30.0)
    assert sensitivity.median_seconds({"gaze_median_window": "x"}) == pytest.approx(5 / 30.0)


@pytest.mark.parametrize("text,level,warned", [
    ("cursor_sensitivity: 9\n", 5, True),
    ("cursor_sensitivity: 0\n", 1, True),
    ("cursor_sensitivity: hizli\n", 3, True),
    ("cursor_sensitivity: 2.5\n", 3, True),
    ("cursor_sensitivity: '2'\n", 2, False),
])
def test_bad_levels(tmp_path, said, text, level, warned):
    cfg = config.load_config(_write(tmp_path, 'left_click_key: "space"\n' + text))
    assert cfg["deadzone"] == sensitivity.preset(level)["deadzone"]
    assert bool([m for m in said if "cursor_sensitivity" in m]) == warned
    assert all(m.isascii() for m in said)


def test_read_config_is_raw_unless_asked(tmp_path, said):
    p = _write(tmp_path, 'left_click_key: "space"\ncursor_sensitivity: 4\n'
               'deadzone: "auto"\n')
    raw, problem = config.read_config(p)
    assert problem is None
    assert raw["deadzone"] == "auto" and "max_cursor_speed" not in raw
    eff, problem = config.read_config(p, effective=True)
    assert problem is None
    assert eff["deadzone"] == sensitivity.preset(4)["deadzone"]
    assert eff["max_cursor_speed"] == sensitivity.preset(4)["max_cursor_speed"]


def test_backup_config_is_resolved_too(tmp_path, said):
    p = _write(tmp_path, "left_click_key: [\n")             # broken YAML
    _write(tmp_path, 'left_click_key: "space"\ncursor_sensitivity: 2\n',
           name="config.son-iyi.yaml")
    cfg = config.load_config(p)
    assert config.used_backup(cfg)
    assert cfg["deadzone"] == sensitivity.preset(2)["deadzone"]


def test_settings_writer_round_trips_the_new_keys(tmp_path, said):
    from eye_gaze import config_writer
    p = _write(tmp_path, V120_FILE)
    config_writer.set_values(p, {"cursor_sensitivity": 4})
    config_writer.set_values(p, {"cursor_sensitivity": "auto", "deadzone": "auto"})
    config_writer.set_values(p, {"gaze_median_seconds": 0.2})
    cfg = config.load_config(p)
    assert cfg["deadzone"] == sensitivity.preset(3)["deadzone"]
    assert sensitivity.median_seconds(cfg) == pytest.approx(0.2)


def test_shipped_template_is_auto(shipped_config, said, tmp_path):
    with open(shipped_config, "r", encoding="utf-8") as f:
        text = f.read()
    raw = yaml.safe_load(text)
    assert raw["cursor_sensitivity"] == "auto"
    assert set(raw) <= config.KNOWN_KEYS
    # a copy: load_config keeps a last-known-good file next to what it loads
    cfg = config.load_config(_write(tmp_path, text))
    p3 = sensitivity.preset(3)
    for key in sensitivity.KEYS:
        assert cfg[key] == p3[key], key


def test_describe_is_ascii():
    for cfg in ({}, {"cursor_sensitivity": "auto", **sensitivity.preset(3)},
                {"cursor_sensitivity": 5, **dict(sensitivity.preset(5), deadzone=40.0)}):
        line = sensitivity.describe(cfg)
        assert line.isascii() and line


# --- the simulation: a 60 px aiming correction through the real filters --------

def settle_time(values, fps=30.0, sigma=8.0, step=60.0, seed=1, tol=20.0):
    """Seconds after a `step` px target jump until the cursor is within
    `tol` px of the new target and stays there (median -> 1-Euro -> speed
    limiter -> stabilizer, as the driver chains them)."""
    rng = random.Random(seed)
    fx = OneEuroFilter(fps, values["mincutoff"], values["beta"], 1.0)
    fy = OneEuroFilter(fps, values["mincutoff"], values["beta"], 1.0)
    lim = SpeedLimiter(values["max_cursor_speed"])
    st = FixationStabilizer(values["deadzone"])
    st.set_frame_rate(fps)
    hist = deque(maxlen=timebase.frames(values["gaze_median_seconds"], fps))
    t0, n = 3.0, int(7.0 * fps)
    out = []
    for i in range(n):
        t = i / fps
        tx = 500.0 + (step if t >= t0 else 0.0)
        hist.append((tx + rng.gauss(0, sigma), 400.0 + rng.gauss(0, sigma)))
        mx = statistics.median(p[0] for p in hist)
        my = statistics.median(p[1] for p in hist)
        lx, ly = lim.update(fx(mx, t), fy(my, t), t)
        lx, ly = st.update(lx, ly)
        out.append((t, math.hypot(lx - 500.0 - step, ly - 400.0)))
    last_out = max(i for i, (_, d) in enumerate(out) if d > tol)
    return out[last_out + 1][0] - t0


SEEDS = range(1, 11)


def test_preset_3_settles_a_60px_correction_within_a_second():
    times = [settle_time(sensitivity.preset(3), seed=s) for s in SEEDS]
    assert max(times) <= 1.0, times


def test_preset_1_is_todays_slow_glide():
    # recorded: ~1.7-2.2 s at sigma 8 (the evidence's "~2.3 s")
    times = [settle_time(sensitivity.preset(1), seed=s) for s in SEEDS]
    assert 1.4 <= statistics.median(times) <= 3.0, times


def test_settle_time_falls_with_each_level():
    med = [statistics.median(settle_time(sensitivity.preset(n), seed=s)
                             for s in SEEDS) for n in sensitivity.LEVELS]
    assert med == sorted(med, reverse=True), med
    assert med[4] < 0.5


@pytest.mark.parametrize("fps", [15.0, 60.0])
def test_preset_3_stays_quick_at_other_frame_rates(fps):
    times = [settle_time(sensitivity.preset(3), fps=fps, seed=s) for s in SEEDS]
    assert max(times) <= 1.2, times


# --- an unusable filter line under a preset follows the preset ---------------------

@pytest.mark.parametrize("line", ["mincutoff: abc", "mincutoff: 0",
                                  "mincutoff: -0.5", "beta: abc"])
def test_unusable_line_under_a_preset_follows_it(tmp_path, said, line):
    key = line.split(":")[0]
    cfg = config.load_config(_write(
        tmp_path, 'left_click_key: "space"\ncursor_sensitivity: 3\n' + line + "\n"))
    assert cfg[key] == sensitivity.preset(3)[key]
    assert any(key in m for m in said)              # still said on the console


def test_a_real_number_under_a_preset_still_wins(tmp_path, said):
    cfg = config.load_config(_write(
        tmp_path, 'left_click_key: "space"\ncursor_sensitivity: 3\n'
        "mincutoff: 1.0\nbeta: 0,007\ngaze_median_window: 9.0\n"))
    assert cfg["mincutoff"] == 1.0 and cfg["beta"] == 0.007
    assert sensitivity.median_seconds(cfg) == pytest.approx(9 / 30.0)


@pytest.mark.parametrize("line,want", [("mincutoff: abc", 1.0), ("mincutoff: 0", 1.0),
                                       ("mincutoff: -0.5", 1.0), ("beta: abc", 0.007)])
def test_unusable_line_without_the_key_is_v120(tmp_path, said, line, want):
    key = line.split(":")[0]
    cfg = config.load_config(_write(tmp_path, 'left_click_key: "space"\n' + line + "\n"))
    assert cfg[key] == want == config.DEFAULTS[key]


# --- what the settings window asks ----------------------------------------------

@pytest.mark.parametrize("value,follows", [
    (None, True), ("auto", True), (" AUTO ", True), (20, True), ("20", True),
    (20.0, True), (16, False), (0, False), ("abc", True)])
def test_follows_is_the_rule_resolve_applies(value, follows):
    assert sensitivity.follows("deadzone", value) is follows


def test_the_driver_falls_back_to_missing_without_the_line():
    # The settings window shows sensitivity.MISSING for a missing line; the
    # driver must run with the same numbers.
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "gaze_driver.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert ('SpeedLimiter(float(cfg.get("max_cursor_speed",'
            ' sensitivity.MISSING["max_cursor_speed"])))') in src
    assert ('FixationStabilizer(float(cfg.get("deadzone",'
            ' sensitivity.MISSING["deadzone"])))') in src
    assert sensitivity.MISSING == {"max_cursor_speed": 0.0, "deadzone": 20.0}
