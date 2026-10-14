"""Recording timing, in-place SDOCX writing, page layout, file access and setup helpers."""

import hashlib
import json
import struct
import zipfile
from pathlib import Path

import pytest

from sdocx_bridge import transcribe
from sdocx_bridge.cli import main as cli_main
from sdocx_bridge.files import allowed_dirs, find_files, resolve_input
from sdocx_bridge.handwriting import compose_document, profiles, sync_profile_file
from sdocx_bridge.layout import page_layout
from sdocx_bridge.timing import (
    format_clock,
    nearest_writing,
    parse_recordings,
    recording_info,
    timeline,
    writing_during,
)


def _utf16(text):
    return struct.pack("<H", len(text)) + text.encode("utf-16le")


def _note_bytes(start_ms, length_ms):
    return (
        b"\x00" * 16
        + struct.pack("<I", 1)
        + _utf16("음성 001")
        + _utf16("00:15:32")
        + struct.pack("<Q", start_ms)
        + b"\x00" * 12
        + struct.pack("<Q", length_ms)
        + b"\x00" * 16
    )


def test_recording_record_is_decoded(tmp_path):
    start = 1_760_000_000_000
    records = parse_recordings(_note_bytes(start, 932_400))
    assert records == [
        {
            "media_id": 1,
            "title": "음성 001",
            "start_ms": start,
            "length_ms": 932_400,
            "length_text": "00:15:32",
        }
    ]
    (tmp_path / "note.note").write_bytes(_note_bytes(start, 932_400))
    info = recording_info(tmp_path, f"1@{start // 1000:08x}_x.m4a")
    assert info["timing_source"] == "note.note recording record"
    assert info["title"] == "음성 001"
    # Without note.note the file name still gives the start (to the second).
    (tmp_path / "note.note").unlink()
    assert recording_info(tmp_path, f"1@{start // 1000:08x}_x.m4a")["start_ms"] == start
    assert format_clock(932.4) == "15:32" and format_clock(3725) == "1:02:05"


def test_writing_is_matched_to_recording_time():
    t0 = 1_760_000_000_000
    note = {
        "pages": [
            {"strokes": [
                {"id": "a", "created_us": (t0 + 10_000) * 1000, "points": [[10, 20], [30, 40]]},
                {"id": "b", "created_us": (t0 + 12_000) * 1000, "points": [[50, 20], [60, 45]]},
            ]},
            {"strokes": [
                {"id": "c", "created_us": (t0 + 130_000) * 1000, "points": [[5, 5], [6, 6]]},
                {"id": "d", "points": [[0, 0]]},  # no time: ignored
            ]},
        ]
    }
    from sdocx_bridge.timing import stroke_events

    events = stroke_events(note)
    assert [e["id"] for e in events] == ["a", "b", "c"]
    assert writing_during(events, t0, t0 + 60_000) == [
        {"page": 1, "strokes": 2, "box": [10, 20, 60, 45]}
    ]
    assert nearest_writing(events, t0 + 100_000)["page"] == 2
    t = timeline(note, {"id": "audio-1", "start_ms": t0, "length_ms": 180_000})
    assert [b["strokes"] for b in t["buckets"]] == [2, 0, 1]
    assert t["strokes_written_during_recording"] == 3


def _imported_note(lib, text="AB가나"):
    made = compose_document(lib, text, synthesize=False, make_sdocx=True)
    path = lib.root / "exports" / made["export_id"] / "handwriting.sdocx"
    return lib.import_file(path), path


def test_writing_into_samsung_note_keeps_original_and_other_members(lib):
    note, source = _imported_note(lib)
    original = lib.root / "notes" / note["id"]
    before = {p: p.read_bytes() for p in original.rglob("*") if p.is_file()}
    first = compose_document(lib, "12", target_id=note["id"], synthesize=False)
    assert first["target"] == "sdocx" and first["pages_written"] == [1]
    out = Path(lib.root / "exports" / first["export_id"])
    patched = next(out.glob("*_손글씨.sdocx"))
    assert next(out.glob("*_미리보기.pdf")).read_bytes().startswith(b"%PDF")
    assert {p: p.read_bytes() for p in original.rglob("*") if p.is_file()} == before
    with zipfile.ZipFile(source) as a, zipfile.ZipFile(patched) as b:
        assert a.namelist() == b.namelist()
        changed = [n for n in a.namelist() if a.read(n) != b.read(n)]
    assert changed and all(n.endswith(".page") or n == "pageIdInfo.dat" for n in changed)
    again = lib.import_file(patched)
    assert again["strokes"] == note["strokes"] + first["strokes"]
    # Writing into the patched copy works too (hash chain was rebuilt correctly).
    second = compose_document(lib, "+", target_id=again["id"], synthesize=False)
    assert second["target"] == "sdocx"


def test_page_layout_reports_lines_and_blank_space(lib):
    note, _ = _imported_note(lib, "AB\n\n\n가나")
    page = lib.get(note["id"])["pages"][0]
    layout = page_layout(page)
    assert layout["width"] > 0 and layout["lines"]
    assert all(b["fits_lines"] >= 0 for b in layout["blank_bands"])


def test_files_are_only_opened_from_allowed_folders(tmp_path, monkeypatch, lib):
    monkeypatch.setenv("SDOCX_BRIDGE_NO_DEFAULT_DIRS", "1")
    allowed = tmp_path / "allowed"
    (allowed / "sub").mkdir(parents=True)
    (allowed / "sub" / "강의.sdocx").write_bytes(b"x")
    (allowed / "notes.txt").write_text("x")
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF")
    roots = allowed_dirs(lib.root, [str(allowed)])
    assert resolve_input("강의.sdocx", roots).name == "강의.sdocx"
    assert [f["name"] for f in find_files(roots, "강의")] == ["강의.sdocx"]
    for bad in (str(secret), "../secret.pdf", str(allowed / "notes.txt"), ""):
        with pytest.raises(ValueError):
            resolve_input(bad, roots)


def test_extension_glyph_file_is_installed_and_refreshed(lib, tmp_path):
    source = tmp_path / "glyphs.json"
    data = json.loads((lib.root / "profiles" / "my-handwriting.json").read_text(encoding="utf-8"))
    data["glyphs"] = data["glyphs"][:3]
    source.write_text(json.dumps(data), encoding="utf-8")
    assert sync_profile_file(lib, "${user_config.glyph_file}") is None
    assert sync_profile_file(lib, "") is None
    import os
    import time

    future = time.time() + 5
    os.utime(source, (future, future))
    assert sync_profile_file(lib, source)["glyphs"] == 3
    assert next(p for p in profiles(lib) if p["id"] == "my-handwriting")["glyphs"] == 3
    with pytest.raises(ValueError):
        sync_profile_file(lib, tmp_path / "missing.json")


def test_transcription_reports_state_without_running_whisper(lib):
    note, _ = _imported_note(lib)
    assert transcribe.status(lib, note["id"], "audio-1") == {"state": "not started"}
    with pytest.raises(ValueError, match="No transcript"):
        transcribe.transcript(lib, note["id"], "audio-1")
    if transcribe.available():
        with pytest.raises(ValueError, match="Unknown recording"):
            transcribe.start(lib, note["id"], "audio-1")


def test_cli_ignores_unfilled_extension_placeholders(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cli_main(["--library", "${user_config.library}", "list"])
    assert json.loads(capsys.readouterr().out) == []
    assert (tmp_path / "Documents" / "Inkbridge").is_dir()


def test_extension_manifest_matches_package(tmp_path):
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    import tomllib

    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert manifest["version"] == project["version"]
    from sdocx_bridge import __version__

    assert __version__ == project["version"]
    from sdocx_bridge.mcp_server import create_server
    import asyncio

    names = {t.name for t in asyncio.run(create_server(tmp_path / "library").list_tools())}
    assert {t["name"] for t in manifest["tools"]} == names
    assert hashlib.sha256((root / "icon.png").read_bytes()).hexdigest()


def test_web_layout_and_transcript_endpoints(lib):
    from fastapi.testclient import TestClient

    from sdocx_bridge.web import create_app

    note, _ = _imported_note(lib)
    client = TestClient(create_app(lib.root, token="t"))
    h = {"x-bridge-token": "t"}
    layout = client.get(f"/api/notes/{note['id']}/pages/0/layout", headers=h)
    assert layout.status_code == 200 and layout.json()["lines"]
    missing = client.get(f"/api/notes/{note['id']}/audio/audio-1/transcript", headers=h)
    assert missing.status_code == 200 and missing.json()["state"] == "not started"
    written = client.post(
        "/api/write",
        headers=h,
        json={"text": "AB", "target_id": note["id"], "synthesize": False, "color": "blue"},
    )
    assert written.status_code == 200, written.text
    assert written.json()["target"] == "sdocx"
