"""Build native Windows EXE launcher, standalone ZIP, and single-file SFX EXE distributor."""

import os
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT_DIR / 'dist'
PACKAGE_ROOT = 'HPNT_Search_Windows_DoubleClick'
CSC_EXE = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')

ROOT_FILES = (
    'HPNT_Start.cmd',
    'HPNT_Import_Notes.cmd',
    'HPNT_Search.exe',
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


def ensure_ico():
    """Create a standard PNG-in-ICO file from app/icon-180.png."""
    png_path = ROOT_DIR / 'app' / 'icon-180.png'
    ico_path = ROOT_DIR / 'app' / 'hpnt_icon.ico'
    png_data = png_path.read_bytes()
    w, h = struct.unpack('>II', png_data[16:24])
    header = struct.pack('<HHH', 0, 1, 1)
    entry = struct.pack('<BBBBHHII', w if w < 256 else 0, h if h < 256 else 0, 0, 0, 1, 32, len(png_data), 22)
    ico_path.write_bytes(header + entry + png_data)
    return ico_path


def compile_cs(cs_file, out_exe, ico_path=None, extra_refs=(), target='winexe'):
    if not CSC_EXE.is_file():
        raise FileNotFoundError(f'C# compiler not found: {CSC_EXE}')
    cmd = [
        str(CSC_EXE),
        '/nologo',
        '/optimize+',
        f'/target:{target}',
        '/platform:x64',
        f'/out:{out_exe}',
    ]
    if ico_path and ico_path.is_file():
        cmd.append(f'/win32icon:{ico_path}')
    for ref in extra_refs:
        cmd.append(f'/reference:{ref}')
    cmd.append(str(cs_file))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f'csc failed for {cs_file.name}:\n{res.stdout}\n{res.stderr}')
    print(f'[Compile] Built {out_exe.name} ({target})')


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


def build_all():
    version = read_version()
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    if not (ROOT_DIR / 'runtime' / 'python.exe').is_file():
        raise FileNotFoundError('runtime/python.exe not found; cannot build standalone package.')

    ico_path = ensure_ico()

    # 1. Build folder tray launcher HPNT_Search.exe (winexe, no terminal window)
    launcher_exe = ROOT_DIR / 'HPNT_Search.exe'
    compile_cs(
        ROOT_DIR / 'scripts' / 'launcher.cs',
        launcher_exe,
        ico_path=ico_path,
        extra_refs=('System.Windows.Forms.dll', 'System.Drawing.dll'),
        target='winexe',
    )
    chk1 = subprocess.run([str(launcher_exe), '--check'], capture_output=True, text=True, check=True)
    print(f'[Verify] HPNT_Search.exe --check rc={chk1.returncode} {chk1.stdout.strip()}')

    desktop_launcher = Path.home() / 'Desktop' / 'HPNT 업무검색.exe'
    if desktop_launcher.parent.is_dir():
        shutil.copy2(launcher_exe, desktop_launcher)
        print(f'[Copy] Desktop shortcut EXE: {desktop_launcher}')

    # 2. Build standalone ZIP package
    zip_name = f'HPNT_Search_Windows_v{version}.zip'
    dist_zip = DIST_DIR / zip_name
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
            f'{PACKAGE_ROOT}/HPNT_Search.exe',
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

    zip_mb = dist_zip.stat().st_size / (1024 * 1024)
    print(f'[Build] Created & verified ZIP: {dist_zip} ({added} files, {zip_mb:.2f} MB)')

    # 3. Build single-file SFX standalone EXE (winexe, no terminal window)
    sfx_stub = DIST_DIR / 'sfx_stub.exe'
    compile_cs(
        ROOT_DIR / 'scripts' / 'standalone_sfx.cs',
        sfx_stub,
        ico_path=ico_path,
        extra_refs=(
            'System.IO.Compression.dll',
            'System.IO.Compression.FileSystem.dll',
            'System.Windows.Forms.dll',
        ),
        target='winexe',
    )
    exe_name = f'HPNT_Search_Windows_v{version}.exe'
    dist_exe = DIST_DIR / exe_name

    stub_bytes = sfx_stub.read_bytes()
    zip_bytes = dist_zip.read_bytes()
    payload_offset = len(stub_bytes)
    payload_length = len(zip_bytes)
    footer = struct.pack('<qq8s', payload_offset, payload_length, b'HPNTSFX1')
    dist_exe.write_bytes(stub_bytes + zip_bytes + footer)
    sfx_stub.unlink(missing_ok=True)

    chk2 = subprocess.run([str(dist_exe), '--check'], capture_output=True, text=True, check=True)
    exe_mb = dist_exe.stat().st_size / (1024 * 1024)
    print(f'[Build] Created & verified SFX EXE: {dist_exe} ({exe_mb:.2f} MB) -> {chk2.stdout.strip()}')

    # 4. Copy ZIP and SFX EXE to Desktop and Google Drive
    for artifact in (dist_zip, dist_exe):
        for dest_dir in (Path.home() / 'Desktop', Path(r'G:\내 드라이브\HPNT_업무')):
            if dest_dir.is_dir():
                target = dest_dir / artifact.name
                shutil.copy2(artifact, target)
                print(f'[Copy] Saved to: {target}')

    return dist_zip, dist_exe


if __name__ == '__main__':
    build_all()
