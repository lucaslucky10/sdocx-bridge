"""Free, local speech-to-text for recordings inside notes (faster-whisper).

Nothing is uploaded: the Whisper model is downloaded once from Hugging Face into
``<library>/models`` and runs on this computer's CPU. Long lectures take a while,
so transcription runs in a background thread with a progress file; callers poll.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from pathlib import Path

from .security import identifier, json_write, under
from .timing import format_clock, nearest_writing, stroke_events, writing_during

MODELS = {
    "turbo": "large-v3-turbo",
    "large-v3-turbo": "large-v3-turbo",
    "small": "small",
    "medium": "medium",
    "base": "base",
    "large-v3": "large-v3",
}
DEFAULT_MODEL = "turbo"
_JOBS: dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def _paths(library, note_id, audio_id):
    folder = library.root / "transcripts" / identifier(note_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{identifier(audio_id)}.json", folder / f"{audio_id}.status.json"


def _audio_entry(library, note_id, audio_id):
    note = library.get(note_id)
    entry = next((a for a in note["audio"] if a["id"] == audio_id), None)
    if entry is None:
        names = ", ".join(a["id"] for a in note["audio"]) or "none"
        raise ValueError(f"Unknown recording '{audio_id}'. Recordings in this note: {names}")
    return note, entry


def status(library, note_id, audio_id) -> dict:
    result, state = _paths(library, note_id, audio_id)
    if result.is_file():
        data = json.loads(result.read_text(encoding="utf-8"))
        return {
            "state": "done",
            "model": data["model"],
            "segments": len(data["segments"]),
            "length": format_clock(data["duration"]),
        }
    if state.is_file():
        data = json.loads(state.read_text(encoding="utf-8"))
        running = _JOBS.get(f"{note_id}/{audio_id}")
        if data["state"] in ("starting", "downloading model", "transcribing") and not (
            running and running.is_alive()
        ):
            data["state"] = "interrupted"
            data["hint"] = "The program restarted during transcription; start it again."
        return data
    return {"state": "not started"}


def start(library, note_id, audio_id, model=DEFAULT_MODEL, language="ko") -> dict:
    """Start (or report) transcription. Returns immediately."""
    if not available():
        raise ValueError(
            "Speech-to-text is not installed. Install the 'audio' extra: pip install \"sdocx-bridge[audio]\""
        )
    if model not in MODELS:
        raise ValueError("model must be one of: " + ", ".join(sorted(MODELS)))
    if language and (len(language) > 5 or not language.isalpha()):
        raise ValueError("language must be a code such as ko or en, or empty for auto")
    _audio_entry(library, note_id, audio_id)
    key = f"{note_id}/{audio_id}"
    with _LOCK:
        current = status(library, note_id, audio_id)
        if current["state"] == "done":
            return current
        if key in _JOBS and _JOBS[key].is_alive():
            return current
        _, state = _paths(library, note_id, audio_id)
        json_write(state, {"state": "starting", "model": model, "progress": 0.0})
        t = threading.Thread(
            target=_run, args=(library, note_id, audio_id, model, language), daemon=True
        )
        _JOBS[key] = t
        t.start()
    return {"state": "started", "model": model}


def _run(library, note_id, audio_id, model, language):
    result, state = _paths(library, note_id, audio_id)
    try:
        note, entry = _audio_entry(library, note_id, audio_id)
        audio = under(library.folder(note_id), entry["path"])
        json_write(state, {"state": "downloading model", "model": model, "progress": 0.0})
        from faster_whisper import WhisperModel

        threads = max(1, (os.cpu_count() or 2) - 1)
        whisper = WhisperModel(
            MODELS[model],
            device="cpu",
            compute_type="int8",
            cpu_threads=threads,
            download_root=str(library.root / "models"),
        )
        segments, info = whisper.transcribe(
            str(audio),
            language=language or None,
            vad_filter=True,
            beam_size=5,
            condition_on_previous_text=False,
        )
        out = []
        began = time.time()
        for s in segments:
            out.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
            done = s.end / max(info.duration, 1)
            elapsed = time.time() - began
            json_write(
                state,
                {
                    "state": "transcribing",
                    "model": model,
                    "progress": round(min(done, 1.0), 3),
                    "position": format_clock(s.end),
                    "length": format_clock(info.duration),
                    "estimated_seconds_left": round(elapsed / max(done, 1e-3) * (1 - done)),
                },
            )
        json_write(
            result,
            {
                "note_id": note_id,
                "audio_id": audio_id,
                "model": model,
                "language": info.language,
                "duration": info.duration,
                "segments": out,
            },
        )
        state.unlink(missing_ok=True)
    except Exception as exc:  # reported through status()
        json_write(
            state,
            {
                "state": "failed",
                "model": model,
                "error": f"{type(exc).__name__}: {exc}",
                "detail": traceback.format_exc(limit=3)[-1500:],
            },
        )


def transcript(library, note_id, audio_id, offset=0, limit=80, align=True) -> dict:
    """Transcript segments with recording clock and the handwriting written meanwhile."""
    result, _ = _paths(library, note_id, audio_id)
    if not result.is_file():
        raise ValueError(
            "No transcript yet. Call transcribe_recording first and wait until it is done."
        )
    if offset < 0 or not 1 <= limit <= 2000:
        raise ValueError("offset >= 0 and limit 1..2000")
    data = json.loads(result.read_text(encoding="utf-8"))
    note, entry = _audio_entry(library, note_id, audio_id)
    start = entry.get("start_ms")
    events = stroke_events(note) if align and start is not None else []
    rows = []
    for s in data["segments"][offset : offset + limit]:
        row = {
            "at": format_clock(s["start"]),
            "until": format_clock(s["end"]),
            "text": s["text"],
        }
        if events:
            t0, t1 = start + s["start"] * 1000, start + s["end"] * 1000
            during = writing_during(events, t0 - 5000, t1 + 5000)
            row["written_meanwhile"] = during
            if not during:
                row["nearest_writing"] = nearest_writing(events, (t0 + t1) / 2)
        rows.append(row)
    total = len(data["segments"])
    return {
        "recording": audio_id,
        "title": entry.get("title"),
        "model": data["model"],
        "length": format_clock(data["duration"]),
        "segments_total": total,
        "offset": offset,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        "alignment": (
            "written_meanwhile lists handwriting created within ±5 s of each sentence (page, stroke count, box in page units). "
            "Sentences with no writing nearby are candidates for notes you missed."
            if events
            else "No stroke/recording time alignment is available for this note."
        ),
        "segments": rows,
    }


def plain_text(library, note_id, audio_id) -> str:
    result, _ = _paths(library, note_id, audio_id)
    data = json.loads(result.read_text(encoding="utf-8"))
    return "\n".join(f"[{format_clock(s['start'])}] {s['text']}" for s in data["segments"])
