# gaze/eye_gaze/ui/wizard.py
"""The first-run wizard (spec 5): language, camera, screen and graphics
card, downloads, keys, click method, then the driver starts and asks for its
calibration.

The language picker sits at the top of the first step (each language in its
own name); choosing one redraws the wizard in it at once and tells the
launcher (on_language), whose windows follow too. It is a part of step 1, not
a step of its own, so the step order every caller and test knows stays.

Every step has "Geri" and "Atla"; "Atla" leaves that step's choice out
(the config keeps its value, makine.json simply lacks the measurement and
the driver keeps its old rule for it). Nothing is written until the end -
"Kalibrasyona başla" (or closing the window, after asking) - so a half-done
wizard never leaves half a config behind.

What is written (installed and portable mode only):
  config.yaml  the changed keys and the click method, through
               config_writer (comments kept, verified, last click route
               protected);
  makine.json  camera, screens, scale, machine, gpu (incl. "cuda"),
               gaze_ms / gaze_provider from the benchmark, and the
               decisions hardware.decide() takes from them.
In source mode (this checkout, R24) the wizard runs only when asked
(--sihirbaz) and writes NOTHING - unless the checkout is fresh, without any
config.yaml (R29: the launcher then copies config.default.yaml and lets the
first-start wizard write, as in a package): the development machine's config.yaml names its
camera and Whisper size explicitly, and a makine.json decision such as
stt_device "cpu" would move its dictation off the GPU.

The camera preview holds the camera only while the camera step is shown;
it is let go before the driver starts.
"""
import base64
import threading
import time
import tkinter as tk
from tkinter import ttk

from eye_gaze import hardware, i18n, models_manifest
from eye_gaze.i18n import t
from eye_gaze.ui import widgets
from eye_gaze.ui.widgets import BG

CPU = "CPUExecutionProvider"


class Step:
    name = ""
    title_key = ""

    @property
    def title(self):
        return t(self.title_key) if self.title_key else ""

    def __init__(self, wizard):
        self.w = wizard
        self.frame = tk.Frame(wizard.body, bg=BG)
        self.build(self.frame)

    def build(self, f):
        pass

    def on_enter(self):
        pass

    def on_leave(self):
        pass

    def apply(self):
        """Take this step's choice; False keeps the wizard on the step."""
        return True


# --- 1 -----------------------------------------------------------------------

class WelcomeStep(Step):
    name = "hosgeldiniz"
    title_key = "wizard.welcome.title"

    def build(self, f):
        s = self.w.services
        self.lang_var = tk.StringVar(f, i18n.language())
        langs = i18n.available()
        if len(langs) > 1:
            box = tk.Frame(f, bg=BG)
            box.pack(anchor="w", pady=(0, 16))
            widgets.text(box, "bold", value=t("wizard.language.label")).pack(
                anchor="w")
            row = tk.Frame(box, bg=BG)
            row.pack(anchor="w", pady=4)
            for code, own in langs:
                widgets.radio(row, own, self.lang_var, code,
                              command=self._language_chosen).pack(
                                  side="left", padx=(0, 10))
            widgets.text(box, "body", value=t("wizard.language.note"),
                         fg=widgets.DISABLED).pack(anchor="w")
        widgets.text(f, "big", value=t("wizard.welcome.lead")).pack(
            anchor="w", pady=(0, 12))
        widgets.text(f, "body", value=t("wizard.welcome.intro")).pack(
            anchor="w", pady=4)
        self.note_var = tk.StringVar(f, "")
        if not self.w.writable:
            self.note_var.set(t("wizard.welcome.source_note"))
        widgets.text(f, "warn", textvariable=self.note_var).pack(
            anchor="w", pady=4)
        self.perm_var = tk.StringVar(f, "")
        widgets.text(f, "body", textvariable=self.perm_var).pack(
            anchor="w", pady=(12, 4))
        self.perm_button = widgets.button(f, t("wizard.welcome.perm_button"),
                                          self.install_permissions,
                                          primary=True)
        if s.os_name == "linux":
            ok, why = s.linux_permission()
            if ok:
                self.perm_var.set(t("wizard.welcome.perm_ok"))
            else:
                self.perm_var.set(t("wizard.welcome.perm_needed",
                                    why=why.rstrip(".")))
                self.perm_button.pack(anchor="w", pady=4)
        self.caps_var = tk.StringVar(f, "")
        try:
            lines = widgets.missing_lines(s.capabilities(),
                                          widgets.CRITICAL_CAPS)
        except Exception as exc:
            lines = [t("launcher.caps_failed", error=exc)]
        if lines:
            self.caps_var.set(t("wizard.welcome.caps_missing") + "\n"
                              + "\n".join(lines))
        widgets.text(f, "warn", textvariable=self.caps_var).pack(
            anchor="w", pady=(12, 0))

    def _language_chosen(self):
        self.w.set_language(self.lang_var.get())

    def install_permissions(self):
        self.perm_var.set(t("wizard.welcome.perm_opening"))
        self.perm_button.configure(state="disabled")

        def done(result):
            ok, message = result
            self.perm_var.set(message)
            self.perm_button.configure(state="normal")
            if ok:
                self.perm_button.pack_forget()

        def failed(exc):
            self.perm_var.set(t("launcher.perm.run_failed", error=exc))
            self.perm_button.configure(state="normal")
        self.w.worker.run(self.w.services.install_permissions, done, failed)


# --- 2 -----------------------------------------------------------------------

def _png_b64(frame, width=480):
    import cv2
    h, w = frame.shape[:2]
    if w > width:
        frame = cv2.resize(frame, (width, int(h * width / w)))
    ok, buf = cv2.imencode(".png", cv2.flip(frame, 1))     # a mirror
    return base64.b64encode(buf.tobytes()) if ok else None


class _Preview:
    """Reads the chosen camera on a thread; frames and the face check go to
    the Tk thread through post(). stop() releases the camera. The camera
    itself is open in a child process (Services.open_camera): a capture
    still stuck in its driver after the wait is killed, child and all."""

    FRAME_S = 0.08
    FACE_S = 0.4

    def __init__(self, services, info, post, on_frame, on_face):
        self.services, self.info, self.post = services, info, post
        self.on_frame, self.on_face = on_frame, on_face
        self._stop = threading.Event()
        self._cap = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="kamera-onizleme")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(3)
        kill = getattr(self._cap, "kill", None)
        if self._thread.is_alive() and kill is not None:
            kill()

    def _run(self):
        cap = None
        detector = None
        try:
            cap = self._cap = self.services.open_camera(self.info)
            if cap is None or not cap.isOpened():
                self.post(lambda: self.on_face(
                    t("wizard.preview.open_failed"), False))
                return
            detector = self.services.face_detector()
            last = 0.0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.05)
                    continue
                data = _png_b64(frame)
                if data is not None:
                    self.post(lambda d=data: self.on_frame(d))
                now = time.monotonic()
                if detector is not None and now - last >= self.FACE_S:
                    last = now
                    try:
                        found = bool(detector(frame))
                    except Exception:
                        found = False
                    self.post(lambda f=found: self.on_face(
                        t("wizard.preview.face_found") if f else
                        t("wizard.preview.face_missing"), f))
                time.sleep(self.FRAME_S)
        except Exception as exc:
            self.post(lambda: self.on_face(
                t("wizard.preview.stopped", error=exc), False))
        finally:
            for obj in (cap, detector):
                for meth in ("release", "close"):
                    fn = getattr(obj, meth, None)
                    if fn is not None:
                        try:
                            fn()
                        except Exception:
                            pass


class CameraStep(Step):
    name = "kamera"
    title_key = "wizard.camera.title"
    PREVIEW_W = 480         # px; _png_b64 scales frames to this width
    PREVIEW_H = 360

    def build(self, f):
        self.cameras = []
        self.probed = False
        self._preview = None
        self._image = None
        self.choice = tk.IntVar(f, 0)
        self.status_var = tk.StringVar(f, "")
        self.face_var = tk.StringVar(f, "")
        widgets.text(f, "body", textvariable=self.status_var).pack(
            anchor="w", pady=(0, 8))
        self.retry_button = widgets.button(f, t("wizard.camera.retry"),
                                           self.retry)
        self.retry_button.pack(anchor="w", pady=4)
        row = tk.Frame(f, bg=BG)
        row.pack(fill="both", expand=True, pady=8)
        self.list_frame = tk.Frame(row, bg=BG)
        self.list_frame.pack(side="left", fill="y", padx=(0, 16))
        right = tk.Frame(row, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        # A Label's width/height count CHARACTERS and LINES until it shows
        # an image: with no frame yet (or no camera at all) a 480x360 label
        # asked for 480 characters by 360 lines and the wizard window grew
        # to ~7000x10000 px, its buttons off every screen. The pixel size
        # belongs to a frame that does not follow its content.
        self.image_box = tk.Frame(right, bg=widgets.PANEL,
                                  width=self.PREVIEW_W,
                                  height=self.PREVIEW_H)
        self.image_box.pack_propagate(False)
        self.image_box.pack(anchor="w")
        self.image_label = tk.Label(self.image_box, bg=widgets.PANEL,
                                    text="")
        self.image_label.pack(fill="both", expand=True)
        self.face_label = widgets.text(right, "bold",
                                       textvariable=self.face_var)
        self.face_label.pack(anchor="w", pady=6)

    def on_enter(self):
        if not self.probed:
            self.retry()
        else:
            self._start_preview()

    def on_leave(self):
        self._stop_preview()

    def retry(self):
        self._stop_preview()
        # One search at a time: two would fight over the same cameras.
        self.retry_button.configure(state="disabled")
        self.status_var.set(t("wizard.camera.searching"))
        self.w.worker.run(self.w.services.probe_cameras, self._probed,
                          self._probe_failed)

    def _probe_failed(self, exc):
        self._probed([])
        self.status_var.set(t("wizard.camera.search_failed", error=exc))

    def _probed(self, cams):
        self.probed = True
        self.retry_button.configure(state="normal")
        self.cameras = list(cams)
        for child in self.list_frame.winfo_children():
            child.destroy()
        if not self.cameras:
            self.status_var.set(t("wizard.camera.none"))
            self.face_var.set("")
            self._image = None
            self.image_label.configure(image="")
            return
        self.status_var.set(t("wizard.camera.found", n=len(self.cameras)))
        want = (self.w.profile.get("camera") or {}).get("index")
        pick = 0
        for i, c in enumerate(self.cameras):
            if c.index == want:
                pick = i
            label = t("wizard.camera.item", index="%d" % c.index,
                      width="%d" % c.width, height="%d" % c.height,
                      fps=widgets._num(round(c.fps_measured)))
            if c.name and c.name != "fake":
                label += "\n" + c.name
            widgets.radio(self.list_frame, label, self.choice, i,
                          command=self._start_preview).pack(anchor="w",
                                                            pady=4)
        self.choice.set(pick)
        if self.w.current is self:
            self._start_preview()

    def _start_preview(self):
        self._stop_preview()
        if not self.cameras:
            return
        info = self.cameras[self.choice.get()]
        self.face_var.set(t("wizard.camera.preview_opening"))
        self._preview = _Preview(self.w.services, info, self.w.worker.post,
                                 self._frame, self._face)
        self._preview.start()

    def _stop_preview(self):
        p, self._preview = self._preview, None
        if p is not None:
            p.stop()

    def _frame(self, data):
        if self._preview is None:
            return
        try:
            self._image = tk.PhotoImage(master=self.frame, data=data)
            self.image_label.configure(image=self._image)
        except tk.TclError:
            pass

    def _face(self, message, found):
        if self._preview is None:
            return
        self.face_var.set(message)
        self.face_label.configure(fg=widgets.OK if found else widgets.WARN)

    def apply(self):
        if self.cameras:
            info = self.cameras[self.choice.get()]
            self.w.profile["camera"] = hardware.camera_record(info)
            self.w.profile.pop("camera_missing", None)
        return True


# --- 3 -----------------------------------------------------------------------

class HardwareStep(Step):
    name = "donanim"
    title_key = "wizard.hardware.title"

    def build(self, f):
        self.info_var = tk.StringVar(f, t("wizard.hardware.reading"))
        self.result_var = tk.StringVar(f, "")
        self.detail_var = tk.StringVar(f, "")
        widgets.text(f, "body", textvariable=self.info_var).pack(
            anchor="w", pady=(0, 12))
        widgets.text(f, "body", value=t("wizard.hardware.intro")).pack(
            anchor="w", pady=4)
        self.measure_button = widgets.button(f, t("wizard.hardware.measure"),
                                             self.measure, primary=True)
        self.measure_button.pack(anchor="w", pady=8)
        widgets.text(f, "big", textvariable=self.result_var).pack(
            anchor="w", pady=4)
        widgets.text(f, "body", textvariable=self.detail_var).pack(anchor="w")

    def on_enter(self):
        self.w.gather_hardware(self._show_info)

    def _show_info(self):
        p = self.w.profile
        scr = p.get("screens") or []
        lines = []
        if scr:
            main = scr[0]
            lines.append(t("wizard.hardware.screen", count="%d" % len(scr),
                           width="%d" % main[2], height="%d" % main[3],
                           scale="%d" % round(float(p.get("scale") or 1.0)
                                              * 100)))
        gpu = p.get("gpu") or {}
        name = gpu.get("name") or t("wizard.hardware.gpu_none")
        via = {"DmlExecutionProvider": "wizard.hardware.via_dml",
               "CUDAExecutionProvider": "wizard.hardware.via_cuda"}.get(
                   gpu.get("provider"), "wizard.hardware.via_none")
        lines.append(t("wizard.hardware.gpu", name=name, via=t(via)))
        m = p.get("machine") or {}
        lines.append(t("wizard.hardware.cpu",
                       cores="%s" % (m.get("cpu_cores") or "?"),
                       ram=widgets._num(m.get("ram_gb"))
                       if m.get("ram_gb") else "?"))
        self.info_var.set("\n".join(lines))

    def measure(self):
        self.result_var.set(t("wizard.hardware.measuring"))
        self.detail_var.set("")
        self.measure_button.configure(state="disabled")
        self.w.worker.run(self.w.services.benchmark, self._measured,
                          self._failed)

    def _measured(self, result):
        self.measure_button.configure(state="normal")
        ms, provider = result
        p = self.w.profile
        p["gaze_ms"] = float(ms)
        p["gaze_provider"] = provider
        on = hardware.decide(p).get("deep_gaze")
        if on:
            where = t("wizard.hardware.where_cpu" if provider == CPU
                      else "wizard.hardware.where_gpu")
            self.result_var.set(t("wizard.hardware.deep_on", where=where))
        else:
            self.result_var.set(t("wizard.hardware.deep_off"))
        self.detail_var.set(t("wizard.hardware.frame_time",
                              ms="%d" % round(ms),
                              limit="%d" % hardware.DEEP_MAX_MS))

    def _failed(self, exc):
        self.measure_button.configure(state="normal")
        self.w.profile.pop("gaze_ms", None)
        self.w.profile.pop("gaze_provider", None)
        self.result_var.set(t("wizard.hardware.failed"))
        self.detail_var.set(str(exc))


# --- 4 -----------------------------------------------------------------------

def _size_text(nbytes):
    gb = nbytes / 1024.0 ** 3
    if gb >= 1:
        return t("wizard.download.size_gb", gb=widgets._num(round(gb, 1)))
    return t("wizard.download.size_mb", mb="%d" % round(nbytes / 1024.0 ** 2))


class _DownloadRow:
    def __init__(self, step, parent, name, title, note):
        self.step, self.name = step, name
        w = step.w
        self.running = False
        self.cancel_event = None
        try:
            total = sum(it.size for it in models_manifest.items(name))
        except Exception:
            total = 0
        self.frame = tk.Frame(parent, bg=BG, pady=8)
        self.frame.pack(fill="x")
        widgets.text(self.frame, "bold", value="%s (%s)" % (
            title, _size_text(total))).pack(anchor="w")
        widgets.text(self.frame, "body", value=note, fg=widgets.DISABLED).pack(
            anchor="w")
        self.status_var = tk.StringVar(self.frame, "")
        widgets.text(self.frame, "body", textvariable=self.status_var).pack(
            anchor="w")
        self.bar = ttk.Progressbar(self.frame, length=560, maximum=1000)
        self.bar.pack(anchor="w", pady=4, ipady=6)
        buttons = tk.Frame(self.frame, bg=BG)
        buttons.pack(anchor="w")
        self.start_button = widgets.button(buttons, t("wizard.download.get"),
                                           self.start, primary=True)
        self.start_button.pack(side="left")
        self.cancel_button = widgets.button(buttons,
                                            t("wizard.download.cancel"),
                                            self.cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        state = w.services.download_status(name)
        self.ok = state == "ok"
        if self.ok:
            self.status_var.set(t("wizard.download.ready"))
            self.bar["value"] = 1000
            self.start_button.configure(state="disabled")
        elif state == "partial":
            self.status_var.set(t("wizard.download.partial"))
        else:
            self.status_var.set(t("wizard.download.missing"))

    def destroy(self):
        self.frame.destroy()

    def start(self):
        if self.running or self.ok:
            return
        w = self.step.w
        self.running = True
        self.cancel_event = threading.Event()
        cancel = self.cancel_event
        self.status_var.set(t("wizard.download.downloading"))
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")

        def progress(done, total):
            w.worker.post(lambda: self._progress(done, total))
        w.worker.run(lambda: w.services.download(self.name, progress,
                                                 cancel),
                     lambda r: self._done(r, cancel),
                     lambda exc: self._done((None, str(exc)), cancel))

    def cancel(self):
        if self.cancel_event is not None:
            self.cancel_event.set()

    def _alive(self):
        """False once the row's widgets are gone (a language switch builds
        every step again while a download may still report in)."""
        try:
            return bool(self.frame.winfo_exists())
        except tk.TclError:
            return False

    def _progress(self, done, total):
        if not self.running or not self._alive():
            return
        if total:
            self.bar["value"] = int(1000 * done / total)
            self.status_var.set(t("wizard.download.progress",
                                  done="%d" % (done // 2 ** 20),
                                  total="%d" % (total // 2 ** 20)))

    def _done(self, result, cancel):
        path, err = result
        self.running = False
        if not self._alive():
            return
        self.cancel_button.configure(state="disabled")
        if path:
            self.ok = True
            self.bar["value"] = 1000
            self.status_var.set(t("wizard.download.done"))
            self.step.downloaded(self.name)
            return
        self.start_button.configure(state="normal")
        if cancel.is_set():
            self.status_var.set(t("wizard.download.cancelled"))
        else:
            self.status_var.set(t("wizard.download.failed",
                                  error=err or t("launcher.unknown_error")))


class DownloadStep(Step):
    name = "indirmeler"
    title_key = "wizard.download.title"
    ROWS_H = 400            # px: about two rows; more scroll

    def build(self, f):
        self.rows = {}
        widgets.text(f, "body", value=t("wizard.download.intro")).pack(
            anchor="w", pady=(0, 8))
        # The rows scroll in a fixed height: three of them (GPU pack,
        # Whisper, the English voice) made the wizard ~1170 px tall, its
        # buttons below the edge of a 1080p screen at 125 % (Task 16).
        self.scroll = scroll = widgets.ScrollFrame(f, height=self.ROWS_H)
        scroll.pack(fill="both", expand=True)
        self.rows_frame = scroll.inner

    def on_enter(self):
        self.w.gather_hardware(self.refresh_rows)

    def wanted(self):
        s, p = self.w.services, self.w.profile
        out = []
        gpu = p.get("gpu") or {}
        if "nvidia" in str(gpu.get("name") or "").lower():
            out.append((models_manifest.gpu_pack(s.os_name == "windows"),
                        t("wizard.download.gpu_pack"),
                        t("wizard.download.gpu_note_linux"
                          if s.os_name == "linux"
                          else "wizard.download.gpu_note")))
        size = hardware.decide(p).get("stt_model") or "small"
        out.append(("whisper-%s" % size,
                    t("wizard.download.whisper", size=size),
                    t("wizard.download.whisper_note")))
        # The language's own piper voice when the program downloads it (the
        # English one; the Turkish voice comes with the program): chosen on
        # step 1, so the row follows the language picker.
        voice = i18n.setting(self.w.values, "tts_voice", "tr")
        pack = models_manifest.voice_pack(voice)
        if pack:
            out.append((pack, t("wizard.download.voice", name=voice),
                        t("wizard.download.voice_note")))
        return out

    def refresh_rows(self):
        wanted = self.wanted()
        names = [n for n, _, _ in wanted]
        for name in list(self.rows):
            row = self.rows[name]
            if name not in names and not row.running and not row.ok:
                row.destroy()
                del self.rows[name]
        for name, title, note in wanted:
            if name not in self.rows:
                self.rows[name] = _DownloadRow(self, self.rows_frame, name,
                                               title, note)

    def downloaded(self, name):
        if not name.startswith("nvidia-gpu"):
            return
        s = self.w.services

        def load():
            # Loading ~1 GB of CUDA libraries takes seconds: not on Tk.
            try:
                s.refresh_gpu_libs()
            except Exception as exc:
                print("[sihirbaz] GPU kitapliklari yuklenemedi: %s"
                      % ascii(exc))
            return bool(s.cuda_ready())

        def done(cuda):
            gpu = dict(self.w.profile.get("gpu") or {})
            gpu["cuda"] = cuda
            self.w.profile["gpu"] = gpu
            self.refresh_rows()

        def failed(exc):
            print("[sihirbaz] CUDA denetlenemedi: %s" % ascii(exc))
        self.w.worker.run(load, done, failed)

    def cancel_all(self):
        for row in self.rows.values():
            if row.running:
                row.cancel()


# --- 5 -----------------------------------------------------------------------

class KeysStep(Step):
    name = "tuslar"
    title_key = "wizard.keys.title"
    LIST_H = 380            # px: the list's height in 1.2.0 - at least this

    def build(self, f):
        w = self.w
        self.table = widgets.KeyTable(f, w.services, dict(w.values), w.ask,
                                      w.tell, w.worker, height=self.LIST_H)
        self.table.pack(fill="both", expand=True)

    def apply(self):
        problems = self.table.conflicts()
        if problems:
            self.w.tell(self.title, t("wizard.keys.conflicts") + "\n\n"
                        + "\n\n".join(problems))
            return False
        changed = self.table.changed
        route = any(k in changed for k in widgets.CLICK_ROUTE_KEYS)
        if route and not self.w.confirm_click_route(
                dict(self.w.values, **changed), "continue"):
            return False
        self.w.values.update(changed)
        self.w.pending.update(changed)
        return True


# --- 6 -----------------------------------------------------------------------

class ClickStep(Step):
    name = "tiklama"
    title_key = "wizard.click.title"

    def build(self, f):
        self.method = tk.StringVar(f, widgets.method_of(self.w.values))
        widgets.text(f, "body", value=t("wizard.click.question")).pack(
            anchor="w", pady=(0, 8))
        for value, label, note in widgets.click_methods():
            widgets.radio(f, label, self.method, value).pack(anchor="w",
                                                             pady=(8, 0))
            widgets.text(f, "body", value=note, fg=widgets.DISABLED,
                         padx=40).pack(anchor="w")

    def apply(self):
        w = self.w
        mv = widgets.method_values(self.method.get())
        if not w.confirm_click_route(dict(w.values, **mv), "continue"):
            return False
        changed = {k: v for k, v in mv.items() if w.values.get(k) != v}
        w.values.update(changed)
        w.pending.update(changed)
        return True


# --- 7 -----------------------------------------------------------------------

class FinishStep(Step):
    name = "kalibrasyon"
    title_key = "wizard.finish.title"

    def build(self, f):
        self.summary_var = tk.StringVar(f, "")
        widgets.text(f, "big", value=t("wizard.finish.lead")).pack(anchor="w")
        widgets.text(f, "body", value=t("wizard.finish.intro")).pack(
            anchor="w", pady=8)
        widgets.text(f, "body", textvariable=self.summary_var).pack(
            anchor="w", pady=8)

    def on_enter(self):
        w = self.w
        lines = []
        if w.pending:
            lines.append(t("wizard.finish.pending", n=len(w.pending)))
        cam = w.profile.get("camera")
        if cam:
            lines.append(t("wizard.finish.camera", index="%d" % cam["index"],
                           width="%d" % cam["width"],
                           height="%d" % cam["height"]))
        if not w.writable:
            lines.append(t("wizard.finish.source"))
        self.summary_var.set("\n".join(lines))


# --- the wizard --------------------------------------------------------------

class Wizard:
    STEPS = (WelcomeStep, CameraStep, HardwareStep, DownloadStep, KeysStep,
             ClickStep, FinishStep)

    def __init__(self, parent, services, on_done, ask=None, tell=None,
                 worker=None, writable=None, on_language=None):
        self.services = services
        self.on_done = on_done
        self.on_language = on_language
        self.win = widgets.toplevel(parent, t("wizard.window_title"))
        widgets.apply_theme(self.win)
        d_ask, d_tell = widgets.dialogs(self.win)
        self.ask = ask or d_ask
        self.tell = tell or d_tell
        self.worker = worker or widgets.Worker(self.win)
        # None: read-only in source mode (R24). The launcher passes True for
        # a first start, which in source mode means a fresh checkout (R29).
        self.writable = (services.mode() != "source" if writable is None
                         else bool(writable))
        cfg, _problem = services.read_config()
        self.values = dict(cfg)
        self._language_at_start = cfg.get("language")
        self.pending = {}
        self.profile = dict(services.load_profile() or {})
        self._hw_done = False
        self._route_accepted = None
        self.closed = False

        outer = tk.Frame(self.win, bg=BG, padx=28, pady=20)
        outer.pack(fill="both", expand=True)
        self.counter_var = tk.StringVar(outer, "")
        self.title_var = tk.StringVar(outer, "")
        widgets.text(outer, "body", textvariable=self.counter_var).pack(
            anchor="w")
        widgets.text(outer, "title", textvariable=self.title_var).pack(
            anchor="w", pady=(0, 12))
        # Every step on one page that scrolls: as tall as the tallest step
        # where the screen has room, shorter where it has not (_fit).
        self.body_scroll = widgets.ScrollFrame(outer, height=400,
                                               autohide=True)
        self.body_scroll.pack(fill="both", expand=True)
        self.body = self.body_scroll.inner
        # The buttons are packed BEFORE the step body: when the window is
        # smaller than its content (maxsize below, a small screen), pack
        # takes the missing height from the body, never from Geri/İleri.
        # Created after it, so Tab still reaches the step's controls first.
        self.foot = foot = tk.Frame(outer, bg=BG, pady=12)
        foot.pack(fill="x", side="bottom", before=self.body_scroll)
        self.back_button = widgets.button(foot, t("wizard.back"), self.back)
        self.back_button.pack(side="left")
        self.next_button = widgets.button(foot, t("wizard.next"), self.next,
                                          primary=True)
        self.next_button.pack(side="right")
        self.skip_button = widgets.button(foot, t("wizard.skip"), self.skip)
        self.skip_button.pack(side="right", padx=12)
        self.win.protocol("WM_DELETE_WINDOW", self.request_close)

        self.steps = [cls(self) for cls in self.STEPS]
        self.index = -1
        self._fit(place=True)
        self._show(0)
        widgets.show(self.win)

    MIN_SIZE = (980, 760)

    def _fit(self, place):
        """The page as large as the largest step (the Keys and Downloads
        lists at their 1.2.0 heights), then the whole window on the work
        area: only the page gives up the height a small screen lacks
        (widgets.fit_to_work_area). Then each of those two lists takes the
        page's height its step leaves - longer on a large screen, shorter
        on a small one, never an empty band under a scrolled list."""
        lists = ((self.step("tuslar"), self.step("tuslar").table.scroll,
                  KeysStep.LIST_H),
                 (self.step("indirmeler"), self.step("indirmeler").scroll,
                  DownloadStep.ROWS_H))
        for _step, scroll, height in lists:
            scroll.canvas.configure(height=height)
        self.win.update_idletasks()
        frames = [s.frame for s in self.steps]
        self.body_scroll.canvas.configure(
            width=max(f.winfo_reqwidth() for f in frames),
            height=max(f.winfo_reqheight() for f in frames))
        widgets.fit_to_work_area(self.win, [self.body_scroll], self.MIN_SIZE,
                                 place=place)
        page = int(self.body_scroll.canvas.cget("height"))
        for step, scroll, _height in lists:
            rest = step.frame.winfo_reqheight() - int(
                scroll.canvas.cget("height"))
            scroll.canvas.configure(height=max(widgets.MIN_SCROLL,
                                               page - rest))
        self.win.update_idletasks()
        self.body_scroll.sync_bar()

    @property
    def current(self):
        return self.steps[self.index]

    def step(self, name):
        return next(s for s in self.steps if s.name == name)

    # --- language ------------------------------------------------------------

    def set_language(self, code):
        """Switch every text to `code` now: the launcher's windows through
        on_language, this wizard by building its steps again (what was
        chosen so far lives in values / pending / profile, not in the
        widgets). The choice is written at the end like any other."""
        code = i18n.normalize(code)
        if self.closed or code == i18n.language():
            return
        if self.on_language is not None:
            used = self.on_language(code)
        else:
            used = i18n.set_language(code)
        if used != i18n.normalize(self._language_at_start):
            self.values["language"] = used
            self.pending["language"] = used
        else:
            self.pending.pop("language", None)
            if self._language_at_start is None:
                self.values.pop("language", None)
            else:
                self.values["language"] = self._language_at_start
        keep = max(self.index, 0)
        if 0 <= self.index < len(self.steps):
            self.current.on_leave()
        # A download in flight would report into rows about to go; it
        # resumes where it stopped when "İndir" is pressed again.
        self.step("indirmeler").cancel_all()
        for s in self.steps:
            s.frame.destroy()
        self.steps = [cls(self) for cls in self.STEPS]
        self.index = -1
        self._fit(place=False)
        try:
            self.win.title(t("wizard.window_title"))
            self.back_button.configure(text=t("wizard.back"))
        except tk.TclError:
            pass
        self._show(keep)

    def _show(self, i):
        if 0 <= self.index < len(self.steps):
            self.current.on_leave()
            self.current.frame.pack_forget()
        self.index = i
        step = self.current
        self.counter_var.set(t("wizard.counter", i=i + 1, n=len(self.steps)))
        self.title_var.set(step.title)
        step.frame.pack(fill="both", expand=True)
        self.body_scroll.canvas.yview_moveto(0)
        self.win.update_idletasks()
        self.body_scroll.sync_bar()
        last = i == len(self.steps) - 1
        self.back_button.configure(state="normal" if i > 0 else "disabled")
        self.next_button.configure(text=t("wizard.start") if last
                                   else t("wizard.next"))
        self.skip_button.configure(text=t("wizard.not_now") if last
                                   else t("wizard.skip"))
        step.on_enter()
        try:
            self.next_button.focus_set()
        except tk.TclError:
            pass

    def next(self):
        if self.closed or not self.current.apply():
            return
        if self.index == len(self.steps) - 1:
            self.finish(True)
        else:
            self._show(self.index + 1)

    def skip(self):
        if self.closed:
            return
        if self.index == len(self.steps) - 1:
            self.finish(False)
        else:
            self._show(self.index + 1)

    def back(self):
        if not self.closed and self.index > 0:
            self._show(self.index - 1)

    # --- hardware facts ------------------------------------------------------

    def _collect_hardware(self):
        s = self.services
        return {"screens": s.screens(), "scale": s.scale(),
                "machine": s.machine(), "gpu": s.gpu_info(),
                "cuda": s.cuda_ready()}

    def _apply_hardware(self, d):
        p = self.profile
        p["screens"] = [list(r) for r in d["screens"]]
        p["scale"] = float(d["scale"])
        p["machine"] = dict(d["machine"])
        gpu = dict(d["gpu"] or {})
        gpu["cuda"] = bool(d["cuda"])
        p["gpu"] = gpu
        self._hw_done = True

    def gather_hardware(self, then=None):
        """Screens, scale, CPU/RAM and the GPU into the profile, once (the
        GPU query may take a second: nvidia-smi)."""
        then = then or (lambda: None)
        if self._hw_done:
            then()
            return

        def done(d):
            self._apply_hardware(d)
            then()

        def failed(exc):
            print("[sihirbaz] donanim okunamadi: %s" % ascii(exc))
            then()
        self.worker.run(self._collect_hardware, done, failed)

    # --- the end -------------------------------------------------------------

    def request_close(self):
        if self.ask(t("app.title"), t("wizard.close_question")):
            self.finish(True)

    def confirm_click_route(self, values, verb):
        """True when `values` leave a click route that works now, or the
        user said yes to exactly these route values already (one question
        for one decision, whichever step or exit reaches it first). `verb`
        is "continue" (a step) or "save" (the end)."""
        problem = widgets.click_route_problem(
            values, self.services.gamepad_present())
        if problem is None:
            return True
        snap = tuple(values.get(k) for k in widgets.CLICK_ROUTE_KEYS)
        if snap == self._route_accepted:
            return True
        question = t("wizard.route_save" if verb == "save"
                     else "wizard.route_continue")
        if self.ask(t("wizard.route_title"), problem + "\n\n" + question):
            self._route_accepted = snap
            return True
        return False

    def finish(self, start):
        if self.closed:
            return
        # Every way out (İleri, "Şimdi değil", closing the window) writes
        # the same config: none may write one that cannot click unless the
        # user said yes to it. A "no" keeps the wizard open.
        route = any(k in self.pending for k in widgets.CLICK_ROUTE_KEYS)
        if self.writable and route and not self.confirm_click_route(
                self.values, "save"):
            return
        self.closed = True
        self.current.on_leave()
        self.step("kamera").on_leave()
        self.step("indirmeler").cancel_all()
        if self.writable:
            self._write()
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.on_done(start)

    def _write(self):
        s = self.services
        if self.pending:
            try:
                s.set_values(dict(self.pending))
            except Exception as exc:
                self.tell(t("wizard.save_failed_title"),
                          t("wizard.save_failed", error=exc))
        if not self._hw_done:
            try:
                self._apply_hardware(self._collect_hardware())
            except Exception as exc:
                print("[sihirbaz] donanim okunamadi: %s" % ascii(exc))
        profile = dict(self.profile)
        # The camera step came before the benchmark: its mode is chosen again
        # from the measured modes now that gaze_ms is known.
        if "camera" in profile:
            profile["camera"] = hardware.rechoose(profile["camera"], profile)
        dec = hardware.decide(profile)
        if dec:
            profile["decisions"] = dec
        s.save_profile(profile)
