"""일배치 의도 클라이언트 — mock(결정론)과 GLM/OpenAI 실호출.

한 라운드(하루) = 한 번의 배치 호출. 응답은 intent.parse_intents가 스키마를 강제한다.
실호출 실패 시 무음 대체를 하지 않는다 — stderr 경고와 대체 표시를 남긴다.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from .agents import Guest, SLOT_VISIT_P, decide_venue
from .intent import Intent, parse_intents
from .world import SLOTS, World

GLM_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
AINIZE_ENDPOINT = "https://ainize.ai/v1/chat/completions"
AINIZE_DEFAULT_MODEL = "GLM-5.3-Flash@0xe8f46f8c27b0569d12c4f6f8d01496882aeaa4ba"
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"


class DayBatchClient:
    def __init__(self, mode: str = "mock", model: Optional[str] = None, api_key: Optional[str] = None):
        self.mode = mode
        if model is None:
            model = os.environ.get("AINIZE_MODEL", AINIZE_DEFAULT_MODEL) if mode == "ainize" else "glm-5.3-flash"
        self.model = model
        self.api_key = (
            api_key
            or os.environ.get("AINIZE_API_KEY")
            or os.environ.get("GLM_API_KEY")
            or os.environ.get("ZHIPUAI_API_KEY")
        )
        self.calls = 0
        self.fallbacks = 0
        self.errors: List[str] = []
        self.chunk_size = int(os.environ.get("MIROFISH_LLM_CHUNK", "10"))
        self.timeout = int(os.environ.get("MIROFISH_LLM_TIMEOUT", "300"))
        self.workers = int(os.environ.get("MIROFISH_LLM_WORKERS", "2"))

    def decide(
        self,
        day_index: int,
        day: date,
        guests: Sequence[Guest],
        world: World,
        price_index: Dict[str, float],
        prev_fill: Dict[str, float],
        seed: int,
        closed_extra: Optional[Dict[str, set]] = None,
    ) -> List[Intent]:
        open_by_slot = {
            slot: [v for v in world.open_venues(day, slot)]
            for slot, _, _ in SLOTS
        }

        if self.mode == "mock":
            return self._mock(day_index, guests, open_by_slot, price_index, prev_fill, seed)

        chunks = [list(guests[i:i + self.chunk_size]) for i in range(0, len(guests), self.chunk_size)] or [[]]
        try:
            with ThreadPoolExecutor(max_workers=min(self.workers, len(chunks))) as pool:
                futures = [
                    pool.submit(self._llm, day_index, day, chunk, world, open_by_slot,
                                price_index, prev_fill, closed_extra)
                    for chunk in chunks if chunk
                ]
                intents: List[Intent] = []
                for f in futures:
                    intents.extend(f.result())
            self.calls += len(chunks)
            return intents
        except Exception as exc:
            self.fallbacks += 1
            self.errors.append(f"day{day_index}: {exc}")
            print(f"[DayBatchClient] {self.mode} 호출 실패({exc}) → mock 대체", file=sys.stderr)
            return self._mock(day_index, guests, open_by_slot, price_index, prev_fill, seed)

    def _mock(self, day_index, guests, open_by_slot, price_index, prev_fill, seed) -> List[Intent]:
        out: List[Intent] = []
        for g in guests:
            for slot, _, _ in SLOTS:
                venue = decide_venue(g, day_index, slot, open_by_slot[slot], price_index, prev_fill, seed)
                if venue:
                    out.append(Intent(g.id, slot, venue, "mock preference model"))
        return out

    def _llm(self, day_index, day, guests, world, open_by_slot, price_index, prev_fill, closed_extra=None) -> List[Intent]:
        if not self.api_key:
            raise RuntimeError(f"API 키 없음 ({self.mode.upper()}_API_KEY)")
        lines = [f"=== WALKERHILL DAY {day_index + 1} ({day.isoformat()}, {day.strftime('%a')}) ==="]
        lines.append("\n[VENUES — slot: open venues (capacity, price index, yesterday fill)]")
        for slot, _, _ in SLOTS:
            rows = []
            for v in open_by_slot[slot]:
                cap = world.venues[v].capacity(world.capacity_scale)
                rows.append(f"{v}(cap {cap}, price x{price_index.get(v, 1.0):.2f}, fill {prev_fill.get(v, 0.0):.0%})")
            lines.append(f"- {slot}: {', '.join(rows) if rows else 'none open'}")
        closed = sorted((closed_extra or {}).get("venues", set()))
        if closed:
            lines.append(f"\n[CLOSED TODAY — do not choose] {', '.join(closed)}")
        lines.append("\n[GUESTS IN STAY — with personal state and memory]")
        for g in guests:
            aff = ",".join(f"{k}{v:+.2f}" for k, v in sorted(g.affinity.items(), key=lambda kv: -abs(kv[1]))[:3]) or "-"
            mem = "; ".join(g.memory_digest()) or "no visits yet"
            lines.append(
                f"{g.id}: {g.archetype} party={g.party} loyalty={g.loyalty} "
                f"satisfaction={g.satisfaction:.2f} budget_left={g.budget_left:.0f} "
                f"visited_before={aff} memory=[{mem}]"
            )
        lines.append(
            "\n[INSTRUCTION] Each guest may visit at most one venue per slot, and may skip slots. "
            "Use each guest's satisfaction, remaining budget, learned affinity and memory. "
            "Avoid venues where they were just rejected; respect party size vs capacity and archetype price sensitivity.\n"
            "Return ONLY JSON: "
            '{"decisions":[{"agent_id":"g0001","slot":"DINNER","venue":"MYONGWOLGWAN","reasoning":"<short>"}]}'
        )
        prompt = "\n".join(lines)
        if self.mode == "glm":
            url = GLM_ENDPOINT
        elif self.mode == "ainize":
            url = AINIZE_ENDPOINT
        else:
            url = OPENAI_ENDPOINT
        body = {
            "model": self.model,
            "temperature": 0.0,
            "max_tokens": 4000,
            "messages": [
                {"role": "system", "content": "You are a multi-agent hotel guest simulator. Output JSON only."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        intents, errors = parse_intents(
            content,
            valid_agents=[g.id for g in guests],
            valid_slots=[s for s, _, _ in SLOTS],
            valid_venues=set(world.venues),
        )
        if errors:
            self.errors.extend(f"day{day_index}: {e}" for e in errors[:5])
        return intents
