# gaze/tests/test_stt_silence.py
"""End-of-speech detection.

This user cannot comfortably press a key twice, so recording has to end by
itself when they stop talking. Everything here is pure arithmetic over audio
frames - no microphone, no model - so it can be tested exactly.
"""
import numpy as np

from eye_gaze.stt import SilenceDetector, rms


def _f(level, n=1600):
    """One 0.1 s frame at 16 kHz with the given amplitude."""
    return np.full(n, level, dtype=np.float32)


def test_rms_of_silence_is_zero():
    assert rms(_f(0.0)) == 0.0


def test_rms_of_constant_signal():
    assert abs(rms(_f(0.5)) - 0.5) < 1e-6


def test_rms_of_an_empty_frame_is_zero():
    """A dropped buffer must not raise inside the audio callback."""
    assert rms(np.zeros(0, dtype=np.float32)) == 0.0


def test_no_stop_before_user_speaks():
    """Kullanici konusmaya baslamadan sessizlik kaydi bitirmemeli."""
    d = SilenceDetector(silence_seconds=0.3, frame_seconds=0.1, calib_frames=3)
    for _ in range(50):
        assert d.update(_f(0.001)) is False


def test_stops_after_speech_then_silence():
    d = SilenceDetector(silence_seconds=0.3, frame_seconds=0.1, calib_frames=3)
    for _ in range(3):
        d.update(_f(0.001))          # gurultu tabani olculur
    for _ in range(5):
        assert d.update(_f(0.4)) is False   # konusuyor
    assert d.update(_f(0.001)) is False     # 0.1 s sessiz
    assert d.update(_f(0.001)) is False     # 0.2 s
    assert d.update(_f(0.001)) is True      # 0.3 s -> bitti


def test_pause_inside_a_sentence_does_not_stop():
    d = SilenceDetector(silence_seconds=0.3, frame_seconds=0.1, calib_frames=3)
    for _ in range(3):
        d.update(_f(0.001))
    d.update(_f(0.4))
    d.update(_f(0.001))
    d.update(_f(0.001))
    d.update(_f(0.4))                        # kelime arasi duraklama bitti
    assert d.update(_f(0.001)) is False      # sayac sifirlandi
    assert d.update(_f(0.001)) is False


def test_threshold_adapts_to_noisy_room():
    quiet = SilenceDetector(calib_frames=2, frame_seconds=0.1)
    quiet.update(_f(0.001))
    quiet.update(_f(0.001))
    noisy = SilenceDetector(calib_frames=2, frame_seconds=0.1)
    noisy.update(_f(0.05))
    noisy.update(_f(0.05))
    assert noisy.threshold > quiet.threshold


def test_threshold_never_below_floor():
    d = SilenceDetector(calib_frames=2, frame_seconds=0.1, floor=0.01)
    d.update(_f(0.0))
    d.update(_f(0.0))
    assert d.threshold >= 0.01


def test_reset_clears_state():
    d = SilenceDetector(silence_seconds=0.2, frame_seconds=0.1, calib_frames=1)
    d.update(_f(0.001))
    d.update(_f(0.4))
    d.reset()
    assert d.spoke is False
    assert d.threshold is None


# --- the room can turn out quieter than the first frames claimed ------------
#
# The calibration frames are whatever the microphone hears in the first 0.3 s
# after the key is pressed. Someone who starts talking straight away - which is
# exactly what an impatient user does - has their own VOICE measured as the
# noise floor. The threshold then sits above anything they can produce, no
# frame ever counts as speech, and the recording can only end on its time cap.


def test_a_room_that_turns_out_quieter_lowers_the_threshold():
    d = SilenceDetector(silence_seconds=0.2, frame_seconds=0.1, calib_frames=2)
    d.update(_f(0.4))
    d.update(_f(0.4))
    assert d.threshold > 0.4, "konusma gurultu tabani sayilmadi (test bayat)"
    for _ in range(3):
        d.update(_f(0.002))          # they pause; the real room is heard
    assert d.threshold < 0.4
    assert d.update(_f(0.4)) is False        # speech is recognised again
    d.update(_f(0.002))
    assert d.update(_f(0.002)) is True       # and the silence now ends it


def test_a_genuinely_noisy_room_keeps_its_high_threshold():
    """Non-vacuity: the floor may only ever be lowered by what is actually
    heard, or a noisy room would end every recording instantly."""
    d = SilenceDetector(silence_seconds=0.2, frame_seconds=0.1, calib_frames=2,
                        floor=0.001)
    d.update(_f(0.05))
    d.update(_f(0.05))
    before = d.threshold
    for _ in range(10):
        assert d.update(_f(0.05)) is False   # steady room noise, no speech
    assert d.threshold == before


# --- a digital-zero frame is not the room -----------------------------------
#
# Stream start and AGC dips deliver frames of pure digital silence. Treating
# one as "the room turned out quieter" dropped the threshold to the floor for
# the rest of the recording; with a fan or a BiPAP running, every frame then
# counted as speech and the recording could only end on its 30 s cap.


def test_a_digital_zero_frame_does_not_collapse_the_threshold():
    d = SilenceDetector(silence_seconds=0.2, frame_seconds=0.1, calib_frames=2)
    d.update(_f(0.05))                  # fan / BiPAP noise floor
    d.update(_f(0.05))
    before = d.threshold
    d.update(_f(0.0))                   # one dropped / zeroed buffer
    assert d.threshold == before
    for _ in range(10):
        d.update(_f(0.05))
    assert d.spoke is False, "fan gurultusu konusma sayildi"


def test_a_real_quiet_frame_still_lowers_the_threshold():
    """Non-vacuity: only near-zero frames are ignored, a genuinely quieter
    room must still be learned."""
    d = SilenceDetector(silence_seconds=0.2, frame_seconds=0.1, calib_frames=2)
    d.update(_f(0.05))
    d.update(_f(0.05))
    before = d.threshold
    d.update(_f(0.003))
    assert d.threshold < before
