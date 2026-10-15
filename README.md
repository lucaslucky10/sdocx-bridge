# Inkbridge · SDOCX Bridge

**삼성노트(.sdocx)와 PDF를 Claude가 직접 읽고, 녹음을 받아쓰고, 내 손글씨 획으로 써 넣게 해 주는 로컬 프로그램.**

Claude에게 이렇게 말하면 됩니다.

> 다운로드 폴더의 `회전운동.pdf` 3페이지 오른쪽 위에 "관성모멘트 $I=\sum mr^2$" 를 내 손글씨로 작게 넣어줘.

> `8강.sdocx`의 손글씨를 전부 읽어 줘. 그리고 녹음을 받아써서 내가 필기 못 한 내용을 관련 필기 옆에 내 손글씨로 채워 줘.

Claude는 파일을 찾아 열고, 페이지를 눈으로 보고, 녹음을 이 컴퓨터에서 무료로 받아쓰고(Whisper), 강의 중 **그 문장이 나올 때 무엇을 쓰고 있었는지**를 획의 작성 시각으로 맞춰 본 뒤, 빈 자리에 내 글씨를 **실제 펜 획**으로 써 넣습니다. 원본 파일은 바꾸지 않고 새 파일을 만듭니다.

## 설치 (Windows, Python 설치 필요 없음)

### 1. Claude에 연결하기 — 권장

1. [Claude Desktop](https://claude.ai/download)을 설치하고 로그인합니다.
2. [최신 릴리스](https://github.com/lucaslucky10/sdocx-bridge/releases/latest)에서 `sdocx-bridge-<버전>.mcpb` 를 내려받아 **더블클릭**합니다. Claude 설정의 확장 프로그램 화면이 열리면 **설치**를 누릅니다.
3. 설정 화면에서 **내 손글씨 파일 glyphs.json** 에 `my-handwriting/data/glyphs.json` 을 고릅니다. 작업 폴더는 기본값(`문서\Inkbridge`)으로 두면 됩니다.
4. 처음 켤 때 Claude가 Python과 필요한 패키지를 자동으로 내려받습니다(인터넷 필요, 몇 분). 그다음부터는 바로 켜집니다.

다운로드·문서·바탕 화면 폴더의 파일은 바로 열 수 있습니다. 다른 폴더는 설정의 **추가로 열 수 있는 폴더**에 넣으세요.

> `.mcpb` 설치가 안 되는 Claude 버전이라면: 저장소 ZIP을 풀고 `connect-claude.cmd` 를 실행한 뒤 Claude를 완전히 종료했다가 다시 켭니다. 자세한 내용과 Claude Code 연결은 [연결 가이드](docs/CLAUDE.md).

### 2. 브라우저 화면 쓰기 (선택)

릴리스의 `sdocx-bridge-<버전>-windows.zip` 을 풀고 `start.cmd` 를 실행하면 http://127.0.0.1:8765 가 열립니다. 첫 실행은 실행 도구(uv)와 패키지를 설치합니다. Claude 확장과 같은 작업 폴더(`문서\Inkbridge`)를 씁니다.

macOS/Linux: [uv](https://docs.astral.sh/uv/)를 설치한 뒤 저장소 폴더에서 `uv run --extra audio sdocx-bridge web`.

## 할 수 있는 일

| 작업 | 내용 |
|---|---|
| 노트 읽기 | 페이지 이미지(확대·좌표 격자), 획 좌표·색·필압·**작성 시각**, 입력된 텍스트, 첨부 이미지·PDF 배경 |
| 녹음 | 녹음 추출(재인코딩 없음), 제목·길이·**녹음 시작 시각** 해석, 무료 로컬 받아쓰기(faster-whisper `large-v3-turbo`, CPU) |
| 녹음 ↔ 필기 맞추기 | 받아쓴 문장마다 그때(±5초) 쓴 필기의 페이지와 위치, 필기가 없던 구간 표시 |
| 내 글씨로 쓰기 | 글자 변형 선택, 한글 자모 조합, LaTeX 수식(`$...$`), 색, 크기, 위치·너비 지정 |
| PDF에 쓰기 | 원본 PDF를 보존한 복사본에 `/Ink` 주석(벡터 획)으로 추가 |
| 삼성노트에 쓰기 | **기존 .sdocx 복사본에 새 펜 획 객체를 추가**. 녹음·이미지·다른 페이지는 바이트 그대로 유지하고 무결성 해시를 다시 계산. 글씨 크기는 그 노트의 내 글씨 크기에 자동으로 맞춤 |
| 빈 자리 찾기 | 페이지의 필기 줄과 빈 영역, 평소 글자 높이 |
| 새 노트 | 내 글씨로 새 PDF/SDOCX 생성 |
| 글자 학습 | 노트의 획을 골라 없는 글자를 사전에 추가 |
| **종이 노트 사진·스캔에 쓰기** (0.3) | 사진(.jpg/.png …)이나 스캔 PDF에서 인쇄된 줄(휘어 있어도)·표 칸·이미 쓴 글씨를 찾아, 그 줄을 따라 **볼펜으로 쓰고 스캔한 것처럼** 씀. 볼펜 잉크 끊김·뭉침·종이 결·스캐너 번짐을 재현하고, 그 페이지에 이미 있는 펜의 굵기·색을 재서 맞춤 |
| 종이 글씨체 | 태블릿 글씨를 종이 글씨처럼 바꿈(글자 간격, 획 붙임, 획 끝 늘어짐, 분수 크기, 볼펜으로 휘갈긴 흘림: 글자마다 기울기·크기 흔들림, 둥글게 뭉개진 꺾임, 올라가는 가로획, 글자 사이 이어쓰기). 덜 흘리려면 `style: {"scribble": 0.5}`, 끄려면 0. 태블릿 글씨(`write_handwriting`)는 그대로. 실제 스캔의 줄과 같은 측정값이 나오도록 맞춘 기본값, `learn_paper_style` 로 내 스캔에 다시 맞춤 |
| 펜·표시 | 볼펜(검정·파랑·빨강), 젤펜, 연필, 형광펜, 플러스펜. 밑줄·동그라미·네모·체크·별·화살표·괄호·손으로 그린 선, 지우기(인쇄된 줄은 남김) |
| 빈 종이 | 스캔한 것 같은 줄 노트·모눈·점선·무지 페이지 |

## Claude 도구

`find_files` · `open_file` · `list_notes` · `get_note` · `view_page` · `page_layout` · `read_strokes` · `search_notes` · `recording_timeline` · `transcribe_recording` · `get_transcript` · `list_handwriting_profiles` · `import_handwriting_profile` · `write_handwriting` · `view_output` · `export_note` · `save_copy` · `learn_character` · `analyze_scan` · `view_scan` · `write_on_scan` · `make_blank_page` · `pen_samples` · `learn_paper_style`

페이지 번호는 사람이 말하는 대로 1부터 셉니다. `write_handwriting` 은 결과를 확대한 미리보기 이미지를 함께 돌려주므로 Claude가 겹침을 보고 다시 쓸 수 있습니다. 결과 파일은 `문서\Inkbridge\exports\…` 에 생기고, `save_copy` 로 원하는 폴더에 복사합니다.

## 개인정보·비용

- 모든 처리는 이 컴퓨터에서 합니다. 받아쓰기 모델은 처음 한 번 Hugging Face에서 내려받고(약 1.6GB) 이후 오프라인으로 돌아갑니다. 유료 API나 API 키가 필요 없습니다.
- Claude가 도구로 읽은 페이지 이미지·문장은 Claude 대화에 들어갑니다. 그 밖의 업로드는 없습니다.
- 손글씨 사전(glyphs.json)·노트·녹음은 이 저장소에 포함되어 있지 않고 올리지도 않습니다.

## 검증과 한계

자세한 내용은 [검증 보고서](docs/VALIDATION.md).

- 비공개 노트 30개(116쪽, 획 76,524개)를 모두 읽었고, 모든 획의 작성 시각을 해석했습니다. 30개 모두에 획을 추가한 복사본을 만들고 다시 읽어 확인했습니다.
- 실제 강의 녹음(15분 32초)을 받아쓰기해, 문장과 그때 쓴 필기가 맞게 연결되는지 페이지 화면으로 확인했습니다.
- **삼성노트 앱에서 결과 .sdocx를 여는 것은 아직 실기기로 확인하지 못했습니다.** 형식은 비공개이며 역분석에 기반합니다. 앱이 미리보기 캐시를 보여 줄 가능성이 있습니다.
- **Claude Desktop에서 .mcpb 설치는 아직 실제 앱으로 확인하지 못했습니다.** 같은 명령(`uv run …`)으로 서버를 띄워 MCP 클라이언트로 끝까지 실행하는 것은 확인했습니다. uv 런타임 확장은 Claude Desktop에서 실험 기능입니다.
- 종이 쓰기(0.3)는 실제 개념정리노트 스캔 54쪽으로 줄 찾기를 확인하고, 손으로 쓴 줄 11개를 지운 뒤 같은 내용을 다시 써서 글자 높이·줄 위 위치·줄 폭·획 길이를 비교해 기본값을 맞췄습니다. 사람 눈으로 구별되는지는 사용자가 판단할 몫이고, 생성 글씨가 실제보다 조금 고르게 보입니다.
- 손글씨 인식(OCR) 기능은 따로 없습니다. Claude가 페이지 이미지를 보고 읽습니다.
- 받아쓰기는 CPU로 돌아가서 시간이 걸립니다. 테스트 서버(4코어)에서 15분 32초 녹음에 약 6분이 걸렸고, 컴퓨터마다 다릅니다. Windows ARM 기기는 받아쓰기 패키지가 없어 받아쓰기를 쓸 수 없습니다.
- 삼성노트 쓰기는 한 번에 한 페이지, 레이어가 하나인 페이지에만 합니다.

## 개발

```bash
uv sync --extra audio --extra dev
uv run pytest -q
```

`src/sdocx_bridge`: 보관함·MCP·웹·삼성노트 쓰기·녹음 정렬. `src/note_pipeline`: MIT Dietrich 기반 파서. `src/handwriting_engine`: 저장소 소유자의 손글씨 조판 코드와 MathJax 번들. [출처·변경 기록](third_party/NOTICE.md), [파일 형식 메모](docs/FORMAT.md), [다른 프로젝트와 비교](docs/COMPARISON.md).

AGPL-3.0-only. 포함된 MIT·Apache-2.0 코드의 라이선스는 유지됩니다. Samsung Notes는 Samsung Electronics의 상표이며, 이 프로젝트는 삼성이나 Anthropic의 공식 제품이 아닙니다.
