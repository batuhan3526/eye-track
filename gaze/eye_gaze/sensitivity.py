# gaze/eye_gaze/sensitivity.py
"""Cursor responsiveness presets: `cursor_sensitivity: "auto" | 1..5`.

Five settings decide how quickly the cursor follows the eye and how still it
holds: the 1-Euro filter's mincutoff and beta, the speed limiter's
max_cursor_speed, the fixation stabilizer's deadzone and the live median
window. v1.2.0 shipped them tuned for maximum calm, and a 60 px aiming
correction took ~2 s to settle at 30 fps - "very sluggish" on another
person's machine. One setting now moves all five together:

  1 = calmest  = the v1.2.0 factory values, exactly
  3 = "auto"   = a 60 px correction settles in well under a second (measured
                 with the real filters, tests/test_sensitivity.py)
  5 = fastest

Compatibility (binding): a config.yaml WITHOUT the key - every v1.2.0
install - is left exactly as it is; the primary user validated that feel.
The template for new installs says "auto". With the key present, a filter
line that is missing, "auto" or still at its v1.2.0 factory value follows
the preset; any other value was chosen on purpose and wins.

The median window is a duration now (eye_gaze.timebase): the old
`gaze_median_window` (frames) is still read and means frames at 30 fps; the
new `gaze_median_seconds` wins over it.

Values are in reference pixels; eye_gaze.physical scales the pixel ones for
the screen afterwards. The settings window takes the table from here
(table(), preset(), FACTORY, level_of) instead of keeping its own copy.
"""
import math

from eye_gaze import timebase

KEY = "cursor_sensitivity"
AUTO = "auto"
LEVELS = (1, 2, 3, 4, 5)
AUTO_LEVEL = 3

KEYS = ("mincutoff", "beta", "max_cursor_speed", "deadzone",
        "gaze_median_seconds")

# mincutoff (Hz), beta (Hz per px/s), max_cursor_speed (px/s), deadzone (px),
# median window (s; frames at 30 fps / 30). Settle times of a 60 px step at
# 30 fps, 8 px gaze noise (median of 20 seeds): 2.0 / 1.2 / 0.6 / 0.3 / 0.2 s.
_ROWS = {
    1: (0.20, 0.002, 1200.0, 20.0, 7 / 30.0),
    2: (0.30, 0.003, 1400.0, 18.0, 6 / 30.0),
    3: (0.45, 0.004, 1700.0, 16.0, 5 / 30.0),
    4: (0.70, 0.006, 2100.0, 13.0, 4 / 30.0),
    5: (1.00, 0.008, 2600.0, 10.0, 3 / 30.0),
}

# v1.2.0's config.default.yaml values: a line still holding one of these was
# never changed by anyone, so it may follow the preset.
FACTORY = dict(zip(KEYS, _ROWS[1]))
FACTORY_MEDIAN_WINDOW = 7          # gaze_median_window, frames
DEFAULT_MEDIAN_WINDOW = 5          # the driver's own fallback without the line


def preset(level):
    """{key: value} of one level (1..5)."""
    return dict(zip(KEYS, _ROWS[int(level)]))


def table():
    """{level: {key: value}} for every level - a copy."""
    return {n: preset(n) for n in LEVELS}


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip().replace(",", ".")
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def level_of(value):
    """1..5 for "auto" or a level, None for anything else."""
    if isinstance(value, str) and value.strip().lower() == AUTO:
        return AUTO_LEVEL
    f = _number(value)
    if f is None or f != int(f) or int(f) not in LEVELS:
        return None
    return int(f)


def coerce(value):
    """(normalised value, problem) for a cursor_sensitivity line.

    "auto" and 1..5 pass; a whole number outside 1..5 is clamped to the
    nearest level; anything else becomes "auto". `problem` is True when the
    line was not usable as written (the caller says so on the console).
    """
    if isinstance(value, str) and value.strip().lower() == AUTO:
        return AUTO, False
    lvl = level_of(value)
    if lvl is not None:
        return lvl, False
    f = _number(value)
    if f is not None and f == int(f):
        return min(LEVELS[-1], max(LEVELS[0], int(f))), True
    return AUTO, True


def _is_auto(value):
    return isinstance(value, str) and value.strip().lower() == AUTO


def _same(a, b):
    f = _number(a)
    return f is not None and math.isclose(f, float(b), rel_tol=1e-9,
                                          abs_tol=1e-12)


def _explicit(key, cfg, user):
    """True when `key` is a deliberate choice in the user's file: written,
    usable (the loader kept it), not "auto" and not the factory value."""
    if key not in user or key not in cfg or _is_auto(cfg[key]):
        return False
    factory = FACTORY_MEDIAN_WINDOW if key == "gaze_median_window" else FACTORY.get(key)
    return factory is None or not _same(cfg[key], factory)


def resolve(cfg, user):
    """Write the effective values of the five settings into `cfg`, in place.

    `cfg` is the coerced config, `user` the keys the file itself holds - the
    only way to tell a missing line from one written with the same value.
    Without the cursor_sensitivity key nothing changes, except that a filter
    line saying "auto" means its factory value (there is no level to follow).
    """
    if KEY not in user:
        for key in KEYS:
            if key in user and _is_auto(cfg.get(key)):
                cfg[key] = FACTORY[key]
        return cfg
    level = level_of(cfg.get(KEY)) or AUTO_LEVEL
    values = preset(level)
    for key in ("mincutoff", "beta", "max_cursor_speed", "deadzone"):
        if not _explicit(key, cfg, user):
            cfg[key] = values[key]
    if "gaze_median_seconds" in user and "gaze_median_seconds" in cfg             and not _is_auto(cfg["gaze_median_seconds"]):
        pass                                        # explicit seconds win
    elif _explicit("gaze_median_window", cfg, user):
        cfg["gaze_median_seconds"] = median_seconds(
            {"gaze_median_window": cfg["gaze_median_window"]})
    else:
        cfg["gaze_median_seconds"] = values["gaze_median_seconds"]
    return cfg


def median_seconds(cfg):
    """The live median window as a duration (s).

    gaze_median_seconds when usable; else the old gaze_median_window
    (frames at 30 fps, 5 when missing - the driver's v1.2.0 fallback)."""
    s = _number(cfg.get("gaze_median_seconds"))
    if s is not None and s > 0:
        return s
    n = _number(cfg.get("gaze_median_window", DEFAULT_MEDIAN_WINDOW))
    if n is None or n < 1:
        n = DEFAULT_MEDIAN_WINDOW
    return timebase.ref_seconds(int(n))


def overrides(cfg):
    """Keys whose effective value differs from the chosen level's."""
    level = level_of(cfg.get(KEY)) if KEY in cfg else None
    if level is None:
        return []
    values = preset(level)
    out = []
    for key in KEYS:
        got = (median_seconds(cfg) if key == "gaze_median_seconds"
               else cfg.get(key))
        if not _same(got, values[key]):
            out.append(key)
    return out


def describe(cfg):
    """One ASCII console line about the cursor's responsiveness."""
    def num(key, fmt):
        v = _number(cfg.get(key))
        return fmt % v if v is not None else "-"
    vals = ("mincutoff %s, beta %s, azami hiz %s px/sn, deadzone %s px,"
            " medyan %.2f sn"
            % (num("mincutoff", "%g"), num("beta", "%g"),
               num("max_cursor_speed", "%.0f"), num("deadzone", "%.0f"),
               median_seconds(cfg)))
    if KEY not in cfg:
        return ("Imlec hassasiyeti: ayar yok (v1.2 davranisi) - " + vals)
    level = level_of(cfg.get(KEY))
    name = ("%d (otomatik)" % level if _is_auto(cfg.get(KEY))
            else "%s" % level)
    line = "Imlec hassasiyeti: %s - %s" % (name, vals)
    over = overrides(cfg)
    if over:
        line += " (elle yazilan: %s)" % ", ".join(over)
    return line
