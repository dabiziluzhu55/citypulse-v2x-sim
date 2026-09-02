"""Sticky lead-CAV lease management."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass

HARD_INVALIDATION_REASONS = frozenset({"hard_invalidation", "lease_completion", "vehicle_departed", "safety_empty", "control_authority_lost"})


@dataclass
class LeadLease:
    intersection_id: str
    movement: str
    vehicle_id: str
    assignment_epoch: int
    issued_at: float
    expires_at: float
    active: bool = True
    release_reason: str | None = None

    @property
    def identity_key(self) -> tuple[str, str]:
        return self.intersection_id, self.movement


class StickyLeadCAV:
    """Keep a movement leader until an explicit hard invalidation/completion."""

    def __init__(self) -> None:
        self._leases: dict[tuple[str, str], LeadLease] = {}
        self._next_assignment_epoch = 1
        self._assignment_count = 0
        self._release_counts: Counter[str] = Counter()

    def assign(self, intersection_id: str, movement: str, vehicle_id: str, *, now: float, lease_s: float) -> LeadLease:
        key = (str(intersection_id), str(movement))
        current = self._leases.get(key)
        if current and current.active:
            return current
        lease = LeadLease(
            intersection_id=key[0],
            movement=key[1],
            vehicle_id=str(vehicle_id),
            assignment_epoch=self._next_assignment_epoch,
            issued_at=float(now),
            expires_at=float(now) + float(lease_s),
        )
        self._next_assignment_epoch += 1
        self._leases[key] = lease
        self._assignment_count += 1
        return lease

    def refresh_signal(self, intersection_id: str, movement: str, *, now: float, green_window: float | None = None) -> LeadLease | None:
        lease = self._leases.get((str(intersection_id), str(movement)))
        if lease and lease.active:
            lease.expires_at = max(lease.expires_at, float(now) + max(0.0, float(green_window or 0.0)))
        return lease

    def release(self, intersection_id: str, movement: str, reason: str) -> bool:
        if reason not in HARD_INVALIDATION_REASONS:
            raise ValueError(f"ordinary update cannot release sticky leader: {reason}")
        lease = self._leases.get((str(intersection_id), str(movement)))
        if lease is None or not lease.active:
            return False
        lease.active = False; lease.release_reason = reason
        self._release_counts[str(reason)] += 1
        return True

    def get(self, intersection_id: str, movement: str) -> LeadLease | None:
        lease = self._leases.get((str(intersection_id), str(movement)))
        return lease if lease and lease.active else None

    def active(self) -> tuple[LeadLease, ...]:
        return tuple(lease for lease in self._leases.values() if lease.active)

    def diagnostics(self) -> dict[str, object]:
        releases = sum(self._release_counts.values())
        return {
            "assignments": self._assignment_count,
            "releases": releases,
            "release_reasons": dict(self._release_counts),
            "active_leases": len(self.active()),
        }
