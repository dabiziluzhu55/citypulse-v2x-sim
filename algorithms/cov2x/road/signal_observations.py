"""Protocol 2.0 -> signal (road) agent observations and phase legality.

One logical signal agent exists per controlled intersection; parameters are
shared across the homogeneous road-agent population.  The action is an index
into the intersection's ``phase_order``, so the action space is the phase
sequence rather than raw SUMO phase ids.  Legality is enforced here at the
algorithm boundary (minimum green and transition safety); the simulator still
owns yellow/clearance execution through ``SafePhaseController.request_phase``.

The observation also carries the cloud coordinator's per-intersection
priority (3-class one-hot), which is the cloud -> road communication channel.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

MAX_PHASES = 8
MAX_APPROACHES = 8
CLOUD_PRIORITY_CLASSES = 3
SIGNAL_ACTION_KEEP = 0
SIGNAL_ACTION_ADVANCE = 1
SIGNAL_ACTION_DIM = 2

# 8 approaches x (halting, vehicles, waiting) + 8 phase one-hot
# + (stage elapsed, min-green remaining) + 3 cloud priority one-hot
SIGNAL_OBS_DIM = MAX_APPROACHES * 3 + MAX_PHASES + 2 + CLOUD_PRIORITY_CLASSES

GREEN_STATES = frozenset({"G", "g", "GREEN"})


@dataclass(frozen=True)
class SignalObservation:
    """One road-agent decision input for one intersection."""

    tls_id: str
    state: np.ndarray  # shape (SIGNAL_OBS_DIM,)
    action_mask: np.ndarray  # shape (SIGNAL_ACTION_DIM,), bool; False is illegal
    phase_order: tuple[int, ...]
    current_phase: int
    stage: str
    stage_elapsed: float
    min_green_s: float
    cloud_priority: np.ndarray  # shape (CLOUD_PRIORITY_CLASSES,), one-hot


def _approach_features(lanes: Mapping[str, Any]) -> list[float]:
    """Aggregate lanes into up to MAX_APPROACHES edge-level feature slots."""
    by_edge: dict[str, list[float]] = {}
    for lane_id, lane in lanes.items():
        if not isinstance(lane, Mapping):
            continue
        parts = str(lane_id).rsplit("_", 1)
        edge = parts[0] if len(parts) == 2 and parts[1].isdigit() else str(lane_id)
        halting = int(lane.get("halting_count", 0) or 0)
        vehicles = int(lane.get("vehicle_count", 0) or 0)
        waiting = float(lane.get("waiting_time", 0.0) or 0.0)
        slot = by_edge.setdefault(edge, [0.0, 0.0, 0.0])
        slot[0] += halting
        slot[1] += vehicles
        slot[2] += waiting
    features: list[float] = []
    for edge in sorted(by_edge):
        halting, vehicles, waiting = by_edge[edge]
        features.extend(
            [
                min(halting / 10.0, 1.0),
                min(vehicles / 10.0, 1.0),
                min(waiting / 300.0, 1.0),
            ]
        )
    if len(features) < MAX_APPROACHES * 3:
        features.extend([0.0] * (MAX_APPROACHES * 3 - len(features)))
    return features[: MAX_APPROACHES * 3]


def _phase_one_hot(current_phase: int, phase_order: Sequence[int]) -> np.ndarray:
    one_hot = np.zeros(MAX_PHASES, dtype=np.float32)
    try:
        index = int(phase_order.index(int(current_phase)))
    except ValueError:
        index = 0
    if 0 <= index < MAX_PHASES:
        one_hot[index] = 1.0
    return one_hot


def phase_legal_mask(
    *,
    phase_order: Sequence[int],
    current_phase: int,
    stage: str,
    stage_elapsed: float,
    min_green_s: float,
) -> np.ndarray:
    """Return a length-2 bool mask over {keep current phase, advance to next}.

    Keeping the current phase is always legal while the intersection is in a
    known phase.  Advancing is legal only after minimum green is satisfied and
    the phase order has more than one phase; transition/minimum-green holds
    keep the current phase.  This enforces the fixed phase sequence of the
    CCTV-MAC TSC design instead of arbitrary phase jumps.
    """
    mask = np.zeros(SIGNAL_ACTION_DIM, dtype=bool)
    try:
        current_index = int(phase_order.index(int(current_phase)))
    except ValueError:
        current_index = -1
    mask[SIGNAL_ACTION_KEEP] = current_index >= 0
    if (
        len(phase_order) > 1
        and str(stage).upper() in GREEN_STATES
        and float(stage_elapsed) + 1e-9 >= float(min_green_s)
    ):
        mask[SIGNAL_ACTION_ADVANCE] = True
    return mask


def build_signal_observations(
    payload: Mapping[str, Any],
    *,
    phase_orders: Mapping[str, tuple[int, ...]],
    cloud_priorities: Mapping[str, np.ndarray] | None = None,
    min_green_s: float = 5.0,
) -> list[SignalObservation]:
    """Build one observation per controlled intersection, in phase-order key order."""
    intersections = payload.get("intersections", {}) or {}
    priorities = cloud_priorities or {}
    observations: list[SignalObservation] = []
    # 受控路口以 payload 为准（子集场景不得输出 checkpoint 中不在本场景的路口）。
    for tls_id in sorted(intersections):
        order = phase_orders.get(str(tls_id))
        if order is None:
            meta = intersections.get(str(tls_id)) or {}
            order = tuple(int(phase) for phase in (meta.get("phase_order") or []))
        order = tuple(int(phase) for phase in order)
        if not order:
            continue
        obs = intersections.get(str(tls_id)) or {}
        lanes = obs.get("lanes", {}) or {}
        current_phase = int(obs.get("current_phase", order[0]) or order[0])
        stage = str(obs.get("stage", "GREEN"))
        stage_elapsed = float(obs.get("stage_elapsed", 0.0) or 0.0)
        priority = priorities.get(str(tls_id))
        if priority is None:
            priority = np.zeros(CLOUD_PRIORITY_CLASSES, dtype=np.float32)
            priority[1] = 1.0  # neutral
        else:
            priority = np.asarray(priority, dtype=np.float32).reshape(-1)

        state = np.concatenate(
            [
                np.asarray(_approach_features(lanes), dtype=np.float32),
                _phase_one_hot(current_phase, order),
                np.asarray(
                    [
                        min(stage_elapsed / 60.0, 1.0),
                        (
                            max(
                                0.0,
                                float(min_green_s) - stage_elapsed,
                            )
                            / max(float(min_green_s), 1e-6)
                            if str(stage).upper() in GREEN_STATES
                            else 0.0
                        ),
                    ],
                    dtype=np.float32,
                ),
                priority[:CLOUD_PRIORITY_CLASSES],
            ],
            dtype=np.float32,
        )
        observations.append(
            SignalObservation(
                tls_id=str(tls_id),
                state=state,
                action_mask=phase_legal_mask(
                    phase_order=order,
                    current_phase=current_phase,
                    stage=stage,
                    stage_elapsed=stage_elapsed,
                    min_green_s=min_green_s,
                ),
                phase_order=order,
                current_phase=current_phase,
                stage=stage,
                stage_elapsed=stage_elapsed,
                min_green_s=float(min_green_s),
                cloud_priority=priority,
            )
        )
    return observations
