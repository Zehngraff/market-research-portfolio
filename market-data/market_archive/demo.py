"""A repeatable fault-injection exercise for the extracted collection engine."""
import json
from pathlib import Path

from .archive import Archive
from .collector import run
from .replay import FIXED_TIME, MANIFEST, SyntheticReplay
from .verify import verify


def demo(output):
    root = Path(output)
    if root.exists() and any(root.iterdir()):
        raise ValueError('demo output must be a new or empty directory; existing evidence is never overwritten')
    root.mkdir(parents=True, exist_ok=True)
    reports = []
    for repaired in (False, True):
        replay = SyntheticReplay(repaired=repaired)
        archive = Archive(root, transport=replay, clock=lambda: FIXED_TIME,
                          monotonic=lambda: 0, sleep=lambda _: None, source_label='SYNTHETIC_FIXTURE')
        result = run(root, MANIFEST, archive=archive)
        result['fixture_transport_calls'] = len(replay.calls)
        reports.append(result)
    initial, resumed = reports
    checks = dict(
        rate_limit_retried_once=len(initial['retries']) == 1,
        duplicate_rows_detected=initial['trades'][0]['identical_rows'] == 1,
        bad_page_left_checkpoint_unchanged=initial['inventory'][0]['exhausted'] == 0,
        invalid_page_reported=any(e['condition'] == 'demo-closed' for e in initial['errors']),
        cursor_resumed=initial['inventory'][1]['cursor'] == 'cursor-b' and resumed['inventory'][1]['exhausted'] == 1,
        recovered_without_duplicate_rows=resumed['trade_index']['fingerprints'] == 4,
        stale_book_rejected=any(not b['valid_quote'] for b in resumed['books']),
        invalid_fee_rejected=any(not b['fee_available'] for b in resumed['books']),
        no_strategy_readiness_claim=not initial['strategy_ready'] and not resumed['strategy_ready'],
    )
    verification = verify(root)
    result = dict(source_label='SYNTHETIC_FIXTURE', executed_offline=True,
                  exercise_passed=all(checks.values()) and verification['passed'],
                  assertions=checks, archive_verification=verification, runs=reports,
                  outcome='Pipeline and fault handling demonstrated; sample snapshot quality intentionally fails.')
    (root / 'demo-report.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    (root / 'demo-report.md').write_text(render_report(result))
    if not result['exercise_passed']:
        raise RuntimeError('offline pipeline exercise failed; inspect demo-report.json')
    return result


def render_report(report):
    a, b = report['runs']
    verified = report['archive_verification']
    return f'''# Public market-data archive: offline evidence report

All inputs are clearly labelled SYNTHETIC_FIXTURE. The transport is a replay,
while archive writes, SQLite transactions, cursor resume, deduplication,
validation, retries and reconciliation run through the extracted implementation.
No network request is made by this demo.

## Observed pipeline behavior

- First pass: {a['validated_pages']} valid pages committed; {len(a['errors'])} invalid page rejected and raw response retained.
- A simulated HTTP 429 is retried {len(a['retries'])} time using bounded Retry-After handling.
- One repeated response row is recognized by its canonical fingerprint.
- Second pass resumes the saved cursor and retries the rejected market.
- Final index: {b['trade_index']['fingerprints']} distinct response-row fingerprints across {b['validated_pages']} committed pages.
- Independent replay verification checks {verified['raw_files_checked']} compressed content hashes, {verified['indexed_rows_checked']} indexed rows and {verified['committed_pages_checked']} committed page records.
- A stale book and non-finite fee remain rejected. Collection recovery does not turn poor snapshot quality into a pass.

## Evidence and limits

Exercise passed: {str(report['exercise_passed']).lower()}.
Archive reconciliation passed: {str(verified['passed']).lower()}.
Snapshot-quality gate passed: {str(b['snapshot_quality_passed']).lower()} (intentionally false).
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
'''
