"""미로피쉬 세계 — 업장 좌석·운영 창·슬롯 캘린더.

운영 창은 timeline/walkerhill_timeline_validator.py 의 WALKERHILL_VENUE_WINDOWS(◎ 근거)를,
좌석은 prices.SEATS(◎ 근거)를 그대로 재사용한다. 외부 운영 업장(빛의 시어터, §12)은 세계에서 제외.
"""
from __future__ import annotations

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

SLOTS: List[tuple[str, int, int]] = [
    ("BREAKFAST", 7, 10),
    ("MORNING", 10, 12),
    ("LUNCH", 12, 15),
    ("AFTERNOON", 15, 18),
    ("DINNER", 18, 21),
]
SLOT_INDEX = {code: i for i, (code, _, _) in enumerate(SLOTS)}
CALENDAR_START = date(2026, 10, 1)


@dataclass(frozen=True)
class Venue:
    code: str
    name: str
    seats: int
    windows: tuple

    def open_in_slot(self, day: date, slot_code: str) -> bool:
        idx = SLOT_INDEX[slot_code]
        slot_start, slot_end = SLOTS[idx][1], SLOTS[idx][2]
        for w in self.windows:
            if not w.opens_on(day):
                continue
            if w.open_at.hour < slot_end and w.close_at.hour > slot_start:
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
