# gaze/tests/conftest.py
import os

import pytest


@pytest.fixture(autouse=True)
def _no_real_escape_key(monkeypatch):
    """_Clicker.release() taps a REAL Esc (it cancels a drag). A test that
    reaches it without stubbing must not send Esc to whatever window the
    person running the suite is typing in. Tests that record it override."""
    from eye_gaze import actions
    monkeypatch.setattr(actions, "escape", lambda: None)


@pytest.fixture(autouse=True)
def _no_real_audio_device(monkeypatch):
    """On Linux every sound goes through playback's shared PortAudio output.
    No test may open the real device (the Docker run has none; a developer's
    machine would beep): the real sounddevice is refused, and each test
    starts from a fresh output. Tests that need one pass a fake module."""
    from eye_gaze import playback

    def refuse():
        raise OSError("the test suite never opens a real audio device")
    monkeypatch.setattr(playback, "_import_sounddevice", refuse)
    monkeypatch.setattr(playback, "_SD_BACKEND", None)


@pytest.fixture(autouse=True)
def _signal_handlers_restored():
    """The Linux close guard installs SIGHUP/SIGTERM handlers; one test's
    handler must not fire a later test's (or pytest's) fake clicker."""
    import signal
    saved = {}
    for name in ("SIGHUP", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            saved[sig] = signal.getsignal(sig)
    yield
    for sig, handler in saved.items():
        try:
            if handler is not None:
                signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


@pytest.fixture(autouse=True)
def _turkish_after_each_test():
    """Every test starts in Turkish, the default the suite's expectations
    are written in: a test that switches the language (test_i18n) must not
    leave the next one reading English."""
    yield
    from eye_gaze import i18n
    if i18n.language() != i18n.DEFAULT or i18n.notice():
        i18n.reset()


# --- the shipped config -------------------------------------------------------
# gaze/config.yaml is each install's own file (.gitignore): never read by the
# tests, so a developer's local settings cannot change their outcome.

from pathlib import Path as _Path

GAZE_DIR = _Path(__file__).resolve().parent.parent
TEMPLATE_CONFIG = GAZE_DIR / "config.default.yaml"


@pytest.fixture
def shipped_config():
    """The config a fresh start from this tree runs with:
    config.default.yaml, which the launcher copies to config.yaml."""
    return TEMPLATE_CONFIG


# --- one Tk interpreter for the whole run ------------------------------------

def _new_tk(tk, tries=3):
    last = None
    for _ in range(tries if os.name == "nt" else 1):
        try:
            return tk.Tk()
        except tk.TclError as e:
            last = e
    raise last


@pytest.fixture(scope="session")
def tk_root():
    """The suite's ONE Tk interpreter, shared by every module that shows
    the launcher's windows (test_ui_wizard_flow, test_i18n,
    test_i18n_resources), as the real launcher has one for its lifetime.
    A fresh Tk() per test - and later one per module - failed now and then
    on Windows under pytest's fd capture ("can't find a usable init.tcl",
    "invalid command name tcl_findLibrary"; never with -s, never in a
    30-root loop outside pytest), and the tests then skipped as if there
    were no display (final review M-6). "No display" is a Linux reason
    only: on Windows a Tk that cannot start is retried, then a failure."""
    tk = pytest.importorskip("tkinter")
    from eye_gaze.ui import widgets
    saved = widgets.HIDDEN
    widgets.HIDDEN = True
    try:
        r = _new_tk(tk)
    except tk.TclError as e:
        widgets.HIDDEN = saved
        if os.name == "nt":
            pytest.fail("Tk did not start on Windows: %s" % e)
        pytest.skip("no display: %s" % e)
    r.withdraw()
    yield r
    widgets.HIDDEN = saved
    try:
        r.destroy()
    except tk.TclError:
        pass
    # The interpreter must die here, on the main thread: freed later by a
    # garbage collection that happens to run on another thread, Tcl aborts
    # the process ("Tcl_AsyncDelete: async handler deleted by the wrong
    # thread"). Collect what still points at it, then drop the last ref.
    import gc
    gc.collect()
    del r
