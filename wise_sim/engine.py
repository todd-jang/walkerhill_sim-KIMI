"""WISE 시뮬레이션 엔진 — Gate B(배칭)·Gate F(시간 압축) 대응.

- MockAgent: 규칙 기반 로지스틱 의사결정 (LLM 키 없이 전체 파이프라인 검증용)
- LLMAgent: OpenAI 호환 API 어댑터. 20명/호출 배칭(Gate B), 온도>0 + 고정 시드.
  API 키가 없으면 MockAgent로 자동 폴백.
- simulate_weekend(): 한 주말의 예약 결정 시뮬레이션 → 점유율·RevPAR 산출
- run_policy_grid(): 정책 3×3 격자(Gate C) 전체 실행 + trace 로깅
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import urllib.request
from dataclasses import dataclass, field

from personas import Persona

BATCH_SIZE = 20          # Gate B: 20명/호출
TIME_COMPRESSION = 24    # Gate F: 24배 smoke (12배 조건부)
BASE_ROOM_PRICE = 350_000  # KRW 기본 객실료(가정치)
TOTAL_ROOMS = 580          # 가정치


@dataclass(frozen=True)
class Policy:
    """정책 옵션 — 3×3 격자의 한 칸."""
    price_delta: float      # -0.10 / 0.0 / +0.10
    promo: str              # none / package / breakfast

    @property
    def label(self) -> str:
        return f"가격{self.price_delta:+.0%}×{self.promo}"


def policy_grid() -> list[Policy]:
    return [Policy(d, p) for d in (-0.10, 0.0, 0.10)
            for p in ("none", "package", "breakfast")]


@dataclass
class Decision:
    persona_id: int
    archetype: str
    booked: bool
    abstained: bool          # 기권(결정 불가) — 기권률 지표용
    spend: float             # 총 소비(객실+부대), 미예약시 0
    trace: str = ""          # 의사결정 근거(trace 축)


class MockAgent:
    """규칙 기반 모의 에이전트. 시드 고정으로 재현 가능."""
    name = "mock"

    def decide_batch(self, personas: list[Persona], policy: Policy,
                     weekend: bool, seed: int) -> list[Decision]:
        rng = random.Random(seed)
        out = []
        for p in personas:
            price_factor = 1.0 + policy.price_delta
            promo_boost = {"none": 0.0, "package": 0.12, "breakfast": 0.06}[policy.promo]
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
    """OpenAI 호환 API 어댑터. 20명/호출 배칭, 온도>0, 시드 고정."""
    name = "llm"

    def __init__(self, model: str = "gpt-4o-mini",
                 base_url: str | None = None, api_key: str | None = None):
        self.model = model
        self.base_url = base_url or os.environ.get(
            "WISE_LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.environ.get("WISE_LLM_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("LLM API 키 없음 — WISE_LLM_API_KEY 설정 또는 MockAgent 사용")

    def decide_batch(self, personas: list[Persona], policy: Policy,
                     weekend: bool, seed: int) -> list[Decision]:
        prompt = self._build_prompt(personas, policy, weekend)
        body = json.dumps({
            "model": self.model, "temperature": 0.7, "seed": seed,  # Gate B
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            content = json.loads(resp.read())["choices"][0]["message"]["content"]
        return self._parse(content, personas, policy)

    def _build_prompt(self, personas, policy, weekend) -> str:
        desc = "\n".join(p.describe() for p in personas)
        price = BASE_ROOM_PRICE * (1 + policy.price_delta)
        return (
            f"당신은 호텔 수요 시뮬레이터입니다. {'주말' if weekend else '주중'} 숙박, "
            f"객실료 {price:,.0f}원, 프로모션: {policy.promo}.\n"
            f"아래 {len(personas)}명 각각에 대해 예약 여부를 판단하세요.\n{desc}\n"
            'JSON으로만 응답: {"decisions":[{"id":N,"booked":true|false,'
            '"abstain":false,"reason":"..."}]}'
        )

    def _parse(self, content, personas, policy) -> list[Decision]:
        data = json.loads(content)
        by_id = {d["id"]: d for d in data.get("decisions", [])}
        out = []
        for p in personas:
            d = by_id.get(p.id)
            if d is None:
                out.append(Decision(p.id, p.archetype, False, True, 0.0, "no-response"))
                continue
            booked = bool(d.get("booked")) and not bool(d.get("abstain"))
            spend = (BASE_ROOM_PRICE * (1 + policy.price_delta)
                     * p.stay_nights * p.spend_index if booked else 0.0)
            out.append(Decision(p.id, p.archetype, booked, bool(d.get("abstain")),
                                spend, str(d.get("reason", ""))[:200]))
        return out


def get_agent(prefer_llm: bool = True):
    if prefer_llm:
        try:
            return LLMAgent()
        except RuntimeError:
            pass
    return MockAgent()


def _batched(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def simulate_weekend(agent, personas: list[Persona], policy: Policy,
                     weekend_idx: int, weekend: bool = True,
                     seed: int = 1000) -> dict:
    """한 주말 시뮬레이션. 배칭 적용, 시드는 (seed, weekend_idx, policy)로 결정."""
    decisions: list[Decision] = []
    for bi, batch in enumerate(_batched(personas, BATCH_SIZE)):
        tag = f"{seed}|{weekend_idx}|{policy.label}|{bi}"
        bseed = int(hashlib.sha256(tag.encode()).hexdigest()[:8], 16)
        decisions.extend(agent.decide_batch(batch, policy, weekend, bseed))

    valid = [d for d in decisions if not d.abstained]
    booked = [d for d in valid if d.booked]
    rooms = min(sum(d.trace and 1 for d in booked) * 1.0, TOTAL_ROOMS)
    occupancy = sum(p.stay_nights for p in personas if any(
        d.persona_id == p.id and d.booked for d in booked)) / (TOTAL_ROOMS * 2.0)
    occupancy = min(occupancy, 1.0)
    revpar = sum(d.spend for d in booked) / TOTAL_ROOMS
    return {
        "policy": policy.label,
        "weekend_idx": weekend_idx,
        "n": len(personas),
        "abstain_rate": 1 - len(valid) / max(len(decisions), 1),
        "booking_rate": len(booked) / max(len(valid), 1),
        "occupancy": round(occupancy, 4),
        "revpar": round(revpar, 0),
        "decisions": decisions,
    }


def run_policy_grid(agent, personas: list[Persona], n_weekends: int,
                    seed: int = 1000) -> dict:
    """3×3 정책 격자 × n개 주말 실행. trace 전체 보존."""
    grid = policy_grid()
    results = {p.label: [] for p in grid}
    for p in grid:
        for w in range(n_weekends):
            results[p.label].append(simulate_weekend(agent, personas, p, w, seed=seed))
    return results
