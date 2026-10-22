# gaze/tests/test_entegrasyon.py
"""Cross-feature checks for the seven feat/oz-* branches merged together.

Each builder could only see its own feature. These pin down what happens
where two of them meet: one key doing two things, the alarm sharing the one
audio channel with speech and earcons, the dwell ring over the click menu's
screenshot and the calibration screens, dwell in AAC scan mode, and a stale
dwell after a pause.
"""
import inspect
import io
import threading
import time
import wave

import gaze_driver as gd
from eye_gaze import alarm as al
from eye_gaze import click_menu as cm
from eye_gaze import keymap
from eye_gaze.dwell import DwellClicker
from eye_gaze.dwell_ring import DwellRing, guard_capture
from eye_gaze.playback import WavPlayer


def _wav(seconds, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


def _quiet(*a, **k):
    pass


# --- one key, one job ----------------------------------------------------------

BASE = {"left_click_key": "space", "zoom_key": "f6", "aac_key": "f5",
        "osk_key": "scroll lock", "tts_key": "f4", "stt_key": "f3",
        "quit_key": "f10", "recalibrate_key": "f9", "pause_key": "f8",
        "drift_key": "f7", "click_menu_key": "f2", "speech_stop_key": "f11",
        "alarm_key": "f12", "gamepad_left": "A", "gamepad_right": "B",
        "gamepad_double": "Y", "gamepad_drag": "X", "gamepad_alarm": ""}


def _resolve(**over):
    cfg = dict(BASE, **over)
    lines = []
    off = keymap.resolve_key_conflicts(cfg, log=lines.append)
    return cfg, off, lines


def test_the_shipped_keys_do_not_collide():
    cfg, off, lines = _resolve()
    assert off == [] and lines == []
    assert cfg == BASE


def test_a_new_key_on_a_driver_key_is_turned_off_with_a_warning():
    for name, taken in (("click_menu_key", "space"), ("speech_stop_key", "f4"),
                        ("alarm_key", "f5"), ("click_menu_key", "f10"),
                        ("speech_stop_key", "f8")):
        cfg, off, lines = _resolve(**{name: taken})
        assert cfg[name] == "", name
        assert [o[0] for o in off] == [name]
        assert lines and all(ln.isascii() for ln in lines)
        assert name in lines[0]


def test_names_are_compared_like_the_hook_compares_them():
    cfg, off, _ = _resolve(click_menu_key=" F5 ")
    assert cfg["click_menu_key"] == ""


def test_the_alarm_keeps_its_key_over_the_convenience_keys():
    cfg, off, _ = _resolve(click_menu_key="f12")
    assert cfg["alarm_key"] == "f12" and cfg["click_menu_key"] == ""
    cfg, off, _ = _resolve(speech_stop_key="f12")
    assert cfg["alarm_key"] == "f12" and cfg["speech_stop_key"] == ""


def test_menu_key_beats_speech_stop_key():
    cfg, off, _ = _resolve(speech_stop_key="f2")
    assert cfg["click_menu_key"] == "f2" and cfg["speech_stop_key"] == ""


def test_gamepad_alarm_on_a_click_button_is_turned_off():
    cfg, off, lines = _resolve(gamepad_alarm="a")
    assert cfg["gamepad_alarm"] == ""
    assert "alarm_switch_hold" in lines[0]
    cfg, off, _ = _resolve(gamepad_alarm="START")
    assert cfg["gamepad_alarm"] == "START"


def test_defaults_missing_from_cfg_are_still_seen():
    # quit_key etc. absent: their driver defaults are what is hooked.
    cfg = {"click_menu_key": "f8", "speech_stop_key": "f11", "alarm_key": "f12"}
    keymap.resolve_key_conflicts(cfg, log=_quiet)
    assert cfg["click_menu_key"] == ""


def test_resolve_never_raises():
    class Bad(dict):
        def get(self, *a):
            raise RuntimeError("boom")
    assert keymap.resolve_key_conflicts(Bad(), log=_quiet) == []


def test_main_resolves_conflicts_before_any_key_is_hooked():
    src = inspect.getsource(gd.main)
    i = src.index("resolve_key_conflicts(cfg")
    assert i < src.index("KeyPresses(")
    assert i < src.index("start_alarm(")
    assert i < src.index("build_speech_control(")
    assert i < src.index("click_menu.from_config(")


# --- the alarm owns the audio channel while it rings --------------------------

class FakeBackend:
    def __init__(self):
        self.started = []
        self.stops = 0

    def start(self, wav):
        self.started.append(wav)
        return True

    def stop(self):
        self.stops += 1


def test_held_channel_drops_earcons_and_speech():
    be = FakeBackend()
    p = WavPlayer(backend=be, fallback=lambda w: be.started.append(w), tail=0.0)
    p.hold()
    assert p.try_play(_wav(0.02)) is False
    assert p.play(_wav(0.02)) is False
    assert be.started == []
    p.release()
    assert p.try_play(_wav(0.02)) is True
    assert len(be.started) == 1


def test_stop_during_the_alarm_does_not_silence_the_siren():
    """A sentence was playing when the alarm started; F11 (or the sentence's
    own stop) must not send PlaySound(NULL) - that would cut the siren."""
    be = FakeBackend()
    p = WavPlayer(backend=be, tail=0.0)
    res = {}
    th = threading.Thread(target=lambda: res.setdefault("r", p.play(_wav(3.0))))
    th.start()
    time.sleep(0.05)
    p.hold()
    p.stop()
    th.join(2.0)
    assert not th.is_alive()
    assert res["r"] is False
    assert be.stops == 0
    p.release()


class FakeSound:
    SND_FILENAME, SND_ASYNC, SND_NODEFAULT = 0x20000, 0x1, 0x2

    def __init__(self, log):
        self.log = log

    def PlaySound(self, sound, flags):
        self.log.append(("play", sound is not None))

    def Beep(self, f, ms):
        pass


class FakeChannel:
    def __init__(self, log):
        self.log = log

    def hold(self):
        self.log.append("hold")

    def release(self):
        self.log.append("release")


def test_tone_player_takes_the_channel_before_sounding(tmp_path):
    log = []
    tp = al.TonePlayer(sound=FakeSound(log), tmpdir=str(tmp_path),
                       channel=FakeChannel(log))
    tp.start(0.0)
    assert log[:2] == ["hold", ("play", True)]
    tp.stop()
    assert log[-2:] == [("play", False), "release"]
    tp.close()


def test_tone_player_survives_a_broken_channel(tmp_path):
    class Broken:
        def hold(self):
            raise RuntimeError("x")

        def release(self):
            raise RuntimeError("y")
    log = []
    tp = al.TonePlayer(sound=FakeSound(log), tmpdir=str(tmp_path),
                       channel=Broken())
    tp.start(0.0)
    assert ("play", True) in log
    tp.stop()
    tp.close()


def test_the_real_alarm_player_shares_the_speech_channel():
    from eye_gaze.playback import shared_player

    class KB:
        def hook_key(self, key, cb, suppress=False):
            return lambda: None

    class T:
        def __init__(self, target=None, args=(), daemon=None):
            pass

        def start(self):
            pass
    w = al.start_alarm({}, kb=KB(), gamepad=None, log=_quiet, thread=T,
                       os_keys=None)
    try:
        assert w.core.player._channel is shared_player()
    finally:
        w.close()
    assert shared_player().held is False


# --- dwell ring vs the screens that cover it -----------------------------------

class FakeRing:
    def __init__(self, log):
        self.log = log

    def prepare_capture(self):
        self.log.append("ring hidden")

    def hide(self):
        self.log.append("ring hidden")


class _Clk:
    dragging = False

    def discard(self):
        pass


def test_click_menu_screenshot_never_contains_the_ring():
    log = []

    def grab():
        log.append("grab")
        return None
    menu = cm.ClickMenu(1280, 720, _Clk(), ui=type("U", (), {
        "show": lambda s, i: None, "close": lambda s: None})(),
        grab=grab, get_cursor=lambda: (10, 10), set_cursor=lambda x, y: None,
        scroll=lambda n: None, log=_quiet)
    guard_capture(menu, FakeRing(log))
    menu.begin((300, 300), 0.0)
    assert log == ["ring hidden", "grab"]


def test_main_guards_the_click_menu_capture():
    src = inspect.getsource(gd.main)
    assert "guard_capture(clickmenu, dring)" in src
    assert src.index("clickmenu = click_menu.from_config(") < \
        src.index("guard_capture(clickmenu, dring)")


class FakeWin:
    def __init__(self):
        self.calls = []

    def create(self, size):
        pass

    def exclude_from_capture(self):
        return True

    def draw(self, bgra):
        self.calls.append("draw")

    def move(self, l, t):
        self.calls.append("move")

    def hide(self):
        self.calls.append("hide")

    def flush(self):
        pass

    def pump(self):
        pass

    def destroy(self):
        pass


def test_ring_hide_takes_a_shown_ring_off_the_screen():
    d = DwellClicker(40, 1.0)
    win = FakeWin()
    ring = DwellRing(d, 40, win, log=_quiet)
    d.update(100, 100, 0.0)
    d.update(100, 100, 0.5)
    ring.tick(0.5)
    assert "move" in win.calls
    ring.hide()
    assert win.calls[-1] == "hide"
    ring.hide()                          # idempotent, never raises
    assert win.calls.count("hide") == 1


class Layer:
    def __init__(self, log, name):
        self.log, self.name, self.active = log, name, True

    def cancel(self):
        self.log.append(self.name)
        self.active = False


def test_quiet_layers_closes_every_overlay_and_hides_the_ring():
    log = []
    gd._quiet_layers(Layer(log, "zoom"), Layer(log, "aac"), Layer(log, "menu"),
                     FakeRing(log))
    assert sorted(log) == ["aac", "menu", "ring hidden", "zoom"]


def test_quiet_layers_tolerates_none_and_failures():
    class Boom:
        def cancel(self):
            raise RuntimeError("x")

        def hide(self):
            raise RuntimeError("y")
    log = []
    gd._quiet_layers(Boom(), None, Layer(log, "menu"), Boom())
    assert log == ["menu"]
    gd._quiet_layers(None, None, None, None)


def test_calibration_and_drift_start_from_a_clean_screen():
    src = inspect.getsource(gd.main)
    recal = src.index("if hotkeys.take_recal():")
    drift = src.index("if hotkeys.take_drift():")
    q1 = src.index("_quiet_layers(", recal)
    assert q1 < src.index("run_calibration()", recal)
    q2 = src.index("_quiet_layers(", drift)
    assert q2 < src.index("measure_drift(", drift)


# --- dwell in AAC scan mode, and after a pause --------------------------------

class Grid:
    def __init__(self, active, scanning):
        self.active, self.scanning = active, scanning


def test_dwell_is_not_fed_while_the_scan_grid_is_open():
    assert gd._dwell_live(None) is True
    assert gd._dwell_live(Grid(False, True)) is True
    assert gd._dwell_live(Grid(True, False)) is True      # gaze grid: dwell picks
    assert gd._dwell_live(Grid(True, True)) is False      # scan: switch only


def test_main_gates_dwell_on_the_scan_grid():
    src = inspect.getsource(gd.main)
    assert "_dwell_live(aacgrid)" in src
    assert "fire_left_click(dwell=True)" in src


def test_a_dwell_does_not_fire_the_moment_feeding_resumes():
    """Paused (or calibrating, or scanning) with the cursor parked: when the
    loop feeds the dweller again, the old anchor must not count as a dwell
    that has been building the whole time."""
    d = DwellClicker(40, 1.0)
    assert d.update(500, 500, 0.0) is False
    assert d.update(500, 500, 0.4) is False
    # 30 s with no feeding, cursor still there
    assert d.update(502, 500, 30.0) is False
    assert d.update(502, 500, 30.5) is False
    assert d.update(502, 500, 31.05) is True


def test_a_blink_inside_a_dwell_does_not_restart_it():
    d = DwellClicker(40, 1.0)
    d.update(500, 500, 0.0)
    d.update(500, 500, 0.3)
    # eyes closed for 0.4 s: frames not fed
    assert d.update(500, 500, 0.7) is False
    assert d.update(500, 500, 1.0) is True


# --- the dwell ring while no frame is being processed --------------------------
#
# The ring hides itself only from tick(), which runs at the END of a normal
# frame. While the camera is gone (or a frame keeps throwing before the tick)
# a part-filled 'about to click' ring would stay frozen on top of everything.

def test_ring_off_hides_and_never_raises():
    class Ring:
        hides = 0

        def hide(self):
            Ring.hides += 1

    class Broken:
        def hide(self):
            raise RuntimeError("pencere yok")

    gd._ring_off(None)
    gd._ring_off(Broken())
    r = Ring()
    gd._ring_off(r)
    assert Ring.hides == 1


def test_the_ring_is_hidden_while_the_camera_gives_no_frame():
    src = inspect.getsource(gd.main)
    branch = src.index("if not ok:")
    cont = src.index("continue", branch)
    assert "_ring_off(dring)" in src[branch:cont]


def test_the_ring_is_hidden_when_a_frame_throws():
    src = inspect.getsource(gd.main)
    handler = src.index("if _frame_error(frame_errs, exc):")
    sleep = src.index("time.sleep(0.01)", handler)
    assert "_ring_off(dring)" in src[handler:sleep]
