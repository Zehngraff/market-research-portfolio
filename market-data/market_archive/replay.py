"""Deterministic synthetic responses sent through the real collection path."""
import copy
import urllib.error

from .transport import CLOB, DATA, GAMMA

FIXED_TIME = 1_800_000_000
MANIFEST = [
    {'condition': 'demo-open', 'event_id': 'demo-event-a', 'source_group': 'synthetic', 'closed': False},
    {'condition': 'demo-closed', 'event_id': 'demo-event-b', 'source_group': 'synthetic', 'closed': True},
]


def row(condition='demo-open', identity='a', **changes):
    return dict(condition_id=condition, token_id='demo-yes', transaction_hash=f'synthetic-{identity}',
                timestamp=FIXED_TIME - 50, price=0.5, size=4, side='BUY', **changes)


def page(rows, cursor=None):
    return dict(data=rows, pagination=dict(has_more=cursor is not None, next_cursor=cursor))


class SyntheticReplay:
    """No fallback transport exists. Unknown fixture requests fail immediately."""
    def __init__(self, *, repaired=False):
        self.repaired = repaired
        self.calls = []
        self.throttled = False

    def __call__(self, base, path, params):
        self.calls.append(dict(base=base, path=path, params=dict(params)))
        if base == DATA and path == '/v2/trades':
            if params['condition'] == 'demo-open':
                cursor = params.get('cursor')
                if cursor is None:
                    return page([row()], 'cursor-a')
                if cursor == 'cursor-a':
                    if not self.throttled and not self.repaired:
                        self.throttled = True
                        raise urllib.error.HTTPError('synthetic-fixture', 429, 'synthetic throttle', {'Retry-After': '2'}, None)
                    return page([row(), row(identity='b')], 'cursor-b')
                if cursor == 'cursor-b':
                    return page([row(identity='c')])
            if params['condition'] == 'demo-closed':
                r = row('demo-closed', 'd')
                if not self.repaired:
                    r['size'] = 0  # Deliberately invalid; raw must survive but checkpoint must not.
                return page([r])
        if base == GAMMA and path == '/markets' and params == {'condition_ids': 'demo-open'}:
            return [{'conditionId': 'demo-open', 'closed': False, 'acceptingOrders': True,
                     'clobTokenIds': ['demo-yes', 'demo-no']}]
        if base == CLOB and params.get('token_id') in ('demo-yes', 'demo-no'):
            token = params['token_id']
            if path == '/book':
                stamp = FIXED_TIME - (1 if token == 'demo-yes' else 1000)
                return {'asset_id': token, 'timestamp': str(stamp * 1000), 'min_order_size': '1',
                        'bids': [{'price': '.48', 'size': '20'}],
                        'asks': [{'price': '.50', 'size': '5'}, {'price': '.50', 'size': '15'}]}
            if path == '/fee-rate':
                return {'base_fee': '20' if token == 'demo-yes' else 'NaN'}
        raise ValueError(f'unexpected synthetic request: {path} {params}')
