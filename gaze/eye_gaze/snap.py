"""Snap-to-target: pull the gaze cursor onto the nearest clickable element.

A webcam gaze cursor lands 80-150 px from where the user looks (measured in
real use). A folder icon or a link is smaller than that, so
"look and press Space" misses most real targets. Windows already knows where
every button, link, list item and edit box is (UI Automation), so instead of
asking the eye for more precision than a webcam can give, the cursor is
placed ON the clickable element nearest to the gaze.

Pieces, each testable without COM:
  * collect_targets(): ask the element under a handful of points around the
    gaze, walk up from it to the first clickable ancestor, keep its rectangle.
    The UI Automation calls are injected.
  * pick_target(): the element under the gaze, else the nearest within the
    radius; the current one is kept while the gaze wobbles near it (webcam
    jitter), and given up only when the gaze goes INTO another element or
    out of reach.
  * snap_point(): small targets are hit in the centre; a very wide one (an
    Explorer details row) keeps the gaze x so the cursor is not thrown across
    the screen.
  * TargetFinder: the queries are cross-process calls (3-7 ms each, more for
    a busy app, unbounded for a hung one), so they run on a worker thread and
    the gaze loop only ever reads the latest answer. A hung app freezes the
    worker, never the cursor: stale answers are simply not used.
  * TargetSnap: the driver glue. Never raises; any fault = plain gaze cursor.

Coordinates: UI Automation in a DPI-UNAWARE process (the driver is one)
reports and takes LOGICAL pixels - the same space as the cursor. Measured at
150 % scaling: the taskbar rectangle is the same from both
GetWindowRect and UIA, and ElementFromPoint at the logical centre of a
taskbar button returns that button.
"""
import math
import os
import threading
import time

# UIA_*ControlTypeId
BUTTON = 50000
CHECK_BOX = 50002
COMBO_BOX = 50003
EDIT = 50004
HYPERLINK = 50005
LIST_ITEM = 50007
MENU_ITEM = 50011
RADIO_BUTTON = 50013
TAB_ITEM = 50019
TEXT = 50020
TREE_ITEM = 50024
GROUP = 50026
DATA_ITEM = 50029
DOCUMENT = 50030
SPLIT_BUTTON = 50031
WINDOW = 50032
PANE = 50033
HEADER_ITEM = 50035

CLICKABLE = frozenset((BUTTON, CHECK_BOX, COMBO_BOX, EDIT, HYPERLINK,
                       LIST_ITEM, MENU_ITEM, RADIO_BUTTON, TAB_ITEM, TREE_ITEM,
                       DATA_ITEM, SPLIT_BUTTON, HEADER_ITEM))
# Reaching one of these without meeting a clickable element means the point
# is on background: a page, a pane, a window frame.
STOP = frozenset((DOCUMENT, WINDOW, PANE))

# Text inside a link inside a list item is three steps; more than this per
# sample point is cross-process time spent on what is almost always
# background.
MAX_PARENT_STEPS = 4

# UIA_*PropertyId, fetched with the element in one round trip.
_PROP_PID, _PROP_TYPE = 30002, 30003

DEFAULT_RADIUS = 120.0
DEFAULT_MAX_TARGET = 0.4


def rect_distance(x, y, rect):
    """0 inside the rectangle (edges included), else distance to its edge."""
    left, top, right, bottom = rect
    dx = max(left - x, 0, x - right)
    dy = max(top - y, 0, y - bottom)
    return math.hypot(dx, dy)


def _area(rect):
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


def sample_points(x, y, radius):
    """The gaze point, a ring at half the radius and a ring at the radius."""
    pts = [(int(round(x)), int(round(y)))]
    for frac in (0.5, 1.0):
        for k in range(8):
            a = k * math.pi / 4
            pts.append((int(round(x + frac * radius * math.cos(a))),
                        int(round(y + frac * radius * math.sin(a)))))
    return pts


def pick_target(x, y, rects, radius, current=None):
    """The rectangle the cursor should go to, or None for plain gaze."""
    best, best_key = None, None
    for r in rects:
        d = rect_distance(x, y, r)
        if d > radius:
            continue
        key = (d, _area(r))
        if best_key is None or key < best_key:
            best, best_key = r, key
    if current is not None and current in rects:
        d_cur = rect_distance(x, y, current)
        if d_cur <= radius:
            # Keep it through jitter; only looking INTO another element
            # takes the cursor away.
            if best is not None and best_key[0] == 0 and d_cur > 0:
                return best
            return current
    return best


def _axis(v, lo, hi, radius):
    if hi - lo <= 3 * radius:
        return int(round((lo + hi) / 2.0))
    inset = radius / 2.0
    return int(round(min(max(v, lo + inset), hi - inset)))


def snap_point(x, y, rect, radius):
    left, top, right, bottom = rect
    return _axis(x, left, right, radius), _axis(y, top, bottom, radius)


def collect_targets(points, element_at, info, bounds, parent_of, own_pid,
                    max_w, max_h):
    """Rectangles of the clickable elements found at `points`.

    element_at(x, y) -> element; info(element) -> (control_type, pid);
    bounds(element) -> (left, top, right, bottom) in LOGICAL pixels, asked
    only of the clickable element that is kept; parent_of(element) ->
    element or a falsy value at the root. Any of them may raise: that point
    is skipped, the others still count.
    """
    found = []
    for x, y in points:
        try:
            el = element_at(x, y)
            steps = 0
            while el:
                ctype, pid = info(el)
                if pid == own_pid or ctype in STOP:
                    break
                if ctype in CLICKABLE:
                    rect = tuple(int(v) for v in bounds(el))
                    w, h = rect[2] - rect[0], rect[3] - rect[1]
                    if 0 < w <= max_w and 0 < h <= max_h and rect not in found:
                        found.append(rect)
                    break
                if steps >= MAX_PARENT_STEPS:
                    break
                el = parent_of(el)
                steps += 1
        except Exception:
            continue
    return found


class TargetFinder:
    """Runs the element queries on a worker thread.

    make_query() is called ON the worker (COM objects belong to the thread
    that made them) and returns query(x, y) -> [rect, ...]. The gaze loop
    calls request() every frame and reads snapshot() = (time, point, rects).
    """

    def __init__(self, make_query, min_interval=0.08, refresh=0.3,
                 move_px=15.0, max_errors=5, start_attempts=3,
                 start_retry_s=0.5, log=print):
        self._make_query = make_query
        self._start_attempts = max(1, int(start_attempts))
        self._start_retry_s = float(start_retry_s)
        self._min_interval = float(min_interval)
        self._refresh = float(refresh)
        self._move_px = float(move_px)
        self._max_errors = int(max_errors)
        self._log = log
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._want = None
        self._stop = False
        self._snap = None
        self.failed = False
        self._thread = threading.Thread(target=self._run, name="hedef-yapisma",
                                        daemon=True)
        self._thread.start()

    def request(self, x, y):
        with self._lock:
            self._want = (int(round(x)), int(round(y)))
        self._event.set()

    def snapshot(self):
        return self._snap

    def close(self):
        self._stop = True
        self._event.set()

    def _say(self, msg):
        try:
            self._log(msg)
        except Exception:
            pass

    def _start(self):
        """make_query(), retried: a start can fail transiently (E_FAIL when
        another UI Automation client starts at the same moment)."""
        for attempt in range(self._start_attempts):
            try:
                return self._make_query()
            except Exception as exc:
                if attempt + 1 >= self._start_attempts or self._stop:
                    self.failed = True
                    self._say("Hedefe yapisma baslatilamadi (%s: %s); duz"
                              " bakis imleci." % (type(exc).__name__, exc))
                    return None
                time.sleep(self._start_retry_s)
        return None

    def _run(self):
        query = self._start()
        if query is None:
            return
        errors = 0
        last_pt, last_t = None, -1e9
        while not self._stop:
            self._event.wait(self._refresh)
            self._event.clear()
            if self._stop:
                break
            with self._lock:
                pt = self._want
            if pt is None:
                continue
            now = time.monotonic()
            if (last_pt is not None and now - last_t < self._refresh
                    and math.hypot(pt[0] - last_pt[0], pt[1] - last_pt[1])
                    < self._move_px):
                continue
            wait = self._min_interval - (now - last_t)
            if wait > 0:
                time.sleep(wait)
            try:
                rects = list(query(*pt))
            except Exception as exc:
                errors += 1
                if errors >= self._max_errors:
                    self.failed = True
                    self._say("Hedefe yapisma KAPATILDI: Windows ogeleri"
                              " okunamiyor (%s: %s). Imlec duz bakisla devam"
                              " ediyor." % (type(exc).__name__, exc))
                    return
                continue
            errors = 0
            last_pt, last_t = pt, time.monotonic()
            self._snap = (last_t, pt, rects)


class TargetSnap:
    """adjust(x, y, now) -> where the cursor goes. Never raises."""

    def __init__(self, finder, radius=DEFAULT_RADIUS, max_age=1.0,
                 max_w=0, max_h=0):
        self.finder = finder
        self.radius = float(radius)
        self.max_age = float(max_age)
        self.max_w, self.max_h = int(max_w), int(max_h)
        self.current = None

    def adjust(self, x, y, now, active=True):
        try:
            return self._adjust(x, y, now, active)
        except Exception:
            self.current = None
            return x, y

    def _adjust(self, x, y, now, active):
        if not active or self.finder.failed:
            self.current = None
            return x, y
        self.finder.request(x, y)
        snap = self.finder.snapshot()
        if snap is None:
            self.current = None
            return x, y
        t, (qx, qy), rects = snap
        if (now - t > self.max_age
                or math.hypot(x - qx, y - qy) > self.radius / 2.0):
            # The eye moved on and the worker has not caught up (or an app
            # hung it): targets around the OLD point would drag the cursor
            # back.
            self.current = None
            return x, y
        tgt = pick_target(x, y, rects, self.radius, current=self.current)
        self.current = tgt
        if tgt is None:
            return x, y
        return snap_point(x, y, tgt, self.radius)

    def reset(self):
        self.current = None

    def close(self):
        try:
            self.finder.close()
        except Exception:
            pass


# Two threads starting a UI Automation client at the same moment fail ~1 in
# 10 with E_FAIL at CreateCacheRequest (measured: 1/10 concurrent, 0/6
# sequential). Starts take this lock; the clients then run in parallel.
_UIA_START_LOCK = threading.Lock()


def uia_query_factory(radius, max_w, max_h, own_pid):
    """make_query for TargetFinder backed by Windows UI Automation."""
    def make():
        from ctypes import wintypes

        import comtypes
        import comtypes.client
        with _UIA_START_LOCK:
            comtypes.CoInitialize()
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as U
            uia = comtypes.client.CreateObject(U.CUIAutomation,
                                               interface=U.IUIAutomation)
            # Type and process id come with the element in one round trip.
            # The rectangle does NOT: the cached BoundingRectangle is in
            # PHYSICAL pixels in a DPI-unaware process (measured x1.5 at
            # 150 %), while the live CurrentBoundingRectangle is logical like
            # the cursor.
            cache = uia.CreateCacheRequest()
            for prop in (_PROP_PID, _PROP_TYPE):
                cache.AddProperty(prop)
            walker = uia.ControlViewWalker

        def at(x, y):
            return uia.ElementFromPointBuildCache(wintypes.POINT(x, y), cache)

        def info(e):
            return e.CachedControlType, e.CachedProcessId

        def bounds(e):
            b = e.CurrentBoundingRectangle
            return b.left, b.top, b.right, b.bottom

        def parent(e):
            return walker.GetParentElementBuildCache(e, cache)

        def query(x, y):
            return collect_targets(sample_points(x, y, radius), at, info,
                                   bounds, parent, own_pid, max_w, max_h)
        return query
    return make


_TRUE = frozenset("true yes on 1 evet acik açık".split())


def _on(value):
    if isinstance(value, str):
        return value.strip().lower() in _TRUE
    return bool(value)


def make_target_snap(cfg, sw, sh, make_query=None, log=print):
    """TargetSnap from config, or None when off or not on Windows."""
    if not _on(cfg.get("snap_to_targets", False)):
        return None
    if make_query is None and os.name != "nt":
        return None
    try:
        radius = float(cfg.get("snap_radius", DEFAULT_RADIUS))
        frac = float(cfg.get("snap_max_target", DEFAULT_MAX_TARGET))
    except (TypeError, ValueError):
        radius, frac = DEFAULT_RADIUS, DEFAULT_MAX_TARGET
    if not radius > 0:
        radius = DEFAULT_RADIUS
    if not 0 < frac <= 1:
        frac = DEFAULT_MAX_TARGET
    max_w, max_h = int(sw * frac), int(sh * frac)
    if make_query is None:
        make_query = uia_query_factory(radius, max_w, max_h, os.getpid())
    finder = TargetFinder(make_query, log=log)
    log("  Hedefe yapisma: acik (%d px)" % radius)
    return TargetSnap(finder, radius=radius, max_w=max_w, max_h=max_h)
