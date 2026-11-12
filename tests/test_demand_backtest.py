import csv
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from wise_sim.demand_backtest import (
    forecast_future,
    load_daily_csv,
    run_temporal_backtest,
    run_virtual_clock,
)
from wise_sim.l2_engine import forecast_from_history, replay_forecast_from_history


def daily_history(days=1_500):
    start = date(2020, 1, 1)
    records = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        weekend = 16 if day.weekday() >= 4 else 0
        annual = round(15 * math.sin(2 * math.pi * day.timetuple().tm_yday / 365.25))
        rooms_sold = min(580, max(0, 300 + weekend + annual))
        adr = 180_000 + 5_000 * (day.weekday() >= 4) + 10_000 * (day.month in (7, 8, 12))
        records.append({
            "date": day,
            "reservations": max(1, round(rooms_sold * 0.8)),
            "rooms_sold": rooms_sold,
            "available_rooms": 580,
            "room_revenue": rooms_sold * adr,
        })
    return records


class DemandBacktestTests(unittest.TestCase):
    def setUp(self):
        self.records = daily_history()

    def test_chronological_backtest_reports_realistic_metrics_and_seasons(self):
        result = run_temporal_backtest(self.records)
        self.assertEqual(result["analysis_unit"], "hotel × calendar day")
        self.assertEqual(result["train_days"], 730)
        self.assertEqual(result["test_days"], 365)
        self.assertLess(result["training_period"][1], result["test_period"][0])
        self.assertEqual(result["metrics"]["reservations"]["n"], 365)
        self.assertEqual(result["metrics"]["occupancy"]["n"], 365)
        self.assertEqual(result["metrics"]["adr"]["n"], 365)
        self.assertEqual(result["metrics"]["revpar"]["n"], 365)
        self.assertIn("baseline_metrics", result)
        self.assertIn("mae_improvement_vs_baseline", result)
        self.assertIn("occupancy", result["mae_improvement_vs_baseline"])
        self.assertIn("high", result["seasonal_calibration"])
        self.assertIn("low", result["seasonal_calibration"])
        self.assertIn(
            "calibration_ratio",
            result["seasonal_calibration"]["high"]["occupancy"],
        )
        self.assertEqual(len(result["daily"]), 365)

    def test_future_forecast_is_bounded_and_wired_into_l2(self):
        forecasts = forecast_future(self.records, days=8, available_rooms=[500] * 8)
        self.assertEqual(len(forecasts), 8)
        self.assertLessEqual(forecasts[0]["rooms_sold"], 500)
        self.assertEqual(forecasts[0]["available_rooms"], 500)
        l2 = forecast_from_history(self.records, days=3)
        self.assertEqual(l2["simulation_layer"], "L2")
        self.assertFalse(l2["policy_effect_applied"])
        self.assertEqual(len(l2["days"]), 3)

    def test_virtual_clock_uses_24x_daily_ticks_and_l2_replay(self):
        forecasts = forecast_future(self.records, days=3)
        delays = []
        ticks = []
        replayed = run_virtual_clock(
            forecasts, speed=24, sleep=delays.append, on_tick=ticks.append
        )
        self.assertEqual(delays, [3600, 3600])
        self.assertEqual([tick["virtual_tick"] for tick in replayed], [1, 2, 3])
        l2_ticks = replay_forecast_from_history(
            self.records, days=2, tick_seconds=0, on_tick=ticks.append
        )
        self.assertEqual([tick["virtual_tick"] for tick in l2_ticks], [1, 2])

    def test_csv_contract_loads_daily_rows_and_rejects_missing_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily.csv"
            with path.open("w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(
                    output,
                    fieldnames=[
                        "date",
                        "reservations",
                        "rooms_sold",
                        "available_rooms",
                        "room_revenue",
                        "season",
                    ],
                )
                writer.writeheader()
                writer.writerow({
                    "date": "2025-01-01",
                    "reservations": 10,
                    "rooms_sold": 12,
                    "available_rooms": 580,
                    "room_revenue": 2_400_000,
                    "season": "low",
                })
            loaded = load_daily_csv(path)
            self.assertEqual(loaded[0]["season"], "low")
            self.assertEqual(loaded[0]["rooms_sold"], 12)

    def test_backtest_rejects_gaps_and_short_history(self):
        with self.assertRaisesRegex(ValueError, "consecutive"):
            run_temporal_backtest([self.records[0], self.records[2]])
        with self.assertRaisesRegex(ValueError, "requires"):
            run_temporal_backtest(self.records[:1_000])


if __name__ == "__main__":
    unittest.main()
