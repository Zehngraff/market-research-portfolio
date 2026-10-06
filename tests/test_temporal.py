"""Adversarial point-in-time, SQL parity and chronological split tests."""

from dataclasses import replace
from decimal import Decimal as D
import random
import unittest

from research_demo.demo import feature_fixtures, split_fixtures
from research_demo.temporal import (
    Decision, Observation, Sample, asof_python, asof_sqlite, chronological_split,
)


class AsOfTests(unittest.TestCase):
    def setUp(self):
        self.observations, self.decisions, self.features = feature_fixtures()

    def selected(self, at, token="TOKEN_A", rows=None):
        return asof_python(self.observations if rows is None else rows,
                           [Decision("decision", token, at)], ["toy_count"])["decision"]["toy_count"]

    def test_same_tick_availability_excluded(self):
        self.assertIsNone(self.selected(12))
        self.assertEqual(self.selected(13)["version_id"], "a-old-r1")

    def test_revision_not_known_at_its_availability_tick(self):
        self.assertEqual(self.selected(30)["version_id"], "a-old-r1")
        self.assertEqual(self.selected(31)["version_id"], "a-old-r2")

    def test_newer_observation_beats_late_old_revision(self):
        self.assertEqual(self.selected(41)["version_id"], "a-new-r1")

    def test_future_observation_excluded(self):
        self.assertEqual(self.selected(80)["version_id"], "a-new-r1")
        self.assertEqual(self.selected(81)["version_id"], "a-future")

    def test_missing_token_and_feature_are_null(self):
        self.assertIsNone(self.selected(41, "TOKEN_C"))
        result = asof_python(self.observations, self.decisions, self.features)
        self.assertTrue(all(row["missing_feature"] is None for row in result.values()))

    def test_token_isolation(self):
        self.assertEqual(self.selected(41, "TOKEN_B")["value"], "99")
        self.assertNotEqual(self.selected(41)["value"], "99")

    def test_tied_availability_highest_revision_wins(self):
        rows = [Observation("v1", "TOKEN_A", "toy_count", 5, 10, 1, D("1")),
                Observation("v2", "TOKEN_A", "toy_count", 5, 10, 2, D("2"))]
        self.assertEqual(self.selected(11, rows=rows)["version_id"], "v2")
        self.assertIsNone(self.selected(10, rows=rows))

    def test_duplicate_version_id_raises(self):
        for engine in (asof_python, asof_sqlite):
            with self.subTest(engine=engine.__name__), self.assertRaisesRegex(ValueError, "duplicate version_id"):
                engine([self.observations[0]] * 2, self.decisions, self.features)

    def test_duplicate_revision_raises(self):
        row = replace(self.observations[0], version_id="new-id")
        with self.assertRaisesRegex(ValueError, "duplicate observation revision"):
            self.selected(50, rows=[self.observations[0], row])

    def test_backwards_revision_availability_raises(self):
        row = replace(self.observations[1], available_at=11)
        with self.assertRaisesRegex(ValueError, "must not decrease"):
            self.selected(50, rows=[self.observations[0], row])

    def test_duplicate_decision_and_feature_raises(self):
        with self.assertRaisesRegex(ValueError, "duplicate decision_id"):
            asof_python([], [self.decisions[0]] * 2, self.features)
        with self.assertRaisesRegex(ValueError, "duplicate requested feature"):
            asof_python([], self.decisions, ["x", "x"])

    def test_bad_observation_data_raises(self):
        row = self.observations[0]
        for changed in ({"available_at": 9}, {"observed_at": -1}, {"revision": 0},
                        {"revision": True}, {"value": D("NaN")}, {"value": 2.0},
                        {"token": " "}, {"observed_at": True}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                replace(row, **changed)

    def test_bad_decision_data_raises(self):
        for at in (-1, True, 1.5, 2**63):
            with self.subTest(at=at), self.assertRaises(ValueError):
                Decision("id", "A", at)

    def test_sqlite_integer_domain_boundary(self):
        maximum = 2**63 - 1
        rows = [Observation("last", "A", "f", maximum - 1, maximum - 1, maximum, D("1"))]
        decisions = [Decision("last", "A", maximum)]
        self.assertEqual(asof_python(rows, decisions, ["f"]), asof_sqlite(rows, decisions, ["f"]))
        with self.assertRaises(ValueError):
            replace(rows[0], revision=maximum + 1)

    def test_sql_fixture_parity(self):
        self.assertEqual(asof_python(self.observations, self.decisions, self.features),
                         asof_sqlite(self.observations, self.decisions, self.features))

    def test_sql_empty_inputs_parity(self):
        for rows, decisions, features in (([], [], []), ([], self.decisions, []),
                                           ([], self.decisions, ["x"]), (self.observations, [], self.features)):
            with self.subTest(rows=len(rows), decisions=len(decisions), features=len(features)):
                self.assertEqual(asof_python(rows, decisions, features), asof_sqlite(rows, decisions, features))

    def test_sql_seeded_randomized_parity_and_input_permutation(self):
        rng = random.Random(731)
        rows = []
        for token in ("A", "B"):
            for feature in ("f", "g"):
                for observed_at in (0, 5, 13, 24):
                    available = observed_at
                    for revision in (1, 2, 3):
                        available += rng.randint(0, 9)
                        rows.append(Observation(f"{token}-{feature}-{observed_at}-{revision}", token,
                                                feature, observed_at, available, revision,
                                                D(str(rng.randint(-100, 100))) / D("10")))
        decisions = [Decision(f"d{at}-{token}", token, at) for at in range(60) for token in ("A", "B", "C")]
        expected = asof_python(rows, decisions, ["f", "g", "missing"])
        rng.shuffle(rows)
        rng.shuffle(decisions)
        self.assertEqual(expected, asof_python(rows, decisions, ["missing", "g", "f"]))
        self.assertEqual(expected, asof_sqlite(rows, decisions, ["missing", "g", "f"]))
        for decision in decisions:
            for record in expected[decision.decision_id].values():
                if record is not None:
                    self.assertLess(record["available_at"], decision.at)
                    self.assertLess(record["observed_at"], decision.at)


class SplitTests(unittest.TestCase):
    def setUp(self):
        self.samples = split_fixtures()
        self.result = chronological_split(self.samples, 100)

    def test_parent_event_purged_even_without_window_overlap(self):
        self.assertEqual(self.result["purged"]["purge_shared_event"], ["shared_parent_event"])

    def test_label_window_crossing_purged(self):
        self.assertIn("label_window_overlap", self.result["purged"]["purge_label_overlap"])

    def test_holding_window_crossing_purged(self):
        self.assertEqual(self.result["purged"]["purge_holding_overlap"], ["holding_window_overlap"])

    def test_end_at_boundary_is_not_overlap(self):
        self.assertIn("train_holding_ends_at_boundary", self.result["train"])

    def test_same_tick_label_availability_purged(self):
        self.assertEqual(self.result["purged"]["purge_label_same_tick"], ["label_unavailable_at_boundary"])

    def test_boundary_decision_is_test(self):
        self.assertIn("test_at_boundary", self.result["test"])

    def test_train_test_invariants(self):
        by_id = {row.sample_id: row for row in self.samples}
        train = [by_id[key] for key in self.result["train"]]
        test = [by_id[key] for key in self.result["test"]]
        self.assertTrue({row.parent_event_id for row in train}.isdisjoint(row.parent_event_id for row in test))
        for row in train:
            self.assertLess(row.decision_at, 100)
            self.assertLess(row.label_available_at, 100)
            self.assertLessEqual(row.holding_end, 100)
            self.assertLessEqual(row.label_end, 100)
        for row in test:
            self.assertGreaterEqual(row.decision_at, 100)

    def test_split_is_input_order_independent(self):
        self.assertEqual(self.result, chronological_split(reversed(self.samples), 100))

    def test_bad_samples_and_duplicate_ids_raise(self):
        for change in ({"label_end": 9}, {"holding_end": 9}, {"label_available_at": 29},
                       {"parent_event_id": ""}, {"decision_at": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.samples[0], **change)
        with self.assertRaisesRegex(ValueError, "duplicate sample_id"):
            chronological_split([self.samples[0]] * 2, 100)

    def test_empty_split_and_invalid_boundary(self):
        self.assertEqual(chronological_split([], 100), {"boundary": 100, "train": [], "test": [], "purged": {}})
        with self.assertRaises(ValueError):
            chronological_split([], -1)


if __name__ == "__main__":
    unittest.main()
