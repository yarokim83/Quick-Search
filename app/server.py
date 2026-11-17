import sqlite3, re, json, mimetypes, os, shutil, time, threading, subprocess, sys, zipfile
from collections import Counter
from itertools import chain
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote

APP_DIR = str(Path(__file__).resolve().parent)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from catalog import BASE, DB, ROOT, connect, clean, grams, init, scan, seed, process, state, norm
from evidence import has_date, record_query, record_evidence, report_period
from related import group_related

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
}
ORIGINAL_EXTENSIONS = frozenset((
    '.pdf', '.xlsx', '.xlsm', '.xls', '.docx', '.doc', '.hwp', '.hwpx',
    '.txt', '.rtf', '.lis', '.list', '.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff',
    '.avi', '.mp4', '.webm', '.dwg', '.dxf', '.zip', '.rar', '.alz', '.pptx', '.ppt',
))
OPEN_LOCAL_EXTENSIONS = ORIGINAL_EXTENSIONS | frozenset(('.msg', '.eml', '.csv', '.md', '.json'))
scan_lock = threading.Lock()

QC_PREFIX = r'(?<![a-z0-9])q/?c[ \t]*[-‐‑–]?[ \t]*'
QC_NUMBER = re.compile(QC_PREFIX + r'([0-9]{3})(?![a-z0-9])(?:호기?)?')
LENGTH_VALUE = re.compile(r'(?<!\S)(\d+(?:[.,]\d+)?)[ \t]*(mm|cm|m)(?!\S)')
DRAWING_NUMBER = re.compile(r'jr\d[0-9a-z]*|j\d{3}[a-z][a-z]?\d{4,}|fh\d+\.\d+\.[a-z0-9]+-\d+')
DRAWING_SPACED_NUMBER = re.compile(r'(?<![a-z0-9])(j\d{3}[a-z])[ \t]*([a-z]\d{4,})(?![a-z0-9])')
DRAWING_TITLE_NUMBER = re.compile(r'^([^\n·]+?)[ \t]+·', re.MULTILINE)
TOKEN_SCANNER = re.compile(r'([!-]?)"([^"]+)"|(\S+)')


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
    spaced = re.sub(r'\s*\|\s*', ' | ', raw_query.strip())
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
        if (low.startswith('-') or low.startswith('!')) and len(low) > 1:
            excludes.append(low[1:])
            continue
        if low.startswith('path:') and len(low) > 5:
            p_val = low[5:].replace('\\', '/').strip('/')
            if p_val:
                path_filters.append(p_val)
            continue
        if low.startswith('ext:') and len(low) > 4:
            for item in low[4:].split(','):
                ext = item.strip()
                if ext:
                    ext_tokens.append(ext if ext.startswith('.') else '.' + ext)
            continue
        raw_items.append(val)

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


def find(query='', category='', scope='all', ext_filter='', sort_by='score', folder='', offset=0, limit=DEFAULT_LIMIT):
    parsed = parse_advanced_query(query)
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

    exclude_patterns = [re.compile(re.escape(ex)) for ex in parsed['excludes']]
    phrase_patterns = [re.compile(re.escape(ph)) for ph in parsed['phrases']]
    or_patterns = [
        [re.compile(re.escape(alt)) for alt in group]
        for group in parsed['or_groups']
    ]

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

    fts_expr = fts_match_expr(terms, lengths, drawing_parts, parsed['phrases'], parsed['or_groups'])

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

    folder_clean = norm(folder).strip('/')

    c = connect()
    clauses = ['d.present=1']
    params = []
    source = 'docs d'
    if fts_expr:
        source = 'search join docs d on d.id=search.rowid'
        clauses.append('search match ?')
        params.append(fts_expr)
    if category:
        clauses.append('d.category=?')
        params.append(category)
    if allowed_exts:
        ph = ','.join('?' for _ in allowed_exts)
        clauses.append(f'd.extension in ({ph})')
        params.extend(allowed_exts)
    if folder_clean:
        clauses.append("(d.path=? or d.path like ? escape '\\')")
        params.extend([folder_clean, escape_like(folder_clean) + '/%'])
    if scope == 'body':
        clauses.append("d.status in ('ready','partial')")

    rows = c.execute(f"select d.*, null as matched_alias from {source} where {' and '.join(clauses)}", params)
    if scope != 'body':
        alias_clauses = ["(?='' or d.category=?)"]
        alias_params = [category, category]
        if allowed_exts:
            ph = ','.join('?' for _ in allowed_exts)
            alias_clauses.append(f'd.extension in ({ph})')
            alias_params.extend(allowed_exts)
        if folder_clean:
            alias_clauses.append("(d.path=? or d.path like ? escape '\\')")
            alias_params.extend([folder_clean, escape_like(folder_clean) + '/%'])
        aliases = c.execute(f"""
            select d.*, a.path as matched_alias
            from photo_aliases p
            join docs a on a.id=p.source_id and a.present=0 and a.size=p.size and a.mtime=p.source_mtime
            join docs d on d.id=p.target_id and d.present=1 and d.size=p.size and d.mtime=p.target_mtime
            where {' and '.join(alias_clauses)}
        """, alias_params)
        rows = chain(rows, aliases)

    hits = []
    matched_ids = set()
    context_chunks = {}
    drawing_chunks = {}
    split_length_rows = set()
    priority_ids = set()

    for r in rows:
        if r['id'] in matched_ids:
            continue
        body = clean(r['body'])
        path = clean(r['matched_alias'] or r['path'])
        if parsed['path_filters'] and not all(pf in path for pf in parsed['path_filters']):
            continue
        text = body if scope == 'body' else path + '\n' + body
        if exclude_patterns and any(ep.search(text) for ep in exclude_patterns):
            continue
        if not all(pattern.search(text) for pattern in patterns):
            continue
        if phrase_patterns and not all(pp.search(text) for pp in phrase_patterns):
            continue
        if or_patterns and not all(any(alt.search(text) for alt in group) for group in or_patterns):
            continue
        if record and not has_date(text, record[1]):
            continue

        score_patterns = patterns + phrase_patterns + [alt for grp in or_patterns for alt in grp]
        score = sum(min(3, len(p.findall(body))) for p in score_patterns)
        if scope != 'body':
            name = clean(Path(r['path']).name)
            score += sum(8 * bool(p.search(name)) + 2 * bool(p.search(path)) for p in score_patterns)

        hit = dict(
            id=r['id'], path=r['path'], category=r['category'], status=r['status'],
            status_label=LABELS[r['status']], score=score, extension=r['extension'],
            size=r['size'], mtime=r['mtime'],
        )
        if r['matched_alias']:
            hit['matched_alias'] = r['matched_alias']
        if positive_query:
            period = report_period(r['path'], positive_query)
            if period:
                hit['period_label'] = period['label']
                if period['matches']:
                    hit['score'] += 100
                    priority_ids.add(r['id'])
        if record:
            chunks = c.execute('select location,text,method from chunks where doc_id=?', (r['id'],)).fetchall()
            proof = record_evidence(chunks, *record)
            if proof:
                hit.update(location=proof['location'], snippet=proof['snippet'], evidence_label=proof['label'], method='record')
                hit['score'] += 100
                priority_ids.add(r['id'])
            else:
                hit['evidence_label'] = '문서 내 검색어 일치 · 같은 기록은 미확인'
        if drawing_numbers and r['extension'] == '.pdf' and r['status'] == 'partial':
            best = None
            best_score = (0, 0)
            for chunk in c.execute(
                "select location,text,method from chunks where doc_id=? and method='vision-title' order by rowid",
                (r['id'],),
            ):
                chunk_text = clean(chunk['text'])
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
                drawing_chunks[r['id']] = best
                if best_score == (len(drawing_numbers), len(patterns)):
                    priority_ids.add(r['id'])
        if qc_title_patterns and r['extension'] == '.pdf' and r['status'] == 'partial' and re.search(r'(?<![a-z0-9])qc(?![a-z0-9])', path):
            for chunk in c.execute("select location,text,method from chunks where doc_id=? and method='vision-title' order by rowid", (r['id'],)):
                if all(pattern.search(clean(chunk['text'])) for pattern in qc_title_patterns):
                    drawing_chunks[r['id']] = chunk
                    priority_ids.add(r['id'])
                    break
        if not record and context_patterns and all(pattern.search(body) for pattern in context_patterns):
            chunks = c.execute('select location,text,method from chunks where doc_id=? order by rowid', (r['id'],))
            for chunk in chunks:
                chunk_text = clean(chunk['text'])
                if all(pattern.search(chunk_text) for pattern in context_patterns):
                    context_chunks[r['id']] = chunk
                    break
        if (length_patterns and r['id'] not in context_chunks and r['extension'] in ('.xls', '.xlsx', '.xlsm')
                and all(pattern.search(body) for pattern in equipment_patterns)):
            split_length_rows.add(r['id'])

        hits.append(hit)
        matched_ids.add(r['id'])

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

    hits = group_related(c, hits)
    facets = compute_facets(hits, active_folder=folder_clean)

    all_snippet_patterns = patterns + phrase_patterns + [alt for grp in or_patterns for alt in grp]
    page_hits = hits[offset:offset + limit]

    for h in page_hits:
        h['path_confirmed'] = (ROOT / h['path']).is_file()
        h['original_available'] = h['extension'] in ORIGINAL_EXTENSIONS and h['path_confirmed']
        for original in h.get('related', {}).get('originals', ()):
            original['path_confirmed'] = (ROOT / original['path']).is_file()
            original['original_available'] = original['extension'] in ORIGINAL_EXTENSIONS and original['path_confirmed']
        if h.get('method') == 'record':
            continue
        chunks = c.execute('select location,text,method from chunks where doc_id=?', (h['id'],)).fetchall()
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
            chunk_text = clean(chunk['text'])
            positions = [match.start() for pattern in all_snippet_patterns if (match := pattern.search(chunk_text))]
            length_positions = [match.start() for pattern in row_length_patterns if (match := pattern.search(chunk_text))]
            if positions:
                matching.append((len(positions), len(length_positions), min(length_positions or positions), chunk))
        if matching:
            drawing = drawing_chunks.get(h['id'])
            context = drawing or context_chunks.get(h['id'])
            context_match = next((match for match in matching if match[3] == context), None)
            _, length_count, pos, best = context_match or max(matching, key=lambda match: (match[1], match[0]))
            loc, text, method = best
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
        {t for t, _ in terms if len(t) >= 2}
        | {ph for ph in parsed['phrases'] if len(ph) >= 2}
        | {alt for grp in parsed['or_groups'] for alt in grp if len(alt) >= 2},
        key=len,
        reverse=True,
    )

    c.close()
    return {
        'total': len(hits),
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
    return dict(total=sum(counts.values()), counts=counts, labels=LABELS, state=meta, root_name=ROOT.name)


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
    return [{'name': r['folder'], 'path': '' if r['folder'] == '(루트)' else r['folder'], 'count': r['cnt'], 'size': r['total_size']} for r in rows]


def run_scan_once():
    if not scan_lock.acquire(blocking=False):
        return False
    try:
        if ROOT.is_dir():
            scan()
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

    def allowed_request(self):
        port = self.server.server_port
        return self.headers.get_all('Host') in ([f'127.0.0.1:{port}'], [f'localhost:{port}'])

    def allowed_origin(self):
        port = self.server.server_port
        return self.headers.get('Origin', '') in (f'http://127.0.0.1:{port}', f'http://localhost:{port}')

    def send(self, data, typ='application/json; charset=utf-8', status=200, frame_ancestors="'none'"):
        csp = f"default-src 'self'; img-src 'self' data:; frame-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors {frame_ancestors}"
        self.send_response(status)
        self.send_header('Content-Type', typ)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', csp)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def parse_post_json(self):
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 < length <= 1024:
            return None
        return json.loads(self.rfile.read(length))

    def resolve_doc_path(self, doc_id, allowed_exts=None):
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
            if type(doc_id) is not int or doc_id < 1:
                return self.send(b'Bad request', status=400)
            allowed_exts = OPEN_LOCAL_EXTENSIONS if route == '/api/open-local' else None
            p, status_code = self.resolve_doc_path(doc_id, allowed_exts=allowed_exts)
            if status_code == 415:
                return self.send('이 형식은 제목에서 열 수 없습니다. 파일 위치를 확인해 주세요.'.encode(), 'text/plain; charset=utf-8', 415)
            if status_code != 200 or p is None:
                return self.send('원본 위치를 확인할 수 없습니다. Google Drive 연결과 파일 위치를 확인해 주세요.'.encode(), 'text/plain; charset=utf-8', 404)
            if route == '/api/open-local':
                if not hasattr(os, 'startfile'):
                    return self.send('이 기능은 Windows PC에서 사용할 수 있습니다.'.encode(), 'text/plain; charset=utf-8', 501)
                os.startfile(str(p))
            else:
                if os.name != 'nt':
                    return self.send('이 기능은 Windows PC에서 사용할 수 있습니다.'.encode(), 'text/plain; charset=utf-8', 501)
                if '"' in str(p):
                    raise OSError('invalid path')
                subprocess.Popen(f'explorer.exe /select,"{p}"')
            return self.send(json.dumps({'ok': True}).encode())
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
                offset = max(0, int(q.get('offset', ['0'])[0]))
                limit = min(MAX_LIMIT, max(10, int(q.get('limit', [str(DEFAULT_LIMIT)])[0])))

                parsed = parse_advanced_query(query)
                has_filter = bool(
                    folder or category or ext_filter or scope == 'body'
                    or parsed['path_filters'] or parsed['ext_tokens']
                    or parsed['phrases'] or parsed['or_groups']
                )
                has_term = any(len(t) >= 2 for t in clean(parsed['positive_query']).split())
                if not has_term and not has_filter:
                    return self.send(json.dumps({
                        'total': 0, 'hits': [], 'facets': None,
                        'message': '2글자 이상 검색어를 입력하거나 왼쪽 폴더·필터를 선택해 주세요.',
                    }, ensure_ascii=False).encode())

                result = find(
                    query, category=category, scope=scope,
                    ext_filter=ext_filter, sort_by=sort_by,
                    folder=folder, offset=offset, limit=limit,
                )
                return self.send(json.dumps(result, ensure_ascii=False).encode())

            if u.path in ['/api/excel-preview', '/api/excel-image']:
                c = connect()
                r = c.execute('select realpath,extension from docs where id=? and present=1', (int(q['id'][0]),)).fetchone()
                c.close()
                if not r or r['extension'] not in ('.xlsx', '.xlsm', '.xls'):
                    return self.send(b'Not found', status=404)
                p = Path(r['realpath']).resolve()
                if not p.is_relative_to(ROOT.resolve()) or not p.is_file():
                    return self.send(b'Not found', status=404)
                st = p.stat()
                if st.st_size > 20000000:
                    return self.send('파일이 커서 미리보기할 수 없습니다. 원본을 열어 주세요.'.encode(), 'text/plain; charset=utf-8', 413)
                if u.path == '/api/excel-image':
                    if r['extension'] == '.xls':
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
                    preview = subprocess.run([sys.executable, str(BASE / 'preview_excel.py'), str(p), str(sheet), str(start)], capture_output=True, timeout=30)
                    if preview.returncode:
                        raise ValueError('preview failed')
                    if (p.stat().st_size, p.stat().st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                        raise ValueError('file changed')
                    return self.send(preview.stdout)
                except (OSError, ValueError, subprocess.SubprocessError):
                    return self.send('엑셀 미리보기를 열 수 없습니다. 원본을 확인해 주세요.'.encode(), 'text/plain; charset=utf-8', 503)

            if u.path in ['/api/detail', '/original']:
                c = connect()
                r = c.execute('select * from docs where id=? and present=1', (int(q['id'][0]),)).fetchone()
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
        except (ValueError, KeyError, FileNotFoundError):
            self.send(b'Not found', status=404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.send(b'Request failed', status=500)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--search-only', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    init()
    with ThreadingHTTPServer(('127.0.0.1', args.port), Handler) as server:
        if os.environ.get('HPNT_OPEN_BROWSER') == '1':
            import webbrowser
            threading.Timer(.2, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}/')).start()
        if not args.search_only:
            threading.Thread(target=worker, daemon=True).start()
        print(f'HPNT search: http://127.0.0.1:{args.port}', flush=True)
        server.serve_forever()
