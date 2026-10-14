# Validation — 2026-10-15

Release candidate: 0.1.1. Local execution: Windows, Python 3.14.6. Source fixtures below are private and are **not** included in this repository.

## Private real-file corpus

| Check | Observed result |
|---|---:|
| Supplied SDOCX files imported | 27 / 27 |
| Parsed pages, including stored blank pages | 112 |
| Parsed native handwriting strokes | 72,431 |
| Decoded coordinate points | 7,307,919 |
| Notes containing recording assets | 2 |
| Extracted M4A recordings | 4 |
| Total recording bytes | 115,479,246 |
| Original file SHA-256 unchanged after work | 27 / 27 |
| Extracted recording SHA-256 equals archived source | 4 / 4 |

Each import checks archive limits, retains an original copy and a per-member hash manifest. The corpus file list and per-file hashes are retained privately for the owner. These counts demonstrate parser execution on this corpus; they do not prove all original objects were rendered, perfect Samsung visual fidelity, or correct transcription.

## Personal handwriting integration

The owner's existing dictionary imported 13,183 glyph variants over 629 labels. Three legacy samples with zero width were accepted without changing the source dictionary because composition uses the measured positive profile pen width. The dictionary is not distributed.

- Korean prose sample generated 79 native strokes, with two Hangul syllables explicitly reported as jamo compositions.
- A two-line Korean/LaTeX sample generated 81 strokes without missing glyphs. Its PDF was rendered and visually inspected.
- Every generation now reads its own emitted binary SDOCX, checks stroke and point counts, and records maximum coordinate error. The real math sample's maximum coordinate error was **0.0156160491 page unit**, within the 1/64 quantization budget.
- Source strokes and generated strokes remain distinct. The system does not claim that generated pressure or timing is a recording of the user's pen motion.

## Automated tests

**54 tests passed** locally. Test data is synthetic; no private corpus is required for public tests.

- ZIP traversal, absolute/Windows paths, reserved devices, symlink entries, case collisions, compression bombs.
- Input validation, upload handling, duplicate imports, malformed archives, scoped profile IDs, nonfinite coordinates.
- No data loss for extracted audio bytes and preservation of blank pages.
- Native SDOCX stroke/point count and coordinate roundtrip.
- 1,200-point compact encoding regression: coordinate error ≤ 1/64, pressure error ≤ 1/(64×128), timestamp equality.
- PDF `/Ink` annotations, absence of raster handwriting on blank output, preservation of original PDF text and page count, selected-page placement.
- Multi-page output, overflow failures, deterministic seeds, explicit missing-glyph failure.
- Label selection → new private profile → new native handwriting.
- JSON/SVG/PDF/Markdown/audio export surfaces.
- API session protection and Host validation.
- A real subprocess MCP stdio session exercises all 10 tools, note resources and transcription prompts; path escape and read-only tool registration are also checked.
- Existing PDF annotations and links, rotated-page writing at 90/180/270 degrees, original PDF preview pixels, and protected generated previews.
- Single-point native ink, detached glyph components staying on the same page and retaining source stroke order.
- Malformed profile shapes and PDFs, Windows ZIP aliases, atomic export rollback, order-independent duplicate learning and cross-process profile locking.
- Actual optional MathJax fraction layout, explicit invalid/unsupported formula errors, missing renderer/timeout failures and CLI commands.

One dependency deprecation warning from Starlette's `httpx` TestClient was present; it did not fail the tests. NumPy is initialized before asynchronous server threads to avoid an observed Windows first-call import hang.

## UI / packaging

The browser UI was used to load the real note library, select the personal dictionary, create a Korean handwriting result, inspect its download buttons and preview, and open a note containing recordings. The v0.1.1 audit additionally exercised mixed valid/invalid batch uploads, drag-selecting a two-stroke A, learning a new private profile and composing from it, PDF page 2 placement, and browser download. The downloaded PDF retained both original comment annotations and placed all 79 new Ink annotations on page 2. Source distribution and wheel builds succeeded; the wheel was checked for UI/math resources and absence of notes, recordings, glyph databases and Node modules.

## Not verified / not promised

- Actual Samsung Notes device import, editing, and pixel-by-pixel comparison with Samsung output.
- Actual end-user Claude Desktop/Claude Code session (the standard MCP transport was tested).
- Complete decoding of all SDOCX versions, SPI tiles, eraser semantics, every pen style, or every object type.
- Automatic handwriting transcription accuracy, audio transcription, or voice/ink synchronization.
- Identity-level similarity to the user's handwriting, or broad superiority over other projects.

The GitHub Actions matrix tests Windows/Linux and Python 3.11/3.13. Its online results should be read separately from the local checks above.

## PDF implementation reference

The rotation change uses [PyMuPDF Page.remove_rotation](https://pymupdf.readthedocs.io/en/latest/page.html#Page.remove_rotation), which preserves appearance and adjusts annotations/links/widgets. A normal PDF re-export now copies the original bytes instead of rebuilding pages with show_pdf_page; previews rasterize the original page with annotations. Automated tests verify the resulting behavior.
