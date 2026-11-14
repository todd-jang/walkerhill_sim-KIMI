"""미로피쉬 세계 — 업장 좌석·운영 창·슬롯 캘린더.

운영 창은 timeline/walkerhill_timeline_validator.py 의 WALKERHILL_VENUE_WINDOWS(◎ 근거)를,
좌석은 prices.SEATS(◎ 근거)를 그대로 재사용한다. 외부 운영 업장(빛의 시어터, §12)은 세계에서 제외.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

HILLINK = Path(__file__).resolve().parent.parent
for sub in ("timeline", "research"):
    p = HILLINK / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from prices import ROOMS, SEATS  # noqa: E402
from walkerhill_timeline_validator import WALKERHILL_VENUE_WINDOWS  # noqa: E402

VENUE_CODE = {
    '더뷔페': 'THE_BUFFET', '피자힐': 'PIZZA_HILL', '파빌리온': 'THE_PAVILION',
    '명월관': 'MYONGWOLGWAN', '모에기': 'MOEGI', '온달': 'ONDAL',
    '금룡': 'GEUMRYONG', '르파사쥬': 'LE_PASSAGE', '럭스바': 'LUX_BAR',
    '스카이야드': 'SKYARD', '포레스트 파크': 'FOREST_PARK', '수영장': 'GRAND_POOL',
    '웰니스 사우나': 'WELLNESS_SAUNA', '빛의 시어터': 'THEATRE_OF_LIGHT',
}
VENUE_NAME = {code: name for name, code in VENUE_CODE.items()}
EXTERNAL_VENUES = {'THEATRE_OF_LIGHT'}
SEATS_MAP = {VENUE_CODE[name]: SEATS[name]['n'] for name in SEATS if name in VENUE_CODE and name != '빛의 시어터'}

SLOT_MINUTES = int(os.environ.get("MIROFISH_SLOT_MINUTES", "60"))
DAY_START_MIN = 7 * 60
DAY_END_MIN = 22 * 60


def _build_slots(minutes: int) -> List[tuple[str, int, int]]:
    out: List[tuple[str, int, int]] = []
    t = DAY_START_MIN
    while t + minutes <= DAY_END_MIN:
        out.append((f"{t // 60:02d}:{t % 60:02d}", t, t + minutes))
        t += minutes
    return out


SLOTS: List[tuple[str, int, int]] = _build_slots(SLOT_MINUTES)
CALENDAR_START = date(2026, 10, 1)


@dataclass(frozen=True)
class Venue:
    code: str
    name: str
    seats: int
    windows: tuple

    def open_in_slot(self, day: date, slot_code: str) -> bool:
        start = next((s for code, s, _ in SLOTS if code == slot_code), None)
        if start is None:
            return False
        for w in self.windows:
            if not w.opens_on(day):
                continue
            open_min = w.open_at.hour * 60 + w.open_at.minute
            last_min = (w.last_order.hour * 60 + w.last_order.minute) if w.last_order else None
            close_min = w.close_at.hour * 60 + w.close_at.minute
            cut = last_min if last_min is not None else close_min
            if open_min <= start < cut:
                return True
        return False

    def capacity(self, capacity_scale: float = 1.0) -> int:
        return max(1, int(self.seats * capacity_scale))


class World:
    def __init__(self, capacity_scale: float = 1.0):
        self.capacity_scale = capacity_scale
        self.venues: Dict[str, Venue] = {}
        for code, name in VENUE_NAME.items():
            if code in EXTERNAL_VENUES:
                continue
            windows = WALKERHILL_VENUE_WINDOWS.get(code, ())
            seats = SEATS_MAP.get(code, 0)
            if not windows or seats <= 0:
                continue
            self.venues[code] = Venue(code=code, name=name, seats=seats, windows=windows)

    def day_of(self, index: int) -> date:
        return CALENDAR_START + timedelta(days=index)

    def open_venues(self, day: date, slot_code: str) -> List[str]:
        return [c for c, v in self.venues.items() if v.open_in_slot(day, slot_code)]

    def room_units(self) -> Dict[str, int]:
        return {k: v['n'] for k, v in ROOMS.items()}
