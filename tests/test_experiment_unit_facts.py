import unittest

from wise_sim.experiment_pipeline import (
    build_experiment_unit_facts,
    estimate_fact_itt,
    summarize_operations_per_stay,
    summarize_survey_responders,
)


CUTOFF = "2026-03-01T00:00:00+00:00"


def build_fixture(
    *,
    cutoff=CUTOFF,
    outcome_matures_at="2026-02-01T00:00:00+00:00",
    cancellations=None,
    refunds=None,
):
    assignments = []
    for index, arm in enumerate(("control", "treatment", "control", "treatment")):
        assignments.append({
            "experiment_id": "christmas-family-benefit-v1",
            "design": "randomized",
            "data_origin": "randomized_experiment",
            "assignment_id": f"a{index + 1}",
            "unit_id": f"u{index + 1}",
            "segment": "family",
            "assigned_variant": arm,
            "assignment_probability": 0.5,
            "assigned_at": f"2026-01-01T00:0{index}:00+00:00",
            "outcome_matures_at": outcome_matures_at,
            "protocol_id": "pilot-protocol-v1",
            "eligibility_version": "eligibility-v1",
            "assignment_logic_version": "assignment-v1",
            "randomization_block_id": f"block-{index // 2}",
        })
    return build_experiment_unit_facts(
        assignments,
        exposures=[
            {
                "event_id": "e1",
                "assignment_id": "a1",
                "exposed_at": "2026-01-01T01:00:00+00:00",
                "rendered_variant": "control",
                "page": "family-package",
                "channel": "web",
                "price": {"amount": 200000, "currency": "KRW"},
                "inventory_snapshot": {"remaining_rooms": 4},
            },
            {
                "event_id": "e4",
                "assignment_id": "a4",
                "exposed_at": "2026-01-01T01:00:00+00:00",
                "rendered_variant": "control",
            },
        ],
        bookings=[
            {
                "booking_id": "b1",
                "assignment_id": "a1",
                "booked_at": "2026-01-02T00:00:00+00:00",
            },
            {
                "booking_id": "b4",
                "assignment_id": "a4",
                "booked_at": "2026-01-02T00:00:00+00:00",
            },
            {
                "booking_id": "b-old",
                "assignment_id": "a1",
                "booked_at": "2026-01-02T00:00:00+00:00",
            },
            {
                "booking_id": "b-unlinked",
                "assignment_id": None,
                "booked_at": "2026-01-02T00:00:00+00:00",
            },
        ],
        payments=[
            {
                "payment_id": "p1",
                "booking_id": "b1",
                "payment_status": "succeeded",
                "paid_at": "2026-01-02T00:00:00+00:00",
                "amount": 60,
                "currency": "KRW",
            },
            {
                "payment_id": "p1b",
                "booking_id": "b1",
                "payment_status": "succeeded",
                "paid_at": "2026-01-03T00:00:00+00:00",
                "amount": 40,
                "currency": "KRW",
            },
            {
                "payment_id": "p4",
                "booking_id": "b4",
                "payment_status": "succeeded",
                "paid_at": "2026-01-02T00:00:00+00:00",
                "amount": 200,
                "currency": "KRW",
            },
            {
                "payment_id": "p-old",
                "booking_id": "b-old",
                "payment_status": "succeeded",
                "paid_at": "2026-01-20T00:00:00+00:00",
                "amount": 50,
                "currency": "KRW",
            },
            {
                "payment_id": "p-unlinked",
                "booking_id": "b-unlinked",
                "payment_status": "succeeded",
                "paid_at": "2026-01-02T00:00:00+00:00",
                "amount": 30,
                "currency": "KRW",
            },
        ],
        cancellations=cancellations if cancellations is not None else [{
                "event_id": "cancel-b4",
                "booking_id": "b4",
                "cancelled_at": "2026-01-10T00:00:00+00:00",
                "status": "cancelled",
                "fee_amount": 50,
                "currency": "KRW",
            }],
        refunds=refunds if refunds is not None else [
            {
                "event_id": "refund-b1",
                "refund_id": "r1",
                "booking_id": "b1",
                "payment_id": "p1",
                "event_at": "2026-01-12T00:00:00+00:00",
                "status": "completed",
                "amount": 20,
                "currency": "KRW",
            },
            {
                "event_id": "refund-b4",
                "refund_id": "r4",
                "booking_id": "b4",
                "payment_id": "p4",
                "event_at": "2026-01-12T00:00:00+00:00",
                "status": "completed",
                "amount": 150,
                "currency": "KRW",
            },
        ],
        costs=[
            {
                "event_id": "cost-b1",
                "booking_id": "b1",
                "incurred_at": "2026-02-01T00:00:00+00:00",
                "amount": 10,
                "currency": "KRW",
            },
            {
                "event_id": "cost-b4",
                "booking_id": "b4",
                "incurred_at": "2026-02-01T00:00:00+00:00",
                "amount": 20,
                "currency": "KRW",
            },
        ],
        stays=[
            {
                "event_id": "stay-b1",
                "booking_id": "b1",
                "checked_in_at": "2026-02-10T00:00:00+00:00",
                "checked_out_at": "2026-02-12T00:00:00+00:00",
            }
        ],
        operations=[
            {
                "event_id": "op-a1",
                "assignment_id": "a1",
                "occurred_at": "2026-02-02T00:00:00+00:00",
                "event_type": "complaint",
            },
            {
                "event_id": "op-a1-safety",
                "assignment_id": "a1",
                "occurred_at": "2026-02-02T00:00:00+00:00",
                "event_type": "safety_incident",
                "severity": "minor",
            },
        ],
        surveys=[
            {
                "survey_id": "survey-a1",
                "assignment_id": "a1",
                "invited_at": "2026-02-05T00:00:00+00:00",
                "responded_at": "2026-02-06T00:00:00+00:00",
                "satisfaction_score": 4,
                "recommendation_score": 9,
            },
            {
                "survey_id": "survey-a4",
                "assignment_id": "a4",
                "invited_at": "2026-02-05T00:00:00+00:00",
                "responded_at": None,
                "satisfaction_score": None,
                "recommendation_score": None,
            },
        ],
        analysis_cutoff_at=cutoff,
        source_complete_through=cutoff,
        attribution_window_days=7,
        data_version="pilot-export-2026-03-01-v1",
    )


class ExperimentUnitFactTests(unittest.TestCase):
    def test_build_keeps_all_assignments_and_aggregates_ledgers_once(self):
        result = build_fixture()
        facts = {fact["assignment_id"]: fact for fact in result["facts"]}

        self.assertEqual(len(facts), 4)
        self.assertEqual(facts["a2"]["paid_booking_flag"], 0)
        self.assertEqual(facts["a2"]["exposed_flag"], False)
        self.assertEqual(facts["a1"]["net_revenue"], 80)
        self.assertEqual(facts["a1"]["gross_payment"], 100)
        self.assertEqual(facts["a1"]["booking_count"], 1)
        self.assertEqual(facts["a1"]["contribution_margin"], 70)
        self.assertEqual(facts["a4"]["fully_cancelled_flag"], 1)
        self.assertEqual(facts["a4"]["contribution_margin"], 30)
        self.assertEqual(facts["a4"]["cancellation_fee_amount"], 50)
        self.assertEqual(facts["a4"]["exposure_variant_mismatch_count"], 1)
        self.assertEqual(facts["a1"]["first_exposure_page"], "family-package")
        self.assertEqual(
            facts["a1"]["first_exposure_inventory_snapshot"]["remaining_rooms"], 4
        )
        self.assertEqual(facts["a1"]["stay_count"], 1)
        self.assertEqual(facts["a1"]["safety_incidents_by_severity"]["minor"], 1)
        self.assertEqual(result["quality"]["unattributed_booking_count"], 1)
        self.assertEqual(result["quality"]["out_of_window_booking_count"], 1)
        self.assertTrue(result["quality"]["refunds_reconciled_to_payments"])
        self.assertEqual(
            result["quality"]["financial_reconciliation_deltas_by_currency"]["KRW"][
                "gross_payment"
            ],
            80,
        )
        self.assertIn("sample_ratio_mismatch_by_segment", result["quality"])

    def test_itt_uses_assignment_arm_and_retains_non_exposed_non_buyers(self):
        facts = build_fixture()["facts"]
        effect = estimate_fact_itt(facts, "net_revenue", segment="family")

        self.assertEqual(effect["assignment_count"], {"control": 2, "treatment": 2})
        self.assertEqual(effect["non_exposed_assignments"], 2)
        self.assertEqual(effect["non_purchasing_assignments"], 2)
        self.assertFalse(effect["exposure_filter_applied"])
        self.assertEqual(effect["control_mean"], 40)
        self.assertEqual(effect["treatment_mean"], 25)
        self.assertEqual(effect["itt_estimate"], -15)

    def test_itt_requires_segment_specific_analysis(self):
        facts = build_fixture()["facts"]
        facts[0]["segment"] = "adult"
        with self.assertRaisesRegex(ValueError, "each segment separately"):
            estimate_fact_itt(facts, "paid_booking")

    def test_operational_per_stay_rates_are_labeled_non_itt(self):
        facts = build_fixture()["facts"]
        result = summarize_operations_per_stay(facts, segment="family")
        self.assertEqual(result["completed_stays"], 1)
        self.assertEqual(result["complaints_per_100_completed_stays"], 100)
        self.assertIn("not ITT", result["interpretation"])

    def test_unmatured_final_outcomes_are_not_analyzed(self):
        facts = build_fixture(
            cutoff="2026-01-15T00:00:00+00:00",
            outcome_matures_at="2026-02-01T00:00:00+00:00",
        )["facts"]
        with self.assertRaisesRegex(ValueError, "complete outcomes"):
            estimate_fact_itt(facts, "contribution_margin")

    def test_booking_cancellation_status_history_uses_latest_state(self):
        facts = build_fixture(cancellations=[
            {
                "event_id": "cancel-b4",
                "booking_id": "b4",
                "cancelled_at": "2026-01-10T00:00:00+00:00",
                "status": "cancelled",
            },
            {
                "event_id": "reinstate-b4",
                "booking_id": "b4",
                "cancelled_at": "2026-01-11T00:00:00+00:00",
                "status": "reinstated",
            },
        ])["facts"]
        fact = next(row for row in facts if row["assignment_id"] == "a4")
        self.assertEqual(fact["net_booking_flag"], 1)
        self.assertEqual(fact["fully_cancelled_flag"], 0)
        self.assertEqual(fact["cancellation_event_count"], 2)

    def test_refund_status_history_uses_completed_state_at_cutoff(self):
        facts = build_fixture(refunds=[
            {
                "event_id": "refund-b1-pending",
                "refund_id": "r1",
                "booking_id": "b1",
                "payment_id": "p1",
                "event_at": "2026-01-11T00:00:00+00:00",
                "status": "pending",
                "amount": 20,
                "currency": "KRW",
            },
            {
                "event_id": "refund-b1-done",
                "refund_id": "r1",
                "booking_id": "b1",
                "payment_id": "p1",
                "event_at": "2026-01-12T00:00:00+00:00",
                "status": "completed",
                "amount": 20,
                "currency": "KRW",
            },
            {
                "event_id": "refund-b1-reversed",
                "refund_id": "r1",
                "booking_id": "b1",
                "payment_id": "p1",
                "event_at": "2026-01-13T00:00:00+00:00",
                "status": "reversed",
                "amount": 20,
                "currency": "KRW",
            },
            {
                "event_id": "refund-b4",
                "refund_id": "r4",
                "booking_id": "b4",
                "payment_id": "p4",
                "event_at": "2026-01-12T00:00:00+00:00",
                "status": "completed",
                "amount": 200,
                "currency": "KRW",
            },
        ])
        a1 = next(fact for fact in facts["facts"] if fact["assignment_id"] == "a1")
        self.assertEqual(a1["refund_amount"], 0)
        self.assertEqual(a1["net_revenue"], 100)
        self.assertEqual(a1["refund_status_event_count"], 3)

    def test_survey_scores_are_descriptive_and_nonresponse_is_not_zero_score(self):
        result = summarize_survey_responders(build_fixture()["facts"], segment="family")
        self.assertEqual(result["responded_count"], 1)
        self.assertEqual(result["response_rate_per_assignment"], 0.25)
        self.assertEqual(result["satisfaction_score_respondent_mean"], 4)
        self.assertEqual(result["satisfaction_score_respondent_count"], 1)
        self.assertIn("not ITT", result["interpretation"])

    def test_rejects_refunds_above_payment(self):
        result = build_fixture()
        self.assertEqual(len(result["facts"]), 4)
        assignments = [{
            "experiment_id": "x",
            "design": "randomized",
            "data_origin": "randomized_experiment",
            "assignment_id": "a",
            "unit_id": "u",
            "segment": "family",
            "assigned_variant": "control",
            "assignment_probability": 0.5,
            "assigned_at": "2026-01-01T00:00:00+00:00",
            "outcome_matures_at": "2026-02-01T00:00:00+00:00",
            "protocol_id": "pilot-protocol-v1",
            "eligibility_version": "eligibility-v1",
            "assignment_logic_version": "assignment-v1",
        }, {
            "experiment_id": "x",
            "design": "randomized",
            "data_origin": "randomized_experiment",
            "assignment_id": "b",
            "unit_id": "v",
            "segment": "family",
            "assigned_variant": "treatment",
            "assignment_probability": 0.5,
            "assigned_at": "2026-01-01T00:00:00+00:00",
            "outcome_matures_at": "2026-02-01T00:00:00+00:00",
            "protocol_id": "pilot-protocol-v1",
            "eligibility_version": "eligibility-v1",
            "assignment_logic_version": "assignment-v1",
        }]
        with self.assertRaisesRegex(ValueError, "refunds exceed payment"):
            build_experiment_unit_facts(
                assignments,
                exposures=[],
                bookings=[{
                    "booking_id": "b1",
                    "assignment_id": "a",
                    "booked_at": "2026-01-02T00:00:00+00:00",
                }],
                payments=[{
                    "payment_id": "p1",
                    "booking_id": "b1",
                    "payment_status": "succeeded",
                    "paid_at": "2026-01-02T00:00:00+00:00",
                    "amount": 100,
                    "currency": "KRW",
                }],
                cancellations=[],
                refunds=[{
                    "event_id": "refund-b1",
                    "refund_id": "r1",
                    "booking_id": "b1",
                    "payment_id": "p1",
                    "event_at": "2026-01-03T00:00:00+00:00",
                    "status": "completed",
                    "amount": 101,
                    "currency": "KRW",
                }],
                costs=[],
                stays=[],
                operations=[],
                surveys=[],
                analysis_cutoff_at=CUTOFF,
                source_complete_through=CUTOFF,
                attribution_window_days=7,
                data_version="v1",
            )


if __name__ == "__main__":
    unittest.main()
