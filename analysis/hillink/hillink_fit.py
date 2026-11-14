"""
HILLINK 실데이터 적합 어댑터 — timeline/ 3개 원장을 논문 Algorithm 1 입력으로 변환한다.

  python3 hillink_fit.py                          # (2.6) truncated 적합
  python3 hillink_fit.py --calibrated             # (2.2) 캘리브레이션 포함
  python3 hillink_fit.py --n-step 500 --top 15    # N 격자 굵게, 상위 가중치 표시 늘리기

데이터 결합 (timeline/ 아래 세 CSV)
  menu.csv       (업장, 상품) → 단가.  관측 셀 (u, p) 의 가격.
  revenue_l5.csv 체류별 구매 원장 (성별, 대표자 연령, 구성·방문 목적, 업장, 상품, 수량, 결제액).
  persona_grid.csv 페르소나 원장 (성별, 연령, 목적, 박수, 아동동반, 비중).

매핑 규칙 (선택 근거는 각 주석)
  셀      = 결제액>0 인 (업장, 상품) 중 menu 단가가 있는 것 → 관측 d = 결제 건수, p = menu 단가.
            포함(결제액 0) 행은 실현 가격이 없어서 제외한다 — 가격·수요 정의가 모호해지기 때문.
  페르소나 = persona_grid 의 (성별, 연령, 목적) 고유 조합 (박수·아동동반·인원수 변형은 풀링).
            revenue 의 '· 2인 · 비투숙' 같은 접미사는 목적 접두어 일치로 같은 페르소나로 묶는다.
  q_k(u)  = 페르소나 k 의 상품 u 결제 건수 / k 의 체류 수 (체류 1회당 구매확률), 1 초과면 1 로 자른다.
  N       = 노출 인원(체류 규모 추정치). 실데이터라 ~3만 수준이라 fit 의 n_step 으로 격자를 굵게 푼다.

주의: 여기서 q는 LLM 시뮬레이션이 아니라 실결제 원장에서 뽑은 경험적 구매확률이다.
논문의 추정 구조(Algorithm 1)를 실데이터에 올리는 것이 목적이며, q를 LLM으로 만드는 단계는 별도다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import persona_exposure as pe

TIMELINE_DIR = Path(__file__).resolve().parent.parent / 'timeline'
DEFAULT_TOP = 10


@dataclass(frozen=True)
class HillinkDataset:
    """persona_grid × menu × revenue_l5 를 결합한 Algorithm 1 입력."""

    observations: list[pe.Observation]
    probabilities: list[list[float]]
    persona_names: list[str]
    persona_shares: list[float]
    persona_stays: list[int]
    stay_count: int
    clipped_probabilities: int


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline='', encoding='utf-8-sig') as handle:
        return list(csv.DictReader(handle))


def _purpose_prefixes(rows: list[dict[str, str]]) -> list[str]:
    return sorted({row['목적'].strip() for row in rows}, key=len, reverse=True)


def _match_purpose(purpose: str, prefixes: list[str]) -> str:
    for prefix in prefixes:
        if purpose.startswith(prefix):
            return prefix
    raise ValueError(f'PERSONA_PURPOSE_UNMATCHED:{purpose}')


def build_dataset(timeline_dir: Path = TIMELINE_DIR) -> HillinkDataset:
    menu_rows = _read_csv(timeline_dir / 'menu.csv')
    menu_price = {(row['업장'].strip(), row['상품'].strip()): float(row['단가']) for row in menu_rows}

    persona_rows = _read_csv(timeline_dir / 'persona_grid.csv')
    prefixes = _purpose_prefixes(persona_rows)
    share_by_name: dict[str, float] = {}
    for row in persona_rows:
        name = f"{row['성별'].strip()}/{row['연령'].strip()}/{_match_purpose(row['목적'].strip(), prefixes)}"
        share_by_name[name] = share_by_name.get(name, 0.0) + float(row['비중'])
    persona_names = sorted(share_by_name)
    share_total = sum(share_by_name.values())
    if share_total <= 0:
        raise ValueError('PERSONA_SHARES_EMPTY')
    persona_shares = [share_by_name[name] / share_total for name in persona_names]

    revenue_rows = _read_csv(timeline_dir / 'revenue_l5.csv')
    stays_unique: dict[str, set[str]] = {name: set() for name in persona_names}
    events_by_pair: dict[tuple[str, tuple[str, str]], int] = {}
    for row in revenue_rows:
        name = f"{row['성별'].strip()}/{row['대표자 연령'].strip()}/{_match_purpose(row['구성·방문 목적'].strip(), prefixes)}"
        stays_unique[name].add(row['체류'])
        if float(row['결제액']) > 0:
            key = (row['업장'].strip(), row['상품'].strip())
            if key in menu_price:
                events_by_pair[(name, key)] = events_by_pair.get((name, key), 0) + 1
    stays_by_persona = {name: len(stays) for name, stays in stays_unique.items()}
    stay_count = sum(stays_by_persona.values())
    if stay_count == 0:
        raise ValueError('REVENUE_STAYS_EMPTY')

    cells = sorted({key for (_, key) in events_by_pair})
    observations: list[pe.Observation] = []
    probabilities: list[list[float]] = []
    clipped = 0
    for venue, product in cells:
        demand = sum(count for (name, key), count in events_by_pair.items() if key == (venue, product))
        if demand == 0:
            continue
        row: list[float] = []
        for name in persona_names:
            stays = stays_by_persona[name]
            events = events_by_pair.get((name, (venue, product)), 0)
            probability = events / stays if stays > 0 else 0.0
            if probability > 1.0:
                clipped += 1
                probability = 1.0
            row.append(probability)
        observations.append(pe.Observation(f'{venue}/{product}', menu_price[(venue, product)], demand))
        probabilities.append(row)

    if not observations:
        raise ValueError('HILLINK_DATASET_EMPTY')
    return HillinkDataset(
        observations,
        probabilities,
        persona_names,
        persona_shares,
        [stays_by_persona[name] for name in persona_names],
        stay_count,
        clipped,
    )


def _report(dataset: HillinkDataset, result, *, top: int, band_delta: float, calibrated: bool) -> None:
    demands = [o.demand for o in dataset.observations]
    fitted = _fitted_mean(dataset, result, calibrated)
    print(
        f'셀 {len(dataset.observations)}개 · 페르소나 {len(dataset.persona_names)}명 · 체류 {dataset.stay_count:,}회 '
        f'(q 클립 {dataset.clipped_probabilities}건, 관측 수요 {min(demands)}~{max(demands)})'
    )
    print(f'N̂={result.exposure:,}  V={result.value:.4f}  profile band={pe.profile_band(result, delta=band_delta)}')
    if calibrated:
        print(f'캘리브레이션 â={result.intercept:+.4f}  b̂={result.slope:.4f}')
    print(f'평균수요 재현: 적합 {fitted:,.1f} vs 관측 {sum(demands):,} (결제 건수 합계)')
    weights = [float(w) for w in result.weights]
    order = sorted(range(len(weights)), key=lambda i: -weights[i])[:top]
    print('상위 가중치 (α̂ vs persona_grid 비중):')
    for index in order:
        if weights[index] < 1e-4:
            continue
        name = dataset.persona_names[index] if index < len(dataset.persona_names) else 'dummy_never_buys'
        share = dataset.persona_shares[index] if index < len(dataset.persona_shares) else 0.0
        print(f'  {name:40s} α̂={weights[index]:.4f}  비중={share:.4f}')


def _fitted_mean(dataset: HillinkDataset, result, calibrated: bool) -> float:
    import numpy as np

    probs = np.asarray(dataset.probabilities, dtype=float)
    weights = np.asarray(result.weights)[: probs.shape[1]]
    if calibrated:
        probs = pe.calibrate_matrix(probs, result.intercept, result.slope)
    per_cell = result.exposure * (probs @ weights)
    return float(sum(per_cell))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='HILLINK 실데이터로 Algorithm 1 적합')
    parser.add_argument('--timeline-dir', type=Path, default=TIMELINE_DIR)
    parser.add_argument('--n-max', type=int, default=40_000)
    parser.add_argument('--n-step', type=int, default=250)
    parser.add_argument('--full', action='store_true', help='(2.3) 완전 우도 (기본은 (2.6) 절단)')
    parser.add_argument('--calibrated', action='store_true', help='(2.2) 캘리브레이션 포함')
    parser.add_argument('--calibration-iters', type=int, default=pe.CALIBRATION_ITERS_DEFAULT)
    parser.add_argument('--random-starts', type=int, default=0)
    parser.add_argument('--tolerance', type=float, default=1e-5)
    parser.add_argument('--max-iter', type=int, default=800)
    parser.add_argument('--band-delta', type=float, default=1e-3)
    parser.add_argument('--top', type=int, default=DEFAULT_TOP)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    try:
        dataset = build_dataset(args.timeline_dir)
    except ValueError as exc:
        print(f'입력 확인 필요: {exc}', file=sys.stderr)
        return 2

    if args.calibrated:
        result = pe.fit_calibrated(
            dataset.observations,
            dataset.probabilities,
            n_max=args.n_max,
            truncated=not args.full,
            tolerance=args.tolerance,
            max_iter=args.max_iter,
            calibration_iters=args.calibration_iters,
            n_random_starts=args.random_starts,
            n_step=args.n_step,
        )
    else:
        result = pe.fit(
            dataset.observations,
            dataset.probabilities,
            n_max=args.n_max,
            truncated=not args.full,
            tolerance=args.tolerance,
            max_iter=args.max_iter,
            n_random_starts=args.random_starts,
            n_step=args.n_step,
        )

    if args.json:
        payload = {
            'mode': ('calibrated-' if args.calibrated else '') + ('full' if args.full else 'truncated'),
            'exposure': result.exposure,
            'value': result.value,
            'profile_band': list(pe.profile_band(result, delta=args.band_delta)),
            'stay_count': dataset.stay_count,
            'cells': len(dataset.observations),
            'personas': len(dataset.persona_names),
            'weights': dict(
                zip([*dataset.persona_names, 'dummy_never_buys'], [round(w, 8) for w in result.weights])
            ),
        }
        if args.calibrated:
            payload['intercept'] = result.intercept
            payload['slope'] = result.slope
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    _report(dataset, result, top=args.top, band_delta=args.band_delta, calibrated=args.calibrated)
    return 0


if __name__ == '__main__':
    sys.exit(main())
