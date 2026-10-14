# Validation — v0.2.1 (2026-10-14)

v0.2.1 fixes the first real Claude Desktop install on Windows (owner's PC): (v0.2.2: the same cross-drive error then hit the managed Python install folder, so Python is now installed inside the extension too via `UV_PYTHON_INSTALL_DIR`.) uv inspected a system Python 3.14 and failed to write its cache (`os error 17`, cross-drive move). The extension now runs uv with `--python-preference only-managed`, its own cache folder inside the extension and `--link-mode copy`. Re-tested in the container only; the Windows retry is pending.

## v0.2.0

Checks for this release were run in a Linux container (4 CPU cores, Python 3.12). Windows was **not** run for this release; the GitHub Actions Windows jobs are the only Windows evidence. Private notes, recordings and the glyph dictionary are not in this repository.

## What was actually executed

| Check | Result |
|---|---|
| Private SDOCX notes parsed (fresh library) | 30 / 30 (116 pages, 76,524 strokes) |
| Strokes with a decoded creation time | 76,524 / 76,524 |
| Notes with recordings / recordings with decoded start + length from `note.note` | 2 / 4 |
| In-place stroke addition to a copy of each note, then re-import | 30 / 30, stroke count +1, max coordinate error 0.0 |
| Integrity chain (object → layer → page → `pageIdInfo.dat`) recomputed and re-verified | 30 / 30 |
| Members other than the edited `.page` and `pageIdInfo.dat` | byte-identical (raw compressed bytes copied) |
| Local transcription, `large-v3-turbo` int8 on CPU | 15 min 32 s lecture → 215 sentences in about 6 min |
| Sentences with handwriting written within ±5 s | 143 / 215 |
| Visual check of the alignment | a sentence at 02:47 matched the handwritten line it describes (page 1 box rendered and inspected) |
| Korean + LaTeX written into a PDF page through the packed `.mcpb` (`uv run …`, no Node.js on PATH) | done; preview inspected visually |
| Korean text written into a real lecture note (`_손글씨.sdocx`) | 114 strokes, max coordinate error 0.0156; preview inspected visually |
| Automated tests | 70 passed (synthetic data only) |

The `.mcpb` was built with `@anthropic-ai/mcpb` 2.1.2 (`validate` + `pack`), unpacked into an empty folder, and started exactly as the manifest says (`uv run --directory <folder> --extra audio --quiet sdocx-bridge --library … mcp …`) with an empty uv cache. Environment creation took 16 s on the container's network. A real MCP client session then listed 18 tools, auto-installed the glyph file named by `SDOCX_BRIDGE_PROFILE_FILE` (13,183 glyphs, 629 characters), found and opened a PDF from an allowed folder, and wrote Korean + LaTeX on page 1.

A bug found by this run and fixed: the embedded MathJax engine (mini-racer) refuses time limits on a thread that runs an asyncio loop, which is where MCP tools run. MathJax now runs on its own worker thread; a regression test calls it from inside an event loop.

## Not verified

- Opening a patched or generated `.sdocx` in the Samsung Notes app on a device. The app may show a cached page preview (`.spi`) until it redraws; this was not observed either way.
- Installing the `.mcpb` in the real Claude Desktop app (Windows or macOS). The uv runtime for extensions is marked experimental in the MCPB documentation. A slow first download could exceed the app's start-up wait; restarting the extension should then work because uv keeps what it downloaded.
- `start.cmd` / `connect-claude.cmd` on Windows.
- Transcription accuracy beyond reading the sample output; speed on the user's computer.
- Windows on ARM: faster-whisper's engine (CTranslate2) has no wheel there, so transcription is unavailable on that platform.

---

# Validation — v0.1.1 (2026-10-15)

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

**57 tests passed** locally. Test data is synthetic; no private corpus is required for public tests.

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

## v0.1.1 full corpus re-audit

All 27 supplied files were imported into fresh temporary libraries (no cached import summaries). All 135 export calls (27 × JSON/SVG/PDF/Markdown/audio) completed. The 112 exported PDF pages were rasterized successfully, and their 72,431 Ink annotations matched the imported stroke count. First/last-page previews were also exercised. Original SHA-256 checks passed for 27/27 files; extracted recording hashes passed for 4/4 files. No additional per-object PDF rendering errors were reported. This is execution/integrity coverage, not a pixel-fidelity comparison with Samsung Notes.

The current suite has 57 cases. GitHub Actions runs all four Windows/Linux × Python 3.11/3.13 jobs, including optional MathJax. The Windows npm installation needed an explicit working directory; the workflow and README were corrected. See Actions for the final commit's result.
