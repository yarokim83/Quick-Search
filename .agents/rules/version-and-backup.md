# Backup, Versioning, and GitHub Release Rule

Every update to `Quick Search` MUST complete these steps in order:

1. **Pre-change Backup**
   - Run `runtime\python.exe -X utf8 -B scripts\backup_and_sync.py --backup-only` before editing code or DB schemas.
2. **Version Bump & Changelog**
   - Increment `VERSION` (`MAJOR.MINOR.PATCH`):
     - Feature / UI / search capability additions -> bump `MINOR` (e.g. `1.2.0` -> `1.3.0`).
     - Bug fixes / minor tweaks -> bump `PATCH` (e.g. `1.2.0` -> `1.2.1`).
   - Prepend the release notes to `CHANGELOG.md`.
3. **Verification & Sync**
   - Run `py_compile` and integration checks after the final edit.
   - Run `runtime\python.exe -X utf8 -B scripts\backup_and_sync.py --sync` to update the Desktop (`Desktop\HPNT_업무검색`) and Google Drive (`HPNT_Search_Windows_DoubleClick`) app bundles.
4. **Security Audit & Push to `https://github.com/yarokim83/Quick-Search.git`**
   - Verify via `git status --short` that `app/verified_notes.json`, `app/related_documents.json`, `*.sqlite*`, `windows_config.json`, `runtime/`, and `backups/` are NOT tracked or staged.
   - Commit with `v<VERSION>: <summary>`, create annotated/lightweight tag `v<VERSION>`, and run `git push origin main --tags`.
