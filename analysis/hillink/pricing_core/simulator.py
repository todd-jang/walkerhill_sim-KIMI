"""
시뮬레이션 및 비교 평가 엔진 (Simulator & Comparative Evaluator).

논문:
- Algorithm 1: Simulation-Based Evaluation of Pricing Policies via Large Language Models
- Algorithm 2: Comparative Performance Analysis of Pricing Policies (Paired A/B Simulation)
- 수식 (1)~(7), (18)~(23) 및 Table IV 집계 지표 산출
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from .environment import RoomCategory, InventoryMatrix, BookingRecord
from .policies import BasePolicy, StaticPolicy, OccupancyDynamicPolicy
from .population import CustomerAgent, PopulationGenerator, ARCHETYPES, LOYALTY_TIERS
from .llm_client import LLMDecisionClient, AgentDecision


@dataclass
class SimulationMetrics:
    """단일 시뮬레이션 실행 결과 집계."""
    policy_name: str
    horizon: int
    total_revenue: float           # 총 수익 V(π)
    room_nights_sold: int          # 총 판매 객실 박수
    total_capacity_nights: int     # 전체 공급 가능 객실 박수 (총 객실 수 * T)
    occupancy_rate: float          # 평균 점유율 (%)
    adr: float                     # 객실 평균 단가 (Average Daily Rate = 총매출 / 판매박수)
    revpar: float                  # 가용 객실당 매출 (Revenue Per Available Room = 총매출 / 전체공급박수)
    booking_rate: float            # 전체 고객 예약 전환율 (%)
    total_agents: int              # 총 모의 고객 수
    total_booked_agents: int       # 실제 예약 완료 고객 수
    segment_booking_rates: Dict[str, float]  # 아키타입별 예약 전환율 (%)
    loyalty_booking_rates: Dict[str, float]  # 충성도별 예약 전환율 (%)
    category_sales_count: Dict[int, int]     # 카테고리별 예약 건수
    daily_revenue: List[float]               # 일별 매출 추이
    daily_occupancy: List[float]             # 일별 점유율 추이
    bookings: List[BookingRecord]            # 상세 예약 원장


@dataclass
class ComparativeEvaluationResult:
    """Algorithm 2 쌍체 비교 평가 결과."""
    policy_a_name: str
    policy_b_name: str
    num_trials: int
    mean_revenue_a: float
    mean_revenue_b: float
    mean_treatment_effect: float   # ΔV = V(B) - V(A)
    revenue_uplift_pct: float      # 매출 증가율 (%)
    std_treatment_effect: float    # 처리효과의 표준편차
    t_statistic: float
    p_value: float
    mean_occupancy_a: float
    mean_occupancy_b: float
    mean_adr_a: float
    mean_adr_b: float
    mean_revpar_a: float
    mean_revpar_b: float
    segment_uplift: Dict[str, float]  # 아키타입별 전환율 증감 (pp)
    trials_data: List[Dict[str, Any]]


class HotelPricingSimulator:
    """호텔 동적 가격 시뮬레이터 (Algorithm 1)."""

    def __init__(
        self,
        categories: List[RoomCategory],
        horizon: int = 31,
        alpha_reference: float = 0.5,
        llm_client: Optional[LLMDecisionClient] = None,
    ):
        self.categories = list(categories)
        self.horizon = horizon
        self.alpha_reference = alpha_reference
        self.llm_client = llm_client or LLMDecisionClient(mode="mock")

    def _compute_reference_prices(
        self,
        recent_prices: Dict[int, float],
        competitor_prices: Dict[int, float],
    ) -> Dict[int, float]:
        """
        논문 수식 (6):
        r_c = α * P^{recent}_c + (1 - α) * C_c
        """
        ref_prices: Dict[int, float] = {}
        for c in self.categories:
            p_rec = recent_prices.get(c.id, c.base_static_price)
            c_comp = competitor_prices.get(c.id, c.default_competitor_price)
            ref_prices[c.id] = self.alpha_reference * p_rec + (1.0 - self.alpha_reference) * c_comp
        return ref_prices

    def run_simulation(
        self,
        policy: BasePolicy,
        population: List[CustomerAgent],
        competitor_price_trajectory: Optional[Dict[int, Dict[int, float]]] = None,
        order_seed: Optional[int] = None,
    ) -> SimulationMetrics:
        """
        논문 Algorithm 1 실행:
        고정된 합성 인구(population)에 대해 정책(policy)을 1..T 일간 평가.
        """
        inventory = InventoryMatrix(self.categories, self.horizon)
        daily_revenue: List[float] = [0.0] * (self.horizon + 1)
        daily_occupancy: List[float] = [0.0] * (self.horizon + 1)

        # 결정일(decision_day)별 고객 그룹핑
        agents_by_day: Dict[int, List[CustomerAgent]] = {d: [] for d in range(1, self.horizon + 1)}
        for agent in population:
            d_day = max(1, min(self.horizon, agent.decision_day))
            agents_by_day[d_day].append(agent)

        recent_prices: Dict[int, float] = {c.id: c.base_static_price for c in self.categories}

        # Phase 3: 일별 순차 시뮬레이션
        for t in range(1, self.horizon + 1):
            # 1. 당일 경쟁사 가격
            comp_prices = (
                competitor_price_trajectory.get(t, {})
                if competitor_price_trajectory
                else {c.id: c.default_competitor_price for c in self.categories}
            )

            # 2. 가격 업데이트: Pt = π(st)
            current_prices = policy.get_prices(t, inventory, comp_prices)
            for c_id, p in current_prices.items():
                recent_prices[c_id] = p

            # 3. 참조 가격 계산 (수식 6)
            ref_prices = self._compute_reference_prices(recent_prices, comp_prices)

            # 4. 활성 에이전트 식별 및 순서 랜덤화
            #    order_seed가 주어지면 로컬 RNG로 고정 — paired A/B에서 A와 B가
            #    동일한 예약 경합 순서를 갖도록 보장 (δn = 순수 정책 효과)
            active_agents = list(agents_by_day[t])
            if order_seed is not None:
                random.Random(f"{order_seed}:{t}").shuffle(active_agents)
            else:
                random.shuffle(active_agents)

            # 5. 당일 잔여 객실 수 조회
            availability = {
                c.id: inventory.get_available_units(c.id, t)
                for c in self.categories
            }

            # 6. 배치 LLM 추론 실행
            decisions = self.llm_client.evaluate_batch(
                day=t,
                categories=self.categories,
                hotel_prices=current_prices,
                availability=availability,
                competitor_prices=comp_prices,
                reference_prices=ref_prices,
                active_agents=active_agents,
            )

            # 7. 결정 파싱 및 예약 처리
            agent_map = {a.id: a for a in active_agents}
            for dec in decisions:
                if dec.decision == "book" and dec.category_id is not None:
                    agent = agent_map.get(dec.agent_id)
                    if not agent:
                        continue
                    price_applied = current_prices.get(dec.category_id, 0.0)
                    success = inventory.book(
                        agent_id=agent.id,
                        category_id=dec.category_id,
                        stay_date_start=agent.target_stay_date,
                        trip_length=agent.trip_length,
                        price_per_night=price_applied,
                        decision_step=t,
                        persona=agent.archetype,
                        loyalty=agent.loyalty,
                    )

            # 당일 통계 기록
            daily_occupancy[t] = inventory.calculate_total_occupancy_ratio(t)

        # Phase 4: 사후 분석 (Post-Simulation Analytics)
        total_rev = sum(b.total_price for b in inventory.bookings)
        room_nights = sum(b.trip_length for b in inventory.bookings)
        total_rooms = sum(c.units for c in self.categories)
        total_cap_nights = total_rooms * self.horizon
        avg_occ = (room_nights / total_cap_nights) if total_cap_nights > 0 else 0.0
        adr = (total_rev / room_nights) if room_nights > 0 else 0.0
        revpar = (total_rev / total_cap_nights) if total_cap_nights > 0 else 0.0
        total_agents = len(population)
        total_booked = len(inventory.bookings)
        b_rate = (total_booked / total_agents * 100.0) if total_agents > 0 else 0.0

        # 세그먼트별 집계
        seg_booked: Dict[str, int] = {k: 0 for k in ARCHETYPES}
        seg_total: Dict[str, int] = {k: 0 for k in ARCHETYPES}
        loy_booked: Dict[str, int] = {k: 0 for k in LOYALTY_TIERS}
        loy_total: Dict[str, int] = {k: 0 for k in LOYALTY_TIERS}
        cat_sales: Dict[int, int] = {c.id: 0 for c in self.categories}

        for a in population:
            seg_total[a.archetype] = seg_total.get(a.archetype, 0) + 1
            loy_total[a.loyalty] = loy_total.get(a.loyalty, 0) + 1

        for b in inventory.bookings:
            seg_booked[b.persona] = seg_booked.get(b.persona, 0) + 1
            loy_booked[b.loyalty] = loy_booked.get(b.loyalty, 0) + 1
            cat_sales[b.category_id] = cat_sales.get(b.category_id, 0) + 1

        seg_rates = {
            k: (seg_booked[k] / seg_total[k] * 100.0) if seg_total[k] > 0 else 0.0
            for k in ARCHETYPES
        }
        loy_rates = {
            k: (loy_booked[k] / loy_total[k] * 100.0) if loy_total[k] > 0 else 0.0
            for k in LOYALTY_TIERS
        }

        # 일별 매출 분배
        for b in inventory.bookings:
            d_step = b.decision_step
            if 1 <= d_step <= self.horizon:
                daily_revenue[d_step] += b.total_price

        return SimulationMetrics(
            policy_name=policy.name,
            horizon=self.horizon,
            total_revenue=round(total_rev, 2),
            room_nights_sold=room_nights,
            total_capacity_nights=total_cap_nights,
            occupancy_rate=round(avg_occ * 100.0, 2),
            adr=round(adr, 2),
            revpar=round(revpar, 2),
            booking_rate=round(b_rate, 2),
            total_agents=total_agents,
            total_booked_agents=total_booked,
            segment_booking_rates=seg_rates,
            loyalty_booking_rates=loy_rates,
            category_sales_count=cat_sales,
            daily_revenue=daily_revenue[1:],
            daily_occupancy=daily_occupancy[1:],
            bookings=inventory.bookings,
        )


class ComparativeEvaluator:
    """Algorithm 2: 정책 간 쌍체 비교 평가기 (Comparative Performance Analysis)."""

    def __init__(self, simulator: HotelPricingSimulator):
        self.simulator = simulator

    def run_paired_comparison(
        self,
        policy_a: BasePolicy,
        policy_b: BasePolicy,
        num_trials: int = 10,
        agents_per_trial: int = 112,
        base_seed: int = 1000,
        archetype_priors: Optional[Dict[str, float]] = None,
    ) -> ComparativeEvaluationResult:
        """
        논문 Algorithm 2:
        각 시행마다 난수 시드 Sn으로 동일한 합성 인구 Pop_n을 생성하고,
        동일 Pop_n에 대해 Policy A와 Policy B를 각각 평가하여 δn = V(B) - V(A) 산출.
        """
        deltas: List[float] = []
        rev_a_list: List[float] = []
        rev_b_list: List[float] = []
        occ_a_list: List[float] = []
        occ_b_list: List[float] = []
        adr_a_list: List[float] = []
        adr_b_list: List[float] = []
        revpar_a_list: List[float] = []
        revpar_b_list: List[float] = []

        seg_a_rates: Dict[str, List[float]] = {k: [] for k in ARCHETYPES}
        seg_b_rates: Dict[str, List[float]] = {k: [] for k in ARCHETYPES}
        trials_data: List[Dict[str, Any]] = []

        for n in range(1, num_trials + 1):
            seed_n = base_seed + n
            # 동일 인구 생성
            pop_gen = PopulationGenerator(
                horizon=self.simulator.horizon,
                seed=seed_n,
                archetype_priors=archetype_priors,
            )
            pop_n = pop_gen.generate_population(target_total_agents=agents_per_trial)

            # Policy A 실행 (order_seed 고정 — A·B 동일 경합 순서)
            m_a = self.simulator.run_simulation(policy_a, pop_n, order_seed=seed_n)
            # Policy B 실행 (동일 Pop_n · 동일 순서 투입)
            m_b = self.simulator.run_simulation(policy_b, pop_n, order_seed=seed_n)

            delta = m_b.total_revenue - m_a.total_revenue
            deltas.append(delta)
            rev_a_list.append(m_a.total_revenue)
            rev_b_list.append(m_b.total_revenue)
            occ_a_list.append(m_a.occupancy_rate)
            occ_b_list.append(m_b.occupancy_rate)
            adr_a_list.append(m_a.adr)
            adr_b_list.append(m_b.adr)
            revpar_a_list.append(m_a.revpar)
            revpar_b_list.append(m_b.revpar)

            for k in ARCHETYPES:
                seg_a_rates[k].append(m_a.segment_booking_rates.get(k, 0.0))
                seg_b_rates[k].append(m_b.segment_booking_rates.get(k, 0.0))

            trials_data.append({
                "trial": n,
                "seed": seed_n,
                "revenue_a": m_a.total_revenue,
                "revenue_b": m_b.total_revenue,
                "delta": delta,
                "booking_rate_a": m_a.booking_rate,
                "booking_rate_b": m_b.booking_rate,
            })

        mean_delta = float(np.mean(deltas))
        std_delta = float(np.std(deltas, ddof=1)) if num_trials > 1 else 0.0
        mean_rev_a = float(np.mean(rev_a_list))
        mean_rev_b = float(np.mean(rev_b_list))
        uplift_pct = ((mean_rev_b - mean_rev_a) / mean_rev_a * 100.0) if mean_rev_a > 0 else 0.0

        # t-statistic and approximate p-value
        if std_delta > 1e-6 and num_trials > 1:
            t_stat = mean_delta / (std_delta / math.sqrt(num_trials))
            # 단순 근사 정규 분포 기반 양측 검정 p-value
            z = abs(t_stat)
            p_val = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2))))
        else:
            t_stat = 0.0
            p_val = 1.0

        seg_uplift: Dict[str, float] = {}
        for k in ARCHETYPES:
            diff = float(np.mean(seg_b_rates[k])) - float(np.mean(seg_a_rates[k]))
            seg_uplift[k] = round(diff, 2)

        return ComparativeEvaluationResult(
            policy_a_name=policy_a.name,
            policy_b_name=policy_b.name,
            num_trials=num_trials,
            mean_revenue_a=round(mean_rev_a, 2),
            mean_revenue_b=round(mean_rev_b, 2),
            mean_treatment_effect=round(mean_delta, 2),
            revenue_uplift_pct=round(uplift_pct, 2),
            std_treatment_effect=round(std_delta, 2),
            t_statistic=round(t_stat, 3),
            p_value=round(p_val, 4),
            mean_occupancy_a=round(float(np.mean(occ_a_list)), 2),
            mean_occupancy_b=round(float(np.mean(occ_b_list)), 2),
            mean_adr_a=round(float(np.mean(adr_a_list)), 2),
            mean_adr_b=round(float(np.mean(adr_b_list)), 2),
            mean_revpar_a=round(float(np.mean(revpar_a_list)), 2),
            mean_revpar_b=round(float(np.mean(revpar_b_list)), 2),
            segment_uplift=seg_uplift,
            trials_data=trials_data,
        )
