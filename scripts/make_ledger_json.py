"""실험 원장 JSON 생성기 — 효과 크기·표본·노출률을 지정해 검증된 원장을 산출.

시나리오 공간(예): 효과 방향 3(성공/중립/역효과) × 효과 크기 2(대/소) ×
세그먼트 이질성 2(균질/이질) × 노출률 2(고/저) × 표본 2(소/대) = 48가지.
무작위화 규칙(블록당 양군 1명·세그먼트별 양군 존재)과 귀속 기간을 자동 준수하고,
쓰기 전에 analyze_bundle 검증을 통과시킨다.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wise_sim.web_app import analyze_bundle

KST = timezone(timedelta(hours=9))
DEFAULT_CUTOFF = "2026-12-31T23:59:59+09:00"


def _ts(value: datetime) -> str:
    return value.isoformat()


def generate_bundle(
    *,
    per_arm: int = 120,
    control_rate: float = 0.20,
    effect_pp: float = 0.0,
    exposure_rate: float = 0.85,
    seed: int = 20261118,
    experiment_id: str = "pilot-scenario",
    cutoff_at: str = DEFAULT_CUTOFF,
) -> dict[str, Any]:
    """결정적(시드 고정) 원장 번들을 생성한다. 예약 건수는 목표 전환율에 정확히 맞춘다."""
    total = per_arm * 2
    rng = random.Random(seed)
    control = round(control_rate * per_arm)
    treatment_rate = min(max(control_rate + effect_pp / 100.0, 0.0), 0.95)
    treatment = round(treatment_rate * per_arm)

    ledger: dict[str, list[dict[str, Any]]] = {
        key: [] for key in (
            "assignments", "exposures", "bookings", "payments", "cancellations",
            "refunds", "costs", "stays", "operations", "surveys",
        )
    }
    booking_serial = 0
    for index in range(total):
        block = index // 2
        segment = "family" if block % 2 == 0 else "adult"
        arm = "treatment" if index % 2 == 0 else "control"
        assignment_id = f"a-{index:03d}"
        assigned_at = datetime(2026, 11, 1, 9, 0, tzinfo=KST) + timedelta(hours=4 * index)
        ledger["assignments"].append({
            "experiment_id": experiment_id,
            "design": "randomized",
            "data_origin": "randomized_experiment",
            "assignment_id": assignment_id,
            "unit_id": f"u-{index:03d}",
            "segment": segment,
            "assigned_variant": arm,
            "assignment_probability": 0.5,
            "assigned_at": _ts(assigned_at),
            "outcome_matures_at": _ts(assigned_at + timedelta(days=14)),
            "randomization_block_id": f"blk-{block:03d}",
            "protocol_id": "protocol-v1",
            "eligibility_version": "elig-v1",
            "assignment_logic_version": "assign-v1",
        })
        if rng.random() >= exposure_rate:
            continue
        ledger["exposures"].append({
            "event_id": f"e-{index:03d}",
            "assignment_id": assignment_id,
            "exposed_at": _ts(assigned_at + timedelta(minutes=45)),
            "rendered_variant": arm,
            "page": "xmas-package",
            "channel": "web",
            "price": {"amount": 445000, "currency": "KRW"},
            "inventory_snapshot": {"remaining_rooms": 12},
        })
        ledger["surveys"].append({
            "survey_id": f"sv-{index:03d}",
            "assignment_id": assignment_id,
            "invited_at": _ts(assigned_at + timedelta(days=21)),
            "responded_at": None,
            "satisfaction_score": None,
            "recommendation_score": None,
        })
        if rng.random() < 0.40:
            responded = ledger["surveys"][-1]
            responded["responded_at"] = _ts(assigned_at + timedelta(days=22))
            responded["satisfaction_score"] = rng.randint(3, 5)
            responded["recommendation_score"] = rng.randint(7, 10)
        if rng.random() < 0.05:
            ledger["operations"].append({
                "event_id": f"o-{index:03d}",
                "assignment_id": assignment_id,
                "occurred_at": _ts(assigned_at + timedelta(days=4)),
                "event_type": "complaint",
            })

    _fill_bookings(ledger, "treatment", treatment_rate, rng)
    _fill_bookings(ledger, "control", control_rate, rng)
    return {"experiment": {
        "experiment_id": experiment_id,
        "analysis_cutoff_at": cutoff_at,
        "source_complete_through": cutoff_at,
        "attribution_window_days": 14,
        "data_version": "scenario-v1",
    }, **ledger}


def _fill_bookings(
    ledger: dict[str, list[dict[str, Any]]], arm: str, rate: float, rng: random.Random,
) -> None:
    """세그먼트 층화로 목표 전환율을 정확히 맞춰 예약·결제·취소/환불·원가·투숙을 채운다."""
    by_segment: dict[str, list[dict[str, Any]]] = {}
    for item in ledger["assignments"]:
        if item["assigned_variant"] == arm:
            by_segment.setdefault(item["segment"], []).append(item)
    serial = 0
    for candidates in by_segment.values():
        rng.shuffle(candidates)
        count = round(rate * len(candidates))
        for assignment in candidates[:count]:
            serial += 1
            _write_booking(ledger, arm, serial, assignment, rng)


def _write_booking(
    ledger: dict[str, list[dict[str, Any]]], arm: str, serial: int,
    assignment: dict[str, Any], rng: random.Random,
) -> None:
    booking_id = f"b-{arm[0]}-{serial:03d}"
    booked_at = datetime.fromisoformat(assignment["assigned_at"]) + timedelta(hours=6)
    amount = 445000 if assignment["segment"] == "family" else 390000
    ledger["bookings"].append({
        "booking_id": booking_id,
        "assignment_id": assignment["assignment_id"],
        "booked_at": _ts(booked_at),
    })
    ledger["payments"].append({
        "payment_id": f"p-{arm[0]}-{serial:03d}",
        "booking_id": booking_id,
        "payment_status": "succeeded",
        "paid_at": _ts(booked_at + timedelta(minutes=5)),
        "amount": amount,
        "currency": "KRW",
    })
    if rng.random() < 0.10:
        ledger["cancellations"].append({
            "event_id": f"c-{arm[0]}-{serial:03d}",
            "booking_id": booking_id,
            "cancelled_at": _ts(booked_at + timedelta(days=2)),
            "status": "cancelled",
            "fee_amount": 0,
            "currency": "KRW",
        })
        ledger["refunds"].append({
            "event_id": f"r-{arm[0]}-{serial:03d}",
            "refund_id": f"rf-{arm[0]}-{serial:03d}",
            "booking_id": booking_id,
            "payment_id": f"p-{arm[0]}-{serial:03d}",
            "event_at": _ts(booked_at + timedelta(days=3)),
            "status": "completed",
            "amount": amount,
            "currency": "KRW",
        })
    else:
        ledger["stays"].append({
            "event_id": f"s-{arm[0]}-{serial:03d}",
            "booking_id": booking_id,
            "checked_in_at": _ts(booked_at + timedelta(days=18)),
            "checked_out_at": _ts(booked_at + timedelta(days=19)),
        })
    ledger["costs"].append({
        "event_id": f"k-{arm[0]}-{serial:03d}",
        "booking_id": booking_id,
        "incurred_at": _ts(booked_at + timedelta(days=18)),
        "amount": int(amount * 0.32),
        "currency": "KRW",
    })


def write_scenario(output: Path, *, label: str, **kwargs: Any) -> dict[str, Any]:
    bundle = generate_bundle(**kwargs)
    result = analyze_bundle(bundle)
    segments = {}
    for segment, data in result["segments"].items():
        metric = data["metrics"]["paid_booking"]
        segments[segment] = {
            "control": round(metric["control_mean"], 3),
            "treatment": round(metric["treatment_mean"], 3),
            "itt": round(metric["itt_estimate"], 3),
            "ci95": [round(value, 3) for value in metric["confidence_interval"]],
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{label}: {output.name} ({len(bundle['assignments'])} 배정) → " + " / ".join(
        f"{segment} ITT {value['itt']:+.3f} CI({value['ci95'][0]:+.3f},{value['ci95'][1]:+.3f})"
        for segment, value in segments.items()
    ))
    return {"label": label, "file": str(output), "segments": segments}


def main() -> int:
    parser = argparse.ArgumentParser(description="실험 원장 JSON 생성기")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="scenario")
    parser.add_argument("--per-arm", type=int, default=120)
    parser.add_argument("--control-rate", type=float, default=0.20)
    parser.add_argument("--effect-pp", type=float, default=0.0)
    parser.add_argument("--exposure-rate", type=float, default=0.85)
    parser.add_argument("--seed", type=int, default=20261118)
    parser.add_argument("--experiment-id", default="pilot-scenario")
    args = parser.parse_args()
    write_scenario(
        args.output,
        label=args.label,
        per_arm=args.per_arm,
        control_rate=args.control_rate,
        effect_pp=args.effect_pp,
        exposure_rate=args.exposure_rate,
        seed=args.seed,
        experiment_id=args.experiment_id,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
