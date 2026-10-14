from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Local SDOCX workspace and real-ink MCP bridge"
    )
    parser.add_argument(
        "--library", default=os.environ.get("SDOCX_BRIDGE_LIBRARY", "./library")
    )
    commands = parser.add_subparsers(dest="command", required=True)
    web = commands.add_parser("web")
    web.add_argument("--port", type=int, default=8765)
    mcp = commands.add_parser("mcp")
    mcp.add_argument("--read-only", action="store_true")
    imp = commands.add_parser("import")
    imp.add_argument("files", nargs="+")
    prof = commands.add_parser("profile")
    prof.add_argument("file")
    prof.add_argument("--name", default="my-handwriting")
    commands.add_parser("list")
    exp = commands.add_parser("export")
    exp.add_argument("note_id")
    exp.add_argument("format", choices=["json", "svg", "pdf", "markdown", "audio"])
    write = commands.add_parser("write")
    write.add_argument("text")
    write.add_argument("--profile", default="my-handwriting")
    write.add_argument("--pdf-id")
    write.add_argument("--seed", type=int, default=7)
    commands.add_parser("claude-config")
    a = parser.parse_args()
    if a.command == "mcp":
        from .mcp_server import create_server

        create_server(a.library, a.read_only).run(transport="stdio")
        return
    if a.command == "web":
        import uvicorn
        from .web import create_app

        print(f"Open http://127.0.0.1:{a.port}")
        uvicorn.run(
            create_app(a.library), host="127.0.0.1", port=a.port, access_log=False
        )
        return
    if a.command == "claude-config":
        print(
            json.dumps(
                {
                    "mcpServers": {
                        "sdocx-bridge": {
                            "command": sys.executable,
                            "args": [
                                "-m",
                                "sdocx_bridge",
                                "--library",
                                str(Path(a.library).resolve()),
                                "mcp",
                            ],
                        }
                    }
                },
                indent=2,
            )
        )
        return
    from .library import Library
    from .handwriting import import_profile, compose_document

    lib = Library(a.library)
    if a.command == "import":
        result = [lib.import_file(p) for p in a.files]
    elif a.command == "profile":
        result = import_profile(lib, a.file, a.name)
    elif a.command == "list":
        result = lib.list()
    elif a.command == "export":
        result = lib.export(a.note_id, a.format)
    elif a.command == "write":
        result = compose_document(lib, a.text, a.profile, a.pdf_id, seed=a.seed)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
