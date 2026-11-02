# gaze/gozfaresi.py
"""Göz Faresi: one program, several roles (spec 6.2).

  gozfaresi                     the launcher: status window, first-run
                                wizard, settings; runs the driver as a child
  gozfaresi --surucu [args...]  the driver itself (gaze_driver.main), with
                                its exit code unchanged: 0 quit, 2 config
                                error, anything else a crash
  gozfaresi --sihirbaz          the launcher, wizard first (in source mode
                                the only way to see the wizard)
  gozfaresi --ayarlar           the launcher, settings window open
  gozfaresi --kamera-ara <istek>  the camera search, in a process of its
                                own (eye_gaze.camera_proc)
  gozfaresi --kamera-ac <istek>   one open camera, for the wizard's preview

The packaged exe (PyInstaller) starts here; in a source checkout
`python gozfaresi.py` does the same, while BASLAT.bat keeps running
gaze_driver.py directly as it always has.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def run_driver(args):
    """gaze_driver's own __main__, from the same exe (frozen builds have no
    separate script to start)."""
    sys.argv = [sys.argv[0]] + list(args)
    if os.environ.get("GOZFARESI_SUPERVISED") == "1":
        from eye_gaze.ui.supervisor import watch_supervisor_pipe
        watch_supervisor_pipe()
    import gaze_driver
    try:
        return gaze_driver.main()
    except KeyboardInterrupt:
        return 0
    except Exception:
        import traceback
        traceback.print_exc()
        return 1


def run_camera_child(argv):
    """A camera child (eye_gaze.camera_proc): the camera search
    (--kamera-ara) or one open camera (--kamera-ac). A driver fault ends
    only this process. It ends with os._exit: a probe thread stuck in a
    driver call must not hold up the interpreter's shutdown."""
    from eye_gaze import camera_proc
    try:
        if argv[0] == camera_proc.PROBE_ROLE:
            from eye_gaze import hardware
            code = hardware.probe_child(argv[1:])
        else:
            code = camera_proc.serve(argv[1:])
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(code)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--surucu"]:
        return run_driver(argv[1:])
    if argv[:1] and argv[0] in ("--kamera-ara", "--kamera-ac"):
        return run_camera_child(argv)
    from eye_gaze import ui
    return ui.main(argv)


CRASH_LOG = "cokme.log"
# Lines faulthandler starts a dump with.
_DUMP_MARKS = ("Windows fatal exception", "Fatal Python error")
_TAIL_BYTES = 4 * 1024 * 1024    # how much of the log crash_log_end reads
_crash_header = [None]


def _role():
    return " ".join(sys.argv[1:2]) or "baslatici"


def crash_log():
    """A native crash (a camera or GPU driver, a library under it) ends the
    process with no Python traceback, and the windowed exe has no console:
    the wizard simply vanished. faulthandler writes every thread's stack to
    data_dir()/cokme.log instead (a caught OpenCV error writes nothing).
    The header names the version: which install wrote a log is the first
    question about it. The file stays open for the life of the process;
    None when it cannot be opened."""
    try:
        import faulthandler
        import time
        from eye_gaze import __version__, paths
        d = paths.data_dir()
        os.makedirs(d, exist_ok=True)
        f = open(os.path.join(d, CRASH_LOG), "a", encoding="utf-8")
        header = "--- %s pid %d %s (surum %s)" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), _role(),
            __version__)
        f.write(header + "\n")
        f.flush()
        _crash_header[0] = header
        faulthandler.enable(f, all_threads=True)
        return f
    except Exception:
        return None


def crash_log_end(f, code):
    """This process is ending normally (main() returned `code`). On Windows
    faulthandler also writes exceptions that were HANDLED - COM raises some
    of its errors that way (a tester's driver logged 0x80040155 and
    0x80010108 and ran on for minutes). When a dump follows this process's
    header, say under it that the process was not stopped by it; otherwise
    write nothing. Never raises."""
    header = _crash_header[0]
    if f is None or header is None:
        return
    try:
        import time
        f.flush()
        with open(f.name, "rb") as r:
            r.seek(0, os.SEEK_END)
            r.seek(max(0, r.tell() - _TAIL_BYTES))
            text = r.read().decode("utf-8", "replace")
        i = text.rfind(header)
        if i < 0 or not any(m in text[i:] for m in _DUMP_MARKS):
            return
        f.write("--- %s pid %d %s kapandi (kod %s): yukaridaki 'Windows fatal"
                " exception' kayitlari bu sureci DURDURMADI (Windows'un kendi"
                " icinde yakalayip gectigi hatalar)\n"
                % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), _role(),
                   code))
        f.flush()
    except Exception:
        pass


if __name__ == "__main__":
    _crash_file = crash_log()
    _code = main()
    crash_log_end(_crash_file, _code)
    raise SystemExit(_code)
