# gaze/tests/test_ui_keycapture.py
""""Press the key you want" (ui/keycapture.py), with fake keyboards: no
test hooks the real keyboard."""
import threading
import time

import pytest

from eye_gaze.platform import keys
from eye_gaze.ui import keycapture as kc


class Ev:
    def __init__(self, event_type, name, injected=False):
        self.event_type = event_type
        self.name = name
        self.injected = injected


class ReadEventKb:
    """The Windows `keyboard` module's read_event, fed from a list."""

    def __init__(self, events):
        self.events = list(events)
        self.calls = 0
        self.block = threading.Event()

    def read_event(self, suppress=False):
        self.calls += 1
        if self.events:
            return self.events.pop(0)
        self.block.wait(5)      # like the real one: waits for a key
        return Ev("down", "x")


def test_keycapture_returns_platform_name(monkeypatch):
    monkeypatch.setattr(kc._platform, "OS", "windows")
    kb = ReadEventKb([Ev("up", "space"), Ev("down", "scroll lock")])
    assert kc.capture_key(timeout_s=2, kb=kb) == "scroll lock"


@pytest.mark.parametrize("raw, want", [
    ("Scroll Lock", "scroll lock"), ("A", "a"), ("F12", "f12"),
    ("spacebar", "space"), ("ı", "ı"), ("  page   up ", "page up"),
])
def test_captured_names_are_the_ones_config_yaml_uses(monkeypatch, raw,
                                                      want):
    monkeypatch.setattr(kc._platform, "OS", "windows")
    kb = ReadEventKb([Ev("down", raw)])
    assert kc.capture_key(timeout_s=2, kb=kb) == want


def test_keycapture_times_out_with_none(monkeypatch):
    monkeypatch.setattr(kc._platform, "OS", "windows")
    kb = ReadEventKb([])
    t0 = time.monotonic()
    try:
        assert kc.capture_key(timeout_s=0.2, kb=kb) is None
    finally:
        kb.block.set()
    assert time.monotonic() - t0 < 2


class HookKb:
    """A Linux backend keyboard: hook_key / remover."""

    def __init__(self, fail=()):
        self.hooks = {}
        self.suppress = {}
        self.removed = []
        self.fail = set(fail)

    def hook_key(self, name, callback, suppress=False):
        if name in self.fail:
            raise ValueError(name)
        self.hooks[name] = callback
        self.suppress[name] = suppress

        def remove():
            self.removed.append(name)
            self.hooks.pop(name, None)
        return remove

    def press(self, name):
        cb = self.hooks.get(name)
        return None if cb is None else cb(Ev("down", name))


def test_linux_hooks_every_known_key_and_takes_the_first_press(monkeypatch):
    monkeypatch.setattr(kc._platform, "OS", "linux")
    kb = HookKb()
    out = {}

    def run():
        out["name"] = kc.capture_key(timeout_s=3, kb=kb)
    t = threading.Thread(target=run)
    t.start()
    deadline = time.monotonic() + 3
    while len(kb.hooks) < len(keys.KNOWN) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert set(kb.hooks) == set(keys.KNOWN)
    assert all(kb.suppress.values())
    # The pressed key is swallowed: it must not reach the focused window.
    assert kb.press("f13") is False
    t.join(3)
    assert out["name"] == "f13"
    assert kb.hooks == {}
    assert sorted(kb.removed) == sorted(keys.KNOWN)


def test_linux_hooks_are_removed_on_timeout(monkeypatch):
    monkeypatch.setattr(kc._platform, "OS", "linux")
    kb = HookKb(fail={"pause"})
    assert kc.capture_key(timeout_s=0.1, kb=kb) is None
    assert kb.hooks == {}
    assert len(kb.removed) == len(keys.KNOWN) - 1


def test_linux_without_any_hook_says_why(monkeypatch):
    monkeypatch.setattr(kc._platform, "OS", "linux")
    kb = HookKb(fail=set(keys.KNOWN))
    with pytest.raises(kc.CaptureUnavailable) as e:
        kc.capture_key(timeout_s=0.1, kb=kb)
    assert "klavye" in str(e.value).lower()


class Pad:
    def __init__(self, frames, present=True):
        self.frames = list(frames)
        self.present = present

    def buttons(self, index=0):
        return 0 if self.present else None

    def pressed_names(self, index=0):
        if len(self.frames) > 1:
            return self.frames.pop(0)
        return self.frames[0] if self.frames else []


def test_capture_pad_takes_a_fresh_press():
    # B is held when the dialog opens (e.g. the caregiver's thumb): not it.
    pad = Pad([["B"], ["B"], [], ["X"]])
    assert kc.capture_pad(timeout_s=2, reader=pad, sleep=lambda s: None) \
        == "X"


def test_capture_pad_times_out():
    t = [0.0]

    def clock():
        t[0] += 0.5
        return t[0]
    assert kc.capture_pad(timeout_s=2, reader=Pad([[]]), clock=clock,
                          sleep=lambda s: None) is None


def test_capture_pad_without_a_device_says_so():
    with pytest.raises(kc.CaptureUnavailable) as e:
        kc.capture_pad(timeout_s=1, reader=Pad([], present=False))
    assert "gamepad" in str(e.value).lower()
