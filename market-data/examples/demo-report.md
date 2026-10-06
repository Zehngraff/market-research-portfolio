# Public market-data archive: offline evidence report

All inputs are clearly labelled SYNTHETIC_FIXTURE. The transport is a replay,
while archive writes, SQLite transactions, cursor resume, deduplication,
validation, retries and reconciliation run through the extracted implementation.
No network request is made by this demo.

## Observed pipeline behavior

- First pass: 2 valid pages committed; 1 invalid page rejected and raw response retained.
- A simulated HTTP 429 is retried 1 time using bounded Retry-After handling.
- One repeated response row is recognized by its canonical fingerprint.
- Second pass resumes the saved cursor and retries the rejected market.
- Final index: 4 distinct response-row fingerprints across 4 committed pages.
- Independent replay verification checks 10 compressed content hashes, 4 indexed rows and 4 committed page records.
- A stale book and non-finite fee remain rejected. Collection recovery does not turn poor snapshot quality into a pass.

## Evidence and limits

Exercise passed: true.
Archive reconciliation passed: true.
Snapshot-quality gate passed: false (intentionally false).
Strategy readiness: false. Market-history completeness: unproven.

The manifest supplies explicit market IDs. There are no strategy rules,
private selection filters, trading clients, or profitability estimates.
An exact-row hash is not an exchange fill ID. Endpoint exhaustion is not
proof of complete history. Book and fee reads are not synchronized. Neither
hash verification nor this fixture exercise validates live API availability.

## Inspect locally

- quality.json: final-run collection and quality detail
- demo-report.json: both runs, assertions and verification counts
- raw/: immutable gzip JSON response envelopes, named by SHA-256
- index.db: markets, rows, pages and cursor_history tables
