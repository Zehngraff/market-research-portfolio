"""Single-attempt public HTTP reads; Archive owns the only retry loop."""
import json
import math
import urllib.parse
import urllib.request

GAMMA = 'https://gamma-api.polymarket.com'
CLOB = 'https://clob.polymarket.com'
DATA = 'https://data-api.polymarket.com'
KALSHI = 'https://external-api.kalshi.com/trade-api/v2'
BASES = frozenset((GAMMA, CLOB, DATA, KALSHI))
MAX_RESPONSE_BYTES = 2_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('redirect refused: public source must be explicit')


def get_json(base, path, params, *, timeout=20):
    """No credential lookup, arbitrary host, redirects, retry, or write verbs."""
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    if base not in BASES or not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('unapproved public endpoint')
    if any(x in path for x in ('?', '#', '..', '//')):
        raise ValueError('invalid endpoint path')
    url = base + path + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'market-research-archive/1.0'}, method='GET')
    with urllib.request.build_opener(NoRedirect).open(req, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError('public response exceeds decompressed byte limit')
    return json.loads(raw)


def parse_jsonish(value):
    return json.loads(value) if isinstance(value, str) else value
