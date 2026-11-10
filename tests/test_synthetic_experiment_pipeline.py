import copy
import unittest

from wise_sim.experiment_pipeline import (
    estimate_itt,
    generate_synthetic_records,
    map_ontology_concept,
    validate_experiment_records,
)


class SyntheticExperimentPipelineTests(unittest.TestCase):
    def test_synthetic_records_validate_map_and_produce_known_itt(self):
        records = generate_synthetic_records(n=200, seed=73)
        validate_experiment_records(records)

        self.assertEqual(len(records), 200)
        self.assertTrue(all(
            record["provenance"]["data_origin"] == "synthetic_fixture"
            for record in records
        ))
        self.assertTrue(all(
            record["experiment"]["design"] == "synthetic_randomized"
            for record in records
        ))
        self.assertTrue(all(
            record["offer"]["building"]["canonical_id"] == "WISE:Building:GRAND"
            for record in records
        ))
        self.assertTrue(all(
            record["offer"]["room_type"]["mapping_status"] == "unmapped"
            and record["offer"]["room_type"]["canonical_id"] is None
            for record in records
        ))

        arm_sizes = {
            arm: sum(record["experiment"]["assigned_arm"] == arm for record in records)
            for arm in ("control", "treatment")
        }
        self.assertEqual(arm_sizes, {"control": 100, "treatment": 100})
        self.assertEqual(
            sum(
                record["experiment"]["assigned_arm"] == "treatment"
                and not record["offer"]["exposed"]
                for record in records
            ),
            10,
        )

        booking_itt = estimate_itt(records, "booking")
        revenue_itt = estimate_itt(records, "total_revenue")
        self.assertAlmostEqual(booking_itt["itt_estimate"], 0.09)
        self.assertAlmostEqual(revenue_itt["itt_estimate"], 21_600.0)
        self.assertEqual(booking_itt["exposed_units_per_arm"], {
            "control": 100,
            "treatment": 90,
        })
        self.assertFalse(booking_itt["exposure_filter_applied"])
        self.assertTrue(booking_itt["synthetic_only"])
        self.assertEqual(booking_itt["randomization_blocks"], 100)
        self.assertEqual(
            booking_itt["interval_method"],
            "paired-block normal approximation",
        )
        self.assertLess(
            booking_itt["confidence_interval"][0],
            booking_itt["itt_estimate"],
        )
        self.assertGreater(
            booking_itt["confidence_interval"][1],
            booking_itt["itt_estimate"],
        )

    def test_unknown_ontology_terms_remain_unmapped(self):
        binding = map_ontology_concept(
            "room_type", "test_pms", "UNKNOWN-SUITE", "Unknown suite"
        )
        self.assertEqual(binding["mapping_status"], "unmapped")
        self.assertIsNone(binding["canonical_id"])
        self.assertEqual(binding["source_code"], "UNKNOWN-SUITE")

    def test_rejects_duplicate_or_mismatched_unit_records(self):
        records = generate_synthetic_records(n=200, seed=73)
        mismatched = copy.deepcopy(records)
        mismatched[0]["experiment"]["assignment_unit_id"] = "different-unit"
        with self.assertRaisesRegex(ValueError, "assignment unit and outcome unit"):
            validate_experiment_records(mismatched)

        duplicate = copy.deepcopy(records)
        duplicate[1]["unit"]["unit_id"] = duplicate[0]["unit"]["unit_id"]
        duplicate[1]["experiment"]["assignment_unit_id"] = duplicate[0]["unit"]["unit_id"]
        with self.assertRaisesRegex(ValueError, "duplicate experimental unit"):
            validate_experiment_records(duplicate)

    def test_itt_rejects_incomplete_outcomes_and_invalid_designs(self):
        incomplete = generate_synthetic_records(n=200, seed=73)
        incomplete[0]["outcome"]["status"] = "pending"
        with self.assertRaises(ValueError):
            estimate_itt(incomplete, "booking")

        not_randomized = generate_synthetic_records(n=200, seed=73)
        for record in not_randomized:
            record["experiment"]["design"] = "observational"
            record["experiment"]["assigned_arm"] = "not_randomized"
            record["experiment"]["assignment_probability"] = None
            record["provenance"]["data_origin"] = "observational_extract"
        with self.assertRaisesRegex(ValueError, "ITT requires randomized"):
            estimate_itt(not_randomized, "booking")

    def test_generator_requires_sample_size_for_known_truth(self):
        with self.assertRaisesRegex(ValueError, "multiple of 200"):
            generate_synthetic_records(n=100)

    def test_generator_is_reproducible(self):
        first = generate_synthetic_records(n=200, seed=73)
        second = generate_synthetic_records(n=200, seed=73)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
