"""Page geometry helpers: handwriting lines, blank space and typical letter size.

These give an AI client concrete coordinates to place new handwriting next to the
writer's own lines instead of guessing from a picture.
"""

from __future__ import annotations

import statistics


def _box(stroke):
    xs = [p[0] for p in stroke["points"]]
    ys = [p[1] for p in stroke["points"]]
    return [min(xs), min(ys), max(xs), max(ys)]


def _clusters(boxes, pad):
    """Union boxes that touch (with padding) into connected components."""
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    active = []
    for i in order:
        x0, y0, x1, y1 = boxes[i]
        active = [j for j in active if boxes[j][2] + pad >= x0]
        for j in active:
            a = boxes[j]
            if a[1] - pad <= y1 and y0 <= a[3] + pad:
                parent[find(i)] = find(j)
        active.append(i)
    groups = {}
    for i in range(len(boxes)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def typical_char_height(page) -> float | None:
    """Median height of letter-sized ink clusters on a page (page units)."""
    boxes = [_box(s) for s in page["strokes"]]
    boxes = [b for b in boxes if b[3] - b[1] < page["height"] / 10]
    if len(boxes) < 8:
        return None
    heights = []
    for group in _clusters(boxes, 6.0):
        x0 = min(boxes[i][0] for i in group)
        y0 = min(boxes[i][1] for i in group)
        x1 = max(boxes[i][2] for i in group)
        y1 = max(boxes[i][3] for i in group)
        w, h = x1 - x0, y1 - y0
        if h > 8 and 0.4 < w / h < 2.2:
            heights.append(h)
    if len(heights) < 5:
        return None
    return round(statistics.median(heights), 1)


def page_layout(page, min_gap: float | None = None) -> dict:
    """Ink lines (top to bottom) and blank horizontal bands between them."""
    w, h = page["width"], page["height"]
    char = typical_char_height(page)
    boxes = [_box(s) for s in page["strokes"]]
    boxes += [list(o["rect"]) for o in page.get("objects", []) if o.get("rect")]
    gap = min_gap if min_gap is not None else max(4.0, (char or h / 80) * 0.35)
    spans = sorted([b[1], b[3], b] for b in boxes)
    lines = []
    for y0, y1, b in spans:
        if lines and y0 <= lines[-1]["y1"] + gap:
            line = lines[-1]
            line["y1"] = max(line["y1"], y1)
            line["x0"] = min(line["x0"], b[0])
            line["x1"] = max(line["x1"], b[2])
            line["items"] += 1
        else:
            lines.append({"y0": y0, "y1": y1, "x0": b[0], "x1": b[2], "items": 1})
    out_lines = [
        {
            "box": [round(l["x0"], 1), round(l["y0"], 1), round(l["x1"], 1), round(l["y1"], 1)],
            "strokes_or_objects": l["items"],
        }
        for l in lines
    ]
    bands = []
    cursor = 0.0
    for l in lines:
        if l["y0"] - cursor > 0:
            bands.append([cursor, l["y0"]])
        cursor = max(cursor, l["y1"])
    if h - cursor > 0:
        bands.append([cursor, h])
    need = (char or h / 60) * 1.4
    blank = [
        {
            "y0": round(a, 1),
            "y1": round(b, 1),
            "height": round(b - a, 1),
            "fits_lines": int((b - a) // need) if need else 0,
        }
        for a, b in bands
        if b - a >= need
    ]
    left = min((l["x0"] for l in lines), default=w * 0.05)
    right = max((l["x1"] for l in lines), default=w * 0.95)
    return {
        "width": w,
        "height": h,
        "typical_handwriting_height": char,
        "content_left": round(left, 1),
        "content_right": round(right, 1),
        "lines": out_lines,
        "blank_bands": blank,
        "note": "Coordinates are page units, origin top-left. typical_handwriting_height is a good default size for added handwriting.",
    }
