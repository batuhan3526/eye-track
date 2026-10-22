# gaze/tests/test_tts_selection.py
"""F4: copy the selection, read it back, speak it - without ever speaking an
OLD sentence. When nothing is selected, Ctrl+C leaves the clipboard alone and
the old code read out whatever was there (usually the last dictation): a
wrong sentence said to a caregiver by someone who cannot easily correct it."""
import gaze_driver as gd


class Clip:
    def __init__(self, text="eski cumle", seq=7, copies_text=None):
        self.text = text
        self.seq = seq
        self.copies_text = copies_text
        self.copied = 0

    def copy(self):
        self.copied += 1
        if self.copies_text is not None:
            self.text = self.copies_text
            self.seq += 1

    def sequence(self):
        return self.seq

    def read(self):
        return self.text


def _sel(clip, ours=False):
    return gd._selection_text(ours, clip.copy, clip.sequence, clip.read,
                              wait=0.0, poll=0.0)


def test_a_fresh_selection_is_read():
    clip = Clip(copies_text="yeni secim")
    text, note = _sel(clip)
    assert text == "yeni secim" and note == ""


def test_nothing_copied_means_nothing_spoken():
    clip = Clip()                                   # copy() changes nothing
    text, note = _sel(clip)
    assert text is None
    assert "SECIN" in note


def test_our_own_window_in_front_speaks_nothing():
    # Ctrl+C must not go to our console (it kills the driver), and without a
    # copy the clipboard can only be stale.
    clip = Clip(copies_text="x")
    text, note = _sel(clip, ours=True)
    assert text is None and clip.copied == 0
    assert note


def test_unknown_sequence_number_falls_back_to_reading():
    clip = Clip(copies_text="secim")
    text, _ = gd._selection_text(False, clip.copy, lambda: None, clip.read,
                                 wait=0.0, poll=0.0)
    assert text == "secim"


def test_a_copy_that_lands_late_is_still_seen():
    clip = Clip()
    calls = {"n": 0}

    def seq():
        calls["n"] += 1
        return 7 if calls["n"] < 3 else 8          # changes on a later poll
    clip.text = "gec gelen"
    text, _ = gd._selection_text(False, clip.copy, seq, clip.read,
                                 wait=0.0, poll=0.0)
    assert text == "gec gelen"
