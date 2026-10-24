# gaze/tests/test_ui_settings.py
"""The settings window's cursor, click, magnifier, calibration, sound and
camera controls: every one writes its config.yaml line through the validated
writer, and a control nobody touched writes nothing.

Real Tk, hidden; no mainloop, camera, keyboard or driver (test_ui_wizard_flow's
fakes)."""
import os
import re

import pytest

tk = pytest.importorskip("tkinter")

from eye_gaze import config, hardware, sensitivity  # noqa: E402
from tests.test_ui_wizard_flow import (  # noqa: E402,F401  (fixtures)
    DEFAULT_CONFIG, FakeSup, _shared_root, _write_config, data_dir,
    make_services, make_settings, root)

# v1.2.0's own five filter lines: no cursor_sensitivity at all.
_V120 = {"mincutoff": "0.2", "beta": "0.002", "max_cursor_speed": "1200",
         "deadzone": "20", "gaze_median_seconds": "0.2333333333"}


def _v120_config(data_dir, extra=""):
    """The shipped template as a v1.2.0 install has it: no
    cursor_sensitivity line, the filter lines at the factory numbers."""
    with open(DEFAULT_CONFIG, encoding="utf-8") as f:
        lines = f.read().split("\n")
    out = []
    for line in lines:
        key = line.split(":", 1)[0]
        if key == sensitivity.KEY:
            continue
        if key in _V120:
            line = re.sub(r':\s*"auto"', ": " + _V120[key], line, count=1)
        out.append(line)
    path = os.path.join(str(data_dir), "config.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + extra)
    return path


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _read(path):
    cfg, problem = config.read_config(path, report=lambda m: None)
    assert problem is None
    return cfg


def _saved(s, path):
    assert s.save() is True, s.message_var.get()
    return _read(path)


# --- nothing touched, nothing written ----------------------------------------

@pytest.mark.parametrize("make", [_write_config, _v120_config])
def test_an_untouched_window_writes_nothing(root, data_dir, make):
    path = make(data_dir)
    before = _bytes(path)
    sup = FakeSup()
    s = make_settings(root, make_services(), sup)
    root.update()                       # let every widget draw once
    assert s.pending() == {}
    assert s.save() is True
    assert "Değişiklik yok" in s.message_var.get()
    assert _bytes(path) == before and sup.calls == []


def test_the_window_has_the_new_tabs_in_order(root, data_dir):
    _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    tabs = [s.notebook.tab(t, "text") for t in s.notebook.tabs()]
    assert tabs == ["Tuşlar", "İmleç", "Tıklama", "Büyüteç", "Kalibrasyon",
                    "Kamera", "Ses", "Gelişmiş", "Eksikler",
                    "Dil (Language)"]


# --- cursor sensitivity --------------------------------------------------------

def test_without_the_line_the_level_shows_1_as_today_s_setting(root,
                                                               data_dir):
    path = _v120_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    c = s.controls[sensitivity.KEY]
    assert c.slider.value == 1 and c.auto is False
    assert "mevcut" in c.slider.text()
    # Moving away and back to 1: still nothing to write.
    c.slider.move_to(4)
    assert s.pending() == {sensitivity.KEY: 4}
    c.slider.move_to(1)
    assert s.pending() == {}
    # Otomatik on and off again: back to 1 - still nothing to write.
    c.check.invoke()
    assert s.pending() == {sensitivity.KEY: "auto"}
    c.check.invoke()
    assert c.slider.value == 1 and s.pending() == {}
    c.slider.move_to(2)
    assert _saved(s, path)[sensitivity.KEY] == 2


def test_the_level_and_otomatik_round_trip(root, data_dir):
    path = _v120_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    c = s.controls[sensitivity.KEY]
    c.check.invoke()                    # Otomatik on
    assert c.auto is True and c.slider.value == sensitivity.AUTO_LEVEL
    assert _saved(s, path)[sensitivity.KEY] == "auto"
    c.check.invoke()                    # off again: the slider's level
    c.slider.move_to(5)
    assert _saved(s, path)[sensitivity.KEY] == 5


def test_the_template_s_auto_level_shows_3(root, data_dir):
    _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    c = s.controls[sensitivity.KEY]
    assert c.auto is True and c.slider.value == 3
    assert str(c.slider.scale["state"]) == "disabled"


# --- deadzone / max speed: follow the level or a number -------------------------

def test_a_filter_field_follows_the_level_live(root, data_dir):
    _write_config(data_dir)                         # level auto, deadzone auto
    s = make_settings(root, make_services(), FakeSup())
    dz = s.controls["deadzone"]
    assert dz.auto is True
    assert dz.slider.value == sensitivity.preset(3)["deadzone"]
    s.controls[sensitivity.KEY].check.invoke()      # Otomatik off: level 3
    s.controls[sensitivity.KEY].slider.move_to(5)
    assert dz.slider.value == sensitivity.preset(5)["deadzone"]
    assert "deadzone" not in s.pending()


def test_a_filter_field_number_and_back_to_the_level(root, data_dir):
    path = _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    dz = s.controls["deadzone"]
    dz.check.invoke()                               # explicit now
    dz.slider.move_to(25)
    assert _saved(s, path)["deadzone"] == 25
    assert config.read_config(path, report=lambda m: None,
                              effective=True)[0]["deadzone"] == 25
    dz.check.invoke()                               # follow again
    assert _saved(s, path)["deadzone"] == "auto"


def test_a_factory_number_under_another_level_says_it_follows(root,
                                                             data_dir):
    _write_config(data_dir)                         # level auto = 3
    s = make_settings(root, make_services(), FakeSup())
    dz = s.controls["deadzone"]
    dz.check.invoke()
    dz.slider.move_to(sensitivity.FACTORY["deadzone"])
    assert "fabrika" in dz.note_var.get()
    dz.slider.move_to(22)
    assert dz.note_var.get() == ""


def test_a_v120_factory_line_unticked_and_ticked_writes_nothing(root,
                                                               data_dir):
    path = _v120_config(data_dir)
    before = _bytes(path)
    s = make_settings(root, make_services(), FakeSup())
    for key in ("deadzone", "max_cursor_speed"):
        c = s.controls[key]
        assert c.auto is True                       # 20 / 1200 = factory
        c.check.invoke()
        c.check.invoke()
    assert s.pending() == {}
    assert s.save() is True and _bytes(path) == before


def test_max_speed_zero_is_off_and_shown_as_zero(root, data_dir):
    path = _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    ms = s.controls["max_cursor_speed"]
    ms.check.invoke()
    ms.slider.move_to(0)
    assert "kapalı" in ms.slider.text()
    assert _saved(s, path)["max_cursor_speed"] == 0
    s2 = make_settings(root, make_services(), FakeSup())
    assert s2.controls["max_cursor_speed"].slider.value == 0
    assert s2.controls["max_cursor_speed"].auto is False


# --- the plain sliders ---------------------------------------------------------

SLIDERS = [("snap_radius", 160), ("dwell_seconds", 1.5), ("dwell_radius", 60),
           ("dwell_ring_px", 60), ("blink_hold_seconds", 0.8),
           ("zoom_scale", 0.5), ("zoom_timeout", 12), ("pursuit_speed", 380),
           ("feedback_volume", 0.5), ("tts_speed", 1.5), ("tts_volume", 0.8)]


@pytest.mark.parametrize("key,value", SLIDERS)
def test_each_slider_round_trips_and_keeps_the_comments(root, data_dir, key,
                                                        value):
    path = _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    s.controls[key].move_to(value)
    assert s.pending() == {key: value}
    assert _saved(s, path)[key] == pytest.approx(value)
    with open(path, encoding="utf-8") as f:
        assert "KONUSMA HIZI" in f.read()
    again = make_settings(root, make_services(), FakeSup())
    assert again.controls[key].value == pytest.approx(value)


def test_a_zero_is_shown_as_zero_not_one(root, data_dir):
    path = _write_config(data_dir)
    with open(path, encoding="utf-8") as f:
        text = f.read().replace("feedback_volume: 0.3", "feedback_volume: 0")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    s = make_settings(root, make_services(), FakeSup())
    assert s.controls["feedback_volume"].value == 0
    assert s.controls["feedback_volume"].var.get() == 0
    assert s.pending() == {}


def test_a_line_missing_from_the_file_shows_the_driver_s_own_value(root,
                                                                   data_dir):
    path = _write_config(data_dir)
    with open(path, encoding="utf-8") as f:
        text = "\n".join(ln for ln in f.read().split("\n")
                         if not ln.startswith(("dwell_seconds:",
                                               "zoom_scale:")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    s = make_settings(root, make_services(), FakeSup())
    assert s.controls["dwell_seconds"].value == 1.0
    assert s.controls["zoom_scale"].value == 0.25
    s.controls["dwell_seconds"].move_to(2.0)
    s.controls["dwell_seconds"].move_to(1.0)
    assert s.pending() == {}


def test_a_value_outside_the_slider_s_range_is_shown_and_kept(root,
                                                              data_dir):
    path = _write_config(data_dir)
    with open(path, encoding="utf-8") as f:
        text = f.read().replace("dwell_radius: 40", "dwell_radius: 250")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    before = _bytes(path)
    s = make_settings(root, make_services(), FakeSup())
    c = s.controls["dwell_radius"]
    assert c.value == 250 and c.var.get() == 250
    root.update()
    assert s.save() is True and _bytes(path) == before


# --- on/off and choices ----------------------------------------------------------

@pytest.mark.parametrize("key", ["saccade_jump", "snap_to_targets",
                                 "dwell_ring", "feedback_sounds"])
def test_each_switch_round_trips(root, data_dir, key):
    path = _write_config(data_dir)
    was = _read(path)[key]
    s = make_settings(root, make_services(), FakeSup())
    s.controls[key].invoke()
    assert _saved(s, path)[key] is (not was)


@pytest.mark.parametrize("key,value", [("calibration_mode", "dots"),
                                       ("calibration_path", "grid"),
                                       ("calibration_passes", 1)])
def test_each_choice_round_trips(root, data_dir, key, value):
    path = _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    s.controls[key][value].invoke()
    assert _saved(s, path)[key] == value


def test_a_choice_back_to_the_file_s_spelling_writes_nothing(root, data_dir):
    path = _write_config(data_dir)
    with open(path, encoding="utf-8") as f:
        text = f.read().replace('calibration_path: "rows"',
                                'calibration_path: "satir"')
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    s = make_settings(root, make_services(), FakeSup())
    s.controls["calibration_path"]["grid"].invoke()
    s.controls["calibration_path"]["rows"].invoke()
    assert s.pending() == {}


def test_poly2_is_not_offered(root, data_dir):
    _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    assert "calibration_poly2" not in s.controls


# --- closed-eye threshold / camera field of view: "auto" or a number ------------

def test_the_closed_eye_threshold_auto_or_number(root, data_dir):
    path = _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    c = s.controls["blink_closed_threshold"]
    assert c.auto is True
    c.check.invoke()
    c.slider.move_to(0.18)
    assert "otomatik" in c.note_var.get()
    c.slider.move_to(0.22)
    assert c.note_var.get() == ""
    assert _saved(s, path)["blink_closed_threshold"] == pytest.approx(0.22)
    c.check.invoke()
    assert _saved(s, path)["blink_closed_threshold"] == "auto"


def test_the_camera_field_of_view_says_it_needs_a_calibration(root,
                                                             data_dir):
    path = _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    c = s.controls["camera_hfov_deg"]
    assert c.auto is True
    assert "F9" in c.note_var.get()
    c.check.invoke()
    c.slider.move_to(70)
    assert _saved(s, path)["camera_hfov_deg"] == 70


# --- camera ---------------------------------------------------------------------

def test_the_camera_tab_shows_the_chosen_and_every_measured_mode(root,
                                                                 data_dir):
    _write_config(data_dir)
    modes = [{"width": 1920, "height": 1080, "fps": 30.0, "fourcc": "MJPG",
              "request": [1920, 1080], "ok": True},
             {"width": 1280, "height": 720, "fps": 30.0, "fourcc": "",
              "request": [1280, 720], "ok": True},
             {"width": 640, "height": 480, "fps": 15.0, "fourcc": "",
              "request": [1280, 960], "ok": False}]
    hardware.save_profile({"camera": {
        "index": 0, "backend": "msmf", "width": 1920, "height": 1080,
        "fps": 30, "fps_measured": 29.7, "name": "", "fourcc": "MJPG",
        "modes": modes}})
    s = make_settings(root, make_services(), FakeSup())
    mode = s.camera_mode_var.get()
    assert "1920x1080" in mode and "MJPG" in mode and "29,7" in mode
    listed = s.camera_modes_var.get()
    assert "1280x720" in listed and "640x480" in listed
    assert "seçili" in listed
    assert "F9" in s.camera_upgrade_note


def test_a_searched_camera_shows_its_measured_modes(root, data_dir):
    _write_config(data_dir)
    cam = hardware.CameraInfo(
        1, "dshow", 1920, 1080, 30.0, "", "MJPG",
        ({"width": 1920, "height": 1080, "fps": 30.0, "fourcc": "MJPG",
          "request": [1920, 1080], "ok": True},), None, None)
    s = make_settings(root, make_services(cameras=[cam]), FakeSup())
    s.search_cameras()
    s._pick_camera(0)
    assert "1920x1080" in s.camera_modes_var.get()


# --- the language switch keeps what was changed --------------------------------

def test_a_rebuild_keeps_the_changed_controls(root, data_dir):
    _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    s.controls["dwell_seconds"].move_to(2.0)
    s.controls["deadzone"].check.invoke()
    s.controls["deadzone"].slider.move_to(30)
    s._build_content()
    assert s.controls["dwell_seconds"].value == 2.0
    assert s.controls["deadzone"].auto is False
    assert s.controls["deadzone"].slider.value == 30
    assert s.pending() == {"dwell_seconds": 2.0, "deadzone": 30}


# --- keyboard: every new control takes the focus --------------------------------

def test_every_new_control_is_reachable_by_tab(root, data_dir):
    _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    for key, c in s.controls.items():
        if isinstance(c, dict):                 # a choice: every option
            ws = list(c.values())
        elif hasattr(c, "slider"):              # "auto" or a number
            ws = [c.check, c.slider.scale]
        elif hasattr(c, "scale"):               # a slider
            ws = [c.scale]
        else:                                   # a switch
            ws = [c]
        for w in ws:
            assert str(w.cget("takefocus")) in ("", "1"), key
            assert int(w.cget("highlightthickness")) >= 3, key


def test_every_slider_lies_inside_what_the_loader_accepts():
    from eye_gaze.ui import settings
    for key, (lo, hi, step, kind) in settings.SLIDERS.items():
        if key == sensitivity.KEY:
            assert (lo, hi) == (sensitivity.LEVELS[0], sensitivity.LEVELS[-1])
            continue
        rlo, rhi = config.RANGES[key]
        assert rlo <= lo < hi <= rhi, key
        assert step > 0 and kind in (int, float), key


def test_the_window_fits_a_1280_wide_screen(root, data_dir):
    _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    s.win.update_idletasks()
    assert s.win.winfo_reqwidth() <= 1240


# --- small screens: the whole window, Kaydet included, on the work area -------

AREAS = [(0, 0, 1024, 700), (0, 0, 1280, 680), (0, 0, 1366, 728),
         (0, 0, 1093, 700), (0, 0, 1093, 614), (0, 0, 2560, 1400),
         (100, 40, 1024, 700)]


def _fits(win, area, footer, content):
    """req size inside the work area (minus the frame and title bar), the
    footer packed before the content so a squeeze never takes it."""
    from eye_gaze.ui import widgets
    win.update_idletasks()
    aw, ah = area[2] - widgets.DECOR[0], area[3] - widgets.DECOR[1]
    assert win.winfo_reqwidth() <= aw, (win.winfo_reqwidth(), area)
    assert win.winfo_reqheight() <= ah, (win.winfo_reqheight(), area)
    slaves = footer.master.pack_slaves()
    assert slaves.index(footer) < slaves.index(content)
    maxw, maxh = (int(v) for v in win.maxsize())
    minw, minh = (int(v) for v in win.minsize())
    assert maxw <= aw and maxh <= ah and minw <= aw and minh <= ah
    geo = win.geometry()            # "WxH+X+Y"; the position is ours
    x, y = (int(v) for v in geo.split("+")[1:3])
    assert area[0] <= x and area[1] <= y


@pytest.mark.parametrize("area", AREAS)
@pytest.mark.parametrize("lang", ["tr", "en"])
def test_the_settings_window_fits_the_work_area(root, data_dir, monkeypatch,
                                                area, lang):
    from eye_gaze import i18n
    from eye_gaze.ui import widgets
    monkeypatch.setattr(widgets, "work_area", lambda win: area)
    i18n.set_language(lang)
    _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    _fits(s.win, area, s.footer, s.notebook)
    if area[2] >= 1920:                 # a large screen keeps the roomy look
        assert s.win.winfo_reqheight() >= 760
    s._build_content()                  # a language switch builds again
    _fits(s.win, area, s.footer, s.notebook)


@pytest.mark.parametrize("lang", ["tr", "en"])
def test_a_narrow_screen_narrows_the_tab_strip_and_a_wide_one_gets_it_back(
        root, data_dir, monkeypatch, lang):
    """Linux fonts made the one-row tab strip wider than a 1024 px screen.
    There it gives up padding, the language tab's bilingual part and a few
    points of font (never below TAB_FONT_MIN); a wide screen keeps all."""
    from tkinter import ttk
    import tkinter.font as tkfont
    from eye_gaze import i18n
    from eye_gaze.ui import settings, widgets
    i18n.set_language(lang)
    _write_config(data_dir)
    tab = settings.TAB_STYLE + ".Tab"

    def build(area):
        monkeypatch.setattr(widgets, "work_area", lambda win: area)
        s = make_settings(root, make_services(), FakeSup())
        _fits(s.win, area, s.footer, s.notebook)
        names = [s.notebook.tab(x, "text") for x in s.notebook.tabs()]
        return s, names, str(ttk.Style(s.win).lookup(tab, "font"))

    s, names, font = build((0, 0, 1024, 700))
    assert len(names) == 10 and all(names)
    assert names[-1] in (i18n.t("settings.tab.language"),
                         i18n.t("settings.tab_short.language"))
    if font != "TkDefaultFont":
        assert tkfont.nametofont(font, root=root).actual()["size"] >=             settings.TAB_FONT_MIN
    s, names, font = build((0, 0, 2560, 1400))
    assert names[-1] == i18n.t("settings.tab.language")
    assert font == "TkDefaultFont"
    pad = ttk.Style(s.win).lookup(tab, "padding")
    pad = pad if isinstance(pad, (tuple, list)) else str(pad).split()
    assert str(pad[0]) == str(settings.TAB_PADDING[0])


def test_the_tabs_change_by_keyboard(root, data_dir):
    _write_config(data_dir)
    s = make_settings(root, make_services(), FakeSup())
    # ttk's enable_traversal: Ctrl+Tab / Ctrl+Shift+Tab on the window.
    assert s.win.bind("<Control-Key-Tab>")


def test_the_work_area_never_raises(root, monkeypatch):
    from eye_gaze.ui import widgets

    def broken():
        raise OSError("no user32")
    monkeypatch.setattr(widgets, "_windows_work_area", broken)
    x, y, w, h = widgets.work_area(root)
    assert w > 0 and h > 0


@pytest.mark.parametrize("area", AREAS)
def test_the_wizard_fits_the_work_area(root, data_dir, monkeypatch, area):
    from tests.test_ui_wizard_flow import make_wizard
    from eye_gaze.ui import widgets
    monkeypatch.setattr(widgets, "work_area", lambda win: area)
    _write_config(data_dir)
    w, _ = make_wizard(root, make_services())
    for i in range(len(w.steps)):
        w._show(i)
        _fits(w.win, area, w.foot, w.body_scroll)


# 1.2.0's own heights of the wizard's two lists (KeyTable 380, Downloads 400).
_LISTS_120 = {"tuslar": 380, "indirmeler": 400}


def _wizard_list(w, name):
    step = w.step(name)
    return step, (step.table.scroll if name == "tuslar" else step.scroll)


@pytest.mark.parametrize("area", [(0, 0, 1536, 800), (0, 0, 1536, 824),
                                  (0, 0, 1920, 1040), (0, 0, 2560, 1400)])
@pytest.mark.parametrize("lang", ["tr", "en"])
def test_the_wizard_lists_keep_their_1_2_0_height(root, data_dir,
                                                  monkeypatch, area, lang):
    # Screens 1.2.0 fitted on: the Keys and Downloads lists at least as
    # tall as then, the page holding the step without a second scrollbar.
    from tests.test_ui_wizard_flow import make_wizard
    from eye_gaze import i18n
    from eye_gaze.ui import widgets
    monkeypatch.setattr(widgets, "work_area", lambda win: area)
    i18n.set_language(lang)
    _write_config(data_dir)
    w, _ = make_wizard(root, make_services())
    page = int(w.body_scroll.canvas.cget("height"))
    for name, least in _LISTS_120.items():
        w._show(w.steps.index(w.step(name)))
        step, scroll = _wizard_list(w, name)
        assert int(scroll.canvas.cget("height")) >= least, (name, area)
        assert step.frame.winfo_reqheight() <= page, (name, area)
        assert w.body_scroll.bar.winfo_manager() == "", (name, area)
    _fits(w.win, area, w.foot, w.body_scroll)


@pytest.mark.parametrize("area", [(0, 0, 1024, 700), (0, 0, 1280, 680)])
def test_on_a_small_screen_the_wizard_lists_fill_the_page(root, data_dir,
                                                          monkeypatch, area):
    # No double shrink: the list gets the page's height its step leaves,
    # so the step fills the page - no empty band, no second scrollbar.
    from tests.test_ui_wizard_flow import make_wizard
    from eye_gaze.ui import widgets
    monkeypatch.setattr(widgets, "work_area", lambda win: area)
    _write_config(data_dir)
    w, _ = make_wizard(root, make_services())
    page = int(w.body_scroll.canvas.cget("height"))
    for name in _LISTS_120:
        w._show(w.steps.index(w.step(name)))
        step, scroll = _wizard_list(w, name)
        assert int(scroll.canvas.cget("height")) >= widgets.MIN_SCROLL
        assert page - 2 <= step.frame.winfo_reqheight() <= page, name
        assert w.body_scroll.bar.winfo_manager() == "", name
