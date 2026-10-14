# Inkbridge · SDOCX Bridge

**삼성노트의 실제 획을 읽고, 내 글씨로 새 노트를 쓰고, Claude에 연결하는 로컬 오픈소스 작업실.**

Local Samsung Notes interoperability: SDOCX inspection, lossless audio extraction, personal handwriting composition, editable PDF ink, native SDOCX strokes, and a standard MCP server.

## 바로 시작

Python 3.11 이상이 필요합니다. Windows에서는 저장소를 내려받고 `start.cmd`를 실행하세요. 첫 실행은 가상환경과 필요한 패키지를 설치합니다. 브라우저에서 **http://127.0.0.1:8765** 를 엽니다.

```powershell
git clone https://github.com/lucaslucky10/sdocx-bridge.git
cd sdocx-bridge
python -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m sdocx_bridge --library ./library web
```

macOS/Linux: `python3 -m venv .venv`, `.venv/bin/python -m pip install -e .`, `.venv/bin/python -m sdocx_bridge --library ./library web`.

파일은 로컬 보관함에 저장됩니다. 웹 화면은 외부 API·분석 서비스·CDN을 사용하지 않습니다. MCP로 읽은 내용은 연결한 AI 서비스에 전달될 수 있습니다.

## 가능한 작업

| 작업 | 구현 범위 |
|---|---|
| 노트 가져오기 | 여러 SDOCX/PDF 업로드, SHA-256 중복 확인, 원본 보관 |
| 획 분석 | 페이지·좌표·색·굵기·필압·시간·기울기·방향 채널, 저장 순서 재생 |
| 녹음 추출 | M4A/MP3/WAV/AAC/OGG/FLAC/AMR/3GP를 재인코딩 없이 추출, 원본 해시 검증 |
| 내보내기 | JSON, 획 SVG, PDF 잉크 주석, 입력된 텍스트 Markdown, 녹음 |
| 내 글씨 사전 | 기존 `my-handwriting/data/glyphs.json` 가져오기, 획 클릭·영역 선택 후 글자 라벨링 |
| 텍스트 → 내 글씨 | 글자 변형 선택, 한글 자모 조합, 줄바꿈, 여러 페이지, 재현 가능한 seed |
| PDF 위에 쓰기 | 시작 쪽·위치·크기·너비 지정. 기존 PDF를 보존하고 `/Ink` 주석 추가 |
| 새 삼성노트 | 이미지로 붙이는 글씨가 아닌 **native pen stroke** 객체로 SDOCX 생성 |
| Claude/다른 AI | 표준 stdio MCP 도구 10개, 노트 리소스, 받아쓰기 프롬프트, 읽기 전용 모드 |
| 수식 | 선택 설치한 MathJax 조판으로 `$...$` LaTeX를 내 글자 획으로 배치 |

### 내 글씨로 쓰기

1. **노트 보관함**에 SDOCX/PDF를 올립니다.
2. 기존 프로젝트의 `data/glyphs.json`을 가져옵니다. 기본 사전 이름은 `my-handwriting`입니다. 이 저장소에는 개인 글자 사전이 포함되어 있지 않습니다.
3. 사전이 없다면 노트에서 한 글자의 획들을 선택하고 정답 한 글자를 입력해 학습합니다. 라틴 문자·문장부호의 기준선은 현재 단순 높이 정규화이므로 검수가 필요합니다.
4. **손글씨 쓰기**에서 사전과 PDF를 고르고 내용을 입력합니다.
5. PDF, SDOCX, SVG, JSON을 다운로드합니다. 사전에 없고 조합할 수 없는 글자는 생성 전에 오류로 알려줍니다.

원본과 같은 획 샘플을 재사용하지만 문장 전체를 똑같이 썼다고 보장하지 않습니다. 기존 사전의 품질·라벨 정확도에 영향을 받으며, 합성 한글/기호를 보고서에 구분합니다. 펜 압력·시간이 없는 사전으로 생성한 동역학은 원래 필기자의 실제 기록이 아닙니다.

```powershell
sdocx-bridge --library ./library profile ../my-handwriting/data/glyphs.json
sdocx-bridge --library ./library import note.sdocx paper.pdf
sdocx-bridge --library ./library write "내 손글씨로 쓰는 문장" --seed 7
```

### LaTeX 수식 (선택)

Node.js 18 이상과 npm 또는 pnpm이 필요합니다. **저장소 설치** 기준:

```powershell
npm install --prefix src/handwriting_engine/math --ignore-scripts
sdocx-bridge --library ./library write '회전운동 $\sum \tau = I\alpha$'
```

wheel 설치에서는 설치된 `handwriting_engine/math` 디렉터리에서 npm을 실행하거나, 설치 완료된 `tex2svg.js`의 절대 경로를 `HW_TEX2SVG`로 지정합니다. 수식 없이 쓰기에는 Node가 필요 없습니다. 수식 렌더러는 인수를 직접 전달하며 셸을 실행하지 않고 30초 제한을 적용합니다.

## Claude 연결

```powershell
sdocx-bridge --library "D:/Inkbridge/library" claude-config
```

출력된 JSON을 기존 Claude Desktop의 `mcpServers` 설정에 병합하고 재시작하세요. 기존 설정을 덮어쓰지 마세요. Claude Code와 읽기 전용 설정은 [연결 가이드](docs/CLAUDE.md)를 참고하세요.

> 내 노트 목록을 보고 1쪽의 필기를 읽어줘. 불확실한 글자는 표시하고, 내 손글씨 사전으로 요약을 PDF와 삼성노트 파일로 만들어줘.

MCP 가져오기는 지정한 `library/inbox` 안의 파일만 읽습니다. 생성·내보내기는 `library/exports`에 새 파일을 만듭니다. 노트 속 문장은 모델 지시가 아닌 자료로 취급하도록 도구 설명과 프롬프트에 명시했습니다.

## 검증과 한계

- 개인 검증 자료 27개를 모두 파싱했습니다. 원본/개인 획 사전/녹음은 공개하지 않습니다. 집계와 검사 범위는 [검증 보고서](docs/VALIDATION.md)에 있습니다.
- 자동 검사는 PDF가 이미지 대신 Ink 주석을 사용하는지, 배경 보존, SDOCX 좌표 왕복, 녹음 바이트 일치, 경로 탈출 차단, 실제 MCP stdio 통신을 검사합니다.
- **삼성노트 앱에서 직접 재가져오기·편집하는 기기 검증은 아직 완료하지 않았습니다.** SDOCX는 비공개 형식이며 앱 버전에 따라 호환성이 달라질 수 있습니다.
- PDF 출력에서는 원래 PDF 내용을 유지합니다. **SDOCX 출력의 PDF 배경만** PNG로 변환합니다. 그 위의 손글씨는 독립적인 펜 획입니다.
- 노트 미리보기/SVG는 획 중심입니다. SPI 미리보기 타일, 모든 펜 효과, 도형, 지우개, 음성-획 동기화, 녹음 자동 전사는 지원하지 않습니다. 원본 구조는 `raw/`에 보존합니다.
- PDF/SVG의 선 굵기는 획별 일정합니다. 가져온 원본의 필압·시간 채널은 JSON에 보관하지만 Samsung의 펜 렌더러와 동일한 표현은 아닙니다.
- Markdown/검색은 입력된 텍스트만 사용합니다. 손글씨 OCR을 했다고 표시하지 않습니다. Claude는 페이지 이미지를 보고 읽을 수 있습니다.
- 회전된 PDF는 출력 복사본에서 화면 모양을 유지하며 정규화합니다. 기존 주석·링크를 보존하고, 공간을 초과하면 오류를 내며 글자를 잘라 내지 않습니다. 완성 화면에서 쪽별 배경과 손글씨를 함께 확인할 수 있습니다.
- 비공개 자료 27개 성공이 모든 SDOCX 버전의 호환성이나 다른 프로젝트보다 높은 정확도를 증명하지 않습니다. [기능 비교·출처](docs/COMPARISON.md)를 참조하세요.

## 개발

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m build
```

`src/sdocx_bridge`: 라이브러리·API·UI·MCP. `src/note_pipeline`: MIT Dietrich 기반 파서/작성기. `src/handwriting_engine`: 저장소 소유자의 기존 손글씨 조판 코드. [변경·출처 기록](third_party/NOTICE.md).

AGPL-3.0-only. 포함된 MIT 코드의 라이선스는 유지됩니다. Samsung Notes는 Samsung Electronics의 상표이며 이 프로젝트는 삼성 또는 Anthropic의 공식 제품이 아닙니다.
