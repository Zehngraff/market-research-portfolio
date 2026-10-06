# Synthetic raw inputs

These are invented computational fixtures, not downloaded observations.

- `era5_hourly.csv`: hourly kelvin temperature and cumulative precipitation in metres; includes a missing interior hour and a decreasing accumulation to exercise quality checks
- `satellite_arrays.json`: small already co-registered pixel grids; includes clouds, nodata, separate NDMI coverage, adjacent same-date items, a late-available acquisition and a future acquisition
- `stac_pages.json`: two synthetic paginated catalogue responses, including one repeated item to exercise deduplication
- `jsonstat.json`: invented sparse values, missing cells and a status flag in JSON-stat dimensions
- `fixture_metadata.json`: deterministic dates, explicit quality settings and a neutral query
- `generate_fixtures.py`: regenerates the five inputs without randomness or network

Run `python fixtures/synthetic/generate_fixtures.py` from the module directory, then run the demo. All responses routed through the snapshot store in the demo are marked synthetic.
