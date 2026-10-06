"""Masked Sentinel-style indices and cutoff features from the source pipeline.

Array statistics, SCL masking, same-date pixel weighting, peak/latest NDVI,
trapezoidal NDVI AUC and NDMI summaries are retained. Raster transport and
reprojection are deliberately outside this lightweight numerical module.
"""
import math

import numpy as np
import pandas as pd

from .validation import finite_number, require_columns, utc_timestamp


def band_metadata(asset):
    """Read single-band STAC Raster v1 or v2 metadata without assuming scale.

    Require a declared scale and offset for reflectance inputs. A missing
    transform is an error instead of quietly mixing digital numbers and
    physical reflectance. The caller may provide verified metadata itself.
    """
    if "raster:bands" in asset:
        bands = asset["raster:bands"]
        if len(bands) != 1:
            raise ValueError("Exactly one raster band is required")
        meta = bands[0]
        scale, offset = meta.get("scale"), meta.get("offset")
    elif "bands" in asset:
        bands = asset["bands"]
        if len(bands) != 1:
            raise ValueError("Exactly one raster band is required")
        meta = bands[0]
        scale, offset = meta.get("raster:scale"), meta.get("raster:offset")
    else:
        raise ValueError("Missing raster band scale/offset metadata")
    if scale is None or offset is None:
        raise ValueError("Explicit scale and offset are required")
    scale, offset = finite_number(scale, "scale"), finite_number(offset, "offset")
    if scale <= 0:
        raise ValueError("Reflectance scale must be positive")
    return {"scale": scale, "offset": offset, "nodata": meta.get("nodata")}


def scaled_band(values, metadata):
    arr = np.ma.asarray(values, dtype=float)
    raw = np.asarray(arr.filled(np.nan), dtype=float)
    valid = ~np.ma.getmaskarray(arr) & np.isfinite(raw)
    nodata = metadata.get("nodata")
    if nodata is not None:
        if isinstance(nodata, str) and nodata.lower() == "nan":
            valid &= ~np.isnan(raw)
        else:
            valid &= raw != float(nodata)
    scale = finite_number(metadata["scale"], "scale")
    offset = finite_number(metadata["offset"], "offset")
    if scale <= 0:
        raise ValueError("Reflectance scale must be positive")
    physical = raw * scale + offset
    valid &= np.isfinite(physical) & (physical >= 0)
    return physical, valid


def stats(array, valid):
    """Mean, median, count, retaining the original finite-pixel reduction."""
    vals = array[valid & np.isfinite(array)]
    if vals.size == 0:
        return math.nan, math.nan, 0
    return float(np.mean(vals)), float(np.median(vals)), int(vals.size)


def observe_arrays(red, nir, swir, scl, *, metadata, geometry_mask=None, valid_scl=(4, 5, 6, 7)):
    """Compute indices on co-registered arrays; all bands must share one grid.

    Defaults retain original non-cloud SCL classes. Water/unclassified classes
    can be excluded by explicitly passing (4, 5); this is a methodological
    setting rather than an empirically fitted model threshold.
    """
    r, rv = scaled_band(red, metadata["red"])
    n, nv = scaled_band(nir, metadata["nir"])
    w, wv = scaled_band(swir, metadata["swir16"])
    q = np.ma.asarray(scl, dtype=float)
    qv = np.asarray(q.filled(np.nan))
    if r.ndim != 2 or r.shape != n.shape or r.shape != w.shape or r.shape != q.shape:
        raise ValueError("All input bands must be co-registered two-dimensional arrays")
    geometry = np.ones(r.shape, dtype=bool) if geometry_mask is None else np.asarray(geometry_mask, dtype=bool)
    if geometry.shape != r.shape:
        raise ValueError("geometry_mask shape differs from the band grid")
    if not valid_scl or any(type(x) is not int or x < 0 or x > 11 for x in valid_scl):
        raise ValueError("valid_scl must contain Sentinel SCL class integers")
    clear = geometry & ~np.ma.getmaskarray(q) & np.isfinite(qv) & np.isin(qv, valid_scl)
    ndvi_valid = clear & rv & nv & ((n + r) > 0)
    ndmi_valid = clear & nv & wv & ((n + w) > 0)
    ndvi = np.full(r.shape, np.nan)
    ndmi = np.full(r.shape, np.nan)
    np.divide(n - r, n + r, out=ndvi, where=ndvi_valid)
    np.divide(n - w, n + w, out=ndmi, where=ndmi_valid)
    vi_mean, vi_median, vi_count = stats(ndvi, ndvi_valid)
    mi_mean, mi_median, mi_count = stats(ndmi, ndmi_valid)
    total = int(geometry.sum())
    return {
        "ndvi_mean": vi_mean, "ndvi_median": vi_median,
        "ndmi_mean": mi_mean, "ndmi_median": mi_median,
        "valid_pixels": vi_count, "ndmi_valid_pixels": mi_count,
        "geometry_pixels": total,
        "clear_fraction": vi_count / total if total else math.nan,
        "ndmi_clear_fraction": mi_count / total if total else math.nan,
    }


def weighted(group, column, weight_column="valid_pixels"):
    """Original valid-pixel weighted reduction with explicit positive weights."""
    valid = np.isfinite(group[column]) & np.isfinite(group[weight_column]) & (group[weight_column] > 0)
    subset = group[valid]
    return float(np.average(subset[column], weights=subset[weight_column])) if len(subset) else math.nan


DAILY_COLUMNS = ["field_id", "date", "ndvi", "ndmi", "valid_pixels", "ndmi_valid_pixels", "geometry_pixels", "clear_fraction", "item_count"]
FEATURE_COLUMNS = ["field_id", "cutoff_utc", "availability_filter_applied", "good_observations", "first_date", "last_date", "mean_clear_fraction", "peak_ndvi", "peak_ndvi_date", "latest_ndvi", "auc_ndvi_days", "mean_ndmi", "min_ndmi"]


def cutoff_features(observations, cutoff, *, minimum_pixels=1, minimum_clear_fraction=0.0, require_available_at=True):
    """Collapse overlapping same-date items before cutoff phenology features.

    Availability time is separate from acquisition time. A later-retrieved
    historical record does not become historically available by backdating it.
    All rows must be from one intended growing-season/year window supplied by
    the caller; mixing years is rejected. Spatially overlapping pixels in
    adjacent tiles remain repeated contributions, not a mosaic/union.
    """
    cutoff = utc_timestamp(cutoff)
    if type(minimum_pixels) is not int or minimum_pixels < 1:
        raise ValueError("minimum_pixels must be a positive integer")
    minimum_clear_fraction = finite_number(minimum_clear_fraction, "minimum_clear_fraction")
    if not 0 <= minimum_clear_fraction <= 1:
        raise ValueError("minimum_clear_fraction must be between 0 and 1")
    required = ["field_id", "item_id", "datetime", "ndvi_mean", "ndmi_mean", "valid_pixels", "ndmi_valid_pixels", "geometry_pixels", "clear_fraction"]
    if require_available_at:
        required.append("available_at")
    require_columns(observations, required)
    obs = observations.copy()
    if obs["field_id"].isna().any() or obs["item_id"].isna().any():
        raise ValueError("field_id and item_id are required")
    if obs.duplicated(["field_id", "item_id"]).any():
        raise ValueError("Duplicate field/item key")
    obs["datetime"] = pd.to_datetime([utc_timestamp(v) for v in obs.datetime], utc=True)
    if require_available_at:
        obs["available_at"] = pd.to_datetime([utc_timestamp(v) for v in obs.available_at], utc=True)
        if (obs.available_at < obs.datetime).any():
            raise ValueError("available_at cannot precede acquisition time")
        obs = obs[obs.available_at <= cutoff]
    obs = obs[obs.datetime <= cutoff].copy()
    if obs.datetime.dt.year.nunique() > 1:
        raise ValueError("Supply one seasonal/year window at a time")
    for col in ["valid_pixels", "ndmi_valid_pixels", "geometry_pixels"]:
        values = pd.to_numeric(obs[col], errors="raise")
        if values.isna().any() or (~np.isfinite(values)).any() or (values < 0).any() or (values % 1 != 0).any():
            raise ValueError(f"Invalid pixel counts: {col}")
        obs[col] = values
    if (obs.valid_pixels > obs.geometry_pixels).any() or (obs.ndmi_valid_pixels > obs.geometry_pixels).any():
        raise ValueError("Valid pixels cannot exceed geometry pixels")
    for col in ["ndvi_mean", "ndmi_mean", "clear_fraction"]:
        obs[col] = pd.to_numeric(obs[col], errors="raise")
        if np.isinf(obs[col]).any():
            raise ValueError(f"Infinite values in {col}")
    if ((obs.clear_fraction.dropna() < 0) | (obs.clear_fraction.dropna() > 1)).any():
        raise ValueError("Invalid clear fractions")
    expected_fraction = obs.valid_pixels / obs.geometry_pixels.replace(0, np.nan)
    if not np.allclose(obs.clear_fraction, expected_fraction, equal_nan=True):
        raise ValueError("clear_fraction must equal valid_pixels / geometry_pixels")
    if ((obs.valid_pixels == 0) & obs.ndvi_mean.notna()).any() or ((obs.ndmi_valid_pixels == 0) & obs.ndmi_mean.notna()).any():
        raise ValueError("Zero valid pixels cannot have a finite index mean")
    obs["date"] = obs.datetime.dt.date.astype(str)
    daily_rows = []
    for (field_id, day), group in obs.groupby(["field_id", "date"], sort=True):
        total = int(group.geometry_pixels.sum())
        pixels = int(group.valid_pixels.sum())
        daily_rows.append({"field_id": field_id, "date": day,
            "ndvi": weighted(group, "ndvi_mean"),
            "ndmi": weighted(group, "ndmi_mean", "ndmi_valid_pixels"),
            "valid_pixels": pixels, "ndmi_valid_pixels": int(group.ndmi_valid_pixels.sum()),
            "geometry_pixels": total, "clear_fraction": pixels / total if total else math.nan,
            "item_count": len(group)})
    daily = pd.DataFrame(daily_rows, columns=DAILY_COLUMNS)
    good = daily[(daily.valid_pixels >= minimum_pixels) & (daily.clear_fraction >= minimum_clear_fraction) & daily.ndvi.notna()]
    rows = []
    for field_id, group in good.groupby("field_id", sort=True):
        group = group.sort_values("date")
        dates = pd.to_datetime(group.date)
        x = (dates - dates.min()).dt.days.to_numpy()
        rows.append({"field_id": field_id, "cutoff_utc": cutoff.isoformat(),
            "availability_filter_applied": require_available_at,
            "good_observations": len(group), "first_date": group.iloc[0].date,
            "last_date": group.iloc[-1].date, "mean_clear_fraction": float(group.clear_fraction.mean()),
            "peak_ndvi": float(group.ndvi.max()), "peak_ndvi_date": group.loc[group.ndvi.idxmax(), "date"],
            "latest_ndvi": float(group.iloc[-1].ndvi),
            "auc_ndvi_days": float(np.trapezoid(group.ndvi.to_numpy(), x)) if len(group) > 1 else math.nan,
            "mean_ndmi": float(group.ndmi.mean()), "min_ndmi": float(group.ndmi.min())})
    return daily, pd.DataFrame(rows, columns=FEATURE_COLUMNS)
