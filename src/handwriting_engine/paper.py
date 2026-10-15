"""Write on a scanned page as if by hand with a real pen.

Pipeline for one page::

    analysis = scanpage.analyze(image)          # printed lines, writing areas, existing ink
    pen = calibrate(image, analysis, "ballpoint")  # match width/darkness of the ink already there
    image, report = write(image, analysis, items, db, pen=pen)

``items`` is a list of dictionaries (all coordinates in image pixels):

* ``{"text": "...", "area": 7}``: write into writing area 7 and, if the text is longer,
  continue in the empty rows below it (same column). ``"areas": [7, 8, 12]`` gives the
  exact rows. Text follows the printed line even where the scan bends it.
* ``{"text": "...", "region": [x0, y0, x1, y1]}``: write inside a box; rows bend like
  the box edges (or snap to the printed lines inside it on grid paper).
* ``{"text": "...", "at": [x, y], "width": 600}``: start at a point (baseline y).
* ``{"mark": "circle"|"box"|"underline"|"double_underline"|"check"|"cross"|"strike"|
  "arrow"|"star"|"bracket", "box": [x0, y0, x1, y1]}`` (arrow: ``"from"``/``"to"``)
* ``{"draw": [[x, y], ...]}``: a hand-drawn line through the points (a graph, a figure).
* ``{"erase": [x0, y0, x1, y1]}``: white out a region but keep the printed lines.

Every item may set ``pen`` (ballpoint, ballpoint-blue, ballpoint-red, gel, pencil,
highlighter …), ``color`` and ``size_mm`` (height of a Hangul syllable).

Paper handwriting differs from tablet handwriting. The glyphs come from the tablet;
:class:`PaperStyle` holds what changes on paper. Its defaults were fitted in a closed
loop on scanned notes: transcribed rows were erased, re-written with this module, and
the same measurements (ink band height, distance above the printed line, row width,
skeleton length, stroke ends and junctions per character) were compared with the real
rows until they agreed. On paper the strokes of a syllable sit closer together, pen
lifts sometimes leave a hairline, the ballpoint line is 0.33–0.38 mm wide, and the
writing sits about 0.14 syllable heights above the printed line. ``learn_paper_style``
in the MCP server repeats the fit on any user's own scans.
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image, ImageFilter

from . import pen as penlib
from .compose import compose


@dataclass(frozen=True)
class PaperStyle:
    x_scale: float = 1.07  # paper / tablet glyph width at equal height (with the tighter gaps below)
    size_mm: float = 5.8  # glyph unit (Hangul syllable box) on paper
    fill: float = 0.62  # glyph unit / row height when rows are narrow
    baseline: float = 0.14  # baseline above the printed line, in glyph units
    unit_per_height: float = 1.08  # glyph unit / measured height of a written row
    wobble: float = 0.022  # baseline wander along a row (syllable heights)
    wobble_len: float = 5.0  # its correlation length (syllables)
    drift_deg: float = 0.5  # how far a row may tilt away from the printed line
    size_jitter: float = 0.03  # word to word size change
    indent: float = 0.2  # gap from the left edge of an area (syllable heights)
    tremor_mm: float = 0.04  # hand tremor on drawn marks
    line_gap: float = 1.75  # row pitch on unlined paper (syllable heights)
    overshoot: float = 0.03  # strokes run on a little past their end (glyph units)
    ligature: float = 0.45  # chance that the pen stays on the paper between two strokes
    ligature_dist: float = 0.45  # ... when the next stroke starts this close (glyph units)
    ligature_ink: float = 0.6  # how much ink such a connecting hairline leaves
    compact: float = 0.9  # strokes of a syllable sit closer together on paper (1 = as on tablet)
    tracking: float = 0.5  # gaps between letters, relative to the tablet
    word_space: float = 0.8  # gaps between words, relative to the tablet
    scribble: float = 1.0  # overall strength of the fast-writing changes below (0 = tablet-neat)
    round_corners: float = 0.09  # corners rounded off by the fast hand (glyph units, smoothing radius)
    warp: float = 0.06  # slow wobble inside each letter (glyph units)
    glyph_rotate_deg: float = 3.5  # each letter turned a little
    glyph_scale: float = 0.05  # each letter a little larger or smaller
    glyph_shift: float = 0.035  # each letter a little up or down (glyph units)
    rise_deg: float = 6.0  # horizontal strokes climb to the right
    word_ligature: float = 0.25  # chance the pen drags from one letter into the next in a word
    math_height: float = 2.4  # tallest math (a fraction) in glyph units; taller is scaled down


DEFAULT_STYLE = PaperStyle()


# ---------------------------------------------------------------- calibration

def thin(mask, max_iter=30):
    """Zhang-Suen thinning (vectorised) → one-pixel centre lines."""
    m = np.pad(mask.astype(bool), 1)
    for _ in range(max_iter):
        changed = False
        for step in (0, 1):
            P2, P3, P4 = m[:-2, 1:-1], m[:-2, 2:], m[1:-1, 2:]
            P5, P6, P7 = m[2:, 2:], m[2:, 1:-1], m[2:, :-2]
            P8, P9 = m[1:-1, :-2], m[:-2, :-2]
            nb = [P2, P3, P4, P5, P6, P7, P8, P9]
            B = sum(n.astype(np.int8) for n in nb)
            seq = nb + [P2]
            A = sum(((~a) & b).astype(np.int8) for a, b in zip(seq[:-1], seq[1:]))
            if step == 0:
                c = ~(P2 & P4 & P6) & ~(P4 & P6 & P8)
            else:
                c = ~(P2 & P4 & P8) & ~(P2 & P6 & P8)
            rm = m[1:-1, 1:-1] & (B >= 2) & (B <= 6) & (A == 1) & c
            if rm.any():
                m[1:-1, 1:-1] &= ~rm
                changed = True
        if not changed:
            break
    return m[1:-1, 1:-1]


def _skeleton_len(mask):
    """Centre-line length of a binary ink mask (pixels)."""
    return max(1.0, float(thin(mask).sum()))


def measure_ink(img, analysis):
    """Width (px), colour and edge softness of the handwriting already on the page, or None."""
    return _measure(np.asarray(img.convert("RGB"), dtype=np.float32), analysis["_dark"], analysis["_ink"])


def _measure(rgb, dark, ink):
    m = ink & (dark > 0.3)
    if m.sum() < 600:
        return None
    near = scanpage_near(m) & ink
    width = float((dark * near).sum() / _skeleton_len(m))
    core = rgb[m & (dark >= np.percentile(dark[m], 60))]
    color = np.median(core, axis=0)
    return {"width_px": width, "color": tuple(int(c) for c in color),
            "mid": float(((dark > 0.15) & (dark < 0.7) & near).sum() / max(1, near.sum()))}


def scanpage_near(mask, r=2):
    out = mask.copy()
    for _ in range(r):
        out[1:] |= out[:-1].copy()
        out[:-1] |= out[1:].copy()
        out[:, 1:] |= out[:, :-1].copy()
        out[:, :-1] |= out[:, 1:].copy()
    return out


def calibrate(img, analysis, pen="ballpoint", dpi=None, db=None, size_px=None):
    """Adjust a pen so its lines match the handwriting already on this page.

    The page's ink is measured, then a sample word is drawn with the pen and measured the
    same way; width and darkness are corrected until both agree (closed loop, so the
    measurement's own bias cancels). Colour is only matched for black pens."""
    p = pen if isinstance(pen, penlib.Pen) else penlib.get_pen(pen)
    dpi = dpi or analysis["dpi"]
    target = measure_ink(img, analysis)
    if not target or p.name == "highlighter":
        return p, None
    mm = dpi / 25.4
    black = p.name in ("ballpoint", "gel", "fineliner") and max(target["color"]) - min(target["color"]) < 40
    if black:
        p = replace(p, color=tuple(int(c) for c in target["color"]))
    p = replace(p, width_mm=max(0.15, min(1.2, target["width_px"] / mm)))
    if db is None:
        return p, target
    size_px = size_px or analysis.get("letter_height") or 5.3 * mm
    st, _, _ = compose(db, "가나다 수학 함수 abc 123", width=1e9, seed=3)
    strokes = [{"points": np.stack([20 + np.asarray(s["x"]) * size_px * 0.87,
                                    20 + np.asarray(s["y"]) * size_px], 1)} for s in st if "missing" not in s]
    Wd = int(max(q["points"][:, 0].max() for q in strokes)) + 30
    Hd = int(max(q["points"][:, 1].max() for q in strokes)) + 30
    for _ in range(3):
        im = penlib.render(strokes, (Wd, Hd), dpi=dpi, pen=p, seed=5)
        rgb = np.asarray(im, dtype=np.float32)
        dark = np.clip(1 - rgb.mean(2) / 255, 0, 1)
        got = _measure(rgb, dark, dark > 0.12)
        if not got:
            break
        k = target["width_px"] / max(got["width_px"], 1e-3)
        upd = {"width_mm": float(np.clip(p.width_mm * k ** 0.9, 0.12, 1.5))}
        if black:
            td = 1 - np.array(target["color"]) / 255
            gd = 1 - np.array(got["color"]) / 255
            pd = 1 - np.array(p.color) / 255
            nd = np.clip(pd * (td / np.maximum(gd, 1e-3)) ** 0.8, 0, 1)
            upd["color"] = tuple(int(round(255 * (1 - v))) for v in nd)
        p = replace(p, **upd)
        if abs(k - 1) < 0.04:
            break
    return p, target


# ---------------------------------------------------------------- noise

def _smooth_noise(n_points, xs, corr, rng):
    """Smooth 1-D noise sampled at positions xs (any units), std ≈ 1."""
    if len(xs) == 0:
        return np.zeros(0)
    lo, hi = float(np.min(xs)), float(np.max(xs))
    m = int((hi - lo) / corr) + 4
    knots = rng.standard_normal(m)
    t = (np.asarray(xs) - lo) / corr + 1
    i = np.clip(t.astype(int), 1, m - 3)
    f = t - i
    p0, p1, p2, p3 = knots[i - 1], knots[i], knots[i + 1], knots[i + 2]
    return 0.5 * ((2 * p1) + (-p0 + p2) * f + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f * f
                  + (-p0 + 3 * p1 - 3 * p2 + p3) * f ** 3)


# ---------------------------------------------------------------- rows

@dataclass
class Row:
    x0: float
    x1: float
    base: object  # callable x -> baseline y (px)
    height: float  # space available above the baseline (px)
    area: int | None = None


def _rows_for_area(a, size_px, style):
    """One row for a line area; several bent rows for a box."""
    top, bot = a["top"], a["bottom"]
    lift = style.baseline * size_px
    # people leave a little room after a border or the start of a printed line
    x0, x1 = _inset(a["x0"], a["x1"], size_px)
    if a["kind"] == "line":
        thick = bot.thickness / 2
        return [Row(x0, x1, lambda x, b=bot, l=lift + thick: b(x) - l, a["height"], a["id"])]
    # a box: rows evenly spaced, bending like the edges
    pitch = size_px * style.line_gap
    h = a["height"]
    n = max(1, int((h - size_px * 0.4) // pitch))
    rows = []
    for k in range(n):
        f = (size_px * 1.25 + k * pitch) / h
        if f > 0.98:
            break

        def base(x, f=f):
            return top(x) + (bot(x) - top(x)) * f

        rows.append(Row(x0, x1, base, pitch, a["id"]))
    return rows


def _inset(x0, x1, size_px):
    w = x1 - x0
    return x0 + min(0.45 * size_px, 0.08 * w), x1 - min(0.3 * size_px, 0.05 * w)


def _rows_grid(a, analysis, size_px, style):
    """Grid paper: one row every k printed lines, baseline on a printed line."""
    lines = [c for c in analysis["hlines"] if c.x1 > a["x0"] + 20 and c.x0 < a["x1"] - 20]
    if not lines:
        return _rows_for_area(dict(a, kind="box"), size_px, style)
    sp = analysis["line_spacing"] or size_px * 1.6
    k = max(1, int(math.ceil(size_px * 1.45 / sp)))
    rows = []
    lift = style.baseline * size_px
    for i in range(k, len(lines), k):
        c = lines[i]
        rx0, rx1 = _inset(max(a["x0"], c.x0), min(a["x1"], c.x1), size_px)
        rows.append(Row(rx0, rx1, lambda x, c=c, l=lift: c(x) - l, sp * k, a["id"]))
    return rows


def _straight_rows(x0, y0, x1, y1, size_px, style, first_base=None):
    pitch = size_px * style.line_gap
    y = first_base if first_base is not None else y0 + size_px * 1.2
    rows = []
    while y <= y1 + 1e-6:
        rows.append(Row(x0, x1, lambda x, y=y: np.full_like(np.asarray(x, dtype=float), y), pitch))
        y += pitch
    return rows


# ---------------------------------------------------------------- text layout

TOKEN = re.compile(r"\$[^$]+\$|\S+|\n")


def _words(text):
    out = []
    for para in text.split("\n"):
        out.extend(m.group(0) for m in TOKEN.finditer(para))
        out.append("\n")
    return out[:-1]


class _Measure:
    def __init__(self, db, style=None):
        self.db = db
        self.style = style
        self.cache = {}

    def width(self, word):
        if word not in self.cache:
            st, missing, (w, _) = compose(self.db, word, width=1e9, seed=1)
            st = [s for s in st if "missing" not in s]
            if self.style is not None:
                st = track(st, self.style)
            xs = [float(np.max(s["x"])) for s in st] or [w]
            self.cache[word] = (max(xs), missing)
        return self.cache[word]


def _split_long(word, room, meas):
    """Break a word that is wider than a whole row into character pieces."""
    if word.startswith("$"):
        return [word]
    pieces, cur = [], ""
    for ch in word:
        if cur and meas.width(cur + ch)[0] > room:
            pieces.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        pieces.append(cur)
    return pieces


def layout_text(text, rows, size_px, db, style, seed):
    """Fill rows with words. Returns ([(row, line_text)], leftover_text, missing)."""
    meas = _Measure(db, style)
    words = _words(text)
    space = getattr(db, "space", 0.49)
    placed, missing = [], set()
    i, r = 0, 0
    while i < len(words) and r < len(rows):
        row = rows[r]
        room = (row.x1 - row.x0) / size_px / style.x_scale - style.indent * 2
        if room <= 0.8:
            r += 1
            continue
        line, used = [], 0.0
        while i < len(words):
            w = words[i]
            if w == "\n":
                i += 1
                break
            width, miss = meas.width(w)
            missing |= set(miss)
            need = width + (space if line else 0)
            if used + need <= room:
                line.append(w)
                used += need
                i += 1
                continue
            if not line and width > room:
                parts = _split_long(w, room, meas)
                words[i:i + 1] = parts
                continue
            break
        placed.append((row, " ".join(line)))
        r += 1
    leftover = " ".join(w if w != "\n" else "\n" for w in words[i:]).replace(" \n ", "\n").strip()
    return [p for p in placed if p[1]], leftover, missing


def track(st, style):
    """Paper spacing: the gaps between letters (and between words) are narrower than on
    the tablet. Strokes of one glyph, or one run of math, move together."""
    if style.tracking == 1.0 and style.word_space == 1.0 and style.math_height >= 9:
        return st
    groups, key_of = [], None
    for i, s in enumerate(st):
        key = s.get("glyph", "math")
        if key != key_of:
            groups.append([key, []])
            key_of = key
        groups[-1][1].append(i)
    out = list(st)
    spans = []
    for key, ids in groups:
        xs = np.concatenate([np.asarray(st[i]["x"], float) for i in ids])
        x0, x1 = float(xs.min()), float(xs.max())
        shrink = 0.0
        if key == "math":
            out, shrink = _small_fractions(out, ids, style)
        spans.append((x0, x1, ids, shrink))
    spans.sort(key=lambda t: t[0])
    st = out
    shift, right = 0.0, None
    for x0, x1, ids, shrink in spans:
        if right is not None:
            gap = x0 - right
            if gap > 0:
                shift -= gap * (1 - (style.tracking if gap < 0.35 else style.word_space))
        for i in ids:
            out[i] = dict(out[i], x=np.asarray(out[i]["x"], float) + shift)
        right = x1 if right is None else max(right, x1)
        # what comes after a narrowed fraction moves up to it
        shift -= shrink
    return out


def _small_fractions(st, ids, style):
    """Display fractions are taller than a ruled row. On paper the numerator and the
    denominator are written smaller, closer to the bar, and the bar is just long enough.
    Returns the strokes and how much narrower the math became."""
    st = list(st)
    ys = np.concatenate([np.asarray(st[i]["y"], float) for i in ids])
    if ys.max() - ys.min() <= style.math_height:
        return st, 0.0
    f = float(np.clip((style.math_height - 1) / max(ys.max() - ys.min() - 1, 1e-6), 0.5, 1.0))

    def box(i):
        x, y = np.asarray(st[i]["x"], float), np.asarray(st[i]["y"], float)
        return float(x.min()), float(x.max()), float(y.mean())

    bars = []
    for i in ids:
        x0, x1, cy = box(i)
        y = np.asarray(st[i]["y"], float)
        if -0.1 <= cy <= 1.1 and x1 - x0 > 0.5 and np.ptp(y) < 0.25 * (x1 - x0):
            bars.append(i)
    total = 0.0
    for b in sorted(bars, key=lambda i: box(i)[0]):
        bx0, bx1, by = box(b)
        parts = [i for i in ids if i != b and bx0 - 0.1 <= (box(i)[0] + box(i)[1]) / 2 <= bx1 + 0.1
                 and not -0.1 <= box(i)[2] <= 1.1]
        if not parts:
            continue
        mid = (bx0 + bx1) / 2
        half = 0.0
        for i in parts:
            x, y = np.asarray(st[i]["x"], float), np.asarray(st[i]["y"], float)
            cy = float(y.mean())
            ny = by - (by - cy) * f * 0.9 if cy < by else by + (cy - by) * f * 0.9
            nx = mid + (x - mid) * f
            st[i] = dict(st[i], x=nx, y=ny + (y - cy) * f)
            half = max(half, float(np.abs(nx - mid).max()))
        half += 0.08
        dl = max(0.0, (bx1 - bx0) / 2 - half)
        x = np.asarray(st[b]["x"], float)
        st[b] = dict(st[b], x=mid + (x - mid) * (half / max((bx1 - bx0) / 2, 1e-6)))
        for i in ids:
            if i == b or i in parts:
                continue
            x0, x1, _ = box(i)
            if x0 >= bx1 - 0.05:
                st[i] = dict(st[i], x=np.asarray(st[i]["x"], float) - 2 * dl)
        # left side keeps its place; the bar and its parts move left by dl
        for i in [b] + parts:
            st[i] = dict(st[i], x=np.asarray(st[i]["x"], float) - dl)
        total += 2 * dl
    return st, total


def _hangul(s):
    ch = s.get("ch", "")
    return len(ch) == 1 and ("가" <= ch <= "힣" or "ㄱ" <= ch <= "ㅣ")


def _arc(s):
    return float(np.hypot(np.diff(s["x"]), np.diff(s["y"])).sum())


def _resample_arc(x, y, step):
    d = np.hypot(np.diff(x), np.diff(y))
    s = np.r_[0, np.cumsum(d)]
    if s[-1] < step * 2:
        return x, y
    t = np.linspace(0, s[-1], max(3, int(s[-1] / step) + 1))
    return np.interp(t, s, x), np.interp(t, s, y)


def _smooth_keep_ends(v, sigma_pts):
    if sigma_pts < 0.5 or len(v) < 5:
        return v
    r = min(int(3 * sigma_pts), len(v) - 2)
    sigma_pts = min(sigma_pts, r / 3 + 0.5)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma_pts) ** 2)
    k /= k.sum()
    pad = np.r_[2 * v[0] - v[r:0:-1], v, 2 * v[-1] - v[-2:-r - 2:-1]]
    out = np.convolve(pad, k, "valid")
    # ends stay where the pen touched down and lifted
    w = np.minimum(1, np.minimum(np.arange(len(v)), np.arange(len(v))[::-1]) / max(r, 1))
    return v * (1 - w) + out * w


def scribble(st, style, nrng):
    """Fast handwriting on paper: corners rounded off, a slow wobble inside each letter,
    letters a little turned, scaled and shifted, horizontal strokes climbing to the right.
    Strokes of one glyph change together; a run of math is only rounded, wobbled and
    tilted, never turned or rescaled piece by piece."""
    k = style.scribble
    groups, run = {}, None
    for i, s in enumerate(st):
        if "glyph" in s:
            groups.setdefault(s["glyph"], []).append(i)
            run = None
        elif "missing" not in s:
            # a run of math moves as one piece: only rounding, wobble and climbing strokes
            if run is None:
                run = ("math", i)
            groups.setdefault(run, []).append(i)
    st = list(st)
    step = 0.01
    for key, ids in groups.items():
        is_math = isinstance(key, tuple)
        xs = np.concatenate([np.asarray(st[i]["x"], float) for i in ids])
        ys = np.concatenate([np.asarray(st[i]["y"], float) for i in ids])
        cx, cy = float(xs.mean()), float(ys.max())  # turn and scale about the bottom centre
        a = 0.0 if is_math else math.radians(nrng.normal(0, style.glyph_rotate_deg * k))
        sc = 1.0 if is_math else 1 + nrng.normal(0, style.glyph_scale * k)
        dy = 0.0 if is_math else nrng.normal(0, style.glyph_shift * k)
        # smooth random displacement field over the letter (a few knots per unit)
        # knots every half glyph unit, so a small letter (=, ㅇ) moves almost as a whole
        x0, y0 = float(xs.min()), float(ys.min())
        n = int(max(xs.max() - x0, ys.max() - y0) / 0.5) + 2
        kx = nrng.normal(0, 1, (2, n + 1, n + 1))
        rise = math.radians(style.rise_deg * k * (0.5 + nrng.random()))
        for i in ids:
            x, y = _resample_arc(np.asarray(st[i]["x"], float), np.asarray(st[i]["y"], float), step)
            # a horizontal stroke is drawn climbing a little, turning about where it starts
            if len(x) > 2:
                ddx, ddy = x[-1] - x[0], y[-1] - y[0]
                if abs(ddx) > 0.15 and abs(ddy) < 0.35 * abs(ddx):
                    t = (x - x[0])
                    y = y - t * math.tan(rise) * np.sign(ddx)
            # rounded corners: smoothing along the stroke, ends kept; small and closed
            # strokes (ㅇ, o) are only softened, or they would shrink and open up
            arc = float(np.hypot(np.diff(x), np.diff(y)).sum()) if len(x) > 1 else 0.0
            closed = len(x) > 2 and math.hypot(x[-1] - x[0], y[-1] - y[0]) < 0.3 * arc
            sig = min(style.round_corners * k, arc / (25 if closed else 10)) / step
            x, y = _smooth_keep_ends(x, sig), _smooth_keep_ends(y, sig)
            u = np.clip((x - x0) / 0.5, 0, n - 0.001)
            v = np.clip((y - y0) / 0.5, 0, n - 0.001)
            iu, iv = u.astype(int), v.astype(int)
            fu, fv = u - iu, v - iv

            def field(c):
                g = kx[c]
                return ((1 - fu) * (1 - fv) * g[iv, iu] + fu * (1 - fv) * g[iv, iu + 1]
                        + (1 - fu) * fv * g[iv + 1, iu] + fu * fv * g[iv + 1, iu + 1])

            x = x + field(0) * style.warp * k
            y = y + field(1) * style.warp * k
            rx = cx + ((x - cx) * math.cos(a) - (y - cy) * math.sin(a)) * sc
            ry = cy + ((x - cx) * math.sin(a) + (y - cy) * math.cos(a)) * sc + dy
            st[i] = dict(st[i], x=rx, y=ry)
    return st


def paperize(st, style, rng):
    """Tablet strokes → paper strokes: syllable parts drawn closer together, run-on stroke
    ends, and light pen-drag hairlines between consecutive strokes of a glyph (the pen is
    not lifted cleanly on paper)."""
    if style.compact != 1.0:
        groups = {}
        for i, s in enumerate(st):
            groups.setdefault(s.get("glyph", ("solo", i)), []).append(i)
        st = list(st)
        for ids in groups.values():
            if len(ids) < 2:
                continue
            cx = np.mean([np.mean(st[i]["x"]) for i in ids])
            cy = np.mean([np.mean(st[i]["y"]) for i in ids])
            ys0 = np.concatenate([np.asarray(st[i]["y"]) for i in ids])
            for i in ids:
                mx, my = float(np.mean(st[i]["x"])), float(np.mean(st[i]["y"]))
                st[i] = dict(st[i], x=np.asarray(st[i]["x"]) + (cx - mx) * (1 - style.compact),
                             y=np.asarray(st[i]["y"]) + (cy - my) * (1 - style.compact))
            # keep the syllable's outer size: the parts touch more, the letter is not smaller
            ys1 = np.concatenate([np.asarray(st[i]["y"]) for i in ids])
            k = (ys0.max() - ys0.min()) / max(ys1.max() - ys1.min(), 1e-6)
            b0 = ys0.max()
            for i in ids:
                st[i] = dict(st[i], x=cx + (np.asarray(st[i]["x"]) - cx) * k,
                             y=b0 + (np.asarray(st[i]["y"]) - ys1.max()) * k)
    if style.scribble > 0:
        st = scribble(st, style, np.random.default_rng(rng.randrange(1 << 30)))
    out = []
    for s in st:
        x = np.asarray(s["x"], float)
        y = np.asarray(s["y"], float)
        if len(x) >= 3 and style.overshoot > 0:
            for end in (0, -1):
                a, b = (1, 0) if end == 0 else (-2, -1)
                d = np.array([x[b] - x[a], y[b] - y[a]])
                n = np.hypot(*d)
                if n > 1e-6:
                    e = d / n * style.overshoot * rng.uniform(0.2, 1.3)
                    if end == 0:
                        x, y = np.r_[x[0] + e[0], x], np.r_[y[0] + e[1], y]
                    else:
                        x, y = np.r_[x, x[-1] + e[0]], np.r_[y, y[-1] + e[1]]
        out.append(dict(s, x=x, y=y, ink=np.ones(len(x))))
    if style.ligature <= 0:
        return out
    merged = []
    for s in out:
        prev = merged[-1] if merged else None
        if (prev is not None and prev.get("glyph") is not None and s.get("glyph") is not None
                and len(prev["x"]) > 1 and len(s["x"]) > 1):
            ex, ey = prev["x"][-1], prev["y"][-1]
            sx, sy = s["x"][0], s["y"][0]
            dist = math.hypot(sx - ex, sy - ey)
            same = prev["glyph"] == s["glyph"]
            # into the next letter of the same word: only a short hop forward
            chance = style.ligature if same else style.word_ligature * style.scribble
            near = dist < (style.ligature_dist if same else 1.4 * style.ligature_dist)
            if not same and not (0 < sx - ex < 0.6):
                near = False
            # dots (colons, i, periods) are tapped, never dragged into, and symbols and
            # digits keep their strokes apart (= must not turn into z)
            if _arc(prev) < 0.15 or _arc(s) < 0.15 or not (_hangul(prev) and _hangul(s)):
                near = False
            if near and rng.random() < chance:
                n = max(3, int(dist / 0.04))
                t = np.linspace(0, 1, n + 2)[1:-1]
                # a slight arc, as the hand swings to the next stroke
                mx, my = (ex + sx) / 2, (ey + sy) / 2 - dist * rng.uniform(0.05, 0.25)
                cx = (1 - t) ** 2 * ex + 2 * (1 - t) * t * mx + t ** 2 * sx
                cy = (1 - t) ** 2 * ey + 2 * (1 - t) * t * my + t ** 2 * sy
                ink = style.ligature_ink * (0.6 + 0.8 * rng.random()) * np.sin(np.pi * t) ** 0.3
                merged[-1] = dict(prev, x=np.r_[prev["x"], cx, s["x"]], y=np.r_[prev["y"], cy, s["y"]],
                                  ink=np.r_[prev["ink"], ink, s["ink"]])
                continue
        merged.append(s)
    return merged


def _place_line(line_text, row, size_px, db, style, rng, seed, x_start=None):
    """Compose one row of text and bend it onto the row's baseline. Returns pixel strokes."""
    st, missing, _ = compose(db, line_text, width=1e9, seed=seed, jitter=0.6)
    st = [s for s in st if "missing" not in s]
    if not st:
        return [], missing
    st = paperize(track(st, style), style, rng)
    drift = math.tan(math.radians(rng.gauss(0, style.drift_deg * 0.5)))
    # word-level size changes: words are separated by gaps wider than 0.3
    order = np.argsort([float(np.min(s["x"])) for s in st])
    word_scale, cur, last_right = {}, 1 + rng.gauss(0, style.size_jitter), None
    for k in order:
        s = st[k]
        left = float(np.min(s["x"]))
        if last_right is not None and left - last_right > 0.35:
            cur = 1 + rng.gauss(0, style.size_jitter)
        word_scale[k] = cur
        last_right = max(last_right or -1e9, float(np.max(s["x"])))
    x0 = row.x0 + style.indent * size_px if x_start is None else x_start
    out = []
    for k, s in enumerate(st):
        x = np.asarray(s["x"], float)
        y = np.asarray(s["y"], float) - 1.0  # baseline at 0, up is negative
        sc = word_scale[k]
        cx = float(np.mean(x))
        x = cx + (x - cx) * sc
        y = y * sc
        X = x0 + x * size_px * style.x_scale
        out.append({"X": X, "y": y, "ink": s.get("ink")})
    # baseline wobble: one smooth curve for the whole row, sampled at every point
    allX = np.concatenate([o["X"] for o in out])
    lo = float(allX.min())
    knots_rng = np.random.default_rng(seed + 17)
    corr = style.wobble_len * size_px
    m = int((float(allX.max()) - lo) / corr) + 4
    knots = knots_rng.standard_normal(m)
    strokes = []
    for o in out:
        t = (o["X"] - lo) / corr + 1
        i = np.clip(t.astype(int), 1, m - 3)
        f = t - i
        p0, p1, p2, p3 = knots[i - 1], knots[i], knots[i + 1], knots[i + 2]
        wob = 0.5 * ((2 * p1) + (-p0 + p2) * f + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f * f
                     + (-p0 + 3 * p1 - 3 * p2 + p3) * f ** 3)
        base = np.asarray(row.base(o["X"]), float)
        Y = base + (o["y"] + wob * style.wobble) * size_px + (o["X"] - x0) * drift
        strokes.append({"points": np.stack([o["X"], Y], 1), "ink": o["ink"]})
    return strokes, missing


# ---------------------------------------------------------------- marks

def _tremor(pts, px_per_mm, style, rng, amount=1.0):
    pts = np.asarray(pts, float)
    if len(pts) < 2:
        return pts
    d = np.hypot(*np.diff(pts, axis=0).T)
    s = np.concatenate([[0], np.cumsum(d)])
    corr = 2.5 * px_per_mm
    nx = _smooth_noise(len(s), s, corr, rng)
    ny = _smooth_noise(len(s), s, corr, rng)
    a = style.tremor_mm * px_per_mm * amount
    return pts + np.stack([nx, ny], 1) * a


def _densify(pts, step):
    pts = np.asarray(pts, float)
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, int(np.hypot(*(b - a)) / step))
        for k in range(1, n + 1):
            out.append(a + (b - a) * k / n)
    return np.array(out)


def mark_strokes(kind, box=None, frm=None, to=None, px_per_mm=6.0, style=DEFAULT_STYLE, rng=None):
    """Hand-drawn marks as lists of point arrays (pixels)."""
    rng = rng or np.random.default_rng(0)
    r = random.Random(int(rng.integers(1 << 30)))
    step = 0.4 * px_per_mm
    out = []
    if kind in ("arrow",):
        a, b = np.asarray(frm, float), np.asarray(to, float)
        v = b - a
        L = float(np.hypot(*v)) or 1.0
        u = v / L
        n = np.array([-u[1], u[0]])
        bend = r.gauss(0, 0.03) * L
        mid = (a + b) / 2 + n * bend
        t = np.linspace(0, 1, max(8, int(L / step)))[:, None]
        line = (1 - t) ** 2 * a + 2 * (1 - t) * t * mid + t ** 2 * b
        out.append(_tremor(line, px_per_mm, style, rng))
        head = min(3.0 * px_per_mm, L * 0.35)
        ang = math.radians(25 + r.gauss(0, 4))
        for sgn in (1, -1):
            c, s = math.cos(sgn * ang), math.sin(sgn * ang)
            d = -np.array([u[0] * c - u[1] * s, u[0] * s + u[1] * c]) * head
            out.append(_tremor(_densify([b, b + d], step), px_per_mm, style, rng, 0.5))
        return out
    x0, y0, x1, y1 = [float(v) for v in box]
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if kind == "circle":
        rx, ry = w / 2 + 0.8 * px_per_mm, h / 2 + 0.8 * px_per_mm
        start = r.uniform(-2.6, -1.6)
        sweep = 2 * math.pi * r.uniform(1.08, 1.22)
        t = np.linspace(start, start + sweep, 120)
        grow = 1 + 0.06 * (t - start) / sweep
        tilt = math.radians(r.gauss(0, 3))
        ex = rx * np.cos(t) * grow
        ey = ry * np.sin(t) * grow
        pts = np.stack([cx + ex * math.cos(tilt) - ey * math.sin(tilt),
                        cy + ex * math.sin(tilt) + ey * math.cos(tilt)], 1)
        out.append(_tremor(pts, px_per_mm, style, rng, 2.0))
    elif kind == "box":
        m = 0.6 * px_per_mm
        c = [(x0 - m, y0 - m), (x1 + m, y0 - m), (x1 + m, y1 + m), (x0 - m, y1 + m)]
        c = [np.array(p) + np.array([r.gauss(0, 0.3), r.gauss(0, 0.3)]) * px_per_mm for p in c]
        over = 0.7 * px_per_mm
        # two strokes: top+right, then left+bottom (how most people draw a box)
        a = _densify([c[0] - [over, 0], c[1], c[2] + [0, over]], step)
        b = _densify([c[0] - [0, over], c[3], c[2] + [over, 0]], step)
        out += [_tremor(a, px_per_mm, style, rng), _tremor(b, px_per_mm, style, rng)]
    elif kind in ("underline", "double_underline"):
        k = 2 if kind == "double_underline" else 1
        for j in range(k):
            yy = y1 + (0.6 + j * 0.9) * px_per_mm
            sag = r.gauss(0, 0.25) * px_per_mm
            t = np.linspace(0, 1, max(8, int(w / step)))
            xs = x0 - 0.5 * px_per_mm + (w + px_per_mm) * t + r.gauss(0, 0.3)
            ys = yy + sag * 4 * t * (1 - t) + (t - 0.5) * r.gauss(0, 0.6) * px_per_mm
            out.append(_tremor(np.stack([xs, ys], 1), px_per_mm, style, rng))
    elif kind == "strike":
        t = np.linspace(0, 1, max(8, int(w / step)))
        xs = x0 + w * t
        ys = cy + (t - 0.5) * r.gauss(0, 0.15) * h
        out.append(_tremor(np.stack([xs, ys], 1), px_per_mm, style, rng))
    elif kind == "check":
        p = [(x0, cy), (x0 + w * 0.35, y1), (x1, y0)]
        out.append(_tremor(_densify(p, step), px_per_mm, style, rng))
    elif kind == "cross":
        out.append(_tremor(_densify([(x0, y0), (x1, y1)], step), px_per_mm, style, rng))
        out.append(_tremor(_densify([(x1, y0), (x0, y1)], step), px_per_mm, style, rng))
    elif kind == "star":
        R = min(w, h) / 2
        pts = []
        for k in range(6):
            a = -math.pi / 2 + k * 4 * math.pi / 5
            pts.append((cx + R * math.cos(a), cy + R * math.sin(a)))
        out.append(_tremor(_densify(pts, step), px_per_mm, style, rng))
    elif kind == "bracket":
        m = 0.8 * px_per_mm
        out.append(_tremor(_densify([(x0 + m, y0), (x0, y0 + m), (x0, y1 - m), (x0 + m, y1)], step),
                           px_per_mm, style, rng))
    else:
        raise ValueError(f"Unknown mark '{kind}'")
    return out


def draw_strokes(points, px_per_mm, style, rng, smooth=True):
    pts = np.asarray(points, float)
    if len(pts) < 2:
        raise ValueError("draw needs at least two points")
    if smooth and len(pts) > 2:
        # Catmull-Rom through the given points
        P = np.vstack([pts[:1], pts, pts[-1:]])
        out = []
        for i in range(1, len(P) - 2):
            p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
            n = max(2, int(np.hypot(*(p2 - p1)) / (0.4 * px_per_mm)))
            for t in np.linspace(0, 1, n, endpoint=False):
                out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                                  + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
        out.append(pts[-1])
        pts = np.array(out)
    else:
        pts = _densify(pts, 0.4 * px_per_mm)
    return [_tremor(pts, px_per_mm, style, rng)]


# ---------------------------------------------------------------- erase

def erase(img, analysis, box):
    """Paper colour inside box; printed lines are kept, and where writing crossed a
    printed line the line's own cross-section is put back."""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = img.size
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return img
    src = np.asarray(img.convert("RGB")).astype(np.float32)
    dark = analysis["_dark"]
    rng = np.random.default_rng(x0 * 7 + y0)
    out = src.copy()
    region = (slice(y0, y1), slice(x0, x1))
    paper = np.clip(analysis["_bg"][region], 0, 255)
    # a little scanner noise so the patch is not flat
    out[region] = np.repeat(paper[..., None], 3, 2) + rng.normal(0, 0.8, (y1 - y0, x1 - x0, 3))
    for horizontal, lines in ((True, analysis["hlines"]), (False, analysis["vlines"])):
        lo, hi = (x0, x1) if horizontal else (y0, y1)
        for c in lines:
            t = np.arange(max(lo, int(c.x0)), min(hi, int(c.x1) + 1))
            if len(t) == 0:
                continue
            u = c(t)
            r = max(1, int(c.thickness / 2) + 1)
            offs = range(-r - 2, r + 3)
            pos = [np.clip(np.round(u + d).astype(int), 0, (h if horizontal else w) - 1) for d in offs]
            inside = [(p >= (y0 if horizontal else x0)) & (p < (y1 if horizontal else x1)) for p in pos]
            pix = [src[p, t] if horizontal else src[t, p] for p in pos]
            gray = [q.mean(1) for q in pix]
            # the line's typical cross-section, from columns where nothing crosses it
            centre = np.min(np.stack(gray[2:-2]), 0)
            tone = np.median(centre)
            clean = centre > tone - 45
            if clean.sum() < 5:
                continue
            prof = [np.median(q[clean], 0) for q in pix]
            # columns where writing touches the line (and a few around them) get the
            # typical cross-section; elsewhere the scanned line stays as it is
            dirty = ~clean
            for d, g in zip(offs, gray):
                if abs(d) > r:
                    dirty |= g < min(tone, prof[d + r + 2].mean()) - 25
            dirty = np.convolve(dirty.astype(float), np.ones(7), "same") > 0
            for d, p, ins, q, g in zip(offs, pos, inside, pix, gray):
                dk = dark[p, t] if horizontal else dark[t, p]
                synth = prof[d + r + 2] + rng.normal(0, 1.5, q.shape)
                is_line = ~dirty & (g > tone - 45) & ((abs(d) <= r) | (dk > 0.06))
                val = np.where(dirty[:, None], synth, q)
                use = ins & (dirty | is_line)
                if horizontal:
                    out[p[use], t[use]] = val[use]
                else:
                    out[t[use], p[use]] = val[use]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


# ---------------------------------------------------------------- the page

def _area_by_id(analysis, aid):
    for a in analysis["areas"]:
        if a["id"] == aid:
            return a
    raise ValueError(f"No writing area {aid}; see the numbered preview")


def _following(analysis, a):
    """Empty line areas below a, in the same column."""
    out = []
    for b in analysis["areas"]:
        if b["_ymid"] <= a["_ymid"] + 2 or b["kind"] != "line":
            continue
        ov = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
        if ov < 0.6 * min(a["x1"] - a["x0"], b["x1"] - b["x0"]):
            continue
        out.append(b)
    out.sort(key=lambda b: b["_ymid"])
    rows = []
    for b in out:
        if b["filled"] > 0.08:
            break
        rows.append(b)
    return rows


def natural_size(analysis, style):
    """Syllable height (px) to use on this page."""
    mm = analysis["dpi"] / 25.4
    size = style.size_mm * mm
    est = analysis.get("letter_height")
    if est:
        # same measurement as scanpage._letter_height on our own output gives the factor
        size = 0.3 * size + 0.7 * est * style.unit_per_height
    return size


def write(img, analysis, items, db, pen="ballpoint", style=DEFAULT_STYLE, seed=7, calibrate_pen=True):
    """Draw items on img (a PIL image). Returns (new image, report)."""
    if not isinstance(items, list) or not items:
        raise ValueError("items: a non-empty list")
    dpi = analysis["dpi"]
    mm = dpi / 25.4
    base_pen = pen if isinstance(pen, penlib.Pen) else penlib.get_pen(pen)
    measured = None
    if calibrate_pen:
        base_pen, measured = calibrate(img, analysis, base_pen, dpi, db)
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    strokes, report = [], {"items": [], "pen": base_pen.name, "pen_width_mm": round(base_pen.width_mm, 3),
                           "matched_existing_ink": bool(measured)}
    for n, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError("Each item must be an object")
        p = base_pen
        if it.get("pen"):
            p = penlib.get_pen(it["pen"])
            if p.name.startswith(("ballpoint", "gel")) and measured:
                p = replace(p, width_mm=base_pen.width_mm)
        if it.get("color"):
            p = replace(p, color=tuple(int(c) for c in it["color"]))
        if "erase" in it:
            img = erase(img, analysis, it["erase"])
            report["items"].append({"item": n, "erased": it["erase"]})
            continue
        if "mark" in it:
            pts = mark_strokes(it["mark"], it.get("box"), it.get("from"), it.get("to"), mm, style, nrng)
            strokes += [{"points": q, "pen": p} for q in pts]
            report["items"].append({"item": n, "mark": it["mark"]})
            continue
        if "draw" in it:
            pts = draw_strokes(it["draw"], mm, style, nrng, it.get("smooth", True))
            strokes += [{"points": q, "pen": p} for q in pts]
            report["items"].append({"item": n, "drawn_points": len(it["draw"])})
            continue
        text = it.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"Item {n}: needs text, mark, draw or erase")
        size = it.get("size_mm")
        size_px = float(size) * mm if size else None
        rows = []
        if "area" in it or "areas" in it:
            ids = it["areas"] if "areas" in it else [it["area"]]
            chosen = [_area_by_id(analysis, int(i)) for i in ids]
            if "areas" not in it and it.get("continue", True) and chosen[0]["kind"] == "line":
                chosen += _following(analysis, chosen[0])
            if size_px is None:
                lines = [a["height"] for a in chosen if a["kind"] == "line"]
                size_px = natural_size(analysis, style)
                if lines:
                    size_px = min(size_px, style.fill * float(np.median(lines)) / (1 - style.baseline))
            for a in chosen:
                if analysis["grid_paper"] and a["kind"] == "box":
                    rows += _rows_grid(a, analysis, size_px, style)
                else:
                    rows += _rows_for_area(a, size_px, style)
            if it.get("after_existing") and rows:
                first = chosen[0]
                rows[0] = replace(rows[0], x0=max(rows[0].x0, first["free_from_x"] + 0.4 * size_px))
        elif "region" in it:
            x0, y0, x1, y1 = [float(v) for v in it["region"]]
            size_px = size_px or natural_size(analysis, style)
            inside = [a for a in analysis["areas"] if a["kind"] == "line" and a["x0"] < x1 and a["x1"] > x0
                      and y0 - 2 <= a["_ymid"] <= y1 + 2]
            if inside and not analysis["grid_paper"]:
                for a in sorted(inside, key=lambda a: a["_ymid"]):
                    rows += [replace(r, x0=max(r.x0, x0), x1=min(r.x1, x1)) for r in _rows_for_area(a, size_px, style)]
            elif analysis["grid_paper"]:
                lines = [c for c in analysis["hlines"] if y0 + size_px * 0.8 <= c.mean_y(x0, x1) <= y1]
                sp = analysis["line_spacing"] or size_px * 1.6
                k = max(1, int(math.ceil(size_px * 1.45 / sp)))
                for c in lines[::k]:
                    rows.append(Row(x0, x1, lambda x, c=c: c(x) - style.baseline * size_px, sp * k))
                if not rows:
                    rows = _straight_rows(x0, y0, x1, y1, size_px, style)
            else:
                rows = _straight_rows(x0, y0, x1, y1, size_px, style)
        elif "at" in it:
            x, y = [float(v) for v in it["at"]]
            size_px = size_px or natural_size(analysis, style)
            width = float(it.get("width") or (analysis["width"] - x - 4 * mm))
            if not (0 <= x < analysis["width"] and 0 <= y < analysis["height"]):
                raise ValueError(f"Item {n}: 'at' is outside the page")
            bottom = max(y, analysis["height"] - 2 * mm)
            rows = _straight_rows(x, y - size_px * 1.2, x + width, bottom, size_px, style, first_base=y)
            rows = [replace(r, x0=x) for r in rows]
            rows[0] = replace(rows[0], x0=x - style.indent * size_px)
        else:
            raise ValueError(f"Item {n}: give area, areas, region or at")
        if not rows:
            raise ValueError(f"Item {n}: no room to write there")
        placed, leftover, missing = layout_text(text, rows, size_px, db, style, seed + n)
        if missing:
            raise ValueError("Missing glyphs (nothing written): " + " ".join(sorted(missing)))
        boxes = []
        for k, (row, line) in enumerate(placed):
            st, _ = _place_line(line, row, size_px, db, style, rng, seed * 1000 + n * 50 + k)
            strokes += [dict(q, pen=p) for q in st]
            if st:
                allp = np.vstack([q["points"] for q in st])
                boxes.append([float(allp[:, 0].min()), float(allp[:, 1].min()), float(allp[:, 0].max()), float(allp[:, 1].max())])
        rep = {"item": n, "rows_used": len(placed), "size_px": round(size_px, 1),
               "areas": sorted({r.area for r, _ in placed if r.area is not None})}
        if boxes:
            b = np.array(boxes)
            rep["box"] = [round(float(b[:, 0].min())), round(float(b[:, 1].min())), round(float(b[:, 2].max())), round(float(b[:, 3].max()))]
        if leftover:
            rep["did_not_fit"] = leftover
        busy = sorted({r.area for r, _ in placed if r.area is not None and not it.get("after_existing")
                       and _area_by_id(analysis, r.area)["filled"] > 0.15})
        if busy:
            rep["warning"] = f"areas {busy} already had writing; check the image for overlaps"
        report["items"].append(rep)
    out = penlib.render(strokes, img.size, dpi=dpi, pen=base_pen, seed=seed, background=img)
    report["strokes"] = len(strokes)
    return out, report


# ---------------------------------------------------------------- blank paper

def blank_page(kind="lined", width_mm=210, height_mm=297, dpi=150, seed=0, spacing_mm=None, margin_mm=None):
    """A scanned-looking empty page: lined, grid, dotted or plain, gently bent."""
    mm = dpi / 25.4
    W, H = int(width_mm * mm), int(height_mm * mm)
    rng = np.random.default_rng(seed)
    paper = np.full((H, W), 253.0, np.float32) + rng.normal(0, 1.2, (H, W))
    arr = Image.fromarray(np.clip(paper, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6))
    paper = np.asarray(arr, np.float32)
    # gentle bend (like a page near the binding) and a small rotation
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    bend = (xx / W - 0.5) ** 2 * rng.uniform(-6, 6) * mm / 10 + rng.uniform(-0.004, 0.004) * xx
    ink = np.zeros((H, W), np.float32)
    spacing = (spacing_mm or (8.0 if kind == "lined" else 5.0)) * mm
    margin = (margin_mm if margin_mm is not None else 15) * mm
    if kind in ("lined", "grid"):
        y = margin + spacing
        while y < H - margin * 0.6:
            d = np.abs(yy - (y + bend))
            ink = np.maximum(ink, np.clip(1.3 - d / 0.8, 0, 1) * (0.6 if kind == "lined" else 0.35))
            y += spacing
    if kind == "grid":
        x = margin * 0.5
        while x < W - margin * 0.4:
            d = np.abs(xx - (x + bend * 0.2))
            ink = np.maximum(ink, np.clip(1.3 - d / 0.8, 0, 1) * 0.35)
            x += spacing
    if kind == "dotted":
        gx = (xx - margin) / spacing
        gy = (yy - bend - margin) / spacing
        d = np.hypot((gx - np.round(gx)) * spacing, (gy - np.round(gy)) * spacing)
        inside = (xx > margin * 0.9) & (xx < W - margin * 0.9) & (yy > margin * 0.9) & (yy < H - margin * 0.9)
        ink = np.clip(1.5 - d / 0.8, 0, 1) * 0.35 * inside
    color = np.array([150, 175, 200], np.float32) if kind == "lined" else np.array([160, 160, 165], np.float32)
    rgb = paper[..., None] * (1 - ink[..., None] * (1 - color / 255))
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
