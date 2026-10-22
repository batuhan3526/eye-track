# gaze/tests/test_keypresses.py
"""KeyPresses: the driver's own keys, read from the global hook as discrete
presses (auto-repeat ignored) and swallowed so they never reach the app."""
import types

from eye_gaze.switch import KeyPresses


class _Ev:
    def __init__(self, kind):
        self.event_type = kind


class _FakeKeyboard:
    """Stands in for the `keyboard` module: records hook_key registrations so
    the test can play key events through them, the way the OS hook would."""

    def __init__(self):
        self.hooks = {}
        self.suppress = {}
        self.removed = []

    def hook_key(self, key, callback, suppress=False):
        self.hooks[key] = callback
        self.suppress[key] = suppress
        return lambda: self.removed.append(key)

    def send_event(self, key, kind):
        """Returns what the hook returned: False = event blocked."""
        return self.hooks[key](_Ev(kind))


def _hub(keys=("space",), **kw):
    kb = _FakeKeyboard()
    return KeyPresses(keys, kb=kb, **kw), kb


def test_a_press_is_taken_once():
    hub, kb = _hub()
    kb.send_event("space", "down")
    kb.send_event("space", "up")
    assert hub.take("space") is True
    assert hub.take("space") is False


def test_auto_repeat_while_held_is_one_press():
    # Holding the switch makes Windows repeat key-down ~30x a second. Each one
    # used to be a click (or, for F10, half of a quit).
    hub, kb = _hub()
    for _ in range(25):
        kb.send_event("space", "down")
    assert hub.take("space") is True
    assert hub.take("space") is False
    kb.send_event("space", "up")
    kb.send_event("space", "down")
    assert hub.take("space") is True


def test_tap_shorter_than_a_frame_is_not_lost():
    # Polling is_pressed() once per video frame missed a down+up that both
    # happened between two frames; the hook sees it regardless.
    hub, kb = _hub()
    kb.send_event("space", "down")
    kb.send_event("space", "up")
    assert hub.take("space") is True


def test_keys_are_swallowed_so_the_app_never_sees_them():
    # Space must not also type a space / scroll the page, F5 must not also
    # reload the browser behind the AAC grid.
    hub, kb = _hub(("space", "f5"))
    assert kb.suppress["space"] is True
    assert kb.send_event("space", "down") is False
    assert kb.send_event("space", "up") is False
    assert kb.send_event("f5", "down") is False


def test_pass_through_when_suppression_switched_off():
    # Pause is caregiver mode: their keyboard has to type spaces again.
    hub, kb = _hub()
    hub.suppressing = False
    assert kb.send_event("space", "down") is True
    assert hub.take("space") is True        # still counted as a press


def test_suppress_disabled_by_config():
    hub, kb = _hub(suppress=False)
    assert kb.send_event("space", "down") is True


def test_key_names_are_case_insensitive_and_deduplicated():
    hub, kb = _hub(("Space", "space", "", None, "F5"))
    assert sorted(kb.hooks) == ["f5", "space"]
    kb.send_event("space", "down")
    assert hub.take("SPACE") is True


def test_unknown_key_is_skipped_not_fatal():
    class Picky(_FakeKeyboard):
        def hook_key(self, key, callback, suppress=False):
            if key == "nosuchkey":
                raise ValueError("Key 'nosuchkey' is not mapped")
            return super().hook_key(key, callback, suppress)
    kb = Picky()
    hub = KeyPresses(("nosuchkey", "space"), kb=kb)
    assert hub.bad_keys == ["nosuchkey"]
    kb.send_event("space", "down")
    assert hub.take("space") is True
    assert hub.take("nosuchkey") is False


def test_clear_drops_pending_presses():
    hub, kb = _hub()
    kb.send_event("space", "down")
    hub.clear()
    assert hub.take("space") is False


def test_without_keyboard_lib_nothing_is_pressed(monkeypatch):
    from eye_gaze import platform

    def missing():
        raise ImportError("keyboard")
    monkeypatch.setattr(platform, "keyboard_api", missing)
    hub = KeyPresses(("space",))
    assert hub.ok is False
    assert hub.take("space") is False


def test_close_unhooks_everything():
    hub, kb = _hub(("space", "f5"))
    hub.close()
    assert sorted(kb.removed) == ["f5", "space"]


class _InjEv:
    def __init__(self, kind, injected):
        self.event_type = kind
        self.injected = injected


def test_a_key_typed_by_the_on_screen_keyboard_is_not_a_click(monkeypatch):
    """OSK/OptiKey INJECT their keys. A swallowed OSK Space was counted as a
    click press -> the driver clicked OSK's Space again -> another injected
    Space: a click loop, and no space ever typed."""
    from eye_gaze import injected
    from eye_gaze.switch import KeyPresses
    monkeypatch.setattr(injected, "enabled", True)
    hooks = {}

    class Kb:
        def hook_key(self, name, cb, suppress=False):
            hooks[name] = cb
            return lambda: None
    kp = KeyPresses(["space"], kb=Kb())
    assert hooks["space"](_InjEv("down", True)) is True   # passed to the app
    hooks["space"](_InjEv("up", True))
    assert kp.take("space") is False
    assert hooks["space"](_InjEv("down", False)) is False  # real: swallowed
    assert kp.take("space") is True


def test_injected_keys_count_when_the_setting_is_off(monkeypatch):
    # A switch interface that sends its key through software is injected too.
    from eye_gaze import injected
    from eye_gaze.switch import KeyPresses
    monkeypatch.setattr(injected, "enabled", False)
    hooks = {}

    class Kb:
        def hook_key(self, name, cb, suppress=False):
            hooks[name] = cb
            return lambda: None
    kp = KeyPresses(["space"], kb=Kb())
    hooks["space"](_InjEv("down", True))
    assert kp.take("space") is True


# --- deferred grabs (Linux X11: the driver grabs after loading its models) ---

def test_a_deferred_hub_hooks_nothing_until_armed():
    hub, kb = _hub(("space", "f13"), defer=True)
    assert kb.hooks == {}
    assert hub.take("space") is False and hub.held("space") is False
    hub.arm(["space"])                       # the alarm's click switch first
    assert list(kb.hooks) == ["space"]
    hub.arm()
    hub.arm()                                # idempotent
    assert sorted(kb.hooks) == ["f13", "space"]
    kb.send_event("f13", "down")
    assert hub.take("f13") is True


def test_a_deferred_hub_closed_early_is_never_hooked():
    hub, kb = _hub(("space",), defer=True)
    hub.close()
    hub.arm()
    assert kb.hooks == {}


def test_arm_lists_a_bad_key_like_the_constructor():
    class Picky(_FakeKeyboard):
        def hook_key(self, key, callback, suppress=False):
            if key == "nosuchkey":
                raise ValueError(key)
            return super().hook_key(key, callback, suppress)
    hub = KeyPresses(["space", "nosuchkey"], kb=Picky(), defer=True)
    assert hub.bad_keys == []
    hub.arm()
    assert hub.bad_keys == ["nosuchkey"]


def test_disarm_unhooks_all_but_the_kept_keys_and_arm_brings_them_back():
    # Linux X11 after a camera wait: only the alarm's keys stay grabbed
    # while the models load (gaze_driver.main, final review I-1).
    hub, kb = _hub(("space", "f13", "f2"), defer=True)
    hub.arm()
    kb.send_event("f13", "down")             # held when the grab goes
    hub.disarm(keep=["SPACE"])
    assert sorted(kb.removed) == ["f13", "f2"]
    assert hub.held("f13") is False and hub.take("f13") is False
    assert hub.held("space") is False
    hub.disarm(keep=["space"])               # twice: nothing more removed
    assert sorted(kb.removed) == ["f13", "f2"]
    kb.hooks.clear()
    hub.arm()
    assert sorted(kb.hooks) == ["f13", "f2"]  # space was never unhooked
    kb.send_event("f2", "down")
    assert hub.take("f2") is True
    hub.close()
    assert sorted(kb.removed) == ["f13", "f13", "f2", "f2", "space"]
