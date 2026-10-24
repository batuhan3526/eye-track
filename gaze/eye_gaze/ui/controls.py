# gaze/eye_gaze/ui/controls.py
"""The settings window's number controls.

Slider       one number on a large slider. The value line above it is drawn
             here, in the language's own way ("1,5 sn", "kapalı"), not by
             Tk's small number. The slider reaches the file's value even when
             it lies outside the usual range (the range is widened), so what
             is shown is what is in effect. Tk rounds a value to the slider's
             step; that rounding is not a change: until the person moves the
             slider, `value` stays exactly what was given.
AutoNumber   "Otomatik" (or "follow the level") on top of a Slider: the
             slider is greyed while the box is ticked and shows the value in
             effect then.

Keyboard: Tab reaches every slider and box (thick orange ring), the arrow
keys move a slider by one step, Space / Enter tick a box.
"""
import tkinter as tk

from eye_gaze.ui import widgets
from eye_gaze.ui.widgets import BG

LENGTH = 560        # px: the slider's track
WIDTH = 28          # px: the track's thickness
HANDLE = 44         # px: the handle's length - large enough to hit


def _number(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


class Slider(tk.Frame):
    """title + live value line + slider. on_change(value) after each move
    by the person (never for a set() or Tk's own redraw)."""

    def __init__(self, parent, title, lo, hi, step, value, on_change=None,
                 kind=float, fmt=None):
        super().__init__(parent, bg=BG)
        self.title = title
        self.step = float(step)
        self.kind = kind
        self.fmt = fmt or widgets._num
        self.on_change = on_change
        self.lo, self.hi = float(lo), float(hi)
        self.label_var = tk.StringVar(self, "")
        widgets.text(self, "bold", textvariable=self.label_var).pack(
            anchor="w")
        self.var = tk.DoubleVar(self, 0.0)
        self.scale = tk.Scale(
            self, variable=self.var, from_=self.lo, to=self.hi,
            resolution=self.step, orient="horizontal", showvalue=0,
            length=LENGTH, width=WIDTH, sliderlength=HANDLE, bd=2, bg=BG,
            fg=widgets.FG, troughcolor=widgets.PANEL,
            activebackground=widgets.ACCENT, highlightthickness=3,
            highlightcolor=widgets.FOCUS, highlightbackground=BG,
            command=self._moved)
        self.scale.pack(anchor="w", pady=(2, 0))
        self.set(value)

    # --- the value ------------------------------------------------------------

    def set(self, value):
        """Show `value` (not a change by the person)."""
        f = _number(value)
        if f is None:
            f = self.lo
            value = self.kind(f)
        if f < self.lo or f > self.hi:
            self.lo, self.hi = min(self.lo, f), max(self.hi, f)
            self.scale.configure(from_=self.lo, to=self.hi)
        self._given = self.value = value
        self.var.set(f)
        self._shown = self.var.get()        # as Tk rounded it to the step
        self.relabel()

    def _moved(self, _=None):
        v = self.var.get()
        if abs(v - self._shown) < self.step / 2.0:
            new = self._given               # back where set() put it
        elif self.kind is int:
            new = int(round(v))
        else:
            new = round(float(v), 4)        # 0.30000000000000004 -> 0.3
        if new == self.value:
            self.relabel()
            return
        self.value = new
        self.relabel()
        if self.on_change is not None:
            self.on_change(new)

    def move_to(self, value):
        """What the person does with the mouse or the arrow keys."""
        self.var.set(value)
        self._moved()

    def relabel(self):
        try:
            self.label_var.set("%s: %s" % (self.title, self.fmt(self.value)))
        except (tk.TclError, TypeError, ValueError):
            pass

    def text(self):
        return self.label_var.get()

    def enable(self, on):
        try:
            self.scale.configure(state="normal" if on else "disabled")
        except tk.TclError:
            pass


class AutoNumber(tk.Frame):
    """A box ("Otomatik", "follow the level") and a Slider for the number.
    on_change(auto, number) after each change by the person; show(number)
    puts the value in effect on the greyed slider."""

    def __init__(self, parent, title, auto_label, lo, hi, step, auto,
                 number, on_change, kind=float, fmt=None):
        super().__init__(parent, bg=BG)
        self.on_change = on_change
        self.auto = bool(auto)
        self._manual = None if self.auto else number
        self.slider = Slider(self, title, lo, hi, step, number,
                             on_change=self._number, kind=kind, fmt=fmt)
        self.slider.pack(anchor="w", fill="x")
        self.check_var = tk.BooleanVar(self, self.auto)
        self.check = widgets.check(self, auto_label, self.check_var,
                                   command=self._toggled)
        self.check.pack(anchor="w", pady=(6, 0))
        self.note_var = tk.StringVar(self, "")
        widgets.text(self, "warn", textvariable=self.note_var).pack(
            anchor="w")
        self.slider.enable(not self.auto)

    def _toggled(self):
        self.auto = bool(self.check_var.get())
        if self.auto:
            self._manual = self.slider.value
        elif self._manual is not None:
            # Unticked again: the number it held before, not the greyed one.
            self.slider.set(self._manual)
        self.slider.enable(not self.auto)
        self.on_change(self.auto, self.slider.value)

    def _number(self, value):
        if not self.auto:
            self.on_change(False, value)

    def show(self, number):
        self.slider.set(number)
