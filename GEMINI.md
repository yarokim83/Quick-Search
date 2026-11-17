# HPNT Quick Search — 프로젝트 지침 (GEMINI)

이 프로젝트에서 코드를 수정하거나 기능을 업데이트할 때는 항상 아래 백업·버전업·GitHub 푸시 규칙을 자동으로 따릅니다.

1. **수정 전 백업**: 변경 작업 전 `runtime\python.exe -X utf8 -B scripts\backup_and_sync.py --backup-only`를 실행하여 `%LOCALAPPDATA%\HPNT_Search_Standalone\backups\`에 DB 백업을, `backups\`에 코드 백업을 생성한다.
2. **버전업 및 이력 기록**: 모든 업데이트마다 `VERSION` (`MAJOR.MINOR.PATCH`)을 증가시키고 `CHANGELOG.md`에 변경 사항을 추가한다.
3. **검증 후 실행 폴더 동기화**: `py_compile` 및 테스트 통과 후 `runtime\python.exe -X utf8 -B scripts\backup_and_sync.py --sync`로 바탕화면 및 Google Drive 배포 폴더에 반영한다.
4. **보안 점검 후 GitHub 푸시**: `git status --short`로 `verified_notes.json`, `related_documents.json`, `*.sqlite`, `runtime/`, `backups/`가 `.gitignore`에 의해 제외되었는지 확인한 뒤 `git commit -m "v<VERSION>: <요약>"`, `git tag v<VERSION>`, `git push origin main --tags`로 `https://github.com/yarokim83/Quick-Search.git`에 업로드한다.
5. **업무 원본 보호**: `G:\내 드라이브\HPNT_업무`의 업무 원본 문서와 수동 노트 파일은 명시적 요청 없이 이동·삭제·외부 전송하지 않는다.

상세 절차는 [AGENTS.md](./AGENTS.md) 및 [.agents/rules/version-and-backup.md](./.agents/rules/version-and-backup.md)를 참조하세요.
