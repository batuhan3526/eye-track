# gaze/tests/test_dwell.py
from eye_gaze.dwell import DwellClicker


def _run(d, points):
    """points: list of (x, y, t); returns times at which a click fired."""
    return [t for (x, y, t) in points if d.update(x, y, t)]


def test_stationary_fires_once_after_dwell_time():
    d = DwellClicker(radius_px=40, dwell_seconds=1.0)
    fires = _run(d, [(100, 100, 0.0), (105, 102, 0.4), (98, 99, 0.9),
                     (101, 100, 1.1), (100, 101, 1.5), (99, 100, 3.0)])
    assert fires == [1.1]  # once, not repeatedly while still stationary


def test_movement_resets_the_timer():
    d = DwellClicker(radius_px=40, dwell_seconds=1.0)
    fires = _run(d, [(100, 100, 0.0), (500, 500, 0.8),  # big move resets
                     (500, 500, 1.5), (500, 502, 1.9)])
    assert fires == [1.9]  # 0.8 -> 1.9 is >= 1.0 at the new anchor


def test_small_drift_within_radius_does_not_reset():
    d = DwellClicker(radius_px=40, dwell_seconds=1.0)
    fires = _run(d, [(100, 100, 0.0), (130, 100, 0.5), (110, 120, 1.05)])
    assert fires == [1.05]


def test_must_leave_radius_before_next_click():
    d = DwellClicker(radius_px=40, dwell_seconds=1.0)
    fires = _run(d, [(100, 100, 0.0), (100, 100, 1.0),          # first click
                     (100, 100, 2.5), (100, 100, 9.0),          # still parked: no refire
                     (300, 300, 9.5), (100, 100, 10.0),         # left and came back
                     (100, 100, 11.1)])
    assert fires == [1.0, 11.1]


def test_repeat_mode_refires_while_parked():
    d = DwellClicker(radius_px=40, dwell_seconds=1.0, repeat=True)
    fires = _run(d, [(100, 100, 0.0), (100, 100, 1.0), (100, 100, 1.5),
                     (100, 100, 2.1), (100, 100, 3.2)])
    assert fires == [1.0, 2.1, 3.2]


# --- what the dwell progress ring reads ------------------------------------
#
# The ring is drawn from the dweller's own state, so it can never show a
# different dwell from the one that actually clicks.

def test_dweller_remembers_the_last_position_and_time_it_was_fed():
    d = DwellClicker(radius_px=40, dwell_seconds=1.0)
    assert d.last_t is None and d.last_pos is None and d.fires == 0
    d.update(100, 200, 0.0)
    d.update(104, 199, 0.3)
    assert d.last_t == 0.3
    assert d.last_pos == (104, 199)


def test_dweller_counts_its_clicks():
    d = DwellClicker(radius_px=40, dwell_seconds=1.0, repeat=True)
    for t in (0.0, 1.0, 1.5, 2.1):
        d.update(100, 100, t)
    assert d.fires == 2


def test_progress_is_zero_after_a_one_shot_click():
    d = DwellClicker(radius_px=40, dwell_seconds=1.0)
    d.update(100, 100, 0.0)
    assert d.progress_at(0.5) == 0.5
    assert d.update(100, 100, 1.0)
    assert d.progress_at(1.2) == 0.0


def test_a_fired_dwell_does_not_refire_after_a_feeding_gap():
    """Clicked a button, then eyes closed (or face lost, camera reconnect,
    pause) for longer than MAX_GAP with the cursor frozen on it: reopening
    the eyes on the same spot must not click it a second time."""
    d = DwellClicker(radius_px=40, dwell_seconds=1.0)
    fires = _run(d, [(500, 500, 0.0), (500, 500, 1.0),          # the click
                     (502, 498, 3.5), (502, 498, 4.0),          # 2.5 s unfed
                     (502, 498, 4.6), (501, 499, 6.0)])
    assert fires == [1.0]


def test_after_a_feeding_gap_leaving_and_returning_clicks_again():
    d = DwellClicker(radius_px=40, dwell_seconds=1.0)
    fires = _run(d, [(500, 500, 0.0), (500, 500, 1.0),
                     (500, 500, 5.0),                           # gap, no refire
                     (900, 900, 5.2), (500, 500, 5.4), (500, 500, 6.4)])
    assert fires == [1.0, 6.4]


def test_resting_the_eyes_does_not_complete_a_dwell():
    """Closed-eye frames are not fed; the 2 s gap rule is far longer than a
    rest. Fixate 0.3 s, close 0.8 s -> clicked on the first open frame."""
    from eye_gaze.dwell import DwellClicker
    d = DwellClicker(radius_px=40, dwell_seconds=1.0)
    assert d.update(100, 100, 0.0) is False
    assert d.update(101, 100, 0.3) is False
    d.restart()                                   # eyes closed 0.3 .. 1.1
    assert d.update(101, 100, 1.1) is False       # countdown starts again
    assert d.update(101, 100, 1.9) is False
    assert d.update(101, 100, 2.1) is True        # a real 1 s dwell


def test_restart_does_not_let_the_same_spot_fire_twice():
    from eye_gaze.dwell import DwellClicker
    d = DwellClicker(radius_px=40, dwell_seconds=1.0)
    d.update(100, 100, 0.0)
    assert d.update(100, 100, 1.0) is True
    d.restart()
    assert d.update(100, 100, 1.5) is False
    assert d.update(100, 100, 3.0) is False       # must leave the radius first
