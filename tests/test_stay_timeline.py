"""투숙 세그먼트 합성·I1~I6 엄격 검증 테스트."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from wise_sim.stay_timeline import build_stay_segments, strict_validation, stay_dates


def _record(nights: int = 1, day: int = 5) -> SimpleNamespace:
    return SimpleNamespace(agent_id="agent-001", stay_date_start=day, trip_length=nights)


class StayDatesTest(unittest.TestCase):
    def test_checkin_clamped_to_month_days(self):
        checkin, checkout = stay_dates(_record(nights=2, day=40))
        self.assertEqual(checkin.day, 28)
        self.assertEqual((checkout - checkin).days, 2)


class SegmentTest(unittest.TestCase):
    def test_segments_are_time_ordered(self):
        segments = build_stay_segments(_record(), "클럽 라운지")
        starts = [segment.t_in for segment in segments]
        self.assertEqual(starts, sorted(starts))

    def test_outlet_segments_present_and_tagged(self):
        segments = build_stay_segments(_record(), "더 뷔페")
        venues = [segment.venue for segment in segments if segment.venue]
        self.assertEqual(venues, ["THE_BUFFET", "THE_BUFFET"])
        machines = {segment.machine for segment in segments if segment.venue}
        self.assertEqual(machines, {"M7_OutletSession"})

    def test_no_outlet_yields_four_segments(self):
        self.assertEqual(len(build_stay_segments(_record(), None)), 4)


class StrictValidationTest(unittest.TestCase):
    def test_all_mapped_outlets_are_valid(self):
        for outlet in ("클럽 라운지", "더 뷔페", "온달", "더 파빌리온", None):
            result = strict_validation(_record(), outlet)
            self.assertEqual(result["status"], "valid", msg=f"{outlet}: {result.get('violations')}")
            self.assertEqual(result["violations_count"], 0)

    def test_multi_night_stay_is_valid(self):
        result = strict_validation(_record(nights=3), "온달")
        self.assertEqual(result["status"], "valid")

    def test_outlet_outside_operating_window_is_invalid(self):
        segments = build_stay_segments(_record(), "클럽 라운지", session_hour=3.0)
        from wise_sim.stay_timeline import timeline_validator

        report = timeline_validator.validate_walkerhill_timeline(segments, nights=1)
        self.assertFalse(report.ok)
        self.assertTrue(any(item.invariant == "I3" for item in report.violations))

    def test_coverage_label_is_full_stay(self):
        result = strict_validation(_record(), "온달")
        self.assertEqual(result["coverage"], "full-stay (checkin→room→outlet→room→checkout)")
        self.assertEqual(result["segments"], 6)


if __name__ == "__main__":
    unittest.main()
