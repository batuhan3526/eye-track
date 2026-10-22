# gaze/eye_gaze/implicit.py
"""Implicit accuracy correction, learned from clicks that carry a real error.

Only two things this user does reveal where the gaze cursor was WRONG:

  * an F6 magnifier click: the user aims with the raw gaze cursor, then
    picks the exact point inside the magnified view. The confirmed point
    minus the pre-zoom cursor is the gaze error at that place on the screen.
  * an F7 drift measurement: the user fixates a known dot.

An ordinary click carries NO error signal - nobody knows where the user meant
to click - so nothing here ever learns from one.

The model is a small kernel regression of the RESIDUAL left after the
calibration and the F7 offset (drift):

    R(p) = sum(w_i * e_i) / (sum(w_i) + prior)       w_i = age decay * Gaussian

  * slow:    the prior means one observation moves the cursor a third of the
             way; repeated observations of the same error converge on it.
  * local:   a Gaussian kernel (sigma px) - the error near the taskbar says
             little about the error at the top of the screen.
  * bounded: |R| <= max_px, whatever is stored.
  * decays:  half-life in days; posture and lighting move on.
  * owned:   entries belong to one calibration (its signature); a new
             calibration starts from nothing. The file keeps the few most
             recent calibrations' entries apart, so a GPU flap between two
             calibrations does not wipe either.

F7 sets the GLOBAL offset (drift) on its own, for this session only. Entries
are stored relative to the CALIBRATION (drift 0) - what the total error was
at that point - and the field applies them minus the session's current drift.
So when F7 changes the drift by D, every entry's contribution moves by exactly
D: the global level the field had learned leaves it (the two never add up and
overshoot), the local DIFFERENCES between areas stay, and an F7 that finds
the centre accurate changes nothing. The file never holds a shift made
against a drift the next session will not have. The F7 point is kept as an
observation too (the measured drift at that point).

Per-frame apply() never raises and never moves the cursor on a fault: a bug
here must cost the correction, not the cursor.
"""
import json
import math
import os
import time

_VERSION = 1


def implicit_path(cal_path):
    """calibration.json -> calibration_implicit.json in the same folder."""
    return os.path.splitext(cal_path)[0] + "_implicit.json"


def _say(message):
    try:
        print(str(message).encode("ascii", "backslashreplace").decode("ascii"))
    except Exception:
        pass


def _finite(*vals):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals)


class ImplicitCorrector:
    def __init__(self, path=None, sig=None, enabled=True, max_px=100.0,
                 halflife_days=3.0, sigma=200.0, prior=2.0, outlier_px=200.0,
                 edge_frac=0.03, max_entries=200, clock=time.time, log=_say):
        self.enabled = bool(enabled)
        self._path = path
        self._sig = sig
        self._max = max(0.0, float(max_px))
        self._half = float(halflife_days)
        self._sigma = max(1.0, float(sigma))
        self._prior = max(1e-6, float(prior))
        self._outlier = float(outlier_px)
        self._edge = float(edge_frac)
        self._cap = max(1, int(max_entries))
        self._clock = clock
        self._log = log
        self._entries = []      # [x, y, ex, ey, t, src]; e relative to drift 0
        self._drift = (0.0, 0.0)    # the session's F7 offset (driver's drift)
        self._sets = {}         # other calibrations' entries, by signature
        if self.enabled:
            self._load()

    @classmethod
    def from_config(cls, cfg, cal_path, sig, sw, sh, **kw):
        """The driver's corrector. cal_path None = touch no file (calibration
        saving is off), the corrections then live for this session only.

        Never raises: this runs at startup, outside the per-frame guard. On a
        fault the corrector comes back switched off and the driver runs as it
        did before this feature existed."""
        def num(key, default):
            try:
                v = float(cfg.get(key, default))
                return v if math.isfinite(v) else default
            except (TypeError, ValueError):
                return default
        try:
            return cls(path=implicit_path(cal_path) if cal_path else None,
                       sig=sig,
                       enabled=bool(cfg.get("implicit_correction", True)),
                       max_px=num("implicit_max_px", 100.0),
                       halflife_days=num("implicit_halflife_days", 3.0),
                       sigma=0.15 * max(int(sw), int(sh), 1), **kw)
        except Exception as exc:
            _say("Ortuk duzeltme baslatilamadi (%s); KAPALI." % type(exc).__name__)
            return cls(enabled=False)

    # --- persistence -------------------------------------------------------

    # The file keeps entries PER CALIBRATION (by signature), the few most
    # recently used. deep_gaze: "auto" swaps between two calibrations as the
    # GPU comes and goes; one shared slot would wipe each one's corrections on
    # every swap. A new calibration has a new signature, so it starts empty.
    _KEEP_SETS = 4

    def _clean(self, raw):
        out = []
        for e in raw or []:
            try:
                x, y, ex, ey, t = (float(v) for v in e[:5])
            except Exception:
                continue
            if _finite(x, y, ex, ey, t):
                out.append([x, y, ex, ey, t, str(e[5]) if len(e) > 5 else "z"])
        return out[-self._cap:]

    def _load(self):
        self._sets = {}
        if not self._path or not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict) or d.get("v") != _VERSION \
                    or not isinstance(d.get("sets"), dict):
                raise ValueError("bicim")
            self._sets = {str(k): self._clean(v) for k, v in d["sets"].items()}
            self._entries = list(self._sets.get(str(self._sig), []))
            if self._entries:
                self._log("Ortuk duzeltme: %d olcum yuklendi." % len(self._entries))
            elif self._sets:
                self._log("Ortuk duzeltme: bu kalibrasyon icin kayitli olcum"
                          " yok; sifirdan baslaniyor.")
        except Exception:
            self._sets = {}
            self._entries = []
            self._log("Ortuk duzeltme dosyasi okunamadi; sifirdan baslaniyor.")

    def _save(self):
        if not self._path:
            return False
        tmp = self._path + ".tmp"
        try:
            sets = self._sets
            sets[str(self._sig)] = self._entries
            newest = sorted(sets, key=lambda k: max([e[4] for e in sets[k]] or [0]),
                            reverse=True)
            for k in newest[self._KEEP_SETS:]:
                if k != str(self._sig):
                    del sets[k]
            data = json.dumps({"v": _VERSION, "sets": sets}).encode("ascii")
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
            return True
        except Exception:
            try:
                os.remove(tmp)
            except Exception:
                pass
            return False

    def bind(self, sig):
        """The calibration in use changed (F9, refit): its corrections start
        from nothing. The old calibration's entries stay in the file - a
        recalibration that was NOT saved (graded KOTU) leaves tomorrow's
        corrections for the calibration that will be loaded intact."""
        if sig != self._sig:
            if self._entries:
                self._sets[str(self._sig)] = self._entries
            self._sig = sig
            self._entries = list(self._sets.get(str(sig), []))

    # --- the field -----------------------------------------------------------

    def _weights(self, x, y):
        now = self._clock()
        two_s2 = 2.0 * self._sigma * self._sigma
        out = []
        for e in self._entries:
            age = max(0.0, now - e[4]) / 86400.0
            decay = 0.5 ** (age / self._half) if self._half > 0 else 1.0
            d2 = (x - e[0]) ** 2 + (y - e[1]) ** 2
            out.append(decay * math.exp(-d2 / two_s2))
        return out

    def _raw_offset(self, x, y):
        if not self._entries:
            return 0.0, 0.0
        w = self._weights(x, y)
        sw = sum(w)
        dx, dy = self._drift
        ox = sum(wi * (e[2] - dx) for wi, e in zip(w, self._entries)) / (sw + self._prior)
        oy = sum(wi * (e[3] - dy) for wi, e in zip(w, self._entries)) / (sw + self._prior)
        return ox, oy

    def offset(self, x, y):
        """The correction (px) at screen point (x, y), capped."""
        if not self.enabled or not self._entries:
            return 0.0, 0.0
        ox, oy = self._raw_offset(float(x), float(y))
        mag = math.hypot(ox, oy)
        if mag > self._max:
            k = self._max / mag if mag > 0 else 0.0
            ox, oy = ox * k, oy * k
        return ox, oy

    def apply(self, sx, sy):
        """Per frame: the cursor target (already drift-corrected) plus the
        learned local correction. On ANY fault the input comes back as is."""
        try:
            if not self.enabled or not self._entries or not _finite(sx, sy):
                return sx, sy
            ox, oy = self.offset(sx, sy)
            if not _finite(ox, oy):
                return sx, sy
            return sx + ox, sy + oy
        except Exception:
            return sx, sy

    # --- learning --------------------------------------------------------------

    def _add(self, x, y, ex, ey, src):
        self._entries.append([float(x), float(y), float(ex), float(ey),
                              float(self._clock()), src])
        if len(self._entries) > self._cap:
            del self._entries[:len(self._entries) - self._cap]
        self._save()

    def observe_zoom(self, pre, target, region=None):
        """A confirmed magnifier click: `pre` is where the gaze cursor stood
        when F6 was pressed, `target` the physical point finally clicked,
        `region` the magnified (x0, y0, w, h). True when it was learned."""
        try:
            if not self.enabled:
                return False
            px, py = float(pre[0]), float(pre[1])
            tx, ty = float(target[0]), float(target[1])
            if not _finite(px, py, tx, ty):
                return False
            rx, ry = tx - px, ty - py
            if math.hypot(rx, ry) > self._outlier:
                self._log("Ortuk duzeltme: hata cok buyuk (%.0f px), ogrenilmedi."
                          % math.hypot(rx, ry))
                return False
            if region is not None:
                x0, y0, w, h = (float(v) for v in region)
                bx, by = self._edge * w, self._edge * h
                if not (x0 + bx < tx < x0 + w - 1 - bx
                        and y0 + by < ty < y0 + h - 1 - by):
                    # The cursor stops at the screen edge inside the magnified
                    # view: a target on the border only says "further".
                    return False
            # The residual is relative to what was APPLIED at that point,
            # drift included: stored against the calibration alone.
            ox, oy = self.offset(px, py)
            dx, dy = self._drift
            self._add(px, py, ox + rx + dx, oy + ry + dy, "z")
            self._log("Ortuk duzeltme: buyutec tikindan hata (%+.0f, %+.0f) px"
                      " ogrenildi." % (rx, ry))
            return True
        except Exception:
            return False

    def set_drift(self, drift):
        """The driver's drift (px) changed - zeroed by a recalibration. The
        field is applied relative to it. Never raises; a bad value is 0."""
        try:
            dx, dy = float(drift[0]), float(drift[1])
            self._drift = (dx, dy) if _finite(dx, dy) else (0.0, 0.0)
        except Exception:
            self._drift = (0.0, 0.0)

    def observe_drift(self, centre, drift):
        """F7 just set the global offset to `drift` from a fixation at
        `centre`. Every entry's contribution moves by exactly the CHANGE in
        drift (see the module docstring); nothing stored is rewritten."""
        try:
            self.set_drift(drift)
            if not self.enabled:
                return False
            cx, cy = float(centre[0]), float(centre[1])
            dx, dy = self._drift
            self._add(cx, cy, dx, dy, "d")     # zero residual at the F7 point
            return True
        except Exception:
            return False
