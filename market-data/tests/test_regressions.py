import copy
import gzip
import hashlib
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from market_archive.archive import Archive, Limits
from market_archive.collector import collect_trades, database, register_markets, run
from market_archive.demo import demo
from market_archive.kalshi import validate as validate_kalshi
from market_archive.replay import FIXED_TIME, MANIFEST, SyntheticReplay, page, row
from market_archive.transport import get_json
from market_archive.validation import book_quote, confirmed_outcome, quote_diagnostics, validate_page
from market_archive.verify import verify


class ValidationRegressions(unittest.TestCase):
    def book(self):
        return dict(asset_id='yes', timestamp='1000000', min_order_size='1',
                    bids=[dict(price='.4', size='20')], asks=[dict(price='.5', size='20')])

    def test_reject_nonpositive_and_nonfinite_amount(self):
        for value in (-1, 0, float('nan'), float('inf'), True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                book_quote(self.book(), 'yes', 1001, value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                quote_diagnostics(self.book(), 1001, value)

    def test_missing_minimum_fails_closed(self):
        book = self.book(); del book['min_order_size']
        with self.assertRaises(ValueError):
            book_quote(book, 'yes', 1001, 1)

    def test_all_levels_validated_not_only_best(self):
        for level in [dict(price='.7', size=0), dict(price='nan', size=1), None]:
            book = self.book(); book['asks'].append(level)
            with self.subTest(level=level), self.assertRaises(ValueError):
                book_quote(book, 'yes', 1001, 1)

    def test_age_policy_rejects_nonfinite_or_negative_limits(self):
        for kwargs in (dict(max_age=-1), dict(max_age=float('inf')), dict(future_tolerance=float('nan'))):
            with self.assertRaises(ValueError): book_quote(self.book(), 'yes', 1001, 1, **kwargs)

    def test_top_price_depth_sums_equal_levels(self):
        book = self.book(); book['asks'] = [dict(price='.5', size='1'), dict(price='.5', size='1')]
        self.assertEqual(book_quote(book, 'yes', 1001, 1)[3], 2)

    def test_receive_time_must_be_finite(self):
        for value in ('NaN', True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                book_quote(self.book(), 'yes', value, 1)

    def test_negative_or_future_age_and_crossed_book(self):
        for received in (994, 1121):
            with self.assertRaises(ValueError):
                book_quote(self.book(), 'yes', received, 1)
        book = self.book(); book['bids'][0]['price'] = '.6'
        with self.assertRaises(ValueError):
            book_quote(book, 'yes', 1001, 1)

    def test_bad_trade_envelopes_and_rows(self):
        for payload in (None, [], {}, {'data': {}}, {'data': [None], 'pagination': {'has_more': False}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_page(payload, 'demo-open', now=FIXED_TIME)

    def test_noninteger_or_nonfinite_trade_time(self):
        for stamp in (0, -1, True, 'NaN', 1.5, FIXED_TIME + 301):
            r = row(); r['timestamp'] = stamp
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                validate_page(page([r]), 'demo-open', now=FIXED_TIME)

    def test_pagination_type_and_no_progress(self):
        for pg in ({}, {'has_more': 1}, {'has_more': True, 'next_cursor': 2},
                   {'has_more': False, 'next_cursor': {}}, {'has_more': True, 'next_cursor': ''}):
            with self.subTest(pg=pg), self.assertRaises(ValueError):
                validate_page({'data': [row()], 'pagination': pg}, 'demo-open', now=FIXED_TIME)
        with self.assertRaises(ValueError):
            validate_page(page([], 'next'), 'demo-open', now=FIXED_TIME)

    def test_missing_identity_and_wrong_condition(self):
        for key, value in [('transaction_hash', None), ('transaction_hash', 2), ('token_id', ''),
                           ('side', 'buy'), ('condition_id', 'other'), ('size', True)]:
            r = row(); r[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_page(page([r]), 'demo-open', now=FIXED_TIME)

    def test_resolution_ambiguous_or_invalid_never_passes(self):
        market = dict(closed=True, umaResolutionStatus='resolved', clobTokenIds=['yes','no'], outcomePrices=[1,0])
        for change in (dict(clobTokenIds=['yes','yes']), dict(outcomePrices=['NaN', 0]), dict(outcomePrices='invalid')):
            self.assertIsNone(confirmed_outcome(dict(market, **change), 'yes'))

    def test_kalshi_rejects_malformed_falsy_cursors(self):
        for cursor in (False, 0, [], {}):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                validate_kalshi({'trades':[], 'cursor':cursor}, 'trades', None)
        self.assertEqual(validate_kalshi({'trades':[], 'cursor':''}, 'trades', None), ([],None))

    def test_kalshi_requires_timezone_identity_and_finite_decimal(self):
        r = dict(ticker='T', trade_id='id', created_time='2026-01-01T00:00:00Z',
                 count_fp='1.25', yes_price_dollars='.4', no_price_dollars='.6')
        self.assertEqual(validate_kalshi({'trades':[r]}, 'trades', None)[0], [r])
        for change in (dict(created_time='2026-01-01T00:00:00'), dict(count_fp='NaN'),
                       dict(trade_id=1), dict(no_price_dollars='2')):
            with self.assertRaises(ValueError):
                validate_kalshi({'trades':[dict(r, **change)]}, 'trades', None)
        with self.assertRaises(ValueError):
            validate_kalshi([], 'trades', None)


class ArchiveRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, **kwargs):
        return Archive(self.root, clock=lambda: FIXED_TIME, monotonic=lambda: 0, **kwargs)

    def test_single_retry_owner_exact_attempt_count(self):
        transport = Mock(side_effect=TimeoutError('fixture'))
        sleep = Mock()
        archive = self.archive(transport=transport, sleep=sleep)
        with self.assertRaises(TimeoutError): archive.fetch('fixture', '/page', {})
        self.assertEqual(transport.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2,4])

    def test_malformed_retry_after_uses_bounded_backoff(self):
        transport = Mock(side_effect=[urllib.error.HTTPError('fixture',429,'slow',{'Retry-After':'nonsense'},None),{}])
        sleep = Mock(); archive = self.archive(transport=transport, sleep=sleep)
        archive.fetch('fixture', '/page', {})
        sleep.assert_called_once_with(2)

    def test_nan_retry_after_defers(self):
        transport = Mock(side_effect=urllib.error.HTTPError('fixture',429,'slow',{'Retry-After':'NaN'},None))
        sleep = Mock(); archive = self.archive(transport=transport, sleep=sleep)
        with self.assertRaises(RuntimeError): archive.fetch('fixture','/page',{})
        sleep.assert_not_called()

    def test_http_date_retry_after(self):
        from email.utils import formatdate
        headers = {'Retry-After':formatdate(FIXED_TIME + 9, usegmt=True)}
        transport = Mock(side_effect=[urllib.error.HTTPError('fixture',503,'slow',headers,None),{}])
        sleep = Mock(); archive = self.archive(transport=transport, sleep=sleep)
        archive.fetch('fixture','/page',{})
        sleep.assert_called_once_with(9)

    def test_elapsed_deadline_prevents_request(self):
        transport = Mock(); archive = self.archive(transport=transport)
        archive.monotonic = lambda: 421
        with self.assertRaises(RuntimeError): archive.fetch('fixture','/page',{})
        transport.assert_not_called()

    def test_uncompressed_response_limit(self):
        archive = self.archive(transport=lambda *_: {'x':'a'*5000}, limits=replace(Limits(),response_bytes=1000))
        with self.assertRaises(RuntimeError): archive.fetch('fixture','/page',{})
        self.assertFalse(archive.files)

    def test_compressed_budget_limit_and_no_partial_file(self):
        archive = self.archive(transport=lambda *_: {}, limits=replace(Limits(),run_bytes=1))
        with self.assertRaises(RuntimeError): archive.fetch('fixture','/page',{})
        self.assertFalse(list(self.root.rglob('*.gz')))

    def test_content_address_and_stable_compression(self):
        archive = self.archive(transport=lambda *_: {'ok': True}, source_label='SYNTHETIC_FIXTURE')
        _, _, relative = archive.fetch('fixture','/page',{})
        data = (self.root/relative).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), Path(relative).name.split('.')[0])
        self.assertEqual(json.loads(gzip.decompress(data))['source_label'], 'SYNTHETIC_FIXTURE')
        size = archive.written
        archive.fetch('fixture','/page',{})
        self.assertEqual(archive.written, size)

    def test_existing_hash_corruption_not_silently_reused(self):
        archive = self.archive(transport=lambda *_: {})
        _, _, relative = archive.fetch('fixture','/page',{})
        (self.root/relative).write_bytes(b'corrupt')
        with self.assertRaises(ValueError): archive.fetch('fixture','/page',{})

    def test_transport_rejects_arbitrary_hosts_before_network(self):
        with patch('urllib.request.build_opener') as opener:
            with self.assertRaises(ValueError): get_json('https://example.invalid','/x',{})
            opener.assert_not_called()

    def test_transport_no_nested_retry(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = TimeoutError('fixture')
            with self.assertRaises(TimeoutError): get_json('https://clob.polymarket.com','/book',{})
            self.assertEqual(opener.return_value.open.call_count, 1)

    def test_archive_forwards_configured_timeout(self):
        with patch('market_archive.archive.get_json', return_value={}) as request:
            archive=self.archive(limits=replace(Limits(),request_seconds=3))
            archive.fetch('fixture','/page',{})
            self.assertEqual(request.call_args.kwargs['timeout'], 3)

    def test_transport_forwards_timeout_to_http(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=b'{}'
            get_json('https://clob.polymarket.com','/book',{},timeout=3)
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 3)

    def test_limits_validate_input(self):
        for kwargs in (dict(attempts=0),dict(run_bytes=-1),dict(run_seconds=float('nan')),dict(total_bytes=True)):
            with self.assertRaises(ValueError): Limits(**kwargs)


class PipelineRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def connection(self):
        con = database(self.root); self.addCleanup(con.close)
        register_markets(con, MANIFEST, now=FIXED_TIME)
        return con

    def test_cursor_cycle_detected_across_runs(self):
        con = self.connection()
        transport = Mock(side_effect=[page([row()], 'a'),page([row()], 'b'),page([row()], 'a')])
        archive = Archive(self.root, transport=transport, clock=lambda:FIXED_TIME)
        report = dict(trades=[],errors=[])
        market = con.execute('SELECT * FROM markets WHERE condition="demo-open"').fetchone()
        collect_trades(con, archive, market, report)
        market = con.execute('SELECT * FROM markets WHERE condition="demo-open"').fetchone()
        collect_trades(con, archive, market, report)
        self.assertEqual(con.execute('SELECT cursor FROM markets WHERE condition="demo-open"').fetchone()[0], 'b')
        self.assertEqual(len(report['errors']), 1)

    def test_whole_page_transaction_rolls_back(self):
        con = self.connection()
        con.execute("CREATE TRIGGER reject_page BEFORE INSERT ON pages BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        market = con.execute('SELECT * FROM markets WHERE condition="demo-open"').fetchone()
        archive = Archive(self.root, transport=lambda *_: page([row()], 'a'), clock=lambda:FIXED_TIME)
        report = dict(trades=[],errors=[])
        collect_trades(con, archive, market, report)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM rows').fetchone()[0], 0)
        self.assertIsNone(con.execute('SELECT cursor FROM markets WHERE condition="demo-open"').fetchone()[0])
        self.assertEqual(report['trades'][0]['pages'], 0)
        self.assertEqual(len(archive.files),1)

    def test_manifest_validation_precedes_collection(self):
        transport = Mock()
        archive = Archive(self.root, transport=transport)
        for manifest in ([], MANIFEST*2, [dict(MANIFEST[0],closed=1)], [dict(MANIFEST[0],condition='')]):
            with self.assertRaises(ValueError): run(self.root,manifest,archive=archive)
        transport.assert_not_called()

    def test_unknown_replay_has_no_network_fallback(self):
        with patch('urllib.request.build_opener') as opener:
            with self.assertRaises(ValueError): SyntheticReplay()('unknown','/missing',{})
            opener.assert_not_called()

    def test_demo_offline_and_deterministic_byte_for_byte(self):
        a = self.root/'a'; b = self.root/'b'
        with patch('urllib.request.build_opener', side_effect=AssertionError('network is forbidden')):
            result = demo(a); demo(b)
        self.assertTrue(result['exercise_passed'])
        files = sorted(p.relative_to(a) for p in a.rglob('*') if p.is_file())
        self.assertEqual(files, sorted(p.relative_to(b) for p in b.rglob('*') if p.is_file()))
        for file in files:
            with self.subTest(file=file): self.assertEqual((a/file).read_bytes(), (b/file).read_bytes())

    def test_demo_quality_does_not_fail_open(self):
        report = demo(self.root/'demo')
        first,last = report['runs']
        self.assertFalse(first['collection_complete_without_errors'])
        self.assertTrue(last['collection_complete_without_errors'])
        self.assertFalse(last['snapshot_quality_passed'])
        self.assertFalse(last['strategy_ready'])

    def test_books_stage_failure_blocks_mixed_market_quality_gate(self):
        manifest = [dict(MANIFEST[0]), dict(MANIFEST[1], closed=False)]
        def transport(base, path, params):
            if path == '/v2/trades':
                return page([row(params['condition'])])
            if path == '/markets':
                if params['condition_ids'] == 'demo-closed':
                    return {'malformed': 'metadata envelope'}
                return [{'conditionId':'demo-open', 'closed':False, 'acceptingOrders':True,
                         'clobTokenIds':['yes','no']}]
            if path == '/book':
                return dict(asset_id=params['token_id'], timestamp=str(FIXED_TIME*1000),
                            min_order_size='1', bids=[dict(price='.4',size='10')],
                            asks=[dict(price='.5',size='10')])
            if path == '/fee-rate':
                return {'base_fee': '0'}
            raise AssertionError('unexpected request')
        archive=Archive(self.root, transport=transport, clock=lambda:FIXED_TIME)
        report=run(self.root, manifest, archive=archive)
        self.assertEqual(len(report['books']),2)
        self.assertTrue(all(b['valid_quote'] and b['fee_available'] for b in report['books']))
        self.assertEqual(report['book_skips'],[])
        self.assertTrue(any(e['stage']=='books' for e in report['errors']))
        self.assertFalse(report['collection_complete_without_errors'])
        self.assertFalse(report['snapshot_quality_passed'])

    def test_verify_detects_tampered_raw(self):
        root=self.root/'demo'; demo(root)
        file=next((root/'raw').glob('*.gz')); file.write_bytes(b'corrupt')
        self.assertFalse(verify(root)['passed'])

    def test_verify_detects_tampered_index(self):
        root=self.root/'demo'; demo(root)
        con=sqlite3.connect(root/'index.db')
        with con: con.execute('UPDATE rows SET ts=1')
        con.close()
        self.assertFalse(verify(root)['passed'])

    def test_verify_detects_tampered_page(self):
        root=self.root/'demo'; demo(root)
        con=sqlite3.connect(root/'index.db')
        with con: con.execute('UPDATE pages SET next_cursor="tampered"')
        con.close()
        self.assertFalse(verify(root)['passed'])

    def test_verify_detects_deleted_indexed_row(self):
        root=self.root/'demo'; demo(root)
        con=sqlite3.connect(root/'index.db')
        with con: con.execute('DELETE FROM rows WHERE fingerprint=(SELECT MIN(fingerprint) FROM rows)')
        con.close()
        result=verify(root)
        self.assertFalse(result['passed'])
        self.assertTrue(any(p.get('missing_indexed_rows') == 1 for p in result['problems']))

    def test_verify_detects_orphaned_market(self):
        root=self.root/'demo'; demo(root)
        con=sqlite3.connect(root/'index.db')
        with con: con.execute('DELETE FROM markets WHERE condition="demo-open"')
        con.close()
        result=verify(root)
        self.assertFalse(result['passed'])
        self.assertTrue(any('foreign-key' in p['error'] for p in result['problems']))

    def test_verify_detects_tampered_checkpoint(self):
        root=self.root/'demo'; demo(root)
        con=sqlite3.connect(root/'index.db')
        with con: con.execute('UPDATE markets SET cursor="uncommitted",exhausted=0')
        con.close()
        self.assertFalse(verify(root)['passed'])

    def test_verify_empty_does_not_create_index(self):
        self.assertFalse(verify(self.root)['passed'])
        self.assertFalse((self.root/'index.db').exists())

    def test_demo_does_not_overwrite_evidence(self):
        root=self.root/'demo'; demo(root)
        before=(root/'index.db').read_bytes()
        with self.assertRaises(ValueError): demo(root)
        self.assertEqual((root/'index.db').read_bytes(), before)
