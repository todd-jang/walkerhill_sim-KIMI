"""L2: 결정론적 시뮬레이션 엔진 (Deterministic Engine) — LLM 절대 배제.

- 100% 규칙 기반(Rule-based) Python 수학/집계 알고리즘.
- L1 에이전트의 개별 예약 판단(Decision)만을 입력받아 객실 재고 제약, 점유율, ADR, RevPAR 산출.
- 워커힐 총 객실 수(580실) 기반 오버부킹 제한 및 통계적 집계.
- Gate B(배칭) 및 Gate F(시간 압축 24배) 스케줄링.
"""
from __future__ import annotations

import hashlib
from typing import Iterator, Mapping

try:
    from personas import Persona
    from l1_agent import BaseAgent, Decision, Policy, BATCH_SIZE, BASE_ROOM_PRICE
    from transit_matrix import TransitContext
except ImportError:
    from .personas import Persona
    from .l1_agent import BaseAgent, Decision, Policy, BATCH_SIZE, BASE_ROOM_PRICE
    from .transit_matrix import TransitContext

TOTAL_ROOMS = 580          # 워커힐 기준 총 가용 객실 수 (가정치/설정치)
TIME_COMPRESSION = 24    # Gate F: 24배 smoke (12배 조건부)


def policy_grid() -> list[Policy]:
    """3×3 정책 격자 (Gate C 대응): 가격 3단계 × 프로모션 3단계 = 9개 셀."""
    return [
        Policy(price_delta=d, promo=p)
        for d in (-0.10, 0.0, 0.10)
        for p in ("none", "package", "breakfast")
    ]


def _batched(seq: list, size: int) -> Iterator[list]:
    """리스트를 size 단위로 분할(배칭)."""
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def evaluate_facility_policy(
    policy: Mapping[str, float],
    transit: TransitContext,
    origin: str = "room",
    dest: str = "fnb",
    queue_wait_minutes: float = 0.0,
) -> dict:
    """정책 유인도와 이동·대기 불편도를 L2에서 결정론적으로 평가한다."""
    promo_strength = policy.get("fnb_promo_strength", 0.0)
    if (isinstance(promo_strength, bool)
            or not isinstance(promo_strength, (int, float))
            or not 0.0 <= promo_strength <= 1.0):
        raise ValueError("fnb_promo_strength must be between 0 and 1")
    transit_minutes = transit.get_transit_time(origin, dest)
    disutility = transit.get_disutility(
        origin,
        dest,
        queue_wait_minutes=queue_wait_minutes,
    )
    return {
        "origin": origin,
        "destination": dest,
        "transit_minutes": transit_minutes,
        "queue_wait_minutes": float(queue_wait_minutes),
        "disutility": round(disutility, 4),
        "net_attraction": round(float(promo_strength) - disutility, 4),
        "assumption_based_transit": transit.is_assumption,
    }


def simulate_weekend(agent: BaseAgent, personas: list[Persona], policy: Policy,
                     weekend_idx: int, weekend: bool = True,
                     seed: int = 1000,
                     transit_context: TransitContext | None = None,
                     queue_wait_minutes: float = 0.0) -> dict:
    """단일 주말 시뮬레이션 — 100% 결정론적 집계.
    
    1. L1 에이전트를 BATCH_SIZE(20명) 단위로 호출하여 개별 예약 결정을 획득.
    2. 수집된 결정들로부터 객실 용량(580실) 한도 내 점유율, RevPAR, ADR을 순수 수학 수식으로 집계.
    """
    decisions: list[Decision] = []
    
    # Gate B: 20명 배칭 + 고정 해시 시드로 재현성 보장
    for bi, batch in enumerate(_batched(personas, BATCH_SIZE)):
        tag = f"{seed}|{weekend_idx}|{policy.label}|{bi}"
        bseed = int(hashlib.sha256(tag.encode("utf-8")).hexdigest()[:8], 16)
        decisions.extend(agent.decide_batch(batch, policy, weekend, bseed))

    # --- 100% 결정론적 집계 로직 (LLM 미개입) ---
    valid = [d for d in decisions if not d.abstained]
    booked = [d for d in valid if d.booked]
    
    n_total = len(decisions)
    n_valid = len(valid)
    n_booked = len(booked)

    # 투숙 박수 반영 객실 점유 계산 (워커힐 주말 2박 기준 최대 580실 * 2.0 = 1,160 객실박)
    total_room_capacity_nights = TOTAL_ROOMS * 2.0
    booked_ids = {d.persona_id for d in booked}
    total_nights_demanded = sum(p.stay_nights for p in personas if p.id in booked_ids)
    
    # 객실 한도 적용 (오버부킹 캡)
    capped_nights = min(total_nights_demanded, total_room_capacity_nights)
    occupancy = capped_nights / total_room_capacity_nights if total_room_capacity_nights > 0 else 0.0
    occupancy = min(max(occupancy, 0.0), 1.0)

    # 매출 및 RevPAR 집계
    total_spend = sum(d.spend for d in booked)
    revpar = total_spend / TOTAL_ROOMS if TOTAL_ROOMS > 0 else 0.0
    adr = total_spend / max(n_booked, 1)

    result = {
        "policy": policy.label,
        "weekend_idx": weekend_idx,
        "n_personas": n_total,
        "valid_count": n_valid,
        "booked_count": n_booked,
        "abstain_rate": round(1.0 - (n_valid / max(n_total, 1)), 4),
        "booking_rate": round(n_booked / max(n_valid, 1), 4),
        "occupancy": round(occupancy, 4),
        "revpar": round(revpar, 0),
        "adr": round(adr, 0),
        "decisions": decisions,
    }
    if transit_context is not None:
        promo_strength = {
            "none": 0.0,
            "package": 0.12,
            "breakfast": 0.06,
        }.get(policy.promo, 0.0)
        result["facility_context"] = evaluate_facility_policy(
            {"fnb_promo_strength": promo_strength},
            transit_context,
            queue_wait_minutes=queue_wait_minutes,
        )
    return result


def run_policy_grid(agent: BaseAgent, personas: list[Persona], n_weekends: int,
                    seed: int = 1000,
                    transit_context: TransitContext | None = None,
                    queue_wait_minutes: float = 0.0) -> dict[str, list[dict]]:
    """3×3 정책 격자 × n개 주말 전체 시뮬레이션 일괄 실행.
    
    모든 정책과 주말에 걸친 trace 로그 및 집계 통계를 반환.
    """
    grid = policy_grid()
    results: dict[str, list[dict]] = {p.label: [] for p in grid}
    for p in grid:
        for w in range(n_weekends):
            sim_res = simulate_weekend(
                agent,
                personas,
                p,
                weekend_idx=w,
                weekend=True,
                seed=seed,
                transit_context=transit_context,
                queue_wait_minutes=queue_wait_minutes,
            )
            results[p.label].append(sim_res)
    return results
