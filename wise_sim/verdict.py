"""WISE 판정 및 검증 레거시 호환 모듈.

L3 (l3_verdict.py)의 통계 검증 심볼들을 re-export합니다.
"""
from __future__ import annotations

from l3_verdict import (
    T_DIRECTION,
    T_SPEARMAN,
    T_ABSTAIN,
    T_EPSILON_Z,
    T_FDR,
    T_UNFIT_EXCEED,
    sign,
    direction_accuracy,
    spearman_rho,
    cell_distance_d1,
    bh_fdr,
    binom_tail,
    wilson_ci,
    trace_test,
    verdict,
    full_report,
)

__all__ = [
    "T_DIRECTION",
    "T_SPEARMAN",
    "T_ABSTAIN",
    "T_EPSILON_Z",
    "T_FDR",
    "T_UNFIT_EXCEED",
    "sign",
    "direction_accuracy",
    "spearman_rho",
    "cell_distance_d1",
    "bh_fdr",
    "binom_tail",
    "wilson_ci",
    "trace_test",
    "verdict",
    "full_report",
]
