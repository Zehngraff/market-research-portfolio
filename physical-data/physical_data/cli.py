"""Offline reproducible pipeline and explicit opt-in public collection commands."""
import argparse
import json
from pathlib import Path
import sys

import pandas as pd

from .acquisition import SnapshotStore, collect_stac, eurostat_url, sha256, stac_query
from .jsonstat import jsonstat_rows
from .satellite import band_metadata, cutoff_features, observe_arrays
from .weather import daily_weather, era5_request_plan, weather_summary

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "synthetic"


def write_json(path, document):
    path.write_text(json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def process_satellite(raw):
    """Process user-supplied, already co-registered pixel patches."""
    observations = []
    for record in raw["observations"]:
        meta = {k: band_metadata(v) for k, v in record["assets"].items()}
        results = observe_arrays(record["red"], record["nir"], record["swir16"], record["scl"],
                                 metadata=meta, geometry_mask=record["geometry_mask"])
        observations.append({k: record[k] for k in ["field_id", "item_id", "datetime", "available_at"]} | results)
    obs = pd.DataFrame(observations)
    return obs


def demo(output, fixture_dir=FIXTURES):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    fixture_dir = Path(fixture_dir)
    config = json.loads((fixture_dir / "fixture_metadata.json").read_text())
    if config.get("data_kind") != "synthetic":
        raise ValueError("demo expects explicitly synthetic fixtures")
    raw = json.loads((fixture_dir / "satellite_arrays.json").read_text())
    obs = process_satellite(raw)
    obs.to_csv(output / "satellite_observations.csv", index=False, float_format="%.10g")
    daily_sat, features = cutoff_features(obs, config["satellite_cutoff_utc"],
        minimum_pixels=config["minimum_pixels"], minimum_clear_fraction=config["minimum_clear_fraction"])
    daily_sat.to_csv(output / "satellite_daily.csv", index=False, float_format="%.10g")
    features.to_csv(output / "satellite_features.csv", index=False, float_format="%.10g")
    weather = pd.read_csv(fixture_dir / "era5_hourly.csv")
    daily = daily_weather(weather, config["weather_start"], config["weather_end"], available_by=config["weather_available_by_utc"])
    daily.to_csv(output / "weather_daily.csv", index=False, float_format="%.10g")
    write_json(output / "weather_summary.json", weather_summary(daily))
    table = json.loads((fixture_dir / "jsonstat.json").read_text())
    pd.DataFrame(jsonstat_rows(table, include_missing=True)).to_csv(output / "public_table.csv", index=False)
    pages = json.loads((fixture_dir / "stac_pages.json").read_text())
    def fixture_transport(method, url, body):
        # Same collector and cache code as live mode; only transport is replaced.
        if method == "POST" and body == config["stac_query"]:
            page = pages[0]
        elif method == "GET" and url.endswith("?page=2") and body is None:
            page = pages[1]
        else:
            raise ValueError("Unexpected fixture request")
        return json.dumps(page, sort_keys=True, separators=(",", ":")).encode()
    store = SnapshotStore(output / "synthetic_snapshots", transport=fixture_transport,
                          clock=lambda: config["synthetic_snapshot_time_utc"], data_kind="synthetic")
    catalogue = collect_stac(store, config["stac_query"])
    catalogue["data_kind"] = "synthetic"
    write_json(output / "stac_catalogue.json", catalogue)
    # Cache replay verifies both integrity and that the network is unnecessary.
    replay = SnapshotStore(output / "synthetic_snapshots", offline=True, data_kind="synthetic")
    if collect_stac(replay, config["stac_query"]) != {k: v for k, v in catalogue.items() if k != "data_kind"}:
        raise RuntimeError("Offline replay differs from original collection")
    write_json(output / "era5_request_plan.json", {"executed": False,
        "purpose": "Illustrative request planning only, no CDS connection",
        "requests": era5_request_plan(config["weather_start"], config["weather_end"], [56.1, 9.9, 56.0, 10.0])})
    report = {"data_kind": "synthetic", "no_real_observations_in_demo": True,
        "satellite_observation_rows": len(obs), "cutoff_daily_rows": len(daily_sat),
        "satellite_feature_rows": len(features), "satellite_cutoff_utc": config["satellite_cutoff_utc"],
        "weather": weather_summary(daily), "catalogue_pages": catalogue["page_count"],
        "catalogue_items": len(catalogue["features"]), "offline_cache_replay_equal": True,
        "quality_settings": {"minimum_pixels": config["minimum_pixels"], "minimum_clear_fraction": config["minimum_clear_fraction"]},
        "limitations": ["Synthetic values demonstrate computation, not predictive performance.",
            "Satellite arrays are already co-registered; raster retrieval/reprojection is not part of this package.",
            "Same-date tile weighting does not spatially deduplicate overlapping pixels.",
            "Fixture availability times illustrate as-of filtering; no historical production availability is claimed."]}
    write_json(output / "quality_report.json", report)
    inputs = [{"file": p.name, "sha256": sha256(p.read_bytes()), "bytes": p.stat().st_size}
              for p in sorted(fixture_dir.iterdir()) if p.is_file()]
    outputs = [{"file": p.relative_to(output).as_posix(), "sha256": sha256(p.read_bytes()), "bytes": p.stat().st_size}
               for p in sorted(output.rglob("*")) if p.is_file() and p != output / "manifest.json"]
    write_json(output / "manifest.json", {"schema_version": 1, "data_kind": "synthetic",
        "generated_at_utc": config["synthetic_snapshot_time_utc"],
        "timestamp_is_fixed_fixture_metadata": True, "inputs": inputs, "outputs": outputs})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    offline = commands.add_parser("demo", help="Run offline synthetic fixtures through extracted algorithms")
    offline.add_argument("--output", type=Path, required=True)
    weather = commands.add_parser("weather", help="Process normalized ERA5-Land hourly CSV, without network")
    weather.add_argument("--input", type=Path, required=True)
    weather.add_argument("--start", required=True)
    weather.add_argument("--end", required=True)
    weather.add_argument("--available-by")
    weather.add_argument("--output", type=Path, required=True)
    satellite = commands.add_parser("satellite", help="Process co-registered pixel-patch JSON, without network")
    satellite.add_argument("--input", type=Path, required=True)
    satellite.add_argument("--cutoff", required=True)
    satellite.add_argument("--minimum-pixels", type=int, default=1)
    satellite.add_argument("--minimum-clear-fraction", type=float, default=0.0)
    satellite.add_argument("--output", type=Path, required=True)
    collect = commands.add_parser("collect-stac", help="OPT-IN network: collect public catalogue metadata, not raster files")
    collect.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    collect.add_argument("--start", required=True)
    collect.add_argument("--end", required=True)
    collect.add_argument("--snapshot-dir", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--offline", action="store_true", help="Only replay an existing complete snapshot")
    collect.add_argument("--cloud-max", type=float, default=100)
    collect.add_argument("--max-pages", type=int, default=100)
    euro = commands.add_parser("collect-eurostat", help="OPT-IN network: collect one public crop-statistics slice")
    euro.add_argument("--geo", required=True)
    euro.add_argument("--crop", required=True)
    euro.add_argument("--year", type=int, required=True)
    euro.add_argument("--snapshot-dir", type=Path, required=True)
    euro.add_argument("--output", type=Path, required=True)
    euro.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            report = demo(args.output)
        elif args.command == "weather":
            daily = daily_weather(pd.read_csv(args.input), args.start, args.end, available_by=args.available_by)
            args.output.mkdir(parents=True, exist_ok=True)
            daily.to_csv(args.output / "weather_daily.csv", index=False, float_format="%.10g")
            report = weather_summary(daily)
            write_json(args.output / "weather_summary.json", report)
        elif args.command == "satellite":
            obs = process_satellite(json.loads(args.input.read_text(encoding="utf-8")))
            daily, features = cutoff_features(obs, args.cutoff, minimum_pixels=args.minimum_pixels,
                minimum_clear_fraction=args.minimum_clear_fraction)
            args.output.mkdir(parents=True, exist_ok=True)
            obs.to_csv(args.output / "satellite_observations.csv", index=False, float_format="%.10g")
            daily.to_csv(args.output / "satellite_daily.csv", index=False, float_format="%.10g")
            features.to_csv(args.output / "satellite_features.csv", index=False, float_format="%.10g")
            report = {"observation_rows": len(obs), "daily_rows": len(daily), "feature_rows": len(features)}
        elif args.command == "collect-stac":
            query = stac_query(args.bbox, args.start, args.end, cloud_max=args.cloud_max)
            store = SnapshotStore(args.snapshot_dir, offline=args.offline)
            catalogue = collect_stac(store, query, max_pages=args.max_pages)
            args.output.mkdir(parents=True, exist_ok=True)
            write_json(args.output / "catalogue.json", catalogue)
            write_json(args.output / "provenance.json", {"data_kind": "public_catalogue_metadata", "snapshots": store.records})
            report = {"items": len(catalogue["features"]), "pages": catalogue["page_count"], "raster_assets_downloaded": False}
        else:
            store = SnapshotStore(args.snapshot_dir, offline=args.offline)
            raw = store.fetch(eurostat_url(args.geo, args.crop, args.year))
            rows = jsonstat_rows(raw, include_missing=True)
            args.output.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_csv(args.output / "eurostat.csv", index=False)
            write_json(args.output / "provenance.json", {"data_kind": "public_statistics", "snapshots": store.records})
            report = {"rows": len(rows), "nonmissing": sum(not row["is_missing"] for row in rows)}
        print(json.dumps(report, indent=2, allow_nan=False))
        return 0
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(f"Data pipeline failed: {exc}", file=sys.stderr)
        return 2
