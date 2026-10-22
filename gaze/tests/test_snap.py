"""Snap-to-target: pull the gaze cursor onto the nearest clickable element.

Everything here runs without COM: the UI Automation calls are injected, so
the tests pin the decisions (which element, where to put the cursor, when to
let go) rather than Windows.
"""
import threading
import time

import pytest

from eye_gaze import snap


# --- geometry ---------------------------------------------------------------


def test_distance_is_zero_inside_and_euclidean_outside():
    r = (100, 100, 200, 150)
    assert snap.rect_distance(150, 120, r) == 0
    assert snap.rect_distance(90, 120, r) == 10            # left of it
    assert snap.rect_distance(203, 154, r) == 5            # corner: 3-4-5


def test_sample_points_cover_the_radius_around_the_gaze():
    pts = snap.sample_points(500, 400, 100)
    assert (500, 400) in pts
    assert len(pts) == 17
    far = max(((x - 500) ** 2 + (y - 400) ** 2) ** 0.5 for x, y in pts)
    assert 99 <= far <= 101


# --- choosing a target ------------------------------------------------------


def test_the_element_under_the_gaze_wins():
    under = (100, 100, 200, 140)
    near = (210, 100, 300, 140)
    assert snap.pick_target(150, 120, [near, under], 120) == under


def test_the_nearest_element_within_the_radius_is_chosen():
    a = (300, 100, 340, 140)      # 100 px to the right
    b = (100, 200, 140, 240)      # 60 px below
    assert snap.pick_target(120, 140, [a, b], 120) == b


def test_nothing_beyond_the_radius_is_ever_chosen():
    assert snap.pick_target(0, 0, [(500, 500, 540, 540)], 120) is None
    assert snap.pick_target(0, 0, [], 120) is None


def test_of_nested_elements_under_the_gaze_the_smaller_one_wins():
    """A list item inside a bigger clickable row: the item is the target."""
    row = (0, 100, 1000, 160)
    icon = (20, 110, 60, 150)
    assert snap.pick_target(40, 130, [row, icon], 120) == icon


def test_the_current_target_is_kept_while_the_gaze_wobbles_nearby():
    """Webcam gaze jitters by tens of pixels. A neighbouring button that is
    a little closer for one frame must not steal the cursor."""
    cur = (100, 100, 160, 140)
    other = (200, 100, 260, 140)
    # gaze between the two, slightly nearer to `other`
    assert snap.pick_target(185, 120, [cur, other], 120, current=cur) == cur


def test_looking_INTO_another_element_switches_to_it():
    cur = (100, 100, 160, 140)
    other = (200, 100, 260, 140)
    assert snap.pick_target(210, 120, [cur, other], 120, current=cur) == other


def test_the_current_target_is_released_once_the_gaze_leaves_its_reach():
    cur = (100, 100, 160, 140)
    assert snap.pick_target(600, 600, [cur], 120, current=cur) is None


# --- where the cursor goes --------------------------------------------------


def test_a_small_target_is_hit_in_its_centre():
    assert snap.snap_point(95, 90, (100, 100, 140, 120), 120) == (120, 110)


def test_a_very_wide_target_keeps_the_gaze_x_and_centres_y():
    """An Explorer details row 1500 px wide: jumping to its middle would
    throw the cursor across the screen."""
    row = (0, 300, 1500, 330)
    x, y = snap.snap_point(700, 280, row, 120)
    assert x == 700 and y == 315


def test_a_wide_target_pulls_the_gaze_inside_its_edge():
    row = (100, 300, 1600, 330)
    x, _ = snap.snap_point(40, 315, row, 120)
    assert 100 < x <= 100 + 120


# --- collecting targets from an element tree --------------------------------


class _El:
    def __init__(self, ctype, rect, pid=10, parent=None):
        self.ctype, self.rect, self.pid, self.parent = ctype, rect, pid, parent


def _info(e):
    return e.ctype, e.pid


def _bounds(e):
    return e.rect


def _collect(points_to_el, own_pid=1, max_w=1000, max_h=600):
    return snap.collect_targets(
        list(points_to_el), lambda x, y: points_to_el[(x, y)],
        _info, _bounds, lambda e: e.parent,
        own_pid=own_pid, max_w=max_w, max_h=max_h)


def test_text_inside_a_button_yields_the_button():
    button = _El(snap.BUTTON, (10, 10, 90, 40))
    label = _El(snap.TEXT, (20, 15, 80, 35), parent=button)
    assert _collect({(30, 20): label}) == [(10, 10, 90, 40)]


def test_panes_documents_and_windows_are_never_targets():
    doc = _El(snap.DOCUMENT, (0, 0, 800, 600))
    text = _El(snap.TEXT, (10, 10, 50, 20), parent=doc)
    assert _collect({(15, 15): text, (400, 300): doc}) == []


def test_our_own_windows_are_ignored():
    """The dwell ring and the overlays belong to the driver process."""
    mine = _El(snap.BUTTON, (10, 10, 90, 40), pid=1)
    assert _collect({(30, 20): mine}, own_pid=1) == []


def test_huge_elements_are_not_targets():
    big = _El(snap.LIST_ITEM, (0, 0, 2000, 100))
    assert _collect({(30, 20): big}, max_w=1000) == []


def test_empty_and_offscreen_rects_are_dropped():
    flat = _El(snap.BUTTON, (10, 10, 10, 40))
    assert _collect({(10, 20): flat}) == []


def test_the_same_element_seen_from_many_points_is_listed_once():
    b = _El(snap.HYPERLINK, (10, 10, 90, 40))
    assert _collect({(20, 20): b, (60, 30): b}) == [(10, 10, 90, 40)]


def test_a_failing_point_does_not_lose_the_others():
    b = _El(snap.BUTTON, (10, 10, 90, 40))

    def at(x, y):
        if x == 0:
            raise OSError("COM hatasi")
        return b
    got = snap.collect_targets([(0, 0), (20, 20)], at, _info, _bounds,
                               lambda e: e.parent, own_pid=1,
                               max_w=1000, max_h=600)
    assert got == [(10, 10, 90, 40)]


def test_bounds_are_asked_only_of_the_clickable_element():
    """The CACHED rectangle comes back in PHYSICAL pixels in a DPI-unaware
    process (measured: x1.5 at 150 % scaling) while the cursor is
    logical, so bounds() reads the live (logical) rectangle - one extra
    cross-process call, paid only for the element that is kept."""
    asked = []
    button = _El(snap.BUTTON, (10, 10, 90, 40))
    label = _El(snap.TEXT, (20, 15, 80, 35), parent=button)

    def bounds(e):
        asked.append(e)
        return e.rect
    got = snap.collect_targets([(30, 20)], lambda x, y: label, _info, bounds,
                               lambda e: e.parent, own_pid=1,
                               max_w=1000, max_h=600)
    assert got == [(10, 10, 90, 40)]
    assert asked == [button]


def test_the_parent_walk_is_bounded():
    """A deep tree of non-clickable elements must not cost a long walk per
    sample point (cross-process calls)."""
    calls = []
    top = _El(snap.BUTTON, (0, 0, 50, 50))
    el = top
    for _ in range(20):
        el = _El(snap.GROUP, (0, 0, 50, 50), parent=el)

    def parent(e):
        calls.append(e)
        return e.parent
    got = snap.collect_targets([(5, 5)], lambda x, y: el, _info, _bounds,
                               parent,
                               own_pid=1, max_w=1000, max_h=600)
    assert got == []
    assert len(calls) <= snap.MAX_PARENT_STEPS


# --- the background finder --------------------------------------------------


def _wait(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_the_finder_answers_in_the_background():
    asked = []

    def make_query():
        def q(x, y):
            asked.append((x, y))
            return [(x - 10, y - 10, x + 10, y + 10)]
        return q
    f = snap.TargetFinder(make_query, min_interval=0.0)
    try:
        f.request(300, 200)
        assert _wait(lambda: f.snapshot() is not None)
        t, point, rects = f.snapshot()
        assert point == (300, 200)
        assert rects == [(290, 190, 310, 210)]
    finally:
        f.close()


def test_the_query_object_is_built_on_the_worker_thread():
    """COM objects belong to the thread that created them."""
    where = []

    def make_query():
        where.append(threading.current_thread())
        return lambda x, y: []
    f = snap.TargetFinder(make_query, min_interval=0.0)
    try:
        f.request(1, 1)
        assert _wait(lambda: f.snapshot() is not None)
        assert where and where[0] is not threading.main_thread()
    finally:
        f.close()


def test_a_query_that_keeps_failing_switches_the_finder_off():
    logs = []

    def make_query():
        def q(x, y):
            raise OSError("UIA yok")
        return q
    f = snap.TargetFinder(make_query, min_interval=0.0, max_errors=3,
                          log=logs.append)
    try:
        for i in range(10):
            f.request(i * 50, 0)
            time.sleep(0.01)
        assert _wait(lambda: f.failed)
        assert any("KAPATILDI" in m for m in logs), logs
    finally:
        f.close()


def test_a_transient_start_failure_is_retried():
    """Two UI Automation clients starting at the same moment in one process
    fail ~1 in 10 with E_FAIL (measured). One bad start must not cost the
    feature for the whole session."""
    tries = []

    def make_query():
        tries.append(1)
        if len(tries) < 3:
            raise OSError("E_FAIL")
        return lambda x, y: [(0, 0, 10, 10)]
    f = snap.TargetFinder(make_query, min_interval=0.0, start_retry_s=0.01,
                          log=lambda m: None)
    try:
        f.request(5, 5)
        assert _wait(lambda: f.snapshot() is not None)
        assert not f.failed and len(tries) == 3
    finally:
        f.close()


def test_uia_start_is_serialised_across_threads():
    """The E_FAIL above comes from CONCURRENT starts; they take one lock."""
    import inspect
    src = inspect.getsource(snap.uia_query_factory)
    assert "with _UIA_START_LOCK:" in src
    assert isinstance(snap._UIA_START_LOCK, type(threading.Lock()))


def test_a_finder_that_cannot_even_start_is_failed_not_fatal():
    def make_query():
        raise OSError("comtypes yok")
    logs = []
    f = snap.TargetFinder(make_query, log=logs.append)
    try:
        assert _wait(lambda: f.failed)
    finally:
        f.close()


def test_close_stops_the_worker():
    f = snap.TargetFinder(lambda: (lambda x, y: []), min_interval=0.0)
    f.close()
    assert _wait(lambda: not f._thread.is_alive())


# --- the driver glue --------------------------------------------------------


class _FakeFinder:
    def __init__(self, snap_=None):
        self.snap = snap_
        self.requests = []
        self.failed = False

    def request(self, x, y):
        self.requests.append((x, y))

    def snapshot(self):
        return self.snap

    def close(self):
        pass


def test_adjust_snaps_onto_a_fresh_nearby_target():
    f = _FakeFinder((10.0, (500, 400), [(520, 380, 560, 420)]))
    s = snap.TargetSnap(f, radius=120)
    assert s.adjust(500, 400, now=10.1) == (540, 400)
    assert f.requests[-1] == (500, 400)


def test_a_stale_answer_is_not_used():
    f = _FakeFinder((10.0, (500, 400), [(520, 380, 560, 420)]))
    s = snap.TargetSnap(f, radius=120, max_age=0.5)
    assert s.adjust(500, 400, now=12.0) == (500, 400)


def test_an_answer_for_a_far_away_point_is_not_used():
    """The eye jumped; the worker has not caught up. Snapping to targets
    found around the OLD point would drag the cursor back."""
    f = _FakeFinder((10.0, (100, 100), [(120, 80, 160, 120)]))
    s = snap.TargetSnap(f, radius=120)
    assert s.adjust(900, 700, now=10.1) == (900, 700)


def test_inactive_passes_the_gaze_through_and_forgets_the_target():
    f = _FakeFinder((10.0, (500, 400), [(520, 380, 560, 420)]))
    s = snap.TargetSnap(f, radius=120)
    s.adjust(500, 400, now=10.1)
    assert s.adjust(500, 400, now=10.2, active=False) == (500, 400)
    assert s.current is None


def test_adjust_never_raises():
    class Boom(_FakeFinder):
        def snapshot(self):
            raise RuntimeError("x")
    s = snap.TargetSnap(Boom(), radius=120)
    assert s.adjust(5, 6, now=1.0) == (5, 6)


def test_a_failed_finder_means_plain_gaze():
    f = _FakeFinder((10.0, (500, 400), [(520, 380, 560, 420)]))
    f.failed = True
    s = snap.TargetSnap(f, radius=120)
    assert s.adjust(500, 400, now=10.1) == (500, 400)


@pytest.mark.parametrize("value", [False, "hayir", "false"])
def test_make_target_snap_respects_off(value):
    assert snap.make_target_snap({"snap_to_targets": value}, 2560, 1440,
                                 make_query=lambda: None) is None


def test_make_target_snap_reads_the_settings():
    s = snap.make_target_snap({"snap_to_targets": True, "snap_radius": 90,
                               "snap_max_target": 0.3}, 2000, 1000,
                              make_query=lambda: (lambda x, y: []),
                              log=lambda m: None)
    try:
        assert s.radius == 90
        assert s.max_w == 600 and s.max_h == 300
    finally:
        s.close()
