# gaze/gozfaresi.py
"""Göz Faresi: one program, two roles (spec 6.2).

  gozfaresi                     the launcher: status window, first-run
                                wizard, settings; runs the driver as a child
  gozfaresi --surucu [args...]  the driver itself (gaze_driver.main), with
                                its exit code unchanged: 0 quit, 2 config
                                error, anything else a crash
  gozfaresi --sihirbaz          the launcher, wizard first (in source mode
                                the only way to see the wizard)
  gozfaresi --ayarlar           the launcher, settings window open

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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--surucu"]:
        return run_driver(argv[1:])
    from eye_gaze import ui
    return ui.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
