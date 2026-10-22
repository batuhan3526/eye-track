# gaze/tests/test_stt_driver.py
"""What the dictation key does, decided outside the gaze loop so it can be
tested at all.

The ordering of these branches is the whole point: a press that arrives while
the previous recording is still being transcribed must not be read as "stop",
and a press while recording must not open a second microphone.
"""
import gaze_driver as gd


class FakeRecorder:
    def __init__(self, active=False):
        self.active = active


def test_no_recorder_falls_back_to_windows_dictation():
    assert gd._stt_action(None, busy=False) == "winh"


def test_a_press_while_transcribing_is_refused():
    assert gd._stt_action(FakeRecorder(active=False), busy=True) == "wait"


def test_a_press_while_transcribing_is_refused_even_if_still_recording():
    """busy is checked BEFORE active: otherwise this would try to stop a
    recorder that has already handed its audio over."""
    assert gd._stt_action(FakeRecorder(active=True), busy=True) == "wait"


def test_a_press_while_recording_stops_it():
    assert gd._stt_action(FakeRecorder(active=True), busy=False) == "stop"


def test_a_press_at_rest_starts_recording():
    assert gd._stt_action(FakeRecorder(active=False), busy=False) == "start"


# --- a recording that dies must not die quietly ----------------------------
#
# poll() closes the stream and returns whatever it had when the device fails -
# an unplugged USB microphone, a driver reset. If it had nothing, it returns
# None, active goes False, and the loop simply stops polling. The user is still
# waiting to be transcribed, and the console says nothing at all.


class _DeadRecorder:
    def __init__(self):
        self.active = True

    def poll(self):
        self.active = False
        return None


class _LiveRecorder:
    active = True

    def poll(self):
        return None


def test_a_recording_that_ends_with_no_audio_is_reported():
    r = _DeadRecorder()
    audio, note = gd._stt_poll(r)
    assert audio is None
    assert note and "mikrofon" in note.lower(), note


def test_a_normal_poll_says_nothing():
    """Non-vacuity: this runs once per video frame."""
    assert gd._stt_poll(_LiveRecorder()) == (None, "")


def test_a_finished_recording_returns_its_audio():
    class Done:
        active = True

        def poll(self):
            self.active = False
            return b"ses"

    audio, note = gd._stt_poll(Done())
    assert audio == b"ses" and note == ""


# --- nothing in one frame may take the cursor down -------------------------
#
# This happened for real: get_clipboard_text() raised an access violation while
# handling F4, the exception propagated out of main(), and the driver closed.
# Settings, speech and dictation are all survivable losses. The cursor is this
# user's only way to operate the machine - it is not.


def test_the_first_frame_error_is_reported(capsys):
    state = {}
    assert gd._frame_error(state, RuntimeError("patladi")) is True
    out = capsys.readouterr().out
    assert "KARE HATASI" in out and "RuntimeError" in out, out
    assert "DEVAM" in out, "surucunun devam ettigi soylenmiyor"
    assert "Imlec CALISMAYA" not in out, (
        "imlecin yasadigi SOZ VERILIYOR - oysa bu, hatanin karenin neresinde"
        " oldugua bagli ve her zaman dogru degil")


def test_the_same_error_is_not_reported_again(capsys):
    """It can recur every frame at 30 fps; reprinting buries everything else."""
    state = {}
    gd._frame_error(state, RuntimeError("patladi"))
    capsys.readouterr()
    for _ in range(50):
        assert gd._frame_error(state, RuntimeError("patladi")) is False
    assert capsys.readouterr().out == ""


def test_a_different_error_is_reported(capsys):
    """Non-vacuity: the first message must not silence every later fault."""
    state = {}
    gd._frame_error(state, RuntimeError("bir"))
    capsys.readouterr()
    assert gd._frame_error(state, OSError("baska")) is True
    assert "OSError" in capsys.readouterr().out


def test_every_error_is_counted_even_when_not_printed():
    state = {}
    for _ in range(7):
        gd._frame_error(state, RuntimeError("ayni"))
    assert state["n"] == 7


def test_a_non_ascii_error_message_does_not_break_the_report(capsys):
    """The console is cp1254. A UnicodeEncodeError raised HERE would do exactly
    what this guard exists to prevent."""
    state = {}
    gd._frame_error(state, RuntimeError("Ağrım var — şğüöçı"))
    assert "KARE HATASI" in capsys.readouterr().out


def test_a_fault_that_never_stops_is_escalated(capsys):
    """One quiet line at the start would leave the user staring at a driver
    that is alive and useless: everything after the fault - possibly the cursor
    update itself - never runs again."""
    state = {}
    for _ in range(gd._STUCK_AFTER + 1):
        gd._frame_error(state, RuntimeError("hep ayni"))
    out = capsys.readouterr().out
    assert "TAKILMIS olabilir" in out, out
    assert "yeniden baslatin" in out, out


def test_the_escalation_is_printed_only_once(capsys):
    state = {}
    for _ in range(gd._STUCK_AFTER * 3):
        gd._frame_error(state, RuntimeError("hep ayni"))
    assert capsys.readouterr().out.count("TAKILMIS") == 1


def test_a_recovering_fault_is_not_escalated(capsys):
    """Non-vacuity: an intermittent error must not raise the alarm."""
    state = {}
    for _ in range(gd._STUCK_AFTER * 2):
        gd._frame_error(state, RuntimeError("bir"))
        gd._frame_error(state, OSError("iki"))
    assert "TAKILMIS" not in capsys.readouterr().out


def test_a_recording_with_no_speech_says_so_not_microphone():
    """Recorder now refuses to hand silence to Whisper (it invents sentences
    from it). The user must hear 'nothing was heard', not be sent off to
    check a microphone that works."""
    class Silent:
        active = True
        no_speech = False

        def poll(self):
            self.active = False
            self.no_speech = True
            return None

    audio, note = gd._stt_poll(Silent())
    assert audio is None
    assert "duyulmadi" in note.lower() and "mikrofon" not in note.lower()


def test_manual_stop_message_distinguishes_silence():
    class R:
        no_speech = True
    assert "duyulmadi" in gd._stt_empty_note(R()).lower()

    class R2:
        no_speech = False
    assert "hicbir ses alinmadi" in gd._stt_empty_note(R2()).lower()
