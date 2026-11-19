# Changelog

## [1.5.2] - 2026-11-19
### Added
- **전체 파일·메일 목록 원클릭 단축 버튼 및 단축키 (`app/server.py`, `app/index.html`)**:
  - 상단 빠른 필터 바 맨 앞에 **`📂 전체 목록`** 버튼(`#chip-all-files`) 및 키보드 단축키 **`a`** 추가
  - 백엔드 `/api/search`의 `browse_all=1` 파라미터 지원으로 검색어 입력 없이 클릭 한 번에 전체 업무 파일 및 보관 메일 목록을 최신 수정일 순(`mtime_desc`)으로 즉시 조회 가능

## [1.5.1] - 2026-11-19
### Improved
- **PST 변환 결과 자동 복원 및 중복 제거 고도화 (`app/mail_archive.py`)**:
  - 다른 PC 설치나 캐시 초기화 시에도 `추가메일/*/검색레코드.json`, `PST_메일정리/*/검색레코드.json`, `PST_메일보관/*/검색레코드.json`을 자동 감지해 `imported_mails`로 자동 복원 (`imported_manifests` 추적)
  - 기존 `40,360`건 보관 메일(`mails` 테이블)과 `(날짜, 제목, 발신자, 첨부파일명)` 기준 교차 중복 제거 적용
- **PST 추출 포맷 및 첨부 메일 처리 보강 (`app/pst_worker.cjs`)**:
  - `본문.txt` 및 검색 본문(`bodyText`)에 **`수신(To)`·`참조(CC)`** 헤더 포함
  - 탐색기 가독성을 위해 메일 폴더명을 `<YYYYMMDD_제목요약_ID>` 형식으로 변경 (Windows 260자 경로 안전 길이 자동 조절)
  - 전달된 첨부 메일(`EMBEDDED_MSG`, `embeddedPSTMessage`) 본문 및 내부 첨부파일 재귀 추출(최대 깊이 3) 지원
- **변환 작업 중단 및 임시 파일 자동 정리 (`app/pst_converter.py`, `app/server.py`, `app/index.html`)**:
  - 변환 진행 중 **`[⏹ 변환 중단]`** 버튼(`POST /api/pst/cancel`) 추가
  - 드래그 앤 드롭 업로드용 임시 스테이징 파일(`pst_staging/*.pst`)을 변환 완료·중단·오류 시 자동 정리

## [1.5.0] - 2026-11-18
### Added
- **Outlook `.pst` 파일 드래그 앤 드롭 · 탐색기 선택 자동 변환 및 통합 검색 연동 (`app/pst_converter.py`, `app/pst_worker.cjs`, `app/mail_archive.py`, `app/server.py`, `app/index.html`)**:
  - 상단 **`📥 PST 메일 변환·추가`** 메뉴와 화면 전체 `.pst` 드래그 앤 드롭 오버레이, Windows 네이티브 파일 선택 창(`GetOpenFileNameW`) 및 로컬 파일 자동 매칭·스트리밍 업로드를 지원
  - 경량화된 `pst-extractor` 런타임(`app/vendor/pst_node`)을 내장해 Google Drive I/O 병목 없이 폴더·메일 건수 사전 검사(`--inspect`) 및 기존 메일 보관함 포맷(`<연도>/<폴더명>/<메일ID>/본문.txt`, `HTML원문.txt`, `메일정보.json`, SHA-256 검증 첨부파일) 자동 추출(`--convert`) 제공
  - `mails_cache.sqlite`의 `imported_mails` 테이블에 SHA-256 지문(`fingerprint`) 기반 중복 제외 후 즉시 등록되어 별도 재시작 없이 `✉️ 메일자료` 통합 검색·미리보기·첨부파일 열기 지원
  - 변환 위치 선택(`통합 메일 보관함` 또는 `HPNT_업무\PST_메일정리` 업무 폴더), 실시간 진행률·연도별 집계 표시, 완료 후 `[📂 변환된 폴더 열기]` 및 `[🔍 변환된 메일 바로 검색]` 제공
- **배포/동기화 스크립트 보강 (`scripts/backup_and_sync.py`, `scripts/build_windows_dist.py`)**:
  - `pst_converter.py`, `pst_worker.cjs` 및 `app/vendor` 동기화·ZIP 무결성 검증 항목 추가

## [1.4.9] - 2026-11-18
### Changed
- **백엔드 인메모리 검색 엔진 고속화 및 I/O 병목 제거 (`app/server.py`, `app/mail_archive.py`, `app/evidence.py`, `app/related.py`)**:
  - `qc107` 등 장비번호·길이·도면번호 정규식 검색 전에 필수 부분 문자열(`required_substrs`) 사전 필터링 및 단일/다중 검색어 제너레이터 할당 제거로 `qc107` 검색 지연을 `1127.5ms` → `97.3ms`로 대폭 단축
  - `get_mail_cache(force_check=False)`가 일반 검색 및 `/api/status` 폴링 중 Google Drive(`메일검색.html`) `.stat()` 호출 없이 메모리 및 로컬 `mails_cache.sqlite`에서 즉시 로드되도록 개선하고, 첨부파일 확장자/UI 메타(`attachments_ui`, `status_label`, `top_folder`)를 캐시 생성 시 사전 계산
  - `_CATALOG_CACHE`(1.5초 TTL)에 `chunks_by_doc` 인메모리 캐시를 통합하고 `_load_groups_by_path()`에 2.0초 TTL을 적용해 검색 중 불필요한 SQLite 반복 쿼리 및 파일 `stat()` 호출 제거
  - `evidence.py`에 보고서 기간 질의 사전 판별(`parse_report_query`) 및 정규식 프리컴파일, `server.py`에 조기 종료 횟수 집계(`_count_capped3`) 적용
- **실시간 타이핑 반응성(IME 조합·디바운스·캐시·렌더링) 개선 (`app/index.html`)**:
  - 한글 입력 조합 중 끝 자모 보정 기준(`normalizeLiveQuery`)으로 동일 검색어(`교` → `교ㅊ`) 입력 시 진행 중인 요청을 강제 취소(`abort`)하거나 중복 요청하지 않도록 개선하고 디바운스 지연 시간을 `45ms` → `15ms`로 단축
  - 클라이언트 인메모리 결과 캐시(`searchCache`, 최대 40개, 스캔 갱신 시 자동 무효화)를 추가해 백스페이스/재입력 시 즉시 렌더링
  - 즐겨찾기 `Set`(`favIdSet`) `O(1)` 조회, 하이라이팅 정규식 단일 컴파일(`buildHighlightRegex`), 폴더 브레드크럼 이벤트 위임 적용으로 결과 목록 DOM 렌더링 지연 최소화

## [1.4.8] - 2026-11-18
### Added
- **모든 업무 폴더(빈 폴더 포함 5,713개) 색인 및 검색 지원 (`app/catalog.py`, `app/server.py`, `app/index.html`)**:
  - `folders` 테이블 추가 및 스캔 연동으로 `OOG Twistlock pin Set`처럼 내부 파일이 없는 빈 폴더를 포함한 모든 폴더를 검색 결과(`📁 폴더`, `ext:folder`)로 조회 가능
  - 검색 결과 및 인스펙터에서 폴더를 바로 탐색기로 열기(`[열기]`) 또는 앱 내 하위 폴더 탐색(`[탐색]`/`[앱 안에서 내부 보기]`) 지원
- **폴더 필터 제한 안내 배너 및 자동 해제 (`app/server.py`, `app/index.html`)**:
  - 특정 폴더 필터(`state.folder`)가 켜진 상태에서 검색할 때 다른 폴더의 일치 개수(`outside_folder_total`, `global_total`)를 계산해 결과 최상단에 안내 배너 및 `[전체 HPNT_업무 결과 모두 보기]` 버튼 표시
  - 검색창 지우기(`×`) 및 최근 검색어 클릭 시 기존 폴더 필터를 자동 해제해 새 검색 시 누락 착오 방지

## [1.4.7] - 2026-11-17
### Fixed
- **바탕화면 `HPNT 업무검색.exe` 중복 아이콘 자동 생성 제거 (`scripts/standalone_sfx.cs`, `scripts/build_windows_dist.py`)**:
  - `HPNT_Search_Windows_v*.exe` 실행 시 바탕화면에 별도 `HPNT 업무검색.exe`(구형 캐시 작은 아이콘)를 복사·생성하던 동작을 제거
  - 검색 서버가 이미 실행 중일 때 실행 시 경고 창 대신 잠기지 않은 최신 파일을 조용히 반영하고 브라우저(`http://127.0.0.1:8765/`)를 바로 열도록 개선
  - 빌드 시 바탕화면에 남아 있던 기존 `HPNT 업무검색.exe`를 정리해 단일 실행 아이콘(`HPNT_Search_Windows_v*.exe`)만 유지

## [1.4.6] - 2026-11-17
### Changed
- **애플 스타일 풀사이즈 스쿼클(Squircle) 아이콘 및 6종 멀티 해상도 `.ico` 적용 (`scripts/build_windows_dist.py`, `app/icon-*.png`)**:
  - 기존 단색 직각 PNG(`180×180` 단일 프레임) 대신 투명 알파 채널과 애플 스타일 연속 곡률(Superellipse 스쿼클) 및 내부 심볼(문서·돋보기)을 확대한 풀블리드 아이콘(`512×512`, `192×192`, `180×180`) 생성
  - Windows 탐색기가 바탕화면 아이콘 슬롯을 꽉 채워 렌더링하도록 표준 **`256×256` 포함 6개 해상도(`256`, `128`, `64`, `48`, `32`, `16`)** 멀티 프레임 `app/hpnt_icon.ico` 빌드 적용
- **바탕화면 실행 파일 무중단 교체 및 아이콘 캐시 즉시 갱신 (`scripts/build_windows_dist.py`)**: 트레이에서 `HPNT 업무검색.exe`가 실행 중일 때도 임시 스테이징 이동으로 안전 교체 후 `SHChangeNotify`로 Windows 아이콘 캐시 즉시 갱신

## [1.4.5] - 2026-11-17
### Added
- **`🚫 메일 자료 제외` 퀵 필터 버튼 (`app/index.html`, `app/server.py`)**: 상단 검색창 아래 `✉️ 메일 자료만` 버튼 옆에 `🚫 메일 자료 제외` 토글 버튼을 추가하여 클릭 한 번으로 과거 Outlook 보관 메일(`40,360`건)을 제외하고 업무 파일만 빠르게 조회·탐색 지원
- **백엔드 `exclude_mail` 필터링 및 상호배타 연동 (`app/server.py`, `app/index.html`)**: `/api/search`의 `exclude_mail=1` 파라미터 및 `-메일보관/` 키워드 자동 제외를 지원하며, `메일 자료만`/`메일보관` 폴더 필터와 상호배타 전환 및 결과 헤더 브레드크럼(`메일 자료 제외 ×`) 표시

## [1.4.4] - 2026-11-17
### Changed
- **상단 검색 영역 2단 슬림화 (`app/index.html`)**: 검색창과 필터 드롭다운(`유형`, `형식`, `정렬`, `표/카드 보기`)을 한 줄로 통합하고, 빠른 연산자 칩과 최근 검색어를 한 줄에 나란히 배치하여 상단 높이를 대폭 축소
- **경로 탐색(Breadcrumb) 인라인 통합**: 독립 바를 제거하고 결과 헤더 내 인라인으로 통합(필터 미적용 시 자동 숨김)
- **3-Pane 폭 재배분 및 테이블 고밀도화**: 좌측 사이드바(`195px`)·우측 인스펙터(`350px`) 폭 축소로 가운데 목록 가용 폭 확대, `table-layout: fixed` 고정 컬럼 폭 및 1줄 말줄임(툴팁 제공) 적용으로 크기·수정일·실행 버튼 잘림 방지
- **검색 근거 중복 문구 생략**: `"검색어가 파일명 또는 경로에 있습니다."` 등 불필요한 2줄 설명을 `파일명·경로` 1줄로 정리해 행 높이를 슬림화(`~38px`)하고 한 화면에 표시되는 파일 목록 수 확대
- `scripts/build_windows_dist.py`: 바탕화면 `HPNT 업무검색.exe`가 트레이에서 실행 중일 때 `PermissionError`로 빌드가 중단되지 않도록 예외 처리 및 이전 배포 번들 정리 추가

## [1.4.3] - 2026-11-17
### Changed
- `scripts/launcher.cs`, `scripts/standalone_sfx.cs`, `scripts/build_windows_dist.py`: Windows GUI 서브시스템(`/target:winexe`) 및 `runtime\pythonw.exe` 전환으로 검은 터미널 창 없이 백그라운드 실행
- **시스템 트레이 상주 지원**: 우측 하단 Windows 트레이 아이콘 더블클릭(검색 화면 열기) 및 우클릭 메뉴(`검색 화면 열기`, `지금 목록 갱신`, `검색 서버 종료`) 추가
- `app/start.py`, `app/server.py`: 무콘솔 환경 로그(`server.log`), 엑셀 미리보기 `CREATE_NO_WINDOW`, `ShellExecuteW(..., SW_SHOWNORMAL)` 전면 창 열기 보강

## [1.4.2] - 2026-11-17
### Added
- `scripts/launcher.cs`, `HPNT_Search.exe`: 네이티브 Windows 64비트 `.exe` 런처 추가 (중복 실행 시 브라우저만 새로 열기 지원, 아이콘 내장)
- `scripts/standalone_sfx.cs`, `scripts/build_windows_dist.py`: 파일 1개만 복사해 실행하면 자동 압축 해제·설치·실행되는 단일 배포 실행 파일(`HPNT_Search_Windows_v<VERSION>.exe`) 빌드 지원

## [1.4.1] - 2026-11-17
### Added
- `scripts/build_windows_dist.py`: Windows 64비트 독립 실행 배포 패키지(`HPNT_Search_Windows_v<VERSION>.zip`) 자동 빌드 및 무결성(`testzip`) 검증 스크립트 추가
- `사용안내.txt`: `v1.4.1` 통합 검색·실시간 타이핑·메일 검색 기능 기준으로 최신화
- `.gitignore`, `app/catalog.py`: `dist/`, `backups/`, `scripts/`, `*.zip` 제외 규칙 보강

## [1.4.0] - 2026-11-17
### Added
- **과거 Outlook 보관 메일 통합 검색 (`app/mail_archive.py`, `app/server.py`)**:
  - Google Drive `메일보관_백업_2026-10-31\메일검색.html`의 `40,360`건 메일과 `54,381`건 첨부파일을 로컬 SQLite 캐시(`mails_cache.sqlite`)와 인메모리 캐시에 연동해 업무 파일(`46,344`건)과 함께 실시간 통합 검색 지원
  - 메일 전용 필터(`category=메일자료`, `ext=mail`), 연도별 폴더 탐색(`메일보관/2024`), 첨부파일 확장자 교차 필터링 지원
  - 메일 본문 상세 조회(`/api/detail`), 메일 본문·첨부파일 로컬 열기/탐색기 열기(`/api/open-local`, `/api/open-folder`), 첨부 엑셀·PDF 인라인 미리보기 지원
- **메일 자료 전용 UI/UX (`app/index.html`)**:
  - 결과 목록과 카드에 `✉️ 메일자료` 배지, 발신자, 일시, `📎 첨부 N개` 배지 표시
  - 상단 `[✉️ 메일 자료만]` 퀵 필터 토글 및 `파일 수 · 메일 수` 통합 상태 바 표시
  - 우측 인스펙터에서 메일 발신 정보, 본문 전체, 첨부파일별 열기/폴더/다운로드/엑셀·PDF 미리보기 제공

## [1.3.2] - 2026-11-17
### Changed
- `app/index.html`: 최근 검색어(`#recent-row`) 아래의 예시 키워드 섹션 제거로 상단 검색 영역 간소화

## [1.3.1] - 2026-11-17
### Fixed
- `app/server.py`: 실시간 타이핑 중 브라우저의 `AbortController.abort()`가 이전 요청을 취소할 때 발생하는 Windows 소켓 `ConnectionAbortedError` (`[WinError 10053]`)를 `send()`, `do_POST()`, `do_GET()`에서 안전하게 무시해 콘솔 트레이스백 출력 제거

## [1.3.0] - 2026-11-17
### Added
- **Everything 스타일 실시간 타이핑 즉시 검색 (`app/server.py`, `app/index.html`)**:
  - 인메모리 카탈로그 캐시(`get_catalog_cache`) 및 `warm_cache()` 구축으로 46,344건 전체 검색을 수 밀리초(3~20ms)대로 단축
  - 1글자 검색 허용(`감`, `j`, `1` 등 첫 글자 입력 즉시 결과 표시) 및 1글자 하이라이팅 지원
  - 한글 입력 조합 중 미완성 끝 자모 보정(`strip_trailing_jamo`: `감속ㄱ` → `감속`, 단독 자모 토큰 무시)으로 타이핑 도중 결과 깜빡임 제거
  - `q` 입력 시 `45ms` 디바운스 + `AbortController` 이전 요청 자동 취소, 최근 검색어 저장 시점 최적화(Enter/제출/문서 열람 시에만 저장)
- **관련 문서 캐시 최적화 (`app/related.py`)**:
  - 매 검색마다 반복되던 `related_documents.json` 읽기 및 파일 `stat()` 호출을 DB 상태 시그니처 기준 인메모리 캐싱으로 전환

## [1.2.0] - 2026-11-17
### Added
- **상용 검색 툴 스타일 고급 연산자 (`app/server.py`)**: `-제외어`/`!제외어`, `A | B` / `A OR B` 병렬 검색, `"정확한 구문"`, `path:폴더`, `ext:pdf,xlsx` 지원
- **폴더·조건 탐색(Browse) 모드 및 페이징**: 검색어 없이도 `/api/folders` 26개 업무 폴더 및 필터 탐색, `offset`/`limit` 기반 더 보기 지원
- **실시간 패싯(Facet) 집계**: 결과 내 폴더별·하위폴더별·확장자별·자료유형별 건수 집계 및 Drill-down
- **3-영역 워크스페이스 UI/UX (`app/index.html`)**:
  - 좌측 사이드바 (폴더 탐색/패싯 분포, ⭐ 즐겨찾기, 🕒 최근 열람)
  - 중앙 결과 영역 (표 보기 ↔ 카드 보기 전환, 경로 브레드크럼 클릭 필터, 퀵 연산자 칩)
  - 우측 인스펙터 패널 (원본 열기, 폴더 열기, 경로 복사, 추출 본문, 엑셀 시트 & PDF 인라인 미리보기)
  - 키보드 단축키 (`/`, `Ctrl+K`, `↑`/`↓`, `Enter`, `v`, `Esc`)
- **백업·버전업·GitHub 자동 연동 체계**: `AGENTS.md`, `GEMINI.md`, `.agents/rules/version-and-backup.md`, `scripts/backup_and_sync.py`, `VERSION`, `CHANGELOG.md` 구축

## [1.1.0] - 2026-11-16
### Added
- `app/server.py`: 파일 형식 필터(`ext`), 정렬(`sort`), 탐색기 위치 열기(`POST /api/open-folder`), 수동 목록 갱신(`POST /api/rescan`), PDF 인라인 미리보기 CSP(`frame-ancestors 'self'`)
- `app/index.html`: 상단 실시간 상태 요약 바, 최근 검색어, 검색어 `<mark>` 하이라이팅, 액션 버튼 바, 인라인 PDF/엑셀 미리보기
- `HPNT_Start.cmd`: ASCII 안전 스크립트 및 로컬 런타임 폴백 경로 지원

### Fixed
- `app/catalog.py`: `desktop.ini`, `~$` Office 잠금 파일, 검색앱 패키지 폴더 스캔 제외 및 기존 오염 레코드 정리(`6,765`건), 변경 없는 재스캔 쓰기 최적화
- `app/related.py`: macOS-Windows 간 나노초 `mtime` 반올림 오차(`<= 2ms`) 허용으로 관련 문서 묶음 매칭 복구
- `import_verified_notes.py`: 수동 검색 정보 `138`건 SHA-256 해시 대조 및 반영 완료

## [1.0.0] - 2026-11-16
### Added
- Windows 64비트 독립 실행판 초기 이관 (`start.py`, `server.py`, `catalog.py`, `evidence.py`, `related.py`, `preview_excel.py`, `index.html`)
- 검색 결과 제목 더블클릭 및 Enter 키를 통한 로컬 기본 프로그램 원본 열기(`/api/open-local`)
