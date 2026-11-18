"""L1 실행기 — 페르소나 예약 결정 → 직교 상태머신(M1~M7) → I1~I6 검증 → 단일 JSON.

구성:
- L1 결정: MockAgent 또는 OllamaCloudAgent 가 가격±10%×프로모션 정책별로 예약/기권/거절 결정
- 상태머신: pricing_core 어댑터가 BookingRecord → M1(예약)·M2(투숙)·M3(Folio) 전이 생성,
  러너가 M4(컴플라이언스)·M5(이동)·M7(업장세션) 합성 이벤트를 추가 (source로 구분)
- 검증: 어댑터의 타임라인 검증(I1~I6) 결과를 예약별로 기록
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parent.parent
PRICING_CORE_DIR = ROOT / "analysis" / "hillink"
for extra in (str(ROOT), str(PRICING_CORE_DIR)):
    if extra not in sys.path:
        sys.path.insert(0, extra)

from pricing_core.environment import BookingRecord  # noqa: E402
from pricing_core.state_machine_adapter import GuestStateMachineAdapter  # noqa: E402
from wise_sim.l1_agent import MockAgent, Policy, get_ollama_cloud_agent  # noqa: E402
from wise_sim.personas import generate_population  # noqa: E402
from wise_sim.stay_timeline import strict_validation  # noqa: E402

PRICE_DELTAS = (-0.10, 0.0, 0.10)
PROMOS = ("none", "package", "breakfast")
CATEGORY_ID = 101
OUTLET_BY_ARCHETYPE = {
    "가족여행": "더 뷔페",
    "비즈니스": "클럽 라운지",
    "커플": "더 파빌리온",
    "외국인관광": "온달",
}


class L1RunError(ValueError):
    """L1 실행 입력이 기대와 어긋날 때."""


def build_policies(grid: str) -> list[Policy]:
    if grid == "baseline":
        return [Policy(price_delta=0.0, promo="none")]
    if grid == "3x3":
        return [Policy(price_delta=delta, promo=promo) for delta in PRICE_DELTAS for promo in PROMOS]
    raise L1RunError(f"policies 는 baseline 또는 3x3 이어야 합니다: {grid}")


def build_booking_records(
    decisions: Sequence[Any], personas_by_id: dict[int, Any], seed: int, horizon: int = 28,
) -> list[BookingRecord]:
    """예약 결정(booked=True)만 BookingRecord로 변환한다."""
    rng = random.Random(seed)
    records: list[BookingRecord] = []
    for decision in decisions:
        if not decision.booked:
            continue
        persona = personas_by_id[decision.persona_id]
        nights = max(1, int(round(persona.stay_nights)))
        start_day = rng.randint(1, max(1, horizon - nights))
        per_night = round(float(decision.spend) / nights) if decision.spend else 0
        records.append(BookingRecord(
            agent_id=f"agent-{decision.persona_id:03d}",
            category_id=CATEGORY_ID,
            stay_date_start=start_day,
            trip_length=nights,
            price_per_night=float(per_night),
            total_price=float(round(decision.spend)),
            decision_step=1,
            persona=decision.archetype,
            loyalty="high" if persona.spend_index >= 1.2 else "medium",
        ))
    return records


def outlet_for(persona: Any) -> str | None:
    dining = getattr(persona, "dining_preference", "unknown")
    if dining != "unknown":
        return dining
    return OUTLET_BY_ARCHETYPE.get(getattr(persona, "archetype", ""))


def synthetic_machines(persona: Any, rng: random.Random) -> dict[str, Any]:
    """M4·M5·M7 — 어댑터 범위 밖 머신의 시뮬레이션 이벤트(출처 명시).

    M7 업장은 페르소나 dining_preference가 없으므로 아키타입→업장 매핑으로 합성한다.
    """
    outlet = outlet_for(persona)
    outlet_path = ["ENTERED", "SEATED", "ORDERED", "SERVED", "PAID", "EXITED"] if outlet else []
    return {
        "m4_compliance": {
            "id_verified": True,
            "privacy_consent": True,
            "status": "PASSED",
            "source": "runner-simulated",
        },
        "m5_transport": {
            "mode": rng.choice(["SELF_DRIVE", "TAXI", "SHUTTLE"]),
            "status": "ARRIVED_ON_PROPERTY",
            "source": "runner-simulated",
        },
        "m7_outlet_session": {
            "outlet": outlet,
            "state_path": outlet_path,
            "source": "runner-simulated(archetype-mapped)",
        },
    }


def decision_payload(decision: Any) -> dict[str, Any]:
    return {
        "persona_id": decision.persona_id,
        "archetype": decision.archetype,
        "booked": decision.booked,
        "abstained": decision.abstained,
        "spend": round(float(decision.spend), 1),
        "trace": decision.trace,
    }


def run_policy(
    agent: Any, personas: Sequence[Any], policy: Policy, weekend: bool, seed: int, horizon: int,
) -> dict[str, Any]:
    decisions = agent.decide_batch(list(personas), policy, weekend=weekend, seed=seed)
    personas_by_id = {persona.id: persona for persona in personas}
    records = build_booking_records(decisions, personas_by_id, seed=seed, horizon=horizon)
    rng = random.Random(seed + 1)
    bookings: list[dict[str, Any]] = []
    violations = 0
    for record in records:
        transitions = GuestStateMachineAdapter.booking_to_state_machine_events(record)
        transitions.update(synthetic_machines(personas_by_id[int(record.agent_id.split("-")[1])], rng))
        validation = GuestStateMachineAdapter.validate_booking_timeline(record)
        persona = personas_by_id[int(record.agent_id.split("-")[1])]
        strict = strict_validation(record, outlet_for(persona))
        violations += int(strict.get("violations_count", 0) or 0)
        bookings.append({
            "agent_id": record.agent_id,
            "persona": record.persona,
            "loyalty": record.loyalty,
            "checkin_day": record.stay_date_start,
            "nights": record.trip_length,
            "price_per_night": record.price_per_night,
            "total_price": record.total_price,
            "machines": transitions,
            "validation": validation,
            "validation_strict": strict,
        })
    booked = sum(1 for decision in decisions if decision.booked)
    abstained = sum(1 for decision in decisions if decision.abstained)
    spend = sum(float(decision.spend) for decision in decisions)
    return {
        "policy": policy.label,
        "price_delta": policy.price_delta,
        "promo": policy.promo,
        "decision_counts": {
            "booked": booked,
            "abstained": abstained,
            "rejected": len(decisions) - booked - abstained,
        },
        "abstain_rate": round(abstained / max(len(decisions), 1), 3),
        "total_spend": round(spend, 1),
        "decisions": [decision_payload(decision) for decision in decisions],
        "bookings": bookings,
        "invariant_violations": violations,
    }


def run(
    *, agents: int = 24, seed: int = 42, provider: str = "mock", policies: str = "baseline",
    weekend: bool = False, horizon: int = 28, model: str | None = None,
) -> dict[str, Any]:
    personas = generate_population(agents, seed=seed)
    if provider == "mock":
        agent = MockAgent()
    elif provider == "ollama-cloud":
        agent = get_ollama_cloud_agent(model=model)
    else:
        raise L1RunError(f"provider 는 mock 또는 ollama-cloud 여야 합니다: {provider}")
    policy_list = build_policies(policies)
    runs = [
        run_policy(agent, personas, policy, weekend, seed + index, horizon)
        for index, policy in enumerate(policy_list)
    ]
    total_booked = sum(item["decision_counts"]["booked"] for item in runs)
    total_violations = sum(item["invariant_violations"] for item in runs)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seed": seed,
        "provider": provider,
        "model": getattr(agent, "model", None),
        "weekend": weekend,
        "agents": agents,
        "horizon_days": horizon,
        "state_machine_scope": {
            "adapter_machines": ["M1", "M2", "M3"],
            "runner_simulated_machines": ["M4", "M5", "M7"],
            "validation_coverage": "full-stay segment (checkin→room→outlet→room→checkout) · I1~I6",
        },
        "summary": {
            "policies": len(runs),
            "decisions": agents * len(runs),
            "booked": total_booked,
            "invariant_violations": total_violations,
            "validations_passed": sum(len(item["bookings"]) for item in runs) - total_violations,
        },
        "runs": runs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="L1 실행 — 페르소나 결정 + 직교 상태머신 + I1~I6 검증")
    parser.add_argument("--agents", type=int, default=24)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--provider", choices=("mock", "ollama-cloud"), default="mock")
    parser.add_argument("--policies", choices=("baseline", "3x3"), default="baseline")
    parser.add_argument("--weekend", action="store_true")
    parser.add_argument("--horizon", type=int, default=28)
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", type=Path, default=Path("analysis/l1_run.json"))
    args = parser.parse_args()
    report = run(
        agents=args.agents, seed=args.seed, provider=args.provider, policies=args.policies,
        weekend=args.weekend, horizon=args.horizon, model=args.model,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = report["summary"]
    print(
        f"L1 실행 완료 → {args.out} | 정책 {summary['policies']} · 결정 {summary['decisions']} · "
        f"예약 {summary['booked']} · 불변식 위반 {summary['invariant_violations']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
