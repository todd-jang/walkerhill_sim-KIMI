"""미로피쉬 세계 — 결정론·불변식·배속·시나리오 검증."""
from __future__ import annotations

import sys
import unittest
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mirofish.agents import Guest, decide_venue, generate_guests
from mirofish.engine import Scenario, compare, simulate
from mirofish.intent import parse_intents
from mirofish.llm import DayBatchClient
from mirofish.world import SLOTS, World

MIN_SPEEDUP = 24.0


def run(days=14, guests=600, seed=17, scenario=None, include_events=True):
    world = World()
    gs = generate_guests(guests, days, seed)
    return world, simulate(
        world, gs, days, seed, scenario or Scenario(), DayBatchClient(mode="mock"),
        include_events=include_events,
    )


class DeterminismTests(unittest.TestCase):
    def test_same_seed_same_summary(self):
        _, a = run()
        _, b = run()
        self.assertEqual(a["summary"], b["summary"])
        self.assertEqual(a["daily"], b["daily"])


class InvariantTests(unittest.TestCase):
    def test_capacity_never_exceeded_per_slot(self):
        world, res = run()
        fill = defaultdict(int)
        for e in res["events"]:
            if e["outcome"] == "ACCEPT":
                fill[(e["day"], e["slot"], e["venue"])] += e["party"]
        for (_, _, venue), n in fill.items():
            self.assertLessEqual(n, world.venues[venue].capacity())

    def test_no_accept_at_closed_venue(self):
        _, res = run(scenario=Scenario("closed", closed_venues={"GEUMRYONG"}))
        self.assertFalse([e for e in res["events"] if e["outcome"] == "ACCEPT" and e["venue"] == "GEUMRYONG"])
        self.assertGreater(res["summary"]["rejections"]["CLOSED"], 0)

    def test_one_venue_per_slot_per_agent(self):
        _, res = run()
        seen = set()
        for e in res["events"]:
            if e["outcome"] != "ACCEPT":
                continue
            key = (e["day"], e["slot"], e["agent"])
            self.assertNotIn(key, seen)
            seen.add(key)

    def test_slots_and_venues_valid(self):
        _, res = run()
        slots = {s for s, _, _ in SLOTS}
        for e in res["events"]:
            self.assertIn(e["slot"], slots)


class SpeedTests(unittest.TestCase):
    def test_speed_at_least_24x(self):
        world = World()
        gs = generate_guests(2000, 31, 5)
        res = simulate(world, gs, 31, 5, Scenario(), DayBatchClient(mode="mock"))
        self.assertGreaterEqual(res["speed"]["speedup_realtime_x"], MIN_SPEEDUP)
        self.assertGreater(res["speed"]["speedup_realtime_x"], 100_000.0)


class ScenarioTests(unittest.TestCase):
    def test_price_hike_changes_spend_proxy(self):
        world = World()
        gs = generate_guests(800, 14, 21)
        res = compare(
            world, gs, 14, 21,
            Scenario("treatment", price_index={"THE_BUFFET": 1.4}),
            lambda: DayBatchClient(mode="mock"),
        )
        self.assertNotEqual(res["delta"]["spend_proxy"], 0.0)


class IntentValidationTests(unittest.TestCase):
    def test_rejects_unknown_agent_and_bad_venue(self):
        intents, errors = parse_intents(
            [
                {"agent_id": "g0001", "slot": "19:00", "venue": "MYONGWOLGWAN"},
                {"agent_id": "ghost", "slot": "19:00", "venue": "MYONGWOLGWAN"},
                {"agent_id": "g0001", "slot": "23:00", "venue": "MYONGWOLGWAN"},
                {"agent_id": "g0001", "slot": "19:00", "venue": "MOON"},
            ],
            valid_agents=["g0001"], valid_slots=[s for s, _, _ in SLOTS],
            valid_venues=["MYONGWOLGWAN"],
        )
        self.assertEqual(len(intents), 1)
        self.assertEqual(len(errors), 3)


class StatefulAgentTests(unittest.TestCase):
    def test_guest_state_updates(self):
        g = Guest("g1", 0, 2, 2, "FAMILY", "low", budget=10.0)
        g.on_accept(0, "LUNCH", "THE_BUFFET", 4.0)
        self.assertEqual(g.spent, 4.0)
        self.assertGreater(g.satisfaction, 0.6)
        self.assertGreater(g.affinity["THE_BUFFET"], 0)
        g.on_reject(0, "DINNER", "PIZZA_HILL", "CAPACITY")
        self.assertEqual(g.frustration, 1)
        self.assertLess(g.satisfaction, 0.6 + 0.05)
        self.assertLess(g.affinity["PIZZA_HILL"], 0)
        self.assertEqual(len(g.history), 2)
        g.on_day_end()
        self.assertLess(abs(g.affinity["THE_BUFFET"]), 0.15)

    def test_exhausted_guest_skips_slots(self):
        g = Guest("g2", 0, 2, 3, "BUSINESS", "medium", budget=5.0)
        g.on_accept(0, "BREAKFAST", "THE_BUFFET", 5.0)
        self.assertTrue(g.exhausted)
        venue = decide_venue(g, 0, "19:00", ["THE_BUFFET", "LE_PASSAGE"], {}, {}, 17)
        self.assertIsNone(venue)

    def test_rejected_guest_avoids_same_venue(self):
        g = Guest("g3", 0, 3, 2, "LEISURE", "low", budget=60.0)
        for _ in range(4):
            g.on_reject(0, "LUNCH", "PIZZA_HILL", "CAPACITY")
        picks = [decide_venue(g, 1, "12:00", ["PIZZA_HILL", "THE_PAVILION"], {}, {}, s)
                 for s in range(600)]
        chosen = [p for p in picks if p]
        self.assertGreaterEqual(chosen.count("THE_PAVILION"), chosen.count("PIZZA_HILL"))

    def test_reroutes_under_contention(self):
        world = World()
        gs = generate_guests(3000, 7, 99)
        res = simulate(world, gs, 7, 99, Scenario(), DayBatchClient(mode="mock"), include_events=True)
        rerouted = [e for e in res["events"] if e.get("rerouted")]
        self.assertGreater(res["summary"]["reroutes"], 0)
        self.assertEqual(res["summary"]["reroutes"], len(rerouted))
        for e in rerouted:
            self.assertIn("from", e)
            self.assertNotEqual(e["venue"], e["from"])

    def test_satisfaction_recorded_per_day(self):
        world = World()
        gs = generate_guests(1200, 10, 7)
        res = simulate(world, gs, 10, 7, Scenario(), DayBatchClient(mode="mock"))
        self.assertTrue(all(0.0 <= d["satisfaction"] <= 1.0 for d in res["daily"]))
        self.assertGreaterEqual(res["summary"]["avg_satisfaction"], 0.3)
        self.assertIn("satisfaction_by_archetype", res["summary"])


class SwarmTests(unittest.TestCase):
    def test_swarm_frames_cover_horizon_and_subset(self):
        world = World()
        gs = generate_guests(300, 5, 3)
        ids = [g.id for g in gs[:40]]
        res = simulate(world, gs, 5, 3, Scenario(), DayBatchClient(mode="mock"), swarm_agents=ids)
        sw = res["swarm"]
        self.assertEqual(len(sw["frames"]), 5 * len(SLOTS))
        allowed = set(ids)
        for fr in sw["frames"]:
            for a, v, ok in fr["a"]:
                self.assertIn(a, allowed)
                self.assertIn(v, world.venues)
                self.assertIn(ok, (0, 1))

    def test_swarm_absent_when_disabled(self):
        world = World()
        gs = generate_guests(50, 2, 3)
        res = simulate(world, gs, 2, 3, Scenario(), DayBatchClient(mode="mock"))
        self.assertNotIn("swarm", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
