"""
논문 Algorithm 1·2 의 HILLINK 간이 구현 — 가격정책 시뮬레이션 및 쌍대(A/B) 비교.

  python3 pricing_sim.py                                  # 기본: ε=1.5, N=200 시행
  python3 pricing_sim.py --elasticity 1.0 1.5 2.0         # 탄성 민감도
  python3 pricing_sim.py --trials 400 --b-markup 0.20     # 시행수·정책 B 마진 조절

논문 원문(arXiv 2606.16183) 대비한 간이화 결정 (전부 코드에 명시):
  Algorithm 1
    Phase 1  F̂lead(리드타임) 생략 — 원장에 예약 시점이 없다. F̂trip = 체류기간 실측분포
             {1일, 2일, 3일}. F̂occ 대신 페르소나 (성별, 연령, 목적) 가 동반 규모를 흡수.
             λ_d = γ·M_d 의 γ=1, M_d = 실측 일별 체류 수 (논문의 '과거 예약 수에 대한
             상수 보정'과 동일한 역할).
    Phase 2  N_d ~ Poisson(λ_d). θ_i ~ 실측 세그먼트 경험분포 (persona_grid 비중이 아니라
             실현 세그먼트 믹스 — πA가 관측 매출을 재현해야 A/B 차이가 가격 효과로 깨끗이
             귀속되기 때문). 체류기간 τ_i ~ F̂trip 은 프로파일 통계로만 출력 (간이에서는
             구매확률 q가 체류 기간을 평균낸 값이라 반영 생략 — 한계로 문서화).
    Phase 3  LLM 추론을 보정된 반응함수 스텁으로 대체: 구매확률
             q'_k(u) = clip(q_k(u) · (p_t(u)/p_0(u))^(-ε), 0, 1).
             이것이 'LLM이 θ와 가격을 보고 결정한다'를 흉내내는 최소 함수형 스텁이다.
             실제 LLM 호출로 교체하는 지점은 run_policy 의 resp_matrix 생성 한 곳이다.
    Phase 4  V(π) = Σ_u buys_u · p_t(u)  (33일 수평선 총매출)
  Algorithm 2
    시행 n 마다 seed S_n = base+n 로 재현 가능한 모집단 Pop_n 을 만들고, 동일 Pop_n 에
    πA·πB 를 실행해 δ_n = V(πB) − V(πA). 날짜 구조는 총계로 접는다(정책이 시간불변일 때
    수평선 총계와 동치 — 간이 허용 범위).
  대상 차이 한계: 논문 사례는 객실 가격정책(호텔), HILLINK는 F&B. 객실 수·지역·규모가
  크게 다르므로 이 비교는 구조 이식성 확인용 간이 비교다.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import hillink_fit

MULTISTART_SEED = 17_000  # Algorithm 2 시드 기저 (persona_exposure.MULTISTART_SEED와 별개 축)


@dataclass(frozen=True)
class Calibration:
    """Algorithm 1 Phase 1 — 원장에서 추정한 시뮬레이션 파라미터."""

    lambda_total: float
    trip_probabilities: tuple[float, ...]
    persona_weights: tuple[float, ...]
    modeled_baseline_revenue: float
    realized_revenue: float


@dataclass(frozen=True)
class Population:
    """Algorithm 1 Phase 2 결과 — 정책 간 공유되는 정적 모집단."""

    persona_counts: np.ndarray  # (K,) 각 페르소나의 표본 체류 수
    total: int


def phase1(dataset: hillink_fit.HillinkDataset) -> Calibration:
    import csv
    from collections import Counter

    rows: list[dict[str, str]] = []
    with (dataset_timeline(dataset) / 'revenue_l5.csv').open(newline='', encoding='utf-8-sig') as handle:
        rows = list(csv.DictReader(handle))
    stay_days = Counter()
    stays: dict[str, set[str]] = {}
    for row in rows:
        stays.setdefault(row['체류'], set()).add(row['구매 날짜'])
    for days in stays.values():
        stay_days[len(days)] += 1
    trips = tuple(stay_days.get(k, 0) for k in (1, 2, 3))
    total_trips = sum(trips)
    trip_probabilities = tuple(count / total_trips for count in trips)

    persona_weights = tuple(count / dataset.stay_count for count in dataset.persona_stays)
    modeled = float(sum(o.price * o.demand for o in dataset.observations))
    realized = float(sum(float(row['결제액']) for row in rows if float(row['결제액']) > 0))
    return Calibration(
        lambda_total=float(dataset.stay_count),
        trip_probabilities=trip_probabilities,
        persona_weights=persona_weights,
        modeled_baseline_revenue=modeled,
        realized_revenue=realized,
    )


def dataset_timeline(dataset: hillink_fit.HillinkDataset) -> Path:
    return hillink_fit.TIMELINE_DIR


def phase2(calibration: Calibration, rng: np.random.Generator) -> Population:
    total = int(rng.poisson(calibration.lambda_total))
    counts = rng.multinomial(total, calibration.persona_weights)
    return Population(persona_counts=counts, total=total)


def policy_baseline(dataset: hillink_fit.HillinkDataset) -> np.ndarray:
    """πA — 현행 menu 단가."""
    return np.array([o.price for o in dataset.observations], dtype=float)


def policy_reweight(dataset: hillink_fit.HillinkDataset, *, markup: float, discount: float, top: int, bottom: int) -> np.ndarray:
    """πB — 수요 상위 셀 가삭(markup), 하위 셀 할인(discount) 하는 수요 기반 재조정 정책."""
    prices = policy_baseline(dataset)
    demands = np.array([o.demand for o in dataset.observations], dtype=float)
    order = np.argsort(-demands)
    prices[order[:top]] *= 1.0 + markup
    tail = [i for i in order[::-1] if demands[i] > 0][:bottom]
    prices[tail] *= 1.0 - discount
    return prices


def run_policy(
    population: Population,
    dataset: hillink_fit.HillinkDataset,
    prices: np.ndarray,
    *,
    elasticity: float,
    rng: np.random.Generator,
) -> float:
    """Algorithm 1 Phase 3+4 — LLM 추론 스텁(가격 반응함수)으로 수요를 뽑고 V(π) 계산."""
    q = np.asarray(dataset.probabilities, dtype=float).T  # (K, U)
    base_prices = np.array([o.price for o in dataset.observations], dtype=float)
    response = np.power(prices / base_prices, -elasticity)
    effective = np.clip(q * response[np.newaxis, :], 0.0, 1.0)
    buys = rng.binomial(
        population.persona_counts[:, np.newaxis],  # (K, 1)
        effective,  # (K, U)
    )
    return float(buys.sum(axis=0) @ prices)


def sample_trips(population: Population, calibration: Calibration, rng: np.random.Generator) -> dict[int, int]:
    indices = rng.choice(len(calibration.trip_probabilities), size=population.total, p=calibration.trip_probabilities)
    return {length: int((indices == index).sum()) for index, length in enumerate((1, 2, 3))}


def algorithm2(
    dataset: hillink_fit.HillinkDataset,
    calibration: Calibration,
    prices_a: np.ndarray,
    prices_b: np.ndarray,
    *,
    trials: int,
    elasticity: float,
) -> dict:
    v_a = np.empty(trials)
    v_b = np.empty(trials)
    trips_profile = {1: 0, 2: 0, 3: 0}
    for n in range(trials):
        rng = np.random.default_rng(MULTISTART_SEED + n)  # 시행별 고정 시드 — 재현성
        population = phase2(calibration, rng)
        v_a[n] = run_policy(population, dataset, prices_a, elasticity=elasticity, rng=rng)
        v_b[n] = run_policy(population, dataset, prices_b, elasticity=elasticity, rng=rng)
        if n == 0:  # 프로파일은 시행 1회만 대표 출력
            trips_profile = sample_trips(population, calibration, rng)
    delta = v_b - v_a
    mean = float(delta.mean())
    std = float(delta.std(ddof=1))
    sem = std / math.sqrt(trials)
    return {
        'trials': trials,
        'elasticity': elasticity,
        'mean_V_A': float(v_a.mean()),
        'mean_V_B': float(v_b.mean()),
        'delta_mean': mean,
        'delta_std': std,
        'delta_sem': sem,
        'ci95': (mean - 1.96 * sem, mean + 1.96 * sem),
        'share_positive': float((delta > 0).mean()),
        'trip_profile': trips_profile,
    }


def _fmt_won(value: float) -> str:
    return f'{value:,.0f}원'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='논문 Algorithm 1·2 HILLINK 간이 구현 — 가격정책 A/B 비교')
    parser.add_argument('--trials', type=int, default=200)
    parser.add_argument('--elasticity', type=float, nargs='+', default=[1.5])
    parser.add_argument('--b-markup', type=float, default=0.15)
    parser.add_argument('--b-discount', type=float, default=0.10)
    parser.add_argument('--b-top', type=int, default=10)
    parser.add_argument('--b-bottom', type=int, default=20)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    dataset = hillink_fit.build_dataset()
    calibration = phase1(dataset)
    prices_a = policy_baseline(dataset)
    prices_b = policy_reweight(
        dataset,
        markup=args.b_markup,
        discount=args.b_discount,
        top=args.b_top,
        bottom=args.b_bottom,
    )

    results = [
        algorithm2(dataset, calibration, prices_a, prices_b, trials=args.trials, elasticity=eps)
        for eps in args.elasticity
    ]

    if args.json:
        print(json.dumps(
            {
                'modeled_baseline_revenue': calibration.modeled_baseline_revenue,
                'realized_revenue': calibration.realized_revenue,
                'trip_distribution': dict(zip(('1일', '2일', '3일'), calibration.trip_probabilities)),
                'policy_B': {
                    'markup': args.b_markup,
                    'discount': args.b_discount,
                    'top': args.b_top,
                    'bottom': args.b_bottom,
                },
                'results': [
                    {**r, 'ci95': list(r['ci95'])}
                    for r in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    print(f'기준(모형) 매출 V(πA)≈{_fmt_won(calibration.modeled_baseline_revenue)} · 실결제액 합 {_fmt_won(calibration.realized_revenue)}')
    print(f'체류기간 분포: 1일 {calibration.trip_probabilities[0]:.1%} · 2일 {calibration.trip_probabilities[1]:.1%} · 3일 {calibration.trip_probabilities[2]:.1%}')
    print(f'πB: 수요 상위 {args.b_top}셀 +{args.b_markup:.0%}, 하위 {args.b_bottom}셀 −{args.b_discount:.0%}')
    for r in results:
        lo, hi = r['ci95']
        print(
            f"ε={r['elasticity']:.1f}  V(πA)={_fmt_won(r['mean_V_A'])}  V(πB)={_fmt_won(r['mean_V_B'])}  "
            f"ΔV={_fmt_won(r['delta_mean'])} [{_fmt_won(lo)}, {_fmt_won(hi)}]  "
            f"δ>0 시행 {r['share_positive']:.0%} (N={r['trials']}, 시행1 모집단 {r['trip_profile']})"
        )
    return 0


if __name__ == '__main__':
    sys.exit(main())
