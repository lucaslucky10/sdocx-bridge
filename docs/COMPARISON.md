# 비교 범위와 재사용 출처

2026-10-15 공개 저장소와 README를 확인했습니다. 이 표는 기능 범위 비교이며 성능 순위나 전수 호환성 벤치마크가 아닙니다.

| 프로젝트 | 확인한 중심 기능 | Inkbridge의 추가 작업 흐름 |
|---|---|---|
| [twangodev/sdocx](https://github.com/twangodev/sdocx) | Rust/WASM 파서·SVG/PNG/PDF 변환, 필압 등 | 개인 글자 사전으로 새 텍스트 생성, native SDOCX 쓰기, 쓰기 MCP |
| [Mhemd139/Samsung_Notes (Inkport)](https://github.com/Mhemd139/Samsung_Notes) | 웹 노트 읽기·AI 전달·읽기 전용 MCP | 글자 라벨링과 개인 획 재조합, PDF Ink, 생성 MCP |
| [Downhillghost/Dietrich](https://github.com/Downhillghost/Dietrich) | SDOCX 읽기/쓰기·Excalidraw·PNG | 실제 재사용 기반. 안전한 로컬 보관함, 녹음 해시 추출, 사용자 UI, MCP, 손글씨 조판 추가 |
| [bxff/samsung-notes-format](https://github.com/bxff/samsung-notes-format) | 획 형식 연구·SVG 렌더링 | 통합 앱·녹음·학습·생성·MCP |
| [0xA01F3/sdocx_parser](https://github.com/0xA01F3/sdocx_parser) | 입력된 텍스트와 서식 추출 | 펜 획 기반 읽기·개인 손글씨 출력 |

기존 파서들의 전체 렌더링 품질을 능가한다고 주장하지 않습니다. Inkbridge는 노트 → 개인 획 사전 → PDF/SDOCX → AI 도구의 연결 범위를 넓히는 데 집중합니다. v0.2에서 확인한 범위에서는 위 프로젝트 README에 없는 기능으로 **기존 .sdocx에 획을 추가하면서 무결성 해시를 다시 계산하는 쓰기**, **녹음 시작 시각과 획 작성 시각의 정렬**, **로컬 받아쓰기와 필기 누락 구간 표시**를 더했습니다. SPI 타일 디코딩이나 펜 효과 등에서는 다른 도구가 더 완전할 수 있습니다.

Dietrich 작성기에서 확인한 **좌표 양자화 누적 오차**는 수정했습니다. 원래 이전 입력 좌표를 기준으로 델타를 만들던 코드를, 실제 복원될 좌표 기준으로 바꾸었습니다. 1,200점 합성 획 회귀 테스트에서 좌표별 오차 ≤ 1/64 page unit, 필압 오차 ≤ 1/(64×128)을 검사합니다. 이것은 해당 인코더의 개선이며 문서 전체 정확도 우위의 증거는 아닙니다.

## 라이선스와 범위

Dietrich의 MIT `note_pipeline` 코드만 소스에 포함했습니다. 나머지 공개 저장소는 비교·문서 참고용이며 GPL 코드를 무단으로 MIT에 재라이선스하지 않았습니다. 전체 앱은 PyMuPDF 의존성과 함께 AGPL-3.0-only로 배포합니다.

기존 `lucaslucky10/my-handwriting` 소유자가 명시적으로 요청한 새 프로젝트로 해당 조판 코드를 재사용했습니다. 개인 글자 사전, 라벨, 예제 필기, 오디오, 원본 파일은 배포 대상에서 제외했습니다. 누구나 자신의 사전을 가져와 사용할 수 있습니다.
