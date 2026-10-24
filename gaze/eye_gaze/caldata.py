# gaze/eye_gaze/caldata.py
"""Raw calibration samples, kept beside calibration.json.

calibration.json holds only the fitted coefficients. Everything the fit was
made FROM - the feature vectors, the screen targets, their timestamps, and for
pursuit the ball path and the learned eye lag - used to be thrown away the
moment the fit was done. So a MAPPING option (calibration_poly2, reach_margin,
pursuit_latency) could only take effect through a new full calibration, which
is physically taxing for this user; in practice the option silently did
nothing, because a loaded calibration keeps the options it was fitted with.

Here the samples are written next to the calibration whenever it is saved
(calibration_samples.<feature stamp>.npz, plus one previous generation), and
reconcile() refits from them at startup when an option changed - graded on
HELD-OUT points, and only installed when it is not clearly worse.

The feature vector itself cannot change this way: samples are keyed by the
feature stamp and their feature count, exactly like the calibration backups.

A calibration fitted before the per-axis model selection (eye_gaze.modelsel)
existed is refitted from its samples once at startup the same way, and only
installed when it is not clearly worse - both measured the same way on the
same samples.

Nothing here raises into the driver. A failure to save samples costs only the
convenience they offer; it must never cost the calibration.
"""
import hashlib
import io
import json
import math
import os
import time
from collections import namedtuple

import numpy as np

from . import modelsel
from .mapping import fit_with_rejection, holdout_error
from .pursuit import PursuitPath, best_latency_fit, pursuit_keep, pursuit_pairs

_VERSION = 1
_CV_GOOD, _CV_BAD = 100.0, 200.0     # same grades as gaze_driver._cal_label
_WORSE = 1.25                        # same tolerance as gaze_driver._should_save

# report: what modelsel measured (per-axis held-out error, coverage slope,
# chosen inputs; see modelsel.select), or None for a fit without selection.
RefitResult = namedtuple("RefitResult", "mapper err err_cv latency report",
                         defaults=(None,))


def _say(message):
    try:
        print(str(message).encode("ascii", "backslashreplace").decode("ascii"))
    except Exception:
        pass


class CalSamples:
    """What one calibration was fitted from.

    kind "dots":    gaze (N, n), screen (N, 2), t = capture time per point.
    kind "pursuit": gaze (N, n), t = seconds since the ball started moving
                    (restarting each pass); the screen target is the ball
                    position at t - latency, so the PATH is kept instead of the
                    targets and the latency can be relearned.
    group: pass index per sample, or None (files from before it was kept,
    and single-pass dots).
    """

    def __init__(self, kind, gaze, t, screen, meta, group=None):
        self.kind = kind
        self.gaze = np.asarray(gaze, dtype=float)
        self.t = np.asarray(t, dtype=float)
        self.screen = (np.zeros((0, 2)) if screen is None
                       else np.asarray(screen, dtype=float).reshape(-1, 2))
        self.meta = dict(meta)
        self.group = _group(group, len(self.gaze))

    # The two builders run INSIDE the calibration routines, right after the
    # fit the user just paid for. They return None instead of raising: a
    # fault here may cost the samples, never the calibration.

    @classmethod
    def dots(cls, gaze_pts, screen_pts, stamps, opts, groups=None):
        try:
            n = len(gaze_pts)
            t = list(stamps) if stamps is not None and len(stamps) == n \
                else list(range(n))
            return cls("dots", [tuple(g) for g in gaze_pts], t,
                       [tuple(s) for s in screen_pts],
                       {"kind": "dots", "opts": dict(opts)}, group=groups)
        except Exception:
            return None

    @classmethod
    def pursuit(cls, samples, path, latency_used, opts, groups=None):
        try:
            pts = [list(map(float, p)) for p in getattr(path, "_pts")]
            return cls("pursuit", [tuple(g) for g, _ in samples],
                       [float(t) for _, t in samples], None,
                       {"kind": "pursuit", "opts": dict(opts),
                        "latency_used": float(latency_used),
                        "speed": float(path.speed), "path_pts": pts},
                       group=groups)
        except Exception:
            return None

    @property
    def n_inputs(self):
        return int(self.gaze.shape[1]) if self.gaze.ndim == 2 else 0

    def path(self):
        """The pursuit path exactly as it was during the calibration."""
        return PursuitPath(1, 1, self.meta["speed"],
                           waypoints=[tuple(p) for p in self.meta["path_pts"]])

    def raw(self):
        """Pursuit samples as calibrate_pursuit collected them."""
        return [(tuple(g), float(t)) for g, t in zip(self.gaze.tolist(), self.t)]

    def pairs(self, latency=None):
        """(gaze_points, screen_points) training lists."""
        if self.kind == "pursuit":
            lat = self.meta["latency_used"] if latency is None else float(latency)
            return pursuit_pairs(self.raw(), self.path(), lat)
        return ([tuple(g) for g in self.gaze.tolist()],
                [tuple(s) for s in self.screen.tolist()])

    def groups(self):
        """Pass index per sample: as recorded; for a pursuit file from before
        it was recorded, read from the t restarts; else None (dots: one
        pass, or passes not recorded)."""
        if self.group is not None:
            return self.group
        if self.kind == "pursuit":
            return modelsel.groups_from_t(self.t)
        return None

    def grouped_pairs(self, latency=None):
        """pairs() plus the pass index of each pair (or None)."""
        grp = self.groups()
        if self.kind == "pursuit":
            lat = self.meta["latency_used"] if latency is None else float(latency)
            raw = self.raw()
            idx, s_pts = pursuit_keep(raw, self.path(), lat)
            return ([raw[i][0] for i in idx], s_pts,
                    None if grp is None else [int(grp[i]) for i in idx])
        g, s_pts = self.pairs()
        return g, s_pts, None if grp is None else [int(v) for v in grp]

    def with_opts(self, opts, **meta):
        m = dict(self.meta)
        m["opts"] = dict(opts)
        m.update(meta)
        return CalSamples(self.kind, self.gaze, self.t,
                          self.screen if self.kind == "dots" else None, m,
                          group=self.group)


def _group(group, n):
    """A usable pass-index array of length n, or None."""
    if group is None:
        return None
    try:
        g = np.asarray(group).astype(int).reshape(-1)
        return g if len(g) == n else None
    except Exception:
        return None


# --- files ---------------------------------------------------------------------


def samples_path(cal_path, key):
    """calibration.json -> calibration_samples.<key>.npz in the same folder."""
    return "%s_samples.%s.npz" % (os.path.splitext(cal_path)[0], key)


def previous_path(path):
    return path[:-4] + ".onceki.npz" if path.endswith(".npz") else path + ".onceki"


def mapper_sig(mapper):
    """Short fingerprint of a fitted mapper - which calibration this is.

    Exact floats on purpose: json round-trips Python floats exactly, so a
    calibration read back from calibration.json has the same signature as
    the one that was saved, and any refit has a different one.
    """
    d = mapper.to_dict()
    items = [d["cx"], d["cy"], d["lo"], d["hi"], bool(d["poly2"])]
    if "axes" in d:     # absent from older files: their signature is unchanged
        items.append(d["axes"])
    blob = json.dumps(items)
    return hashlib.sha1(blob.encode("ascii")).hexdigest()[:16]


def safe_sig(mapper):
    """mapper_sig, or None for no / an unfitted mapper. Never raises."""
    try:
        return None if mapper is None else mapper_sig(mapper)
    except Exception:
        return None


def _to_bytes(samples, meta):
    buf = io.BytesIO()
    extra = {} if samples.group is None else {"group": samples.group}
    np.savez_compressed(buf, gaze=samples.gaze, t=samples.t,
                        screen=samples.screen,
                        meta=np.array(json.dumps(meta)), **extra)
    return buf.getvalue()


def load_samples(path):
    """CalSamples from `path`, or None. Never raises."""
    try:
        if not path or not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            data = f.read()
        with np.load(io.BytesIO(data), allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            gaze, t, screen = z["gaze"], z["t"], z["screen"]
            group = z["group"] if "group" in z.files else None
        if not isinstance(meta, dict) or meta.get("v") != _VERSION:
            return None
        kind = meta.get("kind")
        if kind not in ("dots", "pursuit") or gaze.ndim != 2 \
                or len(t) != len(gaze):
            return None
        if kind == "dots" and screen.shape != (len(gaze), 2):
            return None
        if kind == "pursuit" and not (meta.get("path_pts") and meta.get("speed")):
            return None
        return CalSamples(kind, gaze, t, screen if kind == "dots" else None, meta,
                          group=group)
    except Exception:
        return None


def _write_flushed(path, data):
    try:
        with open(path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        return True
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass
        return False


def save_samples(cal_path, samples, mapper, feat=None, clock=time.time):
    """Write `samples` for `mapper` beside `cal_path`; True on success.

    The previous file moves to the .onceki slot - unless it already describes
    this very calibration (a self-heal or identical re-save), which would
    spend the one generation of history on a copy of itself.
    """
    try:
        key = feat or "n%d" % samples.n_inputs
        path = samples_path(cal_path, key)
        meta = dict(samples.meta)
        meta.update({"v": _VERSION, "kind": samples.kind, "feat": feat,
                     "n_inputs": samples.n_inputs, "sig": mapper_sig(mapper),
                     "saved_at": float(clock())})
        err_cv = getattr(mapper, "err_cv", None)
        meta["err_cv"] = (float(err_cv) if isinstance(err_cv, (int, float))
                          else None)
        data = _to_bytes(samples, meta)
    except Exception as exc:
        _say("UYARI: kalibrasyon ornekleri hazirlanamadi (%s); kalibrasyonun"
             " kendisi etkilenmedi." % type(exc).__name__)
        return False
    tmp = path + ".tmp"
    if not _write_flushed(tmp, data):
        _say("UYARI: kalibrasyon ornekleri kaydedilemedi (%s). Kalibrasyon"
             " kaydedildi; yalniz ayar degisince yeniden hesaplama yapilamaz."
             % path)
        return False
    try:
        old = load_samples(path)
        if old is not None and old.meta.get("sig") != meta["sig"]:
            os.replace(path, previous_path(path))
    except Exception:
        pass        # one generation lost; the slot itself is written below
    try:
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        _say("UYARI: kalibrasyon ornekleri kaydedilemedi (%s). Kalibrasyon"
             " kaydedildi; yalniz ayar degisince yeniden hesaplama yapilamaz."
             % path)
        return False


def save_beside(cal_path, mapper, feat):
    """The hook _save_calibration calls after a successful write. A mapper
    without samples (restored from a backup) is a no-op. Never raises."""
    try:
        cs = getattr(mapper, "cal_samples", None)
        if not isinstance(cs, CalSamples):
            return False
        return save_samples(cal_path, cs, mapper, feat=feat)
    except Exception:
        return False


def find_samples(cal_path, feat, n_inputs, sig=None):
    """Samples for this feature set (newest generation first), optionally
    only those of the calibration with signature `sig`."""
    path = samples_path(cal_path, feat or "n%d" % int(n_inputs))
    for p in (path, previous_path(path)):
        cs = load_samples(p)
        if cs is None or cs.n_inputs != int(n_inputs):
            continue
        stamp = cs.meta.get("feat")
        if feat and stamp and stamp != feat:
            continue
        if sig is not None and cs.meta.get("sig") != sig:
            continue
        return cs
    return None


# --- refitting -----------------------------------------------------------------


def _is_auto(latency):
    return isinstance(latency, str) and latency.strip().lower() == "auto"


def _select(samples, reach_margin, poly2, latency, feat):
    """The per-axis selection (modelsel); RefitResult or None. poly2 = the
    quadratic candidates are allowed at all."""
    try:
        if samples.kind == "pursuit":
            lats = (modelsel.LATENCIES if _is_auto(latency)
                    else (float(latency),))
            sel, lat = modelsel.select_pursuit(
                samples.raw(), samples.path(), samples.groups(), feat=feat,
                reach=reach_margin, allow_quad=poly2, latencies=lats)
        else:
            g, s, grp = samples.grouped_pairs()
            sel, lat = modelsel.select(g, s, grp, feat=feat, reach=reach_margin,
                                       allow_quad=poly2), None
        if sel is None:
            return None
        return RefitResult(sel.mapper, sel.err, sel.err_cv, lat, sel.report)
    except Exception:
        return None


def _legacy_fit(samples, reach_margin, poly2, latency):
    """The single all-inputs model of earlier versions: the fallback when
    nothing could be validated per axis."""
    if samples.kind == "pursuit":
        raw, path = samples.raw(), samples.path()
        if _is_auto(latency):
            mapper, err, lat = best_latency_fit(
                raw, path, reach_margin=reach_margin, poly2=poly2)
        else:
            mapper, err, lat = best_latency_fit(
                raw, path, reach_margin=reach_margin, poly2=poly2,
                candidates=(float(latency),))
        if mapper is None:
            return None
        g, s = pursuit_pairs(raw, path, lat)
        err_cv = holdout_error(g, s, reach_margin=reach_margin,
                               poly2=poly2, blocks=10)
    else:
        g, s = samples.pairs()
        mapper, err = fit_with_rejection(g, s, reach_margin=reach_margin,
                                         poly2=poly2)
        if mapper is None:
            return None
        lat = None
        err_cv = holdout_error(g, s, reach_margin=reach_margin, poly2=poly2)
    return RefitResult(mapper, err, err_cv, lat, None)


def refit(samples, reach_margin, poly2, latency="auto", feat=None):
    """Fit a mapper from saved samples with the given mapping options.

    Same procedure as the live calibration: the per-axis model selection
    (modelsel; for pursuit the eye lag too, unless fixed), graded the honest
    way - on passes (or blocks) the fit did not see. Should the selection be
    impossible, the earlier single model is fitted instead. feat names the
    inputs (default: the stamp the samples were saved under). Returns
    RefitResult or None; never raises.
    """
    try:
        if feat is None:
            feat = samples.meta.get("feat")
        r = _select(samples, reach_margin, poly2, latency, feat)
        if r is None:
            r = _legacy_fit(samples, reach_margin, poly2, latency)
        if r is None:
            return None
        mapper, err, err_cv, lat = r.mapper, r.err, r.err_cv, r.latency
        opts = {"poly2": bool(poly2), "reach": float(reach_margin),
                "latency": latency if _is_auto(latency) else
                (None if latency is None else float(latency))}
        extra = {} if lat is None else {"latency_used": float(lat)}
        mapper.err_cv = err_cv
        mapper.cal_samples = samples.with_opts(opts, **extra)
        return RefitResult(mapper, err, err_cv, lat, r.report)
    except Exception:
        return None


def score_of(mapper, samples):
    """The held-out error of the model `mapper` was fitted as, re-measured
    on its own samples the way a refit is graded (modelsel.validate,
    contiguous blocks): the number a refit is compared against. None when
    not measurable."""
    try:
        d = mapper.to_dict()
        g, s, grp = samples.grouped_pairs()
        rep = modelsel.validate(g, s, grp, reach=float(d.get("reach", 0.0)),
                                poly2=bool(d.get("poly2")),
                                axes=getattr(mapper, "axes", None))
        return None if rep is None else rep["err"]
    except Exception:
        return None


def describe(report):
    """One ASCII line: per axis the chosen inputs, held-out error and how
    much of the calibrated range the cursor spans, then the error after a
    posture change when it was measured. "" without a report."""
    try:
        parts = []
        for ax in ("x", "y"):
            a = report[ax]
            slope = a.get("slope")
            parts.append("%s: %s%s %.0f px, kapsama %s" % (
                ax, "+".join(a.get("groups") or ["?"]),
                " (kare)" if a.get("quad") else "", a["rmse"],
                "?" if slope is None else "%.2f" % slope))
        line = "Model | %s" % " | ".join(parts)
        post = report.get("posture")
        if isinstance(post, dict):
            line += (" | oturus degisince (turlar arasi): %.0f px"
                     % post["err"])
        return line
    except Exception:
        return ""


_OPT_NAMES = (("poly2", "calibration_poly2"), ("reach", "reach_margin"),
              ("latency", "pursuit_latency"))


def _norm_latency(v):
    if v is None or _is_auto(v):
        return "auto"
    try:
        return round(float(str(v).replace(",", ".")), 6)
    except Exception:
        return str(v)


def options_changed(stored, current, kind):
    """config.yaml names of the mapping options that differ."""
    out = []
    stored = stored or {}
    for k, name in _OPT_NAMES:
        if k == "latency":
            if kind != "pursuit":
                continue
            if _norm_latency(stored.get(k)) != _norm_latency(current.get(k)):
                out.append(name)
        elif k == "poly2":
            if bool(stored.get(k)) != bool(current.get(k)):
                out.append(name)
        else:
            try:
                if abs(float(stored.get(k)) - float(current.get(k))) > 1e-9:
                    out.append(name)
            except Exception:
                out.append(name)
    return out


def _label(err_cv):
    if err_cv is None:
        return "?"
    return "IYI" if err_cv < _CV_GOOD else "ORTA" if err_cv < _CV_BAD else "KOTU"


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        else None


def reconcile(cal_path, mapper, feat, n_inputs, opts, should_save, save,
              log=_say):
    """Startup: make the calibration in use match today's mapping options.

    Also, once per calibration, a loaded calibration from before the
    per-axis model selection is refitted from its samples with it.

    mapper: the calibration that was loaded, or None when none could be.
    should_save(err_cv, ref) -> (ok, reason), ref = the loaded calibration's
    error measured the same way (or None); save(mapper) -> bool.
    Returns the mapper to use. Never raises: on any fault the loaded
    calibration is returned untouched.
    """
    try:
        poly2 = bool(opts.get("poly2"))
        reach = float(opts.get("reach", 0.0))
        latency = opts.get("latency", "auto")
        if mapper is None:
            cs = find_samples(cal_path, feat, n_inputs)
            if cs is None:
                return None
            r = refit(cs, reach, poly2, latency, feat)
            if r is None:
                return None
            save(r.mapper)
            log("Kalibrasyon dosyasi kullanilamadi, ama KAYITLI KALIBRASYON"
                " ORNEKLERINDEN yeniden hesaplandi: hata %s px [%s]."
                " YENIDEN KALIBRASYON GEREKMEDI."
                % ("?" if r.err_cv is None else "%.0f" % r.err_cv,
                   _label(r.err_cv)))
            if r.report:
                log("  " + describe(r.report))
            if r.err_cv is None or r.err_cv >= _CV_BAD:
                log("  Imlec belirgin sekilde kayiyorsa F9 x2 ile yeniden"
                    " kalibre edin.")
            return r.mapper
        cs = find_samples(cal_path, feat, n_inputs, sig=mapper_sig(mapper))
        if cs is None:
            return mapper
        changed = options_changed(cs.meta.get("opts"), opts, cs.kind)
        upgrade = (not changed and getattr(mapper, "axes", None) is None
                   and not cs.meta.get("sel_tried"))
        if not changed and not upgrade:
            return mapper
        if upgrade:
            # Once per calibration. Marked BEFORE the attempt, so neither a
            # refusal nor a later "geri al" back to this calibration (its
            # samples then come back from the previous generation, mark
            # and all) brings the attempt back on every startup.
            save_samples(cal_path, cs.with_opts(cs.meta.get("opts") or {},
                                                sel_tried=modelsel._VERSION),
                         mapper, feat=feat)
        names = ", ".join(changed)
        r = refit(cs, reach, poly2, latency, feat)
        if r is None or r.err_cv is None:
            if changed:
                log("Ayar degisti (%s), ama kayitli orneklerden yeniden"
                    " hesaplanamadi; kayitli kalibrasyon KULLANILIYOR." % names)
            return mapper
        if upgrade and not r.report:
            return mapper       # no per-axis model came out: nothing to gain
        # Both measured the same way on the same samples: the grade stored
        # with the loaded calibration may come from another validation
        # (leave-one-out for dots, a different eye lag), so it is the
        # fallback only.
        ref = score_of(mapper, cs)
        new = r.err_cv if r.report else score_of(r.mapper, r.mapper.cal_samples)
        if ref is None or new is None:
            ref, new = None, r.err_cv
        stored = ref
        if stored is None:
            stored = _num(getattr(mapper, "err_cv", None))
            if stored is None:
                stored = _num(cs.meta.get("err_cv"))
        if stored is not None and new > _WORSE * stored:
            if changed:
                log("Ayar degisti (%s): yeni ayarla hata %.0f px, kayitli"
                    " kalibrasyon %.0f px - daha iyi olan kayitli kalibrasyon"
                    " KULLANILIYOR (yeni ayar uygulanmadi)."
                    % (names, new, stored))
            else:
                log("Yeni kalibrasyon modeli kayitli orneklerden denendi: hata"
                    " %.0f px, kayitli kalibrasyon %.0f px - kayitli"
                    " kalibrasyon KULLANILIYOR." % (new, stored))
            return mapper
        ok, why = should_save(r.err_cv, ref)
        if not ok:
            if why:
                log(why)
            return mapper
        # Same samples, same screen and camera: keep the setup stamp.
        r.mapper.screen = getattr(mapper, "screen", None)
        r.mapper.camera = getattr(mapper, "camera", None)
        save(r.mapper)
        if changed:
            log("Ayar degisti (%s): kalibrasyon KAYITLI ORNEKLERDEN yeniden"
                " hesaplandi, hata %.0f px [%s]%s. YENIDEN KALIBRASYON"
                " GEREKMEDI."
                % (names, r.err_cv, _label(r.err_cv),
                   "" if stored is None else " (onceki %.0f px)" % stored))
        else:
            log("Kalibrasyon modeli KAYITLI ORNEKLERDEN yenilendi (eksen"
                " basina ozellik secimi): hata %s -> %.0f px [%s], ikisi de"
                " gormedigi veride ayni sekilde olculdu. YENIDEN KALIBRASYON"
                " GEREKMEDI; eskisi yedekte."
                % ("?" if stored is None else "%.0f" % stored, new,
                   _label(r.err_cv)))
        if r.report:
            log("  " + describe(r.report))
        return r.mapper
    except Exception:
        return mapper


def main(argv=None):
    """Read-only report: how would these samples fit with other options?

        python -m eye_gaze.caldata calibration_samples.<stamp>.npz
               [--poly2 0|1] [--reach 0.25] [--latency auto|0.15]

    Writes nothing; the driver applies an option change itself at startup.
    """
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--poly2", type=int, default=None)
    ap.add_argument("--reach", type=float, default=None)
    ap.add_argument("--latency", default=None)
    a = ap.parse_args(argv)
    cs = load_samples(a.path)
    if cs is None:
        _say("Ornek dosyasi okunamadi: %s" % a.path)
        return 1
    opts = dict(cs.meta.get("opts") or {})
    poly2 = bool(opts.get("poly2", True)) if a.poly2 is None else bool(a.poly2)
    reach = float(opts.get("reach", 0.25)) if a.reach is None else a.reach
    lat = opts.get("latency", "auto") if a.latency is None else a.latency
    _say("%s kalibrasyonu, %d ornek, %d ozellik (%s); kayitli hata: %s px"
         % (cs.kind, len(cs.gaze), cs.n_inputs, cs.meta.get("feat"),
            cs.meta.get("err_cv")))
    r = refit(cs, reach, poly2, lat)
    if r is None:
        _say("Bu ayarlarla yeniden hesaplanamadi.")
        return 1
    _say("poly2=%s reach=%.2f latency=%s -> egitim %.0f px, gormedigi"
         " noktalarda %s px [%s]%s"
         % (poly2, reach, lat, r.err,
            "?" if r.err_cv is None else "%.0f" % r.err_cv, _label(r.err_cv),
            "" if r.latency is None else "; goz gecikmesi %.2f sn" % r.latency))
    if r.report:
        _say(describe(r.report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
