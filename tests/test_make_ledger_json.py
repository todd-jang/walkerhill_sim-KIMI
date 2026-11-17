"""실험 원장 생성기 테스트 (무작위화 규칙·목표 전환율·ITT 부호·결정성)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from make_ledger_json import generate_bundle, write_scenario  # noqa: E402
from wise_sim.web_app import analyze_bundle  # noqa: E402

BASE = {"per_arm": 120, "control_rate": 0.20, "exposure_rate": 0.85, "seed": 7}


class RandomizationRulesTest(unittest.TestCase):
    def test_blocks_have_one_unit_per_arm(self):
        bundle = generate_bundle(effect_pp=0.0)
        pairs = {}
        for item in bundle["assignments"]:
            pairs.setdefault(item["randomization_block_id"], []).append(item["assigned_variant"])
        for arms in pairs.values():
            self.assertEqual(sorted(arms), ["control", "treatment"])

    def test_segments_have_both_arms(self):
        bundle = generate_bundle(effect_pp=0.0)
        for segment in {"family", "adult"}:
            arms = {item["assigned_variant"] for item in bundle["assignments"] if item["segment"] == segment}
            self.assertEqual(arms, {"control", "treatment"})


class BookingRateTest(unittest.TestCase):
    def test_counts_match_target_rates_exactly(self):
        bundle = generate_bundle(per_arm=120, control_rate=0.20, effect_pp=20, seed=7)
        self.assertEqual(len(bundle["payments"]), 
            sum(round(0.20 * n) for n in (60, 60)) + sum(round(0.40 * n) for n in (60, 60)))
        self.assertEqual(len(bundle["bookings"]), len(bundle["payments"]))


class EffectDirectionTest(unittest.TestCase):
    def _metrics(self, effect_pp):
        result = analyze_bundle(generate_bundle(effect_pp=effect_pp, seed=7))
        return [segment["metrics"]["paid_booking"] for segment in result["segments"].values()]

    def test_strong_effect_positive_with_ci_excluding_zero(self):
        for metric in self._metrics(20):
            self.assertTrue(metric["available"])
            self.assertGreater(metric["itt_estimate"], 0)
            self.assertGreater(metric["confidence_interval"][0], 0)

    def test_backfire_negative_with_ci_excluding_zero(self):
        for metric in self._metrics(-15):
            self.assertLess(metric["itt_estimate"], 0)
            self.assertLess(metric["confidence_interval"][1], 0)

    def test_flat_effect_ci_includes_zero(self):
        for metric in self._metrics(0):
            self.assertEqual(metric["itt_estimate"], 0)
            self.assertLessEqual(metric["confidence_interval"][0], 0)
            self.assertGreaterEqual(metric["confidence_interval"][1], 0)


class DeterminismTest(unittest.TestCase):
    def test_generation_is_deterministic(self):
        first = generate_bundle(**BASE, effect_pp=5)
        second = generate_bundle(**BASE, effect_pp=5)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_write_scenario_persists_and_summarizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "scenario.json"
            summary = write_scenario(output, label="테스트", effect_pp=18, per_arm=120, seed=7)
            self.assertTrue(output.exists())
            self.assertIn("segments", summary)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["experiment"]["data_version"], "scenario-v1")


if __name__ == "__main__":
    unittest.main()
