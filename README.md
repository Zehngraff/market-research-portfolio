# Market and physical data pipelines

Selected data-collection and processing code from my prediction-market and satellite/weather research. The two modules focus on traceable inputs, repeatable ingestion and checks before data reaches an analysis.

Core routines are extracted or adapted from the research implementations. Public interfaces, offline fixtures and tests make those components inspectable without exposing research-specific selection rules, calibration or private datasets.

## Explore the code

### Prediction-market data collection

[market-data](market-data/) collects explicitly requested public market data into a content-addressed archive and SQLite index. It handles retry and storage budgets, cursor checkpoints, deduplication and quote validation.

- [Content-addressed archive and retry budgets](market-data/market_archive/archive.py)
- [Collection, indexing and checkpoints](market-data/market_archive/collector.py)
- [Kalshi metadata and trade-page adapter](market-data/market_archive/kalshi.py)
- [Quote, fee and settlement validation](market-data/market_archive/validation.py)
- [Module setup and commands](market-data/README.md)
- [Case study](case-studies/01-prediction-market-research.md)

The offline example exercises the collection path against controlled responses and produces evidence that can be inspected and verified. Optional live collection requires an explicit command and input manifest.

### Satellite and weather data processing

[physical-data](physical-data/) discovers public satellite records, archives public statistical responses and transforms satellite arrays and hourly weather into research features.

- [Satellite catalogue acquisition](physical-data/physical_data/acquisition.py)
- [Sentinel-2 features and observation aggregation](physical-data/physical_data/satellite.py)
- [Weather accumulation and daily features](physical-data/physical_data/weather.py)
- [JSON-stat decoding](physical-data/physical_data/jsonstat.py)
- [Module setup and commands](physical-data/README.md)
- [Case study](case-studies/02-physical-data-research.md)

The included examples use small, labelled fixtures. Full raster retrieval, geospatial reprojection and authenticated climate downloads are outside this extracted module.

## Run both modules offline

Use Python 3.11 or later. The market-data module uses the standard library; physical-data also uses NumPy and pandas.

Verified on Python 3.12.14 with NumPy 2.3.5 and pandas 2.2.3: **106 tests pass** (62 market-data, 44 physical-data). Both examples repeat byte-for-byte in fresh directories in that environment.

```sh
python -m pip install -r requirements.txt
python verify.py
```

The [verification runner](verify.py) executes both test suites, runs each example twice in fresh directories, compares every generated file and checks the market archive. It does not call live collection. Live collection paths have not been verified against live services for this release; no real observations are bundled. To retain the resulting archives, tables and verification report:

```sh
python verify.py --output output/verified
```

Choose a new output directory. Existing files are never overwritten by the root runner. Individual module READMEs document their offline and live commands separately.

## Inspect generated outputs

The checked-in examples are generated from labelled offline fixtures using the collection and transformation functions above:

- [Market-data archive walkthrough](market-data/examples/demo-report.md) and [verification counts](market-data/examples/demo-summary.json): pagination, retries, resumed collection and evidence reconciliation.
- [Physical-data quality report](physical-data/examples/output/quality_report.json): coverage, catalogue replay and missing-data checks.
- [Daily weather table](physical-data/examples/output/weather_daily.csv): temperature, precipitation-cycle checks and accumulated features.
- [Satellite feature table](physical-data/examples/output/satellite_features.csv): observation coverage, cutoff filtering and vegetation/moisture summaries.

These outputs demonstrate the processing path. They are not real field measurements, revenue forecasts or trading results.

## Research context

The market project deals with imperfect quote, trade and settlement evidence. The physical-data project deals with observation coverage, weather timing and the gap between physical production and recognised revenue. Both require careful handling of dates, units and missing information before modelling.

These skills are relevant to power-market research, where weather affects production and volume uncertainty interacts with prices. This repository demonstrates data engineering and preprocessing. It does not establish forecast accuracy, profitable trading or operating experience on a power desk.

## Included scope

Public collection, transformation, quality checks and reproducible tests are included. Strategies, market selection, fitted parameters, private datasets, company-specific forecasts and original repository history remain outside this repository. See each module's README for the boundary between retained research routines and the public demonstration wrappers.
