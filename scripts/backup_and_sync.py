"""Backup catalog DB & source snapshot, and sync verified app files to runtime locations."""

import argparse
import datetime
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = ROOT_DIR / 'app'
LOCAL_DATA_DIR = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'HPNT_Search_Standalone'
DB_FILE = LOCAL_DATA_DIR / 'catalog.sqlite'
DB_BACKUP_DIR = LOCAL_DATA_DIR / 'backups'
CODE_BACKUP_DIR = ROOT_DIR / 'backups'
RUNTIME_PY = ROOT_DIR / 'runtime' / 'python.exe'

SYNC_APP_FILES = (
    'start.py',
    'server.py',
    'catalog.py',
    'mail_archive.py',
    'evidence.py',
    'related.py',
    'preview_excel.py',
    'import_verified_notes.py',
    'index.html',
    'manifest.webmanifest',
    'icon-180.png',
    'icon-192.png',
    'icon-512.png',
)
SYNC_ROOT_FILES = ('HPNT_Start.cmd', 'HPNT_Import_Notes.cmd', '사용안내.txt', 'VERSION', 'CHANGELOG.md')
SYNC_TARGETS = (
    Path.home() / 'Desktop' / 'HPNT_업무검색',
    Path(r'G:\내 드라이브\HPNT_업무\HPNT_검색_Windows_제목열기_업데이트\HPNT_Search_Windows_DoubleClick'),
)


def read_version():
    ver_file = ROOT_DIR / 'VERSION'
    if ver_file.is_file():
        return ver_file.read_text(encoding='utf-8').strip() or '0.0.0'
    return '0.0.0'


def backup_sqlite(version, stamp):
    if not DB_FILE.is_file():
        print(f'[DB Backup] Skipped (not found): {DB_FILE}')
        return None
    DB_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dst = DB_BACKUP_DIR / f'catalog_v{version}_{stamp}.sqlite'
    src_conn = sqlite3.connect(f'file:{DB_FILE}?mode=ro', uri=True, timeout=30)
    dst_conn = sqlite3.connect(dst)
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    print(f'[DB Backup] Created: {dst} ({dst.stat().st_size:,} bytes)')
    return dst


def backup_code(version, stamp):
    dest = CODE_BACKUP_DIR / f'code_v{version}_{stamp}'
    (dest / 'app').mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in SYNC_APP_FILES:
        src = APP_DIR / name
        if src.is_file():
            shutil.copy2(src, dest / 'app' / name)
            copied += 1
    for name in (*SYNC_ROOT_FILES, 'README.md', 'AGENTS.md', 'GEMINI.md', '.gitignore'):
        src = ROOT_DIR / name
        if src.is_file():
            shutil.copy2(src, dest / name)
            copied += 1
    scripts_dir = ROOT_DIR / 'scripts'
    if scripts_dir.is_dir():
        (dest / 'scripts').mkdir(parents=True, exist_ok=True)
        for py_file in scripts_dir.glob('*.py'):
            shutil.copy2(py_file, dest / 'scripts' / py_file.name)
            copied += 1
    print(f'[Code Backup] Created: {dest} ({copied} files)')
    return dest


def sync_targets():
    py_exe = str(RUNTIME_PY if RUNTIME_PY.is_file() else Path(sys.executable))
    for target in SYNC_TARGETS:
        if not target.is_dir():
            print(f'[Sync] Skipped (folder missing): {target}')
            continue
        target_app = target / 'app'
        target_app.mkdir(parents=True, exist_ok=True)
        for name in SYNC_APP_FILES:
            src = APP_DIR / name
            if src.is_file():
                shutil.copy2(src, target_app / name)
        for name in SYNC_ROOT_FILES:
            src = ROOT_DIR / name
            if src.is_file():
                shutil.copy2(src, target / name)

        py_targets = [str(target_app / f) for f in SYNC_APP_FILES if f.endswith('.py') and (target_app / f).is_file()]
        subprocess.run([py_exe, '-m', 'py_compile', *py_targets], check=True)
        print(f'[Sync] Updated & py_compile PASSED: {target}')


def main():
    parser = argparse.ArgumentParser(description='Backup HPNT Search DB/code and sync runtime packages.')
    parser.add_argument('--backup-only', action='store_true', help='Only create DB and code backups.')
    parser.add_argument('--sync', action='store_true', help='Only sync app files to Desktop/Google Drive targets.')
    args = parser.parse_args()

    version = read_version()
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

    if not args.sync:
        backup_sqlite(version, stamp)
        backup_code(version, stamp)
    if not args.backup_only:
        sync_targets()


if __name__ == '__main__':
    main()
