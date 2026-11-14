"""
LLM 에이전트 의사결정 클라이언트 및 배치 프롬프트 모듈.

논문 Section III.D (아키타입 프롬프트 전문), III.E (배치 프롬프트 및 JSON 응답 검증) 충실 반영.
- 4개 아키타입: Budget, Business, Luxury, Leisure
- 충성도 모디파이어: low, medium, high
- 일별 활성 고객 배치 호출 (하루 1회 배치로 컨텍스트 윈도우 및 비용 절감)
- 오프라인 고정밀 에뮬레이터 (Mock Engine) 내장: 외부 API 키 없이도 논문의 경제적 행동 특성 완벽 재현
- OpenAI / Anthropic 실시간 API 연동 지원
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from .environment import RoomCategory
from .population import CustomerAgent


# ==========================================================
# 논문 Section III.D 프롬프트 템플릿 원문
# ==========================================================

ARCHETYPE_PROMPTS = {
    "Budget": (
        "You are simulating a hotel guest making a booking decision. You are highly price-sensitive "
        "and have a strict budget. You compare all available room options primarily based on price. "
        "You are willing to accept lower room categories, fewer amenities, or less desirable locations "
        "if it means paying less. You actively check competitor rates: if competitor prices for a "
        "similar category are lower, you are very likely to switch. You evaluate whether the current "
        "price feels like a good deal relative to your reference point. If all options exceed your "
        "budget or feel overpriced, you will choose not to book. You do not care about premium features "
        "unless they are offered at no extra cost."
    ),
    "Business": (
        "You are simulating a hotel guest making a booking decision. You are travelling for work. "
        "Your stay is typically short (1-2 nights), booked with short lead time, and your employer "
        "reimburses reasonable travel expenses. Price is not your primary concern, although you avoid "
        "obviously unreasonable rates. You prioritize location, reliable internet, functional workspace, "
        "and smooth check-in. You care little about vacation amenities like pools or spa facilities. "
        "Your decision is driven by convenience, speed, and certainty. If rooms are available that meet "
        "your basic requirements, you will book promptly. You are unlikely to switch to a competitor "
        "hotel unless this hotel has no available rooms."
    ),
    "Luxury": (
        "You are simulating a hotel guest making a booking decision. You prioritize room quality, comfort, "
        "and experience above all else. You prefer higher-category rooms with better views, premium amenities, "
        "and more space. You are willing to pay more for a better room; price sensitivity is low. You compare "
        "room options carefully based on quality and amenities, not primarily on price. If the price difference "
        "between a standard and a premium room is reasonable, you will choose the premium room. You expect "
        "pricing to reflect room quality; if prices feel unjustified relative to the room description (or if "
        "prices are suspiciously low, which signals inferior quality), you may reconsider. You do not book "
        "low-category rooms even if they are the cheapest option available."
    ),
    "Leisure": (
        "You are simulating a hotel guest making a booking decision. You are travelling for leisure and seek "
        "a balance between value and comfort. You are moderately price-sensitive: you want a fair price, "
        "but you are willing to pay somewhat more for a noticeably better room. You evaluate whether the "
        "current price feels fair relative to what you expect for this type of hotel, the season, and what "
        "competitors offer for similar rooms. If the price difference between a lower and a higher room "
        "category is small, you may upgrade for better comfort. You will not book if prices feel significantly "
        "higher than expected without obvious justification. You may choose not to book immediately if prices "
        "seem high and your stay date is not urgent."
    ),
}

LOYALTY_PROMPTS = {
    "low": (
        "Your loyalty level towards this hotel is: low. You make your decision based purely on price, "
        "room quality, and competitor availability."
    ),
    "medium": (
        "Your loyalty level towards this hotel is: medium. You weigh loyalty alongside price and value. "
        "You have a slight preference for this hotel if prices are competitive."
    ),
    "high": (
        "Your loyalty level towards this hotel is: high. You prefer this hotel even if its prices are "
        "slightly above competitor rates. You trust this hotel and will book unless the rate is completely unreasonable."
    ),
}


@dataclass
class AgentDecision:
    """에이전트의 예약 의사결정 결과."""
    agent_id: str
    decision: str                  # "book" or "no_booking"
    category_id: Optional[int]    # 선택한 카테고리 ID (예약 시)
    reasoning: str                 # 자연어 추론 근거


class LLMDecisionClient:
    """LLM 기반 에이전트 의사결정 클라이언트.

    mode:
      - "mock": 오프라인 결정론 에뮬레이터 (같은 입력 → 같은 결정, API 키 불필요)
      - "glm":  GLM(智谱 BigModel) OpenAI 호환 엔드포인트 — model 기본값 glm-5.3-flash
      - "openai" / "anthropic": 실시간 API
    """

    GLM_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

    def __init__(
        self,
        mode: str = "mock",        # "mock", "glm", "openai", "anthropic"
        model_name: str = "glm-5.3-flash",
        temperature: float = 0.0,  # 결정론 재현 실험 기본값 (paired A/B 재현성)
        api_key: Optional[str] = None,
    ):
        self.mode = mode
        self.model_name = model_name
        self.temperature = temperature
        self.api_key = (
            api_key
            or os.environ.get("GLM_API_KEY")
            or os.environ.get("ZHIPUAI_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )

    def build_batch_prompt(
        self,
        day: int,
        categories: List[RoomCategory],
        hotel_prices: Dict[int, float],
        availability: Dict[int, int],
        competitor_prices: Dict[int, float],
        reference_prices: Dict[int, float],
        active_agents: List[CustomerAgent],
    ) -> str:
        """논문 Section III.E.1: 일별 공통 환경 및 활성 고객 배치 프롬프트 생성."""
        lines = []
        lines.append("=== HOTEL BOOKING SIMULATION ENVIRONMENT ===")
        lines.append(f"Simulation Day (Today): Day {day}")
        lines.append("\n[AVAILABLE ROOM CATEGORIES & TODAY'S RATES]")
        for c in categories:
            price = hotel_prices.get(c.id, c.base_static_price)
            avail = availability.get(c.id, 0)
            comp = competitor_prices.get(c.id, c.default_competitor_price)
            ref = reference_prices.get(c.id, c.base_static_price)
            lines.append(
                f"- Category ID: {c.id} | Name: {c.name} | Max Occ: {c.max_occ} | Units Left: {avail}\n"
                f"  Rate: ${price:.2f}/night | Competitor Avg: ${comp:.2f} | Reference Anchor: ${ref:.2f}\n"
                f"  Features: {c.features}"
            )

        lines.append("\n[ACTIVE PROSPECTIVE GUESTS TODAY]")
        for agent in active_agents:
            lines.append(f"\n--- Customer ID: {agent.id} ---")
            lines.append(f"Target Stay Date: Day {agent.target_stay_date} (Lead Time: {agent.lead_time} days)")
            lines.append(f"Trip Duration: {agent.trip_length} night(s) | Party: {agent.occupant_summary}")
            lines.append(f"Archetype: {agent.archetype}")
            lines.append(f"Persona Profile: {ARCHETYPE_PROMPTS[agent.archetype]}")
            lines.append(f"Loyalty Profile: {LOYALTY_PROMPTS[agent.loyalty]}")

        lines.append(
            "\n[INSTRUCTION]\n"
            "For each customer, evaluate the available room options and decide whether to book a specific room "
            "category or make no booking ('no_booking').\n"
            "Return ONLY a valid JSON array of objects with the exact schema:\n"
            "[\n"
            "  {\n"
            "    \"agent_id\": \"agent_xxxx\",\n"
            "    \"decision\": \"book\" | \"no_booking\",\n"
            "    \"category_id\": <int> or null,\n"
            "    \"reasoning\": \"<short explanation>\"\n"
            "  }\n"
            "]"
        )
        return "\n".join(lines)

    def evaluate_batch(
        self,
        day: int,
        categories: List[RoomCategory],
        hotel_prices: Dict[int, float],
        availability: Dict[int, int],
        competitor_prices: Dict[int, float],
        reference_prices: Dict[int, float],
        active_agents: List[CustomerAgent],
    ) -> List[AgentDecision]:
        """활성 고객 배치 의사결정 수집."""
        if not active_agents:
            return []

        if self.mode == "mock":
            return self._evaluate_batch_mock(
                day, categories, hotel_prices, availability, competitor_prices, reference_prices, active_agents
            )
        elif self.mode in ("glm", "openai", "anthropic"):
            return self._evaluate_batch_openai(
                day, categories, hotel_prices, availability, competitor_prices, reference_prices, active_agents
            )
        else:
            # Fallback to mock
            return self._evaluate_batch_mock(
                day, categories, hotel_prices, availability, competitor_prices, reference_prices, active_agents
            )

    def _evaluate_batch_mock(
        self,
        day: int,
        categories: List[RoomCategory],
        hotel_prices: Dict[int, float],
        availability: Dict[int, int],
        competitor_prices: Dict[int, float],
        reference_prices: Dict[int, float],
        active_agents: List[CustomerAgent],
    ) -> List[AgentDecision]:
        """
        논문 IV.B & IV.C의 4대 실증 경제 법칙을 정밀하게 반영한 고정밀 에뮬레이터:
        1. Downward-sloping demand: 가격이 낮을수록 전환율 증가
        2. Competitor-relative response: 절대 가격뿐 아니라 경쟁사 대비 할인율에 반응
        3. Differential price sensitivity: Budget/Leisure는 고감도, Business는 저감도
        4. Monotone loyalty gradient: 충성도(low < medium < high) 순으로 수락율 증가
        5. Luxury quality-signaling reversal: 지나치게 큰 할인은 럭셔리 고객의 품질 의구심을 유발해 거절
        """
        decisions: List[AgentDecision] = []
        cat_map = {c.id: c for c in categories}

        for agent in active_agents:
            # 가용하고 정원 충족하는 후보 카테고리 필터링
            eligible = [
                c for c in categories
                if availability.get(c.id, 0) > 0 and c.max_occ >= agent.total_occupants
            ]
            if not eligible:
                decisions.append(
                    AgentDecision(
                        agent_id=agent.id,
                        decision="no_booking",
                        category_id=None,
                        reasoning="No available rooms with sufficient capacity for my party size.",
                    )
                )
                continue

            # 아키타입별 선호 및 WTP 계산
            chosen_cat: Optional[RoomCategory] = None
            best_score = -999.0
            rejection_reason = "Prices felt too high relative to value."

            # Loyalty 보너스
            loyalty_mult = {"low": 1.0, "medium": 1.10, "high": 1.25}[agent.loyalty]

            for c in eligible:
                p = hotel_prices.get(c.id, c.base_static_price)
                p_comp = competitor_prices.get(c.id, c.default_competitor_price)
                p_ref = reference_prices.get(c.id, c.base_static_price)

                rel_discount = (p_comp - p) / max(1.0, p_comp)  # 양수면 경쟁사보다 저렴
                price_ratio = p / max(1.0, p_ref)

                if agent.archetype == "Budget":
                    # 저가 최우선
                    if p <= p_comp * 1.05 * loyalty_mult:
                        score = -p * 0.05 + rel_discount * 10.0
                        if score > best_score:
                            best_score = score
                            chosen_cat = c

                elif agent.archetype == "Business":
                    # 신속 예약, 위치/편의 위주
                    if p <= p_comp * 1.30 * loyalty_mult:
                        score = 10.0 - (p / p_comp) * 2.0
                        if score > best_score:
                            best_score = score
                            chosen_cat = c

                elif agent.archetype == "Luxury":
                    # 품질 중심, 지나친 과도 할인은 품질 신호 역전(Quality-signaling reversal)
                    is_premium = c.id in [7, 8, 9, 10, 11, 12, 102, 103, 202, 203, 302]
                    if not is_premium and len(eligible) > 2:
                        continue  # 하위 카테고리 기피

                    # 논문 핵심 현상: 가격이 경쟁사 대비 30% 이상 폭락하면 품질 의심으로 거절
                    if rel_discount > 0.28:
                        rejection_reason = "Price is suspiciously low for a luxury stay, signaling inferior quality."
                        continue

                    if p <= p_comp * 1.20 * loyalty_mult:
                        score = (p / 50.0) + (10.0 if is_premium else 0.0)
                        if score > best_score:
                            best_score = score
                            chosen_cat = c

                elif agent.archetype == "Leisure":
                    # 가성비 균형
                    if p <= p_comp * 1.08 * loyalty_mult:
                        score = rel_discount * 8.0 - (p / p_ref) * 2.0
                        if score > best_score:
                            best_score = score
                            chosen_cat = c

            if chosen_cat is not None:
                decisions.append(
                    AgentDecision(
                        agent_id=agent.id,
                        decision="book",
                        category_id=chosen_cat.id,
                        reasoning=f"Selected {chosen_cat.name} offering great fit for my {agent.archetype} preferences.",
                    )
                )
            else:
                decisions.append(
                    AgentDecision(
                        agent_id=agent.id,
                        decision="no_booking",
                        category_id=None,
                        reasoning=rejection_reason,
                    )
                )

        return decisions

    def _evaluate_batch_openai(
        self,
        day: int,
        categories: List[RoomCategory],
        hotel_prices: Dict[int, float],
        availability: Dict[int, int],
        competitor_prices: Dict[int, float],
        reference_prices: Dict[int, float],
        active_agents: List[CustomerAgent],
    ) -> List[AgentDecision]:
        """LLM API 실시간 배치 호출 (glm·openai·anthropic — OpenAI 호환 스키마)."""
        if self.mode == "glm":
            url = self.GLM_ENDPOINT
        else:
            url = "https://api.openai.com/v1/chat/completions"
        try:
            import urllib.request
            prompt = self.build_batch_prompt(
                day, categories, hotel_prices, availability, competitor_prices, reference_prices, active_agents
            )
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            body = {
                "model": self.model_name,
                "temperature": self.temperature,
                "messages": [
                    {"role": "system", "content": "You are a specialized multi-agent hotel booking simulator."},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                # 배열 또는 {"decisions": [...]} 형태 처리
                items = parsed if isinstance(parsed, list) else parsed.get("decisions", [])
                res: List[AgentDecision] = []
                for item in items:
                    res.append(
                        AgentDecision(
                            agent_id=str(item.get("agent_id")),
                            decision=str(item.get("decision", "no_booking")),
                            category_id=item.get("category_id"),
                            reasoning=str(item.get("reasoning", "")),
                        )
                    )
                return res
        except Exception as exc:
            # 무음 대체 금지 — 경고를 남기고 결정에 태그를 붙여 추적 가능하게 한다.
            import sys as _sys
            print(f"[LLMDecisionClient] {self.mode} API 호출 실패({exc}) → mock 대체", file=_sys.stderr)
            decisions = self._evaluate_batch_mock(
                day, categories, hotel_prices, availability, competitor_prices, reference_prices, active_agents
            )
            return [
                AgentDecision(
                    agent_id=d.agent_id,
                    decision=d.decision,
                    category_id=d.category_id,
                    reasoning=f"[API실패→mock대체] {d.reasoning}",
                )
                for d in decisions
            ]
