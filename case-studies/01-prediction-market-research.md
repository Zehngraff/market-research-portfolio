# Prediction market data collection

I built data infrastructure for prediction-market research so candidate observations could be traced back to their underlying responses. The collection problem includes changing pagination, repeated trades, incomplete quotes and data that arrives too late for a historical decision.

## Code included here

The [market-data module](../market-data/) exposes selected collection and validation components:

- A bounded archive that retains compressed responses with content hashes and limits retries, elapsed time and storage growth.
- SQLite indexing and cursor checkpoints, with trade deduplication and resumable page collection.
- Public market metadata, trade-page, order-book and fee collection, separated from research-specific discovery and market selection.
- Quote and settlement checks, plus integrity verification for the retained evidence.

The [collector](../market-data/market_archive/collector.py) and [market adapter](../market-data/market_archive/kalshi.py) are the main entry points for code review. The module README explains the extracted routines and the interfaces adapted for this public version.

## Reproduction

The offline example sends controlled responses through the ingestion path, producing an archive, index and quality report. Repeating it tests determinism without depending on a changing live market. The tests cover normal responses and failure cases, including malformed pages, retries, repeated records and invalid quotes. Live collection is a separate, explicit operation using a user-supplied manifest.

## Research boundary

Retained evidence makes an observation inspectable; it does not establish that a historical quote could have been filled. Source availability, execution and statistical validity require separate checks. The public module contains no trading signal, selection hypothesis or profitability claim, and it is not a certification of every legacy analysis in the broader research project.
