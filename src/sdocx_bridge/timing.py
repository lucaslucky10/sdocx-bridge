"""Voice-recording metadata and handwriting/recording time alignment.

Samsung Notes stores each voice recording as ``media/<id>@<hex seconds>_<suffix>.m4a``
and describes it in ``note.note`` (title, ``HH:MM:SS`` length, start time and exact
length in milliseconds). Every stroke object carries its creation time in epoch
microseconds. Together these let us say which handwriting was written while a given
moment of the lecture was being recorded.

The layout was decoded from real notes; it is reverse engineered and each value is
validated (duration text vs. millisecond length, file name start vs. note start).
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

_DURATION = re.compile(r"^(\d{1,3}):([0-5]\d):([0-5]\d)$")
_MEDIA_NAME = re.compile(r"^(\d+)@([0-9a-fA-F]{8})(?:_|\.)")


def _utf16_at(data: bytes, offset: int):
    if offset + 2 > len(data):
        return None, offset
    n = struct.unpack_from("<H", data, offset)[0]
    end = offset + 2 + 2 * n
    if n == 0 or n > 200 or end > len(data):
        return None, offset
    try:
        return data[offset + 2 : end].decode("utf-16le"), end
    except UnicodeDecodeError:
        return None, offset


def parse_recordings(note_bytes: bytes) -> list[dict]:
    """Find recording records in note.note. Returns media id, title, start, length."""
    found = []
    for offset in range(0, len(note_bytes) - 18):
        if note_bytes[offset : offset + 2] != b"\x08\x00":
            continue
        text, end = _utf16_at(note_bytes, offset)
        if not text or not (m := _DURATION.match(text)):
            continue
        seconds = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        if end + 8 > len(note_bytes):
            continue
        start_ms = struct.unpack_from("<Q", note_bytes, end)[0]
        if not 946684800_000 <= start_ms <= 4102444800_000:
            continue
        length_ms = None
        for probe in range(end + 8, min(len(note_bytes) - 8, end + 80)):
            value = struct.unpack_from("<Q", note_bytes, probe)[0]
            if abs(value - seconds * 1000) < 1000:
                length_ms = value
                break
        title, media_id = None, None
        # Walk back over "<u32 media id><u16 len><utf16 title>".
        for title_len in range(1, 60):
            t_off = offset - 2 - 2 * title_len
            if t_off < 4:
                break
            if struct.unpack_from("<H", note_bytes, t_off)[0] == title_len:
                candidate, t_end = _utf16_at(note_bytes, t_off)
                if candidate and t_end == offset and candidate.isprintable():
                    title = candidate
                    media_id = struct.unpack_from("<I", note_bytes, t_off - 4)[0]
                    break
        found.append(
            {
                "media_id": media_id,
                "title": title,
                "start_ms": start_ms,
                "length_ms": length_ms or seconds * 1000,
                "length_text": text,
            }
        )
    return found


def recording_info(note_root: Path, audio_name: str) -> dict:
    """Start time and length for one extracted recording, best evidence first."""
    m = _MEDIA_NAME.match(audio_name)
    media_id = int(m[1]) if m else None
    name_start_ms = int(m[2], 16) * 1000 if m else None
    note = note_root / "note.note"
    records = parse_recordings(note.read_bytes()) if note.is_file() else []
    match = next((r for r in records if r["media_id"] == media_id), None)
    if match is None and name_start_ms is not None:
        match = next(
            (r for r in records if abs(r["start_ms"] - name_start_ms) < 2000), None
        )
    if match:
        return {
            "title": match["title"],
            "start_ms": match["start_ms"],
            "length_ms": match["length_ms"],
            "timing_source": "note.note recording record",
        }
    if name_start_ms is not None:
        return {
            "title": None,
            "start_ms": name_start_ms,
            "length_ms": None,
            "timing_source": "file name (second precision)",
        }
    return {"title": None, "start_ms": None, "length_ms": None, "timing_source": None}


def format_clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m:02}:{s:02}"


def stroke_events(note: dict) -> list[dict]:
    """All strokes with a creation time, sorted by time."""
    events = []
    for page_index, page in enumerate(note["pages"]):
        for s in page["strokes"]:
            t = s.get("created_us")
            if isinstance(t, int):
                xs = [p[0] for p in s["points"]]
                ys = [p[1] for p in s["points"]]
                events.append(
                    {
                        "t_ms": t / 1000,
                        "page": page_index,
                        "id": s["id"],
                        "box": [min(xs), min(ys), max(xs), max(ys)],
                    }
                )
    events.sort(key=lambda e: e["t_ms"])
    return events


def _merge_boxes(boxes):
    return [
        round(min(b[0] for b in boxes), 1),
        round(min(b[1] for b in boxes), 1),
        round(max(b[2] for b in boxes), 1),
        round(max(b[3] for b in boxes), 1),
    ]


def writing_during(events, start_ms: float, end_ms: float) -> list[dict]:
    """Group strokes written in [start_ms, end_ms) by page with a bounding box."""
    by_page: dict[int, list] = {}
    for e in events:
        if start_ms <= e["t_ms"] < end_ms:
            by_page.setdefault(e["page"], []).append(e)
    return [
        {
            "page": page + 1,
            "strokes": len(items),
            "box": _merge_boxes([e["box"] for e in items]),
        }
        for page, items in sorted(by_page.items())
    ]


def nearest_writing(events, at_ms: float, window_ms: float = 120_000) -> dict | None:
    """The handwriting written closest in time to a moment (for placing additions)."""
    best = None
    for e in events:
        d = abs(e["t_ms"] - at_ms)
        if d <= window_ms and (best is None or d < best[0]):
            best = (d, e)
    if not best:
        return None
    d, e = best
    return {
        "page": e["page"] + 1,
        "box": [round(v, 1) for v in e["box"]],
        "seconds_away": round(d / 1000, 1),
    }


def timeline(note: dict, audio: dict, bucket_seconds: int = 60) -> dict:
    """Minute-by-minute map of what was written while this recording ran."""
    start = audio.get("start_ms")
    length = audio.get("length_ms")
    if start is None:
        raise ValueError("This recording has no decoded start time")
    events = stroke_events(note)
    if not events:
        raise ValueError("This note has no stroke creation times")
    if length is None:
        length = max(0, events[-1]["t_ms"] - start)
    buckets = []
    for b0 in range(0, int(length) + 1, bucket_seconds * 1000):
        b1 = min(b0 + bucket_seconds * 1000, length)
        if b1 <= b0:
            break
        written = writing_during(events, start + b0, start + b1)
        buckets.append(
            {
                "from": format_clock(b0 / 1000),
                "to": format_clock(b1 / 1000),
                "strokes": sum(w["strokes"] for w in written),
                "writing": written,
            }
        )
    inside = sum(1 for e in events if start <= e["t_ms"] < start + length)
    return {
        "recording": audio["id"],
        "length": format_clock(length / 1000),
        "strokes_written_during_recording": inside,
        "strokes_with_time": len(events),
        "buckets": buckets,
    }
