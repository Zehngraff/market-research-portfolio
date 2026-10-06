import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from physical_data.cli import demo


class DemoTests(unittest.TestCase):
    def test_offline_demo_and_byte_reproducibility(self):
        def hashes(root):
            return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in Path(root).rglob("*") if p.is_file()}
        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Network forbidden in offline demo")):
                report = demo(first)
                demo(second)
            self.assertEqual(hashes(first), hashes(second))
            self.assertEqual(report["satellite_observation_rows"], 14)
            self.assertEqual(report["cutoff_daily_rows"], 8)
            self.assertEqual(report["satellite_feature_rows"], 2)
            self.assertEqual(report["weather"]["valid_precipitation_days"], 10)
            self.assertIsNone(report["weather"]["precipitation_total_mm"])
            self.assertTrue(report["offline_cache_replay_equal"])
            self.assertEqual(report["catalogue_items"], 3)

    def test_same_output_repeat_is_stable(self):
        with TemporaryDirectory() as folder:
            first = demo(folder)
            second = demo(folder)
            self.assertEqual(first, second)
