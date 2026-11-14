#!/usr/bin/env python3
"""MiroFish 리플레이 뷰어 — pricing_run.py JSON을 24배속 재생 HTML로 렌더링.

    python3 hillink/pricing_replay.py /tmp/mirofish_compare.json
    → /tmp/mirofish_replay.html
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CSS = """
:root {
  --ink: #1a1a1a; --muted: #57606a; --line: #d0d7de;
  --accent: #0b5394; --accent-soft: #eef3f9; --code-bg: #f6f8fa; --good: #1a7f37;
}
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 28px 34px 40px; max-width: 1180px;
  font-family: "Apple SD Gothic Neo", "NanumGothic", -apple-system, sans-serif;
  font-size: 14px; line-height: 1.62; color: var(--ink); background: #fbfcfd;
}
.header { border-bottom: 3px solid var(--accent); padding-bottom: 14px; margin-bottom: 20px; }
.kicker { font-size: 11px; letter-spacing: 3px; text-transform: uppercase; color: var(--accent); font-weight: 700; margin: 0 0 6px; }
h1 { font-size: 27px; margin: 0 0 7px; line-height: 1.25; }
.subtitle { font-size: 14px; color: var(--muted); margin: 0 0 10px; }
.meta { font-size: 12.5px; color: var(--muted); }
.meta code { background: var(--code-bg); border: 1px solid var(--line); border-radius: 3px; padding: 1px 6px; font-size: 11.5px; }
h2 { font-size: 17px; margin: 26px 0 10px; padding: 4px 0 4px 12px; border-left: 5px solid var(--accent); background: var(--accent-soft); }
.kpis { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin: 18px 0 6px; }
.kpi { background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 10px 12px; }
.kpi .v { font-size: 20px; font-weight: 800; color: var(--accent); letter-spacing: -0.5px; }
.kpi .l { font-size: 11px; color: var(--muted); margin-top: 2px; }
.kpi .v.good { color: var(--good); }
.controls { display: flex; align-items: center; gap: 10px; margin: 14px 0 10px; }
.controls button {
  border: 1px solid var(--line); background: #fff; border-radius: 6px;
  padding: 6px 14px; font-size: 13px; font-weight: 700; color: var(--ink); cursor: pointer;
}
.controls button.on { background: var(--accent); color: #fff; border-color: var(--accent); }
.daycounter { font-variant-numeric: tabular-nums; font-weight: 800; font-size: 15px; color: var(--accent); min-width: 130px; }
.chartbox { background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 14px; }
.bars { display: flex; align-items: flex-end; gap: 3px; height: 180px; }
.bars .bar { flex: 1; background: var(--accent); border-radius: 2px 2px 0 0; min-height: 0; position: relative; opacity: 0; transition: opacity .18s; }
.bars .bar.shown { opacity: .9; }
.occ { margin-top: 8px; }
table { width: 100%; border-collapse: collapse; margin: 8px 0 14px; font-size: 12.5px; background: #fff; }
th, td { border: 1px solid var(--line); padding: 5px 9px; text-align: left; }
th { background: #f2f5f8; font-weight: 700; white-space: nowrap; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.verdict { background: #fff; border: 1px solid var(--line); border-left: 5px solid var(--good); border-radius: 6px; padding: 12px 16px; margin: 12px 0; font-size: 14px; }
.verdict b { color: var(--good); }
footer { margin-top: 30px; padding-top: 12px; border-top: 2px solid var(--accent); font-size: 11.5px; color: var(--muted); }
footer code { background: var(--code-bg); border: 1px solid var(--line); border-radius: 3px; padding: 1px 6px; font-size: 11px; }
.tag { display: inline-block; background: var(--accent-soft); border: 1px solid var(--line); border-radius: 10px; padding: 1px 9px; font-size: 11px; color: var(--accent); font-weight: 700; margin-left: 6px; }
"""

JS = """
const DATA = __DATA__;
const $ = (s) => document.querySelector(s);
const won = (n) => Number(n).toLocaleString('ko-KR', {maximumFractionDigits: 0});

const SPEEDS = [1, 8, 24];
let speed = 24, playing = false, timer = null, cursor = 0;
const BASE_MS = 1500; // 1배속에서 1스텝 = 1.5초

function stepCount() { return DATA.experiment === 'evaluate' ? DATA.daily_revenue.length : DATA.trials.length; }
function renderStep(i) {
  if (DATA.experiment === 'evaluate') showDay(i); else showTrial(i);
}
function tick() {
  if (cursor >= stepCount()) { stop(); cursor = stepCount(); return; }
  renderStep(cursor); cursor++;
}
function play() {
  if (playing) return; playing = true;
  timer = setInterval(tick, BASE_MS / speed);
}
function stop() { playing = false; clearInterval(timer); }
function setSpeed(s) {
  speed = s;
  document.querySelectorAll('.controls button[data-speed]').forEach(b =>
    b.classList.toggle('on', Number(b.dataset.speed) === s));
  if (playing) { stop(); play(); }
}

function buildBars() {
  const vals = DATA.experiment === 'evaluate' ? DATA.daily_revenue : DATA.trials.map(t => t.revenue_a);
  const vmax = Math.max(...vals, 1);
  const box = $('#bars');
  vals.forEach((v, i) => {
    const b = document.createElement('div');
    b.className = 'bar';
    b.style.height = Math.max(2, v / vmax * 170) + 'px';
    b.title = `#${i + 1} — ${won(v)}원`;
    box.appendChild(b);
  });
}
function showDay(d) {
  const bars = document.querySelectorAll('#bars .bar');
  bars.forEach((b, i) => b.classList.toggle('shown', i <= d));
  let rev = 0; for (let i = 0; i <= d; i++) rev += DATA.daily_revenue[i];
  $('#counter').textContent = `Day ${d + 1} / ${DATA.daily_revenue.length}`;
  $('#cum').textContent = `누적 매출 ${won(rev)}원 · 점유율 ${(DATA.daily_occupancy[d]).toFixed(1)}%`;
}
function showTrial(t) {
  document.querySelectorAll('#bars .bar').forEach((b, i) => b.classList.toggle('shown', i <= t));
  const tr = DATA.trials;
  const shown = tr.slice(0, t + 1);
  const mean = shown.reduce((s, x) => s + x.delta, 0) / shown.length;
  $('#counter').textContent = `Trial ${t + 1} / ${tr.length}`;
  $('#cum').textContent = `평균 ΔV ${won(mean)}원`;
  const tbody = $('#trialbody'); if (tbody) {
    const row = tr[t];
    tbody.insertAdjacentHTML('beforeend',
      `<tr><td>${row.trial}</td><td class="num">${won(row.revenue_a)}</td><td class="num">${won(row.revenue_b)}</td><td class="num">${won(row.delta)}</td></tr>`);
  }
}

function init() {
  buildBars();
  $('#counter').textContent = '준비 — 재생을 누르세요';
  document.querySelectorAll('.controls button[data-speed]').forEach(b =>
    b.addEventListener('click', () => setSpeed(Number(b.dataset.speed))));
  $('#play').addEventListener('click', () => { if (cursor >= stepCount()) { cursor = 0; renderStep(0); } play(); });
  $('#reset').addEventListener('click', () => { stop(); cursor = 0; renderStep(0); });
  setSpeed(24);
}
init();
"""


def build(data: dict) -> str:
    exp = data.get('experiment', 'evaluate')
    if exp == 'compare':
        c = data['comparison']
        sp = data['speed']
        kpis = f"""
  <div class="kpi"><div class="v {'good' if c['delta_v'] >= 0 else ''}">{c['delta_v']:,.0f}</div><div class="l">ΔV = V(B)−V(A) (원)</div></div>
  <div class="kpi"><div class="v good">{c['revenue_uplift_pct']:+.2f}%</div><div class="l">매출 증가율</div></div>
  <div class="kpi"><div class="v">{c['mean_revpar_a']:,.0f}</div><div class="l">RevPAR A (정적)</div></div>
  <div class="kpi"><div class="v">{c['mean_revpar_b']:,.0f}</div><div class="l">RevPAR B ({'동적' if 'ynamic' in data['policy_b'] else data['policy_b'][:12]})</div></div>
  <div class="kpi"><div class="v">{c['t_statistic']:.2f}</div><div class="l">t (p={c['p_value']:.4f})</div></div>
  <div class="kpi"><div class="v">{sp['speedup_realtime_x']:,.0f}×</div><div class="l">실측 배속 (실시간 대비)</div></div>"""
        trials_table = """
<h2>시행 원장 — Paired A/B (동일 인구·동일 경합 순서)</h2>
<table><tr><th>시행</th><th>V(A) 정적</th><th>V(B) 동적</th><th>Δ</th></tr><tbody id="trialbody"></tbody></table>"""
        playback_caption = '시행 단위 재생 — 정적(A) 매출 막대'
        title = '쌍체 A/B 반사실 실험'
        verdict = f"""<div class="verdict">판정: 동적 정책(B)은 정적(A) 대비 평균 <b>{c['delta_v']:,.0f}원 ({c['revenue_uplift_pct']:+.2f}%)</b> —
 {len(data['trials'])}회 paired 시행, t={c['t_statistic']:.2f}·p={c['p_value']:.4f} · 동일 인구·동일 경합 순서(order_seed 고정)로 Δ는 순수 정책 효과</div>"""
    else:
        m = data['metrics']
        sp = data['speed']
        kpis = f"""
  <div class="kpi"><div class="v">{m['total_revenue']:,.0f}</div><div class="l">총 매출 V(π) (원)</div></div>
  <div class="kpi"><div class="v">{m['occupancy_rate_pct']:.1f}%</div><div class="l">점유율</div></div>
  <div class="kpi"><div class="v">{m['adr']:,.0f}</div><div class="l">ADR</div></div>
  <div class="kpi"><div class="v">{m['revpar']:,.0f}</div><div class="l">RevPAR</div></div>
  <div class="kpi"><div class="v">{m['booking_rate_pct']:.1f}%</div><div class="l">예약 전환율</div></div>
  <div class="kpi"><div class="v">{sp['speedup_realtime_x']:,.0f}×</div><div class="l">실측 배속 (실시간 대비)</div></div>"""
        trials_table = ""
        playback_caption = '일 단위 재생 — 일별 매출 막대'
        title = '단일 정책 전구간 평가'
        verdict = ""

    scale_tag = f"<span class='tag'>신의 눈 — 가격 ×{data['price_scale']:.2f}</span>" if data.get('price_scale', 1.0) != 1.0 else ""
    data_json = json.dumps(data, ensure_ascii=False).replace('</', '<\\/')
    js = JS.replace('__DATA__', data_json)

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HILLINK MiroFish — {title}</title><style>{CSS}</style></head>
<body>
<div class="header">
  <p class="kicker">HILLINK · MiroFish Replay</p>
  <h1>MiroFish — {title}</h1>
  <p class="subtitle">LLM 페르소나 의사결정 · 결정론 재판관 · paired 반사실 비교</p>
  <p class="meta">프리셋 <code>{data['preset']}</code> · LLM <code>{data['llm_mode']}</code>
     · 시드 <code>{data.get('seed', data.get('base_seed'))}</code>{scale_tag}</p>
</div>

<div class="kpis">{kpis}</div>

<h2>리플레이 <span class="note">{playback_caption} · 1×/8×/24× 배속 지원</span></h2>
<div class="controls">
  <button id="play">▶ 재생</button>
  <button id="reset">⟲ 처음</button>
  <button data-speed="1">1×</button>
  <button data-speed="8">8×</button>
  <button data-speed="24">24×</button>
  <span class="daycounter" id="counter"></span>
  <span id="cum" style="color:var(--muted);font-size:13px"></span>
</div>
<div class="chartbox"><div class="bars" id="bars"></div>
  <div class="occ" id="cum2" style="color:var(--muted);font-size:12px">칸에 커서를 올리면 값이 보입니다</div>
</div>
{trials_table}
{verdict}

<footer>
  재현: <code>python3 hillink/pricing_run.py --mode {exp} --preset {data['preset']}</code>
  → <code>python3 hillink/pricing_replay.py &lt;json&gt;</code> ·
  결정론 — 같은 시드는 항상 같은 리플레이 · mock 모드는 오프라인 결정론 에뮬레이터,
  GLM 실호 모드는 <code>--llm glm</code> (GLM_API_KEY 필요) ·
  근거: Majidi et al. 2026 Algorithm 1/2 · Huang &amp; Wang α̂ 캘리브레이션 연계
</footer>
<script>{js}</script>
</body></html>"""


def main(argv: list | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    src = Path(args[0]) if len(args) > 0 else Path('/tmp/mirofish_compare.json')
    out = Path(args[1]) if len(args) > 1 else src.with_name('mirofish_replay.html')
    out.write_text(build(json.loads(src.read_text())))
    print(f"OK {out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
