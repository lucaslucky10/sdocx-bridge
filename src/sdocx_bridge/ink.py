from __future__ import annotations

import base64
import html
import math
import uuid
from pathlib import Path


def stroke_bounds(strokes):
    points = [p for s in strokes for p in s["points"]]
    if not points:
        raise ValueError("No ink selected")
    return [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]


def check_ink(page):
    if not 1 <= page["width"] <= 50000 or not 1 <= page["height"] <= 200000:
        raise ValueError("Unsupported page dimensions")
    total = 0
    for s in page["strokes"]:
        total += len(s["points"])
        if not s["points"] or len(s["points"]) > 60000:
            raise ValueError("Invalid stroke size")
        if not math.isfinite(s["width"]) or not 0 < s["width"] <= 1000:
            raise ValueError("Invalid pen width")
        if any(
            len(p) != 2 or not all(math.isfinite(x) and abs(x) < 1e7 for x in p)
            for p in s["points"]
        ):
            raise ValueError("Invalid coordinates")
        for channel in ("pressure", "time"):
            values = s.get(channel, [])
            if values and (
                len(values) != len(s["points"])
                or not all(math.isfinite(v) for v in values)
            ):
                raise ValueError("Invalid stroke channel")
        if len(s["color"]) != 3 or any(not 0 <= c <= 1 for c in s["color"]):
            raise ValueError("Invalid color")
    if total > 5_000_000:
        raise ValueError("Page point limit exceeded")


def svg(page, background: bytes | None = None):
    check_ink(page)
    w, h = page["width"], page["height"]
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    if background:
        out.append(
            f'<image width="{w}" height="{h}" href="data:image/png;base64,{base64.b64encode(background).decode()}"/>'
        )
    for s in page["strokes"]:
        pts = s["points"]
        if len(pts) == 1:
            pts = pts + [[pts[0][0] + 0.001, pts[0][1]]]
        points = " ".join(f"{x:.4f},{y:.4f}" for x, y in pts)
        color = "#" + "".join(f"{round(c * 255):02x}" for c in s["color"])
        out.append(
            f'<polyline data-id="{html.escape(s["id"], quote=True)}" points="{points}" fill="none" stroke="{color}" stroke-width="{s["width"]:.4f}" opacity="{s.get("opacity", 1):.3f}" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    out.append("</svg>")
    return "\n".join(out)


def draw_pdf_ink(page, strokes, ink_annotations=True):
    """PDF /Ink annotations store editable point sequences, never raster handwriting."""
    for s in strokes:
        pts = s["points"]
        if len(pts) == 1:
            pts = pts + [[pts[0][0] + 0.001, pts[0][1]]]
        if ink_annotations:
            ann = page.add_ink_annot([pts])
            ann.set_colors(stroke=s["color"])
            ann.set_border(width=s["width"])
            ann.set_opacity(s.get("opacity", 1))
            ann.set_info(subject="SDOCX Bridge vector ink", content=s["id"])
            ann.update()
        else:
            shape = page.new_shape()
            shape.draw_polyline(pts)
            shape.finish(
                color=s["color"],
                width=s["width"],
                lineCap=1,
                lineJoin=1,
                stroke_opacity=s.get("opacity", 1),
            )
            shape.commit()


def sdocx_export(pages, destination: Path, backgrounds: list[Path] | None = None):
    from note_pipeline.model import (
        NoteDocument,
        NotePage,
        SourceInfo,
        StrokeElement,
        ImageElement,
        Asset,
    )
    from note_pipeline.output.samsung_notes import SamsungNotesExporter
    from note_pipeline.output.base import ExportOptions

    doc = NoteDocument(
        source=SourceInfo("", "bridge", "", "handwriting"),
        note_id=str(uuid.uuid4()),
        title="handwriting",
    )
    for i, p in enumerate(pages):
        check_ink(p)
        page = NotePage(str(uuid.uuid4()), i, round(p["width"]), round(p["height"]))
        if backgrounds and i < len(backgrounds) and backgrounds[i]:
            aid = f"background-{i}"
            doc.assets[aid] = Asset(aid, "image", str(backgrounds[i]))
            page.elements.append(
                ImageElement(aid, (0, 0, p["width"], p["height"]), aid, 0, 0, 0)
            )
        for j, s in enumerate(p["strokes"]):
            color = (
                (round(s.get("opacity", 1) * 255) << 24)
                | (round(s["color"][0] * 255) << 16)
                | (round(s["color"][1] * 255) << 8)
                | round(s["color"][2] * 255)
            )
            page.elements.append(
                StrokeElement(
                    s["id"],
                    s["points"],
                    color,
                    f"#{color:08X}",
                    (*s["color"], s.get("opacity", 1)),
                    s["width"],
                    None,
                    1,
                    j + 1,
                    j + 1,
                    pressures=s.get("pressure", []),
                    timestamps=s.get("time", []),
                )
            )
        doc.pages.append(page)
    result = SamsungNotesExporter().export(
        doc, str(destination), ExportOptions(thickness_scale=1)
    )
    return Path(result.output_paths[0]), result.warnings
