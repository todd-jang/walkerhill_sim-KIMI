"""Observed-confounder AIPW analysis and diagnostics for offer records."""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from .experiment_pipeline import validate_experiment_records


_Z_95 = 1.959963984540054
_PROPENSITY_LIMIT = 0.05
_MAX_FOLDS = 5
_MIN_PER_ARM = 20
_RIDGE = 1e-3


def _concept_value(value: Mapping[str, Any] | None) -> str:
    if value is None:
        return "MISSING"
    canonical = value.get("canonical_id")
    if canonical:
        return str(canonical)
    return f"{value.get('source_system', 'UNKNOWN')}:{value.get('source_code', 'MISSING')}"


def _event_value(events: Sequence[Mapping[str, Any]]) -> str:
    values = sorted(_concept_value(event) for event in events)
    return "|".join(values) if values else "NO_EVENT"


def _design_matrix(records: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, list[str]]:
    categorical_rows: list[dict[str, str]] = []
    numeric_rows: list[list[float]] = []
    for record in records:
        conditions = record["conditions"]
        unit = record["unit"]
        offer = record["offer"]
        stay = date.fromisoformat(unit["stay_date"])
        lead_time = conditions["lead_time_days"]
        inventory = conditions["availability"]["remaining_inventory"]
        numeric_rows.append([
            float(lead_time) if lead_time is not None else float("nan"),
            float(conditions["weekday"]) if conditions["weekday"] is not None else float("nan"),
            float(inventory) if inventory is not None else float("nan"),
            float(stay.month),
        ])
        categorical_rows.append({
            "building": _concept_value(offer.get("building")),
            "room_type": _concept_value(offer.get("room_type")),
            "season": _concept_value(conditions.get("season")),
            "events": _event_value(conditions.get("events", [])),
            "availability_status": conditions["availability"]["status"],
            "availability_available": str(conditions["availability"]["available"]),
        })

    numeric = np.asarray(numeric_rows, dtype=float)
    for column in range(numeric.shape[1]):
        values = numeric[:, column]
        finite = np.isfinite(values)
        fill = float(np.mean(values[finite])) if finite.any() else 0.0
        values[~finite] = fill
        scale = float(np.std(values))
        numeric[:, column] = (values - float(np.mean(values))) / (
            scale if scale > 1e-12 else 1.0
        )

    columns = [numeric]
    feature_names = [
        "lead_time_days",
        "weekday",
        "remaining_inventory",
        "stay_month",
    ]
    for field in categorical_rows[0]:
        levels = sorted({row[field] for row in categorical_rows})
        if len(levels) <= 1:
            continue
        for level in levels[1:]:
            columns.append(np.asarray([
                [float(row[field] == level)] for row in categorical_rows
            ]))
            feature_names.append(f"{field}={level}")
    matrix = np.column_stack([np.ones(len(records)), *columns])
    return matrix, ["intercept", *feature_names]


def _missing_covariate_counts(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    missing: Counter[str] = Counter()
    for record in records:
        conditions = record["conditions"]
        for field in ("lead_time_days", "weekday"):
            if conditions[field] is None:
                missing[field] += 1
        if conditions["availability"]["remaining_inventory"] is None:
            missing["remaining_inventory"] += 1
        if conditions.get("season") is None:
            missing["season"] += 1
        if record["offer"].get("building") is None:
            missing["building"] += 1
        if record["offer"].get("room_type") is None:
            missing["room_type"] += 1
    return dict(sorted(missing.items()))


def _fit_logistic(design: np.ndarray, outcome: np.ndarray) -> np.ndarray:
    if len(np.unique(outcome)) < 2:
        raise ValueError("nuisance logistic model requires both outcome classes")

    def objective(coefficients: np.ndarray) -> tuple[float, np.ndarray]:
        logits = design @ coefficients
        probabilities = expit(logits)
        loss = np.logaddexp(0.0, logits).sum() - outcome @ logits
        loss += 0.5 * _RIDGE * np.sum(coefficients[1:] ** 2)
        gradient = design.T @ (probabilities - outcome)
        gradient[1:] += _RIDGE * coefficients[1:]
        return float(loss), gradient

    result = minimize(
        objective,
        np.zeros(design.shape[1]),
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": 1_000},
    )
    if not result.success:
        raise RuntimeError(f"observational logistic fit failed: {result.message}")
    return result.x


def _fit_linear(design: np.ndarray, outcome: np.ndarray) -> np.ndarray:
    penalty = np.eye(design.shape[1]) * _RIDGE
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ outcome)
    if not np.isfinite(coefficients).all():
        raise RuntimeError("observational linear outcome fit produced non-finite coefficients")
    return coefficients


def _assign_folds(treatment: np.ndarray, folds: int, seed: int) -> np.ndarray:
    rng = random.Random(seed)
    fold_ids = np.full(len(treatment), -1, dtype=int)
    for arm in (0, 1):
        indexes = np.flatnonzero(treatment == arm).tolist()
        rng.shuffle(indexes)
        for position, index in enumerate(indexes):
            fold_ids[index] = position % folds
    return fold_ids


def _effective_sample_size(weights: np.ndarray) -> float:
    total = float(weights.sum())
    squared = float(np.square(weights).sum())
    return total * total / squared if squared > 0 else 0.0


def _weighted_smd(
    design: np.ndarray,
    treatment: np.ndarray,
    weights: np.ndarray | None = None,
) -> float:
    maxima: list[float] = []
    for column in range(1, design.shape[1]):
        values = design[:, column]
        treated = treatment == 1
        control = treatment == 0
        if weights is None:
            mean_t = float(np.mean(values[treated]))
            mean_c = float(np.mean(values[control]))
            var_t = float(np.var(values[treated], ddof=1))
            var_c = float(np.var(values[control], ddof=1))
        else:
            wt = weights[treated]
            wc = weights[control]
            mean_t = float(np.average(values[treated], weights=wt))
            mean_c = float(np.average(values[control], weights=wc))
            var_t = float(np.average(np.square(values[treated] - mean_t), weights=wt))
            var_c = float(np.average(np.square(values[control] - mean_c), weights=wc))
        pooled = math.sqrt(max((var_t + var_c) / 2.0, 0.0))
        if pooled > 1e-12:
            maxima.append(abs(mean_t - mean_c) / pooled)
    return max(maxima, default=0.0)


def estimate_observational_effect(
    records: Sequence[Mapping[str, Any]],
    metric: str = "booking",
    seed: int = 42,
) -> dict[str, Any]:
    """Estimate adjusted effect of a shown discounted offer with cross-fit AIPW.

    Treatment is defined as an actually shown offer with a positive percentage
    or fixed-amount discount. Comparison offers must also have been shown and
    carry an explicit no-discount/zero-discount marker. Non-exposed and
    unclassifiable offers are excluded from this explicitly defined estimand.

    The output is an observational estimate conditional on measured
    pre-treatment covariates, not proof of causal identification.
    """
    if metric not in {"booking", "total_revenue"}:
        raise ValueError("metric must be 'booking' or 'total_revenue'")
    validate_experiment_records(records)
    if records[0]["experiment"]["design"] not in {
        "observational",
        "synthetic_observational",
    }:
        raise ValueError("observational analysis requires observational records")
    if records[0]["experiment"]["assignment_level"] != "customer":
        raise ValueError(
            "this observational estimator requires one record per customer-level unit"
        )

    selected: list[Mapping[str, Any]] = []
    treatment_values: list[int] = []
    excluded = Counter()
    for record in records:
        offer = record["offer"]
        if not offer["exposed"] or offer["exposure_status"] != "shown":
            excluded["not_shown"] += 1
            continue
        discount = offer["discount"]
        if discount is None:
            excluded["discount_unclassified"] += 1
            continue
        kind = discount["kind"]
        value = discount["value"]
        if kind == "percentage" and value is not None:
            treatment = value > 0
        elif kind == "fixed_amount" and value is not None:
            treatment = value > 0
        elif kind == "none":
            treatment = False
        elif value == 0:
            treatment = False
        else:
            excluded["discount_unclassified"] += 1
            continue

        if record["outcome"]["status"] != "complete":
            excluded["outcome_incomplete"] += 1
            continue
        if metric == "booking":
            if record["outcome"]["booking_status"] not in {"booked", "not_booked"}:
                excluded["booking_outcome_unknown"] += 1
                continue
        elif record["outcome"]["total_revenue"] is None:
            excluded["revenue_missing"] += 1
            continue
        selected.append(record)
        treatment_values.append(int(treatment))

    treatment = np.asarray(treatment_values, dtype=int)
    treated_count = int(treatment.sum())
    control_count = int(len(treatment) - treated_count)
    if min(treated_count, control_count) < _MIN_PER_ARM:
        raise ValueError(
            f"at least {_MIN_PER_ARM} analyzable records per treatment group are required; "
            f"found treated={treated_count}, control={control_count}"
        )

    if metric == "booking":
        outcome = np.asarray([
            1.0 if record["outcome"]["booking_status"] == "booked" else 0.0
            for record in selected
        ])
    else:
        currencies = {
            record["outcome"]["total_revenue"]["currency"] for record in selected
        }
        units = {
            record["outcome"]["total_revenue"]["unit"] for record in selected
        }
        if len(currencies) != 1 or len(units) != 1:
            raise ValueError("revenue analysis requires one currency and money unit")
        outcome = np.asarray([
            float(record["outcome"]["total_revenue"]["amount"])
            for record in selected
        ])

    missing_covariates = _missing_covariate_counts(selected)
    design, feature_names = _design_matrix(selected)
    folds = min(_MAX_FOLDS, control_count, treated_count)
    fold_ids = _assign_folds(treatment, folds, seed)
    propensity = np.empty(len(selected), dtype=float)
    outcome0 = np.empty(len(selected), dtype=float)
    outcome1 = np.empty(len(selected), dtype=float)
    binary_outcome = metric == "booking"

    for fold in range(folds):
        test = fold_ids == fold
        train = ~test
        if len(np.unique(treatment[train])) != 2:
            raise ValueError("cross-fitting fold lacks a treatment group")
        propensity_model = _fit_logistic(design[train], treatment[train])
        propensity[test] = expit(design[test] @ propensity_model)
        for arm, predictions in ((0, outcome0), (1, outcome1)):
            arm_train = train & (treatment == arm)
            if len(np.unique(outcome[arm_train])) < 2 and binary_outcome:
                raise ValueError(
                    f"booking outcome has only one class in training arm {arm}"
                )
            if binary_outcome:
                model = _fit_logistic(design[arm_train], outcome[arm_train])
                predictions[test] = expit(design[test] @ model)
            else:
                model = _fit_linear(design[arm_train], outcome[arm_train])
                predictions[test] = design[test] @ model

    propensity = np.clip(propensity, 1e-6, 1.0 - 1e-6)
    influence = (
        outcome1 - outcome0
        + treatment * (outcome - outcome1) / propensity
        - (1 - treatment) * (outcome - outcome0) / (1.0 - propensity)
    )
    estimate = float(np.mean(influence))
    standard_error = float(np.std(influence, ddof=1) / math.sqrt(len(influence)))
    lower = estimate - _Z_95 * standard_error
    upper = estimate + _Z_95 * standard_error

    ipw = np.where(treatment == 1, 1.0 / propensity, 1.0 / (1.0 - propensity))
    low_overlap = propensity < _PROPENSITY_LIMIT
    high_overlap = propensity > 1.0 - _PROPENSITY_LIMIT
    overlap_violations = low_overlap | high_overlap
    overlap_adequate = not bool(overlap_violations.any())
    balance_before = _weighted_smd(design, treatment)
    balance_after = _weighted_smd(design, treatment, ipw)
    unadjusted_difference = float(
        np.mean(outcome[treatment == 1]) - np.mean(outcome[treatment == 0])
    )
    assumptions = [
        "no unmeasured confounding after conditioning on the recorded pre-treatment covariates",
        "conditional positivity: comparable treated and control offers exist at each covariate profile",
        "consistency: recorded offer treatment represents the intervention being evaluated",
        "no interference between analyzed units",
        "outcomes and confounders are correctly measured; nuisance models are sufficiently specified",
    ]
    cautions = []
    if not overlap_adequate:
        cautions.append(
            "propensity scores outside [0.05, 0.95]; positivity/overlap concern"
        )
    if balance_after > 0.1:
        cautions.append(
            "maximum weighted standardized mean difference exceeds 0.1"
        )
    if any(excluded.values()):
        cautions.append(
            "some records were excluded because treatment or outcome could not be classified"
        )
    if missing_covariates:
        cautions.append(
            "missing pre-treatment covariates were mean-imputed or coded as MISSING"
        )

    result: dict[str, Any] = {
        "analysis": "cross-fitted augmented inverse-probability weighting (AIPW)",
        "design": records[0]["experiment"]["design"],
        "synthetic_only": (
            records[0]["experiment"]["design"] == "synthetic_observational"
            or all(
                record["provenance"]["data_origin"] == "synthetic_fixture"
                for record in records
            )
        ),
        "treatment_definition": "discounted offer actually shown vs shown offer with no discount",
        "metric": metric,
        "estimand": "average treatment effect among analyzable shown offers (ATE)",
        "sample_counts": {
            "input": len(records),
            "analyzed": len(selected),
            "treated": treated_count,
            "control": control_count,
            "excluded": dict(sorted(excluded.items())),
        },
        "covariates": feature_names[1:],
        "cross_fitting_folds": folds,
        "estimate": estimate,
        "unadjusted_difference": unadjusted_difference,
        "standard_error": standard_error,
        "confidence_level": 0.95,
        "confidence_interval": [lower, upper],
        "interval_method": "normal approximation from cross-fitted influence scores",
        "diagnostics": {
            "propensity_score_range": [
                float(np.min(propensity)),
                float(np.max(propensity)),
            ],
            "propensity_outside_0_05_0_95_count": int(overlap_violations.sum()),
            "overlap_adequate_at_0_05_threshold": overlap_adequate,
            "maximum_absolute_smd_before_weighting": balance_before,
            "maximum_absolute_smd_after_ipw": balance_after,
            "unadjusted_difference": unadjusted_difference,
            "missing_pre_treatment_covariate_counts": missing_covariates,
            "weighted_effective_sample_size": {
                "treated": _effective_sample_size(ipw[treatment == 1]),
                "control": _effective_sample_size(ipw[treatment == 0]),
            },
            "status": "caution" if cautions else "diagnostics_passed",
            "cautions": cautions,
        },
        "identification_assumptions": assumptions,
        "limitations": [
            "diagnostics cannot detect unmeasured confounding or prove causal identification",
            "confidence interval is a large-sample approximation and does not account for clustering",
            "only listed pre-treatment covariates are adjusted; unmeasured confounding remains possible",
            "result applies only to analyzable actually-shown discounted vs non-discounted offers",
            "this estimate is observational and is not an ITT estimate",
        ],
    }
    if metric == "booking":
        result["unit"] = "probability_difference"
    else:
        result["currency"] = next(iter(currencies))
        result["money_unit"] = next(iter(units))
    return result


def load_jsonl_records(path: str | Path) -> list[dict[str, Any]]:
    """Load one JSON experiment record per non-empty JSONL line."""
    records = []
    with open(path, encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid JSON: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"{path}:{line_number}: each JSONL value must be an object"
                )
            records.append(record)
    if not records:
        raise ValueError(f"{path}: no JSON records found")
    return records


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Estimate an adjusted observational contrast from ontology-backed "
            "reservation exposure JSONL records."
        )
    )
    parser.add_argument("input", type=Path, help="one reservation record per JSONL line")
    parser.add_argument(
        "--metric",
        choices=("booking", "total_revenue"),
        default="booking",
        help="outcome to analyze (default: booking)",
    )
    parser.add_argument("--seed", type=int, default=42, help="cross-fitting seed")
    args = parser.parse_args(argv)
    records = load_jsonl_records(args.input)
    result = estimate_observational_effect(
        records, metric=args.metric, seed=args.seed
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
