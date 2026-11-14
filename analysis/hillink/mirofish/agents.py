"""미로피쉬 손님 — 생성·동반 구성·결정론 mock 선호 모델.

mock 모델은 LLM 없이 돌아가는 결정론 에뮬레이터다. 같은 (seed, 손님, 날, 슬롯)이면
항상 같은 판단을 내고, 상태 피드백(전일 혼잡도·거절 기억)과 신의 눈 가격지수를 입력으로 받는다.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Sequence

ARCHETYPES = ("FAMILY", "COUPLE", "BUSINESS", "LUXURY", "LEISURE")
ARCHETYPE_LABEL = {
    "FAMILY": "가족·친족", "COUPLE": "커플·기념일", "BUSINESS": "비즈니스",
    "LUXURY": "럭셔리", "LEISURE": "휴식·산책",
}
DEFAULT_PRIORS = {"FAMILY": 0.30, "COUPLE": 0.25, "BUSINESS": 0.11, "LUXURY": 0.09, "LEISURE": 0.25}

SLOT_VISIT_P = {"BREAKFAST": 0.55, "MORNING": 0.25, "LUNCH": 0.50, "AFTERNOON": 0.45, "DINNER": 0.65}

VENUE_PREF: Dict[str, Dict[str, float]] = {
    "FAMILY": {"THE_BUFFET": 0.45, "GRAND_POOL": 0.50, "PIZZA_HILL": 0.40,
               "FOREST_PARK": 0.45, "MYONGWOLGWAN": 0.15, "THE_PAVILION": 0.20},
    "COUPLE": {"MYONGWOLGWAN": 0.40, "ONDAL": 0.30, "GEUMRYONG": 0.30, "LUX_BAR": 0.35,
               "GRAND_POOL": 0.25, "THE_PAVILION": 0.25, "FOREST_PARK": 0.20},
    "BUSINESS": {"THE_BUFFET": 0.50, "LE_PASSAGE": 0.40, "WELLNESS_SAUNA": 0.30, "LUX_BAR": 0.20},
    "LUXURY": {"MYONGWOLGWAN": 0.45, "ONDAL": 0.40, "GEUMRYONG": 0.45, "LUX_BAR": 0.40,
               "WELLNESS_SAUNA": 0.50, "GRAND_POOL": 0.30},
    "LEISURE": {"THE_BUFFET": 0.35, "FOREST_PARK": 0.60, "GRAND_POOL": 0.45,
                "PIZZA_HILL": 0.25, "THE_PAVILION": 0.20},
}
PRICE_SENSITIVITY = {"FAMILY": 0.6, "COUPLE": 0.4, "BUSINESS": 0.15, "LUXURY": 0.05, "LEISURE": 0.8}
CROWD_AVERSION = {"FAMILY": 0.2, "COUPLE": 0.3, "BUSINESS": 0.1, "LUXURY": 0.0, "LEISURE": 0.5}


@dataclass
class Guest:
    id: str
    arrival_day: int
    nights: int
    party: int
    archetype: str
    loyalty: str
    rejected: Dict[str, int] = field(default_factory=dict)

    def present_on(self, day_index: int) -> bool:
        return self.arrival_day <= day_index < self.arrival_day + self.nights


def generate_guests(
    n: int,
    horizon: int,
    seed: int,
    priors: Optional[Dict[str, float]] = None,
) -> List[Guest]:
    rng = random.Random(f"guests:{seed}")
    priors = dict(priors or DEFAULT_PRIORS)
    total = sum(priors.values())
    keys = list(priors)
    probs = [priors[k] / total for k in keys]
    guests: List[Guest] = []
    for i in range(n):
        arrival = rng.randrange(0, max(1, horizon - 1))
        nights = rng.choice([1, 1, 1, 2, 2, 3])
        arch = rng.choices(keys, weights=probs, k=1)[0]
        party = {
            "FAMILY": rng.choice([3, 3, 4, 4, 5]),
            "COUPLE": 2,
            "BUSINESS": rng.choice([1, 1, 2]),
            "LUXURY": rng.choice([2, 2, 3]),
            "LEISURE": rng.choice([2, 2, 3]),
        }[arch]
        loyalty = rng.choices(["low", "medium", "high"], weights=[0.607, 0.321, 0.072], k=1)[0]
        guests.append(Guest(f"g{i:04d}", arrival, nights, party, arch, loyalty))
    return guests


def decide_venue(
    guest: Guest,
    day_index: int,
    slot_code: str,
    open_venues: Sequence[str],
    price_index: Dict[str, float],
    prev_fill: Dict[str, float],
    seed: int,
) -> Optional[str]:
    rng = random.Random(f"{seed}:{guest.id}:{day_index}:{slot_code}")
    if rng.random() > SLOT_VISIT_P[slot_code] * (1.15 if guest.archetype == "LEISURE" else 1.0):
        return None
    prefs = VENUE_PREF[guest.archetype]
    sens = PRICE_SENSITIVITY[guest.archetype]
    crowd = CROWD_AVERSION[guest.archetype]
    weights: Dict[str, float] = {}
    for venue in open_venues:
        base = prefs.get(venue, 0.02)
        price_term = (price_index.get(venue, 1.0)) ** (-sens)
        crowd_term = max(0.05, 1.0 - crowd * prev_fill.get(f"{venue}", 0.0))
        memory_term = 0.35 if guest.rejected.get(venue) == day_index - 1 else 1.0
        weights[venue] = base * price_term * crowd_term * memory_term
    if not weights:
        return None
    total = sum(weights.values())
    if total <= 0:
        return None
    pick = rng.random() * total
    acc = 0.0
    for venue, w in weights.items():
        acc += w
        if pick <= acc:
            return venue
    return next(iter(weights))


def fill_ratio_by_venue_slot(events: List[dict], day_index: int, seat_total: Dict[str, int]) -> Dict[str, float]:
    """전일 상태 피드백 — 업장별 최대 슬롯 점유율(0~1)."""
    per: Dict[str, int] = {}
    for ev in events:
        if ev["day"] != day_index or ev["outcome"] != "ACCEPT":
            continue
        per[ev["venue"]] = max(per.get(ev["venue"], 0), ev["party"])
    return {v: min(1.0, per.get(v, 0) / max(1, seat_total.get(v, 1))) for v in seat_total}
