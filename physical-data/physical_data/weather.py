"""ERA5-Land daily data preparation, extracted and hardened for public reuse.

This is ONLY for hourly cumulative total precipitation (metres), NOT hourly
increments, ERA5, or already-aggregated daily precipitation. Input timestamps
are validity times. Temperature is an instantaneous hourly value in kelvin.
"""
from datetime import timedelta
import math

import numpy as np
import pandas as pd

from .validation import iso_date, require_columns, strict_numeric, utc_timestamp


WEATHER_COLUMNS = ["datetime", "temperature_k", "precip_m"]


def daily_weather(hourly, start, end, *, available_by=None):
    """Return every requested UTC day, retaining missing/invalid-day flags.

    Retained source method: K -> C, m -> mm, shift precipitation by one hour,
    take each 01:00–next-00:00 cycle's terminal value, then daily min/max GDD.
    Unlike the original script, missing terminal or interior hours cannot
    silently produce a valid daily rainfall total or a misleading zero.
    """
    start, end = iso_date(start), iso_date(end)
    if end < start:
        raise ValueError("end must be on or after start")
    require_columns(hourly, WEATHER_COLUMNS)
    frame = hourly.copy()
    frame["datetime"] = pd.to_datetime([utc_timestamp(v) for v in frame.datetime], utc=True)
    if frame.datetime.duplicated().any():
        raise ValueError("Duplicate hourly timestamps; resolve source overlap first")
    if not frame.datetime.eq(frame.datetime.dt.floor("h")).all():
        raise ValueError("Hourly values must occur exactly on the hour")
    for col in WEATHER_COLUMNS[1:]:
        frame[col] = strict_numeric(frame[col], col)
    if (frame.temperature_k.dropna() < 0).any():
        raise ValueError("temperature_k cannot be negative")
    if available_by is not None:
        if "available_at" not in frame:
            raise ValueError("available_at is required for an availability cutoff")
        cutoff = utc_timestamp(available_by)
        frame["available_at"] = pd.to_datetime([utc_timestamp(v) for v in frame.available_at], utc=True)
        if (frame.available_at < frame.datetime).any():
            raise ValueError("available_at cannot precede the weather validity time")
        frame = frame[(frame.datetime <= cutoff) & (frame.available_at <= cutoff)]
    frame = frame.sort_values("datetime")
    frame["temperature_c"] = frame.temperature_k - 273.15
    frame["precip_cumulative_mm"] = frame.precip_m * 1000.0
    frame["date"] = frame.datetime.dt.date
    frame["precip_date"] = (frame.datetime - pd.Timedelta(hours=1)).dt.date
    rows = []
    for day in pd.date_range(start, end, freq="D", tz="UTC"):
        d = day.date()
        temp = frame[frame.date == d]
        precip = frame[frame.precip_date == d]
        expected_temp = pd.date_range(day, periods=24, freq="h")
        expected_precip = pd.date_range(day + pd.Timedelta(hours=1), periods=24, freq="h")
        temp_complete = set(temp.datetime) == set(expected_temp) and temp.temperature_c.notna().all()
        steps_complete = set(precip.datetime) == set(expected_precip) and precip.precip_cumulative_mm.notna().all()
        vals = precip.precip_cumulative_mm
        # Generic numerical tolerance, not an agronomic calibration.
        decreases = int((vals.dropna().diff() < -1e-9).sum())
        nonnegative = bool((vals.dropna() >= 0).all())
        valid_cycle = steps_complete and nonnegative and decreases == 0
        terminal = precip[precip.datetime == day + pd.Timedelta(days=1)]
        lower_bound = float(vals.dropna().iloc[-1]) if len(vals.dropna()) and nonnegative and not decreases else math.nan
        mean = float(temp.temperature_c.mean()) if temp_complete else math.nan
        minimum = float(temp.temperature_c.min()) if temp_complete else math.nan
        maximum = float(temp.temperature_c.max()) if temp_complete else math.nan
        rainfall = float(terminal.precip_cumulative_mm.iloc[0]) if valid_cycle else math.nan
        rows.append({
            "date": d.isoformat(), "temp_steps": len(temp),
            "temp_complete_24h": bool(temp_complete), "temp_mean_c": mean,
            "temp_min_c": minimum, "temp_max_c": maximum,
            "precip_steps": len(precip), "precip_complete_24h": bool(steps_complete),
            "precip_terminal_present": not terminal.empty,
            "precip_within_cycle_decreases": decreases,
            "precip_nonnegative": nonnegative,
            "precip_valid": bool(valid_cycle), "precip_mm": rainfall,
            "precip_observed_lower_bound_mm": lower_bound,
            "gdd_base5": max((maximum + minimum) / 2 - 5.0, 0) if temp_complete else math.nan,
            "gdd_base10": max((maximum + minimum) / 2 - 10.0, 0) if temp_complete else math.nan,
            "hot_day_ge30c": int(maximum >= 30) if temp_complete else math.nan,
            "frost_day_lt0c": int(minimum < 0) if temp_complete else math.nan,
            "dry_day_lt1mm": int(rainfall < 1) if valid_cycle else math.nan,
        })
    daily = pd.DataFrame(rows)
    # Any gap makes the total-to-date unknown rather than resuming a partial sum.
    for source, destination in [("precip_mm", "cum_precip_mm"), ("gdd_base5", "cum_gdd_base5"), ("gdd_base10", "cum_gdd_base10")]:
        daily[destination] = daily[source].cumsum(skipna=False)
    return daily


def weather_summary(daily):
    """Coverage first; incomplete sums are explicitly partial or null."""
    require_columns(daily, ["precip_mm", "temp_mean_c", "gdd_base5", "precip_valid", "temp_complete_24h"])
    def full_sum(col):
        return float(daily[col].sum()) if len(daily) and daily[col].notna().all() else None
    return {
        "days": len(daily), "valid_precipitation_days": int(daily.precip_valid.sum()),
        "complete_temperature_days": int(daily.temp_complete_24h.sum()),
        "precipitation_total_mm": full_sum("precip_mm"),
        "precipitation_observed_valid_days_mm": float(daily.precip_mm.sum(min_count=1)) if daily.precip_mm.notna().any() else None,
        "gdd_base5_total": full_sum("gdd_base5"),
        "mean_temperature_c": float(daily.temp_mean_c.mean()) if len(daily) and daily.temp_mean_c.notna().all() else None,
        "partial_totals_are_not_full_period_totals": True,
    }


def era5_request_plan(start, end, area):
    """Build CDS requests, including the next-midnight boundary; no API login.

    area is [north, west, south, east]. Planned request data must be inspected
    and downloaded by an authorized CDS client outside this minimal package.
    """
    start, end = iso_date(start), iso_date(end)
    if end < start:
        raise ValueError("end must be on or after start")
    if len(area) != 4 or not all(np.isfinite(area)):
        raise ValueError("area must contain four finite coordinates")
    north, west, south, east = area
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("Invalid north/west/south/east area")
    days = pd.date_range(start, end)
    requests = []
    for (year, month), group in pd.Series(days).groupby([days.year, days.month]):
        requests.append({"dataset": "reanalysis-era5-land", "request": {
            "variable": ["2m_temperature", "total_precipitation"],
            "year": str(year), "month": f"{month:02d}",
            "day": [f"{d.day:02d}" for d in group],
            "time": [f"{h:02d}:00" for h in range(24)],
            "data_format": "netcdf", "download_format": "unarchived", "area": list(area),
        }})
    boundary = end + timedelta(days=1)
    requests.append({"dataset": "reanalysis-era5-land", "request": {
        "variable": ["total_precipitation"], "year": str(boundary.year),
        "month": f"{boundary.month:02d}", "day": [f"{boundary.day:02d}"],
        "time": ["00:00"], "data_format": "netcdf", "download_format": "unarchived", "area": list(area),
    }})
    return requests
