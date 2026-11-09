"""WISE 페르소나 엔진 — Gate A(아키타입 캘리브레이션) 대응.

호텔 투숙객 아키타입을 정의하고, 아키타입 비율에 따라 페르소나를 생성한다.
캘리브레이션: generate_population()의 실제 아키타입 비율이 목표 비율과
오차 <= 5%p 이내인지 calibrate_check()로 검증한다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Mapping


NUMERIC_PROFILE_FIELDS = (
    "base_demand",
    "price_sensitivity",
    "promo_affinity",
    "weekend_preference",
    "stay_nights",
    "spend_index",
    "booking_count",
    "cancellation_rate",
    "lead_time_days",
    "avg_room_rate",
)
CATEGORICAL_PROFILE_FIELDS = (
    "floor_preference",
    "view_preference",
    "dining_preference",
)


@dataclass(frozen=True)
class NumericDistribution:
    mean: float
    stddev: float


@dataclass(frozen=True)
class PopulationProfile:
    """한 population_id에서 관측한 프로필 분포."""
    share: float
    numeric: dict[str, NumericDistribution]
    categorical: dict[str, dict[str, float]]


@dataclass(frozen=True)
class ProfileCalibration:
    """익명화·집계된 PMS 프로필로부터 학습한 생성 파라미터."""
    populations: dict[str, PopulationProfile]
    sample_count: int


def fit_calibration(
    records: Iterable[Mapping[str, object]],
    population_key: str = "population_id",
) -> ProfileCalibration:
    """고객별 집계 행의 평균·표준편차와 카테고리 분포를 계산한다.

    원시 예약 이벤트가 아니라 고객별로 집계된 행을 입력한다. 예: 3년간 예약
    건수, 평균 리드타임, 취소율, 선호도 및 가격 민감도 추정치. 가격 민감도는
    입력 데이터가 제공할 때만 학습하며, 예약 이력만으로 인과 탄력성을 추정하지 않는다.
    """
    grouped: dict[str, list[Mapping[str, object]]] = {}
    for row_index, record in enumerate(records):
        population_id = record.get(population_key)
        if not isinstance(population_id, str) or not population_id.strip():
            raise ValueError(
                f"record {row_index} must contain a non-empty {population_key!r}"
            )
        grouped.setdefault(population_id.strip(), []).append(record)
    if not grouped:
        raise ValueError("at least one profile record is required")

    total = sum(len(rows) for rows in grouped.values())
    populations: dict[str, PopulationProfile] = {}
    for population_id, rows in grouped.items():
        numeric: dict[str, NumericDistribution] = {}
        for field_name in NUMERIC_PROFILE_FIELDS:
            values = []
            for row_index, row in enumerate(rows):
                value = row.get(field_name)
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError(
                        f"{field_name!r} in population {population_id!r}, "
                        f"record {row_index} must be numeric"
                    )
                number = float(value)
                if not isfinite(number):
                    raise ValueError(
                        f"{field_name!r} in population {population_id!r} "
                        "must be finite"
                    )
                values.append(number)
            if values:
                mean = sum(values) / len(values)
                variance = (sum((value - mean) ** 2 for value in values)
                            / max(len(values) - 1, 1))
                numeric[field_name] = NumericDistribution(
                    mean=mean, stddev=variance ** 0.5
                )

        categorical: dict[str, dict[str, float]] = {}
        for field_name in CATEGORICAL_PROFILE_FIELDS:
            counts: dict[str, int] = {}
            for row in rows:
                value = row.get(field_name)
                if value is None:
                    continue
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f"{field_name!r} in population {population_id!r} "
                        "must be a non-empty string"
                    )
                category = value.strip()
                counts[category] = counts.get(category, 0) + 1
            observed = sum(counts.values())
            if observed:
                categorical[field_name] = {
                    value: count / observed for value, count in counts.items()
                }

        populations[population_id] = PopulationProfile(
            share=len(rows) / total,
            numeric=numeric,
            categorical=categorical,
        )
    return ProfileCalibration(populations=populations, sample_count=total)


@dataclass(frozen=True)
class Archetype:
    """투숙객 아키타입. price_sensitivity가 높을수록 가격에 민감."""
    name: str
    share: float                 # 모집단 내 비율 (합계 1.0)
    base_demand: float           # 기본 예약 확률 성향 (0~1)
    price_sensitivity: float     # 가격 민감도 (0~1, 높을수록 탄력적)
    promo_affinity: float        # 프로모션 반응도 (0~1)
    weekend_preference: float    # 주말 선호 (0~1)
    avg_stay_nights: float       # 평균 숙박 일수
    avg_spend_index: float       # 부대 소비 지수 (기본 객실료 대비)


# v1.1 제안 기본값 — 워커힐 실데이터(Gate D) 확보 시 재추정
ARCHETYPES: list[Archetype] = [
    Archetype("가족여행",   0.30, 0.55, 0.70, 0.80, 0.90, 2.1, 1.30),
    Archetype("커플",       0.25, 0.60, 0.45, 0.70, 0.85, 1.6, 1.45),
    Archetype("비즈니스",   0.20, 0.50, 0.20, 0.15, 0.25, 1.2, 1.10),
    Archetype("외국인관광", 0.15, 0.58, 0.50, 0.40, 0.60, 2.8, 1.20),
    Archetype("프리미엄",   0.10, 0.65, 0.15, 0.30, 0.70, 1.8, 2.10),
]

CALIBRATION_TOLERANCE = 0.05  # Gate A: 오차 ≤ 5%p


@dataclass
class Persona:
    id: int
    archetype: str
    # 개인차: 아키타입 파라미터에 노이즈를 준 값
    base_demand: float
    price_sensitivity: float
    promo_affinity: float
    weekend_preference: float
    stay_nights: float
    spend_index: float
    booking_count: float = 0.0
    cancellation_rate: float = 0.0
    lead_time_days: float = 0.0
    avg_room_rate: float = 0.0
    floor_preference: str = "unknown"
    view_preference: str = "unknown"
    dining_preference: str = "unknown"

    @property
    def population_id(self) -> str:
        """PMS 데이터의 population_id와 기존 archetype 이름을 연결한다."""
        return self.archetype

    def describe(self) -> str:
        """LLM 프롬프트용 자연어 서술."""
        return (
            f"페르소나 #{self.id} (population_id={self.population_id}): "
            f"가격민감도 {self.price_sensitivity:.2f}, 프로모션 반응 {self.promo_affinity:.2f}, "
            f"주말 선호 {self.weekend_preference:.2f}, 평균 {self.stay_nights:.1f}박, "
            f"소비지수 {self.spend_index:.2f}, 과거 예약 {self.booking_count:.0f}건, "
            f"취소율 {self.cancellation_rate:.2f}, 리드타임 {self.lead_time_days:.0f}일, "
            f"선호 층 {self.floor_preference}, 뷰 {self.view_preference}, "
            f"다이닝 {self.dining_preference}"
        )


def generate_population(n: int, seed: int = 42,
                        archetypes: list[Archetype] = ARCHETYPES,
                        calibration: ProfileCalibration | None = None
                        ) -> list[Persona]:
    """목표 비율에 맞춰 페르소나 생성. 보정치가 있으면 PMS 분포를 사용한다."""
    if n < 0:
        raise ValueError("n must be non-negative")
    if calibration is not None:
        if not calibration.populations:
            raise ValueError("calibration must contain at least one population")
        groups = list(calibration.populations.items())
        total_share = sum(profile.share for _, profile in groups)
        if total_share <= 0 or any(profile.share < 0 for _, profile in groups):
            raise ValueError("calibration population shares must be positive")
    else:
        groups = [(a.name, PopulationProfile(a.share, {}, {}))
                  for a in archetypes]
        total_share = sum(profile.share for _, profile in groups)
        if not groups or total_share <= 0 or any(
                profile.share < 0 for _, profile in groups):
            raise ValueError("archetype shares must be positive")

    archetypes_by_name = {a.name: a for a in archetypes}
    rng = random.Random(seed)
    pop: list[Persona] = []
    for i in range(n):
        r = rng.random() * total_share
        acc = 0.0
        population_id, profile = groups[-1]
        for candidate_id, candidate_profile in groups:
            acc += candidate_profile.share
            if r <= acc:
                population_id, profile = candidate_id, candidate_profile
                break

        at = archetypes_by_name.get(population_id)
        defaults = {
            "base_demand": at.base_demand if at else 0.55,
            "price_sensitivity": at.price_sensitivity if at else 0.50,
            "promo_affinity": at.promo_affinity if at else 0.50,
            "weekend_preference": at.weekend_preference if at else 0.50,
            "stay_nights": at.avg_stay_nights if at else 1.5,
            "spend_index": at.avg_spend_index if at else 1.0,
            "booking_count": 0.0,
            "cancellation_rate": 0.0,
            "lead_time_days": 0.0,
            "avg_room_rate": 0.0,
        }

        def sample_numeric(field_name: str, minimum: float | None = None,
                           maximum: float | None = None) -> float:
            distribution = profile.numeric.get(field_name)
            if distribution is None:
                if field_name in (
                    "base_demand", "price_sensitivity", "promo_affinity",
                    "weekend_preference",
                ):
                    value = jitter(defaults[field_name])
                elif field_name == "stay_nights":
                    value = defaults[field_name] + rng.gauss(0, 0.3)
                elif field_name == "spend_index":
                    value = defaults[field_name] + rng.gauss(0, 0.15)
                else:
                    value = defaults[field_name]
            else:
                value = rng.gauss(distribution.mean, distribution.stddev)
            if minimum is not None:
                value = max(minimum, value)
            if maximum is not None:
                value = min(maximum, value)
            return value

        def sample_category(field_name: str) -> str:
            weights = profile.categorical.get(field_name)
            if not weights:
                return "unknown"
            pick = rng.random()
            cumulative = 0.0
            for value, weight in weights.items():
                cumulative += weight
                if pick <= cumulative:
                    return value
            return next(reversed(weights))

        def jitter(v: float, spread: float = 0.08) -> float:
            return min(1.0, max(0.0, v + rng.gauss(0, spread)))
        pop.append(Persona(
            id=i,
            archetype=population_id,
            base_demand=sample_numeric("base_demand", 0.0, 1.0),
            price_sensitivity=sample_numeric("price_sensitivity", 0.0, 1.0),
            promo_affinity=sample_numeric("promo_affinity", 0.0, 1.0),
            weekend_preference=sample_numeric("weekend_preference", 0.0, 1.0),
            stay_nights=sample_numeric("stay_nights", 1.0),
            spend_index=sample_numeric("spend_index", 0.5),
            booking_count=sample_numeric("booking_count", 0.0),
            cancellation_rate=sample_numeric("cancellation_rate", 0.0, 1.0),
            lead_time_days=sample_numeric("lead_time_days", 0.0),
            avg_room_rate=sample_numeric("avg_room_rate", 0.0),
            floor_preference=sample_category("floor_preference"),
            view_preference=sample_category("view_preference"),
            dining_preference=sample_category("dining_preference"),
        ))
    return pop


def calibrate_check(pop: list[Persona],
                    archetypes: list[Archetype] = ARCHETYPES) -> dict:
    """Gate A: 아키타입 비율 캘리브레이션 오차 ≤ 5%p 검증."""
    counts: dict[str, int] = {}
    for p in pop:
        counts[p.archetype] = counts.get(p.archetype, 0) + 1
    n = len(pop)
    report = {"n": n, "tolerance": CALIBRATION_TOLERANCE, "items": [], "pass": True}
    for a in archetypes:
        actual = counts.get(a.name, 0) / n if n else 0.0
        err = abs(actual - a.share)
        report["items"].append({
            "archetype": a.name, "target": a.share,
            "actual": round(actual, 4), "error_pp": round(err * 100, 2),
        })
        if n == 0 or err > CALIBRATION_TOLERANCE:
            report["pass"] = False
    return report
