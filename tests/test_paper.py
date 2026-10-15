"""Scanned paper: line finding, pen rendering, writing on scans through the MCP tools."""
import asyncio
import json

import numpy as np
import pymupdf as fitz
import pytest
from PIL import Image

from handwriting_engine import paper, pen, scanpage
from sdocx_bridge import paper_tools
from sdocx_bridge.handwriting import load_db
from sdocx_bridge.mcp_server import create_server


@pytest.fixture(scope="module")
def lined():
    return paper.blank_page("lined", 150, 120, dpi=150, seed=3, spacing_mm=8, margin_mm=10)


def test_lines_found_on_bent_page(lined):
    an = scanpage.analyze(lined, dpi=150)
    mm = 150 / 25.4
    # 10 mm margin, then a line every 8 mm while y < height - 6 mm
    expected = int((120 - 10 - 6) // 8)
    assert abs(len(an["hlines"]) - expected) <= 1
    assert abs(an["line_spacing"] - 8 * mm) < 2
    assert not an["grid_paper"]
    lines = [a for a in an["areas"] if a["kind"] == "line"]
    assert len(lines) >= expected - 2 and all(a["filled"] < 0.05 for a in lines)
    # the detected curve follows the printed line to within a pixel
    g = np.asarray(lined.convert("L"), np.float32)
    c = an["hlines"][2]
    for x in (100, 400, 700):
        y = int(round(float(c(x))))
        assert g[y - 1:y + 2, x].min() < g[y - 6, x] - 15


def test_grid_and_plain_pages():
    grid = scanpage.analyze(paper.blank_page("grid", 120, 100, seed=1), dpi=150)
    assert grid["grid_paper"] and len(grid["areas"]) == 1
    plain = scanpage.analyze(paper.blank_page("plain", 120, 100, seed=1), dpi=150)
    assert len(plain["areas"]) == 1 and plain["areas"][0]["kind"] == "box"


def test_ballpoint_ink_is_uneven_and_stays_on_the_stroke():
    pts = np.stack([np.linspace(20, 380, 200), 50 + 10 * np.sin(np.linspace(0, 6, 200))], 1)
    img = pen.render([{"points": pts}], (400, 100), dpi=200, pen="ballpoint", seed=1)
    g = np.asarray(img.convert("L"), np.float32)
    on = g[np.round(pts[20:-20:3, 1]).astype(int), np.round(pts[20:-20:3, 0]).astype(int)]
    assert on.mean() < 150  # dark on the stroke
    assert on.std() > 6  # ballpoint density varies along the stroke
    assert g[:15].mean() > 240 and g[85:].mean() > 240  # nothing far from it


def test_pen_names():
    assert pen.get_pen("파란볼펜").name == "ballpoint-blue"
    with pytest.raises(ValueError):
        pen.get_pen("crayon")


def test_marks_and_erase(lined):
    an = scanpage.analyze(lined, dpi=150)
    for kind in ("circle", "underline", "check", "star", "box"):
        assert paper.mark_strokes(kind, [100, 100, 300, 140], px_per_mm=6, rng=np.random.default_rng(0))
    arrow = paper.mark_strokes("arrow", frm=[50, 50], to=[200, 90], px_per_mm=6, rng=np.random.default_rng(0))
    assert len(arrow) >= 2
    dirty = lined.copy()
    dirty.paste((20, 20, 20), (300, 200, 340, 230))
    clean = paper.erase(dirty, an, [290, 190, 350, 240])
    assert np.asarray(clean.convert("L"))[205:225, 305:335].mean() > 220


def _call(server, name, **kw):
    r = asyncio.run(server.call_tool(name, kw))
    items = r[0] if isinstance(r, tuple) else r
    data = next((json.loads(c.text) for c in items if getattr(c, "type", "") == "text"), None)
    images = [c for c in items if getattr(c, "type", "") == "image"]
    return data, images


def test_write_on_scan_tools(lib, tmp_path, lined, monkeypatch):
    monkeypatch.setenv("SDOCX_BRIDGE_NO_DEFAULT_DIRS", "1")
    inbox = tmp_path / "in"
    inbox.mkdir()
    lined.save(inbox / "note.jpg", quality=88)
    server = create_server(lib.root, allow_dirs=[str(inbox)])
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert {"analyze_scan", "view_scan", "write_on_scan", "make_blank_page", "pen_samples",
            "learn_paper_style"} <= names

    found, _ = _call(server, "find_files", kind="scan")
    assert [f["name"] for f in found["files"]] == ["note.jpg"]
    an, img = _call(server, "analyze_scan", source="note.jpg")
    assert img and an["areas"]
    first = an["areas"][0]["id"]
    out, img = _call(server, "write_on_scan", source="note.jpg", items=[
        {"area": first, "text": "AB가나 123 " * 12},
        {"mark": "underline", "box": [100, 300, 400, 310]},
        {"at": [100, 600], "text": "1+2", "color": "red"},
    ])
    rep = out["report"]["items"][0]
    assert rep["rows_used"] >= 2  # long text flowed onto the next empty lines
    assert out["files"][0].endswith("_손글씨.jpg") and img
    written = Image.open(out["files"][0])
    assert written.size == lined.size
    diff = np.abs(np.asarray(written.convert("L"), np.float32) - np.asarray(lined.convert("L"), np.float32))
    assert (diff > 60).sum() > 500
    # the original is untouched; the result can be written on again
    assert Image.open(inbox / "note.jpg").size == lined.size
    again, _ = _call(server, "write_on_scan", source=out["export_id"], items=[{"at": [100, 650], "text": "AB"}])
    assert again["export_id"] != out["export_id"]
    view, _ = _call(server, "view_scan", source=again["export_id"], region=[0, 0, 400, 400], grid=True)

    with pytest.raises(Exception, match="Missing glyphs"):
        _call(server, "write_on_scan", source="note.jpg", items=[{"area": first, "text": "없는글자"}])
    with pytest.raises(Exception, match="not allowed|not found"):
        _call(server, "analyze_scan", source=str(tmp_path / "elsewhere.jpg"))


def test_pdf_scan_keeps_pages(lib, tmp_path, lined):
    src = tmp_path / "scan.pdf"
    data = paper_tools._encode(lined, None)
    with fitz.open() as doc:
        for _ in range(2):
            pg = doc.new_page(width=lined.width * 72 / 150, height=lined.height * 72 / 150)
            pg.insert_image(pg.rect, stream=data)
        doc.save(src)
    db = load_db(lib, "my-handwriting")
    result, out, an = paper_tools.write_on_scan(lib, db, src, 2, [{"at": [100, 300], "text": "AB 12"}])
    assert abs(an["dpi"] - 150) < 1
    with fitz.open(lib.root / "exports" / result["export_id"] / result["scan_output"]) as doc:
        assert len(doc) == 2
        assert [len(p.get_images()) for p in doc] == [1, 1]
        pix = doc[1].get_pixmap(dpi=150)
        assert (pix.width, pix.height) == lined.size
    with fitz.open(src) as doc:
        assert len(doc[1].get_images()) == 1


def test_style_overrides_are_checked(lib):
    st = paper_tools.style_with(paper_tools.load_style(lib), {"x_scale": 0.9})
    assert st.x_scale == 0.9
    with pytest.raises(ValueError, match="Unknown style"):
        paper_tools.style_with(st, {"bogus": 1})
