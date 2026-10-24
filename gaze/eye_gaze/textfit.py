# gaze/eye_gaze/textfit.py
"""Lines of text laid out to fit a screen (OpenCV's Hershey font).

The calibration result screen was laid out for a large monitor: on a
1280x720 logical screen (a 1080p laptop at 150 %, as this DPI-unaware
process sees it) or a 1024x768 one, its last hints - the camera and light
advice, exactly the ones needed after a poor calibration - were drawn below
the bottom edge.

fit() wraps every line to the screen width by measuring the real text, and
when the whole does not fit the height it first makes the text smaller
(down to MIN_K of its full size), then drops the lines with the lowest
`drop` rank first. Lines with drop=None are always kept; if even those do
not fit at MIN_K, the text goes on shrinking (down to LAST_K) instead.
"""
TITLE_SCALE = 1.2       # the first line, at full size
BODY_SCALE = 0.9
THICKNESS = 2
TITLE_STEP = 60         # px from one baseline to the next, at full size
BODY_STEP = 40
GAP = 14                # px more after each of the given lines
MARGIN_X = 40
MARGIN_BOTTOM = 20
SHRINK = 0.9            # each try is this much smaller than the last
MIN_K = 0.62            # body text ~0.56: the smallest before lines go
LAST_K = 0.35           # the kept lines alone, on a tiny screen


def _cv2_width(text, scale, thick):
    import cv2
    return cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)[0][0]


def wrap(text, width_px, scale, thick=THICKNESS, measure=_cv2_width):
    """`text` as lines no wider than `width_px` (a single word wider than
    that stands alone on its line). [""] for an empty text."""
    words = str(text).split()
    if not words:
        return [""]
    out, cur = [], words[0]
    for w in words[1:]:
        cand = cur + " " + w
        if measure(cand, scale, thick) <= width_px:
            cur = cand
        else:
            out.append(cur)
            cur = w
    out.append(cur)
    return out


def _ks():
    k, out = 1.0, []
    while k >= MIN_K:
        out.append(k)
        k *= SHRINK
    return out


def _layout(lines, sw, sh, k, measure, force=False):
    """[(text, colour, scale, (x, y))] for `lines` at size `k`, or None when
    they do not fit the height (force: placed from the top anyway)."""
    mx = max(10, int(round(MARGIN_X * k)))
    room = max(40, sw - 2 * mx)
    parts = []          # (text, or None for a gap; colour, scale, step)
    for i, (text, colour, _drop) in enumerate(lines):
        scale = (TITLE_SCALE if i == 0 else BODY_SCALE) * k
        step = int(round((TITLE_STEP if i == 0 else BODY_STEP) * k))
        for part in wrap(text, room, scale, THICKNESS, measure):
            parts.append((part, colour, scale, step))
        parts.append((None, None, None, int(round(GAP * k))))
    if not lines:
        return []
    last = max(i for i, p in enumerate(parts) if p[0] is not None)
    height = sum(p[3] for p in parts[:last])    # first to last baseline
    # The first baseline sits a sixth down where there is room (the look on
    # a large screen); higher, down to just under the top edge, where not.
    top = int(round(TITLE_SCALE * k * 22)) + 12
    descent = int(round(BODY_SCALE * k * 10)) + THICKNESS
    y0 = min(max(60, sh // 6), sh - MARGIN_BOTTOM - descent - height)
    if y0 < top:
        if not force:
            return None
        y0 = top
    out, y = [], y0
    for text, colour, scale, step in parts:
        if text is not None:
            out.append((text, colour, scale, (mx, y)))
        y += step
    return out


def fit(lines, sw, sh, measure=_cv2_width):
    """Lay out [(text, colour, drop)] on a `sw` x `sh` screen.

    Returns (placed, dropped): placed = [(text, colour, scale, (x, y))] with
    THICKNESS, the first line as the title; dropped = the lines left out
    (most general first). Never raises: on a fault every line is placed at
    full size from the top, as before."""
    try:
        lines = [(str(t), c, d) for t, c, d in lines]
        keep = list(lines)
        ranks = sorted({d for _, _, d in lines if d is not None})
        dropped = []
        for k in _ks():
            placed = _layout(keep, sw, sh, k, measure)
            if placed is not None:
                return placed, dropped
        k_min = _ks()[-1]
        for rank in ranks:
            gone = [ln for ln in keep if ln[2] == rank]
            keep = [ln for ln in keep if ln[2] != rank]
            dropped.extend(gone)
            placed = _layout(keep, sw, sh, k_min, measure)
            if placed is not None:
                return placed, dropped
        k = k_min * SHRINK
        while k >= LAST_K:
            placed = _layout(keep, sw, sh, k, measure)
            if placed is not None:
                return placed, dropped
            k *= SHRINK
        return _layout(keep, sw, sh, LAST_K, measure, force=True), dropped
    except Exception:
        out, y = [], 60
        for i, (text, colour, _d) in enumerate(lines):
            out.append((text, colour, TITLE_SCALE if i == 0 else BODY_SCALE,
                        (MARGIN_X, y)))
            y += TITLE_STEP if i == 0 else BODY_STEP
        return out, []
