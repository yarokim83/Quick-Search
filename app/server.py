import sqlite3, re, json, mimetypes, os, shutil, time, threading, subprocess, sys, zipfile, ctypes
from collections import Counter
from itertools import chain
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

APP_DIR = str(Path(__file__).resolve().parent)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from catalog import BASE, DB, ROOT, connect, clean, grams, init, scan, seed, process, state, norm, category
from evidence import has_date, record_query, record_evidence, report_period, parse_report_query
from related import group_related
from mail_archive import get_mail_cache, get_mail_by_id, resolve_mail_target_path, mail_snippet

LABELS = {
    'ready': '본문 검색 가능',
    'partial': '일부 본문만 색인',
    'pending': '본문 처리 대기',
    'cloud': '본문 미색인',
    'unsupported': '파일명 검색만 지원',
    'ocr_needed': 'OCR 필요',
    'empty': '본문 확인 필요',
    'failed': '본문 처리 실패',
    'large': '대용량 처리 보류',
}
DEFAULT_LIMIT = 100
MAX_LIMIT = 200
EXT_FILTER_GROUPS = {
    'excel': ('.xlsx', '.xlsm', '.xls', '.csv'),
    'pdf': ('.pdf',),
    'cad': ('.dwg', '.dxf'),
    'doc': ('.docx', '.doc', '.hwp', '.hwpx', '.txt', '.md', '.rtf', '.pptx', '.ppt'),
    'image': ('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.gif'),
    'archive': ('.zip', '.rar', '.alz'),
    'folder': ('.folder',),
}
ORIGINAL_EXTENSIONS = frozenset((
    '.pdf', '.xlsx', '.xlsm', '.xls', '.docx', '.doc', '.hwp', '.hwpx',
    '.txt', '.rtf', '.lis', '.list', '.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff',
    '.avi', '.mp4', '.webm', '.dwg', '.dxf', '.zip', '.rar', '.alz', '.pptx', '.ppt',
))
OPEN_LOCAL_EXTENSIONS = ORIGINAL_EXTENSIONS | frozenset(('.msg', '.eml', '.csv', '.md', '.json'))
FOLDER_ID_OFFSET = 1_000_000
scan_lock = threading.Lock()
_CATALOG_LOCK = threading.Lock()
_CATALOG_CACHE = {'sig': None, 'checked_at': 0.0, 'docs': [], 'aliases': [], 'folders': [], 'chunks_by_doc': {}}

QC_PREFIX = r'(?<![a-z0-9])q/?c[ \t]*[-‐‑–]?[ \t]*'
QC_NUMBER = re.compile(QC_PREFIX + r'([0-9]{3})(?![a-z0-9])(?:호기?)?')
LENGTH_VALUE = re.compile(r'(?<!\S)(\d+(?:[.,]\d+)?)[ \t]*(mm|cm|m)(?!\S)')
DRAWING_NUMBER = re.compile(r'jr\d[0-9a-z]*|j\d{3}[a-z][a-z]?\d{4,}|fh\d+\.\d+\.[a-z0-9]+-\d+')
DRAWING_SPACED_NUMBER = re.compile(r'(?<![a-z0-9])(j\d{3}[a-z])[ \t]*([a-z]\d{4,})(?![a-z0-9])')
DRAWING_TITLE_NUMBER = re.compile(r'^([^\n·]+?)[ \t]+·', re.MULTILINE)
TOKEN_SCANNER = re.compile(r'([!-]?)"([^"]+)"|(\S+)')
JAMO_CHARS = r'\u3131-\u318E\u1100-\u11FF\uA960-\uA97F\uD7B0-\uD7FF'
TRAILING_JAMO = re.compile(rf'([^\s{JAMO_CHARS}"])[{JAMO_CHARS}]+(?=\s|"|$)')
ONLY_JAMO = re.compile(rf'^[{JAMO_CHARS}]+$')


def strip_trailing_jamo(text):
    """Strip incomplete Hangul Jamo while typing so live search never flickers to zero."""
    return TRAILING_JAMO.sub(r'\1', text)


def _build_cache_entry(row, matched_alias=None):
    path = row['path']
    path_clean = clean(matched_alias or path)
    name_clean = clean(Path(path).name)
    body = row['body'] or ''
    body_clean = clean(body) if body else ''
    return {
        'id': row['id'],
        'path': path,
        'top_folder': path.split('/', 1)[0] if '/' in path else '(루트)',
        'category': row['category'],
        'status': row['status'],
        'status_label': LABELS[row['status']],
        'extension': row['extension'],
        'size': row['size'],
        'mtime': row['mtime'],
        'body_clean': body_clean,
        'path_clean': path_clean,
        'name_clean': name_clean,
        'text_all': f'{path_clean}\n{body_clean}' if body_clean else path_clean,
        'matched_alias': matched_alias,
    }


def _build_folder_entry(row):
    path = row['path']
    path_clean = clean(path)
    name_clean = clean(Path(path).name)
    return {
        'id': -FOLDER_ID_OFFSET - row['id'],
        'kind': 'folder',
        'path': path,
        'top_folder': path.split('/', 1)[0] if '/' in path else '(루트)',
        'category': category(path),
        'status': 'unsupported',
        'status_label': '업무 폴더',
        'extension': '.folder',
        'size': 0,
        'mtime': row['mtime'],
        'body_clean': '',
        'path_clean': path_clean,
        'name_clean': name_clean,
        'text_all': name_clean,
        'matched_alias': None,
    }


def get_catalog_cache(c=None, force=False):
    """Return normalized in-memory document, alias, folder, and chunk caches, rebuilding on DB change."""
    now = time.monotonic()
    with _CATALOG_LOCK:
        if not force and _CATALOG_CACHE['sig'] is not None and (now - _CATALOG_CACHE['checked_at']) < 1.5:
            return (
                _CATALOG_CACHE['docs'],
                _CATALOG_CACHE['aliases'],
                _CATALOG_CACHE['folders'],
                _CATALOG_CACHE['chunks_by_doc'],
            )

    own_conn = c is None
    if own_conn:
        c = connect()
    try:
        cnt, max_idx = c.execute('select count(*), coalesce(max(indexed_at), 0) from docs where present=1').fetchone()
        has_folders_tbl = bool(c.execute("select 1 from sqlite_master where type='table' and name='folders'").fetchone())
        folder_cnt = c.execute('select count(*) from folders where present=1').fetchone()[0] if has_folders_tbl else 0
        last_scan_row = c.execute("select value from state where key='last_scan'").fetchone()
        sig = (cnt, max_idx, folder_cnt, last_scan_row[0] if last_scan_row else '')

        with _CATALOG_LOCK:
            if not force and _CATALOG_CACHE['sig'] == sig:
                _CATALOG_CACHE['checked_at'] = now
                return (
                    _CATALOG_CACHE['docs'],
                    _CATALOG_CACHE['aliases'],
                    _CATALOG_CACHE['folders'],
                    _CATALOG_CACHE['chunks_by_doc'],
                )

        docs = [
            _build_cache_entry(r)
            for r in c.execute('select id,path,category,status,extension,size,mtime,body from docs where present=1')
        ]
        aliases = [
            _build_cache_entry(r, matched_alias=r['matched_alias'])
            for r in c.execute("""
                select d.id,d.path,d.category,d.status,d.extension,d.size,d.mtime,d.body,a.path as matched_alias
                from photo_aliases p
                join docs a on a.id=p.source_id and a.present=0 and a.size=p.size and a.mtime=p.source_mtime
                join docs d on d.id=p.target_id and d.present=1 and d.size=p.size and d.mtime=p.target_mtime
            """)
        ]
        folders = [
            _build_folder_entry(r)
            for r in c.execute('select id,path,mtime from folders where present=1')
        ] if has_folders_tbl else []
        chunks_by_doc = {}
        for doc_id, loc, text, method in c.execute('select doc_id,location,text,method from chunks order by rowid'):
            chunks_by_doc.setdefault(doc_id, []).append({
                'location': loc,
                'text': text,
                'method': method,
                'text_clean': clean(text),
            })

        with _CATALOG_LOCK:
            _CATALOG_CACHE['sig'] = sig
            _CATALOG_CACHE['checked_at'] = time.monotonic()
            _CATALOG_CACHE['docs'] = docs
            _CATALOG_CACHE['aliases'] = aliases
            _CATALOG_CACHE['folders'] = folders
            _CATALOG_CACHE['chunks_by_doc'] = chunks_by_doc
        return docs, aliases, folders, chunks_by_doc
    finally:
        if own_conn:
            c.close()


def search_terms(query):
    terms = []
    lengths = {}

    def join_length(match):
        value, unit = match.groups()
        term = value + unit
        lengths[term] = (value, unit)
        return term

    # QC_NUMBER captures the equipment number, which sits between the plain-text sections.
    sections = QC_NUMBER.split(query)
    for i, section in enumerate(sections):
        if i % 2:
            # File names can join an equipment ID to a part name, such as QC107Catenary.
            terms.append((section, re.compile(QC_PREFIX + section + r'(?![0-9])(?:호기?)?')))
        else:
            if len(sections) > 1:
                section = LENGTH_VALUE.sub(join_length, section)
            terms.extend((term, None) for term in section.split())
    return terms, lengths


def parse_advanced_query(raw_query):
    """Split search operators (-exclude, path:, ext:, "phrase", A | B) from plain search text."""
    normalized_raw = strip_trailing_jamo(raw_query.strip())
    spaced = re.sub(r'\s*\|\s*', ' | ', normalized_raw)
    tokens = []
    for match in TOKEN_SCANNER.finditer(spaced):
        prefix, quoted, plain = match.groups()
        if quoted is not None:
            tokens.append(('quoted', prefix or '', quoted))
        elif plain is not None:
            tokens.append(('plain', '', plain))

    excludes = []
    path_filters = []
    ext_tokens = []
    phrases = []
    raw_items = []

    for kind, prefix, val in tokens:
        if kind == 'quoted':
            cleaned = clean(val).strip()
            if not cleaned:
                continue
            if prefix in ('-', '!'):
                excludes.append(cleaned)
            else:
                phrases.append(cleaned)
            continue

        low = clean(val)
        if val in ('|', 'OR'):
            if raw_items and raw_items[-1] != '|':
                raw_items.append('|')
            continue
        if ONLY_JAMO.fullmatch(low):
            continue
        if (low.startswith('-') or low.startswith('!')) and len(low) > 1:
            ex = low[1:]
            if not ONLY_JAMO.fullmatch(ex):
                excludes.append(ex)
            continue
        if low.startswith('path:') and len(low) > 5:
            p_val = low[5:].replace('\\', '/').strip('/')
            if p_val and not ONLY_JAMO.fullmatch(p_val):
                path_filters.append(p_val)
            continue
        if low.startswith('ext:') and len(low) > 4:
            for item in low[4:].split(','):
                ext = item.strip()
                if ext:
                    ext_tokens.append(ext if ext.startswith('.') else '.' + ext)
            continue
        raw_items.append(val)

    while raw_items and raw_items[-1] == '|':
        raw_items.pop()

    # Resolve A | B (or A OR B) groups while keeping sequential plain tokens untouched.
    or_groups = []
    plain_tokens = []
    i = 0
    while i < len(raw_items):
        if i + 2 < len(raw_items) and raw_items[i + 1] == '|':
            group = [raw_items[i]]
            j = i + 1
            while j + 1 < len(raw_items) and raw_items[j] == '|':
                if raw_items[j + 1] != '|':
                    group.append(raw_items[j + 1])
                j += 2
            cleaned_group = [clean(x) for x in group if clean(x)]
            if len(cleaned_group) >= 2:
                or_groups.append(cleaned_group)
            elif len(cleaned_group) == 1:
                plain_tokens.append(cleaned_group[0])
            i = j
        elif raw_items[i] == '|':
            i += 1
        else:
            plain_tokens.append(raw_items[i])
            i += 1

    return {
        'positive_query': ' '.join(plain_tokens),
        'excludes': excludes,
        'path_filters': path_filters,
        'ext_tokens': sorted(set(ext_tokens)),
        'phrases': phrases,
        'or_groups': or_groups,
    }


def escape_like(value):
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def fts_match_expr(terms, lengths, drawing_parts, phrases, or_groups):
    """Build an FTS5 MATCH expression combining mandatory AND trigrams and OR groups."""
    and_grams = set()
    for term, _ in terms:
        for part in lengths.get(term) or drawing_parts.get(term, (term,)):
            and_grams.update(grams(part).split())
    for phrase in phrases:
        and_grams.update(grams(phrase).split())

    clauses = []
    if and_grams:
        clauses.append(' AND '.join(sorted(and_grams)))

    for group in or_groups:
        alt_exprs = []
        for alt in group:
            alt_grams = grams(alt).split()
            if alt_grams:
                alt_exprs.append('(' + ' AND '.join(sorted(set(alt_grams))) + ')')
        if len(alt_exprs) == len(group) and alt_exprs:
            clauses.append('(' + ' OR '.join(alt_exprs) + ')')
        # If any alternative is 1-char (no bigram), skip FTS narrowing for that OR group
        # so regex verification handles it accurately without dropping matches.

    return ' AND '.join(clauses)


def _count_capped3(text, sub):
    """Count non-overlapping occurrences of sub in text, stopping once 3 matches are found."""
    if not sub:
        return 0
    pos = text.find(sub)
    if pos == -1:
        return 0
    step = len(sub)
    pos = text.find(sub, pos + step)
    if pos == -1:
        return 1
    pos = text.find(sub, pos + step)
    return 2 if pos == -1 else 3


def compute_facets(hits, active_folder=''):
    """Aggregate folder, extension, category, and index status counts across all hits."""
    prefix = norm(active_folder).strip('/')
    prefix_slash = prefix + '/' if prefix else ''
    folder_counts = Counter()
    ext_counts = Counter()
    cat_counts = Counter()
    indexed_count = 0

    for h in hits:
        path = h['path']
        if prefix_slash and path.startswith(prefix_slash):
            rel = path[len(prefix_slash):]
            if '/' in rel:
                sub_name = rel.split('/', 1)[0]
                folder_counts[(sub_name, f'{prefix}/{sub_name}')] += 1
            else:
                folder_counts[('(현재 폴더 내 파일)', prefix)] += 1
        elif not prefix_slash and (top := h.get('top_folder')) is not None:
            folder_counts[(top, '' if top == '(루트)' else top)] += 1
        elif '/' in path:
            top = path.split('/', 1)[0]
            folder_counts[(top, top)] += 1
        else:
            folder_counts[('(루트)', '')] += 1

        ext = h['extension'] or '(없음)'
        ext_counts[ext] += 1
        cat_counts[h['category']] += 1
        if h['status'] in ('ready', 'partial'):
            indexed_count += 1

    folders = [
        {'name': name, 'path': fpath, 'count': cnt}
        for (name, fpath), cnt in sorted(folder_counts.items(), key=lambda x: (-x[1], x[0][0]))[:20]
    ]
    extensions = [
        {'ext': ext, 'count': cnt}
        for ext, cnt in ext_counts.most_common(12)
    ]
    categories = [
        {'name': cat, 'count': cnt}
        for cat, cnt in cat_counts.most_common()
    ]
    return {
        'folders': folders,
        'extensions': extensions,
        'categories': categories,
        'indexed_count': indexed_count,
        'filename_only_count': len(hits) - indexed_count,
    }


def find(query='', category='', scope='all', ext_filter='', sort_by='score', folder='', offset=0, limit=DEFAULT_LIMIT, exclude_mail=False):
    parsed = parse_advanced_query(query)
    if '메일보관/' in parsed['excludes']:
        exclude_mail = True
        parsed['excludes'] = [ex for ex in parsed['excludes'] if ex != '메일보관/']
    positive_query = parsed['positive_query']
    record = record_query(positive_query) if positive_query and not (
        parsed['phrases'] or parsed['or_groups'] or parsed['excludes'] or parsed['path_filters'] or parsed['ext_tokens']
    ) else None

    normalized_query = DRAWING_SPACED_NUMBER.sub(r'\1\2', clean(positive_query))
    terms, lengths = ([(record[0], None)], {}) if record else search_terms(normalized_query)
    length_patterns = {
        term: re.compile(rf'(?<![a-z0-9.,]){re.escape(value)}[ \t]*{unit}(?![a-z0-9])')
        for term, (value, unit) in lengths.items()
    }
    drawing_numbers = {term for term, _ in terms if DRAWING_NUMBER.fullmatch(term)}
    drawing_parts = {
        term: match.groups() for term in drawing_numbers
        if (match := DRAWING_SPACED_NUMBER.fullmatch(term))
    }
    drawing_patterns = {
        term: re.compile(r'[ \t]*'.join(map(re.escape, parts)))
        for term, parts in drawing_parts.items()
    }
    patterns = [
        equipment or length_patterns.get(term) or drawing_patterns.get(term) or re.compile(re.escape(term))
        for term, equipment in terms
    ]
    substr_terms = [
        term for term, equipment in terms
        if equipment is None and term not in length_patterns and term not in drawing_patterns
    ]
    rx_terms = [
        pattern for (term, equipment), pattern in zip(terms, patterns)
        if equipment is not None or term in length_patterns or term in drawing_patterns
    ]
    req_set = set(substr_terms)
    for term, equipment in terms:
        if equipment is not None:
            req_set.update((term, 'q', 'c'))
        elif term in lengths:
            req_set.update(lengths[term])
        elif term in drawing_parts:
            req_set.update(drawing_parts[term])
    required_substrs = sorted(req_set, key=len, reverse=True)
    req0 = required_substrs[0] if len(required_substrs) == 1 else None

    excludes = parsed['excludes']
    phrases = parsed['phrases']
    or_groups = parsed['or_groups']
    path_filters = parsed['path_filters']
    parsed_period = parse_report_query(positive_query) if positive_query else None

    numeric_context = not record and len(set(terms)) > 1 and any(re.search(r'[0-9]', term) for term, _ in terms)
    context_patterns = [
        equipment or length_patterns.get(term) or drawing_patterns.get(term) or re.compile(
            (r'(?<![a-z0-9])' if re.match(r'[a-z0-9]', term) else '')
            + re.escape(term)
            + (r'(?![a-z0-9])' if re.search(r'[a-z0-9]$', term) else '')
        )
        for term, equipment in dict.fromkeys(terms)
    ] if numeric_context else []
    equipment_patterns = [equipment for _, equipment in terms if equipment is not None]
    qc_title_patterns = [pattern for (term, _), pattern in zip(terms, patterns) if term != 'qc'] if (
        scope != 'body' and len(terms) > 1 and any(term == 'qc' for term, _ in terms)
    ) else []

    allowed_exts = list(EXT_FILTER_GROUPS.get(ext_filter) or ())
    if not allowed_exts and ext_filter.startswith('.') and len(ext_filter) >= 2:
        allowed_exts = [clean(ext_filter)]
    if parsed['ext_tokens']:
        if allowed_exts:
            allowed_exts = [e for e in allowed_exts if e in parsed['ext_tokens']]
            if not allowed_exts:
                allowed_exts = ['__impossible_ext__']
        else:
            allowed_exts = parsed['ext_tokens']
    allowed_exts_set = frozenset(allowed_exts) if allowed_exts else None

    folder_clean = norm(folder).strip('/')
    folder_prefix_slash = (folder_clean + '/') if folder_clean else ''
    is_mail_folder = (folder_clean == '메일보관' or folder_clean.startswith('메일보관/'))
    mail_only_ext = ext_filter in ('mail', '.mail') or parsed['ext_tokens'] == ['.mail']
    folder_only_ext = ext_filter in ('folder', '.folder') or parsed['ext_tokens'] == ['.folder']

    include_docs_global = category != '메일자료' and not mail_only_ext
    include_mails_global = (not exclude_mail) and category in ('', '메일자료') and not folder_only_ext

    include_docs = include_docs_global and not is_mail_folder
    include_mails = include_mails_global and (not folder_clean or is_mail_folder)

    need_docs = include_docs or (bool(folder_clean) and include_docs_global)
    need_mails = include_mails or (bool(folder_clean) and include_mails_global)

    has_query = bool(terms or parsed['phrases'] or parsed['or_groups'])

    docs_cache, aliases_cache, folders_cache, chunks_by_doc = get_catalog_cache() if need_docs else ([], [], [], {})
    mails_cache, mail_by_id = get_mail_cache() if need_mails else ([], {})
    doc_rows = (docs_cache if scope == 'body' else chain(docs_cache, aliases_cache, folders_cache)) if need_docs else ()
    mail_rows = mails_cache if need_mails else ()
    rows = chain(doc_rows, mail_rows)

    hits = []
    matched_ids = set()
    outside_matched_ids = set()
    context_chunks = {}
    drawing_chunks = {}
    split_length_rows = set()
    priority_ids = set()
    score_substrs = substr_terms + phrases + [alt for grp in or_groups for alt in grp]
    sc0 = score_substrs[0] if len(score_substrs) == 1 else None

    for r in rows:
        doc_id = r['id']
        if doc_id in matched_ids or doc_id in outside_matched_ids:
            continue
        is_mail = (r.get('kind') == 'mail')
        is_folder = (r.get('kind') == 'folder')
        if is_folder and not folder_only_ext and not has_query and not path_filters:
            if not folder_clean or not r['path'].startswith(folder_prefix_slash):
                continue
            if '/' in r['path'][len(folder_prefix_slash):]:
                continue
        if category and r['category'] != category:
            continue
        if allowed_exts_set is not None:
            if is_mail:
                if not mail_only_ext and not (r['att_exts'] & allowed_exts_set):
                    continue
            elif r['extension'] not in allowed_exts_set:
                continue
        in_active_folder = (
            not folder_clean
            or r['path'] == folder_clean
            or r['path'].startswith(folder_prefix_slash)
        )
        if folder_clean and not has_query and not in_active_folder:
            continue
        if not is_mail and scope == 'body' and r['status'] not in ('ready', 'partial'):
            continue

        path = r['path_clean']
        if path_filters and not all(pf in path for pf in path_filters):
            continue
        body = r['body_clean']
        text = body if scope == 'body' else r['text_all']

        if req0 is not None:
            if req0 not in text:
                continue
        elif required_substrs:
            matched_req = True
            for t in required_substrs:
                if t not in text:
                    matched_req = False
                    break
            if not matched_req:
                continue
        if excludes and any(ex in text for ex in excludes):
            continue
        if rx_terms and not all(rx.search(text) for rx in rx_terms):
            continue
        if phrases and not all(ph in text for ph in phrases):
            continue
        if or_groups and not all(any(alt in text for alt in grp) for grp in or_groups):
            continue
        if record and not has_date(text, record[1]):
            continue

        if not in_active_folder:
            outside_matched_ids.add(doc_id)
            continue

        if body:
            score = _count_capped3(body, sc0) if sc0 is not None else sum(_count_capped3(body, s) for s in score_substrs)
            if rx_terms:
                score += sum(min(3, len(rx.findall(body))) for rx in rx_terms)
        else:
            score = 0
        if scope != 'body':
            name = r['name_clean']
            if sc0 is not None:
                score += 8 * (sc0 in name) + 2 * (sc0 in path)
            else:
                score += sum(8 * (s in name) + 2 * (s in path) for s in score_substrs)
            if rx_terms:
                score += sum(8 * bool(rx.search(name)) + 2 * bool(rx.search(path)) for rx in rx_terms)

        if is_mail:
            hit = dict(
                id=doc_id, kind='mail', path=r['path'], top_folder=r['top_folder'],
                category='메일자료', status='ready', status_label=r['status_label'],
                score=score, extension='.mail', size=r['size'], mtime=r['mtime'],
                sender=r['sender'], date=r['date'], year=r['year'], source=r['source'],
                attachments=r['attachments_ui'],
            )
        elif is_folder:
            hit = dict(
                id=doc_id, kind='folder', path=r['path'], top_folder=r['top_folder'],
                category=r['category'], status='unsupported',
                status_label=r['status_label'], score=score, extension='.folder',
                size=0, mtime=r['mtime'],
            )
        else:
            hit = dict(
                id=doc_id, kind='doc', path=r['path'], top_folder=r['top_folder'],
                category=r['category'], status=r['status'],
                status_label=r['status_label'], score=score, extension=r['extension'],
                size=r['size'], mtime=r['mtime'],
            )
            if r['matched_alias']:
                hit['matched_alias'] = r['matched_alias']
            if parsed_period:
                period = report_period(r['path'], positive_query, parsed_period)
                if period:
                    hit['period_label'] = period['label']
                    if period['matches']:
                        hit['score'] += 100
                        priority_ids.add(doc_id)
            doc_chunks = chunks_by_doc.get(doc_id, ())
            if record:
                proof = record_evidence(doc_chunks, *record)
                if proof:
                    hit.update(location=proof['location'], snippet=proof['snippet'], evidence_label=proof['label'], method='record')
                    hit['score'] += 100
                    priority_ids.add(doc_id)
                else:
                    hit['evidence_label'] = '문서 내 검색어 일치 · 같은 기록은 미확인'
            if drawing_numbers and r['extension'] == '.pdf' and r['status'] == 'partial':
                best = None
                best_score = (0, 0)
                for chunk in doc_chunks:
                    if chunk['method'] != 'vision-title':
                        continue
                    chunk_text = chunk['text_clean']
                    title_numbers = {
                        DRAWING_SPACED_NUMBER.sub(r'\1\2', number)
                        for number in DRAWING_TITLE_NUMBER.findall(chunk_text)
                    }
                    exact_count = len(drawing_numbers.intersection(title_numbers))
                    if exact_count:
                        sc = (exact_count, sum(bool(pattern.search(chunk_text)) for pattern in patterns))
                        if sc > best_score:
                            best, best_score = chunk, sc
                if best is not None:
                    drawing_chunks[doc_id] = best
                    if best_score == (len(drawing_numbers), len(patterns)):
                        priority_ids.add(doc_id)
            if qc_title_patterns and r['extension'] == '.pdf' and r['status'] == 'partial' and re.search(r'(?<![a-z0-9])qc(?![a-z0-9])', path):
                for chunk in doc_chunks:
                    if chunk['method'] != 'vision-title':
                        continue
                    if all(pattern.search(chunk['text_clean']) for pattern in qc_title_patterns):
                        drawing_chunks[doc_id] = chunk
                        priority_ids.add(doc_id)
                        break
            if not record and context_patterns and all(pattern.search(body) for pattern in context_patterns):
                for chunk in doc_chunks:
                    chunk_text = chunk['text_clean']
                    if all(pattern.search(chunk_text) for pattern in context_patterns):
                        context_chunks[doc_id] = chunk
                        break
            if (length_patterns and doc_id not in context_chunks and r['extension'] in ('.xls', '.xlsx', '.xlsm')
                    and all(pattern.search(body) for pattern in equipment_patterns)):
                split_length_rows.add(doc_id)

        hits.append(hit)
        matched_ids.add(doc_id)

    browse_mode = not (terms or parsed['phrases'] or parsed['or_groups'])
    effective_sort = 'mtime_desc' if (browse_mode and sort_by == 'score') else sort_by

    if effective_sort == 'mtime_desc':
        hits.sort(key=lambda x: (-int(x.get('mtime') or 0), -x['score'], x['path']))
    elif effective_sort == 'name_asc':
        hits.sort(key=lambda x: (Path(x['path']).name.lower(), -x['score'], x['path']))
    elif numeric_context:
        hits.sort(key=lambda x: (x['id'] not in priority_ids, x['id'] not in context_chunks, -x['score'], x['path']))
    else:
        hits.sort(key=lambda x: (x['id'] not in priority_ids, -x['score'], x['path']) if drawing_numbers or qc_title_patterns
                  else (-x['score'], x['path']))

    hits = group_related(None, hits)
    facets = compute_facets(hits, active_folder=folder_clean)

    all_snippet_patterns = patterns + [re.compile(re.escape(ph)) for ph in phrases] + [
        re.compile(re.escape(alt)) for grp in or_groups for alt in grp
    ]
    page_hits = hits[offset:offset + limit]

    for h in page_hits:
        if h.get('kind') == 'mail':
            m_entry = mail_by_id.get(h['id'])
            h['path_confirmed'] = True
            h['original_available'] = False
            loc = f"메일 · {h.get('year', '')}년" + (f" ({h['source']})" if h.get('source') else '')
            snippet = mail_snippet(m_entry, all_snippet_patterns, browse_mode=browse_mode) if m_entry else ''
            h.update(location=loc, snippet=snippet, method='mail')
            continue
        if h.get('kind') == 'folder':
            h['path_confirmed'] = True
            h['original_available'] = False
            h.update(location='업무 폴더', snippet=h['path'], method='folder')
            continue
        h['path_confirmed'] = True
        h['original_available'] = h['extension'] in ORIGINAL_EXTENSIONS
        for original in h.get('related', {}).get('originals', ()):
            original['path_confirmed'] = True
            original['original_available'] = original['extension'] in ORIGINAL_EXTENSIONS
        if h.get('method') == 'record':
            continue
        chunks = chunks_by_doc.get(h['id'], ())
        if h.get('period_label'):
            for chunk in chunks:
                if chunk['location'].strip().startswith('보고자료') and '장비 고장 통계' in chunk['text']:
                    stamp = re.search(r'\bS4=(\d{4}-\d{2}-\d{2})', chunk['text'])
                    if stamp:
                        h['period_label'] += f" · 시트 표시일 {stamp[1]} (S4)"
                        break
        matching = []
        row_length_patterns = length_patterns.values() if h['id'] in split_length_rows else ()
        for chunk in chunks:
            chunk_text = chunk['text_clean']
            positions = [match.start() for pattern in all_snippet_patterns if (match := pattern.search(chunk_text))]
            length_positions = [match.start() for pattern in row_length_patterns if (match := pattern.search(chunk_text))]
            if positions:
                matching.append((len(positions), len(length_positions), min(length_positions or positions), chunk))
        if matching:
            drawing = drawing_chunks.get(h['id'])
            context = drawing or context_chunks.get(h['id'])
            context_match = next((match for match in matching if match[3] is context), None) if context is not None else None
            _, length_count, pos, best = context_match or max(matching, key=lambda match: (match[1], match[0]))
            loc, text, method = best['location'], best['text'], best['method']
            snippet = text[max(0, pos - 70):pos + 450]
            if drawing and drawing_numbers:
                h['evidence_label'] = '표제란의 도면 번호 일치'
            elif drawing:
                h['evidence_label'] = 'QC 경로와 도면 표제란 일치'
            elif h['id'] in context_chunks:
                label = '같은 페이지' if method.startswith('pdf') or 'vision' in method else '같은 행' if method in ('xls', 'xlsx') else '같은 본문 구간'
                h['evidence_label'] = f'{label}에서 검색어 일치'
            elif length_count:
                h['evidence_label'] = '측정값이 있는 행 · 장비번호는 문서 내 일치'
        else:
            if browse_mode:
                loc = '폴더·조건 탐색'
                snippet = (h['path'].rsplit('/', 1)[0] if '/' in h['path'] else '최상위 폴더')
                method = 'browse'
            else:
                loc = '파일명·경로 검색'
                snippet = '검색어가 파일명 또는 경로에 있습니다.'
                method = 'filename'
        h.update(location=loc, snippet=snippet, method=method)

    highlight_terms = sorted(
        {t for t, _ in terms if len(t) >= 1}
        | {ph for ph in parsed['phrases'] if len(ph) >= 1}
        | {alt for grp in parsed['or_groups'] for alt in grp if len(alt) >= 1},
        key=len,
        reverse=True,
    )

    outside_folder_total = len(outside_matched_ids)
    return {
        'total': len(hits),
        'outside_folder_total': outside_folder_total,
        'global_total': len(hits) + outside_folder_total,
        'offset': offset,
        'limit': limit,
        'has_more': (offset + len(page_hits)) < len(hits),
        'returned': len(page_hits),
        'limited': False,
        'hits': page_hits,
        'facets': facets,
        'highlight_terms': highlight_terms,
        'browse_mode': browse_mode,
    }


def stats():
    c = connect()
    counts = {r[0]: r[1] for r in c.execute('select status,count(*) from docs where present=1 group by status')}
    meta = {r[0]: r[1] for r in c.execute('select * from state')}
    c.close()
    mails_cache, _ = get_mail_cache()
    return dict(total=sum(counts.values()), mail_total=len(mails_cache), counts=counts, labels=LABELS, state=meta, root_name=ROOT.name)


def top_folders():
    c = connect()
    rows = c.execute("""
        SELECT CASE WHEN instr(path, '/') > 0 THEN substr(path, 1, instr(path, '/') - 1) ELSE '(루트)' END AS folder,
               COUNT(*) AS cnt,
               COALESCE(SUM(size), 0) AS total_size
        FROM docs
        WHERE present = 1
        GROUP BY folder
        ORDER BY cnt DESC, folder ASC
    """).fetchall()
    c.close()
    folders = [{'name': r['folder'], 'path': '' if r['folder'] == '(루트)' else r['folder'], 'count': r['cnt'], 'size': r['total_size']} for r in rows]
    mails_cache, _ = get_mail_cache()
    if mails_cache:
        folders.insert(0, {'name': '✉️ 메일보관 (전체)', 'path': '메일보관', 'count': len(mails_cache), 'size': sum(m['size'] for m in mails_cache)})
    return folders


def run_scan_once():
    if not scan_lock.acquire(blocking=False):
        return False
    try:
        if ROOT.is_dir():
            scan()
            with _CATALOG_LOCK:
                _CATALOG_CACHE['checked_at'] = 0.0
            get_mail_cache(force_check=True)
        else:
            c = connect()
            state(c, 'scan_running', '0')
            state(c, 'worker_error', '업무 폴더에 연결할 수 없습니다. Google Drive와 선택한 폴더를 확인해 주세요.')
            c.commit()
            c.close()
    except Exception as e:
        c = connect()
        state(c, 'scan_running', '0')
        state(c, 'worker_error', str(e)[:500])
        c.commit()
        c.close()
    finally:
        scan_lock.release()
    return True


def worker():
    while True:
        run_scan_once()
        time.sleep(600)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def allowed_request(self):
        port = self.server.server_port
        return self.headers.get_all('Host') in ([f'127.0.0.1:{port}'], [f'localhost:{port}'])

    def allowed_origin(self):
        port = self.server.server_port
        return self.headers.get('Origin', '') in (f'http://127.0.0.1:{port}', f'http://localhost:{port}')

    def send(self, data, typ='application/json; charset=utf-8', status=200, frame_ancestors="'none'"):
        csp = f"default-src 'self'; img-src 'self' data:; frame-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors {frame_ancestors}"
        try:
            self.send_response(status)
            self.send_header('Content-Type', typ)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', csp)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def parse_post_json(self):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 1024:
            return None
        return json.loads(self.rfile.read(length))

    def resolve_target_path(self, doc_id, att_index=None, allowed_exts=None):
        if doc_id <= -FOLDER_ID_OFFSET - 1:
            folder_id = -doc_id - FOLDER_ID_OFFSET
            c = connect()
            r = c.execute('select realpath from folders where id=? and present=1', (folder_id,)).fetchone()
            c.close()
            if not r:
                return None, 404
            p = Path(r['realpath']).resolve()
            if not p.is_relative_to(ROOT.resolve()) or not p.is_dir():
                return None, 404
            return p, 200
        if doc_id < 0:
            if att_index is not None and (type(att_index) is not int or att_index < 0):
                return None, 400
            p = resolve_mail_target_path(doc_id, att_index)
            if p is None:
                return None, 404
            if allowed_exts is not None and p.suffix.lower() not in allowed_exts:
                return None, 415
            return p, 200
        c = connect()
        r = c.execute('select realpath,extension from docs where id=? and present=1', (doc_id,)).fetchone()
        c.close()
        if not r or (allowed_exts is not None and r['extension'] not in allowed_exts):
            return None, 415 if r else 404
        p = Path(r['realpath']).resolve()
        if not p.is_relative_to(ROOT.resolve()) or not p.is_file():
            return None, 404
        return p, 200

    def do_POST(self):
        if not self.allowed_request():
            return self.send(b'Forbidden', status=403)
        if not self.allowed_origin() or self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            return self.send(b'Forbidden', status=403)
        route = urlparse(self.path).path
        if route == '/api/rescan':
            try:
                payload = self.parse_post_json()
                if payload is None or not isinstance(payload, dict):
                    return self.send(b'Bad request', status=400)
            except (ValueError, json.JSONDecodeError):
                return self.send(b'Bad request', status=400)
            if scan_lock.locked():
                return self.send(json.dumps({'ok': False, 'started': False, 'message': '이미 목록을 확인하고 있습니다.'}, ensure_ascii=False).encode())
            threading.Thread(target=run_scan_once, daemon=True).start()
            return self.send(json.dumps({'ok': True, 'started': True, 'message': '목록 갱신을 시작했습니다.'}, ensure_ascii=False).encode())

        if route not in ('/api/open-local', '/api/open-folder'):
            return self.send(b'Not found', status=404)
        try:
            payload = self.parse_post_json()
            if not isinstance(payload, dict):
                return self.send(b'Bad request', status=400)
            doc_id = payload.get('id')
            att_index = payload.get('att_index')
            if type(doc_id) is not int or doc_id == 0:
                return self.send(b'Bad request', status=400)
            if att_index is not None and (type(att_index) is not int or att_index < 0):
                return self.send(b'Bad request', status=400)
            allowed_exts = OPEN_LOCAL_EXTENSIONS if route == '/api/open-local' else None
            p, status_code = self.resolve_target_path(doc_id, att_index=att_index, allowed_exts=allowed_exts)
            if status_code == 415:
                return self.send('이 형식은 제목에서 열 수 없습니다. 파일 위치를 확인해 주세요.'.encode(), 'text/plain; charset=utf-8', 415)
            if status_code != 200 or p is None:
                return self.send('원본 위치를 확인할 수 없습니다. Google Drive 연결과 파일 위치를 확인해 주세요.'.encode(), 'text/plain; charset=utf-8', 404)
            if route == '/api/open-local':
                if os.name == 'nt':
                    rc = ctypes.windll.shell32.ShellExecuteW(None, 'open', str(p), None, str(p.parent), 1)
                    if rc <= 32:
                        raise OSError(f'ShellExecuteW failed: {rc}')
                elif hasattr(os, 'startfile'):
                    os.startfile(str(p))
                else:
                    return self.send('이 기능은 Windows PC에서 사용할 수 있습니다.'.encode(), 'text/plain; charset=utf-8', 501)
            else:
                if os.name != 'nt':
                    return self.send('이 기능은 Windows PC에서 사용할 수 있습니다.'.encode(), 'text/plain; charset=utf-8', 501)
                if '"' in str(p):
                    raise OSError('invalid path')
                subprocess.Popen(f'explorer.exe /select,"{p}"')
            return self.send(json.dumps({'ok': True}).encode())
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (ValueError, KeyError, json.JSONDecodeError):
            return self.send(b'Bad request', status=400)
        except OSError:
            return self.send('파일을 열 수 없습니다. Google Drive 연결과 해당 파일을 여는 프로그램을 확인해 주세요.'.encode(), 'text/plain; charset=utf-8', 503)

    def do_GET(self):
        if not self.allowed_request():
            return self.send(b'Forbidden', status=403)
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == '/':
                return self.send((BASE / 'index.html').read_bytes(), 'text/html; charset=utf-8')
            if u.path == '/manifest.webmanifest':
                return self.send((BASE / 'manifest.webmanifest').read_bytes(), 'application/manifest+json; charset=utf-8')
            if u.path in ('/icon-180.png', '/icon-192.png', '/icon-512.png'):
                return self.send((BASE / u.path.lstrip('/')).read_bytes(), 'image/png')
            if u.path == '/api/status':
                return self.send(json.dumps(stats(), ensure_ascii=False).encode())
            if u.path == '/api/folders':
                return self.send(json.dumps({'folders': top_folders()}, ensure_ascii=False).encode())
            if u.path == '/api/search':
                query = q.get('q', [''])[0][:300]
                category = q.get('category', [''])[0]
                scope = q.get('scope', ['all'])[0]
                ext_filter = q.get('ext', [''])[0]
                sort_by = q.get('sort', ['score'])[0]
                folder = q.get('folder', [''])[0][:300]
                exclude_mail = q.get('exclude_mail', [''])[0] in ('1', 'true', 'yes')
                offset = max(0, int(q.get('offset', ['0'])[0]))
                limit = min(MAX_LIMIT, max(10, int(q.get('limit', [str(DEFAULT_LIMIT)])[0])))

                parsed = parse_advanced_query(query)
                has_filter = bool(
                    folder or category or ext_filter or scope == 'body' or exclude_mail
                    or parsed['excludes'] or parsed['path_filters'] or parsed['ext_tokens']
                    or parsed['phrases'] or parsed['or_groups']
                )
                has_term = any(len(t) >= 1 for t in clean(parsed['positive_query']).split())
                if not has_term and not has_filter:
                    return self.send(json.dumps({
                        'total': 0, 'hits': [], 'facets': None,
                        'message': '검색어를 입력하거나 왼쪽 폴더·필터를 선택해 주세요.',
                    }, ensure_ascii=False).encode())

                result = find(
                    query, category=category, scope=scope,
                    ext_filter=ext_filter, sort_by=sort_by,
                    folder=folder, offset=offset, limit=limit,
                    exclude_mail=exclude_mail,
                )
                return self.send(json.dumps(result, ensure_ascii=False).encode())

            if u.path in ['/api/excel-preview', '/api/excel-image']:
                doc_id = int(q['id'][0])
                att_index = int(q['att'][0]) if 'att' in q else None
                p, status_code = self.resolve_target_path(doc_id, att_index=att_index, allowed_exts=('.xlsx', '.xlsm', '.xls'))
                if status_code != 200 or p is None:
                    return self.send(b'Not found', status=404)
                ext = p.suffix.lower()
                st = p.stat()
                if st.st_size > 20000000:
                    return self.send('파일이 커서 미리보기할 수 없습니다. 원본을 열어 주세요.'.encode(), 'text/plain; charset=utf-8', 413)
                if u.path == '/api/excel-image':
                    if ext == '.xls':
                        return self.send(b'Not found', status=404)
                    index = int(q.get('n', ['-1'])[0])
                    with zipfile.ZipFile(p) as z:
                        images = [
                            x for x in z.infolist()
                            if x.filename.startswith('xl/media/')
                            and Path(x.filename).suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif')
                            and x.file_size <= 10000000
                        ][:12]
                        if index < 0 or index >= len(images):
                            return self.send(b'Not found', status=404)
                        item = images[index]
                        data = z.read(item)
                        typ = mimetypes.guess_type(item.filename)[0] or 'application/octet-stream'
                    return self.send(data, typ)
                sheet = int(q.get('sheet', ['0'])[0])
                start = int(q.get('start', ['1'])[0])
                if not 0 <= sheet < 500 or not 1 <= start <= 20001:
                    return self.send(b'Invalid request', status=400)
                try:
                    kwargs = {'capture_output': True, 'timeout': 30}
                    if os.name == 'nt':
                        kwargs['creationflags'] = 0x08000000
                    preview = subprocess.run([sys.executable, str(BASE / 'preview_excel.py'), str(p), str(sheet), str(start)], **kwargs)
                    if preview.returncode:
                        raise ValueError('preview failed')
                    if (p.stat().st_size, p.stat().st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                        raise ValueError('file changed')
                    return self.send(preview.stdout)
                except (OSError, ValueError, subprocess.SubprocessError):
                    return self.send('엑셀 미리보기를 열 수 없습니다. 원본을 확인해 주세요.'.encode(), 'text/plain; charset=utf-8', 503)

            if u.path in ['/api/detail', '/original']:
                doc_id = int(q['id'][0])
                if doc_id <= -FOLDER_ID_OFFSET - 1:
                    return self.send(b'Not found', status=404)
                if doc_id < 0:
                    if u.path == '/api/detail':
                        m = get_mail_by_id(doc_id)
                        if not m:
                            return self.send(b'Not found', status=404)
                        payload = {
                            'path': m['path'],
                            'kind': 'mail',
                            'subject': m['subject'],
                            'sender': m['sender'],
                            'date': m['date'],
                            'source': m['source'],
                            'attachments': [
                                {'index': i, 'name': a.get('name', ''), 'ext': Path(a.get('name', '')).suffix.lower()}
                                for i, a in enumerate(m['attachments'])
                            ],
                            'parts': [{'location': f"발신: {m['sender']} · {m['date']}", 'text': m['body_text'], 'method': 'mail'}],
                            'status': '메일 자료',
                            'error': '',
                        }
                        return self.send(json.dumps(payload, ensure_ascii=False).encode())
                    att_index = int(q['att'][0]) if 'att' in q else None
                    p, status_code = self.resolve_target_path(doc_id, att_index=att_index, allowed_exts=ORIGINAL_EXTENSIONS)
                    if status_code != 200 or p is None:
                        return self.send(b'Not found', status=404)
                else:
                    c = connect()
                    r = c.execute('select * from docs where id=? and present=1', (doc_id,)).fetchone()
                    if not r:
                        c.close()
                        return self.send(b'Not found', status=404)
                    if u.path == '/api/detail':
                        parts = [dict(location=a, text=b, method=m) for a, b, m in c.execute('select location,text,method from chunks where doc_id=?', (r['id'],))]
                        c.close()
                        return self.send(json.dumps({'path': r['path'], 'parts': parts, 'status': LABELS[r['status']], 'error': r['error']}, ensure_ascii=False).encode())
                    c.close()
                    p = Path(r['realpath']).resolve()
                    if not p.is_relative_to(ROOT.resolve()) or not p.is_file() or p.suffix.lower() not in ORIGINAL_EXTENSIONS:
                        return self.send(b'Not found', status=404)
                typ = mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
                disp = 'inline' if p.suffix.lower() == '.pdf' else 'attachment'
                try:
                    f = p.open('rb')
                    st = os.fstat(f.fileno())
                    # Fetch cloud-backed content before sending headers; a Drive failure must not
                    # be delivered as a truncated, apparently successful file.
                    if getattr(st, 'st_flags', 0) & 0x40000000:
                        while f.read(1024 * 1024):
                            pass
                        f.seek(0)
                    else:
                        f.read(1)
                        f.seek(0)
                    after = os.fstat(f.fileno())
                    if (st.st_size, st.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise OSError('file changed while opening')
                except OSError:
                    try:
                        f.close()
                    except (NameError, UnboundLocalError):
                        pass
                    message = (
                        '현재 PC에서 Google Drive 원본을 읽을 수 없습니다. '
                        'Windows의 Google Drive 연결을 확인하고 파일을 오프라인 사용 가능으로 설정한 뒤 다시 시도해 주세요.'
                    ).encode('utf-8')
                    return self.send(message, 'text/plain; charset=utf-8', 503)
                with f:
                    self.send_response(200)
                    self.send_header('Content-Type', typ)
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'self'")
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', str(after.st_size))
                    self.send_header('Content-Disposition', disp + "; filename*=UTF-8''" + quote(p.name))
                    self.end_headers()
                    try:
                        shutil.copyfileobj(f, self.wfile, 1024 * 1024)
                    except OSError:
                        return
                return

            self.send(b'Not found', status=404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except (ValueError, KeyError, FileNotFoundError):
            self.send(b'Not found', status=404)
        except Exception:
            try:
                self.send(b'Request failed', status=500)
            except (ConnectionError, OSError):
                pass


class QuietHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def warm_cache():
    try:
        c = connect()
        get_catalog_cache(c)
        group_related(c, [])
        c.close()
    except Exception:
        pass
    try:
        get_mail_cache()
    except Exception:
        pass


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--search-only', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    init()
    threading.Thread(target=warm_cache, daemon=True).start()
    with QuietHTTPServer(('127.0.0.1', args.port), Handler) as server:
        if os.environ.get('HPNT_OPEN_BROWSER') == '1':
            import webbrowser
            threading.Timer(.2, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}/')).start()
        if not args.search_only:
            threading.Thread(target=worker, daemon=True).start()
        print(f'HPNT search: http://127.0.0.1:{args.port}', flush=True)
        server.serve_forever()
