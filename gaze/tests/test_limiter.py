# gaze/tests/test_limiter.py
from eye_gaze.limiter import SpeedLimiter


def test_first_update_passes_through():
    s = SpeedLimiter(max_speed=1000)
    assert s.update(500, 300, t=0.0) == (500, 300)


def test_small_move_within_budget_passes_through():
    s = SpeedLimiter(max_speed=1000)
    s.update(100, 100, t=0.0)
    # 1000 px/s * 0.05 s = 50 px budget; a 30 px move fits
    assert s.update(130, 100, t=0.05) == (130, 100)


def test_big_jump_is_clamped_to_speed_budget():
    s = SpeedLimiter(max_speed=1000)
    s.update(0, 0, t=0.0)
    x, y = s.update(1000, 0, t=0.05)  # budget: 50 px
    assert abs(x - 50) < 1e-6 and y == 0


def test_glide_converges_to_target_over_frames():
    s = SpeedLimiter(max_speed=1000)
    s.update(0, 0, t=0.0)
    t, pos = 0.0, (0, 0)
    for _ in range(100):
        t += 0.05
        pos = s.update(1000, 0, t)
    assert abs(pos[0] - 1000) < 1e-6


def test_zero_speed_disables_limiting():
    s = SpeedLimiter(max_speed=0)
    s.update(0, 0, t=0.0)
    assert s.update(5000, 5000, t=0.01) == (5000, 5000)


def test_long_gap_does_not_teleport():
    # after a pause dt is huge; the budget must stay capped so the cursor
    # still glides instead of jumping across the screen
    s = SpeedLimiter(max_speed=1000)
    s.update(0, 0, t=0.0)
    x, y = s.update(3000, 0, t=60.0)
    assert x <= 200  # capped-dt budget, not 60000 px


def test_reset_forgets_position():
    s = SpeedLimiter(max_speed=1000)
    s.update(0, 0, t=0.0)
    s.reset()
    assert s.update(2000, 900, t=0.01) == (2000, 900)
