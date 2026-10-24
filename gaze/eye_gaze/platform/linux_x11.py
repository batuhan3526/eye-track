# gaze/eye_gaze/platform/linux_x11.py
"""X11 backend: XTest for the cursor, clicks, scroll and the keys we send;
XGrabKey for the driver's keys (no root needed); XQueryKeymap for "is it
held?"; Xrandr for the monitors; EWMH for raising a window and for "whose
window has the focus?".

Two kinds of Display connection, because Xlib must not be used from two
threads at once: the callers' (cursor, clicks, screens, windows; another one
for is_pressed), each behind a lock, and one owned by the key thread (grabs,
key events, everything we type - so a key we send cannot race our grab).

How a grabbed key is passed through (the callback returned True)
----------------------------------------------------------------
The keys are grabbed in synchronous keyboard mode: when one is pressed, the
server freezes the keyboard and hands the press to us alone. Then:

  * swallow - XAllowEvents(SyncKeyboard): the press is gone; the next key
    event (normally its release) comes to us the same way;
  * pass    - XAllowEvents(ReplayKeyboard): the server delivers THAT SAME
    press to the focused window as if we had never grabbed it.

A re-sent XTest copy (ungrab, XTestFakeKeyEvent, grab again) does not work
for a real keyboard: the server already holds the key down from the real
press and drops the copy as a duplicate - measured on Xvfb with a second
slave keyboard, the application received only an orphan release. With
xdotool both presses come from the same XTest device, so the copy looks
like auto-repeat and a test passes that a real keyboard would fail.

A replayed press ends our grab, and the key stays ungrabbed until it is
released: the server's auto-repeat presses would activate the grab again,
and a replayed repeat never reaches the application (measured: from the
first repeat on, neither did the final release). So repeats and release go
straight to the application, and the key thread watches XQueryKeymap and
reports the release to the callbacks itself. The same happens to a swallowed
key when another key is typed while it is held (a held grab would otherwise
eat the on-screen keyboard's typing): the other key is replayed, our grab
ends, and the held key's release reaches the application unpaired -
harmless, the application never saw its press.

Injected keys (the on-screen keyboard's click loop)
---------------------------------------------------
onboard, xdotool and AAC tools type through XTest, and their keys hit our
grab exactly like real ones: onboard's Space, clicked with the switch, would
be counted as the next click (the Windows click loop, eye_gaze/injected.py).
The core event does not say where it came from, but the XInput2 raw event
the server sends just before it does: its sourceid is the slave device, and
XTest keys come from the "... XTEST keyboard" slaves. So the key thread
selects raw key events on the root, keeps the last few per keycode, and
stamps a grabbed event injected=True when its raw twin came from an XTEST
slave. Without XInput 2.1 nothing is stamped and capabilities() says so.

Only a fresh press asks the raw events. Its release and its repeats take
the press's flag: with the switch held, onboard's Space is dropped by the
server (the key is already down) but its release goes through and ends the
real hold - stamped injected from its own raw twin, that release was
ignored, the hook kept the key "held", and the next real press counted as a
repeat (review of 2026-10-20).

Safety
------
The keyboard is frozen while the key thread handles one event, so callbacks
must not block (as on Windows, where a slow hook is removed). A watchdog
enforces it: one event taking over the freeze budget - or a callback that
blocks the thread over that long while X events wait unread - gets the key
connection shut down. The server then drops every grab and thaws the
keyboard. A key thread that dies for any reason is said once too.

The budget is half the server's auto-repeat delay (_freeze_budget): the
keyboard must thaw before a held key's first auto-repeat. When the X
server's repeat timer fires while our grab keeps the keyboard frozen, the
server can segfault: its backtrace runs from the timer through the XKB
repeat (XkbHandleActions) into the frozen keyboard's event queue (dix
EnqueueEvent), and it faults at the end of its stack - as if it copied a
whole InternalEvent out of the smaller event the repeat built there. Xorg
has the same input code, so on a desktop the session would likely go down
with it. Measured with Xvfb 21.1.4 (Ubuntu 22.04, default 660 ms delay),
switch held, keyboard frozen and then cut, a new server each time: frozen
1.0 s (the old budget) - 3 of 40 servers died; 2.0 s - 7/40; 0.4 s -
0/40; 2.0 s with the delay raised to 5 s - 0/40; 0.6 s with a 150 ms delay
- 2/40. The test suite's Xvfb died like this too, and Tk's dead connection
then exit()ed pytest without a summary (2026-10-21).

What this covers: a callback that blocks the key thread. What it does not:
the watchdog is a thread of this same process, so a process-wide stall -
one C call holding the GIL (a model being imported or built, a long garbage
collection, a slow laptop) - stops it as well, and a stall longer than the
repeat delay with a grabbed key held can still freeze the keyboard past
it. So the driver grabs only the alarm before loading its models
(gaze_driver.main), and the evdev backend (no synchronous grab) is chosen
before this one whenever the person may read /dev/input. A watchdog that
wakes late was starved itself (_watchdog_verdict): it looks once more
instead of cutting a key thread that was only starved with it, so stalls
on a slow machine do not become cuts that use up the re-arms below and
leave the keys off for good. One stall gets at most _MAX_RECHECKS such
looks: a callback that really blocks while the watchdog keeps being
starved is still cut.

Either way the keys come back by themselves: after a cut by the watchdog
_CUT_COOLDOWN seconds, after any other death _REARM_COOLDOWN seconds, the
thread opens a new connection and grabs every hooked key again (said
once per re-arm). While it is down keyboard.alive() is False and
capabilities() reports the keys as not working; after _REARM_MAX re-arms
within _REARM_WINDOW seconds it gives up for good. A key held at the cut
stays one hold: its release is reported when it happens (read from the
keymap meanwhile), and if it is still held after the re-arm, its next
auto-repeat press is a repeat, not a new click.

Not available here: the gamepad (Task 8).
"""
import collections
import os
import select
import socket
import threading
import time

from eye_gaze.i18n import t as _t
from eye_gaze.platform import keys

NAME = "x11"
_GENERIC_EVENT = 35          # X GenericEvent (XInput2 events arrive as these)

# Seconds the monitor list is reused: set_cursor() asks for the primary
# screen every frame, the answer changes only on a hotplug.
_SCREENS_TTL = 1.0
# The key thread's poll period while a passed-through key is held (its
# release is read from the keymap), and its idle wake-up otherwise.
_RELEASE_POLL = 0.02
_IDLE = 0.5
# How long a release waits for its auto-repeat press (X reports a held key
# as release + press pairs with one timestamp).
_REPEAT_WAIT = 0.005
# Callers give up on the key thread after this long (it never blocks).
_CALL_TIMEOUT = 5.0
# The keyboard frozen longer than the freeze budget: hooks are cut. The
# budget is half the server's auto-repeat delay (see Safety at the top),
# never under _WATCHDOG_MIN. A delay XKB does not report, and any delay
# above it, counts as _SERVER_REPEAT_DELAY: Xorg's default, the one a
# keyboard plugged in later starts with - so the budget is at most 0.33 s.
_WATCHDOG_MIN = 0.05
_SERVER_REPEAT_DELAY = 0.66
# A repeat delay under this is said once: the budget is then very short.
_SHORT_REPEAT_DELAY = 0.2
# While idle the watchdog reads the repeat delay again this often (the
# desktop's keyboard settings may change); only a shorter budget is taken.
_REPEAT_REREAD = 10.0
# After a cut or a death: wait, then connect and grab again - at most
# _REARM_MAX times within _REARM_WINDOW seconds. A watchdog cut comes back
# sooner: the connection itself is fine, a callback was only slow.
_REARM_COOLDOWN = 3.0
_CUT_COOLDOWN = 0.75
_REARM_MAX = 5
_REARM_WINDOW = 60.0
# A starved watchdog looks again instead of cutting - at most this many
# times for one stall, then it cuts (final review M-1).
_MAX_RECHECKS = 3
# Raw key events kept per keycode to find a grabbed event's twin.
_RAW_KEEP = 8

_X = None          # Xlib.X, imported by the first backend
_XK = None
_xdisplay = None
_xerror = None
_xevent = None
_xinput = None
_RAW = None        # wire layout of an XInput2 raw key event
_XkbUseExtension = None    # the two XKB requests the budget needs
_XkbGetControls = None
_XKB_USE_CORE_KBD = 0x100
_XKB_REPEAT_KEYS = 1 << 0  # XkbRepeatKeysMask in enabledControls


def _load_xlib():
    global _X, _XK, _xdisplay, _xerror, _xevent, _xinput, _RAW
    global _XkbUseExtension, _XkbGetControls
    if _X is None:
        from Xlib import X, XK, display, error
        from Xlib.protocol import event, rq
        from Xlib.ext import xinput
        import Xlib.ext.xtest  # noqa: F401  (registers xtest_fake_input)
        # python-xlib parses the XInput2 device events but not the raw ones
        # (xXIRawEvent after evtype; key events carry no valuators).
        _RAW = rq.Struct(
            xinput.DEVICEID("deviceid"), rq.Card32("time"),
            rq.Card32("detail"), xinput.DEVICEID("sourceid"),
            rq.Card16("valuators_len"), rq.Card32("flags"), rq.Pad(4))

        # python-xlib has no XKB: the two requests that read the auto-repeat
        # delay, laid out as in XKBproto.h (xkbUseExtension, xkbGetControls).
        class UseExtension(rq.ReplyRequest):
            _request = rq.Struct(
                rq.Card8("opcode"), rq.Opcode(0), rq.RequestLength(),
                rq.Card16("wanted_major"), rq.Card16("wanted_minor"))
            _reply = rq.Struct(
                rq.ReplyCode(), rq.Bool("supported"),
                rq.Card16("sequence_number"), rq.ReplyLength(),
                rq.Card16("server_major"), rq.Card16("server_minor"),
                rq.Pad(20))

        class GetControls(rq.ReplyRequest):
            _request = rq.Struct(
                rq.Card8("opcode"), rq.Opcode(6), rq.RequestLength(),
                rq.Card16("device_spec"), rq.Pad(2))
            _reply = rq.Struct(
                rq.ReplyCode(), rq.Card8("device_id"),
                rq.Card16("sequence_number"), rq.ReplyLength(),
                rq.Pad(8),        # mouse-keys button ... ignore-lock real mods
                rq.Pad(4),        # internal / ignore-lock virtual mods
                rq.Card16("repeat_delay"), rq.Card16("repeat_interval"),
                rq.Pad(24),       # slow keys ... access-X timeout options
                rq.Pad(8),        # access-X timeout controls mask / values
                rq.Card32("enabled_controls"),
                rq.Pad(32))       # per-key repeat bits

        _XkbUseExtension, _XkbGetControls = UseExtension, GetControls
        _XK, _xdisplay, _xerror, _xevent = XK, display, error, event
        _xinput = xinput
        _X = X


def _log(msg):
    """Once-only notices go to the console (and gaze.log): ASCII only."""
    from eye_gaze.platform import ascii_text
    try:
        print("[x11] " + ascii_text(msg), flush=True)
    except Exception:
        pass


def _xi2_version(d):
    """XInput2 version this connection may use, after asking for 2.2;
    (0, 0) without XInput2 or the Generic Event extension."""
    if not (d.has_extension("XInputExtension")
            and d.has_extension("Generic Event Extension")):
        return (0, 0)
    r = _xinput.XIQueryVersion(
        display=d.display,
        opcode=d.display.get_extension_major(_xinput.extname),
        major_version=2, minor_version=2)
    return (int(r.major_version), int(r.minor_version))


def _repeat_delay(display_name):
    """The core keyboard's auto-repeat delay in seconds (XKB GetControls),
    or None: no XKB, repeat keys off, or no answer. Never raises.

    Asked on a connection of its own: the server may filter the core
    MappingNotify of a client that has called XkbUseExtension by the XKB
    map events it selected, and the key connection needs MappingNotify."""
    try:
        d = _xdisplay.Display(display_name)
    except Exception:
        return None
    try:
        ext = d.query_extension("XKEYBOARD")
        if ext is None or not ext.present:
            return None
        major = ext.major_opcode
        r = _XkbUseExtension(display=d.display, opcode=major,
                             wanted_major=1, wanted_minor=0)
        if not r.supported:
            return None
        c = _XkbGetControls(display=d.display, opcode=major,
                            device_spec=_XKB_USE_CORE_KBD)
        if not c.enabled_controls & _XKB_REPEAT_KEYS:
            return None
        return int(c.repeat_delay) / 1000.0
    except Exception:
        return None
    finally:
        try:
            d.close()
        except Exception:
            pass


def _freeze_budget(repeat_delay):
    """Seconds the keyboard may stay frozen before the watchdog cuts: half
    the auto-repeat delay `repeat_delay` (seconds; None = unknown, and
    nothing above _SERVER_REPEAT_DELAY counts), at least _WATCHDOG_MIN.
    See Safety at the top."""
    delay = _SERVER_REPEAT_DELAY
    if repeat_delay is not None and repeat_delay > 0:
        delay = min(delay, repeat_delay)
    return max(_WATCHDOG_MIN, delay / 2.0)


def _watchdog_period(budget):
    """The watchdog's check period: a quarter of the budget, at most 0.1 s,
    so a cut comes within budget + period < the repeat delay (except under
    a repeat delay shorter than that at the _WATCHDOG_MIN floor)."""
    return min(0.1, budget / 4.0)


def _watchdog_verdict(now, last_wake, budget, busy_since, poll_busy_since,
                      events_waiting, rechecks=0):
    """One watchdog check: "cut", "recheck" or "ok". Pure (timestamps in
    monotonic seconds); events_waiting() is asked only when it matters.

    Frozen: a key event handled longer than `budget` (busy_since), or a
    release-poll callback running that long (poll_busy_since) while events
    wait unread. A frozen verdict from a watchdog that woke later than
    period + budget/2 after its last check is "recheck": the watchdog was
    starved itself (a process-wide stall), and the key thread, starved with
    it, may answer at once now - it looks again one period later. A stall
    that already had `rechecks` >= _MAX_RECHECKS such looks is cut."""
    frozen = busy_since is not None and now - busy_since > budget
    if not frozen and poll_busy_since is not None and \
            now - poll_busy_since > budget:
        frozen = bool(events_waiting())
    if not frozen:
        return "ok"
    if rechecks < _MAX_RECHECKS and last_wake is not None and \
            now - last_wake > _watchdog_period(budget) + budget / 2.0:
        return "recheck"
    return "cut"


class _Rechecks:
    """The rechecks one stall has had so far. A stall is known by its start
    times (busy_since, poll_busy_since): a new stall starts from zero."""

    def __init__(self):
        self._stall = None
        self.given = 0

    def for_stall(self, stall):
        if stall != self._stall:
            self._stall, self.given = stall, 0
        return self.given

    def note(self, verdict):
        if verdict == "recheck":
            self.given += 1


def _positional(name):
    # Letters and digits are named by their place on a US keyboard
    # (keys.py): a hook takes only that physical key, never wherever the
    # layout put the letter (on a Turkish Q keyboard keysym "i" is another
    # key).
    return len(name) == 1


def _keycode_of_evdev(name, lo, hi):
    """X keycode of the physical key: evdev code + 8 under Xorg, XWayland
    and Xvfb alike."""
    code = keys.to_evdev(name) + 8
    return code if lo <= code <= hi else None


def _bit(keymap, code):
    return bool(keymap[code >> 3] & (1 << (code & 7)))


def _char_keysym(ch):
    if ch == "\n":
        return 0xff0d                      # Return
    if ch == "\t":
        return 0xff09                      # Tab
    o = ord(ch)
    if 0x20 <= o <= 0x7e or 0xa0 <= o <= 0xff:
        return o                           # Latin-1 keysyms are the code
    return 0x01000000 | o                  # Unicode keysym


def _text_of(prop):
    if prop is None or not prop.value:
        return None
    v = prop.value
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return str(v)


class X11Backend:
    name = NAME

    @classmethod
    def available(cls):
        """An X11 session we can connect to: session x11 and DISPLAY set."""
        from eye_gaze import platform
        return (platform.session_type() == "x11"
                and bool(os.environ.get("DISPLAY")))

    def __init__(self, display_name=None):
        # Must not call platform.linux_backend()/keyboard_api(): those hold
        # the (non-reentrant) backend lock while this runs.
        _load_xlib()
        self._display_name = display_name or os.environ.get("DISPLAY")
        self._d = _xdisplay.Display(self._display_name)
        try:
            if not self._d.has_extension("XTEST"):
                raise RuntimeError("the X server has no XTEST extension")
            self._root = self._d.screen().root
            self._randr = self._d.has_extension("RANDR")
            if self._randr:
                try:
                    self._d.xrandr_query_version()
                except Exception:
                    self._randr = False
        except Exception:
            self._d.close()
            raise
        # Raw key events tell injected keys apart from XInput 2.1 on.
        try:
            ver = _xi2_version(self._d)
            if ver >= (2, 1):
                self._xi2_reason = None
            elif ver == (0, 0):
                self._xi2_reason = _t("platform.x11.no_xi21")
            else:
                self._xi2_reason = _t("platform.x11.old_xi",
                                     version="%d.%d" % ver)
        except Exception as exc:
            self._xi2_reason = _t("platform.x11.xi_query_failed", error=exc)
        self._lock = threading.Lock()
        self._atoms = {}
        self._screens_at = 0.0
        self._screens = None
        self._primary = None
        self._primary_mm = None
        self._closed = False
        self.keyboard = X11Keyboard(self._display_name)

    # --- screens ------------------------------------------------------------

    def screens(self):
        """[(x, y, w, h)] of the active monitors (Xrandr), else the root
        window."""
        with self._lock:
            self._refresh_screens()
            return list(self._screens)

    def primary(self):
        with self._lock:
            self._refresh_screens()
            return self._primary

    def primary_mm(self):
        """(width_mm, height_mm) of the primary monitor as Xrandr reports
        it, or None (no RandR, or the server does not know)."""
        with self._lock:
            self._refresh_screens()
            return self._primary_mm

    def _refresh_screens(self):
        now = time.monotonic()
        if self._screens is not None and now - self._screens_at < _SCREENS_TTL:
            return
        self._drain()
        found, primary, mm = [], None, {}
        if self._randr:
            try:
                for m in self._root.xrandr_get_monitors(is_active=True).monitors:
                    rect = (int(m.x), int(m.y), int(m.width_in_pixels),
                            int(m.height_in_pixels))
                    if rect[2] > 0 and rect[3] > 0:
                        found.append(rect)
                        try:
                            mm[rect] = (int(m.width_in_millimeters),
                                        int(m.height_in_millimeters))
                        except Exception:
                            pass
                        if m.primary and primary is None:
                            primary = rect
            except Exception:
                found, primary, mm = [], None, {}
        if not found:
            g = self._root.get_geometry()
            found = [(0, 0, int(g.width), int(g.height))]
        self._screens = found
        self._primary = primary or found[0]
        self._primary_mm = mm.get(self._primary)
        self._screens_at = now

    def _drain(self):
        """Read the events this connection collects unasked (MappingNotify
        goes to every client): they would pile up for the process's life."""
        while self._d.pending_events():
            self._d.next_event()

    # --- cursor and buttons ---------------------------------------------------

    def get_cursor(self):
        with self._lock:
            p = self._root.query_pointer()
            return (int(p.root_x), int(p.root_y))

    def set_cursor(self, x, y):
        with self._lock:
            self._root.warp_pointer(int(x), int(y))
            self._d.flush()

    def button(self, btn, down):
        number = {"left": 1, "middle": 2, "right": 3}.get(btn)
        if number is None:
            raise ValueError("unknown mouse button: %r" % (btn,))
        with self._lock:
            self._d.xtest_fake_input(
                _X.ButtonPress if down else _X.ButtonRelease, number)
            self._d.flush()

    def scroll(self, amount):
        """amount in Windows wheel units like actions.scroll(): 120 = one
        notch, > 0 up (X button 4), < 0 down (button 5)."""
        amount = int(amount)
        if not amount:
            return
        notches = max(1, int(round(abs(amount) / 120.0)))
        number = 4 if amount > 0 else 5
        with self._lock:
            for _ in range(notches):
                self._d.xtest_fake_input(_X.ButtonPress, number)
                self._d.xtest_fake_input(_X.ButtonRelease, number)
            self._d.flush()

    # --- windows --------------------------------------------------------------

    def _atom(self, name):
        a = self._atoms.get(name)
        if a is None:
            a = self._atoms[name] = self._d.intern_atom(name)
        return a

    def _title(self, win):
        t = _text_of(win.get_full_property(self._atom("_NET_WM_NAME"),
                                           self._atom("UTF8_STRING")))
        if t is None:
            name = win.get_wm_name()
            if isinstance(name, bytes):
                name = name.decode("latin-1", "replace")
            t = name or None
        return t

    def _top_levels(self):
        """Client windows: the window manager's list (EWMH), or without one
        the root's children."""
        p = self._root.get_full_property(self._atom("_NET_CLIENT_LIST"),
                                         _X.AnyPropertyType)
        if p is not None and len(p.value):
            return [self._d.create_resource_object("window", int(w))
                    for w in p.value]
        return list(self._root.query_tree().children)

    def raise_window(self, title):
        """Put the window titled `title` above the others: the EWMH
        "always on top" state (honoured by the window manager) and
        XRaiseWindow. False if no such window."""
        with self._lock:
            try:
                target = None
                for win in self._top_levels():
                    try:
                        if self._title(win) == title:
                            target = win
                    except Exception:
                        continue            # closed while we looked
                if target is None:
                    return False
                msg = _xevent.ClientMessage(
                    window=target, client_type=self._atom("_NET_WM_STATE"),
                    data=(32, [1,                           # _NET_WM_STATE_ADD
                               self._atom("_NET_WM_STATE_ABOVE"), 0, 1, 0]))
                self._root.send_event(
                    msg, event_mask=(_X.SubstructureRedirectMask
                                     | _X.SubstructureNotifyMask))
                target.configure(stack_mode=_X.Above)
                self._d.sync()
                return True
            except Exception:
                return False

    def foreground_pid(self):
        """pid of the focused window's program (_NET_ACTIVE_WINDOW +
        _NET_WM_PID), or None when the window manager does not say."""
        with self._lock:
            try:
                p = self._root.get_full_property(
                    self._atom("_NET_ACTIVE_WINDOW"), _X.AnyPropertyType)
                if p is None or not len(p.value) or not int(p.value[0]):
                    return None
                win = self._d.create_resource_object("window", int(p.value[0]))
                q = win.get_full_property(self._atom("_NET_WM_PID"),
                                          _X.AnyPropertyType)
                if q is None or not len(q.value):
                    return None
                return int(q.value[0])
            except Exception:
                return None

    # --- the rest -------------------------------------------------------------

    def capabilities(self):
        """Overrides of the generic x11 entries in eye_gaze.platform."""
        kb = self.keyboard
        caps = {}
        why = self._xi2_reason or kb.xi_error
        if why is None:
            caps["injected_detection"] = (True, _t("platform.x11.xi_ok"))
        else:
            caps["injected_detection"] = (
                False, _t("platform.x11.xi_missing", why=why))
        if not kb.alive():
            if kb.gave_up:
                dead = _t("platform.keys_dead", error=kb.error)
            else:
                dead = _t("platform.keys_restarting", error=kb.error)
            caps["global_keys"] = (False, dead)
            caps["suppress_keys"] = (False, dead)
        return caps

    def gamepad(self):
        return None

    def close(self):
        self.keyboard.close()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._d.close()
            except Exception:
                pass


class _Hook:
    __slots__ = ("name", "codes", "callback", "suppress")

    def __init__(self, name, codes, callback, suppress):
        self.name = name
        self.codes = codes
        self.callback = callback
        self.suppress = suppress


class X11Keyboard:
    """The `keyboard` library subset (see eye_gaze.platform) on X11.

    Everything that talks to X runs on one key thread with its own
    connection; the public methods hand their work to it and wait. Several
    hooks on one key behave like the library's: the event passes only if
    no suppressing hook returned a false value."""

    def __init__(self, display_name):
        self._display_name = display_name
        self._lock = threading.Lock()
        self._thread = None
        self._kd = None
        self._requests = []
        self._wake_r = self._wake_w = None
        self._stop = False
        self._closed = False
        self.error = None
        # Key-thread state.
        self._hooks = {}          # id -> _Hook
        self._next_id = 1
        self._by_code = {}        # keycode -> [hook id]
        self._masks = (0,)
        self._scratch = {}        # keysym -> spare keycode a hook bound
        self._typing = {}         # keysym -> spare keycode write() bound
        self._held = set()        # swallowed, held, our grab still active
        self._watch = set()       # held, our grab gone: release from keymap
        self._passed = set()      # passed through, ungrabbed until released
        self._expect = {}         # (keycode, "down"/"up") -> sent by us
        self._xi_opcode = None    # XInput2 raw events selected
        self.xi_error = None      # why they are not (Turkish, for Tk)
        self._xtest_ids = frozenset()
        self._raw = {}            # keycode -> deque of (time, kind, source)
        self._down_injected = {}  # keycode -> its last press was injected
        # Watchdog: when the current key event's handling began (keyboard
        # possibly frozen) and when a release-poll callback began (None =
        # idle).
        self._busy_since = None
        self._poll_busy_since = None
        # Read again at every connect (a re-arm too).
        self.repeat_delay = None  # the server's, seconds; None = unknown
        self.freeze_budget = _freeze_budget(None)
        self._short_said = False
        self._dead = False        # connection cut / thread failed
        self._dead_since = 0.0
        self._cooldown_s = _REARM_COOLDOWN   # of the current death
        self.gave_up = False      # no more re-arms: dead for good
        self._rearms = []         # monotonic times of the re-arms
        self._doomed = set()      # hooks removed while dead
        # Keys held when the connection died: keycode -> (press injected,
        # hook ids), until released or picked up by the re-arm.
        self._carry = {}
        self._stop_ev = threading.Event()
        self._hier_said = False
        # is_pressed() may come from any thread: its own connection.
        self._qd = None
        self._qlock = threading.Lock()

    # --- public API -------------------------------------------------------------

    def hook_key(self, name, callback, suppress=False):
        """callback(event) runs on the key thread while the keyboard is
        frozen: it must return at once (count, set a flag) and never block.
        One that takes over the freeze budget (self.freeze_budget, about
        0.33 s at the default repeat delay) gets every hook cut (see the
        module docstring)."""
        n = keys.normalize(name)
        if n is None:
            raise ValueError("unknown key name: %r" % (name,))
        hid = self._call(self._do_hook, n, callback, bool(suppress))

        def remove():
            # Noted first: removed while the thread is down, the hook must
            # not come back with the re-arm.
            with self._lock:
                self._doomed.add(hid)
                dead = self._dead
            if dead:
                return              # the re-arm drops it (_finish_rearm)
            try:
                self._call(self._do_unhook, hid)
            except Exception:
                pass
        return remove

    def unhook_all(self):
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            self._doomed.update(list(self._hooks))
            dead = self._dead
        if running and not dead:
            self._call(self._do_unhook_all)

    def send(self, combo):
        """"ctrl+v", "esc", "ctrl+c, ctrl+v" - the library's syntax."""
        steps = []
        for part in str(combo).split(","):
            names = [s.strip() for s in part.split("+") if s.strip()]
            if not names:
                continue
            normed = [keys.normalize(p) for p in names]
            if None in normed:
                raise ValueError("unknown key in %r" % (combo,))
            steps.append(normed)
        if steps:
            self._call(self._do_send, steps)

    def write(self, text):
        text = str(text or "")
        if text:
            self._call(self._do_write, text)

    def is_pressed(self, name):
        n = keys.normalize(name)
        if n is None:
            raise ValueError("unknown key name: %r" % (name,))
        with self._qlock:
            if self._closed:
                return False
            d = self._query_display()
            info = d.display.info
            codes = set()
            pc = _keycode_of_evdev(n, info.min_keycode, info.max_keycode)
            if pc is not None:
                codes.add(pc)
            if not _positional(n):
                ks = _XK.string_to_keysym(keys.to_keysym(n))
                codes.update(c for c, _ in d.keysym_to_keycodes(ks))
                bound = self._scratch.get(ks)
                if bound:
                    codes.add(bound)
            km = d.query_keymap()
        return any(_bit(km, c) for c in codes)

    def _query_display(self):
        """The is_pressed connection (under _qlock), its events read."""
        if self._qd is None:
            self._qd = _xdisplay.Display(self._display_name)
        d = self._qd
        while d.pending_events():
            ev = d.next_event()
            if ev.type == _X.MappingNotify:
                d.refresh_keyboard_mapping(ev)
        return d

    def _side_keymap(self):
        """XQueryKeymap without the key connection (it is dead); None when
        that fails too."""
        with self._qlock:
            if self._closed:
                return None
            try:
                return self._query_display().query_keymap()
            except Exception:
                d, self._qd = self._qd, None
                try:
                    d.close()
                except Exception:
                    pass
                return None

    def alive(self):
        """False while the key thread is down - cut by the watchdog or
        failed, until its re-arm (self.error says why) - and for good once
        it gave up (self.gave_up). True before the first hook, too."""
        t = self._thread
        return t is None or (t.is_alive() and not self._dead)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._stop = True
            self._stop_ev.set()
            t = self._thread
        if t is not None:
            self._wake()
            t.join(2.0)
            # Closed only now, never by the thread: a write to a closed and
            # reused descriptor number would land in some other file.
            fds = (self._wake_r, self._wake_w)
            self._wake_r = self._wake_w = None
            if not t.is_alive():
                for fd in fds:
                    try:
                        os.close(fd)
                    except Exception:
                        pass
        with self._qlock:
            if self._qd is not None:
                try:
                    self._qd.close()
                except Exception:
                    pass
                self._qd = None

    # --- handing work to the key thread -------------------------------------------

    def _call(self, fn, *args):
        if threading.current_thread() is self._thread:
            return fn(*args)        # a callback calling send(): no deadlock
        self._ensure_thread()
        if self._dead:
            raise RuntimeError("X11 key listener is down: %s" % self.error)
        done = threading.Event()
        box = {}
        with self._lock:
            self._requests.append((fn, args, done, box))
        self._wake()
        if not done.wait(_CALL_TIMEOUT):
            raise RuntimeError("X11 key thread does not answer")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _wake(self):
        fd = self._wake_w
        if fd is not None:
            try:
                os.write(fd, b"x")
            except Exception:
                pass

    def _ensure_thread(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("keyboard closed")
            if self._thread is not None:
                # It re-arms itself after a failure; once it gave up, the
                # caller must hear it.
                if self._thread.is_alive():
                    return
                raise RuntimeError("X11 key thread stopped: %s" % self.error)
            self._connect()
            self._wake_r, self._wake_w = os.pipe()
            t = threading.Thread(target=self._run, name="x11-keys",
                                 daemon=True)
            self._thread = t
            t.start()
            self._start_watchdog()

    def _connect(self):
        """A new key connection: XTEST, lock masks, XInput2 raw events."""
        kd = _xdisplay.Display(self._display_name)
        try:
            if not kd.has_extension("XTEST"):
                raise RuntimeError("the X server has no XTEST extension")
            masks = self._lock_masks(kd)
        except Exception:
            kd.close()
            raise
        self._masks = masks
        self._xi_opcode = None
        self.xi_error = None
        self._xtest_ids = frozenset()
        # Before the first grab: the watchdog started next must already
        # thaw the keyboard ahead of the first auto-repeat.
        self.repeat_delay = _repeat_delay(self._display_name)
        self.freeze_budget = _freeze_budget(self.repeat_delay)
        self._note_short_delay()
        self._kd = kd
        self._setup_xi2(kd)

    def _start_watchdog(self):
        threading.Thread(target=self._watchdog, args=(self._kd,),
                         name="x11-keys-watchdog", daemon=True).start()

    def _setup_xi2(self, kd):
        """Select XInput2 raw key events on the root (the injected-key
        check, see the top). Never raises: without them nothing is stamped
        and xi_error says why."""
        try:
            ver = _xi2_version(kd)
            if ver < (2, 1):
                self.xi_error = "X sunucusunda XInput 2.1 yok"
                return
            xi = _xinput
            opcode = kd.display.get_extension_major(xi.extname)
            for evtype in (xi.RawKeyPress, xi.RawKeyRelease):
                kd.ge_add_event_data(opcode, evtype, _RAW)
            kd.screen().root.xinput_select_events(
                [(xi.AllDevices, xi.RawKeyPressMask | xi.RawKeyReleaseMask
                  | xi.HierarchyChangedMask)])
            self._xtest_ids = self._query_xtest(kd)
            kd.sync()
            self._xi_opcode = opcode
        except Exception as exc:
            self.xi_error = _t("platform.x11.xi_select_failed", error=exc)

    @staticmethod
    def _query_xtest(kd):
        """Device ids of the XTest slaves ("Virtual core XTEST keyboard",
        one per master)."""
        ids = set()
        for dv in kd.xinput_query_device(_xinput.AllDevices).devices:
            name = dv.name
            if isinstance(name, bytes):
                name = name.decode("utf-8", "replace")
            if "XTEST" in str(name):
                ids.add(int(dv.deviceid))
        return frozenset(ids)

    def _watchdog(self, kd):
        """Cuts connection `kd` when the keyboard may stay frozen: one key
        event handled too long, or a release-poll callback that blocks the
        thread while X events wait unread (the next grabbed press freezes
        the keyboard and nobody answers it). Shutting the socket makes the
        server drop our grabs and thaw the keyboard at once - another
        connection cannot: XAllowEvents only releases its sender's grab.

        Within the freeze budget plus one check period (_watchdog_period),
        so a held key never auto-repeats into a frozen keyboard (the X
        server crash in Safety at the top) - unless the whole process
        stalls, watchdog included (see there)."""
        def waiting():
            # Unread events: on the socket, or already read into
            # python-xlib's queue by the poll's own request.
            try:
                queued = getattr(kd.display, "event_queue", None)
                ready, _, _ = select.select([kd.fileno()], [], [], 0)
                return bool(ready) or bool(queued)
            except Exception:
                return False
        last = None
        rechecks = _Rechecks()
        reread = time.monotonic() + _REPEAT_REREAD
        while self._kd is kd and not self._stop and not self._dead:
            now = time.monotonic()
            budget = self.freeze_budget
            busy, poll = self._busy_since, self._poll_busy_since
            verdict = _watchdog_verdict(now, last, budget, busy, poll, waiting,
                                        rechecks.for_stall((busy, poll)))
            rechecks.note(verdict)
            last = now
            if verdict == "cut":
                self._mark_dead("a key callback kept the keyboard frozen for "
                                "over %.2f s" % budget, said=True,
                                cooldown=_CUT_COOLDOWN)
                _log("UYARI: bir tus islemi klavyeyi %.2f sn'den uzun "
                     "dondurdu; klavye kilitli kalmasin diye surucu tuslari "
                     "kapatildi, %.2f sn sonra yeniden kurulacak."
                     % (budget, _CUT_COOLDOWN))
                try:
                    kd.display.socket.shutdown(socket.SHUT_RDWR)
                except Exception as exc:
                    _log("HATA: tus baglantisi kapatilamadi: %s" % exc)
                return
            if verdict == "ok" and now >= reread and \
                    self._busy_since is None and self._poll_busy_since is None:
                reread = now + _REPEAT_REREAD
                self._reread_repeat_delay()
                # Its own connection takes a moment: that wait is not
                # starvation, and a freeze that began meanwhile is cut on time.
                last = time.monotonic()
            time.sleep(_watchdog_period(self.freeze_budget))

    def _reread_repeat_delay(self):
        """The desktop may have shortened the repeat delay since we
        connected: a shorter budget is taken at once (a longer one waits for
        the next connect). Its own connection, never the key one."""
        delay = _repeat_delay(self._display_name)
        budget = _freeze_budget(delay)
        if budget < self.freeze_budget:
            self.repeat_delay = delay
            self.freeze_budget = budget
            self._note_short_delay()

    def _note_short_delay(self):
        """Said once: a repeat delay this short leaves the budget very
        short, and a stall that long cuts the keys."""
        d = self.repeat_delay
        if d is None or d >= _SHORT_REPEAT_DELAY or self._short_said:
            return
        self._short_said = True
        _log("NOT: klavye tekrar gecikmesi %d ms, cok kisa; surucu tuslari "
             "klavyeyi en fazla %d ms dondurabilir, daha uzun bir takilmada "
             "tuslar kisa bir sure kapanir. %d ms ustu bir gecikme onerilir."
             % (round(d * 1000), round(self.freeze_budget * 1000),
                round(_SHORT_REPEAT_DELAY * 1000)))

    def _mark_dead(self, reason, said=False, cooldown=None):
        if self._dead:
            return
        self.error = reason
        self._cooldown_s = _REARM_COOLDOWN if cooldown is None else cooldown
        self._dead_since = time.monotonic()
        self._dead = True
        if not said:
            _log("HATA: tus dinleyicisi durdu (%s); surucu tuslari calismiyor,"
                 " %.0f sn sonra yeniden kurulacak." % (reason,
                                                        _REARM_COOLDOWN))

    @staticmethod
    def _lock_masks(kd):
        """The 4 combinations of Caps Lock and Num Lock: a grab is per
        modifier state, and either lock may be on."""
        num = 0
        nl = kd.keysym_to_keycode(_XK.string_to_keysym("Num_Lock"))
        for i, row in enumerate(kd.get_modifier_mapping()):
            if nl and nl in row:
                num = 1 << i
                break
        return tuple(sorted({0, _X.LockMask, num, num | _X.LockMask}))

    # --- the key thread -------------------------------------------------------------

    def _run(self):
        try:
            while True:
                kd = self._kd
                try:
                    self._loop(kd)
                except Exception as exc:
                    if not self._stop:
                        self._mark_dead("%s: %s" % (type(exc).__name__, exc))
                if self._stop:
                    break
                self._drop_connection(kd)
                if not self._rearm():
                    break
        except Exception as exc:
            # The recovery itself failed: said, and for good (alive() is
            # False with the thread, capabilities() says to restart).
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self._dead = True
            self.gave_up = True
            if not self._stop:
                _log("HATA: tus dinleyicisi yeniden kurulamadi (%s); surucu "
                     "tuslari calismiyor, programi yeniden baslatin."
                     % self.error)
        finally:
            self._shutdown()

    def _loop(self, kd):
        while not self._stop:
            self._serve_requests()
            self._poll_releases()
            self._drain()
            timeout = _RELEASE_POLL if self._watch else _IDLE
            ready, _, _ = select.select([kd.fileno(), self._wake_r], [], [],
                                        timeout)
            if self._wake_r in ready:
                os.read(self._wake_r, 4096)

    def _drop_connection(self, kd):
        """The key connection failed: the server dropped its grabs; unbind
        our spare keycodes from elsewhere and forget its state."""
        # Closed before any callback runs: with no watchdog up, the server
        # must not keep a grab of ours frozen meanwhile.
        try:
            kd.close()
        except Exception:
            pass
        self._unbind_elsewhere(sorted(self._bound_codes()))
        self._fail_requests()
        # A key held at the cut sends us no release now. Still held, it is
        # carried: the re-arm's grab would take its next auto-repeat press
        # for a new click (a long hold is the alarm gesture). Released, the
        # hooks hear "up" at once - KeyPresses would keep it "held" and take
        # the first real press after the re-arm for a repeat.
        for code in self._held | self._watch:
            self._carry[code] = (self._down_injected.get(code, False),
                                 list(self._by_code.get(code, ())))
        self._poll_carried()
        self._forget_connection()

    def _poll_carried(self):
        """While the listener is down: report the release of a carried key
        as it happens - the alarm reads "held". All of them when the keymap
        cannot be read."""
        if not self._carry:
            return
        km = self._side_keymap()
        for code in sorted(self._carry):
            if km is None or not _bit(km, code):
                injected, hids = self._carry.pop(code)
                self._dispatch(code, "up", injected, hids)

    def _resume_carried(self):
        """Re-armed: a carried key still held goes on as that hold - its
        repeats are repeats, its release comes from the keymap poll (no
        grab saw its press). Read after the grabs, so a release in between
        is not missed."""
        if not self._carry:
            return
        km = self._kd.query_keymap()
        carry, self._carry = self._carry, {}
        for code, (injected, hids) in sorted(carry.items()):
            if _bit(km, code) and self._by_code.get(code):
                self._watch.add(code)
                self._down_injected[code] = injected
            else:
                self._dispatch(code, "up", injected, hids)

    def _release_carried(self):
        """The listener stops for good: nobody will see these releases."""
        carry, self._carry = self._carry, {}
        for code, (injected, hids) in sorted(carry.items()):
            self._dispatch(code, "up", injected, hids)

    def _forget_connection(self):
        """Per-connection state (the hooks themselves are kept)."""
        self._by_code = {}
        self._scratch = {}
        self._typing = {}
        self._held = set()
        self._watch = set()
        self._passed = set()
        self._expect = {}
        self._raw = {}
        self._down_injected = {}
        self._busy_since = self._poll_busy_since = None

    def _fail_requests(self):
        with self._lock:
            pending, self._requests = self._requests, []
        for _, _, done, box in pending:
            box["error"] = RuntimeError("X11 key listener is down: %s"
                                        % self.error)
            done.set()

    def _rearm(self):
        """Wait out the cooldown, connect again, grab every hook again.
        False when stopping or after _REARM_MAX re-arms in _REARM_WINDOW."""
        while not self._stop:
            now = time.monotonic()
            self._rearms = [t for t in self._rearms
                            if now - t < _REARM_WINDOW]
            if len(self._rearms) >= _REARM_MAX:
                self.gave_up = True
                _log("HATA: tus dinleyicisi %d kez yeniden kuruldu ve yine "
                     "durdu (%s); birakildi. Surucu tuslari calismiyor, "
                     "programi yeniden baslatin." % (_REARM_MAX, self.error))
                return False
            if not self._cooldown(self._dead_since + self._cooldown_s):
                return False
            self._rearms.append(time.monotonic())
            try:
                self._connect()
                self._reattach_hooks()
                self._resume_carried()
                self._finish_rearm()        # clears _dead: the last step
            except Exception as exc:
                # Said, and tried again after the cooldown (at most
                # _REARM_MAX times): a thread dying here would leave the
                # keys dead without a word.
                self.error = "%s: %s" % (type(exc).__name__, exc)
                self._dead_since = time.monotonic()
                self._cooldown_s = _REARM_COOLDOWN
                _log("UYARI: tus dinleyicisi yeniden kurulamadi (%s); %.0f sn"
                     " sonra tekrar denenecek." % (self.error,
                                                   _REARM_COOLDOWN))
                self._drop_connection(self._kd)
                continue
            self._start_watchdog()
            _log("tus dinleyicisi yeniden kuruldu (%d/%d)"
                 % (len(self._rearms), _REARM_MAX))
            return True
        return False

    def _cooldown(self, until):
        """Wait until monotonic `until`, reporting carried releases
        meanwhile. False when stopping."""
        while True:
            self._poll_carried()
            wait = until - time.monotonic()
            if wait <= 0:
                return not self._stop
            step = min(wait, _RELEASE_POLL) if self._carry else wait
            if self._stop_ev.wait(step):
                return False

    def _finish_rearm(self):
        """Clear _dead only when no hook removed meanwhile is still on:
        remove() and unhook_all() look at _dead under the same lock and
        leave a dead listener's hooks to this."""
        while True:
            with self._lock:
                late = sorted(h for h in self._doomed if h in self._hooks)
                if not late:
                    self._dead = False
                    return
            for hid in late:
                self._do_unhook(hid)

    def _reattach_hooks(self):
        """Grab every hook again on the new connection. self._hooks is
        replaced only at the end: unhook_all() from another thread reads it
        meanwhile and must see every hook, not the ones grabbed so far."""
        with self._lock:
            doomed = set(self._doomed)
            old = dict(self._hooks)
        hooks = {}
        for hid, h in sorted(old.items()):
            if hid in doomed:
                continue
            try:
                self._attach(hid, h.name, h.callback, h.suppress, hooks)
            except Exception as exc:
                _log("UYARI: '%s' tusu yeniden alinamadi (%s); bu tus "
                     "calismayacak." % (h.name, exc))
        with self._lock:
            self._hooks = hooks
            self._doomed -= doomed          # dropped above, for good

    def _serve_requests(self):
        while True:
            with self._lock:
                if not self._requests:
                    return
                fn, args, done, box = self._requests.pop(0)
            try:
                box["result"] = fn(*args)
            except Exception as exc:
                box["error"] = exc
            done.set()

    def _shutdown(self):
        self._release_carried()
        kd = self._kd
        bound = sorted(self._bound_codes())
        try:
            self._do_unhook_all()
            for code in self._typing.values():
                kd.change_keyboard_mapping(code, [(0, 0)])
            kd.sync()
        except Exception:
            # The connection is gone (watchdog, X error): the server dropped
            # the grabs with it, but the spare keycodes we bound stay bound
            # until someone unbinds them.
            self._unbind_elsewhere(bound)
        self._typing.clear()
        self._scratch.clear()
        try:
            kd.close()
        except Exception:
            pass
        self._fail_requests()

    def _unbind_elsewhere(self, codes):
        if not codes:
            return
        try:
            d = _xdisplay.Display(self._display_name)
            try:
                for code in codes:
                    d.change_keyboard_mapping(code, [(0, 0)])
                d.sync()
            finally:
                d.close()
        except Exception:
            pass

    def _drain(self, limit=None):
        """Handle queued events: all of them, or only the first `limit`
        (those that arrived before a sync's reply)."""
        kd = self._kd
        n = 0
        while (kd.pending_events() if limit is None else n < limit):
            self._on_event(kd.next_event())
            n += 1

    def _queued(self):
        q = getattr(self._kd.display, "event_queue", None)
        return len(q) if q is not None else self._kd.pending_events()

    def _on_event(self, ev):
        if ev.type == _GENERIC_EVENT:
            if self._xi_opcode is not None and \
                    getattr(ev, "extension", None) == self._xi_opcode:
                self._on_xi(ev)
        elif ev.type == _X.MappingNotify:
            self._kd.refresh_keyboard_mapping(ev)
        elif ev.type in (_X.KeyPress, _X.KeyRelease):
            # From here until _allow() the keyboard may be frozen.
            outer = self._busy_since is None
            if outer:
                self._busy_since = time.monotonic()
            try:
                if ev.type == _X.KeyPress:
                    self._on_key(ev, True)
                    return
                nxt = self._repeat_press(ev)
                if nxt is None:
                    self._on_key(ev, False)
                else:
                    # Auto-repeat of a held key: X sends release + press with
                    # one timestamp. The release is not real; the press is a
                    # repeat (the callbacks ignore repeats, as on Windows).
                    self._allow(_X.AsyncKeyboard)
                    self._on_key(nxt, True, repeat=True)
            finally:
                if outer:
                    self._busy_since = None

    def _on_xi(self, ev):
        xi = _xinput
        if ev.evtype in (xi.RawKeyPress, xi.RawKeyRelease):
            d = ev.data
            code = getattr(d, "detail", None)
            if code is None:
                return              # unparsed: never guess "injected"
            kind = "down" if ev.evtype == xi.RawKeyPress else "up"
            q = self._raw.get(code)
            if q is None:
                q = self._raw[code] = collections.deque(maxlen=_RAW_KEEP)
            q.append((int(d.time), kind, int(d.sourceid)))
        elif ev.evtype == xi.HierarchyChanged:
            # A keyboard plugged in or a master added: XTest slaves may be
            # new too. A failed query keeps the ids we have.
            try:
                self._xtest_ids = self._query_xtest(self._kd)
            except Exception as exc:
                if not self._hier_said:
                    self._hier_said = True
                    _log("UYARI: XInput aygit listesi okunamadi (%s); eski "
                         "liste kullaniliyor." % exc)

    def _is_injected(self, code, kind, t):
        """Did the raw twin of this core event (same keycode and kind, the
        latest at or before X time `t`; None = the latest) come from an
        XTest slave?"""
        q = self._raw.get(code)
        if self._xi_opcode is None or not q:
            return False
        for rt, rk, source in reversed(q):
            # X time is 32-bit milliseconds and wraps: "at or before" mod 2^32.
            if rk == kind and (t is None or ((t - rt) & 0xffffffff) < 1 << 31):
                return source in self._xtest_ids
        return False

    def _repeat_press(self, ev):
        """The auto-repeat press paired with release `ev`, taken off the
        queue; None for a real release. Raw events in between are handled
        on the way."""
        kd = self._kd
        waited = False
        while True:
            if not kd.pending_events():
                if waited:
                    return None
                waited = True
                ready, _, _ = select.select([kd.fileno()], [], [],
                                            _REPEAT_WAIT)
                if not ready or not kd.pending_events():
                    return None
            q = getattr(kd.display, "event_queue", None)
            head = q[0] if q else None
            if head is None:
                return None
            if head.type == _GENERIC_EVENT:
                self._on_event(kd.next_event())
                continue
            if (head.type == _X.KeyPress and head.detail == ev.detail
                    and head.time == ev.time):
                return kd.next_event()
            return None

    def _allow(self, mode):
        self._kd.allow_events(mode, _X.CurrentTime)
        self._kd.flush()

    def _on_key(self, ev, down, repeat=False):
        """One key event reported to us. The grab is synchronous, so the
        keyboard may be frozen until _allow() - which always runs."""
        code = ev.detail
        kind = "down" if down else "up"
        mode = _X.AsyncKeyboard
        try:
            if self._expect.get((code, kind)):
                # A key we sent came back through a grab: stamped injected,
                # never counted, and delivered to the application.
                self._expect[(code, kind)] -= 1
                self._dispatch(code, kind, injected=True)
                mode = self._replay_mode()
            elif not self._by_code.get(code):
                # Another key while we hold a swallowed one: the person's (or
                # the on-screen keyboard's) typing goes on to the application.
                mode = self._replay_mode()
            elif down:
                if repeat or code in self._held or code in self._watch:
                    # A repeat of a held key: the source of its press.
                    injected = self._down_injected.get(code, False)
                else:
                    injected = self._is_injected(code, kind, ev.time)
                    self._down_injected[code] = injected
                if self._dispatch(code, kind, injected):
                    self._held.discard(code)
                    mode = self._replay_mode()
                    self._ungrab(code)          # until released (see top)
                    self._passed.add(code)
                    self._watch.add(code)
                else:
                    self._watch.discard(code)
                    self._held.add(code)
                    mode = _X.SyncKeyboard
            else:
                passed_on = code in self._watch
                self._held.discard(code)
                self._watch.discard(code)
                # The release of the press we heard, whoever sent it: see
                # the top (onboard's release ending a real hold).
                self._dispatch(code, kind, self._down_injected.get(code, False))
                # A swallowed press: the application never saw it, so the
                # release stays ours. A passed-through press: hand it over.
                if passed_on:
                    mode = self._replay_mode()
                    self._regrab(code)
        finally:
            self._allow(mode)

    def _replay_mode(self):
        # Replaying ends our grab: a key we swallowed and is still held has
        # lost it, and its release will go to the application.
        self._watch |= self._held
        self._held.clear()
        return _X.ReplayKeyboard

    def _dispatch(self, code, kind, injected, hids=None):
        """Call every hook of `code` (or those of `hids`); True if the event
        may pass."""
        from eye_gaze.platform import KeyEvent
        passed = True
        if hids is None:
            hids = self._by_code.get(code, ())
        for hid in list(hids):
            h = self._hooks.get(hid)
            if h is None:
                continue
            try:
                ok = h.callback(KeyEvent(kind, h.name, injected))
            except Exception:
                ok = True          # a broken callback must not eat the key
            if h.suppress and not ok:
                passed = False
        return passed

    def _poll_releases(self):
        if not self._watch:
            return
        km = self._kd.query_keymap()
        for code in sorted(self._watch):
            if not _bit(km, code):
                self._watch.discard(code)
                self._regrab(code)
                # Not frozen here (the watchdog only acts if events wait).
                self._poll_busy_since = time.monotonic()
                try:
                    self._dispatch(code, "up",
                                   self._down_injected.get(code, False))
                finally:
                    self._poll_busy_since = None

    def _regrab(self, code):
        """A passed-through key was released: grab it again."""
        if code in self._passed:
            self._passed.discard(code)
            if self._by_code.get(code):
                self._grab(code)
                self._kd.flush()

    # --- grabs ------------------------------------------------------------------------

    def _grab(self, code, check=False):
        kd = self._kd
        root = kd.screen().root
        catch = _xerror.CatchError(_xerror.BadAccess)
        for m in self._masks:
            root.grab_key(code, m, False, _X.GrabModeAsync, _X.GrabModeSync,
                          onerror=catch)
        if check:
            kd.sync()
            if catch.get_error() is not None:
                self._ungrab(code)
                raise OSError("key code %d is already taken by another "
                              "program" % code)

    def _ungrab(self, code):
        root = self._kd.screen().root
        for m in self._masks:
            root.ungrab_key(code, m)

    def _free_keycode(self, taken):
        kd = self._kd
        info = kd.display.info
        lo, hi = max(info.min_keycode, 9), info.max_keycode
        rows = kd.get_keyboard_mapping(lo, hi - lo + 1)
        for i, row in enumerate(rows):
            code = lo + i
            if code not in taken and not any(row):
                return code
        return None

    def _bound_codes(self):
        return set(self._scratch.values()) | set(self._typing.values())

    def _hook_codes(self, name):
        """Keycodes a hook on `name` grabs: the physical key, plus (not for
        letters and digits) whatever key the layout gives that keysym. A
        keysym no key has (F13..F24 on a stock keymap) is bound to a spare
        keycode, so a program that sends it by name (xdotool, an AAC tool)
        reaches the hook too."""
        kd = self._kd
        info = kd.display.info
        codes = []
        pc = _keycode_of_evdev(name, info.min_keycode, info.max_keycode)
        if pc is not None:
            codes.append(pc)
        if not _positional(name):
            ks = _XK.string_to_keysym(keys.to_keysym(name))
            if ks in self._scratch:
                codes.append(self._scratch[ks])
            else:
                mapped = sorted({c for c, _ in kd.keysym_to_keycodes(ks)})
                if mapped:
                    codes.extend(mapped)
                elif ks:
                    spare = self._free_keycode(self._bound_codes())
                    if spare is not None:
                        kd.change_keyboard_mapping(spare, [(ks, ks)])
                        kd.sync()
                        self._scratch[ks] = spare
                        codes.append(spare)
        out = []
        for c in codes:
            if c not in out:
                out.append(c)
        if not out:
            raise ValueError("key %r has no key code on this display" % name)
        return out

    def _do_hook(self, name, callback, suppress):
        hid = self._next_id
        self._next_id += 1
        self._attach(hid, name, callback, suppress)
        return hid

    def _attach(self, hid, name, callback, suppress, hooks=None):
        """Grab `name` for hook `hid`, recorded in `hooks` (self._hooks)."""
        codes = self._hook_codes(name)
        new = [c for c in codes if not self._by_code.get(c)]
        done = []
        try:
            for c in new:
                self._grab(c, check=True)
                done.append(c)
        except Exception:
            for c in done:
                self._ungrab(c)
            self._release_scratch()
            self._kd.sync()
            raise
        (self._hooks if hooks is None else hooks)[hid] = _Hook(
            name, codes, callback, suppress)
        for c in codes:
            self._by_code.setdefault(c, []).append(hid)

    def _do_unhook(self, hid):
        with self._lock:
            self._doomed.discard(hid)
        h = self._hooks.pop(hid, None)
        if h is None:
            return
        for c in h.codes:
            ids = self._by_code.get(c, [])
            if hid in ids:
                ids.remove(hid)
            if not ids:
                self._by_code.pop(c, None)
                self._ungrab(c)
                self._held.discard(c)
                self._watch.discard(c)
                self._passed.discard(c)
        self._release_scratch()
        self._kd.sync()

    def _do_unhook_all(self):
        for hid in list(self._hooks):
            self._do_unhook(hid)

    def _release_scratch(self):
        """Give back the spare keycodes no hook uses any more."""
        for ks, code in list(self._scratch.items()):
            if not self._by_code.get(code):
                self._kd.change_keyboard_mapping(code, [(0, 0)])
                del self._scratch[ks]

    # --- sending ------------------------------------------------------------------------

    def _end_our_grab(self):
        """Keys we send must reach the application, not the grab a held
        swallowed key keeps active."""
        if self._held:
            self._kd.ungrab_keyboard(_X.CurrentTime)
            self._watch |= self._held
            self._held.clear()

    def _send_code(self, name):
        """(keycode, needs shift) that types `name` in the current layout -
        "ctrl+c" must be the key that says c - else the physical key."""
        kd = self._kd
        ks = _XK.string_to_keysym(keys.to_keysym(name))
        if ks in self._scratch:
            return self._scratch[ks], False
        shifted = None
        for code, index in kd.keysym_to_keycodes(ks):
            if index == 0:
                return code, False
            if index == 1 and shifted is None:
                shifted = (code, True)
        if shifted is not None:
            return shifted
        info = kd.display.info
        pc = _keycode_of_evdev(name, info.min_keycode, info.max_keycode)
        if pc is None:
            raise ValueError("key %r has no key code on this display" % name)
        return pc, False

    def _shift_code(self):
        code = self._kd.keysym_to_keycode(_XK.string_to_keysym("Shift_L"))
        if not code:
            raise RuntimeError("no Shift key on this display")
        return code

    def _fake_keys(self, strokes):
        """strokes: [(keycode, down)]. Our grabs on those keycodes are lifted
        meanwhile, so our keys never reach our own hooks (the click loop of
        an injected Space); anything that still comes back is stamped."""
        kd = self._kd
        self._end_our_grab()
        grabbed = sorted({c for c, _ in strokes if self._by_code.get(c)
                          and c not in self._passed})
        for c in grabbed:
            self._ungrab(c)
        for c, down in strokes:
            if self._by_code.get(c):
                k = (c, "down" if down else "up")
                self._expect[k] = self._expect.get(k, 0) + 1
            kd.xtest_fake_input(_X.KeyPress if down else _X.KeyRelease, c)
        for c in grabbed:
            self._grab(c)
        kd.sync()
        # An echo of ours arrived before the sync reply, if at all.
        try:
            self._drain(limit=self._queued())
        finally:
            self._expect.clear()

    def _do_send(self, steps):
        shift = None
        for names in steps:
            strokes, pressed = [], []
            for n in names:
                code, need_shift = self._send_code(n)
                if need_shift:
                    shift = shift or self._shift_code()
                    strokes.append((shift, True))
                    pressed.append(shift)
                strokes.append((code, True))
                pressed.append(code)
            strokes.extend((c, False) for c in reversed(pressed))
            self._fake_keys(strokes)

    def _do_write(self, text):
        kd = self._kd
        wanted = {_char_keysym(c) for c in text}
        plan = []
        bound_now = False
        for ch in text:
            ks = _char_keysym(ch)
            code = need_shift = None
            if ks in self._typing:
                code, need_shift = self._typing[ks], False
            else:
                for c, index in kd.keysym_to_keycodes(ks):
                    if index in (0, 1):
                        code, need_shift = c, index == 1
                        if index == 0:
                            break
            if code is None:
                # The layout has no key for it (Turkish letters on a US
                # layout): bind a spare keycode, as xdotool does. It stays
                # bound: the application reads the mapping when it handles
                # the key, which may be after we would have restored it.
                spare = self._free_keycode(self._bound_codes())
                if spare is None:
                    spare = self._recycle_typing(wanted)
                if spare is None:
                    raise RuntimeError("no spare key code left to type %r"
                                       % ch)
                kd.change_keyboard_mapping(spare, [(ks, ks)])
                self._typing[ks] = spare
                code, need_shift = spare, False
                bound_now = True
            plan.append((code, need_shift))
        if bound_now:
            kd.sync()
        shift = self._shift_code() if any(s for _, s in plan) else None
        strokes = []
        for code, need_shift in plan:
            if need_shift:
                strokes.append((shift, True))
            strokes.extend([(code, True), (code, False)])
            if need_shift:
                strokes.append((shift, False))
        self._fake_keys(strokes)

    def _recycle_typing(self, keep):
        for ks, code in list(self._typing.items()):
            if ks not in keep:
                del self._typing[ks]
                return code
        return None
