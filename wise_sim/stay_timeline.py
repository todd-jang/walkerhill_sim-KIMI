"""투숙 하루 세그먼트 합성 + I1~I6 엄격 검증 (업장 세션 포함).

어댑터의 기본 검증(객실 세그먼트 1개)과 달리, 체크인 대기 → 키 발급 → 객실 →
업장 세션(SEATED/DINING) → 객실 → 체크아웃 전 구간을 구성해 타임라인 검증기를 돌린다.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

TIMELINE_DIR = Path(__file__).resolve().parent.parent / "analysis" / "timeline"
if str(TIMELINE_DIR) not in sys.path:
    sys.path.insert(0, str(TIMELINE_DIR))

import walkerhill_timeline_validator as timeline_validator  # noqa: E402

OUTLET_VENUE = {
    "더 뷔페": "THE_BUFFET",
    "클럽 라운지": "GRAND_CLUB_LOUNGE",
    "더 파빌리온": "THE_PAVILION",
    "온달": "ONDAL",
}
ROOM_LOCATION = "GRAND_ROOM"
FRONT_DESK = "GRAND_FRONT_DESK"
OPEN_CHECKIN = time(15, 0)
CHECKOUT_TIME = time(11, 0)


def stay_dates(record: Any) -> tuple[date, date]:
    """어댑터와 동일한 기준(2026-10, 1~28일)으로 체크인/체크아웃 날짜를 정한다."""
    day = min(28, max(1, int(record.stay_date_start)))
    checkin = date(2026, 10, day)
    return checkin, checkin + timedelta(days=max(1, int(record.trip_length)))


def build_stay_segments(record: Any, outlet: str | None, session_hour: float = 18.0) -> list[Any]:
    """체크인→객실→업장→객실→체크아웃 세그먼트를 만든다 (업장 없으면 객실만)."""
    checkin, checkout = stay_dates(record)
    start = datetime.combine(checkin, OPEN_CHECKIN)
    seat = datetime.combine(checkin, time(int(session_hour), 0))
    seated_end = seat + timedelta(minutes=10)
    dining_end = seat + timedelta(minutes=80)
    back_in_room = dining_end + timedelta(minutes=30)
    room_end = datetime.combine(checkout, CHECKOUT_TIME) - timedelta(minutes=60)
    checkout_start = room_end + timedelta(minutes=30)

    segments = [
        timeline_validator.TimelineSegment(
            state="IN_CHECKIN_QUEUE", location=FRONT_DESK,
            t_in=start - timedelta(minutes=30), t_out=start,
        ),
        timeline_validator.TimelineSegment(
            state="KEY_ISSUED", location=FRONT_DESK,
            t_in=start, t_out=start + timedelta(minutes=10),
        ),
    ]
    venue_key = OUTLET_VENUE.get(outlet or "")
    if venue_key:
        segments += [
            timeline_validator.TimelineSegment(
                state="SEATED", location=venue_key, machine="M7_OutletSession",
                t_in=seat, t_out=seated_end, venue=venue_key,
            ),
            timeline_validator.TimelineSegment(
                state="DINING", location=venue_key, machine="M7_OutletSession",
                t_in=seated_end, t_out=dining_end, venue=venue_key,
            ),
        ]
        room_return = back_in_room
    else:
        room_return = start + timedelta(minutes=60)
    segments += [
        timeline_validator.TimelineSegment(
            state="IN_ROOM", location=ROOM_LOCATION,
            t_in=room_return, t_out=room_end,
        ),
        timeline_validator.TimelineSegment(
            state="CHECKED_OUT_SETTLED", location=FRONT_DESK,
            t_in=checkout_start, t_out=checkout_start + timedelta(minutes=30),
        ),
    ]
    segments.sort(key=lambda segment: segment.t_in)
    return segments


def strict_validation(record: Any, outlet: str | None) -> dict[str, Any]:
    """전 구간 세그먼트로 I1~I6를 검증하고 위반을 수집한다."""
    segments = build_stay_segments(record, outlet)
    try:
        report = timeline_validator.validate_walkerhill_timeline(
            segments, nights=max(1, int(record.trip_length)),
        )
    except Exception as error:  # noqa: BLE001 — 검증기 내부 예외도 결과로 기록한다
        return {"status": "error", "message": str(error), "violations": [], "violations_count": 0}
    violations = [
        {"invariant": item.invariant, "code": item.code, "detail": item.detail, "at": str(item.at)}
        for item in report.violations
    ]
    return {
        "status": "valid" if report.ok else "invalid",
        "violations_count": len(violations),
        "violations": violations,
        "coverage": "full-stay (checkin→room→outlet→room→checkout)",
        "segments": len(segments),
    }
