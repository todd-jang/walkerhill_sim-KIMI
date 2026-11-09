import sys
import unittest
from pathlib import Path

# Add wise_sim to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "wise_sim"))

import personas
import l1_agent
import l2_engine
import l3_verdict


class TestPipelineLayers(unittest.TestCase):
    def setUp(self):
        self.personas = personas.generate_population(n=400, seed=42)

    def test_l0_calibration(self):
        check = personas.calibrate_check(self.personas)
        self.assertTrue(check["pass"])

    def test_l1_and_l2_deterministic_simulation(self):
        agent = l1_agent.MockAgent()
        policy = l1_agent.Policy(price_delta=0.0, promo="none")
        res = l2_engine.simulate_weekend(agent, self.personas, policy, weekend_idx=0, seed=100)
        
        self.assertIn("occupancy", res)
        self.assertIn("revpar", res)
        self.assertGreaterEqual(res["occupancy"], 0.0)
        self.assertLessEqual(res["occupancy"], 1.0)
        self.assertGreater(res["revpar"], 0)

    def test_l3_statistics_and_verdict(self):
        rho = l3_verdict.spearman_rho([1, 2, 3, 4], [1, 2, 3, 4])
        self.assertAlmostEqual(rho, 1.0, places=4)

        lo, hi = l3_verdict.wilson_ci(k=8, n=10)
        self.assertTrue(0.0 <= lo <= hi <= 1.0)

        v_pass, _ = l3_verdict.verdict(direction=0.85, n_exceed=0)
        self.assertEqual(v_pass, "적합")

        v_fail, _ = l3_verdict.verdict(direction=0.60, n_exceed=0)
        self.assertEqual(v_fail, "부적합")


if __name__ == "__main__":
    unittest.main()
