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
from .policy import (
    IntersectionMetadata,
    IntersectionObservation,
    LaneObservation,
    SimulationMetadata,
    SimulationObservation,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_SUMOCFG = DEFAULT_OUTPUT_DIR / "official_tls.sumocfg"
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
        )
    return SimulationMetadata(
        intersections=intersections,
        decision_interval=decision_interval,
        minimum_green=minimum_green,
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
    return SimulationObservation(
        simulation_time=simulation_time,
        intersections=observations,
    )


def _validate_actions(actions, intersection_ids: Sequence[str]) -> Mapping[str, int | None]:
    if not isinstance(actions, Mapping):
        raise TypeError("Policy.act() must return a mapping of intersection id to phase.")
    unknown = set(actions) - set(intersection_ids)
    if unknown:
        raise ValueError(f"Policy returned unknown intersections: {sorted(unknown)}")
    result = {}
    for intersection_id, value in actions.items():
        if value is None:
            result[intersection_id] = None
        elif isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                f"Policy phase for {intersection_id} must be int or None, got {value!r}."
            )
        else:
            result[intersection_id] = value
    return result


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
        "--end",
        str(args.end),
        "--no-step-log",
        "true",
        "--collision.action",
        "warn",
        "--collision.check-junctions",
        "true",
    ]

    controllers = {}
    policy = None
    metadata = None
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
            if not args.policy:
                raise RuntimeError("--policy is required when --mode policy is selected.")
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
            )
            policy = _load_policy(args.policy)
            policy.reset(metadata)
            for intersection_id, controller in controllers.items():
                _apply_controller_state(
                    traci, selected_manifest[intersection_id], controller
                )

        next_decision = 0.0
        step_count = 0
        while (
            traci.simulation.getMinExpectedNumber() > 0
            and traci.simulation.getTime() < args.end
        ):
            started_at = time.perf_counter()
            traci.simulationStep()
            simulation_time = float(traci.simulation.getTime())
            if args.mode == "policy":
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
                        policy.act(observation), args.intersection
                    )
                    for intersection_id, target_phase in actions.items():
                        if target_phase is None:
                            continue
                        controller = controllers[intersection_id]
                        if controller.request_phase(target_phase, simulation_time):
                            _apply_controller_state(
                                traci,
                                selected_manifest[intersection_id],
                                controller,
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
    parser.add_argument("--intersection", nargs="+", default=["demo_1"])
    parser.add_argument("--program", default="demo_1_morning_peak")
    parser.add_argument("--policy", default="")
    parser.add_argument("--sumocfg", type=Path, default=DEFAULT_SUMOCFG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--plans", type=Path, default=DEFAULT_PLANS)
    parser.add_argument("--topology", type=Path, default=DEFAULT_TOPOLOGY)
    parser.add_argument("--decision-interval", type=float, default=1.0)
    parser.add_argument("--minimum-green", type=float, default=5.0)
    parser.add_argument("--step-length", type=float, default=0.05)
    parser.add_argument("--end", type=float, default=200.0)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    if args.decision_interval <= 0 or args.step_length <= 0 or args.end <= 0:
        parser.error("decision interval, step length and end time must be positive.")
    if args.minimum_green < 0:
        parser.error("minimum green cannot be negative.")
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
