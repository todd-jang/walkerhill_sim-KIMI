import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wise_sim.package_pilot import (
    build_hillink_dummy_eligibility,
    build_assignment_plan,
    calculate_sample_size,
    synthetic_dry_run,
)


class PackagePilotTests(unittest.TestCase):
    def test_dry_run_randomizes_within_each_segment_without_generating_outcomes(self):
        result = synthetic_dry_run(customers_per_segment=101, seed=19)

        self.assertEqual(result["status"], "assignment_dry_run_only")
        self.assertEqual(result["data_origin"], "synthetic_dry_run")
        self.assertEqual(result["assigned_total"], 202)
        self.assertEqual(result["arm_counts"]["family"], {
            "control": 50,
            "treatment": 51,
        })
        self.assertEqual(result["arm_counts"]["adult"], {
            "control": 50,
            "treatment": 51,
        })
        self.assertFalse(result["outcomes_generated"])
        self.assertFalse(result["effect_estimated"])
        family = next(
            assignment for assignment in result["assignments"]
            if assignment["segment"] == "family"
        )
        self.assertEqual(
            family["control_offer"],
            "Memorable Familycation base package",
        )
        self.assertEqual(
            family["treatment_offer"],
            "Same base package plus kids activity kit",
        )

    def test_randomization_balances_pairs_within_segment_and_strata(self):
        participants = [
            {
                "customer_id": f"{segment}-{channel}-{index}",
                "segment": segment,
                "channel": channel,
            }
            for segment in ("family", "adult")
            for channel in ("web", "app")
            for index in range(5)
        ]
        assignments = build_assignment_plan(
            participants,
            seed=4,
            strata_fields=("channel",),
        )
        for segment in ("family", "adult"):
            for channel in ("web", "app"):
                arms = [
                    record["assigned_arm"]
                    for record in assignments
                    if record["segment"] == segment
                    and record["stratum"]["channel"] == channel
                ]
                self.assertEqual(abs(arms.count("control") - arms.count("treatment")), 1)

    def test_duplicate_customers_and_unregistered_segments_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate customer_id"):
            build_assignment_plan([
                {"customer_id": "same", "segment": "family"},
                {"customer_id": "same", "segment": "family"},
            ])
        with self.assertRaisesRegex(ValueError, "unsupported segment"):
            build_assignment_plan([{"customer_id": "x", "segment": "unknown"}])

    def test_sample_size_rejects_impossible_conversion_values(self):
        with self.assertRaisesRegex(ValueError, "must be below 1"):
            calculate_sample_size(0.9, 0.2)
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            calculate_sample_size(1.0, 0.05)

    def test_sample_size_applies_two_test_familywise_alpha(self):
        plan = calculate_sample_size(
            baseline_conversion=0.2,
            absolute_mde=0.05,
        )
        self.assertEqual(plan.alpha_per_test, 0.025)
        self.assertEqual(plan.customers_per_arm_per_segment, 1325)
        self.assertEqual(plan.total_customers, 5300)
        self.assertAlmostEqual(plan.target_conversion, 0.25)

    def test_assignment_cli_writes_pseudonymous_assignment_rows(self):
        module_path = Path(__file__).resolve().parents[1] / "wise_sim" / "package_pilot.py"
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "eligible.csv"
            output_path = Path(temporary_directory) / "assigned.csv"
            with input_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["customer_id", "segment", "channel"])
                writer.writeheader()
                for segment in ("family", "adult"):
                    for index in range(4):
                        writer.writerow({
                            "customer_id": f"pseudonym-{segment}-{index}",
                            "segment": segment,
                            "channel": "web",
                        })
            result = subprocess.run(
                [
                    sys.executable,
                    str(module_path),
                    "assign",
                    str(input_path),
                    str(output_path),
                    "--stratify-by",
                    "channel",
                    "--seed",
                    "3",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            status = json.loads(result.stdout)
            with output_path.open(encoding="utf-8", newline="") as stream:
                assignments = list(csv.DictReader(stream))

        self.assertEqual(status["status"], "assignment_plan_created")
        self.assertEqual(status["assigned_customers"], 8)
        self.assertEqual(len(assignments), 8)
        self.assertEqual(
            {assignment["experiment_id"] for assignment in assignments},
            {
                "WISE:Christmas:FamilyKit:v1",
                "WISE:Christmas:AdultLoungeDrink:v1",
            },
        )
        self.assertTrue(all(
            assignment["data_origin"] == "assignment_plan"
            for assignment in assignments
        ))

    def test_hillink_templates_make_reproducible_synthetic_eligibility_rows(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "hillink" / "timeline" / "persona_grid.csv"
            source.parent.mkdir(parents=True)
            source.write_text(
                "성별,연령,목적,비중,인원수,아동동반,박수,근거\n"
                "여성,30~39세,가족·친족,0.5,3:1.0,1,1:1.0,가정 기반 템플릿\n"
                "남성,40~49세,가족·친족,0.5,4:1.0,1,2:1.0,가정 기반 템플릿\n"
                "여성,30~39세,커플·기념일,0.5,2:1.0,0,1:1.0,가정 기반 템플릿\n"
                "남성,40~49세,혼자 휴식,0.5,1:1.0,0,2:1.0,가정 기반 템플릿\n",
                encoding="utf-8",
            )

            first = build_hillink_dummy_eligibility(
                root, customers_per_segment=8, seed=13
            )
            second = build_hillink_dummy_eligibility(
                root, customers_per_segment=8, seed=13
            )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)
        self.assertEqual(
            {segment: sum(row["segment"] == segment for row in first)
             for segment in ("family", "adult")},
            {"family": 8, "adult": 8},
        )
        self.assertTrue(all(
            row["data_origin"] == "synthetic_dry_run"
            and row["eligibility_status"] == "synthetic_only_not_real_customer"
            and row["customer_id"].startswith("synthetic-hillink-")
            for row in first
        ))
        self.assertTrue(all(
            row["child_count_assumption"] > 0
            for row in first if row["segment"] == "family"
        ))
        self.assertTrue(all(
            row["child_count_assumption"] == 0
            for row in first if row["segment"] == "adult"
        ))
        self.assertTrue(all(
            row["stay_nights_assumption"] > 0
            for row in first
        ))

    def test_assignment_cli_preserves_synthetic_origin(self):
        module_path = Path(__file__).resolve().parents[1] / "wise_sim" / "package_pilot.py"
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "eligible.csv"
            output_path = Path(temporary_directory) / "assigned.csv"
            with input_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["customer_id", "segment", "data_origin"],
                )
                writer.writeheader()
                for segment in ("family", "adult"):
                    for index in range(2):
                        writer.writerow({
                            "customer_id": f"synthetic-{segment}-{index}",
                            "segment": segment,
                            "data_origin": "synthetic_dry_run",
                        })
            result = subprocess.run(
                [
                    sys.executable,
                    str(module_path),
                    "assign",
                    str(input_path),
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        self.assertEqual(json.loads(result.stdout)["data_origin"], "synthetic_dry_run")


if __name__ == "__main__":
    unittest.main()
