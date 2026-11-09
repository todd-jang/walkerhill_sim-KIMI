import unittest

from wise_sim.personas import (
    fit_calibration,
    generate_population,
)


class PersonaCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "population_id": "family",
                "price_sensitivity": 0.4,
                "booking_count": 2,
                "stay_nights": 2,
                "view_preference": "river",
                "floor_preference": "high",
                "dining_preference": "breakfast",
            },
            {
                "population_id": "family",
                "price_sensitivity": 0.8,
                "booking_count": 6,
                "stay_nights": 4,
                "view_preference": "city",
                "floor_preference": "high",
                "dining_preference": "breakfast",
            },
            {
                "population_id": "business",
                "price_sensitivity": 0.2,
                "booking_count": 10,
                "stay_nights": 1,
                "view_preference": "city",
                "floor_preference": "low",
                "dining_preference": "none",
            },
        ]

    def test_fit_calibration_and_generate_deterministic_profiles(self):
        calibration = fit_calibration(self.records)
        family = calibration.populations["family"]
        self.assertEqual(calibration.sample_count, 3)
        self.assertAlmostEqual(family.share, 2 / 3)
        self.assertAlmostEqual(family.numeric["price_sensitivity"].mean, 0.6)
        self.assertAlmostEqual(
            family.numeric["price_sensitivity"].stddev, 0.2828427124
        )
        self.assertEqual(family.categorical["floor_preference"], {"high": 1.0})

        first = generate_population(100, seed=7, calibration=calibration)
        second = generate_population(100, seed=7, calibration=calibration)
        self.assertEqual(first, second)
        self.assertTrue(all(p.population_id in {"family", "business"} for p in first))
        family_persona = next(p for p in first if p.population_id == "family")
        self.assertEqual(family_persona.floor_preference, "high")
        self.assertIn(family_persona.view_preference, {"river", "city"})
        self.assertIn(family_persona.dining_preference, {"breakfast"})
        self.assertGreaterEqual(family_persona.booking_count, 0)

    def test_empty_input_and_invalid_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            fit_calibration([])
        with self.assertRaisesRegex(ValueError, "must be numeric"):
            fit_calibration([{"population_id": "family", "booking_count": "many"}])
        with self.assertRaisesRegex(ValueError, "must contain"):
            fit_calibration([{"booking_count": 1}])

    def test_legacy_generation_and_empty_population(self):
        population = generate_population(100, seed=3)
        self.assertEqual(len(population), 100)
        self.assertTrue(all(p.view_preference == "unknown" for p in population))
        self.assertEqual(generate_population(0), [])


if __name__ == "__main__":
    unittest.main()
