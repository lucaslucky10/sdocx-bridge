"""Which folders on this computer the AI connection may read notes from.

Claude cannot hand a chat attachment to a local MCP server, so the server reads
files the user names from a small set of folders: the library inbox, the usual
Downloads/Documents/Desktop folders, and any folders the user adds with
``--allow-dir`` or ``SDOCX_BRIDGE_ALLOWED_DIRS``. Only .sdocx, .pdf and glyph
dictionary .json files are opened, and originals are never modified.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

READABLE = {".sdocx", ".pdf", ".json"}
SKIP_DIRS = {"node_modules", ".git", "AppData", "Library", "$RECYCLE.BIN", ".venv", "library"}


def default_dirs() -> list[Path]:
    home = Path.home()
    names = ["Downloads", "Documents", "Desktop", "다운로드", "문서", "바탕 화면"]
    candidates = [home / n for n in names]
    for onedrive in home.glob("OneDrive*"):
        candidates += [onedrive / n for n in names]
    return [p for p in candidates if p.is_dir()]


def allowed_dirs(library_root: Path, extra: list[str] | None = None) -> list[Path]:
    dirs = [library_root / "inbox"]
    env = os.environ.get("SDOCX_BRIDGE_ALLOWED_DIRS", "")
    for item in [*(extra or []), *[e for e in env.split(os.pathsep) if e.strip()]]:
        p = Path(item).expanduser()
        if p.is_dir():
            dirs.append(p)
    if os.environ.get("SDOCX_BRIDGE_NO_DEFAULT_DIRS") != "1":
        dirs += default_dirs()
    seen, out = set(), []
    for d in dirs:
        r = d.resolve()
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def resolve_input(path: str, roots: list[Path]) -> Path:
    """Resolve a user-named file inside the allowed folders (or by bare file name)."""
    if not path or len(path) > 1000 or "\x00" in path:
        raise ValueError("Give a file path or file name")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        matches = [r / path for r in roots if (r / path).is_file()]
        if not matches:
            found = find_files(roots, Path(path).name, limit=5)
            exact = [f for f in found if Path(f["path"]).name == Path(path).name]
            if len(exact) == 1:
                matches = [Path(exact[0]["path"])]
            elif len(exact) > 1:
                raise ValueError(
                    "Several files have that name; pass the full path: "
                    + "; ".join(f["path"] for f in exact)
                )
        if not matches:
            raise ValueError(
                "File not found in the allowed folders. Use find_files, or put it in "
                + str(roots[0])
            )
        candidate = matches[0]
    resolved = candidate.resolve()
    if not any(resolved.is_relative_to(r) for r in roots):
        raise ValueError(
            "That folder is not allowed. Allowed: "
            + "; ".join(str(r) for r in roots)
            + ". Add more with --allow-dir."
        )
    if resolved.suffix.lower() not in READABLE or not resolved.is_file():
        raise ValueError("Only existing .sdocx, .pdf or glyph .json files can be opened")
    return resolved


def find_files(roots: list[Path], query: str = "", kinds=(".sdocx", ".pdf"), limit=30, max_depth=4):
    """Newest matching files in the allowed folders (name contains query)."""
    q = query.casefold().strip()
    results = []
    deadline = time.monotonic() + 8
    for root in roots:
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if time.monotonic() > deadline:
                break
            depth = len(Path(dirpath).parts) - base_depth
            dirnames[:] = [
                d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
            ] if depth < max_depth else []
            for name in filenames:
                if Path(name).suffix.lower() in kinds and q in name.casefold():
                    p = Path(dirpath) / name
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    results.append((st.st_mtime, p, st.st_size))
    results.sort(reverse=True)
    out, seen = [], set()
    for mtime, p, size in results:
        if p in seen:
            continue
        seen.add(p)
        out.append(
            {
                "path": str(p),
                "name": p.name,
                "megabytes": round(size / 1024**2, 1),
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)),
            }
        )
        if len(out) >= limit:
            break
    return out
