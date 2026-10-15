import json
import zipfile
import hashlib

import pymupdf as fitz
import pytest

from sdocx_bridge.handwriting import compose_document, learn_glyph, validate_profile
from sdocx_bridge.ink import sdocx_export
from sdocx_bridge.security import sha256


def test_real_ink_roundtrip_and_pdf(lib):
    result = compose_document(lib, "AB 가나\n123", synthesize=False)
    root = lib.root / "exports" / result["export_id"]
    data = json.loads((root / "ink.json").read_text(encoding="utf-8"))
    original = [s for p in data["pages"] for s in p["strokes"]]
    with fitz.open(root / "handwriting.pdf") as pdf:
        annotation_types = [a.type[0] for page in pdf for a in page.annots()]
        assert len(annotation_types) == result["strokes"]
        assert all(t == fitz.PDF_ANNOT_INK for t in annotation_types)
        assert all(not p.get_images() for p in pdf)
    sdocx = next(root.glob("*.sdocx"))
    imported = lib.import_file(sdocx)
    read = [s for p in lib.get(imported["id"])["pages"] for s in p["strokes"]]
    assert len(read) == len(original)
    for a, b in zip(original, read):
        assert len(a["points"]) == len(b["points"])
        assert (
            max(
                abs(v - w)
                for p, q in zip(a["points"], b["points"])
                for v, w in zip(p, q)
            )
            < 0.1
        )


def test_deterministic_and_missing(lib):
    a = compose_document(lib, "AB가", seed=17, synthesize=False)
    b = compose_document(lib, "AB가", seed=17, synthesize=False)

    def pages(r):
        return json.loads(
            (lib.root / "exports" / r["export_id"] / "ink.json").read_text()
        )["pages"]

    assert pages(a) == pages(b)
    before = set((lib.root / "exports").iterdir())
    with pytest.raises(ValueError, match="Missing glyphs"):
        compose_document(lib, "🐬", synthesize=False)
    assert set((lib.root / "exports").iterdir()) == before


def test_pdf_preserves_original(lib, tmp_path):
    source = tmp_path / "paper.pdf"
    with fitz.open() as pdf:
        p = pdf.new_page()
        p.insert_text((50, 40), "ORIGINAL BACKGROUND")
        pdf.new_page()
        pdf.save(source)
    digest = sha256(source)
    note = lib.import_file(source)
    result = compose_document(
        lib, "AB", pdf_id=note["id"], page_index=1, synthesize=False, make_sdocx=True
    )
    root = lib.root / "exports" / result["export_id"]
    with fitz.open(root / result["preview_pdf"]) as pdf:
        assert len(pdf) == 2
        assert "ORIGINAL BACKGROUND" in pdf[0].get_text()
        assert not list(pdf[0].annots() or [])
        assert len(list(pdf[1].annots())) == result["strokes"]
    assert sha256(source) == digest
    generated = lib.import_file(next(root.glob("*.sdocx")))
    assert generated["strokes"] == result["strokes"]
    assert any(p["objects"] for p in lib.get(generated["id"])["pages"])


def test_overflow_and_pages(lib):
    result = compose_document(lib, "\n".join(["AB"] * 35), synthesize=False)
    assert result["pages"] >= 2
    with pytest.raises(ValueError):
        compose_document(lib, "AB", x=600)


def test_audio_bytes_and_empty_pages(lib, tmp_path):
    p = {"width": 595, "height": 842, "strokes": []}
    source, _ = sdocx_export([p], tmp_path / "made")
    audio = b"\x00\x00\x00\x20ftypM4A " + b"audio-fixture" * 100
    with zipfile.ZipFile(source, "a") as z:
        z.writestr("media/1@recording.m4a", audio)
    n = lib.import_file(source)
    assert n["pages"] >= 1 and n["strokes"] == 0
    a = n["audio"][0]
    assert (lib.folder(n["id"]) / a["path"]).read_bytes() == audio
    assert a["sha256"] == hashlib.sha256(audio).hexdigest()
    assert lib.import_file(source)["id"] == n["id"]


def test_learn_original_strokes(lib):
    generated = compose_document(lib, "A", synthesize=False)
    note = lib.import_file(
        next((lib.root / "exports" / generated["export_id"]).glob("*.sdocx"))
    )
    p = lib.page(note["id"], 0)
    ids = [s["id"] for s in p["strokes"]]
    r = learn_glyph(lib, note["id"], 0, "Z", ids)
    assert r["characters"] == 1
    result = compose_document(lib, "ZZ", profile="learned", synthesize=False)
    assert result["strokes"] == 4
    with pytest.raises(ValueError):
        learn_glyph(lib, note["id"], 0, "Z", ids)


def test_export_formats(lib):
    r = compose_document(lib, "A", synthesize=False)
    n = lib.import_file(next((lib.root / "exports" / r["export_id"]).glob("*.sdocx")))
    for fmt in ["json", "svg", "pdf", "markdown", "audio"]:
        export = lib.export(n["id"], fmt)
        assert (lib.root / "exports" / export["export_id"] / "result.json").exists()


def test_profile_rejects_nan(lib):
    p = json.loads(
        (lib.root / "profiles/my-handwriting.json").read_text(encoding="utf-8")
    )
    p["glyphs"][0]["strokes"][0]["p"][0][0] = float("nan")
    with pytest.raises(ValueError):
        validate_profile(p)
