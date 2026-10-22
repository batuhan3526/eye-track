# gaze/eye_gaze/click_menu.py
"""Single-switch click-type menu: right click, double click, drag and scroll
for someone who has exactly one finger switch.

The click key (Space / gamepad A) only ever does a left click. Everything else
a mouse can do needed a second, third and fourth switch, or OptiKey. Here the
user presses the menu key (F2) - or, optionally, HOLDS the click switch - and a
menu opens beside the point they were looking at:

    Sol tik | Sag tik | Cift tik | Surukle | Kaydir yukari | Kaydir asagi | Iptal

They look at an option and press the click switch. The action is applied at
the point where the menu was OPENED (the remembered target), never on the menu
tile itself - the tile is only how the choice is made.

Pure and injectable: the window, the screenshot, the cursor and the wheel are
all handed in, so every decision here is tested without a screen. The drag
state is NOT kept here: drags go through the driver's _Clicker (inject + fire),
the one object whose release() runs on pause, exit and console close. A second
copy of "is the button down" is how a button ends up held with nobody able to
let go of it.
"""
import math

from eye_gaze.i18n import t_ascii

ITEMS = ("left", "right", "double", "drag", "scroll_up", "scroll_down",
         "cancel")
# With an undo callback the menu also offers "Kalibrasyonu geri al": the only
# place a single-switch user can reach it. It gets a full-width row of its
# own (as Iptal does) so it is never the neighbour of a click tile.
ITEMS_UNDO = ITEMS[:-1] + ("undo", "cancel")
_WIDE = ("undo", "cancel")

# Tile texts are in the language catalog: click_menu.<kind>.

# BGR tile colours: distinct enough to be told apart in peripheral vision,
# which is where the menu is while the eyes are still on the target.
_COLOURS = {"left": (60, 120, 40), "right": (140, 70, 30),
            "double": (120, 110, 20), "drag": (30, 100, 170),
            "scroll_up": (90, 90, 90), "scroll_down": (90, 90, 90),
            "undo": (110, 60, 110), "cancel": (40, 40, 150)}

# The Turkish window title; the window itself is named window_title().
_WIN = "Tik menusu - secenege bakip tiklayin"


def window_title():
    return t_ascii("click_menu.window")

# After a choice closes the menu, the switch is ignored this long: a bouncing
# switch (or a shaky finger) counts a second press that surfaces a frame
# later, over whatever the choice just opened - a context menu at the target.
# Short against this user's deliberate press rate; scan.PRESS_GUARD is 0.3.
SELECT_MUTE = 0.4


def item_label(kind, dragging=False):
    """What a tile says. ASCII on purpose: drawn with OpenCV's Hershey font."""
    if kind == "drag":
        return t_ascii("click_menu.drag_stop" if dragging
                       else "click_menu.drag_start")
    if kind not in ITEMS_UNDO:
        raise KeyError(kind)
    return t_ascii("click_menu." + kind)


def layout(tx, ty, sw, sh, pad=12, gap=70, margin=8, items=ITEMS):
    """(panel, rects) for a menu opened at target (tx, ty) on a sw x sh screen.

    Two columns, four rows (Iptal spans the last row), placed BESIDE the
    target on whichever side has more room, so the target itself - where the
    action will land - stays visible. Tiles are as large as that side allows,
    capped at 22% of the screen: the measured gaze error is 100-134 px and a
    small target is a guess.
    """
    tx = min(max(int(tx), 0), sw - 1)
    ty = min(max(int(ty), 0), sh - 1)
    n_small = sum(1 for k in items if k not in _WIDE)
    rows = (n_small + 1) // 2 + sum(1 for k in items if k in _WIDE)
    room_r = sw - margin - (tx + gap)
    room_l = (tx - gap) - margin
    right = room_r >= room_l
    room = max(room_r, room_l)
    cw = max(60, min(int(sw * 0.22), (room - 3 * pad) // 2))
    ch = max(40, min(int(sh * 0.22),
                     (sh - 2 * margin - (rows + 1) * pad) // rows))
    pw, ph = 2 * cw + 3 * pad, rows * ch + (rows + 1) * pad
    x0 = tx + gap if right else tx - gap - pw
    # Only a screen too small for any placement gets clamped (and covered).
    x0 = min(max(x0, margin), max(margin, sw - margin - pw))
    y0 = min(max(ty - ph // 2, margin), max(margin, sh - margin - ph))
    rects = []
    r = c = 0
    for kind in items:
        if kind in _WIDE:
            if c:
                r, c = r + 1, 0
            rects.append((x0 + pad, y0 + pad + r * (ch + pad), 2 * cw + pad,
                          ch))
            r += 1
            continue
        rects.append((x0 + pad + c * (cw + pad), y0 + pad + r * (ch + pad),
                      cw, ch))
        c += 1
        if c == 2:
            r, c = r + 1, 0
    return (x0, y0, pw, ph), rects


def render(background, sw, sh, panel, rects, labels, target, items=ITEMS):
    """The menu as a full-screen BGR image: the frozen, dimmed screen, a
    marker on the target, and the tiles. Rendered once per opening; the
    per-frame pointer goes on top of a copy (aac.overlay_pointer)."""
    import cv2
    import numpy as np

    if (background is not None and getattr(background, "shape", None)
            is not None and background.shape[:2] == (sh, sw)):
        img = (background.astype(np.float32) * 0.45).astype(np.uint8)
        if img.ndim == 2 or img.shape[2] != 3:
            img = np.full((sh, sw, 3), 30, dtype=np.uint8)
    else:
        img = np.full((sh, sw, 3), 30, dtype=np.uint8)
    px, py, pw, ph = panel
    cv2.rectangle(img, (px, py), (px + pw - 1, py + ph - 1), (20, 20, 20), -1)
    for kind, label, (x, y, w, h) in zip(items, labels, rects):
        cv2.rectangle(img, (x, y), (x + w - 1, y + h - 1),
                      _COLOURS.get(kind, (80, 80, 80)), -1)
        scale = max(0.7, min(1.5, w / 190.0))
        thick = 3 if scale >= 1.1 else 2
        words = label.split()
        lines = [label]
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale,
                                      thick)
        if lw > w - 16 and len(words) > 1:
            lines = [" ".join(words[:-1]), words[-1]]
        step = int(lh * 1.8)
        cy = y + h // 2 - (len(lines) - 1) * step // 2 + lh // 2
        for line in lines:
            (lw, _), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX,
                                         scale, thick)
            cv2.putText(img, line, (x + max(4, (w - lw) // 2), cy),
                        cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255),
                        thick)
            cy += step
    # The target: where the chosen action will land.
    tx, ty = int(target[0]), int(target[1])
    cv2.circle(img, (tx, ty), 22, (0, 0, 0), 6)
    cv2.circle(img, (tx, ty), 22, (0, 0, 255), 3)
    cv2.line(img, (tx - 34, ty), (tx + 34, ty), (0, 0, 255), 2)
    cv2.line(img, (tx, ty - 34), (tx, ty + 34), (0, 0, 255), 2)
    return img


_COUNT_W = 250      # width of "Kapaniyor: N" at scale 1.1


def countdown_origin(target, panel, sw, sh):
    """Where the closing countdown goes: just below (or above) the target, on
    the side away from the panel, always on screen. The panel can be nearly
    full height, so 'above the panel' is not a place that always exists."""
    tx = min(max(int(target[0]), 0), sw - 1)
    ty = min(max(int(target[1]), 0), sh - 1)
    px, _, pw, _ = panel
    if px > tx:                         # panel on the right
        x = min(tx - _COUNT_W // 2, px - _COUNT_W - 4)
    else:
        x = max(tx - _COUNT_W // 2, px + pw + 4)
    x = min(max(x, 4), max(4, sw - _COUNT_W - 4))
    y = ty + 70 if ty + 70 < sh - 10 else ty - 50
    return x, min(max(y, 30), sh - 10)


class HoldGate:
    """Tells a short press of the click switch from a long hold.

    update() runs once per frame with: whether a new press arrived (pressed),
    whether the switch is down right now (held), and the cursor position. It
    returns a list of events:

      ("click", pos) - a short press, to be clicked where it was PRESSED
                       (the cursor drifts a little while the finger lifts)
      ("menu",  pos) - held for `hold` seconds: open the menu at that point

    A hold fires the menu exactly once and its release does nothing. A gap in
    updates longer than GAP means something else owned the input meanwhile (an
    overlay, a calibration), and a half-finished press is dropped rather than
    turned into a click on whatever is in front now.
    """

    GAP = 0.5

    def __init__(self, hold):
        self.hold = float(hold)
        self._last = None
        self.reset()

    def reset(self):
        self._state = "idle"
        self._t0 = None
        self._pos = None

    def update(self, now, pressed, held, pos):
        out = []
        if self._last is not None and now - self._last > self.GAP:
            self.reset()
        self._last = now
        if self._state == "pending":
            if not held or pressed:
                out.append(("click", self._pos))
                self._state = "idle"
            elif now - self._t0 >= self.hold:
                out.append(("menu", self._pos))
                self._state = "spent"
        elif self._state == "spent":
            if not held or pressed:
                self._state = "idle"
        if pressed and self._state == "idle":
            if held:
                self._state, self._t0, self._pos = "pending", now, pos
            else:
                out.append(("click", pos))
        return out


class CvWindow:
    """Full-screen OpenCV window, forced topmost - the same handling as the
    AAC grid and the magnifier, which is what survives OptiKey."""

    def __init__(self, title=None, topmost=None, place=None):
        if title is None:
            title = window_title()
        self.title = title
        self._topmost = topmost
        # place(title): moves the new window onto the primary monitor before
        # it goes fullscreen (gaze_driver._place_on_primary; Linux only).
        self._place = place
        self._made = False

    def show(self, img):
        import cv2
        if not self._made:
            cv2.namedWindow(self.title, cv2.WINDOW_NORMAL)
            if self._place is not None:
                self._place(self.title)
            cv2.setWindowProperty(self.title, cv2.WND_PROP_FULLSCREEN,
                                  cv2.WINDOW_FULLSCREEN)
            self._made = True
        cv2.imshow(self.title, img)
        if self._topmost is not None:
            self._topmost(self.title)

    def close(self):
        if self._made:
            self._made = False
            import cv2
            cv2.destroyWindow(self.title)


def grab_screen(sw, sh, origin=(0, 0)):
    """The primary screen (top-left at `origin`) as a sw x sh BGR image, or
    None. zoom.grab_region reads PHYSICAL pixels (this process is DPI-unaware
    on purpose), so the shot is scaled back to the logical size the menu is
    laid out in."""
    from eye_gaze.zoom import grab_region
    img = grab_region(int(origin[0]), int(origin[1]), sw, sh)
    if img is None:
        return None
    if img.shape[:2] != (sh, sw):
        import cv2
        img = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA)
    return img


class ClickMenu:
    """The menu as a layer of the driver's input routing (see route())."""

    def __init__(self, sw, sh, clicker, key="f2", timeout=10.0, hold=0.0,
                 scroll_notches=5, ui=None, grab=None, get_cursor=None,
                 set_cursor=None, scroll=None, log=print, on_undo=None,
                 origin=(0, 0)):
        self._sw, self._sh = int(sw), int(sh)
        # Top-left of the primary monitor the menu fills. The menu is laid
        # out in its pixels; the cursor and the target are screen points.
        self._ox, self._oy = int(origin[0]), int(origin[1])
        self._on_undo = on_undo
        self.items = ITEMS_UNDO if on_undo is not None else ITEMS
        self._clicker = clicker
        self.key = str(key or "").strip().lower()
        self.timeout = float(timeout)
        self.hold = float(hold)
        self.scroll_notches = int(scroll_notches)
        self._ui = ui if ui is not None else CvWindow()
        self._grab = grab if grab is not None else (
            lambda: grab_screen(self._sw, self._sh, (self._ox, self._oy)))
        if get_cursor is None or set_cursor is None or scroll is None:
            from eye_gaze import actions, cursor
            get_cursor = get_cursor or cursor.get_cursor
            # virtual=True: the target came from GetCursorPos, so it is a real
            # point; clamping it to the PRIMARY monitor would move a target on
            # a second screen somewhere else entirely.
            set_cursor = set_cursor or (
                lambda x, y: cursor.set_cursor(x, y, virtual=True))
            scroll = scroll or actions.scroll
        self._get_cursor = get_cursor
        self._set_cursor = set_cursor
        self._scroll = scroll
        self._log = log
        self._gate = HoldGate(self.hold) if self.hold > 0 else None
        self._open = False
        self._t0 = 0.0
        self._hover = None
        self._base = None
        self.target = None
        self.panel = None
        self.rects = []
        self.labels = []

    @property
    def active(self):
        return self._open

    def _say(self, msg):
        try:
            self._log(str(msg).encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass

    def seconds_left(self, now):
        return self.timeout - (now - self._t0)

    # --- opening / drawing --------------------------------------------------

    def _local(self, point):
        """A screen point in menu pixels."""
        return (int(point[0]) - self._ox, int(point[1]) - self._oy)

    def begin(self, target, now):
        self.target = (int(target[0]), int(target[1]))
        tx, ty = self._local(self.target)
        self.panel, self.rects = layout(tx, ty, self._sw, self._sh,
                                        items=self.items)
        dragging = bool(getattr(self._clicker, "dragging", False))
        self.labels = [item_label(k, dragging) for k in self.items]
        try:
            shot = self._grab()
        except Exception:
            shot = None
        mark = (min(max(tx, 0), self._sw - 1), min(max(ty, 0), self._sh - 1))
        self._base = render(shot, self._sw, self._sh, self.panel, self.rects,
                            self.labels, mark, self.items)
        self._t0 = now
        self._hover = None
        self._open = True
        self._paint(now)

    def _paint(self, now):
        from eye_gaze.aac import cell_at, overlay_pointer
        px, py = self._local(self._get_cursor())
        idx = cell_at(px, py, self.rects)
        if idx is not None and idx != self._hover:
            # Reading from tile to tile is activity; only a gaze parked on one
            # tile (or off the menu) runs the clock down.
            if self._hover is not None:
                self._t0 = now
            self._hover = idx
        img = overlay_pointer(self._base, self.rects, idx, px, py)
        left = self.seconds_left(now)
        if 0 < left <= 5:
            # The close used to come with no warning on the other overlays.
            import cv2
            x, y = countdown_origin(self._local(self.target), self.panel,
                                    self._sw, self._sh)
            cv2.putText(img, t_ascii("common.closing",
                                     n="%d" % math.ceil(left)), (x, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 255), 3)
        self._ui.show(img)

    def tick(self, now):
        """True only on the frame the menu closed itself (timeout)."""
        if not self._open:
            return False
        if now - self._t0 > self.timeout:
            self.cancel()
            self._say("Tik menusu zaman asimiyla kapandi.")
            return True
        self._paint(now)
        return False

    def cancel(self):
        if self._open:
            self._open = False
            try:
                self._ui.close()
            except Exception:
                pass

    # --- choosing -----------------------------------------------------------

    def confirm(self):
        """Apply whatever tile the gaze cursor is on, at the target."""
        if not self._open:
            return
        from eye_gaze.aac import cell_at
        px, py = self._local(self._get_cursor())
        idx = cell_at(px, py, self.rects)
        if idx is None:
            return                  # a glance between tiles chooses nothing
        kind = self.items[idx]
        tx, ty = self.target
        # Close FIRST: the action must reach the app under the target, not
        # this full-screen window.
        self.cancel()
        if kind == "cancel":
            self._say("Tik menusu: iptal.")
            return
        if kind == "undo":
            # Nothing reaches the app; the driver swaps the calibration.
            self._say("Tik menusu: kalibrasyonu geri al.")
            self._on_undo()
            return
        self._set_cursor(tx, ty)
        if kind in ("scroll_up", "scroll_down"):
            n = self.scroll_notches * 120
            self._scroll(n if kind == "scroll_up" else -n)
        else:
            # Only this one action: anything else queued this frame was
            # pressed at the menu and must not land on the app as well.
            self._clicker.discard()
            self._clicker.inject(kind)
            self._clicker.fire()
        self._say("Tik menusu: %s (%d, %d)"
                  % (item_label(kind, kind == "drag" and
                                not bool(getattr(self._clicker, "dragging",
                                                 False))), tx, ty))

    # --- one frame of input -------------------------------------------------

    def route(self, now, keys, other_keys=()):
        """One frame of switch routing for the menu. True when the menu owns
        the input (the caller must then not act on any other key).

        Call it where no other overlay is open. While the menu is open the
        click switch chooses, the menu key closes it, and every other layer
        key and button is dropped - not saved up to fire when it closes.
        Never raises: a failing window is closed, never left covering the
        screen while the cursor is the user's only way out.
        """
        was_open = self._open
        try:
            if self._open:
                if self.tick(now):
                    self._clicker.mute(now)
                elif self._clicker.take("left"):
                    self.confirm()
                    if not self._open:
                        # Chosen (or cancelled by its tile): a bounce of
                        # this press must not land on the app behind.
                        self._clicker.mute(now, SELECT_MUTE)
                elif self.key and keys.take(self.key):
                    self.cancel()
                    self._say("Tik menusu kapatildi.")
                for k in other_keys:
                    if k:
                        keys.take(k)
                self._clicker.discard()
                return True
            target = None
            if self._gate is not None:
                try:
                    events = self._gate.update(
                        now, self._clicker.take("left"),
                        bool(self._clicker.left_held()), self._get_cursor())
                except Exception as exc:
                    # Without this, a gate that throws every frame would eat
                    # every left press for the rest of the session.
                    self._gate = None
                    self._say("Tik menusu: basili tutma KAPATILDI (%s);"
                              " tiklama normal calisiyor." % type(exc).__name__)
                    events = []
                for ev, pos in events:
                    if ev == "click":
                        self._set_cursor(*pos)
                        self._clicker.inject("left")
                    elif ev == "menu":
                        target = pos
            if self.key and keys.take(self.key) and target is None:
                target = self._get_cursor()
            if target is None:
                return False
            self._clicker.fire()        # anything pressed before the menu
            self.begin(target, now)
            return True
        except Exception as exc:
            self.cancel()
            self._say("Tik menusu hatasi (%s: %s); menu kapatildi."
                      % (type(exc).__name__, exc))
            if was_open:
                # The press that hit the failure was aimed at the menu.
                self._clicker.discard()
                self._clicker.mute(now)
                return True
            return False


def _number(cfg, key, default, kind):
    try:
        v = kind(cfg.get(key, default))
        return v if v == v else default         # NaN
    except (TypeError, ValueError, OverflowError):
        return default


def from_config(cfg, sw, sh, clicker, topmost=None, log=print, on_undo=None,
                origin=(0, 0), place=None):
    """Build the menu from config.yaml, or None when it is switched off.

    Never raises: it runs at startup, outside the per-frame guard, and a bad
    setting for this menu must not keep the cursor from starting.
    """
    try:
        return _from_config(cfg, sw, sh, clicker, topmost, log, on_undo,
                            origin, place)
    except Exception as exc:
        try:
            log("Tik menusu KAPALI - kurulamadi (%s)." % type(exc).__name__)
        except Exception:
            pass
        return None


def _from_config(cfg, sw, sh, clicker, topmost, log, on_undo=None,
                 origin=(0, 0), place=None):
    key = str(cfg.get("click_menu_key", "f2") or "").strip().lower()
    hold = _number(cfg, "click_menu_on_hold", 0.0, float)
    if hold > 0:
        # Shorter than this and an ordinary press opens the menu.
        hold = max(0.3, hold)
    else:
        hold = 0.0
    if not key and hold <= 0:
        return None
    timeout = _number(cfg, "click_menu_timeout", 10.0, float)
    notches = _number(cfg, "click_menu_scroll", 5, int)
    menu = ClickMenu(sw, sh, clicker, key=key,
                     timeout=timeout if timeout > 0 else 10.0,
                     hold=hold,
                     scroll_notches=notches if notches > 0 else 5,
                     ui=CvWindow(topmost=topmost, place=place), log=log,
                     on_undo=on_undo, origin=origin)
    how = []
    if key:
        how.append("'%s'" % key)
    if hold > 0:
        how.append("tiklama tusunu %.1f sn basili tut" % hold)
    menu._say("  TIK MENUSU (sag/cift tik, surukle, kaydir): "
              + " ya da ".join(how))
    return menu
