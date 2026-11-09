"""GMM 기반 합성 고객과 가격 민감도 proxy 추정용 mockup."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logsumexp

try:
    from personas import Persona
except ImportError:
    from .personas import Persona


PROFILE_FEATURES = (
    "booking_count",
    "stay_nights",
    "lead_time_days",
    "avg_room_rate",
)
CATEGORICAL_FEATURES = (
    "floor_preference",
    "view_preference",
    "dining_preference",
)
PROMOTIONS = ("none", "package", "breakfast")
BASE_ROOM_PRICE = 350_000.0
PRICE_COEFFICIENT_SCALE = 5.0


@dataclass(frozen=True)
class SegmentFit:
    sample_count: int
    price_log_odds_coefficient: float
    price_sensitivity_proxy: float
    baseline_booking_probability: float
    weekend_log_odds_coefficient: float


@dataclass
class GMMPersonaModel:
    feature_means: np.ndarray
    feature_scales: np.ndarray
    mixture_weights: np.ndarray
    mixture_means: np.ndarray
    mixture_covariances: np.ndarray
    segment_fits: dict[int, SegmentFit]
    category_probabilities: dict[int, dict[str, dict[str, float]]]
    n_components: int


def generate_mock_pms_data(
    n: int = 4_000,
    seed: int = 42,
) -> list[dict[str, object]]:
    """GMM 및 가격 계수 추정 흐름 검증용 가상 제안/예약 데이터를 만든다.

    가격 변화와 프로모션은 모의 고객에게 무작위 배정한다. 실제 PMS 데이터가
    아니며, 반환되는 mock_segment는 생성 결과 진단에만 쓰는 정답 라벨이다.
    """
    if n <= 0:
        raise ValueError("n must be positive")

    rng = np.random.default_rng(seed)
    weights = np.array([0.30, 0.25, 0.25, 0.20])
    means = np.array([
        [2.0, 1.4, 12.0, 290_000.0],
        [5.0, 2.2, 35.0, 410_000.0],
        [10.0, 1.3, 5.0, 330_000.0],
        [4.0, 3.1, 55.0, 520_000.0],
    ])
    stddevs = np.array([
        [1.0, 0.25, 5.0, 18_000.0],
        [1.5, 0.35, 8.0, 25_000.0],
        [2.0, 0.25, 2.5, 20_000.0],
        [1.5, 0.45, 10.0, 30_000.0],
    ])
    price_betas = np.array([-2.0, -3.0, -4.0, -1.5])
    intercepts = np.array([0.5, 0.1, 0.7, -0.2])
    weekend_betas = np.array([0.2, 0.6, -0.1, 0.8])
    promo_betas = {"none": 0.0, "package": 0.35, "breakfast": 0.2}
    categorical = [
        ("middle", "city", "none"),
        ("high", "river", "breakfast"),
        ("low", "city", "none"),
        ("high", "river", "fine_dining"),
    ]

    hidden_segments = rng.choice(len(weights), size=n, p=weights)
    profiles = np.vstack([
        rng.normal(means[segment], stddevs[segment])
        for segment in hidden_segments
    ])
    profiles[:, 0] = np.maximum(0.0, profiles[:, 0])
    profiles[:, 1] = np.maximum(1.0, profiles[:, 1])
    profiles[:, 2] = np.maximum(0.0, profiles[:, 2])
    profiles[:, 3] = np.maximum(100_000.0, profiles[:, 3])

    price_deltas = rng.choice(np.array([-0.10, 0.0, 0.10]), size=n)
    promo_values = rng.choice(np.array(PROMOTIONS), size=n)
    weekends = rng.integers(0, 2, size=n)
    logits = (
        intercepts[hidden_segments]
        + price_betas[hidden_segments] * price_deltas
        + np.array([promo_betas[promo] for promo in promo_values])
        + weekend_betas[hidden_segments] * weekends
    )
    booking_probabilities = expit(logits)
    booked = rng.binomial(1, booking_probabilities)

    records: list[dict[str, object]] = []
    for i, segment in enumerate(hidden_segments):
        record: dict[str, object] = {
            "booking_count": float(profiles[i, 0]),
            "stay_nights": float(profiles[i, 1]),
            "lead_time_days": float(profiles[i, 2]),
            "avg_room_rate": float(profiles[i, 3]),
            "price_delta": float(price_deltas[i]),
            "promo": str(promo_values[i]),
            "weekend": int(weekends[i]),
            "booked": int(booked[i]),
            "mock_segment": int(segment),
        }
        for field_name, value in zip(CATEGORICAL_FEATURES, categorical[segment]):
            record[field_name] = value
        records.append(record)
    return records


def _validate_records(records: Sequence[Mapping[str, object]]) -> np.ndarray:
    if not records:
        raise ValueError("at least one PMS record is required")
    matrix = []
    for row_index, record in enumerate(records):
        values = []
        for field_name in PROFILE_FEATURES:
            value = record.get(field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"record {row_index} field {field_name!r} must be numeric"
                )
            number = float(value)
            if not np.isfinite(number):
                raise ValueError(
                    f"record {row_index} field {field_name!r} must be finite"
                )
            values.append(number)
        if record.get("booked") not in (0, 1, False, True):
            raise ValueError(f"record {row_index} field 'booked' must be 0 or 1")
        price_delta = record.get("price_delta")
        if (isinstance(price_delta, bool)
                or not isinstance(price_delta, (int, float))
                or not np.isfinite(float(price_delta))):
            raise ValueError(
                f"record {row_index} field 'price_delta' must be finite numeric"
            )
        if record.get("promo") not in PROMOTIONS:
            raise ValueError(
                f"record {row_index} field 'promo' must be one of {PROMOTIONS}"
            )
        if record.get("weekend") not in (0, 1, False, True):
            raise ValueError(f"record {row_index} field 'weekend' must be 0 or 1")
        matrix.append(values)
    return np.asarray(matrix, dtype=float)


def _log_gaussian_density(
    values: np.ndarray,
    mean: np.ndarray,
    covariance: np.ndarray,
) -> np.ndarray:
    cholesky = np.linalg.cholesky(covariance)
    centered = (values - mean).T
    whitened = np.linalg.solve(cholesky, centered)
    mahalanobis = np.sum(whitened * whitened, axis=0)
    log_determinant = 2.0 * np.log(np.diag(cholesky)).sum()
    dimensions = values.shape[1]
    return -0.5 * (
        dimensions * np.log(2.0 * np.pi) + log_determinant + mahalanobis
    )


def _fit_gmm(
    values: np.ndarray,
    n_components: int,
    seed: int,
    max_iterations: int = 200,
    tolerance: float = 1e-5,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """표준화된 관측값에 full-covariance EM 가우시안 혼합 모델을 적합한다."""
    n_samples, dimensions = values.shape
    best: tuple[float, np.ndarray, np.ndarray, np.ndarray] | None = None
    global_covariance = np.atleast_2d(np.cov(values, rowvar=False))
    global_covariance += np.eye(dimensions) * 1e-4

    for initialization in range(5):
        rng = np.random.default_rng(seed + initialization)
        centers = [values[rng.integers(n_samples)]]
        while len(centers) < n_components:
            distances = np.min(
                np.sum(
                    (values[:, None, :] - np.asarray(centers)[None, :, :]) ** 2,
                    axis=2,
                ),
                axis=1,
            )
            total_distance = distances.sum()
            if total_distance <= 0:
                next_index = rng.integers(n_samples)
            else:
                next_index = rng.choice(
                    n_samples, p=distances / total_distance
                )
            centers.append(values[next_index])

        means = np.asarray(centers, dtype=float)
        covariances = np.repeat(
            global_covariance[None, :, :], n_components, axis=0
        )
        weights = np.full(n_components, 1.0 / n_components)
        previous_likelihood = -np.inf

        for _ in range(max_iterations):
            log_probabilities = np.column_stack([
                np.log(max(weights[k], 1e-12))
                + _log_gaussian_density(values, means[k], covariances[k])
                for k in range(n_components)
            ])
            log_normalizer = logsumexp(log_probabilities, axis=1)
            likelihood = float(log_normalizer.sum())
            responsibilities = np.exp(log_probabilities - log_normalizer[:, None])
            masses = responsibilities.sum(axis=0)

            weights = masses / n_samples
            means = responsibilities.T @ values / masses[:, None]
            for k in range(n_components):
                centered = values - means[k]
                weighted = centered * responsibilities[:, k, None]
                covariances[k] = (
                    weighted.T @ centered / masses[k]
                    + np.eye(dimensions) * 1e-5
                )
            if abs(likelihood - previous_likelihood) <= (
                tolerance * (1.0 + abs(likelihood))
            ):
                break
            previous_likelihood = likelihood

        candidate = (likelihood, weights.copy(), means.copy(), covariances.copy())
        if best is None or candidate[0] > best[0]:
            best = candidate

    if best is None:
        raise RuntimeError("GMM fitting did not produce a candidate model")
    _, weights, means, covariances = best
    return weights, means, covariances


def _predict_gmm(
    values: np.ndarray,
    weights: np.ndarray,
    means: np.ndarray,
    covariances: np.ndarray,
) -> np.ndarray:
    log_probabilities = np.column_stack([
        np.log(max(weights[k], 1e-12))
        + _log_gaussian_density(values, means[k], covariances[k])
        for k in range(len(weights))
    ])
    return np.argmax(log_probabilities, axis=1)


def _fit_logistic(features: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    """절편 포함 L2-regularized 이진 로지스틱 회귀 계수를 구한다."""
    design = np.column_stack((np.ones(len(features)), features))
    penalty = 1e-4

    def objective(coefficients: np.ndarray) -> tuple[float, np.ndarray]:
        logits = design @ coefficients
        probabilities = expit(logits)
        loss = np.logaddexp(0.0, logits).sum() - outcomes @ logits
        loss += 0.5 * penalty * np.sum(coefficients[1:] ** 2)
        gradient = design.T @ (probabilities - outcomes)
        gradient[1:] += penalty * coefficients[1:]
        return float(loss), gradient

    result = minimize(
        objective,
        np.zeros(design.shape[1]),
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": 1_000},
    )
    if not result.success:
        raise RuntimeError(f"logistic regression failed: {result.message}")
    return result.x


def fit_gmm_personas(
    records: Sequence[Mapping[str, object]],
    n_components: int = 4,
    seed: int = 42,
) -> GMMPersonaModel:
    """프로필 GMM 및 군집별 예약 로지스틱 회귀를 학습한다.

    가격 민감도 proxy는 군집별 가격 로그오즈 계수 β를
    clip(-β / 5, 0, 1)로 매핑한다. 이 매핑은 L1 MockAgent 계수와 맞춘
    프로토타입 척도이며, 실데이터로 검증된 탄력성 추정치와 동일하지 않다.
    """
    matrix = _validate_records(records)
    if n_components < 1 or n_components > len(records):
        raise ValueError("n_components must be between 1 and the record count")

    feature_means = matrix.mean(axis=0)
    feature_scales = matrix.std(axis=0)
    feature_scales[feature_scales == 0] = 1.0
    standardized = (matrix - feature_means) / feature_scales
    mixture_weights, mixture_means, mixture_covariances = _fit_gmm(
        standardized, n_components, seed
    )
    segments = _predict_gmm(
        standardized, mixture_weights, mixture_means, mixture_covariances
    )

    segment_fits: dict[int, SegmentFit] = {}
    category_counts: dict[int, dict[str, dict[str, int]]] = {}
    for segment in range(n_components):
        indices = np.flatnonzero(segments == segment)
        if len(indices) < 10:
            raise ValueError(
                f"GMM segment {segment} has only {len(indices)} records; "
                "use more data or fewer components"
            )
        y = np.asarray([int(records[i]["booked"]) for i in indices], dtype=int)
        if len(np.unique(y)) != 2:
            raise ValueError(
                f"GMM segment {segment} must contain both booked and not-booked rows"
            )

        x = []
        for i in indices:
            row = records[i]
            x.append([
                float(row["price_delta"]),
                float(row["promo"] == "package"),
                float(row["promo"] == "breakfast"),
                float(bool(row["weekend"])),
            ])
        coefficients = _fit_logistic(np.asarray(x), y)
        beta_price = coefficients[1]
        beta_weekend = coefficients[4]
        baseline_probability = float(expit(coefficients[0]))
        segment_fits[segment] = SegmentFit(
            sample_count=len(indices),
            price_log_odds_coefficient=float(beta_price),
            price_sensitivity_proxy=float(np.clip(
                -beta_price / PRICE_COEFFICIENT_SCALE, 0.0, 1.0
            )),
            baseline_booking_probability=baseline_probability,
            weekend_log_odds_coefficient=float(beta_weekend),
        )

        segment_counts: dict[str, dict[str, int]] = {}
        for field_name in CATEGORICAL_FEATURES:
            counts: dict[str, int] = {}
            for i in indices:
                value = records[i].get(field_name)
                if value is None:
                    continue
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"record {i} field {field_name!r} must be a non-empty string"
                    )
                category = value.strip()
                counts[category] = counts.get(category, 0) + 1
            if counts:
                segment_counts[field_name] = counts
        category_counts[segment] = segment_counts

    category_probabilities = {
        segment: {
            field_name: {
                category: count / sum(counts.values())
                for category, count in counts.items()
            }
            for field_name, counts in fields.items()
        }
        for segment, fields in category_counts.items()
    }
    return GMMPersonaModel(
        feature_means=feature_means,
        feature_scales=feature_scales,
        mixture_weights=mixture_weights,
        mixture_means=mixture_means,
        mixture_covariances=mixture_covariances,
        segment_fits=segment_fits,
        category_probabilities=category_probabilities,
        n_components=n_components,
    )


def generate_personas(
    model: GMMPersonaModel,
    n: int,
    seed: int = 42,
) -> list[Persona]:
    """학습한 GMM에서 합성 고객을 추출해 기존 L1 Persona로 반환한다."""
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return []

    rng = np.random.default_rng(seed)
    segments = rng.choice(
        model.n_components, size=n, p=model.mixture_weights
    )
    standardized_samples = np.vstack([
        rng.multivariate_normal(
            model.mixture_means[segment],
            model.mixture_covariances[segment],
        )
        for segment in segments
    ])
    profiles = (
        standardized_samples * model.feature_scales + model.feature_means
    )
    personas: list[Persona] = []
    for i, (values, segment_value) in enumerate(zip(profiles, segments)):
        segment = int(segment_value)
        fit = model.segment_fits[segment]
        sampled_categories: dict[str, str] = {}
        for field_name, probabilities in model.category_probabilities.get(
            segment, {}
        ).items():
            categories = list(probabilities)
            weights = list(probabilities.values())
            sampled_categories[field_name] = str(
                rng.choice(categories, p=weights)
            )
        personas.append(Persona(
            id=i,
            archetype=f"GMM_{segment}",
            base_demand=fit.baseline_booking_probability,
            price_sensitivity=fit.price_sensitivity_proxy,
            promo_affinity=0.5,
            weekend_preference=float(expit(fit.weekend_log_odds_coefficient)),
            stay_nights=max(1.0, float(values[1])),
            spend_index=max(0.5, float(values[3]) / BASE_ROOM_PRICE),
            booking_count=max(0.0, float(values[0])),
            lead_time_days=max(0.0, float(values[2])),
            avg_room_rate=max(0.0, float(values[3])),
            floor_preference=sampled_categories.get("floor_preference", "unknown"),
            view_preference=sampled_categories.get("view_preference", "unknown"),
            dining_preference=sampled_categories.get("dining_preference", "unknown"),
        ))
    return personas


def run_mock_demo(
    n_records: int = 4_000,
    n_personas: int = 20_000,
    seed: int = 42,
) -> dict[str, object]:
    """모의 PMS → GMM/로지스틱 추정 → 대규모 합성 프로필 생성."""
    records = generate_mock_pms_data(n=n_records, seed=seed)
    model = fit_gmm_personas(records, n_components=4, seed=seed)
    personas = generate_personas(model, n=n_personas, seed=seed)
    return {
        "records": records,
        "model": model,
        "personas": personas,
        "segments": {
            f"GMM_{segment}": {
                "sample_count": fit.sample_count,
                "price_beta": round(fit.price_log_odds_coefficient, 3),
                "price_sensitivity_proxy": round(
                    fit.price_sensitivity_proxy, 3
                ),
                "baseline_booking_probability": round(
                    fit.baseline_booking_probability, 3
                ),
            }
            for segment, fit in model.segment_fits.items()
        },
    }


if __name__ == "__main__":
    result = run_mock_demo()
    print(f"Mock records: {len(result['records']):,}")
    print(f"Generated personas: {len(result['personas']):,}")
    for segment, metrics in result["segments"].items():
        print(f"{segment}: {metrics}")
