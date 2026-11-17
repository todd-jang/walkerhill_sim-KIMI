"""Annex workbook B_repeat 3-year price panel → YoY / weekend premium prior (KIMI wise_sim).

Per sheet row (product line), the primary price = the first number in the cell with a '만/unit'
pattern; when 'weekday/weekend' labels exist, the respective values are additionally extracted.
Cells that fail to parse are excluded from the summary with parse_ok=False (no silent fabrication).
The annual YoY and weekend premium (weekend over weekday %) computed this way serve as
directional-level price priors for Gate A persona price_sensitivity calibration and
scenario ±% grid justification, and are supplied as a JSON file.

Limitations (explicit by design): range notation like "100k–150k range" adopts the first (low) value;
label variants such as "Mon–Thu/Fri–Sun" are not recognized, only weekday/weekend/weekday/weekend.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Union

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

CellValue = Union[str, int, float, datetime, None]  # openpyxl data_only 셀 값

PANEL_SHEET = "B_반복"
_YEARS = (2024, 2025, 2026)
_MAN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*만")
_WON_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,})\s*원")
_LABEL_RE = re.compile(r"(주중|weekday|주말|weekend)")
_DATA_START_ROW = 3  # 1~2행 헤더(2행 중복)


class PanelParseError(ValueError):
    """패널 시트 구조·값이 기대와 어긋날 때."""


@dataclass(frozen=True)  # noqa: SLOTS_OK — KIMI venv Python 3.9 (slots 키워드 미지원), 레포 컨벤션 준수
class PricePoint:
    year: int = 0
    primary: int | None = None
    weekday: int | None = None
    weekend: int | None = None
    parse_ok: bool = False
    raw: str = ""


@dataclass(frozen=True)  # noqa: SLOTS_OK
class PriceSeries:
    hotel: str
    series: str
    product_ids: tuple[str, ...]
    points: tuple[PricePoint, ...]


@dataclass(frozen=True)  # noqa: SLOTS_OK
class PanelSummary:
    n_series: int
    n_obs: int
    median_yoy_pct: float | None
    median_weekend_premium_pct: float | None
    yoy_obs: tuple[float, ...]
    premium_obs: tuple[float, ...]


def _to_krw(match: re.Match[str], unit: str) -> int:
    if unit == "man":
        return int(float(match.group(1)) * 10_000)
    return int(match.group(1).replace(",", ""))


def _number_events(text: str) -> list[tuple[int, int, int]]:
    events: list[tuple[int, int, int]] = []
    for match in _MAN_RE.finditer(text):
        events.append((match.start(), match.end(), _to_krw(match, "man")))
    for match in _WON_RE.finditer(text):
        events.append((match.start(), match.end(), _to_krw(match, "won")))
    events.sort(key=lambda item: item[0])
    return events


def _numbers(text: str) -> list[int]:
    return [value for _, _, value in _number_events(text)]


def _labeled(text: str) -> dict[str, int]:
    """라벨은 바로 뒤(다음 라벨 등장 전)의 숫자와만 짝짓는다.

    '주중/주말 요금 차등화: 주중 15.5만 / 주말 16.9만'에서 앞쪽 복합 언급이
    뒤의 가격을 가로채는 오배정을 방지한다.
    """
    labels = list(_LABEL_RE.finditer(text))
    numbers = _number_events(text)
    labeled: dict[str, int] = {}
    for index, match in enumerate(labels):
        key = "weekday" if match.group(1) in ("주중", "weekday") else "weekend"
        if key in labeled:
            continue
        span_end = labels[index + 1].start() if index + 1 < len(labels) else len(text)
        value = next((v for s, e, v in numbers if s >= match.end() and e <= span_end), None)
        if value is not None:
            labeled[key] = value
    return labeled


def parse_price_cell(raw: str) -> PricePoint:
    text = raw.strip()
    numbers = _numbers(text)
    primary = numbers[0] if numbers else None
    labels = _labeled(text)
    parse_ok = primary is not None
    return PricePoint(
        primary=primary, weekday=labels.get("weekday"), weekend=labels.get("weekend"),
        parse_ok=parse_ok, raw=text,
    )


def _point_years(sheet: Worksheet) -> dict[str, int]:
    header: dict[str, int] = {}
    for column in range(1, sheet.max_column + 1):
        value = sheet.cell(1, column).value
        if value is not None and str(value).strip() in {str(year) for year in _YEARS}:
            header[str(value).strip()] = column
    if not header:
        raise PanelParseError(f"{PANEL_SHEET}: no year columns {_YEARS} in header row 1")
    return header


def load_panel(xlsx_path: Path) -> list[PriceSeries]:
    workbook = load_workbook(xlsx_path, data_only=True)
    try:
        sheet = workbook[PANEL_SHEET]
        years = _point_years(sheet)
        hotel_col = _column_of(sheet, "호텔")
        series_col = _column_of(sheet, "상품 계열")
        ids_col = _column_of(sheet, "관련 상품ID")
        result: list[PriceSeries] = []
        for row in range(_DATA_START_ROW, sheet.max_row + 1):
            hotel = sheet.cell(row, hotel_col).value
            if hotel in (None, ""):
                continue
            name = str(sheet.cell(row, series_col).value or "").strip()
            if not name:
                continue
            raw_ids = str(sheet.cell(row, ids_col).value or "").strip()
            product_ids = tuple(part.strip() for part in re.split(r"[,\s]+", raw_ids) if part.strip())
            points = tuple(
                _point(sheet.cell(row, years[str(year)]).value, year)
                for year in _YEARS
            )
            result.append(PriceSeries(hotel=str(hotel).strip(), series=name, product_ids=product_ids, points=points))
        return result
    finally:
        workbook.close()


def _point(cell: CellValue, year: int) -> PricePoint:
    text = "" if cell is None else str(cell).strip()
    if not text:
        return PricePoint(year=year, primary=None, weekday=None, weekend=None, parse_ok=False, raw="")
    parsed = parse_price_cell(text)
    return PricePoint(
        year=year, primary=parsed.primary, weekday=parsed.weekday,
        weekend=parsed.weekend, parse_ok=parsed.parse_ok, raw=parsed.raw,
    )


def _column_of(sheet: Worksheet, name: str) -> int:
    for column in range(1, sheet.max_column + 1):
        value = sheet.cell(1, column).value
        if isinstance(value, str) and value.strip() == name:
            return column
    raise PanelParseError(f"{PANEL_SHEET}: missing column {name!r}")


def series_yoy(points: tuple[PricePoint, ...]) -> list[float]:
    values: list[float] = []
    for prev, curr in zip(points, points[1:]):
        if prev.primary and curr.primary and prev.primary > 0:
            values.append(round((curr.primary - prev.primary) / prev.primary * 100, 2))
    return values


def series_premium(points: tuple[PricePoint, ...]) -> list[float]:
    values: list[float] = []
    for point in points:
        if point.weekday and point.weekend and point.weekday > 0:
            values.append(round((point.weekend - point.weekday) / point.weekday * 100, 2))
    return values


def summarize(series_list: list[PriceSeries]) -> PanelSummary:
    yoy: list[float] = []
    premium: list[float] = []
    for series in series_list:
        yoy.extend(series_yoy(series.points))
        premium.extend(series_premium(series.points))
    return PanelSummary(
        n_series=len(series_list),
        n_obs=sum(1 for series in series_list for point in series.points if point.parse_ok),
        median_yoy_pct=round(statistics.median(yoy), 2) if yoy else None,
        median_weekend_premium_pct=round(statistics.median(premium), 2) if premium else None,
        yoy_obs=tuple(yoy),
        premium_obs=tuple(premium),
    )


def prior_payload(series_list: list[PriceSeries], source: str) -> dict[str, object]:  # noqa: OBJECT_OK — JSON 덤프 페이로드(키·값 다형)
    summary = summarize(series_list)
    return {
        "source": source,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_series": summary.n_series,
        "n_obs": summary.n_obs,
        "median_yoy_pct": summary.median_yoy_pct,
        "median_weekend_premium_pct": summary.median_weekend_premium_pct,
        "yoy_obs": list(summary.yoy_obs),
        "weekend_premium_obs": list(summary.premium_obs),
        "series": [
            {
                "hotel": series.hotel,
                "series": series.series,
                "product_ids": list(series.product_ids),
                "points": [
                    {"year": point.year, "primary": point.primary, "weekday": point.weekday,
                     "weekend": point.weekend, "parse_ok": point.parse_ok, "raw": point.raw}
                    for point in series.points
                ],
                "yoy_pct": series_yoy(series.points),
                "weekend_premium_pct": series_premium(series.points),
            }
            for series in series_list
        ],
        "notes": [
            "primary=셀 첫 숫자(만/원 단위) — 'n만~m만 원대'는 하한 채택",
            "라벨은 바로 뒤(다음 라벨 전) 숫자와만 짝 — '주중/주말 요금 차등화'류 서술형 복합 언급은 오배정하지 않음",
            "YoY·프리미엄은 parse_ok 관측치만 사용 — 2026 미공개 라인업은 자동 제외",
        ],
    }


def write_prior(xlsx_path: Path, out_path: Path) -> PanelSummary:
    series_list = load_panel(xlsx_path)
    payload = prior_payload(series_list, xlsx_path.name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summarize(series_list)


def main() -> int:
    parser = argparse.ArgumentParser(description="B_repeat 3-year price panel → prior JSON.")
    parser.add_argument("--xlsx", type=Path, default=Path("/Users/robotjang/Downloads/호텔조사_정리 (2).xlsx"))
    parser.add_argument("--out", type=Path, default=Path("analysis/price_panel_prior.json"))
    args = parser.parse_args()
    summary = write_prior(args.xlsx, args.out)
    print(
        f"가격 패널 프라이어 → {args.out}  계열 {summary.n_series}개 · 관측 {summary.n_obs}건 · "
        f"중앙 YoY {summary.median_yoy_pct}% · 주말 프리미엄 {summary.median_weekend_premium_pct}%"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
