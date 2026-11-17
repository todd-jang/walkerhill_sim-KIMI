"""Unit tests for wise_sim.price_panel (B_repeat 3-year price panel prior).

Run (KIMI repo root):
    .venv/bin/python -m unittest discover -s tests -p "test_price_panel.py" -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from wise_sim.price_panel import (
    load_panel,
    parse_price_cell,
    prior_payload,
    series_premium,
    series_yoy,
    summarize,
    write_prior,
)


def _point_values(point):
    return (point.primary, point.weekday, point.weekend, point.parse_ok)


class ParsePriceCellTest(unittest.TestCase):
    def test_weekday_weekend_labeled_cell(self):
        point = parse_price_cell("2026년부터 주중/주말 요금 차등화: 주중 15.5만 / 주말 16.9만 (성인)")
        self.assertEqual(_point_values(point), (155_000, 155_000, 169_000, True))

    def test_plain_man_cell_takes_first_number(self):
        point = parse_price_cell("망고 빙수 11만 원")
        self.assertEqual(_point_values(point), (110_000, None, None, True))

    def test_won_comma_cell(self):
        point = parse_price_cell("성수기 디너 21.5만 원 (服务료 별도)")
        self.assertEqual(_point_values(point), (215_000, None, None, True))

    def test_range_takes_lower_bound(self):
        point = parse_price_cell("10만~15만 원대 실속 라인업")
        self.assertEqual(point.primary, 100_000)

    def test_unparseable_cell_is_flagged(self):
        point = parse_price_cell("연말 케이크 라인업 공개 전")
        self.assertEqual(_point_values(point), (None, None, None, False))

    def test_non_price_numbers_ignored(self):
        point = parse_price_cell("12/24~25 피크일 수령 한정")
        self.assertEqual(_point_values(point), (None, None, None, False))

    def test_decimal_man(self):
        point = parse_price_cell("주중 5.5만 / 주말 5.8만 (단독입장)")
        self.assertEqual((point.primary, point.weekday, point.weekend), (55_000, 55_000, 58_000))


def _build_workbook(path: Path) -> None:
    wb = Workbook()
    sheet = wb.active
    sheet.title = "B_반복"
    header = ["호텔", "상품 계열", "관련 상품ID", "2024", "2025", "2026", "구성·가격 변화", "출처"]
    sheet.append(header)
    sheet.append(header)
    sheet.append([
        "그랜드 하얏트 서울", "여름 시그니처 빙수 (갤러리)", "GHS-26S-03",
        "망고 빙수 10만 원대", "망고 빙수 11만 원", "망고 빙수 12만 원 / 말차 빙수 신설",
        "매년 1만 원씩 점진 인상", "언론 보도·블로그",
    ])
    sheet.append([])
    sheet.append([
        "그랜드 하얏트 서울", "겨울 아이스링크 이용권", "GHS-25W-02",
        "주중 5.5만 / 주말 5.8만", "통합 5.8만 (단일가)", "월~목 5.5만 / 금~일 6.5만",
        "주말 차등 재도입", "공식 PDF",
    ])
    wb.save(path)


class PanelTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workbook = Path(self._tmp.name) / "panel.xlsx"
        _build_workbook(self.workbook)

    def tearDown(self):
        self._tmp.cleanup()

    def test_load_panel(self):
        series_list = load_panel(self.workbook)
        self.assertEqual(len(series_list), 2)
        bingsu = series_list[0]
        self.assertEqual(bingsu.product_ids, ("GHS-26S-03",))
        self.assertEqual([p.primary for p in bingsu.points], [100_000, 110_000, 120_000])
        rink = series_list[1]
        self.assertEqual([p.year for p in rink.points], [2024, 2025, 2026])
        self.assertEqual((rink.points[0].weekday, rink.points[0].weekend), (55_000, 58_000))
        self.assertEqual((rink.points[1].primary, rink.points[1].weekday, rink.points[1].weekend), (58_000, None, None))
        self.assertEqual((rink.points[2].primary, rink.points[2].weekday, rink.points[2].weekend), (55_000, None, None))

    def test_series_yoy_and_premium(self):
        series_list = load_panel(self.workbook)
        self.assertEqual(series_yoy(series_list[0].points), [10.0, 9.09])
        self.assertEqual(series_yoy(series_list[1].points), [5.45, -5.17])
        self.assertEqual(series_premium(series_list[1].points), [5.45])

    def test_summarize_obs_and_medians(self):
        summary = summarize(load_panel(self.workbook))
        self.assertEqual(summary.n_series, 2)
        self.assertEqual(summary.n_obs, 6)
        self.assertEqual(summary.yoy_obs, (10.0, 9.09, 5.45, -5.17))
        self.assertEqual(summary.premium_obs, (5.45,))
        self.assertEqual(summary.median_yoy_pct, 7.27)
        self.assertEqual(summary.median_weekend_premium_pct, 5.45)

    def test_write_prior_json(self):
        out = Path(self._tmp.name) / "analysis" / "price_panel_prior.json"
        summary = write_prior(self.workbook, out)
        self.assertEqual(summary.n_series, 2)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["n_series"], 2)
        self.assertEqual(payload["series"][0]["product_ids"], ["GHS-26S-03"])
        self.assertIn("notes", payload)


if __name__ == "__main__":
    unittest.main()
