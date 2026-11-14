"""물리 동선 — 업장 좌표·건물을 이용한 최단 경로(분).

같은 건물 안은 직선 이동, 건물을 옮길 때는 로비(그랜드↔비스타 공용)를 경유한다.
좌표는 도식 배치(추정)이며, 1 좌표 단위를 3분으로 환산한다.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

VENUE_XY: Dict[str, Tuple[float, float]] = {
    "SKYARD": (0.17, 0.40), "LUX_BAR": (0.14, 0.57), "THE_PAVILION": (0.30, 0.56),
    "THE_BUFFET": (0.64, 0.47), "LE_PASSAGE": (0.56, 0.57), "MYONGWOLGWAN": (0.72, 0.38),
    "ONDAL": (0.70, 0.56), "GEUMRYONG": (0.59, 0.36), "MOEGI": (0.76, 0.51),
    "WELLNESS_SAUNA": (0.67, 0.60), "GRAND_POOL": (0.51, 0.74), "PIZZA_HILL": (0.63, 0.72),
    "FOREST_PARK": (0.22, 0.20),
}
BUILDING: Dict[str, str] = {
    "SKYARD": "VISTA", "LUX_BAR": "VISTA", "THE_PAVILION": "VISTA",
    "THE_BUFFET": "GRAND", "LE_PASSAGE": "GRAND", "MYONGWOLGWAN": "GRAND",
    "ONDAL": "GRAND", "GEUMRYONG": "GRAND", "MOEGI": "GRAND", "WELLNESS_SAUNA": "GRAND",
    "GRAND_POOL": "OUTDOOR", "PIZZA_HILL": "OUTDOOR", "FOREST_PARK": "OUTDOOR",
}
LOBBY = (0.58, 0.50)
MINUTES_PER_UNIT = 30.0
TRANSITION_MINUTES = 2.0


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def travel_minutes(origin: str | None, destination: str) -> float:
    if origin is None or origin == destination:
        return 0.0 if origin == destination else 2.0
    if origin not in VENUE_XY or destination not in VENUE_XY:
        return 2.0
    if BUILDING.get(origin) == BUILDING.get(destination):
        return round(_dist(VENUE_XY[origin], VENUE_XY[destination]) * MINUTES_PER_UNIT, 1)
    total = (_dist(VENUE_XY[origin], LOBBY) + _dist(LOBBY, VENUE_XY[destination])) * MINUTES_PER_UNIT
    return round(total + TRANSITION_MINUTES, 1)


def route(origin: str | None, destination: str) -> Tuple[List[str], float]:
    """동선 경로와 소요 분. 건물 이동이면 로비 경유."""
    if origin is None:
        return ([destination], 2.0)
    if origin == destination:
        return ([destination], 0.0)
    if origin not in VENUE_XY or destination not in VENUE_XY:
        return ([origin, destination], 2.0)
    if BUILDING.get(origin) == BUILDING.get(destination):
        return ([origin, destination], travel_minutes(origin, destination))
    return ([origin, "LOBBY", destination], travel_minutes(origin, destination))
