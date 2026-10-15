# Validation — v0.3.3 (2026-10-15): paper scans and pen ink

New: `analyze_scan`, `view_scan`, `write_on_scan`, `make_blank_page`, `pen_samples`, `learn_paper_style` (modules `handwriting_engine/scanpage.py`, `pen.py`, `paper.py`, `sdocx_bridge/paper_tools.py`). Run in a Linux container through an MCP client; Windows Claude Desktop not run. The owner's scans, glyph dictionary and comparison images are not in this repository.

| Check | Result |
|---|---|
| Printed lines found on the owner's scanned concept-note pages (54 page images from 21 PDFs, 146–150 dpi and phone photos) | analysis runs on all 54 (0.1–4 s each); rows and table cells checked by eye on the numbered previews of a sample of form pages, not against hand-labelled ground truth. Pages without printed lines give one page box |
| Synthetic bent pages (lined, grid, plain) | line count exact ±1, detected curve within 1 px of the printed line (tests/test_paper.py) |
| Size, position and width of paper handwriting | closed loop on 11 transcribed rows of real writing: the row is erased, the same text is written into it, and the same measurement is applied to both. Ink band height 31 px real vs 30 px written, bottom 4 px above the printed line in both, row width ratio real/written 1.01 (rows that fit). The fit is repeatable on anyone's scans with `learn_paper_style` |
| Paper letter shape and ink (0.3.2) | same rows, real vs written, ratio real/written: word width 0.97-0.99, word height 1.01, row width 1.01, ink components per character 1.87 vs 1.88, stroke width at three ink thresholds within 4%, median darkness along the centre of a line 14 vs 21 (0.3.0: 51). Every change is the same for every letter (no random turn or shift); 0.3.1 random per-letter scribble was withdrawn after the owner judged it unstable. Paper only; tablet output unchanged |
| Phone photo of a printed form (0.3.3) | owner's photo of the concept-note form (1500×2000, grey paper): written ink was pure black (0-5 on paper 182) against print at 60-80; now no darker than the darkest 5% of the page (photo ≈0.15 of paper, scans 0). Ballpoint width kept to 0.25-0.45 mm. Plain-text formulas (`tan θ = y/x`) no longer get word-sized gaps, math is not split from the letters next to it, rows in a box start after a printed label (성질①, 설명) and run on into the ruled lines below, small fields (date) shrink to one line, overflowing text is written up to 15% smaller before did_not_fit. Checked by eye on the owner's blank form; scan fit unchanged (row width 1.02, word width 1.00) |
| Pen width and colour | measured from the ink already on the page (skeleton length vs ink area) and matched by rendering and re-measuring (closed loop) |
| Scanned PDF output | the page image is replaced in place (same size, same JPEG tables when the source is JPEG); other pages untouched; result can be written on again by `export_id` |
| Full test suite | 77 passed, 1 skipped |

Known limits: written text is a little more regular than real writing; characters missing from the glyph dictionary stop the write (nothing is written) and are named; very small or dense printed text inside a row is not distinguished from handwriting, so `filled` can be high for rows with printed text near the line; photos taken at an angle are not flattened (lines may be curved but the page is assumed roughly frontal).

Reference: thinning for the ink width measurement follows Zhang & Suen, "A fast parallel algorithm for thinning digital patterns", CACM 27(3), 1984.

> 0.2.7: 0.2.6은 사용자 Windows PC에서 복사와 시작까지는 됐지만 서버가 몇 초 만에 코드 0으로 끝났습니다(launch.log). 입력이 닫힌 것으로 보고, 표준 입출력을 자식 프로세스에 넘기지 않고 파이프로 중계하도록 바꿨습니다(원인은 추정, 다음 로그의 바이트 수로 확인). 치환되지 않은 `${DOCUMENTS}` 같은 값도 직접 채웁니다. Linux에서 MCP 클라이언트로 확인, Windows 미확인.

> 0.2.6: 0.2.5는 일부 Windows에서 프로그램 복사(fs.cpSync → CopyFile2)가 EPERM/UNKNOWN으로 실패했습니다(사용자 launch.log로 확인). 파일을 하나씩 읽고 써서 복사하고, 임시 폴더에 다 채운 뒤 이름을 바꿉니다. Linux에서 확장을 풀어 MCP 클라이언트로 실행(도구 18개, 손글씨 사전 확인)하고 확장 폴더가 바뀌지 않는 것을 확인했습니다. Windows 실기기는 미확인.

# Validation — v0.2.5 (2026-10-14)

v0.2.5: building the package also renames files next to the sources, which failed inside the extension folder; the launcher now copies the program to `~/.inkbridge/app-<version>` and runs from there, and writes `~/.inkbridge/launch.log`. Checked that the unpacked extension folder is left unchanged.

v0.2.4: Claude Desktop itself also runs uv at install time for "uv" extensions and failed the same way, so the extension now uses type "node" (Claude's built-in Node.js) with a small launcher, `server/launch.js`, that runs uv with everything in `~/.inkbridge`. Tested by packing, unpacking and driving it with an MCP client in the container.

v0.2.3: on the owner's PC even renaming a folder inside the extension directory failed with os error 17 (likely Windows app-data virtualization for the Claude Desktop package). uv's Python, cache and virtual environment now live in `%USERPROFILE%\.inkbridge` instead. Windows retry pending.

## v0.2.1

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
