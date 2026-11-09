import unittest

import numpy as np

from wise_sim.l1_agent import MockAgent, Policy
from wise_sim.l2_engine import evaluate_facility_policy, simulate_weekend
from wise_sim.personas import generate_population
from wise_sim.transit_matrix import TransitContext
from wise_sim.l3_verdict import full_report


class TransitContextTests(unittest.TestCase):
    def test_default_matrix_and_disutility_include_queue_wait(self):
        context = TransitContext()
        self.assertEqual(context.get_transit_time("room", "fnb"), 10.0)
        self.assertAlmostEqual(
            context.get_disutility("room", "fnb"), 0.2
        )
        self.assertAlmostEqual(
            context.get_disutility(
                "room", "fnb", queue_wait_minutes=5
            ),
            0.3 + 0.01 * 5**1.5,
        )
        self.assertTrue(context.is_assumption)
        with self.assertRaises(ValueError):
            context.matrix[0, 1] = 4

    def test_invalid_matrix_routes_and_negative_queue_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "shape"):
            TransitContext(matrix=np.zeros((2, 2)))
        with self.assertRaisesRegex(ValueError, "diagonal"):
            TransitContext(matrix=np.ones((6, 6)))
        with self.assertRaisesRegex(ValueError, "unknown facility"):
            TransitContext().get_transit_time("lobby", "fnb")
        with self.assertRaisesRegex(ValueError, "queue_wait_minutes"):
            TransitContext().get_disutility(
                "room", "fnb", queue_wait_minutes=-1
            )

    def test_l2_transit_is_optional_and_l3_discloses_assumptions(self):
        personas = generate_population(40, seed=2)
        agent = MockAgent()
        policy = Policy(price_delta=0.0, promo="package")
        baseline = simulate_weekend(agent, personas, policy, 0, seed=3)
        with_transit = simulate_weekend(
            agent, personas, policy, 0, seed=3,
            transit_context=TransitContext(), queue_wait_minutes=5,
        )
        self.assertNotIn("facility_context", baseline)
        self.assertEqual(baseline["booking_rate"], with_transit["booking_rate"])
        self.assertEqual(
            with_transit["facility_context"]["transit_minutes"], 10.0
        )
        self.assertEqual(
            with_transit["facility_context"]["queue_wait_minutes"], 5.0
        )
        self.assertTrue(
            with_transit["facility_context"]["assumption_based_transit"]
        )

        report = full_report(
            direction=0.8,
            rho=0.7,
            abstain=0.0,
            trace_result={"n_exceed": 0},
            n_weekends=8,
            n_hits=6,
            transit_assumption=True,
        )
        self.assertTrue(report["transit"]["confidence_note_only"])
        self.assertIn("가정값", report["transit"]["note"])

    def test_facility_policy_calculates_net_attraction(self):
        result = evaluate_facility_policy(
            {"fnb_promo_strength": 0.5},
            TransitContext(),
            queue_wait_minutes=5,
        )
        self.assertAlmostEqual(result["disutility"], 0.4118, places=4)
        self.assertAlmostEqual(result["net_attraction"], 0.0882, places=4)
        self.assertTrue(result["assumption_based_transit"])
        with self.assertRaisesRegex(ValueError, "fnb_promo_strength"):
            evaluate_facility_policy(
                {"fnb_promo_strength": 1.1}, TransitContext()
            )


if __name__ == "__main__":
    unittest.main()
