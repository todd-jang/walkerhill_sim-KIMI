"""Unit tests for wise_sim.web_views (가격 패널 로더 + 판정 계산기)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from wise_sim.l3_verdict import full_report
from wise_sim.web_views import (
    PanelNotFound,
    ViewDataError,
    compute_verdict,
    load_price_panel,
)

VALID_REQUEST = {
    "direction": 0.75,
    "rho": 0.64,
    "abstain": 0.12,
    "n_weekends": 8,
    "n_hits": 6,
    "n_exceed": 0,
}


class LoadPricePanelTest(unittest.TestCase):
    def test_real_artifact_loads(self):
        payload = load_price_panel()
        self.assertIsInstance(payload, dict)
        self.assertGreater(payload.get("n_series", 0), 0)
        self.assertIn("series", payload)
        self.assertIn("notes", payload)

    def test_missing_file_raises_panel_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PanelNotFound):
                load_price_panel(Path(tmp) / "없는_산출물.json")

    def test_invalid_json_raises_view_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "broken.json"
            target.write_text("not json", encoding="utf-8")
            with self.assertRaises(ViewDataError):
                load_price_panel(target)

    def test_non_object_json_raises_view_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "array.json"
            target.write_text("[]", encoding="utf-8")
            with self.assertRaises(ViewDataError):
                load_price_panel(target)


class ComputeVerdictTest(unittest.TestCase):
    def test_matches_full_report_direct_call(self):
        result = compute_verdict(VALID_REQUEST)
        expected = full_report(0.75, 0.64, 0.12, {"n_exceed": 0}, 8, 6)
        self.assertEqual(result, expected)

    def test_fit_verdict_when_direction_above_threshold(self):
        self.assertEqual(compute_verdict(VALID_REQUEST)["판정"], "적합")

    def test_unfit_verdict_when_direction_below_threshold(self):
        result = compute_verdict({**VALID_REQUEST, "direction": 0.55})
        self.assertEqual(result["판정"], "부적합")

    def test_conditional_verdict_with_exceed(self):
        result = compute_verdict({**VALID_REQUEST, "n_exceed": 1})
        self.assertEqual(result["판정"], "조건부")

    def test_optional_induction_adds_metric(self):
        without = compute_verdict(VALID_REQUEST)
        self.assertNotIn("유도저항", without["보고 지표"])
        with_induction = compute_verdict({**VALID_REQUEST, "induction_resist": 0.9})
        self.assertIn("유도저항", with_induction["보고 지표"])

    def test_wilson_ci_present(self):
        backtest = compute_verdict(VALID_REQUEST)["백테스트"]
        self.assertEqual(backtest["n"], 8)
        self.assertEqual(len(backtest["Wilson 95% CI"]), 2)


class ComputeVerdictValidationTest(unittest.TestCase):
    def test_rejects_bool_as_number(self):
        with self.assertRaises(ViewDataError):
            compute_verdict({**VALID_REQUEST, "direction": True})

    def test_rejects_missing_field(self):
        for key in ("direction", "rho", "abstain", "n_weekends", "n_hits"):
            payload = {k: v for k, v in VALID_REQUEST.items() if k != key}
            with self.assertRaises(ViewDataError, msg=key):
                compute_verdict(payload)

    def test_rejects_hits_greater_than_n(self):
        with self.assertRaises(ViewDataError):
            compute_verdict({**VALID_REQUEST, "n_hits": 9})

    def test_rejects_non_integer_counts(self):
        with self.assertRaises(ViewDataError):
            compute_verdict({**VALID_REQUEST, "n_weekends": 8.5})

    def test_rejects_out_of_range_direction(self):
        with self.assertRaises(ViewDataError):
            compute_verdict({**VALID_REQUEST, "direction": 1.2})

    def test_rejects_non_mapping_payload(self):
        with self.assertRaises(ViewDataError):
            compute_verdict(["not", "a", "dict"])

    def test_rejects_induction_out_of_range(self):
        with self.assertRaises(ViewDataError):
            compute_verdict({**VALID_REQUEST, "induction_resist": 1.5})


class HandlerWiringTest(unittest.TestCase):
    """엔드포인트가 web_app에 실제 연결됐는지 소스 수준 확인 (세션·보안 흐름 재사용)."""

    def test_routes_registered(self):
        source = Path("wise_sim/web_app.py").read_text(encoding="utf-8")
        self.assertIn('path == "/api/price-panel"', source)
        self.assertIn('path == "/api/verdict"', source)
        self.assertIn("load_price_panel", source)
        self.assertIn("compute_verdict", source)

    def test_verdict_payload_serializes_korean_keys(self):
        report = compute_verdict(VALID_REQUEST)
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertIn("판정", encoded)


if __name__ == "__main__":
    unittest.main()
