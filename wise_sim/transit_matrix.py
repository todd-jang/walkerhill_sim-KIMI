"""시설 간 이동 시간과 이동·대기 불편도 컨텍스트."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

FACILITIES = ("room", "fnb", "spa", "pool", "casino", "wedding")

TRANSIT_MINUTES = np.array([
    [0, 10,  8, 12, 15, 20],
    [10, 0,  5,  7, 12, 18],
    [8,  5,  0,  6, 10, 15],
    [12, 7,  6,  0,  8, 14],
    [15, 12, 10, 8,  0, 12],
    [20, 18, 15, 14, 12, 0],
], dtype=float)
TRANSIT_MINUTES.setflags(write=False)


@dataclass
class TransitContext:
    """L2에 주입되는 시설 이동시간 및 가정값 표시 컨텍스트."""

    matrix: np.ndarray = field(
        default_factory=lambda: TRANSIT_MINUTES.copy()
    )
    facilities: list[str] = field(default_factory=lambda: list(FACILITIES))
    is_assumption: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.is_assumption, bool):
            raise ValueError("is_assumption must be a boolean")
        self.facilities = list(self.facilities)
        if any(not isinstance(name, str) or not name for name in self.facilities):
            raise ValueError("facility names must be non-empty strings")
        if not self.facilities or len(set(self.facilities)) != len(self.facilities):
            raise ValueError("facilities must be non-empty and unique")

        matrix = np.array(self.matrix, dtype=float, copy=True)
        expected_shape = (len(self.facilities), len(self.facilities))
        if matrix.shape != expected_shape:
            raise ValueError(f"matrix must have shape {expected_shape}")
        if not np.isfinite(matrix).all() or (matrix < 0).any():
            raise ValueError("transit times must be finite and non-negative")
        if not np.allclose(np.diag(matrix), 0.0):
            raise ValueError("transit matrix diagonal must be zero")
        matrix.setflags(write=False)
        self.matrix = matrix

    def get_transit_time(self, origin: str, dest: str) -> float:
        try:
            i = self.facilities.index(origin)
            j = self.facilities.index(dest)
        except ValueError as exc:
            raise ValueError(
                f"unknown facility in route {origin!r} -> {dest!r}"
            ) from exc
        return float(self.matrix[i, j])

    def get_disutility(
        self,
        origin: str,
        dest: str,
        penalty_per_min: float = 0.02,
        queue_wait_minutes: float = 0.0,
    ) -> float:
        """이동 및 대기 시간을 합쳐 선형·비선형 불편도 페널티를 계산한다."""
        if not np.isfinite(penalty_per_min) or penalty_per_min < 0:
            raise ValueError("penalty_per_min must be finite and non-negative")
        if not np.isfinite(queue_wait_minutes) or queue_wait_minutes < 0:
            raise ValueError("queue_wait_minutes must be finite and non-negative")

        effective_minutes = (
            self.get_transit_time(origin, dest) + float(queue_wait_minutes)
        )
        linear_penalty = penalty_per_min * effective_minutes
        if effective_minutes <= 10:
            return linear_penalty
        return linear_penalty + 0.01 * (effective_minutes - 10) ** 1.5
