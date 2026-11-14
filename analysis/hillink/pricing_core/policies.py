"""
가격 책정 정책 (Pricing Policies) 모듈.

1. BasePolicy (추상 기반 클래스)
2. StaticPolicy: 과거 평균 가격 pc 고정 적용 (수식 19)
3. OccupancyDynamicPolicy: 실시간 점유율 기반 피드백 동적 가격 정책 (수식 20~22)
4. RuleBasedPolicy: 지불의사금액(WTP) 임계값 기반 규칙 베이스라인 (수식 16~17)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from .environment import RoomCategory, InventoryMatrix


class BasePolicy(ABC):
    """가격 책정 정책 기본 인터페이스."""

    @abstractmethod
    def get_prices(
        self,
        day: int,
        inventory: InventoryMatrix,
        competitor_prices: Dict[int, float],
    ) -> Dict[int, float]:
        """
        day(t) 시점에 각 카테고리별 제시 가격을 계산하여 반환.
        Returns: {category_id: price_for_category}
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass


class StaticPolicy(BasePolicy):
    """
    정적 가격 정책 (Static Flat Pricing) — 논문 수식 (19).
    과거 평균 기준가(base_static_price)를 시뮬레이션 전 기간 동안 불변으로 유지.
    """

    def __init__(self, categories: List[RoomCategory], custom_prices: Optional[Dict[int, float]] = None):
        self._prices: Dict[int, float] = {}
        for c in categories:
            if custom_prices and c.id in custom_prices:
                self._prices[c.id] = float(custom_prices[c.id])
            else:
                self._prices[c.id] = float(c.base_static_price)

    @property
    def name(self) -> str:
        return "Static Flat Pricing (Policy A)"

    def get_prices(
        self,
        day: int,
        inventory: InventoryMatrix,
        competitor_prices: Dict[int, float],
    ) -> Dict[int, float]:
        return dict(self._prices)


class OccupancyDynamicPolicy(BasePolicy):
    """
    점유율 기반 동적 가격 정책 (Occupancy-Based Dynamic Pricing) — 논문 수식 (20)~(22).
    p_{c,t} = p_{base,c} * f(ρ_{c,t})
    f(ρ) = max(f_min, 1 + β * (ρ - ρ_target))
    """

    def __init__(
        self,
        categories: List[RoomCategory],
        rho_target: float = 0.5,
        beta: float = 0.4,
        f_min: float = 0.65,
        use_competitor_as_base: bool = True,
    ):
        self.categories = {c.id: c for c in categories}
        self.rho_target = rho_target
        self.beta = beta
        self.f_min = f_min
        self.use_competitor_as_base = use_competitor_as_base

    @property
    def name(self) -> str:
        return "Occupancy-Based Dynamic Pricing (Policy B)"

    def get_multiplier(self, rho: float) -> float:
        """점유율 승수 f(ρ) 계산 (수식 22)."""
        mult = 1.0 + self.beta * (rho - self.rho_target)
        return max(self.f_min, mult)

    def get_prices(
        self,
        day: int,
        inventory: InventoryMatrix,
        competitor_prices: Dict[int, float],
    ) -> Dict[int, float]:
        prices: Dict[int, float] = {}
        for cat_id, cat in self.categories.items():
            # 1. Base price 선정 (경쟁사 가격 or 카테고리 기본가)
            if self.use_competitor_as_base and cat_id in competitor_prices:
                p_base = competitor_prices[cat_id]
            else:
                p_base = cat.default_competitor_price

            # 2. 당일 해당 카테고리 점유율 산출 (수식 20)
            rho = inventory.calculate_occupancy_ratio(cat_id, day)

            # 3. 승수 적용 (수식 21)
            mult = self.get_multiplier(rho)
            prices[cat_id] = round(p_base * mult, 2)

        return prices


class RuleBasedPolicy(BasePolicy):
    """
    지불의사금액(WTP) 기반 규칙 베이스라인 (논문 Section III.F 수식 16~17).
    에이전트가 WTP 임계값 규칙을 따를 때의 가격 제공자.
    """

    def __init__(self, categories: List[RoomCategory], markup_ratio: float = 1.0):
        self.categories = {c.id: c for c in categories}
        self.markup_ratio = markup_ratio

    @property
    def name(self) -> str:
        return f"Rule-Based Pricing (Markup={self.markup_ratio:.2f})"

    def get_prices(
        self,
        day: int,
        inventory: InventoryMatrix,
        competitor_prices: Dict[int, float],
    ) -> Dict[int, float]:
        prices: Dict[int, float] = {}
        for cat_id, cat in self.categories.items():
            comp_price = competitor_prices.get(cat_id, cat.default_competitor_price)
            prices[cat_id] = round(comp_price * self.markup_ratio, 2)
        return prices
