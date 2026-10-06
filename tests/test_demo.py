"""Offline reproducibility and main-fixture disclosure checks."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from research_demo.demo import build_demo


class DemoTests(unittest.TestCase):
    def test_repeat_calls_produce_identical_json(self):
        first = json.dumps(build_demo(), sort_keys=True, allow_nan=False)
        self.assertEqual(first, json.dumps(build_demo(), sort_keys=True, allow_nan=False))

    def test_module_cli_matches_public_builder(self):
        root = Path(__file__).resolve().parent.parent
        output = subprocess.check_output([sys.executable, "-m", "research_demo"], cwd=root, text=True)
        self.assertEqual(json.loads(output), build_demo())

    def test_example_has_acceptance_rejection_and_capital_lifecycle(self):
        data = build_demo()
        attempts = [item for item in data["execution"] if item["kind"] == "order_attempt"]
        self.assertEqual(sum(item["accepted"] for item in attempts), 2)
        self.assertEqual(sum(not item["accepted"] for item in attempts), 9)
        checkpoints = [item for item in data["execution"] if item["kind"] == "settlement_cutoff"]
        self.assertEqual([item["state"]["locked_principal"] for item in checkpoints], ["6.00", "6.00", "0.00"])
        self.assertEqual(checkpoints[-1]["cash_credited"], checkpoints[-1]["principal_released"])
        self.assertEqual(data["final_accounting_state"]["available_cash"], "7.41")
        self.assertEqual(data["final_accounting_state"]["locked_principal"], "2.50")
        self.assertEqual(data["final_accounting_state"]["fees_paid"], "0.09")

    def test_no_performance_metric_and_explicit_synthetic_notice(self):
        data = build_demo()
        self.assertIn("Synthetic", data["notice"])
        self.assertIn("no signal", data["notice"])
        self.assertTrue(data["features"]["sqlite_parity"])
        def keys(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield key.lower()
                    yield from keys(child)
            elif isinstance(value, list):
                for child in value:
                    yield from keys(child)
        self.assertTrue(set(keys(data)).isdisjoint({"pnl", "profit", "return", "sharpe", "alpha", "win_rate"}))


if __name__ == "__main__":
    unittest.main()
