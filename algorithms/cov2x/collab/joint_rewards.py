"""Network-level (team) and end-specific rewards for the joint CTDE stack.

The team reward is the primary system-level signal: it rewards completed
trip arrivals (throughput) and penalizes hard braking and network halting.
Every policy family (signal, vehicle, cloud) receives ``team_weight`` times
this shared reward on top of its own local objective, so all three ends are
driven by the same traffic outcome instead of only local comfort.

This module is protocol-agnostic: callers pass payload-shaped traffic and
intersection observations (the same Mapping used by the Protocol 2.0
controller), which keeps it testable without SUMO.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


TEAM_ARRIVED_WEIGHT = 2.0
TEAM_BRAKING_WEIGHT = 0.02
TEAM_HALTING_WEIGHT = 0.1
TEAM_WAITING_WEIGHT = 0.004
TEAM_SPEED_WEIGHT = 1.0
TEAM_WEIGHT_DEFAULT = 2.0

SIGNAL_HALTING_WEIGHT = 0.05
SIGNAL_WAITING_WEIGHT = 0.01
SIGNAL_SWITCH_PENALTY = 0.1

CLOUD_HALTING_WEIGHT = 0.02


@dataclass(frozen=True)
class TrafficSnapshot:
    """System-level traffic counters captured at one decision time."""

    active_vehicles: int = 0
    departed_vehicles: int = 0
    arrived_vehicles: int = 0
    min_expected_vehicles: int = 0
    hard_braking_events: int = 0
    total_halting: int = 0
    total_waiting_time: float = 0.0
    mean_speed: float = 0.0

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "TrafficSnapshot":
        traffic = payload.get("traffic", {}) or {}
        intersections = payload.get("intersections", {}) or {}
        halting = 0
        waiting = 0.0
        speed_weighted = 0.0
        vehicle_count = 0
        for obs in intersections.values():
            if not isinstance(obs, Mapping):
                continue
            for lane in (obs.get("lanes", {}) or {}).values():
                if isinstance(lane, Mapping):
                    halting += int(lane.get("halting_count", 0) or 0)
                    waiting += float(lane.get("waiting_time", 0.0) or 0.0)
                    count = int(lane.get("vehicle_count", 0) or 0)
                    vehicle_count += count
                    speed_weighted += (
                        float(lane.get("mean_speed", 0.0) or 0.0) * count
                    )
        return cls(
            active_vehicles=int(traffic.get("active_vehicles", 0) or 0),
            departed_vehicles=int(traffic.get("departed_vehicles", 0) or 0),
            arrived_vehicles=int(traffic.get("arrived_vehicles", 0) or 0),
            min_expected_vehicles=int(
                traffic.get("min_expected_vehicles", 0) or 0
            ),
            hard_braking_events=int(
                traffic.get("hard_braking_events", 0) or 0
            ),
            total_halting=halting,
            total_waiting_time=waiting,
            mean_speed=(
                speed_weighted / vehicle_count if vehicle_count > 0 else 0.0
            ),
        )


def team_reward(before: TrafficSnapshot, after: TrafficSnapshot) -> float:
    """Network-level reward for the interval between two snapshots.

    Positive for arrived trips, negative for additional hard braking and for
    halting vehicles appearing (or failing to clear) across the network.
    """
    arrived_delta = max(
        0, after.arrived_vehicles - before.arrived_vehicles
    )
    braking_delta = max(
        0, after.hard_braking_events - before.hard_braking_events
    )
    halting_delta = after.total_halting - before.total_halting
    waiting_delta = after.total_waiting_time - before.total_waiting_time
    speed_delta = after.mean_speed - before.mean_speed
    return (
        TEAM_ARRIVED_WEIGHT * arrived_delta
        - TEAM_BRAKING_WEIGHT * braking_delta
        - TEAM_HALTING_WEIGHT * halting_delta
        - TEAM_WAITING_WEIGHT * waiting_delta
        + TEAM_SPEED_WEIGHT * speed_delta
    )


def signal_local_reward(
    intersection_payload: Mapping[str, Any],
    *,
    source_phase: int,
    requested_phase: int,
    executed_switch: bool,
) -> tuple[float, dict[str, float]]:
    """Signal local objective: congestion and gratuitous switching penalty."""
    lanes = intersection_payload.get("lanes", {}) or {}
    halting = 0
    waiting = 0.0
    for lane in lanes.values():
        if not isinstance(lane, Mapping):
            continue
        halting += int(lane.get("halting_count", 0) or 0)
        waiting += float(lane.get("waiting_time", 0.0) or 0.0)
    halting_term = -SIGNAL_HALTING_WEIGHT * halting
    waiting_term = -SIGNAL_WAITING_WEIGHT * min(waiting / 100.0, 10.0)
    switch_term = (
        -SIGNAL_SWITCH_PENALTY
        if executed_switch and int(requested_phase) != int(source_phase)
        else 0.0
    )
    total = halting_term + waiting_term + switch_term
    return total, {
        "signal_halting": halting_term,
        "signal_waiting": waiting_term,
        "signal_switch": switch_term,
        "total": total,
    }


def cloud_local_reward(
    before: TrafficSnapshot, after: TrafficSnapshot
) -> tuple[float, dict[str, float]]:
    """Cloud local objective: extra penalty for network halting growth."""
    halting_term = -CLOUD_HALTING_WEIGHT * max(
        0, after.total_halting - before.total_halting
    )
    team = team_reward(before, after)
    total = team + halting_term
    return total, {
        "cloud_halting": halting_term,
        "cloud_team": team,
        "total": total,
    }
