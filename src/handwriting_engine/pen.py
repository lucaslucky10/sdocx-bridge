"""Physical pen rendering: ballpoint, gel, pencil, highlighter and a plain fineliner.

The glyph dictionary stores pen paths (centre lines). A tablet draws them with a
constant width and colour; a ballpoint on paper does not. This module turns the
same paths into raster ink that behaves like a ballpoint pen seen through a
scanner or a phone "document scan":

* the ball needs a moment to start rolling, so stroke starts are lighter and a
  little thinner, and ink pools at the end of slow strokes;
* the ball skips: ink density changes along the stroke in streaks whose length is
  a few tenths of a millimetre, more on fast and on strongly curved parts;
* paper grain lets light ink break up into speckles;
* where two strokes cross the ink is darker (the layers add up);
* the scanner blurs a little and then stretches contrast.

All sizes are in millimetres and converted with the page's dots per inch, so the
same pen looks the same on a 150 dpi flatbed scan and on a 300 dpi phone photo.
Only numpy and Pillow are used.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image, ImageFilter


@dataclass(frozen=True)
class Pen:
    name: str
    width_mm: float = 0.36  # visible line width of the core
    color: tuple = (22, 22, 26)  # colour of fully inked line after scanning
    density: float = 0.97  # ink coverage in the middle of a stroke (0..1)
    skip: float = 0.28  # strength of ball-skip streaks
    skip_mm: float = 0.9  # typical length of a streak
    start_mm: float = 0.35  # length of the lighter start of a stroke
    start_density: float = 0.55
    blob: float = 0.35  # chance of an ink blob at a slow stroke end
    edge_mm: float = 0.06  # softness of the line edge
    grain: float = 0.35  # paper grain showing through light ink
    pressure_width: float = 0.10  # width change with pressure
    speed_fade: float = 0.25  # lighter on fast straight parts
    blend: str = "multiply"
    flat: bool = False  # highlighter: chisel tip
    scan_blur_px: float = 0.45  # scanner softness at 150 dpi
    contrast: float = 1.25  # document-scanner contrast stretch on the ink


PENS = {
    "ballpoint": Pen("ballpoint"),
    "ballpoint-blue": Pen("ballpoint-blue", color=(28, 52, 150), density=0.93),
    "ballpoint-red": Pen("ballpoint-red", color=(205, 32, 38), density=0.93),
    "gel": Pen("gel", width_mm=0.42, density=0.99, skip=0.06, start_density=0.85, blob=0.15, grain=0.12,
               speed_fade=0.05, contrast=1.35),
    "gel-blue": Pen("gel-blue", width_mm=0.42, color=(25, 50, 160), density=0.99, skip=0.06,
                    start_density=0.85, blob=0.15, grain=0.12, speed_fade=0.05),
    "pencil": Pen("pencil", width_mm=0.5, color=(70, 70, 76), density=0.62, skip=0.35, skip_mm=0.25,
                  start_density=0.7, blob=0.0, edge_mm=0.1, grain=0.85, pressure_width=0.05, speed_fade=0.2,
                  contrast=1.0),
    "highlighter": Pen("highlighter", width_mm=4.0, color=(255, 236, 60), density=0.75, skip=0.05,
                       start_density=0.9, blob=0.0, edge_mm=0.25, grain=0.1, pressure_width=0.0,
                       speed_fade=0.0, flat=True, contrast=1.0),
    "fineliner": Pen("fineliner", width_mm=0.4, density=1.0, skip=0.0, start_density=1.0, blob=0.0,
                     grain=0.0, pressure_width=0.0, speed_fade=0.0, contrast=1.0),
}
ALIASES = {
    "볼펜": "ballpoint", "검정볼펜": "ballpoint", "파란볼펜": "ballpoint-blue", "빨간볼펜": "ballpoint-red",
    "blue": "ballpoint-blue", "red": "ballpoint-red", "black": "ballpoint", "젤펜": "gel", "연필": "pencil",
    "형광펜": "highlighter", "펜": "fineliner", "tablet": "fineliner",
}


def get_pen(name="ballpoint", **overrides) -> Pen:
    key = ALIASES.get(str(name).strip().lower(), str(name).strip().lower())
    if key not in PENS:
        raise ValueError(f"Unknown pen '{name}'. Choose one of: {', '.join(PENS)}")
    pen = PENS[key]
    overrides = {k: v for k, v in overrides.items() if v is not None}
    if "color" in overrides:
        overrides["color"] = tuple(int(c) for c in overrides["color"])
    return replace(pen, **overrides) if overrides else pen


# ---------------------------------------------------------------- noise helpers

def _noise1d(n, corr, rng):
    """Smooth noise with correlation length ``corr`` samples, mean 0, std about 1."""
    if n <= 0:
        return np.zeros(0)
    corr = max(corr, 1.0)
    m = int(n / corr) + 4
    knots = rng.standard_normal(m)
    t = np.arange(n) / corr + 1
    i = np.clip(t.astype(int), 1, m - 3)
    f = t - i
    # Catmull-Rom through the knots
    p0, p1, p2, p3 = knots[i - 1], knots[i], knots[i + 1], knots[i + 2]
    return 0.5 * ((2 * p1) + (-p0 + p2) * f + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f * f
                  + (-p0 + 3 * p1 - 3 * p2 + p3) * f ** 3)


def paper_grain(h, w, seed=0, scale_px=0.8):
    """Fine paper texture in 0..1 (mostly small values, occasional high spots)."""
    rng = np.random.default_rng(seed + 991)
    g = rng.random((h, w)).astype(np.float32)
    img = Image.fromarray((g * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(scale_px))
    g = np.asarray(img, dtype=np.float32) / 255.0
    g = (g - g.mean()) / (g.std() + 1e-6)
    return np.clip(g * 0.5 + 0.2, 0, 1)


# ---------------------------------------------------------------- stroke model

def _resample(points, step):
    p = np.asarray(points, dtype=np.float64)
    if len(p) == 1:
        return p, np.zeros(1)
    d = np.hypot(*np.diff(p, axis=0).T)
    s = np.concatenate([[0], np.cumsum(d)])
    if s[-1] < 1e-9:
        return p[:1], np.zeros(1)
    n = max(2, int(math.ceil(s[-1] / step)) + 1)
    t = np.linspace(0, s[-1], n)
    return np.stack([np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])], 1), t


def _profile(pts, s, pen, px_per_mm, rng, pressure=None, timing=None):
    """Radius (px) and ink density along a resampled stroke."""
    n = len(pts)
    L = s[-1] if n > 1 else 0.0
    r0 = pen.width_mm * px_per_mm / 2
    if pressure is not None and len(pressure) > 1:
        pr = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(pressure)), pressure)
        pr = pr / max(np.median(pr), 1e-6)
    else:
        # hand pressure: quick rise, a gentle wave, a release at the end
        u = s / max(L, 1e-9)
        pr = 0.85 + 0.15 * np.minimum(1, u * 6) - 0.12 * np.clip((u - 0.85) / 0.15, 0, 1)
        pr = pr + 0.05 * _noise1d(n, max(3, 3 * px_per_mm / max(1e-6, (s[1] - s[0]) if n > 1 else 1)), rng)
    radius = r0 * (1 + pen.pressure_width * (np.clip(pr, 0.5, 1.5) - 1))

    dens = np.full(n, pen.density)
    if n > 2:
        step = s[1] - s[0]
        # curvature: a ballpoint lays down more ink on turns, less on long fast lines
        d1 = np.gradient(pts, axis=0)
        ang = np.unwrap(np.arctan2(d1[:, 1], d1[:, 0]))
        curv = np.abs(np.gradient(ang)) / max(step, 1e-6)
        if n >= 5:
            curv = np.convolve(curv, np.ones(5) / 5, mode="same")
        straight = np.clip(1 - curv * px_per_mm * 0.8, 0, 1)
        dens = dens * (1 - pen.speed_fade * straight * np.clip(L / (6 * px_per_mm), 0, 1))
        # ball skip streaks
        corr = max(2.0, pen.skip_mm * px_per_mm / step)
        nz = _noise1d(n, corr, rng)
        dens = dens * (1 - pen.skip * np.clip(nz - 0.35, 0, None) * 0.9)
        # lighter start while the ball begins to roll
        k = np.clip(s / max(pen.start_mm * px_per_mm, 1e-6), 0, 1)
        start = pen.start_density + (1 - pen.start_density) * (1 - (1 - k) ** 2)
        if rng.random() < 0.55:
            dens = dens * start
        # ink pools at a slow end
        if rng.random() < pen.blob:
            m = np.clip((s - (L - 0.25 * px_per_mm)) / (0.25 * px_per_mm), 0, 1)
            dens = np.maximum(dens, m * 1.0)
            radius = radius * (1 + 0.18 * m)
    pr_clip = np.clip(pr, 0.6, 1.3)
    dens = np.clip(dens * (0.9 + 0.1 * pr_clip), 0, 1)
    return radius, dens


def _rasterize(pts, radius, dens, pen, ss, x0, y0, w, h, flat_dir=None):
    """Coverage of one stroke on a supersampled grid (h*ss, w*ss) whose origin is (x0, y0)."""
    H, W = h * ss, w * ss
    out = np.zeros((H, W), np.float32)
    P = (pts - [x0, y0]) * ss
    R = radius * ss
    edge = max(0.5, pen.edge_mm * ss * (radius.mean() / max(pen.width_mm / 2, 1e-6)) / 1.0)
    edge = max(0.6, min(edge, R.mean()))
    if len(P) == 1:
        P = np.vstack([P, P + 0.01])
        R = np.repeat(R, 2)
        dens = np.repeat(dens, 2)
    # process the stroke in chunks of segments to bound memory
    seg = 24
    for a in range(0, len(P) - 1, seg):
        b = min(len(P) - 1, a + seg)
        A, B = P[a:b], P[a + 1:b + 1]
        ra, rb = R[a:b], R[a + 1:b + 1]
        da, db = dens[a:b], dens[a + 1:b + 1]
        rmax = float(max(ra.max(), rb.max())) + edge + 1
        bx0 = int(max(0, math.floor(min(A[:, 0].min(), B[:, 0].min()) - rmax)))
        by0 = int(max(0, math.floor(min(A[:, 1].min(), B[:, 1].min()) - rmax)))
        bx1 = int(min(W, math.ceil(max(A[:, 0].max(), B[:, 0].max()) + rmax) + 1))
        by1 = int(min(H, math.ceil(max(A[:, 1].max(), B[:, 1].max()) + rmax) + 1))
        if bx1 <= bx0 or by1 <= by0:
            continue
        yy, xx = np.mgrid[by0:by1, bx0:bx1].astype(np.float32)
        px = xx.reshape(-1, 1)
        py = yy.reshape(-1, 1)
        AB = (B - A).astype(np.float32)
        L2 = (AB ** 2).sum(1) + 1e-9
        t = ((px - A[:, 0]) * AB[:, 0] + (py - A[:, 1]) * AB[:, 1]) / L2
        t = np.clip(t, 0, 1)
        cx = A[:, 0] + t * AB[:, 0]
        cy = A[:, 1] + t * AB[:, 1]
        if pen.flat and flat_dir is not None:
            # chisel tip: distance measured in a squashed frame
            c, s_ = flat_dir
            dx, dy = px - cx, py - cy
            u = dx * c + dy * s_
            v = -dx * s_ + dy * c
            d = np.sqrt((u * 0.35) ** 2 + v ** 2)
        else:
            d = np.sqrt((px - cx) ** 2 + (py - cy) ** 2)
        r = ra + t * (rb - ra)
        k = da + t * (db - da)
        cov = np.clip((r - d) / edge + 0.5, 0, 1) * k
        out[by0:by1, bx0:bx1] = np.maximum(out[by0:by1, bx0:bx1], cov.max(1).reshape(by1 - by0, bx1 - bx0))
    return out


def render(strokes, size, dpi=150.0, pen="ballpoint", seed=0, background=None, supersample=3,
           scan=True):
    """Draw strokes onto a page.

    strokes: [{"points": [[x, y], ...] in pixels, optional "pen" (Pen or name), "color",
               "pressure"}]
    size: (width, height) in pixels. background: an RGB PIL image of that size (a scan)
    or None for white paper. Returns an RGB PIL image.
    """
    W, H = size
    px_per_mm = dpi / 25.4
    rng = np.random.default_rng(seed)
    base_pen = pen if isinstance(pen, Pen) else get_pen(pen)
    if background is None:
        bg = np.full((H, W, 3), 255, np.float32)
    else:
        bg = np.asarray(background.convert("RGB"), dtype=np.float32).copy()
    grain = None
    # one coverage layer per distinct pen, so crossings of the same pen add up and the
    # scanner treatment applies per ink
    layers: dict = {}
    for st in strokes:
        p = st.get("pen") or base_pen
        p = p if isinstance(p, Pen) else get_pen(p)
        if st.get("color") is not None:
            p = replace(p, color=tuple(int(c) for c in st["color"]))
        pts = np.asarray(st["points"], dtype=np.float64)
        if len(pts) == 0:
            continue
        step = max(0.25, 0.35 * px_per_mm / 6)
        rp, s = _resample(pts, step)
        radius, dens = _profile(rp, s, p, px_per_mm, rng, st.get("pressure"))
        if st.get("ink") is not None:
            # per-point ink amount (e.g. a light connecting hairline between two strokes)
            src = np.asarray(st["ink"], dtype=np.float64)
            d0 = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]) if len(pts) > 1 else np.zeros(1)
            k = np.interp(s, d0, src) if len(src) == len(pts) else np.ones(len(s))
            dens = dens * k
            radius = radius * (0.55 + 0.45 * k)
        pad = float(radius.max()) + 3
        x0 = int(max(0, math.floor(rp[:, 0].min() - pad)))
        y0 = int(max(0, math.floor(rp[:, 1].min() - pad)))
        x1 = int(min(W, math.ceil(rp[:, 0].max() + pad)))
        y1 = int(min(H, math.ceil(rp[:, 1].max() + pad)))
        if x1 <= x0 or y1 <= y0:
            continue
        fd = None
        if p.flat:
            a = math.radians(-35)
            fd = (math.cos(a), math.sin(a))
        cov = _rasterize(rp, radius, dens, p, supersample, x0, y0, x1 - x0, y1 - y0, fd)
        cov = cov.reshape(y1 - y0, supersample, x1 - x0, supersample).mean((1, 3))
        key = p
        if key not in layers:
            layers[key] = np.zeros((H, W), np.float32)
        lay = layers[key][y0:y1, x0:x1]
        # crossings of the same pen darken: 1-(1-a)(1-b)
        layers[key][y0:y1, x0:x1] = 1 - (1 - lay) * (1 - cov)
    out = bg
    for p, a in layers.items():
        if p.grain > 0:
            if grain is None:
                grain = paper_grain(H, W, seed)
            a = a * (1 - p.grain * grain * (1 - a) * 1.6)
            a = np.clip(a, 0, 1)
        if scan:
            blur = p.scan_blur_px * dpi / 150.0
            if blur > 0.05:
                img = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))
                a = np.asarray(img.filter(ImageFilter.GaussianBlur(blur)), dtype=np.float32) / 255
            if p.contrast != 1.0:
                a = np.clip((a - 0.04) * p.contrast, 0, 1)
        ink = np.asarray(p.color, np.float32)
        if p.blend == "multiply":
            factor = 1 - a[..., None] * (1 - ink / 255.0)
            out = out * factor
        else:
            out = out * (1 - a[..., None]) + ink * a[..., None]
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8))
