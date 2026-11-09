import unittest

from wise_sim.gmm_personas import (
    fit_gmm_personas,
    generate_mock_pms_data,
    generate_personas,
)


class GMMPersonaMockupTests(unittest.TestCase):
    def test_mock_data_fits_segments_and_generates_l1_personas(self):
        records = generate_mock_pms_data(n=2_000, seed=17)
        model = fit_gmm_personas(records, n_components=4, seed=17)
        personas = generate_personas(model, n=500, seed=17)

        self.assertEqual(len(personas), 500)
        self.assertEqual(len(model.segment_fits), 4)
        self.assertTrue(all(fit.sample_count > 10 for fit in model.segment_fits.values()))
        self.assertTrue(all(
            fit.price_log_odds_coefficient < 0
            for fit in model.segment_fits.values()
        ))
        self.assertTrue(all(
            0.0 <= fit.price_sensitivity_proxy <= 1.0
            for fit in model.segment_fits.values()
        ))
        self.assertGreater(
            len({
                round(fit.price_sensitivity_proxy, 2)
                for fit in model.segment_fits.values()
            }),
            1,
        )
        self.assertTrue(all(
            persona.archetype.startswith("GMM_")
            and persona.lead_time_days >= 0
            and persona.avg_room_rate >= 0
            for persona in personas
        ))
        self.assertEqual(
            personas,
            generate_personas(model, n=500, seed=17),
        )

    def test_price_exposure_is_needed_for_logistic_estimation(self):
        with self.assertRaisesRegex(ValueError, "price_delta"):
            fit_gmm_personas([{
                "booking_count": 1,
                "stay_nights": 1,
                "lead_time_days": 1,
                "avg_room_rate": 300_000,
                "promo": "none",
                "weekend": 0,
                "booked": 1,
            }])


if __name__ == "__main__":
    unittest.main()
