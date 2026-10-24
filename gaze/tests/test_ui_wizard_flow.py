# gaze/tests/test_ui_wizard_flow.py
"""The launcher's windows - first-run wizard, settings, status - driven
through fake hardware. Real Tk, hidden (withdrawn) on Windows and under Xvfb
in Docker; no mainloop, no real camera, keyboard, GPU or network."""
import gc
import os
import shutil
import sys

import pytest

tk = pytest.importorskip("tkinter")

from eye_gaze import hardware, paths  # noqa: E402
from eye_gaze.ui import widgets  # noqa: E402

GAZE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(GAZE_DIR, "config.default.yaml")


@pytest.fixture
def _shared_root(tk_root):
    # The suite's one interpreter (conftest.tk_root): a fresh Tk() per test,
    # or per module, failed now and then on Windows.
    return tk_root


@pytest.fixture
def root(_shared_root, monkeypatch):
    monkeypatch.setattr(widgets, "HIDDEN", True)
    yield _shared_root
    for child in _shared_root.winfo_children():
        try:
            child.destroy()
        except tk.TclError:
            pass
    # This test's Tk variables go now, on the Tk thread (from another
    # thread each would wait for a main loop that never runs).
    gc.collect()


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    d = tmp_path / "Örnek verisi çğı"
    d.mkdir()
    monkeypatch.setenv("GOZFARESI_DATA_DIR", str(d))
    return d


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setattr(paths, "mode", lambda: "installed")


def _cam(index=0, w=1280, h=960, fps=30.0):
    return hardware.CameraInfo(index, "msmf", w, h, fps, "")


class FakeSup:
    def __init__(self):
        self.calls = []
        self.state = "kapandi"
        self.reason = None
        self.message = ""
        self.exit_code = None
        self.alive = False
        self.pending = False        # a crash restart is due
        self.pauses = 0
        self.wanted = False

    def start(self):
        if self.pauses:
            self.wanted = True
            return
        self.calls.append("start")
        self.alive = True
        self.pending = False
        self.state = "calisiyor"

    def pause(self):
        # The real Supervisor's rule: nested; running OR pending counts.
        if self.pauses == 0:
            self.wanted = self.alive or self.pending
            if self.wanted:
                self.stop()
        self.pauses += 1

    def resume(self, restart=True):
        if not restart:
            self.wanted = False
        self.pauses -= 1
        if self.pauses == 0 and self.wanted:
            self.wanted = False
            self.start()

    def stop(self):
        self.calls.append("stop")
        self.alive = False
        self.pending = False
        self.state = "kapandi"

    def restart(self):
        self.calls.append("restart")
        self.alive = True

    def running(self):
        return self.alive


def make_services(**over):
    from eye_gaze import ui

    class Fake(ui.Services):
        bench = over.get("bench", (20.0, "DmlExecutionProvider"))
        gpu = over.get("gpu", {"name": "NVIDIA Test GPU",
                               "provider": "DmlExecutionProvider",
                               "vram_mb": 24576, "vram_free_mb": 20000})
        downloads = over.get("downloads", {})
        keys_pressed = list(over.get("keys_pressed", []))
        pads_pressed = list(over.get("pads_pressed", []))
        pad_there = over.get("pad_there", True)
        caps = over.get("caps", None)
        perm = over.get("perm", (True, "Giriş aygıtı izinleri tamam"))

        def __init__(self):
            super().__init__()
            self.os_name = over.get("os_name", "windows")
            self.log = []
            self.probes = 0
            self.refreshed = []
            self.cuda = over.get("cuda", False)
            self.statuses = dict(over.get("statuses", {}))
            # On the instance: a function stored on the class would bind.
            self.cameras = over.get("cameras", [_cam()])

        def probe_cameras(self):
            self.probes += 1
            got = self.cameras
            if callable(got):
                got = got(self.probes)
            return list(got)

        def open_camera(self, info):
            return None             # no preview frames in tests

        def face_detector(self):
            return None

        def screens(self):
            return [(0, 0, 2560, 1440)]

        def scale(self):
            return 1.5

        def machine(self):
            return {"cpu_cores": 16, "ram_gb": 32.0}

        def gpu_info(self):
            return dict(self.gpu)

        def cuda_ready(self):
            return self.cuda

        def benchmark(self):
            if isinstance(self.bench, Exception):
                raise self.bench
            return self.bench

        def download_status(self, name):
            return self.statuses.get(name, "missing")

        def download(self, name, progress=None, cancel=None):
            self.log.append(("download", name))
            result = self.downloads.get(name, ("/m/" + name, None))
            if progress is not None:
                progress(5, 10)
            if result[0] is not None:
                self.statuses[name] = "ok"
            return result

        def refresh_gpu_libs(self):
            self.refreshed.append(True)
            self.cuda = True

        def capture_key(self, timeout_s=10):
            self.log.append("capture_key")
            return self.keys_pressed.pop(0) if self.keys_pressed else None

        def capture_pad(self, timeout_s=10):
            self.log.append("capture_pad")
            return self.pads_pressed.pop(0) if self.pads_pressed else None

        def gamepad_present(self):
            return self.pad_there

        def capabilities(self):
            if self.caps is not None:
                return dict(self.caps)
            # Never the real one: on Linux it starts an input backend.
            from eye_gaze.platform import CAPABILITY_KEYS
            return {k: (True, "") for k in CAPABILITY_KEYS}

        def autostart_enabled(self):
            return False

        def linux_permission(self):
            return self.perm

    return Fake()


class Dialogs:
    """answer: the reply to every question; answers: replies in order
    (then `answer`)."""

    def __init__(self, answer=True, answers=()):
        self.asked = []
        self.told = []
        self.answer = answer
        self.answers = list(answers)

    def ask(self, title, text):
        self.asked.append(text)
        if self.answers:
            return self.answers.pop(0)
        return self.answer

    def tell(self, title, text):
        self.told.append(text)


def make_wizard(root, services, dialogs=None, done=None):
    from eye_gaze.ui import wizard
    dialogs = dialogs or Dialogs()
    finished = done if done is not None else []
    w = wizard.Wizard(root, services, on_done=finished.append,
                      ask=dialogs.ask, tell=dialogs.tell,
                      worker=widgets.SyncWorker())
    return w, finished


def goto(w, name):
    while w.current.name != name:
        w.skip()


def _write_config(data_dir, extra=""):
    path = os.path.join(str(data_dir), "config.yaml")
    shutil.copyfile(DEFAULT_CONFIG, path)
    if extra:
        with open(path, "a", encoding="utf-8") as f:
            f.write(extra)
    return path


# --- first run -----------------------------------------------------------

def test_first_run_copies_default_config_and_opens_wizard(root, data_dir,
                                                          installed):
    from eye_gaze import ui
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    cfg = data_dir / "config.yaml"
    with open(DEFAULT_CONFIG, "rb") as f:
        assert cfg.read_bytes() == f.read()
    assert app.wizard is not None
    assert app.wizard.current.name == "hosgeldiniz"
    # The driver waits for the wizard: the camera is the wizard's now.
    assert sup.calls == []


def test_a_second_start_does_not_open_the_wizard(root, data_dir, installed):
    from eye_gaze import ui
    _write_config(data_dir)
    hardware.save_profile({"machine": {"cpu_cores": 4, "ram_gb": 8.0}})
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    assert app.wizard is None and sup.calls == ["start"]


def test_source_mode_never_opens_the_wizard_by_itself(root, data_dir,
                                                      monkeypatch):
    """A source install that runs (the development machine: config.yaml present) is
    left as it is (R24): no wizard unless asked, nothing copied."""
    from eye_gaze import ui
    monkeypatch.setattr(paths, "mode", lambda: "source")
    path = _write_config(data_dir, "\n# bu makine\n")
    with open(path, "rb") as f:
        before = f.read()
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    assert app.wizard is None
    with open(path, "rb") as f:
        assert f.read() == before
    assert sup.calls == ["start"]
    app2 = ui.Launcher(root, make_services(), argv=["--sihirbaz"],
                       supervisor=FakeSup(), ask=Dialogs().ask,
                       tell=Dialogs().tell, worker=widgets.SyncWorker())
    app2.start()
    assert app2.wizard is not None
    assert app2.wizard.writable is False        # --sihirbaz: read-only


def test_a_fresh_source_checkout_is_set_up_like_a_first_start(
        root, data_dir, monkeypatch):
    """R29: the source zip carries no config.yaml (export-ignore). Started
    from it, the launcher copies config.default.yaml and opens the wizard,
    which may write (nothing of a running install to protect)."""
    from eye_gaze import ui
    monkeypatch.setattr(paths, "mode", lambda: "source")
    assert ui.fresh_source_checkout()
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    with open(DEFAULT_CONFIG, "rb") as f:
        assert (data_dir / "config.yaml").read_bytes() == f.read()
    assert app.wizard is not None and app.wizard.writable is True
    assert sup.calls == []      # the driver waits for the wizard
    assert not ui.fresh_source_checkout()       # only once


@pytest.mark.parametrize("left", [
    "calibration.json", "config.son-iyi.yaml",
    # final review M-4: any trace of a calibration or a machine profile
    "calibration.json.3.bak", "calibration.json.11.en-iyi",
    "calibration_samples.pe1-hp1-ht1-dg1.npz", "Calibration.JSON",
    "config.son-iyi.yaml.bak", "makine.json"])
def test_a_source_install_missing_only_config_yaml_is_not_reset(
        root, data_dir, monkeypatch, left):
    """A running source install whose config.yaml went missing keeps its
    calibration / backup untouched: no default copied over it, no wizard -
    the driver reports (or uses the backup) as before."""
    from eye_gaze import ui
    monkeypatch.setattr(paths, "mode", lambda: "source")
    (data_dir / left).write_text("{}", encoding="utf-8")
    assert not ui.fresh_source_checkout()
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    assert app.wizard is None
    assert not (data_dir / "config.yaml").exists()
    assert (data_dir / left).read_text(encoding="utf-8") == "{}"
    assert sup.calls == ["start"]


# --- wizard steps ----------------------------------------------------------

def test_every_step_has_skip_and_back(root, data_dir, installed):
    w, _ = make_wizard(root, make_services())
    names = []
    for i in range(len(w.steps)):
        names.append(w.current.name)
        assert w.skip_button.winfo_exists() and w.back_button.winfo_exists()
        assert str(w.back_button["state"]) == ("disabled" if i == 0
                                               else "normal")
        assert str(w.skip_button["state"]) == "normal"
        if i < len(w.steps) - 1:
            w.skip()
    assert names == ["hosgeldiniz", "kamera", "donanim", "indirmeler",
                     "tuslar", "tiklama", "kalibrasyon"]
    w.back()
    assert w.current.name == "tiklama"


def test_wizard_text_is_large(root, data_dir, installed):
    import tkinter.font as tkfont
    w, _ = make_wizard(root, make_services())
    for name in ("TkDefaultFont", "TkTextFont"):
        # root= always: another test module keeps a Tk alive on a thread of
        # its own (linux_misc clipboard), and the default root may be it.
        f = tkfont.nametofont(name, root=root)
        assert abs(f.actual("size")) >= 16


def test_wizard_no_camera_shows_retry(root, data_dir, installed):
    services = make_services(cameras=lambda n: [] if n == 1 else [_cam(1)])
    w, _ = make_wizard(root, services)
    goto(w, "kamera")
    step = w.current
    assert "Kamera bulunamadı" in step.status_var.get()
    assert "Yeniden dene" in step.status_var.get()
    assert step.retry_button.winfo_manager()        # shown
    step.retry()
    assert services.probes == 2
    assert [c.index for c in step.cameras] == [1]
    assert "Kamera bulunamadı" not in step.status_var.get()
    w.next()
    assert w.profile["camera"]["index"] == 1


def test_wizard_without_any_camera_still_finishes(root, data_dir, installed):
    services = make_services(cameras=[])
    w, done = make_wizard(root, services)
    _write_config(data_dir)
    for _ in range(len(w.steps) - 1):
        w.next()
    w.next()                    # "Kalibrasyona başla"
    assert done == [True]
    assert "camera" not in hardware.load_profile()


def test_hardware_step_sentence(root, data_dir, installed):
    w, _ = make_wizard(root, make_services())
    goto(w, "donanim")
    step = w.current
    assert "NVIDIA Test GPU" in step.info_var.get()
    assert "2560x1440" in step.info_var.get()
    step.measure()
    assert step.result_var.get() == "Derin model: açık (ekran kartı)"
    assert w.profile["gaze_ms"] == 20.0

    slow = make_services(bench=(80.0, "CPUExecutionProvider"))
    w2, _ = make_wizard(root, slow)
    goto(w2, "donanim")
    w2.current.measure()
    assert w2.current.result_var.get() == \
        "Derin model: kapalı (bilgisayar yavaş)"


def test_a_failed_benchmark_decides_nothing(root, data_dir, installed):
    w, _ = make_wizard(root, make_services(bench=RuntimeError("model yok")))
    goto(w, "donanim")
    w.current.measure()
    assert "ölçülemedi" in w.current.result_var.get()
    assert "gaze_ms" not in w.profile


def test_wizard_offline_marks_whisper_missing_and_continues(root, data_dir,
                                                            installed):
    offline = ("İnternet bağlantısı yok; dosya indirilemedi.",)
    services = make_services(
        gpu={"name": "Intel UHD", "provider": "DmlExecutionProvider",
             "vram_mb": None, "vram_free_mb": None},
        downloads={"whisper-small": (None, offline[0])})
    _write_config(data_dir)
    w, done = make_wizard(root, services)
    goto(w, "indirmeler")
    step = w.current
    # No NVIDIA card: no GPU pack offered.
    assert list(step.rows) == ["whisper-small"]
    row = step.rows["whisper-small"]
    row.start()
    assert "indirilemedi" in row.status_var.get().lower()
    assert "İnternet" in row.status_var.get()
    while w.current.name != "kalibrasyon":
        w.next()
    w.next()
    assert done == [True]


def test_gpu_pack_download_refreshes_cuda_and_whisper_size(root, data_dir,
                                                           installed):
    services = make_services()
    w, _ = make_wizard(root, services)
    goto(w, "indirmeler")
    step = w.current
    pack = [n for n in step.rows if n.startswith("nvidia-gpu")]
    assert pack, "an NVIDIA card is offered the GPU pack"
    assert "whisper-small" in step.rows       # no CUDA yet: small on CPU
    step.rows[pack[0]].start()
    assert services.refreshed == [True]
    assert "whisper-medium" in step.rows      # CUDA + 20 GB free: medium
    assert w.profile["gpu"]["cuda"] is True


def test_key_step_captures_and_warns_about_conflicts(root, data_dir,
                                                     installed):
    services = make_services(keys_pressed=["f2"])
    w, _ = make_wizard(root, services)
    goto(w, "tuslar")
    step = w.current
    step.table.change("alarm_key")
    assert services.log.count("capture_key") == 1
    assert step.table.values["alarm_key"] == "f2"
    assert "Tık menüsü" in step.table.warning_var.get()
    # A conflict keeps the wizard on this step.
    w.next()
    assert w.current.name == "tuslar"


def test_click_step_and_finish_write_config_and_profile(root, data_dir,
                                                        installed):
    services = make_services(keys_pressed=["f13"], pads_pressed=["BTN3"])
    path = _write_config(data_dir)
    w, done = make_wizard(root, services)
    w.next()
    assert w.current.name == "kamera"
    w.next()                    # the camera is taken with "İleri" only
    assert w.current.name == "donanim"
    w.current.measure()
    goto(w, "tuslar")
    w.current.table.change("left_click_key")
    w.current.table.change("gamepad_left")
    w.next()
    assert w.current.name == "tiklama"
    w.current.method.set("dwell")
    w.next()
    w.next()
    assert done == [True]
    from eye_gaze import config
    cfg, problem = config.read_config(path, report=lambda m: None)
    assert problem is None
    assert cfg["left_click_key"] == "f13" and cfg["gamepad_left"] == "BTN3"
    assert cfg["dwell_click"] is True and cfg["blink_click"] is False
    prof = hardware.load_profile()
    assert prof["camera"]["index"] == 0 and prof["camera"]["width"] == 1280
    assert prof["machine"] == {"cpu_cores": 16, "ram_gb": 32.0}
    assert prof["gpu"]["name"] == "NVIDIA Test GPU"
    assert prof["gpu"]["cuda"] is False
    assert prof["gaze_ms"] == 20.0
    assert prof["gaze_provider"] == "DmlExecutionProvider"
    assert prof["decisions"]["deep_gaze"] is True
    assert prof["decisions"]["stt_model"] == "small"
    assert prof["screens"] == [[0, 0, 2560, 1440]] and prof["scale"] == 1.5


def test_source_mode_wizard_writes_nothing(root, data_dir, monkeypatch):
    monkeypatch.setattr(paths, "mode", lambda: "source")
    services = make_services(keys_pressed=["f13"])
    path = _write_config(data_dir)
    with open(path, "rb") as f:
        before = f.read()
    w, done = make_wizard(root, services)
    assert "değiştirmez" in w.steps[0].note_var.get()
    goto(w, "donanim")
    w.current.measure()
    goto(w, "tuslar")
    w.current.table.change("left_click_key")
    goto(w, "kalibrasyon")
    w.next()
    assert done == [True]
    with open(path, "rb") as f:
        assert f.read() == before
    assert not os.path.exists(hardware.profile_path())


def test_click_step_without_a_live_route_asks(root, data_dir, installed):
    services = make_services(pad_there=False)
    _write_config(data_dir)
    dialogs = Dialogs(answer=False)
    w, done = make_wizard(root, services, dialogs)
    goto(w, "tuslar")
    w.current.table.set_value("left_click_key", "")
    w.next()                    # no key, no pad: asks, user says no
    assert len(dialogs.asked) == 1
    assert "gamepad" in dialogs.asked[-1].lower()
    assert w.current.name == "tuslar"
    dialogs.answer = True
    w.next()
    assert w.current.name == "tiklama"
    w.next()                    # the same route values: not asked twice
    assert len(dialogs.asked) == 2 and w.current.name == "kalibrasyon"
    # Switching to dwell gives a working route: no question at all.
    w.back()
    w.current.method.set("dwell")
    w.next()
    assert len(dialogs.asked) == 2


def test_skipping_the_click_step_cannot_write_a_dead_config(root, data_dir,
                                                            installed):
    """The reviewer's path: no gamepad, "Kaldır" on the left-click key,
    İleri, Atla on Tıklama, Kalibrasyona başla."""
    from eye_gaze import config
    path = _write_config(data_dir)
    dialogs = Dialogs(answer=False)
    w, done = make_wizard(root, make_services(pad_there=False), dialogs)
    goto(w, "tuslar")
    w.current.table.set_value("left_click_key", "")
    w.next()
    assert dialogs.asked and w.current.name == "tuslar"
    # Forced past the key step anyway (pending set as an older build or a
    # later step might): finish itself must still ask.
    w.pending["left_click_key"] = ""
    w.values["left_click_key"] = ""
    goto(w, "kalibrasyon")
    asked = len(dialogs.asked)
    w.next()
    assert len(dialogs.asked) == asked + 1
    assert "kaydedilsin mi" in dialogs.asked[-1]
    assert done == [] and w.current.name == "kalibrasyon"
    cfg, _ = config.read_config(path, report=lambda m: None)
    assert cfg["left_click_key"] == "space"         # nothing written
    dialogs.answer = True
    w.next()
    assert done == [True]
    cfg, _ = config.read_config(path, report=lambda m: None)
    assert cfg["left_click_key"] == ""


def test_closing_the_wizard_never_writes_a_dead_config_silently(
        root, data_dir, installed):
    from eye_gaze import config
    path = _write_config(data_dir)
    # "close?" yes, then "no click route - save anyway?" no.
    dialogs = Dialogs(answers=[True, False])
    w, done = make_wizard(root, make_services(pad_there=False), dialogs)
    w.pending["left_click_key"] = ""
    w.values["left_click_key"] = ""
    w.request_close()
    assert len(dialogs.asked) == 2 and "gamepad" in dialogs.asked[1].lower()
    assert done == [] and not w.closed and w.win.winfo_exists()
    cfg, _ = config.read_config(path, report=lambda m: None)
    assert cfg["left_click_key"] == "space"


def test_linux_permission_install(root, data_dir, installed, monkeypatch,
                                  tmp_path):
    folder = tmp_path / "gozfaresi-izin-abc"
    folder.mkdir()
    script = folder / "izin-kur.sh"
    script.write_text("#!/bin/sh\n")
    runs = []

    from eye_gaze import ui
    services = make_services(os_name="linux",
                             perm=(False, "Klavyeyi okuma izni yok"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(ui, "_permission_cmd",
                        lambda: ["pkexec", str(script), "tester"])
    monkeypatch.setattr(ui, "_run_command",
                        lambda argv: runs.append(argv) or 0)
    w, _ = make_wizard(root, services)
    step = w.current
    assert "Klavyeyi okuma izni yok" in step.perm_var.get()
    assert step.perm_button.winfo_manager()
    step.install_permissions()
    assert runs == [["pkexec", str(script), "tester"]]
    assert "Oturumu kapatıp açın" in step.perm_var.get()
    assert not folder.exists()          # the frozen temp copy is gone


def test_linux_permission_refused(root, data_dir, installed, monkeypatch):
    from eye_gaze import ui
    services = make_services(os_name="linux", perm=(False, "izin yok"))
    monkeypatch.setattr(ui, "_permission_cmd",
                        lambda: ["pkexec", "/x/izin-kur.sh", "u"])
    monkeypatch.setattr(ui, "_run_command", lambda argv: 126)
    w, _ = make_wizard(root, services)
    w.current.install_permissions()
    assert "verilmedi" in w.current.perm_var.get()


# --- settings ----------------------------------------------------------------

def make_settings(root, services, sup, dialogs=None):
    from eye_gaze.ui import settings
    dialogs = dialogs or Dialogs()
    return settings.SettingsWindow(root, services, sup, ask=dialogs.ask,
                                   tell=dialogs.tell,
                                   worker=widgets.SyncWorker())


def test_settings_save_conflict_blocks_and_explains(root, data_dir,
                                                    monkeypatch):
    from eye_gaze import config_writer
    path = _write_config(data_dir)
    writes = []
    monkeypatch.setattr(config_writer, "set_values",
                        lambda p, v: writes.append(v))
    sup = FakeSup()
    s = make_settings(root, make_services(keys_pressed=["space"]), sup)
    s.keys.change("alarm_key")
    assert s.save() is False
    assert writes == [] and "restart" not in sup.calls
    text = s.message_var.get()
    assert "Acil alarm" in text and "Sol tık" in text
    assert "Kaydedilmedi" in text
    with open(path, "rb") as f, open(DEFAULT_CONFIG, "rb") as g:
        assert f.read() == g.read()


def test_settings_save_writes_and_restarts(root, data_dir):
    path = _write_config(data_dir)
    sup = FakeSup()
    s = make_settings(root, make_services(), sup)
    s.set_value("tts_speed", 1.5)
    s.set_value("snap_to_targets", False)
    assert s.save() is True
    from eye_gaze import config
    cfg, _ = config.read_config(path, report=lambda m: None)
    assert cfg["tts_speed"] == 1.5 and cfg["snap_to_targets"] is False
    assert sup.calls == ["restart"]
    assert "Kaydedildi" in s.message_var.get()
    # Comments survive (config_writer edits one line).
    with open(path, encoding="utf-8") as f:
        assert "KONUSMA HIZI" in f.read()


def test_settings_save_without_a_live_click_route_asks(root, data_dir,
                                                       monkeypatch):
    from eye_gaze import config_writer
    _write_config(data_dir)
    writes = []
    monkeypatch.setattr(config_writer, "set_values",
                        lambda p, v: writes.append(dict(v)))
    dialogs = Dialogs(answer=False)
    sup = FakeSup()
    s = make_settings(root, make_services(pad_there=False), sup, dialogs)
    s.set_value("left_click_key", "")
    assert s.save() is False and writes == []
    assert "gamepad" in dialogs.asked[-1].lower()
    dialogs.answer = True
    assert s.save() is True and writes == [{"left_click_key": ""}]


def test_settings_writer_refusal_is_shown(root, data_dir, monkeypatch):
    from eye_gaze import config_writer

    def refuse(p, v):
        raise PermissionError("config.yaml salt okunur (yazma korumalı)")
    monkeypatch.setattr(config_writer, "set_values", refuse)
    _write_config(data_dir)
    sup = FakeSup()
    s = make_settings(root, make_services(), sup)
    s.set_value("tts_speed", 1.1)
    assert s.save() is False
    assert "salt okunur" in s.message_var.get() and sup.calls == []


def test_key_change_stops_the_driver_and_starts_it_again(root, data_dir):
    _write_config(data_dir)
    sup = FakeSup()
    sup.start()
    sup.calls.clear()
    services = make_services(keys_pressed=["f14"])
    s = make_settings(root, services, sup)
    s.keys.change("tts_key")
    assert sup.calls == ["stop", "start"]
    assert s.keys.values["tts_key"] == "f14"


def test_settings_lists_what_this_computer_lacks(root, data_dir):
    _write_config(data_dir)
    caps = {k: (True, "") for k in ("cursor", "global_keys", "snap")}
    caps["snap"] = (False, "Hedefe yapışma yalnızca Windows'ta var")
    caps["audio"] = (False, "Ses aygıtı açılamadı")
    s = make_settings(root, make_services(caps=caps), FakeSup())
    text = s.missing_text()
    assert "Hedefe yapışma" in text and "Ses" in text
    assert "alarm" in text.lower()
    assert "İmleç" not in text


def test_status_window_shows_critical_missing_capabilities(root, data_dir):
    from eye_gaze import ui
    _write_config(data_dir)
    caps = {"cursor": (False, "Wayland izin vermiyor"),
            "global_keys": (True, ""), "audio": (False, "ses yok"),
            "snap": (False, "yalnızca Windows")}
    sup = FakeSup()
    app = ui.Launcher(root, make_services(caps=caps), argv=[],
                      supervisor=sup, ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    text = app.status.warning_var.get()
    assert "Wayland izin vermiyor" in text and "ses yok" in text
    assert "yalnızca Windows" not in text


def test_status_texts_follow_the_supervisor(root, data_dir):
    from eye_gaze import ui
    _write_config(data_dir)
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    app.on_state("calisiyor")
    assert app.status.state_var.get() == "Çalışıyor"
    sup.message = "Sürücü sürekli çöküyor, günlüğü gönderin."
    sup.reason = "cokuyor"
    app.on_state("kapandi")
    assert "günlüğü gönderin" in app.status.state_var.get()
    sup.reason = None
    app.on_state("ayar-hatasi")
    assert "Ayar hatası" in app.status.state_var.get()


def test_the_crash_back_off_names_its_wait_and_durdur_ends_it(root, data_dir):
    # R36: after a crash loop the launcher keeps trying, slowly; the window
    # says so (not "5 sn") and its button stops it.
    from eye_gaze import i18n, ui
    _write_config(data_dir)
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    sup.alive = False
    sup.pending = True
    sup.state = "yeniden-baslatiliyor"
    sup.reason = "cokuyor"
    sup.message = i18n.t("supervisor.crash_loop", limit="5", seconds="120")
    app.on_state("yeniden-baslatiliyor")
    text = app.status.state_var.get()
    assert text == ("Sürücü sürekli çöküyor, günlüğü gönderin. (1 dakikada"
                    " 5 kez kapandı; 120 sn sonra yeniden denenecek.)")
    assert app.status.toggle_button.cget("text") == "Durdur"
    app.toggle()
    assert sup.calls[-1] == "stop"
    i18n.set_language("en")
    assert i18n.t("supervisor.crash_loop", limit="5", seconds="60") == (
        "The driver keeps crashing; please send the log. (It closed 5 times"
        " in 1 minute; it will try again in 60 s.)")


def test_closing_the_status_window_asks_and_stops_the_driver(root, data_dir):
    from eye_gaze import ui
    _write_config(data_dir)
    sup = FakeSup()
    dialogs = Dialogs(answer=False)
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=dialogs.ask, tell=dialogs.tell,
                      worker=widgets.SyncWorker())
    app.start()
    assert app.request_close() is False
    assert dialogs.asked and "Emin misiniz" in dialogs.asked[-1]
    assert "stop" not in sup.calls
    dialogs.answer = True
    assert app.request_close() is True
    assert sup.calls[-1] == "stop"


def test_a_second_launcher_message_opens_settings_or_wizard(root, data_dir):
    from eye_gaze import ui
    _write_config(data_dir)
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    app.on_message("ayarlar")
    assert app.settings is not None
    app.on_message("sihirbaz")
    assert app.wizard is not None and app.settings is None
    assert sup.calls[-1] == "stop"      # the wizard needs the camera


def test_f10_twice_closes_the_launcher(root, data_dir):
    from eye_gaze import ui
    _write_config(data_dir)
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    sup.reason = "cikis"
    app.on_state("kapandi")
    assert not app.status.win.winfo_exists()


def test_default_config_is_found_in_the_bundle(monkeypatch, tmp_path):
    from eye_gaze import ui
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert ui.default_config_path() == os.path.join(str(tmp_path),
                                                    "config.default.yaml")
    monkeypatch.delattr(sys, "frozen")
    assert ui.default_config_path() == DEFAULT_CONFIG


def test_an_unfinished_wizard_is_offered_again(root, data_dir, installed):
    from eye_gaze import ui
    _write_config(data_dir)         # config.yaml but no makine.json
    dialogs = Dialogs(answer=True)
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=dialogs.ask, tell=dialogs.tell,
                      worker=widgets.SyncWorker())
    app.start()
    assert "yarıda" in dialogs.asked[0] and app.wizard is not None
    assert sup.calls == []


def test_declining_the_offer_is_remembered(root, data_dir, installed):
    from eye_gaze import ui
    _write_config(data_dir)
    dialogs = Dialogs(answer=False)
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=dialogs.ask, tell=dialogs.tell,
                      worker=widgets.SyncWorker())
    app.start()
    assert app.wizard is None and sup.calls == ["start"]
    assert hardware.load_profile().get("sihirbaz") == "atlandi"
    again = Dialogs(answer=True)
    app2 = ui.Launcher(root, make_services(), argv=[], supervisor=FakeSup(),
                       ask=again.ask, tell=again.tell,
                       worker=widgets.SyncWorker())
    app2.start()
    assert again.asked == [] and app2.wizard is None


def test_source_mode_never_offers_the_wizard_again(root, data_dir,
                                                   monkeypatch):
    from eye_gaze import ui
    monkeypatch.setattr(paths, "mode", lambda: "source")
    _write_config(data_dir)
    dialogs = Dialogs(answer=True)
    app = ui.Launcher(root, make_services(), argv=[], supervisor=FakeSup(),
                      ask=dialogs.ask, tell=dialogs.tell,
                      worker=widgets.SyncWorker())
    app.start()
    assert dialogs.asked == [] and app.wizard is None
    assert not os.path.exists(hardware.profile_path())


def test_the_wizard_holds_a_pending_crash_restart(root, data_dir):
    from eye_gaze import ui
    _write_config(data_dir)
    hardware.save_profile({})
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    sup.alive, sup.pending = False, True    # crashed, restart due in 5 s
    app.on_message("sihirbaz")
    assert sup.pending is False and sup.pauses == 1
    sup.start()                             # e.g. "Başlat" meanwhile
    assert not sup.alive
    app.wizard.skip()
    while app.wizard is not None:
        app.wizard.skip()
    assert sup.pauses == 0 and not sup.alive    # "Şimdi değil"


def test_settings_pause_nests_and_holds_a_pending_restart(root, data_dir):
    _write_config(data_dir)
    sup = FakeSup()
    sup.pending = True                      # crash restart due
    services = make_services(keys_pressed=["f14"])
    s = make_settings(root, services, sup)
    s._pause_driver()                       # e.g. a camera search running
    assert sup.calls == ["stop"] and not sup.pending
    s.keys.change("tts_key")                # a capture during the search
    assert sup.calls == ["stop"]            # not restarted mid-search
    s._resume_driver()                      # the search ends
    assert sup.calls == ["stop", "start"]


def test_settings_camera_search_is_one_at_a_time(root, data_dir):
    from eye_gaze.ui import widgets as w_mod
    _write_config(data_dir)
    sup = FakeSup()
    sup.start()
    sup.calls.clear()
    services = make_services()
    probes = []

    class Later(w_mod.SyncWorker):
        def run(self, fn, on_done=None, on_error=None):
            probes.append((fn, on_done))
    from eye_gaze.ui import settings
    s = settings.SettingsWindow(root, services, sup, ask=Dialogs().ask,
                                tell=Dialogs().tell, worker=Later())
    s.search_cameras()
    s.search_cameras()
    assert len(probes) == 1 and sup.calls == ["stop"]
    fn, on_done = probes[0]
    on_done(fn())
    assert sup.calls == ["stop", "start"]
    assert str(s.search_button["state"]) == "normal"


def test_settings_any_writer_failure_is_shown(root, data_dir, monkeypatch,
                                              capsys):
    from eye_gaze import config_writer

    def broken(p, v):
        raise RuntimeError("beklenmeyen ğ hata")
    monkeypatch.setattr(config_writer, "set_values", broken)
    _write_config(data_dir)
    sup = FakeSup()
    s = make_settings(root, make_services(), sup)
    s.set_value("tts_speed", 1.1)
    assert s.save() is False
    assert "beklenmeyen ğ hata" in s.message_var.get()
    out = capsys.readouterr().out
    assert "kaydedilemedi" in out and out.isascii()
    assert sup.calls == []


def test_settings_in_source_mode_say_what_they_write(root, data_dir,
                                                    monkeypatch):
    _write_config(data_dir)
    monkeypatch.setattr(paths, "mode", lambda: "source")
    s = make_settings(root, make_services(), FakeSup())
    assert "gaze/config.yaml" in s.note_var.get()
    monkeypatch.setattr(paths, "mode", lambda: "installed")
    s2 = make_settings(root, make_services(), FakeSup())
    assert s2.note_var.get() == ""


def test_gpu_libraries_load_off_the_tk_thread(root, data_dir, installed):
    from eye_gaze.ui import wizard as wiz
    services = make_services()
    queued = []

    class Recording(widgets.SyncWorker):
        def run(self, fn, on_done=None, on_error=None):
            if getattr(fn, "__name__", "") == "load":
                queued.append((fn, on_done))
                return None
            return super().run(fn, on_done, on_error)
    w = wiz.Wizard(root, services, on_done=lambda s: None,
                   ask=Dialogs().ask, tell=Dialogs().tell,
                   worker=Recording())
    goto(w, "indirmeler")
    step = w.current
    pack = [n for n in step.rows if n.startswith("nvidia-gpu")][0]
    step.rows[pack].start()
    assert services.refreshed == [] and len(queued) == 1
    fn, on_done = queued[0]
    on_done(fn())
    assert services.refreshed == [True] and "whisper-medium" in step.rows


def test_the_status_says_stopping_while_it_waits(root, data_dir):
    from eye_gaze import ui
    _write_config(data_dir)
    hardware.save_profile({})
    app = ui.Launcher(root, make_services(), argv=[], supervisor=None,
                      ask=Dialogs().ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    assert app.supervisor._on_stopping == app._stopping
    # Recorded, not run: a real update_idletasks() here aborted the Linux
    # suite (it ran fine alone - another test module keeps a Tk alive on a
    # thread of its own).
    painted = []
    app.status.win.update_idletasks = lambda: painted.append(True)
    app._stopping()
    assert app.status.state_var.get() == "Durduruluyor…"
    assert painted == [True]


def test_the_focused_control_is_filled_orange_where_tk_draws_a_thin_ring(
        root, monkeypatch):
    # Task 16 frozen smoke: on Windows the thick orange focus ring shows as
    # a thin dotted line only. There (FOCUS_FILL) the focused control is
    # filled orange, black text, and gets its own colours back on leaving.
    # Tk drops a generated <FocusIn> while the (hidden) app has no focus,
    # so the class bindings are checked as registered and the handler is
    # called the way they call it.
    monkeypatch.setattr(widgets, "FOCUS_FILL", True)
    widgets.apply_theme(root)
    widgets.apply_theme(root)           # twice: still one handler
    try:
        for cls in ("Button", "Radiobutton", "Checkbutton"):
            for seq in ("<FocusIn>", "<FocusOut>"):
                script = root.bind_class(cls, seq)
                assert script and script.count("\n") <= 1, (cls, seq, script)
        win = widgets.toplevel(root, "odak")
        var = tk.StringVar(win, value="a")
        primary = widgets.button(win, "İleri", lambda: None, primary=True)
        plain = widgets.button(win, "Geri", lambda: None)
        chosen = widgets.radio(win, "Türkçe", var, "a")
        ticked = widgets.check(win, "Ses", tk.BooleanVar(win, value=True))
        for w in (primary, plain, chosen, ticked):
            w.pack()
        for w in (primary, plain, chosen, ticked):
            own = {k: str(w.cget(k)) for k in widgets._FILL if k in w.keys()}
            widgets.focus_fill(w, True)
            assert str(w.cget("bg")) == widgets.FOCUS
            assert str(w.cget("fg")) == widgets.FG
            if "selectcolor" in own:    # a chosen option shows selectcolor
                assert str(w.cget("selectcolor")) == widgets.FOCUS
            widgets.focus_fill(w, True)     # again: orange is not "own"
            widgets.focus_fill(w, False)
            assert {k: str(w.cget(k)) for k in own} == own
            widgets.focus_fill(w, False)    # a lone FocusOut changes nothing
            assert {k: str(w.cget(k)) for k in own} == own
        primary.destroy()
        widgets.focus_fill(primary, True)   # gone: no TclError
    finally:
        if sys.platform != "win32":     # leave the shared root as it was
            for cls in ("Button", "Radiobutton", "Checkbutton"):
                root.unbind_class(cls, "<FocusIn>")
                root.unbind_class(cls, "<FocusOut>")


def test_focus_fill_is_for_windows_only():
    # Linux draws the thick orange ring (Task 16 screenshots); its buttons
    # keep their colours.
    assert widgets.FOCUS_FILL is (sys.platform == "win32")


def test_show_leaves_the_focus_on_the_control_the_window_chose(
        monkeypatch):
    # Task 16 frozen smoke: the wizard opened with the keyboard focus on
    # the window itself - _show() had focused "İleri", then show()'s
    # focus_force() on the toplevel took it, for good (a focus_set() after
    # it did not win it back). Enter did nothing until someone found Tab.
    monkeypatch.setattr(widgets, "HIDDEN", False)
    calls = []

    class Win:
        shown = "normal"

        def state(self):
            return self.shown

        def deiconify(self):
            calls.append("deiconify")

        def lift(self):
            calls.append("lift")

        def attributes(self, name, value):
            calls.append((name, value))

        def focus_force(self):
            calls.append("focus_force")

    widgets.show(Win())          # a window on screen: no deiconify either
    assert calls == ["lift", ("-topmost", True), ("-topmost", False)]
    calls.clear()
    hidden = Win()
    hidden.shown = "withdrawn"
    widgets.show(hidden)
    assert "focus_force" not in calls
    assert calls == ["deiconify", "lift", ("-topmost", True),
                     ("-topmost", False)]


def test_not_now_leaves_the_status_window_offering_start(root, data_dir,
                                                         installed):
    # Task 16 frozen smoke: after "Şimdi değil" the window said "Durduruldu
    # - «Başlat»a basınca program açılır" over a button that still read
    # "Durdur" (and started the driver when pressed).
    from eye_gaze import ui
    from eye_gaze.i18n import t
    sup = FakeSup()
    app = ui.Launcher(root, make_services(), argv=[], supervisor=sup,
                      ask=Dialogs(answer=True).ask, tell=Dialogs().tell,
                      worker=widgets.SyncWorker())
    app.start()
    while app.wizard is not None:
        app.wizard.skip()               # ... the last one is "Şimdi değil"
    assert not sup.alive
    assert app.status.state_var.get() == t("launcher.state.stopped_hint")
    assert app.status.toggle_button.cget("text") == t("launcher.start")


def test_the_download_step_does_not_grow_with_its_rows(root, data_dir,
                                                       installed):
    # Task 16 frozen smoke: three rows (GPU pack, Whisper, English voice)
    # made the wizard 1166 px tall (logical) - taller than a 1080p screen
    # at 125 % (864 px), "İleri" below its bottom edge. The rows scroll in
    # a fixed-height area now, and the buttons are packed before the step
    # body, so a window cut down to the screen cuts the body, never them.
    w, _ = make_wizard(root, make_services())
    goto(w, "indirmeler")
    step = w.current

    def height_with(n):
        step.wanted = lambda: [
            ("whisper-%s" % s, "Whisper %s" % s, "not") for s in
            ("tiny", "base", "small", "medium")[:n]]
        for row in list(step.rows.values()):
            row.destroy()
        step.rows.clear()
        step.refresh_rows()
        w.win.update_idletasks()
        return w.win.winfo_reqheight()

    assert height_with(1) == height_with(4)
    # (The steps sit on one scrolling page, w.body_scroll.)
    slaves = w.body_scroll.master.pack_slaves()
    assert slaves.index(w.next_button.master) < slaves.index(w.body_scroll)
    # ... while Tab keeps its order: the step's controls, then the buttons.
    kids = w.body_scroll.master.winfo_children()
    assert kids.index(w.body_scroll) < kids.index(w.next_button.master)
    max_w, max_h = w.win.maxsize()
    assert max_w <= w.win.winfo_screenwidth()
    assert max_h <= w.win.winfo_screenheight()
