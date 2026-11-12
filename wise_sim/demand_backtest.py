"""Daily demand forecasting, chronological backtesting, and virtual-time replay."""
from __future__ import annotations

import argparse
import csv
import json
import math
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

_TARGETS = ("reservations", "rooms_sold", "room_revenue")
_LAGS = (1, 7, 28, 364)
_DEFAULT_TRAIN_DAYS = 730
_DEFAULT_TEST_DAYS = 365


def load_daily_csv(path: str | Path) -> list[dict[str, Any]]:
    """Load one daily hotel aggregate per CSV row."""
    required = {
        "date",
        "reservations",
        "rooms_sold",
        "available_rooms",
        "room_revenue",
    }
    records: list[dict[str, Any]] = []
    with open(path, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: CSV header is required")
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"{path}: missing required columns: {sorted(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                record: dict[str, Any] = {
                    "date": date.fromisoformat(row["date"]),
                    "reservations": int(row["reservations"]),
                    "rooms_sold": int(row["rooms_sold"]),
                    "available_rooms": int(row["available_rooms"]),
                    "room_revenue": float(row["room_revenue"]),
                }
                season = (row.get("season") or "").strip()
                if season:
                    record["season"] = season
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: invalid daily value: {exc}") from exc
            records.append(record)
    _validate_daily_records(records)
    return records


def _validate_daily_records(records: Sequence[Mapping[str, Any]]) -> None:
    if not records:
        raise ValueError("at least one daily record is required")
    previous: date | None = None
    for index, record in enumerate(records):
        day = record.get("date")
        if not isinstance(day, date):
            raise ValueError(f"record {index}: date must be a datetime.date")
        if previous is not None and day != previous + timedelta(days=1):
            raise ValueError(
                f"daily records must be consecutive and sorted; gap or duplicate after {previous}"
            )
        previous = day
        for field in ("reservations", "rooms_sold", "available_rooms"):
            value = record.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"record {index}: {field} must be a non-negative integer")
        revenue = record.get("room_revenue")
        if (
            isinstance(revenue, bool)
            or not isinstance(revenue, (int, float))
            or not math.isfinite(float(revenue))
            or revenue < 0
        ):
            raise ValueError(f"record {index}: room_revenue must be finite and non-negative")
        if record["rooms_sold"] > record["available_rooms"]:
            raise ValueError(f"record {index}: rooms_sold exceeds available_rooms")
        season = record.get("season")
        if season is not None and (not isinstance(season, str) or not season.strip()):
            raise ValueError(f"record {index}: season must be a non-empty string when supplied")


def _features(day: date, index: int, history: Sequence[Mapping[str, Any]], target: str,
              scale: float, trend_origin: int, trend_scale: float) -> list[float]:
    day_of_year = day.timetuple().tm_yday
    annual_angle = 2.0 * math.pi * (day_of_year - 1) / 365.2425
    weekday_angle = 2.0 * math.pi * day.weekday() / 7.0
    values = [
        1.0,
        math.sin(weekday_angle),
        math.cos(weekday_angle),
        math.sin(annual_angle),
        math.cos(annual_angle),
        (index - trend_origin) / trend_scale,
    ]
    for lag in _LAGS:
        if len(history) >= lag:
            values.append(float(history[-lag][target]) / scale)
        else:
            raise ValueError("insufficient history for the selected autoregressive lags")
    return values


def _fit_models(
    records: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, np.ndarray], dict[str, float], int]:
    first_index = max(_LAGS)
    if len(records) <= first_index:
        raise ValueError(f"at least {first_index + 1} training days are required")
    coefficients: dict[str, np.ndarray] = {}
    scales: dict[str, float] = {}
    for target in _TARGETS:
        values = np.asarray([float(record[target]) for record in records])
        scale = max(float(np.mean(values)), 1.0)
        scales[target] = scale
        design = np.asarray([
            _features(
                records[index]["date"],
                index,
                records[:index],
                target,
                scale,
                first_index,
                max(len(records) - first_index, 1),
            )
            for index in range(first_index, len(records))
        ])
        normalized_target = values[first_index:] / scale
        penalty = np.eye(design.shape[1]) * 1e-3
        penalty[0, 0] = 0.0
        coefficients[target] = np.linalg.solve(
            design.T @ design + penalty, design.T @ normalized_target
        )
    return coefficients, scales, first_index


def _predict_one(
    day: date,
    index: int,
    history: Sequence[Mapping[str, Any]],
    models: Mapping[str, np.ndarray],
    scales: Mapping[str, float],
    trend_origin: int,
    trend_scale: float,
    available_rooms: int,
) -> dict[str, Any]:
    predicted: dict[str, Any] = {
        "date": day.isoformat(),
        "available_rooms": available_rooms,
    }
    for target in _TARGETS:
        row = _features(
            day,
            index,
            history,
            target,
            scales[target],
            trend_origin,
            trend_scale,
        )
        estimate = max(0.0, float(np.asarray(row) @ models[target]) * scales[target])
        predicted[target] = estimate
    predicted["reservations"] = int(round(predicted["reservations"]))
    predicted["rooms_sold"] = min(
        available_rooms, int(round(predicted["rooms_sold"]))
    )
    predicted["room_revenue"] = round(predicted["room_revenue"], 2)
    predicted["occupancy"] = (
        predicted["rooms_sold"] / available_rooms if available_rooms else 0.0
    )
    predicted["adr"] = (
        predicted["room_revenue"] / predicted["rooms_sold"]
        if predicted["rooms_sold"]
        else None
    )
    predicted["revpar"] = (
        predicted["room_revenue"] / available_rooms if available_rooms else 0.0
    )
    return predicted


def _seasonal_baseline(
    training: Sequence[Mapping[str, Any]],
    day: date,
    available_rooms: int,
) -> dict[str, Any]:
    candidates = [
        record for record in training
        if record["date"].month == day.month
        and record["date"].weekday() == day.weekday()
    ]
    if not candidates:
        candidates = [
            record for record in training if record["date"].weekday() == day.weekday()
        ]
    values = {
        target: float(np.mean([record[target] for record in candidates]))
        for target in _TARGETS
    }
    rooms_sold = min(available_rooms, max(0, int(round(values["rooms_sold"]))))
    revenue = max(0.0, values["room_revenue"])
    return {
        "date": day.isoformat(),
        "available_rooms": available_rooms,
        "reservations": max(0, int(round(values["reservations"]))),
        "rooms_sold": rooms_sold,
        "room_revenue": round(revenue, 2),
        "occupancy": rooms_sold / available_rooms if available_rooms else 0.0,
        "adr": revenue / rooms_sold if rooms_sold else None,
        "revpar": revenue / available_rooms if available_rooms else 0.0,
    }


def _season_groups(
    training: Sequence[Mapping[str, Any]],
    evaluation: Sequence[Mapping[str, Any]],
) -> list[str]:
    monthly: dict[int, list[float]] = {month: [] for month in range(1, 13)}
    for record in training:
        monthly[record["date"].month].append(float(record["rooms_sold"]))
    monthly_means = np.asarray([
        np.mean(monthly[month]) if monthly[month] else 0.0
        for month in range(1, 13)
    ])
    lower, upper = np.quantile(monthly_means, [0.25, 0.75])
    groups = []
    for record in evaluation:
        supplied = record.get("season")
        if supplied:
            groups.append(str(supplied))
        elif upper > lower and monthly_means[record["date"].month - 1] >= upper:
            groups.append("high")
        elif upper > lower and monthly_means[record["date"].month - 1] <= lower:
            groups.append("low")
        else:
            groups.append("shoulder")
    return groups


def _metric_summary(actual: Sequence[float], predicted: Sequence[float]) -> dict[str, Any]:
    if not actual:
        return {
            "n": 0,
            "actual_mean": None,
            "forecast_mean": None,
            "calibration_ratio": None,
            "mae": None,
            "rmse": None,
            "wape": None,
            "bias": None,
        }
    actual_values = np.asarray(actual, dtype=float)
    predicted_values = np.asarray(predicted, dtype=float)
    errors = predicted_values - actual_values
    actual_total = float(np.abs(actual_values).sum())
    actual_mean = float(np.mean(actual_values))
    forecast_mean = float(np.mean(predicted_values))
    return {
        "n": len(actual),
        "actual_mean": actual_mean,
        "forecast_mean": forecast_mean,
        "calibration_ratio": (
            forecast_mean / actual_mean if actual_mean != 0 else None
        ),
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(np.square(errors)))),
        "wape": (
            float(np.abs(errors).sum() / actual_total) if actual_total > 0 else None
        ),
        "bias": float(np.mean(errors)),
    }


def _evaluate(
    actual: Sequence[Mapping[str, Any]],
    predicted: Sequence[Mapping[str, Any]],
    seasons: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    metrics: dict[str, Any] = {}
    actual_values: dict[str, list[float]] = {
        "reservations": [],
        "occupancy": [],
        "adr": [],
        "revpar": [],
    }
    predicted_values: dict[str, list[float]] = {
        key: [] for key in actual_values
    }
    rows: list[dict[str, Any]] = []
    seasonal_pairs: dict[str, dict[str, tuple[list[float], list[float]]]] = {}
    for observed, forecast, season in zip(actual, predicted, seasons):
        actual_day = {
            "reservations": float(observed["reservations"]),
            "occupancy": (
                observed["rooms_sold"] / observed["available_rooms"]
                if observed["available_rooms"]
                else 0.0
            ),
            "adr": (
                observed["room_revenue"] / observed["rooms_sold"]
                if observed["rooms_sold"]
                else None
            ),
            "revpar": (
                observed["room_revenue"] / observed["available_rooms"]
                if observed["available_rooms"]
                else 0.0
            ),
        }
        forecast_day = {
            "reservations": float(forecast["reservations"]),
            "occupancy": float(forecast["occupancy"]),
            "adr": forecast["adr"],
            "revpar": float(forecast["revpar"]),
        }
        rows.append({
            "date": observed["date"].isoformat(),
            "season": season,
            "actual": actual_day,
            "forecast": forecast_day,
        })
        for metric in actual_values:
            if actual_day[metric] is None or forecast_day[metric] is None:
                continue
            actual_number = float(actual_day[metric])
            forecast_number = float(forecast_day[metric])
            actual_values[metric].append(actual_number)
            predicted_values[metric].append(forecast_number)
            metric_pairs = seasonal_pairs.setdefault(season, {}).setdefault(
                metric, ([], [])
            )
            metric_pairs[0].append(actual_number)
            metric_pairs[1].append(forecast_number)
    for metric in actual_values:
        metrics[metric] = _metric_summary(
            actual_values[metric], predicted_values[metric]
        )
    calibration = {
        season: {
            metric: _metric_summary(*pair)
            for metric, pair in sorted(metric_pairs.items())
        }
        for season, metric_pairs in sorted(seasonal_pairs.items())
    }
    return metrics, calibration, rows


def run_temporal_backtest(
    records: Sequence[Mapping[str, Any]],
    train_days: int = _DEFAULT_TRAIN_DAYS,
    test_days: int = _DEFAULT_TEST_DAYS,
) -> dict[str, Any]:
    """Fit on an earlier daily window and test one-day-ahead on the latest window."""
    _validate_daily_records(records)
    if train_days < max(_LAGS) + 1 or test_days < 1:
        raise ValueError("train_days must exceed 364 and test_days must be positive")
    required = train_days + test_days
    if len(records) < required:
        raise ValueError(
            f"backtest requires {required} consecutive days; received {len(records)}"
        )
    window = records[-required:]
    training = window[:train_days]
    evaluation = window[train_days:]
    forecasts = forecast_future(
        training,
        test_days,
        [int(observed["available_rooms"]) for observed in evaluation],
    )
    baseline_forecasts = [
        _seasonal_baseline(
            training,
            observed["date"],
            int(observed["available_rooms"]),
        )
        for observed in evaluation
    ]
    seasons = _season_groups(training, evaluation)
    metrics, calibration, daily = _evaluate(evaluation, forecasts, seasons)
    baseline_metrics, baseline_calibration, _ = _evaluate(
        evaluation, baseline_forecasts, seasons
    )
    improvement = {
        metric: (
            (baseline_metrics[metric]["mae"] - metrics[metric]["mae"])
            / baseline_metrics[metric]["mae"]
            if baseline_metrics[metric]["mae"] not in (None, 0)
            else None
        )
        for metric in metrics
    }
    return {
        "analysis_unit": "hotel × calendar day",
        "forecast_horizon": "fixed-origin recursive forecast across the holdout window",
        "train_days": train_days,
        "test_days": test_days,
        "training_period": [
            training[0]["date"].isoformat(),
            training[-1]["date"].isoformat(),
        ],
        "test_period": [
            evaluation[0]["date"].isoformat(),
            evaluation[-1]["date"].isoformat(),
        ],
        "model": "ridge autoregression with weekday/year seasonality and 1/7/28/364-day lags",
        "metrics": metrics,
        "seasonal_calibration": calibration,
        "baseline": "training-period mean for matching month × weekday",
        "baseline_metrics": baseline_metrics,
        "baseline_seasonal_calibration": baseline_calibration,
        "mae_improvement_vs_baseline": improvement,
        "season_definition": (
            "provided CSV season labels; otherwise high/low/shoulder month groups "
            "derived from training-period monthly mean rooms sold"
        ),
        "daily": daily,
        "limitations": [
            "holdout outcomes are never fed back into the forecast; predictions are recursive across the full test window",
            "the model forecasts hotel-level daily aggregates, not individual customer choices or causal policy effects",
            "holdout and future sellable-room capacity must be known in advance or separately forecast",
        ],
    }


def forecast_future(
    records: Sequence[Mapping[str, Any]],
    days: int,
    available_rooms: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    """Recursively forecast future daily aggregates from the complete history."""
    _validate_daily_records(records)
    if days < 1:
        raise ValueError("days must be positive")
    if available_rooms is not None and len(available_rooms) != days:
        raise ValueError("available_rooms must contain one capacity per forecast day")
    models, scales, trend_origin = _fit_models(records)
    capacities_by_weekday: dict[int, list[int]] = {
        weekday: [] for weekday in range(7)
    }
    for record in records:
        capacities_by_weekday[record["date"].weekday()].append(
            int(record["available_rooms"])
        )
    history = list(records)
    forecasts = []
    for offset in range(days):
        day = records[-1]["date"] + timedelta(days=offset + 1)
        if available_rooms is not None:
            capacity = available_rooms[offset]
            if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 0:
                raise ValueError("forecast room availability must be non-negative integers")
        else:
            values = capacities_by_weekday[day.weekday()]
            capacity = int(round(float(np.median(values))))
        forecast = _predict_one(
            day,
            len(history),
            history,
            models,
            scales,
            trend_origin,
            max(len(records) - trend_origin, 1),
            capacity,
        )
        forecasts.append(forecast)
        history.append({
            "date": day,
            "reservations": forecast["reservations"],
            "rooms_sold": forecast["rooms_sold"],
            "available_rooms": capacity,
            "room_revenue": forecast["room_revenue"],
        })
    return forecasts


def run_virtual_clock(
    forecasts: Sequence[Mapping[str, Any]],
    speed: float = 24.0,
    tick_seconds: float | None = None,
    on_tick: Callable[[Mapping[str, Any]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Replay one forecast day per 86400/speed wall-clock seconds."""
    if speed <= 0 or not math.isfinite(speed):
        raise ValueError("speed must be a finite positive number")
    delay = 86400.0 / speed if tick_seconds is None else tick_seconds
    if delay < 0 or not math.isfinite(delay):
        raise ValueError("tick_seconds must be finite and non-negative")
    replayed = []
    for index, forecast in enumerate(forecasts):
        tick = dict(forecast)
        tick["virtual_tick"] = index + 1
        replayed.append(tick)
        if on_tick is not None:
            on_tick(tick)
        if index + 1 < len(forecasts) and delay:
            sleep(delay)
    return replayed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backtest and forecast daily WISE hotel demand from historical aggregates."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    backtest_parser = subparsers.add_parser("backtest")
    backtest_parser.add_argument("input", type=Path)
    backtest_parser.add_argument("--train-days", type=int, default=_DEFAULT_TRAIN_DAYS)
    backtest_parser.add_argument("--test-days", type=int, default=_DEFAULT_TEST_DAYS)
    forecast_parser = subparsers.add_parser("forecast")
    forecast_parser.add_argument("input", type=Path)
    forecast_parser.add_argument("--days", type=int, default=365)
    forecast_parser.add_argument("--replay", action="store_true")
    forecast_parser.add_argument("--speed", type=float, default=24.0)
    forecast_parser.add_argument("--tick-seconds", type=float)
    args = parser.parse_args(argv)
    records = load_daily_csv(args.input)
    if args.command == "backtest":
        result = run_temporal_backtest(records, args.train_days, args.test_days)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    forecasts = forecast_future(records, args.days)
    if args.replay:
        run_virtual_clock(
            forecasts,
            speed=args.speed,
            tick_seconds=args.tick_seconds,
            on_tick=lambda tick: print(json.dumps(tick, ensure_ascii=False)),
        )
    else:
        print(json.dumps({
            "analysis_unit": "hotel × calendar day",
            "forecast_days": args.days,
            "virtual_clock_speed": args.speed,
            "seconds_per_virtual_day": (
                args.tick_seconds if args.tick_seconds is not None else 86400 / args.speed
            ),
            "daily": forecasts,
            "limitations": [
                "room availability is estimated from historical weekday medians unless supplied through the API",
                "forecast is a statistical projection, not a causal estimate of pricing-policy impact",
            ],
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
