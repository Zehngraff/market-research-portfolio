import unittest

from physical_data.jsonstat import jsonstat_rows


def fixture():
    return {"id": ["geo", "time"], "size": [2, 2],
        "dimension": {"geo": {"category": {"index": {"B": 1, "A": 0}}}, "time": {"category": {"index": ["2022", "2023"]}}},
        "value": {"0": 0, "1": 2, "3": 4}, "status": {"1": "p"}}


class JsonStatTests(unittest.TestCase):
    def test_flat_dimension_order_and_zero(self):
        rows = jsonstat_rows(fixture())
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {"geo": "A", "time": "2022", "value": 0, "status": None, "is_missing": False})
        self.assertEqual(rows[-1]["geo"], "B")
        self.assertEqual(rows[-1]["time"], "2023")
        self.assertEqual(rows[1]["status"], "p")

    def test_missing_is_explicit_and_dense_supported(self):
        raw = fixture(); raw["value"] = [0, 2, None, 4]
        rows = jsonstat_rows(raw, include_missing=True)
        self.assertEqual(len(rows), 4)
        self.assertIsNone(rows[2]["value"])
        self.assertTrue(rows[2]["is_missing"])

    def test_invalid_dimension_or_value(self):
        for mutate in [lambda x: x.update(size=[2, 3]),
                       lambda x: x["value"].update({"4": 5}),
                       lambda x: x["value"].update({"0": float("nan")}),
                       lambda x: x["dimension"]["geo"]["category"].update(index={"A": 0, "B": 0})]:
            raw = fixture(); mutate(raw)
            with self.assertRaises(ValueError):
                jsonstat_rows(raw)

    def test_size_limit_and_empty_values(self):
        with self.assertRaisesRegex(ValueError, "cell limit"):
            jsonstat_rows(fixture(), max_cells=3)
        raw = fixture(); raw["value"] = {}
        self.assertEqual(jsonstat_rows(raw), [])
        self.assertEqual(len(jsonstat_rows(raw, include_missing=True)), 4)

    def test_scalar_status_applies_to_every_cell_including_missing(self):
        raw = fixture(); raw["status"] = "e"
        rows = jsonstat_rows(raw, include_missing=True)
        self.assertEqual([row["status"] for row in rows], ["e"] * 4)
        self.assertEqual([row["value"] for row in rows], [0, 2, None, 4])
        self.assertTrue(rows[2]["is_missing"])
        self.assertEqual([row["status"] for row in jsonstat_rows(raw)], ["e"] * 3)
        self.assertEqual(raw["status"], "e")  # The input document is not mutated.

    def test_dense_statuses_follow_cell_positions(self):
        raw = fixture(); raw["status"] = ["a", "p", "m", "e"]
        rows = jsonstat_rows(raw, include_missing=True)
        self.assertEqual([row["status"] for row in rows], ["a", "p", "m", "e"])
        self.assertEqual([row["status"] for row in jsonstat_rows(raw)], ["a", "p", "e"])

    def test_sparse_statuses_preserve_unassigned_and_missing_cells(self):
        raw = fixture(); raw["status"] = {"1": "p", "2": "m"}
        rows = jsonstat_rows(raw, include_missing=True)
        self.assertEqual([row["status"] for row in rows], [None, "p", "m", None])

    def test_absent_status_returns_none_for_all_cells(self):
        raw = fixture(); raw.pop("status")
        rows = jsonstat_rows(raw, include_missing=True)
        self.assertEqual([row["status"] for row in rows], [None] * 4)

    def test_non_string_scalar_status_is_rejected(self):
        for status in [1, 1.5, True, None]:
            with self.subTest(status=status), self.assertRaises(ValueError):
                raw = fixture(); raw["status"] = status
                jsonstat_rows(raw)

    def test_scalar_status_does_not_relax_value_validation(self):
        for value in ["e", 1, True, None, [0, 2, None],
                      {"0": "1"}, {"0": True}, {"0": float("inf")},
                      {"0": float("nan")}, {"4": 1}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                raw = fixture(); raw.update(status="e", value=value)
                jsonstat_rows(raw)
