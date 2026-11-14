"""§4 메뉴 문서 → 5축 태그 부여 + L2 후보 연결 시뮬레이션 (A팀)"""
import re, json
from pathlib import Path
from collections import defaultdict

SRC = Path('/Users/robotjang/walkerhilling/walkerhilling/hillink/docs/walkerhill-guest-state-machine.md')
OUT = Path('/Users/robotjang/walkerhill_sim-KIMI/docs')
text = SRC.read_text(encoding='utf-8')

OUTLET_TYPES = {
    '온달': '정찬코스', '금룡': '정찬코스', '명월관': '정찬코스', 'MOEGI': '정찬코스',
    '더 뷔페': '뷔페', 'THE BUFFET': '뷔페',
    '파빌리온': '라운지', '클럽 라운지': '라운지', '더글라스 라운지': '라운지',
    'LUX BAR': '바', '르 파사쥬': '델리', '피자힐': '테이크아웃',
}
ZONE = {
    '온달': '그랜드', '금룡': '그랜드', '파빌리온': '그랜드', 'LUX BAR': '그랜드',
    '르 파사쥬': '그랜드', '클럽 라운지': '그랜드',
    '더 뷔페': '비스타', '명월관': '비스타', 'MOEGI': '비스타', '피자힐': '비스타',
    '더글라스 라운지': '더글라스',
}
MEAL = {
    '온달': ['중식','석식'], '금룡': ['중식','석식'], 'MOEGI': ['중식','석식'],
    '명월관': ['중식','석식'], '더 뷔페': ['조식','중식','석식'],
    '파빌리온': ['티타임','중식'], 'LUX BAR': ['석식','야식'],
    '더글라스 라운지': ['조식','티타임'], '피자힐': ['중식','석식'],
    '르 파사쥬': ['전시간'], '클럽 라운지': ['조식','티타임','야식'],
}
HDR = re.compile(r'^### 4\.\d+ (.+?)(?:\s*—\s*(.+))?$')
COND = re.compile(r'2인 ?(?:이상|↑)|사전예약|성인 전용|투숙객|주중|주말|스마트캐주얼|멤버십')

skus, current, header = [], None, ''
for line in text.splitlines():
    m = HDR.match(line)
    if m:
        current = next((k for k in OUTLET_TYPES if k in m.group(1)), None)
        header = line
        continue
    if current is None:
        continue
    if line.startswith('`'):
        header += ' ' + line
    if not line.startswith('|') or line.startswith('|---') or line.startswith('| #'):
        continue
    cells = [c.strip() for c in line.strip('| ').split('|')]
    pm = None
    for c in cells:
        pm = re.fullmatch(r'([0-9]{3},[0-9]{3}|[0-9]{4,6})(\s*원)?', c)
        if pm:
            break
    if not pm:
        continue
    price = int(pm.group(1).replace(',', ''))
    if price < 3000 or price > 1_500_000:
        continue
    menu = (cells[0] or cells[1])[:40].strip()
    if len(menu) < 2:
        continue
    conds = sorted(set(COND.findall(line))) or sorted(set(COND.findall(header))) or ['조건없음']
    band = f"{price//100000}십만대" if price >= 100000 else (f"{price//50000*5}만대" if price >= 50000 else "5만미만")
    skus.append(dict(outlet=current, venue=OUTLET_TYPES[current], zone=ZONE[current],
                     meal=MEAL[current], name=menu, price=price, price_band=band, access=conds[:2]))

print(f"SKU 추출: {len(skus)}개 · 업장 {len(set(s['outlet'] for s in skus))}개")

by_type = defaultdict(list)
for s in skus:
    by_type[s['venue']].append(s)

pairs, seen = [], set()
for otype, items in by_type.items():
    for a in items:
        for b in items:
            if a is b or a['price'] >= b['price'] or (a['name'], b['name']) in seen:
                continue
            seen.add((a['name'], b['name']))
            shared = []
            if abs(a['price'] - b['price']) <= 30000:
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

grades = defaultdict(int)
for p in pairs:
    grades[p['grade']] += 1
print(f"L2 후보 쌍: {len(pairs)}개 · {dict(grades)}")
print("\n— L2-α 샘플 (최대 8건) —")
for p in [x for x in pairs if x['grade'] == 'L2-α'][:8]:
    print(f"  {p['src']}({p['price_a']:,}) → {p['dst']}({p['price_b']:,}) [{','.join(p['shared'])}]")
print("\n— L2-β 샘플 (최대 6건) —")
for p in [x for x in pairs if x['grade'] == 'L2-β'][:6]:
    print(f"  {p['src']}({p['price_a']:,}) → {p['dst']}({p['price_b']:,}) [{','.join(p['shared'])}]")

out = dict(sku_count=len(skus), sku_by_type={k: len(v) for k, v in by_type.items()},
           pairs=len(pairs), grades=dict(grades), sample=pairs[:30])
(OUT / 'l2_sim_result.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
print(f"\n→ l2_sim_result.json 저장 완료")
