# gaze/tests/test_win32_handles.py
"""Every Win32 handle this project touches is 64-bit. ctypes assumes 32.

This is the bug class that closed the driver mid-session: an undeclared
restype makes ctypes read a HANDLE as a C int, and Windows hands out handles
well above 4 GB on the development machine (measured: 0x1c1849e0008). The truncated value
is then either 0 - the call silently "fails" - or a wrong handle, which is
worse, because Windows will happily act on somebody else's object.

The clipboard was where it crashed. These are the other places that have it.
"""
import ctypes

import pytest

import gaze_driver as gd
from eye_gaze import zoom

WINDOWS = hasattr(ctypes, "windll")
pytestmark = pytest.mark.skipif(not WINDOWS, reason="Windows-only")


def test_the_screen_capture_prototypes_are_declared():
    u, g = zoom._gdi()
    if u is None:
        pytest.skip("GDI yok")
    for fn in (u.GetDC, g.CreateCompatibleDC, g.CreateCompatibleBitmap,
               g.SelectObject):
        assert fn.restype is ctypes.c_void_p, fn


def test_the_topmost_prototypes_are_declared():
    u = gd._topmost_api()
    if u is None:
        pytest.skip("user32 yok")
    assert u.FindWindowW.restype is ctypes.c_void_p


def _gdi_objects():
    """How many GDI objects this process owns right now."""
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    k.GetCurrentProcess.restype = ctypes.c_void_p
    u.GetGuiResources.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.GetGuiResources.restype = ctypes.c_uint
    return int(u.GetGuiResources(k.GetCurrentProcess(), 0))   # GR_GDIOBJECTS


def test_the_magnifier_does_not_leak_gdi_objects():
    """The real cost of a truncated handle: DeleteObject/DeleteDC are handed a
    value that is not the object, so they quietly do nothing. Every F6 press
    leaks a bitmap and two DCs. A process that reaches the 10 000 GDI limit
    stops being able to draw ANYTHING - and this user presses F6 to click.
    """
    if zoom.grab_region(0, 0, 32, 32) is None:
        pytest.skip("bu ortamda ekran yakalanamiyor")
    for _ in range(10):                       # warm up any one-off allocations
        zoom.grab_region(0, 0, 64, 64)
    before = _gdi_objects()
    for _ in range(60):
        zoom.grab_region(0, 0, 64, 64)
    after = _gdi_objects()
    assert after - before <= 2, f"{before} -> {after}: GDI nesnesi siziyor"


class _FakeGdi:
    """Records what was created and what was released."""

    def __init__(self, bitblt_ok=True):
        self.freed = []
        self.selected = []
        self._ok = bitblt_ok

    # user32 side
    def GetDC(self, hwnd):
        return 0x1111

    def ReleaseDC(self, hwnd, hdc):
        self.freed.append(("dc", hdc))
        return 1

    # gdi32 side
    def CreateCompatibleDC(self, hdc):
        return 0x2222

    def CreateCompatibleBitmap(self, hdc, w, h):
        return 0x3333

    def SelectObject(self, hdc, obj):
        self.selected.append(obj)
        return 0x4444

    def BitBlt(self, *a):
        return 1 if self._ok else 0

    def GetDIBits(self, hdc, bmp, s, n, buf, bi, u):
        return n

    def DeleteObject(self, obj):
        self.freed.append(("obj", obj))
        return 1

    def DeleteDC(self, hdc):
        self.freed.append(("memdc", hdc))
        return 1


def test_a_failed_capture_still_releases_everything(monkeypatch):
    """BitBlt failing raised OSError right past the three cleanup calls, so
    every failed magnifier press leaked a bitmap and two DCs for the life of
    the process. Screen capture fails for ordinary reasons - a UAC prompt, a
    protected window, a locked session."""
    fake = _FakeGdi(bitblt_ok=False)
    monkeypatch.setattr(zoom, "_gdi", lambda: (fake, fake))
    # scale=1.0: this pins grab_region's OWN objects; screen_scale() takes
    # and releases a screen DC of its own (checked separately below).
    assert zoom.grab_region(0, 0, 64, 64, scale=1.0) is None
    kinds = sorted(k for k, _ in fake.freed)
    assert kinds == ["dc", "memdc", "obj"], fake.freed


def test_a_successful_capture_releases_everything_too(monkeypatch):
    """Non-vacuity: the cleanup must not only run on the failure path."""
    fake = _FakeGdi(bitblt_ok=True)
    monkeypatch.setattr(zoom, "_gdi", lambda: (fake, fake))
    zoom.grab_region(0, 0, 64, 64, scale=1.0)
    assert sorted(k for k, _ in fake.freed) == ["dc", "memdc", "obj"]


def test_the_scale_query_releases_its_dc(monkeypatch):
    fake = _FakeGdi(bitblt_ok=True)
    monkeypatch.setattr(zoom, "_gdi", lambda: (fake, fake))
    zoom.screen_scale()
    assert [k for k, _ in fake.freed] == ["dc"]


def test_grab_region_still_returns_a_real_image():
    """Non-vacuity: none of the above may be satisfied by capturing nothing."""
    img = zoom.grab_region(0, 0, 64, 48, scale=1.0)
    if img is None:
        pytest.skip("bu ortamda ekran yakalanamiyor")
    assert img.shape == (48, 64, 3)
    assert img.dtype.name == "uint8"


def test_forcing_topmost_on_a_window_that_does_not_exist_is_harmless():
    gd._force_topmost("boyle bir pencere yok 12345")      # must not raise


def test_raising_priority_is_harmless():
    gd._raise_process_priority()                          # must not raise


def test_the_original_bitmap_is_put_back_before_deleting_it(monkeypatch):
    """DeleteObject REFUSES to delete a bitmap that is still selected into a
    DC, so without restoring the DC's original object the bitmap survives -
    the leak comes straight back, silently, with the cleanup code still there
    looking correct."""
    fake = _FakeGdi(bitblt_ok=True)
    monkeypatch.setattr(zoom, "_gdi", lambda: (fake, fake))
    zoom.grab_region(0, 0, 64, 64)
    assert len(fake.selected) == 2, fake.selected
    assert fake.selected[0] == 0x3333, "once bizim bitmap secilmeli"
    assert fake.selected[1] == 0x4444, "DC'nin eski nesnesi geri konmadi"


def test_the_original_bitmap_is_restored_on_the_failure_path_too(monkeypatch):
    fake = _FakeGdi(bitblt_ok=False)
    monkeypatch.setattr(zoom, "_gdi", lambda: (fake, fake))
    zoom.grab_region(0, 0, 64, 64)
    assert fake.selected[-1] == 0x4444, fake.selected


# --- F4 must never send Ctrl+C into our own console ------------------------
#
# The magnifier and the preview window belong to this process, and so does the
# console BASLAT.bat runs in. keyboard.send("ctrl+c") goes to whatever has
# FOCUS: land it on the console and cmd.exe asks "Terminate batch job (Y/N)?"
# while Python takes a KeyboardInterrupt and exits - so answering N changes
# nothing, the driver is already gone. Reported from the field, straight after
# using F3.


class _Fg:
    def __init__(self, hwnd, pid):
        self._hwnd = hwnd
        self._pid = pid

    def GetForegroundWindow(self):
        return self._hwnd

    def GetWindowThreadProcessId(self, hwnd, out):
        out._obj.value = self._pid
        return 1


class _Me:
    def GetCurrentProcessId(self):
        return 4242


def test_our_own_window_is_recognised(monkeypatch):
    monkeypatch.setattr(gd, "_fg_api", lambda: (_Fg(0x99, 4242), _Me()))
    assert gd._foreground_is_ours() is True


def test_another_programs_window_is_not_ours(monkeypatch):
    """Non-vacuity: the normal case - Not Defteri focused - must still copy."""
    monkeypatch.setattr(gd, "_fg_api", lambda: (_Fg(0x99, 777), _Me()))
    assert gd._foreground_is_ours() is False


def test_no_foreground_window_counts_as_ours(monkeypatch):
    """Nothing focused: sending Ctrl+C blind is exactly the risk."""
    monkeypatch.setattr(gd, "_fg_api", lambda: (_Fg(0, 0), _Me()))
    assert gd._foreground_is_ours() is True


def test_a_broken_api_counts_as_ours(monkeypatch):
    """Asymmetric risk: guessing wrong the other way kills the driver, and
    guessing this way only means F4 speaks the clipboard as it stands."""
    monkeypatch.setattr(gd, "_fg_api", lambda: (None, None))
    assert gd._foreground_is_ours() is True


def test_the_real_api_answers_without_raising():
    assert gd._foreground_is_ours() in (True, False)
