from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

MAX_UPLOAD = 512 * 1024**2
MAX_EXPANDED = 2 * 1024**3
MAX_MEMBER = 512 * 1024**2


def sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value):
        raise ValueError("Invalid identifier")
    return value


def under(root: Path, relative: str) -> Path:
    root = root.resolve()
    result = (root / relative).resolve()
    if not result.is_relative_to(root):
        raise ValueError("Path escapes configured library")
    return result


def json_write(path: Path, value) -> None:
    import uuid

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        tmp.write_text(
            json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def safe_extract(source: Path, destination: Path) -> list[dict]:
    """Validate the entire directory before writing, including Windows aliases."""
    manifest, seen = [], set()
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > 20000 or sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise ValueError("Archive exceeds expansion limits")
        for item in infos:
            name = item.orig_filename
            parts = PurePosixPath(name).parts
            if (
                not parts
                or name.startswith("/")
                or "\\" in name
                or ":" in name
                or any(
                    p in (".", "..")
                    or p.endswith((" ", "."))
                    or re.match(
                        r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", p, re.I
                    )
                    for p in parts
                )
                or stat.S_ISLNK(item.external_attr >> 16)
            ):
                raise ValueError("Unsafe archive member")
            key = name.rstrip("/").casefold()
            if key in seen:
                raise ValueError("Duplicate archive member")
            seen.add(key)
            if item.flag_bits & 1:
                raise ValueError(
                    "Encrypted notes must be unlocked in Samsung Notes first"
                )
            if item.file_size > MAX_MEMBER or (
                item.file_size > 1024**2
                and item.file_size / max(item.compress_size, 1) > 500
            ):
                raise ValueError("Archive member exceeds limits")
        for item in infos:
            target = under(destination, item.filename)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with archive.open(item) as src, target.open("xb") as dst:
                size = 0
                while chunk := src.read(1024**2):
                    size += len(chunk)
                    if size > min(item.file_size, MAX_MEMBER):
                        raise ValueError("Expanded member too large")
                    dst.write(chunk)
                    digest.update(chunk)
            manifest.append(
                {"path": item.filename, "bytes": size, "sha256": digest.hexdigest()}
            )
    return manifest
