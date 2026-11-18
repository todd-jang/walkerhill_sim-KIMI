"""워커힐 Xmas 시즌 가격 모니터링 — hillink verified_price_catalog 스키마 정확 일치
준수사항 내장: 공식 페이지 확인만 기록 / 실거래 확인 금지 / 요청 최소화 (일 2~4회, 수동 실행 기준)
기간: 2026-11-14 ~ 2026-12-05 (약 3주)  운용: 사람이 실행할 때마다 1 사이클 기록
"""
import json, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
OUT = Path(__file__).parent / "seasonal_price_references.json"
HISTORY = Path(__file__).parent / "xmas_monitor_log.json"

# ── 모니터링 대상 SKU (xmas_2026_hypotheses.md ◎/◆ 기록 기반 초기값) ──
SKUS = [
    dict(sku_id="SANTA-NIGHT-VILLAGE-VISTA-STANDARD",   outlet_id="VISTA",  label="산타나잇빌리지 비스타 스탠다드",
         grade_in="◆v3.4", last={"2026-12-24 피크": 978000}),
    dict(sku_id="SANTA-NIGHT-VILLAGE-VISTA-DELUXE",     outlet_id="VISTA",  label="산타나잇빌리지 비스타 디럭스",
         grade_in="◆v3.4", last={"2026-12-24 피크": 1130000}),
    dict(sku_id="SANTA-NIGHT-VILLAGE-VISTA-SIGNATURE",  outlet_id="VISTA",  label="산타나잇빌리지 비스타 시그니처",
         grade_in="◆v3.4", last={"피크 외": 757000}),
    dict(sku_id="SANTA-NIGHT-VILLAGE-GRAND-STD-DELUXE", outlet_id="GRAND",  label="산타나잇빌리지 그랜드 스탠다드~딜럭스",
         grade_in="◎공개가", last={"시작가": 457000}),
    dict(sku_id="SANTA-NIGHT-VILLAGE-DOUGLAS",          outlet_id="DOUGLAS",label="산타나잇빌리지 더글라스(성인전용)",
         grade_in="◎공개가", last={"시작가": 529000}),
    dict(sku_id="SANTA-KIDS-VILLAGE-EXTRA-CHILD-ADV",   outlet_id="FOREST_PARK", label="키즈빌리지 추가티켓 사전 아동",
         grade_in="◎contract", last={"사전": 135000}),
    dict(sku_id="SANTA-KIDS-VILLAGE-EXTRA-CHILD-ONSITE",outlet_id="FOREST_PARK", label="키즈빌리지 추가티켓 현장 아동",
         grade_in="◎contract", last={"현장": 150000}),
]

PROMPT = """── Xmas 가격 확인 기록 (수동 관찰 입력) ─────────────────
관찰 규칙: 앱/웹에서 '견적가'를 보고 기록 (예약 진행·재고 확인 금지)
1. 대상 SKU 번호 선택 (콤마 복수 가능, Enter=전체): """ 

def record(sku, amount, stay_date, media, observer):
    now = datetime.now(KST).isoformat()
    rec = {
        "sku_id": sku["sku_id"], "outlet_id": sku["outlet_id"], "label": sku["label"],
        "currency": "KRW", "listed_unit_amount": amount,
        "price_status": "OFFICIAL_PAGE_CHECKED" if media in ("web", "app") else "USER_REPORTED",
        "price_source_id": "APP-OBS" if media == "app" else "WEB-OBS",
        "checked_at_kst": now,
        "conditions": {"months": [11, 12], "stay_date": stay_date,
                       "day_rule": "12/24-25 피크일 별도", "tax_service": "포함(앱 표기 기준)"},
        "tax_included": "YES_PER_APP_DISPLAY",
        "service_charge_included": "UNKNOWN",
        "customer_payment_status": "NOT_A_TRANSACTION",
        "auto_bill_allowed": False,
        "limits": ["견적가 기록 · 실거래·재고 미확인", f"관찰 매체: {media}", f"관찰자: {observer}"],
        "record_kind": "POSTED_PRICE",
        "source_amount_confirmed": True,
        "transaction_confirmed": False,
        "price_application_approved": False,
        "grade": "◎" if media == "web" else "◆",   # 웹 직접 캡처 시 ◎, 앱 관찰은 ◆에서 시작
        "grade_promotion_ready": media == "web",
    }
    return rec

def main():
    print(PROMPT)
    for i, s in enumerate(SKUS, 1):
        print(f"  {i}. [{s['grade_in']}] {s['label']} (기존: {s['last']})")
    sel = input("선택: ").strip()
    idxs = [int(x) for x in sel.split(",")] if sel else list(range(1, len(SKUS)+1))
    observer = input("관찰자(이니셜): ").strip() or "unknown"
    media = input("매체 (web/app): ").strip() or "app"

    log = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else {"records": []}
    new = []
    for i in idxs:
        s = SKUS[i-1]
        amount = int(input(f"\n{i}. {s['label']} — 확인한 견적가(원, 숫자만): ").strip().replace(",", ""))
        stay = input(f"   해당 가격의 투숙일 (예: 2026-12-24): ").strip() or "?"
        rec = record(s, amount, stay, media, observer)
        new.append(rec)
        # ◆ 기록과 대조
        for k, v in s["last"].items():
            if v == amount:
                print(f"   ✓ 기존 기록({s['grade_in']}: {k}={v:,})와 일치 → 교차 확인")
            elif amount < v:
                print(f"   ⚠ 기존({v:,})보다 낮음 → 비피크일 견적이거나 가격 하향 · 날짜 재확인")
            else:
                print(f"   ⚠ 기존({v:,})보다 높음 → 피크일 전환 또는 가격 인상 가능성")
        # 승격 판정
        if media == "web" and s["grade_in"].startswith("◆"):
            print(f"   ▲ 웹 직접 확인 → {s['grade_in']} → ◎ 승격 후보 (캡처 보관 시 확정)")

    log["records"].extend(new)
    HISTORY.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n기록 {len(new)}건 저장 → {HISTORY.name} (누적 {len(log['records'])}건)")
    print("※ 시즌 종료 후 C 협의로 seasonal_price_references.json(원장)에 정식 반영")

if __name__ == "__main__":
    main()
