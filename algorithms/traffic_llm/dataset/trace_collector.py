"""Compact Protocol 2.0 traces without dumping full vehicle dictionaries."""

from __future__ import annotations

from typing import Any, Mapping

from simulation.sumo.engine.session import SimulationSnapshot

from traffic_llm_runtime.snapshot import compact_snapshot_summary

from .action_parser import classify_action_space, extract_protocol_actions, parse_target_phases
from .schema import ACTION_SPACE_SIGNAL_ONLY, OBSERVATION_VERSION


LANE_KEEP_KEYS = (
    "vehicle_count",
    "halting_count",
    "mean_speed",
    "occupancy",
    "queue_length_m",
    "waiting_time",
    "lane_has_green",
    "signal_state",
    "current_allowed_speed_mps",
)


def compact_observation(
    observation: Mapping[str, Any] | None,
    *,
    store_full_vehicles: bool = False,
) -> dict[str, Any]:
    if not observation:
        return {"observation_version": OBSERVATION_VERSION}
    intersections: dict[str, Any] = {}
    raw_ix = observation.get("intersections") or {}
    if isinstance(raw_ix, Mapping):
        for iid, i_obs in raw_ix.items():
            if not isinstance(i_obs, Mapping):
                continue
            lanes_out: dict[str, Any] = {}
            lanes = i_obs.get("lanes") or {}
            if isinstance(lanes, Mapping):
                for lane_id, lane in lanes.items():
                    if not isinstance(lane, Mapping):
                        continue
                    lanes_out[str(lane_id)] = {
                        key: lane.get(key) for key in LANE_KEEP_KEYS if key in lane
                    }
            intersections[str(iid)] = {
                "current_phase": i_obs.get("current_phase"),
                "pending_phase": i_obs.get("pending_phase"),
                "stage": i_obs.get("stage"),
                "stage_elapsed": i_obs.get("stage_elapsed"),
                "lanes": lanes_out,
            }
    traffic = observation.get("traffic") if isinstance(observation.get("traffic"), Mapping) else {}
    vehicles = observation.get("vehicles") if isinstance(observation.get("vehicles"), Mapping) else {}
    compact: dict[str, Any] = {
        "observation_version": OBSERVATION_VERSION,
        "simulation_time": observation.get("simulation_time"),
        "step_id": observation.get("step_id"),
        "intersections": intersections,
        "network_summary": {
            "active_vehicles": (traffic or {}).get("active_vehicles"),
            "departed_vehicles": (traffic or {}).get("departed_vehicles"),
            "arrived_vehicles": (traffic or {}).get("arrived_vehicles"),
            "hard_braking_events": (traffic or {}).get("hard_braking_events"),
            "vehicle_count": len(vehicles or {}),
        },
    }
    if store_full_vehicles:
        compact["vehicles"] = dict(vehicles or {})
    return compact


class TraceCollector:
    def __init__(self, *, store_full_vehicles: bool = False) -> None:
        self.store_full_vehicles = store_full_vehicles
        self.records: list[dict[str, Any]] = []
        self.action_spaces: list[str] = []
        self.n_signal_actions = 0
        self.n_vehicle_actions = 0
        self.illegal_phase = False
        self.has_valid_action = False

    def on_decision(self, payload: Mapping[str, Any]) -> None:
        actions = extract_protocol_actions(payload)
        space = classify_action_space(actions)
        self.action_spaces.append(space)
        phases = parse_target_phases(actions["signals"])
        if phases:
            self.has_valid_action = True
            self.n_signal_actions += len(phases)
        if actions["vehicles"]:
            self.n_vehicle_actions += sum(
                1 for command in actions["vehicles"].values() if command
            )
            if space != ACTION_SPACE_SIGNAL_ONLY:
                self.has_valid_action = True
        record = {
            "simulation_time": payload.get("simulation_time"),
            "step_id": payload.get("step_id"),
            "observation": compact_observation(
                payload.get("observation") if isinstance(payload.get("observation"), Mapping) else {},
                store_full_vehicles=self.store_full_vehicles,
            ),
            "actions": actions,
            "requested_action": payload.get("requested_action") or actions,
            "executed_signal_state": dict(payload.get("executed_signal_state") or {}),
            "decision_latency_ms": payload.get("decision_latency_ms"),
            "event_state": list(payload.get("event_state") or ()),
            "teacher_action_space": space,
        }
        self.records.append(record)

    def on_fixed_snapshot(self, snapshot: SimulationSnapshot, step_id: int) -> None:
        executed = {
            str(iid): {
                "current_phase": i_obs.current_phase,
                "pending_phase": i_obs.pending_phase,
                "stage": i_obs.stage,
            }
            for iid, i_obs in snapshot.intersections.items()
        }
        signals = {
            iid: {"target_phase": state["current_phase"]}
            for iid, state in executed.items()
        }
        self.has_valid_action = True
        self.n_signal_actions += len(signals)
        self.action_spaces.append(ACTION_SPACE_SIGNAL_ONLY)
        self.records.append(
            {
                "simulation_time": snapshot.elapsed_seconds,
                "step_id": step_id,
                "observation": compact_snapshot_summary(snapshot),
                "actions": {"signals": signals, "vehicles": {}},
                "requested_action": None,
                "executed_signal_state": executed,
                "decision_latency_ms": 0.0,
                "event_state": [
                    {
                        "event_id": item.event_id,
                        "event_type": item.event_type,
                        "state": item.state,
                        "start_seconds": item.start_seconds,
                        "end_seconds": item.end_seconds,
                    }
                    for item in snapshot.events
                ],
                "teacher_action_space": ACTION_SPACE_SIGNAL_ONLY,
                "note": "fixed mode has no Protocol 2.0 request; executed SUMO phase recorded",
            }
        )

    def summary(self) -> dict[str, Any]:
        spaces = set(self.action_spaces)
        if ACTION_SPACE_SIGNAL_ONLY not in spaces and not spaces:
            space = ACTION_SPACE_SIGNAL_ONLY
        elif any(item != ACTION_SPACE_SIGNAL_ONLY for item in spaces):
            from .schema import ACTION_SPACE_SIGNAL_VEHICLE

            space = ACTION_SPACE_SIGNAL_VEHICLE
        else:
            space = ACTION_SPACE_SIGNAL_ONLY
        return {
            "n_decisions": len(self.records),
            "teacher_action_space": space,
            "has_valid_action": self.has_valid_action,
            "n_signal_actions": self.n_signal_actions,
            "n_vehicle_actions": self.n_vehicle_actions,
            "has_vehicle_actions": space != ACTION_SPACE_SIGNAL_ONLY,
        }
