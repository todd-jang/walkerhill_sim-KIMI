#!/usr/bin/env python3
"""미로피쉬 리플레이 뷰어 — mirofish_run.py JSON을 24배속 재생 HTML로 렌더링.

    python3 hillink/mirofish_replay.py /tmp/mf_cmp.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

CSS = """
:root { --ink:#1a1a1a; --muted:#57606a; --line:#d0d7de; --accent:#0b5394;
  --accent-soft:#eef3f9; --code-bg:#f6f8fa; --good:#1a7f37; --bad:#b42318;
  /* Walkerhill 인상 팔레트(딥네이비·골드·크림) — 공식 가이드 캡처 없음(추정, 검증 전) */
  --wh-navy:#12233F; --wh-navy-2:#1F3A5F; --wh-gold:#B08A46; --wh-cream:#FAF7F2;
  --wh-line:#E3DCCF; --wh-soft:#F1EADC; --wh-muted:#6B6257; }
body.wh { background:var(--wh-cream); color:var(--wh-navy); --ink:var(--wh-navy);
  --muted:var(--wh-muted); --line:var(--wh-line); --accent:var(--wh-navy-2);
  --accent-soft:var(--wh-soft); --good:#2F6B46; --bad:#A33B2B; }
body.wh .header { border-bottom-color:var(--wh-gold); }
body.wh .kicker { color:var(--wh-gold); }
body.wh h2 { border-left-color:var(--wh-gold); background:var(--wh-soft); }
body.wh .kpi { border-color:var(--wh-line); }
body.wh .kpi .v { color:var(--wh-navy); }
body.wh .bars .bar { background:linear-gradient(180deg, var(--wh-navy) 0%, var(--wh-navy-2) 100%);
  border-top:3px solid var(--wh-gold); }
body.wh .controls button.on { background:var(--wh-navy); border-color:var(--wh-navy); }
body.wh .counter { color:var(--wh-navy); }
body.wh th { background:var(--wh-soft); }
body.wh .verdict { border-left-color:var(--wh-gold); }
body.wh footer { border-top-color:var(--wh-gold); }
* { box-sizing: border-box; }
body { margin:0 auto; padding:28px 34px 40px; max-width:1180px;
  font-family:"Apple SD Gothic Neo","NanumGothic",-apple-system,sans-serif;
  font-size:14px; line-height:1.62; color:var(--ink); background:#fbfcfd; }
.header { border-bottom:3px solid var(--accent); padding-bottom:14px; margin-bottom:20px; }
.kicker { font-size:11px; letter-spacing:3px; text-transform:uppercase; color:var(--accent); font-weight:700; margin:0 0 6px; }
h1 { font-size:27px; margin:0 0 7px; line-height:1.25; }
.subtitle { font-size:14px; color:var(--muted); margin:0 0 10px; }
.meta { font-size:12.5px; color:var(--muted); }
.meta code { background:var(--code-bg); border:1px solid var(--line); border-radius:3px; padding:1px 6px; font-size:11.5px; }
h2 { font-size:17px; margin:26px 0 10px; padding:4px 0 4px 12px; border-left:5px solid var(--accent); background:var(--accent-soft); }
h2 .note { float:right; font-size:11px; color:var(--muted); font-weight:400; padding-top:5px; }
.kpis { display:grid; grid-template-columns:repeat(6,1fr); gap:10px; margin:18px 0 6px; }
.kpi { background:#fff; border:1px solid var(--line); border-radius:6px; padding:10px 12px; }
.kpi .v { font-size:20px; font-weight:800; color:var(--accent); letter-spacing:-.5px; }
.kpi .l { font-size:11px; color:var(--muted); margin-top:2px; }
.kpi .v.good { color:var(--good); } .kpi .v.bad { color:var(--bad); }
.controls { display:flex; align-items:center; gap:10px; margin:14px 0 10px; flex-wrap:wrap; }
.controls button { border:1px solid var(--line); background:#fff; border-radius:6px;
  padding:6px 14px; font-size:13px; font-weight:700; color:var(--ink); cursor:pointer; }
.controls button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.counter { font-variant-numeric:tabular-nums; font-weight:800; font-size:15px; color:var(--accent); min-width:150px; }
.chartbox { background:#fff; border:1px solid var(--line); border-radius:6px; padding:14px; }
.bars { display:flex; align-items:flex-end; gap:3px; height:170px; }
.bars .bar { flex:1; background:var(--accent); border-radius:2px 2px 0 0; opacity:0; transition:opacity .18s; }
.bars .bar.shown { opacity:.9; }
.live { margin-top:10px; font-size:13px; color:var(--muted); display:flex; gap:22px; flex-wrap:wrap; }
.live b { color:var(--ink); font-variant-numeric:tabular-nums; }
table { width:100%; border-collapse:collapse; margin:8px 0 14px; font-size:12.5px; background:#fff; }
th,td { border:1px solid var(--line); padding:5px 9px; text-align:left; }
th { background:#f2f5f8; font-weight:700; white-space:nowrap; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
table.heat td { text-align:center; font-variant-numeric:tabular-nums; font-size:10.5px; padding:3px 4px; }
table.heat td span { min-width:44px !important; }
table.heat th:first-child, table.heat td:first-child { text-align:left; white-space:nowrap; font-weight:600; }
.verdict { background:#fff; border:1px solid var(--line); border-left:5px solid var(--good); border-radius:6px; padding:12px 16px; margin:12px 0; }
.verdict b { color:var(--good); }
.tag { display:inline-block; background:var(--accent-soft); border:1px solid var(--line);
  border-radius:10px; padding:1px 9px; font-size:11px; color:var(--accent); font-weight:700; margin-left:6px; }
footer { margin-top:30px; padding-top:12px; border-top:2px solid var(--accent); font-size:11.5px; color:var(--muted); }
footer code { background:var(--code-bg); border:1px solid var(--line); border-radius:3px; padding:1px 6px; font-size:11px; }
"""

JS = """
const DATA = __DATA__;
const ARM = DATA.experiment === 'compare' ? DATA.treatment : DATA;
const daily = ARM.daily;
const $ = (s) => document.querySelector(s);
const won = (n) => Number(n).toLocaleString('ko-KR', {maximumFractionDigits: 0});
const SPEEDS = [1, 8, 24];
let speed = 24, timer = null, cursor = 0, playing = false;
const BASE_MS = 1200;

function buildBars() {
  const vmax = Math.max(...daily.map(d => d.visits), 1);
  const box = $('#bars');
  daily.forEach((d) => {
    const b = document.createElement('div');
    b.className = 'bar';
    b.style.height = Math.max(2, d.visits / vmax * 160) + 'px';
    b.title = `Day ${d.day + 1} — 방문 ${d.visits}`;
    box.appendChild(b);
  });
}
function showDay(i) {
  document.querySelectorAll('#bars .bar').forEach((b, k) => b.classList.toggle('shown', k <= i));
  let visits = 0, cap = 0, dup = 0, closed = 0, reroute = 0;
  for (let k = 0; k <= i; k++) {
    const d = daily[k];
    visits += d.visits; cap += (d.reject_queue || 0); dup += d.reject_dup; closed += d.reject_closed;
    reroute += (d.reroutes || 0);
  }
  $('#counter').textContent = `Day ${daily[i].day + 1} / ${daily.length}`;
  $('#lv-visits').textContent = won(visits);
  $('#lv-reroute').textContent = won(reroute);
  $('#lv-cap').textContent = won(cap);
  $('#lv-dup').textContent = won(dup);
  $('#lv-closed').textContent = won(closed);
}
function tick() {
  if (cursor >= daily.length) { stop(); return; }
  showDay(cursor); cursor++;
}
function play() { if (playing) return; playing = true; timer = setInterval(tick, BASE_MS / speed); }
function stop() { playing = false; clearInterval(timer); }
function setSpeed(s) {
  speed = s;
  document.querySelectorAll('button[data-speed]').forEach(b => b.classList.toggle('on', Number(b.dataset.speed) === s));
  if (playing) { stop(); play(); }
}
function init() {
  buildBars();
  $('#play').addEventListener('click', () => { if (cursor >= daily.length) { cursor = 0; showDay(0); } play(); });
  $('#reset').addEventListener('click', () => { stop(); cursor = 0; showDay(0); });
  document.querySelectorAll('button[data-speed]').forEach(b =>
    b.addEventListener('click', () => setSpeed(Number(b.dataset.speed))));
  setSpeed(24);
  showDay(0);
}
init();
"""


WH_MAP = {
    "river": {"x": 0.0, "y": 0.855, "w": 1.0, "h": 0.145},
    "areas": [
        {"name": "숲 · 산책로", "x": 0.10, "y": 0.10, "w": 0.26, "h": 0.26, "color": "#E8EFE6"},
        {"name": "수영장", "x": 0.43, "y": 0.66, "w": 0.16, "h": 0.16, "color": "#DCE9F2", "round": True},
    ],
    "buildings": [
        {"name": "비스타 워커힐", "x": 0.10, "y": 0.36, "w": 0.28, "h": 0.28},
        {"name": "그랜드 워커힐", "x": 0.52, "y": 0.30, "w": 0.28, "h": 0.34},
        {"name": "더글라스 하우스", "x": 0.30, "y": 0.08, "w": 0.16, "h": 0.14},
    ],
    "venues": {
        "SKYARD": [0.17, 0.40], "LUX_BAR": [0.14, 0.57], "THE_PAVILION": [0.30, 0.56],
        "THE_BUFFET": [0.64, 0.47], "LE_PASSAGE": [0.56, 0.57], "MYONGWOLGWAN": [0.72, 0.38],
        "ONDAL": [0.70, 0.56], "GEUMRYONG": [0.59, 0.36], "MOEGI": [0.76, 0.51],
        "WELLNESS_SAUNA": [0.67, 0.60], "GRAND_POOL": [0.51, 0.74], "PIZZA_HILL": [0.63, 0.72],
        "FOREST_PARK": [0.22, 0.20],
    },
    "labels": {
        "SKYARD": "스카이야드", "LUX_BAR": "럭스바", "THE_PAVILION": "파빌리온",
        "THE_BUFFET": "더뷔페", "LE_PASSAGE": "르파사쥬", "MYONGWOLGWAN": "명월관",
        "ONDAL": "온달", "GEUMRYONG": "금룡", "MOEGI": "모에기",
        "WELLNESS_SAUNA": "사우나", "GRAND_POOL": "수영장", "PIZZA_HILL": "피자힐",
        "FOREST_PARK": "산책로",
    },
}

SWARM_JS = r'''const SWARM = DATA.swarm;
(function () {
  if (!SWARM) return;
  const cv = document.getElementById('swarm'); if (!cv) return;
  const ctx = cv.getContext('2d', {willReadFrequently: true});
  const W = cv.width, H = cv.height;
  const MAP = __MAP__;
  const WH = document.body.classList.contains('wh');
  const COLORS = WH
    ? {FAMILY:'#1F3A5F', COUPLE:'#6B4E7A', BUSINESS:'#2F6B46', LUXURY:'#B08A46', LEISURE:'#4E7C7A'}
    : {FAMILY:'#0b5394', COUPLE:'#7b3fa0', BUSINESS:'#1a7f37', LUXURY:'#b8860b', LEISURE:'#0f7c8a'};
  const HUB_FILL = WH ? '#F6EFE2' : '#eef3f9';
  const HUB_STROKE = WH ? '#B08A46' : '#0b5394';
  const HUB_TEXT = WH ? '#12233F' : '#1a1a1a';
  const REJECT = WH ? '#A33B2B' : '#b42318';
  const LOBBY = [W * 0.58, H * 0.50];

  const venues = Object.keys(MAP.venues).filter(v => ARM.venue_slot_fill && ARM.venue_slot_fill[v]);
  const pos = {};
  venues.forEach(v => { const xy = MAP.venues[v]; pos[v] = [xy[0] * W, xy[1] * H]; });

  const dots = {};
  SWARM.agents.forEach((a, i) => {
    dots[a.i] = {arch: a.a, x: LOBBY[0] + (i % 7) * 3, y: LOBBY[1] + Math.floor(i / 7) * 3,
                 tx: LOBBY[0], ty: LOBBY[1], ok: 1, active: 0, seen: 0,
                 r: 2.8 + (i % 3) * 0.6};
  });
  let fi = 0, playing = false, timer = null, swSpeed = 24, seed = 7;
  function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }

  function setFrame(f) {
    const fr = SWARM.frames[f]; if (!fr) return;
    Object.values(dots).forEach(d => { d.active = 0; });
    let rej = 0;
    fr.a.forEach(([id, v, ok]) => {
      const d = dots[id], hub = pos[v]; if (!d || !hub) return;
      d.tx = hub[0] + (rnd() - 0.5) * 46;
      d.ty = hub[1] + (rnd() - 0.5) * 40 + 8;
      d.active = 1; d.seen = 1; d.ok = ok;
      if (!ok) rej++;
    });
    document.getElementById('sw-counter').textContent = `Day ${fr.d + 1} · ${fr.s}`;
    document.getElementById('sw-frame').textContent = `${f + 1} / ${SWARM.frames.length}`;
    document.getElementById('sw-rej').textContent = rej;
  }

  function rr(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath();
  }

  function drawMap() {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = WH ? '#FAF7F2' : '#fbfcfd'; ctx.fillRect(0, 0, W, H);
    const rv = MAP.river;
    ctx.fillStyle = WH ? '#D9E6EF' : '#dbeafe';
    ctx.fillRect(rv.x * W, rv.y * H, rv.w * W, rv.h * H);
    ctx.fillStyle = WH ? '#5B7C93' : '#6b8fb3';
    ctx.font = '600 11px "Apple SD Gothic Neo", sans-serif'; ctx.textAlign = 'left';
    ctx.fillText('한강', rv.x * W + 14, rv.y * H + 26);
    MAP.areas.forEach(a => {
      ctx.fillStyle = a.color;
      if (a.round) {
        ctx.beginPath();
        ctx.ellipse((a.x + a.w / 2) * W, (a.y + a.h / 2) * H, a.w * W / 2, a.h * H / 2, 0, 0, Math.PI * 2);
        ctx.fill();
      } else {
        rr(a.x * W, a.y * H, a.w * W, a.h * H, 18); ctx.fill();
      }
      ctx.fillStyle = WH ? '#6B6257' : '#57606a';
      ctx.font = '600 10px "Apple SD Gothic Neo", sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(a.name, (a.x + a.w / 2) * W, (a.y + a.h / 2) * H + 4);
    });
    MAP.buildings.forEach(b => {
      rr(b.x * W, b.y * H, b.w * W, b.h * H, 10);
      ctx.fillStyle = '#ffffff'; ctx.fill();
      ctx.strokeStyle = WH ? '#CFC6B6' : '#d0d7de'; ctx.lineWidth = 1.4; ctx.stroke();
      ctx.fillStyle = WH ? '#12233F' : '#1a1a1a';
      ctx.font = '700 11px "Apple SD Gothic Neo", sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(b.name, (b.x + b.w / 2) * W, b.y * H - 7);
    });
    const codes = venues;
    ctx.strokeStyle = WH ? 'rgba(176,138,70,0.38)' : 'rgba(11,83,148,0.22)';
    ctx.setLineDash([3, 5]); ctx.lineWidth = 1.2;
    for (let i = 0; i < codes.length; i++) {
      for (let j = i + 1; j < codes.length; j++) {
        const a = pos[codes[i]], b = pos[codes[j]];
        if (Math.hypot(a[0] - b[0], a[1] - b[1]) > 300) continue;
        ctx.beginPath(); ctx.moveTo(a[0], a[1]);
        ctx.quadraticCurveTo((a[0] + b[0]) / 2 + 14, (a[1] + b[1]) / 2 - 14, b[0], b[1]);
        ctx.stroke();
      }
    }
    ctx.setLineDash([]);
  }

  let lastT = 0;
  function draw() {
    const now = performance.now();
    const dt = lastT ? Math.min(0.05, (now - lastT) / 1000) : 0.016; lastT = now;
    drawMap();
    venues.forEach(v => {
      const xy = pos[v];
      ctx.beginPath(); ctx.arc(xy[0], xy[1], 12, 0, Math.PI * 2);
      ctx.fillStyle = HUB_FILL; ctx.fill();
      ctx.strokeStyle = HUB_STROKE; ctx.lineWidth = 1.4; ctx.stroke();
      ctx.fillStyle = HUB_TEXT; ctx.font = '700 10px "Apple SD Gothic Neo", sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(MAP.labels[v] || v, xy[0], xy[1] + 26);
    });
    let moving = 0;
    const SPEED = 30 * swSpeed;
    Object.values(dots).forEach(d => {
      const dx = d.tx - d.x, dy = d.ty - d.y, dist = Math.hypot(dx, dy);
      if (d.active && dist > 1.5) {
        const step = Math.min(dist, SPEED * dt);
        d.x += dx / dist * step; d.y += dy / dist * step; moving++;
      }
      if (!d.seen) return;
      ctx.beginPath(); ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
      ctx.fillStyle = d.active ? (d.ok ? (COLORS[d.arch] || '#57606a') : REJECT) : (WH ? '#CFC6B6' : '#9aa4ae');
      ctx.globalAlpha = d.active ? 0.95 : 0.14; ctx.fill(); ctx.globalAlpha = 1;
      if (d.active && !d.ok) {
        ctx.beginPath(); ctx.arc(d.x, d.y, d.r + 2.4, 0, Math.PI * 2);
        ctx.strokeStyle = REJECT; ctx.lineWidth = 1; ctx.stroke();
      }
    });
    document.getElementById('sw-moving').textContent = moving;
    requestAnimationFrame(draw);
  }

  function tick() {
    if (fi >= SWARM.frames.length) { if (timer) clearInterval(timer); timer = null; playing = false; return; }
    setFrame(fi); fi++;
  }
  function play() {
    if (playing) return; playing = true;
    if (fi >= SWARM.frames.length) fi = 0;
    timer = setInterval(tick, 900 / swSpeed);
  }
  function stop() { playing = false; if (timer) clearInterval(timer); timer = null; }
  function setSwSpeed(v) {
    swSpeed = v;
    document.querySelectorAll('button[data-sw-speed]').forEach(b =>
      b.classList.toggle('on', Number(b.dataset.swSpeed) === v));
    if (playing) { stop(); play(); }
  }
  document.getElementById('sw-play').addEventListener('click', play);
  document.getElementById('sw-reset').addEventListener('click', () => {
    stop(); fi = 0;
    Object.values(dots).forEach(d => { d.x = LOBBY[0]; d.y = LOBBY[1]; d.active = 0; d.seen = 0; });
    setFrame(0);
    document.getElementById('sw-counter').textContent = '준비 — 재생을 누르세요';
  });
  document.querySelectorAll('button[data-sw-speed]').forEach(b =>
    b.addEventListener('click', () => setSwSpeed(Number(b.dataset.swSpeed))));
  setSwSpeed(24);
  setFrame(0);
  requestAnimationFrame(draw);
})();
'''


def heat_table(venue_slot: dict) -> str:
    slots = sorted({sl for row in venue_slot.values() for sl in row})
    head = "<tr><th>업장</th>" + "".join(f"<th>{s}</th>" for s in slots) + "</tr>"
    rows = []
    for venue, row in venue_slot.items():
        cells = []
        for s in slots:
            r = row.get(s, 0.0)
            alpha = r * 0.8 + (0.05 if r > 0 else 0)
            color = '#fff' if alpha > 0.45 else 'var(--ink)'
            cells.append(f"<td><span style='display:inline-block;min-width:52px;border-radius:3px;"
                         f"background:rgba(11,83,148,{alpha:.2f});color:{color};padding:2px 4px'>{r:.0%}</span></td>")
        rows.append(f"<tr><td>{venue}</td>{''.join(cells)}</tr>")
    return (f"<div style='overflow-x:auto'><table class='heat' style='font-size:11px'>{head}"
            f"{''.join(rows)}</table></div>")


def build(data: dict, replay_only: bool = False, theme: str = "walkerhill") -> str:
    is_cmp = data.get("experiment") == "compare"
    arm = data["treatment"] if is_cmp else data
    s = arm["summary"]
    meta = arm["meta"]
    sp = arm["speed"]
    rejects = s["rejections"]
    total_rej = sum(rejects.values())

    if is_cmp:
        d = data["delta"]
        cls_v = "good" if d["visits"] >= 0 else "bad"
        cls_sp = "good" if d["spend_proxy"] >= 0 else "bad"
        kpis = f"""
  <div class="kpi"><div class="v {cls_v}">{d['visits']:+,}</div><div class="l">Δ방문 (vs 기준)</div></div>
  <div class="kpi"><div class="v {cls_sp}">{d['spend_proxy']:+,.1f}</div><div class="l">Δ지출 프록시</div></div>
  <div class="kpi"><div class="v">{s['accept_rate']:.1%}</div><div class="l">수용률 (처치)</div></div>
  <div class="kpi"><div class="v">{s.get('reroutes', 0):,}</div><div class="l">재경로 (처치)</div></div>
  <div class="kpi"><div class="v">{s['unique_guests_served']:,}</div><div class="l">서비스 손님 수</div></div>
  <div class="kpi"><div class="v">{sp['speedup_realtime_x']:,.0f}×</div><div class="l">실측 배속</div></div>"""
        verdict = (f"<div class='verdict'>판정: <b>{data['scenario']}</b> 시나리오는 기준 대비 방문 "
                   f"<b>{d['visits']:+,}</b> · 지출 프록시 <b>{d['spend_proxy']:+,.1f}</b> · 수용률 {d['accept_rate']:+.2%}pp · "
                   f"대기 포기(QUEUE) {d['rejections']['QUEUE']:+,} — 동일 손님·동일 시드 paired 비교</div>")
    else:
        kpis = f"""
  <div class="kpi"><div class="v">{s['visits']:,}</div><div class="l">방문 (수용)</div></div>
  <div class="kpi"><div class="v">{s['accept_rate']:.1%}</div><div class="l">수용률</div></div>
  <div class="kpi"><div class="v">{s.get('reroutes', 0):,}</div><div class="l">재경로 (거절→대안)</div></div>
  <div class="kpi"><div class="v">{s.get('avg_wait_min', 0):.1f}분</div><div class="l">평균 대기 / 이동 {s.get('avg_travel_min', 0):.1f}분</div></div>
  <div class="kpi"><div class="v">{s['unique_guests_served']:,}</div><div class="l">서비스 손님 수</div></div>
  <div class="kpi"><div class="v">{sp['speedup_realtime_x']:,.0f}×</div><div class="l">실측 배속</div></div>"""
        verdict = ""

    health = arm.get("llm_health", {})
    banner = ""
    if meta.get("llm_mode") != "mock" and health.get("fallbacks"):
        detail = ", ".join(health.get("errors", [])[:2])
        banner = (f"<div class='verdict' style='border-left-color:var(--bad)'>"
                  f"<b>경고 — 이 결과는 실LLM 결과가 아닙니다</b>: 일부 라운드가 mock 대체로 실행됨 "
                  f"(fallback {health.get('fallbacks', 0)} · {detail}). 재실행 필요.</div>")

    tags = ""
    if meta.get("price_index"):
        tags += "<span class='tag'>신의 눈 — 가격지수 " + ", ".join(
            f"{k}×{v:.2f}" for k, v in meta["price_index"].items()) + "</span>"
    if meta.get("closed_venues"):
        tags += "<span class='tag'>폐쇄 — " + ", ".join(meta["closed_venues"]) + "</span>"
    if meta.get("capacity_scale", 1.0) != 1.0:
        tags += f"<span class='tag'>수용량 ×{meta['capacity_scale']:.2f}</span>"

    rej_rows = "".join(
        f"<tr><td>Day {e['day'] + 1}</td><td>{e['slot']}</td><td>{e['agent']}</td><td>{e['venue']}</td>"
        f"<td>{e['party']}</td><td>{e['reason']}</td></tr>"
        for e in arm["rejections_sample"][:10]) or (
        "<tr><td colspan='6' style='color:var(--muted)'>거절 없음 — 이 규모에서는 폐쇄·슬롯·좌석 경합이 발생하지 않았습니다</td></tr>")
    acc_rows = "".join(
        f"<tr><td>Day {e['day'] + 1}</td><td>{e['slot']}</td><td>{e['agent']}</td><td>{e['venue']}</td>"
        f"<td>{e['party']}</td><td>{e['archetype']}</td></tr>"
        for e in arm["events_sample"][:10]) or (
        "<tr><td colspan='6' style='color:var(--muted)'>수용 없음</td></tr>")

    js = JS.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    if data.get("swarm") and not replay_only:
        js += "\n" + SWARM_JS.replace("__MAP__", json.dumps(WH_MAP, ensure_ascii=False))
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HILLINK MiroFish — 라운드 세계</title><style>{CSS}</style></head>
<body class="{'wh' if theme == 'walkerhill' else ''}">
<div class="header">
  <p class="kicker">HILLINK · MiroFish World</p>
  <h1>미로피쉬 — 라운드 기반 멀티에이전트 세계</h1>
  <p class="subtitle">손님 의도 JSON → 결정론 재판관(폐쇄·슬롯·좌석) → 상태 피드백 루프</p>
  <p class="meta">LLM <code>{meta['llm_mode']}</code> · {meta['horizon']}일 · 손님 {meta['guests']:,} · 시드 <code>{meta['seed']}</code>{tags}</p>
</div>
<div class="kpis">{kpis}</div>
{verdict}
{banner}

{'<h2>Swarm — 페르소나 동시 이동 <span class="note">점 1개 = 페르소나 1명 · 원 = 업장 허브 · 빨강 = 좌석 거절</span></h2>' + chr(10) + '<div class="controls"><button id="sw-play">▶ 재생</button><button id="sw-reset">⟲ 처음</button><button data-sw-speed="1">1×</button><button data-sw-speed="8">8×</button><button data-sw-speed="24">24×</button><span class="counter" id="sw-counter">준비 — 재생을 누르세요</span></div>' + chr(10) + '<canvas id="swarm" width="1040" height="520" style="width:100%;height:auto;background:#fff;border:1px solid var(--line);border-radius:6px"></canvas>' + chr(10) + '<div class="live" style="margin-top:6px;font-size:12px"><span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#1F3A5F;margin-right:4px"></span>가족 · <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#6B4E7A;margin-right:4px"></span>커플 · <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#2F6B46;margin-right:4px"></span>비즈니스 · <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#B08A46;margin-right:4px"></span>럭셔리 · <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#4E7C7A;margin-right:4px"></span>휴식 · <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:#A33B2B;margin-right:4px"></span>좌석 거절 · <span style="color:var(--muted)">도식 배치(공식 배치도 아님 · 추정)</span></div>' + chr(10) + '<div class="live" style="margin-top:8px">프레임 <b id="sw-frame">-</b> · 이동 중 <b id="sw-moving">0</b> · 이번 프레임 좌석 거절 <b id="sw-rej">0</b></div>' if data.get("swarm") else ""}

<h2>리플레이 <span class="note">일 단위 재생 · 1×/8×/24× 배속</span></h2>
<div class="controls">
  <button id="play">▶ 재생</button><button id="reset">⟲ 처음</button>
  <button data-speed="1">1×</button><button data-speed="8">8×</button><button data-speed="24">24×</button>
  <span class="counter" id="counter"></span>
</div>
<div class="chartbox">
  <div class="bars" id="bars"></div>
  <div class="live">누적 방문 <b id="lv-visits">0</b> · 재경로 <b id="lv-reroute">0</b> ·
    대기 포기 <b id="lv-cap">0</b> · 슬롯 중복 <b id="lv-dup">0</b> · 폐쇄 <b id="lv-closed">0</b></div>
</div>
<h2>업장 × 슬롯 평균 점유율 <span class="note">개장일 기준 평균 — 좌석 대비 수용 인원</span></h2>
{heat_table(arm['venue_slot_fill'])}
<h2>거절 원장 (샘플 10)</h2>
<table><tr><th>일</th><th>슬롯</th><th>손님</th><th>업장</th><th>인원</th><th>사유</th></tr>{rej_rows}</table>
<h2>수용 원장 (샘플 10)</h2>
<table><tr><th>일</th><th>슬롯</th><th>손님</th><th>업장</th><th>인원</th><th>아키타입</th></tr>{acc_rows}</table>
<footer>
  재현: <code>python3 hillink/mirofish_run.py --mode {'compare' if is_cmp else 'run'} --days {meta['horizon']} --guests {meta['guests']} --seed {meta['seed']}</code>
  → <code>python3 hillink/mirofish_replay.py &lt;json&gt;</code> ·
  결정론 — mock은 같은 시드에 항상 같은 세계 · GLM 실호는 <code>--llm glm</code> (GLM_API_KEY 필요) ·
  운영 창·좌석 근거: walkerhill_timeline_validator ◎ · prices.SEATS ◎
</footer>
<script>{js}</script>
</body></html>"""


def main(argv: list | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    flags = [a for a in argv if a.startswith("--")]
    args = [a for a in argv if not a.startswith("--")]
    replay_only = "--replay-only" in flags
    theme = "plain" if "--plain" in flags else "walkerhill"
    src = Path(args[0]) if args else Path("/tmp/mf_cmp.json")
    out = Path(args[1]) if len(args) > 1 else src.with_name("mirofish_world.html")
    out.write_text(build(json.loads(src.read_text()), replay_only=replay_only, theme=theme),
                   encoding="utf-8")
    print(f"OK {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
