"""Movement-local time-loss credit for the final Vehicle-only candidate."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol
import math


class MovementResolver(Protocol):
    def resolve(self, intersection_id: str, vehicle: Mapping[str, Any]) -> Any: ...


def _time_loss(vehicle: Mapping[str, Any]) -> float:
    traffic = vehicle.get("traffic", {}) or {}
    raw = traffic.get("time_loss_s", traffic.get("time_loss", 0.0))
    value = float(raw or 0.0)
    if not math.isfinite(value):
        raise ValueError("Vehicle time loss must be finite")
    return max(0.0, value)


@dataclass
class MovementLocalCreditLedger:
    """Differential approach time loss, normalized per movement and second."""

    interval_s: float = 5.0
    _previous_time_loss: dict[str, float] = field(default_factory=dict)
    observations: int = 0
    resolved_vehicle_observations: int = 0
    credited_vehicle_intervals: int = 0
    total_local_time_loss_s: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.interval_s) or self.interval_s <= 0.0:
            raise ValueError("movement-local credit interval must be positive")

    def observe(
        self,
        payload: Mapping[str, Any],
        resolver: MovementResolver,
    ) -> dict[tuple[str, str], float]:
        vehicles = payload.get("vehicles", {}) or {}
        current: dict[str, float] = {}
        grouped: dict[tuple[str, str], list[float]] = {}
        for raw_id, raw_vehicle in vehicles.items():
            if not isinstance(raw_vehicle, Mapping):
                continue
            vehicle_id = str(raw_id)
            current_value = _time_loss(raw_vehicle)
            current[vehicle_id] = current_value
            next_signal = raw_vehicle.get("next_signal", {}) or {}
            intersection_id = str(
                next_signal.get("intersection_id")
                or next_signal.get("tls_id")
                or ""
            )
            if not intersection_id:
                continue
            resolution = resolver.resolve(intersection_id, raw_vehicle)
            movement_id = getattr(resolution, "resolved_movement_id", None)
            if not getattr(resolution, "resolved", movement_id is not None):
                continue
            if movement_id is None:
                continue
            key = (intersection_id, str(movement_id))
            self.resolved_vehicle_observations += 1
            previous = self._previous_time_loss.get(vehicle_id)
            delta = 0.0 if previous is None else max(0.0, current_value - previous)
            grouped.setdefault(key, []).append(delta)
            if previous is not None:
                self.credited_vehicle_intervals += 1
                self.total_local_time_loss_s += delta
        self._previous_time_loss = current
        self.observations += 1
        return {
            key: -sum(deltas) / max(1, len(deltas)) / self.interval_s
            for key, deltas in grouped.items()
        }

    def snapshot(self) -> dict[str, float | int | str]:
        return {
            "semantics": "movement_local_time_loss_rate_per_vehicle_v1",
            "normalization": "sum_delta_time_loss/active_resolved_vehicle_count/interval_s",
            "interval_s": float(self.interval_s),
            "observations": int(self.observations),
            "resolved_vehicle_observations": int(
                self.resolved_vehicle_observations
            ),
            "credited_vehicle_intervals": int(self.credited_vehicle_intervals),
            "total_local_time_loss_s": float(self.total_local_time_loss_s),
        }
