# gaze/eye_gaze/ui/__init__.py
"""The launcher: the program's face (spec 6.2).

One process, a small status window ("Çalışıyor", Ayarlar, Durdur/Başlat,
Günlüğü aç); the driver runs as its child under a Supervisor. First start
of an installed or portable copy (no config.yaml in the data folder):
config.default.yaml is copied there and the wizard opens; the driver starts
when the wizard ends. In source mode (this checkout) the wizard opens only
with --sihirbaz (R24): gaze/config.yaml and BASLAT.bat stay as they are -
except in a fresh checkout from the source zip, which has no config.yaml at
all (R29): it is set up like a first start, and its wizard may write.

Services is the one door to the machine (cameras, benchmark, downloads, key
capture, config writes, autostart, Linux permissions): the windows only talk
to it, so the tests and the screenshots replace it with fakes.

Exactly one launcher runs per user session (supervisor.InstanceLock); a
second start hands its request ("goster" / "ayarlar" / "sihirbaz") to the
first one and exits - two launchers would mean two drivers on one camera.
"""
import os
import shutil
import subprocess
import sys
import tkinter as tk

from eye_gaze import config as _config
from eye_gaze import i18n, paths
from eye_gaze import platform as _platform
from eye_gaze.ui import supervisor as _sv
from eye_gaze.ui import widgets
from eye_gaze.i18n import t
from eye_gaze.ui.widgets import BG

# The Turkish name, kept for callers that read the constant; the windows ask
# app_title() so the name follows the chosen language.
APP_TITLE = "Göz Faresi"


def app_title():
    return t("app.title")


def default_config_path():
    """config.default.yaml: next to gaze_driver.py in source, at the root of
    the bundle when frozen (packaging/gozfaresi.spec puts it there)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", paths.app_dir())
        return os.path.join(base, "config.default.yaml")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "config.default.yaml")


def config_path():
    return os.path.join(paths.data_dir(), "config.yaml")


def fresh_source_checkout():
    """Source mode with nothing of a running install in the data folder: no
    config.yaml, no config.son-iyi.yaml backup, no calibration.json - a
    checkout unpacked from the source zip, which by design carries no
    config.yaml (it is export-ignore: it holds one machine's camera and
    paths). Any of the three present means an install that is (or was)
    running: left exactly as it is (R24); a missing config.yaml there is
    the driver's to report, and its backup is the driver's to use.

    Not only those three names: ANY calibration* file (a .N.bak rotation,
    an .en-iyi best copy, a samples .npz), any config.son-iyi* file and
    makine.json also mean "not fresh" (final review M-4) - calibration is
    the costliest thing the user makes. A folder that cannot be listed is
    not fresh either."""
    if paths.mode() != "source":
        return False
    folder = paths.data_dir()
    backup = os.path.splitext(_config.backup_path("config.yaml"))[0].lower()
    try:
        names = [n.lower() for n in os.listdir(folder)]
    except FileNotFoundError:
        return True             # nothing there at all
    except OSError:
        return False
    return not any(n == "config.yaml" or n == "makine.json"
                   or n.startswith("calibration") or n.startswith(backup)
                   for n in names)


def prepare_first_run():
    """True on the first start, after copying config.default.yaml into the
    data folder: an installed/portable copy without config.yaml, or a fresh
    source checkout (fresh_source_checkout()). Never for a source install
    that already runs (the development machine: R24)."""
    if paths.mode() == "source" and not fresh_source_checkout():
        return False
    target = config_path()
    if os.path.exists(target):
        return False
    paths.ensure_dir(os.path.dirname(target))
    tmp = target + ".tmp"
    shutil.copyfile(default_config_path(), tmp)
    os.replace(tmp, target)
    return True


# --- Linux permission installer (module level: tests replace them) ----------

def _permission_cmd():
    from eye_gaze.platform import linux_evdev
    return linux_evdev.install_permissions_cmd()


def _run_command(argv):
    return subprocess.run(argv).returncode


def _remove_installer_copy(argv):
    """A frozen build hands pkexec a private temp copy of izin-kur.sh (the
    AppImage mount may be unreadable for root): remove it afterwards."""
    if not getattr(sys, "frozen", False) or len(argv) < 2:
        return
    folder = os.path.dirname(argv[1])
    if os.path.basename(folder).startswith("gozfaresi-izin-"):
        shutil.rmtree(folder, ignore_errors=True)


class Services:
    """Everything the windows need from the machine. Every method may be
    slow (they run on a Worker thread) unless noted."""

    def __init__(self):
        self.os_name = _platform.OS

    # --- files ---------------------------------------------------------------
    def mode(self):
        return paths.mode()

    def config_path(self):
        return config_path()

    def log_path(self):
        return os.path.join(paths.data_dir(), "gaze.log")

    def read_config(self):
        """(cfg, problem) - quiet: a window must not print on every read."""
        return _config.read_config(self.config_path(),
                                   report=lambda message: None)

    def set_values(self, values):
        from eye_gaze import config_writer
        config_writer.set_values(self.config_path(), values)

    def load_profile(self):
        from eye_gaze import hardware
        return hardware.load_profile()

    def profile_exists(self):
        from eye_gaze import hardware
        return os.path.exists(hardware.profile_path())

    def save_profile(self, profile):
        from eye_gaze import hardware
        return hardware.save_profile(profile)

    # --- hardware ------------------------------------------------------------
    def probe_cameras(self):
        from eye_gaze import hardware
        return hardware.probe_cameras()

    def open_camera(self, info):
        from eye_gaze import hardware
        return hardware.open_capture(info.index, info.width, info.height,
                                     hardware.REQUEST_FPS, info.backend)

    def face_detector(self):
        """callable(frame) -> bool (with .close()), or None."""
        try:
            from eye_gaze.gaze import GazeEstimator
            est = GazeEstimator()
        except Exception as exc:
            print("[baslatici] yuz denetimi acilamadi: %s" % ascii(str(exc)))
            return None

        class _Face:
            def __call__(self, frame):
                return est.estimate(frame) is not None

            def close(self):
                est.close()
        return _Face()

    def screens(self):
        from eye_gaze import hardware
        return hardware.screens()

    def scale(self):
        from eye_gaze import hardware
        return hardware.scale()

    def machine(self):
        from eye_gaze import hardware
        return hardware.machine()

    def gpu_info(self):
        from eye_gaze import accel
        return accel.gpu_info()

    def cuda_ready(self):
        """CTranslate2 (dictation) sees a CUDA device AND its cuBLAS/cuDNN
        load. Never torch: importing it here would wake the GPU and cost
        seconds."""
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() <= 0:
                return False
        except Exception:
            return False
        from eye_gaze import accel
        return bool(accel.preload_cuda(accel.gpu_lib_dir(), "ctranslate2"))

    def benchmark(self):
        """(ms per frame, provider) of the gaze path - may use the GPU: the
        wizard runs it only on the user's button press."""
        from eye_gaze import accel, hardware
        provider = accel.usable(accel.providers("auto"))[0]
        return hardware.benchmark_gaze(3), provider

    # --- downloads -----------------------------------------------------------
    def download_status(self, name):
        from eye_gaze import downloader
        try:
            return downloader.status(name)
        except Exception:
            return "missing"

    def download(self, name, progress=None, cancel=None):
        """(path, None) or (None, Turkish reason)."""
        from eye_gaze import downloader
        path = downloader.ensure(name, progress, cancel)
        if path:
            return path, None
        return None, downloader.last_error or t("launcher.unknown_error")

    def refresh_gpu_libs(self):
        """After the GPU pack arrived mid-session: load it for every CUDA
        consumer on this OS (accel remembered "no" per folder)."""
        from eye_gaze import accel
        libdir = accel.gpu_lib_dir()
        accel.preload_cuda(libdir, "ctranslate2", refresh=True)
        if self.os_name == "linux":
            accel.preload_cuda(libdir, "onnxruntime", refresh=True)

    # --- input ---------------------------------------------------------------
    def capture_key(self, timeout_s=10):
        from eye_gaze.ui import keycapture
        return keycapture.capture_key(timeout_s)

    def capture_pad(self, timeout_s=10):
        from eye_gaze.ui import keycapture
        return keycapture.capture_pad(timeout_s)

    def gamepad_present(self):
        try:
            from eye_gaze.switch import GamepadReader
            return GamepadReader().buttons() is not None
        except Exception:
            return False

    def capabilities(self):
        return _platform.capabilities()

    # --- system --------------------------------------------------------------
    def linux_permission(self):
        from eye_gaze.platform import linux_evdev
        return linux_evdev.permission_ok()

    def install_permissions(self):
        """(ok, Turkish sentence). pkexec asks for the password itself."""
        try:
            argv = _permission_cmd()
        except Exception as exc:
            return False, t("launcher.perm.no_script", error=exc)
        try:
            code = _run_command(argv)
        except FileNotFoundError:
            return False, t("launcher.perm.no_pkexec")
        except Exception as exc:
            return False, t("launcher.perm.run_failed", error=exc)
        finally:
            _remove_installer_copy(argv)
        if code == 0:
            return True, t("launcher.perm.done")
        if code in (126, 127):
            return False, t("launcher.perm.refused")
        return False, t("launcher.perm.failed", code=code)

    def autostart_enabled(self):
        from eye_gaze.ui import autostart
        return autostart.is_enabled()

    def set_autostart(self, on):
        from eye_gaze.ui import autostart
        return autostart.set_enabled(on)

    def open_log(self):
        path = self.log_path()
        if not os.path.exists(path):
            return False
        try:
            if os.name == "nt":
                os.startfile(path)      # noqa: S606 - the user's own log
            else:
                subprocess.Popen(["xdg-open", path])
            return True
        except Exception as exc:
            print("[baslatici] gunluk acilamadi: %s" % ascii(exc))
            return False


# --- status window -----------------------------------------------------------

class StatusWindow:
    """The small window that stays: what the driver is doing, and the way
    to the settings for a caregiver."""

    def __init__(self, root, launcher):
        self.launcher = launcher
        widgets.apply_theme(root)
        # A window of its own over the hidden root: closing it is a
        # question, and the root's life is the program's.
        self.win = win = widgets.toplevel(root, app_title())
        frame = tk.Frame(win, bg=BG, padx=24, pady=18)
        frame.pack(fill="both", expand=True)
        self.title_label = widgets.text(frame, "title", value=app_title())
        self.title_label.pack(anchor="w")
        self.state_var = tk.StringVar(win, "")
        self.warning_var = tk.StringVar(win, "")
        self.state_label = widgets.text(frame, "big",
                                        textvariable=self.state_var, wrap=560)
        self.state_label.pack(anchor="w", pady=(6, 4))
        widgets.text(frame, "warn", textvariable=self.warning_var,
                     wrap=560).pack(anchor="w")
        row = tk.Frame(frame, bg=BG, pady=10)
        row.pack(anchor="w")
        self.settings_button = widgets.button(row, t("launcher.settings"),
                                              launcher.open_settings,
                                              primary=True)
        self.settings_button.pack(side="left")
        self.toggle_button = widgets.button(row, t("launcher.stop"),
                                            launcher.toggle)
        self.toggle_button.pack(side="left", padx=10)
        self.log_button = widgets.button(row, t("launcher.open_log"),
                                         launcher.open_log)
        self.log_button.pack(side="left")
        self._running = True
        win.protocol("WM_DELETE_WINDOW", launcher.request_close)
        try:
            # Windows logoff / shutdown: Tk maps WM_QUERYENDSESSION here.
            win.protocol("WM_SAVE_YOURSELF", launcher.shutdown)
            root.protocol("WM_SAVE_YOURSELF", launcher.shutdown)
        except tk.TclError:
            pass

    def set_running(self, running):
        self._running = bool(running)
        self.toggle_button.configure(text=t("launcher.stop") if running
                                     else t("launcher.start"))

    def retext(self):
        """Every fixed text again, in the language chosen now."""
        try:
            self.win.title(app_title())
            self.title_label.configure(text=app_title())
            self.settings_button.configure(text=t("launcher.settings"))
            self.log_button.configure(text=t("launcher.open_log"))
            self.set_running(self._running)
        except tk.TclError:
            pass


# --- the launcher ------------------------------------------------------------

class Launcher:
    def __init__(self, root, services, argv=(), supervisor=None, ask=None,
                 tell=None, worker=None):
        self.root = root
        self.services = services
        self.argv = list(argv)
        self._language_from_config()
        widgets.apply_theme(root)
        self.status = StatusWindow(root, self)
        d_ask, d_tell = widgets.dialogs(self.status.win)
        self.ask = ask or d_ask
        self.tell = tell or d_tell
        # The wizard and the settings ask over their own window unless the
        # caller (a test) replaced the dialogs: a message box owned by the
        # hidden status window could open behind the wizard.
        self._dialog_override = (ask, tell)
        self.worker = worker or widgets.Worker(root)
        if supervisor is None:
            supervisor = _sv.Supervisor(
                _sv.driver_command(), self.on_state, schedule=root.after,
                cancel=root.after_cancel, cwd=paths.app_dir(),
                on_stopping=self._stopping)
        self.supervisor = supervisor
        self.wizard = None
        self.settings = None
        self._closed = False
        self._last_state = None
        self._show_capabilities()

    # --- language ------------------------------------------------------------

    def _language_from_config(self):
        """config.yaml's `language` for every window (Turkish when absent:
        the development machine's config.yaml has no such line). Never raises."""
        try:
            cfg, _problem = self.services.read_config()
        except Exception:
            cfg = {}
        i18n.setup(cfg)

    def set_language(self, code):
        """The wizard's or the settings window's choice, for the launcher's
        windows at once (the driver takes it at its next start)."""
        used = i18n.set_language(code)
        self.status.retext()
        self._show_capabilities()
        if self._last_state is not None:
            self._render_state(self._last_state)
        return used

    # --- start ---------------------------------------------------------------

    def start(self):
        first = False
        try:
            first = prepare_first_run()
        except OSError as exc:
            self.tell(app_title(), t("launcher.config_create_failed",
                                     error=exc))
        if first:
            # The copied default may name a language: take it now.
            self._language_from_config()
            self.status.retext()
            self._show_capabilities()
        if first or "--sihirbaz" in self.argv or self._offer_wizard_again():
            # A first start may write even in source mode: only a fresh
            # checkout gets here (fresh_source_checkout), nothing to protect.
            self.open_wizard(writable=True if first else None)
            return
        self.show_status()
        self.supervisor.start()
        if "--ayarlar" in self.argv:
            self.open_settings()

    def _offer_wizard_again(self):
        """An installed/portable copy with config.yaml but no makine.json:
        the first-run wizard never finished (closed by a crash, a power cut,
        a kill). Offer it once more; a "no" is remembered in makine.json so
        the question does not come back at every start. Never in source
        mode (R24)."""
        s = self.services
        if s.mode() == "source" or s.profile_exists():
            return False
        if self.ask(app_title(), t("launcher.wizard_again")):
            return True
        profile = dict(s.load_profile() or {})
        profile["sihirbaz"] = "atlandi"
        s.save_profile(profile)
        return False

    def _stopping(self):
        """Supervisor.stop() is about to wait for the driver (seconds)."""
        try:
            self.status.state_var.set(t("launcher.state.stopping"))
            self.status.win.update_idletasks()
        except tk.TclError:
            pass

    def show_status(self):
        widgets.show(self.status.win)

    def _show_capabilities(self):
        try:
            lines = widgets.missing_lines(self.services.capabilities(),
                                          widgets.CRITICAL_CAPS)
        except Exception as exc:
            lines = [t("launcher.caps_failed", error=exc)]
        note = i18n.notice()
        if note:
            lines.insert(0, note)
        self.status.warning_var.set("\n".join(lines))

    # --- supervisor ----------------------------------------------------------

    def on_state(self, state):
        sup = self.supervisor
        self._last_state = state
        self._render_state(state)
        if state == "kapandi" and sup.reason == "cikis":
            # F10 twice: the user closed the program, as BASLAT.bat did.
            self.shutdown()

    def _render_state(self, state):
        sup = self.supervisor
        if state == "calisiyor":
            text = t("launcher.state.running")
        elif state == "yeniden-baslatiliyor" and sup.reason == "cokuyor":
            text = sup.message          # the back-off: names its own wait
        elif state == "yeniden-baslatiliyor":
            text = t("launcher.state.restarting",
                     seconds="%d" % _sv.RESTART_DELAY_S)
            if sup.message:
                text += "\n" + sup.message
        elif state == "ayar-hatasi":
            text = (t("launcher.state.config_error") + "\n"
                    + self._config_problem())
        elif sup.reason == "cokuyor":
            text = sup.message
        elif sup.reason == "durduruldu":
            text = t("launcher.state.stopped")
        else:
            text = t("launcher.state.closed")
        self.status.state_var.set(text)
        self.status.state_label.configure(
            fg=widgets.FG if state == "calisiyor" else widgets.WARN)
        self.status.set_running(state in ("calisiyor",
                                          "yeniden-baslatiliyor"))

    def _config_problem(self):
        try:
            _cfg, problem = self.services.read_config()
        except Exception as exc:
            problem = str(exc)
        tail = problem or t("launcher.config_detail_in_log")
        return tail + "\n" + t("launcher.config_fix_hint")

    def toggle(self):
        if self.supervisor.running() or self.supervisor.state == \
                "yeniden-baslatiliyor":
            self.supervisor.stop()
        else:
            self.supervisor.start()

    # --- windows -------------------------------------------------------------

    def open_settings(self):
        if self.settings is not None:
            widgets.show(self.settings.win)
            return
        from eye_gaze.ui import settings

        def closed():
            self.settings = None
        ask, tell = self._dialog_override
        self.settings = settings.SettingsWindow(
            self.root, self.services, self.supervisor, ask=ask, tell=tell,
            worker=self.worker, on_close=closed,
            on_language=self.set_language)

    def open_wizard(self, writable=None):
        if self.wizard is not None:
            widgets.show(self.wizard.win)
            return
        # The wizard needs the camera (probe, preview) and the keyboard:
        # paused, a crash restart that is due cannot come back meanwhile.
        self.supervisor.pause()
        if self.settings is not None:
            self.settings.close()
        from eye_gaze.ui import wizard
        try:
            self.status.win.withdraw()
        except tk.TclError:
            pass
        ask, tell = self._dialog_override
        self.wizard = wizard.Wizard(self.root, self.services,
                                    on_done=self._wizard_done, ask=ask,
                                    tell=tell, worker=self.worker,
                                    writable=writable,
                                    on_language=self.set_language)

    def _wizard_done(self, start):
        self.wizard = None
        self.show_status()
        self.supervisor.resume(restart=start)
        if start:
            self.supervisor.start()
        else:
            self.status.state_var.set(t("launcher.state.stopped_hint"))
            # The text says "press «Başlat»": the button must read Başlat
            # too (it still said "Durdur" - no state change had redrawn it).
            self.status.set_running(self.supervisor.running())

    def open_log(self):
        if not self.services.open_log():
            self.tell(app_title(), t("launcher.no_log",
                                     path=self.services.log_path()))

    def on_message(self, message):
        """From a second launcher (any thread): marshalled to Tk."""
        def handle():
            if message == "ayarlar":
                self.show_status()
                self.open_settings()
            elif message == "sihirbaz":
                self.open_wizard()
            elif self.wizard is not None:
                widgets.show(self.wizard.win)
            else:
                self.show_status()
        self.worker.post(handle)

    # --- closing -------------------------------------------------------------

    def request_close(self):
        if not self.ask(app_title(), t("launcher.confirm_close")):
            return False
        self.shutdown()
        return True

    def shutdown(self):
        """Stop the driver, close every window, end the main loop."""
        if self._closed:
            return
        self._closed = True
        try:
            self.supervisor.stop()
        finally:
            for win in (getattr(self.wizard, "win", None),
                        getattr(self.settings, "win", None),
                        self.status.win):
                if win is not None:
                    try:
                        win.destroy()
                    except tk.TclError:
                        pass
            self.wizard = self.settings = None
            try:
                self.root.quit()
            except tk.TclError:
                pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    message = ("sihirbaz" if "--sihirbaz" in argv else
               "ayarlar" if "--ayarlar" in argv else "goster")
    lock = _sv.InstanceLock()
    if not lock.acquire():
        if lock.send(message):
            print("[baslatici] Goz Faresi zaten acik; o pencere one"
                  " getirildi.")
            return 0
        try:
            r = tk.Tk()
            r.withdraw()
            try:
                i18n.setup(Services().read_config()[0])
            except Exception:
                pass
            widgets.dialogs(r)[1](app_title(), t("launcher.already_open"))
            r.destroy()
        except tk.TclError:
            print("[baslatici] Goz Faresi zaten acik.")
        return 0
    app = None
    try:
        root = tk.Tk()
        root.withdraw()
        app = Launcher(root, Services(), argv=argv)
        try:
            lock.serve(app.on_message)
        except OSError as exc:
            print("[baslatici] ileti soketi acilamadi: %s" % ascii(exc))
        app.start()
        root.mainloop()
    finally:
        if app is not None:
            app.shutdown()
        try:
            root.destroy()
        except (tk.TclError, NameError):
            pass
        lock.release()
    return 0
