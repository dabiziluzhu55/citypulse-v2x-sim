"""Run fixed official timing or a safe Python signal-control policy."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import time
from collections.abc import Mapping
from numbers import Real
from pathlib import Path
from typing import Dict, Sequence

from .build_tls import (
    DEFAULT_MAPPING,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PLANS,
    DEFAULT_TOPOLOGY,
)
from .config import SignalConfigurationError, load_signal_configuration
from .controller import SafePhaseController, SignalStage, TransitionTiming
from .external_policy import HttpControlPolicy
from .policy import (
    ControlAction,
    IntersectionMetadata,
    IntersectionObservation,
    LaneObservation,
    RoadConnectionMetadata,
    SimulationMetadata,
    SimulationObservation,
    VehicleAdvice,
    VehicleObservation,
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


def _load_policy(specification: str):
    if ":" not in specification:
        raise ValueError("--policy must use the form package.module:ClassName")
    module_name, class_name = specification.split(":", 1)
    module = importlib.import_module(module_name)
    policy_type = getattr(module, class_name)
    policy = policy_type()
    for method in ("reset", "act", "close"):
        if not callable(getattr(policy, method, None)):
            raise TypeError(f"Policy {specification!r} has no callable {method}().")
    return policy


def _load_manifest(path: Path) -> Mapping[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Generated manifest not found: {path}. Run python -m simulation.sumo.build_tls first."
        ) from exc


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


def _build_metadata(
    selected_manifest: Mapping[str, Mapping[str, object]],
    decision_interval: float,
    minimum_green: float,
    network_file: str = "",
) -> SimulationMetadata:
    intersections = {}
    for intersection_id, item in selected_manifest.items():
        movements = {
            int(value["phase_number"]): (
                str(value["movement"]),
                tuple(str(name) for name in value["approaches"]),
            )
            for value in item["phase_movements"]
        }
        intersections[intersection_id] = IntersectionMetadata(
            intersection_id=intersection_id,
            phase_order=tuple(int(value) for value in item["phase_order"]),
            phase_movements=movements,
            incoming_lanes={
                str(approach): tuple(str(lane_id) for lane_id in lane_ids)
                for approach, lane_ids in item["incoming_lanes"].items()
            },
            tls_ids=tuple(str(value) for value in item["tls_ids"]),
            junction_ids=tuple(str(value) for value in item["junction_ids"]),
            connections=tuple(
                RoadConnectionMetadata(
                    approach=str(connection["approach"]),
                    movement=str(connection["movement"]),
                    from_edge=str(connection["from_edge"]),
                    from_lane=int(connection["from_lane"]),
                    to_edge=str(connection["to_edge"]),
                    to_lane=int(connection["to_lane"]),
                    direction=str(connection["direction"]),
                )
                for connection in item["connections"]
            ),
        )
    return SimulationMetadata(
        intersections=intersections,
        decision_interval=decision_interval,
        minimum_green=minimum_green,
        network_file=network_file,
    )


def _build_controllers(
    configurations,
    selected_manifest: Mapping[str, Mapping[str, object]],
    program_id: str,
    minimum_green: float,
) -> Dict[str, SafePhaseController]:
    controllers = {}
    for config in configurations:
        if program_id not in config.programs:
            raise SignalConfigurationError(
                f"{config.intersection_id}: unknown program {program_id!r}."
            )
        program = config.programs[program_id]
        phase_order = tuple(
            int(value) for value in selected_manifest[config.intersection_id]["phase_order"]
        )
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
    metadata: SimulationMetadata,
    controllers: Mapping[str, SafePhaseController],
) -> SimulationObservation:
    observations = {}
    for intersection_id, intersection_metadata in metadata.intersections.items():
        controller = controllers[intersection_id]
        approaches = {}
        for approach, lane_ids in intersection_metadata.incoming_lanes.items():
            lanes = []
            for lane_id in lane_ids:
                lanes.append(
                    LaneObservation(
                        lane_id=lane_id,
                        vehicle_count=int(traci.lane.getLastStepVehicleNumber(lane_id)),
                        halting_count=int(traci.lane.getLastStepHaltingNumber(lane_id)),
                        mean_speed=float(traci.lane.getLastStepMeanSpeed(lane_id)),
                        waiting_time=float(traci.lane.getWaitingTime(lane_id)),
                    )
                )
            approaches[approach] = tuple(lanes)
        observations[intersection_id] = IntersectionObservation(
            intersection_id=intersection_id,
            current_phase=controller.current_phase,
            stage=controller.stage.value,
            stage_elapsed=controller.stage_elapsed(simulation_time),
            approaches=approaches,
        )
    vehicles = {}
    for vehicle_id in traci.vehicle.getIDList():
        vehicles[vehicle_id] = VehicleObservation(
            vehicle_id=vehicle_id,
            road_id=str(traci.vehicle.getRoadID(vehicle_id)),
            lane_id=str(traci.vehicle.getLaneID(vehicle_id)),
            lane_index=int(traci.vehicle.getLaneIndex(vehicle_id)),
            lane_position=float(traci.vehicle.getLanePosition(vehicle_id)),
            speed=float(traci.vehicle.getSpeed(vehicle_id)),
            allowed_speed=float(traci.vehicle.getAllowedSpeed(vehicle_id)),
            waiting_time=float(traci.vehicle.getWaitingTime(vehicle_id)),
            route=tuple(str(edge) for edge in traci.vehicle.getRoute(vehicle_id)),
        )
    return SimulationObservation(
        simulation_time=simulation_time,
        intersections=observations,
        vehicles=vehicles,
    )


def _validate_actions(
    actions,
    intersection_ids: Sequence[str],
    vehicles: Mapping[str, VehicleObservation] | None = None,
) -> ControlAction:
    if isinstance(actions, ControlAction):
        signal_phases = actions.signal_phases
        vehicle_advisories = actions.vehicle_advisories
    elif isinstance(actions, Mapping):
        signal_phases = actions
        vehicle_advisories = {}
    else:
        raise TypeError("Policy.act() must return ControlAction or a phase mapping.")
    if not isinstance(signal_phases, Mapping):
        raise TypeError("ControlAction.signal_phases must be a mapping.")
    unknown = set(signal_phases) - set(intersection_ids)
    if unknown:
        raise ValueError(f"Policy returned unknown intersections: {sorted(unknown)}")
    phases = {}
    for intersection_id, value in signal_phases.items():
        if value is None:
            phases[intersection_id] = None
        elif isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                f"Policy phase for {intersection_id} must be int or None, got {value!r}."
            )
        else:
            phases[intersection_id] = value
    if not isinstance(vehicle_advisories, Mapping):
        raise TypeError("ControlAction.vehicle_advisories must be a mapping.")
    available_vehicles = vehicles or {}
    unknown_vehicles = set(vehicle_advisories) - set(available_vehicles)
    if unknown_vehicles:
        raise ValueError(
            f"Policy returned inactive vehicles: {sorted(unknown_vehicles)}"
        )
    validated_advice = {}
    for vehicle_id, advice in vehicle_advisories.items():
        if not isinstance(advice, VehicleAdvice):
            raise TypeError(f"Advice for {vehicle_id} must be VehicleAdvice.")
        if isinstance(advice.duration, bool) or not isinstance(advice.duration, Real):
            raise TypeError(f"Advice duration for {vehicle_id} must be numeric.")
        if advice.duration <= 0:
            raise ValueError(f"Advice duration for {vehicle_id} must be positive.")
        if advice.target_speed is not None:
            if isinstance(advice.target_speed, bool) or not isinstance(
                advice.target_speed, Real
            ):
                raise TypeError(f"Target speed for {vehicle_id} must be numeric.")
            if not 0 <= advice.target_speed <= available_vehicles[vehicle_id].allowed_speed:
                raise ValueError(
                    f"Target speed for {vehicle_id} must be between 0 and its "
                    f"allowed speed {available_vehicles[vehicle_id].allowed_speed:g}."
                )
        if advice.lane_index is not None and (
            isinstance(advice.lane_index, bool)
            or not isinstance(advice.lane_index, int)
            or advice.lane_index < 0
        ):
            raise ValueError(f"Lane index for {vehicle_id} must be a non-negative int.")
        if advice.target_speed is None and advice.lane_index is None:
            raise ValueError(f"Advice for {vehicle_id} contains no command.")
        validated_advice[vehicle_id] = advice
    return ControlAction(phases, validated_advice)


def _expire_speed_advice(traci, simulation_time: float, expirations: Dict[str, float]) -> None:
    active = set(traci.vehicle.getIDList())
    for vehicle_id, expires_at in tuple(expirations.items()):
        if vehicle_id not in active:
            del expirations[vehicle_id]
        elif simulation_time + 1e-9 >= expires_at:
            traci.vehicle.setSpeed(vehicle_id, -1)
            del expirations[vehicle_id]


def _apply_vehicle_advice(
    traci,
    advisories: Mapping[str, VehicleAdvice],
    simulation_time: float,
    expirations: Dict[str, float],
) -> None:
    for vehicle_id, advice in advisories.items():
        if advice.target_speed is not None:
            traci.vehicle.setSpeed(vehicle_id, float(advice.target_speed))
            expirations[vehicle_id] = simulation_time + float(advice.duration)
        if advice.lane_index is not None:
            traci.vehicle.changeLane(
                vehicle_id,
                int(advice.lane_index),
                float(advice.duration),
            )


def run(args: argparse.Namespace) -> None:
    sumolib, traci = _load_sumo_modules()
    configuration = load_signal_configuration(args.mapping, args.plans, args.topology)
    selected_configs = configuration.select(args.intersection)
    manifest = _load_manifest(args.manifest)
    selected_manifest = _selected_manifest(manifest, args.intersection)
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
    ]
    if args.end is not None:
        command.extend(("--end", str(args.end)))

    controllers = {}
    policy = None
    metadata = None
    speed_advice_expirations: Dict[str, float] = {}
    traci.start(command)
    try:
        if args.mode == "fixed":
            for intersection_id, item in selected_manifest.items():
                if args.program not in item["program_ids"]:
                    raise RuntimeError(
                        f"{intersection_id}: program {args.program!r} was not generated."
                    )
                for tls_id in item["tls_ids"]:
                    traci.trafficlight.setProgram(tls_id, args.program)
        else:
            if not args.policy and not args.policy_endpoint:
                raise RuntimeError(
                    "--policy or --policy-endpoint is required in policy mode."
                )
            controllers = _build_controllers(
                selected_configs,
                selected_manifest,
                args.program,
                args.minimum_green,
            )
            metadata = _build_metadata(
                selected_manifest,
                args.decision_interval,
                args.minimum_green,
                network_file=str(
                    (args.sumocfg.parent / "TotalMap_20.signals.net.xml").resolve()
                ),
            )
            policy = (
                HttpControlPolicy(args.policy_endpoint, args.policy_timeout)
                if args.policy_endpoint
                else _load_policy(args.policy)
            )
            policy.reset(metadata)
            for intersection_id, controller in controllers.items():
                _apply_controller_state(
                    traci, selected_manifest[intersection_id], controller
                )

        next_decision = 0.0
        step_count = 0
        while (
            traci.simulation.getMinExpectedNumber() > 0
            and (args.end is None or traci.simulation.getTime() < args.end)
        ):
            started_at = time.perf_counter()
            traci.simulationStep()
            simulation_time = float(traci.simulation.getTime())
            if args.mode == "policy":
                _expire_speed_advice(
                    traci,
                    simulation_time,
                    speed_advice_expirations,
                )
                for intersection_id, controller in controllers.items():
                    if controller.advance(simulation_time):
                        _apply_controller_state(
                            traci, selected_manifest[intersection_id], controller
                        )
                if simulation_time + 1e-9 >= next_decision:
                    observation = _observe(
                        traci, simulation_time, metadata, controllers
                    )
                    actions = _validate_actions(
                        policy.act(observation),
                        args.intersection,
                        observation.vehicles,
                    )
                    for intersection_id, target_phase in actions.signal_phases.items():
                        if target_phase is None:
                            continue
                        controller = controllers[intersection_id]
                        if controller.request_phase(target_phase, simulation_time):
                            _apply_controller_state(
                                traci,
                                selected_manifest[intersection_id],
                                controller,
                            )
                    _apply_vehicle_advice(
                        traci,
                        actions.vehicle_advisories,
                        simulation_time,
                        speed_advice_expirations,
                    )
                    while next_decision <= simulation_time + 1e-9:
                        next_decision += args.decision_interval
            step_count += 1
            if step_count % 200 == 0:
                LOGGER.info(
                    "t=%.1fs vehicles=%d mode=%s",
                    simulation_time,
                    traci.vehicle.getIDCount(),
                    args.mode,
                )
            if args.realtime:
                elapsed = time.perf_counter() - started_at
                if elapsed < args.step_length:
                    time.sleep(args.step_length - elapsed)
    finally:
        if policy is not None:
            policy.close()
        traci.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixed", "policy"), default="fixed")
    parser.add_argument("--intersection", nargs="+", default=["demo_2"])
    parser.add_argument("--program", default="demo_2_morning_peak")
    parser.add_argument("--policy", default="")
    parser.add_argument(
        "--policy-endpoint",
        default="",
        help="Remote service base URL exposing POST /reset, /act and /close.",
    )
    parser.add_argument("--policy-timeout", type=float, default=2.0)
    parser.add_argument("--sumocfg", type=Path, default=DEFAULT_SUMOCFG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--plans", type=Path, default=DEFAULT_PLANS)
    parser.add_argument("--topology", type=Path, default=DEFAULT_TOPOLOGY)
    parser.add_argument("--decision-interval", type=float, default=1.0)
    parser.add_argument("--minimum-green", type=float, default=5.0)
    parser.add_argument("--step-length", type=float, default=0.05)
    parser.add_argument(
        "--end",
        type=float,
        default=None,
        help="Optional override; otherwise use the end time in the selected sumocfg.",
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
    if args.policy_timeout <= 0:
        parser.error("policy timeout must be positive.")
    if args.policy and args.policy_endpoint:
        parser.error("use either --policy or --policy-endpoint, not both.")
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
