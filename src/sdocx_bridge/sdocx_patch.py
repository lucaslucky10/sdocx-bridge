"""Add native pen strokes to an existing Samsung Notes file without rebuilding it.

Rebuilding a note from a parsed model loses everything the model does not
understand (recordings, SPI tiles, text formatting, pen presets...). Instead this
module edits the original package in place, in a copy:

* only the target ``<page>.page`` and ``pageIdInfo.dat`` change; every other ZIP
  member is copied byte for byte (compressed data included);
* new stroke objects are cloned from one of the writer's own strokes on the same
  note, so they use the same pen, preset and object layout as the real ink;
* the integrity chain Samsung Notes stores is recomputed and checked:
  object hash = sha256(uuid + modified time), layer hash = sha256(object hashes +
  layer identity), page hash = sha256(layer hash + page identity), and the page
  hash recorded in ``pageIdInfo.dat``. The original chain is verified first; an
  unknown layout is refused rather than guessed.

The format is reverse engineered. Results are re-read with the parser before they
are returned, but Samsung Notes itself has not validated them.
"""

from __future__ import annotations

import collections
import hashlib
import struct
import time
import uuid
import zipfile
import zlib
from pathlib import Path

from note_pipeline.input.samsung_notes.page_parser import SpenNotesPageParser
from note_pipeline.output.samsung_notes import (
    _pack_compact_delta,
    _pack_compact_float_series,
    _pack_compact_timestamp_series,
)


def _identity(identifier: str, stamp: int) -> bytes:
    return hashlib.sha256(f"{identifier}{int(stamp)}".encode("utf-8")).digest()


def _new_uuid() -> str:
    node = int.from_bytes(uuid.uuid4().bytes[:6], "big") | 0x010000000000
    return str(uuid.uuid1(node=node))


class PatchError(ValueError):
    pass


# ---------------------------------------------------------------- page editing


class _Page:
    def __init__(self, path: Path):
        self.path = path
        self.parser = SpenNotesPageParser(str(path), str(path.parent))
        self.data = bytearray(self.parser.data)
        meta = self.parser.extract_page_metadata()
        self.page_id = str(meta.get("page_uuid") or "")
        self.width = int(meta.get("page_width") or 0)
        self.height = int(meta.get("page_height") or 0)
        self.page_time_at = 0x28 + 2 * int(meta.get("page_uuid_length") or 0)
        self.page_time = int(meta.get("modified_time_raw") or 0)
        if not self.page_id or not self.page_time:
            raise PatchError("Page header is not in a known layout")
        if bytes(self.data[-26:]) != b"Page for SAMSUNG S-Pen SDK":
            raise PatchError("Page footer is not in a known layout")
        self.layers = self.parser._parse_layers()
        if not self.layers:
            raise PatchError("Page has no readable layers")

    def layer_info(self, layer):
        s, hs = layer["start"], layer["header_size"]
        extra = bytes(self.data[s + 16 : s + hs])
        n = struct.unpack_from("<H", extra, 0)[0]
        layer_id = extra[2 : 2 + 2 * n].decode("utf-16le")
        time_at = s + 16 + 2 + 2 * n
        layer_time = struct.unpack_from("<Q", self.data, time_at)[0]

        def last_end(o):
            return max([o["record_end"]] + [last_end(c) for c in o["children"]])

        objects_end = max(
            [last_end(o) for o in layer["objects"]], default=s + hs + 4
        )
        return layer_id, time_at, layer_time, objects_end

    def object_hashes(self, layer, flat):
        objs = (
            self.parser._iter_flat_objects(layer["objects"])
            if flat
            else layer["objects"]
        )
        return b"".join(bytes(self.data[o["payload_end"] : o["record_end"]]) for o in objs)

    def verify(self):
        """Check the stored integrity chain; returns which object list the hash uses."""
        modes = []
        for layer in self.layers:
            layer_id, _, layer_time, end = self.layer_info(layer)
            stored = bytes(self.data[end : end + 32])
            for flat in (False, True):
                if (
                    hashlib.sha256(
                        self.object_hashes(layer, flat) + _identity(layer_id, layer_time)
                    ).digest()
                    == stored
                ):
                    modes.append(flat)
                    break
            else:
                raise PatchError("Layer integrity hash uses an unknown layout")
        last = self.layers[-1]
        _, _, _, end = self.layer_info(last)
        layer_hash = bytes(self.data[end : end + 32])
        page_hash = bytes(self.data[-58:-26])
        if len(self.layers) == 1 and (
            hashlib.sha256(layer_hash + _identity(self.page_id, self.page_time)).digest()
            != page_hash
        ):
            raise PatchError("Page integrity hash uses an unknown layout")
        return modes

    def page_hash(self):
        return bytes(self.data[-58:-26])


# ------------------------------------------------------------- stroke objects


def _templates(page_paths: list[Path]):
    """Real stroke objects of this note, most common style first."""
    found = collections.Counter()
    samples = {}
    for path in page_paths:
        parser = SpenNotesPageParser(str(path), str(path.parent))
        for layer in parser._parse_layers():
            for obj in parser._iter_flat_objects(layer["objects"]):
                if obj["object_type"] != 1 or obj["child_count"]:
                    continue
                base = next((r for r in obj["subrecords"] if r["type"] == 0), None)
                stroke = next((r for r in obj["subrecords"] if r["type"] == 1), None)
                if base is None or stroke is None or len(obj["subrecords"]) != 2:
                    continue
                t = _template_from(parser.data, obj, base, stroke)
                if t is None:
                    continue
                key = (t["mask1"], t["mask2"], t["color"], t["flexible"])
                found[key] += 1
                samples.setdefault(key, t)
    return [samples[k] for k, _ in found.most_common()]


def _template_from(data, obj, base, stroke):
    data = bytes(data)
    b0, b1 = base["start"], base["end"]
    raw_base = data[b0:b1]
    # uuid (u16 36 + ascii), modified u64, rect 4×f64, then creation time.
    at = raw_base.find(b"\x24\x00", 6, 40)
    if at < 0:
        return None
    uid = raw_base[at + 2 : at + 38]
    if not all(c in b"0123456789abcdefABCDEF-" for c in uid):
        return None
    modified_at = at + 38
    rect_at = modified_at + 8
    if rect_at + 32 > len(raw_base):
        return None
    modified = struct.unpack_from("<Q", raw_base, modified_at)[0]
    created_at = None
    for off in range(rect_at + 32, min(len(raw_base) - 7, rect_at + 40)):
        v = struct.unpack_from("<Q", raw_base, off)[0]
        if modified - 20 * 365 * 86400_000000 <= v <= modified + 1_000000:
            created_at = off
            break
    if created_at is None:
        return None
    s0, s1 = stroke["start"], stroke["end"]
    raw = data[s0:s1]
    flexible_offset = struct.unpack_from("<I", raw, 6)[0]
    l1 = raw[10]
    mask1 = int.from_bytes(raw[11 : 11 + l1], "little")
    l2 = raw[11 + l1]
    mask2 = int.from_bytes(raw[12 + l1 : 12 + l1 + l2], "little")
    if not (mask1 & 0x1 and mask1 & 0x4):
        return None  # only compact geometry with tilt/orientation series
    if not 0 < flexible_offset <= len(raw):
        return None
    flexible = raw[flexible_offset:]
    if len(flexible) < 8 or not mask2 & 0x2:
        return None  # colour must be stored directly so it can be replaced
    color = struct.unpack_from("<I", flexible, 4)[0]
    return {
        "base": raw_base,
        "uuid_at": at + 2,
        "modified_at": modified_at,
        "rect_at": rect_at,
        "created_at": created_at,
        "mask1_bytes": raw[11 : 11 + l1],
        "mask2_bytes": raw[12 + l1 : 12 + l1 + l2],
        "mask1": mask1,
        "mask2": mask2,
        "flexible": flexible,
        "color": color,
    }


def _geometry(points, pressures, times):
    out = bytearray(struct.pack("<dd", *points[0]))
    cx, cy = points[0]
    for x, y in points[1:]:
        dx = _pack_compact_delta(x - cx)
        dy = _pack_compact_delta(y - cy)
        if dx is None or dy is None:
            raise PatchError("A stroke segment is too long for compact geometry")
        out += dx + dy
        rx, ry = struct.unpack("<H", dx)[0], struct.unpack("<H", dy)[0]
        cx += (-1 if rx & 0x8000 else 1) * (rx & 0x7FFF) / 32.0
        cy += (-1 if ry & 0x8000 else 1) * (ry & 0x7FFF) / 32.0
    zeros = [0.0] * len(points)
    for part in (
        _pack_compact_float_series(pressures, 128.0),
        _pack_compact_timestamp_series(times),
        _pack_compact_float_series(zeros, 32.0),
        _pack_compact_float_series(zeros, 32.0),
    ):
        if part is None:
            raise PatchError("Stroke dynamics could not be encoded")
        out += part
    out += struct.pack("<H", 3)
    return bytes(out)


def _pressure_profile(n, level):
    """Natural pressure envelope: quick rise, steady middle, light lift-off."""
    if n == 1:
        return [level]
    ramp = max(1, min(4, n // 4))
    out = []
    for i in range(n):
        f = min(1.0, (i + 1) / (ramp + 1), (n - i) / (ramp + 1))
        out.append(round(level * (0.55 + 0.45 * f), 4))
    return out


def _stroke_object(template, points, color, now_us, created_us, pressure):
    object_id = _new_uuid()
    base = bytearray(template["base"])
    base[template["uuid_at"] : template["uuid_at"] + 36] = object_id.encode("ascii")
    struct.pack_into("<Q", base, template["modified_at"], now_us)
    pad = 2.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    struct.pack_into(
        "<4d",
        base,
        template["rect_at"],
        min(xs) - pad,
        min(ys) - pad,
        max(xs) + pad,
        max(ys) + pad,
    )
    struct.pack_into("<Q", base, template["created_at"], created_us)
    times = [3 * i for i in range(len(points))]
    geometry = _geometry(points, _pressure_profile(len(points), pressure), times)
    flexible = bytearray(template["flexible"])
    struct.pack_into("<I", flexible, 4, color)
    head = (
        bytes([len(template["mask1_bytes"])])
        + template["mask1_bytes"]
        + bytes([len(template["mask2_bytes"])])
        + template["mask2_bytes"]
        + struct.pack("<H", len(points))
    )
    flexible_offset = 6 + 4 + len(head) + len(geometry)
    body = struct.pack("<I", flexible_offset) + head + geometry + bytes(flexible)
    stroke = struct.pack("<IH", len(body) + 6, 1) + body
    payload = bytes(base) + stroke
    digest = _identity(object_id, now_us)
    obj = bytes([1]) + struct.pack("<HI", 0, len(payload) + 32) + payload + digest
    return obj, digest


# ------------------------------------------------------------------- ZIP copy


def _raw_member(handle, info):
    handle.seek(info.header_offset)
    header = handle.read(30)
    if header[:4] != b"PK\x03\x04":
        raise PatchError("Unreadable ZIP member")
    name_len, extra_len = struct.unpack_from("<HH", header, 26)
    handle.seek(info.header_offset + 30 + name_len + extra_len)
    return handle.read(info.compress_size)


def _dos(date_time):
    y, mo, d, h, mi, s = date_time
    return ((y - 1980) << 9) | (mo << 5) | d, (h << 11) | (mi << 5) | (s // 2)


def _write_zip(source: Path, destination: Path, replacements: dict[str, bytes]):
    with zipfile.ZipFile(source) as archive, source.open("rb") as handle:
        infos = archive.infolist()
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 66000))
        tail = handle.read()
        eocd = tail.rfind(b"PK\x05\x06")
        trailing = tail[eocd + 22 :] if eocd >= 0 else b""
        central = bytearray()
        with destination.open("xb") as out:
            for info in infos:
                name = info.filename.encode("utf-8")
                if info.filename in replacements:
                    data = replacements[info.filename]
                    crc = zlib.crc32(data) & 0xFFFFFFFF
                    if info.compress_type == zipfile.ZIP_DEFLATED:
                        c = zlib.compressobj(6, zlib.DEFLATED, -15)
                        packed = c.compress(data) + c.flush()
                    elif info.compress_type == zipfile.ZIP_STORED:
                        packed = data
                    else:
                        raise PatchError("Unsupported ZIP compression")
                    usize = len(data)
                else:
                    packed = _raw_member(handle, info)
                    crc, usize = info.CRC, info.file_size
                date, clock = _dos(info.date_time)
                flags = info.flag_bits & ~0x8  # sizes are written up front
                offset = out.tell()
                out.write(
                    struct.pack(
                        "<IHHHHHIIIHH",
                        0x04034B50,
                        info.extract_version,
                        flags,
                        info.compress_type,
                        clock,
                        date,
                        crc,
                        len(packed),
                        usize,
                        len(name),
                        0,
                    )
                    + name
                )
                out.write(packed)
                central += (
                    struct.pack(
                        "<IHHHHHHIIIHHHHHII",
                        0x02014B50,
                        (info.create_system << 8) | info.create_version,
                        info.extract_version,
                        flags,
                        info.compress_type,
                        clock,
                        date,
                        crc,
                        len(packed),
                        usize,
                        len(name),
                        0,
                        0,
                        0,
                        info.internal_attr,
                        info.external_attr,
                        offset,
                    )
                    + name
                )
            start = out.tell()
            out.write(central)
            out.write(
                struct.pack(
                    "<IHHHHIIH",
                    0x06054B50,
                    0,
                    0,
                    len(infos),
                    len(infos),
                    len(central),
                    start,
                    0,
                )
            )
            out.write(trailing)


# ------------------------------------------------------------------ public API


def add_strokes(
    original: Path,
    raw_root: Path,
    page_id: str,
    strokes: list[dict],
    destination: Path,
    color: int | None = None,
) -> dict:
    """Write a copy of ``original`` with ``strokes`` appended to page ``page_id``.

    ``strokes``: [{"points": [[x, y], ...]}] in the page's own coordinates.
    ``color``: ARGB int; defaults to the writer's most used ink colour.
    """
    page_file = raw_root / f"{page_id}.page"
    if not page_file.is_file():
        raise PatchError("Page file not found in the original note")
    page = _Page(page_file)
    modes = page.verify()
    if len(page.layers) != 1:
        raise PatchError("Notes with several layers are not supported for in-place writing yet")
    templates = _templates(sorted(raw_root.glob("*.page")))
    if not templates:
        raise PatchError("No compatible pen stroke found in this note to copy the pen from")
    template = templates[0]
    if color is not None:
        same = [t for t in templates if t["color"] == color]
        template = same[0] if same else template
    ink = color if color is not None else template["color"]
    pressure = _typical_pressure(page.parser)

    layer = page.layers[-1]
    layer_id, layer_time_at, _, objects_end = page.layer_info(layer)
    now = int(time.time() * 1_000_000)
    new_objects, new_hashes = [], []
    for i, s in enumerate(strokes):
        pts = [(float(x), float(y)) for x, y in s["points"]]
        if len(pts) == 1:
            pts.append((pts[0][0] + 0.05, pts[0][1]))
        obj, digest = _stroke_object(template, pts, ink, now, now + i, pressure)
        new_objects.append(obj)
        new_hashes.append(digest)
    blob = b"".join(new_objects)

    data = page.data
    old_hashes = page.object_hashes(layer, modes[-1])
    count_at = layer["start"] + layer["header_size"]
    struct.pack_into("<I", data, count_at, struct.unpack_from("<I", data, count_at)[0] + len(new_objects))
    data[objects_end:objects_end] = blob
    struct.pack_into("<Q", data, layer_time_at, now)
    layer_hash = hashlib.sha256(old_hashes + b"".join(new_hashes) + _identity(layer_id, now)).digest()
    hash_at = objects_end + len(blob)
    data[hash_at : hash_at + 32] = layer_hash
    struct.pack_into("<Q", data, page.page_time_at, now)
    meta = page.parser.extract_page_metadata()
    if int(meta.get("page_property_mask") or 0) & 0x1 and meta.get("drawn_rect"):
        # Keep the page's "drawn area" covering the new ink (used for thumbnails).
        xs = [p[0] for s in strokes for p in s["points"]]
        ys = [p[1] for s in strokes for p in s["points"]]
        x0, y0, x1, y1 = meta["drawn_rect"]
        struct.pack_into(
            "<4d",
            data,
            int(meta["property_offset"]),
            min(x0, min(xs)),
            min(y0, min(ys)),
            max(x1, max(xs)),
            max(y1, max(ys)),
        )
    page_hash = hashlib.sha256(layer_hash + _identity(page.page_id, now)).digest()
    old_page_hash = page.page_hash()
    data[-58:-26] = page_hash

    info_bytes = bytearray((raw_root / "pageIdInfo.dat").read_bytes())
    at = info_bytes.find(old_page_hash)
    if at < 0 or info_bytes.count(old_page_hash) != 1:
        raise PatchError("pageIdInfo.dat does not list this page hash")
    info_bytes[at : at + 32] = page_hash

    destination.parent.mkdir(parents=True, exist_ok=True)
    _write_zip(
        original,
        destination,
        {f"{page_id}.page": bytes(data), "pageIdInfo.dat": bytes(info_bytes)},
    )
    return verify_added(original, destination, page_id, strokes) | {
        "pen": "copied from your own strokes in this note",
        "color": f"#{ink & 0xFFFFFF:06X}",
    }


def _typical_pressure(parser) -> float:
    values = []
    for r in parser._extract_object_stroke_records()[:400]:
        p = r.get("pressures") or []
        if p:
            values.append(sorted(p)[len(p) // 2])
    if not values:
        return 0.5
    values.sort()
    return max(0.15, min(0.9, values[len(values) // 2]))


def verify_added(original: Path, patched: Path, page_id: str, strokes) -> dict:
    """Re-read the patched note: other members unchanged, chain valid, points exact."""
    import tempfile

    with zipfile.ZipFile(original) as a, zipfile.ZipFile(patched) as b:
        names = [i.filename for i in a.infolist()]
        if names != [i.filename for i in b.infolist()]:
            raise PatchError("ZIP member list changed")
        changed = []
        for n in names:
            if a.read(n) != b.read(n):
                changed.append(n)
        if not set(changed) <= {f"{page_id}.page", "pageIdInfo.dat"}:
            raise PatchError("Unexpected ZIP members changed: " + ", ".join(changed))
        with tempfile.TemporaryDirectory() as tmp:
            for n in (f"{page_id}.page", "pageIdInfo.dat", "note.note"):
                if n in names:
                    (Path(tmp) / n).write_bytes(b.read(n))
            page = _Page(Path(tmp) / f"{page_id}.page")
            page.verify()
            if page.page_hash() not in (Path(tmp) / "pageIdInfo.dat").read_bytes():
                raise PatchError("pageIdInfo.dat hash mismatch after writing")
            records = page.parser._extract_object_stroke_records()
    new = records[-len(strokes) :] if strokes else []
    error = 0.0
    for got, want in zip(new, strokes):
        pts = want["points"] if len(want["points"]) > 1 else want["points"] + [
            [want["points"][0][0] + 0.05, want["points"][0][1]]
        ]
        if len(got["points"]) != len(pts):
            raise PatchError("Stroke point count changed after writing")
        for p, q in zip(got["points"], pts):
            error = max(error, abs(p[0] - q[0]), abs(p[1] - q[1]))
    if error > 1 / 64 + 1e-3:
        raise PatchError("Coordinate error budget exceeded")
    return {
        "changed_members": changed,
        "unchanged_members": len(names) - len(changed),
        "strokes_added": len(new),
        "max_coordinate_error": round(error, 5),
        "integrity_chain": "verified",
    }
