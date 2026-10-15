"""Scanned paper pages: find where to write, write like a real pen, keep the scan look.

These functions back the MCP tools ``analyze_scan``, ``view_scan``,
``write_on_scan``, ``make_blank_page``, ``pen_samples`` and ``learn_paper_style``.
A *scan* is a photo or scanner image (.jpg/.png/…) or a PDF whose pages are
scans. All coordinates are pixels of the page image (origin top-left), so what
Claude reads off ``view_scan(grid=true)`` is what it passes back.
"""
from __future__ import annotations

import io
import json
import math
import re
from dataclasses import asdict, fields, replace
from pathlib import Path

import numpy as np
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageOps

from handwriting_engine import paper as paperlib
from handwriting_engine import pen as penlib
from handwriting_engine import scanpage

from .files import find_files as _find
from .security import export_transaction, identifier, json_write, under

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
SCAN_EXT = IMAGE_EXT | {".pdf"}
MAX_PIXELS = 60_000_000
_CACHE: dict = {}


# ---------------------------------------------------------------- input

def resolve_scan(library, source: str, roots) -> Path:
    """A scan named by path, file name, or the export_id of an earlier result."""
    if not source or len(source) > 1000 or "\x00" in source:
        raise ValueError("Give a file path, file name or export_id")
    if re.fullmatch(r"[0-9a-f]{16}", source):
        folder = under(library.root / "exports", identifier(source))
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        main = result.get("scan_output")
        if not main:
            raise ValueError("That export is not a scan result")
        return folder / main
    p = Path(source).expanduser()
    allowed = list(roots) + [(library.root / "exports").resolve()]
    if not p.is_absolute():
        hits = [r / source for r in roots if (r / source).is_file()]
        if not hits:
            found = _find(roots, Path(source).name, tuple(SCAN_EXT), limit=5)
            hits = [Path(f["path"]) for f in found if Path(f["path"]).name == Path(source).name]
        if not hits:
            raise ValueError("Scan not found in the allowed folders. Use find_files(kind='scan').")
        if len(hits) > 1:
            raise ValueError("Several files have that name; pass the full path: " + "; ".join(map(str, hits)))
        p = hits[0]
    p = p.resolve()
    if not any(p.is_relative_to(r) for r in allowed):
        raise ValueError("That folder is not allowed. Allowed: " + "; ".join(str(r) for r in allowed))
    if p.suffix.lower() not in SCAN_EXT or not p.is_file():
        raise ValueError("A scan must be an existing image (.jpg .png .webp .bmp .tif) or PDF")
    return p


def _pdf_physical_dpi(page_rect, px_w):
    inches = page_rect.width / 72
    if 4.0 <= inches <= 13.0:
        return px_w / inches
    return None


def load_page(path: Path, page: int = 1):
    """(RGB image, info) for one page of a scan. Page numbers start at 1."""
    info = {"path": str(path), "kind": "pdf" if path.suffix.lower() == ".pdf" else "image"}
    if info["kind"] == "image":
        if page != 1:
            raise ValueError("An image has one page")
        with Image.open(path) as im:
            if im.width * im.height > MAX_PIXELS:
                raise ValueError("Image is too large")
            im = ImageOps.exif_transpose(im)
            info["format"] = im.format or path.suffix[1:].upper()
            info["jpeg"] = _jpeg_settings(im) if (im.format == "JPEG") else None
            img = im.convert("RGB")
        info["pages"] = 1
        info["dpi"] = None
        return img, info
    with fitz.open(path) as doc:
        if not 1 <= page <= len(doc):
            raise ValueError(f"page must be 1..{len(doc)}")
        pg = doc[page - 1]
        info["pages"] = len(doc)
        info["page_rect"] = [pg.rect.width, pg.rect.height]
        imgs = pg.get_images(full=True)
        img = None
        if len(imgs) == 1:
            xref = imgs[0][0]
            rects = pg.get_image_rects(xref)
            if rects and rects[0].get_area() >= 0.85 * pg.rect.get_area() and not pg.rotation:
                raw = doc.extract_image(xref)
                with Image.open(io.BytesIO(raw["image"])) as im:
                    info["jpeg"] = _jpeg_settings(im) if im.format == "JPEG" else None
                    img = im.convert("RGB")
                info["image_rect"] = list(rects[0])
        if img is None:
            zoom = 200 / 72
            pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            info["image_rect"] = [0, 0, pg.rect.width, pg.rect.height]
            info["rendered"] = True
        info["dpi"] = _pdf_physical_dpi(fitz.Rect(info["image_rect"]), img.width)
    return img, info


def _jpeg_settings(im):
    """Quantisation tables and chroma subsampling of a JPEG, to save the result the same way."""
    try:
        from PIL import JpegImagePlugin

        q = getattr(im, "quantization", None)
        sub = JpegImagePlugin.get_sampling(im)
        if q:
            return {"qtables": {k: list(v) for k, v in q.items()}, "subsampling": sub}
    except Exception:  # noqa: BLE001 - fall back to a fixed quality
        pass
    return None


def _encode(img, jpeg):
    buf = io.BytesIO()
    if jpeg and jpeg.get("qtables"):
        qt = [jpeg["qtables"][k] for k in sorted(jpeg["qtables"], key=int)]
        sub = jpeg.get("subsampling", -1)
        img.save(buf, "JPEG", qtables=qt, subsampling=sub if sub in (0, 1, 2) else 2, optimize=True)
    else:
        img.save(buf, "JPEG", quality=90, subsampling=2, optimize=True)
    return buf.getvalue()


def analyze(img, info, paper_width_mm=None):
    key = (info["path"], info.get("page", 1), img.size, hash(img.tobytes()[::9973]), paper_width_mm)
    if key in _CACHE:
        return _CACHE[key]
    dpi = info.get("dpi")
    if paper_width_mm:
        dpi = img.width / (paper_width_mm / 25.4)
    an = scanpage.analyze(img, dpi=dpi)
    if len(_CACHE) > 6:
        _CACHE.clear()
    _CACHE[key] = an
    return an


# ---------------------------------------------------------------- previews

def _font(size):
    from PIL import ImageFont

    for name in ("malgun.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def grid_overlay(img, region=None, max_side=1600):
    """Crop to region (pixels) and draw labelled pixel coordinates."""
    w, h = img.size
    x0, y0, x1, y1 = (0, 0, w, h) if not region else [int(round(v)) for v in region]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError("region is outside the page")
    crop = img.crop((x0, y0, x1, y1)).convert("RGB")
    s = min(max_side / crop.width, max_side / crop.height, 4.0)
    crop = crop.resize((max(1, int(crop.width * s)), max(1, int(crop.height * s))), Image.LANCZOS)
    d = ImageDraw.Draw(crop, "RGBA")
    span = max(x1 - x0, y1 - y0)
    step = next(st for st in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000) if span / st <= 16)
    font = _font(max(11, int(13 * max(1, s * 0.7))))
    gx = math.ceil(x0 / step) * step
    while gx <= x1:
        X = (gx - x0) * s
        d.line([(X, 0), (X, crop.height)], fill=(30, 120, 240, 90), width=1)
        d.text((X + 2, 2), str(gx), fill=(20, 90, 220, 255), font=font)
        gx += step
    gy = math.ceil(y0 / step) * step
    while gy <= y1:
        Y = (gy - y0) * s
        d.line([(0, Y), (crop.width, Y)], fill=(30, 120, 240, 90), width=1)
        d.text((2, Y + 1), str(gy), fill=(20, 90, 220, 255), font=font)
        gy += step
    return crop


def png_bytes(img, max_side=1600):
    s = min(1.0, max_side / max(img.size))
    if s < 1:
        img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- style

def style_path(library):
    return library.root / "profiles" / "paper-style.json"


def load_style(library):
    p = style_path(library)
    if not p.is_file():
        return paperlib.DEFAULT_STYLE
    data = json.loads(p.read_text(encoding="utf-8"))
    names = {f.name for f in fields(paperlib.PaperStyle)}
    clean = {k: float(v) for k, v in data.get("style", {}).items() if k in names and isinstance(v, (int, float))}
    return replace(paperlib.DEFAULT_STYLE, **clean)


def style_with(style, overrides):
    if not overrides:
        return style
    if not isinstance(overrides, dict):
        raise ValueError("style must be an object")
    names = {f.name for f in fields(paperlib.PaperStyle)}
    bad = set(overrides) - names
    if bad:
        raise ValueError("Unknown style keys: " + ", ".join(sorted(bad)) + ". Known: " + ", ".join(sorted(names)))
    return replace(style, **{k: float(v) for k, v in overrides.items()})


# ---------------------------------------------------------------- write

def write_on_scan(library, db, path, page, items, pen="ballpoint", seed=7, style=None, output="same",
                  paper_width_mm=None, match_page_ink=True):
    img, info = load_page(path, page)
    info["page"] = page
    an = analyze(img, info, paper_width_mm)
    st = style_with(load_style(library), style)
    items = _clean_items(items)
    out, report = paperlib.write(img, an, items, db, pen=pen, style=st, seed=seed, calibrate_pen=match_page_ink)
    stem = re.sub(r"[^\w.\- ()가-힣]", "_", Path(path).stem)[:60] or "scan"
    with export_transaction(library.root) as (eid, folder):
        files = []
        fmt = output
        if fmt == "same":
            fmt = "pdf" if info["kind"] == "pdf" else ("png" if path.suffix.lower() == ".png" else "jpg")
        if fmt == "pdf":
            name = f"{stem}_손글씨.pdf"
            _save_pdf(path if info["kind"] == "pdf" else None, page, out, _page_info(info, out, an), folder / name)
        elif fmt == "png":
            name = f"{stem}_손글씨.png"
            out.save(folder / name, "PNG", optimize=True)
        elif fmt in ("jpg", "jpeg"):
            name = f"{stem}_손글씨.jpg"
            (folder / name).write_bytes(_encode(out, info.get("jpeg")))
        else:
            raise ValueError("output: same, pdf, jpg or png")
        files.append(name)
        prev = folder / "preview.png"
        prev.write_bytes(png_bytes(out, 2000))
        preview_pdf = name if fmt == "pdf" else None
        if not preview_pdf:
            preview_pdf = "preview.pdf"
            _save_pdf(None, 1, out, _page_info(info, out, an), folder / preview_pdf)
        result = {
            "export_id": eid,
            "source": str(path),
            "page": page,
            "files": files + ["preview.png"],
            "scan_output": name,
            "preview_pdf": preview_pdf,
            "report": report,
        }
        json_write(folder / "result.json", result)
    return result, out, an


def _page_info(info, img, an):
    if info.get("image_rect"):
        return info
    k = 72 / an["dpi"]
    return dict(info, image_rect=[0, 0, img.width * k, img.height * k])


def _clean_items(items):
    from .handwriting import parse_color

    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise ValueError("items: a list of 1..200 objects")
    out = []
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("Each item must be an object")
        it = dict(it)
        if isinstance(it.get("color"), str):
            it["color"] = parse_color(it["color"])
        if isinstance(it.get("text"), str) and len(it["text"]) > 20000:
            raise ValueError("text is too long (max 20000 characters per item)")
        out.append(it)
    return out


def _save_pdf(src_pdf, page, img, info, dest):
    data = _encode(img, info.get("jpeg"))
    if src_pdf:
        with fitz.open(src_pdf) as doc:
            pg = doc[page - 1]
            rect = fitz.Rect(info["image_rect"])
            if info.get("rendered"):
                # vector page: put the written raster on top of the whole page
                pg.insert_image(rect, stream=data, overlay=True)
            else:
                # same pixel size: swap the JPEG data of the page's image in place
                xref = pg.get_images(full=True)[0][0]
                doc.update_stream(xref, data, compress=False)
                for key, value in (("Filter", "/DCTDecode"), ("Width", str(img.width)), ("Height", str(img.height)),
                                   ("ColorSpace", "/DeviceRGB"), ("BitsPerComponent", "8"),
                                   ("DecodeParms", "null"), ("SMask", "null"), ("Decode", "null")):
                    doc.xref_set_key(xref, key, value)
            doc.save(dest, garbage=3, deflate=True)
    else:
        r = info.get("image_rect") or [0, 0, img.width * 72 / 150, img.height * 72 / 150]
        with fitz.open() as doc:
            pg = doc.new_page(width=r[2] - r[0], height=r[3] - r[1])
            pg.insert_image(pg.rect, stream=data)
            doc.save(dest, garbage=3, deflate=True)


# ---------------------------------------------------------------- blank pages and samples

def make_blank(library, kind="lined", paper="A4", spacing_mm=None, dpi=150, seed=1):
    sizes = {"A4": (210, 297), "B5": (182, 257), "A5": (148, 210), "letter": (216, 279)}
    if paper not in sizes:
        raise ValueError("paper: A4, B5, A5 or letter")
    if kind not in ("lined", "grid", "dotted", "plain"):
        raise ValueError("kind: lined, grid, dotted or plain")
    w, h = sizes[paper]
    img = paperlib.blank_page(kind, w, h, dpi, seed, spacing_mm)
    with export_transaction(library.root) as (eid, folder):
        name = f"blank-{kind}.jpg"
        (folder / name).write_bytes(_encode(img, None))
        json_write(folder / "result.json", {"export_id": eid, "files": [name], "scan_output": name,
                                            "preview_pdf": None, "paper_width_mm": w})
    return eid, library.root / "exports" / eid / name, img


def pen_samples(db, text="볼펜으로 쓴 글씨 abc 123", dpi=200, seed=3):
    """One row per pen on white paper."""
    mm = dpi / 25.4
    size = 5.8 * mm
    names = ["ballpoint", "ballpoint-blue", "ballpoint-red", "gel", "pencil", "fineliner"]
    from handwriting_engine.compose import compose

    st, _, _ = compose(db, text, width=1e9, seed=seed)
    st = [s for s in st if "missing" not in s]
    width = int(max(float(np.max(s["x"])) for s in st) * size * 0.95 + 60 + 40 * mm)
    canvas_h = int(len(names) * size * 1.9 + 20)
    strokes = []
    label = []
    for i, n in enumerate(names):
        base = 20 + (i + 1) * size * 1.9 - size * 0.5
        pen = penlib.get_pen(n)
        for s in st:
            pts = np.stack([40 * mm + np.asarray(s["x"]) * size * 0.95, base + (np.asarray(s["y"]) - 1) * size], 1)
            strokes.append({"points": pts, "pen": pen})
        label.append((n, base))
    hl = penlib.get_pen("highlighter")
    strokes.append({"points": np.array([[40 * mm, label[0][1] - size * 0.35], [40 * mm + 20 * mm, label[0][1] - size * 0.3]]),
                    "pen": hl})
    img = penlib.render(strokes, (width, canvas_h), dpi=dpi, pen="ballpoint", seed=seed)
    d = ImageDraw.Draw(img)
    f = _font(int(3 * mm))
    for n, b in label:
        d.text((4, b - size * 0.8), n, fill=(120, 120, 120), font=f)
    return img


# ---------------------------------------------------------------- learning from the user's own scans

def learn_style(library, db, samples, roots):
    """Fit size, width and baseline of paper handwriting to rows the user wrote.

    samples: [{"path", "page", "area", "text"}] - one written row each (Claude reads the
    text from the image). The same measurement is applied to the real row and to a
    synthetic row written into the same place (after erasing it); the style is
    corrected until both agree. Returns the fitted values, saved for later writes.
    """
    if not isinstance(samples, list) or not 2 <= len(samples) <= 60:
        raise ValueError("Give 2..60 samples")
    style = load_style(library)
    real, cases = [], []
    for s in samples:
        path = resolve_scan(library, str(s["path"]), roots)
        img, info = load_page(path, int(s.get("page", 1)))
        info["page"] = int(s.get("page", 1))
        an = analyze(img, info)
        a = next((x for x in an["areas"] if x["id"] == int(s["area"])), None)
        if a is None or a["kind"] != "line":
            raise ValueError(f"Sample area {s.get('area')} is not a written line area")
        text = str(s["text"]).strip()
        if not text:
            raise ValueError("Sample text is empty")
        cases.append((img, an, a, text))
        real.append(_row_metrics(an, a, an["_ink"]))
    report = []
    for _ in range(3):
        syn = [_synth_metrics(img, an, a, text, db, style) for img, an, a, text in cases]
        H = np.median([r["H"] / max(q["H"], 1) for r, q in zip(real, syn)])
        Wr = np.median([r["width"] / max(q["width"], 1) for r, q in zip(real, syn)])
        B = np.median([(r["bottom"] - q["bottom"]) / max(r["H"], 1) for r, q in zip(real, syn)])
        style = replace(style, unit_per_height=float(np.clip(style.unit_per_height * H, 0.7, 1.6)),
                        x_scale=float(np.clip(style.x_scale * Wr / H, 0.7, 1.3)),
                        baseline=float(np.clip(style.baseline - 0.7 * B, -0.1, 0.5)))
        report.append({"height_ratio": round(float(H), 3), "width_ratio": round(float(Wr), 3),
                       "baseline_shift": round(float(B), 3)})
        if abs(H - 1) < 0.03 and abs(Wr - 1) < 0.03 and abs(B) < 0.03:
            break
    keep = {k: round(v, 4) for k, v in asdict(style).items() if k in ("unit_per_height", "x_scale", "baseline")}
    json_write(style_path(library), {"style": keep, "samples": len(samples), "iterations": report})
    return {"style": keep, "iterations": report}


def _row_metrics(an, a, ink):
    xs = np.arange(int(a["x0"]) + 3, int(a["x1"]) - 3)
    prof = np.zeros(400)
    cols = []
    for x in xs:
        t, b = float(a["top"](x)), float(a["bottom"](x))
        t0, b0 = int(t + a["top"].thickness / 2 + 2), int(b + 8)
        col = ink[max(0, t0):b0, x]
        if col.any():
            cols.append(x)
            for v in np.nonzero(col)[0] + t0 - int(b):
                if -200 <= v < 200:
                    prof[v + 200] += 1
    if prof.sum() == 0:
        return {"H": 1, "bottom": 0, "width": 1}
    c = np.cumsum(prof) / prof.sum()
    top, bot = np.searchsorted(c, 0.05) - 200, np.searchsorted(c, 0.97) - 200
    return {"H": float(bot - top), "bottom": float(bot), "width": float(max(cols) - min(cols)) if cols else 1.0}


def _synth_metrics(img, an, a, text, db, style):
    box = [a["x0"] + 2, a["top"].mean_y() + a["top"].thickness, a["x1"] - 2, a["bottom"].mean_y() - 1]
    blank = paperlib.erase(img, an, box)
    an2 = scanpage.analyze(blank, dpi=an["dpi"])
    b = min(an2["areas"], key=lambda r: abs(r["_ymid"] - a["_ymid"]) + abs(r["x0"] - a["x0"]))
    an2["letter_height"] = an.get("letter_height")
    out, _ = paperlib.write(blank, an2, [{"text": text, "areas": [b["id"]]}], db, style=style, seed=11)
    g = np.asarray(out.convert("L"), np.float32)
    bg = np.asarray(blank.convert("L"), np.float32)
    return _row_metrics(an2, b, (bg - g) > 80)
