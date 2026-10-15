from __future__ import annotations

import json
import math
import uuid
from collections import defaultdict
from pathlib import Path
from filelock import FileLock

import pymupdf as fitz
from handwriting_engine.compose import GlyphDB, compose

from .layout import typical_char_height
from .ink import check_ink, draw_pdf_ink, sdocx_export, stroke_bounds, svg
from .security import identifier, json_write, export_transaction


def profile_path(library, name):
    return library.root / "profiles" / (identifier(name) + ".json")


def validate_profile(data):
    if not isinstance(data, dict):
        raise ValueError("Profile must be a JSON object")
    glyphs = data.get("glyphs")
    if not isinstance(glyphs, list) or not 1 <= len(glyphs) <= 100000:
        raise ValueError("Profile needs 1..100000 labelled glyphs")
    count = 0
    for g in glyphs:
        if not isinstance(g, dict):
            raise ValueError("Each glyph must be an object")
        provenance = g.get("provenance", {})
        if (
            not isinstance(provenance, dict)
            or not isinstance(provenance.get("stroke_ids", []), list)
            or any(not isinstance(sid, str) for sid in provenance.get("stroke_ids", []))
        ):
            raise ValueError("Invalid glyph provenance")
        if not isinstance(g.get("ch"), str) or not 1 <= len(g["ch"]) <= 32:
            raise ValueError("Invalid glyph label")
        for k in ("adv", "top", "bot"):
            if (
                not isinstance(g.get(k), (int, float))
                or not math.isfinite(g[k])
                or abs(g[k]) > 100
            ):
                raise ValueError("Invalid glyph metric")
        if (
            not isinstance(g.get("src"), str)
            or not isinstance(g.get("strokes"), list)
            or not g["strokes"]
        ):
            raise ValueError("Glyph source and strokes required")
        for s in g["strokes"]:
            if (
                not isinstance(s, dict)
                or not isinstance(s.get("p"), list)
                or not isinstance(s.get("c"), list)
            ):
                raise ValueError("Glyph strokes require coordinate and color arrays")
            if len(s["c"]) != 3 or any(
                type(c) not in (int, float) or not 0 <= c <= 255 for c in s["c"]
            ):
                raise ValueError("Glyph color must contain three numbers in 0..255")
            count += len(s["p"])
            if count > 10_000_000:
                raise ValueError("Profile point budget exceeded")
            if (
                not isinstance(s.get("w"), (int, float))
                or not math.isfinite(s["w"])
                or s["w"] < 0
            ):
                raise ValueError("Invalid glyph width")
            # Legacy dictionaries contain zero-width samples. compose() uses the
            # profile's measured pen width, so retaining the original is safe.
            check_ink(
                {
                    "width": 100,
                    "height": 100,
                    "strokes": [
                        {
                            "points": s["p"],
                            "width": max(0.0001, s["w"]),
                            "color": [c / 255 for c in s["c"]],
                        }
                    ],
                }
            )
    if count > 10_000_000:
        raise ValueError("Profile point budget exceeded")
    if not isinstance(data.get("stats", {}), dict):
        raise ValueError("Profile statistics must be an object")
    for key in ("gap", "space", "pen"):
        v = data.get("stats", {}).get(key, 0.1)
        if not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v < 10:
            raise ValueError("Invalid profile statistics")
    gaps = data.get("stats", {}).get("gaps", {})
    if not isinstance(gaps, dict):
        raise ValueError("Spacing quantiles must be an object")
    for series in gaps.values():
        if (
            not isinstance(series, list)
            or len(series) != 21
            or any(
                not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 10
                for v in series
            )
        ):
            raise ValueError("Invalid spacing quantiles")
    runs = data.get("runs", [])
    if not isinstance(runs, list) or any(
        not isinstance(r, dict)
        or not isinstance(r.get("text"), str)
        or not isinstance(r.get("src"), str)
        for r in runs
    ):
        raise ValueError("Invalid profile text runs")
    return data


def import_profile(library, source, name="my-handwriting"):
    source = Path(source)
    if source.stat().st_size > 256 * 1024**2:
        raise ValueError("Profile exceeds 256 MiB")
    data = validate_profile(json.loads(source.read_text(encoding="utf-8")))
    target = profile_path(library, name)
    with FileLock(str(target) + ".lock", timeout=30):
        if target.exists():
            raise ValueError("Profile already exists; choose a new name")
        json_write(target, data)
        return profile_summary(name, data)


def sync_profile_file(library, source, name="my-handwriting"):
    """Keep a profile in step with a glyphs.json the user picked in the Claude extension settings.

    Installs it when missing and refreshes it when the chosen file is newer than the
    installed copy. Returns the profile summary, or None when nothing was configured.
    """
    if not source or str(source).startswith("${"):
        return None
    source = Path(source).expanduser()
    if not source.is_file():
        raise ValueError(f"Handwriting file not found: {source}")
    if source.stat().st_size > 256 * 1024**2:
        raise ValueError("Profile exceeds 256 MiB")
    target = profile_path(library, name)
    with FileLock(str(target) + ".lock", timeout=30):
        if target.is_file() and target.stat().st_mtime >= source.stat().st_mtime:
            return profile_summary(name, json.loads(target.read_text(encoding="utf-8")))
        data = validate_profile(json.loads(source.read_text(encoding="utf-8")))
        json_write(target, data)
        return profile_summary(name, data)


def profile_summary(name, data):
    chars = sorted(set(g["ch"] for g in data["glyphs"]))
    return {
        "id": name,
        "glyphs": len(data["glyphs"]),
        "characters": len(chars),
        "coverage": "".join(chars),
    }


def profiles(library):
    return [
        profile_summary(p.stem, json.loads(p.read_text(encoding="utf-8")))
        for p in (library.root / "profiles").glob("*.json")
    ]


def learn_glyph(library, note_id, page_index, label, stroke_ids, profile="learned"):
    if not label or len(label) != 1 or label.isspace():
        raise ValueError("Label one character at a time")
    page = library.page(note_id, page_index)
    selected = set(stroke_ids)
    strokes = [s for s in page["strokes"] if s["id"] in selected]
    if len(strokes) != len(selected) or not strokes:
        raise ValueError("Select existing, nonempty stroke IDs")
    x0, y0, x1, y1 = stroke_bounds(strokes)
    height = max(y1 - y0, 1)
    glyph = {
        "id": uuid.uuid4().hex,
        "ch": label,
        "src": f"{note_id}#{page_index}",
        "adv": max((x1 - x0) / height, 0.1),
        "top": -1,
        "bot": 0,
        "strokes": [
            {
                "p": [[(x - x0) / height, (y - y1) / height] for x, y in s["points"]],
                "w": s["width"] / height,
                "c": [round(c * 255) for c in s["color"]],
            }
            for s in strokes
        ],
        "provenance": {
            "stroke_ids": [s["id"] for s in strokes],
            "normalization": "bounding-box height; review baseline for Latin/punctuation",
        },
    }
    target = profile_path(library, profile)
    with FileLock(str(target) + ".lock", timeout=30):
        data = (
            json.loads(target.read_text(encoding="utf-8"))
            if target.exists()
            else {
                "stats": {"gap": 0.19, "space": 0.49, "pen": 0.07},
                "glyphs": [],
                "runs": [],
            }
        )
        if any(
            set(g.get("provenance", {}).get("stroke_ids", [])) == selected
            and g["src"] == glyph["src"]
            for g in data["glyphs"]
        ):
            raise ValueError("Selection already learned")
        data["glyphs"].append(glyph)
        validate_profile(data)
        json_write(target, data)
        return profile_summary(profile, data)


COLORS = {
    "black": "#272727",
    "검정": "#272727",
    "blue": "#3A5BA0",
    "파랑": "#3A5BA0",
    "red": "#C94141",
    "빨강": "#C94141",
    "green": "#2F7D4F",
    "초록": "#2F7D4F",
}
_DB_CACHE: dict = {}


def parse_color(value) -> tuple[int, int, int] | None:
    """'#RRGGBB', 'RRGGBB' or a colour name → (r, g, b). None keeps the default."""
    if value in (None, ""):
        return None
    text = COLORS.get(str(value).strip().lower(), str(value).strip())
    if text.startswith("#"):
        text = text[1:]
    if len(text) != 6 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise ValueError("Colour must be #RRGGBB or black/blue/red/green")
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))


def load_db(library, profile, synthesize=True):
    target = profile_path(library, profile)
    if not target.is_file():
        raise ValueError(
            f"Handwriting profile '{profile}' is not installed. Import your glyphs.json first."
        )
    stat = target.stat()
    key = (str(target), stat.st_mtime_ns, stat.st_size, synthesize)
    if key not in _DB_CACHE:
        validate_profile(json.loads(target.read_text(encoding="utf-8")))
        _DB_CACHE.clear()
        _DB_CACHE[key] = GlyphDB(str(target), synth=synthesize)
    db = _DB_CACHE[key]
    db.made = set()
    db.derived = set()
    return db


def _safe_name(title: str) -> str:
    keep = "".join(c if c.isalnum() or c in " ._-()" else "_" for c in title).strip(" .")
    return (keep or "note")[:60]


def compose_document(
    library,
    text,
    profile="my-handwriting",
    pdf_id=None,
    page_index=0,
    x=None,
    y=None,
    size=None,
    width=None,
    seed=7,
    synthesize=True,
    strict=True,
    color=None,
    line_spacing=2.1,
    target_id=None,
    make_sdocx=None,
):
    """Write ``text`` with the personal glyph profile.

    Target: nothing (new blank A4 note), an imported PDF (Ink annotations added to a
    copy) or an imported SDOCX note (native strokes added to a copy of the original).
    Coordinates are page units of the target page (PDF points or Samsung units).
    """
    target_id = target_id or pdf_id
    if not text.strip() or len(text) > 20000:
        raise ValueError("Enter 1..20000 characters")
    note = library.get(target_id) if target_id else None
    kind = note["kind"] if note else "new"
    if note and not 0 <= page_index < len(note["pages"]):
        raise ValueError(
            f"Target page out of range: the document has {len(note['pages'])} pages"
        )
    pages = (
        [{"width": p["width"], "height": p["height"], "strokes": []} for p in note["pages"]]
        if note
        else [{"width": 595.0, "height": 842.0, "strokes": []}]
    )
    index = page_index if note else 0
    pw, ph = pages[index]["width"], pages[index]["height"]
    if kind == "sdocx":
        margin = pw * 0.07
        auto = typical_char_height(note["pages"][index])
        size = size if size is not None else (auto or pw / 1440 * 30)
        x = margin if x is None else x
        y = margin if y is None else y
    else:
        margin = 50.0
        size = 22.0 if size is None else size
        x = 50.0 if x is None else x
        y = 60.0 if y is None else y
    width = max(2 * size, pw - margin - x) if width is None else width
    values = (x, y, size, width, line_spacing)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
        raise ValueError("Position, size and width must be numbers")
    if x < 0 or y < 0 or not 0.5 <= size <= 400 or width < 2 * size or not 1 <= line_spacing <= 5:
        raise ValueError(
            "Invalid writing box: x, y ≥ 0, size 0.5..400, width ≥ 2×size, line_spacing 1..5"
        )
    if x + width > pw + 1e-6:
        raise ValueError(
            f"Writing box exceeds the page width ({pw:g}); reduce x or width"
        )
    if y + size > ph:
        raise ValueError(f"y is below the page bottom ({ph:g})")
    rgb = parse_color(color) or (39, 39, 39)

    db = load_db(library, profile, synthesize)
    strokes, missing, _ = compose(
        db,
        text,
        width=max(1, width / size - 2),
        line_gap=line_spacing,
        seed=seed,
        jitter=0.35,
        color=rgb,
    )
    if missing and strict:
        raise ValueError(
            "Missing glyphs (no output written): "
            + " ".join(sorted(missing))
            + ". Rephrase, or learn these characters first."
        )
    strokes = [s for s in strokes if "missing" not in s]
    if not strokes:
        raise ValueError("Text produced no ink")
    bottom_margin = 0 if kind == "sdocx" else 30
    # Lines come from the compositor so dots, equal signs and detached
    # components stay with their line and keep source stroke order.
    lines = defaultdict(list)
    for i, s in enumerate(strokes):
        lines[s["line"]].append(i)
    groups = [
        (
            min(float(min(strokes[i]["y"])) for i in ids),
            max(float(max(strokes[i]["y"])) for i in ids),
            ids,
        )
        for ids in lines.values()
    ]
    offset = 0.0
    written = 0
    touched = []
    for top, bottom, ids in groups:
        if y + (bottom - offset) * size > pages[index]["height"] - bottom_margin:
            if kind == "sdocx":
                raise ValueError(
                    "Text does not fit below y on this page; use a smaller size, a wider box, a higher y, or split the text across pages"
                )
            index += 1
            if index >= len(pages):
                if note:
                    raise ValueError(
                        "Text exceeds available PDF pages; choose smaller letters or shorter text"
                    )
                pages.append({"width": 595.0, "height": 842.0, "strokes": []})
            offset = top
        page = pages[index]
        if y + (bottom - offset) * size > page["height"] - bottom_margin or x + width > page["width"] + 1e-6:
            raise ValueError("A line or formula does not fit the target page")
        for i in sorted(ids):
            s = strokes[i]
            pts = [
                [x + float(px) * size, y + (float(py) - offset) * size]
                for px, py in zip(s["x"], s["y"])
            ]
            pen = float(s["w"]) * size
            if any(
                px - pen / 2 < 0
                or px + pen / 2 > min(page["width"], x + width + size)
                or py - pen / 2 < 0
                or py + pen / 2 > page["height"]
                for px, py in pts
            ):
                raise ValueError(
                    "Ink would be clipped; enlarge the margins or reduce the size"
                )
            page["strokes"].append(
                {
                    "id": f"generated-{written}",
                    "points": pts,
                    "width": pen,
                    "color": [c / 255 for c in rgb],
                    "opacity": 1.0,
                    "pressure": [],
                    "time": [],
                    "origin": "resampled personal glyph",
                }
            )
            written += 1
        if index not in touched:
            touched.append(index)

    title = _safe_name(note["title"]) if note else "handwriting"
    with export_transaction(library.root) as (eid, folder):
        if kind == "sdocx":
            files, report = _write_into_sdocx(
                library, note, pages, touched, folder, title, color is not None and rgb
            )
        else:
            files, report = _write_pdf_or_new(
                library, note, pages, folder, title, make_sdocx
            )
        written_box = stroke_bounds([s for i in touched for s in pages[i]["strokes"]])
        result = {
            "export_id": eid,
            "target": kind,
            "strokes": written,
            "pages": len(pages),
            "pages_written": [i + 1 for i in touched],
            "written_box": [round(v, 1) for v in written_box],
            "size": round(size, 2),
            "missing": sorted(missing),
            "assembled_hangul": sorted(db.made),
            "derived_symbols": sorted(db.derived),
            "seed": seed,
            "files": files,
            "preview_pdf": report.pop("preview_pdf"),
        } | report
        json_write(
            folder / "ink.json",
            {
                "schema": "sdocx-bridge/1",
                "pages": [pages[i] | {"index": i} for i in touched],
                "generation": {k: v for k, v in result.items() if k != "files"},
            },
        )
        result["files"] = result["files"] + ["ink.json"]
        json_write(folder / "result.json", result)
    return result


def _write_pdf_or_new(library, note, pages, folder, title, make_sdocx):
    warnings = []
    original = library.folder(note["id"]) / "original.pdf" if note else None
    want_sdocx = (not original) if make_sdocx is None else bool(make_sdocx)
    backgrounds = []
    pdf_name = f"{title}_손글씨.pdf" if original else "handwriting.pdf"
    with fitz.open(original) if original else fitz.open() as pdf:
        if original:
            for p in pdf:
                if p.rotation:
                    p.remove_rotation()
        for i, p in enumerate(pages):
            pdfpage = pdf[i] if original else pdf.new_page(width=p["width"], height=p["height"])
            if original and want_sdocx:
                bg = folder / f"background-{i + 1:03}.png"
                scale = min(1.5, 2400 / p["width"], 3200 / p["height"])
                pdfpage.get_pixmap(matrix=fitz.Matrix(scale, scale)).save(bg)
                backgrounds.append(bg)
            if p["strokes"]:
                draw_pdf_ink(pdfpage, p["strokes"], True)
        pdf.save(folder / pdf_name, garbage=4, deflate=True)
    files = [pdf_name]
    report = {"preview_pdf": pdf_name}
    if original:
        warnings.append(
            "Original PDF content is preserved; handwriting is added as editable Ink annotations."
        )
    if want_sdocx:
        sdocx, warn = sdocx_export(pages, folder, backgrounds or None)
        warnings += warn
        for bg in backgrounds:
            bg.unlink(missing_ok=True)
        max_error = _check_sdocx(sdocx, pages)
        files.append(sdocx.name)
        report["sdocx_roundtrip_max_coordinate_error"] = max_error
        warnings.append(
            "New SDOCX: generated pressure/timing is not your original pen dynamics; check it in Samsung Notes."
        )
        if original:
            warnings.append("In the SDOCX copy the PDF pages are raster backgrounds.")
    for i, p in enumerate(pages):
        if p["strokes"]:
            name = f"page-{i + 1:03}.svg"
            (folder / name).write_text(svg(p), encoding="utf-8")
            files.append(name)
    report["warnings"] = warnings
    return files, report


def _check_sdocx(sdocx, pages):
    """Read the emitted binary back and compare every point."""
    from note_pipeline.input.samsung_notes.importer import SamsungNotesImporter
    from note_pipeline.model import StrokeElement

    with SamsungNotesImporter() as reader:
        decoded = reader.import_path(str(sdocx))
        got = [s for p in decoded.pages for s in p.elements if isinstance(s, StrokeElement)]
        wanted = [s for p in pages for s in p["strokes"]]
        if len(got) != len(wanted) or any(
            len(a.points) != len(b["points"]) for a, b in zip(got, wanted)
        ):
            raise ValueError("Generated SDOCX failed stroke-count integrity check")
        max_error = max(
            (
                abs(a - b)
                for s, t in zip(got, wanted)
                for p, q in zip(s.points, t["points"])
                for a, b in zip(p, q)
            ),
            default=0,
        )
    if max_error > 1 / 64 + 1e-3:
        raise ValueError("Generated SDOCX exceeds coordinate error budget")
    return max_error


def _write_into_sdocx(library, note, pages, touched, folder, title, rgb):
    from .sdocx_patch import add_strokes

    if len(touched) != 1:
        raise ValueError("Write into one Samsung Notes page at a time")
    index = touched[0]
    root = library.folder(note["id"])
    name = f"{title}_손글씨.sdocx"
    color = None
    if rgb:
        color = 0xFF000000 | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
    report = add_strokes(
        root / "original.sdocx",
        root / "raw",
        note["pages"][index]["id"],
        pages[index]["strokes"],
        folder / name,
        color=color,
    )
    # A preview PDF of the edited note (existing ink + new ink highlighted by order).
    preview = f"{title}_손글씨_미리보기.pdf"
    edited = dict(note)
    edited["pages"] = [
        p | {"strokes": p["strokes"] + (pages[i]["strokes"] if i == index else [])}
        for i, p in enumerate(note["pages"])
    ]
    library.render_note_pdf(note["id"], folder / preview, note=edited, annotations=False)
    report["warnings"] = [
        "Your original file is unchanged; the edited copy keeps every page, recording and attachment.",
        "Samsung Notes format is reverse engineered: open the copy in Samsung Notes to confirm before relying on it.",
    ]
    report["preview_pdf"] = preview
    return [name, preview], report

