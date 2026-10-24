# gaze/tests/test_caldata.py
"""Raw calibration samples are kept so a MAPPING option can change without
another full calibration.

The pursuit calibration is physically taxing for this user. Before this, a
caregiver flipping calibration_poly2 or reach_margin had two choices: the
setting silently did nothing (the stored mapper keeps its own), or the user
paid for a new calibration. With the samples on disk the mapper is refitted
from them - and graded honestly on held-out points, so a worse option is
noticed instead of installed.
"""
import json
import math
import os

import numpy as np
import pytest

from eye_gaze import caldata
from eye_gaze.mapping import GazeMapper
from eye_gaze.pursuit import PursuitPath

SW, SH = 1280, 720
FEAT = "pe1-hp1-ht1-dg0"
OPTS = {"poly2": False, "reach": 0.25, "latency": "auto"}


def _true_gaze(x, y):
    """A smooth, invertible screen -> 'feature' relation with a curve, so an
    affine fit is measurably worse than a quadratic one."""
    gx = (x / SW - 0.5) * 0.4
    gy = (y / SH - 0.5) * 0.3
    return (gx + 0.15 * gx * gx, gy + 0.1 * gx * gy)


def _dots(n_side=5, passes=2, noise=0.002, seed=3):
    rng = np.random.default_rng(seed)
    g, s = [], []
    for _ in range(passes):
        for i in range(n_side):
            for j in range(n_side):
                x = 0.05 * SW + i * 0.9 * SW / (n_side - 1)
                y = 0.05 * SH + j * 0.9 * SH / (n_side - 1)
                gx, gy = _true_gaze(x, y)
                g.append((gx + rng.normal(0, noise), gy + rng.normal(0, noise)))
                s.append((x, y))
    return g, s


def _pursuit(lat=0.15, speed=480.0, noise=0.002, seed=5):
    path = PursuitPath(SW, SH, speed)
    rng = np.random.default_rng(seed)
    samples = []
    t = 0.0
    while t < path.duration + 0.2:
        x, y = path.pos(t - lat)        # the eye LAGS the ball
        gx, gy = _true_gaze(x, y)
        samples.append(((gx + rng.normal(0, noise), gy + rng.normal(0, noise)), t))
        t += 1 / 30.0
    return samples, path


def _fitted(g, s, poly2=False):
    m = GazeMapper(reach_margin=0.25, poly2=poly2)
    m.fit(g, s)
    return m


# --- building and round-tripping -------------------------------------------


def test_dot_samples_round_trip_through_the_file(tmp_path):
    g, s = _dots()
    cs = caldata.CalSamples.dots(g, s, list(range(len(g))), OPTS)
    cal = tmp_path / "calibration.json"
    m = _fitted(g, s)
    assert caldata.save_samples(str(cal), cs, m, feat=FEAT) is True
    path = caldata.samples_path(str(cal), FEAT)
    assert os.path.exists(path)
    back = caldata.load_samples(path)
    assert back.kind == "dots"
    assert back.n_inputs == 2
    assert np.allclose(back.gaze, np.array(g))
    assert np.allclose(back.screen, np.array(s))
    assert np.allclose(back.t, np.arange(len(g)))
    assert back.meta["feat"] == FEAT
    assert back.meta["opts"] == OPTS
    assert back.meta["sig"] == caldata.mapper_sig(m)


def test_pursuit_samples_keep_time_path_and_learned_latency(tmp_path):
    samples, path = _pursuit()
    cs = caldata.CalSamples.pursuit(samples, path, 0.15, OPTS)
    cal = tmp_path / "calibration.json"
    g, s = caldata.CalSamples.pursuit(samples, path, 0.15, OPTS).pairs()
    caldata.save_samples(str(cal), cs, _fitted(g, s), feat=FEAT)
    back = caldata.load_samples(caldata.samples_path(str(cal), FEAT))
    assert back.kind == "pursuit"
    assert back.meta["latency_used"] == pytest.approx(0.15)
    assert back.meta["speed"] == pytest.approx(480.0)
    assert len(back.gaze) == len(samples)
    # The path is rebuilt from what was saved, not from today's constants.
    p2 = back.path()
    for t in (0.0, 1.0, 3.3, path.duration):
        assert p2.pos(t) == pytest.approx(path.pos(t))


def test_the_samples_file_is_keyed_by_feature_stamp(tmp_path):
    """Like the calibration backups: a 9-input calibration must never evict
    the 11-input samples the day the GPU is gone."""
    cal = str(tmp_path / "calibration.json")
    assert caldata.samples_path(cal, "pe1-hp1-ht1-dg1") != \
        caldata.samples_path(cal, "pe1-hp1-ht1-dg0")
    assert os.path.dirname(caldata.samples_path(cal, FEAT)) == str(tmp_path)


def test_a_new_save_keeps_one_previous_generation(tmp_path):
    cal = str(tmp_path / "calibration.json")
    g, s = _dots()
    m1 = _fitted(g, s)
    caldata.save_samples(cal, caldata.CalSamples.dots(g, s, None, OPTS), m1, feat=FEAT)
    g2, s2 = _dots(seed=9)
    m2 = _fitted(g2, s2)
    caldata.save_samples(cal, caldata.CalSamples.dots(g2, s2, None, OPTS), m2, feat=FEAT)
    now = caldata.load_samples(caldata.samples_path(cal, FEAT))
    old = caldata.load_samples(caldata.previous_path(caldata.samples_path(cal, FEAT)))
    assert now.meta["sig"] == caldata.mapper_sig(m2)
    assert old.meta["sig"] == caldata.mapper_sig(m1)


def test_saving_the_same_calibration_twice_does_not_spend_the_previous_slot(tmp_path):
    cal = str(tmp_path / "calibration.json")
    g, s = _dots()
    m1 = _fitted(g, s)
    cs1 = caldata.CalSamples.dots(g, s, None, OPTS)
    caldata.save_samples(cal, cs1, m1, feat=FEAT)
    g2, s2 = _dots(seed=9)
    m2 = _fitted(g2, s2)
    cs2 = caldata.CalSamples.dots(g2, s2, None, OPTS)
    caldata.save_samples(cal, cs2, m2, feat=FEAT)
    caldata.save_samples(cal, cs2, m2, feat=FEAT)   # e.g. a self-heal re-save
    old = caldata.load_samples(caldata.previous_path(caldata.samples_path(cal, FEAT)))
    assert old.meta["sig"] == caldata.mapper_sig(m1)


def test_save_failure_is_reported_not_raised(tmp_path, monkeypatch, capsys):
    cal = str(tmp_path / "calibration.json")
    g, s = _dots()

    def boom(*a, **k):
        raise OSError("disk dolu")
    monkeypatch.setattr(caldata.os, "replace", boom)
    ok = caldata.save_samples(cal, caldata.CalSamples.dots(g, s, None, OPTS),
                              _fitted(g, s), feat=FEAT)
    assert ok is False
    out = capsys.readouterr().out
    assert "UYARI" in out
    out.encode("ascii")                     # console is cp1254: ASCII only
    leftovers = [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    assert leftovers == []


def test_unreadable_or_garbage_files_load_as_none(tmp_path):
    p = tmp_path / "x.npz"
    p.write_bytes(b"not a zip")
    assert caldata.load_samples(str(p)) is None
    assert caldata.load_samples(str(tmp_path / "missing.npz")) is None


def test_save_beside_is_a_no_op_for_a_mapper_without_samples(tmp_path):
    """A calibration restored from a backup carries no samples; its self-heal
    re-save must not invent or delete any."""
    cal = str(tmp_path / "calibration.json")
    g, s = _dots()
    assert caldata.save_beside(cal, _fitted(g, s), FEAT) is False
    assert not any(n.endswith(".npz") for n in os.listdir(tmp_path))


def test_save_beside_never_raises(tmp_path):
    class Weird:
        cal_samples = "not samples"
    assert caldata.save_beside(str(tmp_path / "c.json"), Weird(), FEAT) is False


# --- refitting ---------------------------------------------------------------


def test_refit_dots_reports_an_honest_holdout_error():
    g, s = _dots()
    cs = caldata.CalSamples.dots(g, s, None, OPTS)
    r = caldata.refit(cs, reach_margin=0.25, poly2=True)
    assert r is not None
    assert r.mapper.n_inputs == 2
    assert r.err_cv is not None and r.err_cv >= r.err * 0.5
    assert r.mapper.err_cv == r.err_cv
    # the refitted mapper carries its samples WITH the new options, so the
    # next save records what it was fitted with
    assert r.mapper.cal_samples.meta["opts"]["poly2"] is True


def test_refit_with_quadratic_beats_affine_on_curved_data():
    g, s = _dots(noise=0.0005)
    cs = caldata.CalSamples.dots(g, s, None, OPTS)
    lin = caldata.refit(cs, 0.25, poly2=False)
    quad = caldata.refit(cs, 0.25, poly2=True)
    assert quad.err_cv < lin.err_cv


def test_refit_pursuit_relearns_the_latency():
    samples, path = _pursuit(lat=0.18)
    cs = caldata.CalSamples.pursuit(samples, path, 0.06, OPTS)
    r = caldata.refit(cs, 0.25, poly2=True, latency="auto")
    assert r is not None
    assert r.latency == pytest.approx(0.18)
    assert r.err_cv is not None and r.err_cv < 40


def test_refit_pursuit_with_a_fixed_latency_uses_it():
    samples, path = _pursuit(lat=0.18)
    cs = caldata.CalSamples.pursuit(samples, path, 0.18, OPTS)
    r = caldata.refit(cs, 0.25, poly2=True, latency=0.09)
    assert r.latency == pytest.approx(0.09)


def test_refit_never_raises_on_bad_samples():
    cs = caldata.CalSamples.dots([(0.0, 0.0)], [(1.0, 1.0)], None, OPTS)
    assert caldata.refit(cs, 0.25, poly2=True) is None


def test_mapper_sig_survives_the_json_round_trip():
    g, s = _dots()
    m = _fitted(g, s)
    m2 = GazeMapper.from_dict(json.loads(json.dumps(m.to_dict())))
    assert caldata.mapper_sig(m) == caldata.mapper_sig(m2)
    m3 = _fitted(*_dots(seed=11))
    assert caldata.mapper_sig(m) != caldata.mapper_sig(m3)


def test_options_changed_names_the_config_keys():
    assert caldata.options_changed(OPTS, dict(OPTS), "pursuit") == []
    diff = caldata.options_changed(OPTS, dict(OPTS, poly2=True), "pursuit")
    assert diff == ["calibration_poly2"]
    diff = caldata.options_changed(OPTS, dict(OPTS, reach=0.3, latency=0.12),
                                   "pursuit")
    assert diff == ["reach_margin", "pursuit_latency"]
    # latency means nothing for dot calibration
    assert caldata.options_changed(OPTS, dict(OPTS, latency=0.12), "dots") == []


# --- startup reconciliation --------------------------------------------------
#
# reconcile() runs once at startup with the calibration that was loaded (or
# None). It may only ever trade the stored calibration for one fitted from the
# SAME calibration's samples, and only when that is not clearly worse.


class _Saver:
    def __init__(self, ok=True):
        self.saved = []
        self.ok = ok

    def __call__(self, mapper):
        self.saved.append(mapper)
        return self.ok


def _stored(tmp_path, opts=OPTS, err_cv=None, seed=3):
    """A calibration 'on disk': its mapper + samples saved beside it. Fitted
    the way this version fits (a 1.2.0 one: see _legacy_pursuit below)."""
    cal = str(tmp_path / "calibration.json")
    g, s = _dots(seed=seed)
    cs = caldata.CalSamples.dots(g, s, None, opts)
    m = caldata.refit(cs, opts["reach"], opts["poly2"], feat=FEAT).mapper
    assert m.axes is not None
    m.err_cv = err_cv
    caldata.save_samples(cal, cs, m, feat=FEAT)
    return cal, m


def _ok(err_cv, ref=None):
    return True, ""


def _run(cal, m, opts, save, should_save=_ok, n_inputs=2, feat=FEAT):
    said = []
    out = caldata.reconcile(cal, m, feat, n_inputs, opts, should_save, save,
                            log=said.append)
    return out, said


def test_same_options_keep_the_loaded_calibration(tmp_path):
    cal, m = _stored(tmp_path)
    save = _Saver()
    out, said = _run(cal, m, dict(OPTS), save)
    assert out is m
    assert save.saved == []


def test_a_changed_option_is_applied_from_the_samples(tmp_path):
    cal, m = _stored(tmp_path, err_cv=None)
    save = _Saver()
    out, said = _run(cal, m, dict(OPTS, poly2=True), save)
    assert out is not m
    assert save.saved == [out]
    assert out.cal_samples.meta["opts"]["poly2"] is True
    text = " ".join(said)
    assert "calibration_poly2" in text
    assert "GEREKMEDI" in text
    text.encode("ascii")


def test_a_clearly_worse_refit_is_not_installed(tmp_path, monkeypatch):
    cal, m = _stored(tmp_path)
    real = caldata.refit

    def worse(*a, **k):
        r = real(*a, **k)
        return r._replace(err_cv=10 * caldata.score_of(m, caldata.find_samples(
            cal, FEAT, 2)))
    monkeypatch.setattr(caldata, "refit", worse)
    save = _Saver()
    out, said = _run(cal, m, dict(OPTS, poly2=True), save)
    assert out is m
    assert save.saved == []
    assert "KULLANILIYOR" in " ".join(said)


def test_samples_of_another_calibration_are_never_used(tmp_path):
    """calibration.json restored from an older backup: the samples on disk
    belong to the newer calibration and must not overwrite it."""
    cal, _ = _stored(tmp_path)
    other = _fitted(*_dots(seed=21))
    save = _Saver()
    out, said = _run(cal, other, dict(OPTS, poly2=True), save)
    assert out is other
    assert save.saved == []


def test_the_previous_generation_is_searched_for_a_matching_calibration(tmp_path):
    cal, m1 = _stored(tmp_path, seed=3)
    _stored(tmp_path, seed=8)                  # newer samples take the slot
    save = _Saver()
    out, _ = _run(cal, m1, dict(OPTS, poly2=True), save)
    assert out is not m1 and save.saved == [out]


def test_no_loadable_calibration_is_rebuilt_from_samples(tmp_path):
    """calibration.json deleted and no backup: the samples still hold a whole
    calibration, and rebuilding it beats a full pursuit calibration."""
    cal, _ = _stored(tmp_path)
    save = _Saver()
    out, said = _run(cal, None, dict(OPTS), save)
    assert out is not None and out.n_inputs == 2
    assert save.saved == [out]
    assert "GEREKMEDI" in " ".join(said)


def test_nothing_on_disk_means_nothing_to_rebuild(tmp_path):
    save = _Saver()
    out, said = _run(str(tmp_path / "calibration.json"), None, dict(OPTS), save)
    assert out is None and save.saved == [] and said == []


def test_samples_with_another_feature_count_are_ignored(tmp_path):
    cal, m = _stored(tmp_path)
    save = _Saver()
    out, _ = _run(cal, None, dict(OPTS), save, n_inputs=11)
    assert out is None and save.saved == []


def test_should_save_refusal_keeps_the_stored_calibration(tmp_path):
    cal, m = _stored(tmp_path)
    save = _Saver()
    out, said = _run(cal, m, dict(OPTS, poly2=True), save,
                     should_save=lambda e, ref=None: (False, "KOTU"))
    assert out is m and save.saved == []


def test_reconcile_never_raises(tmp_path):
    cal, m = _stored(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("x")
    out, _ = _run(cal, m, dict(OPTS, poly2=True), boom, should_save=boom)
    assert out is m


def test_the_builders_return_none_instead_of_raising():
    """They run inside the calibration routines, right after the fit the user
    just paid for: a fault may cost the samples, never the calibration."""
    assert caldata.CalSamples.dots(None, None, None, OPTS) is None
    assert caldata.CalSamples.pursuit([(1, 2)], object(), 0.1, OPTS) is None


# --- passes travel with the samples; the model is chosen per axis -----------


def test_pursuit_pass_index_round_trips_through_the_file(tmp_path):
    samples, path = _pursuit()
    grp = [0] * (len(samples) // 2) + [1] * (len(samples) - len(samples) // 2)
    cs = caldata.CalSamples.pursuit(samples, path, 0.15, OPTS, groups=grp)
    cal = str(tmp_path / "calibration.json")
    caldata.save_samples(cal, cs, _fitted(*cs.pairs()), feat=FEAT)
    back = caldata.find_samples(cal, FEAT, 2)
    assert back.groups().tolist() == grp


def test_a_file_without_pass_index_infers_it_from_time_resets(tmp_path):
    samples, path = _pursuit()
    twice = samples + samples                       # t restarts: two passes
    cs = caldata.CalSamples.pursuit(twice, path, 0.15, OPTS)
    assert cs.group is None
    g = cs.groups()
    assert g.tolist() == [0] * len(samples) + [1] * len(samples)


def test_dots_without_pass_index_have_no_groups():
    g, s = _dots()
    cs = caldata.CalSamples.dots(g, s, None, OPTS)
    assert cs.groups() is None
    cs2 = caldata.CalSamples.dots(g, s, None, OPTS,
                                  groups=[0] * 25 + [1] * 25)
    assert cs2.groups().tolist() == [0] * 25 + [1] * 25


def test_a_bad_pass_index_is_ignored_not_fatal(tmp_path):
    g, s = _dots()
    cs = caldata.CalSamples.dots(g, s, None, OPTS, groups=[0, 1])  # wrong length
    assert cs is not None and cs.group is None


def test_refit_reports_the_per_axis_selection():
    samples, path = _pursuit(lat=0.18)
    cs = caldata.CalSamples.pursuit(samples + samples, path, 0.06, OPTS)
    r = caldata.refit(cs, 0.25, poly2=True, latency="auto", feat=FEAT)
    assert r.report is not None
    assert r.report["cv"] == "blocks"
    assert r.report["posture"]["cv"] == "pass"
    assert r.report["latency"] == pytest.approx(r.latency)
    assert r.err_cv == r.report["err"]
    assert r.mapper.selection == r.report
    assert set(r.report["x"]) >= {"rmse", "slope", "groups", "quad", "lam"}


def test_a_v2_file_keeps_its_signature():
    """Samples are found by the signature of the calibration they belong
    to; a new signature for an old file would orphan every samples file in
    the field."""
    from tests.test_mapping import _V2_FILE
    m = GazeMapper.from_dict(_V2_FILE)
    assert caldata.mapper_sig(m) == "a609ef0f2050212c"


def test_describe_is_one_ascii_line():
    samples, path = _pursuit(lat=0.18)
    cs = caldata.CalSamples.pursuit(samples + samples, path, 0.06, OPTS)
    r = caldata.refit(cs, 0.25, poly2=True, feat=FEAT)
    line = caldata.describe(r.report)
    line.encode("ascii")
    assert "\n" not in line and "x" in line and "y" in line
    assert caldata.describe(None) == ""


# --- startup: a calibration from before the selection is refitted ONCE ------


def _legacy_pursuit(tmp_path, seed=5):
    """A 1.2.0 pursuit calibration on disk: all-features poly2 mapper and its
    samples (two passes), no selection anywhere."""
    cal = str(tmp_path / "calibration.json")
    samples, path = _pursuit(lat=0.15, seed=seed)
    cs = caldata.CalSamples.pursuit(samples + samples, path, 0.15, OPTS)
    m = GazeMapper(reach_margin=0.25, poly2=False)
    m.fit(*cs.pairs())
    m.err_cv = 50
    caldata.save_samples(cal, cs, m, feat=FEAT)
    return cal, m


def test_a_calibration_from_before_selection_is_refitted_at_startup(tmp_path):
    cal, m = _legacy_pursuit(tmp_path)
    save = _Saver()
    out, said = _run(cal, m, dict(OPTS), save)
    assert out is not m and out.selection is not None
    assert save.saved == [out]
    text = " ".join(said)
    text.encode("ascii")
    assert "GEREKMEDI" in text and "->" in text      # before -> after numbers


def test_the_upgrade_is_tried_only_once(tmp_path, monkeypatch):
    cal, m = _legacy_pursuit(tmp_path)
    worse = caldata.RefitResult(m, 900.0, 900.0, 0.15, {"err": 900.0})
    calls = []

    def refit(*a, **k):
        calls.append(1)
        return worse
    monkeypatch.setattr(caldata, "refit", refit)
    save = _Saver()
    out, said = _run(cal, m, dict(OPTS), save)
    assert out is m and save.saved == []            # clearly worse: kept
    assert "KULLANILIYOR" in " ".join(said)
    out, said = _run(cal, m, dict(OPTS), save)      # next startup
    assert out is m and len(calls) == 1 and said == []


def test_old_and_new_are_compared_on_the_same_validation(tmp_path, monkeypatch):
    """The stored grade (50 px here) was measured the old, lenient way; it is
    not what the new one is compared against."""
    cal, m = _legacy_pursuit(tmp_path)
    seen = []
    out, said = _run(cal, m, dict(OPTS), _Saver(),
                     should_save=lambda e, ref=None: (seen.append(ref) or
                                                      (True, "")))
    assert seen and seen[0] is not None and seen[0] != 50


def test_an_undone_upgrade_is_not_redone(tmp_path):
    """After the upgrade the old calibration sits in the backup; if the user
    goes back to it, the next startup must not upgrade it again."""
    cal, m = _legacy_pursuit(tmp_path)
    save = _Saver()

    def really_save(new):
        save(new)
        return caldata.save_beside(cal, new, FEAT)
    out, _ = _run(cal, m, dict(OPTS), really_save)
    assert out is not m
    out2, said = _run(cal, m, dict(OPTS), really_save)   # back on the old one
    assert out2 is m and said == []
