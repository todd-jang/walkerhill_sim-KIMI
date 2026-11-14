"""미로피쉬 엔진 — 라운드(=하루) 루프와 결정론 재판관.

의도는 LLM/mock이 내고, 상호작용(폐쇄·슬롯 중복·좌석 초과)은 코드가 해소한다.
거절은 손님 기억으로, 전일 혼잡도는 다음 라운드 피드백으로 되돌아간다.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from .agents import ARCHETYPES, Guest, decide_venue, fill_ratio_by_venue_slot
from .intent import Intent
from .llm import DayBatchClient
from .queueing import QueueBoard
from .routing import route
from .world import SLOTS, World

REJECTIONS = ("CLOSED", "SLOT_DUP", "QUEUE")


def world_slot_minutes() -> int:
    return SLOTS[0][2] - SLOTS[0][1] if SLOTS else 60
REROUTE_ATTEMPTS = 2


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
    swarm_agents: Optional[Sequence[str]] = None,
    service_minutes: float = 20.0,
    patience_minutes: float = 25.0,
) -> Dict:
    t0 = time.perf_counter()
    guests = copy.deepcopy(list(guests))
    world.capacity_scale = scenario.capacity_scale
    events: List[dict] = []
    swarm_set = set(swarm_agents or ())
    swarm_frames: List[dict] = []
    prev_fill: Dict[str, float] = {v: 0.0 for v in world.venues}
    seat_total = {v: world.venues[v].seats for v in world.venues}
    queue = QueueBoard(service_minutes=service_minutes, patience_minutes=patience_minutes,
                       slot_minutes=world_slot_minutes())
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
        open_now = {s: set(world.open_venues(day, s)) - scenario.closed_venues for s, _, _ in SLOTS}

        def seat(guest: Guest, slot: str, venue: str):
            if venue not in open_now[slot]:
                return False, "CLOSED", 0.0, 0.0, [venue]
            if (slot, guest.id) in seated:
                return False, "SLOT_DUP", 0.0, 0.0, [venue]
            path, travel = route(guest.current_venue, venue)
            seats = world.venues[venue].capacity(scenario.capacity_scale)
            status, wait = queue.admit(day_index, slot, venue, seats, guest.party,
                                       arrive_min=travel, slot_end_min=world_slot_minutes())
            if status != "SEATED":
                return False, "QUEUE", wait, travel, path
            fill[(slot, venue)] = fill.get((slot, venue), 0) + guest.party
            seated.add((slot, guest.id))
            return True, "-", wait, travel, path

        for intent in sorted(intents, key=lambda i: (i.slot, i.agent_id)):
            guest = next((g for g in present if g.id == intent.agent_id), None)
            if guest is None:
                continue
            base = {"day": day_index, "slot": intent.slot, "agent": guest.id,
                    "venue": intent.venue, "party": guest.party,
                    "archetype": guest.archetype}
            ok, reason, wait_min, travel_min, path = seat(guest, intent.slot, intent.venue)
            if ok:
                guest.on_accept(day_index, intent.slot, intent.venue,
                                guest.party * scenario.price_of(intent.venue))
                guest.current_venue = intent.venue
                day_events.append({**base, "outcome": "ACCEPT", "reason": "-", "rerouted": False,
                                   "price_index": scenario.price_of(intent.venue),
                                   "wait_min": round(wait_min, 1), "travel_min": round(travel_min, 1),
                                   "path": path})
                continue
            guest.on_reject(day_index, intent.slot, intent.venue, reason)
            rerouted = False
            tried = [intent.venue]
            if reason != "SLOT_DUP":
                for _ in range(REROUTE_ATTEMPTS):
                    alt = decide_venue(guest, day_index, intent.slot, list(open_now[intent.slot]),
                                       scenario.price_index, prev_fill, seed, exclude=tried)
                    if alt is None or alt in tried:
                        break
                    tried.append(alt)
                    ok2, _r2, wait2, travel2, path2 = seat(guest, intent.slot, alt)
                    if ok2:
                        guest.on_accept(day_index, intent.slot, alt,
                                        guest.party * scenario.price_of(alt))
                        guest.current_venue = alt
                        guest.reroutes += 1
                        day_events.append({**base, "venue": alt, "outcome": "ACCEPT", "reason": "-",
                                           "rerouted": True, "from": intent.venue,
                                           "first_reject": reason,
                                           "price_index": scenario.price_of(alt),
                                           "wait_min": round(wait2, 1), "travel_min": round(travel2, 1),
                                           "path": path2})
                        rerouted = True
                        break
            if not rerouted:
                day_events.append({**base, "outcome": "REJECT", "reason": reason, "rerouted": False})

        events.extend(day_events)
        accepts = [e for e in day_events if e["outcome"] == "ACCEPT"]
        daily.append({
            "day": day_index,
            "date": day.isoformat(),
            "present": len(present),
            "visits": len(accepts),
            "parties": sum(e["party"] for e in accepts),
            "spend_proxy": round(sum(e["party"] * e["price_index"] for e in accepts), 1),
            "reroutes": sum(1 for e in day_events if e.get("rerouted")),
            "satisfaction": round(sum(g.satisfaction for g in present) / max(1, len(present)), 3),
            "exhausted": sum(1 for g in present if g.exhausted),
            "reject_closed": sum(1 for e in day_events if e.get("reason") == "CLOSED"),
            "reject_dup": sum(1 for e in day_events if e.get("reason") == "SLOT_DUP"),
            "reject_queue": sum(1 for e in day_events if e.get("reason") == "QUEUE"),
            "avg_wait_min": round(sum(e.get("wait_min", 0.0) for e in accepts) / max(1, len(accepts)), 1),
            "avg_travel_min": round(sum(e.get("travel_min", 0.0) for e in accepts) / max(1, len(accepts)), 1),
        })
        if swarm_set:
            by_slot: Dict[str, Dict[str, list]] = {s: {} for s, _, _ in SLOTS}
            for e in day_events:
                if e["agent"] in swarm_set:
                    by_slot[e["slot"]][e["agent"]] = [
                        e["venue"], 1 if e["outcome"] == "ACCEPT" else 0]
            for s, _, _ in SLOTS:
                swarm_frames.append({"d": day_index, "s": s,
                                     "a": [[a, v, ok] for a, (v, ok) in by_slot[s].items()]})
        for g in present:
            g.on_day_end()
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
            "reroutes": sum(1 for e in accepts_all if e.get("rerouted")),
            "avg_wait_min": round(sum(e.get("wait_min", 0.0) for e in accepts_all) / max(1, len(accepts_all)), 1),
            "avg_travel_min": round(sum(e.get("travel_min", 0.0) for e in accepts_all) / max(1, len(accepts_all)), 1),
            "balked_parties": sum(1 for e in events if e.get("reason") == "QUEUE"),
            "avg_satisfaction": round(sum(g.satisfaction for g in guests) / max(1, len(guests)), 3),
            "exhausted_guests": sum(1 for g in guests if g.exhausted),
            "satisfaction_by_archetype": {
                a: round(sum(g.satisfaction for g in guests if g.archetype == a)
                         / max(1, sum(1 for g in guests if g.archetype == a)), 3)
                for a in ARCHETYPES
            },
        },
        "daily": daily,
        "venue_slot_fill": venue_slot_ratio,
        **({"events": events} if include_events else {}),
        **({"swarm": {"agents": [{"i": i, "a": next(g.archetype for g in guests if g.id == i)}
                                 for i in sorted(swarm_set)],
                      "frames": swarm_frames}} if swarm_set else {}),
        "state_sample": [
            {"id": g.id, "archetype": g.archetype, "party": g.party,
             "satisfaction": round(g.satisfaction, 3), "frustration": g.frustration,
             "budget_left": round(g.budget_left, 1),
             "affinity": {k: round(v, 2) for k, v in
                          sorted(g.affinity.items(), key=lambda kv: -abs(kv[1]))[:4]},
             "history": [f"D{d + 1} {sl} {v} {o}" for d, sl, v, o in g.history[-6:]]}
            for g in sorted(guests, key=lambda g: (g.frustration, len(g.history)), reverse=True)[:5]
            if g.history
        ],
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
