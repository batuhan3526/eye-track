# gaze/tests/test_linux_x11.py
"""The X11 backend against a real X server (Xvfb, inside the Linux Docker
image - packaging/linux/test.sh). Skipped on Windows.

Keys come from a SEPARATE process, the way a foot switch's or an on-screen
keyboard's keys reach a real session; the observing window is a Tk window of
this process that has the input focus. Two senders, because they differ:

  * xdotool - XTest, like an on-screen keyboard or an AAC tool;
  * a "real keyboard" - Xvfb's own slave keyboard device, driven through
    XTestFakeDeviceKeyEvent (_PHYS below). The server keeps its key state
    apart from XTest's, as for a USB keyboard or foot switch: a re-sent copy
    of a press that works with xdotool is dropped for this one.

Only F13+ keys and a dedicated Xvfb are used; nothing reaches a desktop.
"""
import atexit
import os
import shutil
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("Xvfb") is None
    or shutil.which("xdotool") is None,
    reason="needs Linux with Xvfb and xdotool (Docker: packaging/linux/test.sh)")

W, H = 1600, 900
F13_KEY = 183 + 8        # X keycode of the physical F13 key (evdev + 8)
A_KEY = 30 + 8
SPACE_KEY = 57 + 8

# Presses keys on Xvfb's own keyboard device: argv = display, "+code"/"-code".
_PHYS = r"""
import sys
from Xlib import X, display
from Xlib.ext import xinput, xtest
from Xlib.protocol import rq


class DeviceFakeInput(rq.Request):
    # XTestFakeInput with an XInput 1 device event: the device id is the
    # event's last byte.
    _request = rq.Struct(
        rq.Card8("opcode"), rq.Opcode(2), rq.RequestLength(),
        rq.Card8("event_type"), rq.Card8("detail"), rq.Pad(2),
        rq.Card32("time"), rq.Window("root", (X.NONE,)), rq.Pad(8),
        rq.Int16("x"), rq.Int16("y"), rq.Pad(7), rq.Card8("deviceid"))


d = display.Display(sys.argv[1])
kbd = [dv.deviceid for dv in d.xinput_query_device(xinput.AllDevices).devices
       if dv.name.endswith("keyboard") and "XTEST" not in dv.name
       and not dv.name.startswith("Virtual core")][0]
base = d.query_extension("XInputExtension").first_event
for step in sys.argv[2:]:
    DeviceFakeInput(display=d.display,
                    opcode=d.display.get_extension_major(xtest.extname),
                    event_type=base + (1 if step[0] == "+" else 2),
                    detail=int(step[1:]), time=0, root=X.NONE, x=0, y=0,
                    deviceid=kbd)
    d.sync()
d.close()
"""


def _free_display():
    for n in range(99, 140):
        if not os.path.exists("/tmp/.X%d-lock" % n) and \
                not os.path.exists("/tmp/.X11-unix/X%d" % n):
            return ":%d" % n
    pytest.skip("no free X display number")


def _start_xvfb(*extra):
    """(display name, process) of a new Xvfb, ready for clients."""
    from Xlib import display as xdisplay
    name = _free_display()
    proc = subprocess.Popen(
        ["Xvfb", name, "-screen", "0", "%dx%dx24" % (W, H), "-nolisten", "tcp"]
        + list(extra),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    while True:
        try:
            xdisplay.Display(name).close()
            return name, proc
        except Exception:
            if proc.poll() is not None or time.time() > deadline:
                proc.kill()
                pytest.fail("Xvfb %s did not start" % name)
            time.sleep(0.05)


def _stop_xvfb(proc):
    proc.terminate()
    try:
        proc.wait(5)
    except Exception:
        proc.kill()


@pytest.fixture(scope="module")
def xvfb():
    """A dedicated Xvfb of a known size: the xvfb-run display of the suite
    is 1280x1024 and shared with every other test."""
    pytest.importorskip("Xlib")
    name, proc = _start_xvfb()
    # Stopped when the test process ends, not with this module: the
    # `window` fixture's Tk keeps its connection to this server after
    # root.destroy() (Tk closes displays only at exit), and Tk's event
    # loop watches that socket for good. Killed here, the next Tk test in
    # the process - any module - died with "XIO: fatal IO error ... on X
    # server" for this display (Task 16: the whole Linux suite aborted at
    # test_ui_wizard_flow).
    atexit.register(_stop_xvfb, proc)
    yield name


@pytest.fixture
def backend(xvfb):
    from eye_gaze.platform.linux_x11 import X11Backend
    b = X11Backend(display_name=xvfb)
    yield b
    b.close()


@pytest.fixture
def window(xvfb):
    """A focused Tk window at a known place that records what reaches it."""
    import tkinter
    root = tkinter.Tk(screenName=xvfb)
    root.title("Göz Faresi deneme")
    root.geometry("200x150+100+100")
    got = []        # (type, keysym, char, keycode)
    for seq in ("<KeyPress>", "<KeyRelease>"):
        root.bind(seq, lambda e: got.append((e.type.name, e.keysym, e.char,
                                             e.keycode)))
    for n in (1, 3, 4, 5):
        root.bind("<Button-%d>" % n,
                  lambda e, n=n: got.append(("Button", n, "", 0)))
    root.update()
    root.focus_force()
    _pump(root, 0.2)
    root.got = got
    yield root
    root.destroy()


def _pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def _xdotool(display, *args):
    env = dict(os.environ, DISPLAY=display)
    subprocess.run(["xdotool"] + list(args), env=env, check=True, timeout=10)


def _phys(display, *steps):
    env = dict(os.environ, DISPLAY=display)
    subprocess.run([sys.executable, "-c", _PHYS, display] + list(steps),
                   env=env, check=True, timeout=20)


def _keys(got, keysym):
    return [g[0] for g in got if g[1] == keysym]


def _pressed(got):
    return [g for g in got if g[0] == "KeyPress"]


def _codes(got, code):
    return [g[0] for g in got if g[3] == code and g[0] != "Button"]


def _until(root, cond, seconds=2.0):
    end = time.time() + seconds
    while time.time() < end and not cond():
        root.update()
        time.sleep(0.01)
    _pump(root, 0.2)     # and nothing more arrives afterwards


# --- screens and cursor ---------------------------------------------------------

def test_screens_reports_xvfb_size(backend):
    assert backend.screens() == [(0, 0, W, H)]
    assert backend.primary() == (0, 0, W, H)


def test_set_get_cursor_roundtrip(backend):
    backend.set_cursor(123, 456)
    assert backend.get_cursor() == (123, 456)
    backend.set_cursor(W - 1, H - 1)
    assert backend.get_cursor() == (W - 1, H - 1)


def test_left_click_reaches_window(backend, window):
    backend.set_cursor(150, 150)          # inside the window (100..300)
    backend.button("left", True)
    backend.button("left", False)
    backend.button("right", True)
    backend.button("right", False)
    _until(window, lambda: len(window.got) >= 2)
    assert ("Button", 1, "", 0) in window.got
    assert ("Button", 3, "", 0) in window.got


def test_scroll_is_wheel_buttons_4_and_5(backend, window):
    backend.set_cursor(150, 150)
    backend.scroll(120)                    # one notch up (Windows wheel units)
    backend.scroll(-240)                   # two notches down
    _until(window, lambda: len(window.got) >= 3)
    buttons = [g[1] for g in window.got if g[0] == "Button"]
    assert buttons == [4, 5, 5]


# --- the key hook ---------------------------------------------------------------

def test_grab_key_swallows_and_counts(backend, window, xvfb):
    seen = []

    def cb(e):
        seen.append((e.event_type, e.name, e.injected))
        return False                        # swallow
    remove = backend.keyboard.hook_key("f13", cb, suppress=True)
    _xdotool(xvfb, "key", "F13")
    _until(window, lambda: len(seen) >= 2)
    # xdotool types through XTest: stamped injected (XInput2 raw events).
    assert seen == [("down", "f13", True), ("up", "f13", True)]
    assert _keys(window.got, "F13") == []          # the focused window: nothing

    # Unhooked, the key belongs to the applications again. (xdotool binds
    # F13 to a spare keycode only for the moment it sends it, and Tk reads
    # the mapping later: the keysym may arrive as "??", the press arrives.)
    remove()
    _xdotool(xvfb, "key", "F13")
    _until(window, lambda: _pressed(window.got))
    assert _pressed(window.got)
    assert len(seen) == 2


@pytest.mark.parametrize("suppress", [True, False])
def test_passthrough_replays_once_without_double_count(backend, window, xvfb,
                                                       suppress):
    seen = []

    def cb(e):
        seen.append((e.event_type, e.injected))
        return True                         # let it through
    backend.keyboard.hook_key("f14", cb, suppress=suppress)
    _xdotool(xvfb, "key", "F14")
    _until(window, lambda: len(_keys(window.got, "F14")) >= 2
           and len(seen) >= 2)
    # Our own replay never comes back as a second press.
    assert seen == [("down", True), ("up", True)]          # xdotool: XTest
    assert _keys(window.got, "F14") == ["KeyPress", "KeyRelease"]


def test_held_passthrough_key_reports_its_release(backend, window, xvfb):
    # Passed through, the release goes straight to the application: the hook
    # must still hear it, or the switch stays "held" and never counts again.
    seen = []
    backend.keyboard.hook_key("f15", lambda e: seen.append(e.event_type)
                              or True, suppress=True)
    _xdotool(xvfb, "keydown", "F15")
    _until(window, lambda: _keys(window.got, "F15"), seconds=1.0)
    _xdotool(xvfb, "keyup", "F15")
    _until(window, lambda: len(seen) >= 2)
    assert seen == ["down", "up"]
    assert _keys(window.got, "F15") == ["KeyPress", "KeyRelease"]


def test_other_keys_still_type_while_a_swallowed_key_is_held(backend, window,
                                                             xvfb):
    # A held grabbed key grabs the whole keyboard; the person (or the
    # on-screen keyboard) typing meanwhile must not be eaten.
    seen = []
    backend.keyboard.hook_key("f16", lambda e: seen.append(e.event_type)
                              and False, suppress=True)
    _xdotool(xvfb, "keydown", "F16")
    _until(window, lambda: seen, seconds=1.0)
    _xdotool(xvfb, "key", "a")
    _until(window, lambda: _keys(window.got, "a"), seconds=1.0)
    _xdotool(xvfb, "keyup", "F16")
    _until(window, lambda: len(seen) >= 2)
    assert seen == ["down", "up"]
    assert "KeyPress" in _keys(window.got, "a")
    assert "KeyPress" not in _keys(window.got, "F16")


def test_two_hooks_on_one_key_swallow_if_either_says_so(backend, window, xvfb):
    a, b = [], []
    backend.keyboard.hook_key("f17", lambda e: a.append(1) or True,
                              suppress=True)
    backend.keyboard.hook_key("f17", lambda e: b.append(1) and False,
                              suppress=True)
    _xdotool(xvfb, "key", "F17")
    _until(window, lambda: len(a) >= 2 and len(b) >= 2)
    assert len(a) == 2 and len(b) == 2
    assert _keys(window.got, "F17") == []


# --- a real keyboard (Xvfb's own device, not XTest) ------------------------------

def test_real_keyboard_passthrough_reaches_app_once(backend, window, xvfb):
    seen = []
    backend.keyboard.hook_key("f13", lambda e: seen.append(e.event_type)
                              or True, suppress=True)
    _phys(xvfb, "+%d" % F13_KEY, "-%d" % F13_KEY)
    _until(window, lambda: len(_codes(window.got, F13_KEY)) >= 2
           and len(seen) >= 2)
    assert seen == ["down", "up"]
    assert _codes(window.got, F13_KEY) == ["KeyPress", "KeyRelease"]


def test_real_keyboard_swallowed(backend, window, xvfb):
    seen = []
    backend.keyboard.hook_key("f13", lambda e: seen.append(e.event_type)
                              and False, suppress=True)
    _phys(xvfb, "+%d" % F13_KEY, "-%d" % F13_KEY)
    _until(window, lambda: len(seen) >= 2)
    assert seen == ["down", "up"]
    assert _codes(window.got, F13_KEY) == []


def test_real_keyboard_held_passthrough(backend, window, xvfb):
    seen = []
    backend.keyboard.hook_key("f13", lambda e: seen.append(e.event_type)
                              or True, suppress=True)
    _phys(xvfb, "+%d" % F13_KEY)
    _until(window, lambda: _codes(window.got, F13_KEY), seconds=1.0)
    assert seen == ["down"]
    assert backend.keyboard.is_pressed("f13") is True
    _phys(xvfb, "-%d" % F13_KEY)
    _until(window, lambda: len(seen) >= 2)
    assert seen == ["down", "up"]
    assert _codes(window.got, F13_KEY) == ["KeyPress", "KeyRelease"]
    assert backend.keyboard.is_pressed("f13") is False


def test_real_keyboard_typing_while_the_switch_is_held(backend, window, xvfb):
    seen = []
    backend.keyboard.hook_key("f13", lambda e: seen.append(e.event_type)
                              and False, suppress=True)
    _phys(xvfb, "+%d" % F13_KEY)
    _until(window, lambda: seen, seconds=1.0)
    _phys(xvfb, "+%d" % A_KEY, "-%d" % A_KEY)
    _until(window, lambda: len(_codes(window.got, A_KEY)) >= 2, seconds=1.0)
    _phys(xvfb, "-%d" % F13_KEY)
    _until(window, lambda: len(seen) >= 2)
    assert seen == ["down", "up"]
    assert _codes(window.got, A_KEY) == ["KeyPress", "KeyRelease"]
    # Its press never reached the window; only the release, unpaired.
    assert "KeyPress" not in _codes(window.got, F13_KEY)


@pytest.mark.parametrize("sender", ["real", "xdotool"])
def test_auto_repeat_is_not_a_release(backend, window, xvfb, sender):
    # A held key auto-repeats as release + press pairs: the hook must hear
    # one long hold (repeated downs, ONE up), or a resting finger clicks
    # again and again.
    seen = []
    backend.keyboard.hook_key("f13", lambda e: seen.append(e.event_type)
                              and False, suppress=True)
    if sender == "real":
        _phys(xvfb, "+%d" % F13_KEY)
    else:
        _xdotool(xvfb, "keydown", "F13")
    _pump(window, 1.5)                      # past the 660 ms repeat delay
    if sender == "real":
        _phys(xvfb, "-%d" % F13_KEY)
    else:
        _xdotool(xvfb, "keyup", "F13")
    _until(window, lambda: seen and seen[-1] == "up")
    assert seen[0] == "down" and seen[-1] == "up"
    assert seen.count("up") == 1, seen
    assert seen.count("down") > 1, seen    # it did repeat
    assert _codes(window.got, F13_KEY) == []


def test_auto_repeat_of_a_passed_key_reaches_the_app(backend, window, xvfb):
    # Paused (caregiver mode) a held key must repeat in the application as
    # usual, while the hook still hears a single release at the end.
    seen = []
    backend.keyboard.hook_key("f13", lambda e: seen.append(e.event_type)
                              or True, suppress=True)
    _phys(xvfb, "+%d" % F13_KEY)
    _pump(window, 1.5)
    _phys(xvfb, "-%d" % F13_KEY)
    _until(window, lambda: seen and seen[-1] == "up")
    got = _codes(window.got, F13_KEY)
    assert seen.count("up") == 1, (",".join(seen), got)
    assert got.count("KeyPress") > 1 and got[-1] == "KeyRelease", got


def test_key_taken_by_another_program_raises(backend, xvfb):
    # Another program's grab (a desktop shortcut): the hook fails loudly and
    # KeyPresses lists the key in bad_keys, instead of a silent dead key.
    from Xlib import X, display as xdisplay
    other = xdisplay.Display(xvfb)
    try:
        code = 191 + 8                                  # physical F21
        for mask in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
            other.screen().root.grab_key(code, mask, False, X.GrabModeAsync,
                                         X.GrabModeAsync)
        other.sync()
        with pytest.raises(OSError):
            backend.keyboard.hook_key("f21", lambda e: True, suppress=True)
        from eye_gaze.switch import KeyPresses
        kp = KeyPresses(["f21", "f22"], kb=backend.keyboard)
        assert kp.bad_keys == ["f21"]
        kp.close()
    finally:
        other.close()


# --- injected keys, the watchdog, a dead key thread ------------------------------

def test_injected_keys_pass_and_real_keys_count(backend, window, xvfb,
                                                 monkeypatch):
    # The click loop: the on-screen keyboard's Space (XTest) must reach the
    # application and never count as a switch press; the real Space counts
    # and is swallowed.
    from eye_gaze import injected
    from eye_gaze.switch import KeyPresses
    monkeypatch.setattr(injected, "enabled", True)    # ignore_injected_keys
    kp = KeyPresses(["space"], kb=backend.keyboard)
    try:
        _xdotool(xvfb, "key", "space")
        _until(window, lambda: len(_keys(window.got, "space")) >= 2)
        assert _keys(window.got, "space") == ["KeyPress", "KeyRelease"]
        assert kp.take("space") is False
        del window.got[:]

        _phys(xvfb, "+%d" % SPACE_KEY, "-%d" % SPACE_KEY)
        deadline = time.time() + 2
        while time.time() < deadline and kp.held("space") is False and \
                not kp._count.get("space"):
            window.update()
            time.sleep(0.01)
        _pump(window, 0.3)
        assert kp.take("space") is True
        assert _codes(window.got, SPACE_KEY) == []
    finally:
        kp.close()


def test_osk_space_while_the_switch_is_held_keeps_the_next_press(
        backend, window, xvfb, monkeypatch):
    # The switch (real Space) is held; onboard types Space meanwhile. The
    # server drops onboard's press (Space is already down) but lets its
    # release through - that release ends the real hold, and the real
    # release that follows is dropped. The hook must hear it as THE release
    # of the real press, or the next real press is taken for a repeat.
    from eye_gaze import injected
    from eye_gaze.switch import KeyPresses
    monkeypatch.setattr(injected, "enabled", True)
    kp = KeyPresses(["space"], kb=backend.keyboard)
    try:
        _phys(xvfb, "+%d" % SPACE_KEY)
        _until(window, lambda: kp._count.get("space"), seconds=1.0)
        _xdotool(xvfb, "key", "space")
        _pump(window, 0.2)
        _phys(xvfb, "-%d" % SPACE_KEY)
        _pump(window, 0.2)
        _phys(xvfb, "+%d" % SPACE_KEY, "-%d" % SPACE_KEY)
        _until(window, lambda: kp._count.get("space", 0) >= 2)
        assert kp.take("space") is True
        assert kp.take("space") is True
        assert kp.held("space") is False
    finally:
        kp.close()


def test_injected_keys_count_when_ignoring_is_off(backend, window, xvfb,
                                                   monkeypatch):
    # ignore_injected_keys: false (end-to-end smokes send keys by XTest).
    from eye_gaze import injected
    from eye_gaze.switch import KeyPresses
    monkeypatch.setattr(injected, "enabled", False)
    kp = KeyPresses(["f13"], kb=backend.keyboard)
    try:
        _xdotool(xvfb, "key", "F13")
        _until(window, lambda: kp._count.get("f13"))
        assert kp.take("f13") is True
        assert _keys(window.got, "F13") == []
    finally:
        kp.close()


def test_injected_detection_capability(backend):
    backend.keyboard.hook_key("f20", lambda e: True)
    ok, why = backend.capabilities()["injected_detection"]
    assert ok is True and "XInput2" in why
    backend._xi2_reason = "X sunucusunda XInput 2.1 yok"     # older server
    ok, why = backend.capabilities()["injected_detection"]
    assert ok is False and "XInput 2.1" in why


F20_KEY = 190 + 8


def _wait(cond, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline and not cond():
        time.sleep(0.02)
    return cond()


def _repeat_limit(kb):
    """Seconds after which the server auto-repeats a held key."""
    from eye_gaze.platform import linux_x11
    return kb.repeat_delay or linux_x11._SERVER_REPEAT_DELAY


def test_freeze_budget_is_under_the_servers_repeat_delay(backend, xvfb):
    # The watchdog must thaw the keyboard before a held key's first
    # auto-repeat: one fired into a frozen keyboard can crash the X server.
    from eye_gaze.platform import linux_x11
    kb = backend.keyboard
    kb.hook_key("f20", lambda e: True)               # connected
    assert kb.repeat_delay == pytest.approx(0.66)    # Xvfb's default
    assert kb.freeze_budget == pytest.approx(0.33)
    name, proc = _start_xvfb("-ardelay", "200", "-arinterval", "30")
    try:
        assert linux_x11._repeat_delay(name) == pytest.approx(0.2)
    finally:
        _stop_xvfb(proc)
    budget = linux_x11._freeze_budget
    assert budget(0.2) == pytest.approx(0.1)
    assert budget(0.5) == pytest.approx(0.25)        # GNOME's default
    assert budget(None) == pytest.approx(0.33)       # XKB did not say
    # Longer here, but a keyboard plugged in later repeats at the default.
    assert budget(2.0) == pytest.approx(0.33)
    assert budget(0.05) == linux_x11._WATCHDOG_MIN


def test_watchdog_cuts_a_callback_that_blocks(backend, window, xvfb, capsys):
    # A callback that blocks keeps the whole keyboard frozen (synchronous
    # grab). The watchdog cuts our connection: the person's typing reaches
    # the application again long before the callback returns - and a few
    # seconds later the keys are ours again.
    calls = []
    entered = []

    def stuck(e):
        calls.append((e.event_type, time.time()))
        if len(calls) == 1:
            entered.append(time.monotonic())
            # Long: the re-arm (0.75 s after a cut) waits for this to return,
            # and the test checks the keys are the application's until then.
            time.sleep(4.0)
        return False
    backend.keyboard.hook_key("f13", stuck, suppress=True)
    _phys(xvfb, "+%d" % F13_KEY)
    _until(window, lambda: calls, seconds=1.0)
    started = calls[0][1]
    _phys(xvfb, "+%d" % A_KEY, "-%d" % A_KEY, "-%d" % F13_KEY)
    _until(window, lambda: "KeyPress" in _codes(window.got, A_KEY),
           seconds=3.0)
    assert "KeyPress" in _codes(window.got, A_KEY)
    arrived = time.time() - started
    assert arrived < 2.2, arrived          # the watchdog, not the callback
    assert backend.keyboard.alive() is False
    # Cut before the held F13's first auto-repeat could reach the frozen
    # keyboard (the X server crash in linux_x11's Safety notes).
    cut = backend.keyboard._dead_since - entered[0]
    assert cut < _repeat_limit(backend.keyboard), cut
    caps = backend.capabilities()
    assert caps["global_keys"][0] is False
    assert "yeniden kurulacak" in caps["global_keys"][1]
    # Cut: F13 is the application's until the re-arm.
    _phys(xvfb, "+%d" % F13_KEY, "-%d" % F13_KEY)
    _until(window, lambda: "KeyPress" in _codes(window.got, F13_KEY))
    assert "KeyPress" in _codes(window.got, F13_KEY)
    with pytest.raises(RuntimeError):
        backend.keyboard.hook_key("f14", lambda e: True)

    # Re-armed after the cooldown: the same hook counts a real press again.
    assert _wait(backend.keyboard.alive, 8.0)
    del window.got[:]
    n = len(calls)
    _phys(xvfb, "+%d" % F13_KEY, "-%d" % F13_KEY)
    _until(window, lambda: len(calls) >= n + 2)
    assert [k for k, _ in calls[n:]] == ["down", "up"]
    assert _codes(window.got, F13_KEY) == []          # swallowed again
    assert "global_keys" not in backend.capabilities()   # no "dead" any more
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("[x11]")]
    assert len(lines) == 2, out              # the cut, the re-arm
    assert "yeniden kuruldu" in lines[1]
    out.encode("ascii")


def test_dead_key_thread_is_said_and_comes_back(backend, xvfb, capsys):
    kb = backend.keyboard
    seen = []
    kb.hook_key("f20", lambda e: seen.append(e.event_type) and False,
                suppress=True)
    assert kb.alive() is True
    real_poll = kb._poll_releases

    def boom_once():
        kb._poll_releases = real_poll
        raise RuntimeError("boom")
    kb._poll_releases = boom_once         # the thread's loop raises once
    kb._wake()
    assert _wait(lambda: not kb.alive(), 3.0)
    assert "boom" in kb.error
    assert "boom" in backend.capabilities()["suppress_keys"][1]
    with pytest.raises(RuntimeError):
        kb.hook_key("f21", lambda e: True)
    assert _wait(kb.alive, 8.0)
    _phys(xvfb, "+%d" % F20_KEY, "-%d" % F20_KEY)
    assert _wait(lambda: len(seen) >= 2, 3.0)
    assert seen == ["down", "up"]
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("[x11]")]
    assert len(lines) == 2 and "boom" in lines[0], out


def test_key_thread_gives_up_after_five_rearms(backend, capsys, monkeypatch):
    from eye_gaze.platform import linux_x11
    monkeypatch.setattr(linux_x11, "_REARM_COOLDOWN", 0.05)
    kb = backend.keyboard
    kb.hook_key("f20", lambda e: True)

    def boom():
        raise RuntimeError("boom")
    kb._poll_releases = boom              # every connection fails
    kb._wake()
    assert _wait(lambda: not kb._thread.is_alive(), 10.0)
    assert kb.alive() is False and kb.gave_up is True
    ok, why = backend.capabilities()["global_keys"]
    assert ok is False and "yeniden başlatın" in why
    out = capsys.readouterr().out
    assert out.count("yeniden kuruldu (") == 5, out
    assert out.count("birakildi") == 1, out
    with pytest.raises(RuntimeError):
        kb.hook_key("f21", lambda e: True)


def test_removed_hook_stays_removed_across_a_rearm(backend, xvfb):
    kb = backend.keyboard
    seen = []
    remove = kb.hook_key("f20", lambda e: seen.append(1) and False,
                         suppress=True)
    real_poll = kb._poll_releases

    def boom_once():
        kb._poll_releases = real_poll
        raise RuntimeError("boom")
    kb._poll_releases = boom_once
    kb._wake()
    assert _wait(lambda: not kb.alive(), 3.0)
    remove()                               # while the listener is down
    assert _wait(kb.alive, 8.0)
    _phys(xvfb, "+%d" % F20_KEY, "-%d" % F20_KEY)
    time.sleep(0.5)
    assert seen == []


def _kill_once(kb):
    """Make the key thread's loop raise once (it then re-arms)."""
    real_poll = kb._poll_releases

    def boom_once():
        kb._poll_releases = real_poll
        raise RuntimeError("boom")
    kb._poll_releases = boom_once
    kb._wake()


def test_key_held_at_the_cut_does_not_eat_the_next_press(backend, xvfb):
    # The switch is held when the listener dies; its release goes to nobody.
    # After the re-arm the next real press must still count.
    from eye_gaze.switch import KeyPresses
    kb = backend.keyboard
    kp = KeyPresses(["f20"], kb=kb)
    try:
        _phys(xvfb, "+%d" % F20_KEY)
        assert _wait(lambda: kp._count.get("f20"), 2.0)
        assert kp.take("f20") is True
        _kill_once(kb)
        assert _wait(lambda: not kb.alive(), 3.0)
        _phys(xvfb, "-%d" % F20_KEY)            # while the listener is down
        # Heard at once, not at the re-arm: the alarm reads "held", and a
        # release seen 3 s late is a long hold that never happened.
        assert _wait(lambda: not kp.held("f20"), 1.0)
        assert kb.alive() is False
        assert _wait(kb.alive, 8.0)
        _phys(xvfb, "+%d" % F20_KEY, "-%d" % F20_KEY)
        assert _wait(lambda: kp._count.get("f20"), 3.0)
        assert kp.take("f20") is True
        assert _wait(lambda: not kp.held("f20"), 2.0)
    finally:
        kp.close()


def test_key_held_across_the_rearm_is_not_a_new_press(backend, xvfb):
    # The switch is held through the cut and the whole re-arm (the alarm's
    # long hold). The server's next auto-repeat press hits the new grab: it
    # is the same hold, never a click; the release ends it.
    from eye_gaze.switch import KeyPresses
    kb = backend.keyboard
    kp = KeyPresses(["f20"], kb=kb)
    seen = []
    kb.hook_key("f20", lambda e: seen.append(e.event_type))
    try:
        _phys(xvfb, "+%d" % F20_KEY)
        assert _wait(lambda: kp._count.get("f20"), 2.0)
        assert kp.take("f20") is True
        _kill_once(kb)
        assert _wait(lambda: not kb.alive(), 3.0)
        assert _wait(kb.alive, 8.0)
        n = len(seen)
        time.sleep(1.0)                      # auto-repeat reaches the new grab
        assert "down" in seen[n:], seen      # it did
        assert kp.take("f20") is False
        assert kp.held("f20") is True
        _phys(xvfb, "-%d" % F20_KEY)
        assert _wait(lambda: not kp.held("f20"), 2.0)
        assert kp.take("f20") is False
        _phys(xvfb, "+%d" % F20_KEY, "-%d" % F20_KEY)
        assert _wait(lambda: kp._count.get("f20"), 3.0)
        assert kp.take("f20") is True
    finally:
        kp.close()
        _phys(xvfb, "-%d" % F20_KEY)


F19_KEY = 189 + 8


def test_failed_rearm_step_is_retried_and_said(backend, xvfb, capsys,
                                               monkeypatch):
    # An X error while the re-arm drops a hook removed meanwhile must not
    # kill the key thread silently: it is said and the re-arm tried again.
    from eye_gaze.platform import linux_x11
    monkeypatch.setattr(linux_x11, "_REARM_COOLDOWN", 0.3)
    kb = backend.keyboard
    seen, gone = [], []
    kb.hook_key("f20", lambda e: seen.append(e.event_type) and False,
                suppress=True)
    remove = kb.hook_key("f19", lambda e: gone.append(1) and False,
                         suppress=True)
    real_reattach, real_unhook = kb._reattach_hooks, kb._do_unhook

    def reattach_then_remove():
        kb._reattach_hooks = real_reattach
        real_reattach()
        remove()                    # late: the re-arm's last step drops it

    def unhook_fails_once(hid):
        kb._do_unhook = real_unhook
        raise OSError("X error while unhooking")
    kb._reattach_hooks = reattach_then_remove
    kb._do_unhook = unhook_fails_once
    _kill_once(kb)
    assert _wait(lambda: not kb.alive(), 3.0)
    assert _wait(kb.alive, 8.0)
    assert kb._thread.is_alive() and kb.gave_up is False
    _phys(xvfb, "+%d" % F20_KEY, "-%d" % F20_KEY,
          "+%d" % F19_KEY, "-%d" % F19_KEY)
    assert _wait(lambda: len(seen) >= 2, 3.0)
    time.sleep(0.3)
    assert seen == ["down", "up"]
    assert gone == []
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith("[x11]")]
    assert len(lines) == 3, out          # the death, the failed try, the re-arm
    assert "X error while unhooking" in lines[1]
    assert "yeniden kuruldu" in lines[2]


def test_unhook_all_during_the_reattach_removes_every_hook(backend, xvfb):
    # unhook_all() from another thread while the re-arm grabs the hooks
    # again must see all of them, not the ones grabbed so far.
    kb = backend.keyboard
    seen = []
    kb.hook_key("f19", lambda e: seen.append(19) and False, suppress=True)
    kb.hook_key("f20", lambda e: seen.append(20) and False, suppress=True)
    real_attach = kb._attach

    def attach(*args, **kwargs):
        kb._attach = real_attach
        kb.unhook_all()                     # mid re-attach, listener dead
        return real_attach(*args, **kwargs)
    kb._attach = attach
    _kill_once(kb)
    assert _wait(lambda: not kb.alive(), 3.0)
    assert _wait(kb.alive, 8.0)
    _phys(xvfb, "+%d" % F19_KEY, "-%d" % F19_KEY,
          "+%d" % F20_KEY, "-%d" % F20_KEY)
    time.sleep(0.5)
    assert seen == []
    assert not kb._hooks


def test_hook_removed_during_the_rearm_stays_removed(backend, xvfb):
    # remove() between the re-attach and the listener's coming back.
    kb = backend.keyboard
    seen = []
    remove = kb.hook_key("f20", lambda e: seen.append(1) and False,
                         suppress=True)
    real_reattach = kb._reattach_hooks

    def reattach_then_remove():
        real_reattach()
        remove()                              # _dead still True here
    kb._reattach_hooks = reattach_then_remove
    _kill_once(kb)
    assert _wait(lambda: not kb.alive(), 3.0)
    assert _wait(kb.alive, 8.0)
    _phys(xvfb, "+%d" % F20_KEY, "-%d" % F20_KEY)
    time.sleep(0.5)
    assert seen == []
    assert not kb._hooks


def test_watchdog_cuts_a_release_callback_that_blocks(backend, window, xvfb):
    # A blocking callback on the release-poll path does not freeze the
    # keyboard by itself, but the next grabbed press does - nobody answers
    # it. The watchdog cuts then too.
    started = []

    def slow_up(e):
        if e.event_type == "up" and not started:
            started.append(time.time())
            time.sleep(2.5)
        return True                                # passed through
    backend.keyboard.hook_key("f13", slow_up, suppress=True)
    backend.keyboard.hook_key("f14", lambda e: False, suppress=True)
    _phys(xvfb, "+%d" % F13_KEY, "-%d" % F13_KEY)
    assert _wait(lambda: started, 2.0)
    _phys(xvfb, "+%d" % (184 + 8), "-%d" % (184 + 8))     # F14: frozen
    _phys(xvfb, "+%d" % A_KEY, "-%d" % A_KEY)
    _until(window, lambda: "KeyPress" in _codes(window.got, A_KEY),
           seconds=3.0)
    assert "KeyPress" in _codes(window.got, A_KEY)
    assert time.time() - started[0] < 2.3
    assert backend.keyboard.alive() is False


def test_watchdog_sees_a_press_the_poll_already_read(backend, window, xvfb):
    # The release poll's own XQueryKeymap reads every event that came before
    # its reply into python-xlib's queue: a grabbed press waiting there
    # (keyboard frozen) leaves the socket empty. Nothing else is typed here,
    # so only that queue can tell the watchdog.
    #
    # The callback holds the watchdog's poll clock until the press is in that
    # queue (as if the slow callback began only then): before, the watchdog
    # could also find the press on the socket - and cut in the middle of our
    # sync, which then failed. F14 stays held, so the cut must also come
    # before its first auto-repeat (the X server crash in linux_x11's Safety
    # notes - this test's Xvfb died that way, 2026-10-21).
    from Xlib import X
    kb = backend.keyboard
    f14 = 184 + 8
    started = []          # monotonic: callback entered, press in the queue
    in_queue = []
    waited = []
    QUEUE_WAIT = 0.2

    def slow_up(e):
        if e.event_type == "up" and not started:
            started.append(time.monotonic())
            kb._poll_busy_since = None
            _phys(xvfb, "+%d" % f14)                       # frozen
            pressed = time.monotonic()
            kd = kb._kd
            # Capped: F14 is held and the keyboard frozen meanwhile, with no
            # watchdog. Longer than this and an auto-repeat could reach the
            # frozen keyboard - a visible failure here, not a dead Xvfb.
            while time.monotonic() - pressed < QUEUE_WAIT:
                kd.sync()                    # read in, as the poll's reply does
                if any(ev.type == X.KeyPress for ev in kd.display.event_queue):
                    in_queue.append(pressed)
                    break
                time.sleep(0.01)
            waited.append(time.monotonic() - pressed)
            kb._poll_busy_since = time.monotonic()
            started.append(kb._poll_busy_since)
            time.sleep(3.0)
        return True                                        # passed through
    kb.hook_key("f13", slow_up, suppress=True)
    kb.hook_key("f14", lambda e: False, suppress=True)
    try:
        _phys(xvfb, "+%d" % F13_KEY, "-%d" % F13_KEY)
        assert _wait(lambda: len(started) == 2, 4.0)
        if not in_queue:
            pytest.fail("the grabbed F14 press was not in python-xlib's queue "
                        "within %.1f s (waited %.2f s)" % (QUEUE_WAIT, waited[0]))
        assert _wait(lambda: not kb.alive(), 2.0)          # the watchdog cut
        assert time.monotonic() < started[1] + 3.0         # callback not done
        assert kb._dead_since >= started[1]                # from the queue
        cut = kb._dead_since - in_queue[0]
        assert cut < _repeat_limit(kb), cut
        _phys(xvfb, "+%d" % A_KEY, "-%d" % A_KEY)
        _until(window, lambda: "KeyPress" in _codes(window.got, A_KEY))
        assert "KeyPress" in _codes(window.got, A_KEY)
    finally:
        _phys(xvfb, "-%d" % f14)


def test_a_process_wide_stall_is_not_a_cut(backend, xvfb, capsys,
                                           monkeypatch):
    # A stall of the whole process - here one C call that keeps the GIL, as
    # importing or building a model does - starves the watchdog as well. It
    # wakes late and finds the key event "handled" longer than the budget;
    # before the starvation guard it cut, and each such false cut counted
    # toward the permanent give-up (14b review). Now it looks again, the key
    # thread answers, nothing is cut - more stalls than _REARM_MAX included.
    # F13 is pressed and released at once: no key is held through the
    # freeze, so no auto-repeat can reach the frozen keyboard.
    import ctypes
    from eye_gaze.platform import linux_x11
    usleep = ctypes.PyDLL(None).usleep        # PyDLL: the GIL stays held
    usleep.argtypes = [ctypes.c_uint]
    usleep.restype = ctypes.c_int
    kb = backend.keyboard
    stall = 0.6
    stalled = []
    verdicts = []
    real_verdict = linux_x11._watchdog_verdict

    def verdict(*args):
        v = real_verdict(*args)
        verdicts.append(v)
        return v
    monkeypatch.setattr(linux_x11, "_watchdog_verdict", verdict)

    def stalls(e):
        if e.event_type == "down":
            t = time.monotonic()
            usleep(int(stall * 1e6))
            stalled.append(time.monotonic() - t)
        return False                                      # swallowed
    kb.hook_key("f13", stalls, suppress=True)
    budget = kb.freeze_budget
    # Long enough that the watchdog both wakes late and sees a freeze.
    assert stall > budget + linux_x11._watchdog_period(budget) + budget / 2
    for i in range(linux_x11._REARM_MAX + 1):
        _phys(xvfb, "+%d" % F13_KEY, "-%d" % F13_KEY)
        assert _wait(lambda: len(stalled) == i + 1, 3.0), i
        time.sleep(0.3)                     # the watchdog's look after it
        assert kb.alive(), (i, kb.error)
    assert min(stalled) >= stall - 0.01, stalled
    # The GIL was held: the watchdog woke late and looked again instead of
    # cutting (not after every stall - the key thread may get the GIL first
    # and be done before the watchdog looks).
    assert "recheck" in verdicts and "cut" not in verdicts
    assert kb._rearms == [] and not kb.gave_up
    out = capsys.readouterr().out
    assert "[x11]" not in out, out


def test_device_list_failure_keeps_the_xtest_ids(backend, capsys):
    from types import SimpleNamespace
    from eye_gaze.platform import linux_x11
    kb = backend.keyboard
    kb.hook_key("f20", lambda e: True)              # the thread is set up
    before = kb._xtest_ids
    assert before

    def fail(kd):
        raise OSError("XIQueryDevice failed")
    kb._query_xtest = fail
    ev = SimpleNamespace(evtype=linux_x11._xinput.HierarchyChanged)
    kb._on_xi(ev)                                   # a USB switch plugged in
    kb._on_xi(ev)
    assert kb._xtest_ids == before
    out = capsys.readouterr().out
    assert out.count("[x11]") == 1, out


def test_unknown_key_name_raises_value_error(backend):
    with pytest.raises(ValueError):
        backend.keyboard.hook_key("nosuchkey", lambda e: True)


def test_is_pressed_reads_the_keymap(backend, xvfb):
    assert backend.keyboard.is_pressed("pause") is False
    _xdotool(xvfb, "keydown", "Pause")
    try:
        deadline = time.time() + 2
        while time.time() < deadline and not backend.keyboard.is_pressed(
                "pause"):
            time.sleep(0.02)
        assert backend.keyboard.is_pressed("pause") is True
    finally:
        _xdotool(xvfb, "keyup", "Pause")
    assert backend.keyboard.is_pressed("pause") is False
    with pytest.raises(ValueError):
        backend.keyboard.is_pressed("nosuchkey")


def test_send_does_not_hit_our_own_hook(backend, window):
    # A key we send ourselves must reach the application, never our hook
    # (the OSK click loop of 2026-10: our own Space counted as a click).
    seen = []
    backend.keyboard.hook_key("f18", lambda e: seen.append(e) and False,
                              suppress=True)
    backend.keyboard.send("f18")
    backend.keyboard.send("a")
    _until(window, lambda: _keys(window.got, "a") and _keys(window.got, "F18"))
    assert seen == []
    assert _keys(window.got, "F18") == ["KeyPress", "KeyRelease"]
    assert _keys(window.got, "a") == ["KeyPress", "KeyRelease"]


def test_send_combo_holds_the_modifier(backend, window):
    backend.keyboard.send("ctrl+v")
    _until(window, lambda: len(window.got) >= 4)
    order = [(g[0], g[1]) for g in window.got]
    assert order == [("KeyPress", "Control_L"), ("KeyPress", "v"),
                     ("KeyRelease", "v"), ("KeyRelease", "Control_L")]


def test_write_types_turkish_text(backend, window):
    backend.keyboard.write("Aş ı")
    _until(window, lambda: len([1 for g in window.got
                                if g[0] == "KeyPress"]) >= 5)
    chars = "".join(g[2] for g in window.got if g[0] == "KeyPress" and g[2])
    assert chars == "Aş ı"


def test_unhook_all_releases_every_grab(backend, window, xvfb):
    seen = []
    backend.keyboard.hook_key("f19", lambda e: seen.append(e) and False,
                              suppress=True)
    backend.keyboard.unhook_all()
    _xdotool(xvfb, "key", "F19")
    _until(window, lambda: _pressed(window.got))
    assert seen == []
    assert _pressed(window.got)


# --- windows --------------------------------------------------------------------

def test_raise_window_by_title(backend, window, xvfb):
    import tkinter
    other = tkinter.Toplevel(window)
    other.title("üstteki")
    other.geometry("200x150+120+120")
    _pump(window, 0.2)
    assert _top_title(xvfb) == "üstteki"
    assert backend.raise_window("Göz Faresi deneme") is True
    _pump(window, 0.1)
    assert _top_title(xvfb) == "Göz Faresi deneme"
    assert backend.raise_window("böyle bir pencere yok") is False
    other.destroy()


def _top_title(display_name):
    """Title of the topmost named top-level window (no window manager runs,
    so the root's children are the Tk wrappers, bottom to top)."""
    from Xlib import display as xdisplay
    d = xdisplay.Display(display_name)
    try:
        net_name = d.intern_atom("_NET_WM_NAME")
        utf8 = d.intern_atom("UTF8_STRING")
        title = None
        for w in d.screen().root.query_tree().children:
            if w.get_attributes().map_state != 2:      # IsViewable
                continue
            p = w.get_full_property(net_name, utf8)
            if p is not None and p.value:
                v = p.value
                title = v.decode("utf-8") if isinstance(v, bytes) else str(v)
        return title
    finally:
        d.close()


def test_foreground_pid_from_ewmh(backend, xvfb):
    from Xlib import X, Xatom, display as xdisplay
    d = xdisplay.Display(xvfb)
    try:
        root = d.screen().root
        win = root.create_window(0, 0, 10, 10, 0, X.CopyFromParent)
        win.change_property(d.intern_atom("_NET_WM_PID"), Xatom.CARDINAL, 32,
                            [4242])
        active = d.intern_atom("_NET_ACTIVE_WINDOW")
        assert backend.foreground_pid() is None      # no window manager yet
        root.change_property(active, Xatom.WINDOW, 32, [win.id])
        d.sync()
        assert backend.foreground_pid() == 4242
        root.delete_property(active)
        d.sync()
        assert backend.foreground_pid() is None
    finally:
        d.close()


def test_close_is_idempotent(xvfb):
    from eye_gaze.platform.linux_x11 import X11Backend
    b = X11Backend(display_name=xvfb)
    b.keyboard.hook_key("f20", lambda e: True)
    b.close()
    b.close()


def test_gamepad_is_not_this_backends_job(backend):
    assert backend.gamepad() is None
