# gaze/tests/test_linux_evdev.py
"""The evdev/uinput backend against a fake `evdev` module: no real device,
no /dev/uinput, nothing grabbed on the machine running the suite. Runs on
Windows and Linux alike (the fake devices' file descriptors are sockets, so
the backend's select() loop runs unchanged).

What is pinned here is what keeps the user's keyboard alive and the driver's
switch honest:
  * a grabbed keyboard's other keys come back through the virtual keyboard
    exactly once, repeats and modifiers included;
  * every way out (close, signal, stall, failure, unplug) ungrabs and lifts
    every key we pressed in the virtual keyboard;
  * a consumer (KeyPresses) hears exactly one "up" per "down", and a repeat
    is never a new press.

The permission installer script runs only inside the Linux Docker image, in
its test mode (stub usermod/modprobe/udevadm, a scratch root): nothing
touches the real system.
"""
import collections
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import types

import pytest

from eye_gaze.platform import linux_evdev as le
from eye_gaze.switch import GAMEPAD_BUTTONS, KeyPresses

EV_SYN, EV_KEY, EV_REL, EV_ABS, EV_MSC, EV_LED, EV_REP = 0, 1, 2, 3, 4, 0x11, 0x14
SYN_REPORT, SYN_DROPPED = 0, 3
KEY_A, KEY_LEFTSHIFT, KEY_LEFTCTRL, KEY_V, KEY_SPACE = 30, 42, 29, 47, 57
F13 = 183
KBD_CAPS = {EV_SYN: [0], EV_KEY: list(range(1, 200)), EV_MSC: [4],
            EV_LED: [0, 1, 2], EV_REP: []}

AbsInfo = collections.namedtuple(
    "AbsInfo", "value min max fuzz flat resolution")


def wait_for(cond, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for " + what)


class Spec:
    """One fake /dev/input/eventN node. Its file descriptor is one end of a
    socket pair: pushing an event makes it readable."""

    def __init__(self, path, name, caps):
        self.path = path
        self.name = name
        self.caps = caps
        self.active = set()
        self.queue = collections.deque()
        self.gone = False
        self.unreadable = False
        self.busy_elsewhere = False
        self.props = []
        self.grabbed = False
        self.grab_calls = 0
        self.opened = 0
        self.reads = 0
        self.rsock, self.wsock = socket.socketpair()
        self.rsock.setblocking(False)

    def push(self, *events):
        self.queue.extend(events)
        self.wsock.send(b"x")

    def key(self, code, value):
        if value == 1:
            self.active.add(code)
        elif value == 0:
            self.active.discard(code)
        self.push((EV_KEY, code, value), (EV_SYN, SYN_REPORT, 0))

    def unplug(self):
        self.gone = True
        self.grabbed = False
        self.wsock.send(b"x")

    def close(self):
        for s in (self.rsock, self.wsock):
            try:
                s.close()
            except Exception:
                pass


class World:
    def __init__(self):
        self.devices = {}
        self.uinputs = []
        self.uinput_fail = False
        self.uinput_fail_names = set()
        self.clone_calls = 0
        self.clone_delay = 0
        self.clone_fail = False

    def add(self, path, name, caps):
        spec = Spec(path, name, caps)
        self.devices[path] = spec
        return spec

    def keyboard(self, path="/dev/input/event3", name="AT keyboard",
                 caps=None):
        return self.add(path, name, caps or KBD_CAPS)

    def paths(self):
        return sorted(p for p, s in self.devices.items() if not s.gone)

    def ui(self, name):
        found = [u for u in self.uinputs if u.name == name]
        assert found, "no uinput device named %r" % name
        return found[-1]

    def close(self):
        for s in self.devices.values():
            s.close()


def _fake_module(world):
    class Event:
        def __init__(self, t, c, v):
            self.type, self.code, self.value = t, c, v
            self.sec = self.usec = 0

    class InputDevice:
        def __init__(self, path):
            spec = world.devices.get(path)
            if spec is None or spec.gone:
                raise FileNotFoundError(2, "No such file", path)
            if spec.unreadable:
                raise PermissionError(13, "Permission denied", path)
            spec.opened += 1
            self._s = spec
            self.path = path
            self.name = spec.name
            self.fd = spec.rsock.fileno()

        def fileno(self):
            return self.fd

        def capabilities(self, verbose=False, absinfo=True):
            return {t: list(c) for t, c in self._s.caps.items()}

        def input_props(self, verbose=False):
            return list(self._s.props)

        def active_keys(self, verbose=False):
            if self._s.gone:
                raise OSError(19, "No such device")
            return sorted(self._s.active)

        def grab(self):
            if self._s.busy_elsewhere or self._s.grabbed:
                raise OSError(16, "Device or resource busy")
            self._s.grabbed = True
            self._s.grab_calls += 1

        def ungrab(self):
            if not self._s.grabbed:
                raise OSError(22, "Invalid argument")
            self._s.grabbed = False

        def read(self):
            s = self._s
            s.reads += 1
            try:
                while s.rsock.recv(4096):
                    pass
            except (BlockingIOError, OSError):
                pass
            if s.gone:
                raise OSError(19, "No such device")
            if not s.queue:
                raise BlockingIOError(11, "Resource temporarily unavailable")
            evs = []
            while s.queue:
                evs.append(Event(*s.queue.popleft()))
            return iter(evs)

        def close(self):
            self.fd = -1

    class UInput:
        def __init__(self, events=None, name="py-evdev-uinput", **kw):
            if world.uinput_fail or name in world.uinput_fail_names:
                raise OSError(13, "Permission denied", "/dev/uinput")
            self.events = events
            self.name = name
            self.writes = []
            self.closed = False
            self.fail_writes = False
            self.device = None
            world.uinputs.append(self)

        @classmethod
        def from_device(cls, *devices, filtered_types=(0, 0x15), **kw):
            world.clone_calls += 1
            if world.clone_delay:
                time.sleep(world.clone_delay)
            if world.clone_fail:
                raise OSError(22, "Invalid argument", "/dev/uinput")
            caps = {}
            for d in devices:
                for t, codes in d.capabilities().items():
                    caps.setdefault(t, set()).update(codes)
            for t in filtered_types:
                caps.pop(t, None)
            ui = cls(events={t: sorted(c) for t, c in caps.items()}, **kw)
            ui.input_props = kw.get("input_props")
            ui.source = devices[0].path
            return ui

        def write(self, t, c, v):
            if self.closed or self.fail_writes:
                raise OSError(5, "Input/output error")
            self.writes.append((t, c, v))

        def syn(self):
            if self.closed or self.fail_writes:
                raise OSError(5, "Input/output error")
            self.writes.append(("syn",))

        def close(self):
            self.closed = True

    return types.SimpleNamespace(InputDevice=InputDevice, UInput=UInput,
                                 AbsInfo=AbsInfo)


def key_writes(ui, code=None):
    return [(c, v) for w in list(ui.writes) if w[0] == EV_KEY
            for (_, c, v) in [w] if code is None or c == code]


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(le, "_event_devices", w.paths)
    monkeypatch.setattr(le, "_x_screens_source", lambda: None)
    monkeypatch.setattr(le, "_x_keymap_source", lambda: None)
    monkeypatch.setattr(le, "_fallback_screen_size", lambda: (1920, 1080))
    monkeypatch.setattr(le, "_SCAN_PERIOD", 0.1)
    monkeypatch.setattr(le, "_GRAB_RETRY", 0.01)
    monkeypatch.setattr(le, "_IDLE", 0.05)
    monkeypatch.setattr(le, "_WATCHDOG", 0.3)
    monkeypatch.setattr(le, "_REARM_COOLDOWN", 0.2)
    logged = []
    monkeypatch.setattr(le, "_log", logged.append)
    w.logged = logged
    yield w
    w.close()


@pytest.fixture
def make(world):
    made = []

    def build():
        b = le.EvdevBackend(evdev_module=_fake_module(world))
        made.append(b)
        return b
    yield build
    for b in made:
        b.close()


def _hook(b, name="f13", ret=False, suppress=True):
    got = []

    def cb(e):
        got.append((e.event_type, e.name, e.injected))
        return ret
    b.keyboard.hook_key(name, cb, suppress=suppress)
    return got


def _sync(world, spec, vk):
    """Make sure every event pushed before has been handled: an unhooked
    key's forwarded release comes after them."""
    n = len(key_writes(vk, KEY_SPACE))
    spec.key(KEY_SPACE, 1)
    spec.key(KEY_SPACE, 0)
    wait_for(lambda: len(key_writes(vk, KEY_SPACE)) >= n + 2, what="sync")


# --- suppression and passthrough ----------------------------------------------

def test_registered_key_suppressed_not_forwarded(world, make):
    kbd = world.keyboard()
    b = make()
    got = _hook(b, ret=False)
    wait_for(lambda: kbd.grabbed, what="grab")
    vk = world.ui(le.KEYBOARD_NAME)
    kbd.key(F13, 1)
    kbd.key(F13, 2)
    kbd.key(F13, 0)
    _sync(world, kbd, vk)
    assert got == [("down", "f13", False), ("down", "f13", False),
                   ("up", "f13", False)]
    assert key_writes(vk, F13) == []


def test_unregistered_key_forwarded_verbatim(world, make):
    kbd = world.keyboard()
    b = make()
    _hook(b)                         # a suppressing hook: the device is grabbed
    wait_for(lambda: kbd.grabbed, what="grab")
    vk = world.ui(le.KEYBOARD_NAME)
    kbd.key(KEY_LEFTSHIFT, 1)
    kbd.key(KEY_A, 1)
    kbd.key(KEY_A, 2)
    kbd.key(KEY_A, 2)
    kbd.key(KEY_A, 0)
    kbd.key(KEY_LEFTSHIFT, 0)
    wait_for(lambda: (KEY_LEFTSHIFT, 0) in key_writes(vk), what="forward")
    assert key_writes(vk) == [(KEY_LEFTSHIFT, 1), (KEY_A, 1), (KEY_A, 2),
                              (KEY_A, 2), (KEY_A, 0), (KEY_LEFTSHIFT, 0)]
    # Each key event is its own report: SYN after every one.
    w = [x for x in vk.writes]
    assert all(w[i + 1] == ("syn",) for i in range(0, len(w), 2))


def test_passthrough_when_not_suppressing(world, make):
    kbd = world.keyboard()
    b = make()
    got = _hook(b, ret=True)
    wait_for(lambda: kbd.grabbed, what="grab")
    vk = world.ui(le.KEYBOARD_NAME)
    kbd.key(F13, 1)
    kbd.key(F13, 2)
    kbd.key(F13, 0)
    _sync(world, kbd, vk)
    assert key_writes(vk, F13) == [(F13, 1), (F13, 2), (F13, 0)]
    assert [g[0] for g in got] == ["down", "down", "up"]


def test_release_follows_its_press_not_the_callback(world, make):
    # Swallowed press, then suppression switched off (pause) while held:
    # the release must not reach the app either (it never saw the press),
    # and a forwarded press is always released, whatever the callback says.
    kbd = world.keyboard()
    b = make()
    state = {"ret": False}
    b.keyboard.hook_key("f13", lambda e: state["ret"], suppress=True)
    wait_for(lambda: kbd.grabbed, what="grab")
    vk = world.ui(le.KEYBOARD_NAME)
    kbd.key(F13, 1)
    _sync(world, kbd, vk)
    state["ret"] = True
    kbd.key(F13, 2)
    kbd.key(F13, 0)
    kbd.key(F13, 1)
    _sync(world, kbd, vk)
    state["ret"] = False
    kbd.key(F13, 2)
    kbd.key(F13, 0)
    _sync(world, kbd, vk)
    assert key_writes(vk, F13) == [(F13, 1), (F13, 2), (F13, 0)]


def test_without_suppressing_hooks_nothing_is_grabbed(world, make):
    kbd = world.keyboard()
    b = make()
    got = _hook(b, ret=False, suppress=False)
    time.sleep(0.2)
    assert kbd.grab_calls == 0
    kbd.key(F13, 1)
    kbd.key(F13, 0)
    wait_for(lambda: len(got) == 2, what="events")
    assert key_writes(world.ui(le.KEYBOARD_NAME)) == []   # the app had them

    rm = b.keyboard.hook_key("f14", lambda e: False, suppress=True)
    wait_for(lambda: kbd.grabbed, what="grab")
    rm()
    wait_for(lambda: not kbd.grabbed, what="ungrab after the last remover")


def test_physical_events_are_never_injected(world, make):
    kbd = world.keyboard()
    b = make()
    got = _hook(b, ret=True)
    wait_for(lambda: kbd.grabbed, what="grab")
    kbd.key(F13, 1)
    kbd.key(F13, 0)
    wait_for(lambda: len(got) == 2, what="events")
    assert all(g[2] is False for g in got)


def test_our_virtual_devices_are_never_grabbed(world, make):
    own_kbd = world.add("/dev/input/event8", le.KEYBOARD_NAME, KBD_CAPS)
    own_ptr = world.add("/dev/input/event9", le.POINTER_NAME,
                        {EV_SYN: [0], EV_KEY: [0x110, 0x111],
                         EV_ABS: [0, 1], EV_REL: [8]})
    kbd = world.keyboard()
    b = make()
    got = _hook(b, ret=False)
    wait_for(lambda: kbd.grabbed, what="grab")
    own_kbd.key(F13, 1)              # e.g. our own send() coming back
    own_kbd.key(F13, 0)
    vk = world.ui(le.KEYBOARD_NAME)
    _sync(world, kbd, vk)
    time.sleep(0.2)                  # a few rescans
    assert got == []
    for own in (own_kbd, own_ptr):
        assert own.grab_calls == 0 and own.reads == 0


def test_send_and_write_go_through_the_virtual_keyboard(world, make):
    world.keyboard()
    b = make()
    vk = world.ui(le.KEYBOARD_NAME)
    b.keyboard.send("ctrl+v")
    assert key_writes(vk) == [(KEY_LEFTCTRL, 1), (KEY_V, 1), (KEY_V, 0),
                              (KEY_LEFTCTRL, 0)]
    del vk.writes[:]
    # Without the desktop's layout nothing is typed at all: a guessed key
    # types the wrong letter (Turkish Q: the US "i" key types "ı").
    for text in ("Ab 1", "i", " "):
        with pytest.raises(ValueError):
            b.keyboard.write(text)
    b.keyboard.write("")
    assert vk.writes == []
    with pytest.raises(ValueError):
        b.keyboard.send("ctrl+nosuchkey")


class TurkishQ:
    """Fake X keymap of a Turkish Q layout: keysym -> [(X keycode, level)];
    X keycode = evdev code + 8."""

    def __init__(self):
        k = {}

        def put(code, *syms):
            for level, ks in zip((0, 1, 4, 5), syms):
                if ks is not None:
                    k.setdefault(ks, []).append((code + 8, level))
        for ch, code in zip("qwertyuopasdfghjklzxcvbnm",
                            (16, 17, 18, 19, 20, 21, 22, 24, 25, 30, 31, 32,
                             33, 34, 35, 36, 37, 38, 44, 45, 46, 47, 48, 49,
                             50)):
            if ch != "q":
                put(code, ord(ch), ord(ch.upper()))
        put(16, ord("q"), ord("Q"), ord("@"))      # AltGr+Q = @
        put(23, 0x2b9, ord("I"))                    # US "i" key: ı I
        put(40, ord("i"), 0x2a9)                    # US "'" key: i İ
        put(39, 0x1ba, 0x1aa)                       # ş Ş
        put(26, 0x2bb, 0x2ab)                       # ğ Ğ
        put(53, ord("."), ord(":"))                 # US "/" key: . :
        put(57, ord(" "))
        put(28, 0xff0d)
        self.k = k

    def keycodes(self, ks):
        return list(self.k.get(ks, ()))

    def close(self):
        pass


def test_write_types_through_the_desktop_layout(world, make, monkeypatch):
    monkeypatch.setattr(le, "_x_keymap_source", TurkishQ)
    world.keyboard()
    b = make()
    vk = world.ui(le.KEYBOARD_NAME)
    b.keyboard.write("bir iki.")
    assert key_writes(vk) == [
        (48, 1), (48, 0), (40, 1), (40, 0), (19, 1), (19, 0),
        (KEY_SPACE, 1), (KEY_SPACE, 0),
        (40, 1), (40, 0), (37, 1), (37, 0), (40, 1), (40, 0),
        (53, 1), (53, 0)]
    del vk.writes[:]
    b.keyboard.write("Iıİş@")
    assert key_writes(vk) == [
        (KEY_LEFTSHIFT, 1), (23, 1), (23, 0), (KEY_LEFTSHIFT, 0),   # I
        (23, 1), (23, 0),                                           # ı
        (KEY_LEFTSHIFT, 1), (40, 1), (40, 0), (KEY_LEFTSHIFT, 0),   # İ
        (39, 1), (39, 0),                                           # ş
        (100, 1), (16, 1), (16, 0), (100, 0)]                       # @
    del vk.writes[:]
    with pytest.raises(ValueError):
        b.keyboard.write("ab€")     # no key for it: nothing typed
    assert vk.writes == []


def test_send_letters_follow_the_layout(world, make, monkeypatch):
    class Dvorak(TurkishQ):
        def __init__(self):
            self.k = {ord("v"): [(52 + 8, 0)], ord("V"): [(52 + 8, 1)]}
    monkeypatch.setattr(le, "_x_keymap_source", Dvorak)
    world.keyboard()
    b = make()
    vk = world.ui(le.KEYBOARD_NAME)
    b.keyboard.send("ctrl+v")             # V where the layout has it
    assert key_writes(vk) == [(KEY_LEFTCTRL, 1), (52, 1), (52, 0),
                              (KEY_LEFTCTRL, 0)]
    del vk.writes[:]
    b.keyboard.send("ctrl+c")             # no "c" in the keymap: position
    assert key_writes(vk) == [(KEY_LEFTCTRL, 1), (46, 1), (46, 0),
                              (KEY_LEFTCTRL, 0)]


def test_is_pressed_reads_the_kernel_key_state(world, make):
    kbd = world.keyboard()
    b = make()
    _hook(b, name="space", ret=False)
    wait_for(lambda: kbd.grabbed, what="grab")
    assert b.keyboard.is_pressed("space") is False
    kbd.active.add(KEY_SPACE)        # held, event not even read yet
    assert b.keyboard.is_pressed("space") is True
    with pytest.raises(ValueError):
        b.keyboard.is_pressed("nosuchkey")


# --- keeping the consumer's held state honest ----------------------------------

def test_repeat_of_a_key_held_before_start_is_not_a_press(world, make):
    kbd = world.keyboard()
    kbd.active.add(F13)              # held while the program starts
    b = make()
    hub = KeyPresses(["f13"], kb=b.keyboard)
    time.sleep(0.1)
    assert kbd.grab_calls == 0       # never grabbed with a key down
    kbd.key(F13, 2)
    kbd.key(F13, 2)
    kbd.key(F13, 0)
    wait_for(lambda: kbd.grabbed, what="grab once all keys are up")
    assert hub.take("f13") is False
    kbd.key(F13, 1)
    wait_for(lambda: hub.held("f13"), what="press")
    assert hub.take("f13") is True
    kbd.key(F13, 0)
    wait_for(lambda: not hub.held("f13"), what="release")


def test_grab_waits_until_keys_are_up(world, make):
    kbd = world.keyboard()
    b = make()
    kbd.key(KEY_A, 1)                # typing while the hook goes on
    time.sleep(0.05)
    _hook(b)
    time.sleep(0.15)
    assert not kbd.grabbed           # the app saw that press: its release too
    kbd.key(KEY_A, 0)
    wait_for(lambda: kbd.grabbed, what="grab")
    assert key_writes(world.ui(le.KEYBOARD_NAME), KEY_A) == []


def test_device_unplug_does_not_raise(world, make):
    kbd = world.keyboard()
    b = make()
    hub = KeyPresses(["f13"], kb=b.keyboard)
    wait_for(lambda: kbd.grabbed, what="grab")
    vk = world.ui(le.KEYBOARD_NAME)
    kbd.key(KEY_A, 1)                # forwarded, held
    kbd.key(F13, 1)                  # swallowed, held
    wait_for(lambda: hub.held("f13"), what="press")
    assert hub.take("f13") is True
    kbd.unplug()
    wait_for(lambda: not hub.held("f13"), what="up on unplug")
    wait_for(lambda: (KEY_A, 0) in key_writes(vk), what="virtual release")
    assert b.keyboard.alive()
    assert hub.take("f13") is False
    assert any("event3" in m or "AT keyboard" in m for m in world.logged)

    # Plugged in again (same node): found by the rescan, grabbed again.
    kbd2 = world.keyboard()
    wait_for(lambda: kbd2.grabbed, what="regrab after replug")
    kbd2.key(F13, 1)
    wait_for(lambda: hub.held("f13"), what="press on the new device")
    assert hub.take("f13") is True


def test_hotplugged_keyboard_is_found(world, make):
    b = make()
    got = _hook(b, ret=False)
    kbd = world.keyboard(path="/dev/input/event12", name="foot switch")
    wait_for(lambda: kbd.grabbed, what="grab of the new device")
    kbd.key(F13, 1)
    wait_for(lambda: got, what="press")


def test_syn_dropped_resyncs_from_the_kernel_state(world, make):
    kbd = world.keyboard()
    b = make()
    hub = KeyPresses(["f13"], kb=b.keyboard)
    wait_for(lambda: kbd.grabbed, what="grab")
    kbd.key(F13, 1)
    wait_for(lambda: hub.held("f13"), what="press")
    # The buffer overflowed: the release was lost with the dropped events.
    kbd.active.discard(F13)
    kbd.push((EV_SYN, SYN_DROPPED, 0), (EV_KEY, F13, 0),
             (EV_SYN, SYN_REPORT, 0))
    wait_for(lambda: not hub.held("f13"), what="resynced release")
    assert hub.take("f13") is True and hub.take("f13") is False


def test_reader_failure_rearms_and_keeps_a_held_key_one_hold(world, make):
    kbd = world.keyboard()
    b = make()
    hub = KeyPresses(["f13"], kb=b.keyboard)
    wait_for(lambda: kbd.grabbed, what="grab")
    vk = world.ui(le.KEYBOARD_NAME)
    kbd.key(KEY_A, 1)                # forwarded and held at the failure
    kbd.key(F13, 1)
    wait_for(lambda: hub.held("f13"), what="press")
    assert hub.take("f13") is True

    real = b.keyboard._apply_grabs
    boom = {"n": 1}

    def failing():
        if boom["n"]:
            boom["n"] -= 1
            raise RuntimeError("simulated reader bug")
        return real()
    b.keyboard._apply_grabs = failing
    wait_for(lambda: not b.keyboard.alive(), what="dead")
    wait_for(lambda: not kbd.grabbed, what="ungrab on failure")
    wait_for(lambda: (KEY_A, 0) in key_writes(vk), what="virtual release")
    wait_for(lambda: b.keyboard.alive(), what="re-armed")
    assert hub.held("f13")           # still physically held: one hold
    kbd.key(F13, 2)                  # its auto-repeat: not a new press
    kbd.key(KEY_A, 0)
    time.sleep(0.2)                  # not grabbed yet (F13 held): no sync key
    assert hub.take("f13") is False
    kbd.key(F13, 0)
    wait_for(lambda: not hub.held("f13"), what="release")
    assert any("simulated reader bug" in m for m in world.logged)


def test_watchdog_ungrabs_on_stall(world, make):
    kbd = world.keyboard()
    b = make()
    gate = threading.Event()
    calls = []

    def slow(e):
        calls.append(e.event_type)
        if e.event_type == "down" and len(calls) == 1:
            gate.wait(10)            # a callback that blocks
        return False
    b.keyboard.hook_key("f13", slow, suppress=True)
    wait_for(lambda: kbd.grabbed, what="grab")
    vk = world.ui(le.KEYBOARD_NAME)
    kbd.key(KEY_LEFTSHIFT, 1)        # held, forwarded
    wait_for(lambda: (KEY_LEFTSHIFT, 1) in key_writes(vk), what="forward")
    kbd.key(F13, 1)
    wait_for(lambda: not kbd.grabbed, timeout=3, what="watchdog ungrab")
    assert (KEY_LEFTSHIFT, 0) in key_writes(vk)   # no modifier left stuck
    assert not b.keyboard.alive()
    assert "suppress_keys" in b.capabilities()
    assert b.capabilities()["global_keys"][0] is False
    assert any("dondurdu" in m or "surdu" in m for m in world.logged)
    gate.set()
    wait_for(lambda: b.keyboard.alive(), what="alive again")
    kbd.key(KEY_LEFTSHIFT, 0)        # went to the app directly: not ours
    kbd.key(F13, 0)
    wait_for(lambda: kbd.grabbed, what="regrab after the cooldown")
    assert key_writes(vk, KEY_LEFTSHIFT) == [(KEY_LEFTSHIFT, 1),
                                             (KEY_LEFTSHIFT, 0)]


def test_forward_failure_ungrabs_the_keyboard(world, make):
    kbd = world.keyboard()
    b = make()
    _hook(b)
    wait_for(lambda: kbd.grabbed, what="grab")
    world.ui(le.KEYBOARD_NAME).fail_writes = True
    kbd.key(KEY_A, 1)                # cannot be given back: let go
    wait_for(lambda: not kbd.grabbed, what="ungrab")
    time.sleep(0.3)
    assert not kbd.grabbed           # and not grabbed again
    assert b.capabilities()["suppress_keys"][0] is False


def test_grab_taken_by_another_program_is_said_once(world, make):
    kbd = world.keyboard()
    kbd.busy_elsewhere = True        # keyd, a game, ...
    b = make()
    got = _hook(b, ret=False)
    time.sleep(0.4)
    said = [m for m in world.logged if "yakalanamadi" in m]
    assert len(said) == 1
    kbd.key(F13, 1)                  # still heard (not swallowed)
    wait_for(lambda: got, what="press")


def test_keyboard_with_a_mouse_goes_through_its_own_clone(world, make):
    caps = dict(KBD_CAPS)
    caps[EV_KEY] = KBD_CAPS[EV_KEY] + [0x110, 0x111]
    caps[EV_REL] = [0, 1, 8]
    combo = world.keyboard(caps=caps, name="keyboard+mouse")
    combo.props = [0]
    b = make()
    got = _hook(b, ret=False)
    wait_for(lambda: combo.grabbed, what="grab")
    clone = [u for u in world.uinputs if u.name.startswith("gozfaresi-klon")]
    assert len(clone) == 1
    clone = clone[0]
    assert clone.source == combo.path and clone.input_props == [0]
    assert EV_REP not in clone.events and EV_REL in clone.events
    vk = world.ui(le.KEYBOARD_NAME)

    # A movement stays one frame: REL_X and REL_Y, then one SYN.
    combo.push((EV_REL, 0, 5), (EV_REL, 1, -3), (EV_SYN, SYN_REPORT, 0))
    wait_for(lambda: ("syn",) in clone.writes, what="motion")
    assert clone.writes == [(EV_REL, 0, 5), (EV_REL, 1, -3), ("syn",)]
    del clone.writes[:]
    # The hooked key is swallowed out of its frame, the rest goes on.
    combo.active.add(F13)
    combo.push((EV_MSC, 4, 0x70068), (EV_KEY, F13, 1), (EV_REL, 0, 2),
               (EV_SYN, SYN_REPORT, 0))
    combo.active.add(KEY_A)
    combo.push((EV_KEY, KEY_A, 1), (EV_SYN, SYN_REPORT, 0))
    combo.active.add(0x110)
    combo.push((EV_KEY, 0x110, 1), (EV_SYN, SYN_REPORT, 0))   # button held
    wait_for(lambda: (EV_KEY, 0x110, 1) in clone.writes, what="button")
    assert clone.writes == [
        (EV_MSC, 4, 0x70068), (EV_REL, 0, 2), ("syn",),
        (EV_KEY, KEY_A, 1), ("syn",),
        (EV_KEY, 0x110, 1), ("syn",)]
    assert got == [("down", "f13", False)]
    assert key_writes(vk) == []           # nothing through the shared one
    # Ungrab (the hook goes): nothing stays down in the clone.
    del clone.writes[:]
    b.keyboard.unhook_all()
    wait_for(lambda: not combo.grabbed, what="ungrab")
    assert sorted(w for w in clone.writes if w[0] == EV_KEY) == [
        (EV_KEY, KEY_A, 0), (EV_KEY, 0x110, 0)]
    # ... and the clone itself goes with the grab (the kernel releases
    # whatever is still down in a destroyed device).
    assert clone.closed
    assert all(not sp.name.startswith("gozfaresi") or sp.reads == 0
               for sp in world.devices.values())


def _combo(world, name="keyboard+mouse"):
    caps = dict(KBD_CAPS)
    caps[EV_KEY] = KBD_CAPS[EV_KEY] + [0x110, 0x111]
    caps[EV_REL] = [0, 1, 8]
    return world.keyboard(caps=caps, name=name)


def _clones(world):
    return [u for u in world.uinputs if u.name.startswith("gozfaresi-klon")]


def test_events_while_the_clone_is_made_are_not_replayed(world, make):
    # Making a clone takes a while (here longer than the test watchdog):
    # what happens meanwhile went to the desktop and must not come out of
    # the clone again - and the reader must not stall.
    world.clone_delay = 0.5
    combo = _combo(world)
    b = make()
    got = _hook(b, ret=False)
    wait_for(lambda: world.clone_calls == 1, what="clone started")
    combo.push((EV_REL, 0, 7), (EV_SYN, SYN_REPORT, 0))
    combo.key(KEY_A, 1)
    combo.key(KEY_A, 0)
    alive = []
    while not combo.grabbed:
        alive.append(b.keyboard.alive())
        time.sleep(0.02)
        assert len(alive) < 250, "never grabbed"
    assert all(alive)
    assert not any("dondurdu" in m for m in world.logged)
    clone = _clones(world)[0]
    combo.push((EV_REL, 1, 1), (EV_SYN, SYN_REPORT, 0))
    wait_for(lambda: ("syn",) in clone.writes, what="first frame")
    assert clone.writes == [(EV_REL, 1, 1), ("syn",)]
    assert got == []


def test_a_failed_clone_is_not_tried_again(world, make):
    world.clone_fail = True
    combo = _combo(world)
    b = make()
    got = _hook(b, ret=False)
    wait_for(lambda: any("kopya kurulamadi" in m for m in world.logged),
             what="said")
    time.sleep(0.5)                  # several scan periods
    assert world.clone_calls == 1
    assert len([m for m in world.logged if "kopya kurulamadi" in m]) == 1
    assert combo.grab_calls == 0
    ok, why = b.capabilities()["suppress_keys"]
    assert ok is True and "keyboard+mouse" in why
    combo.key(F13, 1)
    wait_for(lambda: got, what="still heard")


def test_quiet_uinput_does_not_look_for_its_node(world):
    cls = le._uinput_class(_fake_module(world))
    assert cls._find_device(object()) is None


def test_a_lift_the_lock_kept_out_is_done_later(world, make):
    kbd = world.keyboard()
    b = make()
    _hook(b)
    wait_for(lambda: kbd.grabbed, what="grab")
    vk = world.ui(le.KEYBOARD_NAME)
    kbd.key(KEY_A, 1)
    wait_for(lambda: (KEY_A, 1) in key_writes(vk), what="forward")
    kb = b.keyboard
    with kb._out_lock:              # e.g. a long write() in progress
        kb._release_all()            # (the watchdog's way: no cooldown)
        assert not kbd.grabbed       # the grab goes at once
        assert (KEY_A, 0) not in key_writes(vk)
    wait_for(lambda: (KEY_A, 0) in key_writes(vk), what="late lift")


def test_pads_keep_working_without_the_watchdog(world, make, monkeypatch):
    pad = world.add("/dev/input/event20", "switch interface",
                    {EV_SYN: [0], EV_KEY: [0x120, 0x121]})
    kbd = world.keyboard()
    b = make()

    def slowish(e):
        time.sleep(0.2)
        return False
    b.keyboard.hook_key("f13", slowish, suppress=True)
    wait_for(lambda: kbd.grabbed, what="grab")
    monkeypatch.setattr(le, "_WATCHDOG", None)
    kbd.key(F13, 1)
    wait_for(lambda: b.keyboard.watchdog_dead, what="watchdog death")
    gp = b.gamepad()
    pad.key(0x120, 1)
    wait_for(lambda: gp.pressed("BTN1"), what="pad still read")
    assert gp.buttons() is not None


def test_a_foot_switch_with_few_keys_is_grabbed(world, make):
    switch = world.add("/dev/input/event14", "USB foot switch",
                       {EV_SYN: [0], EV_KEY: [48, 49, 50], EV_MSC: [4]})
    power = world.add("/dev/input/event0", "Power Button",
                      {EV_SYN: [0], EV_KEY: [116]})
    b = make()
    got = _hook(b, name="b", ret=False)
    wait_for(lambda: switch.grabbed, what="grab of the switch")
    vk = world.ui(le.KEYBOARD_NAME)
    switch.key(48, 1)
    switch.key(49, 1)               # not hooked: given back
    switch.key(49, 0)
    switch.key(48, 0)
    wait_for(lambda: len(got) == 2, what="the switch's release")
    assert key_writes(vk) == [(49, 1), (49, 0)]
    assert [g[0] for g in got] == ["down", "up"]
    assert power.grab_calls == 0    # none of its keys is hooked


def test_a_touchpad_is_never_grabbed_and_that_is_said(world, make):
    caps = dict(KBD_CAPS)
    caps[EV_KEY] = KBD_CAPS[EV_KEY] + [0x110, 0x145, 0x14a]
    caps[EV_ABS] = [0, 1, 0x2f, 0x35, 0x36, 0x39]
    pad = world.keyboard(caps=caps, name="keyboard+touchpad")
    b = make()
    got = _hook(b, ret=False)
    wait_for(lambda: any("dokunmatik" in m for m in world.logged),
             what="notice")
    assert pad.grab_calls == 0
    pad.key(F13, 1)
    wait_for(lambda: got, what="press still heard")
    ok, why = b.capabilities()["suppress_keys"]
    assert ok is True and "keyboard+touchpad" in why
    assert not [u for u in world.uinputs if "klon" in u.name]


def test_try_grab_rechecks_after_its_drain(world):
    # A callback run by the pre-grab drain removes the last suppressing
    # hook: the device must not be grabbed for nothing.
    kbd = world.keyboard()
    kb = le.EvdevKeyboard(_fake_module(world))
    try:
        kb._scan()
        d = kb._devs[kbd.path]
        rm = []

        def cb(e):
            rm[0]()
            return False
        rm.append(kb.hook_key("f13", cb, suppress=True))
        kbd.key(F13, 1)
        kbd.active.discard(F13)
        assert kb._try_grab(d) is True
        assert kbd.grab_calls == 0 and not d.grabbed
    finally:
        kb.close()


def test_a_dead_watchdog_stops_grabbing_and_says_so(world, make,
                                                     monkeypatch):
    kbd = world.keyboard()
    b = make()

    def slowish(e):
        time.sleep(0.2)              # a round the watchdog gets to check
        return False
    b.keyboard.hook_key("f13", slowish, suppress=True)
    wait_for(lambda: kbd.grabbed, what="grab")
    vk = world.ui(le.KEYBOARD_NAME)
    kbd.key(KEY_A, 1)
    wait_for(lambda: (KEY_A, 1) in key_writes(vk), what="forward")
    monkeypatch.setattr(le, "_WATCHDOG", None)    # its next check raises
    kbd.key(F13, 1)
    wait_for(lambda: b.keyboard.watchdog_dead, what="watchdog death")
    wait_for(lambda: not kbd.grabbed, what="ungrab")
    assert not b.keyboard.alive()
    assert b.capabilities()["suppress_keys"][0] is False
    assert len([m for m in world.logged if "bekcisi" in m]) == 1
    assert (KEY_A, 0) in key_writes(vk)


def test_backend_built_off_the_main_thread_says_why_no_signals(
        world, monkeypatch):
    monkeypatch.setattr(le, "_said_no_signals", [False])
    world.keyboard()
    made = []
    for _ in range(2):
        t = threading.Thread(target=lambda: made.append(
            le.EvdevBackend(evdev_module=_fake_module(world))))
        t.start()
        t.join(10)
    try:
        assert len(made) == 2
        assert len([m for m in world.logged if "sinyal" in m]) == 1
    finally:
        for b in made:
            b.close()


# --- every way out lets go -------------------------------------------------------

def test_close_ungrabs_and_releases_virtual_keys(world, make):
    kbd = world.keyboard()
    b = make()
    _hook(b)
    wait_for(lambda: kbd.grabbed, what="grab")
    vk = world.ui(le.KEYBOARD_NAME)
    kbd.key(KEY_A, 1)
    wait_for(lambda: (KEY_A, 1) in key_writes(vk), what="forward")
    b.close()
    assert not kbd.grabbed
    assert key_writes(vk)[-1] == (KEY_A, 0)
    assert vk.closed and world.ui(le.POINTER_NAME).closed
    assert not b.keyboard.alive()
    with pytest.raises(RuntimeError):
        b.keyboard.hook_key("f13", lambda e: True)


def test_signal_handler_ungrabs_and_chains(world, make):
    seen = []

    def previous(signum, frame):
        seen.append(signum)
    old = signal.signal(signal.SIGTERM, previous)
    try:
        kbd = world.keyboard()
        b = make()
        _hook(b)
        wait_for(lambda: kbd.grabbed, what="grab")
        vk = world.ui(le.KEYBOARD_NAME)
        kbd.key(KEY_A, 1)
        wait_for(lambda: (KEY_A, 1) in key_writes(vk), what="forward")
        handler = signal.getsignal(signal.SIGTERM)
        assert handler is not previous
        handler(signal.SIGTERM, None)
        assert seen == [signal.SIGTERM]
        assert not kbd.grabbed
        assert (KEY_A, 0) in key_writes(vk)
        b.close()
        assert signal.getsignal(signal.SIGTERM) is previous
    finally:
        signal.signal(signal.SIGTERM, old)


def test_uinput_failure_raises_so_the_choice_falls_through(world):
    world.keyboard()
    world.uinput_fail = True
    with pytest.raises(OSError):
        le.EvdevBackend(evdev_module=_fake_module(world))


def test_a_half_built_backend_leaves_nothing_behind(world):
    world.keyboard()
    world.uinput_fail_names.add(le.KEYBOARD_NAME)
    with pytest.raises(OSError):
        le.EvdevBackend(evdev_module=_fake_module(world))
    assert world.ui(le.POINTER_NAME).closed


def test_reader_past_its_rearm_budget_stops_and_says_so(world, make,
                                                         monkeypatch):
    monkeypatch.setattr(le, "_REARM_MAX", 1)
    kbd = world.keyboard()
    b = make()
    hub = KeyPresses(["f13"], kb=b.keyboard)
    wait_for(lambda: kbd.grabbed, what="grab")
    kbd.key(F13, 1)
    wait_for(lambda: hub.held("f13"), what="press")

    def broken():
        raise RuntimeError("reader keeps failing")
    b.keyboard._apply_grabs = broken
    wait_for(lambda: b.keyboard.gave_up, what="given up")
    assert not b.keyboard.alive() and not kbd.grabbed
    # Nobody will report the release now: the hold ends here, not never.
    assert not hub.held("f13")
    ok, why = b.capabilities()["global_keys"]
    assert ok is False and "yeniden başlatın" in why
    with pytest.raises(RuntimeError):
        b.keyboard.hook_key("f14", lambda e: True)
    assert any("birakildi" in m for m in world.logged)


def test_stalls_past_the_budget_stop_swallowing_for_good(world, make,
                                                          monkeypatch):
    monkeypatch.setattr(le, "_REARM_MAX", 0)
    kbd = world.keyboard()
    b = make()
    gate = threading.Event()

    def slow(e):
        if e.event_type == "down":
            gate.wait(10)
        return False
    b.keyboard.hook_key("f13", slow, suppress=True)
    wait_for(lambda: kbd.grabbed, what="grab")
    kbd.key(F13, 1)
    wait_for(lambda: not kbd.grabbed, timeout=3, what="watchdog ungrab")
    gate.set()
    kbd.key(F13, 0)
    wait_for(lambda: b.keyboard.alive(), what="alive again")
    time.sleep(0.4)                  # past the cooldown
    assert not kbd.grabbed
    assert b.keyboard.grab_gave_up
    assert b.capabilities()["suppress_keys"][0] is False
    assert "global_keys" not in b.capabilities()   # still heard (default)


# --- pointer --------------------------------------------------------------------

def test_abs_pointer_range_equals_desktop(world, make, monkeypatch):
    class X:
        def screens(self):
            return [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)]

        def primary(self):
            return (0, 0, 1920, 1080)

        def close(self):
            pass
    monkeypatch.setattr(le, "_x_screens_source", X)
    b = make()
    ptr = world.ui(le.POINTER_NAME)
    absinfo = dict(ptr.events[EV_ABS])
    assert (absinfo[0].min, absinfo[0].max) == (0, 3199)
    assert (absinfo[1].min, absinfo[1].max) == (0, 1079)
    assert {0x110, 0x111} <= set(ptr.events[EV_KEY])
    assert 8 in ptr.events[EV_REL]
    assert b.screens() == [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)]
    assert b.primary() == (0, 0, 1920, 1080)
    assert b.get_cursor() == (960, 540)   # nothing written yet: the centre

    b.set_cursor(2000, 500)
    assert ptr.writes[-3:] == [(EV_ABS, 0, 2000), (EV_ABS, 1, 500), ("syn",)]
    assert b.get_cursor() == (2000, 500)
    b.set_cursor(-50, 99999)
    assert b.get_cursor() == (0, 1079)

    b.button("left", True)
    b.button("left", False)
    b.button("right", True)
    assert [w for w in ptr.writes if w[0] == EV_KEY] == [
        (EV_KEY, 0x110, 1), (EV_KEY, 0x110, 0), (EV_KEY, 0x111, 1)]
    with pytest.raises(ValueError):
        b.button("thumb", True)
    b.scroll(240)
    b.scroll(-120)
    b.scroll(0)
    assert [w for w in ptr.writes if w[0] == EV_REL] == [
        (EV_REL, 8, 2), (EV_REL, 8, -1)]


def test_pointer_follows_a_desktop_change(world, make, monkeypatch):
    shapes = [[(0, 0, 1920, 1080)]]

    class X:
        def screens(self):
            return list(shapes[0])

        def primary(self):
            return shapes[0][0]

        def close(self):
            pass
    monkeypatch.setattr(le, "_x_screens_source", X)
    monkeypatch.setattr(le, "_SCREENS_TTL", 0.0)
    b = make()
    first = world.ui(le.POINTER_NAME)
    shapes[0] = [(0, 0, 2560, 1440)]
    b.set_cursor(2500, 1400)
    second = world.ui(le.POINTER_NAME)
    assert second is not first and first.closed
    assert dict(second.events[EV_ABS])[0].max == 2559
    assert second.writes[-3:] == [(EV_ABS, 0, 2500), (EV_ABS, 1, 1400),
                                  ("syn",)]


def test_screen_guess_is_said(world, make, monkeypatch):
    monkeypatch.setattr(le, "_fallback_screen_size", lambda: None)
    b = make()
    assert b.screens() == [(0, 0, 1920, 1080)]
    assert "1920x1080" in b.capabilities()["cursor"][1]


# --- gamepad --------------------------------------------------------------------

def test_gamepad_names(world, make):
    pad = world.add("/dev/input/event20", "Xbox Wireless Controller",
                    {EV_SYN: [0],
                     EV_KEY: [0x130, 0x131, 0x133, 0x134, 0x136, 0x137,
                              0x13a, 0x13b, 0x13c, 0x13d, 0x13e],
                     EV_ABS: [0, 1, 0x10, 0x11]})
    joy = world.add("/dev/input/event21", "USB switch interface",
                    {EV_SYN: [0], EV_KEY: list(range(0x120, 0x125)),
                     EV_ABS: [0, 1]})
    b = make()
    gp = b.gamepad()
    wait_for(lambda: gp.buttons() == 0, what="pads found")
    names = {0x130: "A", 0x131: "B", 0x133: "X", 0x134: "Y", 0x13b: "START",
             0x13a: "BACK", 0x136: "LB", 0x137: "RB", 0x13d: "LS",
             0x13e: "RS"}
    for code, name in names.items():
        pad.key(code, 1)
        wait_for(lambda n=name: gp.pressed(n), what=name)
        assert gp.pressed_names() == [name]
        pad.key(code, 0)
        wait_for(lambda: gp.pressed_names() == [], what=name + " up")
    joy.key(0x120, 1)
    joy.key(0x124, 1)
    wait_for(lambda: gp.pressed_names() == ["BTN1", "BTN5"], what="BTNn")
    joy.key(0x120, 0)
    joy.key(0x124, 0)
    pad.key(0x130, 1)
    pad.push((EV_ABS, 0x10, -1), (EV_SYN, SYN_REPORT, 0))
    wait_for(lambda: gp.pressed("LEFT"), what="hat")
    assert gp.pressed("DPAD_LEFT")           # today's config spelling
    assert gp.buttons() & GAMEPAD_BUTTONS["A"]
    assert gp.buttons() & GAMEPAD_BUTTONS["DPAD_LEFT"]
    assert not gp.buttons() & GAMEPAD_BUTTONS["B"]
    assert pad.grab_calls == 0 and joy.grab_calls == 0
    pad.unplug()
    joy.unplug()
    wait_for(lambda: gp.buttons() is None, what="no pad: None")
    assert gp.pressed("A") is False


def test_joystick_buttons_17_to_32_and_numbering_by_place(world, make):
    # The kernel gives a HID joystick's buttons 1-16 BTN_TRIGGER..BTN_DEAD
    # and 17 on BTN_TRIGGER_HAPPY1.. (0x2c0): all 32 must be reachable.
    big = world.add("/dev/input/event22", "32 button joystick",
                    {EV_SYN: [0],
                     EV_KEY: list(range(0x120, 0x130))
                     + list(range(0x2c0, 0x2d0)),
                     EV_ABS: [0, 1]})
    # Buttons are numbered by their place in the device's own list, not by
    # a fixed code offset: a gap does not skip a number.
    sparse = world.add("/dev/input/event23", "two button switch",
                       {EV_SYN: [0], EV_KEY: [0x120, 0x124]})
    b = make()
    gp = b.gamepad()
    wait_for(lambda: gp.buttons() == 0, what="joysticks found")
    big.key(0x2c0, 1)
    wait_for(lambda: gp.pressed_names() == ["BTN17"], what="BTN17")
    big.key(0x2c0, 0)
    big.key(0x2cf, 1)
    wait_for(lambda: gp.pressed("BTN32"), what="BTN32")
    assert gp.buttons() == GAMEPAD_BUTTONS["BTN32"]
    big.key(0x2cf, 0)
    big.key(0x12f, 1)
    wait_for(lambda: gp.pressed_names() == ["BTN16"], what="BTN16")
    big.key(0x12f, 0)
    sparse.key(0x124, 1)
    big.key(0x129, 1)
    # Settings order (as on Windows), not alphabetical ("BTN10" < "BTN2").
    wait_for(lambda: gp.pressed_names() == ["BTN2", "BTN10"], what="by place")
    assert gp.buttons() == GAMEPAD_BUTTONS["BTN2"] | GAMEPAD_BUTTONS["BTN10"]
    sparse.unplug()                     # unplugged mid-press: released
    wait_for(lambda: gp.pressed_names() == ["BTN10"], what="unplug release")


def test_pad_map_names():
    xbox = le._pad_map([0x130, 0x131, 0x133, 0x134, 0x13c, 0x220, 0x2c0])
    assert xbox[0x130] == "A" and xbox[0x134] == "Y" and xbox[0x220] == "UP"
    assert xbox[0x13c] == "BTN1" and xbox[0x2c0] == "BTN2"
    many = le._pad_map(range(0x2c0, 0x2e8))           # 40 trigger-happy codes
    assert many[0x2c0] == "BTN1" and many[0x2df] == "BTN32"
    assert 0x2e0 not in many                          # no BTN33


# --- permissions -------------------------------------------------------------------

_PROC = """\
I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
P: Phys=LNXPWRBN/button/input0
H: Handlers=kbd event0
B: PROP=0
B: EV=3
B: KEY=10000000000000 0

I: Bus=0011 Vendor=0001 Product=0001 Version=ab41
N: Name="AT Translated Set 2 keyboard"
P: Phys=isa0060/serio0/input0
H: Handlers=sysrq kbd leds event3
B: PROP=0
B: EV=120013
B: KEY=402000000 3803078f800d001 feffffdfffefffff fffffffffffffffe
B: MSC=10
B: LED=7

I: Bus=0003 Vendor=045e Product=028e Version=0110
N: Name="Microsoft X-Box 360 pad"
H: Handlers=event7 js0
B: EV=20000b
B: KEY=7cdb000000000000 0 0 0 0

I: Bus=0003 Vendor=0001 Product=0001 Version=0001
N: Name="gozfaresi-klavye"
H: Handlers=sysrq kbd event9
B: EV=3
B: KEY=ffffffff ffffffffffffffff ffffffffffffffff fffffffffffffffe
"""


def test_proc_devices_parse_finds_keyboards_only():
    assert le._keyboard_nodes_in(_PROC) == ["/dev/input/event3"]


def _perm(monkeypatch, nodes, readable, uinput, group="none"):
    monkeypatch.setattr(le, "_keyboard_nodes", lambda: list(nodes))

    def access(path, mode):
        if path == le.UINPUT:
            if mode == os.F_OK:
                return uinput != "missing"
            return uinput == "ok"
        return path in readable
    monkeypatch.setattr(le, "_access", access)
    monkeypatch.setattr(le, "_group_state", lambda: group)


def test_available_needs_the_keyboards_not_every_device(monkeypatch):
    kb = "/dev/input/event3"
    # The power button and the gamepad being unreadable does not matter.
    _perm(monkeypatch, [kb], {kb}, "ok")
    assert le.EvdevBackend.available() is True
    # Steam's rules: uinput and the pads, but not the keyboards.
    _perm(monkeypatch, [kb], {"/dev/input/event7"}, "ok")
    assert le.EvdevBackend.available() is False
    _perm(monkeypatch, [kb], {kb}, "readonly")
    assert le.EvdevBackend.available() is False
    _perm(monkeypatch, [], set(), "ok")
    assert le.EvdevBackend.available() is False


def test_permission_ok_reasons(monkeypatch):
    kb = "/dev/input/event3"
    _perm(monkeypatch, [kb], {kb}, "ok")
    ok, why = le.permission_ok()
    assert ok is True and why

    _perm(monkeypatch, [kb], set(), "readonly", group="none")
    ok, why = le.permission_ok()
    assert ok is False
    assert "input grubu" in why and "İzinleri kur" in why

    _perm(monkeypatch, [kb], set(), "readonly", group="pending")
    ok, why = le.permission_ok()
    assert ok is False and "input grubu" in why
    assert "oturumu kapatıp" in why

    _perm(monkeypatch, [kb], {kb}, "missing")
    ok, why = le.permission_ok()
    assert ok is False and "/dev/uinput" in why and "uinput" in why

    _perm(monkeypatch, [kb], {kb}, "readonly")
    ok, why = le.permission_ok()
    assert ok is False and "/dev/uinput" in why and "input grubu" in why

    _perm(monkeypatch, [], set(), "ok")
    ok, why = le.permission_ok()
    assert ok is False and "klavye" in why.lower()


def test_install_permissions_cmd_from_source(monkeypatch):
    monkeypatch.setattr(le, "_user_name", lambda: "ayse")
    monkeypatch.delattr(sys, "frozen", raising=False)
    argv = le.install_permissions_cmd()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert argv == ["pkexec", os.path.join(here, "linux", "izin-kur.sh"),
                    "ayse"]
    assert os.path.isfile(argv[1])


def test_install_permissions_cmd_frozen_copies_out_of_the_bundle(
        monkeypatch, tmp_path):
    # An AppImage's files live on a FUSE mount root may not read: pkexec
    # gets a copy outside it.
    bundle = tmp_path / "bundle"
    (bundle / "linux").mkdir(parents=True)
    src = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "linux", "izin-kur.sh")
    shutil.copy(src, str(bundle / "linux" / "izin-kur.sh"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(le, "_user_name", lambda: "ayse")
    argv = le.install_permissions_cmd()
    try:
        assert argv[0] == "pkexec" and argv[2] == "ayse"
        assert not argv[1].startswith(str(bundle))
        assert os.path.basename(argv[1]) == "izin-kur.sh"
        with open(argv[1], "rb") as a, open(src, "rb") as b:
            assert a.read() == b.read()
    finally:
        shutil.rmtree(os.path.dirname(argv[1]), ignore_errors=True)


# --- the installer script (Linux only, test mode, stubbed tools) ------------------

_GAZE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_GAZE, "linux", "izin-kur.sh")
_RULES = os.path.join(_GAZE, "linux", "99-gozfaresi.rules")

_STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "$STUB_LOG"
case "$(basename "$0")" in
  id)
    if [ "$1" = "-nG" ]; then
      if [ -f "$STUB_STATE/member" ]; then echo "$2 input"; else echo "$2"; fi
    fi
    exit 0 ;;
  usermod|gpasswd) touch "$STUB_STATE/member" ;;
  udevadm)
    if [ "$1" = "--version" ]; then echo "255 (255.4-1ubuntu8)"; fi
    if [ "$1" = "verify" ] && [ -f "$STUB_STATE/bad_rule" ]; then exit 1; fi ;;
esac
exit 0
"""


@pytest.fixture
def script_env(tmp_path):
    if os.name == "nt" or shutil.which("sh") is None:
        pytest.skip("the installer is a Linux shell script")
    root = tmp_path / "root"
    (root / "etc" / "udev" / "rules.d").mkdir(parents=True)
    (root / "dev").mkdir()
    (root / "dev" / "uinput").write_text("")
    other = root / "etc" / "udev" / "rules.d" / "50-other.rules"
    other.write_text('KERNEL=="foo", MODE="0600"\n')
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for tool in ("id", "usermod", "gpasswd", "modprobe", "udevadm",
                 "getent", "groupadd", "chgrp"):
        p = stubs / tool
        p.write_text(_STUB)
        p.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "log"
    env = dict(os.environ, GOZFARESI_IZIN_ROOT=str(root),
               PATH=str(stubs) + os.pathsep + os.environ.get("PATH", ""),
               STUB_LOG=str(log), STUB_STATE=str(state))

    def run(user="ayse"):
        return subprocess.run(["sh", _SCRIPT, user], env=env,
                              capture_output=True, text=True, timeout=30)
    return types.SimpleNamespace(root=root, other=other, log=log,
                                 state=state, run=run)


def _calls(env, tool):
    if not env.log.exists():
        return []
    return [line for line in env.log.read_text().splitlines()
            if line.split(" ", 1)[0] == tool]


def test_installer_script_installs_and_is_idempotent(script_env):
    env = script_env
    r = env.run()
    assert r.returncode == 0, r.stdout + r.stderr
    rules = env.root / "etc" / "udev" / "rules.d" / "99-gozfaresi.rules"
    with open(_RULES, encoding="utf-8") as f:
        assert rules.read_text() == f.read()
    assert ('KERNEL=="uinput", GROUP="input", MODE="0660", '
            'OPTIONS+="static_node=uinput"') in rules.read_text()
    conf = env.root / "etc" / "modules-load.d" / "gozfaresi.conf"
    assert conf.read_text().split() == ["uinput"]
    assert _calls(env, "usermod") == ["usermod -aG input ayse"]
    assert _calls(env, "modprobe") == ["modprobe uinput"]
    assert any("--reload-rules" in c for c in _calls(env, "udevadm"))
    before = rules.stat().st_mtime_ns, conf.stat().st_mtime_ns

    r = env.run()                    # again: nothing to do, still exit 0
    assert r.returncode == 0, r.stdout + r.stderr
    assert _calls(env, "usermod") == ["usermod -aG input ayse"]
    assert (rules.stat().st_mtime_ns, conf.stat().st_mtime_ns) == before
    assert env.other.read_text() == 'KERNEL=="foo", MODE="0600"\n'
    leftovers = [p.name for p in rules.parent.iterdir()]
    assert sorted(leftovers) == ["50-other.rules", "99-gozfaresi.rules"]


def test_installer_script_refuses_a_rule_that_fails_verification(script_env):
    env = script_env
    (env.state / "bad_rule").write_text("")
    r = env.run()
    assert r.returncode != 0
    d = env.root / "etc" / "udev" / "rules.d"
    assert sorted(p.name for p in d.iterdir()) == ["50-other.rules"]
    assert not any("--reload-rules" in c for c in _calls(env, "udevadm"))


def test_installer_script_rejects_bad_user_names(script_env):
    env = script_env
    for bad in ("", "root", "-oops", "a b"):
        r = env.run(bad)
        assert r.returncode != 0, bad
    assert _calls(env, "usermod") == []


def test_rules_file_matches_the_brief():
    with open(_RULES, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f
                 if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines == ['KERNEL=="uinput", GROUP="input", MODE="0660", '
                     'OPTIONS+="static_node=uinput"']
