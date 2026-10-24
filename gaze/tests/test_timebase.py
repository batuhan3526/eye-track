# gaze/tests/test_timebase.py
"""Frame counts follow the measured loop rate (eye_gaze.timebase)."""
from collections import deque

import pytest

from eye_gaze import timebase
from eye_gaze.timebase import FrameRate, LoopTiming, frames, per_frame_rate


def test_old_frame_counts_are_unchanged_at_30_fps():
    for n in range(1, 30):
        assert frames(timebase.ref_seconds(n), 30.0) == n
    assert frames(timebase.SCROLL_PERIOD_S, 30.0) == 4


def test_frames_scale_with_the_rate_and_never_reach_zero():
    s = timebase.ref_seconds(7)
    assert frames(s, 60.0) == 14
    assert frames(s, 15.0) == 4          # 3.5 -> nearest
    assert frames(s, 5.0) >= 1
    assert frames(0.0, 30.0) == 1
    assert frames(timebase.ref_seconds(2), 15.0, minimum=2) == 2


def test_per_frame_rate_keeps_the_same_easing_per_second():
    assert per_frame_rate(0.25, 30.0) == 0.25
    r60 = per_frame_rate(0.25, 60.0)
    assert (1 - r60) ** 2 == pytest.approx(0.75)
    r15 = per_frame_rate(0.25, 15.0)
    assert 1 - r15 == pytest.approx(0.75 ** 2)


def _feed(rate, fps, seconds, t0=0.0):
    t, changed = t0, False
    for _ in range(int(seconds * fps)):
        changed = rate.tick(t) or changed
        t += 1.0 / fps
    return t, changed


def test_reports_30_until_enough_intervals():
    r = FrameRate()
    assert r.fps == 30.0
    for i in range(10):
        assert r.tick(i / 15.0) is False
    assert r.fps == 30.0                 # still warming up


def test_settles_on_the_real_rate():
    r = FrameRate()
    _, changed = _feed(r, 15.0, 3.0)
    assert changed and r.fps == pytest.approx(15.0, rel=0.01)
    _feed(r, 60.0, 3.0, t0=100.0)
    assert r.fps == pytest.approx(60.0, rel=0.01)


def test_small_wobble_does_not_republish():
    r = FrameRate()
    t = 0.0
    changes = 0
    for i in range(600):                 # 27-33 fps
        fps = 30.0 + 3.0 * ((-1) ** i)
        changes += r.tick(t)
        t += 1.0 / fps
    assert changes == 0 and r.fps == 30.0


def test_gaps_and_single_slow_frames_are_ignored():
    r = FrameRate()
    t, _ = _feed(r, 30.0, 2.0)
    r.tick(t + 5.0)                      # a pause: not an interval
    t += 5.0
    for i in range(1, 60):
        # every 10th frame takes 0.3 s (a model load) - the median holds
        t += 0.3 if i % 10 == 0 else 1.0 / 30.0
        r.tick(t)
    assert r.fps == 30.0


def test_rate_is_clamped_and_garbage_is_harmless():
    r = FrameRate()
    _feed(r, 400.0, 1.0)
    assert r.fps == timebase.MAX_FPS
    assert r.tick("not a time") is False
    assert FrameRate(nominal="x").fps == 30.0


class _Target:
    def __init__(self):
        self.seen = []

    def set_frame_rate(self, fps):
        self.seen.append(fps)


class _Broken:
    def set_frame_rate(self, fps):
        raise RuntimeError("boom")


def test_loop_timing_converts_and_resizes():
    tgt = _Target()
    lt = LoopTiming(timebase.ref_seconds(7), targets=(tgt, _Broken(), None))
    assert lt.median_frames == 7 and lt.scroll_frames == 4
    assert tgt.seen == [30.0]
    hist = deque(range(7), maxlen=7)
    assert lt.resize(hist) is hist
    t = 0.0
    changed = False
    for _ in range(90):
        changed = lt.tick(t) or changed
        t += 1.0 / 15.0
    assert changed
    assert lt.median_frames == 4 and lt.scroll_frames == 2
    assert tgt.seen[-1] == pytest.approx(15.0, rel=0.01)
    small = lt.resize(hist)
    assert small.maxlen == 4 and list(small) == [3, 4, 5, 6]   # newest kept
