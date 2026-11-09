import json
import unittest

import numpy as np

from wise_sim.l3_abstain import evaluate_l3


class L3AbstainTests(unittest.TestCase):
    def test_insufficient_sample_returns_json_safe_null_interval(self):
        result = evaluate_l3([0.1] * 999, "room")
        self.assertTrue(result.abstain)
        self.assertEqual(result.reason, "insufficient_samples")
        self.assertEqual(result.confidence_interval, (None, None))
        self.assertIsNone(result.to_dict()["confidence_interval"][0])

    def test_out_of_scope_policy_abstains(self):
        result = evaluate_l3([0.1] * 1_000, "casino")
        self.assertTrue(result.abstain)
        self.assertEqual(result.reason, "out_of_scope")

    def test_bootstrap_sign_agreement_and_high_variance_triggers(self):
        uncertain = np.concatenate((np.ones(500), -np.ones(500)))
        low_agreement = evaluate_l3(
            uncertain, "room", seed=3
        )
        self.assertTrue(low_agreement.abstain)
        self.assertEqual(low_agreement.reason, "low_sign_agreement")
        self.assertLess(low_agreement.sign_agreement, 0.6)
        json.dumps(low_agreement.to_dict(), allow_nan=False)

        high_variance = evaluate_l3(
            np.concatenate((np.full(1_000, 0.01), np.ones(1_000))),
            "room",
        )
        self.assertTrue(high_variance.abstain)
        self.assertEqual(high_variance.reason, "high_variance")

    def test_assumed_transit_adds_note_but_does_not_abstain(self):
        result = evaluate_l3(
            [0.1] * 1_000,
            "fnb",
            transit_assumption=True,
        )
        self.assertFalse(result.abstain)
        self.assertIsNone(result.reason)
        self.assertIn("가정값 기반", result.note)
        self.assertGreaterEqual(result.confidence_interval[0], 0.0)
        self.assertEqual(result.sign_agreement, 1.0)

    def test_invalid_inputs_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            evaluate_l3([0.1, np.nan], "room")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            evaluate_l3([0.1], "unknown", min_samples=1)
        with self.assertRaisesRegex(ValueError, "one-dimensional"):
            evaluate_l3([[0.1, 0.2]], "room")


if __name__ == "__main__":
    unittest.main()
