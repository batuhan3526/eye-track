# gaze/eye_gaze/camera_proc.py
"""Cameras opened in a process of their own.

A camera driver that faults - an access violation in a DirectShow filter or
in Media Foundation's buffer handling - ends the process it runs in at once,
with no Python traceback; no try/except can catch it. Measured twice in the
field: the wizard vanished at "Kameralar aranıyor" (1.3.0 in Media
Foundation; 1.3.1 in DirectShow, the fault on the driver's own streaming
thread - its cokme.log marks no Python thread as the current one). So the
launcher never opens a camera itself: the camera search
(hardware.probe_cameras) and the wizard's preview (ChildCapture) run in a
child - this same program in another role - and a driver fault ends only
that child. The driver opens the camera in its own process, as it always
has, under the launcher's Supervisor.

  gozfaresi --kamera-ara <request>   hardware.probe_child: the search
  gozfaresi --kamera-ac <request>    serve(): one capture, frames out as JPEG
"""
import json
import os
import subprocess
import sys
import time

PROBE_ROLE = "--kamera-ara"
CAPTURE_ROLE = "--kamera-ac"
ROLES = (PROBE_ROLE, CAPTURE_ROLE)

_GAZE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OPENED = b"ACIK"
CLOSED = b"KAPALI"
FRAME_S = 0.1           # the preview child sends ~10 frames a second (the
                        # wizard shows ~12): its frames never queue up
JPEG_QUALITY = 85
STOP_WAIT_S = 3.0       # release(): the child lets go of the camera itself


def command(role):
    """argv that starts this program in `role`: the same exe when frozen,
    gozfaresi.py under this interpreter in a source checkout (also when
    the driver runs from gaze_driver.py)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, role]
    return [sys.executable, os.path.join(_GAZE_DIR, "gozfaresi.py"), role]


def creationflags():
    """No console window for a child of a windowless launcher (or of a
    console one: the child has nothing to show)."""
    return 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW


def exit_text(code):
    """A child's exit code for the log: Windows' NTSTATUS in hex
    (0xC0000005 = access violation), a POSIX signal by number."""
    if code is None:
        return "?"
    code = int(code)
    if code < 0:
        return "sinyal %d" % -code
    if code > 255:
        return "0x%08X" % (code & 0xFFFFFFFF)
    return "%d" % code


class CameraLost(RuntimeError):
    """The capture's child ended while frames were expected (a driver
    fault, or the child was killed)."""


class ChildCapture:
    """cv2.VideoCapture's isOpened() / read() / release() for a capture
    that hardware.open_capture() opens in a child (gozfaresi --kamera-ac).

    The constructor only starts the child; the first isOpened() waits for
    its open (in the caller's thread, as cv2's constructor would). read()
    hands out the child's frames - JPEG over its stdout, decoded here - and
    raises CameraLost once the child is gone. release() closes the pipe:
    the child's next write fails, it releases the camera and ends; one
    that does not is killed. kill() ends it from any thread - a driver
    call that never returns, the open included."""

    def __init__(self, index, width, height, fps, backend=None, fourcc=None,
                 cmd=None, popen=subprocess.Popen):
        request = json.dumps({"index": int(index), "width": int(width),
                              "height": int(height), "fps": int(fps),
                              "backend": backend or None,
                              "fourcc": fourcc or None})
        self._proc = popen(list(cmd or command(CAPTURE_ROLE)) + [request],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL,
                           creationflags=creationflags())
        self._opened = None

    def isOpened(self):
        if self._opened is None:
            try:
                first = self._proc.stdout.readline().strip()
            except Exception:
                first = b""
            self._opened = first == OPENED
        return self._opened

    def _lost(self):
        try:
            code = self._proc.wait(STOP_WAIT_S)
        except Exception:
            code = None
        return CameraLost("kamera sureci kapandi (kod %s)" % exit_text(code))

    def read(self):
        import cv2
        import numpy as np
        out = self._proc.stdout
        try:
            head = out.readline()
            size = int(head) if head.strip() else -1
        except (OSError, ValueError):
            size = -1
        if size < 0:
            raise self._lost()
        data = out.read(size) if size else b""
        if len(data) != size:
            raise self._lost()
        frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return frame is not None, frame

    def release(self):
        try:
            self._proc.stdout.close()
        except Exception:
            pass
        try:
            self._proc.wait(STOP_WAIT_S)
        except Exception:
            self.kill()

    def kill(self):
        try:
            self._proc.kill()
            self._proc.wait(STOP_WAIT_S)
        except Exception:
            pass


def _send(out, data):
    out.write(data)
    out.flush()


def serve(argv, out=None, open_capture=None, sleep=time.sleep):
    """gozfaresi --kamera-ac <request>: open the camera as the driver would
    (hardware.open_capture) and send its frames until the reader is gone.

    stdout carries only the protocol: OPENED or CLOSED on a line of its
    own, then per frame its JPEG's length on a line and the bytes. Anything
    printed goes to stderr. Returns the exit code."""
    request = json.loads(argv[0])
    if out is None:
        out = sys.stdout.buffer
        sys.stdout = sys.stderr
    from . import hardware
    cap = None
    try:
        try:
            cap = (open_capture or hardware.open_capture)(
                request["index"], request["width"], request["height"],
                request["fps"], backend=request.get("backend"),
                fourcc=request.get("fourcc"))
        except Exception:
            cap = None
        if cap is None or not cap.isOpened():
            _send(out, CLOSED + b"\n")
            return 0
        _send(out, OPENED + b"\n")
        import cv2
        while True:
            t0 = time.monotonic()
            ok, frame = cap.read()
            if not ok or frame is None:
                sleep(0.05)
                continue
            ok, buf = cv2.imencode(".jpg", frame,
                                   [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if ok:
                data = buf.tobytes()
                _send(out, b"%d\n" % len(data) + data)
            # read() itself waited for the camera's frame.
            sleep(max(0.0, FRAME_S - (time.monotonic() - t0)))
    except (OSError, ValueError):
        return 0                        # the reader closed the pipe: done
    finally:
        hardware._release(cap)
