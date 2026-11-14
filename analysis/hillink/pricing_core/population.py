"""
합성 고객 인구 생성 모듈 (Synthetic Population Generation).

논문 Phase 2 (수식 8~15, Table I, Table II) 구현:
1. 일별 도착 고객 수 샘플링: Nd ~ Poisson(λd)
2. 리드타임 ℓi ~ Flead, 결정일자 ti = d - ℓi
3. 체류기간 τi ~ Ftrip
4. 동반인원 oi = (adults, children, infants) ~ Focc
5. 아키타입(Budget, Business, Luxury, Leisure) 소프트맥스 점수 계산 및 샘플링
   - Huang & Wang 최적화 가중치 α̂가 주어지면 사전확률 wk를 α̂로 대체
6. 충성도 등급(low, medium, high) 할당
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np


ARCHETYPES = ["Budget", "Business", "Luxury", "Leisure"]
LOYALTY_TIERS = ["low", "medium", "high"]


@dataclass
class CustomerAgent:
    """합성 고객 에이전트 프로필."""
    id: str
    target_stay_date: int          # 투숙 희망일 (d)
    lead_time: int                 # 리드타임 (ℓi)
    decision_day: int              # 예약 의사결정일 (ti = d - ℓi, 1..T)
    trip_length: int               # 체류 박수 (τi)
    adults: int                    # 성인 수
    children: int                  # 아동 수
    infants: int                   # 유아 수
    archetype: str                 # 아키타입 (Budget, Business, Luxury, Leisure)
    loyalty: str                   # 충성도 (low, medium, high)

    @property
    def total_occupants(self) -> int:
        return self.adults + self.children + self.infants

    @property
    def occupant_summary(self) -> str:
        if self.adults == 1 and self.children == 0:
            return "Solo adult"
        elif self.adults == 2 and self.children == 0:
            return "2 adults, no children"
        elif self.adults >= 1 and self.children > 0:
            if self.adults == 1:
                return "Single adult with children"
            return "Adults with children"
        return f"{self.adults} adults, {self.children} children"


class PopulationGenerator:
    """합성 인구 생성기."""

    def __init__(
        self,
        horizon: int = 31,
        seed: Optional[int] = 42,
        archetype_priors: Optional[Dict[str, float]] = None,
    ):
        self.horizon = horizon
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        if seed is not None:
            random.seed(seed)

        # 1. 아키타입 사전 가중치 wk (기본값: 논문 분포 기반 약 28:12:7:65 비율)
        # Huang & Wang 최적화 α̂가 전달되면 주입됨
        if archetype_priors:
            self.priors = dict(archetype_priors)
        else:
            self.priors = {
                "Budget": 0.25,
                "Business": 0.11,
                "Luxury": 0.06,
                "Leisure": 0.58,
            }

        # 정규화
        total_p = sum(self.priors.values())
        self.priors = {k: v / total_p for k, v in self.priors.items()}

    def _get_lead_time_score(self, lead_time: int) -> Dict[str, int]:
        """Table I: Lead time score contributions δ_{lead, k}."""
        if lead_time <= 3:
            return {"Budget": -1, "Business": 2, "Luxury": -1, "Leisure": -1}
        elif 4 <= lead_time <= 14:
            return {"Budget": 0, "Business": 1, "Luxury": 0, "Leisure": 0}
        elif 15 <= lead_time <= 30:
            return {"Budget": 1, "Business": -1, "Luxury": 1, "Leisure": 1}
        else:  # > 30
            return {"Budget": 2, "Business": -2, "Luxury": 1, "Leisure": 1}

    def _get_trip_length_score(self, trip_length: int) -> Dict[str, int]:
        """Table I: Trip length score contributions δ_{trip, k}."""
        if trip_length == 1:
            return {"Budget": -1, "Business": 2, "Luxury": -1, "Leisure": -1}
        elif 2 <= trip_length <= 3:
            return {"Budget": 0, "Business": 0, "Luxury": 0, "Leisure": 1}
        elif 4 <= trip_length <= 7:
            return {"Budget": 0, "Business": -1, "Luxury": 1, "Leisure": 2}
        else:  # > 7
            return {"Budget": 1, "Business": -2, "Luxury": 2, "Leisure": 2}

    def _get_occupant_score(self, adults: int, children: int) -> Dict[str, int]:
        """Table I: Occupant score contributions δ_{occ, k}."""
        if adults == 1 and children == 0:
            return {"Budget": 1, "Business": 1, "Luxury": -1, "Leisure": 0}
        elif adults == 2 and children == 0:
            return {"Budget": 0, "Business": 1, "Luxury": 1, "Leisure": 1}
        elif adults >= 2 and children > 0:
            return {"Budget": -1, "Business": -2, "Luxury": 2, "Leisure": 2}
        elif adults == 1 and children > 0:
            return {"Budget": 1, "Business": -1, "Luxury": -1, "Leisure": 1}
        else:
            return {"Budget": 0, "Business": 0, "Luxury": 0, "Leisure": 0}

    def compute_archetype_probabilities(
        self,
        lead_time: int,
        trip_length: int,
        adults: int,
        children: int,
    ) -> Dict[str, float]:
        """
        논문 수식 (13) & (14):
        s_{i,k} = log w_k + sum_j δ_{j,k}(obs_i)
        P(k | obs_i) = exp(s_{i,k}) / sum_{k'} exp(s_{i,k'})
        """
        s_lead = self._get_lead_time_score(lead_time)
        s_trip = self._get_trip_length_score(trip_length)
        s_occ = self._get_occupant_score(adults, children)

        scores: Dict[str, float] = {}
        for k in ARCHETYPES:
            w_k = max(1e-6, self.priors.get(k, 0.25))
            score = math.log(w_k) + s_lead[k] + s_trip[k] + s_occ[k]
            scores[k] = score

        # Softmax
        max_s = max(scores.values())
        exp_scores = {k: math.exp(v - max_s) for k, v in scores.items()}
        sum_exp = sum(exp_scores.values())
        probs = {k: v / sum_exp for k, v in exp_scores.items()}
        return probs

    def sample_archetype(self, probs: Dict[str, float]) -> str:
        """사후 확률 분포로부터 아키타입 샘플링."""
        keys = list(probs.keys())
        p_vals = [probs[k] for k in keys]
        return str(self.rng.choice(keys, p=p_vals))

    def sample_loyalty(self) -> str:
        """논문 분포: low (약 60.7%), medium (약 32.1%), high (약 7.1%)."""
        return str(self.rng.choice(LOYALTY_TIERS, p=[0.607, 0.321, 0.072]))

    def sample_lead_time(self) -> int:
        """Flead: 0일(당일) ~ 35일 분포 샘플링."""
        # 1~3일(30%), 4~14일(40%), 15~30일(20%), >30일(10%)
        tier = self.rng.choice([1, 2, 3, 4], p=[0.30, 0.40, 0.20, 0.10])
        if tier == 1:
            return int(self.rng.integers(0, 4))
        elif tier == 2:
            return int(self.rng.integers(4, 15))
        elif tier == 3:
            return int(self.rng.integers(15, 31))
        else:
            return int(self.rng.integers(31, 45))

    def sample_trip_length(self) -> int:
        """Ftrip: 1박(50%), 2~3박(35%), 4~7박(12%), >7박(3%)."""
        tier = self.rng.choice([1, 2, 3, 4], p=[0.50, 0.35, 0.12, 0.03])
        if tier == 1:
            return 1
        elif tier == 2:
            return int(self.rng.integers(2, 4))
        elif tier == 3:
            return int(self.rng.integers(4, 8))
        else:
            return int(self.rng.integers(8, 12))

    def sample_occupants(self) -> Tuple[int, int, int]:
        """Focc: Solo(40%), 2인 커플/동행(45%), 가족(어린이 동반 15%)."""
        tier = self.rng.choice([1, 2, 3], p=[0.40, 0.45, 0.15])
        if tier == 1:
            return 1, 0, 0
        elif tier == 2:
            return 2, 0, 0
        else:
            adults = int(self.rng.integers(1, 3))
            children = int(self.rng.integers(1, 3))
            return adults, children, 0

    def generate_population(
        self,
        target_total_agents: int = 112,
        daily_arrival_rates: Optional[List[float]] = None,
    ) -> List[CustomerAgent]:
        """
        논문 Algorithm 1 Phase 2:
        - daily_arrival_rates(λd)가 제공되면 Poisson(λd)로 날짜별 인원 생성
        - 없으면 논문 기본 설정인 총 112명을 수평선에 걸쳐 분배
        """
        agents: List[CustomerAgent] = []
        agent_idx = 1

        if daily_arrival_rates is not None:
            for day, lam in enumerate(daily_arrival_rates, start=1):
                n_d = self.rng.poisson(lam)
                for _ in range(n_d):
                    lead_time = self.sample_lead_time()
                    decision_day = max(1, min(self.horizon, day - lead_time))
                    trip_length = self.sample_trip_length()
                    adults, children, infants = self.sample_occupants()

                    probs = self.compute_archetype_probabilities(lead_time, trip_length, adults, children)
                    archetype = self.sample_archetype(probs)
                    loyalty = self.sample_loyalty()

                    agents.append(
                        CustomerAgent(
                            id=f"agent_{agent_idx:04d}",
                            target_stay_date=day,
                            lead_time=lead_time,
                            decision_day=decision_day,
                            trip_length=trip_length,
                            adults=adults,
                            children=children,
                            infants=infants,
                            archetype=archetype,
                            loyalty=loyalty,
                        )
                    )
                    agent_idx += 1
        else:
            # 기본 112명 고정 생성 (논문 표본 수와 비율 정합)
            for _ in range(target_total_agents):
                target_day = int(self.rng.integers(1, self.horizon + 1))
                lead_time = self.sample_lead_time()
                decision_day = max(1, min(self.horizon, target_day - lead_time))
                trip_length = self.sample_trip_length()
                adults, children, infants = self.sample_occupants()

                probs = self.compute_archetype_probabilities(lead_time, trip_length, adults, children)
                archetype = self.sample_archetype(probs)
                loyalty = self.sample_loyalty()

                agents.append(
                    CustomerAgent(
                        id=f"agent_{agent_idx:04d}",
                        target_stay_date=target_day,
                        lead_time=lead_time,
                        decision_day=decision_day,
                        trip_length=trip_length,
                        adults=adults,
                        children=children,
                        infants=infants,
                        archetype=archetype,
                        loyalty=loyalty,
                    )
                )
                agent_idx += 1

        return agents
