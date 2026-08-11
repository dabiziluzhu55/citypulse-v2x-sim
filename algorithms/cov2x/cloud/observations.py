"""Cloud coordinator observation, global (critic) state, and priorities.

The cloud agent is the network-level component of the vehicle-road-cloud
stack.  It runs at a low rate, observes the aggregated state of every
controlled intersection plus network traffic counters, and outputs one
3-class coordination priority (relax / neutral / boost) per intersection.
These priorities are consumed by the road and vehicle actors as part of their
local observations and by the shared centralized critic.

No cloud action is sent to SUMO: the cloud influences traffic only through
the edge policies, which matches the architecture reference (cloud sets
regional goals/priorities; edges keep execution autonomy).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

MAX_INTERSECTIONS = 20
CLOUD_PRIORITY_CLASSES = 3
CLOUD_PRIORITY_LABELS = ("relax", "neutral", "boost")

# 20 intersections x (halting, vehicles, waiting, phase) + traffic(5) + time
CLOUD_OBS_DIM = MAX_INTERSECTIONS * 4 + 5 + 1
# Global critic input: cloud observation + per-intersection priority one-hot.
GLOBAL_STATE_DIM = CLOUD_OBS_DIM + MAX_INTERSECTIONS * CLOUD_PRIORITY_CLASSES


def neutral_priority() -> np.ndarray:
    priority = np.zeros(CLOUD_PRIORITY_CLASSES, dtype=np.float32)
    priority[1] = 1.0
    return priority


def priority_for_action(action: int) -> np.ndarray:
    """One-hot vector for one intersection's cloud priority class."""
    priority = np.zeros(CLOUD_PRIORITY_CLASSES, dtype=np.float32)
    action = int(action)
    if 0 <= action < CLOUD_PRIORITY_CLASSES:
        priority[action] = 1.0
    else:
        priority[1] = 1.0
    return priority


@dataclass(frozen=True)
class CloudObservation:
    """One cloud decision input for the whole controlled network."""

    state: np.ndarray  # shape (CLOUD_OBS_DIM,)
    intersection_ids: tuple[str, ...]  # sorted, up to MAX_INTERSECTIONS


def _intersection_summary(
    intersections: Mapping[str, Any], phase_orders: Mapping[str, tuple[int, ...]]
) -> list[float]:
    features: list[float] = []
    for tls_id in sorted(phase_orders)[:MAX_INTERSECTIONS]:
        obs = intersections.get(str(tls_id)) or {}
        lanes = obs.get("lanes", {}) or {}
        halting = 0
        vehicles = 0
        waiting = 0.0
        for lane in lanes.values():
            if not isinstance(lane, Mapping):
                continue
            halting += int(lane.get("halting_count", 0) or 0)
            vehicles += int(lane.get("vehicle_count", 0) or 0)
            waiting += float(lane.get("waiting_time", 0.0) or 0.0)
        order = phase_orders.get(str(tls_id), ())
        current_phase = int(obs.get("current_phase", order[0] if order else 0) or 0)
        features.extend(
            [
                min(halting / 20.0, 1.0),
                min(vehicles / 20.0, 1.0),
                min(waiting / 600.0, 1.0),
                min(current_phase / 8.0, 1.0),
            ]
        )
    if len(features) < MAX_INTERSECTIONS * 4:
        features.extend([0.0] * (MAX_INTERSECTIONS * 4 - len(features)))
    return features[: MAX_INTERSECTIONS * 4]


def build_cloud_observation(
    payload: Mapping[str, Any],
    *,
    phase_orders: Mapping[str, tuple[int, ...]],
) -> CloudObservation:
    """Build the network-level cloud observation from a Protocol 2.0 payload."""
    traffic = payload.get("traffic", {}) or {}
    simulation_time = float(payload.get("simulation_time", 0.0) or 0.0)
    state = np.concatenate(
        [
            np.asarray(
                _intersection_summary(
                    payload.get("intersections", {}) or {}, phase_orders
                ),
                dtype=np.float32,
            ),
            np.asarray(
                [
                    min(int(traffic.get("active_vehicles", 0) or 0) / 200.0, 1.0),
                    min(int(traffic.get("departed_vehicles", 0) or 0) / 200.0, 1.0),
                    min(int(traffic.get("arrived_vehicles", 0) or 0) / 200.0, 1.0),
                    min(
                        int(traffic.get("min_expected_vehicles", 0) or 0)
                        / 200.0,
                        1.0,
                    ),
                    min(int(traffic.get("hard_braking_events", 0) or 0) / 100.0, 1.0),
                ],
                dtype=np.float32,
            ),
            np.asarray([min(simulation_time / 900.0, 1.0)], dtype=np.float32),
        ],
        dtype=np.float32,
    )
    return CloudObservation(
        state=state,
        intersection_ids=tuple(str(tls_id) for tls_id in sorted(phase_orders)),
    )


def build_global_state(
    payload: Mapping[str, Any],
    *,
    phase_orders: Mapping[str, tuple[int, ...]],
    cloud_priorities: Mapping[str, np.ndarray] | None = None,
) -> np.ndarray:
    """Centralized-critic input: cloud state plus every intersection priority."""
    cloud = build_cloud_observation(payload, phase_orders=phase_orders)
    priorities = cloud_priorities or {}
    priority_features: list[float] = []
    for tls_id in cloud.intersection_ids:
        priority = priorities.get(tls_id)
        if priority is None:
            priority = neutral_priority()
        else:
            priority = np.asarray(priority, dtype=np.float32).reshape(-1)
        priority_features.extend(
            priority[:CLOUD_PRIORITY_CLASSES].tolist()
        )
    if len(priority_features) < MAX_INTERSECTIONS * CLOUD_PRIORITY_CLASSES:
        priority_features.extend(
            [0.0, 1.0, 0.0]
            * (MAX_INTERSECTIONS * CLOUD_PRIORITY_CLASSES // 3 - len(priority_features) // 3)
        )
    state = np.concatenate(
        [cloud.state, np.asarray(priority_features[: MAX_INTERSECTIONS * 3], dtype=np.float32)],
        dtype=np.float32,
    )
    return state
