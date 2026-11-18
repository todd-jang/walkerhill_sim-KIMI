"""L1 실행기 테스트 — 정책 격자·예약기록 변환·상태머신·I1~I6 검증."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from wise_sim.l1_run import (
    L1RunError,
    build_booking_records,
    build_policies,
    run,
)


def _persona(pid: int, nights: float = 2.0, spend_index: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(id=pid, archetype="Business", stay_nights=nights, spend_index=spend_index,
                           dining_preference="unknown")


def _decision(pid: int, booked: bool, spend: float = 100000.0) -> SimpleNamespace:
    return SimpleNamespace(persona_id=pid, archetype="Business", booked=booked,
                           abstained=not booked, spend=spend, trace="test")


class PolicyGridTest(unittest.TestCase):
    def test_baseline_is_single_policy(self):
        self.assertEqual(len(build_policies("baseline")), 1)

    def test_three_by_three_grid(self):
        policies = build_policies("3x3")
        self.assertEqual(len(policies), 9)
        self.assertEqual({p.price_delta for p in policies}, {-0.10, 0.0, 0.10})

    def test_unknown_grid_rejected(self):
        with self.assertRaises(L1RunError):
            build_policies("2x2")


class BookingRecordTest(unittest.TestCase):
    def test_only_booked_decisions_become_records(self):
        decisions = [_decision(1, True, 300000.0), _decision(2, False), _decision(3, True, 0.0)]
        records = build_booking_records(decisions, {1: _persona(1), 2: _persona(2), 3: _persona(3)}, seed=1)
        self.assertEqual([r.agent_id for r in records], ["agent-001", "agent-003"])

    def test_price_per_night_divides_spend_by_nights(self):
        records = build_booking_records([_decision(1, True, 300000.0)], {1: _persona(1, nights=3.0)}, seed=1)
        self.assertEqual(records[0].trip_length, 3)
        self.assertEqual(records[0].price_per_night, 100000.0)

    def test_loyalty_grade_from_spend_index(self):
        decisions = [_decision(1, True), _decision(2, True)]
        personas = {1: _persona(1, spend_index=1.5), 2: _persona(2, spend_index=1.0)}
        records = build_booking_records(decisions, personas, seed=1)
        self.assertEqual([r.loyalty for r in records], ["high", "medium"])


class ReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run(agents=6, seed=42, provider="mock", policies="baseline")

    def test_report_has_all_machines(self):
        bookings = self.report["runs"][0]["bookings"]
        self.assertGreater(len(bookings), 0)
        machines = bookings[0]["machines"]
        for key in ("m1_reservation", "m2_stay", "m3_folio", "m4_compliance", "m5_transport", "m7_outlet_session"):
            self.assertIn(key, machines)

    def test_outlet_session_mapped_from_archetype(self):
        booking = self.report["runs"][0]["bookings"][0]
        session = booking["machines"]["m7_outlet_session"]
        self.assertIsNotNone(session["outlet"])
        self.assertEqual(session["state_path"][0], "ENTERED")

    def test_validation_runs_with_invariant_codes(self):
        booking = self.report["runs"][0]["bookings"][0]
        self.assertIn(booking["validation"]["status"], {"valid", "invalid", "skipped", "error"})
        self.assertEqual(self.report["summary"]["invariant_violations"], 0)

    def test_machine_scope_documented(self):
        scope = self.report["state_machine_scope"]
        self.assertEqual(scope["adapter_machines"], ["M1", "M2", "M3"])
        self.assertEqual(scope["runner_simulated_machines"], ["M4", "M5", "M7"])

    def test_report_is_json_serializable(self):
        json.dumps(self.report, ensure_ascii=False)

    def test_three_by_three_runs_nine_policies(self):
        report = run(agents=4, seed=7, provider="mock", policies="3x3")
        self.assertEqual(len(report["runs"]), 9)
        self.assertEqual(report["summary"]["decisions"], 36)

    def test_same_seed_same_summary(self):
        other = run(agents=6, seed=42, provider="mock", policies="baseline")
        self.assertEqual(self.report["summary"], other["summary"])
        self.assertEqual(json.dumps(self.report["runs"], sort_keys=True),
                         json.dumps(other["runs"], sort_keys=True))


if __name__ == "__main__":
    unittest.main()
