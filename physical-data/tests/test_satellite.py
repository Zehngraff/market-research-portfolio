import unittest

import numpy as np
import pandas as pd

from physical_data.satellite import band_metadata, cutoff_features, observe_arrays


def meta(offset=0):
    return {band: {"scale": 0.0001, "offset": offset, "nodata": 0} for band in ["red", "nir", "swir16"]}


def observe(**kwargs):
    values = dict(red=[[2000]], nir=[[6000]], swir=[[4000]], scl=[[4]], metadata=meta())
    values.update(kwargs)
    return observe_arrays(**values)


def observations():
    return pd.DataFrame([
        {"field_id": "synthetic-a", "item_id": "a", "datetime": "2024-04-01T10:00Z", "available_at": "2024-04-01T14:00Z", "ndvi_mean": .2, "ndmi_mean": .1, "valid_pixels": 2, "ndmi_valid_pixels": 1, "geometry_pixels": 4, "clear_fraction": .5},
        {"field_id": "synthetic-a", "item_id": "b", "datetime": "2024-04-01T11:00Z", "available_at": "2024-04-01T14:00Z", "ndvi_mean": .6, "ndmi_mean": .5, "valid_pixels": 6, "ndmi_valid_pixels": 3, "geometry_pixels": 8, "clear_fraction": .75},
        {"field_id": "synthetic-a", "item_id": "c", "datetime": "2024-04-03T10:00Z", "available_at": "2024-04-03T14:00Z", "ndvi_mean": .7, "ndmi_mean": .3, "valid_pixels": 4, "ndmi_valid_pixels": 4, "geometry_pixels": 4, "clear_fraction": 1},
    ])


class SatelliteTests(unittest.TestCase):
    def test_source_index_formulas(self):
        result = observe()
        self.assertAlmostEqual(result["ndvi_mean"], .5)
        self.assertAlmostEqual(result["ndmi_mean"], .2)
        self.assertEqual(result["valid_pixels"], 1)

    def test_offsets_applied_before_ratios(self):
        result = observe(metadata=meta(-.1))
        self.assertAlmostEqual(result["ndvi_mean"], 2 / 3)
        self.assertAlmostEqual(result["ndmi_mean"], .25)

    def test_nodata_masks_and_clouds(self):
        for kwargs in [{"red": [[0]]}, {"scl": [[9]]}, {"geometry_mask": [[False]]}, {"nir": np.ma.array([[6000]], mask=[[True]])}]:
            with self.subTest(kwargs=kwargs):
                result = observe(**kwargs)
                self.assertEqual(result["valid_pixels"], 0)
                self.assertTrue(np.isnan(result["ndvi_mean"]))

    def test_ndmi_has_own_nodata_and_pixel_count(self):
        result = observe(swir=[[0]])
        self.assertEqual(result["valid_pixels"], 1)
        self.assertEqual(result["ndmi_valid_pixels"], 0)
        self.assertTrue(np.isnan(result["ndmi_mean"]))

    def test_zero_denominator_and_negative_reflectance(self):
        result = observe(red=[[1000]], nir=[[1000]], metadata=meta(-.1))
        self.assertEqual(result["valid_pixels"], 0)
        result = observe(red=[[500]], metadata=meta(-.1))
        self.assertEqual(result["valid_pixels"], 0)

    def test_shape_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError, "co-registered"):
            observe(swir=[[4000, 4000]])

    def test_metadata_v1_v2_and_missing_transform(self):
        self.assertEqual(band_metadata({"raster:bands": [{"scale": .1, "offset": 2, "nodata": 0}]}), {"scale": .1, "offset": 2, "nodata": 0})
        self.assertEqual(band_metadata({"bands": [{"raster:scale": .1, "raster:offset": 2}]}), {"scale": .1, "offset": 2, "nodata": None})
        with self.assertRaises(ValueError):
            band_metadata({"raster:bands": [{"nodata": 0}]})

    def test_duplicate_date_weighting_before_auc(self):
        daily, features = cutoff_features(observations(), "2024-04-03T23:59Z")
        self.assertEqual(len(daily), 2)
        self.assertAlmostEqual(daily.iloc[0].ndvi, .5)
        self.assertAlmostEqual(daily.iloc[0].ndmi, .4)
        self.assertEqual(daily.iloc[0].item_count, 2)
        self.assertAlmostEqual(features.iloc[0].auc_ndvi_days, 1.2)
        self.assertEqual(features.iloc[0].peak_ndvi_date, "2024-04-03")

    def test_delayed_observation_cannot_leak(self):
        frame = observations(); frame.loc[2, "available_at"] = "2024-04-05T00:00Z"
        daily, features = cutoff_features(frame, "2024-04-03T23:59Z")
        self.assertEqual(len(daily), 1)
        self.assertTrue(np.isnan(features.iloc[0].auc_ndvi_days))
        self.assertAlmostEqual(features.iloc[0].latest_ndvi, .5)

    def test_future_observation_cannot_leak(self):
        _, features = cutoff_features(observations(), "2024-04-02T23:59Z")
        self.assertAlmostEqual(features.iloc[0].peak_ndvi, .5)

    def test_duplicate_item_and_bad_quality_rejected(self):
        frame = observations()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            cutoff_features(pd.concat([frame, frame.iloc[[0]]]), "2024-04-03T23:59Z")
        frame.loc[0, "clear_fraction"] = .9
        with self.assertRaisesRegex(ValueError, "clear_fraction"):
            cutoff_features(frame, "2024-04-03T23:59Z")

    def test_empty_cutoff_returns_schema(self):
        daily, features = cutoff_features(observations(), "2024-03-31T23:59Z")
        self.assertTrue(daily.empty)
        self.assertTrue(features.empty)
        self.assertIn("auc_ndvi_days", features)

    def test_missing_availability_requires_explicit_opt_out(self):
        frame = observations().drop(columns="available_at")
        with self.assertRaises(ValueError):
            cutoff_features(frame, "2024-04-03T23:59Z")
        _, result = cutoff_features(frame, "2024-04-03T23:59Z", require_available_at=False)
        self.assertFalse(result.iloc[0].availability_filter_applied)
