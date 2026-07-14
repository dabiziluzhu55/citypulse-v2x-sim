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
from uuid import uuid4

from .build_tls import (
    DEFAULT_MAPPING,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PLANS,
    DEFAULT_TOPOLOGY,
)
from .config import SignalConfigurationError, load_signal_configuration
from .controller import SafePhaseController, SignalStage, TransitionTiming
from .external_policy import HttpAlgorithmClient
from .policy import (
    PROTOCOL_VERSION,
    IntersectionMetadata,
    IntersectionObservation,
    LaneMetadata,
    LaneObservation,
    PhaseMetadata,
    RoadConnectionMetadata,
    SimulationMetadata,
    SimulationObservation,
    TrafficObservation,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_SUMOCFG = DEFAULT_OUTPUT_DIR / "official_traffic_demo_2_morning_peak.sumocfg"
DEFAULT_MANIFEST = DEFAULT_OUTPUT_DIR / "tls_manifest.json"


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
            lanes[lane_id] = LaneMetadata(
                lane_id=lane_id,
                edge_id=edge_id,
                lane_index=lane_index,
                role=role,
                length=float(traci.lane.getLength(lane_id)),
                max_speed=float(traci.lane.getMaxSpeed(lane_id)),
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


def _observe(
    traci,
    simulation_time: float,
    step_id: int,
    metadata: SimulationMetadata,
    controllers: Mapping[str, SafePhaseController],
    departed_vehicles: int,
    arrived_vehicles: int,
) -> SimulationObservation:
    observations = {}
    for intersection_id, intersection_metadata in metadata.intersections.items():
        controller = controllers[intersection_id]
        lanes = {}
        for lane_id in intersection_metadata.lanes:
            vehicle_count = int(traci.lane.getLastStepVehicleNumber(lane_id))
            lanes[lane_id] = LaneObservation(
                vehicle_count=vehicle_count,
                halting_count=int(traci.lane.getLastStepHaltingNumber(lane_id)),
                mean_speed=(
                    float(traci.lane.getLastStepMeanSpeed(lane_id))
                    if vehicle_count
                    else 0.0
                ),
                waiting_time=float(traci.lane.getWaitingTime(lane_id)),
                occupancy=float(traci.lane.getLastStepOccupancy(lane_id)),
            )
        observations[intersection_id] = IntersectionObservation(
            current_phase=controller.current_phase,
            pending_phase=controller.pending_phase,
            stage=controller.stage.value,
            stage_elapsed=controller.stage_elapsed(simulation_time),
            lanes=lanes,
        )
    return SimulationObservation(
        protocol_version=PROTOCOL_VERSION,
        episode_id=metadata.episode_id,
        step_id=step_id,
        simulation_time=simulation_time,
        intersections=observations,
        traffic=TrafficObservation(
            active_vehicles=int(traci.vehicle.getIDCount()),
            departed_vehicles=departed_vehicles,
            arrived_vehicles=arrived_vehicles,
            min_expected_vehicles=int(traci.simulation.getMinExpectedNumber()),
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
        if value is None:
            result[intersection_id] = None
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                f"Action for {intersection_id} must be an integer phase or null."
            )
        if value not in controllers[intersection_id].phase_order:
            raise ValueError(
                f"Action for {intersection_id} must be one of "
                f"{controllers[intersection_id].phase_order}, got {value}."
            )
        result[intersection_id] = value
    return result


def run(args: argparse.Namespace) -> None:
    sumolib, traci = _load_sumo_modules()
    configuration = load_signal_configuration(args.mapping, args.plans, args.topology)
    selected_configs = configuration.select(args.intersection)
    selected_manifest = _selected_manifest(_load_manifest(args.manifest), args.intersection)
    programs = _select_programs(selected_configs, args.program, args.period)
    if not args.sumocfg.is_file():
        raise RuntimeError(f"SUMO configuration not found: {args.sumocfg}")

    binary = sumolib.checkBinary("sumo-gui" if args.gui else "sumo")
    command = [
        binary,
        "--configuration-file",
        str(args.sumocfg),
        "--step-length",
        str(args.step_length),
        "--no-step-log",
        "true",
        "--collision.action",
        "warn",
        "--collision.check-junctions",
        "true",
        "--seed",
        str(args.seed),
    ]
    if args.end is not None:
        command.extend(("--end", str(args.end)))

    controllers = {}
    client = None
    episode_id = str(uuid4())
    finish_reason = "completed"
    last_simulation_time = 0.0
    total_departed = 0
    total_arrived = 0
    traci.start(command)
    try:
        if args.mode == "fixed":
            for config in selected_configs:
                program_id = programs[config.intersection_id].program_id
                item = selected_manifest[config.intersection_id]
                if program_id not in item["program_ids"]:
                    raise RuntimeError(
                        f"{config.intersection_id}: program {program_id!r} was not generated."
                    )
                for tls_id in item["tls_ids"]:
                    traci.trafficlight.setProgram(tls_id, program_id)
        else:
            if not args.algorithm_endpoint:
                raise RuntimeError("--algorithm-endpoint is required in algorithm mode.")
            controllers = _build_controllers(
                selected_configs,
                selected_manifest,
                programs,
                args.minimum_green,
            )
            metadata = _build_metadata(
                traci,
                selected_manifest,
                programs,
                args.period,
                args.seed,
                args.decision_interval,
                args.minimum_green,
                episode_id,
            )
            client = HttpAlgorithmClient(args.algorithm_endpoint, args.algorithm_timeout)
            client.initialize(metadata)
            for intersection_id, controller in controllers.items():
                _apply_controller_state(
                    traci, selected_manifest[intersection_id], controller
                )

        next_decision = 0.0
        decision_step = 0
        simulation_steps = 0
        departed_since_decision = 0
        arrived_since_decision = 0
        while (
            traci.simulation.getMinExpectedNumber() > 0
            and (args.end is None or traci.simulation.getTime() < args.end)
        ):
            started_at = time.perf_counter()
            traci.simulationStep()
            last_simulation_time = float(traci.simulation.getTime())
            departed = int(traci.simulation.getDepartedNumber())
            arrived = int(traci.simulation.getArrivedNumber())
            departed_since_decision += departed
            arrived_since_decision += arrived
            total_departed += departed
            total_arrived += arrived
            if args.mode == "algorithm":
                for intersection_id, controller in controllers.items():
                    if controller.advance(last_simulation_time):
                        _apply_controller_state(
                            traci, selected_manifest[intersection_id], controller
                        )
                if last_simulation_time + 1e-9 >= next_decision:
                    observation = _observe(
                        traci,
                        last_simulation_time,
                        decision_step,
                        metadata,
                        controllers,
                        departed_since_decision,
                        arrived_since_decision,
                    )
                    actions = _validate_actions(
                        client.decide(observation), controllers
                    )
                    for intersection_id, target_phase in actions.items():
                        if target_phase is None:
                            continue
                        controller = controllers[intersection_id]
                        if controller.request_phase(target_phase, last_simulation_time):
                            _apply_controller_state(
                                traci,
                                selected_manifest[intersection_id],
                                controller,
                            )
                    departed_since_decision = 0
                    arrived_since_decision = 0
                    decision_step += 1
                    while next_decision <= last_simulation_time + 1e-9:
                        next_decision += args.decision_interval
            simulation_steps += 1
            if simulation_steps % 200 == 0:
                LOGGER.info(
                    "t=%.1fs vehicles=%d mode=%s",
                    last_simulation_time,
                    traci.vehicle.getIDCount(),
                    args.mode,
                )
            if args.realtime:
                elapsed = time.perf_counter() - started_at
                if elapsed < args.step_length:
                    time.sleep(args.step_length - elapsed)
    except BaseException:
        finish_reason = "error"
        raise
    finally:
        if client is not None:
            try:
                client.finish(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "episode_id": episode_id,
                        "reason": finish_reason,
                        "simulation_time": last_simulation_time,
                        "departed_vehicles": total_departed,
                        "arrived_vehicles": total_arrived,
                    }
                )
            except Exception:
                LOGGER.exception("Could not notify algorithm service that the run finished")
        traci.close()


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
        "--program",
        default="",
        help="Optional exact program ID; normally inferred as demo_N_PERIOD.",
    )
    parser.add_argument("--algorithm-endpoint", default="")
    parser.add_argument("--algorithm-timeout", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sumocfg", type=Path, default=DEFAULT_SUMOCFG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--plans", type=Path, default=DEFAULT_PLANS)
    parser.add_argument("--topology", type=Path, default=DEFAULT_TOPOLOGY)
    parser.add_argument("--decision-interval", type=float, default=5.0)
    parser.add_argument("--minimum-green", type=float, default=5.0)
    parser.add_argument("--step-length", type=float, default=0.05)
    parser.add_argument(
        "--end",
        type=float,
        default=None,
        help="Optional override; otherwise use the selected sumocfg end time.",
    )
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    if args.decision_interval <= 0 or args.step_length <= 0:
        parser.error("decision interval and step length must be positive.")
    if args.end is not None and args.end <= 0:
        parser.error("end time must be positive.")
    if args.minimum_green < 0:
        parser.error("minimum green cannot be negative.")
    if args.algorithm_timeout <= 0:
        parser.error("algorithm timeout must be positive.")
    if args.seed < 0:
        parser.error("seed must be non-negative.")
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
