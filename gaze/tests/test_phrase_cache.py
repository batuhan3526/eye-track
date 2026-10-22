# gaze/tests/test_phrase_cache.py
"""Pre-rendered AAC phrases.

An AAC user asking for help must not wait for a neural vocoder. The phrases are
fixed, so they are synthesised once and replayed from disk.
"""
import os
import time

from eye_gaze.phrase_cache import PhraseCache, cache_key


class FakeSpeaker:
    def __init__(self):
        self.engine = "piper/tr_TR-dfki-medium"
        self.voice = self.engine
        self.ok = True
        self.synthed = []
        self.spoken = []
        self.played = []

    def synth(self, text):
        self.synthed.append(text)
        return b"WAV:" + text.encode("utf-8")

    def speak(self, text):
        self.spoken.append(text)

    def play_wav(self, wav):
        self.played.append(wav)


def test_key_is_stable():
    assert cache_key("piper", "tr", "Su istiyorum") == \
        cache_key("piper", "tr", "Su istiyorum")


def test_key_changes_with_engine_voice_and_text():
    base = cache_key("piper", "tr", "Su istiyorum")
    assert cache_key("onecore", "tr", "Su istiyorum") != base
    assert cache_key("piper", "en", "Su istiyorum") != base
    assert cache_key("piper", "tr", "Su istemiyorum") != base


def test_key_is_filesystem_safe_for_turkish_text():
    k = cache_key("piper", "tr", "Ağrım var, İlaç istiyorum")
    assert k.isalnum() and len(k) == 40


def test_warm_then_speak_uses_cache(tmp_path):
    sp = FakeSpeaker()
    c = PhraseCache(sp, str(tmp_path))
    c.warm(["Su istiyorum", "Ağrım var"])
    c.wait()
    assert sorted(sp.synthed) == sorted(["Su istiyorum", "Ağrım var"])
    assert c.speak("Su istiyorum") is True
    assert sp.played == [b"WAV:Su istiyorum"]
    assert sp.spoken == []


def test_speak_falls_back_to_live_synthesis_when_cold(tmp_path):
    sp = FakeSpeaker()
    c = PhraseCache(sp, str(tmp_path))
    assert c.speak("Hic isitilmadi") is False
    assert sp.spoken == ["Hic isitilmadi"]


def test_cache_survives_a_new_instance(tmp_path):
    sp1 = FakeSpeaker()
    c1 = PhraseCache(sp1, str(tmp_path))
    c1.warm(["Su istiyorum"])
    c1.wait()                       # ayni ornekte bekle, yoksa yaris olur
    sp2 = FakeSpeaker()
    c2 = PhraseCache(sp2, str(tmp_path))
    assert c2.speak("Su istiyorum") is True
    assert sp2.synthed == []          # diskten okundu, yeniden sentez yok


def test_warm_skips_already_cached(tmp_path):
    sp = FakeSpeaker()
    c = PhraseCache(sp, str(tmp_path))
    c.warm(["Su istiyorum"])
    c.wait()
    c.warm(["Su istiyorum"])
    c.wait()
    assert sp.synthed == ["Su istiyorum"]


def test_warm_ignores_empty_and_duplicate_texts(tmp_path):
    sp = FakeSpeaker()
    c = PhraseCache(sp, str(tmp_path))
    c.warm(["", "  ", "Selam", "Selam"])
    c.wait()
    assert sp.synthed == ["Selam"]


def test_unwritable_cache_dir_does_not_raise():
    sp = FakeSpeaker()
    c = PhraseCache(sp, "Z:/olmayan/yol")
    c.warm(["Selam"])
    c.wait()
    assert c.speak("Selam") is False     # canliya duser, cokmez
    assert sp.spoken == ["Selam"]


# --- a half-written WAV would be permanent ---------------------------------
#
# warm() skips anything that already exists. A cache file truncated by a crash
# or a full disk is therefore never repaired: that phrase makes a broken noise
# for the rest of the machine's life, and nothing ever tries to synthesise it
# again.


def test_an_interrupted_write_leaves_no_cache_file(tmp_path, monkeypatch):
    import eye_gaze.phrase_cache as pc
    sp = FakeSpeaker()
    c = PhraseCache(sp, str(tmp_path))
    monkeypatch.setattr(pc.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("dolu")))
    c.warm(["Su istiyorum"])
    c.wait()
    assert list(tmp_path.iterdir()) == [], "yarim/gecici dosya kaldi"
    assert c.speak("Su istiyorum") is False    # yeniden denenebilir durumda


def test_a_failed_synthesis_leaves_nothing_behind(tmp_path):
    class Broken(FakeSpeaker):
        def synth(self, text):
            raise RuntimeError("ses uretilemedi")

    c = PhraseCache(Broken(), str(tmp_path))
    c.warm(["Su istiyorum"])
    c.wait()
    assert list(tmp_path.iterdir()) == []


def test_an_empty_synthesis_result_is_not_cached(tmp_path):
    class Silent(FakeSpeaker):
        def synth(self, text):
            return b""

    c = PhraseCache(Silent(), str(tmp_path))
    c.warm(["Su istiyorum"])
    c.wait()
    assert list(tmp_path.iterdir()) == []


# --- the warmer must not take the voice away from the user -----------------
#
# Task 4 measured it: piper serialises on a single lock. A warm loop working
# through forty-eight phrases can keep re-taking that lock, and the sentence
# the user just asked for queues behind it. The one thing this cache exists to
# prevent is exactly that wait.


def test_the_warmer_stands_aside_after_the_user_speaks(tmp_path):
    c = PhraseCache(FakeSpeaker(), str(tmp_path), defer_seconds=0.20)
    c.speak("canli bir cumle")            # cold -> live synthesis
    t0 = time.monotonic()
    c._yield_to_user()
    assert time.monotonic() - t0 >= 0.15


def test_the_warmer_does_not_wait_when_nobody_is_speaking(tmp_path):
    """Non-vacuity: otherwise warming a board would take a second per phrase."""
    c = PhraseCache(FakeSpeaker(), str(tmp_path), defer_seconds=5.0)
    t0 = time.monotonic()
    c._yield_to_user()
    assert time.monotonic() - t0 < 0.05


def test_a_cached_phrase_also_makes_the_warmer_stand_aside(tmp_path):
    """Playback and synthesis compete for the same audio device."""
    sp = FakeSpeaker()
    c = PhraseCache(sp, str(tmp_path), defer_seconds=0.20)
    c.warm(["Selam"])
    c.wait()
    assert c.speak("Selam") is True
    t0 = time.monotonic()
    c._yield_to_user()
    assert time.monotonic() - t0 >= 0.15


def test_a_second_warm_does_not_orphan_the_first(tmp_path):
    """wait() has to cover everything still running, or shutdown cuts a
    half-written phrase off."""
    sp = FakeSpeaker()
    c = PhraseCache(sp, str(tmp_path))
    c.warm(["bir"])
    c.warm(["iki"])
    c.wait()
    assert sorted(sp.synthed) == ["bir", "iki"]
    assert len(list(tmp_path.iterdir())) == 2


def test_cache_dir_is_created(tmp_path):
    d = tmp_path / "yeni" / "klasor"
    c = PhraseCache(FakeSpeaker(), str(d))
    c.warm(["Selam"])
    c.wait()
    assert os.path.isdir(str(d))


def test_the_warmer_also_waits_for_speech_the_speaker_already_has_queued(tmp_path):
    """F4 during warm-up goes straight to Speaker.speak(), not through this
    cache - so _last_user never moves and the warmer keeps taking piper's lock
    in front of the sentence the user just asked for."""
    class Busy(FakeSpeaker):
        pending = 1

    c = PhraseCache(Busy(), str(tmp_path), defer_seconds=0.20)
    t0 = time.monotonic()
    c._yield_to_user()
    assert time.monotonic() - t0 >= 0.15


def test_an_idle_speaker_does_not_hold_the_warmer_up(tmp_path):
    class Idle(FakeSpeaker):
        pending = 0

    c = PhraseCache(Idle(), str(tmp_path), defer_seconds=5.0)
    t0 = time.monotonic()
    c._yield_to_user()
    assert time.monotonic() - t0 < 0.05


def test_a_permanently_busy_speaker_does_not_stall_warming_for_ever(tmp_path):
    """The wait is a courtesy, not a lock: a Speaker stuck with a queued item
    must not mean the phrases are never prepared at all."""
    class Stuck(FakeSpeaker):
        pending = 1

    c = PhraseCache(Stuck(), str(tmp_path), defer_seconds=0.05)
    t0 = time.monotonic()
    c._yield_to_user()
    assert time.monotonic() - t0 < 3.0


def test_orphan_temp_files_are_cleared_when_warming_starts(tmp_path):
    """Killing the driver mid-warm leaves the .tmp behind - every startup after
    an early quit adds one more, for ever."""
    (tmp_path / "eski.wav.tmp").write_bytes(b"yarim")
    c = PhraseCache(FakeSpeaker(), str(tmp_path))
    c.warm(["Selam"])
    c.wait()
    assert not (tmp_path / "eski.wav.tmp").exists()
    assert len(list(tmp_path.glob("*.wav"))) == 1


def test_a_second_warm_does_not_delete_a_phrase_being_written(tmp_path,
                                                              monkeypatch):
    """The orphan sweep must not take the .tmp a running warm is writing.

    Found when the suite first ran on Linux: there unlink() of an open file
    succeeds, so the second warm() swept the first one's half-written .tmp,
    the os.replace() after it failed, and that phrase was silently never
    cached. Windows hid it only because deleting an open file fails there.
    The first write is held open inside fsync() while the second warm runs.
    """
    import threading
    from eye_gaze import phrase_cache as pc

    inside, release = threading.Event(), threading.Event()
    real_fsync = pc.os.fsync
    calls = []

    def slow_fsync(fd):
        calls.append(fd)
        if len(calls) == 1:
            inside.set()
            release.wait(10)
        real_fsync(fd)

    monkeypatch.setattr(pc.os, "fsync", slow_fsync)
    sp = FakeSpeaker()
    c = PhraseCache(sp, str(tmp_path))
    c.warm(["bir"])
    assert inside.wait(10), "first write never started"
    assert list(tmp_path.glob("*.tmp")), "first write has no .tmp open"
    c.warm(["iki"])
    release.set()
    c.wait()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [os.path.basename(c._path("bir")), os.path.basename(c._path("iki"))])


# --- the voice-clone reference is part of the voice ------------------------
#
# xtts names every clone "xtts/klon", so re-recording tts_speaker_wav (or
# pointing it at another file) left the AAC phrases in the OLD voice.


def _key_for(sp, tmp_path):
    c = PhraseCache(sp, str(tmp_path))
    return os.path.basename(c._path("Su istiyorum"))


def test_the_speaker_reference_path_changes_the_key(tmp_path):
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    a.write_bytes(b"RIFF")
    b.write_bytes(b"RIFF")
    sp = FakeSpeaker()
    sp.speaker_wav = str(a)
    ka = _key_for(sp, tmp_path)
    sp.speaker_wav = str(b)
    assert _key_for(sp, tmp_path) != ka


def test_re_recording_the_reference_changes_the_key(tmp_path):
    ref = tmp_path / "ben.wav"
    ref.write_bytes(b"RIFF")
    os.utime(ref, (1_000_000, 1_000_000))
    sp = FakeSpeaker()
    sp.speaker_wav = str(ref)
    before = _key_for(sp, tmp_path)
    os.utime(ref, (2_000_000, 2_000_000))     # same path, new recording
    assert _key_for(sp, tmp_path) != before


def test_no_reference_keeps_the_existing_keys(tmp_path):
    """Non-vacuity and compatibility: caches already on disk stay valid."""
    sp = FakeSpeaker()
    sp.speaker_wav = ""
    want = cache_key(sp.engine, sp.voice, "Su istiyorum") + ".wav"
    assert _key_for(sp, tmp_path) == want


def test_a_missing_reference_file_does_not_raise(tmp_path):
    sp = FakeSpeaker()
    sp.speaker_wav = str(tmp_path / "yok.wav")
    c = PhraseCache(sp, str(tmp_path))
    assert c.speak("Su istiyorum") is False      # cold cache, live synthesis
    assert sp.spoken == ["Su istiyorum"]
