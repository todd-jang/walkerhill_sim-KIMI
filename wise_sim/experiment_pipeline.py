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


def _parse_aware_datetime(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed


def _ledger_amount(row: Mapping[str, Any], field: str) -> tuple[float, str]:
    amount = row.get("amount")
    currency = row.get("currency")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        raise ValueError(f"{field}.amount must be numeric")
    if not math.isfinite(float(amount)) or amount < 0:
        raise ValueError(f"{field}.amount must be finite and non-negative")
    if not isinstance(currency, str) or not currency:
        raise ValueError(f"{field}.currency must be a non-empty string")
    return float(amount), currency


def build_experiment_unit_facts(
    assignments: Sequence[Mapping[str, Any]],
    *,
    exposures: Sequence[Mapping[str, Any]],
    bookings: Sequence[Mapping[str, Any]],
    payments: Sequence[Mapping[str, Any]],
    cancellations: Sequence[Mapping[str, Any]],
    refunds: Sequence[Mapping[str, Any]],
    costs: Sequence[Mapping[str, Any]],
    stays: Sequence[Mapping[str, Any]],
    operations: Sequence[Mapping[str, Any]],
    surveys: Sequence[Mapping[str, Any]],
    analysis_cutoff_at: str,
    source_complete_through: str,
    attribution_window_days: int,
    data_version: str,
) -> dict[str, Any]:
    """Build one immutable-assignment-centered row per randomized unit.

    Event rows use pseudonymous ``assignment_id``/``booking_id`` values and
    timestamps with timezone offsets. Booking attribution is fixed from
    ``assigned_at`` through ``assigned_at + attribution_window_days``. The
    caller must attest that all supplied source ledgers are complete through
    ``analysis_cutoff_at`` using ``source_complete_through``.
    """
    if not assignments:
        raise ValueError("at least one assignment is required")
    if attribution_window_days < 0:
        raise ValueError("attribution_window_days must be non-negative")
    if not data_version:
        raise ValueError("data_version must be a non-empty string")
    cutoff = _parse_aware_datetime(analysis_cutoff_at, "analysis_cutoff_at")
    source_complete = _parse_aware_datetime(
        source_complete_through, "source_complete_through"
    )
    if source_complete < cutoff:
        raise ValueError("source ledgers must be complete through the analysis cutoff")

    assignment_by_id: dict[str, Mapping[str, Any]] = {}
    assignment_times: dict[str, datetime] = {}
    facts: dict[str, dict[str, Any]] = {}
    experiment_ids: set[str] = set()
    designs: set[str] = set()
    for row in assignments:
        assignment_id = row.get("assignment_id")
        experiment_id = row.get("experiment_id")
        unit_id = row.get("unit_id")
        segment = row.get("segment")
        arm = row.get("assigned_variant")
        design = row.get("design")
        data_origin = row.get("data_origin")
        probability = row.get("assignment_probability")
        if not all(isinstance(value, str) and value for value in (
            assignment_id, experiment_id, unit_id, segment
        )):
            raise ValueError("assignment IDs, experiment, unit, and segment are required")
        if not isinstance(arm, str) or arm not in {"control", "treatment"}:
            raise ValueError(f"unsupported assigned_variant: {arm}")
        if not isinstance(design, str) or design not in {
            "randomized", "synthetic_randomized"
        }:
            raise ValueError("assignment design must be randomized")
        if not isinstance(data_origin, str) or data_origin not in {
            "randomized_experiment", "synthetic_fixture"
        }:
            raise ValueError("assignment data_origin must identify real or synthetic data")
        if (design == "randomized") != (data_origin == "randomized_experiment"):
            raise ValueError("assignment design and data_origin are inconsistent")
        for version_key in (
            "protocol_id", "eligibility_version", "assignment_logic_version"
        ):
            if not isinstance(row.get(version_key), str) or not row[version_key]:
                raise ValueError(f"assignment {version_key} is required")
        if (
            not isinstance(probability, (int, float))
            or isinstance(probability, bool)
            or not 0 < probability < 1
        ):
            raise ValueError("assignment_probability must be between 0 and 1")
        block_id = row.get("randomization_block_id")
        if block_id is not None and (not isinstance(block_id, str) or not block_id):
            raise ValueError("randomization_block_id must be a non-empty string or null")
        if assignment_id in assignment_by_id:
            raise ValueError(f"duplicate assignment_id: {assignment_id}")
        assigned_at = _parse_aware_datetime(
            row.get("assigned_at"), f"assignment {assignment_id}.assigned_at"
        )
        outcome_matures_at = _parse_aware_datetime(
            row.get("outcome_matures_at"),
            f"assignment {assignment_id}.outcome_matures_at",
        )
        if outcome_matures_at < assigned_at:
            raise ValueError(
                f"assignment {assignment_id}.outcome_matures_at precedes assignment"
            )
        if assigned_at > cutoff:
            raise ValueError(f"assignment {assignment_id} is after the analysis cutoff")
        assignment_by_id[assignment_id] = row
        assignment_times[assignment_id] = assigned_at
        experiment_ids.add(experiment_id)
        designs.add(design)
        facts[assignment_id] = {
            "experiment_id": experiment_id,
            "design": design,
            "data_origin": data_origin,
            "synthetic_only": design == "synthetic_randomized",
            "assignment_id": assignment_id,
            "unit_id": unit_id,
            "segment": segment,
            "assigned_variant": arm,
            "assignment_probability": float(probability),
            "assigned_at": assigned_at.isoformat(),
            "outcome_matures_at": outcome_matures_at.isoformat(),
            "randomization_block_id": row.get("randomization_block_id"),
            "protocol_id": row["protocol_id"],
            "eligibility_version": row["eligibility_version"],
            "assignment_logic_version": row["assignment_logic_version"],
            "first_exposed_at": None,
            "first_exposed_variant": None,
            "first_exposure_page": None,
            "first_exposure_channel": None,
            "first_exposure_price": None,
            "first_exposure_inventory_snapshot": None,
            "exposure_history": [],
            "exposure_count": 0,
            "exposed_flag": False,
            "exposure_variant_mismatch_count": 0,
            "booking_count": 0,
            "paid_booking_flag": 0,
            "net_booking_flag": 0,
            "fully_cancelled_flag": 0,
            "cancelled_booking_count": 0,
            "cancellation_event_count": 0,
            "cancellation_fee_amount": 0.0,
            "gross_payment": 0.0,
            "refund_amount": 0.0,
            "refund_status_event_count": 0,
            "net_revenue": 0.0,
            "variable_cost": 0.0,
            "contribution_margin": 0.0,
            "stay_count": 0,
            "stayed_flag": 0,
            "complaint_count": 0,
            "safety_incident_count": 0,
            "safety_incidents_by_severity": {},
            "survey_invited_flag": 0,
            "survey_responded_flag": 0,
            "satisfaction_score": None,
            "recommendation_score": None,
            "booking_outcome_complete": False,
            "financial_outcome_complete": False,
            "operations_outcome_complete": False,
            "survey_outcome_complete": False,
            "analysis_cutoff_at": cutoff.isoformat(),
            "data_version": data_version,
        }
    if len(experiment_ids) != 1:
        raise ValueError("assignments must belong to one experiment")
    if len(designs) != 1:
        raise ValueError("assignments must use one randomization design")
    if len({row.get("unit_id") for row in assignments}) != len(assignments):
        raise ValueError("each unit may be assigned only once per experiment")

    exposure_event_ids: set[str] = set()
    for exposure in exposures:
        event_id = exposure.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("exposure event_id is required")
        if event_id in exposure_event_ids:
            raise ValueError(f"duplicate exposure event_id: {event_id}")
        exposure_event_ids.add(event_id)
        assignment_id = exposure.get("assignment_id")
        if not isinstance(assignment_id, str):
            raise ValueError(f"exposure {event_id} requires assignment_id")
        if assignment_id not in facts:
            raise ValueError(f"exposure references unknown assignment: {assignment_id}")
        exposed_at = _parse_aware_datetime(
            exposure.get("exposed_at"), f"exposure {assignment_id}.exposed_at"
        )
        if exposed_at > cutoff:
            continue
        if exposed_at < assignment_times[assignment_id]:
            raise ValueError(f"exposure {event_id} precedes its assignment")
        rendered_variant = exposure.get("rendered_variant")
        if not isinstance(rendered_variant, str) or rendered_variant not in {
            "control", "treatment"
        }:
            raise ValueError(f"exposure {event_id} has an invalid rendered_variant")
        fact = facts[assignment_id]
        fact["exposure_count"] += 1
        fact["exposed_flag"] = True
        exposure_price = exposure.get("price")
        if exposure_price is not None:
            if not isinstance(exposure_price, Mapping):
                raise ValueError(f"exposure {event_id}.price must be an object")
            amount, currency = _ledger_amount(
                exposure_price,
                f"exposure {event_id}.price",
            )
            exposure_price = dict(exposure_price)
            exposure_price["amount"] = amount
            exposure_price["currency"] = currency
        inventory_snapshot = exposure.get("inventory_snapshot")
        if inventory_snapshot is not None and not isinstance(inventory_snapshot, Mapping):
            raise ValueError(f"exposure {event_id}.inventory_snapshot must be an object")
        history_entry = {
            "event_id": event_id,
            "exposed_at": exposed_at.isoformat(),
            "rendered_variant": exposure["rendered_variant"],
            "page": exposure.get("page"),
            "channel": exposure.get("channel"),
            "price": exposure_price,
            "inventory_snapshot": (
                dict(inventory_snapshot) if inventory_snapshot is not None else None
            ),
        }
        fact["exposure_history"].append(history_entry)
        if fact["first_exposed_at"] is None or exposed_at < _parse_aware_datetime(
            fact["first_exposed_at"], f"assignment {assignment_id}.first_exposed_at"
        ):
            fact["first_exposed_at"] = exposed_at.isoformat()
            fact["first_exposed_variant"] = exposure["rendered_variant"]
            fact["first_exposure_page"] = exposure.get("page")
            fact["first_exposure_channel"] = exposure.get("channel")
            fact["first_exposure_price"] = exposure_price
            fact["first_exposure_inventory_snapshot"] = (
                dict(inventory_snapshot) if inventory_snapshot is not None else None
            )
        if exposure.get("rendered_variant") != fact["assigned_variant"]:
            fact["exposure_variant_mismatch_count"] += 1

    booking_by_id: dict[str, Mapping[str, Any]] = {}
    booking_assignment_candidate: dict[str, str] = {}
    pre_assignment_booking_ids: set[str] = set()
    unattributed_bookings = 0
    pre_assignment_bookings = 0
    for booking in bookings:
        booking_id = booking.get("booking_id")
        if not isinstance(booking_id, str) or not booking_id:
            raise ValueError("booking_id is required")
        if booking_id in booking_by_id:
            raise ValueError(f"duplicate booking_id: {booking_id}")
        booking_by_id[booking_id] = booking
        assignment_id = booking.get("assignment_id")
        if assignment_id is not None and not isinstance(assignment_id, str):
            raise ValueError(f"booking {booking_id}.assignment_id must be a string or null")
        if assignment_id is None or assignment_id not in facts:
            unattributed_bookings += 1
        else:
            booking_assignment_candidate[booking_id] = assignment_id
            booked_at = _parse_aware_datetime(
                booking.get("booked_at"), f"booking {booking_id}.booked_at"
            )
            if booked_at < assignment_times[assignment_id]:
                pre_assignment_bookings += 1
                pre_assignment_booking_ids.add(booking_id)
                booking_assignment_candidate.pop(booking_id)

    payment_by_id: dict[str, Mapping[str, Any]] = {}
    payment_amounts: dict[str, tuple[float, str]] = {}
    payment_times: dict[str, datetime] = {}
    payments_by_booking: dict[str, list[str]] = {}
    for payment in payments:
        payment_id = payment.get("payment_id")
        if not isinstance(payment_id, str) or not payment_id:
            raise ValueError("payment_id is required")
        if payment_id in payment_by_id:
            raise ValueError(f"duplicate payment_id: {payment_id}")
        payment_by_id[payment_id] = payment
        booking_id = payment.get("booking_id")
        if not isinstance(booking_id, str):
            raise ValueError(f"payment {payment_id} requires booking_id")
        if booking_id not in booking_by_id:
            raise ValueError(f"payment references unknown booking: {booking_id}")
        status = payment.get("payment_status")
        if status not in {"succeeded", "failed"}:
            raise ValueError(f"payment {payment_id} has invalid payment_status")
        if status == "failed":
            continue
        paid_at = _parse_aware_datetime(
            payment.get("paid_at"), f"payment {payment_id}.paid_at"
        )
        amount, currency = _ledger_amount(payment, f"payment {payment_id}")
        if paid_at > cutoff:
            continue
        booking_time = _parse_aware_datetime(
            booking_by_id[booking_id].get("booked_at"),
            f"booking {booking_id}.booked_at",
        )
        if paid_at < booking_time:
            raise ValueError(f"payment precedes booking {booking_id}")
        payment_amounts[payment_id] = (amount, currency)
        payment_times[payment_id] = paid_at
        payments_by_booking.setdefault(booking_id, []).append(payment_id)

    booking_assignment: dict[str, str] = {}
    booking_currency: dict[str, str] = {}
    booking_first_paid_at: dict[str, datetime] = {}
    out_of_window_bookings = 0
    unattributed_payments = 0
    pre_assignment_payments = 0
    for booking_id, payment_ids in payments_by_booking.items():
        assignment_id = booking_assignment_candidate.get(booking_id)
        if assignment_id is None:
            if booking_id in pre_assignment_booking_ids:
                pre_assignment_payments += len(payment_ids)
            else:
                unattributed_payments += len(payment_ids)
            continue
        assigned_at = assignment_times[assignment_id]
        in_window = [
            payment_times[payment_id]
            for payment_id in payment_ids
            if assigned_at <= payment_times[payment_id]
            <= assigned_at + timedelta(days=attribution_window_days)
        ]
        if not in_window:
            out_of_window_bookings += 1
            continue
        booking_assignment[booking_id] = assignment_id
        booking_first_paid_at[booking_id] = min(
            payment_times[payment_id] for payment_id in payment_ids
        )
        currencies_for_booking = {
            payment_amounts[payment_id][1] for payment_id in payment_ids
        }
        if len(currencies_for_booking) != 1:
            raise ValueError(f"mixed payment currencies for booking {booking_id}")
        booking_currency[booking_id] = next(iter(currencies_for_booking))
        fact = facts[assignment_id]
        fact["booking_count"] += 1
        fact["gross_payment"] += sum(
            payment_amounts[payment_id][0] for payment_id in payment_ids
        )

    attributed_payment_ids = {
        payment_id
        for booking_id in booking_assignment
        for payment_id in payments_by_booking[booking_id]
    }

    cancellation_history: dict[str, list[tuple[datetime, str]]] = {}
    cancellation_event_ids: set[str] = set()
    for cancellation in cancellations:
        event_id = cancellation.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("cancellation event_id is required")
        if event_id in cancellation_event_ids:
            raise ValueError(f"duplicate cancellation event_id: {event_id}")
        cancellation_event_ids.add(event_id)
        booking_id = cancellation.get("booking_id")
        if not isinstance(booking_id, str):
            raise ValueError(f"cancellation event {event_id} requires booking_id")
        if booking_id not in booking_by_id:
            raise ValueError(f"cancellation references unknown booking: {booking_id}")
        cancelled_at = _parse_aware_datetime(
            cancellation.get("cancelled_at"), f"cancellation {booking_id}.cancelled_at"
        )
        status = cancellation.get("status")
        if not isinstance(status, str) or status not in {"cancelled", "reinstated"}:
            raise ValueError(f"cancellation event {event_id} has invalid status")
        fee_amount = cancellation.get("fee_amount", 0)
        if (
            not isinstance(fee_amount, (int, float))
            or isinstance(fee_amount, bool)
            or not math.isfinite(float(fee_amount))
            or fee_amount < 0
        ):
            raise ValueError(f"cancellation event {event_id} has invalid fee_amount")
        fee_currency = cancellation.get("currency")
        if fee_amount and (not isinstance(fee_currency, str) or not fee_currency):
            raise ValueError(f"cancellation event {event_id} fee currency is required")
        if cancelled_at <= cutoff and booking_id in booking_assignment:
            if cancelled_at < booking_first_paid_at[booking_id]:
                raise ValueError(f"cancellation precedes payment for booking {booking_id}")
            cancellation_history.setdefault(booking_id, []).append((cancelled_at, cancellation))

    cancelled_booking_ids: set[str] = set()
    for booking_id, history in cancellation_history.items():
        history.sort(key=lambda item: item[0])
        if len({event_time for event_time, _ in history}) != len(history):
            raise ValueError(f"ambiguous cancellation status timestamps for booking {booking_id}")
        assignment_id = booking_assignment[booking_id]
        facts[assignment_id]["cancellation_event_count"] += len(history)
        latest_status = history[-1][1]["status"]
        if latest_status == "cancelled":
            cancelled_booking_ids.add(booking_id)
            fee_amount = float(history[-1][1].get("fee_amount", 0))
            fee_currency = history[-1][1].get("currency")
            if fee_amount and fee_currency != booking_currency[booking_id]:
                raise ValueError(
                    f"cancellation fee currency differs from booking {booking_id}"
                )
            facts[assignment_id]["cancellation_fee_amount"] += fee_amount

    refund_event_ids: set[str] = set()
    refund_histories: dict[str, list[tuple[datetime, Mapping[str, Any]]]] = {}
    refund_identity: dict[str, tuple[str, str, float, str]] = {}
    for refund in refunds:
        event_id = refund.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("refund event_id is required")
        if event_id in refund_event_ids:
            raise ValueError(f"duplicate refund event_id: {event_id}")
        refund_event_ids.add(event_id)
        refund_id = refund.get("refund_id")
        if not isinstance(refund_id, str) or not refund_id:
            raise ValueError("refund_id is required")
        booking_id = refund.get("booking_id")
        payment_id = refund.get("payment_id")
        if not isinstance(booking_id, str) or not isinstance(payment_id, str):
            raise ValueError(f"refund event {event_id} requires booking_id and payment_id")
        if booking_id not in booking_by_id:
            raise ValueError(f"refund references unknown booking: {booking_id}")
        if payment_id not in payment_by_id:
            raise ValueError(f"refund references unknown payment: {payment_id}")
        if payment_by_id[payment_id].get("booking_id") != booking_id:
            raise ValueError(f"refund booking and payment do not match: {payment_id}")
        status = refund.get("status")
        if not isinstance(status, str) or status not in {
            "pending", "completed", "failed", "reversed"
        }:
            raise ValueError(f"refund event {event_id} has invalid status")
        event_at = _parse_aware_datetime(
            refund.get("event_at"), f"refund event {event_id}.event_at"
        )
        amount, currency = _ledger_amount(refund, f"refund {refund_id}")
        identity = (booking_id, payment_id, amount, currency)
        if refund_id in refund_identity and refund_identity[refund_id] != identity:
            raise ValueError(f"refund identity or amount changed for refund {refund_id}")
        refund_identity[refund_id] = identity
        if event_at <= cutoff:
            refund_histories.setdefault(refund_id, []).append((event_at, refund))

    refunds_by_payment: dict[str, float] = {}
    effective_refunds: list[dict[str, Any]] = []
    for refund_id, history in refund_histories.items():
        history.sort(key=lambda item: item[0])
        if len({event_at for event_at, _ in history}) != len(history):
            raise ValueError(f"ambiguous refund status timestamps for refund {refund_id}")
        latest = history[-1][1]
        booking_id, payment_id, amount, currency = refund_identity[refund_id]
        if payment_id in attributed_payment_ids:
            facts[booking_assignment[booking_id]]["refund_status_event_count"] += len(history)
        if latest["status"] != "completed":
            continue
        if payment_id not in payment_amounts:
            raise ValueError(
                f"completed refund references a payment not successful in the snapshot: {payment_id}"
            )
        payment_amount, payment_currency = payment_amounts[payment_id]
        if currency != payment_currency:
            raise ValueError(f"refund currency differs from payment {payment_id}")
        completed_at = history[-1][0]
        if completed_at < payment_times[payment_id]:
            raise ValueError(f"refund precedes payment for payment {payment_id}")
        refunds_by_payment[payment_id] = refunds_by_payment.get(payment_id, 0.0) + amount
        if refunds_by_payment[payment_id] > payment_amount:
            raise ValueError(f"refunds exceed payment for payment {payment_id}")
        effective_refunds.append({
            "refund_id": refund_id,
            "booking_id": booking_id,
            "payment_id": payment_id,
            "refunded_at": completed_at.isoformat(),
            "amount": amount,
            "currency": currency,
        })
        if payment_id in attributed_payment_ids:
            facts[booking_assignment[booking_id]]["refund_amount"] += amount

    for booking_id, assignment_id in booking_assignment.items():
        for payment_id in payments_by_booking[booking_id]:
            payment_amount, _ = payment_amounts[payment_id]
            payment_refunds = refunds_by_payment.get(payment_id, 0.0)
            facts[assignment_id]["net_revenue"] += payment_amount - payment_refunds
        if booking_id in cancelled_booking_ids:
            facts[assignment_id]["cancelled_booking_count"] += 1
        else:
            facts[assignment_id]["net_booking_flag"] = 1

    for assignment_id, fact in facts.items():
        fact["paid_booking_flag"] = int(fact["booking_count"] > 0)
        fact["fully_cancelled_flag"] = int(
            fact["booking_count"] > 0
            and fact["cancelled_booking_count"] == fact["booking_count"]
        )
        fact["currency"] = "KRW"

    cost_event_ids: set[str] = set()
    for cost in costs:
        event_id = cost.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("cost event_id is required")
        if event_id in cost_event_ids:
            raise ValueError(f"duplicate cost event_id: {event_id}")
        cost_event_ids.add(event_id)
        booking_id = cost.get("booking_id")
        if not isinstance(booking_id, str):
            raise ValueError(f"cost event {event_id} requires booking_id")
        if booking_id not in booking_by_id:
            raise ValueError(f"cost references unknown booking: {booking_id}")
        incurred_at = _parse_aware_datetime(
            cost.get("incurred_at"), f"cost {booking_id}.incurred_at"
        )
        if incurred_at > cutoff or booking_id not in booking_assignment:
            continue
        paid_at = booking_first_paid_at[booking_id]
        if incurred_at < paid_at:
            raise ValueError(f"cost precedes payment for booking {booking_id}")
        amount, currency = _ledger_amount(cost, f"cost {booking_id}")
        if currency != booking_currency[booking_id]:
            raise ValueError(f"cost currency differs from booking {booking_id}")
        facts[booking_assignment[booking_id]]["variable_cost"] += amount

    for assignment_id, fact in facts.items():
        booking_currencies = {
            booking_currency[booking_id]
            for booking_id, linked_assignment in booking_assignment.items()
            if linked_assignment == assignment_id
        }
        if len(booking_currencies) > 1:
            raise ValueError(f"mixed booking currencies for assignment {assignment_id}")
        fact["currency"] = next(iter(booking_currencies), "KRW")
        fact["contribution_margin"] = fact["net_revenue"] - fact["variable_cost"]

    stay_event_ids: set[str] = set()
    unmatched_stays = 0
    incomplete_stays = 0
    for stay in stays:
        event_id = stay.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("stay event_id is required")
        if event_id in stay_event_ids:
            raise ValueError(f"duplicate stay event_id: {event_id}")
        stay_event_ids.add(event_id)
        booking_id = stay.get("booking_id")
        if not isinstance(booking_id, str):
            raise ValueError(f"stay {event_id} requires booking_id")
        if booking_id not in booking_by_id:
            raise ValueError(f"stay references unknown booking: {booking_id}")
        checked_in_at = _parse_aware_datetime(
            stay.get("checked_in_at"), f"stay {event_id}.checked_in_at"
        )
        checked_out_at = _parse_aware_datetime(
            stay.get("checked_out_at"), f"stay {event_id}.checked_out_at"
        )
        if checked_out_at < checked_in_at:
            raise ValueError(f"stay {event_id} checks out before check-in")
        if checked_in_at > cutoff:
            continue
        if checked_out_at > cutoff:
            incomplete_stays += 1
            continue
        assignment_id = booking_assignment.get(booking_id)
        if assignment_id is None:
            unmatched_stays += 1
            continue
        if checked_in_at < assignment_times[assignment_id]:
            raise ValueError(f"stay {event_id} begins before assignment")
        fact = facts[assignment_id]
        fact["stay_count"] += 1
        fact["stayed_flag"] = 1

    operation_event_ids: set[str] = set()
    for event in operations:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("operation event_id is required")
        if event_id in operation_event_ids:
            raise ValueError(f"duplicate operation event_id: {event_id}")
        operation_event_ids.add(event_id)
        assignment_id = event.get("assignment_id")
        if not isinstance(assignment_id, str):
            raise ValueError(f"operation {event_id} requires assignment_id")
        if assignment_id not in facts:
            raise ValueError(f"operation references unknown assignment: {assignment_id}")
        occurred_at = _parse_aware_datetime(
            event.get("occurred_at"), f"operation {assignment_id}.occurred_at"
        )
        if occurred_at > cutoff:
            continue
        if occurred_at < assignment_times[assignment_id]:
            raise ValueError(f"operation {event_id} precedes assignment")
        event_type = event.get("event_type")
        fact = facts[assignment_id]
        if event_type == "complaint":
            fact["complaint_count"] += 1
        elif event_type == "safety_incident":
            fact["safety_incident_count"] += 1
            severity = event.get("severity", "unspecified")
            if not isinstance(severity, str) or not severity:
                raise ValueError(f"operation {event_id} has invalid severity")
            counts = fact["safety_incidents_by_severity"]
            counts[severity] = counts.get(severity, 0) + 1
        else:
            raise ValueError(f"unsupported operation event_type: {event_type}")

    survey_ids: set[str] = set()
    for survey in surveys:
        survey_id = survey.get("survey_id")
        if not isinstance(survey_id, str) or not survey_id:
            raise ValueError("survey_id is required")
        if survey_id in survey_ids:
            raise ValueError(f"duplicate survey_id: {survey_id}")
        survey_ids.add(survey_id)
        assignment_id = survey.get("assignment_id")
        if not isinstance(assignment_id, str):
            raise ValueError(f"survey {survey_id} requires assignment_id")
        if assignment_id not in facts:
            raise ValueError(f"survey references unknown assignment: {assignment_id}")
        invited_at = _parse_aware_datetime(
            survey.get("invited_at"), f"survey {assignment_id}.invited_at"
        )
        if invited_at > cutoff:
            continue
        if invited_at < assignment_times[assignment_id]:
            raise ValueError(f"survey {survey_id} invitation precedes assignment")
        fact = facts[assignment_id]
        fact["survey_invited_flag"] = 1
        responded_at = survey.get("responded_at")
        if responded_at is None:
            continue
        responded_at_value = _parse_aware_datetime(
            responded_at, f"survey {assignment_id}.responded_at"
        )
        if responded_at_value > cutoff:
            continue
        if responded_at_value < invited_at:
            raise ValueError(f"survey {survey_id} response precedes invitation")
        if fact["survey_responded_flag"]:
            raise ValueError(f"multiple survey responses for assignment: {assignment_id}")
        for score_key in ("satisfaction_score", "recommendation_score"):
            score = survey.get(score_key)
            if score is not None and (
                not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(float(score))
            ):
                raise ValueError(f"survey {survey_id}.{score_key} must be finite numeric or null")
        fact["survey_responded_flag"] = 1
        fact["satisfaction_score"] = survey.get("satisfaction_score")
        fact["recommendation_score"] = survey.get("recommendation_score")

    for assignment_id, fact in facts.items():
        assignment = assignment_by_id[assignment_id]
        attribution_matures_at = assignment_times[assignment_id] + timedelta(
            days=attribution_window_days
        )
        fact["booking_outcome_complete"] = cutoff >= attribution_matures_at
        final_outcome_mature = cutoff >= _parse_aware_datetime(
            assignment["outcome_matures_at"],
            f"assignment {assignment_id}.outcome_matures_at",
        )
        fact["financial_outcome_complete"] = final_outcome_mature
        fact["operations_outcome_complete"] = final_outcome_mature
        fact["survey_outcome_complete"] = final_outcome_mature

    arm_counts: dict[str, dict[str, int]] = {}
    srm_by_segment: dict[str, dict[str, float]] = {}
    for fact in facts.values():
        counts = arm_counts.setdefault(fact["segment"], {"control": 0, "treatment": 0})
        counts[fact["assigned_variant"]] += 1
    for segment in arm_counts:
        segment_facts = [fact for fact in facts.values() if fact["segment"] == segment]
        observed_treatment = sum(
            fact["assigned_variant"] == "treatment" for fact in segment_facts
        )
        expected_treatment = sum(
            fact["assignment_probability"] for fact in segment_facts
        )
        expected_variance = sum(
            fact["assignment_probability"] * (1 - fact["assignment_probability"])
            for fact in segment_facts
        )
        statistic = (
            (observed_treatment - expected_treatment) ** 2 / expected_variance
            if expected_variance
            else 0.0
        )
        srm_by_segment[segment] = {
            "observed_treatment": observed_treatment,
            "expected_treatment": expected_treatment,
            "chi_square_approx": statistic,
            "p_value_approx": math.erfc(math.sqrt(statistic / 2)),
        }
    quality = {
        "assignment_count": len(facts),
        "per_segment_arm_counts": arm_counts,
        "sample_ratio_mismatch_by_segment": srm_by_segment,
        "exposed_count": sum(fact["exposed_flag"] for fact in facts.values()),
        "exposure_variant_mismatch_count": sum(
            fact["exposure_variant_mismatch_count"] for fact in facts.values()
        ),
        "unattributed_booking_count": unattributed_bookings,
        "pre_assignment_booking_count": pre_assignment_bookings,
        "out_of_window_booking_count": out_of_window_bookings,
        "pre_assignment_payment_count": pre_assignment_payments,
        "unattributed_payment_count": unattributed_payments,
        "unmatched_completed_stay_count": unmatched_stays,
        "incomplete_stay_record_count": incomplete_stays,
        "outcome_mature_count": sum(
            fact["booking_outcome_complete"] for fact in facts.values()
        ),
        "financially_mature_count": sum(
            fact["financial_outcome_complete"] for fact in facts.values()
        ),
        "financial_totals_by_currency": {
            currency: {
                "gross_payment": sum(
                    fact["gross_payment"] for fact in facts.values()
                    if fact["currency"] == currency
                ),
                "refund_amount": sum(
                    fact["refund_amount"] for fact in facts.values()
                    if fact["currency"] == currency
                ),
                "net_revenue": sum(
                    fact["net_revenue"] for fact in facts.values()
                    if fact["currency"] == currency
                ),
                "variable_cost": sum(
                    fact["variable_cost"] for fact in facts.values()
                    if fact["currency"] == currency
                ),
                "contribution_margin": sum(
                    fact["contribution_margin"] for fact in facts.values()
                    if fact["currency"] == currency
                ),
            }
            for currency in sorted({fact["currency"] for fact in facts.values()})
        },
        "source_ledger_totals_by_currency": {},
        "refunds_reconciled_to_payments": True,
        "source_complete_through": source_complete.isoformat(),
        "analysis_cutoff_at": cutoff.isoformat(),
        "data_version": data_version,
    }
    source_totals: dict[str, dict[str, float]] = {}
    for payment in payments:
        if payment.get("payment_status") != "succeeded":
            continue
        if _parse_aware_datetime(payment["paid_at"], "payment.paid_at") > cutoff:
            continue
        amount, currency = _ledger_amount(payment, "payment")
        totals = source_totals.setdefault(
            currency, {"gross_payment": 0.0, "refund_amount": 0.0, "variable_cost": 0.0}
        )
        totals["gross_payment"] += amount
    for refund in effective_refunds:
        amount, currency = _ledger_amount(refund, "refund")
        totals = source_totals.setdefault(
            currency, {"gross_payment": 0.0, "refund_amount": 0.0, "variable_cost": 0.0}
        )
        totals["refund_amount"] += amount
    for cost in costs:
        if _parse_aware_datetime(cost["incurred_at"], "cost.incurred_at") > cutoff:
            continue
        amount, currency = _ledger_amount(cost, "cost")
        totals = source_totals.setdefault(
            currency, {"gross_payment": 0.0, "refund_amount": 0.0, "variable_cost": 0.0}
        )
        totals["variable_cost"] += amount
    quality["source_ledger_totals_by_currency"] = source_totals
    quality["financial_reconciliation_deltas_by_currency"] = {
        currency: {
            "gross_payment": totals["gross_payment"] - quality[
                "financial_totals_by_currency"
            ].get(currency, {}).get("gross_payment", 0.0),
            "refund_amount": totals["refund_amount"] - quality[
                "financial_totals_by_currency"
            ].get(currency, {}).get("refund_amount", 0.0),
            "variable_cost": totals["variable_cost"] - quality[
                "financial_totals_by_currency"
            ].get(currency, {}).get("variable_cost", 0.0),
        }
        for currency, totals in source_totals.items()
    }
    return {"facts": list(facts.values()), "quality": quality}


_FACT_METRICS = {
    "paid_booking": ("paid_booking_flag", "booking_outcome_complete", "probability_difference"),
    "net_booking": ("net_booking_flag", "financial_outcome_complete", "probability_difference"),
    "fully_cancelled": ("fully_cancelled_flag", "financial_outcome_complete", "probability_difference"),
    "booking_count": ("booking_count", "booking_outcome_complete", "bookings_per_assigned_unit"),
    "gross_payment": ("gross_payment", "financial_outcome_complete", "money"),
    "refund_amount": ("refund_amount", "financial_outcome_complete", "money"),
    "net_revenue": ("net_revenue", "financial_outcome_complete", "money"),
    "variable_cost": ("variable_cost", "financial_outcome_complete", "money"),
    "contribution_margin": ("contribution_margin", "financial_outcome_complete", "money"),
    "complaint_count": ("complaint_count", "operations_outcome_complete", "events_per_assigned_unit"),
    "safety_incident_count": ("safety_incident_count", "operations_outcome_complete", "events_per_assigned_unit"),
    "survey_responded": ("survey_responded_flag", "survey_outcome_complete", "probability_difference"),
}


def estimate_fact_itt(
    facts: Sequence[Mapping[str, Any]],
    metric: str,
    *,
    segment: str | None = None,
    severity: str | None = None,
) -> dict[str, Any]:
    """Estimate a segment-specific ITT from assignment-centered unit facts.

    No-exposure and no-booking units remain in their original assigned arms.
    Satisfaction scores are intentionally not an ITT metric because they are
    undefined for non-respondents/non-stayers.
    """
    if metric not in _FACT_METRICS:
        raise ValueError(
            f"unsupported ITT metric {metric!r}; choose from {', '.join(_FACT_METRICS)}"
        )
    if severity is not None and metric != "safety_incident_count":
        raise ValueError("severity can only be used with safety_incident_count")
    selected = [fact for fact in facts if segment is None or fact["segment"] == segment]
    if not selected:
        raise ValueError("no assignment facts for the requested segment")
    if segment is None and len({fact["segment"] for fact in selected}) > 1:
        raise ValueError("estimate each segment separately; pooling segments is unsupported")
    if len({fact["experiment_id"] for fact in selected}) != 1:
        raise ValueError("ITT facts must belong to one experiment")
    if len({fact["design"] for fact in selected}) != 1:
        raise ValueError("ITT facts must use one randomization design")
    if len({fact["data_version"] for fact in selected}) != 1:
        raise ValueError("ITT facts must use one data version")
    if len({fact["analysis_cutoff_at"] for fact in selected}) != 1:
        raise ValueError("ITT facts must use one analysis cutoff")
    if len({fact["assignment_probability"] for fact in selected}) != 1:
        raise ValueError("ITT requires a constant assignment probability within segment")
    value_key, completeness_key, unit = _FACT_METRICS[metric]
    if any(not fact[completeness_key] for fact in selected):
        raise ValueError(f"ITT requires complete outcomes for every assignment: {metric}")

    arms: dict[str, list[float]] = {"control": [], "treatment": []}
    for fact in selected:
        arm = fact["assigned_variant"]
        if arm not in arms:
            raise ValueError(f"unsupported assigned_variant: {arm}")
        value = (
            fact["safety_incidents_by_severity"].get(severity, 0)
            if metric == "safety_incident_count" and severity is not None
            else fact[value_key]
        )
        if metric in {"paid_booking", "net_booking", "fully_cancelled", "survey_responded"}:
            value = float(bool(value))
        arms[arm].append(float(value))
    if not arms["control"] or not arms["treatment"]:
        raise ValueError("ITT requires assignments in both arms")

    means = {
        arm: sum(values) / len(values) for arm, values in arms.items()
    }
    variances = {
        arm: sum((value - means[arm]) ** 2 for value in values)
        / max(len(values) - 1, 1)
        for arm, values in arms.items()
    }
    difference = means["treatment"] - means["control"]
    block_ids = [fact.get("randomization_block_id") for fact in selected]
    if any(block_id is not None for block_id in block_ids):
        if any(block_id is None for block_id in block_ids):
            raise ValueError("randomization blocks must be set on all selected facts or none")
        blocks: dict[str, dict[str, float]] = {}
        for fact in selected:
            block = blocks.setdefault(fact["randomization_block_id"], {})
            arm = fact["assigned_variant"]
            if arm in block:
                raise ValueError("each randomization block must contain one unit per arm")
            block[arm] = float(bool(fact[value_key])) if unit == "probability_difference" else float(fact[value_key])
        if any(set(block) != {"control", "treatment"} for block in blocks.values()):
            raise ValueError("each randomization block must contain one unit per arm")
        differences = [block["treatment"] - block["control"] for block in blocks.values()]
        if len(differences) < 2:
            raise ValueError("paired ITT requires at least two randomization blocks")
        mean_difference = sum(differences) / len(differences)
        variance = sum((value - mean_difference) ** 2 for value in differences) / (len(differences) - 1)
        standard_error = math.sqrt(variance / len(differences))
        interval_method = "paired-block normal approximation"
        block_count = len(blocks)
    else:
        standard_error = math.sqrt(
            variances["control"] / len(arms["control"])
            + variances["treatment"] / len(arms["treatment"])
        )
        interval_method = "independent-arm normal approximation"
        block_count = None

    result = {
        "metric": metric,
        "segment": segment,
        "synthetic_only": selected[0]["synthetic_only"],
        "estimand": "mean outcome by originally assigned arm: treatment minus control (ITT)",
        "assignment_count": {
            "control": len(arms["control"]),
            "treatment": len(arms["treatment"]),
        },
        "non_exposed_assignments": sum(not fact["exposed_flag"] for fact in selected),
        "non_purchasing_assignments": sum(not fact["paid_booking_flag"] for fact in selected),
        "randomization_blocks": block_count,
        "exposure_filter_applied": False,
        "control_mean": means["control"],
        "treatment_mean": means["treatment"],
        "itt_estimate": difference,
        "standard_error": standard_error,
        "confidence_level": 0.95,
        "confidence_interval": [
            difference - _Z_95 * standard_error,
            difference + _Z_95 * standard_error,
        ],
        "interval_method": interval_method,
        "unit": unit,
        "data_versions": sorted({fact["data_version"] for fact in selected}),
        "analysis_cutoff_at": sorted({fact["analysis_cutoff_at"] for fact in selected}),
    }
    if metric in {"complaint_count", "safety_incident_count"}:
        result["control_events_per_1000_assignments"] = means["control"] * 1000
        result["treatment_events_per_1000_assignments"] = means["treatment"] * 1000
        result["itt_events_per_1000_assignments"] = difference * 1000
    if severity is not None:
        result["safety_severity"] = severity
    currencies = {fact["currency"] for fact in selected}
    if unit == "money":
        if len(currencies) != 1:
            raise ValueError("financial ITT requires a consistent currency")
        result["currency"] = next(iter(currencies))
    return result


def summarize_survey_responders(
    facts: Sequence[Mapping[str, Any]],
    *,
    segment: str | None = None,
) -> dict[str, Any]:
    """Report response rates and respondent-only scores as descriptive results."""
    selected = [fact for fact in facts if segment is None or fact["segment"] == segment]
    if not selected:
        raise ValueError("no assignment facts for the requested segment")
    result: dict[str, Any] = {
        "segment": segment,
        "assignment_count": len(selected),
        "invited_count": sum(fact["survey_invited_flag"] for fact in selected),
        "responded_count": sum(fact["survey_responded_flag"] for fact in selected),
        "response_rate_per_assignment": sum(
            fact["survey_responded_flag"] for fact in selected
        ) / len(selected),
        "interpretation": "respondent-only scores are descriptive, not ITT effects",
    }
    for score_key in ("satisfaction_score", "recommendation_score"):
        scores = [
            float(fact[score_key])
            for fact in selected
            if fact[score_key] is not None
        ]
        result[f"{score_key}_respondent_mean"] = (
            sum(scores) / len(scores) if scores else None
        )
        result[f"{score_key}_respondent_count"] = len(scores)
    return result


def summarize_operations_per_stay(
    facts: Sequence[Mapping[str, Any]],
    *,
    segment: str | None = None,
) -> dict[str, Any]:
    """Report post-assignment operational event rates per completed stay.

    These conditional rates are operational monitors, not randomized ITT
    effects, because completed stays are a post-assignment subset.
    """
    selected = [fact for fact in facts if segment is None or fact["segment"] == segment]
    if not selected:
        raise ValueError("no assignment facts for the requested segment")
    completed_stays = sum(fact["stay_count"] for fact in selected)
    complaint_count = sum(fact["complaint_count"] for fact in selected)
    safety_incident_count = sum(fact["safety_incident_count"] for fact in selected)
    return {
        "segment": segment,
        "completed_stays": completed_stays,
        "complaint_count": complaint_count,
        "safety_incident_count": safety_incident_count,
        "complaints_per_100_completed_stays": (
            complaint_count * 100 / completed_stays if completed_stays else None
        ),
        "safety_incidents_per_100_completed_stays": (
            safety_incident_count * 100 / completed_stays if completed_stays else None
        ),
        "interpretation": "post-assignment operational monitoring, not ITT",
    }
