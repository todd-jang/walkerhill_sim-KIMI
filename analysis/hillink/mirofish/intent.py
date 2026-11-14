"""의도 JSON 스키마와 검증 — LLM·mock이 내는 행동 의도의 단일 계약."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

SCHEMA_DOC = """[
  {"agent_id": "g0001", "slot": "DINNER", "venue": "MYONGWOLGWAN", "reasoning": "<short>"}
]"""


@dataclass(frozen=True)
class Intent:
    agent_id: str
    slot: str
    venue: str
    reasoning: str = ""


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return m.group(1).strip() if m else text


def parse_intents(
    raw: str | Sequence[dict],
    valid_agents: Iterable[str],
    valid_slots: Iterable[str],
    valid_venues: Iterable[str],
) -> Tuple[List[Intent], List[str]]:
    errors: List[str] = []
    if isinstance(raw, str):
        try:
            data = json.loads(_strip_fences(raw))
        except json.JSONDecodeError as exc:
            return [], [f"JSON_PARSE_FAILED: {exc}"]
    else:
        data = raw
    if isinstance(data, dict):
        data = data.get("intents", [])
    if not isinstance(data, list):
        return [], ["SCHEMA_NOT_LIST"]

    agents, slots, venues = set(valid_agents), set(valid_slots), set(valid_venues)
    out: List[Intent] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"ITEM_{i}_NOT_OBJECT")
            continue
        aid, slot, venue = item.get("agent_id"), item.get("slot"), item.get("venue")
        if not isinstance(aid, str) or aid not in agents:
            errors.append(f"ITEM_{i}_UNKNOWN_AGENT:{aid}")
            continue
        if slot not in slots:
            errors.append(f"ITEM_{i}_BAD_SLOT:{slot}")
            continue
        if venue not in venues:
            errors.append(f"ITEM_{i}_BAD_VENUE:{venue}")
            continue
        out.append(Intent(aid, slot, venue, str(item.get("reasoning", ""))[:200]))
    return out, errors
