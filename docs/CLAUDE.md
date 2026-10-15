# Claude 연결 가이드

Inkbridge는 표준 MCP(stdio) 서버입니다. Claude Desktop, Claude Code, 그 밖의 MCP 클라이언트에서 쓸 수 있습니다. 서버는 내 컴퓨터에서만 돌고, 원격 서버는 없습니다.

## Claude Desktop — 확장(.mcpb) 설치 (권장)

1. [릴리스](https://github.com/lucaslucky10/sdocx-bridge/releases/latest)에서 `sdocx-bridge-<버전>.mcpb` 를 내려받아 더블클릭합니다. (또는 Claude → 설정 → 확장 프로그램 → 고급 설정 → 확장 프로그램 설치)
2. 설정 항목
   - **작업 폴더**: 가져온 노트·받아쓰기·결과가 저장됩니다. 기본 `문서\Inkbridge`.
   - **내 손글씨 파일 glyphs.json**: 고르면 `my-handwriting` 사전으로 설치되고, 파일이 바뀌면 다음 실행 때 다시 읽습니다.
   - **추가로 열 수 있는 폴더**: 다운로드·문서·바탕 화면(OneDrive 포함)은 이미 허용됩니다.
3. 첫 실행 때 Claude가 uv로 Python과 패키지를 준비합니다(인터넷 필요). 시간이 오래 걸려 "연결 실패"가 보이면 확장을 껐다 켜거나 Claude를 다시 시작하세요. 이미 받은 것은 남아 있어 다음에는 빨리 켜집니다.

## Claude Desktop — 설정 파일 등록 (대안)

확장 설치가 안 될 때 씁니다. 저장소 ZIP을 풀고 `connect-claude.cmd` 를 실행하면 다음이 자동으로 됩니다.

- uv가 없으면 설치(astral.sh 공식 설치 스크립트)
- 그 폴더에 `.venv` 를 만들고 패키지 설치
- `%APPDATA%\Claude\claude_desktop_config.json` 의 `mcpServers` 에 `sdocx-bridge` 를 추가(기존 설정은 `…backup-날짜` 파일로 백업)

그 뒤 Claude를 트레이에서도 완전히 종료하고 다시 켭니다. 손글씨 사전은 웹 화면이나 Claude에게 "이 glyphs.json을 내 손글씨로 등록해줘"라고 해서 설치합니다(`import_handwriting_profile`).

직접 넣으려면 `uv run sdocx-bridge claude-config` 가 출력하는 JSON을 기존 `mcpServers` 에 병합하세요.

## Claude Code

```bash
claude mcp add --transport stdio sdocx-bridge -- uv run --directory /path/to/sdocx-bridge --extra audio sdocx-bridge mcp
```

`/mcp` 로 상태를 봅니다.

## 이렇게 부탁하세요

- "다운로드 폴더의 회전운동.pdf 3페이지 오른쪽 위 여백에 '관성모멘트 $I=\sum mr^2$' 를 내 손글씨로 작게, 파란색으로 써줘."
- "a.sdocx의 손글씨를 페이지별로 전부 읽어서 텍스트로 정리해줘. 확실하지 않은 글자는 표시해줘."
- "a.sdocx 녹음을 받아쓰고, 내가 필기하지 않은 중요한 설명을 관련 필기 옆 빈 곳에 내 손글씨로 채워줘."
- "8강 노트 녹음만 따로 뽑아줘." / "결과 파일을 바탕 화면에 복사해줘."

Claude는 보통 `find_files → open_file → view_page(grid) → page_layout → write_handwriting → (미리보기 확인)` 순서로 일합니다. 받아쓰기는 `transcribe_recording` 으로 시작하고 끝날 때까지 상태를 확인한 뒤 `get_transcript` 로 문장과 그때의 필기 위치를 받습니다.

## 도구

| 도구 | 하는 일 |
|---|---|
| `find_files` | 허용된 폴더에서 .sdocx/.pdf 찾기 |
| `open_file` | 파일 가져오기(원본 보존, 같은 파일은 다시 가져오지 않음) |
| `list_notes`, `get_note`, `search_notes` | 노트 목록, 페이지·입력 텍스트·녹음 정보, 텍스트 검색 |
| `view_page` | 페이지 이미지. `region` 으로 확대, `grid` 로 좌표 격자 |
| `page_layout` | 필기 줄 위치, 빈 영역, 평소 글자 높이 |
| `read_strokes` | 획 좌표·필압·작성 시각 |
| `recording_timeline` | 녹음 분 단위로 무엇을 어디에 썼는지 |
| `transcribe_recording`, `get_transcript` | 로컬 받아쓰기 시작/상태, 문장별 필기 위치 |
| `list_handwriting_profiles`, `import_handwriting_profile`, `learn_character` | 손글씨 사전 관리 |
| `write_handwriting` | PDF 복사본, 삼성노트 복사본, 새 페이지에 내 글씨 쓰기. 미리보기 이미지 반환 |
| `view_output`, `export_note`, `save_copy` | 결과 보기, 내보내기(PDF/SVG/JSON/Markdown/녹음), 원하는 폴더로 복사 |

쪽 번호는 1부터, 좌표는 페이지 왼쪽 위가 원점입니다(PDF는 pt, 삼성노트는 노트 자체 단위). `--read-only` 로 실행하면 쓰기 도구가 등록되지 않습니다.

## 개인정보

- API 키나 유료 API를 쓰지 않습니다. 받아쓰기도 이 컴퓨터에서 합니다.
- Claude가 읽은 페이지 이미지·문장·받아쓰기는 Claude 대화에 들어갑니다.
- 서버는 작업 폴더와 허용된 폴더의 .sdocx/.pdf/.json만 엽니다. 노트 안의 글은 Claude에게 지시가 아닌 자료로 다루라고 서버 설명에 적어 두었습니다.
