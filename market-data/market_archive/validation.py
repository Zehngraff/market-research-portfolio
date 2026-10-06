"""Fail-closed data contracts extracted from research collection code."""
import json
import math
import time


def finite_number(value, name):
    if isinstance(value, bool):
        raise ValueError(f'invalid {name}')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'invalid {name}') from exc
    if not math.isfinite(result):
        raise ValueError(f'non-finite {name}')
    return result


def positive_amount(stake):
    stake = finite_number(stake, 'diagnostic amount')
    if stake <= 0:
        raise ValueError('diagnostic amount must be positive')
    return stake


def book_quote(book, token, received, stake, *, max_age=120, future_tolerance=5):
    """Validate one snapshot at a caller-supplied diagnostic amount; no order is created."""
    stake = positive_amount(stake)
    received = finite_number(received, 'receipt time')
    max_age = finite_number(max_age, 'maximum age')
    future_tolerance = finite_number(future_tolerance, 'future tolerance')
    if max_age < 0 or future_tolerance < 0:
        raise ValueError('age tolerances must be nonnegative')
    if not isinstance(book, dict) or str(book.get('asset_id')) != str(token):
        raise ValueError('book token mismatch')
    stamp = finite_number(book.get('timestamp'), 'book timestamp') / 1000
    if stamp <= 0 or not -future_tolerance <= received - stamp <= max_age:
        raise ValueError('stale or future book')
    sides = []
    for side in ('bids', 'asks'):
        raw_levels = book.get(side)
        if not isinstance(raw_levels, list) or not raw_levels:
            raise ValueError('missing bid or ask')
        levels = []
        for row in raw_levels:
            if not isinstance(row, dict):
                raise ValueError('invalid book level')
            p = finite_number(row.get('price'), 'book price')
            s = finite_number(row.get('size'), 'book size')
            if not 0 < p < 1 or s <= 0:
                raise ValueError('invalid book level')
            levels.append((p, s))
        sides.append(levels)
    bid = max(p for p, s in sides[0]); ask = min(p for p, s in sides[1])
    if bid > ask:
        raise ValueError('crossed book')
    bid_size = sum(s for p, s in sides[0] if p == bid)
    ask_size = sum(s for p, s in sides[1] if p == ask)
    if not math.isfinite(bid_size) or not math.isfinite(ask_size):
        raise ValueError('non-finite aggregated book size')
    minimum = finite_number(book.get('min_order_size'), 'minimum size')
    if minimum < 0:
        raise ValueError('invalid minimum size')
    if stake / ask < minimum or ask_size * ask < stake:
        raise ValueError('insufficient size for diagnostic amount')
    return bid, ask, bid_size, ask_size


def quote_diagnostics(book, received, stake=1):
    """Independent age/minimum/depth observations, never permission to trade."""
    stake = positive_amount(stake)
    result = {}
    try:
        age = finite_number(received, 'receipt time') - finite_number(book.get('timestamp'), 'timestamp') / 1000
        result.update(age_seconds=age, fresh=-5 <= age <= 120)
    except (AttributeError, ValueError):
        result['fresh'] = False
    try:
        asks = [(finite_number(x['price'], 'price'), finite_number(x['size'], 'size'))
                for x in book.get('asks', [])]
        if not asks or any(not 0 < p < 1 or s <= 0 for p, s in asks):
            raise ValueError('missing or invalid asks')
        ask = min(p for p, s in asks)
        depth = sum(s for p, s in asks if p == ask)
        minimum = finite_number(book.get('min_order_size'), 'minimum size')
        if minimum < 0 or not math.isfinite(depth):
            raise ValueError('invalid minimum or aggregate size')
        result.update(best_ask=ask, best_ask_shares=depth, minimum_shares=minimum,
            minimum_notional=minimum * ask, stake_meets_minimum=stake / ask >= minimum,
            top_depth_covers_stake=depth * ask >= stake, top_depth_covers_minimum=depth >= minimum)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        result['size_diagnostic_error'] = str(exc)
    return result


def validate_page(payload, condition, *, now=None):
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise ValueError('unexpected trade response envelope')
    now = finite_number(time.time() if now is None else now, 'receipt time')
    for row in payload['data']:
        if not isinstance(row, dict) or row.get('condition_id') != condition:
            raise ValueError('condition mismatch')
        p = finite_number(row.get('price'), 'trade price')
        s = finite_number(row.get('size'), 'trade size')
        if not 0 <= p <= 1 or s <= 0:
            raise ValueError('invalid trade price/size')
        if (row.get('side') not in ('BUY', 'SELL') or
                not isinstance(row.get('transaction_hash'), str) or not row['transaction_hash'] or
                not isinstance(row.get('token_id'), str) or not row['token_id']):
            raise ValueError('missing trade identity or side')
        stamp = finite_number(row.get('timestamp'), 'trade timestamp')
        if not stamp.is_integer() or not 0 < stamp <= now + 300:
            raise ValueError('invalid trade time')
    pg = payload.get('pagination')
    if not isinstance(pg, dict) or type(pg.get('has_more')) is not bool:
        raise ValueError('invalid pagination')
    cursor = pg.get('next_cursor')
    if cursor is not None and not isinstance(cursor, str):
        raise ValueError('invalid pagination cursor type')
    if pg['has_more'] and (not cursor or not payload['data']):
        raise ValueError('non-advancing pagination')
    return payload['data'], cursor if pg['has_more'] else None


def confirmed_outcome(market, token):
    """Metadata-only resolution check. It does not establish availability at an earlier time."""
    unpack = lambda x: json.loads(x) if isinstance(x, str) else x
    if market.get('closed') is not True or market.get('umaResolutionStatus') != 'resolved':
        return None
    try:
        tokens = unpack(market.get('clobTokenIds', []))
        prices = [finite_number(x, 'outcome price') for x in unpack(market.get('outcomePrices', []))]
        if (not isinstance(tokens, list) or len(tokens) != 2 or len(set(map(str, tokens))) != 2 or
                len(prices) != 2 or sorted(prices) != [0.0, 1.0]):
            return None
        if str(token) not in [str(t) for t in tokens]:
            return None
        return int(prices[[str(t) for t in tokens].index(str(token))])
    except (TypeError, ValueError):
        return None
