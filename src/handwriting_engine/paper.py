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
    x_scale: float = 1.02  # paper / tablet glyph width at equal height: on paper syllables are narrower
    size_mm: float = 5.8  # glyph unit (Hangul syllable box) on paper
    fill: float = 0.62  # glyph unit / row height when rows are narrow
    baseline: float = 0.14  # baseline above the printed line, in glyph units
    unit_per_height: float = 1.1  # glyph unit / measured height of a written row
    wobble: float = 0.022  # baseline wander along a row (syllable heights)
    wobble_len: float = 5.0  # its correlation length (syllables)
    drift_deg: float = 0.5  # how far a row may tilt away from the printed line
    size_jitter: float = 0.03  # word to word size change
    indent: float = 0.2  # gap from the left edge of an area (syllable heights)
    tremor_mm: float = 0.04  # hand tremor on drawn marks
    line_gap: float = 1.75  # row pitch on unlined paper (syllable heights)
    overshoot: float = 0.03  # strokes run on a little past their end (glyph units)
    ligature: float = 0.35  # chance that the pen stays on the paper between two strokes
    ligature_dist: float = 0.45  # ... when the next stroke starts this close (glyph units)
    ligature_ink: float = 0.3  # how much ink such a connecting hairline leaves
    compact: float = 0.9  # strokes of a syllable sit closer together on paper (1 = as on tablet)
    stem: float = 1.12  # long vertical strokes of Hangul (ㅣ ㅏ ㅓ ㅔ …) drawn this much longer (below 1: shorter)
    bar: float = 0.9  # long horizontal strokes of Hangul (ㅡ ㅗ ㅜ …) drawn this much longer (below 1: shorter)
    bow: float = 0.06  # long strokes bend a little as the wrist turns (sagitta / length)
    round_corners: float = 0.14  # corners drawn as curves by the faster hand (glyph units; 0 = as on tablet)
    tracking: float = 0.55  # gaps between letters, relative to the tablet
    word_space: float = 0.95  # gaps between words, relative to the tablet
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
    # visible width: pixels at least half dark per unit of line length
    width = float(((dark > 0.5) & near).sum() / _skeleton_len(m))
    # colours relative to the paper right around the ink: on a photo the paper is grey and
    # unevenly lit, and the pen is multiplied onto it, so "ink / paper" is what has to match
    ring = scanpage_near(ink, 4) & ~scanpage_near(ink, 1)
    paper = np.median(rgb[ring], axis=0) if ring.sum() > 50 else np.full(3, 255.0)
    rel = np.clip(rgb * (255.0 / np.maximum(paper, 1)), 0, 255)
    core = rel[m & (dark >= np.percentile(dark[m], 60))]
    color = np.median(core, axis=0)
    sk = thin(ink & (dark > 0.24))
    return {"width_px": width, "color": tuple(int(c) for c in color),
            "paper": tuple(int(c) for c in paper),
            "core": float(np.median(rel[sk].mean(1))) if sk.any() else float(color.mean()),
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
    # with no handwriting on the page the ink measured is print: copy its tone, not its
    # (thin, typeset) line width
    match_width = bool(analysis.get("handwriting_height"))
    if match_width:
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
        upd = {"width_mm": float(np.clip(p.width_mm * k ** 0.9, 0.12, 1.5))} if match_width else {}
        # a document scanner turns ballpoint nearly solid along the middle of a line;
        # match how dark the centre line is, not only the darkest pixels
        kc = (1 - target["core"] / 255) / max(1 - got["core"] / 255, 0.05)
        upd["contrast"] = float(np.clip(p.contrast * kc ** 1.2, 1.0, 2.5))
        if black:
            td = 1 - np.array(target["color"]) / 255
            gd = 1 - np.array(got["color"]) / 255
            pd = 1 - np.array(p.color) / 255
            nd = np.clip(pd * (td / np.maximum(gd, 1e-3)) ** 0.8, 0, 1)
            upd["color"] = tuple(int(round(255 * (1 - v))) for v in nd)
        p = replace(p, **upd)
        if (abs(k - 1) < 0.04 or not match_width) and abs(kc - 1) < 0.03:
            break
    return p, target


def page_black(img, analysis=None):
    """The darkest tone (0..1 of the paper around it) anything printed or written on this page
    reaches. A scan stretches black to black (about 0); a phone photo of grey, unevenly lit
    paper rarely gets below 0.15-0.25, and ink darker than that looks pasted on."""
    gray = img.convert("L")
    paper = gray.reduce(4).filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.BoxBlur(4))
    paper = paper.resize(gray.size, Image.BILINEAR)
    rel = np.asarray(gray, np.float32) / np.maximum(np.asarray(paper, np.float32), 1)
    if analysis and analysis.get("areas"):
        # only the printed form itself: not the binding, the desk or shadows around the page
        a = analysis["areas"]
        x0, x1 = int(min(r["x0"] for r in a)), int(max(r["x1"] for r in a))
        y0 = int(max(0, min(r["_ymid"] for r in a) - 40))
        y1 = int(max(r["_ymid"] for r in a) + 40)
        rel = rel[y0:y1, x0:x1]
    ink = rel[rel < 0.65]
    if ink.size < 200:
        return None
    return float(np.percentile(ink, 5))


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


def _rows_for_area(a, size_px, style, spacing=None):
    """One row for a line area; several bent rows for a box. spacing: the page's printed line
    pitch; a box on a ruled form usually has faint lines at that pitch, so rows sit on them."""
    top, bot = a["top"], a["bottom"]
    lift = style.baseline * size_px
    # people leave a little room after a border or the start of a printed line
    x0, x1 = _inset(a["x0"], a["x1"], size_px)
    if a["kind"] == "line":
        thick = bot.thickness / 2
        return [Row(x0, x1, lambda x, b=bot, l=lift + thick: b(x) - l, a["height"], a["id"])]
    h = a["height"]
    if spacing and 1.3 * size_px <= spacing <= 2.6 * size_px and h >= 1.5 * spacing:
        # rows on the (often faint) ruled lines: counted up from the box's bottom edge
        n = int((h - 0.9 * size_px) // spacing) + 1
        rows = []
        for k in reversed(range(n)):
            f = 1 - k * spacing / h

            def base(x, f=f):
                return top(x) + (bot(x) - top(x)) * f - lift

            rows.append(Row(x0, x1, base, spacing, a["id"]))
        return rows
    # a box: rows evenly spaced, bending like the edges
    pitch = size_px * style.line_gap
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


def _label_rows(a, rows, analysis, size_px, style):
    """A box on a form often starts with a printed label at its left ("성질①", "설명"). People
    write on the label's line, right after it, then on the rows below. Rows that would cut
    through a label are moved onto its baseline."""
    ink = analysis.get("_ink")
    if ink is None or a["kind"] != "box" or not rows:
        return rows
    x0, x1 = int(a["x0"]), int(a["x1"])
    left = x0 + int(0.16 * (x1 - x0))
    y0, y1 = int(a["top"].mean_y()) + 3, int(a["bottom"].mean_y()) - 3
    sub = ink[y0:y1, x0 + 3:left]
    on = np.nonzero(sub.sum(1) > 1)[0]
    if not len(on):
        return rows
    # label lines: runs of inked rows in the left strip
    labels, start = [], on[0]
    for i, j in zip(on, np.r_[on[1:], 10 ** 9]):
        if j - i > 3:
            if i - start >= 0.25 * size_px:
                labels.append((y0 + start, y0 + i))
            start = j
    out = []
    for lt, lb in labels:
        cols = np.nonzero(ink[lt:lb + 1, x0 + 3:x1 - 3].any(0))[0]
        # the label ends where its ink stops for more than a syllable
        end = cols[0]
        for c in cols:
            if c - end > 1.2 * size_px:
                break
            end = c
        lx = x0 + 3 + end + 0.5 * size_px
        base_y = float(lb) + 1 - (a["top"].mean_y() * 0)
        off = base_y - float(a["top"](float(lx)))
        out.append(Row(lx, rows[0].x1, lambda x, o=off, t=a["top"]: t(x) + o, size_px * style.line_gap, a["id"]))
    # keep the ruled rows that do not run into a label row
    for r in rows:
        y = r.base((r.x0 + r.x1) / 2)
        if all(abs(y - (lb + 1)) > 0.8 * size_px for _, lb in labels):
            out.append(r)
    out.sort(key=lambda r: r.base((r.x0 + r.x1) / 2))
    return out


def _free_rows(rows, analysis, size_px):
    """Keep rows off what is already on the page: a printed label ("성질①", "설명") or earlier
    writing inside a box. Each row keeps its widest empty stretch, or is dropped when that is
    too short to be worth writing in."""
    ink = analysis.get("_ink")
    if ink is None:
        return rows
    H, W = ink.shape
    out = []
    for r in rows:
        x0, x1 = int(max(0, r.x0)), int(min(W, r.x1))
        if x1 - x0 < size_px:
            continue
        xs = np.arange(x0, x1)
        base = np.asarray([r.base(float(x)) for x in xs[::8]])
        base = np.interp(xs, xs[::8], base)
        top = np.clip((base - 0.95 * size_px).astype(int), 0, H - 1)
        bot = np.clip((base + 0.1 * size_px).astype(int), 0, H - 1)
        busy = np.array([ink[t:b, x].any() for x, t, b in zip(xs, top, bot)])
        if not busy.any():
            out.append(r)
            continue
        # widest empty stretch, after bridging the gaps inside a printed word
        pad = int(0.5 * size_px)
        grown = busy.copy()
        for k in range(1, pad + 1):
            grown[k:] |= busy[:-k]
            grown[:-k] |= busy[k:]
        best, run, start = (0, 0, 0), 0, 0
        for i, b in enumerate(np.r_[grown, True]):
            if not b:
                if run == 0:
                    start = i
                run += 1
            else:
                if run > best[0]:
                    best = (run, start, start + run)
                run = 0
        if best[0] >= max(3 * size_px, 0.35 * (x1 - x0)):
            out.append(replace(r, x0=float(x0 + best[1]), x1=float(x0 + best[2])))
    return out


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

# a word is everything between spaces; math inside it (which may hold spaces) stays glued to
# the letters around it ("$\theta$에", "$x$)"), so no space is invented there
TOKEN = re.compile(r"(?:\$[^$]+\$|[^\s$])+|\S")


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
    atoms = re.findall(r"\$[^$]+\$|.", word)  # a piece of math is never cut
    if len(atoms) == 1:
        return [word]
    pieces, cur = [], ""
    for ch in atoms:
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
        first = st[ids[0]]
        spans.append((x0, x1, ids, shrink, key != "math" and _hangul(first), first.get("sp")))
    spans.sort(key=lambda t: t[0])
    st = out
    shift, right, prev_h = 0.0, None, None
    for x0, x1, ids, shrink, is_h, sp in spans:
        if right is not None:
            gap = x0 - right
            if gap > 0:
                # a typed space is a word gap; letters of one word close up (math runs carry no
                # flag, so their gap decides)
                word = sp if sp is not None else gap >= 0.35
                new = gap * (style.word_space if word else style.tracking)
                if word:
                    # a space typed between symbols (tan θ = y/x) is a small gap in handwriting,
                    # and no word gap is wider than a hand leaves
                    new = min(max(new, 0.3), 0.3 if not (is_h or prev_h) else 0.65)
                # letters never run into each other
                new = max(new, min(gap, 0.08))
                shift -= gap - new
        for i in ids:
            out[i] = dict(out[i], x=np.asarray(out[i]["x"], float) + shift)
        right = x1 if right is None else max(right, x1)
        prev_h = is_h
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


def _hangul(s):
    ch = s.get("ch", "")
    return len(ch) == 1 and ("가" <= ch <= "힣" or "ㄱ" <= ch <= "ㅣ")


def long_stems(st, k, kb=1.0):
    """On paper the vowel stems run longer than on the tablet: the hand pulls the downstroke
    through instead of stopping at the consonant's height. Long near-vertical strokes of
    Hangul are stretched about their middle (k), long horizontals likewise (kb); short
    strokes (consonant sides) are left alone."""
    if abs(k - 1) < 1e-3 and abs(kb - 1) < 1e-3:
        return st
    out = []
    for s in st:
        x, y = np.asarray(s["x"], float), np.asarray(s["y"], float)
        if _hangul(s) and len(y) > 1:
            dy, dx = float(y.max() - y.min()), float(x.max() - x.min())
            if dy > 0.75 and dx < 0.2 * dy:
                m = 0.5 * (y.max() + y.min())
                s = dict(s, y=m + (y - m) * k)
            elif dx > 0.4 and dy < 0.25 * dx:
                m = 0.5 * (x.max() + x.min())
                s = dict(s, x=m + (x - m) * kb)
        out.append(s)
    return out


def bow_strokes(st, k):
    """Long straight strokes are pulled by the wrist into a shallow arc: horizontals bulge
    up, verticals bulge to the right. The same for every letter, so nothing wobbles."""
    if k <= 0:
        return st
    out = []
    for s in st:
        x, y = np.asarray(s["x"], float), np.asarray(s["y"], float)
        if "missing" not in s and len(x) > 2:
            dx, dy = x[-1] - x[0], y[-1] - y[0]
            L = math.hypot(dx, dy)
            arc = float(np.hypot(np.diff(x), np.diff(y)).sum())
            if L > 0.3 and arc < 1.15 * L:
                d = np.hypot(np.diff(x), np.diff(y))
                t = np.r_[0, np.cumsum(d)] / max(arc, 1e-9)
                nx, ny = -dy / L, dx / L  # left normal of the stroke direction
                if (abs(dx) >= abs(dy) and ny > 0) or (abs(dx) < abs(dy) and nx < 0):
                    nx, ny = -nx, -ny  # horizontals bulge up, verticals to the right
                h = k * L * np.sin(np.pi * t)
                s = dict(s, x=x + nx * h, y=y + ny * h)
        out.append(s)
    return out


def round_corners(st, radius):
    """Corners drawn as curves, the same way every time (no random change of shape).

    A ballpoint on paper is moved faster than a stylus on glass, so ㅁ, ㅈ, ㄹ turn in
    arcs instead of points. Stroke ends stay put; closed and short strokes (ㅇ, o, dots)
    are only softened so they keep their size."""
    if radius <= 0:
        return st
    step = 0.01
    out = []
    for s in st:
        if "missing" in s or len(s["x"]) < 3:
            out.append(s)
            continue
        x, y = _resample_arc(np.asarray(s["x"], float), np.asarray(s["y"], float), step)
        arc = float(np.hypot(np.diff(x), np.diff(y)).sum())
        closed = math.hypot(x[-1] - x[0], y[-1] - y[0]) < 0.3 * arc
        sig = min(radius, arc / (25 if closed else 10)) / step
        out.append(dict(s, x=_smooth_keep_ends(x, sig), y=_smooth_keep_ends(y, sig)))
    return out


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
    st = round_corners(bow_strokes(long_stems(st, style.stem, style.bar), style.bow), style.round_corners)
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
        if (prev is not None and prev.get("glyph") is not None and prev.get("glyph") == s.get("glyph")
                and len(prev["x"]) > 1 and len(s["x"]) > 1):
            ex, ey = prev["x"][-1], prev["y"][-1]
            sx, sy = s["x"][0], s["y"][0]
            dist = math.hypot(sx - ex, sy - ey)
            if dist < style.ligature_dist and rng.random() < style.ligature:
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
    sp = analysis.get("line_spacing") or 0
    rows, edge = [], a["bottom"].mean_y()
    for b in out:
        if b["filled"] > 0.08:
            break
        if a["kind"] == "box" and not rows and b["top"].mean_y() > edge + 0.5 * max(sp, 20):
            break  # a box only runs on into lines that continue it directly below
        if b["height"] < 0.6 * sp:
            continue  # a sliver between a ruled line and a border is no row to write in
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
    else:
        # nothing written yet: a photo's dpi is only a guess (margins, desk, perspective), the
        # printed rows are not. On the scanned notes the writing is 0.6 of the row pitch.
        rows = [a["height"] for a in analysis.get("areas", []) if a["kind"] == "line"]
        if len(rows) >= 3:
            size = min(size, 0.6 * float(np.median(rows)))
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
        if base_pen.name.startswith(("ballpoint", "gel")):
            # a ballpoint or gel line is 0.25-0.45 mm whatever the measurement says (thin or blurred
            # print on a form can pass for handwriting)
            base_pen = replace(base_pen, width_mm=float(np.clip(base_pen.width_mm, 0.25, 0.45)))
        floor = page_black(img, analysis)
        if floor and base_pen.name in ("ballpoint", "gel", "fineliner"):
            v = int(255 * floor)
            base_pen = replace(base_pen, color=tuple(max(int(c), v) for c in base_pen.color))
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
        rows, rows_fn = [], None
        if "area" in it or "areas" in it:
            ids = it["areas"] if "areas" in it else [it["area"]]
            chosen = [_area_by_id(analysis, int(i)) for i in ids]
            if "areas" not in it and it.get("continue", True):
                chosen += _following(analysis, chosen[0])
            if size_px is None:
                lines = [a["height"] for a in chosen if a["kind"] == "line"]
                size_px = natural_size(analysis, style)
                if lines:
                    size_px = min(size_px, style.fill * float(np.median(lines)) / (1 - style.baseline))

            ids_order = {a["id"]: k for k, a in enumerate(chosen)}

            def rows_fn(sz, chosen=chosen, it=it, ids_order=ids_order):
                out = []
                for a in chosen:
                    if analysis["grid_paper"] and a["kind"] == "box":
                        out += _rows_grid(a, analysis, sz, style)
                    else:
                        out += _label_rows(a, _rows_for_area(a, sz, style, analysis.get("line_spacing")),
                                           analysis, sz, style)
                if it.get("after_existing") and out:
                    out[0] = replace(out[0], x0=max(out[0].x0, chosen[0]["free_from_x"] + 0.4 * sz))
                boxes = {a["id"] for a in chosen if a["kind"] == "box"}
                free = _free_rows([r for r in out if r.area in boxes], analysis, sz)
                return sorted([r for r in out if r.area not in boxes] + free,
                              key=lambda r: (ids_order[r.area], r.base((r.x0 + r.x1) / 2)))

            rows = rows_fn(size_px)
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

            def rows_fn(sz, x=x, y=y, width=width, bottom=bottom):
                out = _straight_rows(x, y - sz * 1.2, x + width, bottom, sz, style, first_base=y)
                out = [replace(r, x0=x) for r in out]
                if out:
                    out[0] = replace(out[0], x0=x - style.indent * sz)
                return out

            rows = rows_fn(size_px)
        else:
            raise ValueError(f"Item {n}: give area, areas, region or at")
        if not rows:
            raise ValueError(f"Item {n}: no room to write there")
        placed, leftover, missing = layout_text(text, rows, size_px, db, style, seed + n)
        if missing:
            raise ValueError("Missing glyphs (nothing written): " + " ".join(sorted(missing)))
        one_line = "at" in it and it.get("width") and "\n" not in text
        if one_line and len(placed) > 1:
            # a short entry in a small box ("2026" in a date field) is written smaller, not wrapped
            leftover = leftover or " ".join(line for _, line in placed[1:])
            placed = placed[:1]
        if leftover and not size and rows_fn is not None:
            # a writer running out of room writes a little smaller rather than stopping
            for k in ((0.92, 0.85, 0.75, 0.65, 0.55) if "at" in it and it.get("width") else (0.92, 0.85)):
                rows2 = rows_fn(size_px * k)
                p2, l2, _ = layout_text(text, rows2, size_px * k, db, style, seed + n) if rows2 else (None, leftover, None)
                if p2 is not None and one_line and len(p2) > 1:
                    l2 = l2 or " ".join(line for _, line in p2[1:])
                    p2 = p2[:1]
                if p2 is not None and len(l2) < len(leftover):
                    rows, placed, leftover, size_px = rows2, p2, l2, size_px * k
                if not leftover:
                    break
        boxes = []
        for k, (row, line) in enumerate(placed):
            st, _ = _place_line(line, row, size_px, db, style, rng, seed * 1000 + n * 50 + k)
            strokes += [dict(q, pen=p) for q in st]
            if st:
                allp = np.vstack([q["points"] for q in st])
                boxes.append([float(allp[:, 0].min()), float(allp[:, 1].min()), float(allp[:, 0].max()), float(allp[:, 1].max())])
        rep = {"item": n, "text": text if len(text) <= 120 else text[:117] + "...",
               "rows_used": len(placed), "size_px": round(size_px, 1),
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
