import unittest
from market_archive.validation import book_quote, confirmed_outcome, validate_page


class IntegrityTests(unittest.TestCase):
    def book(self):
        return {'asset_id': 'yes', 'timestamp': '1000000', 'min_order_size': '1',
                'bids': [{'price': '.92','size': '20'}], 'asks': [{'price': '.95','size': '20'}]}

    def test_quote_checks(self):
        self.assertEqual(book_quote(self.book(), 'yes', 1001, 1)[1], .95)
        for field, value in [('timestamp', '1000'), ('asset_id', 'wrong'), ('min_order_size', '5')]:
            b = self.book(); b[field] = value
            with self.assertRaises(ValueError): book_quote(b, 'yes', 1001, 1)
        for price, size in [('.91','20'), ('.95','0'), ('NaN','20'), ('.95','.1')]:
            b = self.book(); b['asks'] = [{'price': price, 'size': size}]
            with self.assertRaises(ValueError): book_quote(b, 'yes', 1001, 1)

    def test_resolution_requires_explicit_final_state(self):
        m = {'closed': True, 'umaResolutionStatus': 'resolved',
             'clobTokenIds': '["no","yes"]', 'outcomePrices': '[0,1]'}
        self.assertEqual(confirmed_outcome(m, 'yes'), 1)
        self.assertEqual(confirmed_outcome(m, 'no'), 0)
        self.assertIsNone(confirmed_outcome(dict(m, umaResolutionStatus='proposed'), 'yes'))
        self.assertIsNone(confirmed_outcome(dict(m, outcomePrices='[0.01,0.99]'), 'yes'))
        self.assertIsNone(confirmed_outcome(m, 'unknown'))

    def test_history_rejects_missing_size_and_broken_cursor(self):
        row={'condition_id':'c','price':.95,'size':10,'side':'BUY','timestamp':100,
             'token_id':'t','transaction_hash':'h'}
        p={'data':[row],'pagination':{'has_more':False}}
        self.assertEqual(validate_page(p,'c')[1],None)
        with self.assertRaises(ValueError): validate_page(p,'wrong')
        row['size']=0
        with self.assertRaises(ValueError): validate_page(p,'c')
        with self.assertRaises(ValueError): validate_page({'data':[],'pagination':{'has_more':True}},'c')
