import asyncio
import sys
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
                blocked = await session.call_tool(
                    "import_note", {"filename": "../../private.sdocx"}
                )
                assert blocked.isError

    asyncio.run(run())
