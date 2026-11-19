"""Load and index archived Outlook emails from Google Drive 메일검색.html into a fast local SQLite cache."""

import datetime
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import unquote

from catalog import DB, ROOT, clean, norm

MAIL_ROOT = Path(os.environ['HPNT_MAIL_ROOT']) if os.environ.get('HPNT_MAIL_ROOT') else ROOT.parent / '메일보관_백업_2026-10-31'
MAIL_DB = DB.parent / 'mails_cache.sqlite'
IMPORTED_MAIL_ID_OFFSET = 500_000

_MAIL_LOCK = threading.Lock()
_MAIL_CACHE = {'sig': None, 'mails': [], 'by_id': {}}


def _parse_iso_ns(date_str):
    if not date_str:
        return 0
    try:
        return int(datetime.datetime.fromisoformat(date_str.replace('Z', '+00:00')).timestamp() * 1_000_000_000)
    except Exception:
        return 0


def _connect_mail_db():
    MAIL_DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(MAIL_DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('pragma journal_mode=WAL')
    return c


def _ensure_mail_schema(c):
    c.executescript('''
        CREATE TABLE IF NOT EXISTS mails(
            id INTEGER PRIMARY KEY,
            mail_id TEXT,
            date TEXT,
            mtime INTEGER,
            year TEXT,
            subject TEXT,
            sender TEXT,
            body_rel TEXT,
            body_text TEXT,
            source TEXT,
            attachments_json TEXT
        );
        CREATE TABLE IF NOT EXISTS imported_mails(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE,
            pst_source TEXT,
            root_dir TEXT,
            mail_id TEXT,
            date TEXT,
            mtime INTEGER,
            year TEXT,
            subject TEXT,
            sender TEXT,
            body_rel TEXT,
            body_text TEXT,
            source TEXT,
            attachments_json TEXT,
            imported_at REAL
        );
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    ''')


def _extract_html_data(html_path):
    raw = html_path.read_text(encoding='utf-8')
    idx1 = raw.find('const DATA=')
    if idx1 == -1:
        idx1 = raw.find('const DATA =')
    if idx1 == -1:
        return []
    start = raw.find('[', idx1)
    idx2 = raw.rfind('];\nconst el=')
    if idx2 == -1:
        idx2 = raw.rfind('];')
    if start == -1 or idx2 == -1 or idx2 <= start:
        return []
    return json.loads(raw[start:idx2 + 1])


def _sync_mail_db(html_path, sig):
    c = _connect_mail_db()
    _ensure_mail_schema(c)
    meta = {r['key']: r['value'] for r in c.execute('select key, value from meta')}
    if meta.get('source_size') == str(sig[0]) and meta.get('source_mtime') == str(sig[1]):
        rows = c.execute('select * from mails order by id').fetchall()
        c.close()
        return rows

    records = _extract_html_data(html_path)
    c.execute('delete from mails')
    payload = [
        (
            idx + 1,
            str(r.get('id', '')),
            r.get('date', ''),
            _parse_iso_ns(r.get('date', '')),
            str(r.get('year', '') or ''),
            r.get('subject', '') or '(제목 없음)',
            r.get('sender', '') or '',
            r.get('body', '') or '',
            r.get('bodyText', '') or '',
            r.get('source', '') or '보관 메일',
            json.dumps(r.get('attachments', []), ensure_ascii=False),
        )
        for idx, r in enumerate(records)
    ]
    c.executemany(
        'insert into mails(id,mail_id,date,mtime,year,subject,sender,body_rel,body_text,source,attachments_json) values(?,?,?,?,?,?,?,?,?,?,?)',
        payload,
    )
    c.execute('insert or replace into meta values(?,?)', ('source_size', str(sig[0])))
    c.execute('insert or replace into meta values(?,?)', ('source_mtime', str(sig[1])))
    c.execute('insert or replace into meta values(?,?)', ('count', str(len(payload))))
    c.commit()
    rows = c.execute('select * from mails order by id').fetchall()
    c.close()
    return rows


def _build_mail_entry(r, id_offset=0, default_root=None):
    atts = json.loads(r['attachments_json']) if r['attachments_json'] else []
    attachments_ui = [
        {'index': i, 'name': a.get('name', ''), 'ext': Path(a.get('name', '')).suffix.lower()}
        for i, a in enumerate(atts)
    ]
    year = r['year'] or '미확인'
    subject = r['subject'] or '(제목 없음)'
    sender = r['sender'] or ''
    body_text = r['body_text'] or ''
    att_names = ' '.join(a.get('name', '') for a in atts)
    path_clean = clean(f"메일보관/{year}/{subject} {sender} {att_names}")
    body_clean = clean(body_text) if body_text else ''
    neg_id = -(id_offset + int(r['id']))
    row_keys = r.keys()
    root_dir = (r['root_dir'] if 'root_dir' in row_keys and r['root_dir'] else str(default_root or MAIL_ROOT))
    return {
        'id': neg_id,
        'kind': 'mail',
        'year': year,
        'path': f"메일보관/{year}/{subject}",
        'top_folder': '메일보관',
        'root_dir': root_dir,
        'subject': subject,
        'sender': sender,
        'date': r['date'] or '',
        'source': r['source'] or '보관 메일',
        'body_rel': r['body_rel'] or '',
        'body_text': body_text,
        'attachments': atts,
        'attachments_ui': attachments_ui,
        'att_exts': frozenset(a['ext'] for a in attachments_ui if a['ext']),
        'category': '메일자료',
        'extension': '.mail',
        'status': 'ready',
        'status_label': f'메일 자료 · 첨부 {len(atts)}개' if atts else '메일 자료',
        'size': len(body_text.encode('utf-8')),
        'mtime': int(r['mtime'] or 0),
        'name_clean': clean(subject),
        'path_clean': path_clean,
        'body_clean': body_clean,
        'text_all': f'{path_clean}\n{body_clean}' if body_clean else path_clean,
        'matched_alias': None,
    }


def _load_imported_rows(c):
    has_imported = c.execute("select 1 from sqlite_master where type='table' and name='imported_mails'").fetchone()
    if not has_imported:
        return []
    return c.execute('select * from imported_mails order by id').fetchall()


def _load_mail_rows_from_local_db():
    if not MAIL_DB.is_file():
        return None, None, []
    c = _connect_mail_db()
    try:
        imported_rows = _load_imported_rows(c)
        has_meta = c.execute("select 1 from sqlite_master where type='table' and name='meta'").fetchone()
        has_mails = c.execute("select 1 from sqlite_master where type='table' and name='mails'").fetchone()
        if not has_meta or not has_mails:
            return None, None, imported_rows
        meta = {r['key']: r['value'] for r in c.execute('select key, value from meta')}
        src_size = meta.get('source_size')
        src_mtime = meta.get('source_mtime')
        if src_size is None or src_mtime is None:
            return None, None, imported_rows
        rows = c.execute('select * from mails order by id').fetchall()
        if not rows and meta.get('count') != '0':
            return None, None, imported_rows
        return (int(src_size), int(src_mtime)), rows, imported_rows
    except (sqlite3.Error, ValueError):
        return None, None, []
    finally:
        c.close()


def import_extracted_mails(records, root_dir, pst_source):
    """Store converted PST mail records into local MAIL_DB with fingerprint deduplication and reload cache."""
    c = _connect_mail_db()
    _ensure_mail_schema(c)
    now = time.time()
    added = 0
    duplicates_skipped = 0
    root_str = str(root_dir)
    source_label = str(pst_source or '추가 PST')
    try:
        for r in records:
            fp = str(r.get('fingerprint') or '')
            if not fp:
                fp = f"{root_str}|{r.get('id', '')}|{r.get('subject', '')}|{r.get('date', '')}"
            cur = c.execute(
                '''
                INSERT OR IGNORE INTO imported_mails(
                    fingerprint, pst_source, root_dir, mail_id, date, mtime, year,
                    subject, sender, body_rel, body_text, source, attachments_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    fp,
                    source_label,
                    root_str,
                    str(r.get('id', '')),
                    r.get('date', ''),
                    _parse_iso_ns(r.get('date', '')),
                    str(r.get('year', '') or ''),
                    r.get('subject', '') or '(제목 없음)',
                    r.get('sender', '') or '',
                    r.get('body', '') or '',
                    r.get('bodyText', '') or '',
                    r.get('source', '') or source_label,
                    json.dumps(r.get('attachments', []), ensure_ascii=False),
                    now,
                ),
            )
            if cur.rowcount == 1:
                added += 1
            else:
                duplicates_skipped += 1
        c.commit()
    finally:
        c.close()

    with _MAIL_LOCK:
        _MAIL_CACHE['sig'] = None
    get_mail_cache(force_check=False)
    return {'added': added, 'duplicates_skipped': duplicates_skipped}


def get_mail_cache(force_check=False):
    """Return cached archived mail records and id map, or ([], {}) when archive is absent."""
    with _MAIL_LOCK:
        if _MAIL_CACHE['sig'] is not None and not force_check:
            return _MAIL_CACHE['mails'], _MAIL_CACHE['by_id']

    base_sig = None
    rows = None
    imported_rows = []
    if not force_check:
        base_sig, rows, imported_rows = _load_mail_rows_from_local_db()

    if base_sig is None:
        html_path = MAIL_ROOT / '메일검색.html'
        html_ok = False
        try:
            if html_path.is_file():
                st = html_path.stat()
                base_sig = (st.st_size, st.st_mtime_ns)
                html_ok = True
        except OSError:
            html_ok = False

        if html_ok:
            rows = _sync_mail_db(html_path, base_sig)
            if MAIL_DB.is_file():
                c = _connect_mail_db()
                try:
                    imported_rows = _load_imported_rows(c)
                finally:
                    c.close()
        elif imported_rows:
            base_sig = ('imported_only',)
            rows = []
        else:
            return [], {}

    max_imp_id = int(imported_rows[-1]['id']) if imported_rows else 0
    full_sig = (base_sig, len(imported_rows), max_imp_id)
    with _MAIL_LOCK:
        if _MAIL_CACHE['sig'] == full_sig and not force_check:
            return _MAIL_CACHE['mails'], _MAIL_CACHE['by_id']

    mails = [_build_mail_entry(r) for r in (rows or ())]
    if imported_rows:
        mails.extend(
            _build_mail_entry(r, id_offset=IMPORTED_MAIL_ID_OFFSET)
            for r in imported_rows
        )
    by_id = {m['id']: m for m in mails}

    with _MAIL_LOCK:
        _MAIL_CACHE['sig'] = full_sig
        _MAIL_CACHE['mails'] = mails
        _MAIL_CACHE['by_id'] = by_id
    return mails, by_id


def get_mail_by_id(neg_id):
    _, by_id = get_mail_cache()
    return by_id.get(neg_id)


def resolve_mail_target_path(neg_id, att_index=None):
    """Resolve a safe path under the mail entry's root_dir (or MAIL_ROOT) for body.txt or attachment."""
    m = get_mail_by_id(neg_id)
    if not m:
        return None
    if att_index is not None:
        atts = m['attachments']
        if not (0 <= att_index < len(atts)):
            return None
        rel_enc = atts[att_index].get('url', '')
    else:
        rel_enc = m['body_rel']
    if not rel_enc:
        return None

    rel = unquote(rel_enc).lstrip('/\\')
    base_root = Path(m.get('root_dir') or MAIL_ROOT)
    try:
        root_resolved = base_root.resolve()
    except OSError:
        return None
    for cand_rel in (rel, norm(rel)):
        cand = base_root / cand_rel
        try:
            resolved = cand.resolve()
            if resolved.is_relative_to(root_resolved) and resolved.is_file():
                return resolved
        except OSError:
            continue
    return None


def mail_snippet(mail_entry, patterns, browse_mode=False):
    body_text = mail_entry['body_text']
    body_clean = mail_entry['body_clean']
    for pattern in patterns:
        match = pattern.search(body_clean)
        if match:
            pos = match.start()
            return body_text[max(0, pos - 70):pos + 320]
    if browse_mode:
        return body_text[:280]
    if body_text:
        return body_text[:280]
    return f"발신: {mail_entry['sender']}"
