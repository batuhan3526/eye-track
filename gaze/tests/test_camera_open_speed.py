# gaze/tests/test_camera_open_speed.py
"""Opening a common USB webcam through Media Foundation took ~130 s on a
test machine: 32 s to open, the rest on the first mode change. With MSMF
hardware transforms off the same open takes ~1 s and delivers the same
frames. That delay hit every start AND every camera reconnect."""
import os
import subprocess
import sys
from pathlib import Path

DRIVER = Path(__file__).resolve().parent.parent / "gaze_driver.py"


def test_hw_transforms_are_disabled_before_cv2_is_imported():
    src = DRIVER.read_text(encoding="utf-8")
    flag = src.find('OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS')
    first_cv2 = src.find("\nimport cv2")
    assert flag != -1, "the MSMF flag is not set"
    assert flag < first_cv2, "OpenCV reads the flag at import time"


def test_importing_the_driver_sets_the_flag():
    env = dict(os.environ)
    env.pop("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", None)
    out = subprocess.run(
        [sys.executable, "-c",
         "import os, gaze_driver; "
         "print(os.environ['OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS'])"],
        cwd=DRIVER.parent, env=env, capture_output=True, text=True,
        timeout=120)
    assert out.stdout.strip().splitlines()[-1] == "0", out.stderr[-2000:]

