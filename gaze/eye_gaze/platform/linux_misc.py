# gaze/eye_gaze/platform/linux_misc.py
"""Linux helpers that are not input: clipboard, on-screen keyboard, the
focused window, raising our windows, the bundled font.

Everything here NEVER raises - the callers are the gaze loop and its worker
threads, where an exception costs the cursor. Failures come back as False /
None plus a reason. Importable on Windows too (tests drive it with fakes);
nothing touches a display or a tool until a function is called.

Clipboard, in this order:
  Wayland  wl-copy / wl-paste (wl-clipboard); without them, XWayland's
           X tools below when DISPLAY is set;
  X11      xclip, then xsel;
  last     a Tk clipboard owned by a hidden Tk root on its own thread.

On X11 the clipboard is not a buffer but a PROGRAM: whoever "owns" the
selection must stay alive and answer every paste. Hence:
  * xclip/xsel/wl-copy each fork a small server that keeps owning it. Their
    stdout/stderr go to /dev/null - left as pipes, the server would inherit
    them and subprocess would wait for its EOF, i.e. forever. They start in
    their own session, so the hang-up of the terminal we were started from
    does not take the clipboard with it; what was copied survives even our
    exit, until something else is copied (as on Windows).
  * The Tk root must keep running an event loop, or no paste is ever
    answered - so it lives on a thread of its own for the life of the
    process. Its content is lost when we exit (no server outlives us);
    xclip/xsel are preferred for exactly that reason.
"""
import atexit
import contextlib
import hashlib
import os
import queue
import shutil
import subprocess
import threading
import time

from eye_gaze.i18n import t as _t

# Seams for the tests.
_which = shutil.which
_run = subprocess.run
_popen = subprocess.Popen
_clock = time.monotonic

_TIMEOUT = 2.0              # s per clipboard tool call
_VERIFY_FOR = 0.5           # s an X clipboard write may take to be served
_RAISE_EVERY = 0.25         # s between re-raises of the same window

_READ = {
    # --type text: wl-paste's "any plain text" (never an image).
    "wl": ["wl-paste", "--no-newline", "--type", "text"],
    "xclip": ["xclip", "-selection", "clipboard", "-o"],
    "xsel": ["xsel", "--clipboard", "--output"],
}
_WRITE = {
    "wl": ["wl-copy"],
    "xclip": ["xclip", "-selection", "clipboard", "-i"],
    "xsel": ["xsel", "--clipboard", "--input"],
}

_problem = [""]


def _say(message):
    """One console line (ASCII: the caller passes ASCII text). Never raises."""
    try:
        print(message)
    except Exception:
        pass


def _session():
    from eye_gaze import platform
    return platform.session_type()


# --- clipboard ----------------------------------------------------------------

def clipboard_method():
    """"wl", "xclip", "xsel", "tk", or None when this session has no
    clipboard we can reach (clipboard_problem() says why)."""
    session = _session()
    if session == "wayland":
        if _which("wl-copy") and _which("wl-paste"):
            return "wl"
        if not os.environ.get("DISPLAY"):
            _problem[0] = "pano icin 'wl-clipboard' paketi gerekiyor"
            return None
    elif session != "x11" or not os.environ.get("DISPLAY"):
        _problem[0] = "grafik oturum yok (DISPLAY bos)"
        return None
    if _which("xclip"):
        return "xclip"
    if _which("xsel"):
        return "xsel"
    return "tk"


def clipboard_problem():
    """Why the last clipboard call failed or read nothing (ASCII; '' = it
    did not)."""
    return _problem[0]


def _tool(method, argv, data=None):
    """(returncode, stdout bytes) of one clipboard tool run, or None (and
    _problem set) when it could not be run at all."""
    try:
        if data is None:
            r = _run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, timeout=_TIMEOUT)
        else:
            # The writers fork a server that keeps the selection: no pipes
            # it could inherit, and a session of its own (module docstring).
            r = _run(argv, input=data, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, timeout=_TIMEOUT,
                     start_new_session=True)
    except subprocess.TimeoutExpired:
        _problem[0] = ("pano sahibi program yanit vermiyor (%s)" % method)
        return None
    except Exception as exc:
        _problem[0] = "pano araci calismadi (%s: %s)" % (
            method, type(exc).__name__)
        return None
    return r.returncode, (r.stdout or b"")


def _read_with(method):
    """Clipboard text via one tool; '' when empty, None when unreadable."""
    if method == "tk":
        return _tk_clipboard().get()
    res = _tool(method, _READ[method])
    if res is None:
        return None
    code, out = res
    if code != 0:
        # xclip and wl-paste exit non-zero for an empty clipboard ("target
        # not available", "Nothing is copied") - that is not a failure.
        return ""
    return out.decode("utf-8", "replace")


def clipboard_get():
    """The clipboard's text: '' when empty, None when it cannot be read
    (clipboard_problem() says why). Never raises."""
    try:
        method = clipboard_method()
        if method is None:
            return None
        text = _read_with(method)
        if text is None:
            return None
        _problem[0] = "" if text else "panoda metin yok"
        return text
    except Exception as exc:
        _problem[0] = "pano okunamadi (%s)" % type(exc).__name__
        return None


def clipboard_set(text):
    """Put `text` on the clipboard; True once it is there. Never raises."""
    try:
        text = str(text)
        method = clipboard_method()
        if method is None:
            return False
        if method == "tk":
            ok = _tk_clipboard().set(text)
        else:
            res = _tool(method, _WRITE[method], text.encode("utf-8"))
            if res is None:
                return False
            if res[0] != 0:
                _problem[0] = "pano yazilamadi (%s cikis kodu %d)" % (
                    method, res[0])
                return False
            ok = method == "wl" or _served(method, text)
        if ok:
            _problem[0] = ""
            _note_content(text)
        return bool(ok)
    except Exception as exc:
        _problem[0] = "pano yazilamadi (%s)" % type(exc).__name__
        return False


def _served(method, text):
    """Wait until the X clipboard actually hands out `text`.

    xsel takes the selection in its forked child, AFTER the parent we waited
    for has exited (and xclip's ownership request may still sit in its
    output buffer): a Ctrl+V sent at once could paste the PREVIOUS
    clipboard. Not done on Wayland: wl-paste there briefly takes the focus
    on some desktops, and it would take it from the field being pasted to.
    """
    end = _clock() + _VERIFY_FOR
    while True:
        if _read_with(method) == text:
            return True
        if _clock() >= end:
            _problem[0] = "panoya yazilan metin geri okunamadi (%s)" % method
            return False
        time.sleep(0.02)


_CLEAR = {
    "wl": ["wl-copy", "--clear"],
    "xsel": ["xsel", "--clipboard", "--clear"],
}


def clipboard_clear():
    """Empty the clipboard and check that it now reads empty: True only
    then. F4 clears before its Ctrl+C, so that ANY text found afterwards was
    just copied - the content-derived sequence below cannot tell "the same
    text copied again" from "nothing copied". Never raises."""
    try:
        method = clipboard_method()
        if method is None:
            return False
        if method == "tk":
            if not _tk_clipboard().set(""):
                return False
        else:
            argv = _CLEAR.get(method)
            # xclip has no "clear": it takes the selection with nothing in it.
            res = (_tool(method, argv) if argv is not None
                   else _tool(method, _WRITE[method], b""))
            if res is None or res[0] != 0:
                if res is not None:
                    _problem[0] = "pano bosaltilamadi (%s cikis kodu %d)" % (
                        method, res[0])
                return False
        end = _clock() + _VERIFY_FOR
        while True:
            if _read_with(method) == "":
                _note_content("")
                return True
            if _clock() >= end:
                _problem[0] = "pano bosaltilamadi (%s)" % method
                return False
            time.sleep(0.02)
    except Exception as exc:
        _problem[0] = "pano bosaltilamadi (%s)" % type(exc).__name__
        return False


# The sequence number: Linux has no clipboard change counter, so one is
# derived from what the clipboard holds - it moves whenever the content
# differs from the last content seen. Copying the very text that is already
# there therefore does not move it (said in clipboard_seq()).
_seq_lock = threading.Lock()
_seq = [0, None]          # counter, digest of the content last seen


def _digest(text):
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def _note_content(text):
    d = _digest(text)
    with _seq_lock:
        if d != _seq[1]:
            _seq[0] += 1
            _seq[1] = d


def clipboard_seq():
    """A counter that changes when the clipboard content changes (each of
    our own writes included). An unreadable clipboard counts as changed on
    every call: "unknown" must not read as "nothing was copied" - the F4
    handler then reads and reports the real problem. Copying exactly the
    text already on the clipboard leaves it unchanged. Never raises."""
    text = clipboard_get()
    with _seq_lock:
        if text is None:
            _seq[0] += 1
            _seq[1] = None
        else:
            d = _digest(text)
            if d != _seq[1]:
                _seq[0] += 1
                _seq[1] = d
        return _seq[0]


class _TkClipboard:
    """A withdrawn Tk root on its own thread, owning the clipboard.

    Tk objects may only be touched from the thread that made them, and the
    root must run mainloop() for other programs' pastes to be answered: every
    request is queued to that thread and polled there. At exit the root is
    destroyed on its own thread too - a Tcl interpreter deleted from another
    thread aborts the process ("Tcl_AsyncDelete")."""

    _POLL_MS = 20

    def __init__(self, thread=threading.Thread):
        self.error = None
        self._q = queue.Queue()
        self._ready = threading.Event()
        self._done = threading.Event()
        self._t = thread(target=self._run, daemon=True, name="tk-clipboard")
        self._t.start()
        if not self._ready.wait(5.0):
            self.error = "Tk baslamadi (5 sn)"
        atexit.register(self.close)

    def _run(self):
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
        except Exception as exc:
            self.error = "Tk acilamadi (%s)" % type(exc).__name__
            self._ready.set()
            self._done.set()
            return
        self._ready.set()

        def poll():
            while True:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    root.quit()
                    return
                fn, box, done = item
                try:
                    box.append((True, fn(root)))
                except Exception as exc:
                    box.append((False, exc))
                done.set()
            root.after(self._POLL_MS, poll)
        root.after(self._POLL_MS, poll)
        try:
            root.mainloop()
        finally:
            try:
                root.destroy()
            except Exception:
                pass
            del root
            self.error = self.error or "Tk panosu kapandi"
            self._done.set()

    def _call(self, fn, timeout=3.0):
        if self.error:
            raise RuntimeError(self.error)
        box, done = [], threading.Event()
        self._q.put((fn, box, done))
        if not done.wait(timeout):
            raise TimeoutError("Tk panosu yanit vermedi")
        ok, value = box[0]
        if not ok:
            raise value
        return value

    def get(self):
        """'' when empty, None when Tk cannot be used."""
        def read(root):
            try:
                return root.clipboard_get()
            except Exception as exc:
                if type(exc).__name__ == "TclError":
                    return ""       # nothing (or no text) on the clipboard
                raise
        try:
            return self._call(read)
        except Exception as exc:
            _problem[0] = "Tk panosu okunamadi (%s)" % type(exc).__name__
            return None

    def set(self, text):
        def write(root):
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update_idletasks()
            return True
        try:
            return bool(self._call(write))
        except Exception as exc:
            _problem[0] = "Tk panosu yazilamadi (%s)" % type(exc).__name__
            return False

    def close(self):
        if self._done.is_set():
            return
        self._q.put(None)
        self._done.wait(2.0)


_tk = [None]
_tk_lock = threading.Lock()


def _tk_clipboard():
    with _tk_lock:
        if _tk[0] is None:
            _tk[0] = _TkClipboard()
        return _tk[0]


def clipboard_status():
    """(available, Turkish reason) for platform.capabilities()."""
    method = clipboard_method()
    if method == "wl":
        return True, "wl-clipboard"
    if method in ("xclip", "xsel"):
        return True, method
    if method == "tk":
        if _session() == "wayland":
            return True, _t("platform.misc.tk_clipboard_xwayland")
        return True, _t("platform.misc.tk_clipboard")
    if _session() == "wayland":
        return False, _t("platform.misc.need_wl_clipboard")
    return False, _t("platform.misc.no_session")


# --- on-screen keyboard -----------------------------------------------------------

_GNOME_OSK = ["gsettings", "set", "org.gnome.desktop.a11y.applications",
              "screen-keyboard-enabled", "true"]
_NO_OSK = "Ekran klavyesi bulunamadı; 'onboard' paketini kurun"


def _osk_choice():
    """("exec", path) | ("gnome", None) | (None, None).

    onboard first: it is a window that stays up and is made for switch and
    pointer users (Ubuntu's accessibility keyboard). GNOME's own keyboard is
    part of the shell - there is no program to start - and is switched on
    by its accessibility setting; it then appears whenever a text field has
    the focus. matchbox-keyboard is the small fallback some distributions
    package for other desktops."""
    path = _which("onboard")
    if path:
        return "exec", path
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    if "GNOME" in desktop and _which("gsettings"):
        return "gnome", None
    path = _which("matchbox-keyboard")
    if path:
        return "exec", path
    return None, None


# gsettings changes a desktop setting that outlives us: the user must be
# told it stays on, and where to turn it off.
_GNOME_OFF = ("program kapanınca da açık kalır; kapatmak için: Ayarlar > "
              "Erişilebilirlik > Ekran Klavyesi")


def osk_status():
    """(available, Turkish reason) for platform.capabilities()."""
    kind, path = _osk_choice()
    if kind == "exec":
        return True, os.path.basename(path)
    if kind == "gnome":
        return True, _t("platform.misc.gnome_osk")
    return False, _t("platform.misc.no_osk")


def launch_osk():
    """Open an on-screen keyboard: (True, which) or (False, Turkish reason).
    Never raises, never waits for the keyboard program."""
    kind, path = _osk_choice()
    if kind is None:
        return False, _NO_OSK
    name = os.path.basename(path) if path else "GNOME ekran klavyesi"
    try:
        if kind == "exec":
            # Its own session and no pipes: it outlives a closed terminal and
            # never blocks on output nobody reads.
            _popen([path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, start_new_session=True)
            return True, name
        r = _run(_GNOME_OSK, stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 timeout=_TIMEOUT)
        if r.returncode != 0:
            return False, ("GNOME ekran klavyesi açılamadı (gsettings çıkış "
                           "kodu %d)" % r.returncode)
        return True, ("GNOME ekran klavyesi açıldı (bir metin kutusu "
                      "seçilince görünür; " + _GNOME_OFF + ")")
    except Exception as exc:
        return False, "Ekran klavyesi (%s) başlatılamadı: %s: %s" % (
            name, type(exc).__name__, exc)


# --- the terminal's Ctrl+C -------------------------------------------------------

def _foreground_tty():
    """(fd, opened_here) of our controlling terminal when OUR process group
    is its foreground group - the group a Ctrl+C typed there would SIGINT -
    else (None, False)."""
    fds = []
    try:
        if os.isatty(0):
            fds.append((0, False))
    except Exception:
        pass
    if not fds:
        try:
            fds.append((os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY), True))
        except Exception:
            pass
    for fd, opened in fds:
        try:
            if os.tcgetpgrp(fd) == os.getpgrp():
                return fd, opened
        except Exception:
            pass
        if opened:
            try:
                os.close(fd)
            except Exception:
                pass
    return None, False


@contextlib.contextmanager
def interrupt_key_off(settle=0.3):
    """Inside: a Ctrl+C reaching the terminal that runs us is NOT turned
    into SIGINT. F4's synthetic Ctrl+C goes wherever the focus is; when that
    is our own terminal - Wayland never tells us, tmux/screen/ssh hide it -
    it would end the driver. ISIG is cleared on the tty (which protects the
    whole process group, from any thread), and on the way out, after
    `settle` seconds for the key to travel display server -> terminal ->
    tty, the ^C byte left in the input is flushed and the settings are put
    back. Does nothing without a terminal of ours. Never raises."""
    fd, opened = _foreground_tty()
    saved = None
    termios = None
    if fd is not None:
        try:
            import termios
            saved = termios.tcgetattr(fd)
            quiet = list(saved)
            quiet[3] = quiet[3] & ~termios.ISIG          # lflag
            termios.tcsetattr(fd, termios.TCSANOW, quiet)
        except Exception:
            saved = None
    try:
        yield saved is not None
    finally:
        if saved is not None:
            time.sleep(settle)
            try:
                termios.tcflush(fd, termios.TCIFLUSH)
            except Exception:
                pass
            try:
                termios.tcsetattr(fd, termios.TCSANOW, saved)
            except Exception:
                pass
        if opened:
            try:
                os.close(fd)
            except Exception:
                pass


# --- windows ----------------------------------------------------------------------

def _window_backend():
    """The input backend if it can see X windows (X11, or evdev with
    XWayland/X11 behind it), else None."""
    try:
        from eye_gaze import platform
        b = platform.linux_backend()
    except Exception:
        return None
    if hasattr(b, "foreground_pid") and hasattr(b, "raise_window"):
        return b
    return None


def _parent_pid(pid):
    """Parent of `pid` from /proc, or None."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as f:
            stat = f.read()
        # "pid (comm) state ppid ...": comm may hold spaces and ")".
        return int(stat[stat.rindex(b")") + 2:].split()[1])
    except Exception:
        return None


def _on_terminal():
    try:
        return os.isatty(0) or os.isatty(1)
    except Exception:
        return False


def _our_pids():
    """Our pid - plus, when we run in a terminal, the processes above us.

    The F4 danger is Ctrl+C landing on the terminal that runs the driver:
    it would SIGINT us. That window belongs to the terminal emulator, an
    ancestor of ours (gnome-terminal-server, xterm, konsole...), never to us.
    Without a terminal no ancestor counts: a file manager that launched the
    AppImage is just another window."""
    pids = {os.getpid()}
    if _on_terminal():
        pid = os.getppid()
        for _ in range(32):
            if not pid or pid <= 1 or pid in pids:
                break
            pids.add(pid)
            pid = _parent_pid(pid)
    return pids


def foreground_is_ours():
    """Is the focused window ours (or our terminal's)? Unknown = False:
    Wayland never says which window has the focus, and "ours" there would
    turn F4 off for good."""
    b = _window_backend()
    if b is None:
        return False
    try:
        pid = b.foreground_pid()
    except Exception:
        return False
    if not pid:
        return False
    return int(pid) in _our_pids()


_raised = {}
_said_no_raise = [False]


def raise_window(title):
    """Keep our window `title` above the others; False when it cannot be.

    The driver calls this every frame while the magnifier or the AAC grid
    is open. On X11 the "above" state sticks once the window manager has
    it, and every raise costs a round trip per top-level window on the
    connection the cursor also uses - so the same title is re-raised at
    most every _RAISE_EVERY seconds. Without an X display (pure Wayland)
    it cannot be done at all: said once."""
    b = _window_backend()
    if b is None:
        if not _said_no_raise[0]:
            _said_no_raise[0] = True
            _say("[pencere] Bu oturumda pencereler en uste itilemiyor "
                 "(X11 baglantisi yok); buyutec ve AAC izgarasi baska "
                 "pencerelerin altinda kalabilir.")
        return False
    now = _clock()
    last = _raised.get(title)
    if last is not None and now - last[0] < _RAISE_EVERY:
        return last[1]
    try:
        ok = bool(b.raise_window(title))
    except Exception:
        ok = False
    # A miss is remembered as well: a window still opening is found on the
    # next try, a title that never matches costs no search every frame.
    _raised[title] = (now, ok)
    return ok


# --- font ---------------------------------------------------------------------------

def font_path():
    """The bundled DejaVu Sans (every Turkish letter; Bitstream Vera
    license, resources/fonts/LICENSE-DejaVu.txt)."""
    from eye_gaze import paths
    return paths.resource_path("fonts", "DejaVuSans.ttf")
