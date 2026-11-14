"""
시간대별 고객 동선 재구성 + 비현실/불가능 고객 필터 + RevPAR·RevPASH 연결.

  python3 route_analysis.py                              # 4,000 체류-일 표본
  python3 route_analysis.py --sample 12000               # 표본 확대
  python3 route_analysis.py --json                       # 기계 판독 출력

무엇을 하는가
  revenue_l5.csv 는 '무엇을 샀나' 는 있지만 '몇 시에' 가 없다. 그래서 구매 **집합은 실측 그대로**
  두고 시간만 재구성한다 — 없는 시간을 만들어내는 것보다, 실제 구매 조합이 운영 창 안에서
  성립하는지 검사하는 쪽이 정직하다.

  운영 창은 timeline/walkerhill_timeline_validator.py 의 WALKERHILL_VENUE_WINDOWS(요일·계절·
  last_order 포함)를 그대로 재사용한다. 좌석 수는 prices.SEATS, 객실 수는 prices.ROOMS.

필터 — '비현실적이거나 불가능한 고객' 을 버리는 규칙 (사유별로 센다)
  CLOSED       그 요일에 업장이 닫혀 있음 (창 없음)
  SLOT_DUP     같은 끼니(조식/중식/석식)에 좌식 식사 두 곳 — 하루에 한 끼를 두 번 앉는 고객
  CAPACITY     동반 인원이 좌석 수를 넘음
  COMPOSITION  아동동반인데 야식(20시 이후 바) — 운영 규칙상 불가
  TIMING       창·이동시간(15분)·체류시간 안에서 겹치지 않는 배치가 없음

집계
  · 시간대 × 업장 방문 행렬
  · 동선 전이 (업장@시각 → 다음 업장@시각)
  · RevPASH = 시간당 매출 ÷ 좌석 수 (업장별)
  · RevPAR = 객실 매출 ÷ (객실 수 × 33일), 객실박 점유율 = 객실박 수 ÷ 가용 객실박
    객실 수 = 그랜드 557 + 비스타 253 + 더글라스 65 = 875 (prices.ROOMS · 등급 표기 그대로)

가정 (전부 코드에 명시 · 리포트에 한계로 기재)
  · 체류시간(분): 뷔페 90 · 좌식 75 · 카페/베이커리 20 · 피자 30 · 수영 120 · 사우나 90 ·
    공연 120 · 바 60 · 산책 60 · 스카이야드 60 · 골프 120 · IRD 30(객실 내)
  · IRD(룸서비스)는 객실 안에서 이뤄지므로 업장 이동·좌석 경쟁에서 제외한다.
  · 객실 상품(그랜드·비스타·더글라스 업장)은 동선이 아니라 RevPAR 분자다.
  · 아동동반·박수는 persona_grid 의 (성별, 연령, 목적) 일치 행에서 비중 가중 추출.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path

TIMELINE = Path(__file__).resolve().parent.parent / 'timeline'
sys.path.insert(0, str(TIMELINE))

from prices import ROOMS, SEATS  # noqa: E402
from walkerhill_timeline_validator import WALKERHILL_VENUE_WINDOWS  # noqa: E402

HORIZON_DAYS = 33  # 2026-10-01 ~ 2026-11-02

VENUE_CODE = {
    '더뷔페': 'THE_BUFFET',
    '피자힐': 'PIZZA_HILL',
    '파빌리온': 'THE_PAVILION',
    '명월관': 'MYONGWOLGWAN',
    '모에기': 'MOEGI',
    '온달': 'ONDAL',
    '금룡': 'GEUMRYONG',
    '르파사쥬': 'LE_PASSAGE',
    '럭스바': 'LUX_BAR',
    '스카이야드': 'SKYARD',
    '포레스트 파크': 'FOREST_PARK',
    '수영장': 'GRAND_POOL',
    '웰니스 사우나': 'WELLNESS_SAUNA',
    '빛의 시어터': 'THEATRE_OF_LIGHT',
}
IN_ROOM_VENUE = 'IRD'

DWELL_MIN = {
    'THE_BUFFET': 90,
    'THE_PAVILION': 75,
    'MYONGWOLGWAN': 75,
    'MOEGI': 75,
    'ONDAL': 75,
    'GEUMRYONG': 75,
    'PIZZA_HILL': 30,
    'LE_PASSAGE': 20,
    'LUX_BAR': 60,
    'SKYARD': 60,
    'FOREST_PARK': 60,
    'GRAND_POOL': 120,
    'VISTA_POOL': 120,
    'WELLNESS_SAUNA': 90,
    'THEATRE_OF_LIGHT': 120,
    'IRD': 30,
}
TRAVEL_MIN = 15
SEATED_MEALS = {'THE_BUFFET', 'THE_PAVILION', 'MYONGWOLGWAN', 'MOEGI', 'ONDAL', 'GEUMRYONG'}
MEAL_CLASS_BY_HOUR = ((5, 10, '조식'), (11, 15, '중식'), (17, 22, '석식'))
BAR_VENUES = {'LUX_BAR'}

REJECTIONS = ('CLOSED', 'SLOT_DUP', 'CAPACITY', 'COMPOSITION', 'TIMING')

# 모델링 가정: 창 전체에 균등 배정하면 넓은 창의 시설(수영장·사우나)이 모든 시간대를
# 독점하는 인공물이 생기므로, 업장 성격별 선호 시간창을 두고 운영 창과 교집합한다.
PREFERRED = {
    'THE_BUFFET': ((7, 0, 9, 30), (12, 0, 14, 0), (18, 0, 20, 0)),
    'THE_PAVILION': ((11, 30, 14, 0), (17, 30, 20, 0)),
    'MYONGWOLGWAN': ((12, 0, 14, 0), (17, 30, 20, 0)),
    'MOEGI': ((12, 0, 14, 0), (18, 0, 20, 0)),
    'ONDAL': ((12, 0, 14, 0), (18, 0, 20, 0)),
    'GEUMRYONG': ((12, 0, 14, 0), (18, 0, 20, 0)),
    'PIZZA_HILL': ((11, 30, 13, 30), (17, 0, 19, 30)),
    'LE_PASSAGE': ((9, 30, 11, 0), (15, 0, 17, 0)),
    'LUX_BAR': ((20, 0, 22, 30),),
    'SKYARD': ((10, 0, 12, 0), (16, 0, 19, 0)),
    'FOREST_PARK': ((10, 0, 12, 0), (15, 0, 18, 0)),
    'GRAND_POOL': ((7, 0, 10, 0), (15, 0, 18, 0)),
    'VISTA_POOL': ((7, 0, 10, 0), (15, 0, 18, 0)),
    'WELLNESS_SAUNA': ((7, 0, 10, 0), (16, 0, 19, 0)),
    'THEATRE_OF_LIGHT': ((10, 30, 12, 30), (14, 30, 16, 30)),
    'IRD': ((7, 0, 9, 0), (21, 0, 23, 0)),
}


@dataclass
class Assignment:
    venue: str
    start: float  # 자정 기준 시간(시간 단위 · 소수)
    dwell: int
    label: str
    revenue: float


@dataclass
class DayResult:
    stayed: bool
    reason: str = ''
    assignments: list[Assignment] = field(default_factory=list)


def _meal_class(venue: str, hour: float) -> str | None:
    if venue not in SEATED_MEALS:
        return None
    for lo, hi, name in MEAL_CLASS_BY_HOUR:
        if lo <= hour < hi:
            return name
    return None


def _windows(venue: str, day: date) -> list[tuple[float, float]]:
    windows = WALKERHILL_VENUE_WINDOWS.get(venue, ())
    result = []
    for window in windows:
        if not window.opens_on(day):
            continue
        open_h = window.open_at.hour + window.open_at.minute / 60
        close_at = window.effective_last_order()
        close_h = close_at.hour + close_at.minute / 60
        if close_h <= open_h:  # 자정 넘김 창 (예: 20:00-23:59 · 01:00 마감)
            close_h += 24
        result.append((open_h, close_h))
    return result


def _parse_party(text: str) -> int:
    import re
    match = re.search(r'(\d+)\s*인', text or '')
    return int(match.group(1)) if match else 0


def _parse_box(text: str) -> dict[int, float]:
    """persona_grid 박수 열 '0:1.0 · 1:1.0' 같은 표기를 분포로."""
    result: dict[int, float] = {}
    for chunk in (text or '').split('·'):
        if ':' not in chunk:
            continue
        try:
            value, weight = chunk.strip().split(':')
            result[int(value)] = float(weight)
        except ValueError:
            continue
    return result


def _read_rows(name: str) -> list[dict[str, str]]:
    with (TIMELINE / name).open(newline='', encoding='utf-8-sig') as handle:
        return list(csv.DictReader(handle))


def load_inputs() -> dict:
    rows = _read_rows('revenue_l5.csv')
    menu = _read_rows('menu.csv')
    persona = _read_rows('persona_grid.csv')
    room_products = {(m['업장'].strip(), m['상품'].strip()) for m in menu if m['객실'].strip()}
    personas_by_key: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in persona:
        personas_by_key[(row['성별'].strip(), row['연령'].strip(), row['목적'].strip())].append(row)
    return {'rows': rows, 'personas': personas_by_key, 'room_products': room_products}


def _persona_profile(row: dict, personas: dict) -> tuple[int, bool]:
    """관측 행 → (동반 인원, 아동동반). 목적 접미사 '2인' 우선, 없으면 persona_grid 추출."""
    size = _parse_party(row['구성·방문 목적'])
    key_candidates = [
        (row['성별'].strip(), row['대표자 연령'].strip(), row['구성·방문 목적'].strip()),
    ]
    purpose = row['구성·방문 목적'].strip()
    for (gender, age, grid_purpose), group in personas.items():
        if (gender, age) == (row['성별'].strip(), row['대표자 연령'].strip()) and purpose.startswith(grid_purpose):
            key_candidates.append((gender, age, grid_purpose))
    child = False
    for key in key_candidates[1:]:
        group = personas.get(key)
        if not group:
            continue
        weights = [float(g['비중']) for g in group]
        total = sum(weights)
        if total <= 0:
            continue
        pick = random.random() * total
        upto = 0.0
        for g, w in zip(group, weights):
            upto += w
            if pick <= upto:
                child = g['아동동반'].strip() == '1'
                if not size:
                    box = _parse_box(g['박수'])
                    if box:
                        size = max(box, key=box.get)
                break
        break
    return size or 2, child


def _hour_shares(start: float, dwell_min: int):
    end = start + dwell_min / 60
    hour = int(start)
    while hour < end:
        lo = max(start, float(hour))
        hi = min(end, hour + 1.0)
        if hi > lo:
            yield hour % 24, hi - lo
        hour += 1


def _mapped(items: list[dict], room_products: set[tuple[str, str]]) -> list[tuple[str, float, str]]:
    venues = []
    for item in items:
        if (item['업장'].strip(), item['상품'].strip()) in room_products:
            continue
        venue = VENUE_CODE.get(item['업장'].strip())
        if venue is None:
            venue = 'IRD' if item['업장'].strip() == IN_ROOM_VENUE else None
        if venue is None:
            continue
        venues.append((venue, float(item['결제액']), item['상품'].strip()))
    return venues


def schedule_day(
    items: list[dict],
    day: date,
    party: int,
    child: bool,
    rng: random.Random,
    room_products: set[tuple[str, str]],
    attempts: int = 40,
) -> DayResult:
    venues = _mapped(items, room_products)

    if not venues:
        return DayResult(stayed=False, reason='CLOSED')

    for venue, _, _ in venues:
        if venue != 'IRD' and not _windows(venue, day):
            return DayResult(stayed=False, reason='CLOSED')
    if party > 1 and any(venue in SEATS_MAP and party > SEATS_MAP[venue] for venue, _, _ in venues):
        return DayResult(stayed=False, reason='CAPACITY')
    if child and any(venue in BAR_VENUES for venue, _, _ in venues):
        return DayResult(stayed=False, reason='COMPOSITION')

    best_reason = 'TIMING'
    for _ in range(attempts):
        order = list(range(len(venues)))
        rng.shuffle(order)
        order.sort(key=lambda i: _meal_order(venues[i][0], day) or _windows_center(venues[i][0], day))
        assigned: list[Assignment] = []
        used_slots: set[str] = set()
        ok = True
        for index in order:
            venue, revenue, label = venues[index]
            if venue == 'IRD':
                slot = rng.uniform(11.5, 20.5)
                assigned.append(Assignment(venue, slot, DWELL_MIN[venue], label, revenue))
                continue
            windows = _intervals(venue, day)
            placed = False
            for _ in range(20):
                lo, hi = rng.choice(windows)
                dwell = DWELL_MIN.get(venue, 60) / 60
                if hi - lo < dwell:
                    best_reason = 'TIMING'
                    continue
                start = rng.uniform(lo, hi - dwell)
                meal = _meal_class(venue, start)
                if meal and meal in used_slots:
                    best_reason = 'SLOT_DUP'
                    continue
                if any(_conflict(start, dwell, other) for other in assigned):
                    best_reason = 'TIMING'
                    continue
                assigned.append(Assignment(venue, start, int(dwell * 60), label, revenue))
                if meal:
                    used_slots.add(meal)
                placed = True
                break
            if not placed:
                ok = False
                break
        if ok:
            return DayResult(stayed=True, assignments=sorted(assigned, key=lambda a: a.start))
        # 끼니 중복이 원인이면 재시도로 안 풀린다 — 바로 확정
        if best_reason == 'SLOT_DUP' and _slot_dup_unavoidable(venues, day):
            return DayResult(stayed=False, reason='SLOT_DUP')
    return DayResult(stayed=False, reason=best_reason)


SEATS_MAP = {code: SEATS[name]['n'] for name, code in (
    ('더뷔페', 'THE_BUFFET'), ('피자힐', 'PIZZA_HILL'), ('파빌리온', 'THE_PAVILION'),
    ('명월관', 'MYONGWOLGWAN'), ('모에기', 'MOEGI'), ('온달', 'ONDAL'),
    ('금룡', 'GEUMRYONG'), ('르파사쥬', 'LE_PASSAGE'), ('럭스바', 'LUX_BAR'),
) if name in SEATS}


def _intervals(venue: str, day: date) -> list[tuple[float, float]]:
    if venue == 'IRD':
        return [(lo_h + lo_m / 60, hi_h + hi_m / 60) for lo_h, lo_m, hi_h, hi_m in PREFERRED['IRD']]
    windows = _windows(venue, day)
    preferred = PREFERRED.get(venue)
    if not preferred:
        return windows
    intervals = []
    for w_lo, w_hi in windows:
        for lo_h, lo_m, hi_h, hi_m in preferred:
            lo = max(w_lo, lo_h + lo_m / 60)
            hi = min(w_hi, hi_h + hi_m / 60)
            if hi > lo:
                intervals.append((lo, hi))
    return intervals or windows


def _meal_order(venue: str, day: date) -> int:
    if venue not in SEATED_MEALS:
        return 0
    centers = (_windows_center(venue, day),)
    for order, (lo, hi, name) in enumerate(MEAL_CLASS_BY_HOUR, start=1):
        if _windows_overlap(venue, day, lo, hi):
            return order
    return 0


def _windows_center(venue: str, day: date) -> float:
    if venue == 'IRD':
        return 12.0
    windows = _windows(venue, day)
    if not windows:
        return 24.0
    lo = min(w[0] for w in windows)
    hi = max(w[1] for w in windows)
    return (lo + hi) / 2


def _conflict(start: float, dwell: float, other: Assignment) -> bool:
    end = start + dwell
    other_end = other.start + other.dwell / 60
    gap = 0 if other.venue == 'IRD' else TRAVEL_MIN / 60
    if other.venue == 'IRD':
        return not (end <= other.start or other_end <= start)
    return not (end + gap <= other.start or other_end + gap <= start)


def _slot_dup_unavoidable(venues: list[tuple[str, float, str]], day: date) -> bool:
    counts: Counter[str] = Counter()
    for venue, _, _ in venues:
        if venue not in SEATED_MEALS:
            continue
        for lo, hi, name in MEAL_CLASS_BY_HOUR:
            if _windows_overlap(venue, day, lo, hi):
                counts[name] += 1
    return any(count > 1 for count in counts.values())


def _windows_overlap(venue: str, day: date, lo: float, hi: float) -> bool:
    return any(w_lo < hi and lo < w_hi for w_lo, w_hi in _windows(venue, day))


def analyze(sample: int, seed: int) -> dict:
    random.seed(seed)
    data = load_inputs()
    rows = data['rows']
    persona_map = data['personas']

    room_products = data['room_products']
    day_items: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        day_items[(row['체류'], row['구매 날짜'])].append(row)
    keys = sorted(day_items)
    all_days = len(keys)
    random.Random(seed).shuffle(keys)
    keys = keys[:sample]
    scale = all_days / max(1, len(keys))

    reasons: Counter[str] = Counter()
    closed_venues: Counter[str] = Counter()
    hour_venue: Counter[tuple[int, str]] = Counter()
    transitions: Counter[tuple[str, int, str, int]] = Counter()
    venue_revenue: Counter[str] = Counter()
    venue_visits: Counter[str] = Counter()
    accepted = 0
    total_items = 0
    for key in keys:
        items = day_items[key]
        party, child = _persona_profile(items[0], persona_map)
        day = date.fromisoformat(key[1])
        result = schedule_day(
            items,
            day,
            party,
            child,
            random.Random(f'{seed}:{key[0]}:{key[1]}'),
            room_products,
        )
        if not result.stayed:
            reasons[result.reason] += 1
            if result.reason == 'CLOSED':
                for venue, _, _ in _mapped(items, room_products):
                    if venue != 'IRD' and not _windows(venue, day):
                        closed_venues[venue] += 1
            continue
        accepted += 1
        total_items += len(result.assignments)
        for a in result.assignments:
            for hour, share in _hour_shares(a.start, a.dwell):
                hour_venue[(hour, a.venue)] += share
            venue_visits[a.venue] += 1
            venue_revenue[a.venue] += a.revenue
        for prev, nxt in zip(result.assignments, result.assignments[1:]):
            transitions[(prev.venue, int(prev.start) % 24, nxt.venue, int(nxt.start) % 24)] += 1

    room_revenue = 0.0
    room_units = 0.0
    for row in rows:
        if (row['업장'].strip(), row['상품'].strip()) in room_products:
            room_revenue += float(row['결제액'])
            room_units += float(row['구매 수량'] or 0)

    rooms_total = sum(info['n'] for info in ROOMS.values())
    available_room_nights = rooms_total * HORIZON_DAYS
    revpar = room_revenue / available_room_nights
    occupancy = room_units / available_room_nights

    repash = {}
    for venue, revenue in venue_revenue.items():
        seats = SEATS_MAP.get(venue)
        if not seats:
            continue
        hours = 0.0
        seen = set()
        for key in keys:
            day = date.fromisoformat(key[1])
            for lo, hi in _windows(venue, day):
                seen.add((key[1], round(lo, 2), round(hi, 2)))
        for _, lo, hi in seen:
            hours += hi - lo
        if hours > 0:
            repash[venue] = revenue * scale / (seats * hours)

    hour_venue_out: dict[str, dict[str, int]] = {}
    for (hour, venue), count in sorted(hour_venue.items()):
        hour_venue_out.setdefault(f'{hour:02d}:00', {})[venue] = round(count, 1)

    hour_totals: Counter[int] = Counter()
    for (hour, _venue), count in hour_venue.items():
        hour_totals[hour] += count
    venue_peak: dict[str, int] = {}
    for (hour, venue), count in hour_venue.items():
        if count > hour_venue.get((venue_peak.get(venue, -1), venue), 0.0):
            venue_peak[venue] = hour

    return {
        'sample_days': len(keys),
        'population_scale': scale,
        'closed_by_venue': dict(closed_venues.most_common(8)),
        'hour_totals': {f'{hour:02d}:00': round(count, 1) for hour, count in sorted(hour_totals.items())},
        'venue_peak_hour': {venue: f'{hour:02d}:00' for venue, hour in sorted(venue_peak.items())},
        'accepted_days': accepted,
        'acceptance_rate': accepted / max(1, len(keys)),
        'rejections': dict(reasons),
        'visits_total': sum(venue_visits.values()),
        'stay_hours_total': round(sum(hour_totals.values()), 1),
        'hour_venue': hour_venue_out,
        'venue_visits': dict(venue_visits.most_common()),
        'transitions_top': [
            {'from': f'{a}@{h1:02d}', 'to': f'{b}@{h2:02d}', 'count': count}
            for (a, h1, b, h2), count in transitions.most_common(15)
        ],
        'revpash': {venue: round(value, 1) for venue, value in sorted(repash.items(), key=lambda kv: -kv[1])},
        'revpar': revpar,
        'occupancy': occupancy,
        'room_revenue': room_revenue,
        'room_units': room_units,
        'rooms_total': rooms_total,
        'available_room_nights': available_room_nights,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='시간대별 동선 재구성 + RevPAR/RevPASH')
    parser.add_argument('--sample', type=int, default=4000, help='체류-일 표본 수')
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    result = analyze(args.sample, args.seed)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(f"표본 {result['sample_days']:,} 체류-일 · 수용 {result['accepted_days']:,} "
          f"({result['acceptance_rate']:.1%}) · 방문 {result['visits_total']:,}건")
    print(f"필터 사유: {result['rejections'] or '없음'} · 폐장 업장: {result['closed_by_venue'] or '없음'}")
    print('시간대 재실(시간 환산): ' + ' · '.join(f'{h} {c:.0f}' for h, c in sorted(result['hour_totals'].items())))
    print(f"RevPAR {result['revpar']:,.0f}원 · 객실박 점유율 {result['occupancy']:.1%} "
          f"(객실 {result['rooms_total']}실 × {HORIZON_DAYS}일 = {result['available_room_nights']:,} 박)")
    print('\n시간대 × 업장 방문 상위:')
    by_hour = sorted(result['hour_venue'].items())
    for hour in ('07:00', '08:00', '12:00', '13:00', '18:00', '19:00', '20:00', '21:00'):
        row = result['hour_venue'].get(hour)
        if row:
            top = sorted(row.items(), key=lambda kv: -kv[1])[:4]
            print(f"  {hour}  " + ' · '.join(f'{v} {c}' for v, c in top))
    print('\n동선 전이 상위:')
    for t in result['transitions_top'][:8]:
        print(f"  {t['from']} → {t['to']}  ({t['count']})")
    print('\nRevPASH (원/시간·좌석 · 전체 모집단 환산):')
    for venue, value in list(result['revpash'].items())[:8]:
        print(f'  {venue:18s} {value:8,.0f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
