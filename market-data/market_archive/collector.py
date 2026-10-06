"""Resumable public research collection. Explicit identifiers only; no selection rules."""
import json
import sqlite3
import time
from pathlib import Path

from .archive import Archive, fingerprint
from .transport import GAMMA, CLOB, DATA, parse_jsonish
from .validation import validate_page, book_quote, quote_diagnostics, finite_number

def collect_trades(con, archive, market, report, *, max_pages=2):
    condition = market['condition']
    cursor = market['cursor']
    # Completed open-market traversals restart to capture later prints; overlaps are reported.
    seen_cursors = {r[0] for r in con.execute(
        'SELECT cursor FROM cursor_history WHERE condition=?', (condition,))}
    if cursor:
        seen_cursors.add(cursor)
    counts = dict(condition=condition, source_group=market['source_group'], closed=bool(market['closed']),
                  rows=0, identical_rows=0, pages=0, endpoint_exhausted=False)
    try:
        for _ in range(max_pages):
            params = dict(condition=condition, limit=500)
            if cursor:
                params['cursor'] = cursor
            payload, received, raw = archive.fetch(DATA, '/v2/trades', params)
            rows, next_cursor = validate_page(payload, condition, now=received)
            if next_cursor and next_cursor in seen_cursors:
                raise ValueError('repeated trade cursor')
            with con:
                duplicate_rows = 0
                for row in rows:
                    result = con.execute('INSERT OR IGNORE INTO rows VALUES(?,?,?,?)',
                        (fingerprint(row), condition, int(row['timestamp']), raw))
                    duplicate_rows += 1 - result.rowcount
                con.execute('UPDATE markets SET cursor=?, exhausted=? WHERE condition=?',
                            (next_cursor, next_cursor is None, condition))
                con.execute('INSERT INTO pages(condition,request_cursor,next_cursor,raw_file,row_count) VALUES(?,?,?,?,?)',
                            (condition, cursor, next_cursor, raw, len(rows)))
                if next_cursor:
                    con.execute('INSERT INTO cursor_history VALUES(?,?)', (condition, next_cursor))
                else:
                    con.execute('DELETE FROM cursor_history WHERE condition=?', (condition,))
            counts['identical_rows'] += duplicate_rows
            counts['rows'] += len(rows)
            counts['pages'] += 1
            counts['endpoint_exhausted'] = next_cursor is None
            cursor = next_cursor
            if not cursor:
                break
            seen_cursors.add(cursor)
    except Exception as exc:
        report['errors'].append(dict(stage='trades', condition=condition, error=str(exc)))
    report['trades'].append(counts)

def collect_books(archive, market, report):
    condition = market['condition']
    try:
        payload, _, _ = archive.fetch(GAMMA, '/markets', dict(condition_ids=condition))
        if not isinstance(payload, list) or any(not isinstance(m, dict) for m in payload):
            raise ValueError('invalid market metadata envelope')
        matching = [m for m in payload if m.get('conditionId') == condition]
        if len(matching) > 1:
            raise ValueError('ambiguous market metadata')
        current = matching[0] if matching else None
        if current is None:
            report['book_skips'].append(dict(condition=condition,
                reason='market metadata missing from current Gamma response; book coverage unknown'))
            return
        if current.get('closed') is not False or current.get('acceptingOrders') is not True:
            report['book_skips'].append(dict(condition=condition, reason='not currently accepting orders'))
            return
        tokens = parse_jsonish(current.get('clobTokenIds', []))
        if (not isinstance(tokens, list) or len(tokens) != 2 or
                any(not isinstance(t, str) or not t for t in tokens) or tokens[0] == tokens[1]):
            raise ValueError('expected binary token mapping')
        for token in tokens:
            entry = dict(condition=condition, token=str(token), valid_quote=False, fee_available=False)
            try:
                book, received, raw = archive.fetch(CLOB, '/book', dict(token_id=token))
                entry['book_raw_file'] = raw
                entry['diagnostics'] = quote_diagnostics(book, received)
                quote = book_quote(book, token, received, 1)
                entry.update(valid_quote=True, bid=quote[0], ask=quote[1], spread=quote[1]-quote[0])
            except Exception as exc:
                entry['quote_error'] = str(exc)
            try:
                fee, _, raw = archive.fetch(CLOB, '/fee-rate', dict(token_id=token))
                entry['fee_raw_file'] = raw
                rate = finite_number(fee['base_fee'], 'fee rate')
                if not 0 <= rate <= 10000:
                    raise ValueError('invalid fee rate')
                entry.update(fee_available=True, base_fee=rate)
            except Exception as exc:
                entry['fee_error'] = str(exc)
            report['books'].append(entry)
    except Exception as exc:
        report['errors'].append(dict(stage='books', condition=condition, error=str(exc)))


def database(root):
    Path(root).mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(Path(root) / 'index.db')
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript('''
      CREATE TABLE IF NOT EXISTS markets (
        condition TEXT PRIMARY KEY, event_id TEXT NOT NULL, source_group TEXT NOT NULL,
        closed INTEGER NOT NULL CHECK(closed IN (0,1)), metadata TEXT NOT NULL,
        discovered REAL NOT NULL, last_attempt REAL NOT NULL DEFAULT 0,
        cursor TEXT, exhausted INTEGER NOT NULL DEFAULT 0 CHECK(exhausted IN (0,1)));
      CREATE TABLE IF NOT EXISTS rows (
        fingerprint TEXT PRIMARY KEY, condition TEXT NOT NULL REFERENCES markets(condition),
        ts INTEGER NOT NULL, raw_file TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS pages (
        id INTEGER PRIMARY KEY, condition TEXT NOT NULL REFERENCES markets(condition),
        request_cursor TEXT, next_cursor TEXT, raw_file TEXT NOT NULL, row_count INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS cursor_history (
        condition TEXT NOT NULL REFERENCES markets(condition), cursor TEXT NOT NULL,
        PRIMARY KEY(condition,cursor));
    ''')
    con.row_factory = sqlite3.Row
    return con


def register_markets(con, manifest, *, now):
    """Explicit caller-supplied IDs replace all private universe/strategy discovery."""
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= 100:
        raise ValueError('manifest must contain 1 to 100 explicit markets')
    identities = set()
    for market in manifest:
        if not isinstance(market, dict):
            raise ValueError('invalid manifest market')
        for name in ('condition', 'event_id', 'source_group'):
            if not isinstance(market.get(name), str) or not market[name].strip():
                raise ValueError(f'missing manifest {name}')
        if type(market.get('closed')) is not bool or market['condition'] in identities:
            raise ValueError('invalid closed state or duplicate manifest identity')
        identities.add(market['condition'])
    with con:
        for market in manifest:
            con.execute('''INSERT INTO markets(condition,event_id,source_group,closed,metadata,discovered)
                VALUES(?,?,?,?,?,?) ON CONFLICT(condition) DO UPDATE SET
                event_id=excluded.event_id,source_group=excluded.source_group,
                closed=excluded.closed,metadata=excluded.metadata''',
                (market['condition'], market['event_id'], market['source_group'], market['closed'],
                 json.dumps(market, sort_keys=True), now))


def run(root, manifest, *, archive=None, max_pages=2, include_books=True):
    if type(max_pages) is not int or not 1 <= max_pages <= 10:
        raise ValueError('max_pages must be an integer from 1 to 10')
    archive = archive or Archive(root)
    report = dict(started_at=archive.clock(), mode='RESEARCH_ONLY', source_label=archive.source_label,
                  trades=[], books=[], book_skips=[], errors=[], full_market_coverage=False,
                  strategy_ready=False)
    con = database(root)
    try:
        register_markets(con, manifest, now=archive.clock())
        for requested in manifest:
            market = con.execute('SELECT * FROM markets WHERE condition=?', (requested['condition'],)).fetchone()
            if market['closed'] and market['exhausted']:
                continue
            with con:
                con.execute('UPDATE markets SET last_attempt=? WHERE condition=?',
                            (archive.clock(), market['condition']))
            collect_trades(con, archive, market, report, max_pages=max_pages)
            if include_books and not market['closed']:
                collect_books(archive, market, report)
        report['inventory'] = [dict(r) for r in con.execute(
            'SELECT condition,event_id,source_group,closed,cursor,exhausted FROM markets ORDER BY condition')]
        report['trade_index'] = dict(con.execute(
            'SELECT COUNT(*) fingerprints,MIN(ts) first_ts,MAX(ts) last_ts FROM rows').fetchone())
        report['validated_pages'] = con.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
    finally:
        con.close()
    report.update(files=archive.files, retries=archive.retries, written_bytes=archive.written)
    report['warnings'] = [
        'Explicit input IDs are a sample, not a market universe or a discovery guarantee.',
        'Exact-row fingerprints detect repeated API rows, not uniquely identified exchange fills.',
        'Endpoint exhaustion is not proof of complete exchange history or on-chain reconciliation.',
        'Trade timestamps do not establish missing-feed gaps; absence may reflect no activity.',
        'Books and fees are separate prospective snapshots; no synchronized or historical execution claim.',
        'Metadata resolution does not establish label availability at an earlier decision time.',
        'No strategy, profitability inference, or investment recommendation is included.'
    ]
    report['collection_complete_without_errors'] = not report['errors']
    report['snapshot_quality_passed'] = bool(report['books']) and all(
        b['valid_quote'] and b['fee_available'] for b in report['books']) and not report['book_skips'] and not any(
        error['stage'] == 'books' for error in report['errors'])
    (Path(root) / 'quality.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    return report
