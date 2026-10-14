from __future__ import annotations

import json
import secrets
import tempfile
import threading
import zipfile
from pathlib import Path

from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, Response, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .library import Library
from .security import MAX_UPLOAD, identifier, under
from .handwriting import import_profile, profiles, learn_glyph, compose_document


class WriteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    profile: str = "my-handwriting"
    pdf_id: str | None = None
    page_index: int = Field(default=0, ge=0)
    x: float = 50
    y: float = 60
    size: float = 22
    width: float = 495
    seed: int = 7
    synthesize: bool = True


class LabelRequest(BaseModel):
    note_id: str
    page_index: int = Field(ge=0)
    label: str = Field(min_length=1, max_length=1)
    stroke_ids: list[str] = Field(min_length=1, max_length=2000)
    profile: str = "learned"


def create_app(root, token=None):
    app = FastAPI(
        title="SDOCX Bridge",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    library = Library(root)
    session = token or secrets.token_urlsafe(32)
    app.state.token = session
    gate = threading.RLock()
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def protect(request, call_next):
        if request.url.path.startswith("/api/"):
            if not secrets.compare_digest(
                request.headers.get("x-bridge-token", ""), session
            ):
                return JSONResponse(
                    {"detail": "Open the local app to start a session"}, status_code=403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(zipfile.BadZipFile)
    async def bad_archive(request, exc):
        return JSONResponse(
            {"detail": "Invalid or damaged SDOCX ZIP archive"}, status_code=400
        )

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):
        return JSONResponse(
            {"detail": "Requested file or profile does not exist"}, status_code=404
        )

    def serial(function, *args, **kwargs):
        with gate:
            return function(*args, **kwargs)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (
            (static / "index.html")
            .read_text(encoding="utf-8")
            .replace("__TOKEN__", session)
        )

    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/api/notes")
    def notes():
        return library.list()

    @app.get("/api/profiles")
    def get_profiles():
        return profiles(library)

    @app.post("/api/upload")
    async def upload(file: UploadFile, profile_name: str = "my-handwriting"):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in (".sdocx", ".pdf", ".json"):
            raise ValueError("Choose SDOCX, PDF, or a labelled glyph JSON dictionary")
        with tempfile.TemporaryDirectory(dir=library.root) as tmp:
            p = Path(tmp) / ("upload" + suffix)
            size = 0
            with p.open("wb") as out:
                while chunk := await file.read(1024**2):
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise ValueError("Upload exceeds 512 MiB")
                    out.write(chunk)
            if suffix == ".json":
                return await run_in_threadpool(
                    serial, import_profile, library, p, profile_name
                )
            return await run_in_threadpool(
                serial, library.import_file, p, Path(file.filename).stem
            )

    @app.get("/api/notes/{note_id}")
    def inspect(note_id: str):
        n = library.get(note_id)
        return library.summarize(n) | {
            "page_details": [
                {
                    "index": i,
                    "width": p["width"],
                    "height": p["height"],
                    "strokes": len(p["strokes"]),
                    "text": p["text"],
                }
                for i, p in enumerate(n["pages"])
            ]
        }

    @app.get("/api/notes/{note_id}/pages/{index}")
    def page(note_id: str, index: int):
        return library.page(note_id, index)

    @app.get("/api/notes/{note_id}/pages/{index}/preview")
    def preview(note_id: str, index: int):
        return Response(library.preview(note_id, index), media_type="image/png")

    @app.get("/api/notes/{note_id}/audio/{audio_id}")
    def audio(note_id: str, audio_id: str):
        entry = next(
            (a for a in library.get(note_id)["audio"] if a["id"] == audio_id), None
        )
        if not entry:
            raise HTTPException(404)
        return FileResponse(
            under(library.folder(note_id), entry["path"]), filename=entry["name"]
        )

    @app.post("/api/notes/{note_id}/export/{format}")
    def export(note_id: str, format: str):
        return serial(library.export, note_id, format)

    @app.get("/api/exports/{export_id}/{filename}")
    def download(export_id: str, filename: str):
        folder = library.root / "exports" / identifier(export_id)
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        if filename not in result["files"]:
            raise HTTPException(404)
        return FileResponse(under(folder, filename), filename=filename)

    @app.post("/api/write")
    def write(data: WriteRequest):
        return serial(compose_document, library, **data.model_dump())

    @app.post("/api/learn")
    def learn(data: LabelRequest):
        return serial(learn_glyph, library, **data.model_dump())

    @app.get("/api/search")
    def search(q: str):
        return library.search(q)

    return app
