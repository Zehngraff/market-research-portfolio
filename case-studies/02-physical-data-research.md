# Satellite and physical data research

I have been developing satellite and weather features alongside conditional revenue scenarios based on agricultural operating data and external prices. The work separates observable activity, saleable volume and recognised revenue.

## What I built

- A field-level feature bundle combining parcel and crop metadata, Sentinel-2 vegetation indices and ERA5-Land weather features.
- Source registries and lineage records for public disclosures and external market data, with dates, units and missing-input checks.
- Crop accounting, production-calendar and feed-related research components that expose gaps in the physical-to-financial reconciliation.
- Conditional group-revenue scenario work using annual-report physical data and external price information. Quarterly and half-year financial outcomes are reserved for held-out evaluation rather than used as model inputs.

The satellite feature work and the group-revenue scenarios are components of the same research direction. A fully integrated, independently validated satellite-to-group-revenue forecast has not been established.

## Modelling choices and limitations

The pipeline is conceptually: dated observations, physical production, saleable volumes, realised prices and revenue timing. Each step needs its own evidence. Harvested crops can enter inventory or be used as feed; production may be sold later; benchmark prices can differ from realised prices because of contracts, product mix and local basis.

Historical field coverage and source vintages are incomplete. Current parcel evidence can misrepresent older operating footprints, and a source hash identifies downloaded bytes without proving historical availability. Missing volume, inventory or price evidence therefore remains a visible limitation rather than being filled with false precision.

The output is conditional scenario research. Forecast accuracy and profitable application are unproven. Further validation would require frozen, dated scenarios scored against later disclosures, with volume, price and timing errors kept separate.

## Relevance to power markets

The connection is the reasoning: weather affects physical output, while volume and price uncertainty interact. Renewable power introduces different assets, time horizons and market rules. This project demonstrates experience with imperfect physical data and explicit uncertainty; it does not claim operating experience on a power trading desk.
