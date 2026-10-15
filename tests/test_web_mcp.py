import asyncio
import os
import sys
import json
import shutil
from datetime import timedelta
from fastapi.testclient import TestClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sdocx_bridge.web import create_app
from sdocx_bridge.mcp_server import create_server


def test_web_session_and_write(lib):
    client = TestClient(create_app(lib.root, token="test-token"))
    assert client.get("/").status_code == 200
    assert client.get("/api/notes").status_code == 403
    headers = {"x-bridge-token": "test-token"}
    assert client.get("/api/profiles", headers=headers).json()[0]["characters"] > 0
    r = client.post(
        "/api/write", headers=headers, json={"text": "AB", "synthesize": False}
    )
    assert r.status_code == 200, r.text
    result = r.json()
    path = f"/api/exports/{result['export_id']}/handwriting.pdf"
    assert client.get(path, headers=headers).content.startswith(b"%PDF")
    assert (
        client.get("/api/notes", headers=headers | {"host": "evil.example"}).status_code
        == 400
    )


def test_readonly_tools(lib):
    async def run():
        server = create_server(lib.root, read_only=True)
        names = {t.name for t in await server.list_tools()}
        assert {"list_notes", "view_page", "get_transcript", "find_files"} <= names
        assert "write_handwriting" not in names
        assert "open_file" not in names

    asyncio.run(run())


def _json(result):
    return json.loads(next(c.text for c in result.content if c.type == "text"))


def test_mcp_stdio_protocol(lib, tmp_path):
    outside = tmp_path / "private.sdocx"
    outside.write_bytes(b"x")

    async def run():
        args = ["-m", "sdocx_bridge", "--library", str(lib.root), "mcp"]
        env = dict(os.environ, SDOCX_BRIDGE_NO_DEFAULT_DIRS="1", SDOCX_BRIDGE_ALLOWED_DIRS="")
        async with stdio_client(
            StdioServerParameters(command=sys.executable, args=args, env=env)
        ) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=60)
            ) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                assert {"write_handwriting", "open_file", "transcribe_recording"} <= tools
                result = await session.call_tool("list_handwriting_profiles", {})
                assert not result.isError
                written = await session.call_tool("write_handwriting", {"text": "AB"})
                assert not written.isError, written
                assert any(c.type == "image" for c in written.content)
                generated = _json(written)
                sdocx = next(f for f in generated["files"] if f.endswith(".sdocx"))
                shutil.copyfile(sdocx, lib.root / "inbox" / "test.sdocx")
                found = _json(await session.call_tool("find_files", {"query": "test"}))
                assert [f["name"] for f in found["files"]] == ["test.sdocx"]
                opened = await session.call_tool("open_file", {"path": "test.sdocx"})
                assert not opened.isError, opened
                note = _json(opened)
                nid = note["note_id"]
                for name, args in [
                    ("list_notes", {}),
                    ("get_note", {"note_id": nid}),
                    ("page_layout", {"note_id": nid, "page": 1}),
                    ("search_notes", {"query": "test"}),
                    ("export_note", {"note_id": nid, "format": "audio"}),
                ]:
                    result = await session.call_tool(name, args)
                    assert not result.isError, (name, result)
                for args in ({"note_id": nid}, {"note_id": nid, "grid": True, "region": [0, 0, 300, 200]}):
                    preview = await session.call_tool("view_page", args)
                    assert not preview.isError and any(c.type == "image" for c in preview.content)
                bad_page = await session.call_tool("view_page", {"note_id": nid, "page": 0})
                assert bad_page.isError
                strokes = _json(
                    await session.call_tool("read_strokes", {"note_id": nid, "limit": 1})
                )
                assert strokes["next_offset"] == 1 and len(strokes["strokes"]) == 1
                learned = await session.call_tool(
                    "learn_character",
                    {"note_id": nid, "page": 1, "label": "Z", "stroke_ids": [strokes["strokes"][0]["id"]]},
                )
                assert not learned.isError
                into = await session.call_tool(
                    "write_handwriting",
                    {"text": "BA", "target_id": nid, "page": 1, "x": 60, "y": 300, "size": 20},
                )
                assert not into.isError, into
                into_json = _json(into)
                assert into_json["target"] == "sdocx"
                assert any(f.endswith("_손글씨.sdocx") for f in into_json["files"])
                shown = await session.call_tool("view_output", {"export_id": into_json["export_id"]})
                assert not shown.isError
                resource = await session.read_resource(f"sdocx://note/{nid}")
                assert resource.contents
                for prompt in ("read_handwriting", "fill_missing_notes"):
                    assert (await session.get_prompt(prompt, {"note_id": nid})).messages
                missing = await session.call_tool("get_transcript", {"note_id": nid})
                assert missing.isError
                for path in ("../../private.sdocx", str(outside)):
                    blocked = await session.call_tool("open_file", {"path": path})
                    assert blocked.isError

    asyncio.run(run())
