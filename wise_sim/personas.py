"""WISE 페르소나 엔진 — Gate A(아키타입 캘리브레이션) 대응.

호텔 투숙객 아키타입을 정의하고, 아키타입 비율에 따라 페르소나를 생성한다.
캘리브레이션: generate_population()의 실제 아키타입 비율이 목표 비율과
오차 <= 5%p 이내인지 calibrate_check()로 검증한다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field


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

    def describe(self) -> str:
        """LLM 프롬프트용 자연어 서술."""
        return (
            f"페르소나 #{self.id} ({self.archetype}): "
            f"가격민감도 {self.price_sensitivity:.2f}, 프로모션 반응 {self.promo_affinity:.2f}, "
            f"주말 선호 {self.weekend_preference:.2f}, 평균 {self.stay_nights:.1f}박, "
            f"소비지수 {self.spend_index:.2f}"
        )


def generate_population(n: int, seed: int = 42,
                        archetypes: list[Archetype] = ARCHETYPES) -> list[Persona]:
    """목표 비율에 맞춰 페르소나 n명 생성. 고정 시드 → 재현성(Gate B)."""
    rng = random.Random(seed)
    pop: list[Persona] = []
    for i in range(n):
        r = rng.random()
        acc = 0.0
        at = archetypes[-1]
        for a in archetypes:
            acc += a.share
            if r <= acc:
                at = a
                break
        def jitter(v: float, spread: float = 0.08) -> float:
            return min(1.0, max(0.0, v + rng.gauss(0, spread)))
        pop.append(Persona(
            id=i,
            archetype=at.name,
            base_demand=jitter(at.base_demand),
            price_sensitivity=jitter(at.price_sensitivity),
            promo_affinity=jitter(at.promo_affinity),
            weekend_preference=jitter(at.weekend_preference),
            stay_nights=max(1.0, at.avg_stay_nights + rng.gauss(0, 0.3)),
            spend_index=max(0.5, at.avg_spend_index + rng.gauss(0, 0.15)),
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
        actual = counts.get(a.name, 0) / n
        err = abs(actual - a.share)
        report["items"].append({
            "archetype": a.name, "target": a.share,
            "actual": round(actual, 4), "error_pp": round(err * 100, 2),
        })
        if err > CALIBRATION_TOLERANCE:
            report["pass"] = False
    return report
