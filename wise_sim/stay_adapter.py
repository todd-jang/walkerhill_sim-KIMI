"""WISE - Hillink 계층적 진실의 원천 연계 어댑터.

L2(거시 수요/수용량)가 발급한 stay_id를 기반으로 Hillink L5(미시 동선/소비)와 연계하며,
stay_id 외래키(FK) 및 체크인~체크아웃 시간 경계 정합성을 엄격히 검증합니다.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

CONFIG_PATH = Path(__file__).parent / "wise_hillink_ontology_map.json"


class IntegrityViolationError(Exception):
    """stay_id 누락 또는 미등록 등 참조 정합성 위반."""
    pass


class TemporalViolationError(Exception):
    """체크아웃 이후 소비 등 시간 경계 위반."""
    pass


@dataclass(frozen=True)
class StayMaster:
    """L2 엔진이 독점 발급하는 불변 투숙 마스터 레코드."""
    stay_id: str
    persona_id: int
    archetype: str
    building: str
    room_number: str
    check_in_time: str
    check_out_time: str
    room_rate_krw: float
    package_benefits: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_ontology_map(config_path: Path | str = CONFIG_PATH) -> dict[str, Any]:
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_stay_masters(
    booked_decisions: Sequence[Any],
    personas: Sequence[Any],
    weekend_idx: int = 0,
    base_date_str: str = "2026-10-02",
    config_path: Path | str = CONFIG_PATH,
) -> list[StayMaster]:
    """L2 예약 확정 고객에게 stay_id 및 건물/객실을 배정하여 StayMaster 목록을 생성."""
    cfg = load_ontology_map(config_path)
    building_alloc = cfg["room_inventory_allocation"]["buildings"]
    archetype_map = cfg["archetype_to_profile_binding"]["mappings"]

    persona_dict = {p.id: p for p in personas}
    stays: list[StayMaster] = []

    # 건물별 객실 카운터
    room_counters = {"GRAND": 1, "VISTA": 1, "DOUGLAS": 1}
    base_dt = datetime.fromisoformat(base_date_str)

    for d in booked_decisions:
        if not getattr(d, "booked", False) or getattr(d, "abstained", False):
            continue

        p = persona_dict.get(d.persona_id)
        archetype = getattr(d, "archetype", p.archetype if p else "family")
        default_bld = archetype_map.get(archetype, {}).get("default_building", "GRAND")

        # 건물 용량 체크 및 폴백
        bld = default_bld
        limit = building_alloc.get(bld, {}).get("allocated_rooms", 367)
        if room_counters[bld] > limit:
            bld = "GRAND"

        seq = room_counters[bld]
        room_counters[bld] += 1

        stay_id = f"STAY_W{weekend_idx}_{bld[:2]}_{seq:03d}_P{d.persona_id}"
        room_no = f"{bld[:2]}-{seq:03d}"

        stay_nights = getattr(p, "stay_nights", 2)
        cin_dt = base_dt.replace(hour=15, minute=0, second=0)
        cout_dt = (base_dt + timedelta(days=stay_nights)).replace(hour=11, minute=0, second=0)

        benefits = cfg["promo_to_sku_binding"].get(getattr(d, "promo", "package"), {}).get("included_skus", [])

        stays.append(
            StayMaster(
                stay_id=stay_id,
                persona_id=d.persona_id,
                archetype=archetype,
                building=bld,
                room_number=room_no,
                check_in_time=cin_dt.isoformat(),
                check_out_time=cout_dt.isoformat(),
                room_rate_krw=float(getattr(d, "spend", 350000.0)),
                package_benefits=benefits,
            )
        )

    return stays


def export_confirmed_cohort(
    stays: Sequence[StayMaster],
    policy_promo: str = "package",
    config_path: Path | str = CONFIG_PATH,
) -> dict[str, Any]:
    """Hillink L5 시뮬레이터가 즉시 소비할 수 있는 코호트 JSON 구조체로 직렬화."""
    cfg = load_ontology_map(config_path)
    mappings = cfg["archetype_to_profile_binding"]["mappings"]
    promo_skus = cfg["promo_to_sku_binding"].get(policy_promo, {}).get("included_skus", [])

    cohort = []
    for s in stays:
        pool = mappings.get(s.archetype, {}).get("profile_pool", ["default_guest"])
        chosen_profile = pool[s.persona_id % len(pool)]

        cohort.append({
            "stay_id": s.stay_id,
            "profile_id": chosen_profile,
            "archetype": s.archetype,
            "building": s.building,
            "room_number": s.room_number,
            "check_in_time": s.check_in_time,
            "check_out_time": s.check_out_time,
            "included_skus": promo_skus,
        })

    return {
        "cohort_size": len(cohort),
        "policy_promo": policy_promo,
        "guests": cohort,
    }


def validate_and_join_l5_ledger(
    stays: Sequence[StayMaster],
    l5_receipts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Hillink L5 소비 영수증의 외래키(stay_id) 및 체크아웃 시간 경계 검증."""
    stay_map = {s.stay_id: s for s in stays}
    verified_records = []

    for idx, r in enumerate(l5_receipts):
        sid = r.get("stay_id")
        if not sid or sid not in stay_map:
            raise IntegrityViolationError(
                f"영수증 #{idx}: 미등록 또는 고아 stay_id ({sid}) 기록 불가"
            )

        stay = stay_map[sid]
        ordered_at = str(r.get("ordered_at", ""))
        if not ordered_at:
            raise IntegrityViolationError(f"영수증 #{idx} ({sid}): 주문 시각(ordered_at) 누락")

        # 시간 경계 검증
        if not (stay.check_in_time <= ordered_at <= stay.check_out_time):
            raise TemporalViolationError(
                f"영수증 #{idx} ({sid}): 주문 시각({ordered_at})이 투숙 기간({stay.check_in_time} ~ {stay.check_out_time}) 밖임"
            )

        verified_records.append({
            **dict(r),
            "persona_id": stay.persona_id,
            "archetype": stay.archetype,
            "building": stay.building,
        })

    return verified_records

