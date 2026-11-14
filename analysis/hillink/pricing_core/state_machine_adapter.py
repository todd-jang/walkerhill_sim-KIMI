"""
워커힐 Guest State Machine (M1~M11) 및 24시간 타임라인 검증기 어댑터.

역할:
1. Majidi 시뮬레이터에서 예약에 성공한 고객(BookingRecord)을
   워커힐 상태기계(M1 Reservation, M2 Stay, M3 Folio, M7 OutletSession) 이벤트로 변환.
2. timeline/walkerhill_timeline_validator.py 와 연동하여
   StayDay [15:00, 익일 11:00] 정합성 및 6대 불변식(I1~I6) 통과 여부를 검증.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .environment import BookingRecord

# timeline 모듈 경로 추가
TIMELINE_DIR = Path(__file__).resolve().parent.parent.parent / "timeline"
if str(TIMELINE_DIR) not in sys.path:
    sys.path.insert(0, str(TIMELINE_DIR))

try:
    import walkerhill_timeline_validator as validator
    HAS_VALIDATOR = True
except ImportError:
    HAS_VALIDATOR = False


class GuestStateMachineAdapter:
    """Guest State Machine 및 타임라인 연계 어댑터."""

    @staticmethod
    def is_validator_available() -> bool:
        return HAS_VALIDATOR

    @staticmethod
    def booking_to_state_machine_events(booking: BookingRecord) -> Dict[str, Any]:
        """
        예약 기록을 워커힐 11개 상태기계의 주요 라이프사이클 이벤트로 사상:
        - M1 (ReservationMachine): INQUIRY -> OPTION_HELD -> GUARANTEED -> CONFIRMED
        - M2 (StayMachine): PRE_ARRIVAL -> ARRIVED_ON_PROPERTY -> CHECKED_IN -> IN_ROOM -> CHECKED_OUT
        - M3 (FolioMachine): FOLIO_OPENED -> ROOM_CHARGE_POSTED -> PAYMENT_SETTLED -> FOLIO_CLOSED
        """
        # 기준 체크인/체크아웃 날짜 생성 (2026-10-xx 기준)
        base_year = 2026
        base_month = 10
        day_clamped = min(28, max(1, booking.stay_date_start))
        checkin_date = date(base_year, base_month, day_clamped)
        checkout_date = checkin_date + timedelta(days=booking.trip_length)

        events = {
            "agent_id": booking.agent_id,
            "persona": booking.persona,
            "loyalty": booking.loyalty,
            "category_id": booking.category_id,
            "checkin_date": str(checkin_date),
            "checkout_date": str(checkout_date),
            "stay_nights": booking.trip_length,
            "m1_reservation": {
                "initial_state": "INQUIRY",
                "final_state": "CONFIRMED",
                "channel": "DIRECT_WEB" if booking.loyalty == "high" else "OTA",
                "guaranteed": True,
            },
            "m2_stay": {
                "arrival_time": f"{checkin_date} 15:00:00",
                "checkin_state": "REGISTRATION_SIGNED",
                "room_assigned": f"ROOM_{booking.category_id:03d}_101",
                "departure_time": f"{checkout_date} 11:00:00",
                "final_state": "CHECKED_OUT",
            },
            "m3_folio": {
                "folio_id": f"FOLIO_{booking.agent_id}_{booking.stay_date_start}",
                "room_charge_total": booking.total_price,
                "price_per_night": booking.price_per_night,
                "currency": "KRW",
                "status": "SETTLED",
            },
        }
        return events

    @staticmethod
    def validate_booking_timeline(
        booking: BookingRecord,
        location: str = "그랜드_디럭스",
    ) -> Dict[str, Any]:
        """
        timeline/walkerhill_timeline_validator.py 의 validate_walkerhill_timeline 을 호출하여
        해당 투숙객의 24시간 타임라인 불변식 검증 수행.
        """
        if not HAS_VALIDATOR:
            return {
                "status": "skipped",
                "reason": "walkerhill_timeline_validator module not available",
            }

        # 2026-10-15 기준 투숙 타임라인 세그먼트 생성
        base_date = date(2026, 10, 15)
        # StayDay [15:00 ~ 익일 11:00]
        start_dt = datetime.combine(base_date, time(15, 0))
        end_dt = datetime.combine(base_date + timedelta(days=booking.trip_length), time(11, 0))

        # 표준 투숙 세그먼트: 체크인 후 객실 체류
        segments = [
            validator.TimelineSegment(
                state="ROOM_OCCUPIED_PRESENT",
                location=location,
                t_in=start_dt,
                t_out=end_dt,
            )
        ]

        try:
            report = validator.validate_walkerhill_timeline(
                segments=segments,
                nights=max(1, booking.trip_length),
            )
            return {
                "status": "valid" if report.ok else "invalid",
                "violations_count": len(report.violations),
                "violations": [
                    {
                        "invariant": v.invariant,
                        "code": v.code,
                        "detail": v.detail,
                        "at": str(v.at),
                    }
                    for v in report.violations
                ],
                "total_duration_hours": round((end_dt - start_dt).total_seconds() / 3600.0, 2),
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
            }
