"""Deterministic invented inputs, never measurements or private field data.

Run from any directory: python fixtures/synthetic/generate_fixtures.py
"""
import csv
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def save(name, data):
    (ROOT / name).write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def main():
    start = datetime(2024, 4, 1, tzinfo=timezone.utc)
    rows = []
    for hour in range(12 * 24 + 1):
        stamp = start + timedelta(hours=hour)
        if stamp == start + timedelta(days=3, hours=12):
            continue  # One missing hour, visible in both quality metrics.
        cycle = (stamp - timedelta(hours=1)).day - 1
        step = stamp.hour or 24
        mm = (cycle % 4 + 1) * step / 24
        if stamp == start + timedelta(days=7, hours=18):
            mm = 0.1  # Deliberate cumulative decrease, rejected as invalid.
        rows.append({"datetime": stamp.isoformat(),
            "available_at": (stamp + timedelta(hours=3)).isoformat(),
            "temperature_k": round(273.15 + 11 + hour / 96 + 6 * math.sin(stamp.hour * math.pi / 12), 6),
            "precip_m": round(mm / 1000, 9)})
    with (ROOT / "era5_hourly.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    assets = {k: {"raster:bands": [{"scale": 0.0001, "offset": -0.1, "nodata": 0}]} for k in ["red", "nir", "swir16"]}
    observations = []
    for day, tile in [(1, "a"), (3, "a"), (3, "b"), (5, "a"), (7, "a"), (9, "a"), (12, "a")]:
        for index, field in enumerate(["synthetic-field-a", "synthetic-field-b"]):
            stamp = datetime(2024, 4, day, 10, tzinfo=timezone.utc)
            available = stamp + timedelta(days=2 if day == 9 else 0, hours=4)
            observations.append({"field_id": field, "item_id": f"synthetic-{day:02d}-{tile}",
                "datetime": stamp.isoformat(), "available_at": available.isoformat(),
                "assets": assets, "geometry_mask": [[True, True, True], [True, True, True], [True, True, False]],
                "red": [[2000 + day * 20, 2200, 2100], [2300, 0, 2100], [2000, 2200, 1900]],
                "nir": [[5000 + day * 65 + index * 100, 5400, 5300], [5200, 5500, 5000], [5100, 5400, 5000]],
                "swir16": [[4000, 4100, 0], [4200, 4300, 4000], [4200, 3900, 4000]],
                "scl": [[9, 9, 9], [9, 9, 9], [9, 9, 9]] if index == 1 and day == 5 else [[4, 5, 4], [4, 4, 9], [4, 7, 4]]})
    save("satellite_arrays.json", {"data_kind": "synthetic", "observations": observations})
    query = {"collections": ["sentinel-2-l2a"], "bbox": [9.9, 56.0, 10.0, 56.1],
        "datetime": "2024-04-01T00:00:00Z/2024-04-12T23:59:59Z", "limit": 2,
        "query": {"eo:cloud_cover": {"lte": 100}}}
    items = [{"type": "Feature", "collection": "sentinel-2-l2a", "id": f"synthetic-item-{i}",
        "geometry": None, "properties": {"datetime": f"2024-04-0{i}T10:00:00Z", "synthetic": True},
        "assets": assets} for i in [1, 2, 3]]
    save("stac_pages.json", [{"type": "FeatureCollection", "features": items[:2],
        "links": [{"rel": "next", "href": "https://earth-search.aws.element84.com/v1/search?page=2"}]},
        {"type": "FeatureCollection", "features": items[1:], "links": []}])
    save("jsonstat.json", {"label": "Synthetic JSON-stat schema exercise; no official statistics",
        "id": ["geo", "time"], "size": [2, 2],
        "dimension": {"geo": {"category": {"index": {"XX_A": 0, "XX_B": 1}}},
                      "time": {"category": {"index": ["2022", "2023"]}}},
        "value": {"0": 4.0, "1": 4.5, "3": 3.75}, "status": {"1": "p", "2": ":"}})
    save("fixture_metadata.json", {"data_kind": "synthetic", "randomness": "none",
        "satellite_cutoff_utc": "2024-04-10T23:59:59Z", "weather_start": "2024-04-01", "weather_end": "2024-04-12",
        "weather_available_by_utc": "2024-04-13T06:00:00Z", "synthetic_snapshot_time_utc": "2024-04-13T06:00:00Z",
        "minimum_pixels": 1, "minimum_clear_fraction": 0.0, "stac_query": query})


if __name__ == "__main__":
    main()
