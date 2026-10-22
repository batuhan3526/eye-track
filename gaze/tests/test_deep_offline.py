# gaze/tests/test_deep_offline.py
"""The deep model's checkpoint used to be fetched through hf_hub_download on
EVERY start: online it pinged the Hub (the log's 'unauthenticated requests'
warning), on a captive or black-holed network startup stalled ~14 s, and a
wiped HF cache meant no deep model -> the 11-feature calibration rejected ->
a forced recalibration. Now a local copy is used first and network is the
last resort."""
import gaze_driver as gd
from eye_gaze import deep


def _calls():
    log = []

    def cache():
        log.append("cache")
        return None

    def download():
        log.append("download")
        raise OSError("ag yok")
    return log, cache, download


def test_the_local_copy_wins_without_touching_the_network(tmp_path):
    (tmp_path / deep.CHECKPOINT_NAME).write_bytes(b"agirliklar")
    log, cache, download = _calls()
    p = deep.ethxgaze_checkpoint(str(tmp_path), cache, download)
    assert p.endswith(deep.CHECKPOINT_NAME) and log == []


def test_a_cached_hub_file_is_copied_locally(tmp_path):
    src = tmp_path / "hf" / "model.safetensors"
    src.parent.mkdir()
    src.write_bytes(b"agirliklar")
    local = tmp_path / "yerel"
    log = []
    p = deep.ethxgaze_checkpoint(str(local), lambda: str(src),
                                 lambda: log.append("download"))
    assert log == []
    assert (local / deep.CHECKPOINT_NAME).read_bytes() == b"agirliklar"
    assert p == str(local / deep.CHECKPOINT_NAME)


def test_download_is_the_last_resort(tmp_path):
    src = tmp_path / "indi.safetensors"
    src.write_bytes(b"x")
    log = []

    def download():
        log.append("download")
        return str(src)
    p = deep.ethxgaze_checkpoint(str(tmp_path / "yerel"), lambda: None, download)
    assert log == ["download"] and p.endswith(deep.CHECKPOINT_NAME)


def test_a_copy_failure_still_returns_a_usable_path(tmp_path, monkeypatch):
    src = tmp_path / "m.safetensors"
    src.write_bytes(b"x")
    monkeypatch.setattr(deep.shutil, "copyfile",
                        lambda *a: (_ for _ in ()).throw(OSError("dolu")))
    p = deep.ethxgaze_checkpoint(str(tmp_path / "yerel"), lambda: str(src),
                                 lambda: None)
    assert p == str(src)


# --- a deep-model fault in one frame must not end the calibration ----------


class _Geo:
    openness = 0.3
    landmarks = None

    def estimate(self, frame):
        return (0.1, 0.2)

    def close(self):
        pass


class _BrokenDeep:
    def __init__(self):
        self.calls = 0

    def estimate(self, frame):
        self.calls += 1
        raise RuntimeError("CUDA error: unspecified launch failure")

    def estimate_from_landmarks(self, frame, lm):
        raise RuntimeError("CUDA error")


def test_a_deep_model_exception_drops_the_frame_instead_of_raising():
    src = gd._FeatureSource(_Geo(), _BrokenDeep())
    assert src.estimate("kare") is None
    assert src.deep_errors == 1


class _GpuDeep(_BrokenDeep):
    device = "cuda"
    closed = False

    def close(self):
        self.closed = True


class _CpuDeep:
    device = "cpu"

    def estimate_from_landmarks(self, frame, lm):
        return (0.5, 0.6)

    def estimate(self, frame):
        return (0.5, 0.6)

    def close(self):
        pass


def test_a_gpu_that_keeps_failing_moves_the_deep_model_to_the_cpu(capsys):
    """After a GPU reset every CUDA call fails: each frame returned None and
    the cursor stayed frozen until a caregiver restarted the driver."""
    gpu = _GpuDeep()
    src = gd._FeatureSource(_Geo(), gpu, rebuild_on_cpu=_CpuDeep)
    for _ in range(gd._FeatureSource.CPU_FALLBACK_AFTER):
        assert src.estimate("kare") is None
    assert gpu.closed
    assert src.estimate("kare") == (0.1, 0.2, 0.5, 0.6)   # same 11->4 shape
    assert "CPU" in capsys.readouterr().out


def test_occasional_gpu_faults_do_not_trigger_the_fallback():
    class Flaky(_GpuDeep):
        def estimate_from_landmarks(self, frame, lm):
            raise RuntimeError("CUDA")

        def estimate(self, frame):
            self.calls += 1
            if self.calls % 2:
                raise RuntimeError("CUDA")
            return (0.1, 0.1)
    built = []
    src = gd._FeatureSource(_Geo(), Flaky(),
                            rebuild_on_cpu=lambda: built.append(1) or _CpuDeep())
    for _ in range(100):
        src.estimate("kare")
    assert built == []
