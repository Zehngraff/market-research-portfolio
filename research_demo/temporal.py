"""Point-in-time feature selection and conservative chronological splitting.

Features: first exclude unavailable versions, then choose the latest observation
time and highest known revision. A late revision to an old observation does not
replace a newer observation. Availability must not precede observation time.
Revision numbers are positive and availability must not decrease across revisions
of the same observation. Features are not forward-filled across tokens.
Ticks and revision numbers fit SQLite's signed 64-bit INTEGER domain.

Split: test decisions are at/after the boundary. Training decisions are before it.
Purge training rows sharing ANY parent event with test, rows whose label or holding
window crosses the boundary, and labels not available strictly before it. Windows
are half-open [decision_at, end), so an end at the boundary does not overlap test.
This is one conservative holdout, not walk-forward model selection or a complete
leakage audit. Test labels are never used to construct training membership except
for event identity and the fixed chronological boundary.
"""

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import sqlite3
from typing import Iterable


def require_id(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def require_tick(value: int, name: str) -> None:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ValueError(f"{name} must be a nonnegative SQLite-range integer tick")


def require_decimal(value: Decimal, name: str, *, minimum=None) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")


def unique_ids(rows: Iterable, field: str) -> None:
    seen = set()
    for row in rows:
        value = getattr(row, field)
        if value in seen:
            raise ValueError(f"duplicate {field}: {value}")
        seen.add(value)


@dataclass(frozen=True)
class Observation:
    version_id: str
    token: str
    feature: str
    observed_at: int
    available_at: int
    revision: int
    value: Decimal

    def __post_init__(self):
        for name in ("version_id", "token", "feature"):
            require_id(getattr(self, name), name)
        for name in ("observed_at", "available_at"):
            require_tick(getattr(self, name), name)
        if self.available_at < self.observed_at:
            raise ValueError("observation cannot be available before observation time")
        if type(self.revision) is not int or not 1 <= self.revision <= 2**63 - 1:
            raise ValueError("revision must be a positive integer")
        require_decimal(self.value, "value")


@dataclass(frozen=True)
class Decision:
    decision_id: str
    token: str
    at: int

    def __post_init__(self):
        require_id(self.decision_id, "decision_id")
        require_id(self.token, "token")
        require_tick(self.at, "at")


def _validate_features(observations, decisions, features):
    observations, decisions, features = list(observations), list(decisions), list(features)
    unique_ids(observations, "version_id")
    unique_ids(decisions, "decision_id")
    if len(features) != len(set(features)):
        raise ValueError("duplicate requested feature")
    for feature in features:
        require_id(feature, "feature")
    versions = {}
    for row in observations:
        key = (row.token, row.feature, row.observed_at)
        group = versions.setdefault(key, {})
        if row.revision in group:
            raise ValueError(f"duplicate observation revision: {key}")
        group[row.revision] = row.available_at
    for group in versions.values():
        times = [group[revision] for revision in sorted(group)]
        if times != sorted(times):
            raise ValueError("revision availability must not decrease")
    return observations, decisions, features


def _feature_record(row):
    return {
        "version_id": row.version_id,
        "observed_at": row.observed_at,
        "available_at": row.available_at,
        "revision": row.revision,
        "value": str(row.value),
    }


def asof_python(observations, decisions, features):
    """Return every requested decision/feature, with None when no version is known."""
    observations, decisions, features = _validate_features(observations, decisions, features)
    result = {}
    for decision in sorted(decisions, key=lambda item: (item.at, item.decision_id)):
        selected = {}
        for feature in sorted(features):
            candidates = [
                row for row in observations
                if row.token == decision.token and row.feature == feature
                and row.observed_at < decision.at and row.available_at < decision.at
            ]
            latest = max(candidates, key=lambda item: (item.observed_at, item.revision), default=None)
            selected[feature] = _feature_record(latest) if latest else None
        result[decision.decision_id] = selected
    return result


def asof_sqlite(observations, decisions, features):
    """Execute the checked-in as-of SQL using an isolated in-memory SQLite database.

    Decimal values are stored as text and never coerced to binary floating point.
    This function expects the repository's adjacent sql/ directory to be present.
    """
    observations, decisions, features = _validate_features(observations, decisions, features)
    query = (Path(__file__).resolve().parent.parent / "sql" / "asof_features.sql").read_text()
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            CREATE TABLE observations (
                version_id TEXT PRIMARY KEY, token TEXT NOT NULL, feature TEXT NOT NULL,
                observed_at INTEGER NOT NULL, available_at INTEGER NOT NULL,
                revision INTEGER NOT NULL, value TEXT NOT NULL,
                UNIQUE(token, feature, observed_at, revision)
            );
            CREATE TABLE decisions (decision_id TEXT PRIMARY KEY, token TEXT NOT NULL, at INTEGER NOT NULL);
            CREATE TABLE requested_features (feature TEXT PRIMARY KEY);
        """)
        db.executemany("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?)", [
            (o.version_id, o.token, o.feature, o.observed_at, o.available_at, o.revision, str(o.value))
            for o in observations
        ])
        db.executemany("INSERT INTO decisions VALUES (?, ?, ?)", [(d.decision_id, d.token, d.at) for d in decisions])
        db.executemany("INSERT INTO requested_features VALUES (?)", [(f,) for f in features])
        result = {d.decision_id: {} for d in sorted(decisions, key=lambda item: (item.at, item.decision_id))}
        for decision_id, feature, version_id, observed_at, available_at, revision, value in db.execute(query):
            result[decision_id][feature] = None if version_id is None else {
                "version_id": version_id, "observed_at": observed_at,
                "available_at": available_at, "revision": revision, "value": value,
            }
        return result


@dataclass(frozen=True)
class Sample:
    sample_id: str
    parent_event_id: str
    decision_at: int
    label_end: int
    holding_end: int
    label_available_at: int

    def __post_init__(self):
        require_id(self.sample_id, "sample_id")
        require_id(self.parent_event_id, "parent_event_id")
        for name in ("decision_at", "label_end", "holding_end", "label_available_at"):
            require_tick(getattr(self, name), name)
        if min(self.label_end, self.holding_end) < self.decision_at:
            raise ValueError("window ends cannot precede decision")
        if self.label_available_at < self.label_end:
            raise ValueError("label cannot be available before its window ends")


def chronological_split(samples, boundary: int):
    require_tick(boundary, "boundary")
    samples = sorted(samples, key=lambda item: (item.decision_at, item.sample_id))
    unique_ids(samples, "sample_id")
    test = [row for row in samples if row.decision_at >= boundary]
    test_events = {row.parent_event_id for row in test}
    train, purged = [], {}
    for row in samples:
        if row.decision_at >= boundary:
            continue
        reasons = []
        if row.parent_event_id in test_events:
            reasons.append("shared_parent_event")
        if row.label_end > boundary:
            reasons.append("label_window_overlap")
        if row.holding_end > boundary:
            reasons.append("holding_window_overlap")
        if row.label_available_at >= boundary:
            reasons.append("label_unavailable_at_boundary")
        if reasons:
            purged[row.sample_id] = reasons
        else:
            train.append(row.sample_id)
    return {"boundary": boundary, "train": train, "test": [row.sample_id for row in test], "purged": purged}
