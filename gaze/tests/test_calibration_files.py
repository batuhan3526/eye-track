# gaze/tests/test_calibration_files.py
"""calibration.json must survive every route by which it can be rejected.

It costs a two-pass pursuit calibration, which is physically taxing for this
user - it is the most expensive artifact the system produces. The feature count
it is keyed on can change WITHOUT anyone touching a setting (deep_gaze: "auto"
drops 2 features the day the GPU is busy or its driver updates), and before
this the chain was: reject -> force a recalibration -> OVERWRITE the tuned file
with the degraded one -> reject again when the GPU comes back -> a third
calibration.
"""
import json
import os

import pytest

import gaze_driver as gd
from eye_gaze.mapping import GazeMapper


def _cal_dict(n, seed=1.0, feat=None):
    """A valid v2 calibration payload with n raw input features."""
    d = {"v": 2,
         "cx": [seed] * (1 + n), "cy": [seed + 0.5] * (1 + n),
         "lo": [0.0] * n, "hi": [1.0] * n,
         "reach": 0.25, "poly2": False,
         "slo": [0.0, 0.0], "shi": [1920.0, 1080.0]}
    if feat:
        d["feat"] = feat
    return d


def _mapper(n, seed=1.0):
    return GazeMapper.from_dict(_cal_dict(n, seed))


def _write(path, n, seed=1.0):
    path.write_text(json.dumps(_cal_dict(n, seed)), encoding="utf-8")


def _n_of(path):
    return len(json.loads(path.read_text(encoding="utf-8"))["lo"])


# --- backups are keyed by feature count ------------------------------------


def test_save_preserves_the_previous_calibration_in_its_feature_slot(tmp_path):
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    assert gd._save_calibration(str(cal), _mapper(9)) is True
    assert _n_of(cal) == 9, "yeni kalibrasyon yazilmadi"
    bak = tmp_path / "calibration.json.11.bak"
    assert bak.exists(), "eski 11 ozellikli kalibrasyon yedeklenmedi"
    assert _n_of(bak) == 11


def test_a_nine_input_save_can_never_evict_the_eleven_input_backup(tmp_path):
    """The exact loss this design exists to make impossible.

    GPU vanishes -> 11-input file rejected -> recalibrate -> save 9. GPU still
    gone next session, user recalibrates again -> save 9 again. With a single
    .bak slot the second save would grind the tuned 11-input file away and the
    user would owe a THIRD full calibration once the GPU returned.
    """
    cal = tmp_path / "calibration.json"
    _write(cal, 11, seed=7.0)
    gd._save_calibration(str(cal), _mapper(9, seed=2.0))
    gd._save_calibration(str(cal), _mapper(9, seed=3.0))
    gd._save_calibration(str(cal), _mapper(9, seed=4.0))
    bak11 = tmp_path / "calibration.json.11.bak"
    assert _n_of(bak11) == 11
    kept = json.loads(bak11.read_text(encoding="utf-8"))
    assert kept["cx"] == [7.0] * 12, "yedekteki 11 ozellikli dosya degismis"
    assert _n_of(tmp_path / "calibration.json.9.bak") == 9


def test_one_previous_generation_is_kept_within_a_slot(tmp_path):
    """Same-feature-count recalibrations do evict each other - but one step of
    history is kept, so a bad recalibration done twice is still recoverable."""
    cal = tmp_path / "calibration.json"
    _write(cal, 9, seed=1.0)
    gd._save_calibration(str(cal), _mapper(9, seed=2.0))
    gd._save_calibration(str(cal), _mapper(9, seed=3.0))
    slot = json.loads((tmp_path / "calibration.json.9.bak")
                      .read_text(encoding="utf-8"))
    prev = json.loads((tmp_path / "calibration.json.9.bak.onceki")
                      .read_text(encoding="utf-8"))
    assert slot["cx"][0] == 2.0
    assert prev["cx"][0] == 1.0


def test_an_unparseable_old_file_never_occupies_a_feature_slot(tmp_path):
    """Garbage must not squat in .11.bak, or it would evict a real 11-input
    backup and then fail to restore it."""
    cal = tmp_path / "calibration.json"
    cal.write_text("{yarim yazil", encoding="utf-8")
    gd._save_calibration(str(cal), _mapper(11))
    assert (tmp_path / "calibration.json.bak").read_text(
        encoding="utf-8") == "{yarim yazil"
    assert not (tmp_path / "calibration.json.11.bak").exists()


def test_an_identical_save_does_not_churn_the_backup(tmp_path):
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    gd._save_calibration(str(cal), _mapper(11))
    assert not (tmp_path / "calibration.json.11.bak").exists()


# --- restore instead of recalibrate ----------------------------------------


def test_a_mismatched_file_is_restored_from_the_matching_slot(tmp_path, capsys):
    """The GPU came back: the live file is the degraded 9-input one, but the
    tuned 11-input one is still in its slot. No recalibration is owed."""
    cal = tmp_path / "calibration.json"
    _write(cal, 9, seed=2.0)
    (tmp_path / "calibration.json.11.bak").write_text(
        json.dumps(_cal_dict(11, seed=7.0)), encoding="utf-8")
    m = gd._load_calibration(str(cal), 11, deep_on=True)
    assert m is not None, "uyan yedek varken yeniden kalibrasyon isteniyor"
    assert m.n_inputs == 11
    out = capsys.readouterr().out
    assert "YENIDEN KALIBRASYON GEREKMIYOR" in out, out


def test_a_restore_self_heals_the_main_file_and_keeps_the_replaced_one(tmp_path):
    cal = tmp_path / "calibration.json"
    _write(cal, 9, seed=2.0)
    (tmp_path / "calibration.json.11.bak").write_text(
        json.dumps(_cal_dict(11, seed=7.0)), encoding="utf-8")
    gd._load_calibration(str(cal), 11, deep_on=True)
    assert _n_of(cal) == 11, "ana dosya duzeltilmedi"
    assert _n_of(tmp_path / "calibration.json.9.bak") == 9, \
        "yerine gecilen 9 ozellikli dosya kayboldu"


def test_a_corrupt_main_file_also_reaches_the_backup(tmp_path):
    cal = tmp_path / "calibration.json"
    cal.write_text("]bozuk[", encoding="utf-8")
    (tmp_path / "calibration.json.11.bak").write_text(
        json.dumps(_cal_dict(11)), encoding="utf-8")
    m = gd._load_calibration(str(cal), 11, deep_on=True)
    assert m is not None and m.n_inputs == 11


def test_a_backup_with_the_wrong_count_is_not_used(tmp_path, capsys):
    """Non-vacuity for the restores above: the slot must actually be checked."""
    cal = tmp_path / "calibration.json"
    _write(cal, 9)
    (tmp_path / "calibration.json.11.bak").write_text(
        json.dumps(_cal_dict(7)), encoding="utf-8")
    assert gd._load_calibration(str(cal), 11, deep_on=True) is None
    assert "yeniden kalibrasyon yapilacak" in capsys.readouterr().out


def test_a_matching_file_is_returned_untouched(tmp_path):
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    before = cal.read_bytes()
    m = gd._load_calibration(str(cal), 11, deep_on=True)
    assert m is not None and m.n_inputs == 11
    assert cal.read_bytes() == before
    assert not (tmp_path / "calibration.json.11.bak").exists()


def test_a_missing_file_says_nothing(tmp_path, capsys):
    assert gd._load_calibration(str(tmp_path / "yok.json"), 11) is None
    assert capsys.readouterr().out == ""


# --- the rejection has to say WHY ------------------------------------------


def test_rejection_names_the_counts_and_blames_the_deep_model(tmp_path, capsys):
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    gd._load_calibration(str(cal), 9, deep_on=False,
                         note="per_eye_features=acik")
    out = capsys.readouterr().out
    assert "kayitli: 11 ozellik" in out, out
    assert "su an: 9 ozellik" in out, out
    assert "derin bakis modeli kalibrasyon sirasinda ACIKTI" in out, out
    assert "per_eye_features=acik" in out, out


def test_rejection_blames_config_yaml_when_the_deep_model_is_not_the_cause(
        tmp_path, capsys):
    """Non-vacuity for the test above: the deep-model line must not be the
    answer to every mismatch, or it is not a diagnosis."""
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    gd._load_calibration(str(cal), 6, deep_on=False)
    out = capsys.readouterr().out
    assert "derin bakis modeli" not in out, out
    assert "per_eye_features" in out and "head_translation" in out, out


def test_rejection_reports_a_deep_model_that_just_turned_on(tmp_path, capsys):
    cal = tmp_path / "calibration.json"
    _write(cal, 9)
    gd._load_calibration(str(cal), 11, deep_on=True)
    out = capsys.readouterr().out
    assert "derin bakis modeli bu oturumda ACILDI" in out, out


# --- failures of the new code itself ---------------------------------------


def test_a_failed_write_leaves_the_existing_calibration_intact(tmp_path, monkeypatch):
    """Disk full / crash during the save. The old open(path, "w") truncated
    first, so this used to destroy the file outright."""
    cal = tmp_path / "calibration.json"
    _write(cal, 11, seed=7.0)
    before = cal.read_bytes()
    monkeypatch.setattr(gd.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("dolu")))
    assert gd._save_calibration(str(cal), _mapper(9)) is False
    assert cal.read_bytes() == before, "yarim yazim eski dosyayi bozdu"
    assert not (tmp_path / "calibration.json.tmp").exists()


def test_a_failed_backup_write_does_not_remove_the_existing_slot(tmp_path,
                                                                 monkeypatch):
    """The hole the first draft of this code opened.

    The slot was rotated to .onceki BEFORE its replacement was written, so a
    full disk left calibration.json.11.bak gone while a good copy sat in
    .onceki that nothing looked at - a new silent-loss path inside the fix for
    silent loss.
    """
    cal = tmp_path / "calibration.json"
    slot = tmp_path / "calibration.json.11.bak"
    slot.write_text(json.dumps(_cal_dict(11, seed=7.0)), encoding="utf-8")
    _write(cal, 11, seed=8.0)
    monkeypatch.setattr(gd, "_write_flushed", lambda p, d: False)
    gd._save_calibration(str(cal), _mapper(9))
    assert slot.exists(), "yedek yuvasi bos yazim yuzunden yok oldu"
    assert json.loads(slot.read_text(encoding="utf-8"))["cx"][0] == 7.0


def test_the_previous_generation_is_used_when_the_slot_is_unusable(tmp_path):
    """Belt and braces for the same failure: an interrupted rotation must not
    be able to hide a good calibration."""
    cal = tmp_path / "calibration.json"
    _write(cal, 9)
    (tmp_path / "calibration.json.11.bak").write_text("]yarim", encoding="utf-8")
    (tmp_path / "calibration.json.11.bak.onceki").write_text(
        json.dumps(_cal_dict(11, seed=7.0)), encoding="utf-8")
    m = gd._load_calibration(str(cal), 11, deep_on=True)
    assert m is not None and m.n_inputs == 11
    assert m.to_dict()["cx"][0] == 7.0


def test_an_unreadable_existing_file_is_not_overwritten_in_silence(tmp_path,
                                                                   monkeypatch,
                                                                   capsys):
    """An antivirus or a sync client holding calibration.json open: the backup
    cannot be taken, and saying nothing would be the same silent loss one layer
    down."""
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    real = open

    def boom(path, mode="r", *a, **k):
        if "r" in mode and str(path) == str(cal):
            raise OSError("kilitli")
        return real(path, mode, *a, **k)

    monkeypatch.setattr(gd, "open", boom, raising=False)
    assert gd._save_calibration(str(cal), _mapper(9)) is True
    out = capsys.readouterr().out
    assert "YEDEGI ALINAMIYOR" in out, out
    assert "kilitli olabilir" in out, out


def test_a_failed_save_is_announced(tmp_path, monkeypatch, capsys):
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    monkeypatch.setattr(gd, "_write_flushed", lambda p, d: False)
    assert gd._save_calibration(str(cal), _mapper(9)) is False
    out = capsys.readouterr().out
    assert "YEDEGI ALINAMADI" in out, out
    assert "KAYDEDILEMEDI" in out, out
    assert "yeniden kalibrasyon gerekir" in out, out


def test_a_failed_backup_still_saves_the_new_calibration(tmp_path, monkeypatch,
                                                         capsys):
    """The mapper in hand was just paid for with a full pursuit calibration.
    Refusing to persist it would guarantee the user pays that price again."""
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    monkeypatch.setattr(gd, "_backup_calibration", lambda p, o: False)
    assert gd._save_calibration(str(cal), _mapper(9)) is True
    assert _n_of(cal) == 9


def test_an_unwritable_backup_directory_does_not_raise(tmp_path, monkeypatch):
    cal = tmp_path / "calibration.json"
    _write(cal, 11)

    def boom(*a, **k):
        raise OSError("yazma korumali")

    # Module-scoped, not builtins: pytest's own capture machinery needs open().
    monkeypatch.setattr(gd, "open", boom, raising=False)
    assert gd._save_calibration(str(cal), _mapper(9)) is False  # must not raise
    assert cal.read_bytes() == json.dumps(_cal_dict(11)).encode("utf-8")


# --- deep model: expected absence vs silent downgrade ----------------------


def _cfg(mode):
    return {"deep_gaze": mode}


def test_no_gpu_and_no_history_is_a_quiet_normal_state(tmp_path, capsys):
    lines = gd._deep_report(_cfg("auto"), None, str(tmp_path / "calibration.json"), 9)
    assert len(lines) == 1
    assert "DIKKAT" not in lines[0], lines
    assert "geometrik mod" in lines[0]


def test_a_machine_that_was_using_the_deep_model_is_told_loudly(tmp_path):
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    lines = gd._deep_report(_cfg("auto"), None, str(cal), 9)
    text = "\n".join(lines)
    assert "DERIN BAKIS MODELI BU OTURUMDA ACILAMADI" in text, text
    assert "11 ozellikle alinmis" in text, text
    assert "YEDEKTE KORUNUYOR" in text, text


def test_the_backup_slot_alone_is_enough_evidence(tmp_path):
    """After one GPU-less session the main file already holds 9 features and
    only calibration.json.11.bak remembers deep was ever used. Without this the
    warning would appear once and then go silent for good."""
    cal = tmp_path / "calibration.json"
    _write(cal, 9)
    (tmp_path / "calibration.json.11.bak").write_text(
        json.dumps(_cal_dict(11)), encoding="utf-8")
    lines = gd._deep_report(_cfg("auto"), None, str(cal), 9)
    assert any("ACILAMADI" in ln for ln in lines), lines


def test_an_explicit_deep_gaze_true_always_shouts(tmp_path):
    lines = gd._deep_report(_cfg(True), None, str(tmp_path / "calibration.json"), 9)
    assert any("ACILAMADI" in ln for ln in lines), lines


@pytest.mark.parametrize("mode", ["false", False, "off", ""])
def test_deep_gaze_off_says_nothing(tmp_path, mode):
    assert gd._deep_report(_cfg(mode), None, str(tmp_path / "c.json"), 9) == []


def test_a_running_deep_model_says_nothing(tmp_path):
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    assert gd._deep_report(_cfg("auto"), object(), str(cal), 9) == []


# --- deep model at startup: a busy GPU must not cost the calibration --------


class _FakeCap:
    def get(self, prop):
        return {gd.cv2.CAP_PROP_FRAME_WIDTH: 1280,
                gd.cv2.CAP_PROP_FRAME_HEIGHT: 960}.get(prop, 0)


@pytest.fixture
def fake_deep(monkeypatch):
    """torch + ptgaze + DeepGaze stand-ins: no real model, no GPU touched. `state`
    sets what CUDA does; `calls` records every device a load was tried on."""
    import sys
    import types
    import eye_gaze.deep as deep_mod
    state = {"cuda": True, "cuda_load_fails": False}
    calls = []
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: state["cuda"])
    monkeypatch.setitem(sys.modules, "torch", torch)
    # The torch backend needs ptgaze too; no ONNX model, so these tests keep
    # exercising the torch path whatever this checkout has built.
    monkeypatch.setitem(sys.modules, "ptgaze", types.ModuleType("ptgaze"))
    monkeypatch.setattr(deep_mod, "_onnx_ready", lambda: False)

    class FakeDeep:
        def __init__(self, w, h, device=None):
            calls.append(device)
            if device == "cuda" and state["cuda_load_fails"]:
                raise RuntimeError("CUDA out of memory")
            self.device = device

    monkeypatch.setattr(deep_mod, "DeepGaze", FakeDeep)
    return state, calls


def test_a_busy_gpu_loads_the_deep_model_on_cpu_when_the_calibration_needs_it(
        fake_deep, capsys):
    """VRAM held by another program: the CUDA load fails. The stored
    calibration was taken WITH the deep model, so dropping it would make that
    calibration unusable and push the user into a tiring F9. CPU gives the
    same features (measured identical), only slower."""
    state, calls = fake_deep
    state["cuda_load_fails"] = True
    dg = gd._make_deep(_cfg("auto"), _FakeCap(), expected=True)
    assert dg is not None and dg.device == "cpu"
    assert calls == ["cuda", "cpu"]
    assert "CPU" in capsys.readouterr().out


def test_no_cuda_at_all_still_runs_on_cpu_when_the_calibration_needs_it(
        fake_deep):
    state, calls = fake_deep
    state["cuda"] = False
    dg = gd._make_deep(_cfg("auto"), _FakeCap(), expected=True)
    assert dg is not None and dg.device == "cpu"
    assert calls == ["cpu"]


def test_a_busy_gpu_says_to_restart_once_it_is_free(fake_deep, capsys):
    # The development machine's message, word for word (it moved into the catalog).
    state, _ = fake_deep
    state["cuda_load_fails"] = True
    gd._make_deep(_cfg(True), _FakeCap(), expected=True)
    out = capsys.readouterr().out
    assert ("DIKKAT: GPU kullanilamadi - derin bakis modeli CPU'da ACILDI."
            " Kalibrasyonunuz GECERLI; imlec biraz daha yavas tepki verir."
            " GPU bosalinca surucuyu yeniden baslatin.") in out


@pytest.mark.parametrize("language, said, not_said", [
    ("tr", "GPU bulunamadi - derin bakis modeli CPU'da calisiyor",
     "GPU bosalinca"),
    ("en", "no GPU usable by the deep model was found", "once the GPU is free"),
])
def test_no_gpu_at_all_does_not_send_the_user_waiting_for_one(
        fake_deep, capsys, language, said, not_said):
    # Task 16 review: deep_gaze: true on a machine without any GPU said
    # "GPU kullanilamadi ... GPU bosalinca yeniden baslatin" - a GPU that
    # never comes. The calibration is still valid: say that, and only that.
    from eye_gaze import i18n
    i18n.set_language(language)
    state, _ = fake_deep
    state["cuda"] = False
    dg = gd._make_deep(_cfg(True), _FakeCap(), expected=True)
    assert dg is not None and dg.device == "cpu"
    out = capsys.readouterr().out
    assert said in out and not_said not in out
    assert "GECERLI" in out or "VALID" in out
    out.encode("ascii")                 # console lines stay ASCII


def test_no_cuda_and_no_deep_calibration_stays_geometric(fake_deep):
    """A machine that never used the deep model: CPU deep would only ADD two
    features the stored calibration does not have, and be slow."""
    state, calls = fake_deep
    state["cuda"] = False
    assert gd._make_deep(_cfg("auto"), _FakeCap(), expected=False) is None
    assert calls == []


def test_a_failed_cuda_load_without_a_deep_calibration_stays_geometric(
        fake_deep):
    state, calls = fake_deep
    state["cuda_load_fails"] = True
    assert gd._make_deep(_cfg("auto"), _FakeCap(), expected=False) is None
    assert calls == ["cuda"]


def test_a_working_gpu_is_used_as_before(fake_deep):
    state, calls = fake_deep
    dg = gd._make_deep(_cfg("auto"), _FakeCap(), expected=True)
    assert dg.device == "cuda" and calls == ["cuda"]


# --- the "no click method" warning must be able to fire --------------------


def test_the_warning_fires_for_an_unbound_gamepad_reader():
    """gaze_driver:653 was dead code: _Clicker builds a GamepadReader whenever
    gamepad_enabled is true (the default), controller attached or not, so
    `clicker.gamepad is None` was false in every shipped configuration and the
    warning could never fire - including in the case it was written for."""
    cfg = {"gamepad_enabled": True, "left_click_key": "", "gamepad_left": ""}
    assert gd._has_click_method(cfg, object(), None, None) is False


@pytest.mark.parametrize("cfg, gamepad, dweller, blinker", [
    ({"left_click_key": "space", "gamepad_left": ""}, object(), None, None),
    ({"left_click_key": "", "gamepad_left": "A"}, object(), None, None),
    ({"left_click_key": "", "gamepad_left": ""}, None, object(), None),
    ({"left_click_key": "", "gamepad_left": ""}, None, None, object()),
])
def test_any_bound_route_counts_as_a_click_method(cfg, gamepad, dweller, blinker):
    assert gd._has_click_method(cfg, gamepad, dweller, blinker) is True


def test_a_gamepad_binding_without_a_reader_is_not_a_click_method():
    """gamepad_enabled: false leaves the binding in config.yaml but nothing to
    read it with."""
    cfg = {"left_click_key": "", "gamepad_left": "A"}
    assert gd._has_click_method(cfg, None, None, None) is False


# --- the feature COUNT is not a fingerprint --------------------------------
#
# 9 features can be reached from more than one set of flags, and GazeMapper's
# saved dict recorded none of them. Restoring across that boundary produces a
# mapper whose coefficients were fitted for different inputs entirely - a
# plausible but WRONG cursor - and the old code announced it in capitals as
# needing no recalibration.


# per_eye on, head pose + translation on, deep off  -> 2+2+2+3   = 9
_NINE_A = dict(per_eye=True, use_head=True, use_tr=True, deep_on=False)
# per_eye off, head pose + translation on, deep on  -> 2+0+2+3+2 = 9
_NINE_B = dict(per_eye=False, use_head=True, use_tr=True, deep_on=True)


def _fid(**kw):
    return gd._feature_id(**kw)


def test_two_different_feature_sets_really_do_collide_on_nine():
    """Non-vacuity for everything below: without this the tests would be
    checking a distinction that never arises in practice."""
    def n_inputs(per_eye, use_head, use_tr, deep_on):
        return (2 + (2 if per_eye else 0) + (2 if use_head else 0)
                + (3 if (use_head and use_tr) else 0) + (2 if deep_on else 0))
    assert n_inputs(**_NINE_A) == n_inputs(**_NINE_B) == 9
    assert _fid(**_NINE_A) != _fid(**_NINE_B)


def test_a_backup_from_a_different_feature_set_is_not_restored(tmp_path, capsys):
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps(_cal_dict(11, feat=_fid(per_eye=True, use_head=True,
                                                      use_tr=True, deep_on=True))),
                   encoding="utf-8")
    (tmp_path / "calibration.json.9.bak").write_text(
        json.dumps(_cal_dict(9, seed=7.0, feat=_fid(**_NINE_B))), encoding="utf-8")
    m = gd._load_calibration(str(cal), 9, feat=_fid(**_NINE_A))
    out = capsys.readouterr().out
    assert m is None, "baska ayarlarla alinmis yedek geri yuklendi"
    assert "YENIDEN KALIBRASYON GEREKMIYOR" not in out, out
    assert "baska ayarlarla" in out, out


def test_a_backup_from_the_same_feature_set_is_restored(tmp_path, capsys):
    """Non-vacuity for the test above: the guard must reject the flags, not the
    backups."""
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    (tmp_path / "calibration.json.9.bak").write_text(
        json.dumps(_cal_dict(9, seed=7.0, feat=_fid(**_NINE_A))), encoding="utf-8")
    m = gd._load_calibration(str(cal), 9, feat=_fid(**_NINE_A))
    assert m is not None and m.to_dict()["cx"][0] == 7.0
    assert "YENIDEN KALIBRASYON GEREKMIYOR" in capsys.readouterr().out


def test_a_saved_calibration_records_the_feature_set_it_was_fitted_for(tmp_path):
    cal = tmp_path / "calibration.json"
    gd._save_calibration(str(cal), _mapper(9), feat=_fid(**_NINE_A))
    assert json.loads(cal.read_text(encoding="utf-8"))["feat"] == _fid(**_NINE_A)


def test_the_main_file_is_rejected_when_its_feature_set_differs(tmp_path, capsys):
    """The same collision one layer up: the live file matches on count alone."""
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps(_cal_dict(9, feat=_fid(**_NINE_B))), encoding="utf-8")
    assert gd._load_calibration(str(cal), 9, feat=_fid(**_NINE_A)) is None
    out = capsys.readouterr().out
    assert "BASKA AYARLARLA" in out, out


def test_a_calibration_without_a_recorded_feature_set_still_loads(tmp_path):
    """Every calibration.json in the field predates this stamp. Refusing them
    would charge a full pursuit calibration for an upgrade."""
    cal = tmp_path / "calibration.json"
    _write(cal, 9)
    m = gd._load_calibration(str(cal), 9, feat=_fid(**_NINE_A))
    assert m is not None and m.n_inputs == 9


def test_an_unstamped_backup_is_restored_but_not_promised(tmp_path, capsys):
    """A backup taken from a pre-stamp calibration.json cannot be verified.
    Restoring it still beats a taxing recalibration, but the message may not
    claim what it does not know."""
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    (tmp_path / "calibration.json.9.bak").write_text(
        json.dumps(_cal_dict(9, seed=7.0)), encoding="utf-8")
    m = gd._load_calibration(str(cal), 9, feat=_fid(**_NINE_A))
    out = capsys.readouterr().out
    assert m is not None and m.to_dict()["cx"][0] == 7.0
    assert "hangi ayarlarla alindigi kayitli degil" in out, out
    assert "YENIDEN KALIBRASYON GEREKMIYOR" not in out, out


def test_an_unverifiable_restore_is_not_stamped_with_the_current_flags(tmp_path):
    """Self-healing must not launder a guess into a recorded certainty."""
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    (tmp_path / "calibration.json.9.bak").write_text(
        json.dumps(_cal_dict(9, seed=7.0)), encoding="utf-8")
    gd._load_calibration(str(cal), 9, feat=_fid(**_NINE_A))
    assert "feat" not in json.loads(cal.read_text(encoding="utf-8"))


def test_a_stamped_mismatch_names_the_setting_that_changed(tmp_path, capsys):
    """With both stamps in hand the cause is no longer a guess. The heuristic
    stays for unstamped files, but it must not be preferred over evidence: here
    it would have blamed config.yaml in general terms."""
    cal = tmp_path / "calibration.json"
    cal.write_text(json.dumps(_cal_dict(11, feat=_fid(per_eye=True, use_head=True,
                                                      use_tr=True, deep_on=True))),
                   encoding="utf-8")
    gd._load_calibration(str(cal), 9, deep_on=True,
                         feat=_fid(per_eye=False, use_head=True, use_tr=True,
                                   deep_on=True))
    out = capsys.readouterr().out
    assert "per_eye_features" in out, out
    assert "EN OLASI SEBEP" not in out, "kanit varken hala tahmin ediliyor"
    assert "kalibrasyonda acik, su an kapali" in out, out


def test_the_guess_is_still_made_for_an_unstamped_file(tmp_path, capsys):
    """Non-vacuity for the test above."""
    cal = tmp_path / "calibration.json"
    _write(cal, 11)
    gd._load_calibration(str(cal), 9, deep_on=False,
                         feat=_fid(per_eye=True, use_head=True, use_tr=True,
                                   deep_on=False))
    assert "EN OLASI SEBEP" in capsys.readouterr().out


# --- rotation must cost something to be worth doing ------------------------


def test_an_unchanged_slot_is_not_rotated(tmp_path):
    """_save_calibration and _remember_good both skip identical content; the
    backup did not, so it burned its one generation of history on a no-op."""
    cal = tmp_path / "calibration.json"
    slot = tmp_path / "calibration.json.11.bak"
    slot.write_text(json.dumps(_cal_dict(11, seed=7.0)), encoding="utf-8")
    _write(cal, 11, seed=7.0)
    gd._save_calibration(str(cal), _mapper(9))
    assert not (tmp_path / "calibration.json.11.bak.onceki").exists(), \
        "ayni icerik yedeklenirken bir kusak gecmis harcandi"


def test_a_gpu_ping_pong_does_not_consume_the_history(tmp_path):
    """The measured failure: the user does nothing but restart twice with the
    GPU flapping, and both generations of the slot end up holding the same
    copy - so the guarantee the .onceki generation exists for is gone."""
    cal = tmp_path / "calibration.json"
    good11 = json.dumps(_cal_dict(11, seed=7.0))
    cal.write_text(good11, encoding="utf-8")
    for _ in range(3):
        # GPU gone: 9 features, the 11-input file is backed up and replaced
        gd._save_calibration(str(cal), _mapper(9, seed=2.0))
        # GPU back: the 11-input backup is restored over the 9-input file
        gd._load_calibration(str(cal), 11, deep_on=True)
    assert not (tmp_path / "calibration.json.11.bak.onceki").exists(), \
        "GPU gidip gelmesi yedegin gecmisini tuketti"
    assert json.loads((tmp_path / "calibration.json.11.bak")
                      .read_text(encoding="utf-8"))["cx"][0] == 7.0


# --- a DELETED calibration.json is the clearest case for the backup --------


def test_a_deleted_calibration_is_recovered_from_its_slot(tmp_path, capsys):
    """calibration.json removed by a sync client, an antivirus or a caregiver
    tidying up. This is the one case where the backup is CERTAINLY valid, and it
    was the one case that never reached it."""
    cal = tmp_path / "calibration.json"
    (tmp_path / "calibration.json.9.bak").write_text(
        json.dumps(_cal_dict(9, seed=7.0, feat=_fid(**_NINE_A))), encoding="utf-8")
    m = gd._load_calibration(str(cal), 9, feat=_fid(**_NINE_A))
    assert m is not None and m.to_dict()["cx"][0] == 7.0
    assert "YENIDEN KALIBRASYON GEREKMIYOR" in capsys.readouterr().out
    assert cal.exists(), "geri yuklenen kalibrasyon ana dosyaya yazilmadi"


def test_a_first_ever_run_is_still_silent(tmp_path, capsys):
    """No calibration and no backup is the normal first start, not a fault."""
    assert gd._load_calibration(str(tmp_path / "calibration.json"), 9) is None
    assert capsys.readouterr().out == ""


# --- the persistent window has to carry the warning ------------------------


def test_the_backup_config_warning_is_on_the_camera_window():
    """The [ayar] lines and the startup banner have scrolled away by the time
    the camera window opens, and that window is the only thing the user looks at
    for the rest of the session."""
    from eye_gaze.config import BACKUP_KEY
    lines = gd._persistent_warnings({BACKUP_KEY: True}, True)
    text = " ".join(lines)
    assert "ESKI AYARLAR" in text, lines
    assert "config.yaml" in text, lines


def test_no_warning_is_drawn_for_a_healthy_session():
    assert gd._persistent_warnings({"left_click_key": "space"}, True) == []


def test_a_session_that_cannot_click_says_so_on_the_window():
    lines = gd._persistent_warnings({}, False)
    assert any("TIKLAMA" in ln for ln in lines), lines


def test_the_shipped_config_is_not_warned_about(tmp_path, shipped_config):
    """The real config.yaml, loaded from a copy so the test never writes into
    the repository (load_config keeps a last-known-good copy beside the file).
    The template where the source zip has no config.yaml."""
    import shutil
    from eye_gaze.config import load_config
    src = str(shipped_config)
    dst = tmp_path / "config.yaml"
    shutil.copyfile(src, dst)
    cfg = load_config(str(dst))
    assert gd._has_click_method(cfg, object(), None, None) is True
    assert cfg.get("left_click_key") == "space", \
        "sevkiyat config'i degismis; bu testin dayanagi kalmadi"


# --- a bad recalibration must not replace a good one -------------------------


def test_the_validation_error_is_stored_with_the_calibration(tmp_path):
    p = tmp_path / "calibration.json"
    gd._save_calibration(str(p), _mapper(11), feat="x", err_cv=87.4)
    assert json.loads(p.read_text(encoding="utf-8"))["err_cv"] == 87


def test_a_bad_recalibration_does_not_overwrite_a_good_one(tmp_path):
    p = tmp_path / "calibration.json"
    gd._save_calibration(str(p), _mapper(11), err_cv=90)
    ok, why = gd._should_save(str(p), 260)
    assert ok is False and "90" in why and "260" in why


def test_a_better_or_acceptable_recalibration_is_saved(tmp_path):
    p = tmp_path / "calibration.json"
    gd._save_calibration(str(p), _mapper(11), err_cv=90)
    assert gd._should_save(str(p), 150)[0] is True     # not KOTU
    assert gd._should_save(str(p), 85)[0] is True


def test_a_bad_recalibration_over_a_bad_one_is_saved(tmp_path):
    # The old file was already bad: the newest is the user's current posture.
    p = tmp_path / "calibration.json"
    gd._save_calibration(str(p), _mapper(11), err_cv=240)
    assert gd._should_save(str(p), 260)[0] is True


def test_an_ungraded_usable_file_is_kept_against_a_bad_recalibration(tmp_path):
    """The field calibration (July) has no err_cv. The first tired F9 used to
    replace it silently; a KOTU newcomer must not, while it still loads."""
    p = tmp_path / "calibration.json"
    _write(p, 11)
    ok, why = gd._should_save(str(p), 400, 11)
    assert ok is False and "400" in why
    assert gd._should_save(str(p), 150, 11)[0] is True     # not KOTU


def test_an_ungraded_file_that_no_longer_fits_never_blocks_a_save(tmp_path):
    # Other feature count: it cannot be used, so keeping it would force a
    # recalibration on every start.
    p = tmp_path / "calibration.json"
    _write(p, 9)
    assert gd._should_save(str(p), 400, 11)[0] is True
    assert gd._should_save(str(p), 400)[0] is True          # count unknown
    assert gd._should_save(str(tmp_path / "yok.json"), 400, 11)[0] is True
    p.write_text("{bozuk", encoding="utf-8")
    assert gd._should_save(str(p), 400, 11)[0] is True


def test_a_restore_keeps_the_stored_grade(tmp_path):
    p = tmp_path / "calibration.json"
    gd._save_calibration(str(p), _mapper(11), err_cv=90)
    gd._save_calibration(str(p), _mapper(9), err_cv=120)   # 11 -> .11.bak
    gd._load_calibration(str(p), 11)                        # restores + heals
    assert json.loads(p.read_text(encoding="utf-8"))["err_cv"] == 90


def test_a_briefly_locked_calibration_file_is_still_saved(tmp_path, monkeypatch):
    """One failed os.replace (antivirus/indexer lock) used to drop the new
    calibration on the floor - it then lived only in memory."""
    p = tmp_path / "calibration.json"
    real = os.replace
    fails = {"n": 2}

    def flaky(a, b):
        if str(b) == str(p) and fails["n"]:
            fails["n"] -= 1
            raise PermissionError("kilitli")
        return real(a, b)

    monkeypatch.setattr(gd.os, "replace", flaky)
    monkeypatch.setattr(gd.time, "sleep", lambda s: None)
    assert gd._atomic_write_bytes(str(p), b"{}") is True
    assert p.read_bytes() == b"{}"
    assert not os.path.exists(str(p) + ".tmp")


# --- the best calibration is never rotated away; undo swaps -----------------


def _err(p):
    return json.loads(p.read_text(encoding="utf-8")).get("err_cv")


def test_three_mediocre_recalibrations_cannot_push_out_the_best(tmp_path):
    """Two .bak generations: 60 px, then 150, 170, 190 (none KOTU, all
    saved) and the 60 px calibration was gone from the disk."""
    p = tmp_path / "calibration.json"
    for seed, e in ((1.0, 60), (2.0, 150), (3.0, 170), (4.0, 190)):
        gd._save_calibration(str(p), _mapper(11, seed), err_cv=e)
    best = tmp_path / "calibration.json.11.en-iyi"
    assert _err(best) == 60


def test_a_better_one_takes_the_best_slot(tmp_path):
    p = tmp_path / "calibration.json"
    gd._save_calibration(str(p), _mapper(11, 1.0), err_cv=90)
    gd._save_calibration(str(p), _mapper(11, 2.0), err_cv=70)
    assert _err(tmp_path / "calibration.json.11.en-iyi") == 70


def test_the_ungraded_field_calibration_fills_an_empty_best_slot(tmp_path):
    p = tmp_path / "calibration.json"
    _write(p, 11, seed=7.0)                       # July: no err_cv
    gd._save_calibration(str(p), _mapper(11, 2.0), err_cv=150)   # ORTA
    best = tmp_path / "calibration.json.11.en-iyi"
    assert _err(best) is None                     # the field one kept
    gd._save_calibration(str(p), _mapper(11, 3.0), err_cv=80)    # IYI
    assert _err(best) == 80


def test_restore_falls_back_to_the_best_slot(tmp_path):
    p = tmp_path / "calibration.json"
    gd._save_calibration(str(p), _mapper(11, 1.0), err_cv=60)
    os.remove(p)
    for b in tmp_path.glob("*.bak*"):
        os.remove(b)
    m = gd._load_calibration(str(p), 11)
    assert m is not None and m.err_cv == 60


def test_undo_swaps_with_the_previous_and_back_again(tmp_path):
    p = tmp_path / "calibration.json"
    a, b = _mapper(11, 1.0), _mapper(11, 2.0)
    gd._save_calibration(str(p), a, err_cv=80)
    gd._save_calibration(str(p), b, err_cv=150)
    m, msg = gd._undo_calibration(str(p), b, 11)
    assert m is not None and gd._same_mapper(m, a) and "geri" in msg
    assert gd._same_mapper(gd._read_calibration(str(p))[0], a)
    m2, _ = gd._undo_calibration(str(p), m, 11)    # a mistaken undo: redo
    assert gd._same_mapper(m2, b)
    assert gd._same_mapper(gd._read_calibration(str(p))[0], b)


def test_undo_of_an_unsaved_calibration_returns_to_the_file(tmp_path):
    # A KOTU recalibration is used for the session but not saved.
    p = tmp_path / "calibration.json"
    a = _mapper(11, 1.0)
    gd._save_calibration(str(p), a, err_cv=80)
    m, msg = gd._undo_calibration(str(p), _mapper(11, 5.0), 11)
    assert gd._same_mapper(m, a) and "Kayitli" in msg


def test_undo_with_nothing_to_go_back_to_says_so(tmp_path):
    p = tmp_path / "calibration.json"
    a = _mapper(11, 1.0)
    gd._save_calibration(str(p), a, err_cv=80)
    m, msg = gd._undo_calibration(str(p), a, 11)
    assert m is None and "yok" in msg


def test_undo_never_restores_a_backup_for_other_features(tmp_path):
    p = tmp_path / "calibration.json"
    gd._save_calibration(str(p), _mapper(11, 1.0), feat="pe1-hp1-ht1-dg0", err_cv=80)
    b = _mapper(11, 2.0)
    gd._save_calibration(str(p), b, feat="pe1-hp1-ht1-dg1", err_cv=90)
    m, _ = gd._undo_calibration(str(p), b, 11, feat="pe1-hp1-ht1-dg1")
    assert m is None


def test_loading_on_another_screen_size_rescales_and_says_so():
    said = []
    m = _mapper(11)
    m.screen, m.camera = (2560, 1440), (1280, 960)
    r = gd._fit_setup(m, 3840, 2160, (1280, 960), say=said.append)
    assert r is not m and r.screen == (3840, 2160)
    assert any("OLCEKLENDI" in s for s in said)
    said.clear()
    assert gd._fit_setup(m, 2560, 1440, (1280, 960), say=said.append) is m
    assert said == []


def test_another_camera_size_is_warned_about():
    said = []
    m = _mapper(11)
    m.screen, m.camera = (2560, 1440), (1280, 960)
    gd._fit_setup(m, 2560, 1440, (640, 480), say=said.append)
    assert any("kamera" in s for s in said)


def test_the_setup_stamp_is_saved_with_the_calibration(tmp_path):
    p = tmp_path / "calibration.json"
    m = _mapper(11)
    m.screen, m.camera = (2560, 1440), (1280, 960)
    gd._save_calibration(str(p), m, err_cv=80)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["screen"] == [2560, 1440] and d["camera"] == [1280, 960]
