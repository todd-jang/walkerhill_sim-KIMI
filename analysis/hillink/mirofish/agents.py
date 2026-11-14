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

HOUR_VISIT_P = {7: 0.30, 8: 0.34, 9: 0.24, 10: 0.12, 11: 0.18, 12: 0.32, 13: 0.30, 14: 0.15,
                15: 0.18, 16: 0.20, 17: 0.22, 18: 0.32, 19: 0.34, 20: 0.26, 21: 0.16}
ARCH_HOUR_BIAS = {
    "FAMILY": {7: 1.4, 8: 1.5, 9: 1.2, 12: 1.3, 13: 1.3, 18: 1.3, 19: 1.3},
    "COUPLE": {19: 1.4, 20: 1.4, 21: 1.2, 18: 1.2},
    "BUSINESS": {12: 1.5, 13: 1.4, 18: 1.3, 19: 1.2},
    "LUXURY": {19: 1.4, 20: 1.4, 21: 1.3},
    "LEISURE": {8: 1.2, 9: 1.1, 15: 1.2, 16: 1.2},
}

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
BUDGET = {"FAMILY": 42.0, "COUPLE": 60.0, "BUSINESS": 30.0, "LUXURY": 120.0, "LEISURE": 36.0}
INITIAL_SATISFACTION = 0.6
SATISFACTION_ACCEPT = 0.05
SATISFACTION_CAPACITY = 0.09
SATISFACTION_CLOSED = 0.02
AFFINITY_ACCEPT = 0.15
AFFINITY_REJECT = -0.10
AFFINITY_DECAY = 0.98
LOW_SATISFACTION = 0.30


@dataclass
class Guest:
    id: str
    arrival_day: int
    nights: int
    party: int
    archetype: str
    loyalty: str
    budget: float = 0.0
    spent: float = 0.0
    satisfaction: float = INITIAL_SATISFACTION
    frustration: int = 0
    affinity: Dict[str, float] = field(default_factory=dict)
    history: List[tuple] = field(default_factory=list)
    rejections: int = 0
    reroutes: int = 0
    current_venue: str | None = None

    def present_on(self, day_index: int) -> bool:
        return self.arrival_day <= day_index < self.arrival_day + self.nights

    @property
    def budget_left(self) -> float:
        return max(0.0, self.budget - self.spent)

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.budget

    def memory_digest(self, limit: int = 3) -> List[str]:
        return [f"D{d} {s} {v} {o}" for d, s, v, o in self.history[-limit:]]

    def on_accept(self, day_index: int, slot: str, venue: str, cost: float) -> None:
        self.spent += cost
        self.satisfaction = min(1.0, self.satisfaction + SATISFACTION_ACCEPT)
        self.affinity[venue] = self.affinity.get(venue, 0.0) + AFFINITY_ACCEPT
        self.history.append((day_index, slot, venue, "ACCEPT"))

    def on_reject(self, day_index: int, slot: str, venue: str, reason: str) -> None:
        self.rejections += 1
        if reason == "CAPACITY":
            self.satisfaction = max(0.0, self.satisfaction - SATISFACTION_CAPACITY)
            self.frustration += 1
        elif reason == "CLOSED":
            self.satisfaction = max(0.0, self.satisfaction - SATISFACTION_CLOSED)
        self.affinity[venue] = self.affinity.get(venue, 0.0) + AFFINITY_REJECT
        self.history.append((day_index, slot, venue, reason))

    def on_day_end(self) -> None:
        for venue in list(self.affinity):
            self.affinity[venue] *= AFFINITY_DECAY
            if abs(self.affinity[venue]) < 1e-3:
                del self.affinity[venue]


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
        guests.append(Guest(f"g{i:04d}", arrival, nights, party, arch, loyalty,
                            budget=BUDGET[arch]))
    return guests


def decide_venue(
    guest: Guest,
    day_index: int,
    slot_code: str,
    open_venues: Sequence[str],
    price_index: Dict[str, float],
    prev_fill: Dict[str, float],
    seed: int,
    exclude: Optional[Sequence[str]] = None,
) -> Optional[str]:
    if guest.exhausted or guest.satisfaction < LOW_SATISFACTION * 0.5:
        return None
    hour = int(slot_code.split(":")[0])
    rng = random.Random(f"{seed}:{guest.id}:{day_index}:{slot_code}:{len(exclude or ())}")
    bias = ARCH_HOUR_BIAS.get(guest.archetype, {}).get(hour, 1.0)
    willingness = HOUR_VISIT_P.get(hour, 0.1) * bias * (0.75 + 0.5 * guest.satisfaction)
    if rng.random() > willingness:
        return None

    prefs = VENUE_PREF[guest.archetype]
    sens = PRICE_SENSITIVITY[guest.archetype]
    crowd = CROWD_AVERSION[guest.archetype] * (1.0 + 0.5 * guest.frustration)
    banned = set(exclude or ())

    weights: Dict[str, float] = {}
    for venue in open_venues:
        if venue in banned:
            continue
        base = prefs.get(venue, 0.02) * (1.0 + guest.affinity.get(venue, 0.0))
        price_term = (price_index.get(venue, 1.0)) ** (-sens)
        crowd_term = max(0.05, 1.0 - crowd * prev_fill.get(venue, 0.0))
        affordability = 0.15 if guest.budget_left < guest.party * 0.6 else 1.0
        weights[venue] = max(0.0, base) * price_term * crowd_term * affordability

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
