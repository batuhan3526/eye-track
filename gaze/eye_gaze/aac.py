# gaze/eye_gaze/aac.py
"""In-driver AAC: read OptiKey's dynamic-keyboard XML files and let the user
pick a phrase by looking at it.

The phrases come from the very same XML files OptiKey uses, so there is one
place to edit and two ways to use them. We need our own grid because OptiKey's
Speak action goes through SAPI and cannot reach the local neural voice.
"""
import os
import re
import xml.etree.ElementTree as ET

from eye_gaze.i18n import t as _t, t_ascii

_DEFAULT_BG = (60, 60, 60)
_DEFAULT_FG = (255, 255, 255)


class Phrase:
    """One tile: what it says, what it speaks, where it leads."""

    __slots__ = ("label", "text", "bg", "fg", "target", "locked")

    def __init__(self, label, text, bg=_DEFAULT_BG, fg=_DEFAULT_FG, target=None):
        self.label = label
        self.text = text
        self.bg = bg
        self.fg = fg
        self.target = target
        self.locked = is_placeholder(text) or is_placeholder(label)

    def __repr__(self):
        return f"Phrase({self.label!r}, target={self.target!r}, locked={self.locked})"


class Board:
    """One grid: a category menu or a page of phrases."""

    __slots__ = ("key", "name", "phrases")

    def __init__(self, key, name, phrases):
        self.key = key
        self.name = name
        self.phrases = phrases

    def __repr__(self):
        return f"Board({self.key!r}, {len(self.phrases)} cumle)"


def is_placeholder(text):
    """True for unfilled template text like '[Aile Adi 1] gelsin.'

    These must never be spoken out loud - the user would be saying a template
    to a visitor. They are shown greyed out until someone fills them in.

    The brackets have to be a real pair, in order: locking a phrase this user
    actually needs is not a harmless mistake, and they cannot edit the file to
    unlock it themselves.
    """
    t = text or ""
    i = t.find("[")
    return i >= 0 and t.find("]", i + 1) > i


def hex_to_bgr(value, default):
    """'#C0392B' -> (43, 57, 192). Anything unparseable returns `default`."""
    v = (value or "").strip().lstrip("#")
    if len(v) != 6:
        return default
    try:
        r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    except ValueError:
        return default
    return (b, g, r)


def _child_text(el, tag):
    """Text of one child element, whitespace collapsed.

    OptiKey wraps long labels across source lines; drawn as they come, the
    newline and its indentation land in the middle of a tile.
    """
    node = el.find(tag)
    if node is None or not node.text:
        return ""
    return re.sub(r"\s+", " ", node.text).strip()


def _speakable(el):
    """False for a key whose only job is to switch OptiKey's own keyboard.

    Anything carrying its own <Text> is speech, and so is an explicit Speak
    action. The rule is deliberately "this key cannot say anything" rather than
    a blacklist of action names: a phrase that merely mentions a keyboard must
    keep working.
    """
    if el.find("Text") is not None:
        return True
    actions = [(a.text or "").strip() for a in el.findall("Action")]
    if any(a == "Speak" for a in actions):
        return True
    return not any(a.endswith("Keyboard") for a in actions)


def parse_keyboard(xml_text, key):
    """Parse one OptiKey dynamic-keyboard XML into a Board. Raises on bad XML."""
    root = ET.fromstring(xml_text)
    name = _child_text(root, "Name") or key
    phrases = []
    content = root.find("Content")
    for el in (content if content is not None else []):
        if not el.tag.endswith("DynamicKey"):
            continue
        label = _child_text(el, "Label")
        text = _child_text(el, "Text") or label
        change = el.find("ChangeKeyboard")
        target = (change.text or "").strip() if change is not None and change.text else None
        if not label and not text and not target:
            continue
        if target is None and not _speakable(el):
            # An OptiKey-only key: <Label>KLAVYE</Label> with
            # <Action>Alpha1Keyboard</Action> and no <Text>. It switches
            # OptiKey to its own alphabet, which this grid cannot do. Shown
            # here it would be a tile that, when selected, says the word
            # "KLAVYE" out loud to whoever is in the room.
            continue
        phrases.append(Phrase(
            label=label or text,
            text=text,
            bg=hex_to_bgr(el.get("BackgroundColor"), _DEFAULT_BG),
            fg=hex_to_bgr(el.get("ForegroundColor"), _DEFAULT_FG),
            target=target))
    return Board(key, name, phrases)


def load_boards(directory):
    """All .xml keyboards in `directory`, keyed by filename without extension.

    A broken file is skipped, never fatal: one bad XML must not cost the user
    every other phrase they have.
    """
    boards = {}
    try:
        names = sorted(os.listdir(directory))
    except Exception:
        return boards
    for fname in names:
        if not fname.lower().endswith(".xml"):
            continue
        key = fname[:-4]
        try:
            with open(os.path.join(directory, fname), "r", encoding="utf-8") as f:
                boards[key] = parse_keyboard(f.read(), key)
        except Exception:
            continue
    return boards


BACK = "__geri__"
NEXT = "__devam__"
PER_PAGE = 7        # 9 hucrenin 2'si GERI/DEVAM icin ayrilir


def grid_shape(n):
    """Rows/cols for n tiles, capped at 3x3. Big targets beat dense ones: the
    measured calibration error is 100-134 px, so tiles stay huge."""
    n = max(1, min(int(n), 9))
    if n <= 1:
        return (1, 1)
    if n <= 2:
        return (1, 2)
    if n <= 4:
        return (2, 2)
    if n <= 6:
        return (2, 3)
    return (3, 3)


def cell_rects(count, w, h, pad=16):
    """Tile rectangles (x, y, w, h) laid out over a w x h screen."""
    rows, cols = grid_shape(count)
    cw = max(1, (int(w) - pad * (cols + 1)) // cols)
    ch = max(1, (int(h) - pad * (rows + 1)) // rows)
    rects = []
    for i in range(min(int(count), rows * cols)):
        r, c = divmod(i, cols)
        rects.append((pad + c * (cw + pad), pad + r * (ch + pad), cw, ch))
    return rects


def cell_at(x, y, rects):
    """Index of the tile containing (x, y), or None.

    The gaps between tiles belong to nobody on purpose: with dwell selection a
    glance that lands between two phrases must say neither of them.
    """
    for i, (rx, ry, rw, rh) in enumerate(rects):
        if rx <= x < rx + rw and ry <= y < ry + rh:
            return i
    return None


def paginate(phrases, per_page=PER_PAGE):
    """Split phrases into pages. Always at least one (possibly empty) page."""
    per_page = max(1, int(per_page))
    if not phrases:
        return [[]]
    return [phrases[i:i + per_page] for i in range(0, len(phrases), per_page)]


def board_phrases(board, is_root, root_key=None):
    """The phrases of `board` minus its own link back to the main menu.

    Every shipped sub-board ends with a "Geri / Ana Menü" key. Left in place it
    sits beside our own GERI tile: two ways back, in two different corners, and
    one of only nine huge tiles spent saying the same thing twice. With eye
    control the POSITION of a tile is how it is found, so there is exactly one
    way back and it is always in the same place.

    Filtering here rather than in page_tiles' output keeps pagination honest -
    removing a tile afterwards can leave the last page holding nothing but a
    GERI box.
    """
    if is_root or not root_key:
        return list(board.phrases)
    return [p for p in board.phrases if p.target != root_key]


def page_tiles(board, page, pages, is_root, root_key=None):
    """Tiles shown for one page: its phrases plus DEVAM / GERI navigation.

    At most PER_PAGE phrases plus the two navigation tiles, so the result can
    never exceed the 9 rectangles cell_rects lays out - a tenth tile would be
    drawn nowhere and could not be selected, with nothing to say why.
    """
    chunks = paginate(board_phrases(board, is_root, root_key))
    tiles = list(chunks[page]) if 0 <= page < len(chunks) else []
    if pages > 1:
        tiles.append(Phrase(label=next_label(), text="", bg=(90, 90, 90),
                            target=NEXT))
    if not is_root:
        tiles.append(Phrase(label=back_label(), text="", bg=(90, 90, 90),
                            target=BACK))
    return tiles


def next_label():
    """The "next page" tile ("DEVAM >>"; ASCII: the fallback draws it with
    OpenCV)."""
    return t_ascii("aac.next")


def back_label():
    """The "back" tile and scan step ("<< GERI")."""
    return t_ascii("aac.back")


def gaze_hint():
    """The title's instruction while the board follows the gaze."""
    return t_ascii("aac.gaze_hint")


_FOLD = str.maketrans({"ğ": "g", "Ğ": "G", "ı": "i", "İ": "I", "ş": "s",
                       "Ş": "S", "ç": "c", "Ç": "C", "ö": "o", "Ö": "O",
                       "ü": "u", "Ü": "U", "â": "a", "î": "i", "û": "u"})

_WINDOWS = os.name == "nt"      # tests flip it to take the other path
_FONT_CANDIDATES = (r"C:\Windows\Fonts\segoeui.ttf",
                    r"C:\Windows\Fonts\arial.ttf",
                    r"C:\Windows\Fonts\calibri.ttf")


def _font_paths():
    """Fonts to try, in order. Linux: the bundled DejaVu Sans - no system
    font can be counted on, and Pillow's built-in bitmap font has no
    Turkish letters at all. Windows keeps its own fonts first, exactly as
    before (the grid the user knows), with DejaVu only as the last resort
    before that bitmap font."""
    try:
        from eye_gaze.platform import linux_misc
        bundled = (linux_misc.font_path(),)
    except Exception:
        bundled = ()
    if _WINDOWS:
        return _FONT_CANDIDATES + bundled
    return bundled


_LOCKED_BG = (70, 70, 70)
_LOCKED_FG = (150, 150, 150)

# Height reserved for the board title. Without it the title was drawn first at
# the very top and then painted over by the first row of tiles - present in the
# code, invisible on screen.
HEADER = 52

_PILLOW_WARNED = False
_FONT_WARNED = False


def ascii_fold(text):
    """Turkish letters -> ASCII. Only used when Pillow is unavailable and we
    have to fall back to OpenCV's Hershey fonts, which cannot draw them."""
    return (text or "").translate(_FOLD)


def wrap_lines(text, max_chars):
    """Word-wrap `text` into lines of at most `max_chars` characters.

    A word longer than the line is split rather than left whole: left alone it
    runs straight out of its tile and across the neighbouring phrase.
    """
    max_chars = max(1, int(max_chars))
    words = []
    for word in (text or "").split():
        while len(word) > max_chars:
            words.append(word[:max_chars])
            word = word[max_chars:]
        words.append(word)
    if not words:
        return []
    lines, cur = [], words[0]
    for word in words[1:]:
        if len(cur) + 1 + len(word) <= max_chars:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def board_rects(count, w, h):
    """Where the tiles go on a w x h board - for DRAWING and for HIT TESTING.

    Both must come from here. If drawing offset the tiles to clear the title
    and hit testing used the raw grid, every glance would select the phrase
    above the one being looked at, and the screen would look perfectly right.
    """
    return [(x, y + HEADER, tw, th)
            for (x, y, tw, th) in cell_rects(count, w, h - HEADER)]


def _load_font(size):
    from PIL import ImageFont
    for path in _font_paths():
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    global _FONT_WARNED
    if not _FONT_WARNED:
        _FONT_WARNED = True
        print("UYARI: AAC yazi tipi bulunamadi (%s); yedek yazi tipi Turkce"
              " harfleri gostermeyebilir." % ascii(_font_paths()))
    return ImageFont.load_default()


def _draw_pillow(tiles, rects, w, h, title):
    import numpy as np
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), (18, 18, 18))
    d = ImageDraw.Draw(img)
    d.text((20, 8), title, font=_load_font(30), fill=(210, 210, 210))
    for tile, (x, y, tw, th) in zip(tiles, rects):
        bg = _LOCKED_BG if tile.locked else tile.bg
        fg = _LOCKED_FG if tile.locked else tile.fg
        d.rectangle([x, y, x + tw, y + th], fill=(bg[2], bg[1], bg[0]))
        size = max(20, min(56, th // 5))
        font = _load_font(size)
        lines = wrap_lines(tile.label, max(8, int(tw / (size * 0.55))))
        if tile.locked:
            lines = lines + [_t("aac.not_filled")]
        total = len(lines) * (size + 8)
        cy = y + (th - total) // 2
        for line in lines:
            box = d.textbbox((0, 0), line, font=font)
            d.text((x + (tw - (box[2] - box[0])) // 2, cy), line,
                   font=font, fill=(fg[2], fg[1], fg[0]))
            cy += size + 8
    return np.asarray(img)[:, :, ::-1].copy()      # RGB -> BGR


def _draw_opencv(tiles, rects, w, h, title):
    """Fallback when Pillow is missing: readable, but Turkish letters folded."""
    import cv2
    import numpy as np

    img = np.full((h, w, 3), 18, dtype=np.uint8)
    cv2.putText(img, ascii_fold(title), (20, 36), cv2.FONT_HERSHEY_SIMPLEX,
                0.9, (210, 210, 210), 2)
    for tile, (x, y, tw, th) in zip(tiles, rects):
        bg = _LOCKED_BG if tile.locked else tile.bg
        fg = _LOCKED_FG if tile.locked else tile.fg
        cv2.rectangle(img, (x, y), (x + tw, y + th), bg, -1)
        lines = wrap_lines(ascii_fold(tile.label), max(8, tw // 22))
        cy = y + th // 2 - (len(lines) - 1) * 22
        for line in lines:
            (lw, _), _ = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2)
            cv2.putText(img, line, (x + (tw - lw) // 2, cy),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, fg, 2)
            cy += 44
    return img


def draw_board(tiles, w, h, title):
    """Render one page of tiles as a BGR image ready for cv2.imshow."""
    global _PILLOW_WARNED
    rects = board_rects(len(tiles), w, h)
    try:
        return _draw_pillow(tiles, rects, w, h, title)
    except Exception as exc:
        # Once, not once per frame - but never in silence: the fallback strips
        # the Turkish letters out of every phrase on screen, and a caregiver
        # who never sees a reason will assume that is simply how it looks.
        if not _PILLOW_WARNED:
            _PILLOW_WARNED = True
            print("AAC: Pillow ile cizilemedi (%s: %s); yedek cizim"
                  " kullaniliyor - TURKCE HARFLER SADELESIR (Agrim, Usuyorum)."
                  % (type(exc).__name__, exc))
        return _draw_opencv(tiles, rects, w, h, title)


class AacNavigator:
    """Where the user is in the phrase tree. No drawing, no windows - so the
    whole navigation behaviour is testable without a screen."""

    def __init__(self, boards, root_key):
        self._boards = boards or {}
        self._root = root_key
        self.reset()

    def reset(self):
        self._key = self._root
        self._page = 0

    @property
    def board(self):
        return self._boards.get(self._key)

    @property
    def at_root(self):
        """True on the main menu - where 'back' can only mean 'close'."""
        return self._key == self._root

    def _shown(self):
        """The phrases actually on this board, after its own back key is
        dropped. Page counting has to use THESE: counting the dropped key would
        advertise a DEVAM tile leading to a page holding nothing but GERI."""
        b = self.board
        if b is None:
            return []
        return board_phrases(b, self._key == self._root, self._root)

    def _pages(self):
        return len(paginate(self._shown()))

    def title(self):
        b = self.board
        if b is None:
            return "AAC"
        pages = self._pages()
        suffix = f"  ({self._page + 1}/{pages})" if pages > 1 else ""
        return f"{b.name}{suffix}   -   {gaze_hint()}"

    def tiles(self):
        b = self.board
        if b is None:
            return []
        return page_tiles(b, self._page, self._pages(), self._key == self._root,
                          self._root)

    def rects(self, w, h):
        """Where those tiles are. board_rects, NOT cell_rects: draw_board
        reserves a header strip for the title, and hit testing that ignored it
        would select the phrase one row above the one being looked at while the
        screen looked perfectly correct."""
        return board_rects(len(self.tiles()), w, h)

    def choose(self, index):
        """Handle a tile selection. Returns the text to speak, or None when the
        tile was navigation (or locked, or out of range)."""
        tiles = self.tiles()
        if not (0 <= index < len(tiles)):
            return None
        tile = tiles[index]
        if tile.target == BACK:
            self._key, self._page = self._root, 0
            return None
        if tile.target == NEXT:
            # Wraps: DEVAM is the only way off a page, and the root board has
            # no GERI, so a last page that led nowhere would be a dead end.
            self._page = (self._page + 1) % max(1, self._pages())
            return None
        if tile.target:
            if tile.target in self._boards:
                self._key, self._page = tile.target, 0
            return None
        if tile.locked:
            return None
        return tile.text or None

    def reachable(self):
        """The boards the user can get to from the main menu, in folder
        order. One folder may hold more than one language's pack (the
        shipped one holds Turkish and English): only the pack the grid opens
        on is ever on screen. With no main menu, every board (as before)."""
        if self._root not in self._boards:
            return list(self._boards.values())
        seen, todo = {self._root}, [self._root]
        while todo:
            for p in self._boards[todo.pop()].phrases:
                if p.target and p.target in self._boards \
                        and p.target not in seen:
                    seen.add(p.target)
                    todo.append(p.target)
        return [b for k, b in self._boards.items() if k in seen]

    def all_phrases(self):
        """Every speakable phrase the user can reach - what the cache warms.
        Not the other language's pack beside it: warming that would render
        its phrases in this language's voice for nobody."""
        out = []
        for b in self.reachable():
            for p in b.phrases:
                if not p.target and not p.locked and p.text:
                    out.append(p.text)
        return out


_HOVER = (0, 255, 255)          # bright yellow: the tile a press would speak
_HOVER_LOCKED = (0, 0, 255)     # red: pressing here does nothing


def overlay_pointer(img, rects, index, x, y, locked=False):
    """Return a copy of `img` with the pointer and the tile it is on marked.

    Reported from the field: "F5'e basiyorum, yeni ekran geliyor ama orada ne
    yapacagimi bilmiyorum - maus ile isaretlenmedi." The board was drawn and
    nothing showed where the user was pointing, so there was no way to know
    what the click key was about to say out loud. For an AAC grid that is the
    difference between choosing a sentence and guessing one.

    A COPY, because the board underneath is rendered once and reused for every
    frame; painting into it would smear each pointer position onto the next.
    Cheap enough to run per frame - a full re-render takes ~36 ms, this ~2.
    """
    import cv2
    out = img.copy()
    if index is not None and 0 <= index < len(rects):
        rx, ry, rw, rh = rects[index]
        colour = _HOVER_LOCKED if locked else _HOVER
        cv2.rectangle(out, (rx, ry), (rx + rw - 1, ry + rh - 1), colour, 8)
    # The pointer itself, so a glance that lands BETWEEN tiles is visible as
    # such rather than looking like the grid has stopped responding.
    cv2.circle(out, (int(x), int(y)), 14, (0, 0, 0), -1)
    cv2.circle(out, (int(x), int(y)), 11, (255, 255, 255), -1)
    cv2.circle(out, (int(x), int(y)), 5, (0, 0, 0), -1)
    return out
