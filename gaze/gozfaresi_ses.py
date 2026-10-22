# gaze/gozfaresi_ses.py
"""Göz Faresi's voice worker: piper-tts + espeak-ng in a process of their own.

Packaged builds only (Task 18b, R44): frozen as GozFaresi-Ses.exe (Windows) /
gozfaresi-ses (Linux) next to the main executable, from its own PyInstaller
Analysis that holds piper, its espeak bridge, onnxruntime (CPU), numpy and
this file - and none of the program's code, CTranslate2, OpenCV or anything
else. The program (eye_gaze/ses_isci.py) starts it once, keeps it warm and
talks to it over stdin/stdout (eye_gaze/ses_protokol.py). A source install
never starts it: there piper runs in-process as it always has.

The worker ends at end of file on stdin - the program closed the pipe or
died. On Linux a watcher thread also ends it the moment its parent is
gone; on Windows the program puts it in a kill-on-close job.

    gozfaresi_ses.py            serve requests on stdin/stdout
"""
import io
import os
import sys
import wave

_HERE = os.path.dirname(os.path.abspath(__file__))
if not getattr(sys, "frozen", False) and _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from eye_gaze.ses_protokol import ProtocolError, read_frame, write_frame  # noqa: E402


def _die_with_parent(poll_s=1.0, getppid=None, exit=None):
    """POSIX: exit as soon as the parent is gone, even while a synthesis is
    running (end of file on stdin is only read between requests). A watcher
    thread, not PR_SET_PDEATHSIG: that fires when the parent THREAD that
    started us ends, and the program starts its worker from whichever thread
    needs it first (the phrase-cache warmer finishes and ends). Windows: the
    program's kill-on-close job object does this."""
    if os.name == "nt":
        return
    import threading
    import time
    getppid = getppid or os.getppid
    exit = exit or os._exit
    parent = getppid()

    def watch():
        while True:
            if getppid() != parent:
                exit(0)
                return                  # only a test's exit() returns
            time.sleep(poll_s)
    threading.Thread(target=watch, daemon=True, name="ebeveyn").start()


class Worker:
    def __init__(self):
        self.voice = None
        self.config = None

    def load(self, h):
        from piper import PiperVoice
        self.voice = PiperVoice.load(h["model"],
                                     espeak_data_dir=h["espeak_dir"])
        scale = float(h.get("length_scale", 1.0))
        volume = float(h.get("volume", 1.0))
        # Same rule as the in-process backend: piper's own defaults unless
        # the user changed speed or volume.
        self.config = None
        if abs(scale - 1.0) > 1e-6 or abs(volume - 1.0) > 1e-6:
            from piper import SynthesisConfig
            self.config = SynthesisConfig(length_scale=scale, volume=volume)
        return {"ok": True}, b""

    def synth(self, h):
        if self.voice is None:
            raise RuntimeError("ses yuklenmedi (once load)")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            self.voice.synthesize_wav(str(h.get("text", "")), wf,
                                      syn_config=self.config)
        return {"ok": True}, buf.getvalue()

    def handle(self, h):
        op = h.get("op")
        if op == "ping":
            return {"ok": True, "pid": os.getpid()}, b""
        if op == "load":
            return self.load(h)
        if op == "synth":
            return self.synth(h)
        if op == "modules":
            return {"ok": True, "modules": sorted(
                k for k, v in sys.modules.items() if v is not None)}, b""
        raise RuntimeError("bilinmeyen istek: %r" % (op,))


def serve(ins, outs):
    """Answer frames until end of file or "quit". Returns the exit code."""
    worker = Worker()
    while True:
        try:
            frame = read_frame(ins)
        except ProtocolError:
            return 2
        if frame is None:
            return 0
        header, _body = frame
        rid = header.get("id")
        if header.get("op") == "quit":
            write_frame(outs, {"ok": True, "id": rid})
            return 0
        try:
            reply, body = worker.handle(header)
        except Exception as exc:
            reply, body = {"ok": False, "error": "%s: %s" % (
                type(exc).__name__, str(exc)[:300])}, b""
        # The program matches answers by id: the late answer of a sentence
        # the user stopped is dropped there, never paired with the next.
        reply["id"] = rid
        write_frame(outs, reply, body)


# Never imported in this process (R44), whatever a library tries: the
# packaged worker shares _internal with the program, so the program's
# extension modules are on disk next to it. A None entry makes an import
# fail at once; hashlib then uses its built-in hashes, so no OpenSSL (of any
# version) is loaded either.
NEVER = ("cv2", "ctranslate2", "faster_whisper", "mediapipe", "gaze_driver",
         "tkinter", "_tkinter", "ssl", "_ssl", "_hashlib", "av")


def _block_imports():
    for name in NEVER:
        if name not in sys.modules:
            sys.modules[name] = None


def main():
    _die_with_parent()
    _block_imports()
    ins = sys.stdin.buffer
    # The frames own the pipe on fd 1: a private duplicate carries them, and
    # fd 1 itself now points at stderr - so neither Python's print nor a C
    # library writing to fd 1 (espeak-ng, onnxruntime) can corrupt a frame.
    sys.stdout.flush()
    outs = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    try:
        return serve(ins, outs)
    except (BrokenPipeError, OSError):
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
