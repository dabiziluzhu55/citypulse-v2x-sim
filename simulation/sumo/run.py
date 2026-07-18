"""Run official fixed timing or an external signal-control algorithm."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Dict, Sequence

from .config import SignalConfigurationError, SignalProgram
from .controller import SafePhaseController, SignalStage, TransitionTiming
from .policy import (
    AIFrameObservation,
    PROTOCOL_VERSION,
    IntersectionMetadata,
    IntersectionObservation,
    LaneMetadata,
    LaneConnectionSignalObservation,
    LaneObservation,
    PhaseMetadata,
    PreviousActionResults,
    RoadConnectionMetadata,
    SimulationMetadata,
    SimulationObservation,
    TrafficObservation,
    VehicleControlMetadata,
    VehicleTypeMetadata,
)


LOGGER = logging.getLogger(__name__)


def _load_sumo_modules():
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        tools_path = str(Path(sumo_home) / "tools")
        if tools_path not in sys.path:
            sys.path.append(tools_path)
    try:
        import sumolib  # type: ignore
        import traci  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Cannot import SUMO Python tools. Set SUMO_HOME to the SUMO installation."
        ) from exc
    return sumolib, traci


def _load_manifest(path: Path) -> Mapping[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Generated manifest not found: {path}. Run simulation.sumo.build_tls first."
        ) from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid generated manifest {path}: {exc}") from exc


def _selected_manifest(
    manifest: Mapping[str, object], intersection_ids: Sequence[str]
) -> Mapping[str, Mapping[str, object]]:
    available = manifest.get("intersections", {})
    result = {}
    for intersection_id in intersection_ids:
        if intersection_id not in available:
            raise RuntimeError(
                f"{intersection_id} is absent from the generated TLS manifest."
            )
        result[intersection_id] = available[intersection_id]
    return result


def _select_programs(configurations, requested: str, period: str):
    programs = {}
    for config in configurations:
        program_id = requested or f"{config.intersection_id}_{period}"
        if program_id not in config.programs:
            raise SignalConfigurationError(
                f"{config.intersection_id}: program {program_id!r} is unavailable. "
                f"Available programs: {sorted(config.programs)}"
            )
        programs[config.intersection_id] = config.programs[program_id]
    return programs


def _select_program_manifests(
    selected_manifest: Mapping[str, Mapping[str, object]],
    programs: Mapping[str, SignalProgram],
) -> Mapping[str, Mapping[str, object]]:
    result = {}
    for intersection_id, item in selected_manifest.items():
        program_id = programs[intersection_id].program_id
        program_views = item.get("programs", {})
        if program_views:
            if program_id not in program_views:
                raise RuntimeError(
                    f"{intersection_id}: generated manifest has no view for "
                    f"program {program_id!r}."
                )
            result[intersection_id] = {**item, **program_views[program_id]}
        else:
            result[intersection_id] = item
    return result


def _build_controllers(
    configurations,
    selected_manifest: Mapping[str, Mapping[str, object]],
    programs,
    minimum_green: float,
) -> Dict[str, SafePhaseController]:
    controllers = {}
    for config in configurations:
        phase_order = tuple(
            int(value) for value in selected_manifest[config.intersection_id]["phase_order"]
        )
        program = programs[config.intersection_id]
        controllers[config.intersection_id] = SafePhaseController(
            phase_order,
            {
                phase.number: TransitionTiming(phase.yellow, phase.clearance)
                for phase in program.phases
            },
            minimum_green=minimum_green,
            initial_phase=phase_order[0],
            start_time=0.0,
        )
    return controllers


def _connection_id(index: int) -> str:
    return f"connection_{index}"


def _build_metadata(
    traci,
    selected_manifest: Mapping[str, Mapping[str, object]],
    programs,
    period: str,
    seed: int,
    decision_interval: float,
    minimum_green: float,
    episode_id: str,
    vehicle_types: Mapping[str, VehicleTypeMetadata] | None = None,
) -> SimulationMetadata:
    incoming_edges = {
        intersection_id: {
            str(connection["from_edge"]) for connection in item["connections"]
        }
        for intersection_id, item in selected_manifest.items()
    }
    outgoing_edges = {
        intersection_id: {
            str(connection["to_edge"]) for connection in item["connections"]
        }
        for intersection_id, item in selected_manifest.items()
    }
    intersections = {}
    for intersection_id, item in selected_manifest.items():
        raw_connections = sorted(
            item["connections"],
            key=lambda value: (
                str(value["approach"]),
                str(value["from_edge"]),
                int(value["from_lane"]),
                str(value["to_edge"]),
                int(value["to_lane"]),
                int(value["link_index"]),
            ),
        )
        connections = []
        raw_by_id = {}
        incoming_lanes = set()
        outgoing_lanes = set()
        lane_definitions = {}
        for index, raw in enumerate(raw_connections):
            connection_id = _connection_id(index)
            from_lane = f"{raw['from_edge']}_{raw['from_lane']}"
            to_lane = f"{raw['to_edge']}_{raw['to_lane']}"
            incoming_lanes.add(from_lane)
            outgoing_lanes.add(to_lane)
            lane_definitions[from_lane] = (
                str(raw["from_edge"]),
                int(raw["from_lane"]),
            )
            lane_definitions[to_lane] = (
                str(raw["to_edge"]),
                int(raw["to_lane"]),
            )
            raw_by_id[connection_id] = raw
            connections.append(
                RoadConnectionMetadata(
                    connection_id=connection_id,
                    approach=str(raw["approach"]),
                    movement=str(raw["movement"]),
                    from_lane=from_lane,
                    to_lane=to_lane,
                    direction=str(raw["direction"]),
                    tls_id=str(raw["tls_id"]),
                    link_index=int(raw["link_index"]),
                )
            )
        lanes = {}
        for lane_id in sorted(incoming_lanes | outgoing_lanes):
            if lane_id in incoming_lanes and lane_id in outgoing_lanes:
                role = "both"
            elif lane_id in incoming_lanes:
                role = "incoming"
            else:
                role = "outgoing"
            edge_id, lane_index = lane_definitions[lane_id]
            lane_connections = [
                connection
                for connection in connections
                if connection.from_lane == lane_id
            ]
            approaches = sorted({item.approach for item in lane_connections})
            if len(approaches) > 1:
                raise ValueError(
                    f"Lane {lane_id} belongs to multiple approaches: {approaches}"
                )
            movements = tuple(
                sorted(
                    {item.movement for item in lane_connections},
                    key=lambda value: (
                        {"through": 0, "left": 1, "right": 2, "uturn": 3}.get(
                            value, 99
                        ),
                        value,
                    ),
                )
            )
            length = float(traci.lane.getLength(lane_id))
            speed_limit = float(traci.lane.getMaxSpeed(lane_id))
            lanes[lane_id] = LaneMetadata(
                lane_id=lane_id,
                edge_id=edge_id,
                lane_index=lane_index,
                role=role,
                length=length,
                max_speed=speed_limit,
                intersection_id=intersection_id,
                approach_id=(
                    f"{intersection_id}_{approaches[0]}_in" if approaches else None
                ),
                movements=movements,
                length_m=length,
                speed_limit_mps=speed_limit,
                downstream_lane_ids=tuple(
                    sorted({item.to_lane for item in lane_connections})
                ),
            )

        program = programs[intersection_id]
        phase_plan = {phase.number: phase for phase in program.phases}
        phase_movements = {
            int(value["phase_number"]): value for value in item["phase_movements"]
        }
        phases = {}
        for phase_id in (int(value) for value in item["phase_order"]):
            priorities = {}
            template = item["templates"][str(phase_id)]
            for connection_id, raw in raw_by_id.items():
                state = template[str(raw["tls_id"])]["green"][int(raw["link_index"])]
                if state == "G":
                    priorities[connection_id] = "protected"
                elif state == "g":
                    priorities[connection_id] = "permissive"
            movement = phase_movements[phase_id]
            timing = phase_plan[phase_id]
            phases[phase_id] = PhaseMetadata(
                phase_id=phase_id,
                name=timing.name,
                movement=str(movement["movement"]),
                approaches=tuple(str(value) for value in movement["approaches"]),
                green_seconds=timing.green,
                yellow_seconds=timing.yellow,
                clearance_seconds=timing.clearance,
                connection_priorities=priorities,
            )
        neighbors = tuple(
            sorted(
                other_id
                for other_id in selected_manifest
                if other_id != intersection_id
                and outgoing_edges[intersection_id] & incoming_edges[other_id]
            )
        )
        intersections[intersection_id] = IntersectionMetadata(
            intersection_id=intersection_id,
            phase_order=tuple(int(value) for value in item["phase_order"]),
            phases=phases,
            lanes=lanes,
            incoming_lanes=tuple(sorted(incoming_lanes)),
            outgoing_lanes=tuple(sorted(outgoing_lanes)),
            connections=tuple(connections),
            direct_neighbors=neighbors,
        )
    return SimulationMetadata(
        protocol_version=PROTOCOL_VERSION,
        episode_id=episode_id,
        period=period,
        seed=seed,
        decision_interval=decision_interval,
        minimum_green=minimum_green,
        intersections=intersections,
        vehicle_types=vehicle_types or {},
        vehicle_control=(
            VehicleControlMetadata(
                supported_actions=("target_speed_mps", "target_lane_index"),
                action_lease_seconds=decision_interval,
            )
            if vehicle_types
            else None
        ),
    )


def _state_key(stage: SignalStage) -> str:
    return {
        SignalStage.GREEN: "green",
        SignalStage.YELLOW: "yellow",
        SignalStage.CLEARANCE: "clearance",
    }[stage]


def _apply_controller_state(traci, intersection_manifest, controller) -> None:
    phase_templates = intersection_manifest["templates"][str(controller.current_phase)]
    key = _state_key(controller.stage)
    for tls_id in intersection_manifest["tls_ids"]:
        state = phase_templates[tls_id][key]
        traci.trafficlight.setRedYellowGreenState(tls_id, state)


def _estimate_queue_length(
    lane_metadata: LaneMetadata,
    *,
    halting_count: int,
    occupancy: float,
    vehicle_tracker,
) -> float:
    if halting_count <= 0:
        return 0.0
    samples = (
        vehicle_tracker.lane_vehicle_samples(lane_metadata.lane_id)
        if vehicle_tracker
        else ()
    )
    stopped = [item for item in samples if item[1] <= 0.1]
    spatial_extent = 0.0
    if stopped:
        queue_tail = min(max(0.0, position - length) for position, _, length, _ in stopped)
        spatial_extent = lane_metadata.length_m - queue_tail
        average_space = sum(length + gap for _, _, length, gap in stopped) / len(stopped)
    else:
        average_space = (
            vehicle_tracker.default_vehicle_space() if vehicle_tracker else 7.5
        )
    count_extent = halting_count * average_space
    occupancy_extent = lane_metadata.length_m * max(0.0, occupancy) / 100.0
    return min(
        lane_metadata.length_m,
        max(0.0, spatial_extent, count_extent, occupancy_extent),
    )


def _lane_signal_details(traci, intersection_metadata, lane_id: str):
    lane_connections = [
        item
        for item in intersection_metadata.connections
        if item.from_lane == lane_id
    ]
    if not lane_connections:
        return None, None, ()
    tls_states = {
        tls_id: str(traci.trafficlight.getRedYellowGreenState(tls_id))
        for tls_id in {item.tls_id for item in lane_connections}
    }
    details = []
    for item in lane_connections:
        state = tls_states[item.tls_id]
        if item.link_index < 0 or item.link_index >= len(state):
            raise ValueError(
                f"TLS {item.tls_id} state has no link index {item.link_index}."
            )
        details.append(
            LaneConnectionSignalObservation(
                connection_id=item.connection_id,
                movement=item.movement,
                downstream_lane_id=item.to_lane,
                signal_state=state[item.link_index],
            )
        )
    states = {item.signal_state for item in details}
    summary = next(iter(states)) if len(states) == 1 else "mixed"
    return any(item.signal_state in {"G", "g"} for item in details), summary, tuple(details)


def _build_intersection_observations(
    traci,
    simulation_time: float,
    metadata: SimulationMetadata,
    controllers: Mapping[str, SafePhaseController],
    *,
    fixed_tracker=None,
    vehicle_tracker=None,
    vehicle_action_controller=None,
):
    observations = {}
    for intersection_id, intersection_metadata in metadata.intersections.items():
        lanes = {}
        for lane_id, lane_metadata in intersection_metadata.lanes.items():
            vehicle_count = int(traci.lane.getLastStepVehicleNumber(lane_id))
            halting_count = int(traci.lane.getLastStepHaltingNumber(lane_id))
            occupancy = float(traci.lane.getLastStepOccupancy(lane_id))
            lane_has_green, signal_state, signal_details = _lane_signal_details(
                traci, intersection_metadata, lane_id
            )
            allowed = tuple(traci.lane.getAllowed(lane_id))
            disallowed = tuple(traci.lane.getDisallowed(lane_id))
            current_speed = float(traci.lane.getMaxSpeed(lane_id))
            if (allowed and "passenger" not in allowed) or "passenger" in disallowed:
                current_speed = 0.0
            controlled_count, minimum_target, mean_target = (
                vehicle_action_controller.speed_control_summary(lane_id)
                if vehicle_action_controller
                else (0, None, None)
            )
            lanes[lane_id] = LaneObservation(
                vehicle_count=vehicle_count,
                halting_count=halting_count,
                mean_speed=(
                    float(traci.lane.getLastStepMeanSpeed(lane_id))
                    if vehicle_count
                    else 0.0
                ),
                waiting_time=float(traci.lane.getWaitingTime(lane_id)),
                occupancy=occupancy,
                lane_has_green=lane_has_green,
                signal_state=signal_state,
                queue_length_m=_estimate_queue_length(
                    lane_metadata,
                    halting_count=halting_count,
                    occupancy=occupancy,
                    vehicle_tracker=vehicle_tracker,
                ),
                queue_length_is_estimate=True,
                current_allowed_speed_mps=current_speed,
                controlled_vehicle_count=controlled_count,
                min_target_speed_mps=minimum_target,
                mean_target_speed_mps=mean_target,
                connection_signal_states=signal_details,
            )
        if intersection_id in controllers:
            controller = controllers[intersection_id]
            signal = (
                controller.current_phase,
                controller.pending_phase,
                controller.stage.value,
                controller.stage_elapsed(simulation_time),
            )
        elif fixed_tracker is not None:
            signal = fixed_tracker.state(intersection_id, simulation_time)
        else:
            raise ValueError(f"No signal state provider for {intersection_id}.")
        observations[intersection_id] = IntersectionObservation(
            current_phase=signal[0],
            pending_phase=signal[1],
            stage=signal[2],
            stage_elapsed=signal[3],
            lanes=lanes,
        )
    return observations


def _observe(
    traci,
    simulation_time: float,
    step_id: int,
    metadata: SimulationMetadata,
    controllers: Mapping[str, SafePhaseController],
    departed_vehicles: int,
    arrived_vehicles: int,
    vehicle_tracker=None,
    vehicle_action_controller=None,
    previous_action_results: PreviousActionResults | None = None,
) -> SimulationObservation:
    observations = _build_intersection_observations(
        traci,
        simulation_time,
        metadata,
        controllers,
        vehicle_tracker=vehicle_tracker,
        vehicle_action_controller=vehicle_action_controller,
    )
    vehicle_observations = (
        vehicle_tracker.observations(reset_interval=True) if vehicle_tracker else {}
    )
    fuel_mg, fuel_ml, braking = (
        vehicle_tracker.totals() if vehicle_tracker else (0.0, 0.0, 0)
    )
    return SimulationObservation(
        protocol_version=PROTOCOL_VERSION,
        episode_id=metadata.episode_id,
        step_id=step_id,
        simulation_time=simulation_time,
        intersections=observations,
        traffic=TrafficObservation(
            active_vehicles=(
                len(vehicle_observations)
                if vehicle_tracker
                else int(traci.vehicle.getIDCount())
            ),
            departed_vehicles=departed_vehicles,
            arrived_vehicles=arrived_vehicles,
            min_expected_vehicles=int(traci.simulation.getMinExpectedNumber()),
            fuel_consumed_mg=fuel_mg,
            fuel_consumed_ml=fuel_ml,
            hard_braking_events=braking,
        ),
        vehicles=vehicle_observations,
        previous_action_results=(
            previous_action_results or PreviousActionResults(step_id=None)
        ),
    )


def _observe_ai_frame(
    traci,
    simulation_time: float,
    frame_id: int,
    metadata: SimulationMetadata,
    controllers: Mapping[str, SafePhaseController],
    *,
    fixed_tracker=None,
    vehicle_tracker=None,
    vehicle_action_controller=None,
    departed_vehicles: int = 0,
    arrived_vehicles: int = 0,
    previous_action_results: PreviousActionResults | None = None,
) -> AIFrameObservation:
    intersections = _build_intersection_observations(
        traci,
        simulation_time,
        metadata,
        controllers,
        fixed_tracker=fixed_tracker,
        vehicle_tracker=vehicle_tracker,
        vehicle_action_controller=vehicle_action_controller,
    )
    vehicles = (
        vehicle_tracker.observations(reset_interval=False) if vehicle_tracker else {}
    )
    fuel_mg, fuel_ml, braking = (
        vehicle_tracker.totals() if vehicle_tracker else (0.0, 0.0, 0)
    )
    return AIFrameObservation(
        protocol_version=PROTOCOL_VERSION,
        episode_id=metadata.episode_id,
        frame_id=frame_id,
        simulation_time=simulation_time,
        intersections=intersections,
        vehicles=vehicles,
        traffic=TrafficObservation(
            active_vehicles=(
                len(vehicles) if vehicle_tracker else int(traci.vehicle.getIDCount())
            ),
            departed_vehicles=departed_vehicles,
            arrived_vehicles=arrived_vehicles,
            min_expected_vehicles=int(traci.simulation.getMinExpectedNumber()),
            fuel_consumed_mg=fuel_mg,
            fuel_consumed_ml=fuel_ml,
            hard_braking_events=braking,
        ),
        previous_action_results=(
            previous_action_results or PreviousActionResults(step_id=None)
        ),
    )


def _validate_actions(
    actions: object,
    controllers: Mapping[str, SafePhaseController],
) -> Mapping[str, int | None]:
    if not isinstance(actions, Mapping):
        raise TypeError("Algorithm actions must be an object keyed by intersection ID.")
    unknown = set(actions) - set(controllers)
    if unknown:
        raise ValueError(f"Algorithm returned unknown intersections: {sorted(unknown)}")
    result = {}
    for intersection_id, value in actions.items():
        if not isinstance(value, Mapping) or set(value) != {"target_phase"}:
            raise TypeError(
                f"Signal action for {intersection_id} must contain only target_phase."
            )
        target = value["target_phase"]
        if isinstance(target, bool) or not isinstance(target, int):
            raise TypeError(
                f"Action for {intersection_id} must be an integer phase."
            )
        if target not in controllers[intersection_id].phase_order:
            raise ValueError(
                f"Action for {intersection_id} must be one of "
                f"{controllers[intersection_id].phase_order}, got {target}."
            )
        result[intersection_id] = target
    return result


def _parse_origins(values: Sequence[str]) -> Mapping[str, tuple[str, ...]]:
    result = {}
    for value in values:
        if ":" not in value:
            raise ValueError(f"Origin must use intersection:approach, got {value!r}.")
        intersection_id, approach = value.split(":", 1)
        if not intersection_id or not approach:
            raise ValueError(f"Invalid origin {value!r}.")
        result.setdefault(intersection_id, []).append(approach)
    return {key: tuple(items) for key, items in result.items()}


def _load_events(path: Path | None):
    if path is None:
        return ()
    from .events import AccidentEvent, LaneClosureEvent, SpeedLimitEvent

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read event file {path}: {exc}") from exc
    result = []
    for item in raw.get("events", []):
        event_type = item.get("event_type")
        common = {
            "event_id": str(item["event_id"]),
            "start_seconds": float(item["start_seconds"]),
            "end_seconds": float(item["end_seconds"]),
        }
        if event_type == "lane_closure":
            result.append(
                LaneClosureEvent(
                    **common,
                    lane_ids=tuple(str(value) for value in item["lane_ids"]),
                )
            )
        elif event_type == "speed_limit":
            result.append(
                SpeedLimitEvent(
                    **common,
                    lane_ids=tuple(str(value) for value in item["lane_ids"]),
                    max_speed=float(item["max_speed"]),
                )
            )
        elif event_type == "accident":
            result.append(
                AccidentEvent(
                    **common,
                    lane_id=str(item["lane_id"]),
                    position_ratio=float(item["position_ratio"]),
                )
            )
        else:
            raise ValueError(f"Unsupported event_type: {event_type!r}")
    return tuple(result)


def run(args: argparse.Namespace) -> None:
    from .session import SimulationConfig, SimulationManager

    manager = SimulationManager()
    duration = args.duration if args.duration is not None else args.end
    config = SimulationConfig(
        intersection_ids=tuple(args.intersection),
        period=args.period,
        origins=_parse_origins(args.origin),
        window_start_seconds=args.window_start,
        duration_seconds=duration,
        flow_multiplier=args.flow_multiplier,
        control_mode=args.mode,
        algorithm_transport=args.algorithm_transport,
        algorithm_endpoint=args.algorithm_endpoint,
        algorithm_module=args.algorithm_module,
        algorithm_timeout=args.algorithm_timeout,
        decision_interval=args.decision_interval,
        minimum_green=args.minimum_green,
        seed=args.seed,
        step_length=args.step_length,
        gui=args.gui,
        realtime=args.realtime,
        playback_speed=args.playback_speed,
        snapshot_interval_seconds=args.snapshot_interval,
        ai_observer_module=args.ai_observer_module,
        ai_frame_interval_seconds=args.ai_frame_interval,
        ai_observer_shutdown_timeout=args.ai_observer_shutdown_timeout,
        initial_events=_load_events(args.event_file),
    )
    session_id = manager.start(config)
    LOGGER.info("Started SUMO session %s", session_id)
    try:
        while True:
            snapshot = manager.snapshot(session_id)
            if snapshot.state in {"STOPPED", "COMPLETED", "FAILED"}:
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        manager.stop(session_id)
        manager.wait(session_id, timeout=30.0)
        raise
    if snapshot.state == "FAILED":
        raise RuntimeError(snapshot.error or "SUMO session failed.")
    LOGGER.info(
        "Session %s %s: departed=%d arrived=%d",
        session_id,
        snapshot.state,
        snapshot.metrics.departed_vehicles,
        snapshot.metrics.arrived_vehicles,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixed", "algorithm"), default="fixed")
    parser.add_argument("--intersection", nargs="+", default=["demo_2"])
    parser.add_argument(
        "--period",
        choices=("morning_peak", "off_peak", "evening_peak"),
        default="morning_peak",
    )
    parser.add_argument(
        "--origin",
        action="append",
        default=[],
        help="Repeatable official origin such as demo_2:west; default uses all origins.",
    )
    parser.add_argument("--window-start", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--flow-multiplier", type=float, default=1.0)
    parser.add_argument("--event-file", type=Path, default=None)
    parser.add_argument("--algorithm-endpoint", default="")
    parser.add_argument(
        "--algorithm-transport",
        choices=("http", "local"),
        default="http",
    )
    parser.add_argument("--algorithm-module", default="")
    parser.add_argument("--algorithm-timeout", type=float, default=2.0)
    parser.add_argument("--ai-observer-module", default="")
    parser.add_argument("--ai-frame-interval", type=float, default=0.1)
    parser.add_argument("--ai-observer-shutdown-timeout", type=float, default=5.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--decision-interval", type=float, default=5.0)
    parser.add_argument("--minimum-green", type=float, default=5.0)
    parser.add_argument("--step-length", type=float, default=0.05)
    parser.add_argument("--snapshot-interval", type=float, default=0.5)
    parser.add_argument(
        "--end",
        type=float,
        default=None,
        help="Deprecated alias for --duration.",
    )
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument(
        "--playback-speed",
        type=float,
        choices=(1.0, 1.25, 1.5, 2.0, 3.0, 5.0),
        default=None,
        help="Pace simulation at the selected wall-clock multiplier.",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    if args.decision_interval <= 0 or args.step_length <= 0 or args.snapshot_interval <= 0:
        parser.error("decision, step and snapshot intervals must be positive.")
    if args.end is not None and args.end <= 0:
        parser.error("end time must be positive.")
    if args.minimum_green < 0:
        parser.error("minimum green cannot be negative.")
    if args.algorithm_timeout <= 0:
        parser.error("algorithm timeout must be positive.")
    if args.ai_frame_interval + 1e-9 < args.step_length:
        parser.error("AI frame interval cannot be smaller than the SUMO step length.")
    if args.ai_observer_shutdown_timeout <= 0:
        parser.error("AI observer shutdown timeout must be positive.")
    if args.seed < 0:
        parser.error("seed must be non-negative.")
    if args.duration is not None and args.end is not None:
        parser.error("use --duration or deprecated --end, not both.")
    return args


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=logging.DEBUG if args.debug else logging.INFO,
    )
    try:
        run(args)
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user")
    except (SignalConfigurationError, RuntimeError, TypeError, ValueError) as exc:
        raise SystemExit(f"Simulation failed: {exc}") from exc


if __name__ == "__main__":
    main()
