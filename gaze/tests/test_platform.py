# gaze/tests/test_platform.py
"""The platform layer: which input backend a Linux session gets, what the
program can and cannot do there (said, never silently skipped), and the key
name tables that let one config.yaml work on both systems.

On Windows nothing may change: keyboard_api() is the `keyboard` module itself.
"""
import os
import sys

import pytest

from eye_gaze import platform as pf
from eye_gaze.platform import keys, linux_evdev, linux_x11
# conftest swaps actions.escape for a no-op in every test (it sends a REAL
# Esc on Windows); the real one is taken here, at import.
from eye_gaze.actions import escape as _REAL_ESCAPE


@pytest.fixture
def linux(monkeypatch):
    """Simulate a Linux process with a fresh backend choice."""
    monkeypatch.setattr(pf, "OS", "linux")
    for var in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(pf, "_backend", [None])
    yield monkeypatch


def _fake_x11(monkeypatch):
    """X11Backend without an X server: these tests are about the choice,
    and DISPLAY=":0" exists neither on Windows nor in the Docker run."""
    class Keyboard(pf.NullKeyboard):
        xi_error = None
        error = None
        dead = False
        gave_up = False

        def alive(self):
            return not self.dead

    def init(self, display_name=None):
        self.keyboard = Keyboard()
        self._xi2_reason = None
    monkeypatch.setattr(linux_x11.X11Backend, "__init__", init)


def _permissions(monkeypatch, devices, readable, uinput_writable):
    """`devices` are the keyboards the kernel lists (linux_evdev's own
    seams: os.access itself is left alone)."""
    monkeypatch.setattr(linux_evdev, "_keyboard_nodes", lambda: list(devices))

    def access(path, mode):
        if path == linux_evdev.UINPUT:
            return uinput_writable and bool(mode & os.W_OK)
        if path in devices:
            return readable(path) and mode == os.R_OK
        return False
    monkeypatch.setattr(linux_evdev, "_access", access)


def _fake_evdev(monkeypatch):
    """EvdevBackend without devices or /dev/uinput (the choice is tested
    here; the backend itself in test_linux_evdev.py)."""
    def init(self, evdev_module=None):
        self.keyboard = pf.NullKeyboard()
    monkeypatch.setattr(linux_evdev.EvdevBackend, "__init__", init)


# --- session ------------------------------------------------------------------

def test_session_type_from_env(linux):
    linux.setenv("XDG_SESSION_TYPE", "wayland")
    linux.setenv("WAYLAND_DISPLAY", "wayland-0")
    linux.setenv("DISPLAY", ":0")        # XWayland is there too: still Wayland
    assert pf.session_type() == "wayland"

    linux.setenv("XDG_SESSION_TYPE", "x11")
    linux.delenv("WAYLAND_DISPLAY")
    assert pf.session_type() == "x11"

    linux.delenv("XDG_SESSION_TYPE")
    linux.delenv("DISPLAY")
    assert pf.session_type() == "none"


def test_session_type_startx_reports_tty_but_has_a_display(linux):
    # startx from a text console leaves XDG_SESSION_TYPE=tty.
    linux.setenv("XDG_SESSION_TYPE", "tty")
    linux.setenv("DISPLAY", ":0")
    assert pf.session_type() == "x11"
    linux.delenv("DISPLAY")
    linux.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert pf.session_type() == "wayland"


def test_session_type_on_windows(monkeypatch):
    monkeypatch.setattr(pf, "OS", "windows")
    monkeypatch.setenv("DISPLAY", ":0")
    assert pf.session_type() == "windows"


# --- backend choice -------------------------------------------------------------

def test_backend_choice_order(linux):
    devs = ["/dev/input/event0", "/dev/input/event1"]
    linux.setenv("XDG_SESSION_TYPE", "x11")
    linux.setenv("DISPLAY", ":0")
    _fake_x11(linux)

    _fake_evdev(linux)
    _permissions(linux, devs, lambda p: True, uinput_writable=True)
    assert pf.linux_backend().name == "evdev"

    linux.setattr(pf, "_backend", [None])
    _permissions(linux, devs, lambda p: False, uinput_writable=False)
    assert pf.linux_backend().name == "x11"

    linux.setattr(pf, "_backend", [None])
    linux.setenv("XDG_SESSION_TYPE", "wayland")
    linux.delenv("DISPLAY")
    assert pf.linux_backend().name == "null"


def test_backend_is_a_singleton(linux):
    _permissions(linux, [], lambda p: False, uinput_writable=False)
    assert pf.linux_backend() is pf.linux_backend()


def test_evdev_needs_every_keyboard_readable(linux):
    # Steam's udev rules give the seat user /dev/uinput and the gamepads, but
    # not the keyboards: evdev there would hook nothing. (Which devices are
    # keyboards: test_linux_evdev.py.)
    devs = ["/dev/input/event0", "/dev/input/event7"]
    _permissions(linux, devs, lambda p: p.endswith("7"), uinput_writable=True)
    assert linux_evdev.EvdevBackend.available() is False
    _permissions(linux, devs, lambda p: True, uinput_writable=False)
    assert linux_evdev.EvdevBackend.available() is False
    _permissions(linux, [], lambda p: True, uinput_writable=True)
    assert linux_evdev.EvdevBackend.available() is False
    _permissions(linux, devs, lambda p: True, uinput_writable=True)
    assert linux_evdev.EvdevBackend.available() is True


def test_x11_needs_an_x11_session_and_a_display(linux):
    linux.setenv("XDG_SESSION_TYPE", "x11")
    assert linux_x11.X11Backend.available() is False      # no DISPLAY
    linux.setenv("DISPLAY", ":0")
    assert linux_x11.X11Backend.available() is True
    linux.setenv("XDG_SESSION_TYPE", "wayland")
    assert linux_x11.X11Backend.available() is False


def test_a_backend_that_fails_to_start_falls_through(linux):
    devs = ["/dev/input/event0"]
    _permissions(linux, devs, lambda p: True, uinput_writable=True)
    linux.setenv("DISPLAY", ":0")
    _fake_x11(linux)

    def boom(self):
        raise OSError("grab failed")
    linux.setattr(linux_evdev.EvdevBackend, "__init__", boom)
    assert pf.linux_backend().name == "x11"


def test_keyboard_api_on_linux_is_the_backends(linux):
    _permissions(linux, [], lambda p: False, uinput_writable=False)
    assert pf.keyboard_api() is pf.linux_backend().keyboard


def test_keyboard_api_on_windows_is_the_keyboard_module(monkeypatch):
    fake = object()
    monkeypatch.setattr(pf, "OS", "windows")
    monkeypatch.setitem(sys.modules, "keyboard", fake)
    assert pf.keyboard_api() is fake
    monkeypatch.setitem(sys.modules, "keyboard", None)
    with pytest.raises(ImportError):
        pf.keyboard_api()


# --- the null backend -------------------------------------------------------

def test_null_backend_hook_raises_value_error():
    nb = pf.NullBackend()
    with pytest.raises(ValueError):
        nb.keyboard.hook_key("space", lambda e: True, suppress=True)
    assert nb.keyboard.is_pressed("f12") is False
    with pytest.raises(RuntimeError):
        nb.keyboard.send("ctrl+v")
    nb.keyboard.unhook_all()
    assert nb.gamepad() is None


def test_null_backend_keys_go_down_the_bad_keys_path(monkeypatch):
    from eye_gaze.switch import KeyPresses
    monkeypatch.setattr(pf, "keyboard_api", lambda: pf.NullBackend().keyboard)
    kp = KeyPresses(["space", "f5"])
    assert kp.bad_keys == ["space", "f5"]
    assert kp.take("space") is False


def test_null_backend_screen_without_a_display_is_a_said_guess(monkeypatch):
    nb = pf.NullBackend()
    monkeypatch.setattr(pf, "_tk_screen_size", lambda: None)
    assert nb.screens() == [(0, 0, 1920, 1080)]
    assert nb.primary() == (0, 0, 1920, 1080)
    assert nb.screen_guessed is True
    nb.set_cursor(10, 20)
    assert nb.get_cursor() == (10, 20)


def test_null_backend_screen_from_tk(monkeypatch):
    nb = pf.NullBackend()
    monkeypatch.setattr(pf, "_tk_screen_size", lambda: (1280, 1024))
    assert nb.screens() == [(0, 0, 1280, 1024)]
    assert nb.screen_guessed is False


# --- capabilities -----------------------------------------------------------

def test_capabilities_windows_all_true_except_linux_only(monkeypatch):
    monkeypatch.setattr(pf, "OS", "windows")
    caps = pf.capabilities()
    assert set(caps) == set(pf.CAPABILITY_KEYS)
    # No capability is Linux-only today; the set exists so one that is gets
    # declared instead of quietly reading False on Windows.
    for key, (ok, why) in caps.items():
        assert ok is (key not in pf.LINUX_ONLY), key
        assert isinstance(why, str)


def test_capabilities_wayland_without_evdev_says_why(linux):
    linux.setenv("XDG_SESSION_TYPE", "wayland")
    linux.setenv("WAYLAND_DISPLAY", "wayland-0")
    _permissions(linux, ["/dev/input/event0"], lambda p: False,
                 uinput_writable=False)
    caps = pf.capabilities()
    assert set(caps) == set(pf.CAPABILITY_KEYS)
    ok, why = caps["cursor"]
    assert ok is False
    assert "izin" in why
    assert caps["zoom_capture"][0] is False
    assert "Xorg" in caps["zoom_capture"][1]
    assert caps["dwell_ring"][0] is False
    assert caps["onecore_tts"][0] is False
    assert caps["snap"][0] is False
    for key, (ok, why) in caps.items():
        assert ok or why.strip(), "%s is False without a reason" % key


def test_capabilities_evdev_on_wayland(linux):
    linux.setenv("XDG_SESSION_TYPE", "wayland")
    linux.setenv("WAYLAND_DISPLAY", "wayland-0")
    _permissions(linux, ["/dev/input/event0"], lambda p: True,
                 uinput_writable=True)

    class FakeEvdev:
        name = "evdev"

        def capabilities(self):
            return {}
    linux.setattr(pf, "_backend", [FakeEvdev()])
    caps = pf.capabilities()
    assert caps["cursor"][0] is True
    assert caps["global_keys"][0] is True
    assert caps["suppress_keys"][0] is True
    assert caps["zoom_capture"][0] is False      # still no screenshots


def test_x11_capabilities_say_what_x11_cannot(linux):
    linux.setenv("XDG_SESSION_TYPE", "x11")
    linux.setenv("DISPLAY", ":0")
    _fake_x11(linux)
    _permissions(linux, [], lambda p: False, uinput_writable=False)
    assert pf.linux_backend().name == "x11"
    caps = pf.capabilities()
    assert caps["cursor"][0] is True
    assert caps["global_keys"][0] is True
    assert caps["suppress_keys"][0] is True
    # XInput2 raw events tell the on-screen keyboard's (XTest) keys apart.
    assert caps["injected_detection"][0] is True
    ok, why = caps["gamepad"]
    assert ok is False and why.strip()

    b = pf.linux_backend()
    b._xi2_reason = "X sunucusunda XInput 2.1 yok"
    ok, why = pf.capabilities()["injected_detection"]
    assert ok is False and "ekran klavyesi" in why and "XInput 2.1" in why

    b.keyboard.dead, b.keyboard.error = True, "ConnectionClosedError"
    caps = pf.capabilities()
    for key in ("global_keys", "suppress_keys"):
        ok, why = caps[key]
        assert ok is False and "durdu" in why and "ConnectionClosed" in why


def test_x11_backend_that_cannot_connect_falls_to_null(linux):
    # No X server behind DISPLAY (or no python-xlib): said, not silent.
    linux.setenv("XDG_SESSION_TYPE", "x11")
    linux.setenv("DISPLAY", ":0")
    _permissions(linux, [], lambda p: False, uinput_writable=False)

    def refuse(self, display_name=None):
        raise ConnectionRefusedError("no X server")
    linux.setattr(linux_x11.X11Backend, "__init__", refuse)
    b = pf.linux_backend()
    assert b.name == "null"
    assert "x11" in b.start_errors[0]
    assert "no X server" in pf.capabilities()["cursor"][1]


def test_null_backend_guessed_screen_is_said(linux):
    linux.setenv("XDG_SESSION_TYPE", "wayland")
    _permissions(linux, [], lambda p: False, uinput_writable=False)
    linux.setattr(pf, "_tk_screen_size", lambda: None)
    pf.linux_backend().screens()
    assert "1920x1080" in pf.capabilities()["cursor"][1]


def test_capability_reasons_have_an_ascii_form_for_the_console(linux):
    linux.setenv("XDG_SESSION_TYPE", "wayland")
    _permissions(linux, [], lambda p: False, uinput_writable=False)
    for ok, why in pf.capabilities().values():
        pf.ascii_text(why).encode("ascii")
    assert pf.ascii_text("Gözlük şöyle İĞÜ ışık") == "Gozluk soyle IGU isik"
    assert pf.ascii_text("a—b") == "a?b"


# --- key name tables --------------------------------------------------------

def test_key_tables_roundtrip():
    assert {"space", "f12", "scroll lock", "a", "0", "f24"} <= keys.KNOWN
    for n in keys.KNOWN:
        assert keys.from_evdev(keys.to_evdev(n)) == n, n
        assert keys.from_keysym(keys.to_keysym(n)) == n, n


def test_key_tables_known_codes_and_aliases():
    # linux/input-event-codes.h
    assert keys.to_evdev("space") == 57
    assert keys.to_evdev("f12") == 88
    assert keys.to_evdev("scroll lock") == 70
    assert keys.to_evdev("esc") == 1
    assert keys.to_keysym("scroll lock") == "Scroll_Lock"
    assert keys.to_keysym("page up") == "Prior"
    assert keys.from_keysym("Page_Up") == "page up"
    assert keys.to_evdev(" Space ") == 57          # config spelling varies
    assert keys.to_evdev("shift") == keys.to_evdev("left shift")
    assert keys.from_keysym("A") == "a"           # Shift held
    assert keys.from_evdev(9999) is None
    assert keys.from_keysym("NoSuchSym") is None
    with pytest.raises(ValueError):
        keys.to_evdev("nosuchkey")
    with pytest.raises(ValueError):
        keys.to_keysym("")


# --- the driver's users go through the platform -----------------------------

def test_keypresses_uses_platform_api(monkeypatch):
    from eye_gaze.switch import KeyPresses
    hooked = []

    class Api:
        def hook_key(self, name, cb, suppress=False):
            hooked.append((name, suppress))
            return lambda: None
    monkeypatch.setattr(pf, "keyboard_api", lambda: Api())
    kp = KeyPresses(["space", "f5"])
    assert kp.ok is True
    assert hooked == [("space", True), ("f5", True)]


def test_hotkeys_use_platform_api(monkeypatch):
    from eye_gaze.hotkeys import Hotkeys
    hooked = []

    class Api:
        def hook_key(self, name, cb, suppress=False):
            hooked.append(name)
            return lambda: None

        def unhook_all(self):
            pass
    monkeypatch.setattr(pf, "keyboard_api", lambda: Api())
    hk = Hotkeys("f10", "f9", "f8", interval=1.0)
    assert hk.mode == "global"
    assert hooked == ["f10", "f9", "f8"]


class _FakeBackend:
    """Records what cursor.py / actions.py ask a Linux backend for."""

    def __init__(self):
        self.calls = []
        self.keyboard = self
        self.cursor = (5, 6)

    def primary(self):
        return (0, 0, 1600, 900)

    def screens(self):
        return [(0, 0, 1600, 900), (1600, -100, 1280, 1024)]

    def get_cursor(self):
        return self.cursor

    def set_cursor(self, x, y):
        self.calls.append(("set_cursor", x, y))

    def button(self, btn, down):
        self.calls.append(("button", btn, down))

    def scroll(self, amount):
        self.calls.append(("scroll", amount))

    def send(self, combo):
        self.calls.append(("send", combo))


def test_cursor_without_user32_uses_the_linux_backend(monkeypatch):
    from eye_gaze import cursor
    fake = _FakeBackend()
    monkeypatch.setattr(cursor, "_user32", None)
    monkeypatch.setattr(pf, "linux_backend", lambda: fake)
    assert cursor.screen_size() == (1600, 900)          # not 1920x1080
    assert cursor.virtual_screen() == (0, -100, 2880, 1024)
    assert cursor.get_cursor() == (5, 6)
    assert cursor.set_cursor(5000, 10) == (1599, 10)     # primary clamp
    assert cursor.set_cursor(-5000, 10, virtual=True) == (0, 10)
    assert fake.calls == [("set_cursor", 1599, 10), ("set_cursor", 0, 10)]
    assert cursor.monitor_rect(2000, 0) == (1600, -100, 1280, 1024)
    assert cursor.monitor_rect(10, 10) == (0, 0, 1600, 900)
    assert cursor.monitor_rect(9000, 300) == (1600, -100, 1280, 1024)


def test_actions_without_user32_use_the_linux_backend(monkeypatch):
    from eye_gaze import actions
    fake = _FakeBackend()
    monkeypatch.setattr(actions, "_u", None)
    monkeypatch.setattr(pf, "linux_backend", lambda: fake)
    actions.left_click()
    actions.right_click()
    actions.scroll(-240)
    _REAL_ESCAPE()
    assert fake.calls == [
        ("button", "left", True), ("button", "left", False),
        ("button", "right", True), ("button", "right", False),
        ("scroll", -240), ("send", "esc")]


def test_injected_install_is_a_windows_only_patch(monkeypatch):
    from eye_gaze import injected
    monkeypatch.setattr(pf, "OS", "linux")
    monkeypatch.setattr(injected, "_installed", [False])
    assert injected.install() is False
    assert injected._installed == [False]


# --- real Linux (Docker) ----------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="real Linux session checks")
def test_real_linux_selection_does_not_raise(monkeypatch):
    monkeypatch.setattr(pf, "_backend", [None])
    b = pf.linux_backend()
    if linux_evdev.EvdevBackend.available():
        assert b.name == "evdev"
    elif linux_x11.X11Backend.available():
        assert b.name == "x11"
    else:
        assert b.name == "null"
    caps = pf.capabilities()
    assert set(caps) == set(pf.CAPABILITY_KEYS)
    assert caps["snap"][0] is False


@pytest.mark.skipif(os.name == "nt" or not os.environ.get("DISPLAY"),
                    reason="needs a real (or Xvfb) X display")
def test_real_linux_null_backend_measures_the_screen():
    import tkinter
    root = tkinter.Tk()
    try:
        size = (root.winfo_screenwidth(), root.winfo_screenheight())
    finally:
        root.destroy()
    nb = pf.NullBackend()
    assert nb.screens() == [(0, 0) + size]
    assert nb.screen_guessed is False
