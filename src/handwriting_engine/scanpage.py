"""Find where to write on a scanned or photographed page.

A scan of a notebook or worksheet is never perfectly straight: the paper bends near
the binding, the scanner or phone adds a little perspective, and so the printed
lines are slightly curved. This module finds those lines in the image and turns
the space between them into writing *areas* that keep the curve:

* ``line`` areas: one row of writing between two printed lines; the baseline is
  the lower printed line itself, as a curve sampled every few pixels.
* ``box`` areas: a cell or box without inner lines (table cells, a blank frame);
  synthetic rows inside it bend like the box's top and bottom edges.

Everything is in image pixels (origin top-left). Only numpy and Pillow are used.
Method: background flattening (divide by a blurred maximum filter), then long
thin dark runs along rows/columns after a one-pixel vertical tolerance (a
morphological opening done with run lengths), chained into curves and refined by
sub-pixel centroids. Grid paper is recognised by its evenly spaced vertical
lines, which then do not cut rows into cells.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


# ---------------------------------------------------------------- image helpers

def to_gray(img):
    return np.asarray(img.convert("L"), dtype=np.float32)


def background(gray, cell=None):
    """Paper brightness everywhere (text and lines removed) for shading correction."""
    h, w = gray.shape
    cell = cell or max(8, int(min(h, w) / 120))
    small = Image.fromarray(gray.astype(np.uint8)).resize((max(1, w // cell), max(1, h // cell)), Image.BOX)
    small = small.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(2))
    bg = np.asarray(small.resize((w, h), Image.BILINEAR), dtype=np.float32)
    return np.maximum(bg, gray)


def darkness(gray, bg=None):
    bg = background(gray) if bg is None else bg
    return np.clip(1 - gray / np.maximum(bg, 1), 0, 1)


def _runs(mask, min_len):
    """Runs of True along axis 1 with length >= min_len → (row, start, end) arrays."""
    m = np.pad(mask, ((0, 0), (1, 1)))
    d = np.diff(m.astype(np.int8), axis=1)
    rs, cs = np.nonzero(d == 1)
    re_, ce = np.nonzero(d == -1)
    # starts and ends come in the same order per row
    keep = (ce - cs) >= min_len
    return rs[keep], cs[keep], ce[keep]


def _hclose(mask, k=2):
    """Bridge gaps of up to 2k pixels along rows (dotted or faint printed lines)."""
    if k <= 0:
        return mask
    c = np.cumsum(np.pad(mask, ((0, 0), (k + 1, k)), constant_values=False).astype(np.int32), axis=1)
    dil = (c[:, 2 * k + 1:] - c[:, : -2 * k - 1]) > 0
    c = np.cumsum(np.pad(~dil, ((0, 0), (k + 1, k)), constant_values=True).astype(np.int32), axis=1)
    return (c[:, 2 * k + 1:] - c[:, : -2 * k - 1]) == 0


def _vdilate(mask, k=1):
    out = mask.copy()
    for s in range(1, k + 1):
        out[s:] |= mask[:-s]
        out[:-s] |= mask[s:]
    return out


class Curve:
    """y = f(x) sampled on a regular x grid; linear extrapolation outside."""

    def __init__(self, xs, ys, thickness=1.0):
        order = np.argsort(xs)
        self.xs = np.asarray(xs, dtype=np.float64)[order]
        self.ys = np.asarray(ys, dtype=np.float64)[order]
        self.thickness = float(thickness)

    @property
    def x0(self):
        return float(self.xs[0])

    @property
    def x1(self):
        return float(self.xs[-1])

    def __call__(self, x):
        x = np.asarray(x, dtype=np.float64)
        y = np.interp(x, self.xs, self.ys)
        if len(self.xs) > 1:
            k0 = (self.ys[min(4, len(self.xs) - 1)] - self.ys[0]) / max(self.xs[min(4, len(self.xs) - 1)] - self.xs[0], 1e-6)
            k1 = (self.ys[-1] - self.ys[max(-5, -len(self.xs))]) / max(self.xs[-1] - self.xs[max(-5, -len(self.xs))], 1e-6)
            y = np.where(x < self.xs[0], self.ys[0] + (x - self.xs[0]) * k0, y)
            y = np.where(x > self.xs[-1], self.ys[-1] + (x - self.xs[-1]) * k1, y)
        return y

    def mean_y(self, x0=None, x1=None):
        x0 = self.x0 if x0 is None else x0
        x1 = self.x1 if x1 is None else x1
        return float(np.mean(self(np.linspace(x0, x1, 16))))

    def to_json(self, step=None):
        n = len(self.xs)
        idx = np.linspace(0, n - 1, min(n, step or 6)).astype(int)
        return [[round(float(self.xs[i]), 1), round(float(self.ys[i]), 1)] for i in idx]


def _chain(segs, max_gap, max_dy):
    """Join (y, x0, x1) segments left to right into lines."""
    segs = sorted(segs, key=lambda s: (s[1], s[0]))
    chains = []
    for y, a, b in segs:
        best, bd = None, None
        for c in chains:
            cy, ca, cb = c[-1]
            if a - cb > max_gap or b < ca:
                continue
            if a < cb:  # overlapping in x: same line only if very close in y
                if abs(y - cy) > 2.5:
                    continue
            if abs(y - cy) > max_dy:
                continue
            d = abs(y - cy) + max(0, a - cb) * 0.02
            if bd is None or d < bd:
                best, bd = c, d
        if best is None:
            chains.append([(y, a, b)])
        else:
            best.append((y, a, b))
    return chains


def _refine(dark, chain, horizontal=True, step=6):
    """Sub-pixel centre of the printed line along its length."""
    D = dark if horizontal else dark.T
    h, w = D.shape
    pts = []
    for y, a, b in chain:
        for x in range(int(a), int(b), step):
            yi = int(round(y))
            y0, y1 = max(0, yi - 4), min(h, yi + 5)
            col = D[y0:y1, x]
            if col.max() < 0.08:
                continue
            ys = np.arange(y0, y1)
            wgt = np.clip(col - 0.5 * col.max(), 0, None) ** 2
            if wgt.sum() <= 0:
                continue
            pts.append((x, float((ys * wgt).sum() / wgt.sum()), float((col > 0.35).sum())))
    if len(pts) < 3:
        return None
    p = np.array(sorted(pts))
    # robust smoothing: median filter then moving average
    ys = p[:, 1]
    k = 5
    if len(ys) >= k:
        pad = np.pad(ys, k // 2, mode="edge")
        med = np.array([np.median(pad[i:i + k]) for i in range(len(ys))])
        ok = np.abs(ys - med) < 2.5
        p = p[ok]
        ys = med[ok]
    if len(ys) >= 7:
        ys = np.convolve(np.pad(ys, 3, mode="edge"), np.ones(7) / 7, mode="valid")
    return Curve(p[:, 0], ys, thickness=float(np.median(p[:, 2])))


def _extend(cv, D, max_gap):
    """Follow a faint line beyond the ends of its detected part (pixel tracking)."""
    h, w = D.shape
    xs, ys = list(cv.xs), list(cv.ys)
    for direction in (1, -1):
        px = list(xs) if direction == 1 else list(xs[::-1])
        py = list(ys) if direction == 1 else list(ys[::-1])
        x, gap, added = px[-1], 0, []
        k = min(len(px), 8)
        slope = (py[-1] - py[-k]) / (px[-1] - px[-k]) if k > 1 and px[-1] != px[-k] else 0.0
        y = py[-1]
        while 0 <= x + 2 * direction < w and gap <= max_gap:
            x += 2 * direction
            y += slope * 2 * direction
            yi = int(round(y))
            if not 2 <= yi < h - 2:
                break
            a, b = yi - 2, yi + 3
            col = D[a:b, int(x)]
            if len(col) and col.max() > 0.05:
                wgt = col ** 2
                yc = float((np.arange(a, b) * wgt).sum() / wgt.sum())
                y = 0.7 * y + 0.3 * yc
                added.append((x, y))
                gap = 0
            else:
                gap += 2
        if len(added) > 3:
            if direction == 1:
                xs = xs + [p[0] for p in added]
                ys = ys + [p[1] for p in added]
            else:
                xs = [p[0] for p in added][::-1] + xs
                ys = [p[1] for p in added][::-1] + ys
    if len(xs) != len(cv.xs):
        ys = np.asarray(ys)
        if len(ys) >= 9:
            ys = np.convolve(np.pad(ys, 4, mode="edge"), np.ones(9) / 9, mode="valid")
        return Curve(xs, ys, cv.thickness)
    return cv


def _plausible(cv, D):
    """Reject 'lines' that run through printed text or a dense row of writing."""
    h, w = D.shape
    xs = np.arange(int(cv.x0), int(cv.x1), 3)
    if len(xs) < 5:
        return False
    ys = np.round(cv(xs)).astype(int)
    if (ys < 3).all() or (ys > h - 4).all():
        return False
    on = np.array([D[max(0, y - 1):y + 2, x].max() if 0 <= y < h else 0 for x, y in zip(xs, ys)])
    above = np.array([D[max(0, y - 12):max(1, y - 4), x].max() if 4 < y < h else 0 for x, y in zip(xs, ys)])
    below = np.array([D[min(h - 1, y + 5):min(h, y + 13), x].max() if y + 5 < h else 0 for x, y in zip(xs, ys)])
    cover = (on > 0.06).mean()
    # printed lines are smooth; handwriting strokes wander
    t = (xs - xs.mean()) / max(1.0, xs.std())
    fit = np.polyval(np.polyfit(t, cv(xs), 2 if len(xs) > 60 else 1), t)
    if np.sqrt(np.mean((cv(xs) - fit) ** 2)) > (2.5 if len(xs) > 60 else 0.9):
        return False
    both = ((above > 0.3) & (below > 0.3)).mean()
    if cv.thickness >= 3.5 and cover < 0.9:
        return False
    if both > 0.22 and cover < 0.92:
        return False
    return cover > 0.7


def find_lines(dark, horizontal=True, min_frac=0.06, thresh=0.09, min_span=0.12):
    """Long thin printed lines (curves) in one direction."""
    D = dark if horizontal else dark.T
    h, w = D.shape
    mask = D > thresh
    tol = _hclose(_vdilate(mask, 1), 2)
    min_len = max(40, int(w * min_frac))
    rs, cs, ce = _runs(tol, min_len)
    if len(rs) == 0:
        return []
    # the one-pixel tolerance repeats a line on neighbouring rows: keep the best row per x-span
    segs = []
    order = np.lexsort((cs, rs))
    rs, cs, ce = rs[order], cs[order], ce[order]
    used = np.zeros(len(rs), bool)
    for i in range(len(rs)):
        if used[i]:
            continue
        group = [i]
        j = i + 1
        while j < len(rs) and rs[j] <= rs[group[-1]] + 1:
            if not used[j] and min(ce[j], ce[i]) - max(cs[j], cs[i]) > 0.5 * min(ce[j] - cs[j], ce[i] - cs[i]):
                group.append(j)
            j += 1
        for g in group:
            used[g] = True
        ys = rs[group]
        segs.append((float(ys.mean()), int(cs[group].min()), int(ce[group].max())))
    # thick lines (box borders) produce several stacked segments: merge rows closer than 3 px
    segs.sort()
    merged = []
    for s in segs:
        if merged and abs(s[0] - merged[-1][0]) <= 3 and min(s[2], merged[-1][2]) - max(s[1], merged[-1][1]) > 0:
            y, a, b = merged[-1]
            merged[-1] = ((y + s[0]) / 2, min(a, s[1]), max(b, s[2]))
        else:
            merged.append(s)
    chains = _chain(merged, max_gap=max(20, int(w * 0.04)), max_dy=max(4, h * 0.004))
    curves = []
    for c in chains:
        span = max(b for _, _, b in c) - min(a for _, a, _ in c)
        cover = sum(b - a for _, a, b in c)
        if span < max(40, w * min_span) or cover < 0.5 * span:
            continue
        cv = _refine(dark, c, horizontal)
        if cv is not None and _plausible(cv, D):
            curves.append(_extend(cv, D, max(12, int(w * 0.012))))
    # drop near-duplicates (double-detected thick borders)
    curves.sort(key=lambda c: c.mean_y())
    out = []
    for c in curves:
        if out:
            p = out[-1]
            ov0, ov1 = max(p.x0, c.x0), min(p.x1, c.x1)
            if ov1 > ov0 and abs(p.mean_y(ov0, ov1) - c.mean_y(ov0, ov1)) < 4:
                if (c.x1 - c.x0) > (p.x1 - p.x0):
                    out[-1] = c
                continue
        out.append(c)
    return out


# ---------------------------------------------------------------- handwriting already on the page

def ink_mask(dark, hlines, vlines, thresh=0.35):
    """Dark pixels that are not the printed lines (handwriting, printed text)."""
    m = dark > thresh
    h, w = m.shape
    for c in hlines:
        xs = np.arange(max(0, int(c.x0)), min(w, int(c.x1) + 1))
        ys = c(xs)
        r = max(2, int(c.thickness / 2) + 2)
        for d in range(-r, r + 1):
            yy = np.clip(np.round(ys + d).astype(int), 0, h - 1)
            m[yy, xs] = False
    for c in vlines:
        ys = np.arange(max(0, int(c.x0)), min(h, int(c.x1) + 1))
        xs = c(ys)
        r = max(2, int(c.thickness / 2) + 2)
        for d in range(-r, r + 1):
            xx = np.clip(np.round(xs + d).astype(int), 0, w - 1)
            m[ys, xx] = False
    return m


def text_height(ink):
    """Typical height (px) of handwritten words, or None."""
    h, w = ink.shape
    rows = ink.any(1)
    if not rows.any():
        return None
    # word blobs: close small horizontal gaps, then measure connected vertical extents per column band
    band = max(4, w // 160)
    cols = ink[:, : (w // band) * band].reshape(h, w // band, band).any(2)
    heights = []
    for j in range(cols.shape[1]):
        c = np.pad(cols[:, j], 1)
        d = np.diff(c.astype(np.int8))
        s, e = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        for a, b in zip(s, e):
            # merge pieces separated by < 15% of a typical letter
            heights.append(b - a)
    heights = np.array(heights)
    heights = heights[(heights > max(6, h * 0.006)) & (heights < h * 0.06)]
    if len(heights) < 20:
        return None
    return float(np.percentile(heights, 70))


# ---------------------------------------------------------------- areas

def analyze(img, dpi=None):
    """Lines, writing areas and existing handwriting on a page image."""
    gray = to_gray(img)
    h, w = gray.shape
    bg = background(gray)
    dark = darkness(gray, bg)
    dpi = dpi or w / 8.27  # assume an A4-wide page when unknown
    hlines = find_lines(dark, True)
    vlines = find_lines(dark, False, min_frac=0.03, min_span=0.035)
    # a printed column line starts and ends on printed rows (table or box borders);
    # vertical handwriting strokes that happen to line up do not
    def _joined(v, y):
        x = float(v(y))
        return any(c.x0 - 6 <= x <= c.x1 + 6 and abs(float(c(x)) - y) < 9 for c in hlines)

    vlines = [v for v in vlines if _joined(v, v.x0) and _joined(v, v.x1)]
    ink = ink_mask(dark, hlines, vlines)
    th = text_height(ink)
    mm = dpi / 25.4
    spacing = [b.mean_y() - a.mean_y() for a, b in zip(hlines, hlines[1:])]
    spacing = [s for s in spacing if 3 * mm < s < 25 * mm]
    typ = float(np.median(spacing)) if spacing else None
    # grid or fine-ruled paper (rows narrower than a handwritten line): write freely and
    # snap rows to the printed lines instead of filling every row
    grid = bool(typ and typ < 6.5 * mm and len(hlines) >= 10)
    areas = []
    # a writing line under a printed label is still one line, so allow up to ~2 rows
    min_h, max_line_h = 4.0 * mm, max(16 * mm, 1.8 * typ if typ else 0)
    if grid:
        # grid paper: one free writing box; rows snap to the grid lines while writing
        x0 = float(np.percentile([c.x0 for c in hlines], 20))
        x1 = float(np.percentile([c.x1 for c in hlines], 80))
        top, bot = hlines[0], hlines[-1]
        areas.append(_area(top, bot, max(x0, 0), min(x1, w), "box", ink, gray))
        hlines_for_areas = []
    else:
        hlines_for_areas = hlines
    for i, top in enumerate(hlines_for_areas):
        # the next line below that overlaps horizontally
        for bot in hlines_for_areas[i + 1:]:
            ov0, ov1 = max(top.x0, bot.x0), min(top.x1, bot.x1)
            if ov1 - ov0 < 0.5 * min(top.x1 - top.x0, bot.x1 - bot.x0):
                continue
            gap = bot.mean_y(ov0, ov1) - top.mean_y(ov0, ov1)
            if gap < min_h:
                break
            # vertical lines crossing this band split it into cells (not on grid paper)
            ymid_top, ymid_bot = top.mean_y(ov0, ov1), bot.mean_y(ov0, ov1)
            cuts = [ov0, ov1]
            if not grid:
                for v in vlines:
                    if v.x0 <= ymid_top + gap * 0.3 and v.x1 >= ymid_bot - gap * 0.3:
                        xv = float(v((ymid_top + ymid_bot) / 2))
                        if ov0 + 8 < xv < ov1 - 8:
                            cuts.append(xv)
            cuts = sorted(cuts)
            for a, b in zip(cuts, cuts[1:]):
                if b - a < 6 * mm:
                    continue
                kind = "line" if gap <= max_line_h else "box"
                areas.append(_area(top, bot, a, b, kind, ink, gray, band=9 * mm))
            break
    areas.sort(key=lambda r: (round(r["_ymid"] / max(min_h, 1)), r["x0"]))
    # reading order: by row band, then left to right
    areas.sort(key=lambda r: (r["_ymid"], r["x0"]))
    rows = []
    for a in areas:
        if rows and abs(a["_ymid"] - rows[-1][0]["_ymid"]) < min_h * 0.6:
            rows[-1].append(a)
        else:
            rows.append([a])
    ordered = [a for r in rows for a in sorted(r, key=lambda a: a["x0"])]
    if not ordered:
        # plain or dotted paper: the page inside the usual margins is one box
        m = 12 * mm
        top = Curve([m, w - m], [m, m], 0.0)
        bot = Curve([m, w - m], [h - m, h - m], 0.0)
        ordered = [_area(top, bot, m, w - m, "box", ink, gray)]
    for k, a in enumerate(ordered, 1):
        a["id"] = k
    letter = _letter_height(ink, ordered, hlines, mm)
    return {
        "width": w,
        "height": h,
        "dpi": round(dpi, 1),
        "grid_paper": bool(grid),
        "line_spacing": round(typ, 1) if typ else None,
        "handwriting_height": round(th, 1) if th else None,
        "letter_height": letter,
        "hlines": hlines,
        "vlines": vlines,
        "areas": ordered,
        "_ink": ink,
        "_dark": dark,
        "_bg": bg,
    }


def _letter_height(ink, areas, hlines, mm):
    """Height of the handwriting already on the page (px): the ink band of written rows."""
    hs = []
    h, w = ink.shape
    for a in areas:
        if a["kind"] != "line" or a["filled"] < 0.2:
            continue
        xs = np.arange(int(a["x0"]), int(a["x1"]))
        yt, yb = a["top"](xs), a["bottom"](xs)
        rows = np.zeros(int(np.max(yb - yt)) + 6)
        for i in range(0, len(xs), 2):
            t, b = int(yt[i] + 2), int(yb[i] + 4)
            if b > t and 0 <= t and b <= h:
                col = ink[t:b, xs[i]]
                rows[: len(col)] += col
        if rows.sum() < 50:
            continue
        # writing rests on the bottom line; a printed label higher up is a separate
        # block of ink: keep only the block that starts near the line
        on = rows > 0.03 * rows.max()
        n = len(rows)
        start = next((i for i in range(n - 1, int(n * 0.8) - 6, -1) if on[i]), None)
        if start is None:
            continue
        end, gap = start, 0
        for i in range(start, -1, -1):
            gap = 0 if on[i] else gap + 1
            if gap >= 4:
                break
            if on[i]:
                end = i
        rows = np.where((np.arange(n) >= end) & (np.arange(n) <= start), rows, 0)
        if rows.sum() < 50:
            continue
        c = np.cumsum(rows) / rows.sum()
        hs.append(float(np.searchsorted(c, 0.95) - np.searchsorted(c, 0.08)))
    if len(hs) < 2:
        return None
    return round(float(np.median(hs)), 1)


def _area(top, bot, x0, x1, kind, ink, gray, band=None):
    """A writing area; on a line, only the band just above the line counts as written
    (a printed label higher up in the same row is not writing)."""
    h, w = ink.shape
    xs = np.arange(int(max(0, x0)), int(min(w, x1)))
    yt = top(xs)
    yb = bot(xs)
    occ = np.zeros(len(xs), bool)
    for i, x in enumerate(xs):
        a = int(math.ceil(yt[i] + top.thickness / 2 + 2))
        b = int(math.floor(yb[i] - bot.thickness / 2 - 1))
        if band and kind == "line":
            a = max(a, b - int(band))
        if b > a:
            occ[i] = ink[a:b, x].any()
    # where the writing on this row ends (free space to the right)
    used = np.nonzero(occ)[0]
    free_from = float(xs[used[-1]] + 1) if len(used) else float(xs[0])
    filled = float(occ.mean()) if len(occ) else 0.0
    return {
        "kind": kind,
        "x0": round(float(x0), 1),
        "x1": round(float(x1), 1),
        "top": top,
        "bottom": bot,
        "height": round(float(np.median(yb - yt)), 1),
        "filled": round(filled, 3),
        "free_from_x": round(free_from, 1),
        "_ymid": float(np.median((yt + yb) / 2)),
    }


def area_json(a):
    return {
        "id": a["id"],
        "kind": a["kind"],
        "x0": a["x0"],
        "x1": a["x1"],
        "y_top": round(a["top"].mean_y(a["x0"], a["x1"]), 1),
        "y_bottom": round(a["bottom"].mean_y(a["x0"], a["x1"]), 1),
        "height": a["height"],
        "filled": a["filled"],
        "free_from_x": a["free_from_x"],
        "bottom_curve": a["bottom"].to_json(),
    }


def summary(an):
    return {
        "width": an["width"],
        "height": an["height"],
        "dpi_estimate": an["dpi"],
        "grid_paper": an["grid_paper"],
        "line_spacing_px": an["line_spacing"],
        "handwriting_height_px": an["handwriting_height"],
        "printed_lines": {"horizontal": len(an["hlines"]), "vertical": len(an["vlines"])},
        "areas": [area_json(a) for a in an["areas"]],
    }


def _font(size):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def preview(img, an, show_lines=True, max_side=1600):
    """The page with writing areas tinted and numbered (empty = green, used = orange)."""
    base = img.convert("RGB")
    over = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    w, h = base.size
    fs = max(12, int(min(w, h) / 70))
    font = _font(fs)
    for a in an["areas"]:
        xs = np.linspace(a["x0"], a["x1"], 24)
        top = a["top"](xs) + a["top"].thickness / 2 + 1
        bot = a["bottom"](xs) - a["bottom"].thickness / 2 - 1
        poly = list(zip(xs, top)) + list(zip(xs[::-1], bot[::-1]))
        col = (40, 180, 80, 60) if a["filled"] < 0.08 else (240, 150, 30, 60)
        d.polygon(poly, fill=col)
        if a["kind"] == "box":
            d.polygon(poly, outline=(40, 90, 220, 160))
        tx, ty = a["x0"] + 3, float(np.mean(top + bot) / 2) - fs / 2
        label = str(a["id"])
        tw = d.textlength(label, font=font)
        d.rectangle([tx - 2, ty - 1, tx + tw + 2, ty + fs + 2], fill=(255, 255, 255, 220))
        d.text((tx, ty), label, fill=(200, 0, 0, 255), font=font)
    if show_lines:
        for c in an["hlines"]:
            xs = np.linspace(c.x0, c.x1, 40)
            d.line(list(zip(xs, c(xs))), fill=(0, 120, 255, 140), width=1)
        for c in an["vlines"]:
            ys = np.linspace(c.x0, c.x1, 40)
            d.line(list(zip(c(ys), ys)), fill=(255, 0, 160, 140), width=1)
    out = Image.alpha_composite(base.convert("RGBA"), over).convert("RGB")
    s = min(1.0, max_side / max(w, h))
    if s < 1:
        out = out.resize((int(w * s), int(h * s)), Image.LANCZOS)
    return out
