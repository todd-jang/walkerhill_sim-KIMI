"""
호텔 환경 및 객실 재고 매트릭스 모델링.

- Twin Peaks Lakeside Inn (13개 카테고리, 36실 - 논문 Table III)
- Walkerhill Hotel & Resort (그랜드 507실, 비스타 244실, 더글라스 50실 - 총 801실)
- 소멸성 재고 매트릭스 A[c, d] 및 가용성 조회/예약/차감 관리
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import copy


@dataclass
class RoomCategory:
    """객실 카테고리 메타데이터."""
    id: int
    name: str
    max_occ: int
    units: int
    features: str
    base_static_price: float       # 정적 기준가 (pc)
    default_competitor_price: float # 경쟁사 기본가 (Cc)


@dataclass
class BookingRecord:
    """확정된 예약 기록."""
    agent_id: str
    category_id: int
    stay_date_start: int           # 체크인 일자 (1-based, 1..T)
    trip_length: int               # 체류 박수 (τi)
    price_per_night: float         # 1박당 적용 요금
    total_price: float             # 총 결제액
    decision_step: int             # 예약 결정 일자 (ti)
    persona: str                   # 고객 페르소나 아키타입
    loyalty: str                   # 충성도 등급


class InventoryMatrix:
    """
    소멸성 재고 매트릭스 A[c, d].
    c: 객실 카테고리 ID
    d: 투숙일 (1..T)
    """

    def __init__(self, categories: List[RoomCategory], horizon: int):
        self.categories: Dict[int, RoomCategory] = {c.id: c for c in categories}
        self.horizon = horizon
        # availability[category_id][day] = 남은 잔여 객실 수
        self.availability: Dict[int, List[int]] = {
            c.id: [c.units for _ in range(horizon + 1)]  # 1-based indexing
            for c in categories
        }
        self.bookings: List[BookingRecord] = []

    def get_capacity(self, category_id: int) -> int:
        return self.categories[category_id].units

    def get_available_units(self, category_id: int, day: int) -> int:
        if 1 <= day <= self.horizon:
            return self.availability[category_id][day]
        return 0

    def is_available(self, category_id: int, stay_date_start: int, trip_length: int) -> bool:
        """체류 전 기간 동안 해당 카테고리 잔여 객실이 1개 이상 존재하는지 확인."""
        for d in range(stay_date_start, stay_date_start + trip_length):
            if d > self.horizon:
                # 시뮬레이션 수평선을 넘어가는 일정은 가용하다고 보거나 수평선 내만 확인
                continue
            if self.availability[category_id][d] <= 0:
                return False
        return True

    def calculate_occupancy_ratio(self, category_id: int, day: int) -> float:
        """
        논문 수식 (20):
        ρ_{c,t} = (capacity_c - availability_{c,t}) / capacity_c
        """
        cap = self.get_capacity(category_id)
        if cap <= 0:
            return 0.0
        avail = self.get_available_units(category_id, day)
        return max(0.0, min(1.0, (cap - avail) / cap))

    def calculate_total_occupancy_ratio(self, day: int) -> float:
        """호텔 전체 객실의 특정 날짜 점유율."""
        total_cap = sum(c.units for c in self.categories.values())
        if total_cap <= 0:
            return 0.0
        total_avail = sum(self.get_available_units(c.id, day) for c in self.categories.values())
        return max(0.0, min(1.0, (total_cap - total_avail) / total_cap))

    def book(
        self,
        agent_id: str,
        category_id: int,
        stay_date_start: int,
        trip_length: int,
        price_per_night: float,
        decision_step: int,
        persona: str,
        loyalty: str,
    ) -> bool:
        """예약 확정 및 재고 차감."""
        if not self.is_available(category_id, stay_date_start, trip_length):
            return False

        # 재고 차감
        for d in range(stay_date_start, stay_date_start + trip_length):
            if d <= self.horizon:
                self.availability[category_id][d] -= 1

        total_price = price_per_night * trip_length
        self.bookings.append(
            BookingRecord(
                agent_id=agent_id,
                category_id=category_id,
                stay_date_start=stay_date_start,
                trip_length=trip_length,
                price_per_night=price_per_night,
                total_price=total_price,
                decision_step=decision_step,
                persona=persona,
                loyalty=loyalty,
            )
        )
        return True

    def clone(self) -> InventoryMatrix:
        """독립적인 A/B 테스트를 위한 깊은 복사."""
        cloned = InventoryMatrix(list(self.categories.values()), self.horizon)
        cloned.availability = copy.deepcopy(self.availability)
        cloned.bookings = copy.deepcopy(self.bookings)
        return cloned


# ==========================================================
# 프리셋 정의
# ==========================================================

def get_twin_peaks_categories() -> List[RoomCategory]:
    """논문 Table III: Twin Peaks Lakeside Inn (13 categories, 36 units)."""
    return [
        RoomCategory(1, "Downstairs 1 Bedroom Unit", 4, 2, "Ground floor, no lake view, queen + sofa bed, kitchenette, accessible shower", 210.0, 225.0),
        RoomCategory(2, "Downstairs 1 Bedroom Unit A", 4, 2, "Ground floor, partial lake view, queen + 2 singles, accessible shower", 215.0, 230.0),
        RoomCategory(3, "Family Unit", 4, 4, "Up/downstairs, lake view option, queen + sofa + 2 singles, 2-element kitchenette", 230.0, 245.0),
        RoomCategory(4, "Ground Floor Double Studio Accessible", 2, 1, "Ground floor, queen bed, wheelchair-accessible wet floor shower", 195.0, 205.0),
        RoomCategory(5, "Ground Floor Double Studio Spa", 2, 1, "Ground floor, queen bed, double spa bath + walk-in shower", 220.0, 235.0),
        RoomCategory(6, "Ground Floor Studio", 2, 5, "Ground floor, queen + single bed, shower over bath", 190.0, 200.0),
        RoomCategory(7, "Lake View Studio King Bed", 2, 4, "Ground floor, lake-facing double-glazed windows, super king bed, kitchenette", 240.0, 260.0),
        RoomCategory(8, "Upstairs Lakeview 1 Bedroom Unit", 4, 4, "Upstairs, lake view, queen + 2 singles, spa bath option", 250.0, 270.0),
        RoomCategory(9, "One Bedroom Suite with Spa", 4, 2, "Ground floor, partial lake view, queen + 2 singles, double spa bath", 265.0, 285.0),
        RoomCategory(10, "Queen Spa Studio", 2, 2, "Upstairs, lake view, queen bed, double spa bath", 235.0, 250.0),
        RoomCategory(11, "Upstairs 2 Bedroom Unit", 5, 1, "Upstairs, king + queen + sofa bed, walk-in shower, sandwich maker", 280.0, 310.0),
        RoomCategory(12, "Upstairs 3 Bedroom Unit", 6, 1, "Upstairs, partial lake view, queen * 2 + 2 king singles, double spa bath", 350.0, 390.0),
        RoomCategory(13, "Upstairs Studio", 3, 6, "Upstairs, partial lake view, queen + single, walk-in shower", 205.0, 218.0),
    ]


def get_walkerhill_categories() -> List[RoomCategory]:
    """워커힐 호텔 3개 건물 801실 온톨로지 프리셋."""
    return [
        # 그랜드 워커힐 서울 (507실)
        RoomCategory(101, "Grand Deluxe Room", 2, 240, "Grand Walkerhill standard deluxe, mountain/river view, high comfort", 280000.0, 300000.0),
        RoomCategory(102, "Grand Club Suite", 3, 180, "Grand Walkerhill club lounge access, luxury amenities, river view", 420000.0, 460000.0),
        RoomCategory(103, "Grand Family Suite", 4, 87, "Grand Walkerhill spacious family unit, 2 bedrooms, premium living space", 600000.0, 650000.0),
        # 비스타 워커힐 서울 (244실)
        RoomCategory(201, "Vista Deluxe Room", 2, 160, "Vista modern luxury, iconic Han-river view, stylish digital features", 370000.0, 400000.0),
        RoomCategory(202, "Vista Junior Suite", 3, 60, "Vista studio suite, rounded bed option, spa bath, scenic skyline", 550000.0, 600000.0),
        RoomCategory(203, "Vista Spa Suite", 2, 24, "Vista exclusive indoor whirlpool spa overlooking Han river, ultra luxury", 850000.0, 920000.0),
        # 더글라스 하우스 (50실)
        RoomCategory(301, "Douglas Deluxe Forest", 2, 35, "Douglas House quiet woodland retreat, private balcony, nature immersion", 260000.0, 280000.0),
        RoomCategory(302, "Douglas Suite", 2, 15, "Douglas House secluded luxury suite, fireplace concept, lounge snacks included", 450000.0, 490000.0),
    ]
