"""v3 — 메뉴 카테고리 축 추가 · α 조건 강화(카테고리 동일 필수) · 합의 후 실행용"""
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

# ── ⑥ 메뉴 카테고리 분류기 ─────────────────────────
LIQUOR = re.compile(r'와인|소주|맥주|위스키|사케|긴죠|다이긴죠|준마이|청주|고량주|발렌타인|조니워커|칵테일|샴페인|꼬냑|보드카|진\b|럼\b|데킬라|하이볼')
COURSE = re.compile(r'코스|상차림|수라상|정찬|도원경|만수무강|진계도|화룡|추미진향|SET|세트|디너|런치.*코스')
DESSERT = re.compile(r'디저트|케이크|빙수|아이스크림|마카롱|타르트|파르페|커피|에스프레소|아메리카노|라떼|티\b|주스|에이드')

def categorize(name, header_ctx):
    if LIQUOR.search(name): return '주류'
    if DESSERT.search(name): return '디저트·음료'
    if COURSE.search(name) or COURSE.search(header_ctx): return '코스·정찬'
    return '일품'

def price_band(p):
    if p < 50000: return '5만미만'
    if p < 100000: return '5-10만'
    if p < 200000: return '10-20만'
    return '20만+'

skus, current, header, section = [], None, '', ''
for line in text.splitlines():
    m = HDR.match(line)
    if m:
        current = next((k for k in OUTLET_TYPES if k in m.group(1)), None)
        header = line
        continue
    if current is None: continue
    if line.startswith('**'):          # 섹션(코스/일품/주류) 추적
        section = line
    if line.startswith('`'): header += ' ' + line
    if not line.startswith('|') or line.startswith('|---'): continue
    cells = [c.strip() for c in line.strip('| ').split('|')]
    groups = [cells]
    if current == '금룡' and len(cells) >= 7:
        groups = [cells[:3], cells[4:7]]
    for g in groups:
        if len(g) < 2: continue
        menu = next((c for c in g if re.search(r'[가-힣]', c) and len(c) >= 2 and c not in ('메뉴','코스')), '')
        pm = None
        for c in g:
            pm = re.fullmatch(r'([0-9]{3},[0-9]{3}|[0-9]{4,6})(\s*원)?', c)
            if pm: break
        if not pm or not menu: continue
        price = int(pm.group(1).replace(',', ''))
        if price < 3000 or price > 1_500_000: continue
        cat = categorize(menu, section)
        conds = sorted(set(COND.findall(line))) or sorted(set(COND.findall(header))) or ['조건없음']
        skus.append(dict(outlet=current, venue=OUTLET_TYPES[current], zone=ZONE[current],
                         meal=MEAL[current], name=menu[:35], price=price,
                         price_band=price_band(price), category=cat, access=conds[:2]))

print(f"SKU 추출: {len(skus)}개 · 업장 {len(set(s['outlet'] for s in skus))}개")
cats = defaultdict(int)
for s in skus: cats[s['category']] += 1
print(f"카테고리 분포: {dict(cats)}")

by_type = defaultdict(list)
for s in skus:
    by_type[s['venue']].append(s)

pairs = []
for otype, items in by_type.items():
    for a in items:
        for b in items:
            if a['outlet'] == b['outlet'] or a['price'] >= b['price']:
                continue
            if a['category'] != b['category']:   # ⑥ 카테고리 동일 필수
                continue
            shared = []
            if a['price_band'] == b['price_band']: shared.append('가격밴드')
            if set(a['meal']) & set(b['meal']): shared.append('끼니슬롯')
            if a['zone'] == b['zone']: shared.append('구역')
            if set(a['access']) & set(b['access']) - {'조건없음'}: shared.append('이용조건')
            if '가격밴드' in shared:
                # α = 카테고리 동일(전제) + 가격밴드 + 2축 이상
                grade = 'L2-α' if len(shared) >= 3 else 'L2-β'
                pairs.append(dict(src=f"{a['outlet']}.{a['name']}", dst=f"{b['outlet']}.{b['name']}",
                    cat=a['category'], price_a=a['price'], price_b=b['price'],
                    shared=shared, grade=grade))

by_src = defaultdict(list)
for p in pairs:
    by_src[p['src']].append(p)
top = []
for src, ps in sorted(by_src.items()):
    ps.sort(key=lambda x: (-len(x['shared']), x['price_b']))
    top.extend(ps[:3])

grades = defaultdict(int)
for p in top: grades[p['grade']] += 1
print(f"타 업장 L2 쌍: {len(pairs)}개 → 대표 {len(top)}개 · {dict(grades)}")
print("\n— 대표 샘플 —")
for p in top[:15]:
    print(f"  [{p['grade']}][{p['cat']}] {p['src']}({p['price_a']:,}) → {p['dst']}({p['price_b']:,}) [{','.join(p['shared'])}]")

out = dict(sku_count=len(skus), categories=dict(cats),
           total_pairs=len(pairs), representative=len(top), grades=dict(grades), sample=top)
(OUT / 'l2_sim_result_v3.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(f"\n→ l2_sim_result_v3.json 저장 (합의 후 D 팀장 전달용)")
