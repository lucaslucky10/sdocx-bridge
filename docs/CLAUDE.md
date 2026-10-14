# Claude / MCP 연결

서버는 표준 MCP **stdio**를 사용합니다. Claude Desktop, Claude Code와 stdio를 지원하는 다른 클라이언트에서 사용할 수 있습니다. 공개 원격 MCP 서버는 배포하지 않습니다.

## Claude Desktop

설치에 사용한 Python으로 아래를 실행합니다.

```powershell
.venv\Scripts\python -m sdocx_bridge --library "D:/Inkbridge/library" claude-config
```

출력은 현재 Python과 보관함의 절대 경로를 포함합니다. Claude Desktop의 Developer/MCP 설정에서 설정 파일을 열어 기존 `mcpServers` 객체에 `sdocx-bridge` 항목을 추가합니다. Windows 기본 위치는 `%APPDATA%/Claude/claude_desktop_config.json`입니다. Claude를 완전히 종료하고 다시 실행합니다.

예시 (경로를 실제 설치 경로로 바꾸세요):

```json
{
  "mcpServers": {
    "sdocx-bridge": {
      "command": "D:/Inkbridge/.venv/Scripts/python.exe",
      "args": ["-m", "sdocx_bridge", "--library", "D:/Inkbridge/library", "mcp"]
    }
  }
}
```

생성과 파일 추가를 막으려면 마지막 인수에 `--read-only`를 추가합니다. 읽기 전용 모드는 쓰기 도구를 등록하지 않습니다.

## Claude Code

[공식 MCP 설정 문서](https://code.claude.com/docs/en/mcp)의 stdio 형식으로 등록합니다.

```powershell
claude mcp add --transport stdio sdocx-bridge -- "D:/Inkbridge/.venv/Scripts/python.exe" -m sdocx_bridge --library "D:/Inkbridge/library" mcp
```

`/mcp`에서 상태를 확인합니다. 필요하면 `claude mcp remove sdocx-bridge`로 연결만 제거할 수 있습니다. 보관함 파일은 남습니다.

## 제공 도구

| 도구 | 입력/동작 |
|---|---|
| `list_notes` | 노트·페이지·획·녹음 개수 |
| `inspect_note` | 페이지 크기, 입력된 텍스트, 미지원 경고 |
| `page_preview` | 실제 획을 볼 수 있는 PNG; SDOCX는 획 중심 |
| `read_strokes` | 페이지당 좌표와 필압·시간 등, offset/limit 페이지네이션 |
| `search_notes` | 제목/입력된 텍스트 검색 |
| `list_handwriting_profiles` | 사전과 직접 기록된 글자 목록 |
| `import_note` | `inbox` 내부 상대 경로의 SDOCX/PDF 가져오기 |
| `export_note` | JSON/SVG/PDF/Markdown/녹음 추출 |
| `learn_character` | 정확히 확인한 글자와 stroke ID를 사전에 저장 |
| `write_handwriting` | 개인 사전 → PDF Ink/SDOCX/SVG/JSON |

페이지 인덱스는 0부터 시작합니다. 위치는 해당 페이지의 논리 좌표, PDF는 pt(72pt=1인치)입니다. `sdocx://note/{note_id}` 리소스와 `transcribe_note` 프롬프트도 제공합니다.

## 운영·개인정보

- 별도의 Anthropic API 키를 이 프로그램에 넣지 않습니다. Claude 구독/API 비용은 사용하는 클라이언트의 정책을 따릅니다.
- Claude가 도구를 실행하면 그 결과가 Claude 문맥에 들어갑니다. 녹음 파일은 추출만 하며 자동 업로드·전사하지 않습니다.
- 서버는 설정한 보관함의 자료에 접근합니다. 읽기 도구도 개인 필기 내용을 노출하므로 신뢰하는 로컬 클라이언트에만 연결하세요.
- 처음에는 `list_notes` → `inspect_note` → `page_preview`를 요청해 확인하세요. 글자 라벨은 시각적으로 검수한 뒤 추가하세요.
- 도구 프로토콜은 실제 SDK 클라이언트로 검증했습니다. 사용자 Claude Desktop/Code 계정의 실제 앱 접속은 별도 확인 사항입니다.

SDK: [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk). 이 릴리스는 v1 API의 `mcp>=1.26,<2`를 사용합니다.
