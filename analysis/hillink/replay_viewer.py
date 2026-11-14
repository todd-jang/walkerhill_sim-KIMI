#!/usr/bin/env python3
"""정적 리플레이 뷰어 — route_analysis JSON을 HTML로 렌더링한다.

LLM 없는 결정론 재구성(§14)의 시각화. 입력: route_analysis.py --json 출력.
    python3 replay_viewer.py /tmp/route_after.json
    → /tmp/replay_reconstruction.html
"""
import json
import sys
from pathlib import Path

LABELS = {
    'THE_BUFFET': '더뷔페', 'PIZZA_HILL': '피자힐', 'MYONGWOLGWAN': '명월관',
    'MOEGI': '모에기', 'ONDAL': '온달', 'GEUMRYONG': '금룡',
    'THE_PAVILION': '파빌리온', 'LE_PASSAGE': '르파사쥬', 'LUX_BAR': '럭스바',
    'SKYARD': '스카이야드', 'FOREST_PARK': '산책로', 'GRAND_POOL': '수영장',
    'VISTA_POOL': '비스타 수영장', 'WELLNESS_SAUNA': '사우나',
    'THEATRE_OF_LIGHT': '빛의 시어터', 'IRD': 'IRD(룸서비스)',
}

CSS = """
:root {
  --ink: #1a1a1a; --muted: #57606a; --line: #d0d7de;
  --accent: #0b5394; --accent-soft: #eef3f9; --code-bg: #f6f8fa;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 28px 34px 40px;
  font-family: "Apple SD Gothic Neo", "NanumGothic", -apple-system, sans-serif;
  font-size: 14px; line-height: 1.62; color: var(--ink);
  background: #fbfcfd; max-width: 1180px; margin-inline: auto;
}
.header { border-bottom: 3px solid var(--accent); padding-bottom: 14px; margin-bottom: 20px; }
.kicker { font-size: 11px; letter-spacing: 3px; text-transform: uppercase; color: var(--accent); font-weight: 700; margin: 0 0 6px; }
h1 { font-size: 27px; margin: 0 0 7px; line-height: 1.25; }
.subtitle { font-size: 14px; color: var(--muted); margin: 0 0 10px; }
.meta { font-size: 12.5px; color: var(--muted); }
.meta code { background: var(--code-bg); border: 1px solid var(--line); border-radius: 3px; padding: 1px 6px; font-size: 11.5px; }
h2 { font-size: 17px; margin: 26px 0 10px; padding: 4px 0 4px 12px; border-left: 5px solid var(--accent); background: var(--accent-soft); }
h2 .note { float: right; font-size: 11px; color: var(--muted); font-weight: 400; padding-top: 5px; }
.kpis { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin: 18px 0 6px; }
.kpi { background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; }
.kpi .v { font-size: 21px; font-weight: 800; color: var(--accent); letter-spacing: -0.5px; }
.kpi .l { font-size: 11px; color: var(--muted); margin-top: 2px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0 14px; font-size: 12.5px; background: #fff; }
th, td { border: 1px solid var(--line); padding: 5px 9px; text-align: left; }
th { background: #f2f5f8; font-weight: 700; white-space: nowrap; }
table.heat td { text-align: center; padding: 4px 6px; font-size: 11px; font-variant-numeric: tabular-nums; }
table.heat th:first-child, table.heat td:first-child { text-align: left; white-space: nowrap; font-weight: 600; }
.heatcell { display: inline-block; min-width: 52px; border-radius: 3px; padding: 2px 4px; }
.flow { margin: 8px 0 14px; font-size: 13px; }
.flow .row { padding: 5px 10px; border-left: 3px solid var(--accent); background: #fff; border-top: 1px solid var(--line); border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); margin-bottom: 5px; display: flex; justify-content: space-between; gap: 12px; }
.flow .arrow { color: var(--accent); font-weight: 700; }
.flow .cnt { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
ul.reasons { margin: 6px 0 12px; padding-left: 20px; font-size: 13px; }
footer { margin-top: 30px; padding-top: 12px; border-top: 2px solid var(--accent); font-size: 11.5px; color: var(--muted); }
footer code { background: var(--code-bg); border: 1px solid var(--line); border-radius: 3px; padding: 1px 6px; font-size: 11px; }
.tag { display: inline-block; background: var(--accent-soft); border: 1px solid var(--line); border-radius: 10px; padding: 1px 9px; font-size: 11px; color: var(--accent); font-weight: 700; margin-left: 6px; }
"""


def fmt(n: float) -> str:
    return f"{n:,.0f}"


def build(data: dict) -> str:
    hv = data['hour_venue']
    venue_totals = {v: sum(row.get(v, 0) for row in hv.values()) for v in
                    {v for row in hv.values() for v in row}}
    top_venues = [v for v, _ in sorted(venue_totals.items(), key=lambda kv: -kv[1])[:9]]
    vmax = max((row.get(v, 0) for row in hv.values() for v in top_venues), default=1) or 1

    rows = []
    for hour in sorted(hv):
        cells = [f"<th style='text-align:left'>{hour}</th>"]
        for v in top_venues:
            n = round(row_n := hv[hour].get(v, 0), 1)
            alpha = (n / vmax) * 0.85 + (0.06 if n > 0 else 0)
            bg = f"rgba(11,83,148,{alpha:.2f})"
            color = '#fff' if alpha > 0.5 else 'var(--ink)'
            title = f"{LABELS.get(v, v)} · {hour} · 재실 {n:.1f}"
            cells.append(f"<td title='{title}'><span class='heatcell' style='background:{bg};color:{color}'>"
                         f"{n:.0f}</span></td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")

    head = ['<th></th>'] + [f"<th>{LABELS.get(v, v)}</th>" for v in top_venues]

    flow_rows = []
    for t in data['transitions_top']:
        a, b = t['from'].split('@')[0], t['to'].split('@')[0]
        h1, h2 = t['from'].split('@')[1], t['to'].split('@')[1]
        flow_rows.append(
            f"<div class='row'><span>{LABELS.get(a, a)} <span class='arrow'>@{h1} →</span> "
            f"{LABELS.get(b, b)} <span class='arrow'>@{h2}</span></span>"
            f"<span class='cnt'>{t['count']:,}회</span></div>")

    closed = ' · '.join(f"{LABELS.get(v, v)} {c}" for v, c in data['closed_by_venue'].items())
    revpash_rows = []
    base = {'THE_BUFFET': 37418, 'PIZZA_HILL': 27353, 'MYONGWOLGWAN': 23036}
    for v, val in data['revpash'].items():
        delta = ''
        if v in base:
            d = (val - base[v]) / base[v] * 100
            delta = f"<span class='tag'>§14.1 제외 후 +{d:.1f}%</span>"
        peak = data['venue_peak_hour'].get(v, '')
        revpash_rows.append(
            f"<tr><td>{LABELS.get(v, v)}</td><td>{fmt(val)}원/좌석·시간{delta}</td>"
            f"<td>{fmt(data['venue_visits'].get(v, 0))}회</td><td>피크 {peak}</td></tr>")

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HILLINK 정적 리플레이 — 실제 투숙 동선 재구성</title><style>{CSS}</style></head>
<body>
<div class="header">
  <p class="kicker">HILLINK · Static Replay</p>
  <h1>정적 리플레이 — 실제 투숙 동선 재구성</h1>
  <p class="subtitle">LLM 없는 결정론 시뮬레이션 · 원장 데이터로 재구성한 실제 방문 흐름</p>
  <p class="meta">입력 <code>route_analysis.py --sample 12000 --seed 17</code> ·
     표본 {data['sample_days']:,} 체류-일 · 모집단 스케일 ×{data['population_scale']:.2f} ·
     시어터 제외(§12) {data['external_excluded_sample']:,}행 → 모집단 {fmt(data['external_excluded_population'])}행</p>
</div>

<div class="kpis">
  <div class="kpi"><div class="v">{fmt(data['revpar'])}</div><div class="l">RevPAR (원/객실박)</div></div>
  <div class="kpi"><div class="v">{data['occupancy']:.1%}</div><div class="l">객실박 점유율</div></div>
  <div class="kpi"><div class="v">{data['acceptance_rate']:.1%}</div><div class="l">동선 수용율</div></div>
  <div class="kpi"><div class="v">{fmt(data['visits_total'])}</div><div class="l">방문 (표본 환산)</div></div>
  <div class="kpi"><div class="v">{fmt(data['stay_hours_total'])}</div><div class="l">재실 시간 (h)</div></div>
  <div class="kpi"><div class="v">{data['accepted_days']:,}</div><div class="l">수용 체류-일</div></div>
</div>

<h2>시간대 × 업장 재실 히트맵 <span class="note">칸 = 재실(시간 환산) · 숫자에 커서를 올리면 상세</span></h2>
<table class="heat"><tr>{''.join(head)}</tr>{''.join(rows)}</table>

<h2>동선 전이 상위 15</h2>
<div class="flow">{''.join(flow_rows)}</div>

<h2>RevPASH (좌석·시간당 매출)</h2>
<table>
  <tr><th>업장</th><th>RevPASH</th><th>방문</th><th>피크 시간</th></tr>
  {''.join(revpash_rows)}
</table>

<h2>필터 — 비현실적 고객</h2>
<ul class="reasons">
  <li><strong>CLOSED {data['rejections'].get('CLOSED', 0)}일</strong> — 운영 창 밖: {closed}</li>
  <li><strong>SLOT_DUP {data['rejections'].get('SLOT_DUP', 0)}일</strong> — 같은 끼니에 좌식 식사 두 곳</li>
  <li>귀속 판정(§12·§14) 반영: 시어터는 외부 관객 혼입으로 투숙 동선에서 제외 — 수용율 {data['acceptance_rate']:.1%}는 투숙객 기준</li>
</ul>

<footer>
  재현: <code>python3 route_analysis.py --sample 12000 --seed 17 --json</code> → <code>python3 replay_viewer.py &lt;json&gt;</code><br>
  결정론 — 같은 명령은 항상 같은 그림 (seed 고정) · RevPAR/점유율은 원장 전체 기준 ·
  근거 등급: ◎ 공식 캡처 · 귀속 판정 근거는 리포트 §12~§15 참조
</footer>
</body></html>"""


def main(argv: list[str]) -> int:
    src = Path(argv[1]) if len(argv) > 1 else Path('/tmp/route_after.json')
    out = Path(argv[2]) if len(argv) > 2 else src.with_name('replay_reconstruction.html')
    data = json.loads(src.read_text())
    out.write_text(build(data))
    print(f"OK {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
