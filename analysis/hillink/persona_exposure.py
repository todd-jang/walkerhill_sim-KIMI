"""
Algorithm 1 — 페르소나 가중치·노출 최적화 (논문 재현)

  LLM이 낸 페르소나별 구매확률 q(x_k, u, p)와 관측된 상품·가격·수요를 받아
  혼합비중 α(페르소나 K명 + 절대 안 사는 더미 하나)와 노출 인원 N을 최대우도로 맞춘다.
  N = 1..N_max 마다 α를 풀어 V(N)을 얻고 argmin V(N)을 고른다.

논문: Huang & Wang, "LLM-Powered Virtual Population for Demand Simulation and Pricing"
      (arXiv 2606.16183) — 식 번호는 그대로 따른다.

  (2.1) 수요 ~ Binomial(N, q_α(u,p)),   q_α(u,p) = Σ_k α_k q(x_k,u,p),   α ∈ Δ^K (α_{K+1} = 더미)
  (2.2) 캘리브레이션 모형 q^cal = σ(a + b·logit q) — LLM 확률의 로짓 스케일 보정 (a, b):
          min_{α∈Δ^K, N, (a,b)}  -Σ_{j,i} log ℓ(d_{j,i} | N, q^cal_α(u_j,p_{j,i}))
          (a,b)가 고정되면 α 문제는 (2.3)/(2.6)과 같고, α가 고정되면 (a,b) 2변수 문제라
          저자 구현(llm_mix_cal.py)과 같은 교대 최적화로 푼다. 초기화: b=1, a = logit(mean d/N) - logit(mean q).
  (2.3) 수요가 항상 관측되면 완전 이항 우도:
          min_{α∈Δ^K, N∈Z+}  -Σ_{j,i} log ℓ(d_{j,i} | N, q_α(u_j,p_{j,i}))
  (2.5) f(d|N,q) = -d log q - (N-d) log(1-q) + log[1-(1-q)^N]
  (2.6) 0 수요가 잘린(truncated) 자료면 양의 수요 조건부 우도, N마다 볼록:
          min_{α∈Δ^K}  Σ_{j,i} [f(d_{j,i}|N,q_α) - log C(N,d_{j,i})]
          s.t. q_α(u_j,p_{j,i}) ≤ 1/2

  python3 persona_exposure.py --selftest                     # 합성 데이터로 복원 확인
  python3 persona_exposure.py --obs obs.csv --probs probs.csv --n-max 200
  python3 persona_exposure.py --obs obs.csv --probs probs.csv --full --json
  python3 persona_exposure.py --obs obs.csv --probs probs.csv --calibrated --random-starts 3

입력 CSV
  obs.csv    헤더 product,price,demand  (수요는 정수. truncated 모드면 전부 > 0)
  probs.csv  첫 줄이 페르소나 이름 K개, 이후 줄이 obs.csv와 같은 순서의 q(x_k, u, p)

수치 방법
  α는 심플렉스 위 볼록 문제라 투영 경사법(정렬 기반 심플렉스 투영 + Armijo 후퇴)으로 푼다.
  (2.6)의 q ≤ 1/2 제약은 로그 배리어로 처리하고 μ를 1e-2 → 1e-7로 낮춰가며 이어 푼다.
  n_random_starts > 0 이면 Dirichlet 랜덤 시작점(시드 고정)을 추가로 풀어 최댓값만 남긴다 —
  평탄한 우도면에서 시작점에 갇히는 것을 줄인다(저자 구현의 멀티스타트에 대응).
  캘리브레이션 (a, b)는 박스 제약(|a| ≤ 20, |log b| ≤ 5) 위 투영 경사법으로 푼다.
  N < max(d)는 이항 지지집합 밖이라 V(N) = inf 로 건너뛴다.

읽을 때 주의 (구현이 아니라 모형의 성질)
  q가 작으면 이항이 푸아송에 가까워져 (N, α)의 스케일이 거의 식별되지 않는다.
  그래서 N̂ 하나만 보고하지 말고 profile_band()로 V 최솟값 근처의 N 구간을 같이 본다.
  반대로 α가 비례 관계인 페르소나끼리는 애초에 구분되지 않으니(설계 행렬의 공선성),
  페르소나별 가격 민감도 모양이 서로 달라야 가중치가 의미를 갖는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

PROB_FLOOR = 1e-9
BARRIER_SCHEDULE = (1e-2, 1e-4, 1e-7)
GRADIENT_CLIP = 1e9
MULTISTART_SEED = 17  # 저자 구현(llm_mix.py)의 랜덤 시작점 시드와 같게 고정
CALIBRATION_LOGIT_CLIP = 1e-6
CALIBRATION_BOUNDS = (20.0, 5.0)  # |intercept|, |log slope|
CALIBRATION_ITERS_DEFAULT = 6


@dataclass(frozen=True)
class Observation:
    """상품 하나의 관측 한 건 — 상품·가격·실현 수요 (논문의 (u_j, p_{j,i}, d_{j,i}))."""

    product: str
    price: float
    demand: int


@dataclass(frozen=True)
class GridPoint:
    """고정 N에서의 해 — α̂^(N), V^(N), 수렴 여부, 최대 구매확률."""

    exposure: int
    weights: tuple[float, ...]
    value: float
    converged: bool
    max_probability: float


@dataclass(frozen=True)
class FitResult:
    """Algorithm 1의 출력 (N̂, α̂)와 N별 격자."""

    exposure: int
    weights: tuple[float, ...]
    value: float
    grid: tuple[GridPoint, ...]


@dataclass(frozen=True)
class CalibratedGridPoint:
    """고정 N에서 캘리브레이션 교대 최적화의 해 — (α̂, â, b̂), V^(N)."""

    exposure: int
    weights: tuple[float, ...]
    intercept: float
    slope: float
    value: float
    converged: bool


@dataclass(frozen=True)
class CalibratedFitResult:
    """캘리브레이션 모형(2.2)의 Algorithm 1 출력 — (N̂, α̂, â, b̂)와 N별 격자."""

    exposure: int
    weights: tuple[float, ...]
    intercept: float
    slope: float
    value: float
    grid: tuple[CalibratedGridPoint, ...]


def fit(
    observations: Sequence[Observation],
    persona_probabilities: Sequence[Sequence[float]],
    *,
    n_max: int,
    truncated: bool = True,
    tolerance: float = 1e-5,
    max_iter: int = 800,
    n_random_starts: int = 0,
    n_step: int = 1,
) -> FitResult:
    """Algorithm 1 — N을 1..n_max로 훑어 α̂^(N)와 V^(N)을 얻고 argmin V(N)을 고른다.

    truncated=True 면 (2.6), False 면 (2.3)을 푼다. 수요 d는 이항 지지집합 때문에 N ≥ max(d)가 필요하다.
    tolerance는 단위 스텝 투영 경사도의 최대 절댓값(정류점 판정)이다.
    n_random_starts > 0 이면 N마다 Dirichlet 랜덤 시작점을 추가로 풀어 최솟값 V를 남긴다.
    n_step > 1 이면 N 격자를 max(d)부터 n_step 간격으로 건너뛰며 푼다(실데이터처럼 N이 클 때).
    """
    probs, demands, persona_count = _prepare(observations, persona_probabilities, n_max=n_max, truncated=truncated)
    demand_max = int(demands.max())
    if n_step < 1:
        raise ValueError('N_STEP_MUST_BE_POSITIVE')
    solve_targets = (
        None
        if n_step == 1
        else frozenset(range(demand_max, n_max + 1, n_step)) | {demand_max}
    )

    grid: list[GridPoint] = []
    best: GridPoint | None = None
    previous_weights: np.ndarray | None = None
    for exposure in range(1, n_max + 1):
        if exposure < demand_max or (solve_targets is not None and exposure not in solve_targets):
            grid.append(GridPoint(exposure, (), math.inf, False, 0.0))
            continue
        weights, value, converged, max_probability = _solve_for_exposure(
            probs,
            demands,
            exposure,
            truncated=truncated,
            tolerance=tolerance,
            max_iter=max_iter,
            initial=previous_weights,
            n_random_starts=n_random_starts,
        )
        previous_weights = weights
        point = GridPoint(exposure, tuple(float(w) for w in weights), float(value), converged, max_probability)
        grid.append(point)
        if best is None or point.value < best.value:
            best = point

    assert best is not None  # n_max ≥ demand_max 이므로 최소 한 점은 유한하다
    return FitResult(best.exposure, best.weights, best.value, tuple(grid))


def profile_band(result: FitResult, delta: float = 1e-3) -> tuple[int, ...]:
    """V(N)이 최솟값 + delta 안에 드는 N들 — (N, α) 스케일이 평탄할 때 N̂ 하나를 보완한다."""
    threshold = result.value + delta * max(1.0, abs(result.value))
    return tuple(point.exposure for point in result.grid if point.value <= threshold)



def predict_probability(weights: Sequence[float], persona_probability_row: Sequence[float]) -> float:
    """q_α(u,p) = Σ_k α_k q(x_k,u,p). 마지막 α는 절대 안 사는 더미라 곱하는 확률이 0이다."""
    active = np.asarray(persona_probability_row, dtype=float)
    if len(weights) != len(active) + 1:
        raise ValueError(f'WEIGHT_LENGTH_MISMATCH:{len(weights)}!={len(active) + 1}')
    return float(np.dot(np.asarray(weights, dtype=float)[: len(active)], active))


def _prepare(
    observations: Sequence[Observation],
    persona_probabilities: Sequence[Sequence[float]],
    *,
    n_max: int,
    truncated: bool,
) -> tuple[np.ndarray, np.ndarray, int]:
    if not observations:
        raise ValueError('OBSERVATIONS_EMPTY')
    if len(persona_probabilities) != len(observations):
        raise ValueError(
            f'PERSONA_PROBABILITY_ROWS_MISMATCH:{len(persona_probabilities)}!={len(observations)}'
        )
    widths = {len(row) for row in persona_probabilities}
    if len(widths) != 1:
        raise ValueError('PERSONA_PROBABILITY_ROW_LENGTH_MISMATCH')
    persona_count = widths.pop()
    if persona_count < 1:
        raise ValueError('PERSONA_PROBABILITY_COLUMNS_EMPTY')

    try:
        probs = np.asarray(persona_probabilities, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError('PERSONA_PROBABILITY_NOT_NUMERIC') from exc
    if not np.all(np.isfinite(probs)) or probs.min() < 0.0 or probs.max() > 1.0:
        raise ValueError('PURCHASE_PROBABILITY_OUT_OF_RANGE')

    demands_raw = [observation.demand for observation in observations]
    for demand in demands_raw:
        if isinstance(demand, bool) or not isinstance(demand, int):
            raise ValueError(f'DEMAND_NOT_INTEGER:{demand!r}')
        if demand < 0:
            raise ValueError(f'DEMAND_NEGATIVE:{demand}')
    if truncated and any(demand == 0 for demand in demands_raw):
        raise ValueError('TRUNCATED_DEMAND_MUST_BE_POSITIVE')

    demands = np.asarray(demands_raw, dtype=float)
    if n_max < 1:
        raise ValueError('MAX_EXPOSURE_MUST_BE_POSITIVE')
    if n_max < int(demands.max()):
        raise ValueError(f'N_MAX_TOO_SMALL_FOR_MAX_DEMAND:{n_max}<{int(demands.max())}')
    return probs, demands, persona_count


def _log_binomial(exposure: int, demands: np.ndarray) -> np.ndarray:
    """log C(N, d) — N이 바뀌면 값도 바뀌므로 V(N) 비교에 반드시 포함한다."""
    return np.array(
        [
            math.lgamma(exposure + 1) - math.lgamma(int(demand) + 1) - math.lgamma(exposure - int(demand) + 1)
            for demand in demands
        ],
        dtype=float,
    )


def objective_and_gradient(
    probs: np.ndarray,
    demands: np.ndarray,
    exposure: int,
    weights: np.ndarray,
    *,
    truncated: bool,
    barrier: float = 0.0,
    log_comb_sum: float | None = None,
) -> tuple[float, np.ndarray]:
    """고정 N의 목적함수(2.3 또는 2.6)와 α에 대한 그래디언트. 배리어는 (2.6)의 q ≤ 1/2 처리용.

    log_comb_sum은 Σ log C(N,d) — N마다 한 번만 계산해 넘기면 목적함수 평가가 싸진다.
    """
    persona_count = probs.shape[1]
    if log_comb_sum is None:
        log_comb_sum = float(_log_binomial(exposure, demands).sum())
    probability = np.clip(probs @ weights[:persona_count], PROB_FLOOR, 1.0 - PROB_FLOOR)
    residual = exposure - demands
    if truncated:
        # f(d|N,q) - log C(N,d),  f = -d log q - (N-d) log(1-q) + log[1-(1-q)^N]
        log_survival = np.log1p(-np.power(1.0 - probability, exposure))
        value = (
            -demands * np.log(probability)
            - residual * np.log1p(-probability)
            + log_survival
        ).sum() - log_comb_sum
        # log[1-(1-q)^N] 의 q 미분 = N(1-q)^{N-1} / [1-(1-q)^N]
        survival = -np.expm1(exposure * np.log1p(-probability))
        gradient_q = (
            -demands / probability
            + residual / (1.0 - probability)
            + exposure * np.power(1.0 - probability, exposure - 1) / survival
        )
    else:
        value = -(demands * np.log(probability) + residual * np.log1p(-probability)).sum() - log_comb_sum
        gradient_q = -demands / probability + residual / (1.0 - probability)
    if barrier > 0.0:
        gap = 0.5 - probability
        if np.any(gap <= 0.0):
            # 배리어 정의역(q < 1/2) 밖 — 후보를 거부하도록 비유한 목적값을 돌려준다
            return math.inf, np.zeros(persona_count + 1)
        value += -barrier * np.log(gap).sum()
        gradient_q = gradient_q + barrier / gap
    gradient = np.empty(persona_count + 1)
    gradient[:persona_count] = probs.T @ gradient_q
    gradient[persona_count] = 0.0  # 더미 페르소나는 어떤 관측에도 확률 0을 곱한다
    return float(value), np.clip(gradient, -GRADIENT_CLIP, GRADIENT_CLIP)


def _solve_for_exposure(
    probs: np.ndarray,
    demands: np.ndarray,
    exposure: int,
    *,
    truncated: bool,
    tolerance: float,
    max_iter: int,
    initial: np.ndarray | None = None,
    n_random_starts: int = 0,
) -> tuple[np.ndarray, float, bool, float]:
    """고정 N의 α 문제를 푼다 — 시작점(warm start) 하나 + 옵션 Dirichlet 랜덤 시작점들, 최솟값 V를 남긴다."""
    persona_count = probs.shape[1]
    log_comb_sum = float(_log_binomial(exposure, demands).sum())

    def objective(weights: np.ndarray, barrier: float) -> tuple[float, np.ndarray]:
        return objective_and_gradient(
            probs,
            demands,
            exposure,
            weights,
            truncated=truncated,
            barrier=barrier,
            log_comb_sum=log_comb_sum,
        )

    primary = _initial_weights(probs, exposure, truncated=truncated)
    if initial is not None:
        candidate = np.asarray(initial, dtype=float)
        feasible = not truncated or float(np.max(probs @ candidate[:persona_count])) < 0.5
        if feasible and len(candidate) == persona_count + 1:
            primary = candidate.copy()
    starts = [primary]
    if n_random_starts > 0:
        rng = np.random.default_rng(MULTISTART_SEED)
        for _ in range(n_random_starts):
            draw = rng.dirichlet(np.ones(persona_count + 1))
            starts.append(_feasible_start(probs, draw, truncated=truncated))

    best: tuple[np.ndarray, float, bool, float] | None = None
    for start in starts:
        solved = _solve_from_start(
            probs,
            demands,
            exposure,
            objective,
            start,
            truncated=truncated,
            tolerance=tolerance,
            max_iter=max_iter,
            persona_count=persona_count,
        )
        if best is None or solved[1] < best[1]:
            best = solved
    assert best is not None
    return best


def _solve_from_start(
    probs: np.ndarray,
    demands: np.ndarray,
    exposure: int,
    objective: Callable[[np.ndarray, float], tuple[float, np.ndarray]],
    start: np.ndarray,
    *,
    truncated: bool,
    tolerance: float,
    max_iter: int,
    persona_count: int,
) -> tuple[np.ndarray, float, bool, float]:
    """한 시작점에서 α를 푼다 — 제약 없이 먼저 풀고 q ≤ 1/2 위반이면 배리어 연속법으로 이어 푼다."""
    weights = start.copy()
    converged = True
    if truncated:
        # 제약이 풀리지 않는 경우가 대부분이라 먼저 제약 없이 풀고, q ≤ 1/2 를 지키는지 본다
        free_weights, _, free_converged = _projected_gradient(
            lambda w: objective(w, 0.0), weights, tolerance, max_iter
        )
        if float(np.max(probs @ free_weights[:persona_count])) < 0.5 - 1e-9:
            return _finish(probs, free_weights, persona_count, objective, free_converged)
        # 제약이 걸리면 배리어로 q < 1/2 내부를 유지하며 μ를 낮춰 (2.6) 해로 수렴
        weights = _initial_weights(probs, exposure, truncated=True)
        for barrier in BARRIER_SCHEDULE:
            weights, _, step_converged = _projected_gradient(
                lambda w, mu=barrier: objective(w, mu), weights, tolerance, max_iter
            )
            converged = converged and step_converged
    else:
        weights, _, converged = _projected_gradient(lambda w: objective(w, 0.0), weights, tolerance, max_iter)

    return _finish(probs, weights, persona_count, objective, converged)


def _feasible_start(probs: np.ndarray, weights: np.ndarray, *, truncated: bool) -> np.ndarray:
    """랜덤 시작점을 정규화하고, truncated 면 저자 구현(_normalize_alpha)처럼 q ≤ 1/2 로 재조정한다."""
    out = np.maximum(np.asarray(weights, dtype=float).ravel(), 0.0)
    total = out.sum()
    out = out / total if total > 0 else _initial_weights(probs, 0, truncated=False)
    if not truncated:
        return out
    persona_count = probs.shape[1]
    while float(np.max(probs @ out[:persona_count])) >= 0.5 * (1.0 - 1e-12):
        out[:persona_count] *= 0.5
        out[persona_count] = 1.0 - out[:persona_count].sum()
        if out[persona_count] >= 1.0 - 1e-15:
            return _initial_weights(probs, 0, truncated=True)
    return out


def _finish(
    probs: np.ndarray,
    weights: np.ndarray,
    persona_count: int,
    objective: Callable[[np.ndarray, float], tuple[float, np.ndarray]],
    converged: bool,
) -> tuple[np.ndarray, float, bool, float]:
    value, _ = objective(weights, 0.0)
    max_probability = float(np.max(probs @ weights[:persona_count]))
    return weights, value, converged, max_probability


def _initial_weights(probs: np.ndarray, exposure: int, *, truncated: bool) -> np.ndarray:
    persona_count = probs.shape[1]
    weights = np.full(persona_count + 1, 1.0 / (persona_count + 1))
    if not truncated:
        return weights
    # q ≤ 1/2 를 처음부터 만족시키려면 활성 페르소나 질량을 더미로 옮긴다
    while float(np.max(probs @ weights[:persona_count])) >= 0.5 * (1.0 - 1e-12):
        weights[:persona_count] *= 0.5
        weights[persona_count] = 1.0 - weights[:persona_count].sum()
        if weights[persona_count] >= 1.0 - 1e-15:
            raise ValueError('TRUNCATED_CONSTRAINT_INFEASIBLE')
    return weights


def _project_simplex(vector: np.ndarray) -> np.ndarray:
    """유클리드 투영 — 정렬 기반으로 Δ 위로 내린다."""
    count = vector.size
    ordered = np.sort(vector)[::-1]
    cumulative = np.cumsum(ordered) - 1.0
    indices = np.arange(1, count + 1)
    active = ordered - cumulative / indices > 0.0
    rho = indices[active][-1]
    return np.maximum(vector - cumulative[rho - 1] / rho, 0.0)


def _projected_gradient(
    objective: Callable[[np.ndarray], tuple[float, np.ndarray]],
    weights: np.ndarray,
    tolerance: float,
    max_iter: int,
) -> tuple[np.ndarray, float, bool]:
    weights = weights.astype(float, copy=True)
    value, gradient = objective(weights)
    step = 1.0 / max(1.0, float(np.max(np.abs(gradient))))
    for _ in range(max_iter):
        projected = _project_simplex(weights - gradient)
        if float(np.max(np.abs(weights - projected))) <= tolerance:
            return weights, value, True
        for _ in range(60):
            candidate = _project_simplex(weights - step * gradient)
            candidate_value, candidate_gradient = objective(candidate)
            displacement = float(np.dot(gradient, weights - candidate))
            if np.isfinite(candidate_value) and value - candidate_value >= 1e-4 * step * displacement:
                weights, value, gradient = candidate, candidate_value, candidate_gradient
                step = min(step * 1.5, 1e5)
                break
            step *= 0.5
        else:
            return weights, value, False
    projected = _project_simplex(weights - gradient)
    return weights, value, float(np.max(np.abs(weights - projected))) <= tolerance


def calibrate_matrix(probs: np.ndarray, intercept: float, slope: float) -> np.ndarray:
    """(2.2) q^cal = σ(a + b·logit q) — 저자 구현(calibrate_probability_matrix)과 같은 클리핑을 쓴다."""
    clipped = np.clip(np.asarray(probs, dtype=float), CALIBRATION_LOGIT_CLIP, 1.0 - CALIBRATION_LOGIT_CLIP)
    logits = np.log(clipped) - np.log1p(-clipped)
    return np.clip(_stable_expit(intercept + slope * logits), PROB_FLOOR, 1.0 - PROB_FLOOR)


def _stable_expit(z: np.ndarray) -> np.ndarray:
    """오버플로 없는 시그모이드 — z의 부호에 따라 분기한다."""
    z = np.asarray(z, dtype=float)
    out = np.empty_like(z)
    positive = z >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-z[positive]))
    exp_negative = np.exp(z[~positive])
    out[~positive] = exp_negative / (1.0 + exp_negative)
    return out


def _logit_matrix(probs: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(probs, dtype=float), CALIBRATION_LOGIT_CLIP, 1.0 - CALIBRATION_LOGIT_CLIP)
    return np.log(clipped) - np.log1p(-clipped)


def calibration_objective_and_gradient(
    probs: np.ndarray,
    demands: np.ndarray,
    exposure: int,
    weights: np.ndarray,
    intercept: float,
    slope: float,
    *,
    truncated: bool,
    log_comb_sum: float | None = None,
) -> tuple[float, np.ndarray]:
    """고정 α에서 캘리브레이션 (a, b)의 목적함수 V와 [∂V/∂a, ∂V/∂log b] 그래디언트.

    truncated 면 q_α를 [1e-9, 1/2]로 클립하고 경계에서 그래디언트를 0으로 만든다(저자 구현과 동일).
    """
    if log_comb_sum is None:
        log_comb_sum = float(_log_binomial(exposure, demands).sum())
    persona_count = probs.shape[1]
    logits = _logit_matrix(probs)
    z = intercept + slope * logits
    calibrated_raw = _stable_expit(z)
    calibrated = np.clip(calibrated_raw, PROB_FLOOR, 1.0 - PROB_FLOOR)
    interior = (calibrated_raw > PROB_FLOOR) & (calibrated_raw < 1.0 - PROB_FLOOR)
    sigma_prime = calibrated_raw * (1.0 - calibrated_raw) * interior

    mixed_raw = calibrated @ weights[:persona_count]
    if truncated:
        upper = 0.5
    else:
        upper = 1.0 - PROB_FLOOR
    probability = np.clip(mixed_raw, PROB_FLOOR, upper)
    at_bound = (mixed_raw <= PROB_FLOOR) | (mixed_raw >= upper)
    residual = exposure - demands
    if truncated:
        log_survival = np.log1p(-np.power(1.0 - probability, exposure))
        value = (
            -demands * np.log(probability)
            - residual * np.log1p(-probability)
            + log_survival
        ).sum() - log_comb_sum
        survival = -np.expm1(exposure * np.log1p(-probability))
        gradient_q = (
            -demands / probability
            + residual / (1.0 - probability)
            + exposure * np.power(1.0 - probability, exposure - 1) / survival
        )
    else:
        value = -(demands * np.log(probability) + residual * np.log1p(-probability)).sum() - log_comb_sum
        gradient_q = -demands / probability + residual / (1.0 - probability)
    gradient_q = np.where(at_bound, 0.0, gradient_q)

    # dV/dq^cal_{r,k} = gradient_q[r] · α_k 를 (a, log b)로 사슬 규칙
    gradient_calibrated = gradient_q[:, None] * weights[:persona_count][None, :]
    gradient_a = float(np.sum(gradient_calibrated * sigma_prime))
    gradient_log_b = float(np.sum(gradient_calibrated * sigma_prime * logits)) * slope
    return float(value), np.array([gradient_a, gradient_log_b])


def _initial_calibration(probs: np.ndarray, demands: np.ndarray, exposure: int) -> tuple[float, float]:
    """b=1로 두고 a로 평균을 맞춘다 — 저자 구현(_initial_calibration)과 동일."""
    raw_mean = float(np.mean(np.clip(probs, CALIBRATION_LOGIT_CLIP, 1.0 - CALIBRATION_LOGIT_CLIP)))
    target = float(np.clip(np.mean(demands) / exposure, CALIBRATION_LOGIT_CLIP, 1.0 - CALIBRATION_LOGIT_CLIP))
    raw_logit = math.log(raw_mean / (1.0 - raw_mean))
    target_logit = math.log(target / (1.0 - target))
    return target_logit - raw_logit, 1.0


def _project_box(params: np.ndarray) -> np.ndarray:
    intercept_bound, slope_bound = CALIBRATION_BOUNDS
    return np.clip(params, [-intercept_bound, -slope_bound], [intercept_bound, slope_bound])


def _optimize_calibration(
    probs: np.ndarray,
    demands: np.ndarray,
    exposure: int,
    weights: np.ndarray,
    intercept: float,
    log_slope: float,
    *,
    truncated: bool,
    tolerance: float = 1e-6,
    max_iter: int = 200,
) -> tuple[np.ndarray, bool]:
    """고정 α에서 (a, log b)를 박스 제약 위 투영 경사법으로 푼다 — 저자 구현의 L-BFGS-B 대응."""
    log_comb_sum = float(_log_binomial(exposure, demands).sum())

    def objective(params: np.ndarray) -> tuple[float, np.ndarray]:
        return calibration_objective_and_gradient(
            probs,
            demands,
            exposure,
            weights,
            float(params[0]),
            math.exp(float(params[1])),
            truncated=truncated,
            log_comb_sum=log_comb_sum,
        )

    params = _project_box(np.array([intercept, log_slope], dtype=float))
    value, gradient = objective(params)
    step = 1.0 / max(1.0, float(np.max(np.abs(gradient))))
    for _ in range(max_iter):
        projected = _project_box(params - gradient)
        if float(np.max(np.abs(params - projected))) <= tolerance:
            return params, True
        for _ in range(60):
            candidate = _project_box(params - step * gradient)
            candidate_value, candidate_gradient = objective(candidate)
            displacement = float(np.dot(gradient, params - candidate))
            if np.isfinite(candidate_value) and value - candidate_value >= 1e-4 * step * displacement:
                params, value, gradient = candidate, candidate_value, candidate_gradient
                step = min(step * 1.5, 1e5)
                break
            step *= 0.5
        else:
            return params, False
    projected = _project_box(params - gradient)
    return params, float(np.max(np.abs(params - projected))) <= tolerance


def fit_calibrated(
    observations: Sequence[Observation],
    persona_probabilities: Sequence[Sequence[float]],
    *,
    n_max: int,
    truncated: bool = True,
    tolerance: float = 1e-5,
    max_iter: int = 800,
    calibration_iters: int = CALIBRATION_ITERS_DEFAULT,
    n_random_starts: int = 0,
    n_step: int = 1,
) -> CalibratedFitResult:
    """캘리브레이션 모형(2.2)의 Algorithm 1 — N마다 α와 (a, b)를 교대 최적화한 뒤 argmin V(N)을 고른다.

    교대 순서는 저자 구현(fit_llm_mix_cal)과 같다: (a,b) 고정 → α 적합 → α 고정 → (a,b) 적합 을
    calibration_iters번 반복하고, 마지막에 α를 한 번 더 적합해 q ≤ 1/2 제약을 닫는다.
    """
    probs, demands, _ = _prepare(observations, persona_probabilities, n_max=n_max, truncated=truncated)
    demand_max = int(demands.max())
    if n_step < 1:
        raise ValueError('N_STEP_MUST_BE_POSITIVE')
    solve_targets = (
        None
        if n_step == 1
        else frozenset(range(demand_max, n_max + 1, n_step)) | {demand_max}
    )

    grid: list[CalibratedGridPoint] = []
    best: CalibratedGridPoint | None = None
    for exposure in range(1, n_max + 1):
        if exposure < demand_max or (solve_targets is not None and exposure not in solve_targets):
            grid.append(CalibratedGridPoint(exposure, (), 0.0, 0.0, math.inf, False))
            continue
        intercept, slope = _initial_calibration(probs, demands, exposure)
        weights: np.ndarray | None = None
        converged = True
        for _ in range(calibration_iters):
            calibrated = calibrate_matrix(probs, intercept, slope)
            weights, _, step_converged, _ = _solve_for_exposure(
                calibrated,
                demands,
                exposure,
                truncated=truncated,
                tolerance=tolerance,
                max_iter=max_iter,
                initial=weights,
                n_random_starts=n_random_starts,
            )
            converged = converged and step_converged
            params, param_converged = _optimize_calibration(
                probs,
                demands,
                exposure,
                weights,
                intercept,
                math.log(slope),
                truncated=truncated,
            )
            intercept, slope = float(params[0]), math.exp(float(params[1]))
            converged = converged and param_converged
        calibrated = calibrate_matrix(probs, intercept, slope)
        weights, value, final_converged, _ = _solve_for_exposure(
            calibrated,
            demands,
            exposure,
            truncated=truncated,
            tolerance=tolerance,
            max_iter=max_iter,
            initial=weights,
            n_random_starts=n_random_starts,
        )
        point = CalibratedGridPoint(
            exposure,
            tuple(float(w) for w in weights),
            intercept,
            slope,
            float(value),
            converged and final_converged,
        )
        grid.append(point)
        if best is None or point.value < best.value:
            best = point

    assert best is not None  # n_max ≥ demand_max 이므로 최소 한 점은 유한하다
    return CalibratedFitResult(
        best.exposure,
        best.weights,
        best.intercept,
        best.slope,
        best.value,
        tuple(grid),
    )


def _read_csv_rows(path: Path) -> list[list[str]]:
    with path.open(newline='', encoding='utf-8') as handle:
        return [row for row in csv.reader(handle) if row and any(cell.strip() for cell in row)]


def read_observations(path: Path) -> list[Observation]:
    rows = _read_csv_rows(path)
    if not rows:
        raise ValueError(f'OBSERVATIONS_CSV_EMPTY:{path}')
    header = [cell.strip().lower() for cell in rows[0]]
    if {'product', 'price', 'demand'} <= set(header):
        columns = {name: header.index(name) for name in ('product', 'price', 'demand')}
        body = rows[1:]
    else:
        if len(rows[0]) != 3:
            raise ValueError(f'OBSERVATIONS_CSV_HEADER_MISSING:{path}')
        columns = {'product': 0, 'price': 1, 'demand': 2}
        body = rows
    observations = []
    for row in body:
        demand_text = row[columns['demand']].strip()
        try:
            demand = int(demand_text)
        except ValueError as exc:
            raise ValueError(f'DEMAND_NOT_INTEGER:{demand_text!r}') from exc
        observations.append(
            Observation(row[columns['product']].strip(), float(row[columns['price']]), demand)
        )
    return observations


def read_persona_probabilities(path: Path) -> tuple[list[str], list[list[float]]]:
    rows = _read_csv_rows(path)
    if len(rows) < 2:
        raise ValueError(f'PERSONA_PROBABILITY_CSV_TOO_SHORT:{path}')
    names = [cell.strip() for cell in rows[0]]
    values = [[float(cell) for cell in row] for row in rows[1:]]
    return names, values


def _selftest() -> int:
    """합성 데이터로 동작을 확인한다 — 복원 정확도가 아니라 최적성·제약·평균수요 재현을 본다.

    (N, α) 스케일이 거의 평탄해서 N̂이 참값과 다르게 나오는 것이 정상이다. profile_band로 그 폭을 같이 본다.
    """
    rng = np.random.default_rng(20260615)
    prices = [120_000, 150_000, 180_000, 210_000, 240_000, 270_000, 300_000, 330_000]
    truth_weights = np.array([0.42, 0.27, 0.18, 0.13])  # 페르소나 3명 + 더미
    truth_exposure = 64
    personas = np.array(
        [
            [0.32, 0.28, 0.24, 0.19, 0.15, 0.11, 0.08, 0.06],
            [0.22, 0.20, 0.18, 0.16, 0.14, 0.12, 0.10, 0.08],
            [0.10, 0.09, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03],
        ]
    )
    observations: list[Observation] = []
    probabilities: list[list[float]] = []
    for product in range(12):
        for price_index, price in enumerate(prices):
            jitter = 1.0 + 0.05 * ((product * 7 + price_index) % 5 - 2) / 2.0
            row = np.round(personas[:, price_index] * jitter, 4).tolist()
            mean = float(np.dot(truth_weights[:3], row))
            observations.append(Observation(f'P{product:02d}', price, int(rng.binomial(truth_exposure, mean))))
            probabilities.append(row)

    kept = [(o, p) for o, p in zip(observations, probabilities) if o.demand > 0]
    cases = (
        ('(2.3) full', observations, probabilities, False),
        ('(2.6) truncated', [o for o, _ in kept], [p for _, p in kept], True),
    )
    for label, case_observations, case_probabilities, truncated in cases:
        result = fit(case_observations, case_probabilities, n_max=120, truncated=truncated)
        probs = np.asarray(case_probabilities)
        demands = np.asarray([o.demand for o in case_observations], dtype=float)
        weights = np.asarray(result.weights)
        fitted_mean = float(np.mean(result.exposure * (probs @ weights[: probs.shape[1]])))
        at_truth, _ = objective_and_gradient(probs, demands, truth_exposure, truth_weights, truncated=truncated)
        print(
            f'{label:16s} N̂={result.exposure:4d}  V={result.value:9.3f}  '
            f'평균수요 재현 {fitted_mean:6.2f} vs 관측 {demands.mean():6.2f}  '
            f'ΔV(참값 대비)={at_truth - result.value:+.3f}'
        )
        print(f'{"":16s} α̂={[round(float(w), 3) for w in weights]}')
        print(f'{"":16s} profile band(ΔV≤0.1%)={profile_band(result, delta=1e-3)}')

    calibrated = fit_calibrated(
        [o for o, _ in kept],
        [p for _, p in kept],
        n_max=120,
        truncated=True,
    )
    probs_kept = np.asarray([p for _, p in kept])
    demands_kept = np.asarray([o.demand for o, _ in kept], dtype=float)
    calibrated_probs = calibrate_matrix(probs_kept, calibrated.intercept, calibrated.slope)
    calibrated_weights = np.asarray(calibrated.weights)
    calibrated_mean = float(
        np.mean(calibrated.exposure * (calibrated_probs @ calibrated_weights[: probs_kept.shape[1]]))
    )
    print(
        f'{"(2.2) calibrated":16s} N̂={calibrated.exposure:4d}  V={calibrated.value:9.3f}  '
        f'평균수요 재현 {calibrated_mean:6.2f} vs 관측 {demands_kept.mean():6.2f}  '
        f'â={calibrated.intercept:+.3f} b̂={calibrated.slope:.3f}'
    )
    print(f'{"":16s} α̂={[round(float(w), 3) for w in calibrated_weights]}')
    print(f'{"":16s} profile band(ΔV≤0.1%)={profile_band(calibrated, delta=1e-3)}')
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Algorithm 1 — 페르소나 가중치·노출 최적화')
    parser.add_argument('--obs', type=Path, help='관측 CSV (product,price,demand)')
    parser.add_argument('--probs', type=Path, help='페르소나별 구매확률 CSV')
    parser.add_argument('--n-max', type=int, default=200, help='최대 노출 인원 N_max (기본 200)')
    parser.add_argument('--full', action='store_true', help='수요가 항상 관측된 자료 — (2.3)을 푼다')
    parser.add_argument('--calibrated', action='store_true', help='캘리브레이션 모형 (2.2) — (a,b)를 α와 교대 추정')
    parser.add_argument('--calibration-iters', type=int, default=CALIBRATION_ITERS_DEFAULT)
    parser.add_argument('--random-starts', type=int, default=0, help='N마다 추가 Dirichlet 랜덤 시작점 개수')
    parser.add_argument('--n-step', type=int, default=1, help='N 격자 간격 (실데이터처럼 N이 크면 > 1)')
    parser.add_argument('--max-iter', type=int, default=800)
    parser.add_argument('--tolerance', type=float, default=1e-5)
    parser.add_argument('--band-delta', type=float, default=1e-3, help='profile band 폭 (V 상대 증가 허용)')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.obs or not args.probs:
        parser.error('--obs 와 --probs 가 필요합니다 (또는 --selftest)')

    try:
        observations = read_observations(args.obs)
        names, probabilities = read_persona_probabilities(args.probs)
        if args.calibrated:
            result = fit_calibrated(
                observations,
                probabilities,
                n_max=args.n_max,
                truncated=not args.full,
                tolerance=args.tolerance,
                max_iter=args.max_iter,
                calibration_iters=args.calibration_iters,
                n_random_starts=args.random_starts,
                n_step=args.n_step,
            )
            calibration_params: dict[str, float] = {
                'intercept': round(result.intercept, 8),
                'slope': round(result.slope, 8),
            }
        else:
            result = fit(
                observations,
                probabilities,
                n_max=args.n_max,
                truncated=not args.full,
                tolerance=args.tolerance,
                max_iter=args.max_iter,
                n_random_starts=args.random_starts,
                n_step=args.n_step,
            )
            calibration_params = {}
    except ValueError as exc:
        print(f'입력 확인 필요: {exc}', file=sys.stderr)
        return 2

    mode = ('calibrated-' if args.calibrated else '') + ('full' if args.full else 'truncated')
    payload = {
        'mode': mode,
        'exposure': result.exposure,
        'profile_band': list(profile_band(result, delta=args.band_delta)),
        'value': result.value,
        'weights': dict(zip([*names, 'dummy_never_buys'], [round(w, 8) for w in result.weights])),
        **calibration_params,
        'grid': [{'exposure': p.exposure, 'value': p.value, 'converged': p.converged} for p in result.grid],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f'mode={payload["mode"]}  N̂={result.exposure}  V={result.value:.6f}')
    if args.calibrated:
        print(f'calibration: a={result.intercept:.4f}  b={result.slope:.4f}')
    print(f'profile band (ΔV≤{args.band_delta:.1%}): {payload["profile_band"]}')
    print('α̂: ' + ', '.join(f'{name}={weight:.4f}' for name, weight in payload['weights'].items()))
    print('N  V(N)  converged')
    for point in result.grid:
        if point.value == math.inf:
            continue
        print(f'{point.exposure:5d}  {point.value:12.4f}  {point.converged}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
