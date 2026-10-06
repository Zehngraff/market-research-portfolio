# Market research and data controls

Two project case studies and a small Python demonstration of research controls: what was known at a decision time, whether an observation is usable, and when committed capital becomes available again.

I work on research infrastructure and physical-data problems. This portfolio shows selected engineering work while keeping research-specific hypotheses, selection rules and datasets private.

## Start here

1. [Prediction market research infrastructure](case-studies/01-prediction-market-research.md): collection, evidence checks and realistic constraints on paper evaluation.
2. [Satellite and physical data research](case-studies/02-physical-data-research.md): connecting weather, physical production and prices to conditional revenue scenarios.
3. Run the synthetic demo below, then inspect the tests.

The case studies describe broader research projects. The code here is a new, self-contained demonstration of general controls. All inputs and outputs are synthetic. It is not a trading strategy, a forecast or evidence of profitability.

## Run the demo

Requires Python 3.11 or later. There are no third-party dependencies, downloads, credentials or network calls.

```sh
python -m research_demo
python -m unittest discover -s tests -v
```

Run both commands from this repository's root directory. On Windows, `py -3` can replace `python`.

Verified on Python 3.12.14: 74 tests pass. Repeated demo runs produce identical JSON, and the Python and SQLite as-of results agree. These are software checks on synthetic inputs.

## Code map

- [temporal.py](research_demo/temporal.py): versioned observations, Python and SQLite as-of lookups, and a purged chronological holdout.
- [execution.py](research_demo/execution.py): quote rejection reasons, fee and depth checks, and the settlement-aware cash ledger.
- [demo.py](research_demo/demo.py): invented fixtures and hand-specified order attempts.
- [asof_features.sql](sql/asof_features.sql): a window-function join that preserves missing features as `NULL`.
- [tests](tests/): boundary cases, malformed inputs, split contamination and accounting invariants.

## What the code demonstrates

- **Information cutoffs.** Observations carry an event time and an availability time. An as-of lookup uses only information available strictly before the decision cutoff, including the correct historical revision.
- **Event-aware chronological splitting.** Training observations are purged when their parent event is shared with evaluation data or their outcome or holding window crosses the evaluation boundary.
- **Quote checks.** Reject future or stale quotes, quotes received too late, wrong instruments, crossed books and invalid numbers.
- **Execution constraints.** A hand-specified order must fit the displayed depth and its cash budget after fees. No missing liquidity or partial fill is invented.
- **Capital lifecycle.** Cash committed to an open position cannot fund another order. Settlement requires verified, instrument-matched evidence available strictly before the processing cutoff.
- **SQL parity.** A SQLite as-of join runs on the synthetic fixture and is checked against the Python implementation.

Time is represented by integer ticks. Records first available at the cutoff are excluded because their ordering within that tick is unknown. Label and holding windows are half-open; a window ending exactly at the split boundary does not overlap the evaluation period. Training labels must still be available strictly before that boundary.

The console output records decisions and accounting states. The accepted examples are mechanical demonstrations; no signal, return series, Sharpe ratio or performance backtest is calculated.

## Design boundaries

The implementation is deliberately narrow. Displayed depth is only an upper-bound check, not a promise of execution. The demo does not model queue priority, changing order books, latency, partial fills, market impact, margin, mark-to-market risk or settlement disputes. Timestamp cutoffs assume that the supplied availability records are trustworthy; they cannot repair missing historical source vintages. Settlement verification is a supplied fixture flag here, not an external resolution check.

Binary synthetic instruments keep the cash lifecycle easy to inspect. European power contracts have different settlement, delivery, balancing and asset constraints. No experience trading EPEX products is implied by this demonstration.

Passing these tests establishes the tested mechanics on controlled inputs. It does not validate the original research codebases, forecast accuracy or live trading performance.

## Included here

This folder contains selected, newly written demonstration code and concise project descriptions. It excludes original repository history, private data, live endpoints, current strategy reports, market filters, calibrated parameters and company-specific forecasts.
