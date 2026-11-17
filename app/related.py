"""Group files whose relationship was confirmed against the source documents."""

import json
from pathlib import Path

from catalog import BASE, ROOT

MTIME_TOLERANCE_NS = 2_000_000


def mtime_matches(actual_ns, expected_ns):
    """Allow minor sub-millisecond timestamp rounding differences across filesystems."""
    return abs(int(actual_ns) - int(expected_ns)) <= MTIME_TOLERANCE_NS


def group_related(connection, hits):
    config = BASE / 'related_documents.json'
    if not config.is_file():
        return hits

    matching_paths = {hit['path'] for hit in hits}
    groups_by_path = {}
    for group in json.loads(config.read_text(encoding='utf-8')):
        if not any(member['path'] in matching_paths for member in group['members']):
            continue
        originals = verified_originals(connection, group['members'])
        if originals:
            relation = {'title': group['title'], 'proof': group['proof'], 'originals': originals}
            for original in originals:
                groups_by_path[original['path']] = relation

    results = []
    displayed_groups = set()
    for hit in hits:
        relation = groups_by_path.get(hit['path'])
        if relation:
            group_key = relation['originals'][0]['path']
            if group_key in displayed_groups:
                continue
            displayed_groups.add(group_key)
            hit['related'] = relation
        results.append(hit)
    return results


def verified_originals(connection, members):
    originals = []
    for member in members:
        row = connection.execute(
            'select id,path,realpath,size,mtime,status,extension from docs where path=? and present=1',
            (member['path'],),
        ).fetchone()
        expected_size, expected_mtime = member['size'], member['mtime']
        if not row or row['size'] != expected_size or not mtime_matches(row['mtime'], expected_mtime):
            return None
        path = Path(row['realpath'])
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
                return None
            info = path.stat()
        except OSError:
            return None
        if not path.is_file() or info.st_size != expected_size or not mtime_matches(info.st_mtime_ns, expected_mtime):
            return None
        originals.append({
            'id': row['id'], 'path': row['path'], 'extension': row['extension'],
            'status': row['status'], 'size': row['size'], 'mtime': row['mtime'],
        })
    return originals
