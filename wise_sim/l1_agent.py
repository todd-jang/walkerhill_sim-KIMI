"""L1: LLM Agent (행동 판정 계층) — Gate B(배칭) 및 자율 의사결정.

- 고객 페르소나를 이해하고 복잡한 조건(가격, 프로모션, 체류일 등)에서 수락/거절/기권을 논리적으로 판단.
- 고성능 모델(GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro, Llama 3 70B 파인튜닝) 지원.
- Gate B: 20명/호출 배칭, 온도 0.7, 재현성을 위한 시드 고정.
- MockAgent: LLM API 키 없이 파이프라인 전체를 검증하기 위한 규칙 기반 모의 에이전트.
"""
from __future__ import annotations

import json
import math
import os
import random
import urllib.request
from dataclasses import dataclass
from typing import Protocol

try:
    from personas import Persona
except ImportError:
    from .personas import Persona

BATCH_SIZE = 20          # Gate B: 20명/호출
BASE_ROOM_PRICE = 350_000  # KRW 기본 객실료(가정치)


@dataclass(frozen=True)
class Policy:
    """정책 옵션 — 3×3 격자의 한 칸."""
    price_delta: float      # -0.10 / 0.0 / +0.10
    promo: str              # none / package / breakfast

    @property
    def label(self) -> str:
        return f"가격{self.price_delta:+.0%}×{self.promo}"


@dataclass
class Decision:
    """개별 고객의 예약 의사결정 결과."""
    persona_id: int
    archetype: str
    booked: bool
    abstained: bool          # 기권(결정 불가) — 기권률 지표용
    spend: float             # 총 소비(객실+부대), 미예약시 0
    trace: str = ""          # 의사결정 근거(trace 축)


class BaseAgent(Protocol):
    """L1 에이전트 공통 인터페이스."""
    name: str

    def decide_batch(self, personas: list[Persona], policy: Policy,
                     weekend: bool, seed: int) -> list[Decision]:
        ...


class MockAgent:
    """규칙 기반 모의 에이전트 (L1 Mock). 시드 고정으로 완전한 재현성 보장."""
    name = "mock"

    def decide_batch(self, personas: list[Persona], policy: Policy,
                     weekend: bool, seed: int) -> list[Decision]:
        rng = random.Random(seed)
        out = []
        for p in personas:
            price_factor = 1.0 + policy.price_delta
            promo_boost = {"none": 0.0, "package": 0.12, "breakfast": 0.06}.get(policy.promo, 0.0)
            logit = (
                4.0 * (p.base_demand - 0.45)
                - 5.0 * p.price_sensitivity * policy.price_delta
                + 1.5 * p.promo_affinity * promo_boost
                + (0.8 * p.weekend_preference if weekend else -0.5)
                + rng.gauss(0, 0.6)          # 온도에 해당하는 확률적 요소
            )
            prob = 1.0 / (1.0 + math.exp(-logit))
            # 극단적 불확실 구간은 기권 처리
            if 0.46 < prob < 0.54 and rng.random() < 0.3:
                out.append(Decision(p.id, p.archetype, False, True, 0.0, "uncertain"))
                continue
            booked = rng.random() < prob
            spend = (BASE_ROOM_PRICE * price_factor * p.stay_nights * p.spend_index
                     if booked else 0.0)
            out.append(Decision(p.id, p.archetype, booked, False, spend,
                                f"logit={logit:.2f},p={prob:.2f}"))
        return out


class LLMAgent:
    """고성능 LLM 에이전트 어댑터 (OpenAI 호환 API 엔드포인트 지원).
    
    GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro, Llama 3 70B 등과 연계 가능.
    20명/호출 배칭(Gate B), 재현성을 위한 시드 고정.
    """
    name = "llm"

    def __init__(self, model: str | None = None,
                 base_url: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get("WISE_LLM_MODEL", "gpt-4o")
        self.base_url = base_url or os.environ.get(
            "WISE_LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.environ.get("WISE_LLM_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                f"LLM API 키가 설정되지 않았습니다. WISE_LLM_API_KEY 환경변수를 설정하거나 MockAgent를 사용하세요."
            )

    def decide_batch(self, personas: list[Persona], policy: Policy,
                     weekend: bool, seed: int) -> list[Decision]:
        prompt = self._build_prompt(personas, policy, weekend)
        body = json.dumps({
            "model": self.model,
            "temperature": 0.7,
            "seed": seed,  # Gate B: 재현성 시드
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "당신은 워커힐 호텔 수요 시뮬레이터입니다. "
                        "제공된 각 고객 페르소나의 성향(가격 민감도, 프로모션 친화도, 체류 일정 등)을 "
                        "면밀히 검토하여 주어진 호텔 가격 정책과 프로모션 조건 하에서 객실 예약 여부를 논리적으로 판단하세요. "
                        "판단이 불가능할 정도로 조건이 애매한 경우 abstain을 true로 표시하세요."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            content = json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]
        return self._parse(content, personas, policy)

    def _build_prompt(self, personas: list[Persona], policy: Policy, weekend: bool) -> str:
        desc = "\n".join(p.describe() for p in personas)
        price = BASE_ROOM_PRICE * (1 + policy.price_delta)
        return (
            f"숙박 구분: {'주말(금/토)' if weekend else '주중(일~목)'}\n"
            f"적용 객실료: 1박당 {price:,.0f}원 (기준 대비 {policy.price_delta:+.0%})\n"
            f"프로모션: {policy.promo}\n\n"
            f"아래 {len(personas)}명의 고객 페르소나 각각에 대해 예약 여부를 판단하세요:\n{desc}\n\n"
            '반드시 아래 형식의 JSON 객체로만 응답하세요:\n'
            '{"decisions": [{"id": N, "booked": true|false, "abstain": false, "reason": "간단한 판단 근거"}]}'
        )

    def _parse(self, content: str, personas: list[Persona], policy: Policy) -> list[Decision]:
        data = json.loads(content)
        by_id = {d["id"]: d for d in data.get("decisions", [])}
        out = []
        for p in personas:
            d = by_id.get(p.id)
            if d is None:
                out.append(Decision(p.id, p.archetype, False, True, 0.0, "no-response"))
                continue
            is_abstain = bool(d.get("abstain", False))
            booked = bool(d.get("booked", False)) and not is_abstain
            spend = (BASE_ROOM_PRICE * (1 + policy.price_delta)
                     * p.stay_nights * p.spend_index if booked else 0.0)
            reason = str(d.get("reason", ""))[:200]
            out.append(Decision(p.id, p.archetype, booked, is_abstain, spend, reason))
        return out


def get_agent(prefer_llm: bool = True, model: str | None = None) -> BaseAgent:
    """에이전트 인스턴스 팩토리."""
    if prefer_llm:
        try:
            return LLMAgent(model=model)
        except RuntimeError:
            pass
    return MockAgent()
