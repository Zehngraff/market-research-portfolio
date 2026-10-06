"""Offline reconciliation of raw hashes, indexed rows and committed page evidence."""
import gzip
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from .archive import fingerprint
from .validation import validate_page

RAW_NAME = re.compile(r'raw/[0-9a-f]{64}\.json\.gz\Z')


def verify(root):
    root = Path(root)
    problems = []
    cache = {}
    files = sorted((root / 'raw').glob('*.json.gz'))
    for path in files:
        relative = path.relative_to(root).as_posix()
        try:
            if path.is_symlink():
                raise ValueError('raw symlinks are not allowed')
            raw = path.read_bytes()
            if not RAW_NAME.fullmatch(relative) or hashlib.sha256(raw).hexdigest() != path.name.split('.')[0]:
                raise ValueError('content hash mismatch')
            with gzip.open(path, 'rb') as handle:
                uncompressed = handle.read(2_000_001)
            if len(uncompressed) > 2_000_000:
                raise ValueError('decompressed response exceeds verification budget')
            envelope = json.loads(uncompressed)
            if not isinstance(envelope, dict) or not all(k in envelope for k in ('response','received_at','source_label','url','params')):
                raise ValueError('invalid archive envelope')
            cache[relative] = envelope
        except (OSError, ValueError, EOFError) as exc:
            problems.append(dict(file=relative, error=str(exc)))
    rows_checked = 0
    pages_checked = 0
    if not (root / 'index.db').is_file():
        problems.append(dict(error='missing SQLite index'))
    else:
        # Read-only URI: verification never creates or changes an index.
        con = sqlite3.connect((root / 'index.db').resolve().as_uri() + '?mode=ro', uri=True)
        try:
            if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                problems.append(dict(error='SQLite integrity check failed'))
            if con.execute('PRAGMA foreign_key_check').fetchall():
                problems.append(dict(error='SQLite foreign-key reconciliation failed'))
            indexed_identities = set()
            committed_identities = set()
            prior_cursors = {}
            expected_history = {}
            for fp, condition, ts, raw in con.execute('SELECT fingerprint,condition,ts,raw_file FROM rows'):
                rows_checked += 1
                indexed_identities.add((fp, condition, ts))
                envelope = cache.get(raw)
                try:
                    if not envelope:
                        raise ValueError('indexed raw response missing or invalid')
                    records, _ = validate_page(envelope['response'], condition, now=envelope['received_at'])
                    if not any(fingerprint(r) == fp and int(r['timestamp']) == ts for r in records):
                        raise ValueError('indexed row not present in archived response')
                except ValueError as exc:
                    problems.append(dict(fingerprint=fp, error=str(exc)))
            for condition, cursor, nxt, raw, count in con.execute(
                    'SELECT condition,request_cursor,next_cursor,raw_file,row_count FROM pages ORDER BY id'):
                pages_checked += 1
                envelope = cache.get(raw)
                try:
                    if not envelope:
                        raise ValueError('committed page raw response missing or invalid')
                    records, observed_next = validate_page(envelope['response'], condition, now=envelope['received_at'])
                    if (observed_next != nxt or len(records) != count or
                            envelope['params'].get('cursor') != cursor or
                            envelope['params'].get('condition') != condition):
                        raise ValueError('committed page differs from raw evidence')
                    committed_identities.update((fingerprint(r), condition, int(r['timestamp'])) for r in records)
                    history = expected_history.setdefault(condition, set())
                    if cursor != prior_cursors.get(condition) or (nxt and nxt in history):
                        raise ValueError('committed cursor chain is inconsistent')
                    prior_cursors[condition] = nxt
                    if nxt:
                        history.add(nxt)
                    else:
                        history.clear()
                except (ValueError, AttributeError) as exc:
                    problems.append(dict(condition=condition, error=str(exc)))
            if indexed_identities != committed_identities:
                problems.append(dict(error='committed page/index identity sets differ',
                                     missing_indexed_rows=len(committed_identities-indexed_identities),
                                     uncommitted_indexed_rows=len(indexed_identities-committed_identities)))
            for condition, cursor, exhausted in con.execute('SELECT condition,cursor,exhausted FROM markets'):
                last = con.execute('SELECT next_cursor FROM pages WHERE condition=? ORDER BY id DESC LIMIT 1', (condition,)).fetchone()
                if last is None:
                    if cursor is not None or exhausted:
                        problems.append(dict(condition=condition, error='checkpoint has no committed page evidence'))
                elif cursor != last[0] or bool(exhausted) != (last[0] is None):
                    problems.append(dict(condition=condition, error='checkpoint differs from latest committed page'))
                seen = {r[0] for r in con.execute('SELECT cursor FROM cursor_history WHERE condition=?', (condition,))}
                if seen != expected_history.get(condition, set()):
                    problems.append(dict(condition=condition, error='cursor history inconsistent with checkpoint'))
        except sqlite3.DatabaseError as exc:
            problems.append(dict(error=f'SQLite schema or query failure: {exc}'))
        finally:
            con.close()
    if not files or not rows_checked or not pages_checked:
        problems.append(dict(error='archive has insufficient committed evidence'))
    return dict(passed=not problems, raw_files_checked=len(files), indexed_rows_checked=rows_checked,
                committed_pages_checked=pages_checked, problems=problems,
                scope='Hash and index reconciliation only; not completeness, timing integrity, or profitability.')
