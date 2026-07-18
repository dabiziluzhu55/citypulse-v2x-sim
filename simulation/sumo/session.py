"""Thread-safe, single-session API intended for the future backend service."""

from __future__ import annotations

import json
import queue
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Sequence
from uuid import uuid4

from .artifacts import GeneratedArtifactLayout
from .events import (
    AccidentEvent,
    DisturbanceEvent,
    DisturbanceScheduler,
    EventSnapshot,
    EventValidationError,
    LaneClosureEvent,
    LaneTarget,
    SpeedLimitEvent,
)
from .external_policy import HttpAlgorithmClient
from .local_policy import LocalAlgorithmClient
from .ai_observer import LocalAIObserver, SimulationTimeFrameClock
from .policy import PROTOCOL_VERSION
from .scenario import (
    DEFAULT_GENERATED_DIR,
    DEFAULT_SESSION_ROOT,
    CompiledScenario,
    ScenarioCompilationError,
    compile_session_scenario,
)


class SessionError(RuntimeError):
    pass


class SessionBusyError(SessionError):
    pass


class UnknownSessionError(SessionError):
    pass


PLAYBACK_SPEEDS = (1.0, 1.25, 1.5, 2.0, 3.0, 5.0)


def _normalize_playback_speed(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("playback speed must be a number, not a boolean.")
    try:
        speed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid playback speed: {value!r}.") from exc
    if speed not in PLAYBACK_SPEEDS:
        raise ValueError(
            f"playback speed must be one of {PLAYBACK_SPEEDS}, got {value!r}."
        )
    return speed


def _playback_delay_seconds(
    step_length: float,
    playback_speed: float | None,
    spent_seconds: float,
) -> float:
    if playback_speed is None:
        return 0.0
    return max(0.0, step_length / playback_speed - spent_seconds)


@dataclass(frozen=True)
class SimulationConfig:
    intersection_ids: tuple[str, ...]
    period: str = "morning_peak"
    origins: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    window_start_seconds: float = 0.0
    duration_seconds: float | None = None
    flow_multiplier: float = 1.0
    control_mode: str = "fixed"
    algorithm_transport: str = "http"
    algorithm_endpoint: str = ""
    algorithm_module: str = ""
    algorithm_timeout: float = 2.0
    decision_interval: float = 5.0
    minimum_green: float = 5.0
    seed: int = 42
    step_length: float = 0.05
    gui: bool = False
    realtime: bool = False
    playback_speed: float | None = None
    start_paused: bool = False
    snapshot_interval_seconds: float = 0.5
    ai_observer_module: str = ""
    ai_frame_interval_seconds: float = 0.1
    ai_observer_shutdown_timeout: float = 5.0
    initial_events: tuple[DisturbanceEvent, ...] = ()


@dataclass(frozen=True)
class OriginCapability:
    origin_id: str
    label: str
    lane_ids: tuple[str, ...]


@dataclass(frozen=True)
class LaneCapability:
    lane_id: str
    edge_id: str
    lane_index: int
    role: str
    approach: str | None
    approach_label: str | None
    length: float
    max_speed: float


@dataclass(frozen=True)
class IntersectionCapability:
    intersection_id: str
    longitude: float | None
    latitude: float | None
    periods: tuple[str, ...]
    origins: tuple[OriginCapability, ...]
    lanes: tuple[LaneCapability, ...]


@dataclass(frozen=True)
class SimulationCatalog:
    intersections: Mapping[str, IntersectionCapability]
    event_types: tuple[str, ...] = ("lane_closure", "speed_limit", "accident")
    flow_multiplier_min: float = 0.1
    flow_multiplier_max: float = 5.0
    playback_speeds: tuple[float, ...] = PLAYBACK_SPEEDS


@dataclass(frozen=True)
class LaneRuntimeSnapshot:
    vehicle_count: int
    halting_count: int
    mean_speed: float
    waiting_time: float
    occupancy: float


@dataclass(frozen=True)
class IntersectionRuntimeSnapshot:
    current_phase: int
    pending_phase: int | None
    stage: str
    stage_elapsed: float
    lanes: Mapping[str, LaneRuntimeSnapshot]


@dataclass(frozen=True)
class VehicleRuntimeSnapshot:
    vehicle_id: str
    x: float
    y: float
    speed: float
    angle: float
    road_id: str
    lane_id: str
    controllable: bool = False
    type_id: str = ""
    acceleration: float = 0.0
    lane_index: int = -1
    lane_position: float = 0.0
    allowed_speed: float = 0.0
    route_id: str = ""
    route_index: int = -1
    waiting_time: float = 0.0
    time_loss: float = 0.0
    distance: float = 0.0
    fuel_rate_mg_s: float = 0.0
    fuel_total_mg: float = 0.0
    fuel_total_ml: float = 0.0
    hard_braking_events: int = 0
    next_intersection_id: str | None = None
    target_speed: float | None = None
    target_lane_index: int | None = None


@dataclass(frozen=True)
class SessionMetrics:
    active_vehicles: int = 0
    departed_vehicles: int = 0
    arrived_vehicles: int = 0
    remaining_vehicles: int = 0
    halting_vehicles: int = 0
    total_waiting_time: float = 0.0
    mean_speed: float = 0.0
    fuel_consumed_mg: float = 0.0
    fuel_consumed_ml: float = 0.0
    hard_braking_events: int = 0


@dataclass(frozen=True)
class SimulationSnapshot:
    session_id: str
    state: str
    sequence: int
    elapsed_seconds: float
    duration_seconds: float
    progress: float
    official_time: str
    playback_speed: float | None = None
    intersections: Mapping[str, IntersectionRuntimeSnapshot] = field(default_factory=dict)
    vehicles: tuple[VehicleRuntimeSnapshot, ...] = ()
    events: tuple[EventSnapshot, ...] = ()
    metrics: SessionMetrics = field(default_factory=SessionMetrics)
    error: str | None = None


@dataclass
class _Command:
    name: str
    payload: object = None
    completed: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


@dataclass
class _SessionRecord:
    session_id: str
    config: SimulationConfig
    scenario: CompiledScenario
    commands: queue.Queue[_Command] = field(default_factory=queue.Queue)
    subscribers: list[queue.Queue[SimulationSnapshot]] = field(default_factory=list)
    snapshot: SimulationSnapshot | None = None
    thread: threading.Thread | None = None
    paused: bool = False
    playback_speed: float | None = None


class SnapshotSubscription:
    def __init__(self, manager: "SimulationManager", session_id: str, channel) -> None:
        self._manager = manager
        self._session_id = session_id
        self._channel = channel
        self._closed = False

    def get(self, timeout: float | None = None) -> SimulationSnapshot:
        return self._channel.get(timeout=timeout)

    def close(self) -> None:
        if not self._closed:
            self._manager._unsubscribe(self._session_id, self._channel)
            self._closed = True


def _read_json(path: Path) -> Mapping[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise SessionError(f"Cannot read generated metadata {path}: {exc}") from exc


def _lane_specs(net_path: Path, required: set[str]):
    result = {}
    try:
        for _, element in ET.iterparse(net_path, events=("end",)):
            if element.tag == "lane" and element.get("id") in required:
                result[element.get("id")] = (
                    float(element.get("length", "0")),
                    float(element.get("speed", "0")),
                )
            element.clear()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise SessionError(f"Cannot inspect generated network {net_path}: {exc}") from exc
    missing = required - set(result)
    if missing:
        raise SessionError(f"Generated network is missing lanes: {sorted(missing)}")
    return result


def load_catalog(generated_dir: Path = DEFAULT_GENERATED_DIR) -> SimulationCatalog:
    layout = GeneratedArtifactLayout(generated_dir)
    traffic = _read_json(layout.traffic_manifest)
    tls = _read_json(layout.tls_manifest)
    if int(traffic.get("schema_version", 0)) != 2:
        raise SessionError("Rebuild traffic artifacts to obtain manifest schema_version 2.")
    if int(tls.get("schema_version", 0)) != 2:
        raise SessionError("Rebuild signal artifacts to obtain manifest schema_version 2.")
    mapping_path = generated_dir.parent / "TotalMap_20.intersections.json"
    mapping = _read_json(mapping_path)
    scenarios = traffic.get("scenarios", {})
    by_intersection = {}
    for scenario in scenarios.values():
        by_intersection.setdefault(str(scenario["intersection_id"]), []).append(scenario)
    required_lanes = set()
    for intersection_id in by_intersection:
        for connection in tls["intersections"][intersection_id]["connections"]:
            required_lanes.add(f"{connection['from_edge']}_{connection['from_lane']}")
            required_lanes.add(f"{connection['to_edge']}_{connection['to_lane']}")
    specs = _lane_specs(layout.network_file, required_lanes)

    intersections = {}
    period_order = {"morning_peak": 0, "off_peak": 1, "evening_peak": 2}
    for intersection_id, own_scenarios in sorted(by_intersection.items()):
        tls_item = tls["intersections"].get(intersection_id)
        if tls_item is None:
            continue
        origin_data = own_scenarios[0].get("origins", {})
        origins = tuple(
            OriginCapability(
                origin_id=name,
                label=str(item["label"]),
                lane_ids=tuple(str(value) for value in item["lane_ids"]),
            )
            for name, item in sorted(origin_data.items())
        )
        sumo_to_official = {
            str(item["sumo_approach"]): (name, str(item["label"]))
            for name, item in origin_data.items()
        }
        lane_values = {}
        for connection in tls_item["connections"]:
            incoming = f"{connection['from_edge']}_{connection['from_lane']}"
            outgoing = f"{connection['to_edge']}_{connection['to_lane']}"
            official = sumo_to_official.get(str(connection["approach"]))
            lane_values[incoming] = (
                str(connection["from_edge"]),
                int(connection["from_lane"]),
                "incoming",
                official,
            )
            lane_values.setdefault(
                outgoing,
                (str(connection["to_edge"]), int(connection["to_lane"]), "outgoing", None),
            )
        lanes = tuple(
            LaneCapability(
                lane_id=lane_id,
                edge_id=value[0],
                lane_index=value[1],
                role=value[2],
                approach=value[3][0] if value[3] else None,
                approach_label=value[3][1] if value[3] else None,
                length=specs[lane_id][0],
                max_speed=specs[lane_id][1],
            )
            for lane_id, value in sorted(lane_values.items())
        )
        mapped = mapping.get(intersection_id, {})
        intersections[intersection_id] = IntersectionCapability(
            intersection_id=intersection_id,
            longitude=float(mapped["lon"]) if "lon" in mapped else None,
            latitude=float(mapped["lat"]) if "lat" in mapped else None,
            periods=tuple(
                sorted(
                    (str(item["period_id"]) for item in own_scenarios),
                    key=lambda value: (period_order.get(value, 99), value),
                )
            ),
            origins=origins,
            lanes=lanes,
        )
    return SimulationCatalog(intersections=intersections)


class SimulationManager:
    def __init__(
        self,
        *,
        generated_dir: Path = DEFAULT_GENERATED_DIR,
        session_root: Path = DEFAULT_SESSION_ROOT,
    ) -> None:
        self.generated_dir = generated_dir
        self.session_root = session_root
        self._lock = threading.RLock()
        self._sessions: dict[str, _SessionRecord] = {}
        self._active_session_id: str | None = None
        self._catalog: SimulationCatalog | None = None

    def catalog(self) -> SimulationCatalog:
        with self._lock:
            if self._catalog is None:
                self._catalog = load_catalog(self.generated_dir)
            return self._catalog

    def start(self, config: SimulationConfig) -> str:
        self._validate_config(config)
        with self._lock:
            if self._active_session_id is not None:
                active = self._sessions[self._active_session_id]
                if active.snapshot is None or active.snapshot.state not in {
                    "STOPPED",
                    "COMPLETED",
                    "FAILED",
                }:
                    raise SessionBusyError("A SUMO simulation is already active.")
            session_id = str(uuid4())
            scenario = compile_session_scenario(
                session_id,
                config.intersection_ids,
                config.period,
                origins=config.origins,
                window_start_seconds=config.window_start_seconds,
                duration_seconds=config.duration_seconds,
                flow_multiplier=config.flow_multiplier,
                step_length=config.step_length,
                generated_dir=self.generated_dir,
                session_root=self.session_root,
            )
            record = _SessionRecord(
                session_id,
                config,
                scenario,
                paused=config.start_paused,
                playback_speed=(
                    _normalize_playback_speed(config.playback_speed)
                    if config.playback_speed is not None
                    else (1.0 if config.realtime or config.start_paused else None)
                ),
            )
            record.snapshot = self._empty_snapshot(record, "STARTING")
            self._sessions[session_id] = record
            self._active_session_id = session_id
            thread = threading.Thread(
                target=self._run_worker,
                args=(record,),
                name=f"sumo-session-{session_id[:8]}",
                daemon=True,
            )
            record.thread = thread
            thread.start()
            return session_id

    def stop(self, session_id: str) -> None:
        self._command(session_id, "stop")

    def pause(self, session_id: str) -> None:
        self.set_playing(session_id, False)

    def resume(self, session_id: str) -> None:
        self.set_playing(session_id, True)

    def set_playing(self, session_id: str, playing: bool) -> None:
        if not isinstance(playing, bool):
            raise ValueError("playing must be a boolean.")
        self._command(session_id, "resume" if playing else "pause")

    def set_playback_speed(self, session_id: str, speed: float) -> None:
        self._command(
            session_id,
            "set_playback_speed",
            _normalize_playback_speed(speed),
        )

    def add_event(self, session_id: str, event: DisturbanceEvent) -> str:
        if not event.event_id:
            event = replace(event, event_id=str(uuid4()))
        self._command(session_id, "add_event", event)
        return event.event_id

    def cancel_event(self, session_id: str, event_id: str) -> None:
        self._command(session_id, "cancel_event", event_id)

    def snapshot(self, session_id: str) -> SimulationSnapshot:
        with self._lock:
            record = self._record(session_id)
            return record.snapshot

    def subscribe(self, session_id: str) -> SnapshotSubscription:
        channel: queue.Queue[SimulationSnapshot] = queue.Queue(maxsize=1)
        with self._lock:
            record = self._record(session_id)
            record.subscribers.append(channel)
            channel.put_nowait(record.snapshot)
        return SnapshotSubscription(self, session_id, channel)

    def wait(self, session_id: str, timeout: float | None = None) -> SimulationSnapshot:
        record = self._record(session_id)
        record.thread.join(timeout=timeout)
        if record.thread.is_alive():
            raise TimeoutError(f"Session {session_id} did not finish before the timeout.")
        return self.snapshot(session_id)

    def _validate_config(self, config: SimulationConfig) -> None:
        catalog = self.catalog()
        unknown = set(config.intersection_ids) - set(catalog.intersections)
        if unknown:
            raise ScenarioCompilationError(f"Unknown intersections: {sorted(unknown)}")
        if config.control_mode not in {"fixed", "algorithm"}:
            raise ScenarioCompilationError("control_mode must be fixed or algorithm.")
        if config.algorithm_transport not in {"http", "local"}:
            raise ScenarioCompilationError(
                "algorithm_transport must be http or local."
            )
        if config.control_mode == "algorithm":
            if config.algorithm_transport == "http" and not config.algorithm_endpoint:
                raise ScenarioCompilationError(
                    "algorithm_endpoint is required for HTTP algorithm transport."
                )
            if config.algorithm_transport == "local" and not config.algorithm_module:
                raise ScenarioCompilationError(
                    "algorithm_module is required for local algorithm transport."
                )
        if config.seed < 0 or config.snapshot_interval_seconds <= 0:
            raise ScenarioCompilationError("seed and snapshot interval are invalid.")
        if config.step_length <= 0:
            raise ScenarioCompilationError("step_length must be positive.")
        if config.decision_interval <= 0 or config.minimum_green < 0:
            raise ScenarioCompilationError("Algorithm timing values are invalid.")
        if config.ai_frame_interval_seconds + 1e-9 < config.step_length:
            raise ScenarioCompilationError(
                "ai_frame_interval_seconds cannot be smaller than step_length."
            )
        if config.ai_observer_shutdown_timeout <= 0:
            raise ScenarioCompilationError(
                "ai_observer_shutdown_timeout must be positive."
            )
        if config.playback_speed is not None:
            try:
                _normalize_playback_speed(config.playback_speed)
            except ValueError as exc:
                raise ScenarioCompilationError(str(exc)) from exc
        lane_ids = {
            lane.lane_id
            for intersection_id in config.intersection_ids
            for lane in catalog.intersections[intersection_id].lanes
        }
        event_ids = set()
        duration = config.duration_seconds
        for event in config.initial_events:
            if not event.event_id or event.event_id in event_ids:
                raise EventValidationError("Initial event IDs must be non-empty and unique.")
            event_ids.add(event.event_id)
            if set((event.lane_id,) if isinstance(event, AccidentEvent) else event.lane_ids) - lane_ids:
                raise EventValidationError(f"Initial event {event.event_id} targets unknown lanes.")
            if duration is not None and event.end_seconds > duration + 1e-9:
                raise EventValidationError(f"Initial event {event.event_id} exceeds duration.")

    def _command(self, session_id: str, name: str, payload: object = None) -> None:
        record = self._record(session_id)
        if record.snapshot.state not in {
            "STARTING",
            "RUNNING",
            "PAUSED",
            "STOPPING",
        }:
            raise SessionError(f"Session {session_id} is not active.")
        command = _Command(name=name, payload=payload)
        record.commands.put(command)
        if not command.completed.wait(timeout=30.0):
            raise TimeoutError(f"Session command {name} timed out.")
        if command.error is not None:
            raise command.error

    def _record(self, session_id: str) -> _SessionRecord:
        with self._lock:
            if session_id not in self._sessions:
                raise UnknownSessionError(f"Unknown session: {session_id}")
            return self._sessions[session_id]

    def _unsubscribe(self, session_id: str, channel) -> None:
        with self._lock:
            record = self._sessions.get(session_id)
            if record and channel in record.subscribers:
                record.subscribers.remove(channel)

    def _publish(self, record: _SessionRecord, snapshot: SimulationSnapshot) -> None:
        with self._lock:
            record.snapshot = snapshot
            for channel in tuple(record.subscribers):
                try:
                    channel.get_nowait()
                except queue.Empty:
                    pass
                try:
                    channel.put_nowait(snapshot)
                except queue.Full:
                    pass

    def _empty_snapshot(
        self, record: _SessionRecord, state: str, error: str | None = None
    ) -> SimulationSnapshot:
        scenario = record.scenario
        return SimulationSnapshot(
            session_id=record.session_id,
            state=state,
            sequence=0,
            elapsed_seconds=0.0,
            duration_seconds=scenario.duration_seconds,
            progress=0.0,
            official_time=_format_clock(
                scenario.official_start_seconds + scenario.window_start_seconds
            ),
            playback_speed=record.playback_speed,
            error=error,
        )

    def _run_worker(self, record: _SessionRecord) -> None:
        from .run import (
            _apply_controller_state,
            _build_controllers,
            _build_metadata,
            _load_manifest,
            _load_sumo_modules,
            _select_programs,
            _select_program_manifests,
            _selected_manifest,
            _validate_actions,
        )
        from .config import load_signal_configuration
        from .build_tls import DEFAULT_MAPPING, DEFAULT_PLANS, DEFAULT_TOPOLOGY
        from .vehicle import (
            VehicleActionController,
            VehicleTelemetryTracker,
            build_vehicle_type_metadata,
        )

        config = record.config
        scenario = record.scenario
        traci = None
        client = None
        client_initialized = False
        ai_observer = None
        scheduler = None
        controllers = {}
        fixed_tracker = None
        vehicle_tracker = None
        vehicle_action_controller = None
        last_snapshot = record.snapshot
        stop_requested = False
        finish_reason = "completed"
        total_departed = 0
        total_arrived = 0
        sequence = 0
        try:
            sumolib, traci = _load_sumo_modules()
            configuration = load_signal_configuration(
                DEFAULT_MAPPING, DEFAULT_PLANS, DEFAULT_TOPOLOGY
            )
            selected_configs = configuration.select(config.intersection_ids)
            selected_manifest = _selected_manifest(
                _load_manifest(
                    GeneratedArtifactLayout(self.generated_dir).tls_manifest
                ),
                config.intersection_ids,
            )
            programs = _select_programs(selected_configs, "", config.period)
            selected_manifest = _select_program_manifests(
                selected_manifest, programs
            )
            command = [
                sumolib.checkBinary("sumo-gui" if config.gui else "sumo"),
                "--configuration-file",
                str(scenario.sumocfg),
                "--step-length",
                str(config.step_length),
                "--seed",
                str(config.seed),
                "--no-step-log",
                "true",
                "--collision.action",
                "warn",
            ]
            traci.start(command)
            vehicle_types = build_vehicle_type_metadata(
                scenario.vehicle_type_profiles,
                scenario.vehicle_profiles,
            )
            tls_to_intersection = {
                str(tls_id): intersection_id
                for intersection_id, item in selected_manifest.items()
                for tls_id in item["tls_ids"]
            }
            vehicle_tracker = VehicleTelemetryTracker(
                traci, vehicle_types, tls_to_intersection
            )
            lane_targets = _lane_targets(traci, selected_manifest)
            scheduler = DisturbanceScheduler(
                traci, lane_targets, scenario.duration_seconds
            )
            for event in config.initial_events:
                scheduler.schedule(event)

            if config.control_mode == "fixed":
                for own_config in selected_configs:
                    program_id = programs[own_config.intersection_id].program_id
                    for tls_id in selected_manifest[own_config.intersection_id]["tls_ids"]:
                        traci.trafficlight.setProgram(tls_id, program_id)
                fixed_tracker = _FixedSignalTracker(selected_manifest)
                fixed_tracker.tick(traci, 0.0)
            else:
                controllers = _build_controllers(
                    selected_configs,
                    selected_manifest,
                    programs,
                    minimum_green=config.minimum_green,
                )
                vehicle_action_controller = VehicleActionController(
                    traci, vehicle_tracker
                )
                for intersection_id, controller in controllers.items():
                    _apply_controller_state(
                        traci, selected_manifest[intersection_id], controller
                    )

            metadata = _build_metadata(
                traci,
                selected_manifest,
                programs,
                config.period,
                config.seed,
                decision_interval=config.decision_interval,
                minimum_green=config.minimum_green,
                episode_id=record.session_id,
                vehicle_types=vehicle_types,
            )
            if config.control_mode == "algorithm":
                if config.algorithm_transport == "local":
                    client = LocalAlgorithmClient(config.algorithm_module)
                else:
                    client = HttpAlgorithmClient(
                        config.algorithm_endpoint, config.algorithm_timeout
                    )
                client.initialize(metadata)
                client_initialized = True
            if config.ai_observer_module:
                ai_observer = LocalAIObserver(config.ai_observer_module)
                ai_observer.initialize(metadata)

            self._publish(
                record,
                replace(
                    record.snapshot,
                    state="PAUSED" if record.paused else "RUNNING",
                    playback_speed=record.playback_speed,
                ),
            )
            next_decision = 0.0
            decision_step = 0
            next_snapshot = 0.0
            departed_since_decision = 0
            arrived_since_decision = 0
            ai_frame_clock = SimulationTimeFrameClock(
                config.ai_frame_interval_seconds
            )
            departed_since_ai_frame = 0
            arrived_since_ai_frame = 0
            while (
                traci.simulation.getMinExpectedNumber() > 0
                and traci.simulation.getTime() < scenario.duration_seconds
            ):
                current_time = float(traci.simulation.getTime())
                stop_requested, sequence = self._process_commands(
                    record,
                    scheduler,
                    current_time,
                    sequence,
                    wait_timeout=0.1 if record.paused else 0.0,
                )
                if stop_requested:
                    stop_requested = True
                    break
                if ai_observer is not None:
                    ai_observer.check_error()
                if record.paused:
                    continue
                loop_started = time.perf_counter()
                traci.simulationStep()
                elapsed = float(traci.simulation.getTime())
                scheduler.tick(elapsed)
                vehicle_tracker.tick(elapsed)
                if fixed_tracker is not None:
                    fixed_tracker.tick(traci, elapsed)
                departed = int(traci.simulation.getDepartedNumber())
                arrived = int(traci.simulation.getArrivedNumber())
                total_departed += departed
                total_arrived += arrived
                departed_since_decision += departed
                arrived_since_decision += arrived
                departed_since_ai_frame += departed
                arrived_since_ai_frame += arrived

                if config.control_mode == "algorithm":
                    for intersection_id, controller in controllers.items():
                        if controller.advance(elapsed):
                            _apply_controller_state(
                                traci, selected_manifest[intersection_id], controller
                            )
                    if elapsed + 1e-9 >= next_decision:
                        from .run import _observe

                        observation = _observe(
                            traci,
                            elapsed,
                            decision_step,
                            metadata,
                            controllers,
                            departed_since_decision,
                            arrived_since_decision,
                            vehicle_tracker=vehicle_tracker,
                            vehicle_action_controller=vehicle_action_controller,
                            previous_action_results=(
                                vehicle_action_controller.previous_results()
                            ),
                        )
                        decision = client.decide(observation)
                        signal_actions = _validate_actions(
                            decision.signal_actions, controllers
                        )
                        vehicle_actions = vehicle_action_controller.validate(
                            decision.vehicle_actions
                        )
                        for intersection_id, target in signal_actions.items():
                            if controllers[intersection_id].request_phase(target, elapsed):
                                _apply_controller_state(
                                    traci,
                                    selected_manifest[intersection_id],
                                    controllers[intersection_id],
                                )
                        vehicle_action_controller.apply(
                            decision_step,
                            vehicle_actions,
                            config.decision_interval,
                        )
                        departed_since_decision = 0
                        arrived_since_decision = 0
                        decision_step += 1
                        while next_decision <= elapsed + 1e-9:
                            next_decision += config.decision_interval

                ai_frame_id = (
                    ai_frame_clock.poll(elapsed)
                    if ai_observer is not None
                    else None
                )
                if ai_observer is not None and ai_frame_id is not None:
                    from .run import _observe_ai_frame

                    frame = _observe_ai_frame(
                        traci,
                        elapsed,
                        ai_frame_id,
                        metadata,
                        controllers,
                        fixed_tracker=fixed_tracker,
                        vehicle_tracker=vehicle_tracker,
                        vehicle_action_controller=vehicle_action_controller,
                        departed_vehicles=departed_since_ai_frame,
                        arrived_vehicles=arrived_since_ai_frame,
                        previous_action_results=(
                            vehicle_action_controller.previous_results()
                            if vehicle_action_controller is not None
                            else None
                        ),
                    )
                    ai_observer.publish(frame)
                    departed_since_ai_frame = 0
                    arrived_since_ai_frame = 0

                if elapsed + 1e-9 >= next_snapshot:
                    sequence += 1
                    last_snapshot = _capture_snapshot(
                        record,
                        traci,
                        selected_manifest,
                        controllers,
                        fixed_tracker,
                        scheduler,
                        elapsed,
                        total_departed,
                        total_arrived,
                        sequence,
                        vehicle_tracker,
                        vehicle_action_controller,
                    )
                    self._publish(record, last_snapshot)
                    while next_snapshot <= elapsed + 1e-9:
                        next_snapshot += config.snapshot_interval_seconds
                if record.playback_speed is not None:
                    spent = time.perf_counter() - loop_started
                    delay = _playback_delay_seconds(
                        config.step_length,
                        record.playback_speed,
                        spent,
                    )
                    if delay > 0:
                        time.sleep(delay)
            sequence += 1
            final_elapsed = float(traci.simulation.getTime())
            last_snapshot = _capture_snapshot(
                record,
                traci,
                selected_manifest,
                controllers,
                fixed_tracker,
                scheduler,
                final_elapsed,
                total_departed,
                total_arrived,
                sequence,
                vehicle_tracker,
                vehicle_action_controller,
            )
            self._publish(record, last_snapshot)
            finish_reason = "stopped" if stop_requested else "completed"

            if ai_observer is not None:
                from .run import _observe_ai_frame

                ai_observer.publish(
                    _observe_ai_frame(
                        traci,
                        final_elapsed,
                        ai_frame_clock.reserve(),
                        metadata,
                        controllers,
                        fixed_tracker=fixed_tracker,
                        vehicle_tracker=vehicle_tracker,
                        vehicle_action_controller=vehicle_action_controller,
                        departed_vehicles=departed_since_ai_frame,
                        arrived_vehicles=arrived_since_ai_frame,
                        previous_action_results=(
                            vehicle_action_controller.previous_results()
                            if vehicle_action_controller is not None
                            else None
                        ),
                    )
                )
        except BaseException as exc:
            finish_reason = "error"
            last_snapshot = replace(
                last_snapshot or self._empty_snapshot(record, "FAILED"),
                state="FAILED",
                error=str(exc),
            )
            self._publish(record, last_snapshot)
        finally:
            if scheduler is not None:
                scheduler.close()
                last_snapshot = replace(last_snapshot, events=scheduler.snapshots())
            finish_payload = {
                "protocol_version": PROTOCOL_VERSION,
                "episode_id": record.session_id,
                "reason": finish_reason,
                "simulation_time": last_snapshot.elapsed_seconds,
                "departed_vehicles": total_departed,
                "arrived_vehicles": total_arrived,
                "fuel_consumed_mg": last_snapshot.metrics.fuel_consumed_mg,
                "fuel_consumed_ml": last_snapshot.metrics.fuel_consumed_ml,
                "hard_braking_events": last_snapshot.metrics.hard_braking_events,
            }
            cleanup_error = None
            if ai_observer is not None:
                try:
                    ai_observer.close(
                        finish_payload,
                        config.ai_observer_shutdown_timeout,
                    )
                except BaseException as exc:
                    cleanup_error = exc
            if client is not None and client_initialized:
                try:
                    client.finish(finish_payload)
                except BaseException as exc:
                    if isinstance(client, LocalAlgorithmClient) and cleanup_error is None:
                        cleanup_error = exc
            if vehicle_action_controller is not None:
                try:
                    vehicle_action_controller.release()
                except Exception:
                    pass
            if traci is not None:
                try:
                    traci.close()
                except Exception:
                    pass
            if cleanup_error is not None:
                finish_reason = "error"
                last_snapshot = replace(
                    last_snapshot,
                    state="FAILED",
                    error=str(cleanup_error),
                )
                self._publish(record, last_snapshot)
            elif finish_reason != "error":
                terminal = "STOPPED" if stop_requested else "COMPLETED"
                self._publish(record, replace(last_snapshot, state=terminal))
            self._fail_pending_commands(record)
            with self._lock:
                if self._active_session_id == record.session_id:
                    self._active_session_id = None

    def _process_commands(
        self,
        record,
        scheduler,
        current_time: float,
        sequence: int,
        *,
        wait_timeout: float = 0.0,
    ) -> tuple[bool, int]:
        stop = False
        first = True
        while True:
            try:
                if first and wait_timeout > 0:
                    command = record.commands.get(timeout=wait_timeout)
                else:
                    command = record.commands.get_nowait()
            except queue.Empty:
                break
            first = False
            try:
                if command.name == "stop":
                    stop = True
                elif command.name == "pause":
                    if not record.paused:
                        record.paused = True
                        sequence += 1
                        self._publish(
                            record,
                            replace(
                                record.snapshot,
                                state="PAUSED",
                                sequence=sequence,
                            ),
                        )
                elif command.name == "resume":
                    if record.paused:
                        record.paused = False
                        sequence += 1
                        self._publish(
                            record,
                            replace(
                                record.snapshot,
                                state="RUNNING",
                                sequence=sequence,
                            ),
                        )
                elif command.name == "set_playback_speed":
                    speed = _normalize_playback_speed(command.payload)
                    if record.playback_speed != speed:
                        record.playback_speed = speed
                        sequence += 1
                        self._publish(
                            record,
                            replace(
                                record.snapshot,
                                playback_speed=speed,
                                sequence=sequence,
                            ),
                        )
                elif command.name == "add_event":
                    scheduler.schedule(
                        command.payload, current_time=current_time
                    )
                    sequence += 1
                    self._publish(
                        record,
                        replace(
                            record.snapshot,
                            events=scheduler.snapshots(),
                            sequence=sequence,
                        ),
                    )
                elif command.name == "cancel_event":
                    scheduler.cancel(str(command.payload))
                    sequence += 1
                    self._publish(
                        record,
                        replace(
                            record.snapshot,
                            events=scheduler.snapshots(),
                            sequence=sequence,
                        ),
                    )
                else:
                    raise SessionError(f"Unknown session command: {command.name}")
            except BaseException as exc:
                command.error = exc
            finally:
                command.completed.set()
        return stop, sequence

    def _fail_pending_commands(self, record) -> None:
        while True:
            try:
                command = record.commands.get_nowait()
            except queue.Empty:
                break
            command.error = SessionError("Session ended before the command was processed.")
            command.completed.set()


def _lane_targets(traci, selected_manifest) -> Mapping[str, LaneTarget]:
    result = {}
    for item in selected_manifest.values():
        for connection in item["connections"]:
            for edge_key, lane_key in (("from_edge", "from_lane"), ("to_edge", "to_lane")):
                edge_id = str(connection[edge_key])
                lane_index = int(connection[lane_key])
                lane_id = f"{edge_id}_{lane_index}"
                result[lane_id] = LaneTarget(
                    lane_id=lane_id,
                    edge_id=edge_id,
                    lane_index=lane_index,
                    length=float(traci.lane.getLength(lane_id)),
                )
    return result


def _format_clock(seconds: float) -> str:
    value = int(seconds) % 86400
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"


def _decode_fixed_signal(traci, item) -> tuple[int, str]:
    values = []
    for tls_id in item["tls_ids"]:
        phase_name = str(traci.trafficlight.getPhaseName(tls_id))
        match = re.fullmatch(r"p(\d+)_(green|yellow|clearance)", phase_name)
        if match is None:
            raise SessionError(f"Cannot decode generated phase name {phase_name!r}.")
        values.append((int(match.group(1)), match.group(2).upper()))
    if len(set(values)) != 1:
        raise SessionError(f"Physical TLS states are inconsistent: {values}")
    return values[0]


class _FixedSignalTracker:
    def __init__(self, selected_manifest) -> None:
        self.selected_manifest = selected_manifest
        self._states = {}

    def tick(self, traci, elapsed: float) -> None:
        for intersection_id, item in self.selected_manifest.items():
            phase, stage = _decode_fixed_signal(traci, item)
            previous = self._states.get(intersection_id)
            if previous is None or previous[:2] != (phase, stage):
                self._states[intersection_id] = (phase, stage, elapsed)

    def state(self, intersection_id: str, elapsed: float):
        phase, stage, started_at = self._states[intersection_id]
        return phase, None, stage, max(0.0, elapsed - started_at)


def _capture_snapshot(
    record,
    traci,
    selected_manifest,
    controllers,
    fixed_tracker,
    scheduler,
    elapsed,
    total_departed,
    total_arrived,
    sequence,
    vehicle_tracker,
    vehicle_action_controller,
) -> SimulationSnapshot:
    intersections = {}
    unique_lanes = set()
    for intersection_id, item in selected_manifest.items():
        lane_ids = sorted(
            {
                f"{connection[key]}_{connection[index_key]}"
                for connection in item["connections"]
                for key, index_key in (("from_edge", "from_lane"), ("to_edge", "to_lane"))
            }
        )
        unique_lanes.update(lane_ids)
        lanes = {}
        for lane_id in lane_ids:
            count = int(traci.lane.getLastStepVehicleNumber(lane_id))
            lanes[lane_id] = LaneRuntimeSnapshot(
                vehicle_count=count,
                halting_count=int(traci.lane.getLastStepHaltingNumber(lane_id)),
                mean_speed=float(traci.lane.getLastStepMeanSpeed(lane_id)) if count else 0.0,
                waiting_time=float(traci.lane.getWaitingTime(lane_id)),
                occupancy=float(traci.lane.getLastStepOccupancy(lane_id)),
            )
        if controllers:
            controller = controllers[intersection_id]
            signal = (
                controller.current_phase,
                controller.pending_phase,
                controller.stage.value,
                controller.stage_elapsed(elapsed),
            )
        else:
            signal = fixed_tracker.state(intersection_id, elapsed)
        intersections[intersection_id] = IntersectionRuntimeSnapshot(
            current_phase=signal[0],
            pending_phase=signal[1],
            stage=signal[2],
            stage_elapsed=signal[3],
            lanes=lanes,
        )
    telemetry = vehicle_tracker.observations(reset_interval=False)
    vehicle_values = []
    speeds = []
    for vehicle_id in traci.vehicle.getIDList():
        vehicle_id = str(vehicle_id)
        observation = telemetry.get(vehicle_id)
        if observation is not None:
            action = (
                vehicle_action_controller.current_action(vehicle_id)
                if vehicle_action_controller is not None
                else None
            )
            speed = observation.motion.speed_mps
            speeds.append(speed)
            vehicle_values.append(
                VehicleRuntimeSnapshot(
                    vehicle_id=vehicle_id,
                    x=observation.position.x_m,
                    y=observation.position.y_m,
                    speed=speed,
                    angle=observation.motion.angle_deg,
                    road_id=observation.location.road_id,
                    lane_id=observation.location.lane_id,
                    controllable=True,
                    type_id=observation.type_id,
                    acceleration=observation.motion.acceleration_mps2,
                    lane_index=observation.location.lane_index,
                    lane_position=observation.location.lane_position_m,
                    allowed_speed=observation.motion.allowed_speed_mps,
                    route_id=observation.location.route_id,
                    route_index=observation.location.route_index,
                    waiting_time=observation.traffic.accumulated_waiting_time_s,
                    time_loss=observation.traffic.time_loss_s,
                    distance=observation.traffic.distance_m,
                    fuel_rate_mg_s=observation.energy.fuel_rate_mg_s,
                    fuel_total_mg=observation.energy.fuel_total_mg,
                    fuel_total_ml=observation.energy.fuel_total_ml,
                    hard_braking_events=(
                        observation.driving_events.hard_braking_total
                    ),
                    next_intersection_id=(
                        observation.next_signal.intersection_id
                        if observation.next_signal
                        else None
                    ),
                    target_speed=(action.target_speed_mps if action else None),
                    target_lane_index=(
                        action.target_lane_index if action else None
                    ),
                )
            )
            continue
        x, y = traci.vehicle.getPosition(vehicle_id)
        speed = float(traci.vehicle.getSpeed(vehicle_id))
        speeds.append(speed)
        vehicle_values.append(
            VehicleRuntimeSnapshot(
                vehicle_id=str(vehicle_id),
                x=float(x),
                y=float(y),
                speed=speed,
                angle=float(traci.vehicle.getAngle(vehicle_id)),
                road_id=str(traci.vehicle.getRoadID(vehicle_id)),
                lane_id=str(traci.vehicle.getLaneID(vehicle_id)),
            )
        )
    halting = sum(int(traci.lane.getLastStepHaltingNumber(lane)) for lane in unique_lanes)
    waiting = sum(float(traci.lane.getWaitingTime(lane)) for lane in unique_lanes)
    scenario = record.scenario
    fuel_mg, fuel_ml, braking = vehicle_tracker.totals()
    return SimulationSnapshot(
        session_id=record.session_id,
        state="PAUSED" if record.paused else "RUNNING",
        sequence=sequence,
        elapsed_seconds=elapsed,
        duration_seconds=scenario.duration_seconds,
        progress=min(1.0, elapsed / scenario.duration_seconds),
        official_time=_format_clock(
            scenario.official_start_seconds + scenario.window_start_seconds + elapsed
        ),
        playback_speed=record.playback_speed,
        intersections=intersections,
        vehicles=tuple(vehicle_values),
        events=scheduler.snapshots(),
        metrics=SessionMetrics(
            active_vehicles=len(vehicle_values),
            departed_vehicles=total_departed,
            arrived_vehicles=total_arrived,
            remaining_vehicles=int(traci.simulation.getMinExpectedNumber()),
            halting_vehicles=halting,
            total_waiting_time=waiting,
            mean_speed=sum(speeds) / len(speeds) if speeds else 0.0,
            fuel_consumed_mg=fuel_mg,
            fuel_consumed_ml=fuel_ml,
            hard_braking_events=braking,
        ),
    )
