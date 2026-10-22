# gaze/tests/test_joystick.py
"""Gamepad and generic joystick input: one set of button names for XInput
pads, winmm joysticks (most switch adapters) and the Linux evdev pad, so one
config.yaml works everywhere. Fake XInput / winmm objects only: nothing here
reads a real device."""
import ctypes
import os

import pytest

from eye_gaze import joystick_win as jw
from eye_gaze import switch
from eye_gaze.switch import GAMEPAD_BUTTONS, GamepadReader, valid_button

NOT_CONNECTED = 1167        # ERROR_DEVICE_NOT_CONNECTED
JOYERR_UNPLUGGED = 167


class FakeXInput:
    def __init__(self, buttons=None):
        self.value = buttons        # None = no controller in slot 0
        self.calls = 0

    def XInputGetState(self, index, ref):
        self.calls += 1
        if self.value is None:
            return NOT_CONNECTED
        ref._obj.Gamepad.wButtons = self.value
        return 0


class FakeWinmm:
    """joyGetPosEx over {joystick id: dwButtons}."""

    def __init__(self, sticks=None):
        self.sticks = dict(sticks or {})
        self.calls = []

    def joyGetPosEx(self, joy_id, ref):
        self.calls.append(joy_id)
        info = ref._obj
        assert info.dwSize == ctypes.sizeof(jw.JOYINFOEX)
        assert info.dwFlags & jw.JOY_RETURNBUTTONS
        if joy_id not in self.sticks:
            return JOYERR_UNPLUGGED
        info.dwButtons = self.sticks[joy_id]
        return 0


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def reader(xinput=None, sticks=None, clock=None):
    clock = clock or Clock()
    x = FakeXInput(xinput)
    mm = FakeWinmm(sticks)
    joy = jw.GenericJoystick(api=mm, clock=clock)
    return GamepadReader(xinput=x, joystick=joy, pad=None, clock=clock), x, mm, clock


# --- names ---------------------------------------------------------------------

def test_xinput_bits_unchanged():
    assert GAMEPAD_BUTTONS["A"] == 0x1000
    assert GAMEPAD_BUTTONS["B"] == 0x2000
    assert GAMEPAD_BUTTONS["X"] == 0x4000
    assert GAMEPAD_BUTTONS["Y"] == 0x8000
    assert GAMEPAD_BUTTONS["LB"] == 0x0100 and GAMEPAD_BUTTONS["RB"] == 0x0200
    assert GAMEPAD_BUTTONS["START"] == 0x0010 and GAMEPAD_BUTTONS["BACK"] == 0x0020
    assert (GAMEPAD_BUTTONS["DPAD_UP"], GAMEPAD_BUTTONS["DPAD_DOWN"],
            GAMEPAD_BUTTONS["DPAD_LEFT"], GAMEPAD_BUTTONS["DPAD_RIGHT"]) == (1, 2, 4, 8)
    # The shared names: the d-pad without the XInput prefix, the stick clicks.
    assert GAMEPAD_BUTTONS["UP"] == GAMEPAD_BUTTONS["DPAD_UP"]
    assert GAMEPAD_BUTTONS["RIGHT"] == GAMEPAD_BUTTONS["DPAD_RIGHT"]
    assert GAMEPAD_BUTTONS["LS"] == 0x0040 and GAMEPAD_BUTTONS["RS"] == 0x0080


def test_generic_buttons_sit_above_the_xinput_bits():
    bits = [GAMEPAD_BUTTONS["BTN%d" % n] for n in range(1, 33)]
    assert len(set(bits)) == 32
    assert min(bits) > 0xFFFF
    assert all(b & (b - 1) == 0 for b in bits)          # one bit each


def test_valid_button_accepts_btn_n_up_to_32():
    for name in ("BTN1", "btn2", "BTN17", "BTN32", "LS", "rs", "UP", "down",
                 "LEFT", "RIGHT", "DPAD_UP", "A"):
        assert valid_button(name), name
    for name in ("BTN0", "BTN33", "BTN", "BTN01", "BTN-1", "LT", "", None):
        assert not valid_button(name), name


def test_names_of_a_mask_in_one_order():
    mask = (GAMEPAD_BUTTONS["BTN10"] | GAMEPAD_BUTTONS["BTN2"]
            | GAMEPAD_BUTTONS["UP"] | GAMEPAD_BUTTONS["A"])
    assert switch.names_of(mask) == ["A", "UP", "BTN2", "BTN10"]
    assert switch.names_of(0) == [] and switch.names_of(None) == []
    assert switch.order_names(["BTN10", "UP", "BTN2", "A"]) == [
        "A", "UP", "BTN2", "BTN10"]


def test_the_valid_names_text_is_short_and_ascii():
    text = switch.button_names_text()
    assert "BTN1..BTN32" in text and "UP" in text and "BTN7" not in text
    assert text.isascii()


# --- Windows: XInput first, then winmm ---------------------------------------------

def test_generic_joystick_button_names():
    gp, x, mm, _ = reader(sticks={2: 0b10001})          # buttons 1 and 5
    assert gp.pressed_names() == ["BTN1", "BTN5"]
    assert gp.pressed("BTN5") and gp.pressed("btn1")
    assert not gp.pressed("BTN2") and not gp.pressed("A")
    assert gp.buttons() == GAMEPAD_BUTTONS["BTN1"] | GAMEPAD_BUTTONS["BTN5"]


def test_button_32_of_a_joystick_is_btn32():
    gp, _, _, _ = reader(sticks={0: 0x80000000})
    assert gp.pressed_names() == ["BTN32"]


def test_xinput_preferred_over_generic():
    gp, x, mm, _ = reader(xinput=0x1000 | 0x0001, sticks={0: 0b1})
    mm.calls.clear()
    assert gp.buttons() == 0x1001                       # today's XInput mask
    assert gp.pressed_names() == ["A", "UP"]
    assert gp.pressed("A") and gp.pressed("DPAD_UP") and not gp.pressed("BTN1")
    assert mm.calls == []                               # the joystick is not read


def test_xinput_connected_with_nothing_pressed_is_zero_not_none():
    gp, _, _, _ = reader(xinput=0, sticks={0: 0b1})
    assert gp.buttons() == 0


def test_no_device_is_none():
    gp, _, _, _ = reader()
    assert gp.buttons() is None
    assert gp.pressed_names() == []
    assert gp.pressed("BTN1") is False


def test_absent_joysticks_are_probed_with_back_off():
    # Microsoft documents probing an absent joystick as slow: ids 0..15 are
    # probed once at start-up (not in the gaze loop), then once per RESCAN.
    clock = Clock()
    gp, x, mm, _ = reader(clock=clock)
    assert mm.calls == list(range(16))
    for _ in range(50):                 # 50 frames inside one back-off window
        clock.t += 0.033
        assert gp.buttons() is None
    assert len(mm.calls) == 16
    clock.t += jw.GenericJoystick.RESCAN
    assert gp.buttons() is None
    assert mm.calls == list(range(16)) * 2


def test_a_joystick_plugged_in_later_is_found_by_the_next_scan():
    clock = Clock()
    gp, x, mm, _ = reader(clock=clock)
    assert gp.buttons() is None
    mm.sticks[3] = 0b100
    assert gp.buttons() is None                         # not before the rescan
    clock.t += jw.GenericJoystick.RESCAN + 0.01
    assert gp.pressed_names() == ["BTN3"]


def test_a_joystick_unplugged_mid_press_releases():
    clock = Clock()
    gp, x, mm, _ = reader(sticks={1: 0b1}, clock=clock)
    assert gp.pressed("BTN1")
    del mm.sticks[1]
    assert gp.buttons() is None                         # the caller backs off
    assert gp.pressed("BTN1") is False
    assert gp.pressed_names() == []
    mm.sticks[1] = 0b1                                   # back again
    clock.t += jw.GenericJoystick.RESCAN + 0.01
    assert gp.pressed("BTN1")


def test_xinput_is_polled_rarely_while_a_joystick_answers():
    # Polling an empty XInput slot every frame is documented as expensive.
    clock = Clock()
    gp, x, mm, _ = reader(sticks={0: 0b1}, clock=clock)
    gp.buttons()
    x.calls = 0
    for _ in range(30):
        clock.t += 0.033
        assert gp.pressed("BTN1")
    assert x.calls <= 1


def test_an_xinput_pad_plugged_in_later_takes_over():
    clock = Clock()
    gp, x, mm, _ = reader(sticks={0: 0b1}, clock=clock)
    assert gp.pressed_names() == ["BTN1"]
    x.value = 0x2000
    clock.t += GamepadReader.XINPUT_RETRY + 0.01
    assert gp.pressed_names() == ["B"]


def test_xinput_unplugged_mid_press_releases():
    gp, x, mm, _ = reader(xinput=0x1000)
    assert gp.pressed("A")
    x.value = None
    assert gp.buttons() is None
    assert gp.pressed("A") is False


def test_a_failing_joystick_call_is_not_pressed():
    class Broken:
        def joyGetPosEx(self, joy_id, ref):
            raise OSError("driver gone")
    joy = jw.GenericJoystick(api=Broken(), clock=Clock())
    gp = GamepadReader(xinput=FakeXInput(None), joystick=joy, pad=None,
                       clock=Clock())
    assert gp.buttons() is None and gp.pressed_names() == []


def test_joyinfoex_layout():
    # 13 DWORDs: dwSize must be exactly what winmm expects.
    assert ctypes.sizeof(jw.JOYINFOEX) == 52
    assert jw.JOY_RETURNBUTTONS == 0x80


@pytest.mark.skipif(os.name != "nt", reason="winmm is Windows-only")
def test_joygetposex_prototype_is_declared():
    lib = jw._load_winmm()
    assert lib is not None
    assert lib.joyGetPosEx.restype is ctypes.c_uint
    assert lib.joyGetPosEx.argtypes == [ctypes.c_uint,
                                        ctypes.POINTER(jw.JOYINFOEX)]


def test_no_winmm_means_no_joystick():
    joy = jw.GenericJoystick(api=None, clock=Clock())
    assert joy.buttons() is None


# --- Linux: the backend's pad --------------------------------------------------------

class FakePad:
    def __init__(self, names):
        self.names = names

    def buttons(self, index=0):
        if self.names is None:
            return None
        mask = 0
        for n in self.names:
            mask |= GAMEPAD_BUTTONS.get(n, 0)
        return mask

    def pressed(self, name, index=0):
        return str(name).upper() in (self.names or ())

    def pressed_names(self):
        return list(self.names or [])


def test_the_linux_pad_is_used_as_is():
    pad = FakePad(["A", "BTN17"])
    gp = GamepadReader(xinput=FakeXInput(0x2000), pad=pad)
    assert gp.buttons() == GAMEPAD_BUTTONS["A"] | GAMEPAD_BUTTONS["BTN17"]
    assert gp.pressed("BTN17") and not gp.pressed("B")
    assert gp.pressed_names() == ["A", "BTN17"]
    pad.names = None                                    # unplugged
    assert gp.buttons() is None and gp.pressed_names() == []


def test_the_linux_reader_comes_from_the_backend(monkeypatch):
    from eye_gaze import platform as plat

    class Backend:
        def gamepad(self):
            return FakePad(["BTN2"])
    monkeypatch.setattr(plat, "OS", "linux")
    monkeypatch.setattr(plat, "linux_backend", lambda: Backend())
    gp = GamepadReader()
    assert gp.pressed_names() == ["BTN2"]


def test_without_a_linux_pad_nothing_is_pressed(monkeypatch):
    from eye_gaze import platform as plat

    class Backend:
        def gamepad(self):
            return None
    monkeypatch.setattr(plat, "OS", "linux")
    monkeypatch.setattr(plat, "linux_backend", lambda: Backend())
    gp = GamepadReader(xinput=None, joystick=None)
    assert gp.buttons() is None and gp.pressed_names() == []


# --- consumers keep the wide mask ---------------------------------------------------

def test_the_click_switch_takes_a_generic_button():
    import gaze_driver as gd
    gp, _, mm, _ = reader(sticks={0: 0})
    clicker = gd._Clicker({"gamepad_left": "BTN20"}, keys=None, gamepad=gp)
    clicker.update(1.0)
    assert not clicker.take("left")
    mm.sticks[0] = 1 << 19                              # button 20
    clicker.update(1.1)
    assert clicker.take("left") and clicker.left_held()


def test_an_unknown_click_button_lists_the_names(capsys):
    import gaze_driver as gd
    gd._Clicker({"gamepad_left": "BTN40"}, keys=None, gamepad=FakePad([]))
    out = capsys.readouterr().out
    assert "BTN40" in out and "BTN1..BTN32" in out


def test_the_alarm_hold_takes_a_generic_button_and_lets_go_on_unplug():
    from eye_gaze import alarm as al
    pp = al._PadPoll(FakePad(["BTN9"]))
    assert pp.pressed("BTN9", 1.0)
    pp._pad.names = None
    assert not pp.pressed("BTN9", 2.0)
