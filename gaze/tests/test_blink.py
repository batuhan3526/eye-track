# gaze/tests/test_blink.py
from eye_gaze.blink import BlinkClicker


def test_open_eyes_never_fire():
    b = BlinkClicker(closed_threshold=0.18, hold_seconds=0.5)
    for t in range(10):
        assert b.update(0.4, t * 0.1) is False


def test_short_blink_does_not_fire():
    b = BlinkClicker(closed_threshold=0.18, hold_seconds=0.5)
    assert b.update(0.1, 0.0) is False
    assert b.update(0.1, 0.2) is False  # closed only 0.2s < 0.5s
    assert b.update(0.4, 0.3) is False  # reopened


def test_long_blink_fires_once():
    b = BlinkClicker(closed_threshold=0.18, hold_seconds=0.5)
    assert b.update(0.1, 0.0) is False
    assert b.update(0.1, 0.4) is False
    assert b.update(0.1, 0.6) is True   # held >= 0.5s -> fire
    assert b.update(0.1, 0.8) is False  # still closed -> no repeat
    # must reopen then close again to fire a second time
    b.update(0.4, 0.9)
    assert b.update(0.1, 1.0) is False
    assert b.update(0.1, 1.6) is True
