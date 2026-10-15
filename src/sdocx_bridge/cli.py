from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


def default_library() -> str:
    env = os.environ.get("SDOCX_BRIDGE_LIBRARY")
    if env and not env.startswith("${"):
        return env
    return str(Path.home() / "Documents" / "Inkbridge")


def claude_desktop_config_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"
    return Path.home() / ".config/Claude/claude_desktop_config.json"


def server_entry(library: str, allow: list[str] | None = None, read_only=False) -> dict:
    args = ["-m", "sdocx_bridge", "--library", str(Path(library).resolve()), "mcp"]
    for a in allow or []:
        args += ["--allow-dir", str(Path(a).resolve())]
    if read_only:
        args.append("--read-only")
    return {"command": sys.executable, "args": args, "env": {"PYTHONUTF8": "1"}}


def install_claude(library: str, allow: list[str] | None, read_only: bool) -> dict:
    """Add (or update) the sdocx-bridge entry in Claude Desktop's config, keeping a backup."""
    path = claude_desktop_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {}
    backup = None
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        config = json.loads(text) if text else {}
        if not isinstance(config, dict):
            raise SystemExit("Claude config is not a JSON object; fix it manually first")
        backup = path.with_name(path.name + f".backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copyfile(path, backup)
    config.setdefault("mcpServers", {})["sdocx-bridge"] = server_entry(library, allow, read_only)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return {"config": str(path), "backup": str(backup) if backup else None, "next": "Quit Claude completely and open it again."}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sdocx-bridge",
        description="Samsung Notes (SDOCX) + PDF handwriting workspace with a Claude MCP server",
    )
    parser.add_argument("--library", default=default_library(), help="library folder (default: Documents/Inkbridge)")
    commands = parser.add_subparsers(dest="command", required=True)
    web = commands.add_parser("web", help="open the local browser app")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-browser", action="store_true")
    mcp = commands.add_parser("mcp", help="run the MCP server on stdio (used by Claude)")
    mcp.add_argument("--read-only", action="store_true")
    mcp.add_argument("--allow-dir", action="append", default=[], help="extra folder Claude may open files from")
    mcp.add_argument("dirs", nargs="*", help="more folders Claude may open files from (same as --allow-dir)")
    imp = commands.add_parser("import", help="import .sdocx/.pdf files")
    imp.add_argument("files", nargs="+")
    prof = commands.add_parser("profile", help="install a glyphs.json handwriting dictionary")
    prof.add_argument("file")
    prof.add_argument("--name", default="my-handwriting")
    commands.add_parser("list")
    exp = commands.add_parser("export")
    exp.add_argument("note_id")
    exp.add_argument("format", choices=["json", "svg", "pdf", "markdown", "audio"])
    write = commands.add_parser("write", help="write text in your handwriting")
    write.add_argument("text")
    write.add_argument("--profile", default="my-handwriting")
    write.add_argument("--target", "--pdf-id", dest="target", help="note_id of an imported PDF or SDOCX")
    write.add_argument("--page", type=int, default=1, help="1-based page")
    write.add_argument("--x", type=float)
    write.add_argument("--y", type=float)
    write.add_argument("--size", type=float)
    write.add_argument("--width", type=float)
    write.add_argument("--color")
    write.add_argument("--seed", type=int, default=7)
    tr = commands.add_parser("transcribe", help="transcribe a note's recording locally")
    tr.add_argument("note_id")
    tr.add_argument("--recording", default="audio-1")
    tr.add_argument("--model", default="turbo")
    tr.add_argument("--language", default="ko")
    cfg = commands.add_parser("claude-config", help="print the Claude Desktop MCP entry")
    cfg.add_argument("--allow-dir", action="append", default=[])
    cfg.add_argument("--read-only", action="store_true")
    ins = commands.add_parser("install-claude", help="add this server to Claude Desktop's config")
    ins.add_argument("--allow-dir", action="append", default=[])
    ins.add_argument("--read-only", action="store_true")
    a = parser.parse_args(argv)
    if not a.library or a.library.startswith("${"):  # unset option in the Claude extension
        a.library = str(Path.home() / "Documents" / "Inkbridge")
    if a.command == "mcp":
        from .mcp_server import create_server

        dirs = [d for d in [*a.allow_dir, *a.dirs] if d and not d.startswith("${")]
        create_server(a.library, a.read_only, dirs).run(transport="stdio")
        return
    if a.command == "web":
        import threading
        import webbrowser

        import uvicorn

        from .web import create_app

        url = f"http://127.0.0.1:{a.port}"
        print(f"Inkbridge: {url}  (library: {Path(a.library).resolve()})")
        if not a.no_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        uvicorn.run(create_app(a.library), host="127.0.0.1", port=a.port, access_log=False)
        return
    if a.command == "claude-config":
        print(json.dumps({"mcpServers": {"sdocx-bridge": server_entry(a.library, a.allow_dir, a.read_only)}}, indent=2))
        return
    if a.command == "install-claude":
        from .library import Library

        Library(a.library)
        print(json.dumps(install_claude(a.library, a.allow_dir, a.read_only), indent=2, ensure_ascii=False))
        return
    from .handwriting import compose_document, import_profile
    from .library import Library

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
        result = compose_document(
            lib,
            a.text,
            a.profile,
            target_id=a.target,
            page_index=a.page - 1,
            x=a.x,
            y=a.y,
            size=a.size,
            width=a.width,
            color=a.color,
            seed=a.seed,
        )
    elif a.command == "transcribe":
        from . import transcribe

        transcribe.start(lib, a.note_id, a.recording, a.model, a.language)
        while (s := transcribe.status(lib, a.note_id, a.recording))["state"] not in ("done", "failed", "interrupted"):
            print(json.dumps(s, ensure_ascii=False), file=sys.stderr, flush=True)
            time.sleep(10)
        result = s if s["state"] != "done" else transcribe.plain_text(lib, a.note_id, a.recording)
        if isinstance(result, str):
            print(result)
            return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
