# HPNT Quick Search — 에이전트 필수 작업 규칙 (AGENTS)

이 워크스페이스(`c:\Users\baewoong.kim\.gemini\Quick Search`)에서 코드, 설정, UI, 스크립트를 수정하거나 업데이트할 때 에이전트는 매 작업마다 아래 절차를 **반드시 순서대로** 준수해야 합니다.

## 1. 작업 전 안전 백업 (필수)
1. 코드 또는 DB 스키마를 수정하기 전, 아래 명령으로 SQLite DB 일관성 백업과 현재 코드 스냅샷 백업을 생성한다.
   ```bat
   runtime\python.exe -X utf8 -B scripts\backup_and_sync.py --backup-only
   ```
2. `G:\내 드라이브\HPNT_업무` 내부의 원본 업무 파일은 명시적 요청 없이 절대 이동·삭제·이름 변경·수정하지 않는다.

## 2. 버전업 및 변경 이력 관리 (필수)
모든 기능 업데이트·버그 수정·UI/UX 개선 완료 시 반드시 아래 두 파일을 함께 갱신한다.
- **`VERSION`**: Semantic Versioning(`MAJOR.MINOR.PATCH`) 규칙에 따라 버전을 올린다.
  - 구조 개편 / 호환되지 않는 변경: `MAJOR` (`x.0.0`)
  - 새로운 기능 추가 / 검색 연산자·UI 주요 개선: `MINOR` (`1.x.0`)
  - 버그 수정 / 소규모 UI·텍스트 패치: `PATCH` (`1.2.x`)
- **`CHANGELOG.md`**: 새 버전 헤더(`## [x.y.z] - YYYY-MM-DD`) 아래에 `Added` / `Changed` / `Fixed` 항목을 구체적으로 기록한다.

## 3. 검증 및 실행 폴더 동기화 (필수)
1. 수정된 모든 Python 파일의 구문 검사(`py_compile`)와 관련 기능/HTTP 통합 테스트를 통과한다.
2. 아래 명령으로 바탕화면 실행 폴더(`Desktop\HPNT_업무검색`) 및 Google Drive 배포 패키지(`HPNT_Search_Windows_DoubleClick`)에 검증된 앱 파일을 동기화한다.
   ```bat
   runtime\python.exe -X utf8 -B scripts\backup_and_sync.py --sync
   ```

## 4. 보안 검증 및 GitHub 원격 저장소 푸시 (필수)
업데이트 검증이 끝나면 반드시 원격 저장소(`https://github.com/yarokim83/Quick-Search.git`, 브랜치 `main`)에 커밋과 버전 태그를 푸시한다.

1. **민감 데이터 차단 확인**: `git status --short`로 아래 파일·폴더가 스테이징에 절대 포함되지 않았는지 확인한다.
   - `app/verified_notes.json`, `app/related_documents.json`
   - `*.sqlite`, `*.sqlite-shm`, `*.sqlite-wal`, `*.db`, `windows_config.json`
   - `runtime/`, `backups/`, `app_backup_*/`
2. **커밋·태그·푸시**:
   ```bash
   git add -A
   git status --short
   git commit -m "v<VERSION>: <변경 핵심 요약>"
   git tag v<VERSION>
   git push origin main --tags
   ```
3. 인증 실패 등 원격 푸시 오류가 발생하면 우회하지 말고 사용자에게 정확한 오류와 필요 조치를 보고한다.
