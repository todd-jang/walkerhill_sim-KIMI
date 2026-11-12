"""Design and dry-run helpers for segment-specific package experiments.

Dry-run assignments contain synthetic IDs only and do not simulate customer
outcomes or estimate package effects.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist
from typing import Any, Mapping, Sequence


SEGMENT_TREATMENTS = {
    "family": {
        "experiment_id": "WISE:Christmas:FamilyKit:v1",
        "control": "Memorable Familycation base package",
        "treatment": "Same base package plus kids activity kit",
    },
    "adult": {
        "experiment_id": "WISE:Christmas:AdultLoungeDrink:v1",
        "control": "Douglas Rest Together base package",
        "treatment": "Same base package plus lounge cocktail/wine benefit",
    },
}
PRIMARY_METRIC = "paid booking conversion per randomized eligible customer (ITT)"
SECONDARY_METRICS = (
    "contribution margin per assigned customer",
    "net retained bookings after cancellation/refund",
    "AOV among paid bookers (descriptive only)",
    "post-stay satisfaction among survey respondents and response rate",
    "operational issue rate per 100 assigned customers",
    "safety incidents",
)


@dataclass(frozen=True)
class SampleSizePlan:
    baseline_conversion: float
    target_conversion: float
    absolute_mde: float
    alpha_familywise: float
    tests: int
    alpha_per_test: float
    power: float
    customers_per_arm_per_segment: int
    total_customers: int


def build_assignment_plan(
    participants: Sequence[Mapping[str, Any]],
    seed: int = 42,
    strata_fields: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Randomize unique customers 1:1 within segment and optional strata."""
    if not participants:
        raise ValueError("at least one eligible participant is required")
    unsupported_strata = set(strata_fields) - {
        "stay_date",
        "channel",
    }
    if unsupported_strata:
        raise ValueError(f"unsupported stratification fields: {sorted(unsupported_strata)}")
    if len(set(strata_fields)) != len(strata_fields):
        raise ValueError("strata_fields must not contain duplicates")

    seen_ids: set[str] = set()
    grouped: dict[tuple[Any, ...], list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for index, participant in enumerate(participants):
        customer_id = participant.get("customer_id")
        segment = participant.get("segment")
        data_origin = participant.get("data_origin", "assignment_plan")
        if not isinstance(customer_id, str) or not customer_id.strip():
            raise ValueError(f"participant {index} must have a non-empty customer_id")
        customer_id = customer_id.strip()
        if customer_id in seen_ids:
            raise ValueError(f"duplicate customer_id: {customer_id}")
        if segment not in SEGMENT_TREATMENTS:
            raise ValueError(f"unsupported segment for {customer_id}: {segment!r}")
        if data_origin not in {"assignment_plan", "synthetic_dry_run"}:
            raise ValueError(f"unsupported data_origin for {customer_id}: {data_origin!r}")
        seen_ids.add(customer_id)

        stratum_values: list[Any] = [segment]
        for field in strata_fields:
            value = participant.get(field)
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                raise ValueError(
                    f"{field} for {customer_id} must be a non-empty string or integer"
                )
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"{field} for {customer_id} must not be empty")
            stratum_values.append(value)
        grouped[tuple(stratum_values)].append((index, participant))

    rng = random.Random(seed)
    assignments: list[dict[str, Any] | None] = [None] * len(participants)
    for stratum, members in sorted(grouped.items(), key=lambda item: repr(item[0])):
        rng.shuffle(members)
        segment = stratum[0]
        spec = SEGMENT_TREATMENTS[segment]
        pair_number = 0
        member_index = 0
        while member_index + 1 < len(members):
            pair = members[member_index:member_index + 2]
            treatment_first = bool(rng.getrandbits(1))
            block_id = f"{spec['experiment_id']}:block:{'|'.join(map(str, stratum))}:{pair_number}"
            arm_order = (
                ("treatment", "control")
                if treatment_first
                else ("control", "treatment")
            )
            for (original_index, participant), arm in zip(pair, arm_order):
                assignments[original_index] = {
                    "customer_id": participant["customer_id"].strip(),
                    "segment": segment,
                    "stratum": {
                        field: participant[field]
                        for field in strata_fields
                    },
                    "experiment_id": spec["experiment_id"],
                    "assigned_arm": arm,
                    "assignment_probability": 0.5,
                    "randomization_block_id": block_id,
                    "control_offer": spec["control"],
                    "treatment_offer": spec["treatment"],
                    "data_origin": data_origin,
                }
            member_index += 2
            pair_number += 1

        if member_index < len(members):
            original_index, participant = members[member_index]
            arm = "treatment" if rng.getrandbits(1) else "control"
            assignments[original_index] = {
                "customer_id": participant["customer_id"].strip(),
                "segment": segment,
                "stratum": {
                    field: participant[field]
                    for field in strata_fields
                },
                "experiment_id": spec["experiment_id"],
                "assigned_arm": arm,
                "assignment_probability": 0.5,
                "randomization_block_id": None,
                "control_offer": spec["control"],
                "treatment_offer": spec["treatment"],
                "data_origin": data_origin,
            }

    if any(assignment is None for assignment in assignments):
        raise RuntimeError("assignment plan did not assign every eligible participant")
    return [assignment for assignment in assignments if assignment is not None]


def calculate_sample_size(
    baseline_conversion: float,
    absolute_mde: float,
    power: float = 0.8,
    alpha_familywise: float = 0.05,
    tests: int = 2,
) -> SampleSizePlan:
    """Approximate two-sided sample size per arm, Bonferroni-adjusted by test."""
    if not 0 < baseline_conversion < 1:
        raise ValueError("baseline_conversion must be between 0 and 1")
    if not 0 < absolute_mde < 1:
        raise ValueError("absolute_mde must be between 0 and 1")
    target_conversion = baseline_conversion + absolute_mde
    if target_conversion >= 1:
        raise ValueError("baseline_conversion + absolute_mde must be below 1")
    if not 0 < power < 1:
        raise ValueError("power must be between 0 and 1")
    if not 0 < alpha_familywise < 1:
        raise ValueError("alpha_familywise must be between 0 and 1")
    if tests < 1:
        raise ValueError("tests must be at least 1")

    alpha_per_test = alpha_familywise / tests
    normal = NormalDist()
    z_alpha = normal.inv_cdf(1 - alpha_per_test / 2)
    z_power = normal.inv_cdf(power)
    pooled = (baseline_conversion + target_conversion) / 2
    numerator = (
        z_alpha * math.sqrt(2 * pooled * (1 - pooled))
        + z_power * math.sqrt(
            baseline_conversion * (1 - baseline_conversion)
            + target_conversion * (1 - target_conversion)
        )
    ) ** 2
    per_arm = math.ceil(numerator / absolute_mde**2)
    return SampleSizePlan(
        baseline_conversion=baseline_conversion,
        target_conversion=target_conversion,
        absolute_mde=absolute_mde,
        alpha_familywise=alpha_familywise,
        tests=tests,
        alpha_per_test=alpha_per_test,
        power=power,
        customers_per_arm_per_segment=per_arm,
        total_customers=per_arm * 2 * tests,
    )


def synthetic_dry_run(
    customers_per_segment: int,
    seed: int = 42,
) -> dict[str, Any]:
    """Check arm allocation using fictional IDs; does not create outcomes."""
    if customers_per_segment < 2:
        raise ValueError("customers_per_segment must be at least 2")
    participants = [
        {
            "customer_id": f"synthetic-{segment}-{index:05d}",
            "segment": segment,
            "data_origin": "synthetic_dry_run",
        }
        for segment in SEGMENT_TREATMENTS
        for index in range(customers_per_segment)
    ]
    assignments = build_assignment_plan(participants, seed=seed)
    counts: dict[str, dict[str, int]] = {}
    for segment in SEGMENT_TREATMENTS:
        counts[segment] = {
            arm: sum(
                item["segment"] == segment and item["assigned_arm"] == arm
                for item in assignments
            )
            for arm in ("control", "treatment")
        }
    return {
        "status": "assignment_dry_run_only",
        "data_origin": "synthetic_dry_run",
        "customers_per_segment": customers_per_segment,
        "assigned_total": len(assignments),
        "arm_counts": counts,
        "outcomes_generated": False,
        "effect_estimated": False,
        "assignments": assignments,
    }


def build_hillink_dummy_eligibility(
    repository_root: str | Path,
    customers_per_segment: int = 96,
    seed: int = 42,
    start_date: date = date(2026, 12, 18),
) -> list[dict[str, Any]]:
    """Create fictional eligibility rows from Hillink persona-grid templates.

    The source grid is an assumption-based synthetic model. Its rows are used
    as profile templates only: no IDs, weights, outcomes, or real eligibility
    claims are copied into the generated participants.
    """
    if customers_per_segment < 1:
        raise ValueError("customers_per_segment must be at least 1")
    root = Path(repository_root)
    if not root.is_dir():
        raise NotADirectoryError(f"Hillink repository directory does not exist: {root}")
    source_path = root / "hillink" / "timeline" / "persona_grid.csv"
    if not source_path.is_file():
        raise FileNotFoundError(f"Hillink persona grid not found: {source_path}")

    with source_path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        required_fields = {"연령", "목적", "인원수", "아동동반", "박수"}
        missing_fields = required_fields - set(reader.fieldnames or ())
        if missing_fields:
            raise ValueError(
                f"Hillink persona grid is missing columns: {sorted(missing_fields)}"
            )
        template_rows: list[tuple[int, dict[str, str]]] = []
        for line_number, row in enumerate(reader, start=2):
            try:
                children = int(row["아동동반"])
                party_size = int(row["인원수"].split(":", 1)[0])
                stay_nights = int(row["박수"].split(":", 1)[0])
            except (TypeError, ValueError) as error:
                raise ValueError(
                    f"invalid party-size/child/stay assumption at source row {line_number}"
                ) from error
            row["_source_line"] = str(line_number)
            row["_children_assumption"] = str(children)
            row["_party_size_assumption"] = str(party_size)
            row["_stay_nights_assumption"] = str(stay_nights)
            template_rows.append((line_number, row))

    adult_purposes = {
        "커플·기념일",
        "혼자 휴식",
        "휴식·산책 동행",
        "미식 방문",
    }

    def eligible_templates(segment: str) -> list[tuple[int, dict[str, str]]]:
        selected = []
        for line_number, row in template_rows:
            children = int(row["_children_assumption"])
            stay_nights = int(row["_stay_nights_assumption"])
            if stay_nights < 1:
                continue
            if segment == "family":
                include = row["목적"] == "가족·친족" and children > 0
            else:
                include = row["목적"] in adult_purposes and children == 0
            if include:
                selected.append((line_number, row))
        if not selected:
            raise ValueError(
                f"Hillink persona grid has no overnight templates for {segment}"
            )
        return selected

    revision_result = subprocess.run(
        ["git", "-C", str(root.resolve()), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    revision = (
        revision_result.stdout.strip()
        if revision_result.returncode == 0
        else "unknown"
    )
    rng = random.Random(seed)
    start_date = date.fromisoformat(start_date.isoformat())
    assignments: list[dict[str, Any]] = []
    for segment in ("family", "adult"):
        pool = eligible_templates(segment)
        rng.shuffle(pool)
        for index in range(customers_per_segment):
            source_line, template = pool[index % len(pool)]
            party_size = int(template["_party_size_assumption"])
            children = int(template["_children_assumption"])
            if party_size <= children:
                raise ValueError(
                    f"source row {source_line}: party size must exceed child count"
                )
            stay_date = start_date + timedelta(days=index % 8)
            channel = "web" if (index // 8) % 2 == 0 else "app"
            if segment == "family":
                eligibility_basis = (
                    "synthetic overnight family-purpose template with child indicator"
                )
            else:
                eligibility_basis = (
                    "synthetic overnight adult-purpose template with no child indicator"
                )
            assignments.append({
                "customer_id": f"synthetic-hillink-{segment}-{index + 1:04d}",
                "segment": segment,
                "stay_date": stay_date.isoformat(),
                "channel": channel,
                "data_origin": "synthetic_dry_run",
                "eligibility_status": "synthetic_only_not_real_customer",
                "eligibility_basis": eligibility_basis,
                "source_age_band_assumption": template["연령"],
                "source_purpose_template": template["목적"],
                "party_size_assumption": party_size,
                "adult_count_assumption": party_size - children,
                "child_count_assumption": children,
                "stay_nights_assumption": int(template["_stay_nights_assumption"]),
                "hillink_source_path": "hillink/timeline/persona_grid.csv",
                "hillink_source_row": source_line,
                "hillink_source_revision": revision,
            })
    return assignments


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare a segment-randomized package pilot; no customer outcomes are fabricated."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    dry_run_parser = subparsers.add_parser("dry-run")
    dry_run_parser.add_argument("--customers-per-segment", type=int, default=100)
    dry_run_parser.add_argument("--seed", type=int, default=42)
    assignment_parser = subparsers.add_parser("assign")
    assignment_parser.add_argument("input_csv")
    assignment_parser.add_argument("output_csv")
    assignment_parser.add_argument(
        "--stratify-by",
        nargs="*",
        choices=("stay_date", "channel"),
        default=(),
    )
    assignment_parser.add_argument("--seed", type=int, default=42)
    hillink_parser = subparsers.add_parser(
        "make-hillink-dummy",
        help="Create a clearly synthetic eligible-customer CSV from Hillink persona templates.",
    )
    hillink_parser.add_argument("repository")
    hillink_parser.add_argument("output_csv")
    hillink_parser.add_argument("--customers-per-segment", type=int, default=96)
    hillink_parser.add_argument("--seed", type=int, default=42)
    hillink_parser.add_argument("--start-date", default="2026-12-18")
    power_parser = subparsers.add_parser("sample-size")
    power_parser.add_argument("--baseline-conversion", type=float, required=True)
    power_parser.add_argument("--absolute-mde", type=float, required=True)
    power_parser.add_argument("--power", type=float, default=0.8)
    power_parser.add_argument("--familywise-alpha", type=float, default=0.05)

    args = parser.parse_args()
    if args.command == "dry-run":
        result = synthetic_dry_run(args.customers_per_segment, args.seed)
    elif args.command == "sample-size":
        result = calculate_sample_size(
            baseline_conversion=args.baseline_conversion,
            absolute_mde=args.absolute_mde,
            power=args.power,
            alpha_familywise=args.familywise_alpha,
            tests=len(SEGMENT_TREATMENTS),
        ).__dict__
    elif args.command == "make-hillink-dummy":
        rows = build_hillink_dummy_eligibility(
            repository_root=args.repository,
            customers_per_segment=args.customers_per_segment,
            seed=args.seed,
            start_date=date.fromisoformat(args.start_date),
        )
        output_path = Path(args.output_csv)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = list(rows[0])
        with output_path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        result = {
            "status": "synthetic_eligibility_csv_created",
            "data_origin": "synthetic_dry_run",
            "rows": len(rows),
            "rows_per_segment": args.customers_per_segment,
            "source": "hillink/timeline/persona_grid.csv",
            "output_csv": str(output_path),
            "real_customer_ids_included": False,
            "outcomes_generated": False,
            "effect_estimated": False,
        }
    else:
        input_path = Path(args.input_csv)
        output_path = Path(args.output_csv)
        if input_path.resolve() == output_path.resolve():
            raise ValueError("input_csv and output_csv must be different files")
        with input_path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            required_columns = {"customer_id", "segment", *args.stratify_by}
            if reader.fieldnames is None or not required_columns.issubset(reader.fieldnames):
                missing = sorted(required_columns - set(reader.fieldnames or ()))
                raise ValueError(f"input CSV is missing required columns: {missing}")
            participants = list(reader)
        assignments = build_assignment_plan(
            participants,
            seed=args.seed,
            strata_fields=args.stratify_by,
        )
        fieldnames = [
            "customer_id",
            "segment",
            "stratum",
            "experiment_id",
            "assigned_arm",
            "assignment_probability",
            "randomization_block_id",
            "control_offer",
            "treatment_offer",
            "data_origin",
        ]
        with output_path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=fieldnames)
            writer.writeheader()
            for assignment in assignments:
                row = dict(assignment)
                row["stratum"] = json.dumps(row["stratum"], ensure_ascii=False, sort_keys=True)
                writer.writerow(row)
        data_origins = {assignment["data_origin"] for assignment in assignments}
        result = {
            "status": "assignment_plan_created",
            "assigned_customers": len(assignments),
            "output_csv": str(output_path),
            "data_origin": (
                next(iter(data_origins)) if len(data_origins) == 1 else "mixed"
            ),
            "outcomes_generated": False,
            "effect_estimated": False,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
