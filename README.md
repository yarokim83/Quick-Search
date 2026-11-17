# HPNT Quick Search (Windows · Mac · iPad 통합 업무자료 검색)

## 프로젝트 목적
- **Mac mini(홈 서버 + 외장 SSD 백업/동기화)** ↔ **Google Drive(`HPNT_업무` 원본 저장소)** ↔ **회사 Windows PC 독립 실행 검색앱 & iPad 검색 웹앱**을 연계해 언제든 빠르고 정확하게 장비·자재·수리·도면·보고서 문서를 찾고 원본을 여는 통합 검색 시스템입니다.
- 회사 PC에서는 VPN 없이 Google Drive 데스크톱 폴더(`HPNT_업무`)를 직접 읽어 `127.0.0.1:8765` 로컬 전용으로 동작합니다.

## 주요 기능 (v1.2.0)
- **고급 검색 연산자**: `-제외어`, `A | B` (OR), `"정확한 구문"`, `path:폴더`, `ext:pdf,xlsx`, 도면번호·QC호기·날짜기록 특화 검색
- **3-영역 실무 워크스페이스 UI**:
  - 좌측: 26개 업무 폴더 탐색 / 실시간 결과 패싯 분포 / ⭐ 즐겨찾기 / 🕒 최근 열람
  - 중앙: Everything 스타일 고밀도 표 보기 ↔ 상세 카드 보기 전환, 폴더 브레드크럼 필터링
  - 우측: 문서 인스펙터, 엑셀 시트·이미지 미리보기, PDF 인라인 뷰어, 추출 본문 확인
- **원클릭 로컬 연동**: 제목 더블클릭 / `[원본 열기]` (Windows 기본 프로그램 실행), `[폴더 열기]` (`explorer /select`), `[경로 복사]`

## 디렉터리 구조
```text
Quick Search/
├── app/                   # 검색 서버, 색인 엔진, 웹 UI
│   ├── start.py           # 폴더 선택 및 로컬 데이터(%LOCALAPPDATA%\HPNT_Search_Standalone) 연결
│   ├── server.py          # HTTP API(검색, 패싯, 미리보기, 로컬 열기, 재스캔)
│   ├── catalog.py         # SQLite FTS5 트라이그램 색인 및 스캔 엔진
│   ├── evidence.py        # 작업 기록·월간 보고 기간 근거 추출
│   ├── related.py         # 관련 원본 문서 묶음 처리
│   ├── preview_excel.py   # 엑셀 시트·이미지 간략 미리보기
│   ├── index.html         # 3-영역 통합 검색 웹 UI
│   └── vendor/            # xlrd 등 내장 라이브러리
├── scripts/
│   └── backup_and_sync.py # SQLite·코드 백업 및 실행 폴더 동기화 자동화
├── .agents/rules/         # 에이전트 백업·버전·GitHub 푸시 규칙
├── AGENTS.md / GEMINI.md  # 프로젝트 상시 적용 룰
├── VERSION                # 현재 앱 버전
├── CHANGELOG.md           # 버전별 변경 이력
├── HPNT_Start.cmd         # Windows 실행 런처
└── HPNT_Import_Notes.cmd  # 수동 검색 정보 대조 가져오기(선택)
```

## 보안 및 데이터 관리 원칙
- **업무 자료 외부 업로드 금지**: `verified_notes.json`, `related_documents.json`, `catalog.sqlite`, `windows_config.json`, `runtime/`은 `.gitignore`로 차단되어 GitHub에 절대 업로드되지 않습니다.
- **Google Drive 원본 보호**: 검색앱은 원본 파일을 읽기 전용으로 참조하며, 명시적 요청 없이 Google Drive 원본을 이동·삭제·수정하지 않습니다.

## 백업·동기화·버전업 실행 방법
```bat
runtime\python.exe -X utf8 -B scripts\backup_and_sync.py --backup-only
runtime\python.exe -X utf8 -B scripts\backup_and_sync.py --sync
```
- 상세 릴리스 및 GitHub(`https://github.com/yarokim83/Quick-Search.git`) 푸시 규칙은 [AGENTS.md](./AGENTS.md)와 [version-and-backup.md](./.agents/rules/version-and-backup.md)를 따릅니다.
