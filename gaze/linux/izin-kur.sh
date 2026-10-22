#!/bin/sh
# gaze/linux/izin-kur.sh - one-time input permission setup for Goz Faresi.
#
# The program starts it through pkexec (as root):   izin-kur.sh <user>
#   1. /etc/udev/rules.d/99-gozfaresi.rules: /dev/uinput for the input group
#      (the program's virtual pointer and keyboard);
#   2. <user> joins the input group (reads the keyboards and pads in
#      /dev/input) - it applies from the next login;
#   3. the uinput module now and at every boot (/etc/modules-load.d).
# Exit status 0 = done.
#
# Safe to run again: whatever is already in place is left alone. It touches
# no other rule file, never drops a group membership (usermod -a), and a rule
# that fails `udevadm verify` is neither installed nor reloaded.
#
# Test mode (the test suite only - pkexec clears the environment, so it
# cannot be switched on through pkexec): GOZFARESI_IZIN_ROOT=<dir> puts /etc
# and /dev under <dir> and keeps PATH, so stub usermod/modprobe/udevadm run
# instead of the system's.
set -eu

ROOT=${GOZFARESI_IZIN_ROOT:-}

die() {
    code=$1
    shift
    echo "HATA: $*" >&2
    exit "$code"
}

if [ -z "$ROOT" ]; then
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    export PATH
    [ "$(id -u)" = "0" ] || die 2 "yonetici olarak calismali: pkexec $0 <kullanici>"
fi

user=${1:-}
case "$user" in
    "") die 3 "kullanici adi verilmedi: $0 <kullanici>" ;;
    root) die 3 "root icin kurulmaz; kendi kullanici adinizi verin" ;;
    -* | *[!A-Za-z0-9._@-]*) die 3 "gecersiz kullanici adi: $user" ;;
esac
id "$user" >/dev/null 2>&1 || die 3 "boyle bir kullanici yok: $user"

RULE_DIR="$ROOT/etc/udev/rules.d"
RULE="$RULE_DIR/99-gozfaresi.rules"
MOD_DIR="$ROOT/etc/modules-load.d"
MOD_CONF="$MOD_DIR/gozfaresi.conf"
UINPUT="$ROOT/dev/uinput"

# Byte for byte gaze/linux/99-gozfaresi.rules (a test compares them).
rule_text() {
    cat <<'RULE'
# /etc/udev/rules.d/99-gozfaresi.rules - installed by izin-kur.sh (Goz Faresi).
# /dev/uinput for the input group: the program's virtual pointer and keyboard.
# static_node: the rule also applies before the uinput module is loaded.
KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
RULE
}

have() { command -v "$1" >/dev/null 2>&1; }

udev_version() {
    udevadm --version 2>/dev/null | sed -n '1s/^\([0-9][0-9]*\).*/\1/p'
}

# 1. The input group (every systemd distribution has it already).
if ! getent group input >/dev/null 2>&1; then
    groupadd --system input || die 4 "input grubu olusturulamadi"
fi

# 2. The udev rule: written beside the target under a name udev does not
#    read (not *.rules), checked, then moved into place in one step.
rule_changed=0
mkdir -p "$RULE_DIR"
if [ -f "$RULE" ] && rule_text | cmp -s - "$RULE"; then
    echo "udev kurali zaten kurulu: $RULE"
else
    tmp="$RULE_DIR/.99-gozfaresi.rules.yeni.$$"
    trap 'rm -f "$tmp"' EXIT
    rule_text >"$tmp"
    rule_text | cmp -s - "$tmp" || die 5 "udev kurali yazilamadi (disk dolu mu?)"
    v=$(udev_version || true)
    if [ -n "$v" ] && [ "$v" -ge 254 ]; then
        udevadm verify --no-style "$tmp" >/dev/null \
            || die 5 "udev kurali dogrulanamadi; kurulmadi"
    elif [ -n "$v" ] && [ "$v" -ge 253 ]; then
        udevadm verify "$tmp" >/dev/null \
            || die 5 "udev kurali dogrulanamadi; kurulmadi"
    fi
    chmod 0644 "$tmp"
    mv -f "$tmp" "$RULE"
    trap - EXIT
    rule_changed=1
    echo "udev kurali kuruldu: $RULE"
fi

# 3. The user joins the input group (-a: the other groups stay).
if id -nG "$user" | tr ' ' '\n' | grep -qx input; then
    echo "$user zaten input grubunda"
else
    # usermod knows only local users; gpasswd also takes directory users.
    usermod -aG input "$user" 2>/dev/null \
        || gpasswd -a "$user" input >/dev/null \
        || die 6 "$user input grubuna eklenemedi"
    echo "$user input grubuna eklendi"
fi

# 4. The uinput module: at every boot, and now.
mkdir -p "$MOD_DIR"
if [ "$(cat "$MOD_CONF" 2>/dev/null || true)" != "uinput" ]; then
    tmpm="$MOD_CONF.yeni.$$"
    printf 'uinput\n' >"$tmpm"
    chmod 0644 "$tmpm"
    mv -f "$tmpm" "$MOD_CONF"
fi
modprobe uinput 2>/dev/null || [ -e "$UINPUT" ] \
    || die 7 "uinput modulu yuklenemedi"

# 5. The rule applied to the node that already exists.
if have udevadm; then
    if [ "$rule_changed" = 1 ]; then
        udevadm control --reload-rules \
            || echo "UYARI: udev kurallari yeniden yuklenemedi"
    fi
    udevadm trigger --action=change --sysname-match=uinput || true
    udevadm settle --timeout=5 || true
fi
# Without a running udev the node keeps root:root: set it as the rule says.
if [ -c "$UINPUT" ]; then
    chgrp input "$UINPUT" && chmod 0660 "$UINPUT" || true
fi

echo "TAMAM: izinler kuruldu. Oturumu kapatip yeniden acin."
exit 0
