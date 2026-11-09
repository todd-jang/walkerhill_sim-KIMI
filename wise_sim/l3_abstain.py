"""L3 신뢰도 점검과 정책 결과 기권(abstain) 판정."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

import numpy as np

SUPPORTED_POLICY_TYPES = frozenset({"room", "fnb", "spa", "pool"})
OUT_OF_SCOPE_POLICY_TYPES = frozenset({"casino", "wedding"})
TRANSIT_ASSUMPTION_NOTE = (
    "가정값 기반 이동 시간 사용 — 신뢰도 보수적 해석 필요"
)


@dataclass(frozen=True)
class L3Result:
    abstain: bool
    reason: str | None
    sign_agreement: float
    confidence_interval: tuple[float | None, float | None]
    note: str
    sample_count: int
    coefficient_of_variation: float | None
    transit_assumption: bool

    def to_dict(self) -> dict[str, object]:
        """JSON 호환 결과. 표본 부족 CI는 NaN 대신 null로 표현한다."""
        return asdict(self)


def evaluate_l3(
    samples: Sequence[float] | np.ndarray,
    policy_type: str,
    transit_assumption: bool = False,
    min_samples: int = 1_000,
    min_sign_agreement: float = 0.6,
    max_cv: float = 0.5,
    bootstrap_samples: int = 1_000,
    seed: int = 42,
) -> L3Result:
    """표본 수·방향 안정성·변동계수·정책 범위를 기준으로 결과를 판정한다.

    samples는 고객별 또는 독립 평가 단위별 정책 효과(예: 매출 변화율)다.
    가격 인과효과 추정이나 표본의 독립성을 보장하는 검정은 수행하지 않는다.
    """
    try:
        values = np.asarray(samples, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("samples must be a one-dimensional numeric sequence") from exc
    if values.ndim != 1:
        raise ValueError("samples must be one-dimensional")
    if not np.isfinite(values).all():
        raise ValueError("samples must contain only finite values")
    if not isinstance(policy_type, str) or not policy_type:
        raise ValueError("policy_type must be a non-empty string")
    if policy_type not in SUPPORTED_POLICY_TYPES | OUT_OF_SCOPE_POLICY_TYPES:
        raise ValueError(f"unsupported policy_type: {policy_type!r}")
    if not isinstance(min_samples, int) or min_samples < 1:
        raise ValueError("min_samples must be a positive integer")
    if not 0.0 <= min_sign_agreement <= 1.0:
        raise ValueError("min_sign_agreement must be between 0 and 1")
    if not np.isfinite(max_cv) or max_cv < 0:
        raise ValueError("max_cv must be finite and non-negative")
    if not isinstance(bootstrap_samples, int) or bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be a positive integer")

    sample_count = len(values)
    transit_note = TRANSIT_ASSUMPTION_NOTE if transit_assumption else None

    def result(
        abstain: bool,
        reason: str | None,
        sign_agreement: float,
        confidence_interval: tuple[float | None, float | None],
        note: str,
        cv: float | None = None,
    ) -> L3Result:
        if transit_note:
            note = f"{note}; {transit_note}"
        serializable_cv = cv if cv is not None and np.isfinite(cv) else None
        return L3Result(
            abstain=abstain,
            reason=reason,
            sign_agreement=sign_agreement,
            confidence_interval=confidence_interval,
            note=note,
            sample_count=sample_count,
            coefficient_of_variation=serializable_cv,
            transit_assumption=transit_assumption,
        )

    if sample_count < min_samples:
        return result(
            True,
            "insufficient_samples",
            0.0,
            (None, None),
            f"표본 {sample_count}명 < 최소 {min_samples}명",
        )

    if policy_type in OUT_OF_SCOPE_POLICY_TYPES:
        return result(
            True,
            "out_of_scope",
            0.0,
            (None, None),
            "카지노/웨딩은 1차 버전 Scope 외",
        )

    observed_mean = float(values.mean())
    observed_sign = np.sign(observed_mean)
    rng = np.random.default_rng(seed)
    bootstrap_means = np.empty(bootstrap_samples, dtype=float)
    for i in range(bootstrap_samples):
        bootstrap_means[i] = rng.choice(
            values, size=sample_count, replace=True
        ).mean()
    sign_agreement = float(np.mean(
        np.sign(bootstrap_means) == observed_sign
    ))
    ci_low, ci_high = np.percentile(bootstrap_means, [2.5, 97.5])

    standard_deviation = float(values.std())
    cv = (
        standard_deviation / abs(observed_mean)
        if observed_mean != 0.0 else float("inf")
    )
    interval = (float(ci_low), float(ci_high))

    if sign_agreement < min_sign_agreement:
        return result(
            True,
            "low_sign_agreement",
            sign_agreement,
            interval,
            f"방향성 일치율 {sign_agreement:.2f} < {min_sign_agreement}",
            cv,
        )
    if cv > max_cv:
        return result(
            True,
            "high_variance",
            sign_agreement,
            interval,
            f"변동계수 {cv:.2f} > {max_cv}",
            cv,
        )

    note = transit_note or "실측 데이터 기반"
    return L3Result(
        abstain=False,
        reason=None,
        sign_agreement=sign_agreement,
        confidence_interval=interval,
        note=note,
        sample_count=sample_count,
        coefficient_of_variation=cv,
        transit_assumption=transit_assumption,
    )
