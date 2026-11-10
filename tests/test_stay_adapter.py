import unittest
from wise_sim.personas import generate_population
from wise_sim.l1_agent import Decision, MockAgent, Policy
from wise_sim.stay_adapter import (
    IntegrityViolationError,
    TemporalViolationError,
    build_stay_masters,
    export_confirmed_cohort,
    load_ontology_map,
    validate_and_join_l5_ledger,
)


class StayAdapterTests(unittest.TestCase):
    def setUp(self):
        self.pop = generate_population(n=20, seed=42)
        agent = MockAgent()
        policy = Policy(price_delta=0.0, promo="package")
        self.decisions = agent.decide_batch(self.pop, policy, weekend=True, seed=42)

    def test_ontology_map_loaded(self):
        cfg = load_ontology_map()
        self.assertEqual(cfg["contract_name"], "WISE_HILLINK_ONTOLOGY_MAP")
        self.assertEqual(cfg["principles"]["join_key"], "stay_id")
        self.assertEqual(cfg["room_inventory_allocation"]["total_rooms"], 580)

    def test_build_stay_masters_and_export_cohort(self):
        stays = build_stay_masters(self.decisions, self.pop, weekend_idx=0, base_date_str="2026-10-02")
        self.assertTrue(len(stays) > 0)

        first = stays[0]
        self.assertTrue(first.stay_id.startswith("STAY_W0_"))
        self.assertEqual(first.check_in_time, "2026-10-02T15:00:00")
        self.assertTrue("SKU_THE_BUFFET_ADULT_2P" in first.package_benefits)

        cohort = export_confirmed_cohort(stays, policy_promo="package")
        self.assertEqual(cohort["cohort_size"], len(stays))
        self.assertEqual(cohort["policy_promo"], "package")
        self.assertEqual(cohort["guests"][0]["stay_id"], first.stay_id)

    def test_validate_and_join_l5_ledger_success(self):
        stays = build_stay_masters(self.decisions, self.pop, weekend_idx=0, base_date_str="2026-10-02")
        valid_sid = stays[0].stay_id

        receipts = [
            {
                "receipt_id": "RCPT_001",
                "stay_id": valid_sid,
                "venue": "금룡",
                "ordered_at": "2026-10-02T18:30:00",
                "amount_krw": 180000,
            }
        ]
        joined = validate_and_join_l5_ledger(stays, receipts)
        self.assertEqual(len(joined), 1)
        self.assertEqual(joined[0]["persona_id"], stays[0].persona_id)

    def test_orphan_stay_id_raises_integrity_error(self):
        stays = build_stay_masters(self.decisions, self.pop, weekend_idx=0, base_date_str="2026-10-02")
        receipts = [
            {
                "receipt_id": "RCPT_002",
                "stay_id": "STAY_INVALID_UNKNOWN",
                "venue": "피자힐",
                "ordered_at": "2026-10-03T18:30:00",
                "amount_krw": 75000,
            }
        ]
        with self.assertRaises(IntegrityViolationError):
            validate_and_join_l5_ledger(stays, receipts)

    def test_checkout_violation_raises_temporal_error(self):
        stays = build_stay_masters(self.decisions, self.pop, weekend_idx=0, base_date_str="2026-10-02")
        valid_sid = stays[0].stay_id

        # 체크아웃은 2026-10-04 11:00인데 10월 5일에 주문한 경우
        receipts = [
            {
                "receipt_id": "RCPT_003",
                "stay_id": valid_sid,
                "venue": "더뷔페",
                "ordered_at": "2026-10-05T12:00:00",
                "amount_krw": 150000,
            }
        ]
        with self.assertRaises(TemporalViolationError):
            validate_and_join_l5_ledger(stays, receipts)


if __name__ == "__main__":
    unittest.main()
