# gaze/tests/test_cursor.py
from eye_gaze.cursor import clamp


def test_clamp_within_bounds():
    assert clamp(100, 200, 1920, 1080) == (100, 200)


def test_clamp_negative_to_zero():
    assert clamp(-5, -10, 1920, 1080) == (0, 0)


def test_clamp_over_to_max():
    assert clamp(5000, 5000, 1920, 1080) == (1919, 1079)


def test_clamp_with_virtual_origin():
    # a secondary monitor left of the primary has negative coordinates
    assert clamp(-500, 300, 3840, 1080, ox=-1920, oy=0) == (-500, 300)
    assert clamp(-5000, 300, 3840, 1080, ox=-1920, oy=0) == (-1920, 300)
    assert clamp(5000, 5000, 3840, 1080, ox=-1920, oy=0) == (1919, 1079)
