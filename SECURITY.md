# Local security model

Run on loopback only. This release is a local desktop companion, not a multi-user internet service.

- ZIP extraction validates traversal, absolute/drive paths, Windows reserved names, symlinks, case-insensitive collisions, encryption, size, compression ratio and entry count before writing.
- Original input is copied and hash-verified. Imports are staged; complete notes are moved into a content-addressed directory.
- API requests require a random per-process session token. Host validation, no cross-origin allowance, no external scripts, CSP and no-sniff headers protect the local browser surface.
- Browser rendering builds DOM text nodes rather than inserting note text as HTML. Generated SVG escapes identifiers.
- MCP file imports are scoped to `library/inbox`; exports use generated IDs and filenames. No tool accepts an arbitrary output path or shell command. The CLI can import user-selected local files.
- Notes are untrusted content; MCP instructions and prompts distinguish note content from commands.
- Limits are conservative safeguards, not an audited resource sandbox. Native PDF libraries and reverse-engineered parsers remain a risk with deliberately hostile inputs. Do not expose the server publicly or run with administrator privileges.
- Multiple desktop processes may share a library for reads. Avoid simultaneous edits to the same glyph profile; cross-process edit transactions are not implemented in 0.1.

Report vulnerabilities privately to the repository owner through GitHub's available security reporting channel. Never attach private notes or recordings to a public issue.
