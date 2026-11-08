# Walkerhill WISE Simulation (walkerhill_sim-KIMI)

워커힐 호텔 수요 시뮬레이션 및 완수평가 판정 파이프라인 레포지토리입니다.
체크시트(`Walkerhill_완수평가_체크시트.xlsx`)의 수식과 평가 게이트(Gate A~F)를 코드로 구현하여 모듈화하였습니다.

---

## 📁 디렉토리 구조

```text
walkerhill_sim-KIMI/
├── Walkerhill_완수평가_체크시트.xlsx   # 완수평가 기준 및 체크시트 수식 원본
├── wise_sim/
│   ├── personas.py                 # Gate A: 아키타입 정의, 페르소나 생성 및 캘리브레이션 검증
│   ├── engine.py                   # Gate B/C/F: 시뮬레이션 엔진 (MockAgent, LLMAgent, 정책 격자)
│   └── verdict.py                  # 3단 판정 파이프라인 (방향 적중률, 순위 상관, D1 trace 검정, Wilson CI)
├── .gitignore
└── README.md
```

---

## 🔑 주요 구성 모듈

### 1. `wise_sim/personas.py` (Gate A: 아키타입 캘리브레이션)
- 투숙객 5대 아키타입 정의:
  - **가족여행** (30%), **커플** (25%), **비즈니스** (20%), **외국인관광** (15%), **프리미엄** (10%)
- 각 아키타입별 가격 민감도(`price_sensitivity`), 프로모션 친화도(`promo_affinity`), 주말 선호도, 체류 일수, 소비 지수 설정
- `generate_population(n, seed)`: 고정 시드 기반 페르소나 모집단 생성 (Gate B 재현성)
- `calibrate_check()`: 실제 생성 비율과 목표 비율 간 오차 $\le 5\%p$ 검증 (Gate A 통과 기준)

### 2. `wise_sim/engine.py` (Gate B: 배칭 / Gate C: 정책 격자 / Gate F: 시간 압축)
- **에이전트 구현체**:
  - `MockAgent`: 규칙 기반 로지스틱 의사결정 모델 (API 키 없이 재현 가능한 파이프라인 검증용)
  - `LLMAgent`: OpenAI 호환 API 어댑터. 20명/호출 배칭(`BATCH_SIZE = 20`, Gate B), 온도 $0.7$, 시드 고정. API 키 미설정 시 자동 폴백 지원
- **정책 격자(Policy Grid)**:
  - 가격 변동 $(-10\%, 0\%, +10\%)$ $\times$ 프로모션 (`none`, `package`, `breakfast`) $\rightarrow$ 9개 셀
- **시뮬레이션 로직**:
  - `simulate_weekend()`: 점유율(Occupancy), RevPAR, 기권률(Abstain Rate), 예약 결정 trace 수집
  - `run_policy_grid()`: 3×3 정책 격자 $\times$ 주말 시뮬레이션 일괄 실행

### 3. `wise_sim/verdict.py` (검증 및 3단 판정 파이프라인)
체크시트의 '기준 요약' 및 '판정 계산기' 수식을 완전하게 반영:
- **방향 적중률 (Direction Accuracy)**: 정책 효과 부호($+ / - / 0$) 일치율 (기준 $\ge 70\%$)
- **순위 상관 (Spearman $\rho$)**: 정책별 성과 순위 상관도 (기준 $\ge 0.60$)
- **Trace 분포 검정 ($D_1$ / BH-FDR)**:
  - 총변동거리(Total Variation Distance, $L_1/2$) 계산
  - Benjamini–Hochberg FDR 보정 ($q=0.05$)으로 임계치($\varepsilon_z = 0.22$) 초과 셀 검출
- **3단 판정**:
  - **적합**: 방향 적중률 $\ge 70\%$ 및 초과 셀 0건 $\rightarrow$ 전체 지표 보고 가능
  - **조건부**: 초과 셀 1~2건 $\rightarrow$ 방향만 인용, 초과 항목 명시
  - **부적합**: 방향 적중률 $< 70\%$ 또는 초과 셀 3건 이상 $\rightarrow$ 인용 불가 및 폐기
- **백테스트 확률 및 신뢰구간**:
  - 이항 우연 확률 $P(X \ge k)$
  - Wilson 95% 신뢰구간 산출 및 70% 기준 포함 여부 확인

---

## 🚀 빠른 시작 (Quick Start)

### MockAgent 기반 테스트 실행

```python
import sys
sys.path.append("wise_sim")

import personas
import engine
import verdict

# 1. 페르소나 400명 생성 및 캘리브레이션 검증
pop = personas.generate_population(n=400, seed=42)
calib = personas.calibrate_check(pop)
print(f"Gate A 통과 여부: {calib['pass']}")

# 2. MockAgent로 3x3 정책 격자 실행
agent = engine.MockAgent()
results = engine.run_policy_grid(agent, pop, n_weekends=4, seed=1000)
print(f"시뮬레이션 완료된 정책 수: {len(results)}")
```
