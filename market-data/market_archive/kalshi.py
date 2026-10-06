"""Optional public-data adapter; no authentication or order capabilities."""
import json
import sqlite3
import time
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

BASE = 'https://external-api.kalshi.com/trade-api/v2'
FIELDS = ('event_ticker', 'series_ticker', 'category', 'title', 'status', 'result', 'close_time')

def store_object(con, kind, key, row, raw):
    identity = 'event_ticker' if kind == 'events' else 'ticker'
    if row.get(identity) != key:
        raise ValueError('Metadata identity mismatch')
    metadata = {k: row[k] for k in FIELDS if k in row}
    con.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?,?)',
                (kind, key, json.dumps(metadata), raw))

def mapping(con):
    objects = {(k,i):json.loads(m) for k,i,m in con.execute('SELECT kind,id,metadata FROM objects')}
    counts, pending = {}, []
    for ticker,n in con.execute('SELECT ticker,COUNT(*) FROM trades GROUP BY ticker'):
        market = objects.get(('markets',ticker), {})
        event = objects.get(('events',market.get('event_ticker')), {})
        category = event.get('category') or 'UNKNOWN'
        counts[category] = counts.get(category,0)+n
        if category == 'UNKNOWN':
            pending.append((ticker,n,market))
    return counts, pending

def hydrate(con, archive, report, limit=60):
    counts, pending = mapping(con)
    attempts = dict(con.execute('SELECT ticker,last_attempt FROM metadata_attempts'))
    pending.sort(key=lambda x:(attempts.get(x[0],0), -x[1], x[0]))
    failures = []
    for ticker,n,market in pending[:limit]:
        if time.time()-archive.started > 350:
            break
        with con:
            con.execute('INSERT OR REPLACE INTO metadata_attempts VALUES(?,?)',(ticker,time.time()))
        try:
            if not market.get('event_ticker'):
                suffix = urllib.parse.quote(ticker, safe='')
                try:
                    payload,_,raw = archive.fetch(BASE,'/markets/'+suffix,{})
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:
                        raise
                    payload,_,raw = archive.fetch(BASE,'/historical/markets/'+suffix,{})
                market = payload['market']
                with con:
                    store_object(con,'markets',ticker,market,raw)
            event_id = market.get('event_ticker')
            if not event_id:
                raise ValueError('Missing event identity')
            old = con.execute("SELECT metadata FROM objects WHERE kind='events' AND id=?",(event_id,)).fetchone()
            if not old or not json.loads(old[0]).get('category'):
                payload,_,raw = archive.fetch(BASE,'/events/'+urllib.parse.quote(event_id,safe=''),
                                            {'with_nested_markets':'false'})
                with con:
                    store_object(con,'events',event_id,payload['event'],raw)
        except Exception as exc:
            failures.append(dict(ticker=ticker,error=str(exc)))
    counts,pending = mapping(con)
    report['mapping'] = dict(category_trade_counts=counts, pending_tickers=len(pending),
        unmapped_trades=sum(n for _,n,_ in pending), failures=failures,
        unknown_rows_quarantined=True)

def database(root):
    Path(root).mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(Path(root) / 'index.db')
    con.executescript('''
        CREATE TABLE IF NOT EXISTS checkpoints (
          path TEXT PRIMARY KEY, cursor TEXT, upper_ts INTEGER, cycles INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS page_cursors (
          path TEXT, cursor TEXT, PRIMARY KEY(path,cursor));
        CREATE TABLE IF NOT EXISTS trades (
          ticker TEXT, trade_id TEXT, created_time TEXT, raw_file TEXT,
          PRIMARY KEY(ticker,trade_id));
        CREATE TABLE IF NOT EXISTS objects (
          kind TEXT, id TEXT, metadata TEXT, raw_file TEXT, PRIMARY KEY(kind,id));
        CREATE TABLE IF NOT EXISTS metadata_attempts (ticker TEXT PRIMARY KEY,last_attempt REAL);
    ''')
    return con

def validate(payload, kind, cursor):
    if not isinstance(payload, dict) or kind not in ('events', 'markets', 'trades'):
        raise ValueError('Invalid page envelope')
    rows = payload.get(kind)
    raw_cursor = payload.get('cursor')
    if raw_cursor is not None and not isinstance(raw_cursor, str):
        raise ValueError('Invalid cursor type')
    nxt = raw_cursor or None
    if not isinstance(rows, list) or (nxt is not None and not isinstance(nxt, str)):
        raise ValueError('Invalid page envelope')
    if nxt and (nxt == cursor or not rows):
        raise ValueError('Non-advancing pagination')
    for row in rows:
        key = {'events': 'event_ticker', 'markets': 'ticker', 'trades': 'trade_id'}[kind]
        if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key]:
            raise ValueError('Missing identity')
        if kind == 'trades':
            if not isinstance(row.get('ticker'), str) or not row['ticker']:
                raise ValueError('Missing ticker')
            try:
                stamp = datetime.fromisoformat(row['created_time'].replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    raise ValueError('Trade time requires an explicit timezone')
            except (KeyError, TypeError, AttributeError) as exc:
                raise ValueError('Invalid trade time') from exc
            for name in ('yes_price_dollars', 'no_price_dollars', 'count_fp'):
                try:
                    value = Decimal(str(row[name]))
                except (InvalidOperation, KeyError, ValueError) as exc:
                    raise ValueError('Invalid trade value') from exc
                if not value.is_finite() or (value <= 0 if name == 'count_fp' else not 0 <= value <= 1):
                    raise ValueError('Invalid trade value')
    return rows, nxt

def page(con, archive, path, kind, head=False):
    previous = con.execute('SELECT cursor,upper_ts,cycles FROM checkpoints WHERE path=?', (path,)).fetchone()
    cursor, upper, cycles = previous or (None, int(time.time()), 0)
    params = {'limit': 100 if kind == 'events' else 500}
    if kind == 'events':
        params['with_nested_markets'] = 'false'
    if kind == 'trades':
        params['max_ts'] = int(time.time()) if head else upper
    if cursor and not head:
        params['cursor'] = cursor
    payload, _, raw = archive.fetch(BASE, path, params)
    rows, nxt = validate(payload, kind, None if head else cursor)
    if not head and nxt and con.execute('SELECT 1 FROM page_cursors WHERE path=? AND cursor=?', (path,nxt)).fetchone():
        raise ValueError('Pagination cursor cycle')
    inserted = 0
    with con:
        for row in rows:
            if kind == 'trades':
                inserted += con.execute('INSERT OR IGNORE INTO trades VALUES(?,?,?,?)',
                    (row['ticker'], row['trade_id'], row['created_time'], raw)).rowcount
            else:
                key = row['event_ticker'] if kind == 'events' else row['ticker']
                # Full rules stay in immutable raw; keep the index small.
                store_object(con, kind, key, row, raw)
        if not head:
            con.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?)',
                (path, nxt, upper if nxt else int(time.time()), cycles + int(nxt is None)))
            if nxt:
                con.execute('INSERT INTO page_cursors VALUES(?,?)', (path,nxt))
            else:
                con.execute('DELETE FROM page_cursors WHERE path=?', (path,))
    return dict(path=path, head=head, rows=len(rows), inserted_trades=inserted,
                cursor_pending=bool(nxt), endpoint_exhausted=nxt is None)
