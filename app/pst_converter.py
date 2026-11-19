"""Inspect, extract, and import Outlook .pst archives into HPNT Quick Search."""

import ctypes
import datetime
import json
import os
import re
import shutil
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

from catalog import BASE, DB, ROOT
from mail_archive import MAIL_ROOT, import_extracted_mails

STAGING_DIR = DB.parent / 'pst_staging'
WORKER_SCRIPT = BASE / 'pst_worker.cjs'
CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

_JOB_LOCK = threading.Lock()
_ACTIVE_PROC = None
_CANCEL_EVENT = threading.Event()
_JOB_STATE = {
    'running': False,
    'phase': 'idle',
    'pst_path': '',
    'pst_name': '',
    'target_mode': 'mail_archive',
    'output_dir': '',
    'expected': 0,
    'exported': 0,
    'skipped_non_mail': 0,
    'attachments': 0,
    'attachment_bytes': 0,
    'warnings': 0,
    'errors': 0,
    'years': {},
    'current_folder': '',
    'current_subject': '',
    'added_to_search': 0,
    'duplicates_skipped': 0,
    'error': '',
    'started_at': 0.0,
    'finished_at': 0.0,
}


def _safe_stem(name, fallback='pst_mail'):
    cleaned = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', '_', str(name or '')).strip(' .')
    return (cleaned[:50] or fallback)


def _find_node_exe():
    found = shutil.which('node')
    if found and Path(found).is_file():
        return found
    candidates = [
        Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'nodejs' / 'node.exe',
        Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'nodejs' / 'node.exe',
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'nodejs' / 'node.exe',
    ]
    for cand in candidates:
        if cand and cand.is_file():
            return str(cand)
    raise FileNotFoundError('Node.js(node.exe)를 찾을 수 없습니다. Node.js 설치 상태를 확인해 주세요.')


def _validate_pst_path(pst_path):
    if not pst_path:
        raise ValueError('PST 파일 경로를 입력하거나 선택해 주세요.')
    p = Path(str(pst_path).strip().strip('"'))
    if not p.is_file():
        raise FileNotFoundError(f'PST 파일을 찾을 수 없습니다: {p}')
    if p.suffix.lower() != '.pst':
        raise ValueError('확장자가 .pst인 Outlook 데이터 파일만 변환할 수 있습니다.')
    return p.resolve()


def pick_pst_file():
    """Open a native Windows file dialog to pick a local .pst file without uploading."""
    if os.name != 'nt':
        raise RuntimeError('파일 탐색기 선택 창은 Windows PC에서 사용할 수 있습니다.')

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ('lStructSize', wintypes.DWORD),
            ('hwndOwner', wintypes.HWND),
            ('hInstance', wintypes.HINSTANCE),
            ('lpstrFilter', wintypes.LPCWSTR),
            ('lpstrCustomFilter', wintypes.LPWSTR),
            ('nMaxCustFilter', wintypes.DWORD),
            ('nFilterIndex', wintypes.DWORD),
            ('lpstrFile', wintypes.LPWSTR),
            ('nMaxFile', wintypes.DWORD),
            ('lpstrFileTitle', wintypes.LPWSTR),
            ('nMaxFileTitle', wintypes.DWORD),
            ('lpstrInitialDir', wintypes.LPCWSTR),
            ('lpstrTitle', wintypes.LPCWSTR),
            ('Flags', wintypes.DWORD),
            ('nFileOffset', wintypes.WORD),
            ('nFileExtension', wintypes.WORD),
            ('lpstrDefExt', wintypes.LPCWSTR),
            ('lCustData', wintypes.LPARAM),
            ('lpfnHook', ctypes.c_void_p),
            ('lpTemplateName', wintypes.LPCWSTR),
            ('pvReserved', ctypes.c_void_p),
            ('dwReserved', wintypes.DWORD),
            ('FlagsEx', wintypes.DWORD),
        ]

    buf = ctypes.create_unicode_buffer(32768)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = None
    ofn.lpstrFilter = 'Outlook 데이터 파일 (*.pst)\0*.pst\0모든 파일 (*.*)\0*.*\0\0'
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = 32768
    ofn.lpstrTitle = '변환할 Outlook PST 파일 선택'
    ofn.Flags = 0x00001000 | 0x00000800 | 0x00000008  # FILEMUSTEXIST | PATHMUSTEXIST | NOCHANGEDIR

    initial_dir = MAIL_ROOT if MAIL_ROOT.is_dir() else Path.home() / 'Desktop'
    if initial_dir.is_dir():
        ofn.lpstrInitialDir = str(initial_dir)

    ok = ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn))
    if not ok:
        return {'ok': True, 'cancelled': True}

    chosen = Path(buf.value)
    if not chosen.is_file() or chosen.suffix.lower() != '.pst':
        raise ValueError('선택한 파일이 올바른 .pst 파일이 아닙니다.')
    st = chosen.stat()
    return {
        'ok': True,
        'cancelled': False,
        'path': str(chosen.resolve()),
        'name': chosen.name,
        'size': st.st_size,
    }


def _candidate_search_dirs():
    home = Path.home()
    dirs = [
        home / 'Desktop',
        home / 'Downloads',
        home / 'Documents',
        home / 'Documents' / 'Outlook 파일',
        home / 'Documents' / 'Outlook Files',
        MAIL_ROOT,
        ROOT,
        STAGING_DIR,
    ]
    for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
        drv = Path(f'{letter}:\\')
        try:
            if drv.is_dir():
                dirs.extend([
                    drv,
                    drv / '아웃룩백업',
                    drv / 'PST_정리작업',
                    drv / '메일보관',
                    drv / 'Outlook',
                ])
        except OSError:
            continue
    seen = set()
    unique = []
    for d in dirs:
        try:
            s = str(d)
            if s not in seen and d.is_dir():
                seen.add(s)
                unique.append(d)
        except OSError:
            continue
    return unique


def resolve_dropped_pst(name, size=None, last_modified_ms=None):
    """Try to locate a browser-dropped .pst file on local/Drive disks by exact name & size."""
    fname = Path(str(name or '')).name
    if not fname or Path(fname).suffix.lower() != '.pst':
        raise ValueError('확장자가 .pst인 파일만 드롭할 수 있습니다.')

    expected_size = int(size) if size is not None else None
    expected_mtime_sec = (float(last_modified_ms) / 1000.0) if last_modified_ms else None
    target_lower = fname.lower()
    matches = []

    for base_dir in _candidate_search_dirs():
        direct = base_dir / fname
        candidates = [direct]
        try:
            for sub in base_dir.iterdir():
                if sub.is_dir():
                    candidates.append(sub / fname)
        except OSError:
            pass
        for cand in candidates:
            try:
                if not cand.is_file() or cand.name.lower() != target_lower:
                    continue
                st = cand.stat()
                if expected_size is not None and st.st_size != expected_size:
                    continue
                mtime_diff = abs(st.st_mtime - expected_mtime_sec) if expected_mtime_sec else 0.0
                matches.append((mtime_diff, cand.resolve(), st.st_size))
            except OSError:
                continue

    if not matches:
        return {'matched': False}
    matches.sort(key=lambda item: item[0])
    _, resolved, fsize = matches[0]
    return {
        'matched': True,
        'path': str(resolved),
        'name': resolved.name,
        'size': fsize,
    }


def save_uploaded_pst(rfile, length, filename):
    """Stream an uploaded .pst file into local staging storage for conversion."""
    fname = Path(str(filename or 'uploaded.pst')).name
    if Path(fname).suffix.lower() != '.pst':
        raise ValueError('확장자가 .pst인 파일만 업로드할 수 있습니다.')
    if length <= 0:
        raise ValueError('빈 파일은 변환할 수 없습니다.')

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = STAGING_DIR / f'{_safe_stem(Path(fname).stem, "uploaded")}_{stamp}.pst'

    remaining = length
    chunk_size = 4 * 1024 * 1024
    written = 0
    try:
        with dst.open('wb') as f:
            while remaining > 0:
                buf = rfile.read(min(chunk_size, remaining))
                if not buf:
                    raise OSError('업로드 스트림이 조기에 종료되었습니다.')
                f.write(buf)
                written += len(buf)
                remaining -= len(buf)
    except Exception:
        dst.unlink(missing_ok=True)
        raise

    return {
        'ok': True,
        'path': str(dst.resolve()),
        'name': fname,
        'size': written,
    }


def inspect_pst(pst_path):
    """Read PST folder hierarchy and item counts without modifying the archive."""
    p = _validate_pst_path(pst_path)
    node_exe = _find_node_exe()
    if not WORKER_SCRIPT.is_file():
        raise FileNotFoundError(f'PST 작업 스크립트를 찾을 수 없습니다: {WORKER_SCRIPT}')

    run = subprocess.run(
        [node_exe, str(WORKER_SCRIPT), '--inspect', str(p)],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=60,
        creationflags=CREATE_NO_WINDOW,
    )
    if run.returncode != 0:
        err_msg = (run.stderr or run.stdout or 'PST 파일 분석에 실패했습니다.').strip()
        raise RuntimeError(err_msg[-600:])
    return json.loads(run.stdout.strip().splitlines()[-1])


def get_job_status():
    with _JOB_LOCK:
        snap = dict(_JOB_STATE)
        snap['years'] = dict(_JOB_STATE['years'])
        return snap


def _choose_output_dir(pst_path, target_mode):
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    folder_name = f'{_safe_stem(pst_path.stem)}_{stamp}'
    if target_mode == 'work_folder':
        return ROOT / 'PST_메일정리' / folder_name
    if MAIL_ROOT.is_dir() or MAIL_ROOT.parent.is_dir():
        return MAIL_ROOT / '추가메일' / folder_name
    return ROOT / 'PST_메일보관' / folder_name


def _cleanup_if_staged(pst_path):
    """Remove temporary staged .pst copy in STAGING_DIR after conversion finishes or cancels."""
    try:
        resolved = Path(pst_path).resolve()
        staging_resolved = STAGING_DIR.resolve()
        if resolved.is_relative_to(staging_resolved) and resolved.is_file():
            resolved.unlink(missing_ok=True)
    except OSError:
        pass


def cancel_conversion():
    """Cancel an in-progress PST conversion job."""
    global _ACTIVE_PROC
    with _JOB_LOCK:
        if not _JOB_STATE['running']:
            return {'ok': False, 'message': '현재 진행 중인 PST 변환 작업이 없습니다.', 'status': dict(_JOB_STATE)}
        _CANCEL_EVENT.set()
        proc = _ACTIVE_PROC
        _JOB_STATE['phase'] = 'cancelling'
        _JOB_STATE['current_folder'] = '변환 중단 중…'
        _JOB_STATE['current_subject'] = ''

    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            pass
    return {'ok': True, 'message': 'PST 변환 중단을 요청했습니다.', 'status': get_job_status()}


def start_conversion(pst_path, target_mode='mail_archive', on_complete=None):
    """Start background PST extraction & search indexing; reject concurrent runs."""
    p = _validate_pst_path(pst_path)
    node_exe = _find_node_exe()
    if target_mode not in ('mail_archive', 'work_folder'):
        target_mode = 'mail_archive'
    output_dir = _choose_output_dir(p, target_mode)

    with _JOB_LOCK:
        if _JOB_STATE['running']:
            raise RuntimeError('이미 다른 PST 변환 작업이 진행 중입니다.')
        _CANCEL_EVENT.clear()
        _JOB_STATE.update({
            'running': True,
            'phase': 'starting',
            'pst_path': str(p),
            'pst_name': p.name,
            'target_mode': target_mode,
            'output_dir': str(output_dir),
            'expected': 0,
            'exported': 0,
            'skipped_non_mail': 0,
            'attachments': 0,
            'attachment_bytes': 0,
            'warnings': 0,
            'errors': 0,
            'years': {},
            'current_folder': '준비 중',
            'current_subject': '',
            'added_to_search': 0,
            'duplicates_skipped': 0,
            'error': '',
            'started_at': time.time(),
            'finished_at': 0.0,
        })

    def _run():
        global _ACTIVE_PROC
        result_data = None
        proc = None
        try:
            proc = subprocess.Popen(
                [node_exe, str(WORKER_SCRIPT), '--convert', str(p), str(output_dir), p.name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
            with _JOB_LOCK:
                _ACTIVE_PROC = proc
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                if _CANCEL_EVENT.is_set():
                    break
                line = raw_line.strip()
                if line.startswith('PROGRESS:'):
                    try:
                        prog = json.loads(line[9:])
                    except json.JSONDecodeError:
                        continue
                    with _JOB_LOCK:
                        _JOB_STATE.update({
                            'phase': 'extracting',
                            'expected': int(prog.get('expected', 0)),
                            'exported': int(prog.get('exported', 0)),
                            'attachments': int(prog.get('attachments', 0)),
                            'warnings': int(prog.get('warnings', 0)),
                            'errors': int(prog.get('errors', 0)),
                            'current_folder': str(prog.get('folder', '')),
                            'current_subject': str(prog.get('subject', '')),
                            'years': dict(prog.get('years') or {}),
                        })
                elif line.startswith('RESULT:'):
                    try:
                        result_data = json.loads(line[7:])
                    except json.JSONDecodeError:
                        pass

            stderr_out = proc.stderr.read() if proc.stderr else ''
            rc = proc.wait()
            if _CANCEL_EVENT.is_set():
                with _JOB_LOCK:
                    _JOB_STATE.update({
                        'running': False,
                        'phase': 'cancelled',
                        'current_folder': '사용자 요청으로 중단됨',
                        'current_subject': '',
                        'finished_at': time.time(),
                    })
                return

            if rc != 0 or not result_data or not result_data.get('ok'):
                raise RuntimeError((stderr_out or 'PST 변환 중 오류가 발생했습니다.').strip()[-600:])

            with _JOB_LOCK:
                _JOB_STATE.update({
                    'phase': 'indexing',
                    'expected': int(result_data.get('expected', 0)),
                    'exported': int(result_data.get('exported', 0)),
                    'skipped_non_mail': int(result_data.get('skippedNonMail', 0)),
                    'attachments': int(result_data.get('attachments', 0)),
                    'attachment_bytes': int(result_data.get('attachmentBytes', 0)),
                    'warnings': int(result_data.get('warnings', 0)),
                    'errors': int(result_data.get('errors', 0)),
                    'years': dict(result_data.get('years') or {}),
                    'current_folder': '통합 검색 색인 등록 중',
                    'current_subject': '',
                })

            records_file = Path(result_data['recordsFile'])
            records = json.loads(records_file.read_text(encoding='utf-8')) if records_file.is_file() else []
            imp = import_extracted_mails(records, output_dir, p.name)

            if on_complete is not None:
                try:
                    on_complete(target_mode, output_dir)
                except Exception:
                    pass

            with _JOB_LOCK:
                _JOB_STATE.update({
                    'running': False,
                    'phase': 'done',
                    'added_to_search': int(imp.get('added', 0)),
                    'duplicates_skipped': int(imp.get('duplicates_skipped', 0)),
                    'current_folder': '완료',
                    'current_subject': '',
                    'finished_at': time.time(),
                })
        except Exception as exc:
            if _CANCEL_EVENT.is_set():
                with _JOB_LOCK:
                    _JOB_STATE.update({
                        'running': False,
                        'phase': 'cancelled',
                        'current_folder': '사용자 요청으로 중단됨',
                        'current_subject': '',
                        'finished_at': time.time(),
                    })
            else:
                with _JOB_LOCK:
                    _JOB_STATE.update({
                        'running': False,
                        'phase': 'error',
                        'error': str(exc)[:600],
                        'finished_at': time.time(),
                    })
        finally:
            with _JOB_LOCK:
                if _ACTIVE_PROC is proc:
                    _ACTIVE_PROC = None
            _cleanup_if_staged(p)

    threading.Thread(target=_run, daemon=True).start()
    return get_job_status()


def is_allowed_output_dir(target_path):
    """Allow opening only the last job's output directory or directories inside MAIL_ROOT / ROOT."""
    try:
        resolved = Path(str(target_path)).resolve()
        if not resolved.is_dir():
            return None
        last_out = get_job_status().get('output_dir')
        allowed_roots = [ROOT.resolve(), MAIL_ROOT.resolve()]
        if last_out:
            last_resolved = Path(last_out).resolve()
            if resolved == last_resolved:
                return resolved
        for base in allowed_roots:
            if resolved.is_relative_to(base):
                return resolved
    except OSError:
        return None
    return None
