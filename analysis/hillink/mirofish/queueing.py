"""물리적 대기행렬 — 좌석 회전(cycle) 기반 결정론 큐.

한 슬롯은 `slot_minutes // service_minutes` 회전으로 나뉜다.
회전마다 좌석 수만큼 손님이 들어가고, 그보다 뒤에 온 손님은 다음 회전을 기다린다.
대기 시간 = 내 앞 대기 인원 // 좌석 수 * 회전 길이(분). 인내심을 넘으면 포기(BALK).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class VenueQueue:
    seats: int
    service_minutes: float = 20.0
    slot_minutes: int = 60
    waiting_persons: int = 0
    seated_persons: int = 0
    served_persons: int = 0
    balked_persons: int = 0

    @property
    def cycles(self) -> int:
        return max(1, int(self.slot_minutes // self.service_minutes))

    @property
    def capacity(self) -> int:
        return self.seats * self.cycles

    def estimate_wait(self, persons_ahead: int) -> float:
        return (persons_ahead // max(1, self.seats)) * self.service_minutes

    def admit(self, party: int, arrive_min: float, slot_end_min: float,
              min_dwell_min: float, patience_min: float) -> Tuple[str, float]:
        """반환: (SEATED|BALK, 대기분)."""
        wait = self.estimate_wait(self.waiting_persons + max(0, self.seated_persons - self.capacity))
        seated_at = arrive_min + wait
        if seated_at + min_dwell_min > slot_end_min or wait > patience_min:
            self.balked_persons += party
            return "BALK", wait
        if self.waiting_persons + party > self.capacity:
            self.balked_persons += party
            return "BALK", wait
        self.waiting_persons += party
        self.seated_persons += party
        self.served_persons += party
        return "SEATED", wait


class QueueBoard:
    """(day, slot, venue) 별 큐 상태 보관."""

    def __init__(self, service_minutes: float = 20.0, patience_minutes: float = 25.0,
                 min_dwell_minutes: float = 30.0, slot_minutes: int = 60):
        self.service_minutes = service_minutes
        self.patience_minutes = patience_minutes
        self.min_dwell_minutes = min_dwell_minutes
        self.slot_minutes = slot_minutes
        self._queues: Dict[Tuple[int, str, str], VenueQueue] = {}

    def queue(self, day: int, slot: str, venue: str, seats: int) -> VenueQueue:
        key = (day, slot, venue)
        if key not in self._queues:
            self._queues[key] = VenueQueue(seats=seats, service_minutes=self.service_minutes,
                                           slot_minutes=self.slot_minutes)
        return self._queues[key]

    def admit(self, day: int, slot: str, venue: str, seats: int, party: int,
              arrive_min: float, slot_end_min: float) -> Tuple[str, float]:
        q = self.queue(day, slot, venue, seats)
        return q.admit(party, arrive_min, slot_end_min,
                       self.min_dwell_minutes, self.patience_minutes)

    def snapshot(self) -> Dict[str, Dict[str, Dict[str, int]]]:
        out: Dict[str, Dict[str, Dict[str, int]]] = {}
        for (day, slot, venue), q in self._queues.items():
            out.setdefault(str(day), {}).setdefault(slot, {})[venue] = {
                "waiting": q.waiting_persons, "served": q.served_persons,
                "balked": q.balked_persons, "capacity": q.capacity,
            }
        return out
