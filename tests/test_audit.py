"""Regression cases found during the second feature-by-feature audit."""

import json
import errno
from unittest.mock import patch

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from sdocx_bridge.handwriting import compose_document, validate_profile
from sdocx_bridge.ink import sdocx_export
from sdocx_bridge.web import create_app
from sdocx_bridge.handwriting import learn_glyph
from sdocx_bridge.security import safe_extract


@pytest.mark.parametrize("data", [None, [], {"glyphs": [None]}, {"glyphs": [{}]}])
def test_malformed_profiles_are_user_errors(lib, data):
    with pytest.raises(ValueError):
        validate_profile(data)
    with TestClient(create_app(lib.root, token="test")) as client:
        r = client.post(
            "/api/upload?profile_name=bad",
            headers={"x-bridge-token": "test"},
            files={"file": ("bad.json", json.dumps(data), "application/json")},
        )
        assert r.status_code == 400
    assert not (lib.root / "profiles/bad.json").exists()


def annotated_pdf(path, rotation=0):
    with fitz.open() as pdf:
        p = pdf.new_page(width=595, height=842)
        p.insert_text((50, 40), "KEEP ORIGINAL")
        a = p.add_rect_annot((100, 100, 200, 200))
        a.set_colors(stroke=(1, 0, 0))
        a.update()
        p.insert_link(
            {
                "kind": fitz.LINK_URI,
                "from": fitz.Rect(50, 30, 180, 45),
                "uri": "https://example.com",
            }
        )
        p.set_rotation(rotation)
        pdf.save(path)


def test_pdf_export_preserves_annotations_and_links(lib, tmp_path):
    source = tmp_path / "annotated.pdf"
    annotated_pdf(source)
    n = lib.import_file(source)
    r = lib.export(n["id"], "pdf")
    with fitz.open(lib.root / "exports" / r["export_id"] / "note.pdf") as pdf:
        p = pdf[0]
        assert len(list(p.annots())) == 1
        assert len(p.get_links()) == 1
    # Match preview's bounded scale, including existing annotations.
    with fitz.open(source) as pdf:
        scale = min(1200 / 595, 1600 / 842, 2)
        expected = pdf[0].get_pixmap(matrix=fitz.Matrix(scale, scale)).tobytes("png")
    assert lib.preview(n["id"], 0) == expected


def test_failed_exports_leave_no_partial_results(lib):
    r = compose_document(lib, "A", synthesize=False)
    n = lib.import_file(lib.root / "exports" / r["export_id"] / "handwriting.sdocx")
    before = set((lib.root / "exports").iterdir())
    with pytest.raises(ValueError):
        lib.export(n["id"], "invalid")
    assert set((lib.root / "exports").iterdir()) == before
    with patch(
        "sdocx_bridge.handwriting.sdocx_export",
        side_effect=ValueError("simulated failure"),
    ):
        with pytest.raises(ValueError, match="simulated failure"):
            compose_document(lib, "A", synthesize=False)
    assert set((lib.root / "exports").iterdir()) == before


def test_single_point_native_stroke_is_not_lost(lib, tmp_path):
    p = {
        "width": 595,
        "height": 842,
        "strokes": [
            {
                "id": "dot",
                "points": [[100, 100]],
                "width": 3,
                "color": [0, 0, 0],
                "pressure": [0.5],
                "time": [42],
            }
        ],
    }
    path, _ = sdocx_export([p], tmp_path / "dot")
    n = lib.import_file(path)
    assert n["strokes"] == 1
    assert lib.page(n["id"], 0)["strokes"][0]["points"] == [[100, 100]]


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_pdf_writing_preserves_content(lib, tmp_path, rotation):
    source = tmp_path / "rotated.pdf"
    annotated_pdf(source, rotation)
    n = lib.import_file(source)
    r = compose_document(lib, "A", pdf_id=n["id"], synthesize=False)
    with fitz.open(lib.root / "exports" / r["export_id"] / r["preview_pdf"]) as pdf:
        p = pdf[0]
        assert "KEEP ORIGINAL" in p.get_text()
        assert len(list(p.annots())) == 1 + r["strokes"]
        assert len(p.get_links()) == 1
        assert p.rect.width == lib.page(n["id"], 0)["width"]
        ink = [a for a in p.annots() if a.type[0] == fitz.PDF_ANNOT_INK]
        assert all(40 < a.rect.x0 < 100 for a in ink)


def test_malformed_pdf_is_client_error(lib):
    with TestClient(
        create_app(lib.root, token="test"), raise_server_exceptions=False
    ) as client:
        r = client.post(
            "/api/upload",
            headers={"x-bridge-token": "test"},
            files={"file": ("bad.pdf", b"not a pdf", "application/pdf")},
        )
        assert r.status_code == 400


@pytest.mark.parametrize(
    "field,value",
    [
        ("strokes", [None]),
        ("strokes", [{"p": [None], "w": 1, "c": [0, 0, 0]}]),
        ("strokes", [{"p": [[1, 2]], "c": [0, 0, 0]}]),
    ],
)
def test_malformed_glyph_shapes(lib, field, value):
    data = json.loads(
        (lib.root / "profiles/my-handwriting.json").read_text(encoding="utf-8")
    )
    data["glyphs"][0][field] = value
    with pytest.raises(ValueError):
        validate_profile(data)


@pytest.mark.parametrize("name", ["a//b", "a/./b", "a\x00evil", "a?.txt", "COM¹.txt"])
def test_archive_path_aliases(tmp_path, name):
    import zipfile

    source = tmp_path / "bad.zip"
    with zipfile.ZipFile(source, "w") as z:
        info = zipfile.ZipInfo(name)
        info.filename = name
        info.orig_filename = name
        z.writestr(info, b"data")
    with pytest.raises(ValueError):
        safe_extract(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_learning_rejects_legacy_reordered_selection(lib):
    r = compose_document(lib, "A", synthesize=False)
    n = lib.import_file(lib.root / "exports" / r["export_id"] / "handwriting.sdocx")
    ids = [s["id"] for s in lib.page(n["id"], 0)["strokes"]]
    learn_glyph(lib, n["id"], 0, "Z", ids)
    path = lib.root / "profiles/learned.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["glyphs"][0]["provenance"]["stroke_ids"] = list(reversed(ids))
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="already learned"):
        learn_glyph(lib, n["id"], 0, "Z", ids)


def test_detached_glyph_strokes_stay_on_same_page_and_in_order(lib):
    # Bottom stroke was written before top stroke. Both belong to one glyph.
    path = lib.root / "profiles/my-handwriting.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["glyphs"][0]["strokes"] = [
        {"p": [[0, 0], [0.7, 0]], "w": 0.06, "c": [0, 0, 0]},
        {"p": [[0, -1], [0.7, -1]], "w": 0.06, "c": [0, 0, 0]},
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    r = compose_document(lib, "A\nA", y=760, size=22, synthesize=False)
    pages = json.loads(
        (lib.root / "exports" / r["export_id"] / "ink.json").read_text(encoding="utf-8")
    )["pages"]
    assert [len(p["strokes"]) for p in pages] == [2, 2]
    assert all(
        p["strokes"][0]["points"][0][1] > p["strokes"][1]["points"][0][1] for p in pages
    )


def test_generated_pdf_preview_and_bounds(lib):
    r = compose_document(lib, "A", synthesize=False)
    with TestClient(create_app(lib.root, token="test")) as client:
        url = f"/api/exports/{r['export_id']}/preview/0"
        assert client.get(url).status_code == 403
        assert client.get(url, headers={"x-bridge-token": "test"}).content.startswith(
            b"\x89PNG"
        )
        assert (
            client.get(url[:-1] + "99", headers={"x-bridge-token": "test"}).status_code
            == 400
        )


def test_profile_import_waits_for_other_process_lock(lib):
    import subprocess
    import sys
    from filelock import FileLock

    path = lib.root / "profiles/concurrent.json"
    code = "from sdocx_bridge.handwriting import import_profile; from sdocx_bridge.library import Library; import sys; print('ready', flush=True); import_profile(Library(sys.argv[1]), sys.argv[2], 'concurrent')"
    with FileLock(str(path) + ".lock"):
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                code,
                str(lib.root),
                str(lib.root / "profiles/my-handwriting.json"),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert process.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=0.3)
            assert not path.exists()
        except BaseException:
            process.kill()
            process.communicate()
            raise
    _, err = process.communicate(timeout=30)
    assert process.returncode == 0, err
    assert json.loads(path.read_text(encoding="utf-8"))["glyphs"]


def test_invalid_learning_provenance_is_rejected(lib):
    data = json.loads(
        (lib.root / "profiles/my-handwriting.json").read_text(encoding="utf-8")
    )
    data["glyphs"][0]["provenance"] = None
    with pytest.raises(ValueError, match="provenance"):
        validate_profile(data)


@pytest.mark.parametrize("error_number", [errno.EEXIST, errno.ENOTEMPTY])
def test_import_race_uses_winners_metadata(lib, tmp_path, error_number):
    import shutil
    from pathlib import Path

    source = tmp_path / "race.pdf"
    annotated_pdf(source)
    original_rename = Path.rename

    def concurrent_publish(stage, destination):
        if stage.name.startswith("import-"):
            shutil.copytree(stage, destination)
            summary = destination / "summary.json"
            data = json.loads(summary.read_text(encoding="utf-8"))
            data["title"] = "other importer"
            summary.write_text(json.dumps(data), encoding="utf-8")
            raise OSError(error_number, "simulated competing importer")
        return original_rename(stage, destination)

    with patch.object(Path, "rename", concurrent_publish):
        result = lib.import_file(source, "this importer")
    assert result["title"] == "other importer"
