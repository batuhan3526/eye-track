# gaze/eye_gaze/cursor_advice.py
"""Cursor settings that defeat the cursor, and what to say about them.

A tester calmed a shaky 1.3.2 cursor the only way the settings offered:
top speed 200 px/s, steady zone 60 px, snap distance 300 px. The cursor
then needed 6.4 s to cross half of his 2560 px screen - "it never gets to
the far left and right" - could not make a correction smaller than 60 px,
and was dragged onto the nearest desktop icon. Each value is legal; what
was missing was being told what it costs. cautions() names the settings
whose value has such a cost, with the number that shows it; the driver
logs them at start (gaze.log is what a remote tester sends back) and the
settings window shows them under the control.

The lines are drawn at the shipped values' far side: the calmest level
(1200 px/s, 20 px) and the template's snap distance (120 px) are fine,
twice the calmest steady zone and half a screen in over 2 s are not.
cfg holds the values the driver runs with (cursor_sensitivity resolved).
"""
import math

from eye_gaze.i18n import decimal, t_ascii

REF_WIDTH = 2560.0      # an unknown screen
SLOW_HALF_SCREEN_S = 2.0
BIG_DEADZONE_PX = 40.0
BIG_SNAP_PX = 200.0


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def cautions(cfg, screen_w=None):
    """[(setting, message key, message params)] in setting order."""
    out = []
    w = _number(screen_w)
    w = w if w and w > 0 else REF_WIDTH
    v = _number(cfg.get("max_cursor_speed"))
    if v is not None and v > 0:
        secs = (w / 2.0) / v
        if secs > SLOW_HALF_SCREEN_S + 1e-9:
            out.append(("max_cursor_speed", "cursor_advice.slow",
                        {"value": "%d" % round(v),
                         "seconds": decimal(round(secs, 1))}))
    d = _number(cfg.get("deadzone"))
    if d is not None and d > BIG_DEADZONE_PX:
        out.append(("deadzone", "cursor_advice.deadzone",
                    {"value": "%d" % round(d)}))
    r = _number(cfg.get("snap_radius"))
    if cfg.get("snap_to_targets") is True and r is not None and r > BIG_SNAP_PX:
        out.append(("snap_radius", "cursor_advice.snap",
                    {"value": "%d" % round(r)}))
    return out


def log_lines(cfg, screen_w=None):
    """The cautions as ASCII console lines naming the config.yaml setting."""
    return ["UYARI (%s: %s): %s" % (key, params["value"],
                                    t_ascii(msg, **params))
            for key, msg, params in cautions(cfg, screen_w)]
