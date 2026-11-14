"""
walkerhilling 동적 가격 책정 시뮬레이션 REST API 서비스 모듈.

엔드포인트:
- GET  /api/pricing/presets          : 지원 프리셋 목록 (twin_peaks, walkerhill)
- POST /api/pricing/evaluate         : Majidi Algorithm 1 단일 정책 평가
- POST /api/pricing/compare          : Majidi Algorithm 2 쌍체 A/B 정책 비교
- POST /api/pricing/calibrate        : Huang & Wang 페르소나 가중치 최적화
- GET  /api/pricing/status           : 비동기 작업 상태 및 결과 조회
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# pricing_core 경로 추가
HILLINK_DIR = Path(__file__).resolve().parent.parent / "hillink"
if str(HILLINK_DIR) not in sys.path:
    sys.path.insert(0, str(HILLINK_DIR))

from pricing_core import (
    RoomCategory,
    InventoryMatrix,
    get_twin_peaks_categories,
    get_walkerhill_categories,
    StaticPolicy,
    OccupancyDynamicPolicy,
    RuleBasedPolicy,
    PopulationGenerator,
    LLMDecisionClient,
    HotelPricingSimulator,
    ComparativeEvaluator,
)
from pricing_core.calibration_bridge import CalibrationBridge
from pricing_core.state_machine_adapter import GuestStateMachineAdapter

CACHE_DIR = Path(__file__).resolve().parent / "cache" / "pricing"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# 메모리 상의 작업 상태 저장소
_TASK_STORE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def get_categories_for_preset(preset_name: str) -> List[RoomCategory]:
    preset_lower = preset_name.lower()
    if "walkerhill" in preset_lower or "walker" in preset_lower or "801" in preset_lower:
        return get_walkerhill_categories()
    return get_twin_peaks_categories()


def handle_get_presets() -> Dict[str, Any]:
    """GET /api/pricing/presets"""
    tp = get_twin_peaks_categories()
    wh = get_walkerhill_categories()

    return {
        "status": "success",
        "presets": {
            "twin_peaks_36": {
                "name": "Twin Peaks Lakeside Inn (Benchmark)",
                "description": "Paper benchmark (13 room categories, 36 units, Taupo NZ)",
                "categories_count": len(tp),
                "total_units": sum(c.units for c in tp),
                "default_horizon_days": 31,
                "categories": [
                    {"id": c.id, "name": c.name, "units": c.units, "base_price": c.base_static_price}
                    for c in tp
                ],
            },
            "walkerhill_801": {
                "name": "Walkerhill Hotels & Resorts (Production)",
                "description": "Grand, Vista, Douglas House (8 categories, 801 units)",
                "categories_count": len(wh),
                "total_units": sum(c.units for c in wh),
                "default_horizon_days": 31,
                "categories": [
                    {"id": c.id, "name": c.name, "units": c.units, "base_price": c.base_static_price}
                    for c in wh
                ],
            },
        },
    }


def handle_evaluate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST /api/pricing/evaluate
    Majidi Algorithm 1 단일 정책 평가 실행.
    """
    preset_name = payload.get("preset", "twin_peaks_36")
    horizon = int(payload.get("horizon", 31))
    total_agents = int(payload.get("total_agents", 112))
    policy_type = payload.get("policy", "dynamic")  # "static", "dynamic", "rule"
    llm_mode = payload.get("llm_mode", "mock")      # "mock", "openai"
    seed = payload.get("seed", 42)

    categories = get_categories_for_preset(preset_name)

    # 정책 인스턴스화
    if policy_type == "static":
        policy = StaticPolicy(categories)
    elif policy_type == "dynamic":
        rho_target = float(payload.get("rho_target", 0.5))
        beta = float(payload.get("beta", 0.4))
        f_min = float(payload.get("f_min", 0.65))
        policy = OccupancyDynamicPolicy(categories, rho_target=rho_target, beta=beta, f_min=f_min)
    else:
        markup = float(payload.get("markup", 1.0))
        policy = RuleBasedPolicy(categories, markup_ratio=markup)

    # Huang & Wang 사전확률 주입 (선택 옵션)
    custom_priors = payload.get("archetype_priors")

    pop_gen = PopulationGenerator(horizon=horizon, seed=seed, archetype_priors=custom_priors)
    population = pop_gen.generate_population(target_total_agents=total_agents)

    llm_client = LLMDecisionClient(mode=llm_mode)
    simulator = HotelPricingSimulator(categories=categories, horizon=horizon, llm_client=llm_client)

    metrics = simulator.run_simulation(policy, population)

    # 워커힐 State Machine 타임라인 검증 샘플 추출 (상위 3명)
    timeline_validations = []
    for b in metrics.bookings[:3]:
        tv = GuestStateMachineAdapter.validate_booking_timeline(b)
        sm_events = GuestStateMachineAdapter.booking_to_state_machine_events(b)
        timeline_validations.append({
            "booking": sm_events,
            "timeline_check": tv,
        })

    return {
        "status": "success",
        "preset": preset_name,
        "policy": metrics.policy_name,
        "metrics": {
            "total_revenue": metrics.total_revenue,
            "room_nights_sold": metrics.room_nights_sold,
            "total_capacity_nights": metrics.total_capacity_nights,
            "occupancy_rate_pct": metrics.occupancy_rate,
            "adr": metrics.adr,
            "revpar": metrics.revpar,
            "booking_rate_pct": metrics.booking_rate,
            "total_agents": metrics.total_agents,
            "total_booked_agents": metrics.total_booked_agents,
            "segment_booking_rates": metrics.segment_booking_rates,
            "loyalty_booking_rates": metrics.loyalty_booking_rates,
            "category_sales_count": metrics.category_sales_count,
        },
        "daily_trends": {
            "daily_revenue": metrics.daily_revenue,
            "daily_occupancy": metrics.daily_occupancy,
        },
        "sample_state_machine_integration": timeline_validations,
    }


def handle_compare(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST /api/pricing/compare
    Majidi Algorithm 2 정책 간 쌍체 비교 A/B 시뮬레이션 실행.
    """
    preset_name = payload.get("preset", "twin_peaks_36")
    horizon = int(payload.get("horizon", 31))
    num_trials = int(payload.get("num_trials", 5))
    agents_per_trial = int(payload.get("agents_per_trial", 112))
    base_seed = int(payload.get("base_seed", 1000))
    llm_mode = payload.get("llm_mode", "mock")

    categories = get_categories_for_preset(preset_name)

    policy_a = StaticPolicy(categories)
    policy_b = OccupancyDynamicPolicy(categories)

    llm_client = LLMDecisionClient(mode=llm_mode)
    simulator = HotelPricingSimulator(categories=categories, horizon=horizon, llm_client=llm_client)
    evaluator = ComparativeEvaluator(simulator)

    custom_priors = payload.get("archetype_priors")

    result = evaluator.run_paired_comparison(
        policy_a=policy_a,
        policy_b=policy_b,
        num_trials=num_trials,
        agents_per_trial=agents_per_trial,
        base_seed=base_seed,
        archetype_priors=custom_priors,
    )

    return {
        "status": "success",
        "preset": preset_name,
        "comparison": {
            "policy_a": result.policy_a_name,
            "policy_b": result.policy_b_name,
            "num_trials": result.num_trials,
            "mean_revenue_a": result.mean_revenue_a,
            "mean_revenue_b": result.mean_revenue_b,
            "mean_treatment_effect_delta_v": result.mean_treatment_effect,
            "revenue_uplift_pct": result.revenue_uplift_pct,
            "std_treatment_effect": result.std_treatment_effect,
            "t_statistic": result.t_statistic,
            "p_value": result.p_value,
            "mean_occupancy_a": result.mean_occupancy_a,
            "mean_occupancy_b": result.mean_occupancy_b,
            "mean_adr_a": result.mean_adr_a,
            "mean_adr_b": result.mean_adr_b,
            "mean_revpar_a": result.mean_revpar_a,
            "mean_revpar_b": result.mean_revpar_b,
            "segment_uplift_pp": result.segment_uplift,
        },
        "trials_detail": result.trials_data,
    }


def handle_calibrate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    POST /api/pricing/calibrate
    Huang & Wang 페르소나 가중치 및 노출 최적화 실행.
    """
    if not CalibrationBridge.is_available():
        return {
            "status": "error",
            "message": "persona_exposure module is not available",
        }

    # 샘플 또는 전달된 데이터 파싱
    obs_raw = payload.get("observations")
    probs_raw = payload.get("probabilities")
    persona_names = payload.get("persona_names", ["Budget", "Business", "Luxury", "Leisure"])
    n_max = int(payload.get("n_max", 150))
    truncated = bool(payload.get("truncated", True))

    if not obs_raw or not probs_raw:
        # 워커힐 실측 객실 기준 샘플 데이터 자동 공급
        obs_raw = [
            ("Standard", 210.0, 15),
            ("Deluxe", 260.0, 10),
            ("Suite", 350.0, 4),
            ("Family", 230.0, 8),
        ]
        probs_raw = [
            [0.60, 0.40, 0.10, 0.50],
            [0.30, 0.50, 0.40, 0.45],
            [0.05, 0.30, 0.80, 0.15],
            [0.40, 0.20, 0.25, 0.65],
        ]

    calib_res = CalibrationBridge.calibrate_persona_weights(
        observations_data=obs_raw,
        persona_probs_matrix=probs_raw,
        persona_names=persona_names,
        n_max=n_max,
        truncated=truncated,
    )

    # Majidi 아키타입으로 변환
    majidi_priors = CalibrationBridge.map_to_majidi_archetype_priors(calib_res["weights"])

    return {
        "status": "success",
        "optimal_exposure_N": calib_res["optimal_N"],
        "calibrated_weights": calib_res["weights"],
        "dummy_non_purchase_weight": calib_res["dummy_non_purchase_weight"],
        "objective_value": calib_res["objective_value"],
        "mapped_majidi_priors": majidi_priors,
        "recommendation": "Pass mapped_majidi_priors into /api/pricing/evaluate or /api/pricing/compare as archetype_priors.",
    }


def handle_get_status(task_id: str) -> Dict[str, Any]:
    """GET /api/pricing/status?task_id=..."""
    with _LOCK:
        if task_id not in _TASK_STORE:
            return {"status": "error", "message": f"Task {task_id} not found"}
        return _TASK_STORE[task_id]
