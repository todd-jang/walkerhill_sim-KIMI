"""미로피쉬 엔진 — 라운드(=하루) 루프와 결정론 재판관.

의도는 LLM/mock이 내고, 상호작용(폐쇄·슬롯 중복·좌석 초과)은 코드가 해소한다.
거절은 손님 기억으로, 전일 혼잡도는 다음 라운드 피드백으로 되돌아간다.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from .agents import Guest, fill_ratio_by_venue_slot
from .intent import Intent
from .llm import DayBatchClient
from .world import SLOTS, World

REJECTIONS = ("CLOSED", "SLOT_DUP", "CAPACITY")


@dataclass
class Scenario:
    label: str = "baseline"
    price_index: Dict[str, float] = field(default_factory=dict)
    closed_venues: Set[str] = field(default_factory=set)
    capacity_scale: float = 1.0

    def price_of(self, venue: str) -> float:
        return self.price_index.get(venue, 1.0)


def simulate(
    world: World,
    guests: Sequence[Guest],
    horizon: int,
    seed: int,
    scenario: Scenario,
    client: DayBatchClient,
    include_events: bool = False,
) -> Dict:
    t0 = time.perf_counter()
    guests = copy.deepcopy(list(guests))
    world.capacity_scale = scenario.capacity_scale
    events: List[dict] = []
    prev_fill: Dict[str, float] = {v: 0.0 for v in world.venues}
    seat_total = {v: world.venues[v].seats for v in world.venues}
    daily: List[dict] = []

    for day_index in range(horizon):
        day = world.day_of(day_index)
        present = [g for g in guests if g.present_on(day_index)]
        intents = client.decide(
            day_index, day, present, world, scenario.price_index, prev_fill, seed,
            closed_extra={"venues": scenario.closed_venues},
        )
        seated: Set[tuple[str, str]] = set()
        fill: Dict[tuple[str, str], int] = {}
        day_events: List[dict] = []
        for intent in sorted(intents, key=lambda i: (i.slot, i.agent_id)):
            guest = next((g for g in present if g.id == intent.agent_id), None)
            if guest is None:
                continue
            base = {"day": day_index, "slot": intent.slot, "agent": guest.id,
                    "venue": intent.venue, "party": guest.party,
                    "archetype": guest.archetype}
            if intent.venue in scenario.closed_venues or intent.venue not in world.open_venues(day, intent.slot):
                day_events.append({**base, "outcome": "REJECT", "reason": "CLOSED"})
                continue
            if (intent.slot, guest.id) in seated:
                day_events.append({**base, "outcome": "REJECT", "reason": "SLOT_DUP"})
                continue
            cap = world.venues[intent.venue].capacity(scenario.capacity_scale)
            key = (intent.slot, intent.venue)
            if fill.get(key, 0) + guest.party > cap:
                day_events.append({**base, "outcome": "REJECT", "reason": "CAPACITY"})
                continue
            fill[key] = fill.get(key, 0) + guest.party
            seated.add((intent.slot, guest.id))
            day_events.append({**base, "outcome": "ACCEPT", "reason": "-",
                               "price_index": scenario.price_of(intent.venue)})

        events.extend(day_events)
        accepts = [e for e in day_events if e["outcome"] == "ACCEPT"]
        daily.append({
            "day": day_index,
            "date": day.isoformat(),
            "present": len(present),
            "visits": len(accepts),
            "parties": sum(e["party"] for e in accepts),
            "spend_proxy": round(sum(e["party"] * e["price_index"] for e in accepts), 1),
            "reject_closed": sum(1 for e in day_events if e.get("reason") == "CLOSED"),
            "reject_dup": sum(1 for e in day_events if e.get("reason") == "SLOT_DUP"),
            "reject_cap": sum(1 for e in day_events if e.get("reason") == "CAPACITY"),
        })
        for e in day_events:
            if e["outcome"] == "REJECT":
                g = next((x for x in guests if x.id == e["agent"]), None)
                if g is not None:
                    g.rejected[e["venue"]] = day_index
        prev_fill = fill_ratio_by_venue_slot(events, day_index, seat_total)

    wall = time.perf_counter() - t0
    accepts_all = [e for e in events if e["outcome"] == "ACCEPT"]
    venue_slot: Dict[str, Dict[str, int]] = {v: {s: 0 for s, _, _ in SLOTS} for v in world.venues}
    for e in accepts_all:
        venue_slot[e["venue"]][e["slot"]] += e["party"]
    open_days: Dict[tuple[str, str], int] = {}
    for day_index in range(horizon):
        day = world.day_of(day_index)
        for slot, _, _ in SLOTS:
            for v in world.open_venues(day, slot):
                open_days[(v, slot)] = open_days.get((v, slot), 0) + 1
    venue_slot_ratio = {
        v: {s: round(min(1.0, n / max(1, world.venues[v].capacity(scenario.capacity_scale) * open_days.get((v, s), 1))), 3)
            for s, n in row.items()}
        for v, row in venue_slot.items()
    }

    return {
        "scenario": scenario.label,
        "meta": {
            "horizon": horizon, "guests": len(guests), "seed": seed,
            "llm_mode": client.mode, "model": client.model if client.mode != "mock" else "mock-engine",
            "price_index": {k: v for k, v in scenario.price_index.items() if v != 1.0},
            "closed_venues": sorted(scenario.closed_venues),
            "capacity_scale": scenario.capacity_scale,
        },
        "summary": {
            "visits": len(accepts_all),
            "accept_rate": round(len(accepts_all) / max(1, len(events)), 4),
            "rejections": {r: sum(1 for e in events if e.get("reason") == r) for r in REJECTIONS},
            "unique_guests_served": len({e["agent"] for e in accepts_all}),
            "spend_proxy": round(sum(e["party"] * e["price_index"] for e in accepts_all), 1),
        },
        "daily": daily,
        "venue_slot_fill": venue_slot_ratio,
        **({"events": events} if include_events else {}),
        "events_sample": accepts_all[:30],
        "rejections_sample": [e for e in events if e["outcome"] == "REJECT"][:20],
        "llm_health": {"calls": client.calls, "fallbacks": client.fallbacks, "errors": client.errors[:10]},
        "speed": {
            "wall_seconds": round(wall, 3),
            "speedup_realtime_x": round(horizon * 86400 / wall, 1) if wall > 0 else 0.0,
        },
    }


def compare(
    world: World,
    guests: Sequence[Guest],
    horizon: int,
    seed: int,
    scenario: Scenario,
    client_factory,
) -> Dict:
    base_client = client_factory()
    alt_client = client_factory()
    a = simulate(world, guests, horizon, seed, Scenario("baseline"), base_client)
    b = simulate(world, guests, horizon, seed, scenario, alt_client)
    delta = {
        "visits": b["summary"]["visits"] - a["summary"]["visits"],
        "spend_proxy": round(b["summary"]["spend_proxy"] - a["summary"]["spend_proxy"], 1),
        "accept_rate": round(b["summary"]["accept_rate"] - a["summary"]["accept_rate"], 4),
        "rejections": {r: b["summary"]["rejections"][r] - a["summary"]["rejections"][r] for r in REJECTIONS},
    }
    return {"experiment": "compare", "scenario": scenario.label,
            "baseline": a, "treatment": b, "delta": delta}
