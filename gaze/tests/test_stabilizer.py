# gaze/tests/test_stabilizer.py
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
