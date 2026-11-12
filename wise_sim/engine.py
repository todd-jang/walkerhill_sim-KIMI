"""WISE 시뮬레이션 엔진 레거시 호환 모듈.

L1 (l1_agent.py)과 L2 (l2_engine.py)의 심볼들을 통합 re-export합니다.
"""
from __future__ import annotations

try:
    from l1_agent import (
        Policy,
        Decision,
        BaseAgent,
        MockAgent,
        LLMAgent,
        OllamaCloudAgent,
        get_ollama_cloud_agent,
        get_agent,
        BATCH_SIZE,
        BASE_ROOM_PRICE,
    )
    from l2_engine import (
        TOTAL_ROOMS,
        TIME_COMPRESSION,
        policy_grid,
        evaluate_facility_policy,
        simulate_weekend,
        run_policy_grid,
    )
    from transit_matrix import TransitContext
except ImportError:
    from .l1_agent import (
        Policy,
        Decision,
        BaseAgent,
        MockAgent,
        LLMAgent,
        OllamaCloudAgent,
        get_ollama_cloud_agent,
        get_agent,
        BATCH_SIZE,
        BASE_ROOM_PRICE,
    )
    from .l2_engine import (
        TOTAL_ROOMS,
        TIME_COMPRESSION,
        policy_grid,
        evaluate_facility_policy,
        simulate_weekend,
        run_policy_grid,
    )
    from .transit_matrix import TransitContext

__all__ = [
    "Policy",
    "Decision",
    "BaseAgent",
    "MockAgent",
    "LLMAgent",
    "OllamaCloudAgent",
    "get_ollama_cloud_agent",
    "get_agent",
    "BATCH_SIZE",
    "BASE_ROOM_PRICE",
    "TOTAL_ROOMS",
    "TIME_COMPRESSION",
    "policy_grid",
    "TransitContext",
    "evaluate_facility_policy",
    "simulate_weekend",
    "run_policy_grid",
]
