"""Build a standalone Windows 64-bit distribution ZIP package for HPNT Quick Search."""

import os
import shutil
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / 'dist'
PACKAGE_ROOT = 'HPNT_Search_Windows_DoubleClick'

ROOT_FILES = (
    'HPNT_Start.cmd',
    'HPNT_Import_Notes.cmd',
    '사용안내.txt',
    '구성요소_출처.txt',
    'VERSION',
    'CHANGELOG.md',
)
SKIP_DIRS = frozenset(('__pycache__', '.git', '.agents', 'backups', 'dist'))
SKIP_FILES = frozenset(('desktop.ini', 'thumbs.db'))


def read_version():
    ver = ROOT_DIR / 'VERSION'
    return ver.read_text(encoding='utf-8').strip() if ver.is_file() else '0.0.0'


def should_skip_file(name):
    low = name.lower()
    return low in SKIP_FILES or low.endswith(('.pyc', '.pyo')) or name.startswith('~$')


def add_dir_to_zip(zf, src_dir, zip_prefix):
    count = 0
    for dp, ds, fs in os.walk(src_dir):
        ds[:] = sorted(d for d in ds if d not in SKIP_DIRS and not d.startswith('.'))
        dp_path = Path(dp)
        for fn in sorted(fs):
            if should_skip_file(fn):
                continue
            full = dp_path / fn
            rel = full.relative_to(src_dir).as_posix()
            zf.write(full, f'{zip_prefix}/{rel}')
            count += 1
    return count


def build_zip():
    version = read_version()
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_name = f'HPNT_Search_Windows_v{version}.zip'
    dist_zip = DIST_DIR / zip_name

    if not (ROOT_DIR / 'runtime' / 'python.exe').is_file():
        raise FileNotFoundError('runtime/python.exe not found; cannot build standalone package.')

    added = 0
    with zipfile.ZipFile(dist_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fname in ROOT_FILES:
            fpath = ROOT_DIR / fname
            if fpath.is_file():
                zf.write(fpath, f'{PACKAGE_ROOT}/{fname}')
                added += 1
        added += add_dir_to_zip(zf, ROOT_DIR / 'app', f'{PACKAGE_ROOT}/app')
        added += add_dir_to_zip(zf, ROOT_DIR / 'runtime', f'{PACKAGE_ROOT}/runtime')

    with zipfile.ZipFile(dist_zip, 'r') as zf:
        bad = zf.testzip()
        if bad is not None:
            raise RuntimeError(f'ZIP integrity check failed at: {bad}')
        names = set(zf.namelist())
        required = (
            f'{PACKAGE_ROOT}/HPNT_Start.cmd',
            f'{PACKAGE_ROOT}/runtime/python.exe',
            f'{PACKAGE_ROOT}/app/start.py',
            f'{PACKAGE_ROOT}/app/server.py',
            f'{PACKAGE_ROOT}/app/catalog.py',
            f'{PACKAGE_ROOT}/app/mail_archive.py',
            f'{PACKAGE_ROOT}/app/index.html',
        )
        missing = [r for r in required if r not in names]
        if missing:
            raise RuntimeError(f'Missing required files in ZIP: {missing}')

    size_mb = dist_zip.stat().st_size / (1024 * 1024)
    print(f'[Build] Created & verified: {dist_zip} ({added} files, {size_mb:.2f} MB)')

    copy_targets = (
        Path.home() / 'Desktop' / zip_name,
        Path(r'G:\내 드라이브\HPNT_업무') / zip_name,
    )
    for target in copy_targets:
        if target.parent.is_dir():
            shutil.copy2(dist_zip, target)
            print(f'[Copy] Saved to: {target}')
        else:
            print(f'[Copy] Skipped (parent missing): {target.parent}')
    return dist_zip


if __name__ == '__main__':
    build_zip()
