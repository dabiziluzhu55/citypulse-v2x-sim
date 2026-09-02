"""Conservative rule vehicle guidance, ported from the CoSLight controller.

This is the Stage-2 rule baseline: same green-passage speed advice and
queue-based lane-change heuristic as ``algorithms/cov2x/controller.py``
``_build_vehicle_actions``, but expressed through the CoV2X lane-state
helpers and Protocol 2.0 payloads.  It is intentionally torch-free.
"""

from __future__ import annotations

from typing import Any, Mapping

from algorithms.cov2x.vehicle.agent import SPEED_FRACTIONS


def _edge_queues(payload: Mapping[str, Any]) -> dict[str, list[tuple[int, float]]]:
    queues: dict[str, list[tuple[int, float]]] = {}
    intersections = payload.get("intersections", {}) or {}
    for intersection in intersections.values():
        if not isinstance(intersection, Mapping):
            continue
        lanes = intersection.get("lanes", {}) or {}
        for lane_id, lane in lanes.items():
            if not isinstance(lane, Mapping):
                continue
            edge_id = lane.get("edge_id") or str(lane_id).rsplit("_", 1)[0]
            try:
                lane_index = int(str(lane_id).rsplit("_", 1)[1])
            except (IndexError, ValueError):
                continue
            try:
                queue_m = float(lane.get("queue_length_m", 0.0) or 0.0)
            except (TypeError, ValueError):
                queue_m = 0.0
            queues.setdefault(str(edge_id), []).append((lane_index, queue_m))
    return queues


def rule_vehicle_actions(
    payload: Mapping[str, Any],
    signal_actions: Mapping[str, Any],
    *,
    lane_state_module: Any = None,
    vehicle_ids: set[str] | None = None,
) -> dict[str, dict[str, float | int]]:
    """Return Protocol 2.0 vehicle commands from the conservative rule."""
    if lane_state_module is None:
        from algorithms.cov2x.road import lane_state as lane_state_module

    vehicles = payload.get("vehicles", {}) or {}
    intersections = payload.get("intersections", {}) or {}
    if not vehicles:
        return {}

    edge_queues = _edge_queues(payload)
    previous_results = (
        (payload.get("previous_action_results") or {}).get("vehicles", {}) or {}
    )
    actions: dict[str, dict[str, float | int]] = {}
    for vehicle_id, vehicle in vehicles.items():
        if vehicle_ids is not None and vehicle_id not in vehicle_ids:
            continue
        if not isinstance(vehicle, Mapping):
            continue
        location = vehicle.get("location", {}) or {}
        motion = vehicle.get("motion", {}) or {}
        next_signal = vehicle.get("next_signal")
        road_id = str(location.get("road_id", ""))
        speed = float(motion.get("speed_mps", 0.0) or 0.0)
        allowed_speed = max(float(motion.get("allowed_speed_mps", 13.9) or 0.0), 0.0)
        target_speed: float | None = None
        target_lane: int | None = None

        if isinstance(next_signal, Mapping):
            distance = float(next_signal.get("distance_m", 999.0) or 999.0)
            signal_state = str(next_signal.get("state", ""))
            tls_id = str(
                next_signal.get("intersection_id")
                or next_signal.get("tls_id", "")
                or ""
            )
            green_left = (
                lane_state_module.green_remaining(tls_id, intersections.get(tls_id))
                if tls_id
                else -1.0
            )
            if signal_state in {"G", "g", "GREEN"} and green_left > 0.0 and distance > 5.0:
                eta = distance / max(speed, 0.1)
                if eta <= green_left:
                    target_speed = min(speed, allowed_speed)
                else:
                    target_speed = min(distance / green_left, allowed_speed)
            elif (
                signal_state in {"r", "R", "RED"}
                and 10.0 < distance < 150.0
                and speed > 2.0
            ):
                target_speed = max(speed - 2.0, 2.0)

        current_lane = location.get("lane_index")
        type_id = str(vehicle.get("type_id", ""))
        if (
            speed >= 0.5
            and road_id
            and not road_id.startswith(":")
            and isinstance(next_signal, Mapping)
            and float(next_signal.get("distance_m", 999.0) or 999.0) > 50.0
            and current_lane is not None
        ):
            queues = edge_queues.get(road_id, [])
            current_queue = next(
                (
                    queue
                    for lane, queue in queues
                    if lane == int(current_lane)
                ),
                None,
            )
            candidates = [
                (queue, lane)
                for lane, queue in queues
                if lane != int(current_lane)
                and (
                    type_id
                    in lane_state_module.lane_allowed_types(road_id, lane)
                )
            ]
            if current_queue is not None and candidates:
                best_queue, best_lane = min(candidates)
                if current_queue > max(best_queue * 1.5, best_queue + 5.0):
                    target_lane = best_lane
                    previous = previous_results.get(vehicle_id) or {}
                    requested = (previous.get("requested") or {}).get(
                        "target_lane_index"
                    )
                    if (
                        previous.get("lane_change_status") == "not_completed"
                        and requested == target_lane
                    ):
                        target_lane = None

        if target_speed is not None or target_lane is not None:
            command: dict[str, float | int] = {}
            if target_speed is not None:
                command["target_speed_mps"] = float(
                    min(max(target_speed, 0.0), allowed_speed)
                )
            if target_lane is not None:
                command["target_lane_index"] = int(target_lane)
            actions[vehicle_id] = command
    return actions


def cruise_actions(
    payload: Mapping[str, Any],
    signal_actions: Mapping[str, Any],
    *,
    speed_fraction: float = 1.0,
) -> dict[str, dict[str, float]]:
    """Trivial baseline: command every controllable vehicle at cruise speed."""
    vehicles = payload.get("vehicles", {}) or {}
    actions: dict[str, dict[str, float]] = {}
    fraction = float(speed_fraction)
    if fraction not in SPEED_FRACTIONS:
        fraction = min(SPEED_FRACTIONS, key=lambda item: abs(item - fraction))
    for vehicle_id, vehicle in vehicles.items():
        if not isinstance(vehicle, Mapping):
            continue
        motion = vehicle.get("motion", {}) or {}
        allowed = float(motion.get("allowed_speed_mps", 13.9) or 0.0)
        if allowed > 0.0:
            actions[vehicle_id] = {
                "target_speed_mps": float(fraction * allowed)
            }
    return actions
