# Source provenance

## Dietrich

- URL: https://github.com/Downhillghost/Dietrich
- Commit: `bb9c9202a8ab2bf96347e6cd0b16eaa354b80483`
- Vendored directory: `src/note_pipeline`
- Original license: MIT, retained in `Dietrich-LICENSE`.
- Changes: lazy exporter imports; compact coordinate/pressure error-feedback encoding to prevent accumulated rounding drift; preservation of one-point pen strokes. Additional hardening changes are described in the validation/security documentation and source comments.
- Tests/data/screenshots from upstream are not included.

## my-handwriting

- URL: https://github.com/lucaslucky10/my-handwriting (may require access)
- Commit: `3b6fff0d71b22aa7c6a87fcbd02b2f85a388a51d`
- Included: compose, align, segment, scripts, hangul, derive, MathJax layout helper.
- Reused for this new open-source project at the repository owner's explicit request. These included files are distributed under this repository's AGPL-3.0-only license.
- Changes: package-local MathJax helper path, bounded subprocess timeout and explicit math error reporting; visual-line metadata for intact pagination and stroke order; runtime compatibility fixes documented in source.
- No personal glyph database, note labels, source SDOCX, media or handwriting images are included.

## Dependencies

PyMuPDF is AGPL-3.0/commercial dual-licensed; this project uses its AGPL option. NumPy, Pillow, FastAPI, Uvicorn, MCP SDK, Kaitai Struct, Matplotlib and optional MathJax retain their own licenses. Dependency wheels and Node modules are not vendored into the source release. Install them from their package registries. See `pyproject.toml` and the optional math package manifest.
