"""v2 — 타 업장 쌍만 · 절대가격대 · 코스 우선 · 금룡 2열 파서 수정"""
import re, json
from pathlib import Path
from collections import defaultdict

SRC = Path('/Users/robotjang/walkerhilling/walkerhilling/hillink/docs/walkerhill-guest-state-machine.md')
OUT = Path('/Users/robotjang/walkerhill_sim-KIMI/docs')
text = SRC.read_text(encoding='utf-8')

OUTLET_TYPES = {'온달':'정찬코스','금룡':'정찬코스','명월관':'정찬코스','MOEGI':'정찬코스',
    '더 뷔페':'뷔페','파빌리온':'라운지','LUX BAR':'바','피자힐':'테이크아웃','르 파사쥬':'델리'}
ZONE = {'온달':'그랜드','금룡':'그랜드','파빌리온':'그랜드','LUX BAR':'그랜드','르 파사쥬':'그랜드',
    '더 뷔페':'비스타','명월관':'비스타','MOEGI':'비스타','피자힐':'비스타'}
MEAL = {'온달':['중식','석식'],'금룡':['중식','석식'],'MOEGI':['중식','석식'],
    '명월관':['중식','석식'],'더 뷔페':['조식','중식','석식'],'파빌리온':['티타임'],
    'LUX BAR':['석식','야식'],'피자힐':['중식','석식'],'르 파사쥬':['전시간']}
HDR = re.compile(r'^### 4\.\d+ (.+?)(?:\s*—\s*(.+))?$')
COND = re.compile(r'2인 ?(?:이상|↑)|사전예약|성인 전용|투숙객|주중|주말|스마트캐주얼|멤버십')

def price_band(p):
    if p < 50000: return '5만미만'
    if p < 100000: return '5-10만'
    if p < 200000: return '10-20만'
    return '20만+'

skus, current, header = [], None, ''
for line in text.splitlines():
    m = HDR.match(line)
    if m:
        current = next((k for k in OUTLET_TYPES if k in m.group(1)), None)
        header = line
        continue
    if current is None: continue
    if line.startswith('`'): header += ' ' + line
    if not line.startswith('|') or line.startswith('|---'): continue
    cells = [c.strip() for c in line.strip('| ').split('|')]
    # 금룡 2열: [번호, 메뉴, 가격, '', 번호, 메뉴, 가격] 처리
    groups = [cells]
    if current == '금룡' and len(cells) >= 7:
        groups = [cells[:3], cells[4:7]]
    for g in groups:
        if len(g) < 2: continue
        # 메뉴 이름 = 한글 포함 셀
        menu = next((c for c in g if re.search(r'[가-힣]', c) and len(c) >= 2 and c != '메뉴' and c != '코스'), '')
        pm = None
        for c in g:
            pm = re.fullmatch(r'([0-9]{3},[0-9]{3}|[0-9]{4,6})(\s*원)?', c)
            if pm: break
        if not pm or not menu: continue
        price = int(pm.group(1).replace(',', ''))
        if price < 3000 or price > 1_500_000: continue
        conds = sorted(set(COND.findall(line))) or sorted(set(COND.findall(header))) or ['조건없음']
        skus.append(dict(outlet=current, venue=OUTLET_TYPES[current], zone=ZONE[current],
                         meal=MEAL[current], name=menu[:35], price=price,
                         price_band=price_band(price), access=conds[:2]))

print(f"SKU 추출: {len(skus)}개 · 업장 {len(set(s['outlet'] for s in skus))}개")

by_type = defaultdict(list)
for s in skus:
    by_type[s['venue']].append(s)

pairs = []
for otype, items in by_type.items():
    for a in items:
        for b in items:
            if a['outlet'] == b['outlet']:  # 타 업장만
                continue
            if a['price'] >= b['price']:
                continue
            shared = []
            if a['price_band'] == b['price_band']:
                shared.append('가격밴드')
            if set(a['meal']) & set(b['meal']):
                shared.append('끼니슬롯')
            if a['zone'] == b['zone']:
                shared.append('구역')
            if set(a['access']) & set(b['access']) - {'조건없음'}:
                shared.append('이용조건')
            if '가격밴드' in shared:
                pairs.append(dict(src=f"{a['outlet']}.{a['name']}", dst=f"{b['outlet']}.{b['name']}",
                    price_a=a['price'], price_b=b['price'], shared=shared,
                    grade='L2-α' if len(shared) >= 3 else 'L2-β'))

# 대표 쌍 선별: 출처·대상 이름 중복 제거 (같은 출처는 최대 3개 대표 연결만)
by_src = defaultdict(list)
for p in pairs:
    by_src[p['src']].append(p)
top = []
for src, ps in sorted(by_src.items()):
    ps.sort(key=lambda x: (-len(x['shared']), x['price_b']))
    top.extend(ps[:3])

grades = defaultdict(int)
for p in top:
    grades[p['grade']] += 1
print(f"타 업장 L2 쌍: {len(pairs)}개 → 대표 선별 {len(top)}개 · {dict(grades)}")
print("\n— 대표 샘플 —")
for p in top[:15]:
    print(f"  [{p['grade']}] {p['src']}({p['price_a']:,}) → {p['dst']}({p['price_b']:,}) [{','.join(p['shared'])}]")

out = dict(sku_count=len(skus), sku_by_type={k:len(v) for k,v in by_type.items()},
           total_pairs=len(pairs), representative=len(top), grades=dict(grades), sample=top)
(OUT / 'l2_sim_result.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(f"\n→ l2_sim_result.json 갱신 (대표 {len(top)}쌍 저장)")
