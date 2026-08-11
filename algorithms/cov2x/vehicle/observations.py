"""Protocol 2.0 → VehicleObservation extractor.

Reuses the authoritative ``algorithms.cov2x.road.lane_state`` builder for the
41-dim state and the action mask, and the new lane_state helpers for static
road lane indices and green remaining time. This module only builds
observations; it does not select actions or train a policy.

Known limitation: Protocol 2.0 does not expose "time to next green" for a
red/yellow approach, so ``time_to_next_green_s`` is always ``None`` here.
It can be supplied later by a cycle/timing model without changing the
observation contract.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

import numpy as np

from algorithms.cov2x.vehicle.agent import VehicleObservation
from algorithms.cov2x.cloud.observations import neutral_priority

GREEN_STATES = frozenset({"G", "g", "GREEN"})
DEFAULT_ALLOWED_SPEED_MPS = 13.9
DEFAULT_NEXT_SIGNAL_DISTANCE_M = 150.0


def _edge_of_lane(lane_id: str) -> str:
    """Return the edge id for a lane id (e.g. ``a_in_0`` -> ``a_in``)."""
    parts = str(lane_id).rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return str(lane_id)


def build_vehicle_observations(
    payload: Dict[str, Any],
    signal_actions: Dict[str, int],
    *,
    lane_state_module: Any = None,
    cloud_priorities: Dict[str, Any] | None = None,
) -> List[VehicleObservation]:
    """Build one observation per active approach slot, in slot order.

    Slots without a candidate vehicle are skipped; the returned list matches
    the active slots of ``build_lane_actor_batch``.

    ``lane_state_module`` is injectable for environments without the full
    CoSLight stack; it defaults to ``algorithms.cov2x.road.lane_state``.
    """
    if lane_state_module is None:
        from algorithms.cov2x.road import lane_state as lane_state_module

    batch = lane_state_module.build_lane_actor_batch(payload, signal_actions)
    vehicles = payload.get("vehicles", {})
    intersections = payload.get("intersections", {})
    previous_results = (
        (payload.get("previous_action_results") or {}).get("vehicles", {})
    )
    cloud_priorities = cloud_priorities or {}
    observations: List[VehicleObservation] = []

    for index, vehicle_id in enumerate(batch.vehicle_ids):
        if vehicle_id is None or not batch.slot_mask[index]:
            continue
        vehicle = vehicles.get(vehicle_id)
        if not isinstance(vehicle, dict):
            continue

        motion = vehicle.get("motion", {}) or {}
        location = vehicle.get("location", {}) or {}
        next_signal = vehicle.get("next_signal", {}) or {}

        tls_id = str(
            next_signal.get("intersection_id")
            or next_signal.get("tls_id")
            or ""
        )
        phase_is_green = str(next_signal.get("state", "")) in GREEN_STATES

        remaining = 0.0
        if tls_id and tls_id in intersections:
            remaining = lane_state_module.green_remaining(
                tls_id, intersections[tls_id]
            )
            if remaining < 0.0:
                remaining = 0.0

        lane_index = location.get("lane_index")
        road_id = str(location.get("road_id", ""))
        road_lane_indices = (
            lane_state_module.edge_lane_indices(road_id) if road_id else ()
        )

        previous = previous_results.get(vehicle_id) or {}
        lane_change_status = previous.get("lane_change_status")
        previous_lane_change_success: bool | None = None
        if isinstance(lane_change_status, str):
            previous_lane_change_success = lane_change_status == "completed"

        cloud_context = cloud_priorities.get(tls_id)
        if cloud_context is None:
            cloud_context = neutral_priority()
        else:
            cloud_context = np.asarray(cloud_context, dtype=np.float32).reshape(-1)

        vehicle_traffic = vehicle.get("traffic", {}) or {}
        # System-level approach waiting: aggregate queue waiting over every
        # lane on this vehicle's approach so the reward responds to congestion
        # instead of only the leading vehicle's SUMO waiting (usually ~0 while
        # the leader is moving).  Falls back to the leader's accumulated
        # waiting when no lane observations are available.
        lane_waiting = 0.0
        if tls_id and tls_id in intersections:
            for lane_id, lane in (
                (intersections[tls_id].get("lanes") or {}).items()
            ):
                if isinstance(lane, Mapping) and _edge_of_lane(lane_id) == road_id:
                    lane_waiting += float(lane.get("waiting_time", 0.0) or 0.0)
        waiting_time_s = (
            lane_waiting
            if lane_waiting > 0.0
            else float(
                vehicle_traffic.get(
                    "accumulated_waiting_time_s",
                    vehicle_traffic.get("waiting_time_s", 0.0),
                )
                or 0.0
            )
        )

        observations.append(
            VehicleObservation(
                vehicle_id=vehicle_id,
                tls_id=tls_id,
                edge_id=road_id,
                slot_index=index,
                speed_mps=float(motion.get("speed_mps", 0.0)),
                accel_mps2=float(motion.get("acceleration_mps2", 0.0)),
                allowed_speed_mps=float(
                    motion.get("allowed_speed_mps", DEFAULT_ALLOWED_SPEED_MPS)
                ),
                dist_to_stopline_m=float(
                    next_signal.get("distance_m", DEFAULT_NEXT_SIGNAL_DISTANCE_M)
                ),
                phase_is_green=phase_is_green,
                signal_remaining_s=remaining,
                time_to_next_green_s=None,
                lane_index=int(lane_index) if lane_index is not None else None,
                road_lane_indices=road_lane_indices,
                previous_lane_change_success=previous_lane_change_success,
                state_41=batch.states[index],
                action_mask=batch.action_mask[index],
                cloud_context=cloud_context,
                waiting_time_s=waiting_time_s,
            )
        )

    return observations
