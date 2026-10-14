import asyncio
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
        assert "list_notes" in names
        assert "write_handwriting" not in names
        assert "import_note" not in names

    asyncio.run(run())


def test_mcp_stdio_protocol(lib):
    async def run():
        args = ["-m", "sdocx_bridge", "--library", str(lib.root), "mcp"]
        async with stdio_client(
            StdioServerParameters(command=sys.executable, args=args)
        ) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=25)
            ) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert "write_handwriting" in {t.name for t in tools.tools}
                result = await session.call_tool("list_handwriting_profiles", {})
                assert not result.isError
                written = await session.call_tool(
                    "write_handwriting", {"text": "AB", "synthesize": False}
                )
                assert not written.isError, written
                generated = json.loads(
                    next(c.text for c in written.content if c.type == "text")
                )
                shutil.copyfile(
                    lib.root / "exports" / generated["export_id"] / "handwriting.sdocx",
                    lib.root / "inbox" / "test.sdocx",
                )
                imported = await session.call_tool(
                    "import_note", {"filename": "test.sdocx"}
                )
                assert not imported.isError
                note = json.loads(
                    next(c.text for c in imported.content if c.type == "text")
                )
                for name, args in [
                    ("list_notes", {}),
                    ("inspect_note", {"note_id": note["id"]}),
                    ("search_notes", {"query": "test"}),
                    ("export_note", {"note_id": note["id"], "format": "audio"}),
                ]:
                    result = await session.call_tool(name, args)
                    assert not result.isError, result
                preview = await session.call_tool(
                    "page_preview", {"note_id": note["id"]}
                )
                assert not preview.isError and any(
                    c.type == "image" for c in preview.content
                )
                result = await session.call_tool(
                    "read_strokes", {"note_id": note["id"], "limit": 1}
                )
                strokes = json.loads(
                    next(c.text for c in result.content if c.type == "text")
                )
                assert strokes["next_offset"] == 1 and len(strokes["strokes"]) == 1
                learned = await session.call_tool(
                    "learn_character",
                    {
                        "note_id": note["id"],
                        "page_index": 0,
                        "label": "Z",
                        "stroke_ids": [strokes["strokes"][0]["id"]],
                    },
                )
                assert not learned.isError
                resource = await session.read_resource(f"sdocx://note/{note['id']}")
                assert resource.contents
                prompt = await session.get_prompt(
                    "transcribe_note", {"note_id": note["id"]}
                )
                assert prompt.messages
                blocked = await session.call_tool(
                    "import_note", {"filename": "../../private.sdocx"}
                )
                assert blocked.isError

    asyncio.run(run())
