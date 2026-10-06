# Source-derived market-data research archive

A selective extraction of working data infrastructure from the author's private
prediction-market research code. This is the actual archive, checkpointing and
validation implementation adapted into a small standalone package, with its
original applicable tests and additional regression coverage.

The default walkthrough runs offline. Only its inputs are synthetic. The
collector, gzip archive, SQLite transactions, validation and evidence checks are
real code paths. No strategy or trading system is included.

## Run it in under a minute

Python 3.11+; runtime and tests need no third-party dependencies. From this folder:

```sh
python -m unittest discover -s tests -v
python -m market_archive demo --output output/demo
python -m market_archive verify --root output/demo
```

Use a fresh output directory for each demo. Existing evidence is never cleared or
overwritten by the demo command. To install the CLI in a virtual environment:

```sh
python -m pip install .
market-archive demo --output output/installed-demo
```

The optional package install needs pip plus one-time access to the declared
setuptools build backend (from a configured package index or local cache); pip
creates its isolated build environment. Runtime and source-tree tests remain
standard-library-only. The verified install used preinstalled build tools with
build isolation disabled; a clean-environment backend download has not been
verified here. The source-tree commands above require no package installation.

Open [the regenerated sample report](examples/demo-report.md) for an immediate
walkthrough. [The compact JSON summary](examples/demo-summary.json) exposes every
assertion and verification count. Generated raw files and databases remain local
and are excluded from version control.

## What happens

1. An explicit manifest registers two fictional market IDs. No universe selection,
   ranking, strategy category filter or private discovery policy is present.
2. A page is archived as a gzip JSON envelope containing request parameters, receipt
   time, source label and response. Its filename is the compressed content's SHA-256.
3. The page validator checks identity, price, size, integer timestamp and cursor
   progress. Invalid pages retain raw evidence without advancing the database.
4. Valid rows, page evidence and the next cursor commit in one SQLite transaction.
   Canonical response-row fingerprints identify exact duplicates.
5. A synthetic 429 exercises bounded Retry-After/backoff. A second run resumes a
   saved cursor and recovers a previously rejected page.
6. A stale order book and a non-finite fee remain rejected even after collection
   recovers. Collection status and snapshot-quality status are separate.
7. The verifier checks compressed hashes, bidirectional page/index correspondence,
   foreign keys and committed checkpoint evidence using a read-only SQLite connection.

The demo is deterministic, including SQLite bytes, across two clean runs in the
same Python/SQLite environment. It mocks no database or validation functions. Its
transport has no network fallback, and a regression test forbids HTTP access.

## Modules and source lineage

- `archive.py`: extracted content-addressed raw archive and bounded retry loop;
  enhanced with injected transport/clock, decompressed-size limits, atomic writes,
  and a monotonic time budget
- `collector.py`: extracted trade pagination, fingerprint indexing, checkpoint
  resume, book/fee collection; private discovery replaced by explicit input IDs
- `validation.py`: extracted book, trade-page and final-resolution contracts;
  tightened for malformed types, non-finite/negative diagnostic amounts, missing
  minimum size, ambiguous outcome identity and invalid cursors
- `transport.py`: the original small JSON HTTP helper separated from research
  orchestration; single-attempt public GET reads, fixed hosts and no redirects
- `kalshi.py`: optional source-derived catalog/trade adapter, keyed trade-ID
  deduplication, metadata joins, unknown-category quarantine and backfill/head
  separation. Tested with fixtures; not part of the Polymarket demo or CLI
- `replay.py`, `demo.py`, `verify.py`: new public-safe fixture, walkthrough and
  independent evidence reconciliation
- `tests/*_carried.py`: 19 carried-over tests, scoped to extracted functionality
- `tests/test_regressions.py`: added correctness, fault, tamper and determinism tests

The extraction preserves engineering behavior rather than private research
parameters. Applicable tests were adapted for renamed imports and the explicit
manifest schema. Tests tied to private selection, paper entries, hypotheses or
trading decisions were intentionally not copied. These checks do not certify the
entire original project.

## Data model

`markets` stores explicit identifiers, supplied event grouping, source group,
metadata, last attempt, next cursor, endpoint-exhaustion status and a separate
closure-refresh state (`none`, `pending` or `active`).

`rows` stores a response fingerprint, market identity, source timestamp and raw
file reference. This fingerprint is not claimed to be an exchange fill ID.

`pages` provides the raw-file link for each committed request/next-cursor pair.
`cursor_history` detects cycles across interrupted runs, not just within one call.

Raw responses are written before validation. SQLite page and row changes are
atomic. A failed page preserves the last good checkpoint. Completed open-market
traversals may start another overlapping traversal. On an open-to-closed change,
a fresh traversal is scheduled durably; exhaustion from before closure does not
suppress it. If the old traversal is incomplete, its saved cursor is completed
first, then the fresh closed traversal starts on the next bounded run. Inventory
shows `pending` or `active` until that fresh traversal successfully exhausts;
failed requests preserve both the refresh state and last good checkpoint.
Subsequent closed runs skip only after it completes. Reopening clears the closure
refresh requirement. Upgrading an older archive schedules a conservative refresh
for existing closed rows because their exhaustion timing is unknown.
Run one writer per archive directory. Multi-process coordination is not supported.

## Optional public collection

The demo and tests make no external requests. Live collection is a separate,
explicit command that requires a manifest of caller-selected IDs:

```sh
python -m market_archive collect --live --manifest my-markets.json --root output/live
```

The manifest is a JSON array with `condition`, `event_id`, `source_group` strings
and an explicit `closed` boolean. Supply current public identifiers yourself.
The synthetic manifest is in `market_archive/replay.py` for illustration only.
No account, credentials, wallet, order submission, scheduled job or strategy is
used. Collection is bounded by page, time and archive-size limits; its safety
budgets are visible in `Limits`. Duration is checked between requests and the
configured socket timeout is passed to HTTP; this is not a hard wall-clock
interrupt of a slow streaming response. The live adapters have not been called in this
portfolio validation. API schema and availability may change.

`collect` exit code 0 means no collection-stage errors; inspect
`snapshot_quality_passed` independently. `verify` exit code 0 means evidence
reconciliation passed. Neither status grants readiness for a strategy.

## Limits that remain visible

- Explicit IDs are a sample, not complete market discovery
- Endpoint exhaustion is not proof of complete exchange history
- Identical API rows are not necessarily unique economic fills
- Absence of trades is not evidence of a data-feed outage
- Prospective books/fees are separate snapshots, not historical executable quotes
- Hashes prove byte consistency, not truth, correct chronology or predictive value
- Final-resolution metadata does not establish when a label was first available
- No profitability, forecast quality, live execution or out-of-sample claim is made

The public folder contains no original datasets, private configs, trading rules,
calibrated parameters, credentials, logs or repository history. No license has
been added by this extraction.
