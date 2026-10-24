# gaze/eye_gaze/ui/widgets.py
"""Look and shared pieces of the launcher's windows.

Whoever reads these windows may be the ALS user (from the chair, through
the magnifier) or a caregiver with no computer background: large type
(16 pt at least), black on white, a thick orange focus ring, one decision
per screen, every control reachable by Tab and pressed by Enter or Space as
well as by the mouse.

Threads: Tk may only be touched from its own thread. Anything slow (camera
probe, benchmark, download, key capture) runs through a Worker, which hands
results back to the Tk thread by polling a queue with after(). SyncWorker
does the same work inline, for the tests and the screenshots.

HIDDEN (tests): every window this package makes is withdrawn at once, so a
test run never puts a window in front of whoever is using the computer.
"""
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from eye_gaze import i18n
from eye_gaze.actions_catalog import ACTIONS, by_key
from eye_gaze.i18n import t

HIDDEN = False

FONT_SIZE = 16
TITLE_SIZE = 24
BIG_SIZE = 20

BG = "#FFFFFF"
FG = "#000000"
PANEL = "#EDEDED"
ACCENT = "#003C8F"          # dark blue: white text on it is AAA contrast
ACCENT_FG = "#FFFFFF"
WARN = "#A00000"
OK = "#005A00"
FOCUS = "#FF8C00"
DISABLED = "#5A5A5A"

WRAP = 860                  # px a paragraph wraps at


def _family(root):
    try:
        fams = set(tkfont.families(root))
    except tk.TclError:
        fams = set()
    for f in ("Segoe UI", "DejaVu Sans", "Noto Sans", "Liberation Sans"):
        if f in fams:
            return f
    return None


def apply_theme(root):
    """Large fonts and high-contrast colours for every widget of `root`'s
    interpreter. Idempotent."""
    family = _family(root)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                 "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont",
                 "TkIconFont", "TkTooltipFont"):
        try:
            f = tkfont.nametofont(name, root=root)
        except tk.TclError:
            continue
        opts = {"size": FONT_SIZE}
        if family:
            opts["family"] = family
        f.configure(**opts)
    try:
        tkfont.nametofont("TkFixedFont", root=root).configure(size=FONT_SIZE)
    except tk.TclError:
        pass
    root.option_add("*Background", BG)
    root.option_add("*Foreground", FG)
    # The focus ring is set per control (button, radio, check, scale):
    # a global thickness would frame every label and panel in grey.
    root.option_add("*highlightColor", FOCUS)
    root.option_add("*highlightBackground", BG)
    root.option_add("*activeBackground", PANEL)
    root.option_add("*disabledForeground", DISABLED)
    try:
        root.configure(bg=BG)
    except tk.TclError:
        pass
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", font="TkDefaultFont", background=BG, foreground=FG)
    style.configure("TNotebook", background=BG)
    # Ten settings tabs side by side must fit a 1280 px wide screen (a
    # 1080p laptop at 150 %): less side padding, the same tall tabs.
    style.configure("TNotebook.Tab", padding=(8, 10), font="TkDefaultFont",
                    background=PANEL, foreground=FG)
    style.map("TNotebook.Tab", background=[("selected", ACCENT)],
              foreground=[("selected", ACCENT_FG)])
    style.configure("Horizontal.TProgressbar", thickness=28,
                    background=ACCENT, troughcolor=PANEL)
    # Enter presses the focused control too (Tk only binds Space).
    for cls in ("Button", "Radiobutton", "Checkbutton"):
        root.bind_class(cls, "<Return>", lambda e: e.widget.invoke())
        root.bind_class(cls, "<KP_Enter>", lambda e: e.widget.invoke())
        if FOCUS_FILL:
            # Replaces (no add="+"): Tk's own class bindings have no
            # FocusIn/FocusOut, and a second apply_theme must not stack a
            # second handler.
            root.bind_class(cls, "<FocusIn>",
                            lambda e: focus_fill(e.widget, True))
            root.bind_class(cls, "<FocusOut>",
                            lambda e: focus_fill(e.widget, False))
    return root


# Windows Tk draws a button's highlight ring as a thin dotted line whatever
# highlightthickness says (Task 16 frozen smoke: the thick orange ring is seen
# on Linux only). There the focused control is filled orange instead, so the
# eye finds it from the chair. Linux keeps the ring it already shows.
FOCUS_FILL = sys.platform == "win32"
_FILL = ("bg", "fg", "activebackground", "activeforeground", "selectcolor")


def focus_fill(widget, on):
    """Fill `widget` with the focus colour (on) or give it back the colours
    it had before (off). The colours are saved once per focus, so a repeated
    FocusIn never saves the orange as the widget's own colour."""
    try:
        if on:
            if getattr(widget, "_gozfaresi_colours", None) is None:
                keys = set(widget.keys())
                widget._gozfaresi_colours = {
                    k: widget.cget(k) for k in _FILL if k in keys}
            widget.configure(**{
                k: (FG if k in ("fg", "activeforeground") else FOCUS)
                for k in widget._gozfaresi_colours})
        else:
            saved = getattr(widget, "_gozfaresi_colours", None)
            if saved is not None:
                widget._gozfaresi_colours = None
                widget.configure(**saved)
    except (tk.TclError, AttributeError):
        pass                            # widget gone mid-focus change


def fonts(root):
    """{"title", "big", "body", "bold"} Font objects (cached per root)."""
    cache = getattr(root, "_gozfaresi_fonts", None)
    if cache is None:
        base = tkfont.nametofont("TkDefaultFont", root=root).actual()
        fam = base.get("family")
        cache = {
            "title": tkfont.Font(root=root, family=fam, size=TITLE_SIZE,
                                 weight="bold"),
            "big": tkfont.Font(root=root, family=fam, size=BIG_SIZE,
                               weight="bold"),
            "body": tkfont.Font(root=root, family=fam, size=FONT_SIZE),
            "bold": tkfont.Font(root=root, family=fam, size=FONT_SIZE,
                                weight="bold"),
        }
        root._gozfaresi_fonts = cache
    return cache


def toplevel(parent, title):
    win = tk.Toplevel(parent, bg=BG)
    win.title(title)
    if HIDDEN:
        win.withdraw()
    return win


def show(win):
    """Map `win` and bring it to the front - unless HIDDEN."""
    if HIDDEN:
        return
    try:
        # Not focus_force(): on Windows it pins Tk's keyboard focus on the
        # toplevel ITSELF, for good - the control the window had chosen
        # (the wizard's "İleri") never got it back, not even from a
        # focus_set() right after it or on <FocusIn>/<Activate> (measured,
        # Task 16 frozen smoke), and Enter pressed nothing until someone
        # found Tab. `wm deiconify` of a window already shown does the same
        # on Windows (measured), so only a hidden one is deiconified. A
        # topmost flick brings the window over the others and leaves the
        # focus where the window put it.
        if win.state() != "normal":
            win.deiconify()
        win.lift()
        win.attributes("-topmost", True)
        win.attributes("-topmost", False)
    except tk.TclError:
        pass


def button(parent, text, command, primary=False, width=None, **kw):
    opts = dict(text=text, command=command, padx=18, pady=8,
                relief="raised", bd=2, cursor="hand2",
                highlightthickness=3, highlightcolor=FOCUS,
                highlightbackground=BG,
                font=fonts(parent.winfo_toplevel())["bold"])
    if primary:
        opts.update(bg=ACCENT, fg=ACCENT_FG, activebackground="#002A66",
                    activeforeground=ACCENT_FG)
    else:
        opts.update(bg=PANEL, fg=FG, activebackground="#D0D0D0")
    if width:
        opts["width"] = width
    opts.update(kw)
    return tk.Button(parent, **opts)


def text(parent, kind="body", textvariable=None, value=None, wrap=WRAP,
         **kw):
    """A label: kind = "title" | "big" | "body" | "bold" | "warn" | "ok"."""
    f = fonts(parent.winfo_toplevel())
    color = {"warn": WARN, "ok": OK}.get(kind, FG)
    font = f.get(kind, f["bold"] if kind in ("warn", "ok") else f["body"])
    opts = dict(bg=kw.pop("bg", BG), fg=color, font=font, justify="left",
                anchor="w", wraplength=wrap)
    if textvariable is not None:
        opts["textvariable"] = textvariable
    else:
        opts["text"] = value or ""
    opts.update(kw)
    return tk.Label(parent, **opts)


SELECTED = "#BFD7FF"        # a chosen option: light blue, pressed in


def _choice_opts(parent):
    # Drawn as large push-buttons (indicatoron=0): the round/square
    # indicator Tk draws is a few pixels wide - too small to see or hit.
    return dict(anchor="w", justify="left", wraplength=WRAP, bg=PANEL,
                fg=FG, activebackground="#D8D8D8", selectcolor=SELECTED,
                indicatoron=0, relief="raised", offrelief="raised", bd=2,
                highlightthickness=3, highlightcolor=FOCUS,
                highlightbackground=BG, padx=14, pady=8, cursor="hand2",
                font=fonts(parent.winfo_toplevel())["body"])


def _marked(widget, variable, label, is_on, on_mark, off_mark):
    """Keep a large mark in front of the label (the colour alone is a weak
    cue for a tired eye or a grey-scale screen)."""
    def update(*_):
        try:
            widget.configure(text="%s  %s" % (
                on_mark if is_on() else off_mark, label))
        except tk.TclError:
            pass                        # widget gone; its trace is harmless
    variable.trace_add("write", update)
    update()
    return widget


def radio(parent, label, variable, value, command=None):
    w = tk.Radiobutton(parent, text=label, variable=variable, value=value,
                       command=command, **_choice_opts(parent))
    return _marked(w, variable, label, lambda: variable.get() == value,
                   "●", "○")


def check(parent, label, variable, command=None):
    w = tk.Checkbutton(parent, text=label, variable=variable,
                       command=command, **_choice_opts(parent))
    return _marked(w, variable, label, lambda: bool(variable.get()),
                   "[✓]", "[  ]")


class ScrollFrame(tk.Frame):
    """A frame whose content (.inner) scrolls; the wheel scrolls it while
    the pointer is over it, and Tab-focus scrolls the focused row into
    view (a keyboard-only caregiver must reach the last row too)."""

    def __init__(self, parent, height=420, **kw):
        super().__init__(parent, bg=BG, **kw)
        self.canvas = tk.Canvas(self, bg=BG, height=height,
                                highlightthickness=0)
        bar = tk.Scrollbar(self, orient="vertical",
                           command=self.canvas.yview, width=22)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self._win = self.canvas.create_window((0, 0), window=self.inner,
                                              anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._win, width=e.width))
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", self._wheel_on)
            w.bind("<Leave>", self._wheel_off)
        self.inner.bind_all("<FocusIn>", self._follow_focus, add="+")

    def _wheel_on(self, _e):
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(
            -2, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(
            2, "units"))

    def _wheel_off(self, _e):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(seq)

    def _wheel(self, e):
        self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def _follow_focus(self, e):
        w = e.widget
        try:
            if not str(w).startswith(str(self.inner)):
                return
            y = w.winfo_rooty() - self.inner.winfo_rooty()
            total = max(1, self.inner.winfo_height())
            top, bottom = self.canvas.yview()
            if not top * total <= y <= bottom * total - w.winfo_height():
                self.canvas.yview_moveto(max(0.0, (y - 40) / total))
        except (tk.TclError, AttributeError):
            pass


# --- threads -----------------------------------------------------------------

class Worker:
    """run(fn, on_done, on_error) in a thread; on_done(result) /
    on_error(exc) and post(fn) run on the Tk thread."""

    POLL_MS = 60

    def __init__(self, widget):
        self._widget = widget
        self._q = queue.Queue()
        self._pump()

    def _pump(self):
        try:
            while True:
                fn = self._q.get_nowait()
                try:
                    fn()
                except Exception as exc:      # a UI callback must not kill
                    print("[arayuz] geri cagirma hatasi: %s" % ascii(exc))
        except queue.Empty:
            pass
        try:
            self._widget.after(self.POLL_MS, self._pump)
        except tk.TclError:
            pass                              # window gone: stop polling

    def post(self, fn):
        self._q.put(fn)

    def run(self, fn, on_done=None, on_error=None):
        def body():
            try:
                result = fn()
            except Exception as exc:
                if on_error is not None:
                    self.post(lambda: on_error(exc))
                else:
                    print("[arayuz] arka plan hatasi: %s" % ascii(exc))
                return
            if on_done is not None:
                self.post(lambda: on_done(result))
        t = threading.Thread(target=body, daemon=True, name="arayuz-is")
        t.start()
        return t


class SyncWorker:
    """Worker that does everything at once, on the calling thread. A post()
    from another thread (e.g. the camera preview's) is kept in .later and
    never run there: Tk must not be touched off its thread."""

    def __init__(self):
        self._thread = threading.current_thread()
        self.later = []

    def post(self, fn):
        if threading.current_thread() is self._thread:
            fn()
        else:
            self.later.append(fn)

    def run(self, fn, on_done=None, on_error=None):
        try:
            result = fn()
        except Exception as exc:
            if on_error is None:
                raise
            on_error(exc)
            return None
        if on_done is not None:
            on_done(result)
        return None


# --- dialogs -----------------------------------------------------------------

def dialogs(parent):
    """(ask, tell) using Tk message boxes over `parent`."""
    from tkinter import messagebox

    def ask(title, message):
        return bool(messagebox.askyesno(title, message, parent=parent))

    def tell(title, message):
        messagebox.showinfo(title, message, parent=parent)
    return ask, tell


# --- names shown to the user -------------------------------------------------

_KEY_TEXT = {"enter": "Enter", "esc": "Esc",
             "tab": "Tab", "scroll lock": "Scroll Lock", "pause": "Pause",
             "insert": "Insert", "delete": "Delete", "home": "Home",
             "end": "End", "page up": "Page Up", "page down": "Page Down",
             "caps lock": "Caps Lock",
             "num lock": "Num Lock", "print screen": "Print Screen"}
# Key names that read differently per language (widgets.key.<name>).
_KEY_WORDS = ("space", "backspace", "up", "down", "left", "right", "menu")


def key_text(name, kind="key"):
    n = str(name or "").strip()
    if not n:
        return t("widgets.key.unassigned")
    if kind == "pad":
        return n.upper()
    low = n.lower()
    if low in _KEY_WORDS:
        return t("widgets.key." + low)
    if low in _KEY_TEXT:
        return _KEY_TEXT[low]
    if len(low) <= 3 and low.startswith("f") and low[1:].isdigit():
        return low.upper()
    return n.upper() if len(n) == 1 else n


def how_text(action, values):
    """How the key is pressed for this job, with this config's timings."""
    if action.how == "cift":
        try:
            sec = float(values.get("hotkey_double_interval", 1.0))
        except (TypeError, ValueError):
            sec = 1.0
        return t("widgets.how.double", sec=_num(sec))
    if action.how == "basili":
        try:
            sec = float(values.get("alarm_hold_seconds", 2.0))
        except (TypeError, ValueError):
            sec = 2.0
        return t("widgets.how.hold", sec=_num(sec))
    return t("widgets.how.once")


def _num(x):
    return i18n.decimal(x)


def cap_name(key):
    """A capability's name for the windows (widgets.cap.<key>)."""
    from eye_gaze.platform import CAPABILITY_KEYS
    return t("widgets.cap." + key) if key in CAPABILITY_KEYS else key


CRITICAL_CAPS = ("cursor", "global_keys", "audio")


def missing_lines(caps, only=None):
    """["Ad: neden", ...] for every False capability (or only those in
    `only`)."""
    out = []
    for key, (ok, why) in caps.items():
        if ok or (only is not None and key not in only):
            continue
        out.append("%s: %s" % (cap_name(key),
                               why or t("widgets.cap.no_reason")))
    return out


# --- clicking ----------------------------------------------------------------

_METHODS = ("switch", "dwell", "blink")


def click_methods():
    """((value, label, note), ...) in the current language."""
    return tuple((m, t("widgets.click." + m), t("widgets.click.%s_note" % m))
                 for m in _METHODS)


# Settings that decide whether the user can click at all.
CLICK_ROUTE_KEYS = ("left_click_key", "gamepad_left", "gamepad_enabled",
                    "dwell_click", "blink_click")


def method_of(values):
    if values.get("dwell_click") is True:
        return "dwell"
    if values.get("blink_click") is True:
        return "blink"
    return "switch"


def method_values(method):
    return {"dwell_click": method == "dwell", "blink_click": method == "blink"}


def click_route_problem(values, pad_present):
    """None while one way to click works NOW; else the Turkish warning.

    config_writer refuses only a config with no click binding at all. Here
    a gamepad binding counts only if a gamepad is really there: "A" with no
    pad plugged in leaves this user unable to press anything."""
    if str(values.get("left_click_key", "") or "").strip():
        return None
    if values.get("dwell_click") is True or values.get("blink_click") is True:
        return None
    pad = str(values.get("gamepad_left", "") or "").strip()
    pad_on = values.get("gamepad_enabled", True) is not False
    if pad and pad_on and pad_present:
        return None
    if pad and pad_on:
        why = t("widgets.route.pad_absent", pad=pad.upper())
    else:
        why = t("widgets.route.pad_unset")
    return t("widgets.route.problem", why=why)


# --- key assignment table ----------------------------------------------------

class KeyTable(tk.Frame):
    """Every job of the catalog with its key, "Değiştir" and "Kaldır", and
    the conflicts keymap.find_conflicts sees - shown before anything is
    saved. `values` is the whole config dict (shared with the caller and
    changed in place: find_conflicts needs every binding).

    before_capture() / after_capture() stop and restart the driver around
    the capture (it hooks and swallows its own keys)."""

    def __init__(self, parent, services, values, ask, tell, worker,
                 before_capture=None, after_capture=None, on_change=None,
                 height=420):
        super().__init__(parent, bg=BG)
        self.services = services
        self.values = values
        self.original = {a.key: values.get(a.key, a.default) for a in ACTIONS}
        self.ask, self.tell, self.worker = ask, tell, worker
        self.before_capture = before_capture or (lambda: None)
        self.after_capture = after_capture or (lambda: None)
        self.on_change = on_change or (lambda: None)
        self.warning_var = tk.StringVar(self, "")
        self._value_vars = {}
        self._dialog = None
        self.capturing = None
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", pady=(0, 6))
        button(top, t("widgets.keytable.reset"),
               self.reset_defaults).pack(side="right")
        text(top, "body", value=t("widgets.keytable.intro"),
             wrap=WRAP - 240).pack(side="left", fill="x", expand=True)
        scroll = ScrollFrame(self, height=height)
        scroll.pack(fill="both", expand=True)
        group = None
        for a in ACTIONS:
            if a.group != group:
                group = a.group
                text(scroll.inner, "big", value=group).pack(
                    fill="x", pady=(12, 2))
            row = tk.Frame(scroll.inner, bg=BG)
            row.pack(fill="x", pady=2)
            left = tk.Frame(row, bg=BG)
            left.pack(side="left", fill="x", expand=True)
            text(left, "bold", value=a.label, wrap=420).pack(anchor="w")
            text(left, "body", value=how_text(a, values), wrap=420,
                 fg=DISABLED).pack(anchor="w")
            var = tk.StringVar(self, key_text(values.get(a.key, ""), a.kind))
            self._value_vars[a.key] = var
            tk.Label(row, textvariable=var, width=16, anchor="w", bg=PANEL,
                     fg=FG, font=fonts(self.winfo_toplevel())["bold"],
                     padx=8, pady=4).pack(side="left", padx=8)
            button(row, t("widgets.keytable.change"),
                   lambda k=a.key: self.change(k)).pack(
                side="left", padx=4)
            button(row, t("widgets.keytable.remove"),
                   lambda k=a.key: self.set_value(k, "")).pack(
                side="left", padx=4)
        text(self, "warn", textvariable=self.warning_var).pack(
            fill="x", pady=(8, 0))
        self.refresh()

    # --- values --------------------------------------------------------------

    @property
    def changed(self):
        return {k: self.values.get(k, "") for k, v in self.original.items()
                if self.values.get(k, "") != v}

    def set_value(self, key, value):
        a = by_key(key)
        self.values[key] = value
        if key in self._value_vars and a is not None:
            self._value_vars[key].set(key_text(value, a.kind))
        self.refresh()
        self.on_change()

    def reset_defaults(self):
        for a in ACTIONS:
            self.set_value(a.key, a.default)

    def conflicts(self):
        from eye_gaze.keymap import find_conflicts
        try:
            return [msg for _, _, msg in find_conflicts(self.values)]
        except Exception as exc:
            # A screen that cannot check must not say "no conflicts".
            return [t("widgets.keytable.check_failed", error=exc)]

    def refresh(self):
        self.warning_var.set("\n".join(self.conflicts()))

    # --- capture -------------------------------------------------------------

    def change(self, key):
        a = by_key(key)
        if a is None or self.capturing is not None:
            return
        self.capturing = key
        self.before_capture()
        self._open_dialog(a)
        fn = (self.services.capture_pad if a.kind == "pad"
              else self.services.capture_key)
        self.worker.run(lambda: fn(10), lambda name: self._captured(a, name),
                        lambda exc: self._capture_failed(a, exc))

    def _open_dialog(self, a):
        win = toplevel(self.winfo_toplevel(),
                       t("widgets.keytable.capture_title"))
        frame = tk.Frame(win, bg=BG, padx=30, pady=24, takefocus=1)
        frame.pack(fill="both", expand=True)
        text(frame, "title", value=t("widgets.keytable.job",
                                     label=a.label)).pack(anchor="w")
        text(frame, "big", value=t("widgets.keytable.press_pad"
                                   if a.kind == "pad" else
                                   "widgets.keytable.press_key")).pack(
            anchor="w", pady=12)
        text(frame, "body", value=t("widgets.keytable.wait_note")).pack(
            anchor="w")
        self._dialog = win
        show(win)
        if not HIDDEN:
            try:
                # The pressed key must not press a button of ours: focus
                # sits on a frame that does nothing with keys.
                win.grab_set()
                frame.focus_set()
            except tk.TclError:
                pass

    def _close_dialog(self):
        win, self._dialog = self._dialog, None
        if win is not None:
            try:
                win.grab_release()
                win.destroy()
            except tk.TclError:
                pass

    def _done(self):
        self._close_dialog()
        self.capturing = None
        self.after_capture()

    def _captured(self, a, name):
        self._done()
        if not name:
            self.tell(t("widgets.keytable.capture_title"),
                      t("widgets.keytable.not_pressed", label=a.label))
            return
        self.set_value(a.key, name)

    def _capture_failed(self, a, exc):
        self._done()
        self.tell(t("widgets.keytable.capture_title"),
                  t("widgets.keytable.capture_failed", label=a.label,
                    error=exc))
