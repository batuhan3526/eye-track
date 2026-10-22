"""Saccade-aware smoothing (feat/oz-sakkad).

The detector watches the RAW per-frame gaze estimate and reports a saccade
once the eye has demonstrably landed somewhere new; the glue then drops the
smoothing state so the cursor goes there now instead of gliding for 0.3-2 s.
Everything is fed with explicit timestamps - no clock, no camera.
"""
import math
import random
from collections import deque

import pytest

from eye_gaze.saccade import SaccadeDetector, SaccadeJump, make_saccade_jump

DT = 1.0 / 30.0


def _feed(det, pts, t0=0.0, dt=DT):
    """Feed (x, y) points at a fixed rate; return list of fire flags."""
    out = []
    t = t0
    for x, y in pts:
        out.append(det.update(x, y, t))
        t += dt
    return out


def _noisy(rnd, cx, cy, n, sigma=12.0):
    return [(cx + rnd.gauss(0, sigma), cy + rnd.gauss(0, sigma)) for _ in range(n)]


# --- detector -----------------------------------------------------------------

def test_noisy_fixation_never_fires():
    rnd = random.Random(1)
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    fired = _feed(det, _noisy(rnd, 600, 400, 30 * 120, sigma=20.0))  # 2 minutes
    assert not any(fired)


def test_clean_jump_fires_once_on_the_confirming_frame():
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    pts = [(300, 300)] * 10 + [(900, 500)] * 10
    fired = _feed(det, pts)
    # first sample at the new place is only a candidate; the second confirms
    assert fired.index(True) == 11
    assert fired.count(True) == 1
    assert det.target == pytest.approx((900, 500))


def test_single_frame_spike_does_not_fire():
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    pts = [(300, 300)] * 10 + [(900, 300)] + [(300, 300)] * 10
    assert not any(_feed(det, pts))


def test_spike_is_not_taken_into_the_fixation_anchor():
    """A rejected spike must not drag the anchor: a real jump right after it
    is still measured from the true fixation."""
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    pts = [(300, 300)] * 10 + [(900, 300)] + [(300, 300)] * 3 + [(300, 440)] * 3
    fired = _feed(det, pts)
    assert fired.count(True) == 1


def test_jump_smaller_than_min_amplitude_does_not_fire():
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    pts = [(300, 300)] * 10 + [(400, 300)] * 10        # 100 px, fast
    assert not any(_feed(det, pts))


def test_slow_drift_over_a_long_distance_does_not_fire():
    """Smooth pursuit / head drift belongs to the normal filters."""
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    pts = [(300 + 10 * i, 300) for i in range(100)]    # 300 px/s, 1000 px total
    assert not any(_feed(det, pts))


def test_mid_flight_sample_then_landing_fires():
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    pts = [(300, 300)] * 10 + [(600, 300)] + [(900, 300)] * 5
    fired = _feed(det, pts)
    assert fired.index(True) == 12
    assert det.target == pytest.approx((900, 300))


def test_incoherent_candidates_do_not_fire():
    """Lids closing before a blink drag the estimate steadily downward:
    every sample far from the fixation, no two of them in the same place."""
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    pts = [(300, 300)] * 10 + [(300, 450), (300, 650), (300, 850)]
    assert not any(_feed(det, pts))


def test_time_gap_aborts_and_reanchors():
    """A gap (eyes closed, paused, recalibrating) carries no velocity
    information: the far sample after it is a new anchor, not a saccade."""
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    fired = _feed(det, [(300, 300)] * 10)
    t = 10 * DT + 0.5                                   # half a second shut
    for i in range(10):
        fired.append(det.update(900, 300, t + i * DT))
    assert not any(fired)


def test_gap_in_the_middle_of_a_candidate_aborts_it():
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    _feed(det, [(300, 300)] * 10)
    t = 10 * DT
    assert det.update(900, 300, t) is False             # candidate
    assert det.update(900, 300, t + 0.5) is False       # after a blink: no
    # ...and the new place is now simply the fixation
    assert not any(_feed(det, [(900, 300)] * 10, t0=t + 0.5 + DT))


def test_hysteresis_no_second_fire_until_settled():
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    pts = [(300, 300)] * 10 + [(900, 300)] * 2 + [(300, 300)] * 2
    fired = _feed(det, pts)
    # the immediate jump back comes before the eye has settled at 900
    assert fired.count(True) == 1


def test_fires_again_after_settling():
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    pts = [(300, 300)] * 10 + [(900, 300)] * 10 + [(300, 300)] * 10
    fired = _feed(det, pts)
    assert fired.count(True) == 2


def test_slow_onset_below_velocity_threshold_does_not_fire():
    """Same amplitude, but the move took far too long to be a saccade."""
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    _feed(det, [(300, 300)] * 10)
    # 150 px reached only after 0.2 s (< 1500 px/s) with no samples between
    t = 9 * DT + 0.2
    assert det.update(450, 300, t) is False
    assert det.update(450, 300, t + DT) is False


def test_reset_forgets_everything():
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    _feed(det, [(300, 300)] * 10)
    det.reset()
    assert not any(_feed(det, [(900, 300)] * 5, t0=1.0))


def test_bad_parameters_are_sanitised():
    det = SaccadeDetector(velocity_px_s=-5, min_px=0, confirm_frames=0)
    assert det.velocity > 0 and det.min_px > 0 and det.confirm_frames >= 1


# --- glue: what the driver calls once per frame -------------------------------

class _Filt:
    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1


def _ident(g):
    return (float(g[0]), float(g[1]))


def test_step_resets_filters_and_trims_history_on_a_saccade():
    sj = SaccadeJump(SaccadeDetector(velocity_px_s=1500, min_px=120))
    filters = [_Filt() for _ in range(4)]
    hist = deque(maxlen=7)
    fired = []
    t = 0.0
    for g in [(300, 300)] * 10 + [(900, 300)] * 2:
        hist.append(g)
        fired.append(sj.step(_ident, g, (0.0, 0.0), t, hist, filters))
        t += DT
    assert fired[-1] is True and fired.count(True) == 1
    assert all(f.resets == 1 for f in filters)
    assert list(hist) == [(900, 300), (900, 300)]     # only the landing
    assert sj.count == 1


def test_step_adds_drift_before_detecting():
    """drift is part of where the cursor goes, so it is part of the raw
    estimate - and a pure constant offset must never look like a saccade."""
    seen = []
    det = SaccadeDetector(velocity_px_s=1500, min_px=120)
    orig = det.update
    det.update = lambda x, y, t: seen.append((x, y)) or orig(x, y, t)
    sj = SaccadeJump(det)
    sj.step(_ident, (100, 100), (5.0, -7.0), 0.0, deque(), [])
    assert seen == [(105.0, 93.0)]


def test_step_never_raises_and_switches_itself_off():
    said = []
    calls = []

    def boom(g):
        calls.append(g)
        raise RuntimeError("bozuk")

    sj = SaccadeJump(SaccadeDetector(), log=said.append)
    hist = deque([(1, 1)])
    assert sj.step(boom, (1, 1), (0, 0), 0.0, hist, [_Filt()]) is False
    assert sj.step(boom, (1, 1), (0, 0), DT, hist, [_Filt()]) is False
    assert len(calls) == 1                     # disabled after the first fault
    assert sj.enabled is False
    assert len(said) == 1 and said[0].isascii() and "bozuk" in said[0]
    assert list(hist) == [(1, 1)]


def test_a_filter_reset_fault_is_contained():
    class _Bad:
        def reset(self):
            raise ValueError("x")

    said = []
    sj = SaccadeJump(SaccadeDetector(velocity_px_s=1500, min_px=120),
                     log=said.append)
    t = 0.0
    for g in [(300, 300)] * 10 + [(900, 300)] * 2:
        sj.step(_ident, g, (0, 0), t, deque(), [_Bad()])
        t += DT
    assert sj.enabled is False and said


def test_make_saccade_jump_is_off_by_default():
    from eye_gaze.config import DEFAULTS
    assert DEFAULTS["saccade_jump"] is False
    assert make_saccade_jump(dict(DEFAULTS), log=lambda s: None) is None
    assert make_saccade_jump({}, log=lambda s: None) is None


def test_make_saccade_jump_reads_the_config():
    said = []
    sj = make_saccade_jump({"saccade_jump": True, "saccade_velocity_px_s": 2500,
                            "saccade_min_px": 200}, log=said.append)
    assert sj is not None
    assert sj.detector.velocity == 2500 and sj.detector.min_px == 200
    assert said and all(s.isascii() for s in said)


def test_make_saccade_jump_never_raises_on_bad_values():
    said = []
    sj = make_saccade_jump({"saccade_jump": True, "saccade_velocity_px_s": "hizli",
                            "saccade_min_px": None}, log=said.append)
    # unusable numbers fall back to the defaults rather than killing startup
    assert sj is not None and sj.detector.velocity > 0 and sj.detector.min_px > 0


def test_config_keys_are_known_and_coerced(tmp_path, monkeypatch):
    from eye_gaze import config as config_mod
    for k in ("saccade_jump", "saccade_velocity_px_s", "saccade_min_px"):
        assert k in config_mod.KNOWN_KEYS
    monkeypatch.setattr(config_mod, "_report", lambda m: None, raising=False)
    p = tmp_path / "c.yaml"
    p.write_text('left_click_key: space\nsaccade_jump: true\n'
                 'saccade_velocity_px_s: "1800"\nsaccade_min_px: 150,5\n',
                 encoding="utf-8")
    cfg = config_mod.load_config(str(p))
    assert cfg["saccade_velocity_px_s"] == 1800.0
    assert cfg["saccade_min_px"] == pytest.approx(150.5)


def test_driver_wires_the_feature():
    """Guard for the merge: the main loop must build it and step it."""
    import inspect
    import gaze_driver as gd
    src = inspect.getsource(gd.main)
    assert "make_saccade_jump(cfg" in src
    assert "saccader.step(" in src


# --- offline evaluation: the real filter chain on a synthetic trace -----------
#
# Mirrors gaze_driver.main(): median over the last N raw samples -> 1-Euro ->
# speed limiter -> fixation stabilizer, with the shipped config.yaml values.

from eye_gaze.limiter import SpeedLimiter          # noqa: E402
from eye_gaze.oneeuro import OneEuroFilter         # noqa: E402
from eye_gaze.stabilizer import FixationStabilizer  # noqa: E402

FIX_A, FIX_B = (300.0, 300.0), (1000.0, 520.0)
T_SACC = 2.0
T_END = 4.5


def _trace(seed=7, sigma=12.0):
    """fixation A (with a 1-frame spike and a blink) -> saccade -> fixation B."""
    rnd = random.Random(seed)
    out = []
    i = 0
    while True:
        t = i * DT
        i += 1
        if t > T_END:
            break
        if 1.0 <= t < 1.2:                     # blink: frames are not fed
            continue
        if t < T_SACC:
            cx, cy = FIX_A
        elif t < T_SACC + DT:                  # one mid-flight sample
            cx = (FIX_A[0] + FIX_B[0]) / 2
            cy = (FIX_A[1] + FIX_B[1]) / 2
        else:
            cx, cy = FIX_B
        x, y = cx + rnd.gauss(0, sigma), cy + rnd.gauss(0, sigma)
        if abs(t - 0.6) < DT / 2:              # glasses glint
            x += 400
        out.append((t, x, y))
    return out


def _run_chain(trace, jump):
    hist = deque(maxlen=7)
    fx = OneEuroFilter(30.0, 0.2, 0.002, 1.0)
    fy = OneEuroFilter(30.0, 0.2, 0.002, 1.0)
    limiter = SpeedLimiter(1200)
    stab = FixationStabilizer(20)
    sj = SaccadeJump(SaccadeDetector(velocity_px_s=1500, min_px=120)) if jump else None
    out = []
    for t, x, y in trace:
        g = (x, y)
        hist.append(g)
        if sj is not None:
            sj.step(_ident, g, (0.0, 0.0), t, hist, (fx, fy, limiter, stab))
        mx = sorted(p[0] for p in hist)[len(hist) // 2] if len(hist) % 2 else \
            sum(sorted(p[0] for p in hist)[len(hist) // 2 - 1:len(hist) // 2 + 1]) / 2
        my = sorted(p[1] for p in hist)[len(hist) // 2] if len(hist) % 2 else \
            sum(sorted(p[1] for p in hist)[len(hist) // 2 - 1:len(hist) // 2 + 1]) / 2
        lx, ly = limiter.update(fx(mx, t), fy(my, t), t)
        lx, ly = stab.update(lx, ly)
        out.append((t, lx, ly))
    return out, sj


def _time_to_target(out, target, radius=40.0):
    for t, x, y in out:
        if t >= T_SACC and math.hypot(x - target[0], y - target[1]) <= radius:
            return t - T_SACC
    return float("inf")


def _path_length(out, t0, t1):
    pts = [(x, y) for t, x, y in out if t0 <= t < t1]
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def test_offline_eval_time_to_target_improves():
    trace = _trace()
    off, _ = _run_chain(trace, jump=False)
    on, sj = _run_chain(trace, jump=True)
    tt_off = _time_to_target(off, FIX_B)
    tt_on = _time_to_target(on, FIX_B)
    assert sj.count == 1, "spike or blink was taken for a saccade"
    # today's chain needs most of a second for a 740 px jump ...
    assert tt_off > 0.4, tt_off
    # ... with the bypass the cursor is there within ~3 frames
    assert tt_on <= 0.12, tt_on
    assert tt_on < tt_off / 3, (tt_on, tt_off)


def test_offline_eval_no_extra_jitter_during_fixation():
    for seed in (7, 8, 9):
        trace = _trace(seed=seed)
        off, _ = _run_chain(trace, jump=False)
        on, _ = _run_chain(trace, jump=True)
        # before the saccade the outputs are the same chain, untouched
        assert _path_length(on, 0.0, T_SACC) == pytest.approx(
            _path_length(off, 0.0, T_SACC))
        # fixation B once BOTH have arrived: jump must not add shimmer
        j_on = _path_length(on, T_SACC + 1.0, T_END)
        j_off = _path_length(off, T_SACC + 1.0, T_END)
        assert j_on <= j_off * 1.10 + 2.0, (seed, j_on, j_off)
        # and right after landing the bypassed cursor settles - it does not
        # wander more than a deadzone or two while the filters warm up again
        j_land = _path_length(on, T_SACC + 0.15, T_SACC + 1.0)
        assert j_land <= 60.0, (seed, j_land)
