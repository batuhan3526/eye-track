# gaze/eye_gaze/injected.py
"""Tell real key presses from synthetic (injected) ones.

The Windows On-Screen Keyboard (ScrLk x2) and OptiKey type by INJECTING key
events. Our hook swallowed an injected Space and counted it as a click press:
the user clicks OSK's Space with the switch -> OSK injects Space -> the driver
clicks at the cursor, which is still on OSK's Space key -> OSK injects Space
again... a click loop, and no space ever typed. The same took OSK/OptiKey's
F-keys away from the app.

keyboard 0.13.5 sees the LLKHF_INJECTED flag in its hook but drops it. This
wraps the hook procedure to record the flag, and stamps it on the
KeyboardEvent (built synchronously in the hook thread), so it also survives
keyboard's queue for non-suppressing hooks. install() must run before the
first hook is registered - the listener builds its procedure only once.
"""

LLKHF_INJECTED = 0x10

# Off for an end-to-end smoke test, which sends its keys from another
# process (those are injected too). config: ignore_injected_keys.
enabled = True

_flag = [False]
_installed = [False]


def install():
    """Patch the keyboard library once. Never raises; False if unavailable."""
    from eye_gaze import platform as _platform
    if _platform.OS != "windows":
        # The Linux backends stamp `injected` on their own events.
        return False
    if _installed[0]:
        return True
    try:
        from keyboard import _winkeyboard as wk
        proc = wk.LowLevelKeyboardProc
        base = wk.KeyboardEvent
    except Exception:
        return False

    def make_proc(fn):
        def wrapped(n_code, w_param, l_param):
            try:
                _flag[0] = bool(l_param.contents.flags & LLKHF_INJECTED)
            except Exception:
                _flag[0] = False
            return fn(n_code, w_param, l_param)
        return proc(wrapped)

    class _Event(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.injected = _flag[0]

    try:
        wk.LowLevelKeyboardProc = make_proc
        wk.KeyboardEvent = _Event
    except Exception:
        return False
    _installed[0] = True
    return True


def is_injected(event):
    """True when `event` was synthesised by a program and should pass
    through to the app untouched instead of acting as one of our keys."""
    return enabled and bool(getattr(event, "injected", False))
