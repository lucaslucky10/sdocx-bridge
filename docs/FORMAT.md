# Portable ink format / API

Each `note.json` is UTF-8 JSON with `schema: "sdocx-bridge/1"`.

```json
{
  "schema": "sdocx-bridge/1",
  "pages": [{
    "id": "page-id",
    "width": 595,
    "height": 842,
    "strokes": [{
      "id": "stroke-id",
      "points": [[50, 60], [51, 62]],
      "width": 1.5,
      "color": [0.1, 0.2, 0.3],
      "opacity": 1,
      "pressure": [0.4, 0.5],
      "time": [0, 8]
    }],
    "text": "typed text only",
    "objects": []
  }]
}
```

- Coordinates: top-left origin, x right, y down. Native source logical units for SDOCX; points for uploaded/generated PDF. Page dimensions accompany every page. Do not assume the source SDOCX is already 72 dpi.
- `points` are stroke centerlines in source order, not glyph font outlines and not image tracing.
- Width is a logical page-unit diameter; RGB channels and opacity are 0..1.
- Pressure/time/tilt/orientation arrays are retained when decoded. Empty means absent, not zero.
- `created_us` (SDOCX strokes): the stroke object's creation time, epoch microseconds, read from the object's base record. Used to align strokes with recordings.
- Renderer uses constant per-stroke width. The raw channels support external training and improved future renderers.
- `origin` in generated ink identifies glyph resampling. Generated source profiles need not contain real-time pen dynamics.
- Generation report names directly missing glyphs, assembled Hangul and derived symbols; includes seed and binary SDOCX roundtrip maximum coordinate error.
- JSON may gain additive fields. Consumers should ignore unknown fields and retain originals.

## HTTP

The local UI uses `/api/notes`, `/api/upload`, `/api/profiles`, `/api/write`, `/api/learn`, and `/api/notes/{id}/export/{format}`. Requests require the current process's `x-bridge-token`. This API is for same-machine development, not an unauthenticated remote server. For AI clients prefer MCP; it does not require browser tokens.

## Glyph dictionaries

Compatible with `my-handwriting` dictionaries: `glyphs[]` with `ch`, `src`, `adv`, `top`, `bot`, `strokes[{p,w,c}]`, optional `stats` and `runs`. Point coordinates are normalized glyph units with baseline conventions from the source dictionary. Import validation rejects nonfinite coordinates, malformed channels and excessive budgets. Zero-width legacy samples are retained; the compositor uses the profile's positive measured pen width.

New labels preserve source note/page/stroke IDs. Labels are explicit annotations; the application does not pretend that unlabeled strokes have known textual meaning. Personal training data stays outside the source package.

## Samsung Notes details used by v0.2 (reverse engineered)

**Recordings.** Media files are named `<id>@<hex unix seconds>_<suffix>.m4a`. `note.note` holds, per recording, `u32 media id`, UTF-16 title (`음성 001`), UTF-16 `HH:MM:SS`, `u64 start (epoch ms)`, and a little later `u64 exact length (ms)`. The parser accepts a record only when the text length and the millisecond length agree within one second; otherwise it falls back to the file-name start time (one-second precision) and says so in `timing_source`.

**Stroke time.** Each object's base subrecord holds `uuid`, `modified (µs)`, its rectangle and `created (µs)`. A transcript sentence at recording offset *t* is matched to strokes whose `created_us/1000` lies within `start_ms + t ± 5 s`.

**Adding strokes to an existing note** (`sdocx_patch.add_strokes`):

1. Verify the page's integrity chain: object hash = SHA-256 of `"{uuid}{modified}"`; layer hash = SHA-256 of the concatenated object hashes + layer identity; page hash = SHA-256 of layer hash + page identity; the page hash is stored in `pageIdInfo.dat`. Unknown layouts are refused rather than guessed.
2. Clone an existing stroke object from the same note as a template (most common pen style, or one matching the requested colour), and fill in new uuid, times, rectangle, compact geometry (f64 start + int16 deltas of 1/32 unit) and a pressure envelope at the note's median pressure.
3. Append to the last layer, update object count, layer time and hash, drawn rectangle, page time and hash, and `pageIdInfo.dat`.
4. Rewrite the ZIP with the same member order; unchanged members keep their original compressed bytes; the trailing footer after the central directory is re-appended.
5. Re-read the result: only the page and `pageIdInfo.dat` may differ, the chain must verify, and every point must be within 1/64 unit.

Pages with more than one layer are refused. The page's `.spi` preview tile is left as it is.
