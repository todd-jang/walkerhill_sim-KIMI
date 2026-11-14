"""
hillink pricing_core 패키지.

LLM 기반 호텔 동적 가격 정책 시뮬레이션 및 반사실적 평가 (Majidi et al. 2026) 구현체.
"""

from .environment import (
    RoomCategory,
    BookingRecord,
    InventoryMatrix,
    get_twin_peaks_categories,
    get_walkerhill_categories,
)
from .policies import (
    BasePolicy,
    StaticPolicy,
    OccupancyDynamicPolicy,
    RuleBasedPolicy,
)
from .population import (
    CustomerAgent,
    PopulationGenerator,
    ARCHETYPES,
    LOYALTY_TIERS,
)
from .llm_client import (
    AgentDecision,
    LLMDecisionClient,
    ARCHETYPE_PROMPTS,
    LOYALTY_PROMPTS,
)
from .simulator import (
    SimulationMetrics,
    ComparativeEvaluationResult,
    HotelPricingSimulator,
    ComparativeEvaluator,
)

__all__ = [
    "RoomCategory",
    "BookingRecord",
    "InventoryMatrix",
    "get_twin_peaks_categories",
    "get_walkerhill_categories",
    "BasePolicy",
    "StaticPolicy",
    "OccupancyDynamicPolicy",
    "RuleBasedPolicy",
    "CustomerAgent",
    "PopulationGenerator",
    "ARCHETYPES",
    "LOYALTY_TIERS",
    "AgentDecision",
    "LLMDecisionClient",
    "ARCHETYPE_PROMPTS",
    "LOYALTY_PROMPTS",
    "SimulationMetrics",
    "ComparativeEvaluationResult",
    "HotelPricingSimulator",
    "ComparativeEvaluator",
]
