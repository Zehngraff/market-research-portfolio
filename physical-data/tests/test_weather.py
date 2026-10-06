import unittest

import numpy as np
import pandas as pd

from physical_data.weather import daily_weather, era5_request_plan, weather_summary


def hourly(start="2024-12-31"):
    times = pd.date_range(start, periods=25, freq="h", tz="UTC")
    return pd.DataFrame({"datetime": times, "temperature_k": [283.15] * 25,
        "precip_m": [0.0] + [i / 1000 for i in range(1, 25)],
        "available_at": times + pd.Timedelta(hours=3)})


class WeatherTests(unittest.TestCase):
    def test_cumulative_is_not_summed(self):
        result = daily_weather(hourly(), "2024-12-31", "2024-12-31").iloc[0]
        self.assertEqual(result.precip_mm, 24.0)
        self.assertNotEqual(result.precip_mm, 300.0)
        self.assertAlmostEqual(result.temp_mean_c, 10.0)
        self.assertAlmostEqual(result.gdd_base5, 5.0)
        self.assertAlmostEqual(result.gdd_base10, 0.0)
        self.assertTrue(result.precip_valid)

    def test_terminal_next_year_boundary_is_required(self):
        result = daily_weather(hourly().iloc[:-1], "2024-12-31", "2024-12-31").iloc[0]
        self.assertFalse(result.precip_terminal_present)
        self.assertTrue(np.isnan(result.precip_mm))
        self.assertEqual(result.precip_observed_lower_bound_mm, 23.0)
        self.assertTrue(np.isnan(result.dry_day_lt1mm))

    def test_missing_interior_hour_is_not_complete(self):
        result = daily_weather(hourly().drop(index=12), "2024-12-31", "2024-12-31").iloc[0]
        self.assertTrue(result.precip_terminal_present)
        self.assertFalse(result.precip_complete_24h)
        self.assertTrue(np.isnan(result.precip_mm))
        self.assertFalse(result.temp_complete_24h)

    def test_duplicate_timestamp_rejected(self):
        frame = hourly()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            daily_weather(pd.concat([frame, frame.iloc[[4]]]), "2024-12-31", "2024-12-31")

    def test_decreasing_cumulative_invalid(self):
        frame = hourly(); frame.loc[14, "precip_m"] = 0.001
        result = daily_weather(frame, "2024-12-31", "2024-12-31").iloc[0]
        self.assertEqual(result.precip_within_cycle_decreases, 1)
        self.assertFalse(result.precip_valid)
        self.assertTrue(np.isnan(result.precip_observed_lower_bound_mm))

    def test_negative_precipitation_invalid(self):
        frame = hourly(); frame.loc[1, "precip_m"] = -0.001
        self.assertFalse(daily_weather(frame, "2024-12-31", "2024-12-31").iloc[0].precip_valid)

    def test_null_temperature_and_precipitation_not_zero(self):
        frame = hourly(); frame.loc[1, ["temperature_k", "precip_m"]] = np.nan
        result = daily_weather(frame, "2024-12-31", "2024-12-31")
        self.assertTrue(np.isnan(result.iloc[0].gdd_base5))
        self.assertIsNone(weather_summary(result)["precipitation_total_mm"])

    def test_availability_cutoff_does_not_use_late_boundary(self):
        result = daily_weather(hourly(), "2024-12-31", "2024-12-31", available_by="2025-01-01T02:00:00Z").iloc[0]
        self.assertFalse(result.precip_terminal_present)
        self.assertTrue(np.isnan(result.precip_mm))

    def test_availability_column_required(self):
        with self.assertRaisesRegex(ValueError, "available_at"):
            daily_weather(hourly().drop(columns="available_at"), "2024-12-31", "2024-12-31", available_by="2025-01-01T03:00Z")

    def test_bad_date_naive_timestamp_and_bad_numeric_rejected(self):
        for start in ["2024-02-30", "12/31/2024"]:
            with self.subTest(start=start), self.assertRaises(ValueError):
                daily_weather(hourly(), start, "2024-12-31")
        frame = hourly(); frame["datetime"] = frame.datetime.dt.tz_localize(None)
        with self.assertRaises(ValueError):
            daily_weather(frame, "2024-12-31", "2024-12-31")
        frame = hourly(); frame["precip_m"] = "bad"
        with self.assertRaises(ValueError):
            daily_weather(frame, "2024-12-31", "2024-12-31")

    def test_missing_whole_day_propagates_cumulative_unknown(self):
        result = daily_weather(hourly(), "2024-12-30", "2024-12-31")
        self.assertTrue(result.cum_precip_mm.isna().all())
        self.assertIsNone(weather_summary(result)["precipitation_total_mm"])
        self.assertEqual(weather_summary(result)["precipitation_observed_valid_days_mm"], 24.0)

    def test_request_plan_includes_new_year_midnight(self):
        plan = era5_request_plan("2024-12-30", "2024-12-31", [56.1, 9.9, 56.0, 10.0])
        self.assertEqual(plan[-1]["request"]["year"], "2025")
        self.assertEqual(plan[-1]["request"]["time"], ["00:00"])
        self.assertEqual(plan[-1]["request"]["day"], ["01"])

    def test_leap_day_and_exact_hour(self):
        result = daily_weather(hourly("2024-02-29"), "2024-02-29", "2024-02-29")
        self.assertTrue(result.iloc[0].precip_valid)
        frame = hourly(); frame["datetime"] += pd.Timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError, "exactly"):
            daily_weather(frame, "2024-12-31", "2024-12-31")
