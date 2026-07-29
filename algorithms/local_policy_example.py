"""Minimal in-process control algorithm for protocol 2.0."""

PROTOCOL_VERSION = "2.0"
EDGE_LANES: dict[str, list[dict]] = {}


def initialize(payload: dict) -> dict:
    global EDGE_LANES
    EDGE_LANES = payload.get("edge_lanes", {})
    return {
        "protocol_version": PROTOCOL_VERSION,
        "episode_id": payload["episode_id"],
        "ready": True,
    }


def allowed_lane_indices(vehicle: dict) -> tuple[int, ...]:
    road_id = vehicle["location"]["road_id"]
    if road_id.startswith(":"):
        return ()
    type_id = vehicle["type_id"]
    return tuple(
        lane["lane_index"]
        for lane in EDGE_LANES.get(road_id, [])
        if type_id in lane.get("allowed_vehicle_type_ids", [])
    )


def step(payload: dict) -> dict:
    signals = {
        intersection_id: {"target_phase": state["current_phase"]}
        for intersection_id, state in payload["intersections"].items()
    }
    vehicle_actions = {}
    for vehicle_id, vehicle in payload.get("vehicles", {}).items():
        candidates = allowed_lane_indices(vehicle)
        current_lane = vehicle["location"]["lane_index"]
        if (
            candidates
            and current_lane not in candidates
            and vehicle["motion"]["speed_mps"] > 0.1
        ):
            vehicle_actions[vehicle_id] = {"target_lane_index": candidates[0]}
    return {
        "protocol_version": PROTOCOL_VERSION,
        "episode_id": payload["episode_id"],
        "step_id": payload["step_id"],
        "actions": {"signals": signals, "vehicles": vehicle_actions},
    }


def finish(payload: dict) -> object:
    return None
