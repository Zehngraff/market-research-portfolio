"""Quote rejection, exact-fee, finite-cash and settlement lifecycle tests."""

from dataclasses import replace
from decimal import Decimal as D
import unittest

from research_demo.execution import Ledger, OrderAttempt, Quote, Settlement, quote_reasons


def order(order_id="o1", at=10, quantity=10, token="A", event="EA"):
    return OrderAttempt(order_id, event, token, at, quantity)


def quote(quote_id="q1", at=9, **changes):
    return replace(Quote(quote_id, "A", at, at, D("0.39"), D("0.40"), 100, 100), **changes)


def settlement(settlement_id="s1", **changes):
    return replace(Settlement(settlement_id, "EA", "A", 20, 30, True, 25, D("1")), **changes)


class QuoteTests(unittest.TestCase):
    def test_valid_book(self):
        self.assertEqual(quote_reasons(quote(), order(), 5), [])

    def test_future_quote(self):
        reasons = quote_reasons(quote(at=11), order(), 5)
        self.assertIn("future_quote", reasons)
        self.assertIn("quote_not_yet_received", reasons)

    def test_timestamp_after_receipt(self):
        self.assertIn("inconsistent_quote_times", quote_reasons(quote(book_at=9, received_at=8), order(), 5))

    def test_stale_boundary_included(self):
        self.assertEqual(quote_reasons(quote(at=5), order(), 5), [])
        self.assertIn("stale_quote", quote_reasons(quote(at=4), order(), 5))

    def test_receipt_same_tick_and_later_rejected(self):
        for received in (10, 11):
            with self.subTest(received=received):
                self.assertIn("quote_not_yet_received", quote_reasons(quote(received_at=received), order(), 5))

    def test_receipt_age_does_not_hide_old_book(self):
        self.assertIn("stale_quote", quote_reasons(quote(book_at=1, received_at=9), order(), 5))

    def test_token_mismatch(self):
        self.assertIn("token_mismatch", quote_reasons(quote(token="B"), order(), 5))

    def test_crossed_book_and_locked_book(self):
        self.assertIn("crossed_book", quote_reasons(quote(bid=D("0.41")), order(), 5))
        self.assertEqual(quote_reasons(quote(bid=D("0.40")), order(), 5), [])

    def test_bad_numeric_values_fail_closed(self):
        cases = [
            {"bid": D("NaN")}, {"ask": D("Infinity")}, {"ask": D("-0.01")},
            {"bid": D("-0.01")}, {"ask": D("0")}, {"bid": D("1.01")},
            {"ask": D("1.01")}, {"ask": D("0.401")}, {"ask": 0.4},
            {"ask_depth": -1}, {"ask_depth": True}, {"bid_depth": 0.5},
            {"book_at": -1}, {"received_at": True}, {"quote_id": ""},
        ]
        for changed in cases:
            with self.subTest(changed=changed):
                self.assertIn("bad_quote_values", quote_reasons(quote(**changed), order(), 5))

    def test_zero_displayed_depth_is_valid_book_but_not_fillable(self):
        self.assertEqual(quote_reasons(quote(ask_depth=0), order(), 5), [])
        result = Ledger(D("10"), D("100"), 5).attempt(order(), quote(ask_depth=0))
        self.assertEqual(result["reasons"], ["insufficient_displayed_depth"])


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(D("10.00"), D("100"), 5)

    def fill(self):
        return self.ledger.attempt(order(), quote())

    def test_accepted_fill_locks_principal_and_pays_fee(self):
        result = self.fill()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["principal_locked"], "4.00")
        self.assertEqual(result["fee_paid"], "0.04")
        self.assertEqual(result["state"]["available_cash"], "5.96")
        self.assertEqual(result["state"]["locked_principal"], "4.00")
        self.assertEqual(result["state"]["fees_paid"], "0.04")

    def test_fee_rounds_up_each_fill(self):
        result = self.ledger.attempt(order(quantity=1), quote(ask=D("0.41")))
        self.assertEqual(result["fee_paid"], "0.01")
        self.assertEqual(result["state"]["available_cash"], "9.58")

    def test_fee_included_in_affordability(self):
        ledger = Ledger(D("4.00"), D("100"), 5)
        result = ledger.attempt(order(), quote())
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reasons"], ["insufficient_available_cash"])
        self.assertEqual(ledger.snapshot()["available_cash"], "4.00")
        self.assertEqual(ledger.snapshot()["fees_paid"], "0.00")

    def test_exact_total_cash_boundary_accepted(self):
        ledger = Ledger(D("4.04"), D("100"), 5)
        self.assertTrue(ledger.attempt(order(), quote())["accepted"])
        self.assertEqual(ledger.snapshot()["available_cash"], "0.00")

    def test_zero_cash_and_zero_fee(self):
        self.assertFalse(Ledger(D("0"), D("0"), 5).attempt(order(), quote())["accepted"])
        result = Ledger(D("4"), D("0"), 5).attempt(order(), quote())
        self.assertTrue(result["accepted"])
        self.assertEqual(result["fee_paid"], "0.00")

    def test_locked_cash_cannot_be_reused(self):
        self.fill()
        result = self.ledger.attempt(order("o2", at=11, quantity=15), quote("q2", at=10))
        self.assertEqual(result["reasons"], ["insufficient_available_cash"])
        self.assertEqual(self.ledger.snapshot()["locked_principal"], "4.00")

    def test_exact_depth_and_repeated_snapshot_consumption(self):
        shallow = quote(ask_depth=10)
        self.assertTrue(self.ledger.attempt(order(), shallow)["accepted"])
        result = self.ledger.attempt(order("o2", at=11, quantity=1), shallow)
        self.assertEqual(result["reasons"], ["insufficient_displayed_depth"])

    def test_partial_depth_fill_not_assumed(self):
        result = self.ledger.attempt(order(), quote(ask_depth=9))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["principal_locked"], "0.00")
        self.assertEqual(result["state"]["open_order_ids"], [])

    def test_rejection_does_not_consume_depth(self):
        thin = quote(ask_depth=1)
        self.assertFalse(self.ledger.attempt(order(), thin)["accepted"])
        self.assertTrue(self.ledger.attempt(order("o2", at=11, quantity=1), thin)["accepted"])

    def test_new_quote_is_new_snapshot(self):
        self.assertTrue(self.ledger.attempt(order(), quote(ask_depth=10))["accepted"])
        self.assertTrue(self.ledger.attempt(order("o2", at=11, quantity=1), quote("q2", at=10, ask_depth=1))["accepted"])

    def test_conflicting_quote_id_raises_without_mutation(self):
        self.fill()
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(ValueError, "quote_id reused"):
            self.ledger.attempt(order("o2", at=11), quote(ask_depth=200))
        self.assertEqual(self.ledger.snapshot(), before)

    def test_duplicate_accepted_or_rejected_order_ids_raise(self):
        for accepted in (True, False):
            ledger = Ledger(D("10"), D("100"), 5)
            ledger.attempt(order(), quote(ask_depth=100 if accepted else 0))
            with self.subTest(accepted=accepted), self.assertRaisesRegex(ValueError, "duplicate order_id"):
                ledger.attempt(order(), quote())

    def test_backward_clock_raises(self):
        self.fill()
        for action in (lambda: self.ledger.attempt(order("o2", at=9), quote(at=8)),
                       lambda: self.ledger.apply_settlements([], 9)):
            with self.assertRaisesRegex(ValueError, "backwards"):
                action()

    def test_same_time_orders_do_not_expose_same_time_quotes(self):
        self.assertTrue(self.ledger.attempt(order(quantity=1), quote())["accepted"])
        self.assertTrue(self.ledger.attempt(order("o2", quantity=1), quote())["accepted"])
        result = self.ledger.attempt(order("o3", quantity=1), quote("q3", at=10))
        self.assertIn("quote_not_yet_received", result["reasons"])

    def test_token_parent_identity_is_consistent(self):
        self.fill()
        with self.assertRaisesRegex(ValueError, "parent event"):
            self.ledger.attempt(order("o2", at=11, event="OTHER"), quote("q2", at=10))

    def test_bad_account_and_order_values_raise(self):
        for cash, fee, age in ((D("-1"), D("0"), 5), (D("NaN"), D("0"), 5),
                               (D("1.001"), D("0"), 5), (D("10"), D("-1"), 5),
                               (D("10"), D("10001"), 5), (D("10"), D("1"), -1)):
            with self.subTest(cash=cash, fee=fee, age=age), self.assertRaises(ValueError):
                Ledger(cash, fee, age)
        for quantity in (0, -1, True, 1.5):
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                order(quantity=quantity)


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger(D("10.00"), D("100"), 5)
        self.ledger.attempt(order(), quote())

    def test_effective_time_alone_does_not_release_cash(self):
        result = self.ledger.apply_settlements([settlement()], 21)
        self.assertEqual(result["settled_order_ids"], [])
        self.assertEqual(result["state"]["locked_principal"], "4.00")
        self.assertEqual(result["state"]["available_cash"], "5.96")

    def test_unverified_record_never_releases_cash(self):
        unverified = settlement(verified=False, verified_at=None)
        result = self.ledger.apply_settlements([unverified], 31)
        self.assertEqual(result["record_statuses"], {"s1": "unverified"})
        self.assertEqual(result["state"]["locked_principal"], "4.00")

    def test_same_tick_availability_excluded_then_release(self):
        at = self.ledger.apply_settlements([settlement()], 30)
        self.assertEqual(at["record_statuses"], {"s1": "not_yet_available"})
        self.assertEqual(at["cash_credited"], "0.00")
        after = self.ledger.apply_settlements([settlement()], 31)
        self.assertEqual(after["cash_credited"], "10.00")
        self.assertEqual(after["principal_released"], "4.00")
        self.assertEqual(after["state"]["locked_principal"], "0.00")
        self.assertEqual(after["state"]["available_cash"], "15.96")

    def test_same_tick_verified_at_is_excluded(self):
        record = settlement(verified_at=30)
        self.assertEqual(self.ledger.apply_settlements([record], 30)["cash_credited"], "0.00")

    def test_settlement_is_idempotent(self):
        self.ledger.apply_settlements([settlement()], 31)
        again = self.ledger.apply_settlements([settlement()], 31)
        self.assertEqual(again["cash_credited"], "0.00")
        self.assertEqual(again["principal_released"], "0.00")
        self.assertEqual(again["state"]["available_cash"], "15.96")

    def test_zero_payout_removes_lock_without_cash_credit(self):
        result = self.ledger.apply_settlements([settlement(payout_per_unit=D("0"))], 31)
        self.assertEqual(result["principal_released"], "4.00")
        self.assertEqual(result["cash_credited"], "0.00")
        self.assertEqual(result["state"]["available_cash"], "5.96")

    def test_cash_can_be_reused_only_after_available_verified_settlement(self):
        expensive = quote("expensive", at=29, ask=D("0.60"))
        self.ledger.apply_settlements([settlement()], 30)
        self.assertFalse(self.ledger.attempt(order("too_early", at=30, token="B", event="EB"),
                                            replace(expensive, token="B"))["accepted"])
        self.ledger.apply_settlements([settlement()], 31)
        self.assertTrue(self.ledger.attempt(order("after", at=31, token="B", event="EB"),
                                           replace(expensive, token="B"))["accepted"])

    def test_known_settled_contract_cannot_be_bought(self):
        self.ledger.apply_settlements([settlement()], 31)
        result = self.ledger.attempt(order("o2", at=32), quote("q2", at=31))
        self.assertEqual(result["reasons"], ["contract_already_settled"])

    def test_settlement_without_position_still_closes_contract(self):
        record = settlement(token="B", parent_event_id="EB")
        self.ledger.apply_settlements([record], 31)
        result = self.ledger.attempt(order("b", at=32, token="B", event="EB"), quote("b", at=31, token="B"))
        self.assertIn("contract_already_settled", result["reasons"])
        self.assertEqual(self.ledger.snapshot()["locked_principal"], "4.00")

    def test_token_settlement_does_not_settle_other_event_token(self):
        self.ledger.attempt(order("b", at=11, quantity=1, token="B", event="EA"), quote("b", at=10, token="B"))
        result = self.ledger.apply_settlements([settlement()], 31)
        self.assertEqual(result["settled_order_ids"], ["o1"])
        self.assertEqual(result["state"]["open_order_ids"], ["b"])

    def test_multiple_positions_settle_once(self):
        self.ledger.attempt(order("o2", at=11, quantity=2), quote("q2", at=10))
        result = self.ledger.apply_settlements([settlement()], 31)
        self.assertEqual(result["settled_order_ids"], ["o1", "o2"])
        self.assertEqual(result["cash_credited"], "12.00")
        self.assertEqual(result["principal_released"], "4.80")

    def test_duplicate_settlement_ids_in_batch_raise(self):
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(ValueError, "duplicate settlement_id"):
            self.ledger.apply_settlements([settlement()] * 2, 31)
        self.assertEqual(self.ledger.snapshot(), before)

    def test_conflicting_settlement_id_raises(self):
        self.ledger.apply_settlements([settlement()], 31)
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(ValueError, "settlement_id reused"):
            self.ledger.apply_settlements([settlement(payout_per_unit=D("0"))], 32)
        self.assertEqual(self.ledger.snapshot(), before)

    def test_conflicting_terminal_batch_is_atomic(self):
        before = self.ledger.snapshot()
        with self.assertRaisesRegex(ValueError, "conflicting verified"):
            self.ledger.apply_settlements([settlement(), settlement("s2", payout_per_unit=D("0"))], 31)
        self.assertEqual(self.ledger.snapshot(), before)
        self.assertEqual(self.ledger.apply_settlements([settlement()], 31)["cash_credited"], "10.00")

    def test_conflicting_parent_event_settlement_raises(self):
        with self.assertRaisesRegex(ValueError, "parent event"):
            self.ledger.apply_settlements([settlement(parent_event_id="WRONG")], 31)

    def test_future_conflicting_outcome_is_not_used_early(self):
        future = settlement("future", available_at=50, payout_per_unit=D("0"))
        result = self.ledger.apply_settlements([settlement(), future], 31)
        self.assertEqual(result["cash_credited"], "10.00")
        self.assertEqual(result["record_statuses"]["future"], "not_yet_available")
        with self.assertRaisesRegex(ValueError, "conflicting verified"):
            self.ledger.apply_settlements([future], 51)

    def test_invalid_settlement_metadata_raises(self):
        for change in ({"available_at": 19}, {"verified_at": 19}, {"verified_at": 31},
                       {"verified_at": None}, {"verified": False}, {"verified": 1},
                       {"payout_per_unit": D("0.5")}, {"payout_per_unit": D("NaN")}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                settlement(**change)


if __name__ == "__main__":
    unittest.main()
