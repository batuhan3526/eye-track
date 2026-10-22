# gaze/eye_gaze/config_writer.py
"""Change settings in config.yaml without rewriting the file.

config.yaml is a caregiver's document as much as a settings file: every
setting carries a Turkish comment saying what it does and what to try when
it misbehaves, and most values were tuned in the field. Re-dumping the parsed
dict would throw all of that away, so set_values() edits only the value on
the one line that holds it; every other byte - comments, column alignment,
blank lines, CRLF or LF, the BOM if there is one - stays as it was.

Where the file is not in the simple one-line `key: value  # comment` shape
(a duplicate key, the key nested under another one, a value spread over
several lines, a quoted key), the writer refuses instead of guessing. A wrong
guess here is a broken config.yaml, and on the next start the driver runs on
yesterday's backup - or not at all.

The written file is read back the way the driver will read it. If any value
did not come back as asked (dropped as unreadable, the whole file unparsable,
no way left to click), the old bytes are put back and ValueError is raised.

config.son-iyi.yaml is NOT touched: only the driver may declare a file "last
known good", because only the driver knows it actually started from it.
"""
import math
import os
import re
import stat

import yaml

from eye_gaze import config
from eye_gaze.i18n import t as _t

_BOM = b"\xef\xbb\xbf"

# A plain YAML scalar cannot start with these; a value that does is a
# collection, a block scalar, an alias, a tag or a directive - not one line.
_NOT_PLAIN = set("[]{}|>&*!%@`,#")

_MISSING = object()


def set_values(path, values):
    """Write `values` ({setting: value}) into the YAML file at `path`.

    Values must be what the driver uses: bool, int, float or str (strings
    are always written double-quoted, so "scroll lock", "auto" and "" are
    safe). A setting missing from the file is appended at the end.

    Raises ValueError when the file cannot be edited safely or the result did
    not read back as asked (the file is then unchanged), TypeError for a value
    type YAML-on-one-line cannot carry, OSError when the disk refuses
    (PermissionError, with a Turkish reason, for a read-only file).
    """
    path = os.fspath(path)
    values = dict(values)
    if not values:
        return
    for key in values:
        if key not in config.KNOWN_KEYS:
            raise ValueError(_t("config_writer.unknown_key", key=key))
    texts = {key: _format_value(v) for key, v in values.items()}

    with open(path, "rb") as f:
        old = f.read()
    bom = old.startswith(_BOM)
    try:
        text = old[len(_BOM):].decode("utf-8") if bom else old.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(_t("config_writer.not_utf8", path=path)) from exc
    current = _parse(text, path)
    # Silently: the settings window saves often, and a warning about a value
    # nobody is changing now would be printed on every save.
    before, _ = config.read_config(path, report=lambda message: None)

    lines = text.split("\n")        # a CRLF line keeps its "\r" at the end
    first = text.find("\n")
    nl = "\r\n" if first > 0 and text[first - 1] == "\r" else "\n"
    appended = []
    for key, vtext in texts.items():
        i = _find_line(lines, key, current)
        if i is None:
            appended.append("%s: %s" % (key, vtext))
        else:
            lines[i] = _replace_value(lines[i], key, vtext, current[key])
    new_text = "\n".join(lines)
    if appended:
        if new_text and not new_text.endswith("\n"):
            new_text += nl
        new_text += "".join(a + nl for a in appended)
    new = (_BOM if bom else b"") + new_text.encode("utf-8")
    if new == old:
        return
    _check_writable(path)

    _atomic_write(path, new)
    try:
        _verify(path, current, values, before)
    except Exception as exc:
        try:
            _atomic_write(path, old)
        except OSError as rexc:
            raise OSError(_t("config_writer.restore_failed", path=path,
                            error=rexc)) from exc
        raise ValueError(_t("config_writer.restored", error=exc)) from exc


# --- reading the file ---------------------------------------------------------

def _parse(text, path):
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # The driver is not running on this file either (it fell back to the
        # backup): editing blind could only add to the damage.
        raise ValueError(_t("config_writer.unreadable", path=path,
                           error=type(exc).__name__)) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(_t("config_writer.not_a_map", path=path))
    return data


def _find_line(lines, key, current):
    """Index of the one top-level `key:` line, or None when the key is absent.

    Raises ValueError for every case where "the line that holds this
    setting" is not obviously one line: duplicates (PyYAML silently keeps the
    last one), indented occurrences (nested, or inside a block scalar), and
    keys spelled in a way this line matcher does not read (quoted keys).
    """
    k = re.escape(key)
    top = re.compile(r"%s[ \t]*:(?=[ \t]|$)" % k)
    other = re.compile(r"""[ \t]+%s[ \t]*:|[ \t]*["']%s["'][ \t]*:""" % (k, k))
    hits = []
    for i, line in enumerate(lines):
        body = line[:-1] if line.endswith("\r") else line
        if top.match(body):
            hits.append(i)
        elif other.match(body):
            raise ValueError(_t("config_writer.nested", key=key))
    if len(hits) > 1:
        raise ValueError(_t("config_writer.duplicate", key=key,
                           n=len(hits)))
    if not hits:
        if key in current:
            raise ValueError(_t("config_writer.odd_shape", key=key))
        return None
    if key not in current:
        raise ValueError(_t("config_writer.not_a_setting", key=key))
    return hits[0]


def _scalar_end(rest):
    """Length of the one-line scalar at the start of `rest`; ValueError when
    the value is not a single-line scalar."""
    if not rest:
        raise ValueError(_t("config_writer.value_empty"))
    q = rest[0]
    if q == '"':
        i = 1
        while i < len(rest):
            if rest[i] == "\\":
                i += 2
            elif rest[i] == '"':
                return i + 1
            else:
                i += 1
        raise ValueError(_t("config_writer.quote_open"))
    if q == "'":
        i = 1
        while i < len(rest):
            if rest[i] == "'":
                if rest[i + 1:i + 2] == "'":
                    i += 2
                    continue
                return i + 1
            i += 1
        raise ValueError(_t("config_writer.quote_open"))
    if q in _NOT_PLAIN or (q in "-?:" and rest[1:2] in ("", " ", "\t")):
        raise ValueError(_t("config_writer.not_one_line"))
    m = re.search(r"[ \t]#", rest)
    plain = rest[:m.start()] if m else rest
    return len(plain.rstrip(" \t"))


def _replace_value(line, key, vtext, parsed):
    """`line` with its value replaced by `vtext`; the comment keeps its
    column when the new value fits, and its original gap when it does not."""
    cr = "\r" if line.endswith("\r") else ""
    body = line[:-1] if cr else line
    head = re.match(r"%s[ \t]*:[ \t]*" % re.escape(key), body).group(0)
    rest = body[len(head):]
    try:
        n = _scalar_end(rest)
    except ValueError as exc:
        raise ValueError(_t("config_writer.line_why", key=key,
                           error=exc)) from exc
    old_value, tail = rest[:n], rest[n:]
    t = re.fullmatch(r"([ \t]*)(#.*)?", tail)
    if t is None or (t.group(2) and not t.group(1)):
        raise ValueError(_t("config_writer.line", key=key))
    try:
        alone = yaml.safe_load("%s: %s" % (key, old_value))
    except yaml.YAMLError:
        alone = _MISSING
    if not isinstance(alone, dict) or alone.get(key, _MISSING) != parsed:
        # The line alone does not hold the whole value: it continues on the
        # next line(s). Replacing only this line would leave the rest behind.
        raise ValueError(_t("config_writer.multi_line", key=key))
    gap, comment = t.group(1), t.group(2)
    if comment and len(vtext) != len(old_value):
        column = len(head) + len(old_value) + len(gap)
        width = len(head) + len(vtext)
        if width < column:
            gap = " " * (column - width)
    return head + vtext + gap + (comment or "") + cr


# --- writing values -----------------------------------------------------------

def _format_value(value):
    """`value` as YAML that reads back as exactly `value`, on one line."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(_t("config_writer.not_finite", value=repr(value)))
        s = repr(float(value))
        mant, e, exp = s.partition("e")
        if e and "." not in mant:
            # PyYAML reads 1e-05 (no dot) as a STRING; 1.0e-05 is a float.
            s = mant + ".0e" + exp
        return s
    if isinstance(value, str):
        return _quote(value)
    raise TypeError(_t("config_writer.bad_type", type=type(value).__name__))


def _quote(s):
    """A double-quoted YAML scalar. Backslashes are escaped, so a Windows
    path the caller passes through stays a valid, identical value - the
    \\U-escape trap the config.yaml comments warn caregivers about."""
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif o < 0x20 or 0x7f <= o <= 0x9f:
            out.append("\\x%02x" % o)
        elif o in (0x2028, 0x2029, 0xFEFF):     # YAML line breaks, BOM
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _check_writable(path):
    """Refuse a read-only config.yaml before anything is written.

    Someone made it read-only on purpose (a caregiver locking tuned settings,
    an administrator). os.replace would quietly put a writable file in its
    place on Linux and fail half-way on Windows; neither is an answer.
    """
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    if not (mode & stat.S_IWRITE) or not os.access(path, os.W_OK):
        raise PermissionError(_t("config_writer.read_only", path=path))


def _remove(path):
    """Delete `path` even if it carries the read-only bit (Windows refuses to
    delete a read-only file); never raises."""
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass
    try:
        os.remove(path)
    except OSError:
        pass


def _atomic_write(path, data):
    """Replace `path` with `data`: a crash or a full disk can leave the old
    file or the new one, never half of one - and never a stray .tmp."""
    tmp = path + ".tmp"
    if os.path.lexists(tmp):
        _remove(tmp)                # left by a crash during an earlier save
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            # os.replace puts a NEW file in place: keep the old one's mode
            # (e.g. a Linux config a caregiver made private) - but never a
            # read-only bit, which would make the next save (and the
            # cleanup below) fail on Windows.
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode) | stat.S_IWRITE)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        _remove(tmp)
        raise


# --- checking what was written ------------------------------------------------

def _same(got, want):
    if isinstance(want, bool) or isinstance(got, bool):
        return type(got) is bool and type(want) is bool and got == want
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return float(got) == float(want)
    if config.is_auto(want):
        return got == "auto"
    return got == want


def _verify(path, before_raw, values, before_cfg):
    """Read `path` back as the driver will; raise ValueError on any surprise."""
    # The loader's own warning lines are collected, not printed: the caller
    # (a settings window) gets them in the error instead.
    notes = []
    cfg, problem = config.read_config(path, report=notes.append)
    if problem is not None:
        raise ValueError(problem)
    with open(path, "rb") as f:
        data = f.read()
    raw = _parse(data[len(_BOM):].decode("utf-8") if data.startswith(_BOM)
                 else data.decode("utf-8"), path)
    if raw != dict(before_raw, **values):
        changed = sorted(k for k in set(raw) | set(before_raw) | set(values)
                         if raw.get(k, _MISSING)
                         != dict(before_raw, **values).get(k, _MISSING))
        raise ValueError(_t("config_writer.read_back_differs",
                           keys=", ".join(changed)))
    for key, want in values.items():
        got = cfg.get(key, _MISSING)
        if not _same(got, want):
            raise ValueError(_t("config_writer.unusable", key=key,
                               want=repr(want),
                               got=repr(None if got is _MISSING else got),
                               notes="".join(" " + n for n in notes)))
    if config.can_click(before_cfg) and not config.can_click(cfg):
        # The cursor would still move and nothing could be pressed: for this
        # user that is a locked computer, not a preference.
        raise ValueError(_t("config_writer.no_click"))
