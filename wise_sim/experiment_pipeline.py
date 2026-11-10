"""Synthetic experiment records, ontology mapping, validation, and ITT summaries."""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


PACKAGE_DIR = Path(__file__).resolve().parent
ONTOLOGY_PATH = PACKAGE_DIR / "wise_hillink_ontology_map.json"
SCHEMA_PATH = PACKAGE_DIR / "schemas" / "reservation_exposure.schema.json"
SYNTHETIC_SAMPLE_SIZE = 200
_Z_95 = 1.959963984540054


def load_ontology() -> dict[str, Any]:
    with open(ONTOLOGY_PATH, encoding="utf-8") as source:
        return json.load(source)


def load_schema_validator() -> Draft202012Validator:
    with open(SCHEMA_PATH, encoding="utf-8") as source:
        schema = json.load(source)
    ontology = load_ontology()
    ontology_uri = urljoin(schema["$id"], "../wise_hillink_ontology_map.json")
    registry = Registry().with_resource(
        ontology_uri,
        Resource(contents=ontology, specification=DRAFT202012),
    )
    return Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )


def map_ontology_concept(
    concept_type: str,
    source_system: str,
    source_code: str,
    display: str | None = None,
) -> dict[str, Any]:
    """Map only exact, registered codes; keep unknown source terms unmapped."""
    return _map_ontology_concept(
        load_ontology(), concept_type, source_system, source_code, display
    )


def _map_ontology_concept(
    ontology: Mapping[str, Any],
    concept_type: str,
    source_system: str,
    source_code: str,
    display: str | None = None,
) -> dict[str, Any]:
    mappings = ontology["canonical_terms"].get(concept_type)
    if mappings is None:
        raise ValueError(f"unsupported ontology concept type: {concept_type}")
    canonical_id = mappings.get(source_code)
    return {
        "source_system": source_system,
        "source_code": source_code,
        "display": display,
        "canonical_id": canonical_id,
        "mapping_status": "mapped" if canonical_id is not None else "unmapped",
        "mapping_basis": (
            f"WISE:Ontology:{concept_type}"
            if canonical_id is not None
            else None
        ),
    }


def _money(amount: float) -> dict[str, Any]:
    return {
        "amount": amount,
        "currency": "KRW",
        "unit": "per_stay",
        "tax_included": True,
    }


def generate_synthetic_records(
    n: int = SYNTHETIC_SAMPLE_SIZE,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Generate a deterministic, explicitly synthetic blocked-randomized fixture.

    The fixture has a known 9 percentage-point assignment ITT on booking and
    a KRW 21,600 assignment ITT on total revenue. Ten percent of assigned
    treatment offers are not shown; their outcomes follow the control potential
    outcome. This is test data only, not a Walkerhill demand estimate.
    """
    if n < SYNTHETIC_SAMPLE_SIZE or n % SYNTHETIC_SAMPLE_SIZE:
        raise ValueError(
            f"n must be a positive multiple of {SYNTHETIC_SAMPLE_SIZE}"
        )

    rng = random.Random(seed)
    pair_count = n // 2
    pair_types = (
        ["always_book"] * (pair_count * 3 // 10)
        + ["price_complier"] * (pair_count // 10)
    )
    pair_types.extend(["never_book"] * (pair_count - len(pair_types)))
    rng.shuffle(pair_types)

    strata_pair_counts = {
        "always_book": pair_count * 3 // 10,
        "price_complier": pair_count // 10,
        "never_book": pair_count - (pair_count * 3 // 10) - (pair_count // 10),
    }
    no_show_pair_limits = {
        stratum: count // 10 for stratum, count in strata_pair_counts.items()
    }
    no_show_pair_seen = {stratum: 0 for stratum in strata_pair_counts}

    base_time = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    stay_date = date(2026, 2, 1)
    ontology = load_ontology()
    building = _map_ontology_concept(
        ontology, "building", "WISE_SYNTHETIC", "GRAND", "Grand"
    )
    room_type = _map_ontology_concept(
        ontology, "room_type", "WISE_SYNTHETIC", "DLX-K", "Deluxe King"
    )
    records: list[dict[str, Any]] = []

    for pair_index, response_type in enumerate(pair_types):
        treatment_first = bool(rng.getrandbits(1))
        pair_is_no_show = (
            no_show_pair_seen[response_type]
            < no_show_pair_limits[response_type]
        )
        no_show_pair_seen[response_type] += 1

        for treatment_assignment in (False, True):
            assigned_treatment = (
                treatment_assignment if treatment_first
                else not treatment_assignment
            )
            arm = "treatment" if assigned_treatment else "control"
            is_exposed = not (assigned_treatment and pair_is_no_show)
            unit_number = pair_index * 2 + int(assigned_treatment)
            unit_id = f"synthetic-unit-{unit_number:06d}"
            assigned_at = base_time + timedelta(minutes=unit_number)
            offer_time = assigned_at + timedelta(seconds=30)
            treated_and_exposed = assigned_treatment and is_exposed

            if response_type == "always_book":
                booked = True
            elif response_type == "price_complier":
                booked = treated_and_exposed
            else:
                booked = False

            nightly_price = 180_000 if treated_and_exposed else 200_000
            total_revenue = nightly_price * 2 if booked else 0
            promotion = (
                _map_ontology_concept(
                    ontology,
                    "promotion",
                    "WISE_SYNTHETIC",
                    "package" if assigned_treatment else "none",
                )
                if is_exposed
                else None
            )
            offer_price = (
                {
                    "amount": nightly_price,
                    "currency": "KRW",
                    "unit": "per_night",
                    "tax_included": True,
                }
                if is_exposed
                else None
            )
            discount = (
                {
                    "kind": "percentage",
                    "value": 10,
                    "unit": "percent",
                }
                if treated_and_exposed
                else {
                    "kind": "none",
                    "value": None,
                    "unit": "not_applicable",
                }
                if is_exposed
                else None
            )
            exposed_at = offer_time.isoformat() if is_exposed else None
            booked_at = (
                (offer_time + timedelta(days=1)).isoformat() if booked else None
            )
            outcome_end = offer_time + timedelta(days=7)
            revenue = _money(total_revenue)
            record = {
                "schema_version": "1.0.0",
                "record_id": f"synthetic-record-{unit_number:06d}",
                "experiment": {
                    "experiment_id": "WISE:Synthetic:PackagePriceITT:v1",
                    "design": "synthetic_randomized",
                    "assignment_level": "customer",
                    "assignment_unit_id": unit_id,
                    "assigned_arm": arm,
                    "assignment_probability": 0.5,
                    "randomization_block_id": f"synthetic-block-{pair_index:06d}",
                    "randomization_strata": [],
                    "assigned_at": assigned_at.isoformat(),
                    "protocol_id": "WISE:Synthetic:PairedAssignment:v1",
                },
                "unit": {
                    "unit_id": unit_id,
                    "eligible": True,
                    "offer_time": offer_time.isoformat(),
                    "stay_date": stay_date.isoformat(),
                    "stay_end_date": (stay_date + timedelta(days=2)).isoformat(),
                },
                "offer": {
                    "exposure_status": (
                        "shown" if is_exposed else "eligible_not_shown"
                    ),
                    "exposed": is_exposed,
                    "exposed_at": exposed_at,
                    "product": None,
                    "room_type": room_type,
                    "building": building,
                    "promotion": promotion,
                    "price": offer_price,
                    "discount": discount,
                },
                "conditions": {
                    "lead_time_days": 31,
                    "weekday": stay_date.isoweekday(),
                    "season": None,
                    "events": [],
                    "availability": {
                        "status": "available",
                        "available": True,
                        "remaining_inventory": 100,
                        "inventory_unit": "room",
                    },
                },
                "outcome": {
                    "status": "complete",
                    "window_start": offer_time.isoformat(),
                    "window_end": outcome_end.isoformat(),
                    "booking_status": "booked" if booked else "not_booked",
                    "booked_at": booked_at,
                    "booking_id": (
                        f"synthetic-booking-{unit_number:06d}" if booked else None
                    ),
                    "cancel_status": "kept" if booked else "not_applicable",
                    "room_revenue": revenue,
                    "total_revenue": revenue,
                    "contribution_margin": _money(total_revenue * 0.7),
                },
                "data_quality": {
                    "completeness": "complete",
                    "missing_fields": [],
                    "currency_unit": "KRW",
                    "quality_flags": (
                        ["mapping_unresolved"] if room_type["canonical_id"] is None
                        else []
                    ),
                },
                "provenance": {
                    "data_origin": "synthetic_fixture",
                    "source_system": "WISE_SYNTHETIC",
                    "source_table": "synthetic_itt_fixture",
                    "source_record_id": None,
                    "extracted_at": "2026-01-09T12:00:00+00:00",
                    "transformation_version": "1.0.0",
                    "source_sha256": None,
                    "consent_or_legal_basis_ref": None,
                    "identifier_protection": {
                        "method": "synthetic",
                        "identity_mapping_access": "none",
                        "key_version": None,
                        "transformation_ref": "WISE:Synthetic:Fixture:v1",
                    },
                },
            }
            records.append(record)

    rng.shuffle(records)
    return records


def validate_experiment_records(
    records: Sequence[Mapping[str, Any]],
) -> None:
    """Validate schema and cross-record experimental-unit integrity."""
    if not records:
        raise ValueError("at least one experiment record is required")

    validator = load_schema_validator()
    seen_record_ids: set[str] = set()
    seen_unit_ids: set[str] = set()
    experiment_ids: set[str] = set()
    designs: set[str] = set()
    for index, record in enumerate(records):
        errors = sorted(
            validator.iter_errors(record),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if errors:
            error = errors[0]
            location = "/".join(str(part) for part in error.absolute_path) or "$"
            raise ValueError(
                f"record {index} ({location}): {error.message}"
            )

        record_id = record["record_id"]
        unit_id = record["unit"]["unit_id"]
        if record_id in seen_record_ids:
            raise ValueError(f"duplicate record_id: {record_id}")
        if unit_id in seen_unit_ids:
            raise ValueError(f"duplicate experimental unit: {unit_id}")
        if record["experiment"]["assignment_unit_id"] != unit_id:
            raise ValueError(
                f"record {record_id}: assignment unit and outcome unit differ"
            )
        seen_record_ids.add(record_id)
        seen_unit_ids.add(unit_id)
        experiment_ids.add(record["experiment"]["experiment_id"])
        designs.add(record["experiment"]["design"])

    if len(experiment_ids) != 1 or len(designs) != 1:
        raise ValueError("records must belong to one experiment and one design")


def estimate_itt(
    records: Sequence[Mapping[str, Any]],
    metric: str,
) -> dict[str, Any]:
    """Estimate assignment-arm difference in means, including non-exposure.

    The reported normal-approximation interval treats experimental units as
    independent; callers must use a cluster-aware analysis for cluster assignment.
    """
    if metric not in {"booking", "total_revenue"}:
        raise ValueError("metric must be 'booking' or 'total_revenue'")
    validate_experiment_records(records)

    experiment = records[0]["experiment"]
    if experiment["design"] not in {"randomized", "synthetic_randomized"}:
        raise ValueError("ITT requires randomized assignment")
    if experiment["assignment_level"] != "customer":
        raise ValueError("this ITT implementation requires customer-level assignment")

    probability_values = {
        record["experiment"]["assignment_probability"] for record in records
    }
    if len(probability_values) != 1:
        raise ValueError(
            "this ITT implementation requires a constant assignment probability"
        )

    arms: dict[str, list[float]] = {"control": [], "treatment": []}
    exposure_counts = {"control": 0, "treatment": 0}
    currencies: set[str] = set()
    money_units: set[str] = set()
    for record in records:
        arm = record["experiment"]["assigned_arm"]
        outcome = record["outcome"]
        if outcome["status"] != "complete":
            raise ValueError("ITT requires complete outcomes for every assigned unit")
        if arm not in arms:
            raise ValueError(f"unsupported assigned arm: {arm}")
        exposure_counts[arm] += int(record["offer"]["exposed"])

        if metric == "booking":
            status = outcome["booking_status"]
            if status not in {"booked", "not_booked"}:
                raise ValueError("booking ITT requires known booking outcomes")
            value = 1.0 if status == "booked" else 0.0
        else:
            money = outcome["total_revenue"]
            if money is None:
                raise ValueError("revenue ITT requires revenue for every unit")
            currencies.add(money["currency"])
            money_units.add(money["unit"])
            value = float(money["amount"])
        arms[arm].append(value)

    if not arms["control"] or not arms["treatment"]:
        raise ValueError("ITT requires units in both control and treatment arms")
    if metric == "total_revenue" and (
        len(currencies) != 1 or len(money_units) != 1
    ):
        raise ValueError("revenue ITT requires a consistent currency and unit")

    control_mean = sum(arms["control"]) / len(arms["control"])
    treatment_mean = sum(arms["treatment"]) / len(arms["treatment"])
    control_variance = sum(
        (value - control_mean) ** 2 for value in arms["control"]
    ) / max(len(arms["control"]) - 1, 1)
    treatment_variance = sum(
        (value - treatment_mean) ** 2 for value in arms["treatment"]
    ) / max(len(arms["treatment"]) - 1, 1)
    block_values = [
        record["experiment"]["randomization_block_id"] for record in records
    ]
    has_blocks = [block_id is not None for block_id in block_values]
    if any(has_blocks) and not all(has_blocks):
        raise ValueError("randomization blocks must be set on all records or none")
    if all(has_blocks):
        paired: dict[str, dict[str, float]] = {}
        for record in records:
            block_id = record["experiment"]["randomization_block_id"]
            arm = record["experiment"]["assigned_arm"]
            if arm not in {"control", "treatment"}:
                raise ValueError(f"unsupported assigned arm: {arm}")
            outcome = record["outcome"]
            if metric == "booking":
                value = 1.0 if outcome["booking_status"] == "booked" else 0.0
            else:
                value = float(outcome["total_revenue"]["amount"])
            pair = paired.setdefault(block_id, {})
            if arm in pair:
                raise ValueError(
                    "each randomization block must contain one unit per arm"
                )
            pair[arm] = value
        if any(set(pair) != {"control", "treatment"} for pair in paired.values()):
            raise ValueError(
                "each randomization block must contain one unit per arm"
            )
        paired_differences = [
            pair["treatment"] - pair["control"] for pair in paired.values()
        ]
        if len(paired_differences) < 2:
            raise ValueError("paired ITT requires at least two randomization blocks")
        paired_mean = sum(paired_differences) / len(paired_differences)
        paired_variance = sum(
            (value - paired_mean) ** 2 for value in paired_differences
        ) / (len(paired_differences) - 1)
        standard_error = math.sqrt(paired_variance / len(paired_differences))
        interval_method = "paired-block normal approximation"
    else:
        standard_error = math.sqrt(
            control_variance / len(arms["control"])
            + treatment_variance / len(arms["treatment"])
        )
        interval_method = "independent-arm normal approximation"
    difference = treatment_mean - control_mean

    result: dict[str, Any] = {
        "metric": metric,
        "estimand": (
            "mean outcome by assigned arm: treatment minus control (ITT)"
        ),
        "assignment_unit": experiment["assignment_level"],
        "units_per_arm": {
            "control": len(arms["control"]),
            "treatment": len(arms["treatment"]),
        },
        "randomization_blocks": len(paired) if all(has_blocks) else None,
        "exposed_units_per_arm": exposure_counts,
        "exposure_filter_applied": False,
        "control_mean": control_mean,
        "treatment_mean": treatment_mean,
        "itt_estimate": difference,
        "standard_error": standard_error,
        "confidence_level": 0.95,
        "confidence_interval": [
            difference - _Z_95 * standard_error,
            difference + _Z_95 * standard_error,
        ],
        "interval_method": interval_method,
        "synthetic_only": (
            experiment["design"] == "synthetic_randomized"
        ),
    }
    if metric == "booking":
        result["unit"] = "probability_difference"
    else:
        result["currency"] = next(iter(currencies))
        result["money_unit"] = next(iter(money_units))
    return result
