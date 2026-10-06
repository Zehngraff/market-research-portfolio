# Physical data: satellite, weather and public statistics

A runnable **data-engineering extraction from an existing agricultural research project**. The original numerical methods are retained and separated from project-specific files and modelling. This module shows catalogue collection, raw-response provenance, physical-unit transformations, pixel-quality handling and cutoff feature construction.

**The checked-in inputs and example outputs are entirely synthetic.** They exercise the extracted implementation; they are not observations, forecasts or a performance result.

## Run the verified offline path

Python 3.11+ is the intended compatibility range. Verified here with Python 3.12.14, NumPy 2.3.5 and pandas 2.2.3.

```sh
cd physical-data
python -m pip install -r requirements.txt
python -m physical_data demo --output examples/output
python -m unittest discover -s tests -v
```

The demo needs no account, network, raster stack, GIS software or credentials. Its input generator is included in `fixtures/synthetic/generate_fixtures.py`. All serialized output is reproducible, including fixed and explicitly synthetic snapshot timestamps.

## What to inspect first

- [Acquisition and snapshot store](physical_data/acquisition.py): public STAC/Eurostat requests, pagination, bounded retries, hash validation and exact replay
- [Weather transformation](physical_data/weather.py): ERA5-Land cumulative precipitation, next-midnight boundary, temperature/GDD and completeness checks
- [Satellite transformation](physical_data/satellite.py): scale/offset/nodata, SCL masks, NDVI/NDMI, same-date weighting and cutoff features
- [JSON-stat decoding](physical_data/jsonstat.py): dimension order, sparse/dense values, missing records and status flags
- [Quality report](examples/output/quality_report.json), [weather daily data](examples/output/weather_daily.csv), [satellite observations](examples/output/satellite_observations.csv), [cutoff features](examples/output/satellite_features.csv) and [input/output hashes](examples/output/manifest.json)

The example produces 14 satellite observation rows, eight same-date/cutoff rows, two feature rows and 12 weather days. Ten rainfall days pass validation; one missing hourly record and one decreasing cumulative cycle remain invalid. The full-period rainfall total is null, while the explicitly partial total of valid days is 22 mm. Those numbers describe the fixture only.

## What was retained and what changed

| Component | Existing method retained | Public extraction changes |
|---|---|---|
| ERA5-Land | Kelvin to Celsius; metres to millimetres; shifted 01:00–next-00:00 cycles; terminal cumulative rainfall; daily min/max GDD | Callable functions; external dates/area; next-midnight request plan; missing/duplicate/inconsistent cycle detection; no incomplete total promoted to a full total |
| Sentinel-style pixels | SCL validity mask; finite-pixel means/medians; NDVI and NDMI ratios | Explicit per-band scale/offset before ratios; nodata and masked arrays; separate NDMI counts; co-registration contract |
| Satellite features | Pixel-weighted same-date collapse; peak/latest NDVI; trapezoidal AUC; NDMI mean/min | Explicit timestamp cutoff and availability filtering; duplicate-key rejection; empty schemas; no embedded parcel identities or fitted thresholds |
| Public statistics | JSON-stat flat-index decoding in dimension order | Dense/sparse support, status/missing retention and schema validation |
| Collection | STAC search, public JSON fetching and source snapshots | All-page GET/POST pagination; immutable request-keyed snapshots; content hashes; cache replay; fail-closed integrity/truncation errors |

The public quality defaults are transparent computational settings, not selected predictive parameters. The original forecasting, target construction, business assumptions, parcel mappings and strategy layers are not included.

## Use your own normalized inputs

No fixture-only algorithms are hidden behind the demo. Both numerical paths accept independent inputs:

```sh
python -m physical_data weather --input normalized_hourly.csv --start 2024-04-01 --end 2024-04-12 --output runs/weather
python -m physical_data satellite --input co_registered_patches.json --cutoff 2024-04-10T23:59:59Z --output runs/satellite
```

Weather CSV requires `datetime`, `temperature_k` and `precip_m`. Timestamps must be timezone-aware and exactly hourly. `precip_m` must contain **ERA5-Land hourly cumulative precipitation**, not hourly increments or daily totals. Include the following day's 00:00 value. For as-of filtering, supply `available_at` and pass `--available-by` with an explicit timezone. Without that option the weather command is retrospective processing; it makes no historical-availability claim.

Satellite JSON uses the shape in [the input fixture](fixtures/synthetic/satellite_arrays.json): an `observations` array containing `field_id`, `item_id`, `datetime`, `available_at`, `red`, `nir`, `swir16`, `scl`, `geometry_mask` and per-band `assets` metadata. The three spectral assets require explicit scale and offset in either Raster v1 `raster:bands` or Raster v2 `bands` format. All pixel arrays must already be co-registered on the same grid. A missing transform is rejected rather than guessed. The cutoff is inclusive, applied to both acquisition and availability timestamps. Supply one intended seasonal/year window at a time.

## Optional public collection commands

These commands make network requests only when explicitly run. **Their request/snapshot/pagination behavior is tested with synthetic responses; live endpoint execution was not verified in this build environment.** No downloaded public observations are bundled.

```sh
# Neutral example bounding box; collect catalogue metadata only.
python -m physical_data collect-stac --bbox 9.9 56.0 10.0 56.1 --start 2024-04-01 --end 2024-04-12 --snapshot-dir runs/stac-vintage-1 --output runs/stac

# Small official crop-statistics query; availability depends on Eurostat's codes/data.
python -m physical_data collect-eurostat --geo DK --crop C1100 --year 2023 --snapshot-dir runs/eurostat-vintage-1 --output runs/eurostat
```

Use `--offline` with the same arguments and snapshot directory to replay a complete capture without network. A snapshot directory represents one vintage. Choose a new directory for a fresh pull. Existing payloads are never silently refreshed; wrong hashes, incomplete cache files and changed request identities cannot be mistaken for the same capture. The client accepts only the two supported public HTTPS hosts and accepts no credentials. STAC page-limit exhaustion raises an error rather than reporting partial results as complete.

Live catalogue collection retains asset metadata but does **not** download or open raster assets. It does not generate observations automatically. Full raster clipping, reprojection and mosaicking are outside this minimal module. Header-dependent STAC pagination is explicitly unsupported and fails loudly; ordinary GET and POST body pagination are covered. Public services may change availability, schemas and terms.

## Scientific and operational boundaries

- ERA5-Land is a gridded reanalysis, not a field rain gauge. This CSV entry point starts after any chosen spatial aggregation. It does not include a NetCDF/CDS downloader.
- The request planner includes the next midnight even across month/year boundaries. It only writes a plan; it does not sign in or download anything.
- A missing interior cumulative step makes this pipeline's conservative daily rainfall estimate invalid, even if the terminal accumulation exists. Observed lower bounds and coverage remain visible.
- GDD bases 5°C and 10°C, a 30°C heat flag, a 0°C frost flag and a 1 mm dry-day flag are visible ordinary methodological features, not fitted forecasts.
- SCL defaults `(4, 5, 6, 7)` retain the source mask. Water and unclassified pixels may be inappropriate for a particular AOI; configure a different mask in the array function if required.
- NDVI AUC is a trapezoidal integral between retained acquisition dates, in index-days. There is no interpolation beyond the endpoints, correction for long gaps, crop-specific season modelling or performance claim.
- Same-date tile weighting avoids duplicated dates in AUC. It does not spatially deduplicate overlapping tile pixels or create a mosaic.
- The availability field must be supplied from a defensible source. A historical acquisition date alone does not prove what was known at a historical cutoff; a current snapshot cannot reconstruct past publication vintages.
- JSON-stat decoding exposes source status flags. Consumers must choose how provisional/estimated records are used; this module does not silently discard those labels.

## Tests and data rights

The 50 offline tests cover physical unit calculations, cumulative rainfall boundaries, leap/year transitions, missing/bad/duplicate inputs, nodata/cloud/scale/offset handling, NDMI pixel counts, same-date weighting, cutoff leakage, JSON-stat dimension order and scalar/dense/sparse status handling, pagination, corrupt caches and byte-identical demo reruns. See [tests](tests/).

No project-level software licence is selected here. No private parcel data, downloaded reports, proprietary archives or real satellite/weather observations are included. Dependency licences remain those of their authors. See [source and data notes](SOURCES.md) before acquiring or redistributing provider data.
