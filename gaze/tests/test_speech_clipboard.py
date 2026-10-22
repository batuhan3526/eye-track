# gaze/tests/test_speech_clipboard.py
"""Clipboard write: no leaked global memory on any failure path, a retry when
another program holds the clipboard, and the sequence number the F4 handler
uses to tell "Ctrl+C copied something" from "the clipboard is stale".
"""
import ctypes
import sys

import pytest

import eye_gaze.speech as sp

# Tests that talk to the REAL Win32 clipboard; the fake-backed ones run
# everywhere.
win32_clipboard = pytest.mark.skipif(
    sys.platform != "win32", reason="Win32 clipboard (user32/kernel32)")


class Kernel:
    """GlobalAlloc/Lock hand out a real buffer, so memmove stays in bounds."""

    def __init__(self, lock_ok=True):
        self.lock_ok = lock_ok
        self.freed = []
        self._bufs = []

    def GlobalAlloc(self, flags, n):
        buf = ctypes.create_string_buffer(n)
        self._bufs.append(buf)
        return 0xABC0 + len(self._bufs)

    def GlobalLock(self, h):
        return ctypes.addressof(self._bufs[-1]) if self.lock_ok else 0

    def GlobalUnlock(self, h):
        return 1

    def GlobalFree(self, h):
        self.freed.append(getattr(h, "value", h))
        return None


class User:
    def __init__(self, open_fails=0, set_ok=True):
        self.open_fails = open_fails
        self.opens = 0
        self.set_ok = set_ok
        self.closed = 0

    def OpenClipboard(self, h):
        self.opens += 1
        return 0 if self.opens <= self.open_fails else 1

    def CloseClipboard(self):
        self.closed += 1
        return 1

    def EmptyClipboard(self):
        return 1

    def SetClipboardData(self, fmt, h):
        return h if self.set_ok else None


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)


def _with(monkeypatch, u, k):
    monkeypatch.setattr(sp, "_win32", lambda: (u, k))


def test_a_failed_lock_frees_the_block(monkeypatch):
    u, k = User(), Kernel(lock_ok=False)
    _with(monkeypatch, u, k)
    assert sp.set_clipboard_text("Merhaba") is False
    assert k.freed == [0xABC1]


def test_a_clipboard_that_stays_held_frees_the_block(monkeypatch):
    u, k = User(open_fails=10_000), Kernel()
    _with(monkeypatch, u, k)
    assert sp.set_clipboard_text("Merhaba") is False
    assert k.freed == [0xABC1]


def test_a_refused_set_frees_the_block(monkeypatch):
    u, k = User(set_ok=False), Kernel()
    _with(monkeypatch, u, k)
    assert sp.set_clipboard_text("Merhaba") is False
    assert k.freed == [0xABC1]
    assert u.closed == 1


def test_a_successful_set_does_not_free_what_windows_now_owns(monkeypatch):
    """Non-vacuity: freeing after SetClipboardData succeeded would leave the
    clipboard pointing at released memory."""
    u, k = User(), Kernel()
    _with(monkeypatch, u, k)
    assert sp.set_clipboard_text("Merhaba") is True
    assert k.freed == []
    assert u.closed == 1


def test_a_briefly_held_clipboard_is_retried(monkeypatch):
    """Clipboard history / cloud clipboard hold it for a moment after every
    write - the same reason the read side retries."""
    u, k = User(open_fails=2), Kernel()
    _with(monkeypatch, u, k)
    assert sp.set_clipboard_text("Merhaba") is True
    assert u.opens == 3
    assert k.freed == []


@win32_clipboard
def test_global_free_prototype_is_declared():
    u, k = sp._win32()
    assert u is not None
    assert k.GlobalFree.argtypes == [ctypes.c_void_p]
    assert k.GlobalFree.restype is ctypes.c_void_p


# --- clipboard_sequence() ---------------------------------------------------


@win32_clipboard
def test_clipboard_sequence_is_a_number_on_this_machine():
    seq = sp.clipboard_sequence()
    assert isinstance(seq, int) and seq >= 0


@win32_clipboard
def test_clipboard_sequence_advances_on_a_write():
    before = sp.clipboard_sequence()
    assert sp.set_clipboard_text("sira testi") is True
    assert sp.clipboard_sequence() != before


@win32_clipboard
def test_clipboard_sequence_prototype_is_declared():
    u, _ = sp._win32()
    assert u.GetClipboardSequenceNumber.restype is ctypes.c_uint
    assert u.GetClipboardSequenceNumber.argtypes == []


def test_clipboard_sequence_is_none_when_win32_is_unavailable(monkeypatch):
    # Windows' contract; on Linux the same door goes to linux_misc
    # (test_linux_misc.py).
    monkeypatch.setattr(sp, "_LINUX", False)
    monkeypatch.setattr(sp, "_win32", lambda: (None, None))
    assert sp.clipboard_sequence() is None


def test_clipboard_sequence_is_none_when_the_call_raises(monkeypatch):
    class Broken:
        def GetClipboardSequenceNumber(self):
            raise OSError("erisim reddedildi")

    monkeypatch.setattr(sp, "_win32", lambda: (Broken(), object()))
    assert sp.clipboard_sequence() is None


# The block is built with create_unicode_buffer, i.e. wchar_t: UTF-16 on
# Windows (what CF_UNICODETEXT is), UTF-32 on Linux. The Win32 path never runs
# on Linux (_win32() is (None, None) there), so only Windows can check it.
@pytest.mark.skipif(sys.platform != "win32",
                    reason="CF_UNICODETEXT needs Windows' 2-byte wchar_t")
def test_text_with_an_emoji_is_copied_whole(monkeypatch):
    """An emoji is two UTF-16 units: (len + 1) * 2 bytes cut the text short
    and dropped the terminator, so a paste could pick up trailing garbage."""
    import ctypes
    import eye_gaze.speech as sp
    got = {}

    class K:
        def GlobalAlloc(self, flags, n):
            got["buf"] = ctypes.create_string_buffer(n)
            return 1

        def GlobalLock(self, h):
            return ctypes.addressof(got["buf"])

        def GlobalUnlock(self, h):
            return 1

        def GlobalFree(self, h):
            return 0

    class U:
        def OpenClipboard(self, h):
            return 1

        def EmptyClipboard(self):
            return 1

        def SetClipboardData(self, fmt, h):
            return h

        def CloseClipboard(self):
            return 1
    monkeypatch.setattr(sp, "_win32", lambda: (U(), K()))
    text = "Su istiyorum \U0001F622 lutfen"
    assert sp.set_clipboard_text(text) is True
    raw = got["buf"].raw
    assert raw.endswith(b"\x00\x00")
    assert raw[:-2].decode("utf-16-le") == text
