import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from market_archive.archive import Archive, fingerprint
from market_archive.collector import database, collect_trades
from market_archive.validation import quote_diagnostics


def page(cursor=None, size=2):
    return dict(data=[dict(condition_id='c', token_id='t', transaction_hash='tx',
        timestamp=1700000000, price=.5, size=size, side='BUY')],
        pagination=dict(has_more=cursor is not None, next_cursor=cursor))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.archive = Archive(self.temp.name)
        self.con = database(self.temp.name)
        self.addCleanup(self.con.close)
        self.con.execute('INSERT INTO markets(condition,event_id,source_group,closed,metadata,discovered) VALUES(?,?,?,?,?,?)',
                         ('c', 'event', 'fixture', 0, '{}', 1))
        self.con.commit()
        self.report = dict(trades=[], errors=[], discovery=[])

    def market(self):
        return self.con.execute('SELECT * FROM markets WHERE condition="c"').fetchone()

    @patch('market_archive.archive.get_json')
    def test_checkpoint_resume_and_identical_rows(self, get):
        get.side_effect = [page('a'), page('b')]
        collect_trades(self.con, self.archive, self.market(), self.report)
        self.assertEqual(self.market()['cursor'], 'b')
        self.assertEqual(self.report['trades'][0]['identical_rows'], 1)
        get.side_effect = [page()]
        collect_trades(self.con, self.archive, self.market(), self.report)
        self.assertEqual(get.call_args.args[2]['cursor'], 'b')
        self.assertEqual(self.market()['exhausted'], 1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM rows').fetchone()[0], 1)
        self.assertFalse(self.report['errors'])

    @patch('market_archive.archive.get_json', return_value=page(size=0))
    def test_bad_page_preserves_raw_not_checkpoint(self, get):
        collect_trades(self.con, self.archive, self.market(), self.report)
        self.assertEqual(len(self.archive.files), 1)
        self.assertIsNone(self.market()['cursor'])
        self.assertEqual(self.market()['exhausted'], 0)
        self.assertTrue(self.report['errors'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM rows').fetchone()[0], 0)

    @patch('market_archive.archive.get_json', side_effect=[page('a'), page('a')])
    def test_repeated_cursor_is_error(self, get):
        collect_trades(self.con, self.archive, self.market(), self.report)
        self.assertEqual(self.market()['cursor'], 'a')
        self.assertEqual(len(self.report['errors']), 1)

    @patch('market_archive.archive.get_json')
    def test_archive_budget_blocks_network(self, get):
        self.archive.initial_bytes = 80_000_000
        with self.assertRaises(RuntimeError):
            self.archive.fetch('base', '/path', {})
        get.assert_not_called()


    def test_fingerprints_are_order_independent_not_transaction_only(self):
        self.assertEqual(fingerprint({'a': 1, 'b': 2}), fingerprint({'b': 2, 'a': 1}))
        self.assertNotEqual(fingerprint(page()['data'][0]), fingerprint(page(size=3)['data'][0]))

    @patch('market_archive.archive.time.sleep')
    @patch('market_archive.archive.get_json')
    def test_retry_after_then_success(self, get, sleep):
        get.side_effect = [urllib.error.HTTPError('url', 429, 'slow', {'Retry-After': '7'}, None), page()]
        self.archive.fetch('base', '/path', {})
        sleep.assert_called_once_with(7)
        self.assertEqual(len(self.archive.retries), 1)
        self.assertEqual(len(self.archive.files), 1)

    @patch('market_archive.archive.time.sleep')
    @patch('market_archive.archive.get_json')
    def test_retry_limit_and_permanent_errors(self, get, sleep):
        get.side_effect = TimeoutError('timeout')
        with self.assertRaises(TimeoutError):
            self.archive.fetch('base', '/path', {})
        self.assertEqual(get.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])
        get.reset_mock()
        get.side_effect = urllib.error.HTTPError('url', 404, 'missing', {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            self.archive.fetch('base', '/path', {})
        self.assertEqual(get.call_count, 1)

    @patch('market_archive.archive.time.sleep')
    @patch('market_archive.archive.get_json')
    def test_long_retry_after_defers_without_sleep(self, get, sleep):
        get.side_effect = urllib.error.HTTPError('url', 429, 'slow', {'Retry-After': '120'}, None)
        with self.assertRaises(RuntimeError):
            self.archive.fetch('base', '/path', {})
        sleep.assert_not_called()

    def test_size_age_and_depth_are_independent(self):
        book = dict(timestamp=1_000_000, min_order_size='5', asks=[dict(price='.9', size='100')])
        d = quote_diagnostics(book, 1000)
        self.assertTrue(d['fresh'])
        self.assertFalse(d['stake_meets_minimum'])
        self.assertEqual(d['minimum_notional'], 4.5)
        self.assertTrue(d['top_depth_covers_stake'])
        self.assertTrue(d['top_depth_covers_minimum'])
        book['asks'][0]['size'] = '1'
        d = quote_diagnostics(book, 1200)
        self.assertFalse(d['fresh'])
        self.assertFalse(d['top_depth_covers_stake'])
        self.assertFalse(d['top_depth_covers_minimum'])
        self.assertIn('size_diagnostic_error', quote_diagnostics(dict(timestamp='nan', asks=[]), 1000))


if __name__ == '__main__':
    unittest.main()

