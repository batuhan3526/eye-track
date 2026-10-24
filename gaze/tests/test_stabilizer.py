# gaze/tests/test_stabilizer.py
import pytest

from eye_gaze.stabilizer import FixationStabilizer


def test_first_point_passes_through():
    s = FixationStabilizer(deadzone_px=25)
    assert s.update(500, 300) == (500, 300)


def test_jitter_inside_deadzone_is_frozen():
    s = FixationStabilizer(deadzone_px=25)
    s.update(500, 300)
    assert s.update(510, 305) == (500, 300)   # micro-jitter -> locked
    assert s.update(495, 292) == (500, 300)
    assert s.update(500, 300) == (500, 300)


def test_real_movement_follows_with_slack():
    # rope model: once outside the deadzone the output trails the target by
    # exactly the deadzone radius - no sudden jump, no jitter
    s = FixationStabilizer(deadzone_px=25)
    s.update(0, 0)
    x, y = s.update(100, 0)
    assert abs(x - 75) < 1e-6 and y == 0      # 100 - 25 slack


def test_long_travel_tracks_continuously():
    s = FixationStabilizer(deadzone_px=25)
    s.update(0, 0)
    out = None
    for tx in range(0, 1001, 50):
        out = s.update(tx, 0)
    assert abs(out[0] - 975) < 1e-6           # trailing by the deadzone


def test_zero_deadzone_disables():
    s = FixationStabilizer(deadzone_px=0)
    s.update(100, 100)
    assert s.update(101, 101) == (101, 101)


def test_reset():
    s = FixationStabilizer(deadzone_px=25)
    s.update(100, 100)
    s.reset()
    assert s.update(900, 900) == (900, 900)


# --- the rope must not leave the cursor short of the target forever ---------
#
# Simulated: the lock stopped exactly `deadzone` px short along the approach
# and stayed there for the whole fixation - 20 px permanent undershoot on the
# targets this user is trying to hit.

import random


def test_a_held_fixation_settles_onto_the_target():
    s = FixationStabilizer(deadzone_px=20)
    s.update(0, 0)
    s.update(100, 0)                         # lock trails at 80
    out = None
    for _ in range(30):                      # ~1 s of steady fixation
        out = s.update(100, 0)
    assert abs(out[0] - 100) <= 5 and out[1] == 0


def test_jittery_fixation_settles_but_stays_calm():
    rnd = random.Random(7)
    s = FixationStabilizer(deadzone_px=20)
    s.update(500, 500)
    s.update(600, 500)                       # arrive with 20 px slack
    outs = [s.update(600 + rnd.gauss(0, 5), 500 + rnd.gauss(0, 5))
            for _ in range(90)]
    settled = outs[-30:]
    assert all(abs(x - 600) <= 8 and abs(y - 500) <= 8 for x, y in settled)
    # it must still be a deadband: almost no movement once settled
    moves = sum(1 for a, b in zip(settled, settled[1:]) if a != b)
    assert moves <= 3


def test_a_short_glance_does_not_trigger_settling():
    s = FixationStabilizer(deadzone_px=20)
    s.update(0, 0)
    s.update(100, 0)
    out = s.update(100, 0)
    assert abs(out[0] - 80) < 1e-6           # 2 frames: still the plain rope


# --- frame counts follow the real frame rate --------------------------------

def _run(s, pts):
    return [s.update(x, y) for x, y in pts]


def _jittery(seed, n=400):
    rnd = random.Random(seed)
    pts, cx = [], 500.0
    for i in range(n):
        if i % 60 == 0:
            cx += rnd.choice((-80.0, 35.0, 120.0))
        pts.append((cx + rnd.gauss(0, 7), 300 + rnd.gauss(0, 7)))
    return pts


def test_30_fps_is_bit_identical_to_the_frame_counted_stabilizer():
    pts = _jittery(3)
    plain = FixationStabilizer(deadzone_px=20)
    timed = FixationStabilizer(deadzone_px=20)
    timed.set_frame_rate(30.0)
    assert _run(plain, pts) == _run(timed, pts)


def test_settle_counts_and_easing_follow_the_rate():
    s = FixationStabilizer(deadzone_px=20)
    s.set_frame_rate(60.0)
    assert s._settle_frames == 10
    assert (1 - s._settle_rate) ** 2 == pytest.approx(0.75)
    s.set_frame_rate(15.0)
    assert s._settle_frames == 2          # 2.5 frames -> nearest even
    assert s._inside.maxlen >= s._settle_frames
    s.set_frame_rate(30.0)
    assert (s._settle_frames, s._settle_rate, s._inside.maxlen) == (5, 0.25, 12)


def test_settling_takes_about_the_same_time_at_any_rate():
    def time_to_settle(fps):
        s = FixationStabilizer(deadzone_px=20)
        s.set_frame_rate(fps)
        s.update(0, 0)
        s.update(30, 0)               # rope: lock at 10 px, 20 px short
        t, dt = 0.0, 1.0 / fps
        while s.update(30, 0)[0] < 26.0:
            t += dt
            assert t < 5.0
        return t
    t30 = time_to_settle(30.0)
    assert time_to_settle(60.0) == pytest.approx(t30, abs=2 / 30.0)
    assert time_to_settle(15.0) == pytest.approx(t30, abs=2 / 15.0)


def test_rate_change_keeps_the_recent_samples():
    s = FixationStabilizer(deadzone_px=20)
    s.update(0, 0)
    for _ in range(3):
        s.update(5, 0)
    s.set_frame_rate(60.0)
    assert len(s._inside) == 3
