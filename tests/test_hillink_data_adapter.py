import csv
import json
import tempfile
import unittest
from pathlib import Path

from wise_sim.hillink_data_adapter import extract_hillink_inputs


class HillinkDataAdapterTests(unittest.TestCase):
    def test_extracts_reference_and_marks_causal_inputs_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            room_path = root / "rooms.csv"
            persona_path = root / "personas.csv"
            evidence_path = root / "evidence.csv"
            self._write_csv(room_path, [
                {
                    "날짜": "2026-10-01", "건물": "그랜드", "객실수": "507",
                    "기준OCC_BASE": "0.79", "상한전요청객실박": "427",
                    "판매객실박": "427", "참고수요계수": "1.06",
                    "공급상한해당": "False",
                    "계수성격": "날짜·목적·행사효과가섞인온톨로지참고계수",
                },
                {
                    "날짜": "2026-10-01", "건물": "비스타", "객실수": "244",
                    "기준OCC_BASE": "0.79", "상한전요청객실박": "205",
                    "판매객실박": "205", "참고수요계수": "1.06",
                    "공급상한해당": "False",
                    "계수성격": "날짜·목적·행사효과가섞인온톨로지참고계수",
                },
                {
                    "날짜": "2026-10-01", "건물": "더글라스", "객실수": "50",
                    "기준OCC_BASE": "0.79", "상한전요청객실박": "43",
                    "판매객실박": "43", "참고수요계수": "1.08",
                    "공급상한해당": "False",
                    "계수성격": "날짜·목적·행사효과가섞인온톨로지참고계수",
                },
            ])
            self._write_csv(persona_path, [{
                "표본ID": "IG01", "게시일": "2026-09-08",
                "방문목적": "가족·친족", "이용시설": "피자힐",
                "연령검증상태": "모름",
            }])
            self._write_csv(evidence_path, [{
                "근거ID": "EC01", "확실성": "△ 추정",
                "반대사례_한계": "단일 시드", "다음확인": "반복 검증",
            }])

            report = extract_hillink_inputs(
                room_path, persona_path, evidence_path
            )

        self.assertEqual(report["ontology_contract"], "WISE_HILLINK_ONTOLOGY_MAP")
        self.assertEqual(report["room_reference"]["source_total_rooms"], 801)
        self.assertTrue(report["room_reference"]["matches_declared_source_total"])
        self.assertEqual(report["room_reference"]["wise_simulation_total_rooms"], 580)
        self.assertFalse(report["room_reference"]["room_count_matches_simulation"])
        self.assertEqual(
            report["room_reference"]["building_room_counts"],
            {"DOUGLAS": 50, "GRAND": 507, "VISTA": 244},
        )
        self.assertFalse(report["model_compatibility"]["can_fit_gmm_personas"])
        self.assertFalse(report["model_compatibility"]["causal_effect_estimable"])
        json.dumps(report, ensure_ascii=False)

    def test_rejects_duplicate_room_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            room_path = root / "rooms.csv"
            room_row = {
                "날짜": "2026-10-01", "건물": "그랜드", "객실수": "507",
                "기준OCC_BASE": "0.79", "상한전요청객실박": "427",
                "판매객실박": "427", "참고수요계수": "1.06",
                "공급상한해당": "False", "계수성격": "reference",
            }
            self._write_csv(room_path, [room_row, room_row])
            self._write_csv(root / "personas.csv", [{
                "표본ID": "IG01", "게시일": "2026-09-08",
                "방문목적": "가족·친족", "이용시설": "피자힐",
                "연령검증상태": "모름",
            }])
            self._write_csv(root / "evidence.csv", [{
                "근거ID": "EC01", "확실성": "△ 추정",
                "반대사례_한계": "한계", "다음확인": "확인",
            }])
            with self.assertRaisesRegex(ValueError, "duplicate date/building"):
                extract_hillink_inputs(
                    room_path, root / "personas.csv", root / "evidence.csv"
                )

    @staticmethod
    def _write_csv(path, rows):
        with open(path, "w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
