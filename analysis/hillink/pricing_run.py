#!/usr/bin/env python3
"""HILLINK MiroFish 러너 — 결정론 반사실 실험 실행기.

Majidi Algorithm 1/2 (pricing_core) 전구간 실행, 실측 배속 측정,
신의 눈(god's-eye) 가격 시나리오 주입 → JSON 출력.

    python3 hillink/pricing_run.py --mode compare --preset walkerhill_801
    python3 hillink/pricing_run.py --mode evaluate --policy static --scale 1.2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pricing_core.environment import RoomCategory, get_twin_peaks_categories, get_walkerhill_categories
from pricing_core.policies import BasePolicy, StaticPolicy, OccupancyDynamicPolicy, RuleBasedPolicy
from pricing_core.population import PopulationGenerator
from pricing_core.llm_client import LLMDecisionClient
from pricing_core.simulator import HotelPricingSimulator, ComparativeEvaluator

DAY_SECONDS = 86400


def categories_for(preset: str) -> List[RoomCategory]:
    if "walker" in preset.lower() or "801" in preset:
        return get_walkerhill_categories()
    return get_twin_peaks_categories()


def scale_categories(categories: List[RoomCategory], scale: float) -> List[RoomCategory]:
    """신의 눈 — 전 카테고리 기준가를 scale배로 재정의한 시나리오 세계."""
    if scale == 1.0:
        return categories
    return [
        RoomCategory(c.id, c.name, c.max_occ, c.units, c.features,
                     c.base_static_price * scale, c.default_competitor_price)
        for c in categories
    ]


def build_policy(policy_type: str, categories: List[RoomCategory], args: argparse.Namespace) -> BasePolicy:
    if policy_type == "static":
        return StaticPolicy(categories)
    if policy_type == "dynamic":
        return OccupancyDynamicPolicy(
            categories, rho_target=args.rho_target, beta=args.beta, f_min=args.f_min,
        )
    if policy_type == "rule":
        return RuleBasedPolicy(categories, markup_ratio=args.markup)
    raise SystemExit(f"알 수 없는 정책: {policy_type}")


def speed_block(horizon: int, wall_s: float, calls: int) -> Dict[str, Any]:
    """배속 = 시뮬레이션 전구간(일×86,400초)을 실행에 걸린 실시간으로 나눈 값."""
    return {
        "wall_seconds": round(wall_s, 3),
        "horizon_days": horizon,
        "speedup_realtime_x": round(horizon * DAY_SECONDS / wall_s, 1) if wall_s > 0 else 0.0,
        "llm_calls": calls,
    }


def bookings_sample(metrics, limit: int = 25) -> List[Dict[str, Any]]:
    rows = []
    for b in metrics.bookings[:limit]:
        rows.append({
            "agent": b.agent_id, "category": b.category_id,
            "stay_start_day": b.stay_date_start, "nights": b.trip_length,
            "price_per_night": b.price_per_night, "total": b.total_price,
            "decision_day": b.decision_step, "persona": b.persona, "loyalty": b.loyalty,
        })
    return rows


def run_evaluate(args: argparse.Namespace) -> Dict[str, Any]:
    cats = scale_categories(categories_for(args.preset), args.scale)
    policy = build_policy(args.policy, cats, args)
    pop = PopulationGenerator(horizon=args.horizon, seed=args.seed).generate_population(
        target_total_agents=args.agents,
    )
    llm = LLMDecisionClient(mode=args.llm, model_name=args.model)
    sim = HotelPricingSimulator(categories=cats, horizon=args.horizon, llm_client=llm)

    t0 = time.perf_counter()
    metrics = sim.run_simulation(policy, pop, order_seed=args.seed)
    wall = time.perf_counter() - t0

    llm_calls = args.horizon if args.llm != "mock" else 0
    return {
        "experiment": "evaluate", "preset": args.preset, "policy": policy.name,
        "llm_mode": args.llm, "model": llm.model_name if args.llm != "mock" else "mock-engine",
        "seed": args.seed, "price_scale": args.scale,
        "metrics": {
            "total_revenue": metrics.total_revenue,
            "room_nights_sold": metrics.room_nights_sold,
            "occupancy_rate_pct": metrics.occupancy_rate,
            "adr": metrics.adr, "revpar": metrics.revpar,
            "booking_rate_pct": metrics.booking_rate,
            "total_agents": metrics.total_agents,
            "segment_booking_rates": metrics.segment_booking_rates,
            "loyalty_booking_rates": metrics.loyalty_booking_rates,
            "category_sales_count": metrics.category_sales_count,
        },
        "daily_revenue": metrics.daily_revenue,
        "daily_occupancy": metrics.daily_occupancy,
        "bookings": bookings_sample(metrics),
        "booked_total": len(metrics.bookings),
        "speed": speed_block(args.horizon, wall, llm_calls),
    }


def run_compare(args: argparse.Namespace) -> Dict[str, Any]:
    cats = scale_categories(categories_for(args.preset), args.scale)
    policy_a = build_policy("static", cats, args)
    policy_b = build_policy(args.policy, cats, args)
    llm = LLMDecisionClient(mode=args.llm, model_name=args.model)
    sim = HotelPricingSimulator(categories=cats, horizon=args.horizon, llm_client=llm)
    evaluator = ComparativeEvaluator(sim)

    t0 = time.perf_counter()
    result = evaluator.run_paired_comparison(
        policy_a=policy_a, policy_b=policy_b,
        num_trials=args.trials, agents_per_trial=args.agents,
        base_seed=args.seed,
    )
    wall = time.perf_counter() - t0

    llm_calls = args.trials * 2 * args.horizon if args.llm != "mock" else 0
    return {
        "experiment": "compare", "preset": args.preset,
        "policy_a": result.policy_a_name, "policy_b": result.policy_b_name,
        "llm_mode": args.llm, "model": llm.model_name if args.llm != "mock" else "mock-engine",
        "base_seed": args.seed, "price_scale": args.scale, "trials": args.trials,
        "comparison": {
            "mean_revenue_a": result.mean_revenue_a,
            "mean_revenue_b": result.mean_revenue_b,
            "delta_v": result.mean_treatment_effect,
            "revenue_uplift_pct": result.revenue_uplift_pct,
            "std_delta": result.std_treatment_effect,
            "t_statistic": result.t_statistic, "p_value": result.p_value,
            "mean_occupancy_a": result.mean_occupancy_a,
            "mean_occupancy_b": result.mean_occupancy_b,
            "mean_revpar_a": result.mean_revpar_a,
            "mean_revpar_b": result.mean_revpar_b,
            "segment_uplift_pp": result.segment_uplift,
        },
        "trials": result.trials_data,
        "speed": speed_block(args.horizon, wall, llm_calls),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="MiroFish 결정론 반사실 실험 러너")
    p.add_argument("--mode", choices=["evaluate", "compare"], default="compare")
    p.add_argument("--preset", default="walkerhill_801")
    p.add_argument("--policy", choices=["static", "dynamic", "rule"], default="dynamic",
                   help="evaluate 모드의 정책, compare 모드의 B안 (A안은 항상 static)")
    p.add_argument("--horizon", type=int, default=31)
    p.add_argument("--agents", type=int, default=300)
    p.add_argument("--trials", type=int, default=10)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--scale", type=float, default=1.0, help="신의 눈 — 전 카테고리 가격 배율")
    p.add_argument("--rho-target", type=float, default=0.5)
    p.add_argument("--beta", type=float, default=0.4)
    p.add_argument("--f-min", type=float, default=0.65)
    p.add_argument("--markup", type=float, default=1.0)
    p.add_argument("--llm", choices=["mock", "glm", "ainize", "openai"], default="mock")
    p.add_argument("--model", default=None)
    p.add_argument("--out", default="-", help="JSON 출력 경로 (기본 stdout)")
    args = p.parse_args(argv)

    result = run_compare(args) if args.mode == "compare" else run_evaluate(args)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(payload)
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n")
        print(f"OK {out} — 배속 {result['speed']['speedup_realtime_x']:,.0f}×", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
