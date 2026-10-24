# gaze/tests/test_i18n.py
"""The language catalog (resources/lang/*.yaml) and its use: every language
has every key with the same {placeholders}, no Turkish text is left in the
code outside the catalog, an unknown language falls back to Turkish once,
and with `language: en` the launcher's windows and the driver's overlays
speak English. The rest of the suite runs in Turkish, the default."""
import ast
import os
import re
import string

import pytest
import yaml

from eye_gaze import i18n

GAZE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANG = os.path.join(GAZE, "resources", "lang")


@pytest.fixture(autouse=True)
def _fresh_language():
    i18n.reset()
    yield
    i18n.reset()


def _flat(tree, prefix="", out=None):
    out = {} if out is None else out
    for k, v in tree.items():
        k = str(k)
        if not prefix and k.startswith("_"):
            continue
        if isinstance(v, dict):
            _flat(v, prefix + k + ".", out)
        else:
            out[prefix + k] = v
    return out


def _catalog(code):
    with open(os.path.join(LANG, code + ".yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fields(text):
    return {f for _, f, _, _ in string.Formatter().parse(text)
            if f is not None}


# --- the catalog files --------------------------------------------------------

def _keys_are_strings(tree, where=""):
    for k, v in tree.items():
        # An unquoted on/off/yes/no key is a YAML boolean, not a name.
        assert isinstance(k, str), (where, k)
        if isinstance(v, dict):
            _keys_are_strings(v, where + str(k) + ".")


def test_every_language_has_the_same_keys():
    codes = [n[:-5] for n in os.listdir(LANG) if n.endswith(".yaml")]
    assert {"tr", "en"} <= set(codes)
    for c in codes:
        _keys_are_strings(_catalog(c), c + ":")
    keys = {c: set(_flat(_catalog(c))) for c in codes}
    for c in codes:
        assert keys[c] == keys["tr"], (
            c, sorted(keys["tr"] - keys[c]), sorted(keys[c] - keys["tr"]))


def test_placeholders_are_the_same_in_every_language():
    codes = [n[:-5] for n in os.listdir(LANG) if n.endswith(".yaml")]
    base = _flat(_catalog("tr"))
    for c in codes:
        other = _flat(_catalog(c))
        for key, text in base.items():
            assert isinstance(text, str) and isinstance(other[key], str), key
            assert _fields(text) == _fields(other[key]), (c, key)


def test_every_language_names_itself_and_turkish_comes_first():
    assert i18n.available()[:2] == [("tr", "Türkçe"), ("en", "English")]
    for code, own in i18n.available():
        assert own and own != code
    assert i18n.meta("_decimal", code="tr") == ","
    assert i18n.meta("_decimal", code="en") == "."


def test_texts_drawn_with_opencv_are_ascii_in_every_language():
    codes = [n[:-5] for n in os.listdir(LANG) if n.endswith(".yaml")]
    drawn = ("driver.", "click_menu.", "scan.", "common.", "aac.next",
             "aac.back", "aac.gaze_hint", "config.")
    for c in codes:
        for key, text in _flat(_catalog(c)).items():
            if key.startswith(drawn):
                assert text.isascii(), (c, key, text)


def _code_files():
    for name in sorted(os.listdir(GAZE)):
        if name.endswith(".py"):
            yield name
    for base, dirs, files in os.walk(os.path.join(GAZE, "eye_gaze")):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name.endswith(".py"):
                yield os.path.relpath(os.path.join(base, name),
                                      GAZE).replace(os.sep, "/")


def _sections():
    return {k.split(".", 1)[0] for k in _flat(_catalog("tr"))}


def test_every_key_the_code_names_is_in_the_catalog():
    keys = set(_flat(_catalog("tr")))
    pat = re.compile(r"^(%s)\.[a-z0-9_]+(\.[a-z0-9_]+)*$"
                     % "|".join(sorted(_sections())))
    named = set()
    for rel in _code_files():
        with open(os.path.join(GAZE, rel), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and pat.match(node.value)
                    and not node.value.endswith((".yaml", ".json"))):
                named.add((rel, node.value))
    missing = sorted((rel, k) for rel, k in named if k not in keys)
    assert not missing, missing
    assert len(named) > 300            # the scan really saw the code
    # Keys the code builds at run time.
    from eye_gaze import click_menu
    from eye_gaze.actions_catalog import ACTIONS
    from eye_gaze.platform import CAPABILITY_KEYS
    from eye_gaze.ui import widgets
    built = (["actions." + a.key for a in ACTIONS]
             + ["actions.group." + a.group_id for a in ACTIONS]
             + ["widgets.cap." + k for k in CAPABILITY_KEYS]
             + ["widgets.key." + k for k in widgets._KEY_WORDS]
             + ["widgets.click." + m for m in widgets._METHODS]
             + ["widgets.click.%s_note" % m for m in widgets._METHODS]
             + ["click_menu." + k for k in click_menu.ITEMS_UNDO
                if k != "drag"]
             + ["settings.tab." + k for k in ("keys", "cursor", "click",
                                              "zoom", "calibration", "camera",
                                              "sound", "advanced", "missing",
                                              "language")])
    assert not [k for k in built if k not in keys]


# --- no Turkish left outside the catalog --------------------------------------

_TR_LETTERS = re.compile("[çğıöşüÇĞİÖŞÜ]")

# Reviewed: every Turkish literal the code may keep, and why. Console/log
# lines (print(...)) and docstrings are not looked at at all.
_ALLOWED = {
    "eye_gaze/alarm.py": {
        "Yardım edin!": "ALARM_TEXT, the Turkish constant; the spoken "
                        "sentence is alarm_text() (alarm.phrase)",
    },
    "eye_gaze/config.py": {
        "true yes on 1 evet": "words config.yaml may say for on (data)",
        "false no off 0 hayir": "words config.yaml may say for off (data)",
    },
    "eye_gaze/snap.py": {
        "true yes on 1 evet": "words config.yaml may say for on (data)",
    },
    "eye_gaze/stt.py": {
        "altyazı m.k.": "Whisper's Turkish hallucinations (data, Part 2)",
        "izlediğiniz için": "Whisper's Turkish hallucinations (data, Part 2)",
        "abone olmayı": "Whisper's Turkish hallucinations (data, Part 2)",
        "altyazılar amara.org": "Whisper's Turkish hallucinations (data)",
    },
    "eye_gaze/ui/__init__.py": {
        "Göz Faresi": "APP_TITLE, the Turkish constant; windows use "
                      "app_title()",
    },
    "eye_gaze/ui/supervisor.py": {
        "Sürücü sürekli çöküyor": "CRASH_LOOP_MESSAGE, the Turkish "
                                  "constant; the window gets "
                                  "supervisor.crash_loop",
    },
    "eye_gaze/ui/autostart.py": {
        "[Desktop Entry]": "the program's name (Name=Göz Faresi) in the "
                           "autostart entry; its Comment is translated",
    },
    "eye_gaze/platform/linux_misc.py": {
        "Ekran klavyesi bulunamadı": "launch_osk(): printed by the driver "
                                     "(console)",
        "program kapanınca da açık": "launch_osk(): console",
        "GNOME ekran klavyesi açıl": "launch_osk(): console",
        "Ekran klavyesi (%s)": "launch_osk(): console",
    },
}


def _turkish_literals(rel):
    with open(os.path.join(GAZE, rel), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)):
                skip.add(id(body[0].value))          # docstring
        if isinstance(node, ast.Call):
            fn = node.func
            name = (fn.id if isinstance(fn, ast.Name) else
                    fn.attr if isinstance(fn, ast.Attribute) else "")
            if name == "print":                       # console / gaze.log
                skip.update(id(n) for n in ast.walk(node))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in skip and _TR_LETTERS.search(node.value)
                and len(node.value) > 1):         # 1 char: folding tables
            yield node.lineno, node.value


def test_no_turkish_text_is_left_in_the_code():
    found, used = [], set()
    for rel in _code_files():
        if rel == "eye_gaze/i18n.py":
            continue
        allowed = _ALLOWED.get(rel, {})
        for line, text in _turkish_literals(rel):
            hit = [p for p in allowed if text.startswith(p)]
            if hit:
                used.add((rel, hit[0]))
            else:
                found.append("%s:%d %r" % (rel, line, text[:70]))
    assert not found, "\n".join(found)
    stale = [(rel, p) for rel, ps in _ALLOWED.items() for p in ps
             if (rel, p) not in used]
    assert not stale, stale         # the allow-list stays honest


# --- lookup ------------------------------------------------------------------

def test_turkish_is_the_default_and_a_config_without_the_line_keeps_it():
    assert i18n.language() == "tr"
    assert i18n.setup({}) == "tr"
    assert i18n.setup({"language": ""}) == "tr"
    assert i18n.t("launcher.settings") == "Ayarlar"
    assert i18n.setup({"language": "EN"}) == "en"
    assert i18n.t("launcher.settings") == "Settings"


def test_an_unknown_language_falls_back_to_turkish_said_once(capsys):
    assert i18n.set_language("xx") == "tr"
    assert i18n.language() == "tr"
    assert i18n.t("launcher.settings") == "Ayarlar"
    assert "xx" in i18n.notice() and "Türkçe" in i18n.notice()
    i18n.set_language("xx")
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if "'xx'" in ln]
    assert len(lines) == 1, err
    assert lines[0].startswith("[dil]") and lines[0].isascii()
    assert i18n.set_language("en") == "en" and i18n.notice() == ""


def test_the_launcher_shows_the_unknown_language_notice(root):
    from eye_gaze import ui
    from eye_gaze.ui import widgets
    from tests.test_ui_wizard_flow import FakeSup, make_services
    services = make_services()
    services.read_config = lambda: ({"language": "xx"}, None)
    app = ui.Launcher(root, services, supervisor=FakeSup(),
                      ask=lambda *a: True, tell=lambda *a: None,
                      worker=widgets.SyncWorker())
    assert i18n.language() == "tr"
    assert "'xx'" in app.status.warning_var.get()
    app.shutdown()


def test_a_missing_or_broken_text_never_raises(tmp_path, monkeypatch,
                                               capsys):
    (tmp_path / "tr.yaml").write_text(
        '_name: "Türkçe"\na:\n  b: "Türkçe B"\n  c: "C {x}"\n',
        encoding="utf-8")
    (tmp_path / "en.yaml").write_text(
        '_name: "English"\na:\n  c: "E {y}"\n', encoding="utf-8")
    (tmp_path / "xx.yaml").write_text('_name: "Xx"\n', encoding="utf-8")
    (tmp_path / "bad.yaml").write_text("a: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(i18n, "lang_dir", lambda: str(tmp_path))
    i18n.reset()
    assert i18n.set_language("xx") == "xx"
    assert i18n.t("a.b") == "Türkçe B"          # xx -> en -> tr
    assert i18n.t("a.c", x=1) == "C 1"          # en's text is broken
    assert i18n.t("no.such.key") == "no.such.key"
    assert [c for c, _ in i18n.available()] == ["tr", "en", "xx"]
    assert i18n.set_language("bad") == "tr"     # unreadable: Turkish
    err = capsys.readouterr().err
    assert err.isascii()
    assert "eksik metin: a.b" in err and "no.such.key" in err


def test_numbers_are_written_the_language_s_way():
    from eye_gaze.ui import widgets
    assert widgets._num(1.5) == "1,5"
    i18n.set_language("en")
    assert widgets._num(1.5) == "1.5"


# --- English on screen: the launcher's windows ---------------------------------

tk = pytest.importorskip("tkinter")


@pytest.fixture
def _shared_root(tk_root):
    # The suite's one interpreter (conftest.tk_root).
    return tk_root


@pytest.fixture
def root(_shared_root, monkeypatch):
    from eye_gaze.ui import widgets
    monkeypatch.setattr(widgets, "HIDDEN", True)
    yield _shared_root
    for child in _shared_root.winfo_children():
        try:
            child.destroy()
        except tk.TclError:
            pass
    import gc
    gc.collect()


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    d = tmp_path / "veri çğ"
    d.mkdir()
    monkeypatch.setenv("GOZFARESI_DATA_DIR", str(d))
    return d


@pytest.fixture
def installed(monkeypatch):
    from eye_gaze import paths
    monkeypatch.setattr(paths, "mode", lambda: "installed")


def _flow():
    from tests import test_ui_wizard_flow as flow
    return flow


def test_the_wizard_in_english(root, data_dir, installed):
    flow = _flow()
    flow._write_config(data_dir, 'language: "en"\n')
    i18n.set_language("en")
    w, _ = flow.make_wizard(root, flow.make_services())
    assert w.win.title() == "Göz Faresi — First-run setup"
    assert w.title_var.get() == "Welcome"
    assert w.counter_var.get() == "Step 1 of 7"
    assert w.next_button["text"] == "Next ▶"
    assert w.skip_button["text"] == "Skip"
    flow.goto(w, "donanim")
    assert w.title_var.get() == "Screen and graphics card"
    w.current.measure()
    assert w.current.result_var.get() == "Deep model: on (graphics card)"
    flow.goto(w, "kalibrasyon")
    assert w.next_button["text"] == "Start calibration"
    assert w.skip_button["text"] == "Not now"


def test_choosing_english_in_the_wizard_redraws_it_and_is_saved(
        root, data_dir, installed):
    from eye_gaze import config
    flow = _flow()
    path = flow._write_config(data_dir)
    told = []
    w, done = flow.make_wizard(root, flow.make_services())
    w.on_language = lambda code: told.append(code) or i18n.set_language(code)
    step = w.current
    assert step.name == "hosgeldiniz" and w.title_var.get() == "Hoş geldiniz"
    step.lang_var.set("en")
    step._language_chosen()
    assert told == ["en"]
    assert w.title_var.get() == "Welcome"
    assert w.current.name == "hosgeldiniz" and w.current is not step
    assert w.current.lang_var.get() == "en"
    assert w.pending == {"language": "en"}
    # Back to Turkish: nothing to write for the language any more.
    w.current.lang_var.set("tr")
    w.current._language_chosen()
    assert w.title_var.get() == "Hoş geldiniz" and w.pending == {}
    w.current.lang_var.set("en")
    w.current._language_chosen()
    flow.goto(w, "kalibrasyon")
    w.next()
    assert done == [True]
    cfg, problem = config.read_config(path, report=lambda m: None)
    assert problem is None and cfg["language"] == "en"


def test_the_launcher_follows_the_wizard_s_language(root, data_dir,
                                                    installed):
    from eye_gaze import ui
    flow = _flow()
    sup = flow.FakeSup()
    app = ui.Launcher(root, flow.make_services(), argv=[], supervisor=sup,
                      ask=flow.Dialogs().ask, tell=flow.Dialogs().tell,
                      worker=flow.widgets.SyncWorker())
    app.start()                         # first run: the wizard opens
    assert app.status.settings_button["text"] == "Ayarlar"
    step = app.wizard.current
    step.lang_var.set("en")
    step._language_chosen()
    assert app.status.settings_button["text"] == "Settings"
    assert app.status.win.title() == "Göz Faresi (Eye Mouse)"
    app.wizard.finish(True)
    assert "start" in sup.calls
    app.on_state("calisiyor")
    assert app.status.state_var.get() == "Running"
    assert app.status.toggle_button["text"] == "Stop"
    app.shutdown()


def test_the_settings_window_in_english(root, data_dir):
    flow = _flow()
    flow._write_config(data_dir)
    i18n.set_language("en")
    s = flow.make_settings(root, flow.make_services(), flow.FakeSup())
    tabs = [s.notebook.tab(t, "text") for t in s.notebook.tabs()]
    assert tabs == ["Keys", "Cursor", "Clicking", "Magnifier", "Calibration",
                    "Camera", "Sound", "Advanced", "Not on this computer",
                    "Language (Dil)"]
    assert s.win.title() == "Göz Faresi — Settings"
    assert s.missing_text() == "Every feature works on this computer."
    assert s.keys.conflicts() == []
    from eye_gaze.actions_catalog import by_key
    assert by_key("alarm_key").label == "Emergency alarm"


def test_a_language_change_in_settings_is_saved_and_shown_at_once(
        root, data_dir):
    from eye_gaze import config
    flow = _flow()
    path = flow._write_config(data_dir)
    sup = flow.FakeSup()
    told = []
    from eye_gaze.ui import settings
    s = settings.SettingsWindow(
        root, flow.make_services(), sup, ask=flow.Dialogs().ask,
        tell=flow.Dialogs().tell, worker=flow.widgets.SyncWorker(),
        on_language=lambda code: told.append(code) or i18n.set_language(code))
    # The language the file already means (no line: Turkish) is no change.
    s.set_value("language", "tr")
    assert s.pending() == {}
    s.set_value("language", "en")
    assert s.pending() == {"language": "en"}
    assert s.save() is True
    assert told == ["en"] and sup.calls == ["restart"]
    cfg, _ = config.read_config(path, report=lambda m: None)
    assert cfg["language"] == "en"
    assert s.notebook.tab(s.notebook.tabs()[0], "text") == "Keys"
    assert s.message_var.get().startswith("Saved.")
    assert s.pending() == {}


# --- English on screen: the driver's overlays ----------------------------------

def test_the_click_menu_in_english():
    from eye_gaze import click_menu as cm
    i18n.set_language("en")
    assert [cm.item_label(k) for k in cm.ITEMS] == [
        "Left click", "Right click", "Double click", "Start drag",
        "Scroll up", "Scroll down", "Cancel"]
    assert cm.item_label("drag", dragging=True) == "End drag"
    assert cm.item_label("undo") == "Undo calibration"
    assert cm.CvWindow().title == "Click menu - look at an option and click"
    i18n.set_language("tr")
    assert cm.item_label("left") == "Sol tik"
    assert cm.CvWindow().title == cm._WIN


def test_the_phrase_board_and_its_scan_in_english():
    from eye_gaze import aac, scan
    board = aac.Board("b", "Food", [aac.Phrase("Water", "Water please")])
    i18n.set_language("en")
    labels = [p.label for p in aac.page_tiles(board, 0, 2, False)]
    assert labels == ["Water", "MORE >>", "<< BACK"]
    title = "Food  (1/2)   -   " + aac.gaze_hint()
    assert title.endswith("look + click = speak")
    assert scan.scan_title(title).endswith("click when it lights up")
    assert "look + click" not in scan.scan_title(title)


def test_the_driver_s_screens_in_english():
    import gaze_driver as gd
    from eye_gaze import alarm
    from eye_gaze.config import BACKUP_KEY
    i18n.set_language("en")
    lines = gd._persistent_warnings({BACKUP_KEY: True}, False)
    assert lines == [
        "OLD SETTINGS - config.yaml could not be read, recent changes are"
        " NOT in effect",
        "NO WAY TO CLICK - config.yaml: left_click_key or dwell_click"]
    assert alarm.alarm_text() == "Help me!"
    assert i18n.t_ascii("driver.cal.point", i=2, total=9) == \
        "Point 2/9 - look STEADILY"
    i18n.set_language("tr")
    assert alarm.alarm_text() == alarm.ALARM_TEXT
    assert gd._persistent_warnings({}, False) == [
        "TIKLAMA YONTEMI YOK - config.yaml: left_click_key ya da dwell_click"]


def test_settings_bring_the_windows_back_to_the_file_s_language(root,
                                                                data_dir):
    """A source-mode wizard switches the windows but writes nothing: the
    next save follows what config.yaml really says (no line: Turkish)."""
    flow = _flow()
    flow._write_config(data_dir)
    i18n.set_language("en")
    from eye_gaze.ui import settings
    told = []
    s = settings.SettingsWindow(
        root, flow.make_services(), flow.FakeSup(), ask=flow.Dialogs().ask,
        tell=flow.Dialogs().tell, worker=flow.widgets.SyncWorker(),
        on_language=lambda code: told.append(code) or i18n.set_language(code))
    assert s.pending() == {}
    assert s.save() is True
    assert told == ["tr"] and i18n.language() == "tr"
    assert s.notebook.tab(s.notebook.tabs()[0], "text") == "Tuşlar"
    assert s.message_var.get() == "Değişiklik yok."
