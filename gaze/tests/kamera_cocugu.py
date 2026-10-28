# gaze/tests/kamera_cocugu.py
"""A camera child for the tests: the real child code (hardware.probe_child,
camera_proc.serve) on fake cameras, some of which crash the process for
real - a native fault, as a driver's would.

  python kamera_cocugu.py <scenario> --kamera-ara <request>
  python kamera_cocugu.py <scenario> --kamera-ac <request>

scenario (JSON): {"cams": {"<index>": {"backends": [...],
  "modes": [[w, h, fps], ...]  (the first is the camera's own),
  "crash": [[backend, w, h, fourcc], ...]  (reading that asked mode ends
                                           the process: faulthandler._sigsegv)
  }}, "backends": [...] (default_backends), "frames": n (preview: crash
  after n frames), "hang_open": true (preview: the open never returns)}
"""
import faulthandler
import json
import os
import sys
import time

_GAZE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _GAZE not in sys.path:
    sys.path.insert(0, _GAZE)

import numpy as np  # noqa: E402

from eye_gaze import camera_proc, hardware  # noqa: E402


def crash():
    """A real fault that ends the process. Windows: an access violation on
    a native thread, as the tester's DirectShow driver faulted (no Python
    thread current; no error-report dialog). Linux: SIGSEGV."""
    if os.name != "nt":
        faulthandler._sigsegv()
    import ctypes
    k32 = ctypes.WinDLL("kernel32")
    k32.SetErrorMode(0x0001 | 0x0002)   # no critical-error / GP-fault box
    k32.CreateThread.restype = ctypes.c_void_p
    k32.CreateThread.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                 ctypes.c_void_p, ctypes.c_void_p,
                                 ctypes.c_uint32, ctypes.c_void_p]
    k32.CreateThread(None, 0, ctypes.c_void_p(0x10), None, 0, None)
    time.sleep(60)


class Cam:
    def __init__(self, spec, backend):
        self.backend = backend
        self.opened = backend in spec.get("backends", ())
        self.modes = {(w, h): fps for w, h, fps in spec.get("modes", ())}
        self.size = tuple(spec["modes"][0][:2]) if spec.get("modes") else None
        self.crash = {tuple(c) for c in spec.get("crash", ())}
        self.req = [0, 0]
        self.fourcc = ""

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        import cv2
        if prop == cv2.CAP_PROP_FOURCC:
            if self.backend == "msmf":
                return False
            self.fourcc = "MJPG"
        elif prop == cv2.CAP_PROP_FRAME_WIDTH:
            self.req[0] = int(value)
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
            self.req[1] = int(value)
            if tuple(self.req) in self.modes:
                self.size = tuple(self.req)
        return True

    def get(self, prop):
        return 0.0

    def read(self):
        if (self.backend, self.req[0], self.req[1], self.fourcc) in self.crash:
            crash()
        time.sleep(1.0 / self.modes[self.size])
        w, h = self.size
        return True, np.zeros((h, w, 3), np.uint8)

    def release(self):
        pass


def main(argv):
    scenario = json.loads(argv[0])
    role, request = argv[1], argv[2]
    cams = {int(k): v for k, v in scenario.get("cams", {}).items()}
    if scenario.get("backends"):
        hardware.default_backends = lambda: tuple(scenario["backends"])

    def opener(index, backend):
        return Cam(cams.get(int(index), {}), backend)
    if role == camera_proc.PROBE_ROLE:
        return hardware.probe_child([request], opener=opener)

    def open_capture(index, width, height, fps, backend=None, fourcc=None):
        if scenario.get("hang_open"):
            time.sleep(3600)
        cam = Cam(cams.get(int(index), {}), backend or "msmf")
        cam.set(3, width)
        cam.set(4, height)
        return cam
    frames = scenario.get("frames")
    if frames is not None:
        real = Cam.read

        def read(self, count=[0]):
            count[0] += 1
            if count[0] > frames:
                crash()
            return real(self)
        Cam.read = read
    return camera_proc.serve([request], open_capture=open_capture,
                             sleep=lambda s: time.sleep(min(s, 0.01)))


if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    os._exit(code)
