# gaze/eye_gaze/keymap.py
"""One key, one job.

The driver's keys are hooked by FOUR separate hubs: the shared KeyPresses
(clicks, F5/F6, F2 ...), Hotkeys (quit, recalibrate, pause, drift), the
speech-stop key's own hub (F11) and the alarm's own hub (F12). Nothing stops
two of them from being given the same key in config.yaml, and then one press
does two things - e.g. alarm_key: f5 would open the AAC grid AND, held, start
the siren; click_menu_key: space would open the menu on every left click.

resolve_key_conflicts() runs once at startup, before anything is hooked, and
turns the NEWER key off (with a console line saying which and why). Priority:
the original driver keys first (the user's muscle memory), then the alarm
(calling for help beats convenience), then the click menu, then speech stop.
find_conflicts() applies the same rule to the settings screen's values, so
what the screen warns about includes exactly what startup would switch off -
plus every other shared key or button, which the screen does not allow at
all.
"""

# The driver's own keys with the driver's own defaults (what main() hooks
# when the key is missing from cfg).
CORE = (("left_click_key", ""), ("right_click_key", ""),
        ("double_click_key", ""), ("drag_key", ""), ("zoom_key", "f6"),
        ("aac_key", ""), ("osk_key", ""), ("tts_key", ""), ("stt_key", ""),
        ("quit_key", "q"), ("recalibrate_key", "c"), ("pause_key", "f8"),
        ("drift_key", "f7"))

# Newer keys, highest priority first.
NEWER = (("alarm_key", "f12"), ("click_menu_key", "f2"),
         ("speech_stop_key", "f11"))

PAD_CLICKS = ("gamepad_left", "gamepad_right", "gamepad_double",
              "gamepad_drag")


def _name(v):
    return str(v or "").strip().lower()


def _say(log, msg):
    try:
        log(str(msg).encode("ascii", "replace").decode("ascii"))
    except Exception:
        pass


def _conflicts(cfg):
    """The one rule, shared by startup and the settings screen.

    Returns [(turned_off_key, owner_key, name), ...] for `cfg` without
    changing it. A key that loses is not an owner afterwards - exactly as
    when the driver blanks it - so a later key colliding with it is not a
    conflict.
    """
    found = []
    owners = {}
    for k, d in CORE:
        n = _name(cfg.get(k, d))
        if n and n not in owners:
            owners[n] = k
    for k, d in NEWER:
        n = _name(cfg.get(k, d))
        if not n:
            continue
        if n in owners:
            found.append((k, owners[n], n))
        else:
            owners[n] = k
    pad = _name(cfg.get("gamepad_alarm", ""))
    if pad:
        for k in PAD_CLICKS:
            if _name(cfg.get(k, "")) == pad:
                found.append(("gamepad_alarm", k, pad))
                break
    return found


def resolve_key_conflicts(cfg, log=print):
    """Blank every newer key that another key already owns. Mutates cfg.

    Returns [(turned_off_key, owner_key), ...]. Never raises: it runs at
    startup, outside every guard, and a check must not cost the cursor.
    """
    off = []
    try:
        for k, owner, n in _conflicts(cfg):
            cfg[k] = ""
            off.append((k, owner))
            if k == "gamepad_alarm":
                _say(log, "UYARI: gamepad_alarm '%s' zaten %s (tiklama)"
                     " dugmesi - gamepad alarmi KAPATILDI. Tiklama"
                     " dugmesiyle alarm icin alarm_switch_hold kullanin."
                     % (n, owner))
            else:
                _say(log, "UYARI: %s '%s' zaten '%s' icin kullaniliyor - bir"
                     " tus tek is yapar, %s KAPATILDI. config.yaml'da baska"
                     " bir tus secin." % (k, n, owner, k))
    except Exception:
        pass
    return off


def find_conflicts(values):
    """The conflicts resolve_key_conflicts would settle at startup, for the
    settings screen to show BEFORE anything is saved.

    Returns [(turned_off_key, owner_key, message), ...]; the message is
    Turkish (full characters - it is for a window, not the console) and names
    both jobs by their catalog labels. `values` is not changed. Unlike the
    startup check this may raise: a screen that cannot check must not say
    "no conflicts".

    The startup conflicts come first, in startup order. After them comes
    every other pair of jobs given the same key (or the same gamepad
    button): turned_off_key is then the later job in catalog order, the one
    the user is asked to move.
    """
    from eye_gaze.actions_catalog import ACTIONS, by_key
    from eye_gaze.i18n import t

    def label(k):
        a = by_key(k)
        return a.label if a is not None else k

    out = []
    for k, owner, n in _conflicts(values):
        if k == "gamepad_alarm":
            msg = t("keymap.pad_taken", label=label(k), name=n.upper(),
                    owner=label(owner))
        else:
            msg = t("keymap.key_taken", label=label(k), name=n,
                    owner=label(owner))
        out.append((k, owner, msg))
    # Startup only settles NEWER keys against older ones; two of the
    # original keys on one key (left_click_key = right_click_key = space)
    # are both hooked and one press does both. The screen enforces "one key,
    # one job" for everything the user assigns. Holding the click switch for
    # the alarm or the click menu (alarm_switch_hold, click_menu_on_hold) is
    # a deliberate second use of the SAME binding - those are durations, not
    # assignments, so they never appear here.
    explained = {k for k, _, _ in out}
    for kind, text_key in (("key", "keymap.same_key"),
                           ("pad", "keymap.same_pad")):
        groups = {}
        for a in ACTIONS:
            if a.kind != kind:
                continue
            ident = _identity(values.get(a.key, _CODE_DEFAULTS.get(a.key, "")),
                              kind)
            if ident:
                groups.setdefault(ident, []).append(a.key)
        for ident, members in groups.items():
            left = [k for k in members if k not in explained]
            for k in left[1:]:
                owner = left[0]
                msg = t(text_key, first=label(owner), second=label(k),
                        name=ident)
                out.append((k, owner, msg))
    return out


# What the driver hooks when a binding is missing from cfg.
_CODE_DEFAULTS = dict(CORE + NEWER)


def _identity(value, kind):
    """The physical key/button a setting names: case and spacing ignored,
    keyboard aliases ("spacebar", "escape") folded into their key."""
    n = " ".join(str(value or "").split())
    if not n:
        return ""
    if kind == "pad":
        return n.upper()
    from eye_gaze.platform import keys as _keys
    n = n.lower()
    return _keys.normalize(n) or n
