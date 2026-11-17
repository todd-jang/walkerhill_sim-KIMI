"""Read-only view payloads for the WISE pilot dashboard (가격 패널·판정 계산기)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from wise_sim.l3_verdict import full_report

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICE_PANEL_PATH = REPO_ROOT / "analysis" / "price_panel_prior.json"
MAX_N_WEEKENDS = 10_000


class ViewDataError(ValueError):
    """대시보드가 다루는 데이터·입력이 기대 형식과 어긋날 때."""


class PanelNotFound(ViewDataError):
    """가격 패널 산출물 파일이 없을 때."""


def load_price_panel(path: Path | None = None) -> dict[str, Any]:
    target = path or PRICE_PANEL_PATH
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise PanelNotFound(f"가격 패널 산출물이 없습니다: {target.name}") from error
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ViewDataError("가격 패널 산출물이 올바른 JSON이 아닙니다") from error
    if not isinstance(payload, dict):
        raise ViewDataError("가격 패널 산출물은 JSON 객체여야 합니다")
    return payload


def _require_number(
    payload: Mapping[str, Any],
    key: str,
    *,
    low: float,
    high: float,
    integer: bool = False,
) -> float:
    if key not in payload:
        raise ViewDataError(f"{key} 값이 필요합니다")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ViewDataError(f"{key} 는 숫자여야 합니다")
    if integer and float(value) != int(value):
        raise ViewDataError(f"{key} 는 정수여야 합니다")
    number = float(int(value) if integer else value)
    if not low <= number <= high:
        raise ViewDataError(f"{key} 는 {low}~{high} 범위여야 합니다")
    return number


def compute_verdict(payload: Mapping[str, Any]) -> dict[str, Any]:
    """폼 입력을 검증한 뒤 l3_verdict.full_report와 동일한 판정 리포트를 계산한다.

    trace 검증(trace_result)은 별도 절차이므로 n_exceed만 입력받아 전달한다.
    """
    if not isinstance(payload, Mapping):
        raise ViewDataError("요청 본문은 JSON 객체여야 합니다")
    direction = _require_number(payload, "direction", low=0.0, high=1.0)
    rho = _require_number(payload, "rho", low=-1.0, high=1.0)
    abstain = _require_number(payload, "abstain", low=0.0, high=1.0)
    n_weekends = int(_require_number(payload, "n_weekends", low=1, high=MAX_N_WEEKENDS, integer=True))
    n_hits = int(_require_number(payload, "n_hits", low=0, high=n_weekends, integer=True))
    n_exceed = int(_require_number(payload, "n_exceed", low=0, high=MAX_N_WEEKENDS, integer=True)) \
        if "n_exceed" in payload else 0
    induction = payload.get("induction_resist")
    induction_value = None
    if induction is not None:
        induction_value = _require_number(payload, "induction_resist", low=0.0, high=1.0)
    return full_report(
        direction,
        rho,
        abstain,
        {"n_exceed": n_exceed},
        n_weekends,
        n_hits,
        induction_value,
    )
