import copy
import random
import unittest

from wise_sim.experiment_pipeline import generate_synthetic_records
from wise_sim.observational_analysis import estimate_observational_effect


def confounded_synthetic_observational_records(n=1_000, seed=19):
    records = generate_synthetic_records(n=n, seed=seed)
    rng = random.Random(seed + 1)
    for index, record in enumerate(records):
        high_demand = index % 2
        treatment_probability = 0.8 if high_demand else 0.2
        treated = rng.random() < treatment_probability
        booked = rng.random() < (0.15 + 0.35 * high_demand + 0.15 * treated)

        record["experiment"]["design"] = "synthetic_observational"
        record["experiment"]["assigned_arm"] = "not_randomized"
        record["experiment"]["assignment_probability"] = None
        record["experiment"]["randomization_block_id"] = None
        record["conditions"]["lead_time_days"] = 50 if high_demand else 5
        record["offer"]["exposure_status"] = "shown"
        record["offer"]["exposed"] = True
        record["offer"]["exposed_at"] = record["unit"]["offer_time"]
        record["offer"]["discount"] = (
            {"kind": "percentage", "value": 10, "unit": "percent"}
            if treated
            else {"kind": "none", "value": None, "unit": "not_applicable"}
        )
        record["offer"]["price"] = {
            "amount": 180_000 if treated else 200_000,
            "currency": "KRW",
            "unit": "per_night",
            "tax_included": True,
        }
        record["offer"]["promotion"] = None
        record["outcome"]["booking_status"] = (
            "booked" if booked else "not_booked"
        )
        record["outcome"]["booked_at"] = (
            record["unit"]["offer_time"] if booked else None
        )
        record["outcome"]["booking_id"] = (
            f"synthetic-booking-{index:06d}" if booked else None
        )
        record["outcome"]["cancel_status"] = (
            "kept" if booked else "not_applicable"
        )
        revenue = 360_000 if booked and treated else 400_000 if booked else 0
        for field in ("room_revenue", "total_revenue"):
            record["outcome"][field]["amount"] = revenue
        record["outcome"]["contribution_margin"]["amount"] = revenue * 0.7
        record["provenance"]["data_origin"] = "synthetic_fixture"
        record["provenance"]["source_system"] = "WISE_SYNTHETIC"
        record["provenance"]["identifier_protection"]["method"] = "synthetic"
        record["provenance"]["identifier_protection"]["identity_mapping_access"] = "none"
        record["provenance"]["identifier_protection"]["key_version"] = None
        record["provenance"]["identifier_protection"]["transformation_ref"] = (
            "WISE:Synthetic:ConfoundedFixture:v1"
        )
    return records


class ObservationalAnalysisTests(unittest.TestCase):
    def test_aipw_adjusts_confounding_and_reports_diagnostics(self):
        records = confounded_synthetic_observational_records()
        result = estimate_observational_effect(records, metric="booking", seed=19)

        raw_difference = result["diagnostics"]["unadjusted_difference"]
        estimate = result["estimate"]
        self.assertTrue(result["synthetic_only"])
        self.assertAlmostEqual(estimate, 0.15, delta=0.07)
        self.assertGreater(raw_difference, estimate + 0.15)
        self.assertLess(
            result["diagnostics"]["maximum_absolute_smd_after_ipw"],
            result["diagnostics"]["maximum_absolute_smd_before_weighting"],
        )
        self.assertTrue(
            result["diagnostics"]["overlap_adequate_at_0_05_threshold"]
        )
        self.assertNotIn(
            "events",
            result["diagnostics"]["missing_pre_treatment_covariate_counts"],
        )
        self.assertEqual(result["sample_counts"]["analyzed"], len(records))
        self.assertIn(
            "no unmeasured confounding after conditioning on the recorded pre-treatment covariates",
            result["identification_assumptions"],
        )

    def test_non_exposed_records_are_reported_as_excluded_from_exposure_estimand(self):
        records = confounded_synthetic_observational_records()
        for record in records[:20]:
            record["offer"]["exposure_status"] = "eligible_not_shown"
            record["offer"]["exposed"] = False
            record["offer"]["exposed_at"] = None
            record["offer"]["price"] = None
            record["offer"]["discount"] = None
            record["offer"]["promotion"] = None

        result = estimate_observational_effect(records, metric="booking")
        self.assertEqual(result["sample_counts"]["excluded"]["not_shown"], 20)
        self.assertEqual(result["sample_counts"]["analyzed"], len(records) - 20)
        self.assertIn(
            "some records were excluded because treatment or outcome could not be classified",
            result["diagnostics"]["cautions"],
        )

    def test_refuses_randomized_or_cluster_assignment_records(self):
        records = confounded_synthetic_observational_records()
        randomized = copy.deepcopy(records)
        for record in randomized:
            record["experiment"]["design"] = "synthetic_randomized"
            record["experiment"]["assigned_arm"] = "control"
            record["experiment"]["assignment_probability"] = 0.5
            record["provenance"]["data_origin"] = "synthetic_fixture"
        with self.assertRaisesRegex(ValueError, "requires observational"):
            estimate_observational_effect(randomized)

        clustered = copy.deepcopy(records)
        for record in clustered:
            record["experiment"]["assignment_level"] = "stay_date_cluster"
        with self.assertRaisesRegex(ValueError, "customer-level"):
            estimate_observational_effect(clustered)

    def test_rejects_insufficient_treatment_overlap(self):
        records = confounded_synthetic_observational_records()
        for index, record in enumerate(records):
            treated = index < 25
            record["conditions"]["lead_time_days"] = 50 if treated else 5
            record["offer"]["discount"] = (
                {"kind": "percentage", "value": 10, "unit": "percent"}
                if treated
                else {"kind": "none", "value": None, "unit": "not_applicable"}
            )
        result = estimate_observational_effect(records, metric="booking")
        self.assertFalse(
            result["diagnostics"]["overlap_adequate_at_0_05_threshold"]
        )
        self.assertIn("caution", result["diagnostics"]["status"])
        self.assertTrue(result["diagnostics"]["cautions"])


if __name__ == "__main__":
    unittest.main()
