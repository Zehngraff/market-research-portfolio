import tempfile
import unittest
import time
from unittest.mock import Mock
from market_archive.kalshi import database, page, store_object, mapping, hydrate


class KalshiCollectionTests(unittest.TestCase):
    def test_hydrate_joins_observed_ticker(self):
        self.con.execute('INSERT INTO trades VALUES(?,?,?,?)',('T','id','now','raw.gz'))
        self.con.commit()
        self.archive.started = time.time()
        self.archive.fetch.side_effect = [
            ({'market':{'ticker':'T','event_ticker':'E','rules_primary':'rule'}},0,'market.gz'),
            ({'event':{'event_ticker':'E','category':'Crypto'}},0,'event.gz')]
        report = {}
        hydrate(self.con,self.archive,report,limit=1)
        self.assertEqual(report['mapping']['unmapped_trades'],0)
        self.assertEqual(report['mapping']['category_trade_counts'],{'Crypto':1})

    def test_mapping_requires_event_category(self):
        self.con.execute('INSERT INTO trades VALUES(?,?,?,?)',('T','id','now','raw.gz'))
        store_object(self.con,'markets','T',{'ticker':'T','event_ticker':'E'},'raw.gz')
        self.assertEqual(mapping(self.con)[0], {'UNKNOWN':1})
        store_object(self.con,'events','E',{'event_ticker':'E','category':'Politics'},'raw.gz')
        self.assertEqual(mapping(self.con)[0], {'Politics':1})

    def test_identity_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            store_object(self.con,'markets','T',{'ticker':'OTHER'},'raw.gz')

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.con = database(tmp.name)
        self.addCleanup(self.con.close)
        self.archive = Mock()
        self.row = dict(ticker='T', trade_id='id', created_time='2026-09-23T00:00:00Z',
                        count_fp='1', yes_price_dollars='.4', no_price_dollars='.6')

    def response(self, cursor):
        self.archive.fetch.return_value = ({'trades': [self.row], 'cursor': cursor}, 0, 'raw.gz')

    def test_resume_and_deduplicate(self):
        self.response('next')
        page(self.con, self.archive, '/markets/trades', 'trades')
        self.response('')
        result = page(self.con, self.archive, '/markets/trades', 'trades')
        self.assertEqual(self.archive.fetch.call_args.args[2]['cursor'], 'next')
        self.assertEqual(result['inserted_trades'], 0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM trades').fetchone()[0], 1)

    def test_invalid_page_does_not_advance(self):
        self.response('a')
        page(self.con, self.archive, '/markets/trades', 'trades')
        self.row['count_fp'] = 'NaN'
        self.response('b')
        with self.assertRaises(ValueError):
            page(self.con, self.archive, '/markets/trades', 'trades')
        self.assertEqual(self.con.execute('SELECT cursor FROM checkpoints').fetchone()[0], 'a')

    def test_head_preserves_backfill(self):
        self.response('a')
        page(self.con, self.archive, '/markets/trades', 'trades')
        self.response('b')
        page(self.con, self.archive, '/markets/trades', 'trades', head=True)
        self.assertNotIn('cursor', self.archive.fetch.call_args.args[2])
        self.assertEqual(self.con.execute('SELECT cursor FROM checkpoints').fetchone()[0], 'a')

    def test_repeated_cursor_rejected(self):
        self.response('a')
        page(self.con, self.archive, '/markets/trades', 'trades')
        with self.assertRaises(ValueError):
            page(self.con, self.archive, '/markets/trades', 'trades')

