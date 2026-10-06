# Satellite and weather data processing

I have been developing satellite and weather features alongside research that connects agricultural operating data to revenue scenarios. The data work needs to preserve observation timing and coverage while keeping physical output, saleable volume and recognised revenue distinct.

## Code included here

The [physical-data module](../physical-data/) exposes the acquisition and preprocessing layer:

- Public catalogue queries and archived responses with provenance, alongside a decoder for Eurostat JSON-stat data.
- Sentinel-2 vegetation and moisture indices, scene-classification checks and valid-observation summaries.
- Same-date observation aggregation, cutoff-aware seasonal features and phenology calculations.
- ERA5-Land daily weather transformations, including the shifted accumulation window for precipitation and growing-degree-day features.

Start with the [satellite routines](../physical-data/physical_data/satellite.py), [weather transformations](../physical-data/physical_data/weather.py) and [catalogue client](../physical-data/physical_data/acquisition.py). The module README identifies the routines adapted from the research project and the acquisition interfaces added for this public version.

## Reproduction

Small, labelled fixtures exercise the numerical transformations and response parsing without distributing field records or proprietary datasets. The generated tables show intermediate features and data-quality information. The tests check units, timing, missing values and malformed input, rather than treating a successful run as evidence of forecast accuracy.

## Research boundary

This extract stops at acquisition and features. It does not download and reproject complete satellite rasters, authenticate to climate-data services or reconstruct the full revenue model. Historical field coverage and source vintages remain limitations in the broader research. Inventory, feed use, contracts and sales timing also prevent production or vegetation measurements from being read directly as revenue. A validated satellite-to-group-revenue forecast has not been established.
