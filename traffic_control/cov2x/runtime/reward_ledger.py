"""Lifecycle-consistent SUMO 1.12 fallback reward ledger."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Mapping


@dataclass
class VehicleLifecycleLedger:
    """Keep active and retired last-observed time-loss values."""
    _last_observed: dict[str, float] = field(default_factory=dict)
    _retired_last_observed: dict[str, float] = field(default_factory=dict)
    _retired_unobserved: set[str] = field(default_factory=set)
    _last_sim_time: float = 0.0

    def observe(
        self,
        active: Mapping[str, float],
        *,
        arrived_ids: set[str] | None = None,
        sim_time: float | None = None,
    ) -> float:
        now = {str(k): max(0.0, float(v)) for k, v in active.items()}
        if sim_time is not None:
            self._last_sim_time = max(self._last_sim_time, float(sim_time))
        previous_ids = set(self._last_observed)
        explicit_arrivals = {str(v) for v in (arrived_ids or set())}
        if explicit_arrivals.intersection(now):
            raise ValueError("arrived vehicle cannot remain active in the same ledger snapshot")
        resurrected = set(now).intersection(self.retired_ids)
        if resurrected:
            raise ValueError(f"retired vehicle id reappeared: {sorted(resurrected)}")
        disappeared = (previous_ids - set(now)) | explicit_arrivals
        for vehicle_id in disappeared:
            if vehicle_id in self._retired_last_observed or vehicle_id in self._retired_unobserved:
                continue
            if vehicle_id in self._last_observed:
                self._retired_last_observed[vehicle_id] = self._last_observed.pop(vehicle_id)
            else:
                self._retired_unobserved.add(vehicle_id)
        self._last_observed.update(now)
        return self.total()

    def retire(self, vehicle_id: str) -> None:
        vehicle_id = str(vehicle_id)
        if vehicle_id in self._retired_last_observed or vehicle_id in self._retired_unobserved:
            return
        value = self._last_observed.pop(vehicle_id, None)
        if value is None:
            self._retired_unobserved.add(vehicle_id)
        else:
            self._retired_last_observed[vehicle_id] = float(value)

    def total(self) -> float:
        return float(sum(self._last_observed.values()) + sum(self._retired_last_observed.values()))

    @property
    def active(self) -> dict[str, float]:
        return dict(self._last_observed)

    @property
    def retired_last_observed_total(self) -> float:
        return float(sum(self._retired_last_observed.values()))

    @property
    def retired_ids(self) -> frozenset[str]:
        return frozenset(set(self._retired_last_observed) | self._retired_unobserved)

    def snapshot(self) -> dict[str, object]:
        return {
            "active": dict(self._last_observed),
            "retired_last_observed": dict(self._retired_last_observed),
            "retired_unobserved": sorted(self._retired_unobserved),
            "total": self.total(),
            "sim_time": self._last_sim_time,
        }


@dataclass(frozen=True)
class TimeLossReward:
    previous_total: float
    current_total: float
    reference_seconds: float

    @property
    def reward(self) -> float:
        return -(self.current_total - self.previous_total) / max(float(self.reference_seconds), 1e-6)


def network_time_loss_reward(previous: float, current: float, reference_seconds: float) -> TimeLossReward:
    return TimeLossReward(float(previous), float(current), float(reference_seconds))
