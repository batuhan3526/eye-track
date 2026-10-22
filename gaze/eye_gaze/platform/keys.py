# gaze/eye_gaze/platform/keys.py
"""Key names shared by both systems, and their Linux codes.

config.yaml names keys the way the Windows `keyboard` library does ("space",
"f12", "scroll lock", "page up", letters, digits). Keeping exactly those names
on Linux means one config file works on both systems; each Linux backend turns
a name into its own code here: evdev key codes (linux/input-event-codes.h,
fixed by the kernel ABI) and X11 keysym names (the strings XStringToKeysym
takes).

KNOWN holds one name per physical key, so name -> code -> name is lossless.
The generic modifiers ("shift", "ctrl", "alt", "windows") and a few common
spellings are accepted as input aliases of the left-hand key but are never
returned. Letters are by position on a US layout: on a Turkish Q keyboard the
key that types "ı" is still called "i" here.

Punctuation is left out on purpose: which key types "," or "-" depends on the
layout, and a driver key bound to one would move with the layout setting.
"""

# name -> (evdev code, X11 keysym name)
_TABLE = {
    "esc": (1, "Escape"),
    "backspace": (14, "BackSpace"),
    "tab": (15, "Tab"),
    "enter": (28, "Return"),
    "space": (57, "space"),
    "caps lock": (58, "Caps_Lock"),
    "num lock": (69, "Num_Lock"),
    "scroll lock": (70, "Scroll_Lock"),
    "print screen": (99, "Print"),
    "pause": (119, "Pause"),
    "menu": (127, "Menu"),
    "home": (102, "Home"),
    "up": (103, "Up"),
    "page up": (104, "Prior"),
    "left": (105, "Left"),
    "right": (106, "Right"),
    "end": (107, "End"),
    "down": (108, "Down"),
    "page down": (109, "Next"),
    "insert": (110, "Insert"),
    "delete": (111, "Delete"),
    "left ctrl": (29, "Control_L"),
    "right ctrl": (97, "Control_R"),
    "left shift": (42, "Shift_L"),
    "right shift": (54, "Shift_R"),
    "left alt": (56, "Alt_L"),
    "right alt": (100, "Alt_R"),
    "left windows": (125, "Super_L"),
    "right windows": (126, "Super_R"),
}

# Letters: KEY_Q..KEY_P = 16..25, KEY_A..KEY_L = 30..38, KEY_Z..KEY_M = 44..50.
for _row, _first in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
    for _i, _ch in enumerate(_row):
        _TABLE[_ch] = (_first + _i, _ch)

# Digits: KEY_1..KEY_9 = 2..10, KEY_0 = 11.
for _d in range(1, 10):
    _TABLE[str(_d)] = (1 + _d, str(_d))
_TABLE["0"] = (11, "0")

# F1..F10 = 59..68, F11 = 87, F12 = 88, F13..F24 = 183..194.
for _n in range(1, 11):
    _TABLE["f%d" % _n] = (58 + _n, "F%d" % _n)
_TABLE["f11"] = (87, "F11")
_TABLE["f12"] = (88, "F12")
for _n in range(13, 25):
    _TABLE["f%d" % _n] = (170 + _n, "F%d" % _n)

del _row, _first, _i, _ch, _d, _n

# Input-only spellings -> the KNOWN name they mean.
_ALIASES = {
    "shift": "left shift",
    "ctrl": "left ctrl",
    "control": "left ctrl",
    "alt": "left alt",
    "alt gr": "right alt",
    "windows": "left windows",
    "escape": "esc",
    "return": "enter",
    "spacebar": "space",
    "del": "delete",
    "pgup": "page up",
    "pgdn": "page down",
    "scrolllock": "scroll lock",
}

# Other keysym names X may report for a KNOWN key.
_KEYSYM_ALIASES = {
    "Page_Up": "page up",
    "Page_Down": "page down",
    "ISO_Level3_Shift": "right alt",   # AltGr on a Turkish layout
    "Meta_L": "left alt",
    "Meta_R": "right alt",
    "KP_Enter": "enter",
}

KNOWN = frozenset(_TABLE)

_BY_EVDEV = {code: name for name, (code, _) in _TABLE.items()}
_BY_KEYSYM = {sym: name for name, (_, sym) in _TABLE.items()}
_BY_KEYSYM.update(_KEYSYM_ALIASES)


def normalize(name):
    """The KNOWN name `name` means, or None."""
    n = " ".join(str(name or "").strip().lower().split())
    n = _ALIASES.get(n, n)
    return n if n in _TABLE else None


def _entry(name):
    n = normalize(name)
    if n is None:
        # ValueError like the keyboard library's, so callers' bad-key paths
        # (KeyPresses.bad_keys) report it the same way on both systems.
        raise ValueError("unknown key name: %r" % (name,))
    return _TABLE[n]


def to_evdev(name):
    return _entry(name)[0]


def to_keysym(name):
    return _entry(name)[1]


def from_evdev(code):
    return _BY_EVDEV.get(code)


def from_keysym(sym):
    name = _BY_KEYSYM.get(sym)
    if name is None and isinstance(sym, str) and len(sym) == 1:
        # Shift held: X reports "A" for the a key.
        name = _BY_KEYSYM.get(sym.lower())
    return name
