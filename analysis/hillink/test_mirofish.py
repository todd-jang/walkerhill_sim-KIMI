"""미로피쉬 세계 — 결정론·불변식·배속·시나리오 검증."""
from __future__ import annotations

import sys
import unittest
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mirofish.agents import generate_guests
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
                {"agent_id": "g0001", "slot": "DINNER", "venue": "MYONGWOLGWAN"},
                {"agent_id": "ghost", "slot": "DINNER", "venue": "MYONGWOLGWAN"},
                {"agent_id": "g0001", "slot": "BRUNCH", "venue": "MYONGWOLGWAN"},
                {"agent_id": "g0001", "slot": "DINNER", "venue": "MOON"},
            ],
            valid_agents=["g0001"], valid_slots=[s for s, _, _ in SLOTS],
            valid_venues=["MYONGWOLGWAN"],
        )
        self.assertEqual(len(intents), 1)
        self.assertEqual(len(errors), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
