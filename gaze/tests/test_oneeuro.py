# gaze/tests/test_oneeuro.py
import pytest
from eye_gaze.oneeuro import OneEuroFilter


def test_constant_input_converges_to_constant():
    f = OneEuroFilter(freq=30.0, mincutoff=1.0, beta=0.0)
    out = None
    for _ in range(200):
        out = f(5.0)
    assert abs(out - 5.0) < 1e-6


def test_reduces_jitter_variance():
    import random
    import statistics
    rnd = random.Random(0)
    f = OneEuroFilter(freq=30.0, mincutoff=0.5, beta=0.0)
    raw = [100.0 + rnd.uniform(-5, 5) for _ in range(300)]
    filt = [f(v) for v in raw]
    assert statistics.pvariance(filt[50:]) < statistics.pvariance(raw[50:])


def test_invalid_freq_raises():
    with pytest.raises(ValueError):
        OneEuroFilter(freq=0.0)


def test_timestamped_matches_fixed_freq_when_dt_equals_period():
    fa = OneEuroFilter(freq=30.0, mincutoff=1.0, beta=0.01)
    fb = OneEuroFilter(freq=30.0, mincutoff=1.0, beta=0.01)
    vals = [5.0, 7.0, 6.0, 9.0, 8.5, 10.0]
    outs_a = [fa(v) for v in vals]
    outs_b = [fb(v, t=i / 30.0) for i, v in enumerate(vals)]
    for a, b in zip(outs_a, outs_b):
        assert abs(a - b) < 1e-9


def test_timestamped_constant_input_converges():
    f = OneEuroFilter(freq=30.0, mincutoff=1.0, beta=0.0)
    out = None
    t = 0.0
    for _ in range(200):
        out = f(5.0, t=t)
        t += 0.021  # slightly slower than the nominal 30 fps
    assert abs(out - 5.0) < 1e-6


def test_non_increasing_timestamp_falls_back_to_nominal_freq():
    f = OneEuroFilter(freq=30.0, mincutoff=1.0, beta=0.0)
    f(5.0, t=1.0)
    assert f(6.0, t=1.0) is not None  # same timestamp: no ZeroDivisionError


def test_reset_starts_over_from_the_next_value():
    """Saccade bypass: after reset() the filter must not remember where the
    eye WAS - the next value passes straight through, like a fresh filter."""
    f = OneEuroFilter(freq=30.0, mincutoff=0.2, beta=0.002)
    for i in range(60):
        f(100.0, i / 30.0)
    f.reset()
    assert f(900.0, 2.5) == 900.0
    fresh = OneEuroFilter(freq=30.0, mincutoff=0.2, beta=0.002)
    fresh(900.0, 2.5)
    assert f(905.0, 2.5 + 1 / 30.0) == fresh(905.0, 2.5 + 1 / 30.0)


def test_a_nan_input_does_not_freeze_the_filter_forever():
    """One NaN used to stay in the state: every later output NaN, and
    int(nan) in cursor.clamp raised on every frame - a frozen cursor."""
    f = OneEuroFilter(30.0, 1.0, 0.007, 1.0)
    for i in range(5):
        f(100.0, t=i / 30)
    held = f(float("nan"), t=5 / 30)
    assert held == pytest.approx(100.0)
    assert f(float("inf"), t=6 / 30) == pytest.approx(100.0)
    out = f(200.0, t=7 / 30)
    assert out == out and 100.0 < out <= 200.0


def test_nan_as_the_very_first_value_is_not_stored():
    f = OneEuroFilter(30.0)
    f(float("nan"))
    assert f(50.0) == 50.0
