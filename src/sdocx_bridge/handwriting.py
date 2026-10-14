from __future__ import annotations

import json
import math
import uuid
from pathlib import Path

import pymupdf as fitz
from handwriting_engine.compose import GlyphDB, compose

from .ink import check_ink, draw_pdf_ink, sdocx_export, stroke_bounds, svg
from .security import identifier, json_write


def profile_path(library, name):
    return library.root / "profiles" / (identifier(name) + ".json")


def validate_profile(data):
    glyphs = data.get("glyphs")
    if not isinstance(glyphs, list) or not 1 <= len(glyphs) <= 100000:
        raise ValueError("Profile needs 1..100000 labelled glyphs")
    count = 0
    for g in glyphs:
        if not isinstance(g.get("ch"), str) or not 1 <= len(g["ch"]) <= 32:
            raise ValueError("Invalid glyph label")
        for k in ("adv", "top", "bot"):
            if (
                not isinstance(g.get(k), (int, float))
                or not math.isfinite(g[k])
                or abs(g[k]) > 100
            ):
                raise ValueError("Invalid glyph metric")
        if not isinstance(g.get("src"), str) or not g.get("strokes"):
            raise ValueError("Glyph source and strokes required")
        for s in g["strokes"]:
            count += len(s["p"])
            if (
                not isinstance(s["w"], (int, float))
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
    for key in ("gap", "space", "pen"):
        v = data.get("stats", {}).get(key, 0.1)
        if not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 < v < 10:
            raise ValueError("Invalid profile statistics")
    for series in data.get("stats", {}).get("gaps", {}).values():
        if len(series) != 21 or any(
            not isinstance(v, (int, float)) or not math.isfinite(v) or abs(v) > 10
            for v in series
        ):
            raise ValueError("Invalid spacing quantiles")
    return data


def import_profile(library, source, name="my-handwriting"):
    source = Path(source)
    if source.stat().st_size > 256 * 1024**2:
        raise ValueError("Profile exceeds 256 MiB")
    data = validate_profile(json.loads(source.read_text(encoding="utf-8")))
    target = profile_path(library, name)
    if target.exists():
        raise ValueError("Profile already exists; choose a new name")
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
            "stroke_ids": list(selected),
            "normalization": "bounding-box height; review baseline for Latin/punctuation",
        },
    }
    target = profile_path(library, profile)
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
        g.get("provenance", {}).get("stroke_ids") == glyph["provenance"]["stroke_ids"]
        and g["src"] == glyph["src"]
        for g in data["glyphs"]
    ):
        raise ValueError("Selection already learned")
    data["glyphs"].append(glyph)
    validate_profile(data)
    json_write(target, data)
    return profile_summary(profile, data)


def compose_document(
    library,
    text,
    profile="my-handwriting",
    pdf_id=None,
    page_index=0,
    x=50.0,
    y=60.0,
    size=22.0,
    width=495.0,
    seed=7,
    synthesize=True,
    strict=True,
):
    if not text.strip() or len(text) > 20000:
        raise ValueError("Enter 1..20000 characters")
    if (
        not all(math.isfinite(v) for v in (x, y, size, width))
        or x < 0
        or y < 0
        or not 3 <= size <= 200
        or width < 2 * size
    ):
        raise ValueError("Invalid writing rectangle or size")
    target = profile_path(library, profile)
    validate_profile(json.loads(target.read_text(encoding="utf-8")))
    db = GlyphDB(str(target), synth=synthesize)
    strokes, missing, _ = compose(
        db, text, width=max(1, width / size - 2), seed=seed, jitter=0.35
    )
    if missing and strict:
        raise ValueError(
            "Missing glyphs (no output written): " + " ".join(sorted(missing))
        )
    strokes = [s for s in strokes if "missing" not in s]
    if not strokes:
        raise ValueError("Text produced no ink")
    note = library.get(pdf_id) if pdf_id else None
    if note and note["kind"] != "pdf":
        raise ValueError("Select an uploaded PDF as the writing background")
    if note and not 0 <= page_index < len(note["pages"]):
        raise ValueError("Target page out of range")
    pages = (
        [
            {"width": p["width"], "height": p["height"], "strokes": []}
            for p in note["pages"]
        ]
        if note
        else [{"width": 595.0, "height": 842.0, "strokes": []}]
    )
    index = page_index if note else 0
    if x + width > pages[index]["width"] or y + size > pages[index]["height"] - 30:
        raise ValueError("Writing rectangle exceeds page")
    # Group overlapping vertical ranges: a glyph or fraction is never cut across pages.
    intervals = sorted(
        (float(min(s["y"])), float(max(s["y"])), i) for i, s in enumerate(strokes)
    )
    groups = []
    for top, bottom, i in intervals:
        if not groups or top > groups[-1][1] + 0.10:
            groups.append([top, bottom, [i]])
        else:
            groups[-1][1] = max(bottom, groups[-1][1])
            groups[-1][2].append(i)
    offset = 0.0
    written = 0
    for top, bottom, ids in groups:
        if y + (bottom - offset) * size > pages[index]["height"] - 30:
            index += 1
            if index >= len(pages):
                if note:
                    raise ValueError(
                        "Text exceeds available PDF pages; choose smaller letters or shorter text"
                    )
                pages.append({"width": 595.0, "height": 842.0, "strokes": []})
            offset = top
        if (
            y + (bottom - offset) * size > pages[index]["height"] - 30
            or x + width > pages[index]["width"]
        ):
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
                or px + pen / 2 > pages[index]["width"]
                or py - pen / 2 < 0
                or py + pen / 2 > pages[index]["height"]
                for px, py in pts
            ):
                raise ValueError(
                    "Ink would be clipped; enlarge the margins or reduce the size"
                )
            pages[index]["strokes"].append(
                {
                    "id": f"generated-{written}",
                    "points": pts,
                    "width": pen,
                    "color": [c / 255 for c in s["c"]],
                    "opacity": 1.0,
                    "pressure": [],
                    "time": [],
                    "origin": "resampled personal glyph",
                }
            )
            written += 1
    eid = uuid.uuid4().hex[:16]
    folder = library.root / "exports" / eid
    folder.mkdir()
    warnings = []
    original = library.folder(pdf_id) / "original.pdf" if note else None
    backgrounds = []
    with fitz.open(original) if original else fitz.open() as pdf:
        if original:
            for p in pdf:
                if p.rotation:
                    raise ValueError(
                        "Rotate PDF pages to zero rotation before handwriting placement"
                    )
        for i, p in enumerate(pages):
            pdfpage = (
                pdf[i]
                if original
                else pdf.new_page(width=p["width"], height=p["height"])
            )
            if original:
                bg = folder / f"background-{i + 1:03}.png"
                pdfpage.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(bg)
                backgrounds.append(bg)
            draw_pdf_ink(pdfpage, p["strokes"], True)
            (folder / f"page-{i + 1:03}.svg").write_text(svg(p), encoding="utf-8")
        pdf.save(folder / "handwriting.pdf", garbage=4, deflate=True)
    sdocx, warn = sdocx_export(pages, folder, backgrounds or None)
    warnings += warn
    # Verify every generated point through the actual binary reader, not merely
    # ZIP validity. Keep generated handwriting as native strokes.
    from note_pipeline.input.samsung_notes.importer import SamsungNotesImporter
    from note_pipeline.model import StrokeElement

    with SamsungNotesImporter() as reader:
        decoded = reader.import_path(str(sdocx))
        got = [
            s for p in decoded.pages for s in p.elements if isinstance(s, StrokeElement)
        ]
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
    if original:
        warnings.append(
            "Original PDF content is preserved in PDF output. For SDOCX only, PDF backgrounds are raster images; handwriting remains native pen strokes."
        )
    warnings.append(
        "Generated pressure/timing is not the original S Pen dynamics. Samsung Notes app reimport must be checked on your device."
    )
    result = {
        "export_id": eid,
        "strokes": written,
        "pages": len(pages),
        "missing": sorted(missing),
        "assembled_hangul": sorted(db.made),
        "derived_symbols": sorted(db.derived),
        "seed": seed,
        "sdocx_roundtrip_max_coordinate_error": max_error,
        "warnings": warnings,
        "files": ["handwriting.pdf", sdocx.name, "ink.json"]
        + [f"page-{i + 1:03}.svg" for i in range(len(pages))],
    }
    json_write(
        folder / "ink.json",
        {
            "schema": "sdocx-bridge/1",
            "pages": pages,
            "generation": {k: v for k, v in result.items() if k != "files"},
        },
    )
    json_write(folder / "result.json", result)
    return result
