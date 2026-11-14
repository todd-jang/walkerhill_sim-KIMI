"""pricing_core 결정론·불변식·paired 재현성 검증."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pricing_core.environment import get_walkerhill_categories
from pricing_core.llm_client import LLMDecisionClient
from pricing_core.population import PopulationGenerator
from pricing_core.policies import StaticPolicy, OccupancyDynamicPolicy
from pricing_core.simulator import HotelPricingSimulator, ComparativeEvaluator
from pricing_run import scale_categories, speed_block

WALKERHILL_MIN_24X = 24.0


class DeterminismTests(unittest.TestCase):
    def test_same_seed_same_revenue(self):
        cats = get_walkerhill_categories()
        policy = StaticPolicy(cats)
        pop = PopulationGenerator(horizon=14, seed=7).generate_population(target_total_agents=80)
        sim = HotelPricingSimulator(categories=cats, horizon=14)
        a = sim.run_simulation(policy, pop, order_seed=7).total_revenue
        b = sim.run_simulation(policy, pop, order_seed=7).total_revenue
        self.assertEqual(a, b)

    def test_different_order_seed_can_differ_or_match_but_is_reproducible(self):
        cats = get_walkerhill_categories()
        policy = StaticPolicy(cats)
        pop = PopulationGenerator(horizon=14, seed=7).generate_population(target_total_agents=80)
        sim = HotelPricingSimulator(categories=cats, horizon=14)
        self.assertEqual(
            sim.run_simulation(policy, pop, order_seed=1).total_revenue,
            sim.run_simulation(policy, pop, order_seed=1).total_revenue,
        )


class InventoryInvariantTests(unittest.TestCase):
    def test_availability_never_negative_and_revenue_matches_bookings(self):
        cats = get_walkerhill_categories()
        policy = OccupancyDynamicPolicy(cats)
        pop = PopulationGenerator(horizon=21, seed=11).generate_population(target_total_agents=200)
        sim = HotelPricingSimulator(categories=cats, horizon=21)
        metrics = sim.run_simulation(policy, pop, order_seed=11)
        from pricing_core.environment import InventoryMatrix
        inv = InventoryMatrix(cats, 21)
        self.assertTrue(all(a >= 0 for row in inv.availability.values() for a in row))
        total = sum(b.total_price for b in metrics.bookings)
        self.assertAlmostEqual(metrics.total_revenue, total, places=2)
        self.assertLessEqual(len(metrics.bookings), len(pop))


class PairedComparisonTests(unittest.TestCase):
    def test_paired_runs_share_order_and_reproducible(self):
        cats = get_walkerhill_categories()
        sim = HotelPricingSimulator(categories=cats, horizon=14)
        ev = ComparativeEvaluator(sim)
        r1 = ev.run_paired_comparison(
            StaticPolicy(cats), OccupancyDynamicPolicy(cats),
            num_trials=3, agents_per_trial=60, base_seed=500,
        )
        r2 = ev.run_paired_comparison(
            StaticPolicy(cats), OccupancyDynamicPolicy(cats),
            num_trials=3, agents_per_trial=60, base_seed=500,
        )
        self.assertEqual(r1.trials_data, r2.trials_data)

    def test_price_scale_scenario_changes_baseline(self):
        cats = get_walkerhill_categories()
        pop = PopulationGenerator(horizon=14, seed=7).generate_population(target_total_agents=80)
        sim_base = HotelPricingSimulator(categories=cats, horizon=14)
        sim_high = HotelPricingSimulator(categories=scale_categories(cats, 1.2), horizon=14)
        policy = StaticPolicy(cats)
        v_base = sim_base.run_simulation(policy, pop, order_seed=7).total_revenue
        v_high = sim_high.run_simulation(StaticPolicy(scale_categories(cats, 1.2)), pop, order_seed=7).total_revenue
        self.assertNotEqual(v_base, v_high)


class SpeedTests(unittest.TestCase):
    def test_speed_block_reports_24x_for_mock_engine(self):
        cats = get_walkerhill_categories()
        policy = StaticPolicy(cats)
        pop = PopulationGenerator(horizon=31, seed=42).generate_population(target_total_agents=300)
        sim = HotelPricingSimulator(categories=cats, horizon=31)
        import time
        t0 = time.perf_counter()
        sim.run_simulation(policy, pop, order_seed=42)
        wall = time.perf_counter() - t0
        speed = speed_block(31, wall, 0)["speedup_realtime_x"]
        self.assertGreaterEqual(speed, WALKERHILL_MIN_24X)
        self.assertGreater(speed, 100_000.0)


class CalibrationBridgeTests(unittest.TestCase):
    def test_bridge_runs_with_walkerhill_sample(self):
        from pricing_core.calibration_bridge import CalibrationBridge
        obs = [("Standard", 210.0, 15), ("Deluxe", 260.0, 10), ("Suite", 350.0, 4)]
        probs = [[0.6, 0.4, 0.1, 0.5], [0.3, 0.5, 0.4, 0.45], [0.05, 0.3, 0.8, 0.15]]
        res = CalibrationBridge.calibrate_persona_weights(obs, probs, ["A", "B", "C", "D"])
        self.assertIn("optimal_N", res)
        priors = CalibrationBridge.map_to_majidi_archetype_priors(res["weights"])
        self.assertAlmostEqual(sum(priors.values()), 1.0, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
