"""Algorithm 1 구현 검증 — 그래디언트, 최적성(scipy 대조), 제약, 평균수요 재현, 입력 검증.

참값 복원을 기대하지 않는 이유: q가 작으면 이항이 푸아송에 가까워져 (N, α) 스케일이
거의 식별되지 않는다(모형의 성질). 그래서 여기서는 "주어진 자료에서 (2.3)/(2.6)/(2.2)의
최적해를 정확히 찾는가"를 본다. 캘리브레이션(2.2)은 (a,b)=(0,1)이 평문 모형을 포함하므로
V(캘리브레이션) ≤ V(평문) 이 성립해야 한다.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import persona_exposure as pe  # noqa: E402

PRICES = [120_000, 150_000, 180_000, 210_000, 240_000, 270_000, 300_000, 330_000]
PERSONAS = np.array(
    [
        [0.32, 0.28, 0.24, 0.19, 0.15, 0.11, 0.08, 0.06],
        [0.22, 0.20, 0.18, 0.16, 0.14, 0.12, 0.10, 0.08],
        [0.10, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03],
    ]
)
TRUTH_WEIGHTS = np.array([0.42, 0.27, 0.18, 0.13])
TRUTH_EXPOSURE = 64


def synthetic(seed: int = 20260615, product_count: int = 12) -> tuple[list[pe.Observation], list[list[float]]]:
    rng = np.random.default_rng(seed)
    observations: list[pe.Observation] = []
    probabilities: list[list[float]] = []
    for product in range(product_count):
        for price_index, price in enumerate(PRICES):
            jitter = 1.0 + 0.05 * ((product * 7 + price_index) % 5 - 2) / 2.0
            row = np.round(PERSONAS[:, price_index] * jitter, 4).tolist()
            mean = float(np.dot(TRUTH_WEIGHTS[:3], row))
            demand = int(rng.binomial(TRUTH_EXPOSURE, mean))
            observations.append(pe.Observation(f'P{product:02d}', price, demand))
            probabilities.append(row)
    return observations, probabilities


def truncated_synthetic(seed: int = 20260615) -> tuple[list[pe.Observation], list[list[float]]]:
    observations, probabilities = synthetic(seed)
    kept = [(o, p) for o, p in zip(observations, probabilities) if o.demand > 0]
    return [o for o, _ in kept], [p for _, p in kept]


class GradientTest(unittest.TestCase):
    def setUp(self) -> None:
        observations, probabilities = truncated_synthetic()
        self.probs = np.asarray(probabilities)
        self.demands = np.asarray([o.demand for o in observations], dtype=float)
        self.exposure = TRUTH_EXPOSURE
        self.weights = np.array([0.40, 0.26, 0.17, 0.17])

    def _finite_difference(self, *, truncated: bool, barrier: float, index: int, h: float = 1e-6) -> float:
        up, down = self.weights.copy(), self.weights.copy()
        up[index] += h
        down[index] -= h
        f_up, _ = pe.objective_and_gradient(self.probs, self.demands, self.exposure, up, truncated=truncated, barrier=barrier)
        f_down, _ = pe.objective_and_gradient(self.probs, self.demands, self.exposure, down, truncated=truncated, barrier=barrier)
        return (f_up - f_down) / (2.0 * h)

    def _check(self, *, truncated: bool, barrier: float) -> None:
        _, gradient = pe.objective_and_gradient(
            self.probs, self.demands, self.exposure, self.weights, truncated=truncated, barrier=barrier
        )
        for index in range(self.weights.size):
            numeric = self._finite_difference(truncated=truncated, barrier=barrier, index=index)
            self.assertAlmostEqual(float(gradient[index]), numeric, places=4)

    def test_full_likelihood_gradient(self) -> None:
        self._check(truncated=False, barrier=0.0)

    def test_truncated_likelihood_gradient(self) -> None:
        self._check(truncated=True, barrier=0.0)

    def test_barrier_gradient(self) -> None:
        self._check(truncated=True, barrier=1e-3)

    def test_dummy_weight_gradient_is_zero(self) -> None:
        _, gradient = pe.objective_and_gradient(self.probs, self.demands, self.exposure, self.weights, truncated=True)
        self.assertEqual(float(gradient[-1]), 0.0)

    def test_barrier_domain_violation_is_rejected(self) -> None:
        weights = np.array([1.0, 0.0, 0.0, 0.0])
        if float(np.max(self.probs @ weights[:3])) >= 0.5:
            value, gradient = pe.objective_and_gradient(
                self.probs, self.demands, self.exposure, weights, truncated=True, barrier=1e-2
            )
            self.assertEqual(value, math.inf)
            self.assertTrue(bool(np.all(gradient == 0.0)))


class OptimalityTest(unittest.TestCase):
    def _slsqp(self, probs: np.ndarray, demands: np.ndarray, exposure: int, *, truncated: bool) -> float:
        from scipy.optimize import minimize

        persona_count = probs.shape[1]

        def objective(weights: np.ndarray) -> float:
            return pe.objective_and_gradient(probs, demands, exposure, weights, truncated=truncated)[0]

        def gradient(weights: np.ndarray) -> np.ndarray:
            return pe.objective_and_gradient(probs, demands, exposure, weights, truncated=truncated)[1]

        constraints = [{'type': 'eq', 'fun': lambda w: float(np.sum(w) - 1.0), 'jac': lambda w: np.ones_like(w)}]
        if truncated:
            constraints.append(
                {
                    'type': 'ineq',
                    'fun': lambda w: 0.5 - 1e-9 - probs @ w[:persona_count],
                    'jac': lambda w: np.hstack([-probs, np.zeros((probs.shape[0], 1))]),
                }
            )
        result = minimize(
            objective,
            np.full(persona_count + 1, 1.0 / (persona_count + 1)),
            jac=gradient,
            method='SLSQP',
            bounds=[(0.0, 1.0)] * (persona_count + 1),
            constraints=constraints,
            options={'maxiter': 800, 'ftol': 1e-14},
        )
        self.assertTrue(result.success or result.status == 0)
        return float(result.fun)

    def test_full_likelihood_matches_slsqp(self) -> None:
        observations, probabilities = synthetic()
        probs = np.asarray(probabilities)
        demands = np.asarray([o.demand for o in observations], dtype=float)
        ours = pe.fit(observations, probabilities, n_max=80, truncated=False, tolerance=1e-6, max_iter=1500)
        value = next(p.value for p in ours.grid if p.exposure == ours.exposure)
        self.assertAlmostEqual(value, self._slsqp(probs, demands, ours.exposure, truncated=False), delta=1e-4)

    def test_truncated_likelihood_matches_slsqp(self) -> None:
        observations, probabilities = truncated_synthetic()
        probs = np.asarray(probabilities)
        demands = np.asarray([o.demand for o in observations], dtype=float)
        ours = pe.fit(observations, probabilities, n_max=80, truncated=True, tolerance=1e-6, max_iter=1500)
        value = next(p.value for p in ours.grid if p.exposure == ours.exposure)
        self.assertAlmostEqual(value, self._slsqp(probs, demands, ours.exposure, truncated=True), delta=1e-4)

    def test_reported_value_matches_recomputed_objective(self) -> None:
        observations, probabilities = truncated_synthetic()
        result = pe.fit(observations, probabilities, n_max=80, truncated=True)
        probs = np.asarray(probabilities)
        demands = np.asarray([o.demand for o in observations], dtype=float)
        recomputed, _ = pe.objective_and_gradient(
            probs, demands, result.exposure, np.asarray(result.weights), truncated=True
        )
        self.assertAlmostEqual(result.value, recomputed, places=8)

    def test_optimum_is_no_worse_than_true_parameters(self) -> None:
        observations, probabilities = synthetic()
        probs = np.asarray(probabilities)
        demands = np.asarray([o.demand for o in observations], dtype=float)
        at_truth, _ = pe.objective_and_gradient(probs, demands, TRUTH_EXPOSURE, TRUTH_WEIGHTS, truncated=False)
        result = pe.fit(observations, probabilities, n_max=80, truncated=False, tolerance=1e-7, max_iter=2000)
        self.assertLessEqual(result.value, at_truth + 1e-6)


class StructureTest(unittest.TestCase):
    def test_weights_live_on_simplex(self) -> None:
        observations, probabilities = truncated_synthetic()
        result = pe.fit(observations, probabilities, n_max=80, truncated=True)
        weights = np.asarray(result.weights)
        self.assertEqual(weights.size, len(probabilities[0]) + 1)
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=8)
        self.assertTrue(bool(np.all(weights >= -1e-12)))

    def test_truncated_constraint_holds(self) -> None:
        observations, probabilities = truncated_synthetic()
        result = pe.fit(observations, probabilities, n_max=80, truncated=True)
        self.assertLessEqual(max(p.max_probability for p in result.grid if p.value < math.inf), 0.5 + 1e-9)

    def test_fitted_mean_demand_reproduces_observed_mean(self) -> None:
        observations, probabilities = truncated_synthetic()
        result = pe.fit(observations, probabilities, n_max=120, truncated=True)
        probs = np.asarray(probabilities)
        fitted_mean = float(np.mean(result.exposure * (probs @ np.asarray(result.weights)[: probs.shape[1]])))
        observed_mean = float(np.mean([o.demand for o in observations]))
        self.assertLess(abs(fitted_mean - observed_mean) / observed_mean, 0.05)

    def test_exposures_below_max_demand_are_skipped(self) -> None:
        observations, probabilities = synthetic()
        result = pe.fit(observations, probabilities, n_max=40, truncated=False)
        demand_max = max(o.demand for o in observations)
        self.assertTrue(all(p.value == math.inf for p in result.grid if p.exposure < demand_max))
        self.assertTrue(all(p.value < math.inf for p in result.grid if p.exposure >= demand_max))

    def test_profile_band_covers_flat_scale_direction(self) -> None:
        observations, probabilities = synthetic()
        result = pe.fit(observations, probabilities, n_max=120, truncated=False)
        band = pe.profile_band(result, delta=1e-3)
        self.assertIn(result.exposure, band)
        self.assertTrue(all(isinstance(exposure, int) for exposure in band))

    def test_warm_and_cold_start_agree(self) -> None:
        observations, probabilities = truncated_synthetic()
        cold = pe.fit(observations, probabilities, n_max=90, truncated=True)
        single = pe._solve_for_exposure(
            np.asarray(probabilities),
            np.asarray([o.demand for o in observations], dtype=float),
            cold.exposure,
            truncated=True,
            tolerance=1e-6,
            max_iter=1500,
        )
        self.assertAlmostEqual(cold.value, single[1], delta=1e-6)


class CalibrationGradientTest(unittest.TestCase):
    def setUp(self) -> None:
        observations, probabilities = truncated_synthetic()
        self.probs = np.asarray(probabilities)
        self.demands = np.asarray([o.demand for o in observations], dtype=float)
        self.exposure = TRUTH_EXPOSURE
        self.weights = np.array([0.40, 0.26, 0.17, 0.17])
        self.intercept = 0.35
        self.slope = 1.2

    def _value(self, intercept: float, slope: float) -> float:
        value, _ = pe.calibration_objective_and_gradient(
            self.probs, self.demands, self.exposure, self.weights, intercept, slope, truncated=True
        )
        return value

    def test_intercept_gradient_matches_finite_difference(self) -> None:
        _, gradient = pe.calibration_objective_and_gradient(
            self.probs, self.demands, self.exposure, self.weights, self.intercept, self.slope, truncated=True
        )
        h = 1e-6
        numeric = (self._value(self.intercept + h, self.slope) - self._value(self.intercept - h, self.slope)) / (2 * h)
        self.assertAlmostEqual(float(gradient[0]), numeric, places=4)

    def test_log_slope_gradient_matches_finite_difference(self) -> None:
        _, gradient = pe.calibration_objective_and_gradient(
            self.probs, self.demands, self.exposure, self.weights, self.intercept, self.slope, truncated=True
        )
        h = 1e-6
        up = self._value(self.intercept, self.slope * math.exp(h))
        down = self._value(self.intercept, self.slope * math.exp(-h))
        numeric = (up - down) / (2 * h)
        self.assertAlmostEqual(float(gradient[1]), numeric, places=4)

    def test_calibrated_value_matches_uncalibrated_when_identity(self) -> None:
        value, _ = pe.calibration_objective_and_gradient(
            self.probs, self.demands, self.exposure, self.weights, 0.0, 1.0, truncated=True
        )
        expected, _ = pe.objective_and_gradient(self.probs, self.demands, self.exposure, self.weights, truncated=True)
        self.assertAlmostEqual(value, expected, places=8)

    def test_calibrate_matrix_is_identity_at_null_calibration(self) -> None:
        calibrated = pe.calibrate_matrix(self.probs, 0.0, 1.0)
        self.assertTrue(bool(np.allclose(calibrated, np.clip(self.probs, pe.CALIBRATION_LOGIT_CLIP, 1 - pe.CALIBRATION_LOGIT_CLIP))))


class CalibrationFitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        observations, probabilities = truncated_synthetic()
        cls.observations = observations
        cls.probabilities = probabilities
        cls.result = pe.fit_calibrated(
            observations, probabilities, n_max=60, truncated=True, calibration_iters=3
        )

    def test_weights_live_on_simplex(self) -> None:
        weights = np.asarray(self.result.weights)
        self.assertEqual(weights.size, len(self.probabilities[0]) + 1)
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=8)
        self.assertTrue(bool(np.all(weights >= -1e-12)))

    def test_truncated_constraint_holds_after_calibration(self) -> None:
        probs = np.asarray(self.probabilities)
        calibrated_probs = pe.calibrate_matrix(probs, self.result.intercept, self.result.slope)
        mixed = calibrated_probs @ np.asarray(self.result.weights)[: probs.shape[1]]
        self.assertLessEqual(float(mixed.max()), 0.5 + 1e-9)

    def test_fitted_mean_demand_reproduces_observed_mean(self) -> None:
        probs = np.asarray(self.probabilities)
        calibrated_probs = pe.calibrate_matrix(probs, self.result.intercept, self.result.slope)
        fitted_mean = float(
            np.mean(self.result.exposure * (calibrated_probs @ np.asarray(self.result.weights)[: probs.shape[1]]))
        )
        observed_mean = float(np.mean([o.demand for o in self.observations]))
        self.assertLess(abs(fitted_mean - observed_mean) / observed_mean, 0.05)

    def test_calibrated_objective_is_no_worse_than_plain_fit(self) -> None:
        plain = pe.fit(self.observations, self.probabilities, n_max=60, truncated=True)
        self.assertLessEqual(self.result.value, plain.value + 1e-3)

    def test_reported_value_matches_recomputed_calibration_objective(self) -> None:
        probs = np.asarray(self.probabilities)
        demands = np.asarray([o.demand for o in self.observations], dtype=float)
        recomputed, _ = pe.calibration_objective_and_gradient(
            probs, demands, self.result.exposure, np.asarray(self.result.weights),
            self.result.intercept, self.result.slope, truncated=True,
        )
        self.assertAlmostEqual(self.result.value, recomputed, places=8)

    def test_exposures_below_max_demand_are_skipped(self) -> None:
        demand_max = max(o.demand for o in self.observations)
        self.assertTrue(all(p.value == math.inf for p in self.result.grid if p.exposure < demand_max))


class MultiStartTest(unittest.TestCase):
    def test_random_starts_do_not_worsen_objective(self) -> None:
        observations, probabilities = truncated_synthetic()
        single = pe.fit(observations, probabilities, n_max=50, truncated=True)
        multi = pe.fit(observations, probabilities, n_max=50, truncated=True, n_random_starts=3)
        self.assertLessEqual(multi.value, single.value + 1e-9)

    def test_random_starts_are_deterministic(self) -> None:
        observations, probabilities = truncated_synthetic()
        first = pe.fit(observations, probabilities, n_max=40, truncated=True, n_random_starts=2)
        second = pe.fit(observations, probabilities, n_max=40, truncated=True, n_random_starts=2)
        self.assertEqual(first.exposure, second.exposure)
        self.assertAlmostEqual(first.value, second.value, places=10)


class Lemma2_1Test(unittest.TestCase):
    """논문 Lemma 2.1 — (2.5)의 f가 (2.6)의 절단 우도와 등가이고 q ∈ (0, 1/2] 에서 볼록하다는 검증."""

    @staticmethod
    def _f(demand: int, exposure: int, q: float) -> float:
        return (
            -demand * math.log(q)
            - (exposure - demand) * math.log1p(-q)
            + math.log1p(-math.pow(1.0 - q, exposure))
        )

    @staticmethod
    def _f_second_difference(demand: int, exposure: int, q: float, h: float = 1e-5) -> float:
        up = Lemma2_1Test._f(demand, exposure, q + h)
        mid = Lemma2_1Test._f(demand, exposure, q)
        down = Lemma2_1Test._f(demand, exposure, q - h)
        return (up - 2.0 * mid + down) / (h * h)

    def test_truncation_identity(self) -> None:
        for exposure in (2, 5, 17, 64, 200):
            for demand in range(1, exposure + 1):
                for q in (0.02, 0.1, 0.3, 0.5, 0.8, 0.99):
                    pmf = (
                        math.exp(pe._log_binomial(exposure, np.array([demand], dtype=float))[0])
                        * q**demand
                        * (1.0 - q) ** (exposure - demand)
                    )
                    if pmf < 1e-240:
                        continue  # 서브노멀 근처 pmf는 상대정밀도가 무너지므로 직접 대조에서 제외한다
                    conditional = -math.log(pmf / (1.0 - (1.0 - q) ** exposure))
                    equivalent = self._f(demand, exposure, q) - pe._log_binomial(
                        exposure, np.array([demand], dtype=float)
                    )[0]
                    self.assertAlmostEqual(conditional, equivalent, places=8)

    def test_f_is_convex_up_to_half(self) -> None:
        rng = np.random.default_rng(7)
        for exposure in (3, 8, 21, 55, 120):
            for demand in range(1, exposure + 1):
                for _ in range(20):
                    a, b = sorted(rng.uniform(1e-4, 0.5, size=2))
                    lam = float(rng.uniform(0.0, 1.0))
                    mid = lam * a + (1.0 - lam) * b
                    chord = lam * self._f(demand, exposure, a) + (1.0 - lam) * self._f(demand, exposure, b)
                    self.assertLessEqual(self._f(demand, exposure, mid), chord + 1e-9)

    def test_second_derivative_is_nonnegative_below_half(self) -> None:
        for exposure in (4, 12, 40, 100):
            for demand in range(1, exposure + 1):
                for q in (0.01, 0.1, 0.25, 0.45):
                    self.assertGreaterEqual(self._f_second_difference(demand, exposure, q), -1e-6)


class ValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.observations, self.probabilities = synthetic(product_count=3)

    def test_empty_observations(self) -> None:
        with self.assertRaisesRegex(ValueError, 'OBSERVATIONS_EMPTY'):
            pe.fit([], [], n_max=10, truncated=False)

    def test_row_count_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, 'PERSONA_PROBABILITY_ROWS_MISMATCH'):
            pe.fit(self.observations, self.probabilities[:-1], n_max=80, truncated=False)

    def test_ragged_probability_rows(self) -> None:
        broken = [row[:] for row in self.probabilities]
        broken[0] = broken[0][:-1]
        with self.assertRaisesRegex(ValueError, 'PERSONA_PROBABILITY_ROW_LENGTH_MISMATCH'):
            pe.fit(self.observations, broken, n_max=80, truncated=False)

    def test_probability_out_of_range(self) -> None:
        broken = [row[:] for row in self.probabilities]
        broken[0][0] = 1.4
        with self.assertRaisesRegex(ValueError, 'PURCHASE_PROBABILITY_OUT_OF_RANGE'):
            pe.fit(self.observations, broken, n_max=80, truncated=False)

    def test_negative_demand(self) -> None:
        broken = list(self.observations)
        broken[0] = pe.Observation(broken[0].product, broken[0].price, -1)
        with self.assertRaisesRegex(ValueError, 'DEMAND_NEGATIVE'):
            pe.fit(broken, self.probabilities, n_max=80, truncated=False)

    def test_non_integer_demand(self) -> None:
        broken = list(self.observations)
        broken[0] = pe.Observation(broken[0].product, broken[0].price, 3.5)
        with self.assertRaisesRegex(ValueError, 'DEMAND_NOT_INTEGER'):
            pe.fit(broken, self.probabilities, n_max=80, truncated=False)

    def test_truncated_mode_rejects_zero_demand(self) -> None:
        broken = list(self.observations)
        broken[0] = pe.Observation(broken[0].product, broken[0].price, 0)
        with self.assertRaisesRegex(ValueError, 'TRUNCATED_DEMAND_MUST_BE_POSITIVE'):
            pe.fit(broken, self.probabilities, n_max=80, truncated=True)

    def test_rejects_n_max_below_max_demand(self) -> None:
        with self.assertRaisesRegex(ValueError, 'N_MAX_TOO_SMALL_FOR_MAX_DEMAND'):
            pe.fit(self.observations, self.probabilities, n_max=3, truncated=False)

    def test_predict_probability_matches_mixture(self) -> None:
        self.assertAlmostEqual(pe.predict_probability([0.5, 0.3, 0.1, 0.1], [0.2, 0.1, 0.4]), 0.5 * 0.2 + 0.3 * 0.1 + 0.1 * 0.4)


if __name__ == '__main__':
    unittest.main()
