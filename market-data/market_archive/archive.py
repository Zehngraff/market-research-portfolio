"""Bounded immutable response archive, adapted from the author's research collector."""
import gzip
import hashlib
import json
import math
import os
import time
import urllib.error
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

from .transport import get_json


@dataclass(frozen=True)
class Limits:
    """Operational safety budgets, not strategy parameters."""
    total_bytes: int = 80_000_000
    run_bytes: int = 5_000_000
    response_bytes: int = 2_000_000
    run_seconds: float = 420
    request_seconds: float = 20
    max_retry_seconds: float = 60
    attempts: int = 3

    def __post_init__(self):
        for name in ('total_bytes', 'run_bytes', 'response_bytes', 'attempts'):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f'{name} must be a positive integer')
        for name in ('run_seconds', 'request_seconds', 'max_retry_seconds'):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')


def fingerprint(row):
    # An exact response-row fingerprint, NOT an exchange fill identifier.
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class Archive:
    def __init__(self, root, *, transport=None, clock=None, monotonic=None, sleep=None,
                 limits=None, source_label='public_api'):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock or time.time
        self.monotonic = monotonic or time.monotonic
        self.sleep = sleep  # Resolve at use time so carried tests can patch time.sleep.
        self.transport = transport
        self.limits = limits or Limits()
        self.source_label = source_label
        self.started = self.clock()
        self.started_monotonic = self.monotonic()
        self.initial_bytes = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
        self.written = 0
        self.files = []
        self.retries = []

    def elapsed(self):
        return self.monotonic() - self.started_monotonic

    def _retry_delay(self, exc, attempt):
        delay = 2 ** (attempt + 1)
        retry_after = exc.headers.get('Retry-After') if getattr(exc, 'headers', None) else None
        if retry_after:
            try:
                value = float(retry_after)
            except (ValueError, TypeError):
                try:
                    value = parsedate_to_datetime(retry_after).timestamp() - self.clock()
                except (ValueError, TypeError, OverflowError):
                    value = 0  # Malformed header: retain bounded exponential backoff.
            if not math.isfinite(value):
                raise RuntimeError('non-finite retry delay; deferred') from exc
            delay = max(delay, value)
        if (delay > self.limits.max_retry_seconds or
                self.elapsed() + delay + self.limits.request_seconds > self.limits.run_seconds):
            raise RuntimeError('retry delay exceeds run budget; deferred to next run') from exc
        return delay

    def fetch(self, base, path, params):
        if self.elapsed() > self.limits.run_seconds:
            raise RuntimeError('run time budget reached')
        if (self.initial_bytes + self.written >= self.limits.total_bytes or
                self.written >= self.limits.run_bytes):
            raise RuntimeError('archive storage budget reached; review before scaling')
        for attempt in range(self.limits.attempts):
            if self.elapsed() + self.limits.request_seconds > self.limits.run_seconds:
                raise RuntimeError('insufficient request time budget')
            try:
                payload = (self.transport(base, path, params) if self.transport is not None else
                           get_json(base, path, params, timeout=self.limits.request_seconds))
                break
            except (urllib.error.URLError, TimeoutError) as exc:
                status = getattr(exc, 'code', None)
                if isinstance(exc, urllib.error.HTTPError) and status not in (408, 429, 500, 502, 503, 504):
                    raise
                if attempt == self.limits.attempts - 1:
                    raise
                delay = self._retry_delay(exc, attempt)
                self.retries.append(dict(path=path, status=status, delay_seconds=delay))
                (self.sleep or time.sleep)(delay)
        received = self.clock()
        encoded = json.dumps(dict(url=base + path, params=params, received_at=received,
                                  source_label=self.source_label, response=payload),
                             sort_keys=True, allow_nan=False).encode()
        if len(encoded) > self.limits.response_bytes:
            raise RuntimeError('response exceeds uncompressed archive limit')
        raw = gzip.compress(encoded, mtime=0)
        if (self.initial_bytes + self.written + len(raw) > self.limits.total_bytes or
                self.written + len(raw) > self.limits.run_bytes):
            raise RuntimeError('response exceeds archive budget; checkpoint unchanged')
        digest = hashlib.sha256(raw).hexdigest()
        dest = self.root / 'raw' / (digest + '.json.gz')
        dest.parent.mkdir(exist_ok=True)
        if not dest.exists():
            # The checkpoint commits only after this durable rename completes.
            pending = dest.with_suffix('.tmp')
            with pending.open('wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            pending.replace(dest)
            self.written += len(raw)
        elif dest.read_bytes() != raw:
            raise ValueError('existing content-addressed file is corrupt')
        self.files.append(dict(file=str(dest.relative_to(self.root)), sha256=digest))
        return payload, received, str(dest.relative_to(self.root))
