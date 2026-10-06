"""Small strict input contracts shared by the extracted routines."""
from datetime import date
import math
import re

import pandas as pd


def utc_timestamp(value):
    """Require an explicit timezone; never guess the input timezone."""
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError(f"Expected a timezone-aware timestamp: {value!r}")
    return stamp.tz_convert("UTC")


def iso_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Expected an ISO date YYYY-MM-DD")
    return date.fromisoformat(value)


def finite_number(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def require_columns(frame, names):
    missing = sorted(set(names) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}")


def strict_numeric(series, name):
    """Nulls stay missing; malformed strings and infinities are rejected."""
    result = pd.to_numeric(series, errors="raise").astype(float)
    if ((result == float("inf")) | (result == -float("inf"))).any():
        raise ValueError(f"{name} contains infinity")
    return result
