# gaze/eye_gaze/ui/settings.py
"""The settings window (spec 6.7): the wizard's pieces in tabs, any time.

  Tuşlar      every catalog job, "Değiştir" (the driver is stopped while
              the key is caught, then started again), conflicts shown live
  Tıklama     switch / dwell / blink
  Kamera      the cameras found now (the driver is stopped for the search)
  Ses         speech speed and volume
  Gelişmiş    deep model, Whisper size, target snapping, start with the
              computer
  Bu bilgisayarda olmayanlar   every capability that is missing, and why
  Dil         the program's language: on Kaydet the launcher's windows switch
              at once (this one is built again), the driver at the restart
              Kaydet gives it

Kaydet: conflicts block the save and say why (keymap.find_conflicts, the
same rule the driver applies at start); a config with no click route that
works NOW (a gamepad binding counts only with a gamepad plugged in) needs a
yes; then config_writer.set_values (comments kept, read back, refused for a
read-only file or the last click route) and Supervisor.restart().
"""
import tkinter as tk
from tkinter import ttk

from eye_gaze import config as _config
from eye_gaze import hardware, i18n
from eye_gaze.actions_catalog import by_key
from eye_gaze.i18n import t
from eye_gaze.ui import widgets
from eye_gaze.ui.widgets import BG


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
        self.profile = dict(services.load_profile() or {})
        self.camera_choice = None
        self.autostart_on = bool(services.autostart_enabled())
        self.caps = services.capabilities()
        self.message_var = tk.StringVar(self.win, problem or "")
        self._vars = {}
        self._outer = None
        self._build_content()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.minsize(1000, 760)
        widgets.show(self.win)

    def _build_content(self):
        """Everything inside the window, in the current language (built
        again after a language change; values / original / profile stay)."""
        if self._outer is not None:
            self._outer.destroy()
        self._vars = {}
        self.win.title(t("settings.window_title"))
        self._outer = outer = tk.Frame(self.win, bg=BG, padx=20, pady=16)
        outer.pack(fill="both", expand=True)
        self.note_var = tk.StringVar(self.win, "")
        if self.services.mode() == "source":
            self.note_var.set(t("settings.source_note"))
            widgets.text(outer, "warn", textvariable=self.note_var).pack(
                fill="x", pady=(0, 8))
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        for key, build in (("keys", self._build_keys),
                           ("click", self._build_click),
                           ("camera", self._build_camera),
                           ("sound", self._build_sound),
                           ("advanced", self._build_advanced),
                           ("missing", self._build_missing),
                           ("language", self._build_language)):
            tab = tk.Frame(self.notebook, bg=BG, padx=16, pady=12)
            build(tab)
            self.notebook.add(tab, text=t("settings.tab." + key))
        widgets.text(outer, "bold", textvariable=self.message_var).pack(
            fill="x", pady=(10, 4))
        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x")
        widgets.button(foot, t("settings.save"), self.save,
                       primary=True).pack(side="right")
        widgets.button(foot, t("settings.close"), self.close).pack(
            side="right", padx=12)

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

    def _build_click(self, tab):
        var = tk.StringVar(tab, widgets.method_of(self.values))
        self._vars["_method"] = var

        def chosen():
            for k, v in widgets.method_values(var.get()).items():
                self.values[k] = v
        for value, label, note in widgets.click_methods():
            widgets.radio(tab, label, var, value, command=chosen).pack(
                anchor="w", pady=(8, 0))
            widgets.text(tab, "body", value=note, fg=widgets.DISABLED,
                         padx=40).pack(anchor="w")

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

    def _build_camera(self, tab):
        self.camera_var = tk.StringVar(tab, self._camera_now())
        widgets.text(tab, "body", textvariable=self.camera_var).pack(
            anchor="w")
        widgets.text(tab, "body", value=t("settings.camera.note")).pack(
            anchor="w", pady=8)
        self.search_button = widgets.button(tab, t("settings.camera.search"),
                                            self.search_cameras)
        self.search_button.pack(anchor="w", pady=4)
        self.camera_list = tk.Frame(tab, bg=BG)
        self.camera_list.pack(anchor="w", fill="x", pady=8)
        self._camera_pick = tk.IntVar(tab, -1)
        self._cameras = []

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

    def _scale(self, tab, key, label, lo, hi, step):
        var = tk.DoubleVar(tab, float(self.values.get(key, 1.0) or 1.0))
        self._vars[key] = var
        widgets.text(tab, "bold", value=label).pack(anchor="w", pady=(12, 0))
        tk.Scale(tab, variable=var, from_=lo, to=hi, resolution=step,
                 orient="horizontal", length=560, width=24, bg=BG,
                 fg=widgets.FG,
                 troughcolor=widgets.PANEL, highlightthickness=3,
                 highlightcolor=widgets.FOCUS,
                 font=widgets.fonts(self.win)["body"],
                 command=lambda _v: self.values.__setitem__(
                     key, round(var.get(), 2))).pack(anchor="w")

    def _build_sound(self, tab):
        self._scale(tab, "tts_speed", t("settings.sound.speed"), 0.8, 2.0,
                    0.05)
        self._scale(tab, "tts_volume", t("settings.sound.volume"),
                    0.3, 1.0, 0.05)

    def _build_advanced(self, tab):
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

        snap = tk.BooleanVar(tab, self.values.get("snap_to_targets") is True)
        self._vars["snap_to_targets"] = snap
        ok, why = self.caps.get("snap", (True, ""))
        c = widgets.check(tab, t("settings.advanced.snap"), snap,
                          command=lambda: self.values.__setitem__(
                              "snap_to_targets", bool(snap.get())))
        c.pack(anchor="w", pady=(14, 0))
        if not ok:
            c.configure(state="disabled")
            widgets.text(tab, "body", value=why, fg=widgets.DISABLED).pack(
                anchor="w")

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
        scroll = widgets.ScrollFrame(tab, height=440)
        scroll.pack(fill="both", expand=True)
        widgets.text(scroll.inner, "body", value=self.missing_text()).pack(
            anchor="w")

    def _language_now(self):
        want = i18n.normalize(self.values.get("language"))
        return want if want in dict(i18n.available()) else i18n.language()

    def _build_language(self, tab):
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
