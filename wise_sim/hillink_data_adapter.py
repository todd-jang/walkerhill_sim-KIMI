"""Extract Hillink public reference data without treating it as PMS or causal data."""
from __future__ import annotations

import csv
import math
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from stay_adapter import load_ontology_map
except ImportError:
    from .stay_adapter import load_ontology_map


GMM_REQUIRED_FIELDS = (
    "booking_count",
    "stay_nights",
    "lead_time_days",
    "avg_room_rate",
    "price_delta",
    "promo",
    "weekend",
    "booked",
)


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise ValueError(f"{path}: CSV must contain at least one data row")
    return rows


def extract_hillink_inputs(
    room_reference_path: str | Path,
    persona_sample_path: str | Path,
    evidence_chain_path: str | Path,
) -> dict[str, Any]:
    """Summarize the three public Hillink inputs as ontology-linked evidence.

    The output intentionally does not manufacture WISE Personas, PMS booking
    records, price responses, or causal estimates from aggregate/narrative data.
    """
    ontology = load_ontology_map()
    source_contract = ontology["external_reference_inputs"]
    building_map = source_contract["room_reference"]["building_mapping"]

    room_rows = _read_csv(room_reference_path)
    required_room_fields = {
        "날짜", "건물", "객실수", "기준OCC_BASE", "상한전요청객실박",
        "판매객실박", "참고수요계수", "공급상한해당", "계수성격",
    }
    room_fields = set(room_rows[0])
    if not required_room_fields.issubset(room_fields):
        raise ValueError(
            "room reference CSV missing columns: "
            + ", ".join(sorted(required_room_fields - room_fields))
        )

    room_daily: dict[tuple[str, str], dict[str, Any]] = {}
    source_capacities: dict[str, int] = {}
    notes: set[str] = set()
    for row_index, row in enumerate(room_rows, start=2):
        date = row["날짜"].strip()
        source_building = row["건물"].strip()
        building = building_map.get(source_building)
        if not date or building is None:
            raise ValueError(
                f"room reference row {row_index}: invalid date/building"
            )
        try:
            rooms = int(row["객실수"])
            base_occ = float(row["기준OCC_BASE"])
            requested = int(row["상한전요청객실박"])
            sold = int(row["판매객실박"])
            demand_factor = float(row["참고수요계수"])
        except ValueError as exc:
            raise ValueError(
                f"room reference row {row_index}: invalid numeric value"
            ) from exc
        if (rooms <= 0 or requested < 0 or sold < 0 or sold > rooms
                or not 0 <= base_occ <= 1
                or not math.isfinite(demand_factor) or demand_factor < 0):
            raise ValueError(
                f"room reference row {row_index}: value outside valid range"
            )
        if row["공급상한해당"] not in {"True", "False"}:
            raise ValueError(
                f"room reference row {row_index}: 공급상한해당 must be True or False"
            )
        key = (date, building)
        if key in room_daily:
            raise ValueError(
                f"room reference row {row_index}: duplicate date/building"
            )
        if building in source_capacities and source_capacities[building] != rooms:
            raise ValueError(
                f"room reference row {row_index}: room count changes for {building}"
            )
        source_capacities[building] = rooms
        notes.add(row["계수성격"].strip())
        room_daily[key] = {
            "date": date,
            "building": building,
            "rooms": rooms,
            "baseline_occupancy": base_occ,
            "requested_room_nights": requested,
            "sold_room_nights": sold,
            "reference_demand_factor": demand_factor,
            "supply_cap_applied": row["공급상한해당"] == "True",
        }

    persona_rows = _read_csv(persona_sample_path)
    persona_required = {"표본ID", "게시일", "방문목적", "이용시설", "연령검증상태"}
    if not persona_required.issubset(persona_rows[0]):
        raise ValueError(
            "persona sample CSV missing columns: "
            + ", ".join(sorted(persona_required - set(persona_rows[0])))
        )
    purpose_counts = Counter(row["방문목적"].strip() for row in persona_rows)
    age_status_counts = Counter(
        row["연령검증상태"].strip() for row in persona_rows
    )

    evidence_rows = _read_csv(evidence_chain_path)
    evidence_required = {"근거ID", "확실성", "반대사례_한계", "다음확인"}
    if not evidence_required.issubset(evidence_rows[0]):
        raise ValueError(
            "evidence chain CSV missing columns: "
            + ", ".join(sorted(evidence_required - set(evidence_rows[0])))
        )
    confidence_counts = Counter(row["확실성"].strip() for row in evidence_rows)

    external_total = sum(source_capacities.values())
    wise_total = int(ontology["room_inventory_allocation"]["total_rooms"])
    declared_source_total = int(
        source_contract["room_reference"]["source_room_count"]
    )
    available_persona_fields = set(persona_rows[0])
    missing_gmm_fields = sorted(
        set(GMM_REQUIRED_FIELDS) - available_persona_fields
    )

    return {
        "ontology_contract": ontology["contract_name"],
        "source_repository": source_contract["repository"],
        "room_reference": {
            "row_count": len(room_rows),
            "date_count": len({row["date"] for row in room_daily.values()}),
            "building_room_counts": dict(sorted(source_capacities.items())),
            "source_total_rooms": external_total,
            "matches_declared_source_total": external_total == declared_source_total,
            "wise_simulation_total_rooms": wise_total,
            "room_count_matches_simulation": external_total == wise_total,
            "requested_room_nights": sum(
                row["requested_room_nights"] for row in room_daily.values()
            ),
            "sold_room_nights": sum(
                row["sold_room_nights"] for row in room_daily.values()
            ),
            "supply_cap_rows": sum(
                row["supply_cap_applied"] for row in room_daily.values()
            ),
            "reference_notes": sorted(note for note in notes if note),
            "use": "ontology-linked aggregate reference; not individual bookings",
        },
        "persona_behavior_evidence": {
            "row_count": len(persona_rows),
            "purpose_counts": dict(sorted(purpose_counts.items())),
            "age_validation_counts": dict(sorted(age_status_counts.items())),
            "use": "descriptive behavior evidence; not calibrated WISE Personas",
        },
        "evidence_chain": {
            "row_count": len(evidence_rows),
            "confidence_counts": dict(sorted(confidence_counts.items())),
            "use": "qualitative hypotheses and caveats; not independent outcome data",
        },
        "model_compatibility": {
            "can_fit_gmm_personas": False,
            "missing_gmm_fields": missing_gmm_fields,
            "causal_effect_estimable": False,
            "missing_causal_fields": [
                "unit-level treatment assignment or randomized offer",
                "actual price offered/exposed before the outcome",
                "booked and non-booked eligible offers",
                "unit-level booking/revenue outcome linked to exposure",
            ],
        },
        "warnings": [
            "Hillink public inputs do not contain individual PMS booking/exposure records or randomized experiment assignments.",
            "The room reference is explicitly a mixed date/purpose/event reference coefficient, not a clean occupancy series for causal estimation.",
            "The room reference capacity differs from WISE's configured simulation capacity; it is reported, not silently applied.",
            "No public input is converted into synthetic reservation outcomes or causal estimates.",
        ],
    }
