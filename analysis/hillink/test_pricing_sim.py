"""pricing_sim (논문 Algorithm 1·2 간이 구현) 테스트."""

import unittest

import numpy as np

import hillink_fit
import pricing_sim as ps


class SmallFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = hillink_fit.build_dataset()
        cls.calibration = ps.phase1(cls.dataset)
        cls.prices_a = ps.policy_baseline(cls.dataset)
        cls.prices_b = ps.policy_reweight(cls.dataset, markup=0.15, discount=0.10, top=10, bottom=20)
        cls.modeled = cls.calibration.modeled_baseline_revenue


class PolicyTest(unittest.TestCase):
    def test_policy_b_marks_up_top_and_discounts_bottom(self):
        dataset = hillink_fit.build_dataset()
        base = ps.policy_baseline(dataset)
        adjusted = ps.policy_reweight(dataset, markup=0.15, discount=0.10, top=10, bottom=20)
        demands = np.array([o.demand for o in dataset.observations], dtype=float)
        order = np.argsort(-demands)
        np.testing.assert_allclose(adjusted[order[:10]], base[order[:10]] * 1.15)
        tail = [i for i in order[::-1] if demands[i] > 0][:20]
        np.testing.assert_allclose(adjusted[tail], base[tail] * 0.90)
        untouched = [i for i in range(len(base)) if i not in set(order[:10]) | set(tail)]
        np.testing.assert_array_equal(adjusted[untouched], base[untouched])
        self.assertTrue((adjusted > 0).all())

    def test_policy_b_price_ratios_unique(self):
        dataset = hillink_fit.build_dataset()
        adjusted = ps.policy_reweight(dataset, markup=0.15, discount=0.10, top=10, bottom=20)
        base = ps.policy_baseline(dataset)
        ratios = np.round(adjusted / base, 6)
        self.assertEqual(set(np.unique(ratios)), {0.9, 1.0, 1.15})


class SimulationTest(SmallFixture):
    def test_phase2_is_deterministic_per_seed_and_sums_to_total(self):
        rng_a = np.random.default_rng(1)
        rng_b = np.random.default_rng(1)
        pop_a = ps.phase2(self.calibration, rng_a)
        pop_b = ps.phase2(self.calibration, rng_b)
        np.testing.assert_array_equal(pop_a.persona_counts, pop_b.persona_counts)
        self.assertEqual(pop_a.total, pop_a.persona_counts.sum())
        self.assertLess(abs(pop_a.total - self.calibration.lambda_total), 5 * np.sqrt(self.calibration.lambda_total))

    def test_baseline_expectation_matches_modeled_revenue(self):
        result = ps.algorithm2(self.dataset, self.calibration, self.prices_a, self.prices_b, trials=80, elasticity=1.5)
        # E[V(πA)] = Λ·Σ_k w_k Σ_u q_ku p_u = 모형 기준 매출 (N~Poisson(Λ) 기대값)
        self.assertLess(abs(result['mean_V_A'] - self.modeled) / self.modeled, 0.01)

    def test_zero_elasticity_delta_equals_price_only_effect(self):
        result = ps.algorithm2(self.dataset, self.calibration, self.prices_a, self.prices_b, trials=80, elasticity=0.0)
        demands = np.array([o.demand for o in self.dataset.observations], dtype=float)
        expected = float(demands @ (self.prices_b - self.prices_a))  # ε=0이면 구매수 불변 → ΔV = Σd·Δp
        self.assertLess(abs(result['delta_mean'] - expected) / abs(expected), 0.01)

    def test_markup_policy_hurts_more_at_higher_elasticity(self):
        deltas = [
            ps.algorithm2(self.dataset, self.calibration, self.prices_a, self.prices_b, trials=60, elasticity=eps)['delta_mean']
            for eps in (1.0, 1.5, 2.0)
        ]
        self.assertLess(deltas[2], deltas[1])
        self.assertLess(deltas[1], deltas[0])
        self.assertLess(abs(deltas[0]), 0.02 * self.modeled)  # ε=1이면 거의 수익중립

    def test_algorithm2_reproducible_with_fixed_seeds(self):
        first = ps.algorithm2(self.dataset, self.calibration, self.prices_a, self.prices_b, trials=20, elasticity=1.5)
        second = ps.algorithm2(self.dataset, self.calibration, self.prices_a, self.prices_b, trials=20, elasticity=1.5)
        self.assertEqual(first['delta_mean'], second['delta_mean'])
        self.assertEqual(first['delta_std'], second['delta_std'])
        self.assertEqual(first['ci95'], second['ci95'])

    def test_trip_profile_matches_empirical_distribution(self):
        rng = np.random.default_rng(0)
        population = ps.phase2(self.calibration, rng)
        profile = ps.sample_trips(population, self.calibration, rng)
        self.assertEqual(sum(profile.values()), population.total)
        p = self.calibration.trip_probabilities
        for length, index in ((1, 0), (2, 1), (3, 2)):
            self.assertLess(abs(profile[length] / population.total - p[index]), 0.02)


if __name__ == '__main__':
    unittest.main()
