"""Hand-authored fixtures demonstrating controls, with no learned signal.

Numbers are arbitrary accounting examples, not calibrated market assumptions or
strategy parameters. Order quantities are explicitly written down. Feature and
split examples do not drive orders. A declared synthetic payout demonstrates the
capital lifecycle only; there is no return series or profitability calculation.
"""

from decimal import Decimal as D

from .execution import Ledger, OrderAttempt, Quote, Settlement
from .temporal import Decision, Observation, Sample, asof_python, asof_sqlite, chronological_split


def feature_fixtures():
    observations = [
        Observation("a-old-r1", "TOKEN_A", "toy_count", 10, 12, 1, D("2")),
        Observation("a-old-r2", "TOKEN_A", "toy_count", 10, 30, 2, D("3")),
        Observation("a-new-r1", "TOKEN_A", "toy_count", 20, 35, 1, D("4")),
        Observation("a-old-r3", "TOKEN_A", "toy_count", 10, 40, 3, D("5")),
        Observation("a-future", "TOKEN_A", "toy_count", 80, 80, 1, D("6")),
        Observation("b-old-r1", "TOKEN_B", "toy_count", 10, 11, 1, D("99")),
    ]
    decisions = [
        Decision("before_first_availability", "TOKEN_A", 12),
        Decision("at_revision_availability", "TOKEN_A", 30),
        Decision("after_revision_availability", "TOKEN_A", 31),
        Decision("new_observation_available", "TOKEN_A", 36),
        Decision("old_revision_does_not_replace_new_observation", "TOKEN_A", 41),
        Decision("other_token", "TOKEN_B", 41),
        Decision("unseen_token", "TOKEN_C", 41),
    ]
    return observations, decisions, ["toy_count", "missing_feature"]


def split_fixtures():
    return [
        Sample("train_safe", "EVENT_SAFE", 10, 30, 40, 31),
        Sample("purge_shared_event", "EVENT_SHARED", 20, 30, 40, 31),
        Sample("purge_label_overlap", "EVENT_LABEL", 70, 105, 90, 106),
        Sample("purge_holding_overlap", "EVENT_HOLD", 75, 85, 120, 86),
        Sample("purge_label_same_tick", "EVENT_LATE", 80, 95, 95, 100),
        Sample("train_holding_ends_at_boundary", "EVENT_BOUNDARY", 85, 90, 100, 99),
        Sample("test_at_boundary", "EVENT_TEST", 100, 120, 125, 121),
        Sample("test_shared_event", "EVENT_SHARED", 110, 125, 130, 126),
    ]


def build_demo():
    observations, decisions, features = feature_fixtures()
    selected = asof_python(observations, decisions, features)
    sql_selected = asof_sqlite(observations, decisions, features)
    if selected != sql_selected:
        raise AssertionError("Python/SQLite as-of results diverged")
    ledger = Ledger(D("10.00"), fee_bps=D("100"), max_quote_age=5)
    history = []

    def attempt(order_id, at, quote, quantity=10, token="TOKEN_A", event="EVENT_A"):
        history.append({"kind": "order_attempt", **ledger.attempt(
            OrderAttempt(order_id, event, token, at, quantity), quote
        )})

    # Paying 1.00 for a declared eventual 1.00 payout makes the main example
    # capital-neutral before fees. This is an accounting fixture, not a trade idea.
    good = Quote("good", "TOKEN_A", 49, 49, D("0.99"), D("1.00"), 20, 6)
    attempt("accepted_initial", 50, good, quantity=6)
    attempt("rejected_consumed_depth", 51, good, quantity=1)
    attempt("rejected_stale", 52, Quote("stale", "TOKEN_A", 40, 41, D("0.39"), D("0.40"), 20, 20))
    attempt("rejected_future", 53, Quote("future", "TOKEN_A", 54, 54, D("0.39"), D("0.40"), 20, 20))
    attempt("rejected_same_tick_receipt", 54, Quote("late", "TOKEN_A", 53, 54, D("0.39"), D("0.40"), 20, 20))
    attempt("rejected_token_mismatch", 55, Quote("mismatch", "TOKEN_B", 54, 54, D("0.39"), D("0.40"), 20, 20))
    attempt("rejected_crossed", 56, Quote("crossed", "TOKEN_A", 55, 55, D("0.50"), D("0.40"), 20, 20))
    attempt("rejected_bad_values", 57, Quote("bad", "TOKEN_A", 56, 56, D("NaN"), D("0.40"), 20, 20))
    attempt("rejected_displayed_depth", 58, Quote("shallow", "TOKEN_A", 57, 57, D("0.39"), D("0.40"), 20, 2))
    attempt("rejected_locked_capital", 59, Quote("costly", "TOKEN_A", 58, 58, D("0.59"), D("0.60"), 20, 20))
    pending = Settlement("unverified", "EVENT_A", "TOKEN_A", 70, 71, False, None, D("1"))
    final = Settlement("verified", "EVENT_A", "TOKEN_A", 70, 80, True, 79, D("1"))
    for cutoff in (72, 80, 81):
        history.append({"kind": "settlement_cutoff", **ledger.apply_settlements([pending, final], cutoff)})
    attempt("accepted_after_verified_settlement", 82,
            Quote("new-token", "TOKEN_B", 81, 81, D("0.49"), D("0.50"), 20, 20),
            quantity=5, token="TOKEN_B", event="EVENT_B")
    return {
        "schema_version": 1,
        "notice": "Synthetic control demonstration only. Hand-specified orders; no signal, profitability metric, or performance claim.",
        "time_policy": "Integer synthetic ticks; external information requires available_at < decision/cutoff. Windows are half-open.",
        "features": {"selected": selected, "sqlite_parity": selected == sql_selected},
        "split": chronological_split(split_fixtures(), boundary=100),
        "execution_assumptions": {
            "initial_cash": "10.00", "fee_bps": "100", "fee_rounding": "up to cents per fill",
            "max_quote_age_ticks": 5, "fill_rule": "all-or-none at ask within remaining displayed snapshot depth",
        },
        "execution": history,
        "final_accounting_state": ledger.snapshot(),
    }
