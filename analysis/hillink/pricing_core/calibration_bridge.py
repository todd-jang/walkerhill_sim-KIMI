"""
Huang & Wang 최적화 엔진 (persona_exposure.py)과 Majidi 시뮬레이터의 연계 브릿지 모듈.

역할:
1. 실측 예약/매출 데이터로부터 Huang & Wang Algorithm 1 (MLE 심플렉스 투영 최적화)을 구동하여
   최적 페르소나 혼합 가중치 α̂과 시장 노출 N̂을 도출.
2. 도출된 α̂를 Majidi et al. 프레임워크의 Phase 1 아키타입 사전 가중치(wk)로 주입하여
   합성 인구 생성(Phase 2)의 실측 부합성을 획기적으로 개선.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

# hillink/research 경로 추가
RESEARCH_DIR = Path(__file__).resolve().parent.parent / "research"
if str(RESEARCH_DIR) not in sys.path:
    sys.path.insert(0, str(RESEARCH_DIR))

try:
    import persona_exposure
    HAS_PERSONA_EXPOSURE = True
except ImportError:
    HAS_PERSONA_EXPOSURE = False


class CalibrationBridge:
    """Huang & Wang 캘리브레이션 브릿지."""

    @staticmethod
    def is_available() -> bool:
        return HAS_PERSONA_EXPOSURE

    @staticmethod
    def calibrate_persona_weights(
        observations_data: List[Tuple[str, float, int]], # (product, price, demand)
        persona_probs_matrix: List[List[float]],          # [row_idx][persona_k_prob]
        persona_names: List[str],
        n_max: int = 150,
        truncated: bool = True,
        use_logit_calibration: bool = False,
    ) -> Dict[str, Any]:
        """
        Huang & Wang 알고리즘 실행:
        - returns: {"optimal_N": int, "weights": {persona_name: weight}, "objective_value": float}
        """
        if not HAS_PERSONA_EXPOSURE:
            raise RuntimeError("persona_exposure module not found in hillink/research")

        obs = [
            persona_exposure.Observation(product=p, price=pr, demand=d)
            for p, pr, d in observations_data
        ]

        if use_logit_calibration:
            fit_res = persona_exposure.fit_calibrated(
                obs,
                persona_probs_matrix,
                n_max=n_max,
                truncated=truncated,
            )
        else:
            fit_res = persona_exposure.fit(
                obs,
                persona_probs_matrix,
                n_max=n_max,
                truncated=truncated,
            )
        opt_n = fit_res.exposure
        alpha_vec = fit_res.weights
        v_val = fit_res.value

        weights: Dict[str, float] = {}
        for i, name in enumerate(persona_names):
            if i < len(alpha_vec):
                weights[name] = float(alpha_vec[i])

        # 마지막 차원은 no-purchase dummy (Huang & Wang 논문의 K+1 더미)
        dummy_weight = float(alpha_vec[-1]) if len(alpha_vec) > len(persona_names) else 0.0

        return {
            "optimal_N": int(opt_n),
            "weights": weights,
            "dummy_non_purchase_weight": dummy_weight,
            "objective_value": float(v_val),
        }

    @staticmethod
    def map_to_majidi_archetype_priors(
        calibrated_weights: Dict[str, float],
    ) -> Dict[str, float]:
        """
        워커힐 45개 페르소나 또는 임의의 세그먼트 가중치를
        Majidi의 4개 대분류 아키타입(Budget, Business, Luxury, Leisure)으로 매핑 및 정규화.
        """
        # 이미 4개 아키타입 키가 포함되어 있다면 해당 키 추출
        standard_keys = ["Budget", "Business", "Luxury", "Leisure"]
        priors: Dict[str, float] = {}

        has_standard = any(k in calibrated_weights for k in standard_keys)
        if has_standard:
            for k in standard_keys:
                priors[k] = max(0.001, calibrated_weights.get(k, 0.1))
        else:
            # 워커힐 페르소나 속성 키워드 기반 분류 매핑
            # 예: 커플/와인/호캉스 -> Leisure/Luxury, 비즈니스/단독 -> Business, 가성비/실속 -> Budget
            priors = {"Budget": 0.0, "Business": 0.0, "Luxury": 0.0, "Leisure": 0.0}
            for p_name, w in calibrated_weights.items():
                lower = p_name.lower()
                if any(x in lower for x in ["budget", "가성비", "실속", "casual", "smart"]):
                    priors["Budget"] += w
                elif any(x in lower for x in ["biz", "business", "출장", "office", "단기"]):
                    priors["Business"] += w
                elif any(x in lower for x in ["luxury", "스위트", "vip", "spa", "wine", "premium"]):
                    priors["Luxury"] += w
                else:
                    priors["Leisure"] += w

            # 최소 플로어 보장
            for k in standard_keys:
                priors[k] = max(0.01, priors[k])

        # 합이 1이 되도록 정규화
        total = sum(priors.values())
        return {k: round(v / total, 4) for k, v in priors.items()}
