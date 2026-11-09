"""L3: 검증 및 판정 파이프라인 (Statistical Verification & Verdict).

체크시트(Walkerhill_완수평가_체크시트.xlsx)의 통계 수식을 Scipy 및 Statsmodels를 활용하여 검증.
- 방향 적중률: 정책 효과 부호(+/-/0) 일치율 (기준 ≥ 70%)
- 순위 상관: Spearman ρ (기준 ≥ 0.6) — scipy.stats.spearmanr 연동
- Trace 분포 검정: 셀별 D₁, ε_z(가격 0.22, 전환 0.12, 페이싱 0.19) 기준 초과 여부
- 다중비교 보정: Benjamini–Hochberg FDR 5% — statsmodels.stats.multitest 연동 (폴백 내장)
- 백테스트 확률: 이항 분포 꼬리 확률(scipy.stats.binomtest) + Wilson 95% 신뢰구간
- 3단 판정: 적합 / 조건부 / 부적합
"""
from __future__ import annotations

import math
from itertools import combinations
from typing import Sequence

# Scipy 및 Statsmodels 임포트 시도 (미설치 시 순수 수학 내장 함수로 무결성 유지)
try:
    import scipy.stats as sp_stats
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

try:
    from statsmodels.stats.multitest import multipletests as sm_multipletests
    from statsmodels.stats.proportion import proportion_confint as sm_proportion_confint
    _HAS_STATSMODELS = True
except ImportError:
    _HAS_STATSMODELS = False


# 임계값 — '기준 요약' 시트 기본값 (협의 가능)
T_DIRECTION = 0.70
T_SPEARMAN = 0.60
T_ABSTAIN = 0.20
T_EPSILON_Z = 0.22      # 가격 셀 가정치 (A/A 30쌍 후 재산정)
T_FDR = 0.05
T_UNFIT_EXCEED = 3      # 초과 3건 이상 → 부적합


def sign(x: float, deadzone: float = 0.01) -> int:
    """부호 판정: 데드존 이내는 0, 초과는 +1, 미만은 -1."""
    return 0 if abs(x) < deadzone else (1 if x > 0 else -1)


def direction_accuracy(sim_effects: list[float], real_effects: list[float]) -> float:
    """정책 효과 방향(+/−/0) 일치율 (기준 ≥ 70%)."""
    assert len(sim_effects) == len(real_effects) and len(sim_effects) > 0, "효과 리스트 길이가 동일하고 0보다 커야 합니다."
    hits = sum(1 for s, r in zip(sim_effects, real_effects)
               if sign(s) == sign(r))
    return hits / len(sim_effects)


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> float:
    """순위 상관 계수 Spearman ρ (기준 ≥ 0.6).
    
    scipy.stats.spearmanr가 사용 가능하면 우선 활용, 아닐 경우 순수 파이썬 구현 사용.
    """
    assert len(x) == len(y) and len(x) > 0
    if _HAS_SCIPY:
        res = sp_stats.spearmanr(x, y)
        val = float(res.correlation if hasattr(res, "correlation") else res[0])
        return 0.0 if math.isnan(val) else val

    # Fallback 순수 구현
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for rank_i, idx in enumerate(order):
            r[idx] = float(rank_i)
        return r

    rx, ry = rank(x), rank(y)
    n = len(x)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx * vy > 0 else 0.0


def cell_distance_d1(sim_dist: Sequence[float], real_dist: Sequence[float]) -> float:
    """두 이산 확률분포 간 총변동거리 (Total Variation Distance, L₁/2)."""
    assert len(sim_dist) == len(real_dist)
    return sum(abs(a - b) for a, b in zip(sim_dist, real_dist)) / 2.0


def bh_fdr(p_values: list[float], alpha: float = T_FDR) -> list[bool]:
    """Benjamini–Hochberg 다중비교 보정 (FDR 5%). True = 유의(초과).
    
    statsmodels.stats.multitest.multipletests 우선 사용, 부재 시 표준 알고리즘 구현 사용.
    """
    if not p_values:
        return []
    
    if _HAS_STATSMODELS:
        rejected, _, _, _ = sm_multipletests(p_values, alpha=alpha, method="fdr_bh")
        return [bool(r) for r in rejected]

    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    significant = [False] * m
    k_max = -1
    for k, idx in enumerate(order, start=1):
        if p_values[idx] <= (k / m) * alpha:
            k_max = k
    if k_max > 0:
        for k, idx in enumerate(order, start=1):
            if k <= k_max:
                significant[idx] = True
    return significant


def binom_tail(n: int, k: int, p: float = 0.5) -> float:
    """우연히 k번 이상 적중할 누적 확률 P(X ≥ k).
    
    scipy.stats.binomtest 우선 사용, 부재 시 조합(math.comb) 누적합 사용.
    """
    if k > n:
        return 0.0
    if k <= 0:
        return 1.0

    if _HAS_SCIPY and hasattr(sp_stats, "binomtest"):
        res = sp_stats.binomtest(k, n, p, alternative="greater")
        return float(res.pvalue)

    return sum(math.comb(n, i) * (p ** i) * ((1.0 - p) ** (n - i))
               for i in range(k, n + 1))


def wilson_ci(k: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Wilson Score 95% 신뢰구간 ('백테스트 확률' 시트 동일 수식)."""
    if n == 0:
        return 0.0, 1.0

    if _HAS_STATSMODELS:
        lo, hi = sm_proportion_confint(k, n, alpha=1.0 - confidence, method="wilson")
        return float(lo), float(hi)

    z = 1.96  # 95% 신뢰수준
    phat = k / n
    denom = 1.0 + (z * z) / n
    center = (phat + (z * z) / (2.0 * n)) / denom
    half = (z * math.sqrt((phat * (1.0 - phat) / n) + ((z * z) / (4.0 * n * n)))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def trace_test(sim_cells: list[dict], real_cells: list[dict],
               epsilon_z: float = T_EPSILON_Z) -> dict:
    """셀별 D₁ 계산 → ε_z 초과 여부 → BH-FDR 보정."""
    d1s, pvals = [], []
    for sc, rc in zip(sim_cells, real_cells):
        d1 = cell_distance_d1(sc["dist"], rc["dist"])
        # 표본 N=400 가정의 정규 근사 p값 (A/A 30쌍 후 순열검정으로 교체 권장)
        sigma = math.sqrt(2.0 * (1.0 / 400.0))
        z = (d1 - epsilon_z) / max(sigma, 1e-9)
        p = 0.5 * math.erfc(z / math.sqrt(2.0))
        d1s.append(round(d1, 4))
        pvals.append(min(max(p, 0.0), 1.0))
    exceed_flags = bh_fdr(pvals)
    exceed_cells = [i for i, f in enumerate(exceed_flags)
                    if f and d1s[i] > epsilon_z]
    return {
        "d1": d1s,
        "p": [round(p, 4) for p in pvals],
        "exceed_cells": exceed_cells,
        "n_exceed": len(exceed_cells),
        "n_cells": len(sim_cells),
    }


def verdict(direction: float, n_exceed: int) -> tuple[str, str]:
    """3단 판정 + 인용 지침 ('기준 요약'·'판정 계산기' 시트 로직).
    
    - 적합: 방향 적중률 ≥ 70% AND 초과 셀 0건
    - 조건부: 방향 적중률 ≥ 70% AND 초과 셀 1~2건
    - 부적합: 방향 적중률 < 70% OR 초과 셀 3건 이상
    """
    if direction < T_DIRECTION or n_exceed >= T_UNFIT_EXCEED:
        return "부적합", "인용 불가, 결과 폐기"
    if n_exceed == 0:
        return "적합", "전체 지표 보고"
    return "조건부", "방향만 인용, 초과 항목 명시"


def full_report(direction: float, rho: float, abstain: float,
                trace_result: dict, n_weekends: int, n_hits: int,
                induction_resist: float | None = None) -> dict:
    """판정 계산기 + 백테스트 확률 시트를 종합 검증 리포트로 산출."""
    verdict_label, citation = verdict(direction, trace_result["n_exceed"])
    lo, hi = wilson_ci(n_hits, n_weekends)
    metrics = {
        "방향 적중률": (direction, T_DIRECTION, ">=", direction >= T_DIRECTION),
        "순위 상관 ρ": (rho, T_SPEARMAN, ">=", rho >= T_SPEARMAN),
        "기권률": (abstain, T_ABSTAIN, "<=", abstain <= T_ABSTAIN),
    }
    if induction_resist is not None:
        metrics["유도저항"] = (induction_resist, 0.85, ">=", induction_resist >= 0.85)

    return {
        "판정": verdict_label,
        "인용 지침": citation,
        "엔진 통계 라이브러리": {
            "scipy": _HAS_SCIPY,
            "statsmodels": _HAS_STATSMODELS,
        },
        "보고 지표": {
            k: {
                "값": round(v[0], 4),
                "기준": f"{v[2]}{v[1]}",
                "통과": v[3]
            } for k, v in metrics.items()
        },
        "trace": trace_result,
        "백테스트": {
            "n": n_weekends,
            "적중 k": n_hits,
            "적중률": round(n_hits / max(n_weekends, 1), 4),
            "우연 확률 P(X≥k)": round(binom_tail(n_weekends, n_hits), 4),
            "Wilson 95% CI": [round(lo, 4), round(hi, 4)],
            "70% 기준이 구간 내": lo <= T_DIRECTION <= hi,
        },
    }
