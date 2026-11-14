# analysis/hillink — HILLINK 실데이터 적합 스냅샷 (협업용)

- **복사일**: 2026-11-14
- **출처**: `rrobotjang/walkerhilling` (hillink 브랜치) — 커밋 `6e0e3bb`(코드·리포트) / `14ea353`(크리스마스 문서)
- **성격**: 단방향 스냅샷. 원본이 바뀌어도 이 폴더는 자동 갱신되지 않는다. 최신이 필요하면 원본 커밋 해시를 확인한다.
- **데이터 민감도**: 매출 원장은 **직접 식별자(이름·연락처·객실번호) 없음** (성별·연령대·구성·목적·결제액 수준). **private 레포 전용** — 공개 레포에 올리지 않는다.

## 구조 (원본 상대 경로 유지 — 코드 수정 없이 실행됨)

```
analysis/
  hillink/          ← research/ 원본 그대로
    persona_exposure.py     논문 Huang & Wang (arXiv 2606.16183) Algorithm 1 적합 + n_step
    hillink_fit.py          3원장 → Algorithm 1 입력 변환 어댑터
    pricing_sim.py          Algorithm 1 Phase1-4 + Algorithm 2 paired 시뮬레이션
    route_analysis.py       운영창 기반 동선 재구성 · 필터 5종 · RevPAR/RevPASH
    test_*.py               테스트 62개 (스킵 1)
    xmas_2026_hypotheses.md 크리스마스 2026 상품 분석 v3.6 (사실·가설 분리)
    persona_exposure_report.md / .pdf / _print.html   리포트 4p
  timeline/
    prices.py, walkerhill_timeline_validator.py      ← hillink 의존성 모듈
    revenue_l5.csv (15MB, 111,386행) · persona_grid.csv · menu.csv
```

## 데이터 범위

- 기간: **2026-10-01 ~ 2026-11-02 (33일)**
- 규모: 셀 121 × 페르소나 54 × 체류 30,126 · 수요 1~7,241(합계 67,686)
- `revenue_l5.csv`는 **BOM(utf-8-sig)** — 읽을 때 `encoding='utf-8-sig'` 필수

## 재현 명령 (`analysis/hillink/`에서)

```bash
# 적합 (plain, 약 7초)
python3 hillink_fit.py
# 보정 적합 (약 61초)
python3 hillink_fit.py --calibrated
# 가격 시뮬레이션 (Algorithm 1·2)
python3 pricing_sim.py --elasticity 1.0 1.5 2.0 --trials 200
# 동선 분석
python3 route_analysis.py --sample 12000
# 전체 테스트 (62개)
python3 -W error::ResourceWarning -m unittest test_persona_exposure test_pricing_sim test_route_analysis
```

## 핵심 결과 (커밋 시점)

- plain 적합: **N̂=30,241** vs 실제 30,126 (오차 0.4%) · V=392.1548
- calibrated: N̂=34,241 · â=+0.0171 · b̂=1.0008
- RevPAR 288,621원 · 점유율 84.7% · RevPASH 뷔페 37,418 > 피자힐 27,353 > 명월관 23,036

## 가정·한계 (리포트 §7·xmas 문서 §2와 동일)

1. RevPAR/RevPASH 절대값은 **결제액 기준 · 구매수량=객실박 · 875실** 가정 확정 전까지 방향 지표다.
2. xmas 문서의 조식 가치 133,925원은 **12/18 그랜드 딜럭스MV(Expedia) 페어 유도값** — 다른 날짜·객실에 전용 시 가정이다.
3. xmas 문서 v3.6 기준: 비스타 상시 조식 포함 · 비피크는 기존 객실가 참조 · 프로그램 프리미엄 음수/0 (표본 3지점, 일반화 금지).
4. **경쟁 우위·예약 효과는 어떤 형태로도 주장하지 않는다** — 모든 제안은 검증 전 가설이다.
