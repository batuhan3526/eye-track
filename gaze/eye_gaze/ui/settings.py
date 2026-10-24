# gaze/eye_gaze/ui/settings.py
"""The settings window (spec 6.7): the wizard's pieces in tabs, any time.

  Tuşlar      every catalog job, "Değiştir" (the driver is stopped while
              the key is caught, then started again), conflicts shown live
  İmleç       cursor sensitivity 1-5 / Otomatik, deadzone and top speed
              (each follows the level or holds a number), target snapping
              and its distance, saccade jump
  Tıklama     switch / dwell / blink, and their times and sizes; the
              closed-eye threshold (Otomatik or a number)
  Büyüteç     how much of the screen it enlarges, when it closes itself
  Kalibrasyon method, ball path, ball speed, one or two passes - for the
              next calibration (calibration_poly2 is not offered: with it on,
              the default, each axis takes quadratic terms only when the
              person's own data says they help - eye_gaze.modelsel)
  Kamera      the mode in use and every mode the probe measured, the
              cameras found now (the driver is stopped for the search), the
              lens's field of view (Otomatik or a number)
  Ses         click sounds and their volume, speech speed and volume
  Gelişmiş    deep model, Whisper size, start with the computer
  Eksikler    every capability that is missing on this computer, and why
  Dil         the program's language: on Kaydet the launcher's windows switch
              at once (this one is built again), the driver at the restart
              Kaydet gives it

Only what the person changed is written: every control compares with the
value the file had when the window opened, and a control put back where it
was - or on a value that means the same (a missing line and the driver's own
fallback, "auto" and the v1.2.0 factory number) - writes nothing. A
config.yaml without cursor_sensitivity (every v1.2.0 install) therefore never
gets the line unless the level is moved.

Kaydet: conflicts block the save and say why (keymap.find_conflicts, the
same rule the driver applies at start); a config with no click route that
works NOW (a gamepad binding counts only with a gamepad plugged in) needs a
yes; then config_writer.set_values (comments kept, read back, refused for a
read-only file, a value outside config.RANGES or the last click route) and
Supervisor.restart().
"""
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from eye_gaze import camera_modes
from eye_gaze import config as _config
from eye_gaze import hardware, i18n, openness, pursuit, sensitivity
from eye_gaze.actions_catalog import by_key
from eye_gaze.i18n import t
from eye_gaze.ui import controls, widgets
from eye_gaze.ui.widgets import BG

_MISSING = object()
SENS = sensitivity.KEY

# The window on any screen (widgets.fit_to_work_area): pages PAGE_H tall where
# there is room, shorter (they scroll) where there is not. The tab strip is
# what makes the window wide (ttk tabs have one row; Linux fonts are wider
# than Segoe UI), so where it does not fit it gives up, in this order: its
# side padding, the language tab's bilingual part ("Dil (Language)" ->
# "Dil"), then a few points of its font - never below TAB_FONT_MIN.
PAGE_H = 440
MIN_SIZE = (1000, 760)
TAB_STYLE = "Settings.TNotebook"
TAB_PADDING = (16, 8, 4, 2)
TAB_FONT_SIZES = (14, 13, 12)
TAB_FONT_MIN = TAB_FONT_SIZES[-1]

# Sliders: (lo, hi, step, kind) - inside config.RANGES (a file value outside
# them widens the slider instead of being moved).
SLIDERS = {
    SENS: (1, 5, 1, int),
    "deadzone": (0, 60, 1, int),
    "max_cursor_speed": (0, 4000, 50, int),
    "snap_radius": (40, 300, 10, int),
    "dwell_seconds": (0.3, 4.0, 0.1, float),
    "dwell_radius": (10, 150, 5, int),
    "dwell_ring_px": (20, 120, 5, int),
    "blink_hold_seconds": (0.2, 2.0, 0.05, float),
    "blink_closed_threshold": (0.08, 0.35, 0.01, float),
    "zoom_scale": (0.15, 0.8, 0.05, float),
    "zoom_timeout": (3, 30, 1, int),
    "pursuit_speed": (200, 900, 20, int),
    "feedback_volume": (0.0, 1.0, 0.05, float),
    "tts_speed": (0.8, 2.0, 0.05, float),
    "tts_volume": (0.3, 1.0, 0.05, float),
    "camera_hfov_deg": (30, 150, 1, int),
}

# What the driver uses when config.yaml has no such line (gaze_driver's
# cfg.get fallbacks; the rest are in config.DEFAULTS): shown then, and
# choosing it writes nothing.
FALLBACK = {
    "dwell_seconds": 1.0, "dwell_radius": 40.0, "blink_hold_seconds": 0.5,
    "zoom_scale": 0.25, "zoom_timeout": 8.0, "pursuit_speed": 480.0,
    "tts_speed": 1.0, "tts_volume": 1.0, "calibration_mode": "pursuit",
    "calibration_passes": 2,
}

_LEVELS = {1: "settings.cursor.level_1", 2: "settings.cursor.level_2",
           3: "settings.cursor.level_3", 4: "settings.cursor.level_4",
           5: "settings.cursor.level_5"}


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _same_number(a, b):
    fa, fb = _number(a), _number(b)
    return fa is not None and fb is not None and abs(fa - fb) < 1e-9


def _quiet(_message):
    pass


def _hfov(value):
    return camera_modes.hfov_setting(value, say=_quiet)


def _mode_name(value):
    v = str(value or "").strip().lower()
    return "dots" if v.startswith(("dot", "nokta")) else "pursuit"


def _passes(value):
    f = _number(value)
    return max(1, int(round(f))) if f is not None else FALLBACK[
        "calibration_passes"]


def _unit(key):
    return lambda v: t(key, value=widgets._num(v))


def _px(v):
    return t("settings.unit.px", value=widgets._num(v))


def _off_or(fmt):
    return lambda v: t("settings.turned_off") if not _number(v) else fmt(v)


def _stt_sizes():
    return (("auto", t("settings.stt_auto")), ("tiny", "tiny"),
            ("base", "base"), ("small", "small"), ("medium", "medium"))


def _deep_choices():
    return (("auto", t("settings.deep_auto")), ("on", t("settings.deep_on")),
            ("off", t("settings.deep_off")))


def _deep_choice(v):
    if v is True:
        return "on"
    if v is False:
        return "off"
    return "auto"


class SettingsWindow:
    def __init__(self, parent, services, supervisor, ask=None, tell=None,
                 worker=None, on_close=None, on_language=None):
        self.services = services
        self.sup = supervisor
        self.on_close = on_close
        self.on_language = on_language
        self.win = widgets.toplevel(parent, t("settings.window_title"))
        widgets.apply_theme(self.win)
        d_ask, d_tell = widgets.dialogs(self.win)
        self.ask = ask or d_ask
        self.tell = tell or d_tell
        self.worker = worker or widgets.Worker(self.win)
        cfg, problem = services.read_config()
        self.values = dict(cfg)
        self.original = dict(cfg)
        self.raw = dict(services.read_file_values())
        self.controls = {}
        self.profile = dict(services.load_profile() or {})
        self.camera_choice = None
        self.autostart_on = bool(services.autostart_enabled())
        self.caps = services.capabilities()
        self.message_var = tk.StringVar(self.win, problem or "")
        self._vars = {}
        self._outer = None
        self._build_content()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        widgets.show(self.win)

    def _build_content(self):
        """Everything inside the window, in the current language (built
        again after a language change; values / original / profile stay)."""
        first = self._outer is None
        if not first:
            self._outer.destroy()
        self._vars = {}
        self.controls = {}
        self._scrolls = []
        self.win.title(t("settings.window_title"))
        self._outer = outer = tk.Frame(self.win, bg=BG, padx=20, pady=16)
        outer.pack(fill="both", expand=True)
        self.note_var = tk.StringVar(self.win, "")
        if self.services.mode() == "source":
            self.note_var.set(t("settings.source_note"))
            widgets.text(outer, "warn", textvariable=self.note_var).pack(
                fill="x", pady=(0, 8))
        self.notebook = ttk.Notebook(outer, style=TAB_STYLE)
        self.notebook.pack(fill="both", expand=True)
        # Ctrl+Tab / Ctrl+Shift+Tab change the tab from anywhere in the
        # window (a narrow screen may also cut the last tabs off).
        self.notebook.enable_traversal()
        for key, build in (("keys", self._build_keys),
                           ("cursor", self._build_cursor),
                           ("click", self._build_click),
                           ("zoom", self._build_zoom),
                           ("calibration", self._build_calibration),
                           ("camera", self._build_camera),
                           ("sound", self._build_sound),
                           ("advanced", self._build_advanced),
                           ("missing", self._build_missing),
                           ("language", self._build_language)):
            tab = tk.Frame(self.notebook, bg=BG, padx=16, pady=12)
            build(tab)
            self.notebook.add(tab, text=t("settings.tab." + key))
            if key == "language":
                self._language_tab = tab
        # The button row and the message are packed BEFORE the tabs (made
        # after them, so Tab still reaches the tabs first): a window held
        # smaller than its content takes the height from the tabs, whose
        # pages scroll, never from Kaydet.
        self.footer = foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", side="bottom", before=self.notebook)
        widgets.button(foot, t("settings.save"), self.save,
                       primary=True).pack(side="right")
        widgets.button(foot, t("settings.close"), self.close).pack(
            side="right", padx=12)
        widgets.text(outer, "bold", textvariable=self.message_var).pack(
            fill="x", pady=(10, 4), side="bottom", before=self.notebook)
        self._fit(place=first)

    def _fit(self, place):
        """The whole window on the work area: narrower tabs first (see
        TAB_PADDING), then shorter pages (widgets.fit_to_work_area)."""
        area = widgets.work_area(self.win)
        room = area[2] - widgets.DECOR[0]
        style = ttk.Style(self.win)
        last = TAB_PADDING[-1]
        steps = ([(pad, None, False) for pad in TAB_PADDING]
                 + [(last, None, True)]
                 + [(last, size, True) for size in TAB_FONT_SIZES])
        for pad, size, short in steps:
            style.configure(TAB_STYLE + ".Tab", padding=(pad, 10),
                            font=self._tab_font(size))
            self._tab_labels(short)
            self.win.update_idletasks()
            if self.win.winfo_reqwidth() <= room:
                break
        widgets.fit_to_work_area(self.win, self._scrolls, MIN_SIZE, area,
                                 place=place)

    def _tab_font(self, size):
        """The tab strip's font: the default one (None) or a smaller copy,
        cached on the root - the style outlives this window."""
        if size is None:
            return "TkDefaultFont"
        root = self.win._root()
        cache = getattr(root, "_gozfaresi_tab_fonts", None)
        if cache is None:
            cache = root._gozfaresi_tab_fonts = {}
        if size not in cache:
            fam = widgets.fonts(root)["body"].actual().get("family")
            cache[size] = tkfont.Font(root=root, family=fam, size=int(size))
        return cache[size]

    def _tab_labels(self, short):
        """The language tab names the other language too, so a person who
        cannot read this one finds it; `short` drops that part."""
        key = "settings.tab_short.language" if short else             "settings.tab.language"
        try:
            self.notebook.tab(self._language_tab, text=t(key))
        except (tk.TclError, AttributeError):
            pass

    # --- the driver around a capture or a camera search -------------------

    def _pause_driver(self):
        # Supervisor.pause() nests and also holds a crash restart that is
        # due: a capture during a camera search, or a driver that would
        # come back mid-capture, cannot take the camera or the keys back.
        self.sup.pause()

    def _resume_driver(self):
        self.sup.resume()

    # --- tabs ----------------------------------------------------------------

    def _build_keys(self, tab):
        self.keys = widgets.KeyTable(
            tab, self.services, self.values, self.ask, self.tell,
            self.worker, before_capture=self._pause_driver,
            after_capture=self._resume_driver, height=430)
        self.keys.pack(fill="both", expand=True)
        self._scrolls.append(self.keys.scroll)

    # --- building blocks ----------------------------------------------------

    def _put(self, key, new, same):
        """values[key] = new - unless same(file's value or None, new): then
        the file's own value (or no line, as the file had none) stays, and
        nothing is written for it."""
        orig = self.original.get(key, _MISSING)
        if same(None if orig is _MISSING else orig, new):
            if orig is _MISSING:
                self.values.pop(key, None)
            else:
                self.values[key] = orig
        else:
            self.values[key] = new

    def _scrolled(self, tab):
        scroll = widgets.ScrollFrame(tab, height=PAGE_H)
        scroll.pack(fill="both", expand=True)
        self._scrolls.append(scroll)
        return scroll.inner

    def _heading(self, parent, key):
        widgets.text(parent, "bold", value=t(key)).pack(anchor="w",
                                                        pady=(16, 0))

    def _note(self, parent, key):
        widgets.text(parent, "body", value=t(key), fg=widgets.DISABLED).pack(
            anchor="w", pady=(2, 0))

    def _slider(self, parent, key, title, fmt=None):
        """One number; the file's value, else the driver's own (FALLBACK /
        DEFAULTS) - a 0 stays 0."""
        lo, hi, step, kind = SLIDERS[key]
        default = FALLBACK.get(key, _config.DEFAULTS.get(key, lo))
        v = self.values.get(key)
        value = v if _number(v) is not None else default

        def same(o, n):
            return (o is None and _same_number(n, default)) \
                or _same_number(o, n)

        c = controls.Slider(parent, title, lo, hi, step, value,
                            on_change=lambda n: self._put(key, n, same),
                            kind=kind, fmt=fmt)
        c.pack(anchor="w", fill="x", pady=(12, 0))
        self.controls[key] = c
        return c

    def _switch(self, parent, key, label, pady=(12, 0)):
        default = _config.DEFAULTS.get(key, False)
        var = tk.BooleanVar(parent, self.values.get(key, default) is True)
        self._vars[key] = var

        def chosen():
            self._put(key, bool(var.get()), lambda o, n: (
                n == (default if o is None else o)))
        w = widgets.check(parent, label, var, command=chosen)
        w.pack(anchor="w", pady=pady)
        self.controls[key] = w
        return w

    def _choice(self, parent, key, title, options, current, same):
        """Radio buttons, one per (value, label); `current` the one shown."""
        self._heading(parent, title)
        var = tk.StringVar(parent, str(current))
        self._vars["_" + key] = var
        by_text = {str(v): v for v, _ in options}
        row = tk.Frame(parent, bg=BG)
        row.pack(anchor="w")
        buttons = {}
        for value, label in options:
            b = widgets.radio(row, label, var, str(value), command=lambda: (
                self._put(key, by_text[var.get()], same)))
            b.pack(anchor="w", pady=(6, 0))
            buttons[value] = b
        self.controls[key] = buttons
        return buttons

    def _auto_number(self, parent, key, title, auto_label, auto, number,
                     changed, fmt=None):
        lo, hi, step, kind = SLIDERS[key]
        c = controls.AutoNumber(parent, title, auto_label, lo, hi, step,
                                auto, number, changed, kind=kind, fmt=fmt)
        c.pack(anchor="w", fill="x", pady=(12, 0))
        self.controls[key] = c
        return c

    # --- İmleç ---------------------------------------------------------------

    def _level(self):
        """The level config.yaml will say, or None without the line."""
        if SENS not in self.values:
            return None
        return sensitivity.level_of(self.values[SENS]) \
            or sensitivity.AUTO_LEVEL

    def _effective(self):
        """The five filter values the driver would run with (reference px),
        as the file stands with this window's changes."""
        live = {k: v for k, v in self.values.items()
                if k in self.raw or self.original.get(k, _MISSING) != v}
        cfg = dict(self.values)
        sensitivity.resolve(cfg, live)
        for k, v in sensitivity.MISSING.items():
            cfg.setdefault(k, v)
        return cfg

    def _level_text(self, n):
        if SENS not in self.values and n == 1:
            return t("settings.cursor.level_now")
        if _config.is_auto(self.values.get(SENS)):
            return t("settings.cursor.level_auto")
        return t(_LEVELS.get(int(n), "settings.cursor.level_3"))

    def _build_cursor(self, tab):
        body = self._scrolled(tab)
        lvl = self._level()
        auto = _config.is_auto(self.values.get(SENS))
        self._auto_number(body, SENS, t("settings.cursor.sensitivity"),
                          t("settings.cursor.sens_auto"), auto, lvl or 1,
                          self._sensitivity_changed, fmt=self._level_text)
        self.sens_note_var = tk.StringVar(body, "")
        widgets.text(body, "body", textvariable=self.sens_note_var,
                     fg=widgets.DISABLED).pack(anchor="w", pady=(4, 0))
        self._note(body, "settings.cursor.about")
        eff = self._effective()
        for key, title, fmt in (
                ("deadzone", "settings.cursor.deadzone", _off_or(_px)),
                ("max_cursor_speed", "settings.cursor.max_speed",
                 lambda v: t("settings.cursor.speed_off") if not _number(v)
                 else t("settings.unit.px_s", value=widgets._num(v)))):
            v = self.values.get(key)
            follows = sensitivity.follows(key, v)
            self._auto_number(
                body, key, t(title), t("settings.cursor.follow"), follows,
                eff[key] if follows else v,
                lambda a, n, key=key: self._filter_changed(key, a, n),
                fmt=fmt)
        self._note(body, "settings.cursor.px_note")

        self._heading(body, "settings.cursor.snap_title")
        ok, why = self.caps.get("snap", (True, ""))
        c = self._switch(body, "snap_to_targets", t("settings.advanced.snap"),
                         pady=(6, 0))
        if not ok:
            c.configure(state="disabled")
            widgets.text(body, "body", value=why, fg=widgets.DISABLED).pack(
                anchor="w")
        self._slider(body, "snap_radius", t("settings.cursor.snap_radius"),
                     fmt=_px)
        self._switch(body, "saccade_jump", t("settings.cursor.saccade"),
                     pady=(16, 0))
        self._refresh_cursor()

    def _sensitivity_changed(self, auto, n):
        c = self.controls[SENS]
        if auto:
            c.show(sensitivity.AUTO_LEVEL)
            self._put(SENS, sensitivity.AUTO,
                      lambda o, new: _config.is_auto(o))
        else:
            self._put(SENS, int(n), lambda o, new: (
                (o is None and new == 1)
                or (not _config.is_auto(o)
                    and sensitivity.level_of(o) == new)))
        self._refresh_cursor()

    def _filter_changed(self, key, auto, n):
        follows = sensitivity.follows
        if auto:
            self._put(key, sensitivity.AUTO, lambda o, new: follows(key, o))
        else:
            self._put(key, n, lambda o, new: (
                (follows(key, o) and follows(key, new))
                or (o is None and SENS not in self.values
                    and _same_number(new, sensitivity.MISSING[key]))
                or (not _config.is_auto(o) and _same_number(o, new))))
        self._refresh_cursor()

    def _refresh_cursor(self):
        """The filter fields and the level's lines after any change."""
        eff = self._effective()
        lvl = self._level()
        for key in ("deadzone", "max_cursor_speed"):
            c = self.controls.get(key)
            if c is None:
                continue
            if c.auto:
                c.show(eff[key])
                c.note_var.set("")
            elif lvl not in (None, 1) and sensitivity.follows(
                    key, c.slider.value):
                c.note_var.set(t(
                    "settings.cursor.factory_note",
                    value=c.slider.fmt(c.slider.value), level="%d" % lvl,
                    follow=c.slider.fmt(sensitivity.preset(lvl)[key])))
            else:
                c.note_var.set("")
        sens = self.controls.get(SENS)
        if sens is not None:
            sens.slider.relabel()
            if lvl is None:
                note = t("settings.cursor.absent_note")
            else:
                over = sensitivity.overrides(eff)
                note = (t("settings.cursor.overrides", keys=", ".join(over))
                        if over else "")
            self.sens_note_var.set(note)

    # --- Tıklama ---------------------------------------------------------------

    def _build_click(self, tab):
        body = self._scrolled(tab)
        var = tk.StringVar(body, widgets.method_of(self.values))
        self._vars["_method"] = var

        def chosen():
            for k, v in widgets.method_values(var.get()).items():
                self.values[k] = v
        for value, label, note in widgets.click_methods():
            widgets.radio(body, label, var, value, command=chosen).pack(
                anchor="w", pady=(8, 0))
            widgets.text(body, "body", value=note, fg=widgets.DISABLED,
                         padx=40).pack(anchor="w")

        seconds = _unit("settings.unit.s")
        self._heading(body, "settings.click.dwell_title")
        self._slider(body, "dwell_seconds", t("settings.click.dwell_seconds"),
                     fmt=seconds)
        self._slider(body, "dwell_radius", t("settings.click.dwell_radius"),
                     fmt=_px)
        self._switch(body, "dwell_ring", t("settings.click.dwell_ring"))
        self._slider(body, "dwell_ring_px", t("settings.click.dwell_ring_px"),
                     fmt=_px)

        self._heading(body, "settings.click.blink_title")
        self._slider(body, "blink_hold_seconds",
                     t("settings.click.blink_hold"), fmt=seconds)
        key = "blink_closed_threshold"
        fixed = openness.parse(self.values.get(key))
        self._auto_number(
            body, key, t("settings.click.closed"),
            t("settings.click.closed_auto"), fixed is None,
            openness.FACTORY if fixed is None else fixed,
            self._closed_changed, fmt=widgets._num)

    def _closed_changed(self, auto, n):
        key = "blink_closed_threshold"

        def same(o, new):
            a, b = openness.parse(o), openness.parse(new)
            return (a is None and b is None) or (
                a is not None and b is not None and abs(a - b) < 1e-9)
        self._put(key, "auto" if auto else n, same)
        self.controls[key].note_var.set(
            t("settings.click.closed_note")
            if not auto and openness.parse(n) is None else "")

    # --- Büyüteç -------------------------------------------------------------

    def _build_zoom(self, tab):
        body = self._scrolled(tab)

        def area(v):
            f = _number(v) or 0.25
            return t("settings.zoom.scale_value", pct="%d" % round(f * 100),
                     times=widgets._num(round(1.0 / f, 1)))
        self._slider(body, "zoom_scale", t("settings.zoom.scale"), fmt=area)
        self._slider(body, "zoom_timeout", t("settings.zoom.timeout"),
                     fmt=_unit("settings.unit.s"))
        self._note(body, "settings.zoom.note")

    # --- Kalibrasyon -----------------------------------------------------------

    def _build_calibration(self, tab):
        body = self._scrolled(tab)
        self._note(body, "settings.calibration.note")
        self._choice(body, "calibration_mode", "settings.calibration.mode",
                     (("pursuit", t("settings.calibration.mode_pursuit")),
                      ("dots", t("settings.calibration.mode_dots"))),
                     _mode_name(self.values.get("calibration_mode")),
                     lambda o, n: _mode_name(o) == n)
        self._choice(body, "calibration_path", "settings.calibration.path",
                     (("rows", t("settings.calibration.path_rows")),
                      ("grid", t("settings.calibration.path_grid"))),
                     pursuit.layout_of(self.values.get("calibration_path"),
                                       say=_quiet),
                     lambda o, n: pursuit.layout_of(o, say=_quiet) == n)
        self._slider(body, "pursuit_speed", t("settings.calibration.speed"),
                     fmt=lambda v: t("settings.unit.px_s",
                                     value=widgets._num(v)))
        now = _passes(self.values.get("calibration_passes"))
        options = [(1, t("settings.calibration.passes_1")),
                   (2, t("settings.calibration.passes_2"))]
        if now not in (1, 2):
            options.append((now, t("settings.calibration.passes_n",
                                   n="%d" % now)))
        self._choice(body, "calibration_passes", "settings.calibration.passes",
                     options, now, lambda o, n: _passes(o) == n)

    # --- Kamera ----------------------------------------------------------------

    def _camera_now(self):
        idx = self.values.get("camera_index")
        cam = self.profile.get("camera") or {}
        if _config.is_auto(idx):
            if cam:
                return t("settings.camera.now_auto",
                         index="%s" % (cam.get("index"),),
                         width="%s" % (cam.get("width"),),
                         height="%s" % (cam.get("height"),))
            return t("settings.camera.now_searching")
        return t("settings.camera.now_fixed", index="%s" % (idx,))

    def _mode_text(self, rec):
        """The mode in use: size, format, measured rate, camera."""
        if not rec or not rec.get("width"):
            text = t("settings.camera.mode_none")
        else:
            fourcc = str(rec.get("fourcc") or "")
            text = t("settings.camera.mode",
                     size="%sx%s" % (rec.get("width"), rec.get("height")),
                     fourcc=" " + fourcc if fourcc else "",
                     fps=widgets._num(round(float(
                         rec.get("fps_measured") or rec.get("fps") or 0), 1)),
                     index="%s" % (rec.get("index"),),
                     backend=str(rec.get("backend") or "-"))
        w, h = (self.values.get("camera_width"),
                self.values.get("camera_height"))
        if _number(w) and _number(h):
            text += " " + t("settings.camera.forced", width="%d" % _number(w),
                            height="%d" % _number(h))
        return text

    def _modes_text(self, rec):
        """Every mode the probe measured, the one in use marked."""
        modes = (rec or {}).get("modes") or []
        if not modes:
            return t("settings.camera.no_modes")
        lines = [t("settings.camera.modes")]
        for m in modes:
            fourcc = str(m.get("fourcc") or "")
            line = "• " + t("settings.camera.mode_item",
                            size="%sx%s" % (m.get("width"), m.get("height")),
                            fourcc=" " + fourcc if fourcc else "",
                            fps=widgets._num(round(float(m.get("fps", 0)),
                                                   1)))
            if (m.get("width"), m.get("height"), fourcc) == (
                    rec.get("width"), rec.get("height"),
                    str(rec.get("fourcc") or "")):
                line += t("settings.camera.chosen")
            if not m.get("ok", True):
                line += t("settings.camera.stretched")
            elif float(m.get("fps", 0)) < hardware.MIN_FPS:
                line += t("settings.camera.slow")
            lines.append(line)
        return "\n".join(lines)

    def _show_camera(self, rec):
        self.camera_mode_var.set(self._mode_text(rec))
        self.camera_modes_var.set(self._modes_text(rec))

    def _build_camera(self, tab):
        body = self._scrolled(tab)
        self.camera_var = tk.StringVar(body, self._camera_now())
        widgets.text(body, "body", textvariable=self.camera_var).pack(
            anchor="w")
        self.camera_mode_var = tk.StringVar(body, "")
        self.camera_modes_var = tk.StringVar(body, "")
        widgets.text(body, "bold", textvariable=self.camera_mode_var).pack(
            anchor="w", pady=(8, 0))
        widgets.text(body, "body", textvariable=self.camera_modes_var).pack(
            anchor="w", pady=(4, 0))
        rec = (hardware.camera_record(self.camera_choice)
               if self.camera_choice is not None
               else self.profile.get("camera") or {})
        self._show_camera(rec)
        self.camera_upgrade_note = t("settings.camera.upgrade")
        widgets.text(body, "body", value=self.camera_upgrade_note).pack(
            anchor="w", pady=(8, 0))
        widgets.text(body, "body", value=t("settings.camera.note"),
                     fg=widgets.DISABLED).pack(anchor="w", pady=8)
        self.search_button = widgets.button(body, t("settings.camera.search"),
                                            self.search_cameras)
        self.search_button.pack(anchor="w", pady=4)
        self.camera_list = tk.Frame(body, bg=BG)
        self.camera_list.pack(anchor="w", fill="x", pady=8)
        self._camera_pick = tk.IntVar(body, -1)
        self._cameras = []

        key = "camera_hfov_deg"
        fixed = _hfov(self.values.get(key))
        c = self._auto_number(
            body, key, t("settings.camera.hfov"),
            t("settings.camera.hfov_auto"), fixed is None,
            camera_modes.AUTO_HFOV_DEG if fixed is None else fixed,
            self._hfov_changed,
            fmt=lambda v: t("settings.unit.deg",
                            value=widgets._num(round(float(v), 1))))
        c.note_var.set(t("settings.camera.hfov_note"))

    def _hfov_changed(self, auto, n):
        def same(o, new):
            a, b = _hfov(o), _hfov(new)
            if a is None:
                # Unticked but not moved: still the "auto" angle.
                return b is None or abs(float(new)
                                        - camera_modes.AUTO_HFOV_DEG) < 1e-6
            return b is not None and abs(a - b) < 1e-9
        self._put("camera_hfov_deg", "auto" if auto else n, same)

    def search_cameras(self):
        if str(self.search_button["state"]) == "disabled":
            return                  # one search at a time
        self.search_button.configure(state="disabled")
        self.camera_var.set(t("settings.camera.searching"))
        self._pause_driver()

        def finished():
            self._resume_driver()
            try:
                self.search_button.configure(state="normal")
            except tk.TclError:
                pass

        def done(cams):
            finished()
            self._cameras = list(cams)
            for c in self.camera_list.winfo_children():
                c.destroy()
            if not cams:
                self.camera_var.set(t("settings.camera.none"))
                return
            self.camera_var.set(self._camera_now())
            for i, c in enumerate(cams):
                widgets.radio(self.camera_list, t(
                    "settings.camera.item", index="%d" % c.index,
                    width="%d" % c.width, height="%d" % c.height),
                    self._camera_pick, i,
                    command=lambda i=i: self._pick_camera(i)).pack(
                        anchor="w")

        def failed(exc):
            finished()
            self.camera_var.set(t("settings.camera.failed", error=exc))
        self.worker.run(self.services.probe_cameras, done, failed)

    def _pick_camera(self, i):
        self.camera_choice = self._cameras[i]
        self._show_camera(hardware.camera_record(self.camera_choice))

    # --- Ses -----------------------------------------------------------------

    def _build_sound(self, tab):
        body = self._scrolled(tab)
        self._switch(body, "feedback_sounds", t("settings.sound.feedback"),
                     pady=(0, 0))
        self._slider(body, "feedback_volume",
                     t("settings.sound.feedback_volume"),
                     fmt=lambda v: t("settings.unit.pct",
                                     value="%d" % round(float(v) * 100)))
        self._slider(body, "tts_speed", t("settings.sound.speed"))
        self._slider(body, "tts_volume", t("settings.sound.volume"))

    def _build_advanced(self, tab):
        tab = self._scrolled(tab)
        deep = tk.StringVar(tab, _deep_choice(self.values.get("deep_gaze")))
        self._vars["_deep"] = deep

        def deep_set():
            self.values["deep_gaze"] = {"on": True, "off": False}.get(
                deep.get(), "auto")
        widgets.text(tab, "bold", value=t("settings.advanced.deep")).pack(
            anchor="w")
        row = tk.Frame(tab, bg=BG)
        row.pack(anchor="w")
        for value, label in _deep_choices():
            widgets.radio(row, label, deep, value, command=deep_set).pack(
                side="left")
        widgets.text(tab, "body", value=t("settings.advanced.deep_note"),
                     fg=widgets.DISABLED).pack(anchor="w")

        stt = tk.StringVar(tab, str(self.values.get("stt_model", "auto")))
        self._vars["_stt"] = stt
        widgets.text(tab, "bold", value=t("settings.advanced.stt")).pack(
            anchor="w", pady=(14, 0))
        row = tk.Frame(tab, bg=BG)
        row.pack(anchor="w")
        for value, label in _stt_sizes():
            widgets.radio(row, label, stt, value, command=lambda: self.values
                          .__setitem__("stt_model", stt.get())).pack(
                              side="left")
        widgets.text(tab, "body", value=t("settings.advanced.stt_note"),
                     fg=widgets.DISABLED).pack(anchor="w")

        auto = tk.BooleanVar(tab, self.autostart_on)
        self._vars["_autostart"] = auto
        widgets.check(tab, t("settings.advanced.autostart"), auto).pack(
            anchor="w", pady=(14, 0))

    def missing_text(self):
        lines = widgets.missing_lines(self.caps)
        if not lines:
            return t("settings.missing.none")
        return "\n\n".join(lines)

    def _build_missing(self, tab):
        widgets.text(self._scrolled(tab), "body",
                     value=self.missing_text()).pack(anchor="w")

    def _language_now(self):
        want = i18n.normalize(self.values.get("language"))
        return want if want in dict(i18n.available()) else i18n.language()

    def _build_language(self, tab):
        tab = self._scrolled(tab)
        var = tk.StringVar(tab, self._language_now())
        self._vars["language"] = var

        def chosen():
            self.set_value("language", var.get())
        widgets.text(tab, "bold", value=t("settings.language.label")).pack(
            anchor="w")
        for code, own in i18n.available():
            widgets.radio(tab, own, var, code, command=chosen).pack(
                anchor="w", pady=(8, 0))
        widgets.text(tab, "body", value=t("settings.language.note"),
                     fg=widgets.DISABLED).pack(anchor="w", pady=(14, 0))
        widgets.text(tab, "body", value=t("settings.language.voice_note"),
                     fg=widgets.DISABLED).pack(anchor="w", pady=(8, 0))

    # --- values --------------------------------------------------------------

    def set_value(self, key, value):
        """Change one setting as its widget would (tests, and the tabs)."""
        if by_key(key) is not None:
            self.keys.set_value(key, value)
            return
        if key == "language":
            # Picking the language the file already means (none written:
            # Turkish) is no change - a config.yaml without the line stays so.
            orig = self.original.get("language")
            value = i18n.normalize(value)
            if value == i18n.normalize(orig):
                if orig is None:
                    self.values.pop("language", None)
                else:
                    self.values["language"] = orig
                return
        self.values[key] = value
        c = self.controls.get(key)
        if isinstance(c, controls.Slider):
            c.set(value)
            return
        var = self._vars.get(key)
        if var is not None:
            try:
                var.set(value)
            except tk.TclError:
                pass

    def pending(self):
        return {k: v for k, v in self.values.items()
                if k in _config.KNOWN_KEYS and self.original.get(k) != v}

    # --- save / close --------------------------------------------------------

    def save(self):
        problems = self.keys.conflicts()
        if problems:
            self.message_var.set(t("settings.not_saved_conflicts") + "\n"
                                 + "\n".join(problems))
            self.notebook.select(0)
            return False
        problem = widgets.click_route_problem(
            self.values, self.services.gamepad_present())
        if problem and not self.ask(t("wizard.route_title"), problem + "\n\n"
                                    + t("wizard.route_save")):
            self.message_var.set(t("settings.not_saved"))
            return False
        changes = self.pending()
        camera_record = None
        if self.camera_choice is not None:
            if _config.is_auto(self.values.get("camera_index")):
                camera_record = hardware.camera_record(self.camera_choice)
            else:
                changes["camera_index"] = int(self.camera_choice.index)
        notes = []
        if changes:
            try:
                self.services.set_values(changes)
            except Exception as exc:
                # Any failure is said here; none may vanish into Tk's
                # callback report (the caregiver would think it saved).
                print("[ayarlar] kaydedilemedi: %s" % ascii(exc))
                self.message_var.set(t("settings.save_failed", error=exc))
                return False
            self.original = dict(self.values)
            self.original.update(changes)
            self.raw.update(changes)
        if camera_record is not None:
            self.profile["camera"] = camera_record
            self.profile.pop("camera_missing", None)
            self.services.save_profile(self.profile)
            self.camera_choice = None
        want_auto = bool(self._vars["_autostart"].get())
        if want_auto != self.autostart_on:
            ok, msg = self.services.set_autostart(want_auto)
            notes.append(msg)
            if ok:
                self.autostart_on = want_auto
            else:
                self._vars["_autostart"].set(self.autostart_on)
        if changes or camera_record is not None:
            self.sup.restart()
        # The language config.yaml now names, for the launcher's windows at
        # once (this one is built again) and the driver at the restart above.
        # Also when the file did not change: the windows may show another
        # language than the file says (a source-mode wizard writes nothing).
        lang = i18n.normalize(self.values.get("language"))
        switch = (lang != i18n.language()
                  and lang in dict(i18n.available()))
        if switch:
            if self.on_language is not None:
                self.on_language(lang)
            else:
                i18n.set_language(lang)
            self._build_content()
        if changes or camera_record is not None:
            notes.insert(0, t("settings.saved_language" if switch
                              else "settings.saved"))
        elif not notes:
            notes.append(t("settings.no_change"))
        self.message_var.set(" ".join(notes))
        return True

    def close(self):
        if self.pending() and not self.ask(
                t("launcher.settings"), t("settings.close_question")):
            return False
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        if self.on_close is not None:
            self.on_close()
        return True
