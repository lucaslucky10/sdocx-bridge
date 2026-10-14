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
- Pressure/time/tilt/orientation arrays are retained when decoded. Empty means absent, not zero. Timing is the decoder's per-stroke channel; no global recording synchronization is inferred.
- Renderer uses constant per-stroke width. The raw channels support external training and improved future renderers.
- `origin` in generated ink identifies glyph resampling. Generated source profiles need not contain real-time pen dynamics.
- Generation report names directly missing glyphs, assembled Hangul and derived symbols; includes seed and binary SDOCX roundtrip maximum coordinate error.
- JSON may gain additive fields. Consumers should ignore unknown fields and retain originals.

## HTTP

The local UI uses `/api/notes`, `/api/upload`, `/api/profiles`, `/api/write`, `/api/learn`, and `/api/notes/{id}/export/{format}`. Requests require the current process's `x-bridge-token`. This API is for same-machine development, not an unauthenticated remote server. For AI clients prefer MCP; it does not require browser tokens.

## Glyph dictionaries

Compatible with `my-handwriting` dictionaries: `glyphs[]` with `ch`, `src`, `adv`, `top`, `bot`, `strokes[{p,w,c}]`, optional `stats` and `runs`. Point coordinates are normalized glyph units with baseline conventions from the source dictionary. Import validation rejects nonfinite coordinates, malformed channels and excessive budgets. Zero-width legacy samples are retained; the compositor uses the profile's positive measured pen width.

New labels preserve source note/page/stroke IDs. Labels are explicit annotations; the application does not pretend that unlabeled strokes have known textual meaning. Personal training data stays outside the source package.
