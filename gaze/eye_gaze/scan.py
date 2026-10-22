# gaze/eye_gaze/scan.py
"""Switch scanning for the AAC grid - for when aiming with the eyes stops
being reliable enough.

ALS progresses; the day can come when the gaze cursor wanders too much to land
on a tile, while a finger can still press a switch. Then the grid lights up one
ROW after another; a press picks the lit row, its tiles light up one by one,
and a press picks the lit tile. Gaze is not needed at all.

Every pass ends on a GERI step. In a row's cells it goes back to the rows, so
picking the wrong row is never a trap; on the row level it asks the grid to go
back a board (or close, on the main menu). A board too small for more than one
row skips the row step entirely: one press fewer for every phrase.

Pure state machine: no window, no key, no clock of its own. The driver feeds
it `now` and the presses it already de-duplicates (KeyPresses counts one press
per physical press and ignores the auto-repeat of a HELD switch), and draws
what highlight() says.
"""
from eye_gaze.aac import HEADER, grid_shape
from eye_gaze.i18n import t_ascii

MODES = ("row-col", "linear")
_MODE_ALIASES = {"row-col": "row-col", "rowcol": "row-col", "row": "row-col",
                 "satir-sutun": "row-col", "satir": "row-col",
                 "linear": "linear", "tek-tek": "linear", "sirali": "linear"}

BACK_STEP = -1          # marker in the step list: the GERI step

# A second press this soon after an accepted one is the same press bouncing (a
# shaky finger, a cheap switch), not a decision. Without it one press could
# pick a row AND the first cell of that row.
PRESS_GUARD = 0.3

# The driver reads the press once per frame AFTER tick() advanced the step,
# and the screen still shows the old highlight until the next paint. A press
# made while item S was lit but read in the frame that moved to S+1 selected
# S+1 - a phrase the user never saw lit. A press within about one frame (at
# 15 fps) of the advance belongs to the item that was lit before it.
LATE_GRACE = 0.08

# The Turkish hints; what is drawn comes from the catalog (scan_hint(),
# aac.gaze_hint()) in the current language.
SCAN_HINT = "isik gelince tikla"
_GAZE_HINT = "bak + tikla = konus"


def scan_hint():
    return t_ascii("scan.hint")

_LIT = (0, 255, 255)            # same yellow as the gaze hover (aac._HOVER)
_LIT_LOCKED = (0, 0, 255)       # red: pressing here says nothing
_DIM = (110, 110, 110)


class Scanner:
    """Where the scan highlight is, and what a press there means.

    press() returns ("tile", index) when a tile was picked, ("back", None) when
    the row-level GERI step was picked, and None otherwise (a row was opened, a
    cell pass was abandoned, the press was a bounce, or nothing was running).
    tick() returns True on the step that used up the idle cycles: the caller
    closes the grid exactly as its own timeout would.
    """

    def __init__(self, seconds=1.5, cycles=3, mode="row-col"):
        self.seconds = max(0.5, min(float(seconds), 10.0))
        self.cycles = max(1, int(cycles))
        self.mode = _MODE_ALIASES.get(str(mode or "").strip().lower(), "row-col")
        self._count = 0
        self._cols = 1
        self._rows = 0
        self._started = False
        self._level = "row"
        self._row = 0
        self._steps = []
        self._step = 0
        self._t = 0.0
        self._idle = 0
        self._last_press = None
        self._before = None     # (time, state) just before the last advance

    # --- setup ---------------------------------------------------------------

    def start(self, count, now):
        """Begin scanning a (new) board of `count` tiles from the top."""
        self._count = max(0, int(count))
        if self._count:
            self._rows, self._cols = grid_shape(self._count)
            # grid_shape caps at 9 tiles; the rows that actually hold tiles:
            self._rows = (min(self._count, self._rows * self._cols)
                          + self._cols - 1) // self._cols
        else:
            self._rows, self._cols = 0, 1
        self._started = True
        self._idle = 0
        self._top(now)

    def _flat(self):
        return self.mode == "linear" or self._rows <= 1

    def _top(self, now):
        if self._flat():
            self._level = "cell"
            self._row = None
            self._steps = list(range(min(self._count, 9))) + [BACK_STEP]
        else:
            self._level = "row"
            self._row = None
            self._steps = list(range(self._rows)) + [BACK_STEP]
        self._step = 0
        self._t = now

    def _open_row(self, row, now):
        first = row * self._cols
        last = min(self._count, first + self._cols)
        self._level = "cell"
        self._row = row
        self._steps = list(range(first, last)) + [BACK_STEP]
        self._step = 0
        self._t = now

    # --- state ---------------------------------------------------------------

    @property
    def level(self):
        return self._level

    @property
    def on_back(self):
        return self._started and self._steps[self._step] == BACK_STEP

    @property
    def back_restarts(self):
        """True when the lit GERI step only returns to the rows (inside a row's
        cells), False when it leaves the board."""
        return self._row is not None

    def highlight(self):
        """Tile indices lit right now ([] on the GERI step or when idle)."""
        if not self._started:
            return []
        cur = self._steps[self._step]
        if cur == BACK_STEP:
            return []
        if self._level == "row":
            first = cur * self._cols
            return list(range(first, min(self._count, first + self._cols)))
        return [cur]

    def seconds_left(self, now):
        """Seconds until an unanswered scan closes the grid, but only during
        the last cycle - the grid shows a countdown then, as the gaze grid's
        timeout does. None otherwise."""
        if not self._started or self._idle < self.cycles - 1:
            return None
        if self._level == "cell" and not self._flat():
            return None     # a cell pass ending falls back to the rows first
        steps = len(self._steps) - self._step
        return max(0.0, steps * self.seconds - (now - self._t))

    # --- time and presses ----------------------------------------------------

    def tick(self, now):
        if not self._started:
            return False
        if now - self._t < self.seconds - 1e-6:     # float slack
            return False
        # One step per tick, timed from NOW: after a stall (a model loading, a
        # slow frame) catching up would jump straight past the item the user
        # was waiting for.
        self._before = (now, (self._level, self._row, self._steps,
                              self._step, self._idle))
        self._t = now
        self._step += 1
        if self._step < len(self._steps):
            return False
        self._idle += 1
        if self._idle >= self.cycles:
            self._started = False
            return True
        self._top(now)
        return False

    def press(self, now):
        if not self._started:
            return None
        if self._last_press is not None and now - self._last_press < PRESS_GUARD:
            return None
        self._last_press = now
        if (self._before is not None
                and 0 <= now - self._before[0] < LATE_GRACE):
            (self._level, self._row, self._steps, self._step,
             self._idle) = self._before[1]
        self._before = None
        self._idle = 0
        cur = self._steps[self._step]
        if self._level == "row":
            if cur == BACK_STEP:
                self._top(now)
                return ("back", None)
            self._open_row(cur, now)
            return None
        if cur == BACK_STEP:
            if self._row is not None:
                self._top(now)          # wrong row: back to the rows
                return None
            self._top(now)
            return ("back", None)
        self._top(now)
        return ("tile", cur)


def scan_from_cfg(cfg):
    """A Scanner when aac_scan is on, else None (the gaze grid is unchanged)."""
    if not bool(cfg.get("aac_scan", False)):
        return None
    raw = str(cfg.get("aac_scan_mode", "row-col") or "row-col").strip().lower()
    if raw not in _MODE_ALIASES:
        print("AAC tarama: aac_scan_mode '%s' taninmadi; \"row-col\""
              " (satir-sutun) kullaniliyor. Gecerli: row-col | linear."
              % ascii(raw)[1:-1])
    try:
        seconds = float(cfg.get("aac_scan_seconds", 1.5))
    except (TypeError, ValueError):
        seconds = 1.5
    try:
        cycles = int(cfg.get("aac_scan_cycles", 3))
    except (TypeError, ValueError):
        cycles = 3
    return Scanner(seconds=seconds, cycles=cycles, mode=raw)


def scan_title(title):
    """The board title with the gaze instruction swapped for the scan one."""
    from eye_gaze.aac import gaze_hint
    gaze, scan = gaze_hint(), scan_hint()
    if gaze in title:
        return title.replace(gaze, scan)
    return title + "   -   " + scan


def _union(rects):
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[0] + r[2] for r in rects)
    y1 = max(r[1] + r[3] for r in rects)
    return x0, y0, x1, y1


def overlay_scan(img, rects, scanner, back_text, locked=()):
    """A copy of `img` with the scan highlight drawn on it.

    A COPY for the same reason as aac.overlay_pointer: the board underneath is
    rendered once and reused every frame. The GERI step has no tile of its
    own - adding one would change the layout the user has learned - so it lives
    in the title strip, always visible, and when lit the whole screen edge is
    framed as well: a small box alone is easy to miss from a bed.
    """
    import cv2
    out = img.copy()
    h, w = out.shape[:2]
    lit = scanner.highlight()
    on_back = scanner.on_back
    from eye_gaze.aac import back_label
    label = (back_label() if scanner.back_restarts
             else str(back_text or back_label()))
    bw = 300
    bx0, by0, bx1, by1 = max(0, w - bw - 16), 4, w - 16, max(5, HEADER - 6)
    if on_back:
        cv2.rectangle(out, (bx0, by0), (bx1, by1), _LIT, -1)
        cv2.putText(out, label, (bx0 + 20, by1 - 12), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (0, 0, 0), 2)
        cv2.rectangle(out, (0, 0), (w - 1, h - 1), _LIT, 14)
    else:
        cv2.rectangle(out, (bx0, by0), (bx1, by1), _DIM, 2)
        cv2.putText(out, label, (bx0 + 20, by1 - 12), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, _DIM, 2)
    boxes = [rects[i] for i in lit if 0 <= i < len(rects)]
    if boxes:
        x0, y0, x1, y1 = _union(boxes)
        colour = (_LIT_LOCKED if len(lit) == 1 and lit[0] in set(locked)
                  else _LIT)
        cv2.rectangle(out, (x0, y0), (x1 - 1, y1 - 1), colour, 10)
    return out
