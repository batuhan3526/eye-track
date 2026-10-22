# packaging/pyi_rth_av_yok.py
"""PyInstaller runtime hook: the package carries no PyAV (Task 18, R41).

faster-whisper imports `av` at the top of faster_whisper/audio.py, but only
its decode_audio() uses it - for a file name or a byte stream. The program
always hands WhisperModel.transcribe() a numpy array straight from the
microphone (eye_gaze/stt.py), which skips decode_audio() entirely. PyAV's
wheel brings its own FFmpeg build with libx264, libx265, GnuTLS, GMP ...
(GPL-2.0-or-later / LGPL); leaving it out (gozfaresi.spec excludes "av")
means none of that ships, so no source for it has to either.

This stub lets `import av` succeed; touching anything in it raises an
AttributeError that names the reason, so a future caller that does need a
file decoded fails loudly instead of mysteriously. Runs before the program's
own code, in the frozen build only - a source install keeps its real PyAV.
"""
import importlib.machinery
import sys
import types

_WHY = ("PyAV (av) bu pakette yok: dikte yalniz mikrofondan gelen numpy ses"
        " dizisini cozer, ses dosyasi acilamaz")


class _NoPyAV(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise AttributeError("av.%s: %s" % (name, _WHY))


_stub = _NoPyAV("av", _WHY)
_stub.__spec__ = importlib.machinery.ModuleSpec("av", None)
sys.modules["av"] = _stub
