"""Public catalogue/JSON acquisition with immutable request-keyed snapshots.

Based on the source STAC catalogue and JSON snapshot fetch functions. New
safeguards cover pagination, request identity, bounded retries, exact-byte
hashes, corrupt caches and transparent offline replay. No credentials accepted.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .validation import iso_date

STAC_SEARCH = "https://earth-search.aws.element84.com/v1/search"
EUROSTAT_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/apro_cpsh1"
PUBLIC_HOSTS = {"earth-search.aws.element84.com", "ec.europa.eu"}


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def validate_public_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in PUBLIC_HOSTS or parsed.port not in (None, 443) or parsed.username or parsed.password:
        raise ValueError("Only supported public HTTPS endpoints are allowed")
    return url


class CheckedRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def http_json_bytes(method, url, body):
    """Read public JSON; retry only transient HTTP errors, never auth failures."""
    validate_public_url(url)
    if method not in {"GET", "POST"}:
        raise ValueError("Only GET and search POST are supported")
    payload = None if body is None else canonical_bytes(body)
    req = Request(url, data=payload, method=method, headers={
        "User-Agent": "PhysicalDataPortfolio/0.2", "Accept": "application/json",
        **({"Content-Type": "application/json"} if payload is not None else {})})
    opener = build_opener(CheckedRedirect())
    for attempt in range(3):
        try:
            with opener.open(req, timeout=30) as response:
                validate_public_url(response.geturl())
                raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError("Response exceeds 16 MiB limit")
            json.loads(raw)  # Do not save HTML/error payloads as successful JSON.
            return raw
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("Unreachable retry state")


class SnapshotStore:
    """A cache directory is a snapshot vintage, not a silently refreshed cache.

    Use a NEW directory to collect a new vintage. Cached bytes and manifests
    are verified on every replay. Retried runs reuse the same exact payloads.
    """
    def __init__(self, root, *, transport=None, offline=False, clock=None, data_kind="public_response"):
        self.root = Path(root)
        self.data_kind = data_kind
        self.transport = transport or http_json_bytes
        self.offline = offline
        self.clock = clock or (lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        self.records = []

    def fetch(self, url, *, method="GET", body=None):
        validate_public_url(url)
        if method not in {"GET", "POST"} or (method == "GET" and body is not None):
            raise ValueError("Unsupported HTTP method/body combination")
        request = {"method": method, "url": url, "body": body}
        key = sha256(canonical_bytes(request))
        folder = self.root / key
        manifest_path, response_path = folder / "manifest.json", folder / "response.json"
        if folder.exists():
            if not manifest_path.is_file() or not response_path.is_file():
                raise ValueError("Incomplete snapshot; use a fresh snapshot directory")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            raw = response_path.read_bytes()
            if manifest.get("request") != request or manifest.get("sha256") != sha256(raw) or manifest.get("bytes") != len(raw) or manifest.get("data_kind") != self.data_kind:
                raise ValueError("Snapshot integrity/request mismatch")
        else:
            if self.offline:
                raise FileNotFoundError(f"Required snapshot {key} is not cached")
            raw = self.transport(method, url, body)
            json.loads(raw)
            manifest = {"schema_version": 1, "request": request, "data_kind": self.data_kind,
                "retrieved_at_utc": self.clock(), "sha256": sha256(raw), "bytes": len(raw)}
            folder.mkdir(parents=True, exist_ok=False)
            # Incomplete writes fail closed on the next run, never masquerade as valid cache.
            response_path.write_bytes(raw)
            manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
        self.records.append({"request_key": key, **manifest})
        return json.loads(raw)


def stac_query(bbox, start, end, *, cloud_max=100, page_size=100):
    start, end = iso_date(start), iso_date(end)
    if end < start:
        raise ValueError("end precedes start")
    if len(bbox) != 4 or not all(math.isfinite(x) for x in bbox):
        raise ValueError("bbox must contain four finite coordinates")
    west, south, east, north = bbox
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Invalid WGS84 west/south/east/north bbox")
    if not 0 <= cloud_max <= 100 or type(page_size) is not int or not 1 <= page_size <= 1000:
        raise ValueError("Invalid cloud limit or page size")
    return {"collections": ["sentinel-2-l2a"], "bbox": list(bbox),
        "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
        "limit": page_size, "query": {"eo:cloud_cover": {"lte": cloud_max}}}


def collect_stac(store, query, *, max_pages=100):
    """Fetch all STAC search pages or fail loudly; no silent truncation.

    Retains whole items/assets, including nodata and scale/offset metadata.
    Supports GET/POST next links and POST merge semantics. Header-dependent
    pagination is rejected explicitly rather than guessed or partly fetched.
    """
    if type(max_pages) is not int or max_pages < 1:
        raise ValueError("max_pages must be positive")
    url, method, body = STAC_SEARCH, "POST", query
    original_body = query.copy()
    seen_requests, seen_items, items = set(), {}, []
    for _ in range(max_pages):
        signature = sha256(canonical_bytes([url, method, body]))
        if signature in seen_requests:
            raise ValueError("STAC pagination loop")
        seen_requests.add(signature)
        page = store.fetch(url, method=method, body=body)
        if page.get("type") != "FeatureCollection" or not isinstance(page.get("features"), list):
            raise ValueError("Expected a STAC FeatureCollection")
        for item in page["features"]:
            if not isinstance(item, dict) or not item.get("id") or not item.get("collection"):
                raise ValueError("Malformed STAC item")
            key = (item["collection"], item["id"])
            if key in seen_items:
                if canonical_bytes(item) != canonical_bytes(seen_items[key]):
                    raise ValueError("Conflicting repeated STAC item")
                continue
            seen_items[key] = item
            items.append(item)
        next_links = [link for link in page.get("links", []) if link.get("rel") == "next"]
        if not next_links:
            return {"type": "FeatureCollection", "features": items,
                "collection_complete": True, "page_count": len(seen_requests),
                "query": query, "raster_assets_downloaded": False}
        if len(next_links) != 1:
            raise ValueError("Ambiguous STAC next links")
        link = next_links[0]
        if link.get("headers"):
            raise ValueError("Header-dependent pagination is unsupported")
        next_url = urljoin(url, link["href"])
        if urlparse(next_url).netloc != urlparse(STAC_SEARCH).netloc:
            raise ValueError("Cross-host STAC pagination rejected")
        validate_public_url(next_url)
        method = link.get("method", "GET").upper()
        if method not in {"GET", "POST"}:
            raise ValueError("Unsupported STAC next method")
        body = link.get("body")
        if body is not None and not isinstance(body, dict):
            raise ValueError("STAC next body must be a JSON object")
        if method == "GET":
            if body:
                raise ValueError("GET pagination with body is unsupported")
            body = None
        elif link.get("merge", False):
            body = {**original_body, **(body or {})}
        url = next_url
    raise ValueError("STAC page limit reached before catalogue completion")


def eurostat_url(geo, crop, year):
    """One deliberately small official crop-statistics query, no private AOI."""
    if not isinstance(geo, str) or not geo.isalpha() or len(geo) not in {2, 3}:
        raise ValueError("geo must be a Eurostat area code")
    if not isinstance(crop, str) or not crop.isalnum():
        raise ValueError("crop must be an alphanumeric Eurostat code")
    if type(year) is not int or not 1900 <= year <= 2100:
        raise ValueError("Invalid year")
    return EUROSTAT_BASE + "?" + urlencode({"lang": "en", "geo": geo.upper(), "crops": crop,
        "time": str(year), "strucpro": "YLD_HUMD_EU_T_HA"})
