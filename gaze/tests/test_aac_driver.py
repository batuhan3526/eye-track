# gaze/tests/test_aac_driver.py
"""The driver's AAC window, with OpenCV stubbed out.

AacNavigator is tested on its own, but the glue is where the two halves can
disagree: the grid asks the navigator for rectangles, maps a CURSOR position
into them, and speaks through the cache. A mistake anywhere in that chain
speaks the wrong phrase, and there is no way to see it from the screen.
"""
import json

import pytest

import gaze_driver as gd

XML_ANA = """<?xml version="1.0" encoding="utf-8"?>
<Keyboard><Name>Ana Menu</Name><Content>
  <DynamicKey><Label>ACIL</Label><ChangeKeyboard>acil</ChangeKeyboard></DynamicKey>
  <DynamicKey><Label>Su istiyorum</Label><Text>Lütfen biraz su verir misiniz?</Text>
    <Action>Speak</Action></DynamicKey>
  <DynamicKey><Label>[Ad] gelsin</Label><Text>[Ad] gelsin</Text>
    <Action>Speak</Action></DynamicKey>
</Content></Keyboard>
"""

XML_ACIL = """<?xml version="1.0" encoding="utf-8"?>
<Keyboard><Name>Acil</Name><Content>
  <DynamicKey><Label>Nefes alamıyorum</Label><Text>Nefes alamıyorum!</Text>
    <Action>Speak</Action></DynamicKey>
  <DynamicKey><Label>Geri</Label><ChangeKeyboard>ana</ChangeKeyboard></DynamicKey>
</Content></Keyboard>
"""


class FakeSpeaker:
    engine = "piper/tr"
    voice = "piper/tr"
    ok = True

    def __init__(self):
        self.spoken = []
        self.played = []

    def synth(self, text):
        return b"WAV:" + text.encode("utf-8")

    def speak(self, text):
        self.spoken.append(text)

    def play_wav(self, wav):
        self.played.append(wav)


class FakeCv2:
    """Only the calls _AacGrid makes."""
    WINDOW_NORMAL = 0
    WND_PROP_FULLSCREEN = 1
    WINDOW_FULLSCREEN = 1

    def __init__(self):
        self.shown = []
        self.destroyed = []

    def namedWindow(self, *a, **k):
        pass

    def setWindowProperty(self, *a, **k):
        pass

    def imshow(self, win, img):
        self.shown.append(img)

    def destroyWindow(self, win):
        self.destroyed.append(win)


@pytest.fixture
def grid(tmp_path, monkeypatch):
    boards = tmp_path / "aac"
    boards.mkdir()
    (boards / "ana.xml").write_text(XML_ANA, encoding="utf-8")
    (boards / "acil.xml").write_text(XML_ACIL, encoding="utf-8")
    fake = FakeCv2()
    monkeypatch.setattr(gd, "cv2", fake)
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    monkeypatch.setattr(gd, "_BASE_DIR", str(tmp_path))
    # The phrase cache lives in paths.cache_dir(), not under _BASE_DIR.
    monkeypatch.setenv("GOZFARESI_DATA_DIR", str(tmp_path))
    speaker = FakeSpeaker()
    cfg = {"aac_dir": str(boards), "aac_root": "ana", "aac_timeout": 20,
           "aac_cache": True}
    g = gd._AacGrid(1920, 1080, cfg, speaker)
    if g._cache is not None:
        g._cache.wait()
    return g, fake, speaker


def _look_at(monkeypatch, grid, index):
    """Put the gaze cursor in the middle of tile `index`."""
    x, y, w, h = grid._nav.rects(1920, 1080)[index]
    monkeypatch.setattr(gd.cursor, "get_cursor", lambda: (x + w // 2, y + h // 2))


def test_the_grid_comes_up_ready(grid):
    g, _, _ = grid
    assert g.ready is True
    assert g.active is False


def test_begin_draws_the_root_board(grid):
    g, fake, _ = grid
    g.begin()
    assert g.active is True
    assert len(fake.shown) == 1
    assert [t.label for t in g._nav.tiles()][0] == "ACIL"


def test_looking_at_a_category_navigates_and_redraws(grid, monkeypatch):
    g, fake, speaker = grid
    g.begin()
    _look_at(monkeypatch, g, 0)
    g.confirm()
    assert g.active is True, "kategori secimi izgarayi kapatmamali"
    assert len(fake.shown) == 2
    assert speaker.spoken == [] and speaker.played == []
    assert g._nav.title().startswith("Acil")


def test_looking_at_a_phrase_speaks_it_and_closes(grid, monkeypatch):
    g, fake, speaker = grid
    g.begin()
    _look_at(monkeypatch, g, 1)
    g.confirm()
    assert g.active is False
    assert speaker.played == [b"WAV:L\xc3\xbctfen biraz su verir misiniz?"
                              .decode("unicode_escape").encode("latin-1")] \
        or speaker.played == [b"WAV:" + "Lütfen biraz su verir misiniz?".encode("utf-8")]
    assert speaker.spoken == [], "onbellek varken canli sentez kullanildi"


def test_a_locked_phrase_says_nothing(grid, monkeypatch):
    g, _, speaker = grid
    g.begin()
    _look_at(monkeypatch, g, 2)
    g.confirm()
    assert speaker.spoken == [] and speaker.played == []
    assert g.active is True, "kilitli kutu izgarayi kapatti"


def test_looking_between_tiles_selects_nothing(grid, monkeypatch):
    g, fake, speaker = grid
    g.begin()
    monkeypatch.setattr(gd.cursor, "get_cursor", lambda: (0, 0))
    g.confirm()
    assert len(fake.shown) == 1, "bosluga bakmak yeniden cizdirdi"
    assert speaker.spoken == [] and speaker.played == []
    assert g.active is True


def test_the_grid_closes_itself_after_the_timeout(grid):
    g, _, _ = grid
    g.begin()
    g.tick(g._t0 + 5)
    assert g.active is True
    g.tick(g._t0 + 21)
    assert g.active is False


def test_navigating_restarts_the_timeout(grid, monkeypatch):
    """Otherwise the clock keeps running while the user reads a sub-board and
    the grid vanishes mid-choice."""
    g, _, _ = grid
    g.begin()
    start = g._t0
    _look_at(monkeypatch, g, 0)
    monkeypatch.setattr(gd.time, "monotonic", lambda: start + 15)
    g.confirm()                      # 15 s in, the user opens a sub-board
    g.tick(start + 30)
    assert g.active is True, "gezinmeden sonra eski sayac hala isliyor"
    g.tick(start + 36)               # 21 s after the last thing they did
    assert g.active is False


def test_confirm_before_begin_is_harmless(grid):
    g, _, speaker = grid
    g.confirm()
    assert speaker.spoken == [] and speaker.played == []


def test_a_missing_board_directory_disables_the_grid(tmp_path, monkeypatch,
                                                     capsys):
    monkeypatch.setattr(gd, "cv2", FakeCv2())
    g = gd._AacGrid(1920, 1080, {"aac_dir": str(tmp_path / "yok")}, None)
    assert g.ready is False
    g.begin()
    assert g.active is False
    assert "cumle dosyasi bulunamadi" in capsys.readouterr().out


def test_an_unknown_root_falls_back_and_says_so(tmp_path, monkeypatch, capsys):
    boards = tmp_path / "aac"
    boards.mkdir()
    (boards / "ana.xml").write_text(XML_ANA, encoding="utf-8")
    monkeypatch.setattr(gd, "cv2", FakeCv2())
    monkeypatch.setattr(gd, "_BASE_DIR", str(tmp_path))
    g = gd._AacGrid(1920, 1080,
                    {"aac_dir": str(boards), "aac_root": "olmayan",
                     "aac_cache": False}, None)
    assert g.ready is True
    out = capsys.readouterr().out
    assert "olmayan" in out and "aac_root" in out


def test_the_cache_is_warmed_for_every_speakable_phrase(grid, tmp_path):
    g, _, _ = grid
    wavs = list((tmp_path / "cache" / "aac").glob("*.wav"))
    # "Lütfen biraz su verir misiniz?" + "Nefes alamıyorum!" - and NOT the
    # locked "[Ad] gelsin", which must never be spoken.
    assert len(wavs) == 2, [p.name for p in wavs]


def test_the_phrase_cache_lives_in_paths_cache_dir(tmp_path, monkeypatch):
    # Installed Linux keeps the cache in XDG_DATA, apart from the settings
    # folder (_BASE_DIR) - so the grid must ask paths, not join _BASE_DIR.
    boards = tmp_path / "aac"
    boards.mkdir()
    (boards / "ana.xml").write_text(XML_ANA, encoding="utf-8")
    monkeypatch.setattr(gd, "cv2", FakeCv2())
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    monkeypatch.setattr(gd, "_BASE_DIR", str(tmp_path / "veri"))
    monkeypatch.setattr(gd.paths, "cache_dir",
                        lambda: str(tmp_path / "onbellek"))
    g = gd._AacGrid(1920, 1080, {"aac_dir": str(boards), "aac_root": "ana",
                                 "aac_cache": True}, FakeSpeaker())
    g._cache.wait()
    assert list((tmp_path / "onbellek" / "aac").glob("*.wav"))
    assert not (tmp_path / "veri").exists()


def test_speaking_works_without_a_cache(tmp_path, monkeypatch):
    boards = tmp_path / "aac"
    boards.mkdir()
    (boards / "ana.xml").write_text(XML_ANA, encoding="utf-8")
    monkeypatch.setattr(gd, "cv2", FakeCv2())
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    monkeypatch.setattr(gd, "_BASE_DIR", str(tmp_path))
    # The phrase cache lives in paths.cache_dir(), not under _BASE_DIR.
    monkeypatch.setenv("GOZFARESI_DATA_DIR", str(tmp_path))
    speaker = FakeSpeaker()
    g = gd._AacGrid(1920, 1080, {"aac_dir": str(boards), "aac_root": "ana",
                                 "aac_cache": False}, speaker)
    g.begin()
    x, y, w, h = g._nav.rects(1920, 1080)[1]
    monkeypatch.setattr(gd.cursor, "get_cursor", lambda: (x + w // 2, y + h // 2))
    g.confirm()
    assert speaker.spoken == ["Lütfen biraz su verir misiniz?"]


def test_the_grid_never_leaves_its_window_open(grid, monkeypatch):
    g, fake, _ = grid
    g.begin()
    _look_at(monkeypatch, g, 1)
    g.confirm()
    assert fake.destroyed == ["AAC - cumleye bakip tiklayin"]
    g.cancel()
    assert fake.destroyed == ["AAC - cumleye bakip tiklayin"], "iki kez kapatildi"


# --- an AAC grid with no voice is a grid that does nothing -----------------
#
# The Speaker is built before the grid and can come back as None (no piper, no
# Tolga, nothing). The grid still opened, still drew, still closed on a
# selection - and said nothing at all. For a user whose only way to speak this
# is, a silent success is the worst possible outcome.


def test_a_grid_without_a_voice_says_so_at_startup(tmp_path, monkeypatch, capsys):
    boards = tmp_path / "aac"
    boards.mkdir()
    (boards / "ana.xml").write_text(XML_ANA, encoding="utf-8")
    monkeypatch.setattr(gd, "cv2", FakeCq := FakeCv2())
    monkeypatch.setattr(gd, "_BASE_DIR", str(tmp_path))
    gd._AacGrid(1920, 1080, {"aac_dir": str(boards), "aac_root": "ana"}, None)
    out = capsys.readouterr().out
    assert "SES YOK" in out, out


def test_choosing_a_phrase_without_a_voice_is_not_silent(tmp_path, monkeypatch,
                                                         capsys):
    boards = tmp_path / "aac"
    boards.mkdir()
    (boards / "ana.xml").write_text(XML_ANA, encoding="utf-8")
    monkeypatch.setattr(gd, "cv2", FakeCv2())
    monkeypatch.setattr(gd, "_force_topmost", lambda *a, **k: None)
    monkeypatch.setattr(gd, "_BASE_DIR", str(tmp_path))
    g = gd._AacGrid(1920, 1080, {"aac_dir": str(boards), "aac_root": "ana"}, None)
    g.begin()
    x, y, w, h = g._nav.rects(1920, 1080)[1]
    monkeypatch.setattr(gd.cursor, "get_cursor", lambda: (x + w // 2, y + h // 2))
    capsys.readouterr()
    g.confirm()
    out = capsys.readouterr().out
    assert "SESLENDIRILEMEDI" in out, out


def test_a_grid_with_a_voice_does_not_warn(tmp_path, monkeypatch, capsys):
    """Non-vacuity: the warning must not be printed for every startup."""
    boards = tmp_path / "aac"
    boards.mkdir()
    (boards / "ana.xml").write_text(XML_ANA, encoding="utf-8")
    monkeypatch.setattr(gd, "cv2", FakeCv2())
    monkeypatch.setattr(gd, "_BASE_DIR", str(tmp_path))
    gd._AacGrid(1920, 1080,
                {"aac_dir": str(boards), "aac_root": "ana", "aac_cache": False},
                FakeSpeaker())
    assert "SES YOK" not in capsys.readouterr().out


def test_tick_reports_that_the_grid_closed_itself(grid):
    """The driver mutes the switch for a moment after an automatic close:
    the press that follows was meant for a phrase, not for the app behind."""
    g, _, _ = grid
    g.begin()
    assert not g.tick(g._t0 + 5)
    assert g.tick(g._t0 + 21) is True
    assert g.tick(g._t0 + 22) is False      # already closed: not reported twice


def test_looking_at_another_tile_restarts_the_timeout(grid, monkeypatch):
    """A slow reader moving from tile to tile is not idle; the grid used to
    vanish 20 s after opening however actively they were reading."""
    g, _, _ = grid
    g.begin()
    start = g._t0
    _look_at(monkeypatch, g, 0)
    g.tick(start + 15)
    _look_at(monkeypatch, g, 1)
    g.tick(start + 18)                  # gaze moved to another phrase
    g.tick(start + 30)
    assert g.active is True
    g.tick(start + 39)                  # 21 s with the gaze on one tile
    assert g.active is False


def test_a_grid_that_cannot_paint_closes_instead_of_freezing(grid, monkeypatch):
    """A paint raising every frame left the fullscreen topmost board up for
    good, and the exception skipped the switch routing behind it."""
    import time as _t
    g, _, _ = grid
    g.begin()

    def boom(now=None):
        raise RuntimeError("cizim")
    monkeypatch.setattr(g, "_paint", boom)
    assert g.tick(_t.time()) is True
    assert g.active is False
