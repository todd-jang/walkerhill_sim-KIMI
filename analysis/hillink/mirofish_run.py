#!/usr/bin/env python3
"""미로피쉬 러너 — 라운드 루프 실행·신의 눈 시나리오·paired 반사실·배속 실측.

    python3 hillink/mirofish_run.py --days 31 --guests 400 --seed 17 --out /tmp/mf.json
    python3 hillink/mirofish_run.py --mode compare --price "GRAND_POOL=1.3" --out /tmp/mf_cmp.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mirofish.agents import Guest, generate_guests
from mirofish.engine import Scenario, compare, simulate
from mirofish.llm import DayBatchClient
from mirofish.world import World


def parse_price(text: Optional[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not text:
        return out
    for part in text.split(','):
        if not part.strip():
            continue
        k, v = part.split('=')
        out[k.strip()] = float(v)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="미로피쉬 — 라운드 기반 멀티에이전트 세계")
    p.add_argument("--mode", choices=["run", "compare"], default="run")
    p.add_argument("--days", type=int, default=31)
    p.add_argument("--guests", type=int, default=400)
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--llm", choices=["mock", "glm", "ainize", "openai"], default="mock")
    p.add_argument("--model", default=None)
    p.add_argument("--price", default=None, help="신의 눈 — 업장 가격지수 'GRAND_POOL=1.3,THE_BUFFET=0.9'")
    p.add_argument("--close", default=None, help="신의 눈 — 폐쇄 업장 'GEUMRYONG,MYONGWOLGWAN'")
    p.add_argument("--capacity-scale", type=float, default=1.0)
    p.add_argument("--swarm", type=int, default=0, help="swarm 뷰에 내보낼 페르소나 수 (0=끔)")
    p.add_argument("--out", default="-")
    args = p.parse_args(argv)

    world = World()
    guests: List[Guest] = generate_guests(args.guests, args.days, args.seed)
    scenario = Scenario(
        label="treatment",
        price_index=parse_price(args.price),
        closed_venues={v.strip() for v in (args.close or "").split(',') if v.strip()},
        capacity_scale=args.capacity_scale,
    )

    def factory() -> DayBatchClient:
        return DayBatchClient(mode=args.llm, model=args.model)

    swarm_ids = [g.id for g in sorted(guests, key=lambda g: (g.arrival_day, -g.nights))[:args.swarm]] if args.swarm else None

    if args.mode == "compare":
        result = compare(world, guests, args.days, args.seed, scenario, factory)
        headline = result["delta"]
    else:
        result = simulate(world, guests, args.days, args.seed, scenario, factory(),
                          swarm_agents=swarm_ids)
        result["experiment"] = "run"
        headline = result["summary"]

    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(payload)
    else:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n")
        sp = result["speed"] if "speed" in result else result["baseline"]["speed"]
        print(f"OK {out} — 배속 {sp['speedup_realtime_x']:,.0f}×", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
