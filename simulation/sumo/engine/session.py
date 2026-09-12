"""Thread-safe, single-session API intended for the future backend service."""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4

from ..building.artifacts import GeneratedArtifactLayout
from ..building.build_traffic import (
    DEFAULT_TRAFFIC_SCOPE_ID,
    SUPPORTED_TRAFFIC_SCOPE_IDS,
)
from .events import (
    AccidentEvent,
    DisturbanceEvent,
    DisturbanceScheduler,
    EventSnapshot,
    EventValidationError,
    LaneClosureEvent,
    LaneTarget,
    MajorEventClosingEvent,
    MajorEventOpeningEvent,
    SpeedLimitEvent,
)
from .queue_estimate import DEFAULT_VEHICLE_SPACE_M, estimate_queue_length_m
from .evaluation_scope import build_evaluation_scope_payload
from .ai_control import AIControlConfig, AIControlStatus
from .ai_executor import AIPlanExecutor
from ..algorithm.local_policy import LocalAlgorithmClient
from ..algorithm.ai_observer import LocalAIObserver, SimulationTimeFrameClock
from ..algorithm.policy import PROTOCOL_VERSION
from .scenario import (
    DEFAULT_GENERATED_DIR,
    DEFAULT_SESSION_ROOT,
    CompiledScenario,
    ScenarioCompilationError,
    compile_session_scenario,
)


logger = logging.getLogger(__name__)


from simulation_protocol.dto import (
    IntersectionCapability,
    IntersectionRuntimeSnapshot,
    LaneCapability,
    LaneRuntimeSnapshot,
    OriginCapability,
    ScenarioScopeCapability,
    SessionMetrics,
    SimulationCatalog,
    SimulationConfig,
    SimulationSnapshot,
    VehicleRuntimeSnapshot,
)
from simulation_protocol.events import EventSnapshot
from simulation_protocol.exceptions import SessionBusyError, SessionError, UnknownSessionError
from simulation_protocol.playback import PLAYBACK_SPEEDS, normalize_playback_speed as _normalize_playback_speed
from simulation_protocol.catalog import load_catalog
from simulation_protocol.validation import validate_simulation_config as _validate_simulation_config
# CoV2X emits SEND/DELIVER/CONSUME per typed envelope each decision step.
# A 20-intersection snapshot with a few hundred vehicles can exceed 1k
# lifecycle events; keep a bounded session window without dropping the
# current step's batch.
V2X_EVENT_WINDOW_SIZE = 4_000


def _playback_delay_seconds(
    step_length: float,
    playback_speed: float | None,
    spent_seconds: float,
) -> float:
    if playback_speed is None:
        return 0.0
    return max(0.0, step_length / playback_speed - spent_seconds)


def _run_algorithm_decision(client, observation):
    started_at = time.perf_counter()
    decision = client.decide(observation)
    return decision, (time.perf_counter() - started_at) * 1000.0


def _executed_signal_state(controllers: Mapping[str, Any], elapsed: float) -> dict[str, dict[str, Any]]:
    executed: dict[str, dict[str, Any]] = {}
    for intersection_id, controller in controllers.items():
        snapshot = controller.snapshot()
        executed[str(intersection_id)] = {
            "current_phase": int(snapshot.current_phase),
            "pending_phase": (
                None if snapshot.pending_phase is None else int(snapshot.pending_phase)
            ),
            "stage": snapshot.stage.value if hasattr(snapshot.stage, "value") else str(snapshot.stage),
            "stage_elapsed": float(controller.stage_elapsed(elapsed)),
        }
    return executed


def _event_state_payload(scheduler) -> list[dict[str, Any]]:
    if scheduler is None:
        return []
    payload: list[dict[str, Any]] = []
    for item in scheduler.snapshots():
        payload.append(
            {
                "event_id": item.event_id,
                "event_type": item.event_type,
                "state": item.state,
                "start_seconds": item.start_seconds,
                "end_seconds": item.end_seconds,
                "error": item.error,
                "details": dict(item.details),
            }
        )
    return payload


def _notify_algorithm_decision_observer(
    observer: Callable[[Mapping[str, Any]], None] | None,
    *,
    simulation_time: float,
    step_id: Any,
    observation: Any,
    decision: Any,
    executed_signal_state: Mapping[str, Any],
    decision_latency_ms: float,
    event_state: Sequence[Mapping[str, Any]],
) -> None:
    """Default-off Protocol 2.0 decision hook. Does not mutate SUMO state."""

    if observer is None:
        return
    from ..algorithm.policy_transport import to_protocol_payload

    requested_signals = dict(getattr(decision, "signal_actions", {}) or {})
    requested_vehicles = dict(getattr(decision, "vehicle_actions", {}) or {})
    observer(
        {
            "simulation_time": float(simulation_time),
            "step_id": step_id,
            "observation": to_protocol_payload(observation),
            "actions": {
                "signals": requested_signals,
                "vehicles": requested_vehicles,
            },
            "requested_action": {
                "signals": requested_signals,
                "vehicles": requested_vehicles,
            },
            "executed_signal_state": dict(executed_signal_state),
            "decision_latency_ms": float(decision_latency_ms),
            "event_state": [dict(item) for item in event_state],
        }
    )



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
    ai_status: AIControlStatus = field(default_factory=AIControlStatus)
    v2x_events: deque[dict[str, object]] = field(
        default_factory=lambda: deque(maxlen=V2X_EVENT_WINDOW_SIZE)
    )
    evaluation_scope: Mapping[str, object] | None = None


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
                scenario_scope=config.scenario_scope,
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
            record.evaluation_scope = _build_record_evaluation_scope(
                config, catalog=self.catalog()
            )
            _persist_evaluation_scope(scenario.directory, record.evaluation_scope)
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
        if bool(getattr(event, "ai_control_enabled", False)):
            raise EventValidationError(
                "AI-controlled disturbance events must be configured at session start."
            )
        if not event.event_id:
            event = replace(event, event_id=str(uuid4()))
        self._command(session_id, "add_event", event)
        return event.event_id

    def cancel_event(self, session_id: str, event_id: str) -> None:
        self._command(session_id, "cancel_event", event_id)

    def install_ai_plan(self, session_id: str, payload: Mapping[str, object]) -> None:
        """Install a backend-validated AI plan in the SUMO worker."""

        if not isinstance(payload, Mapping):
            raise ValueError("AI plan payload must be an object.")
        self._command(session_id, "install_ai_plan", dict(payload))

    def fallback_ai_control(
        self, session_id: str, payload: Mapping[str, object]
    ) -> None:
        if not isinstance(payload, Mapping):
            raise ValueError("AI fallback payload must be an object.")
        self._command(session_id, "fallback_ai_control", dict(payload))

    def release_ai_control(self, session_id: str, reason: str = "released") -> None:
        self._command(session_id, "release_ai_control", str(reason))

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
        _validate_simulation_config(config, self.catalog())

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

    def _publish(
        self,
        record: _SessionRecord,
        snapshot: SimulationSnapshot,
    ) -> None:
        with self._lock:
            record.snapshot = snapshot

            for channel in tuple(record.subscribers):
                try:
                    channel.put_nowait(snapshot)
                    continue
                except queue.Full:
                    pass

                # 队列已满时只丢弃最旧的一帧。
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
            ai_takeover=AIControlStatus(
                baseline_controller=(
                    record.config.baseline_controller or record.config.control_mode
                ),
            ),
            evaluation_scope=record.evaluation_scope,
        )

    def _run_worker(self, record: _SessionRecord) -> None:
        from .run import (
            _apply_controller_state,
            _build_controllers,
            _build_metadata,
            _load_manifest,
            _select_programs,
            _select_program_manifests,
            _selected_manifest,
            _validate_actions,
        )
        from ..building.tls import load_signal_configuration
        from ..building.build_tls import DEFAULT_MAPPING, DEFAULT_PLANS, DEFAULT_TOPOLOGY
        from .vehicle import (
            StoppedLaneChangeGuard,
            VehicleActionController,
            VehicleTelemetryTracker,
            build_vehicle_type_metadata,
        )
        from .tripinfo import load_tripinfo_totals
        from .runtime import load_sumo_runtime

        config = record.config
        scenario = record.scenario
        runtime = None
        client = None
        client_initialized = False
        decision_executor: ThreadPoolExecutor | None = None
        pending_decision: Future | None = None
        pending_decision_step: int | None = None
        pending_decision_elapsed: float | None = None
        ai_observer = None
        scheduler = None
        controllers = {}
        ai_controllers = {}
        ai_executor = None
        fixed_tracker = None
        vehicle_tracker = None
        vehicle_action_controller = None
        last_snapshot = record.snapshot
        stop_requested = False
        finish_reason = "completed"
        total_departed = 0
        total_arrived = 0
        sequence = 0
        runtime_started = False
        try:
            runtime = load_sumo_runtime(gui=config.gui)
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
            record.evaluation_scope = _build_record_evaluation_scope(
                config,
                catalog=self.catalog(),
                selected_manifest=selected_manifest,
            )
            _persist_evaluation_scope(scenario.directory, record.evaluation_scope)
            traffic_manifest = json.loads(
                GeneratedArtifactLayout(self.generated_dir).traffic_manifest.read_text(
                    encoding="utf-8"
                )
            )
            endpoint_policy = traffic_manifest.get("route_endpoint_policy", {})
            if not isinstance(endpoint_policy, Mapping):
                endpoint_policy = {}
            upstream_extensions = endpoint_policy.get("upstream_extensions", {})
            if not isinstance(upstream_extensions, Mapping):
                upstream_extensions = {}
            downstream_extensions = endpoint_policy.get("downstream_extensions", {})
            if not isinstance(downstream_extensions, Mapping):
                downstream_extensions = {}
            command = runtime.command(
                [
                    "--configuration-file",
                    str(scenario.sumocfg),
                    "--step-length",
                    str(config.step_length),
                    "--seed",
                    str(config.seed),
                    "--no-step-log",
                    "true",
                    # 急刹Warning刷屏；急刹次数由VehicleTelemetryTracker统计，不在终端输出
                    "--no-warnings",
                    "true",
                    "--collision.action",
                    "warn",
                ]
            )
            runtime.start(command)
            runtime_started = True
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
                runtime, vehicle_types, tls_to_intersection
            )
            lane_change_guard = StoppedLaneChangeGuard(runtime, vehicle_tracker)
            lane_targets = _lane_targets(runtime, selected_manifest)
            scheduler = DisturbanceScheduler(
                runtime,
                lane_targets,
                scenario.duration_seconds,
                upstream_extensions=upstream_extensions,
                downstream_extensions=downstream_extensions,
            )
            for event in config.initial_events:
                scheduler.schedule(event)

            if config.control_mode == "fixed":
                for own_config in selected_configs:
                    program_id = programs[own_config.intersection_id].program_id
                    for tls_id in selected_manifest[own_config.intersection_id]["tls_ids"]:
                        runtime.trafficlight.setProgram(tls_id, program_id)
                fixed_tracker = _FixedSignalTracker(selected_manifest)
                fixed_tracker.tick(runtime, 0.0)
                ai_controllers = _build_controllers(
                    selected_configs,
                    selected_manifest,
                    programs,
                    minimum_green=config.minimum_green,
                )
            else:
                controllers = _build_controllers(
                    selected_configs,
                    selected_manifest,
                    programs,
                    minimum_green=config.minimum_green,
                )
                vehicle_action_controller = VehicleActionController(
                    runtime, vehicle_tracker
                )
                for intersection_id, controller in controllers.items():
                    _apply_controller_state(
                        runtime, selected_manifest[intersection_id], controller
                    )

            def restore_fixed_program(intersection_id: str) -> None:
                if config.control_mode != "fixed":
                    return
                program_id = programs[intersection_id].program_id
                for tls_id in selected_manifest[intersection_id]["tls_ids"]:
                    runtime.trafficlight.setProgram(tls_id, program_id)
                if fixed_tracker is not None:
                    fixed_tracker.tick(
                        runtime,
                        float(runtime.simulation.getTime()),
                        force=True,
                    )

            runtime_controllers = (
                controllers if config.control_mode == "algorithm" else ai_controllers
            )
            ai_executor = AIPlanExecutor(
                traci=runtime,
                selected_manifest=selected_manifest,
                controllers=runtime_controllers,
                baseline_mode=config.control_mode,
                fixed_state_provider=(
                    fixed_tracker.state if fixed_tracker is not None else None
                ),
                config=config.ai_control,
                baseline_restore=restore_fixed_program,
                baseline_controller=(config.baseline_controller or config.control_mode),
            )
            record.ai_status = ai_executor.status

            metadata = _build_metadata(
                runtime,
                selected_manifest,
                programs,
                config.period,
                config.seed,
                decision_interval=config.decision_interval,
                minimum_green=config.minimum_green,
                episode_id=record.session_id,
                vehicle_types=vehicle_types,
            )
            lane_lengths: dict[str, float] = {}
            for intersection in metadata.intersections.values():
                for lane_id, lane_meta in intersection.lanes.items():
                    lane_lengths[str(lane_id)] = float(lane_meta.length_m)
            for lane_id, target in lane_targets.items():
                lane_lengths.setdefault(str(lane_id), float(target.length))
            if config.control_mode == "algorithm":
                client = LocalAlgorithmClient(config.algorithm_module)
                client.initialize(metadata)
                client_initialized = True
                decision_executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix=f"algorithm-{record.session_id}",
                )
            if config.ai_observer_module:
                ai_observer = LocalAIObserver(config.ai_observer_module)
                ai_observer.initialize(metadata)

            self._publish(
                record,
                replace(
                    record.snapshot,
                    state="PAUSED" if record.paused else "RUNNING",
                    playback_speed=record.playback_speed,
                    ai_takeover=record.ai_status,
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
                runtime.simulation.getMinExpectedNumber() > 0
                and runtime.simulation.getTime() < scenario.duration_seconds
            ):
                current_time = float(runtime.simulation.getTime())
                stop_requested, sequence = self._process_commands(
                    record,
                    scheduler,
                    current_time,
                    sequence,
                    ai_executor=ai_executor,
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
                runtime.simulationStep()
                elapsed = float(runtime.simulation.getTime())
                scheduler.tick(elapsed)
                if ai_executor is not None:
                    ai_executor.observe_events(scheduler.snapshots(), elapsed)
                if fixed_tracker is not None:
                    fixed_tracker.tick(
                        runtime,
                        elapsed,
                        excluded_intersections=(
                            ai_executor.override_intersections
                            if ai_executor is not None
                            else ()
                        ),
                    )
                if ai_executor is not None:
                    ai_executor.advance(elapsed)
                    if ai_executor.plan_expired(elapsed):
                        expired_event_id = ai_executor.status.active_event_id
                        if expired_event_id:
                            ai_executor.mark_fallback(
                                event_id=expired_event_id,
                                reason="plan_expired",
                                current_time=elapsed,
                                rag_status=ai_executor.status.rag_status,
                            )
                            # A fixed-time baseline can often be restored at
                            # once when the controller is already green.  If
                            # it is in yellow/clearance, advance() keeps the
                            # safe override until the next legal green state.
                            ai_executor.advance(elapsed)
                    record.ai_status = ai_executor.status
                departed_ids = tuple(runtime.simulation.getDepartedIDList())
                arrived_ids = tuple(runtime.simulation.getArrivedIDList())
                vehicle_tracker.update_vehicle_set(
                    departed_ids,
                    arrived_ids,
                    elapsed,
                )
                vehicle_tracker.sync_subscription_results()
                departed = len(departed_ids)
                arrived = len(arrived_ids)
                total_departed += departed
                total_arrived += arrived
                departed_since_decision += departed
                arrived_since_decision += arrived
                departed_since_ai_frame += departed
                arrived_since_ai_frame += arrived

                decision_due = (
                    config.control_mode == "algorithm"
                    and pending_decision is None
                    and elapsed + 1e-9 >= next_decision
                )
                fixed_telemetry_due = (
                    config.control_mode == "fixed"
                    and elapsed + 1e-9 >= next_decision
                )
                ai_frame_id = (
                    ai_frame_clock.poll(elapsed)
                    if ai_observer is not None
                    else None
                )
                vehicle_observations = None
                if decision_due or fixed_telemetry_due or ai_frame_id is not None:
                    vehicle_tracker.refresh_observations(elapsed)
                if decision_due or ai_frame_id is not None:
                    vehicle_observations = vehicle_tracker.observations(
                        reset_interval=decision_due
                    )

                if config.control_mode == "algorithm":
                    for intersection_id, controller in controllers.items():
                        if (
                            ai_executor is None
                            or intersection_id not in ai_executor.override_intersections
                        ) and controller.advance(elapsed):
                            _apply_controller_state(
                                runtime, selected_manifest[intersection_id], controller
                            )
                    if pending_decision is not None and pending_decision.done():
                        completed_step = pending_decision_step
                        submitted_elapsed = pending_decision_elapsed
                        decision, decision_duration_ms = pending_decision.result()
                        record.v2x_events.extend(
                            dict(event) for event in decision.v2x_events
                        )
                        pending_decision = None
                        pending_decision_step = None
                        pending_decision_elapsed = None
                        if decision_duration_ms >= 200.0:
                            logger.warning(
                                "Algorithm decision delayed SUMO: "
                                "session=%s step=%s elapsed=%.1f duration_ms=%.1f",
                                record.session_id,
                                completed_step,
                                elapsed,
                                decision_duration_ms,
                            )
                        decision_age = max(
                            0.0,
                            elapsed - (
                                submitted_elapsed
                                if submitted_elapsed is not None
                                else elapsed
                            ),
                        )
                        if decision_age <= config.decision_interval + 1e-9:
                            signal_actions = _validate_actions(
                                decision.signal_actions, controllers
                            )
                            vehicle_actions = vehicle_action_controller.validate(
                                decision.vehicle_actions
                            )
                            for intersection_id, target in signal_actions.items():
                                if controllers[intersection_id].request_phase(
                                    target, elapsed
                                ):
                                    _apply_controller_state(
                                        runtime,
                                        selected_manifest[intersection_id],
                                        controllers[intersection_id],
                                    )
                            vehicle_action_controller.apply(
                                completed_step,
                                vehicle_actions,
                                config.decision_interval,
                            )
                        else:
                            logger.warning(
                                "Discard stale algorithm decision: "
                                "session=%s step=%s age_seconds=%.2f",
                                record.session_id,
                                completed_step,
                                decision_age,
                            )
                    if decision_due:
                        from .run import _observe

                        lane_change_guard.tick()
                        observation = _observe(
                            runtime,
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
                            vehicle_observations=vehicle_observations,
                        )
                        if config.algorithm_decision_observer is None:
                            decision = client.decide(observation)
                            decision_latency_ms = 0.0
                        else:
                            decision, decision_latency_ms = _run_algorithm_decision(
                                client, observation
                            )
                        record.v2x_events.extend(
                            dict(event) for event in decision.v2x_events
                        )
                        signal_actions = _validate_actions(
                            decision.signal_actions, controllers
                        )
                        vehicle_actions = vehicle_action_controller.validate(
                            decision.vehicle_actions
                        )
                        for intersection_id, target in signal_actions.items():
                            if (
                                ai_executor is not None
                                and intersection_id in ai_executor.override_intersections
                            ):
                                continue
                            if controllers[intersection_id].request_phase(target, elapsed):
                                _apply_controller_state(
                                    runtime,
                                    selected_manifest[intersection_id],
                                    controllers[intersection_id],
                                )
                        vehicle_action_controller.apply(
                            decision_step,
                            vehicle_actions,
                            config.decision_interval,
                        )
                        _notify_algorithm_decision_observer(
                            config.algorithm_decision_observer,
                            simulation_time=elapsed,
                            step_id=decision_step,
                            observation=observation,
                            decision=decision,
                            executed_signal_state=_executed_signal_state(
                                controllers, elapsed
                            ),
                            decision_latency_ms=decision_latency_ms,
                            event_state=_event_state_payload(scheduler),
                        )
                        pending_decision_step = decision_step
                        pending_decision_elapsed = elapsed
                        departed_since_decision = 0
                        arrived_since_decision = 0
                        decision_step += 1
                        while next_decision <= elapsed + 1e-9:
                            next_decision += config.decision_interval
                elif fixed_telemetry_due:
                    while next_decision <= elapsed + 1e-9:
                        next_decision += config.decision_interval

                if ai_executor is not None and (decision_due or fixed_telemetry_due):
                    ai_executor.apply_slot(elapsed)
                    record.ai_status = ai_executor.status

                if ai_observer is not None and ai_frame_id is not None:
                    from .run import _observe_ai_frame

                    frame = _observe_ai_frame(
                        runtime,
                        elapsed,
                        ai_frame_id,
                        metadata,
                        ai_executor.snapshot_controllers() if ai_executor is not None else runtime_controllers,
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
                        vehicle_observations=vehicle_observations,
                    )
                    ai_observer.publish(frame)
                    departed_since_ai_frame = 0
                    arrived_since_ai_frame = 0

                if elapsed + 1e-9 >= next_snapshot:
                    sequence += 1
                    snapshot_started = time.perf_counter()
                    last_snapshot = _capture_snapshot(
                        record,
                        runtime,
                        selected_manifest,
                        ai_executor.snapshot_controllers() if ai_executor is not None else runtime_controllers,
                        fixed_tracker,
                        scheduler,
                        elapsed,
                        total_departed,
                        total_arrived,
                        sequence,
                        vehicle_tracker,
                        vehicle_action_controller,
                        lane_lengths=lane_lengths,
                    )
                    snapshot_duration_ms = (
                        time.perf_counter() - snapshot_started
                    ) * 1000.0
                    if snapshot_duration_ms >= 200.0:
                        logger.warning(
                            "Snapshot capture delayed SUMO: "
                            "session=%s sequence=%s vehicles=%s duration_ms=%.1f",
                            record.session_id,
                            sequence,
                            len(last_snapshot.vehicles),
                            snapshot_duration_ms,
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
            final_elapsed = float(runtime.simulation.getTime())
            last_snapshot = _capture_snapshot(
                record,
                runtime,
                selected_manifest,
                ai_executor.snapshot_controllers() if ai_executor is not None else runtime_controllers,
                fixed_tracker,
                scheduler,
                final_elapsed,
                total_departed,
                total_arrived,
                sequence,
                vehicle_tracker,
                vehicle_action_controller,
                lane_lengths=lane_lengths,
            )
            self._publish(record, last_snapshot)
            finish_reason = "stopped" if stop_requested else "completed"

            if ai_observer is not None:
                from .run import _observe_ai_frame

                vehicle_tracker.refresh_observations(final_elapsed)
                ai_observer.publish(
                    _observe_ai_frame(
                        runtime,
                        final_elapsed,
                        ai_frame_clock.reserve(),
                        metadata,
                        ai_executor.snapshot_controllers() if ai_executor is not None else runtime_controllers,
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
            cleanup_error = None
            if vehicle_action_controller is not None:
                try:
                    vehicle_action_controller.release()
                except Exception:
                    pass
            if runtime is not None:
                try:
                    runtime.close()
                except Exception:
                    pass
            if runtime_started:
                try:
                    tripinfo = load_tripinfo_totals(
                        scenario.tripinfo_file,
                        vehicle_types,
                    )
                    total_departed = tripinfo.departed_vehicles
                    total_arrived = tripinfo.arrived_vehicles
                    last_snapshot = replace(
                        last_snapshot,
                        metrics=replace(
                            last_snapshot.metrics,
                            departed_vehicles=total_departed,
                            arrived_vehicles=total_arrived,
                            fuel_consumed_mg=tripinfo.fuel_consumed_mg,
                            fuel_consumed_ml=tripinfo.fuel_consumed_ml,
                        ),
                    )
                except BaseException as exc:
                    # Preserve the original runtime failure when tripinfo is also
                    # unavailable or incomplete after an exceptional shutdown.
                    if finish_reason != "error":
                        cleanup_error = exc
            finish_payload = {
                "protocol_version": PROTOCOL_VERSION,
                "episode_id": record.session_id,
                "reason": "error" if cleanup_error is not None else finish_reason,
                "simulation_time": last_snapshot.elapsed_seconds,
                "departed_vehicles": total_departed,
                "arrived_vehicles": total_arrived,
                "fuel_consumed_mg": last_snapshot.metrics.fuel_consumed_mg,
                "fuel_consumed_ml": last_snapshot.metrics.fuel_consumed_ml,
                "hard_braking_events": last_snapshot.metrics.hard_braking_events,
            }
            if ai_observer is not None:
                try:
                    ai_observer.close(
                        finish_payload,
                        config.ai_observer_shutdown_timeout,
                    )
                except BaseException as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            if pending_decision is not None:
                pending_decision.cancel()
            if decision_executor is not None:
                decision_executor.shutdown(wait=True, cancel_futures=True)
            if pending_decision is not None and not pending_decision.cancelled():
                try:
                    pending_decision.result()
                except BaseException as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            if client is not None and client_initialized:
                try:
                    client.finish(finish_payload)
                except BaseException as exc:
                    if isinstance(client, LocalAlgorithmClient) and cleanup_error is None:
                        cleanup_error = exc
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
        ai_executor: AIPlanExecutor | None = None,
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
                    if record.snapshot.state != "STOPPING":
                        sequence += 1
                        self._publish(
                            record,
                            replace(
                                record.snapshot,
                                state="STOPPING",
                                sequence=sequence,
                            ),
                        )
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
                elif command.name == "install_ai_plan":
                    if ai_executor is None:
                        raise SessionError("AI control executor is unavailable.")
                    ai_executor.install_from_payload(
                        command.payload,
                        current_time=current_time,
                        events=scheduler.snapshots(),
                    )
                    record.ai_status = ai_executor.status
                    ai_executor.apply_slot(current_time)
                    sequence += 1
                    self._publish(
                        record,
                        replace(
                            record.snapshot,
                            ai_takeover=record.ai_status,
                            sequence=sequence,
                        ),
                    )
                elif command.name == "fallback_ai_control":
                    if ai_executor is None:
                        raise SessionError("AI control executor is unavailable.")
                    if not isinstance(command.payload, Mapping):
                        raise SessionError(
                            "fallback_ai_control payload must be an object."
                        )
                    ai_executor.mark_fallback(
                        event_id=str(command.payload.get("event_id", "")),
                        reason=str(command.payload.get("reason", "unknown")),
                        current_time=current_time,
                        rag_status=(
                            None
                            if command.payload.get("rag_status") is None
                            else str(command.payload.get("rag_status"))
                        ),
                    )
                    record.ai_status = ai_executor.status
                    sequence += 1
                    self._publish(
                        record,
                        replace(
                            record.snapshot,
                            ai_takeover=record.ai_status,
                            sequence=sequence,
                        ),
                    )
                elif command.name == "release_ai_control":
                    if ai_executor is None:
                        raise SessionError("AI control executor is unavailable.")
                    ai_executor.release(
                        reason=str(command.payload or "released"),
                        current_time=current_time,
                    )
                    record.ai_status = ai_executor.status
                    sequence += 1
                    self._publish(
                        record,
                        replace(
                            record.snapshot,
                            ai_takeover=record.ai_status,
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
    result: dict[str, LaneTarget] = {}
    roles: dict[str, set[str]] = {}
    for item in selected_manifest.values():
        successors = {}
        for connection in item["connections"]:
            from_lane = f"{connection['from_edge']}_{connection['from_lane']}"
            successors.setdefault(from_lane, set()).add(str(connection["to_edge"]))
        for connection in item["connections"]:
            for role, edge_key, lane_key in (
                ("incoming", "from_edge", "from_lane"),
                ("outgoing", "to_edge", "to_lane"),
            ):
                edge_id = str(connection[edge_key])
                lane_index = int(connection[lane_key])
                lane_id = f"{edge_id}_{lane_index}"
                roles.setdefault(lane_id, set()).add(role)
                result[lane_id] = LaneTarget(
                    lane_id=lane_id,
                    edge_id=edge_id,
                    lane_index=lane_index,
                    length=float(traci.lane.getLength(lane_id)),
                    successor_edge_ids=tuple(sorted(successors.get(lane_id, ()))),
                )
    for lane_id, target in tuple(result.items()):
        lane_roles = roles[lane_id]
        role = next(iter(lane_roles)) if len(lane_roles) == 1 else "both"
        result[lane_id] = LaneTarget(
            lane_id=target.lane_id,
            edge_id=target.edge_id,
            lane_index=target.lane_index,
            length=target.length,
            role=role,
        )
    return result


def _config_event_lanes(event: DisturbanceEvent) -> set[str]:
    if isinstance(event, AccidentEvent):
        return {event.lane_id}
    if isinstance(event, MajorEventOpeningEvent):
        return {event.venue_lane_id, *event.source_lane_ids}
    if isinstance(event, MajorEventClosingEvent):
        return {event.venue_lane_id, *event.destination_lane_ids}
    return set(event.lane_ids)


def _format_clock(seconds: float) -> str:
    value = int(seconds) % 86400
    return f"{value // 3600:02d}:{value % 3600 // 60:02d}:{value % 60:02d}"


def _build_record_evaluation_scope(
    config: SimulationConfig,
    *,
    catalog: SimulationCatalog | None = None,
    selected_manifest: Mapping[str, object] | None = None,
) -> dict[str, object]:
    catalog_intersections = catalog.intersections if catalog is not None else None
    return build_evaluation_scope_payload(
        preset_id=config.scenario_preset_id,
        intersection_ids=config.intersection_ids,
        selected_manifest=selected_manifest,
        catalog_intersections=catalog_intersections,
    )


def _persist_evaluation_scope(
    session_dir: Path,
    evaluation_scope: Mapping[str, object] | None,
) -> None:
    if not evaluation_scope:
        return
    manifest_path = session_dir / "session_manifest.json"
    payload: dict[str, object] = {}
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            payload = loaded
    payload["scenario_preset_id"] = str(evaluation_scope.get("preset_id") or "")
    payload["evaluation_scope"] = dict(evaluation_scope)
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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

    def tick(
        self,
        traci,
        elapsed: float,
        *,
        excluded_intersections: Sequence[str] = (),
        force: bool = False,
    ) -> None:
        excluded = {str(item) for item in excluded_intersections}
        for intersection_id, item in self.selected_manifest.items():
            if intersection_id in excluded:
                continue
            phase, stage = _decode_fixed_signal(traci, item)
            previous = self._states.get(intersection_id)
            if force or previous is None or previous[:2] != (phase, stage):
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
    lane_lengths: Mapping[str, float] | None = None,
) -> SimulationSnapshot:
    intersections = {}
    unique_lanes = set()
    lane_telemetry_cache = {}
    length_by_lane = dict(lane_lengths or {})
    samples_by_lane: dict[str, tuple[tuple[float, float, float, float], ...]] = {}
    default_vehicle_space = DEFAULT_VEHICLE_SPACE_M
    if vehicle_tracker is not None:
        samples_by_lane = vehicle_tracker.lane_vehicle_samples_by_lane()
        default_vehicle_space = float(vehicle_tracker.default_vehicle_space())
    for intersection_id, item in selected_manifest.items():
        connections_by_lane: dict[str, list[dict]] = {}
        outgoing_lanes = set()
        for connection in item["connections"]:
            from_lane = f"{connection['from_edge']}_{connection['from_lane']}"
            to_lane = f"{connection['to_edge']}_{connection['to_lane']}"
            connections_by_lane.setdefault(from_lane, []).append(connection)
            outgoing_lanes.add(to_lane)
        tls_states = {}
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
            lane_telemetry = lane_telemetry_cache.get(lane_id)
            if lane_telemetry is None:
                count = int(traci.lane.getLastStepVehicleNumber(lane_id))
                lane_telemetry = {
                    "vehicle_count": count,
                    "halting_count": int(
                        traci.lane.getLastStepHaltingNumber(lane_id)
                    ),
                    "mean_speed": (
                        float(traci.lane.getLastStepMeanSpeed(lane_id))
                        if count
                        else 0.0
                    ),
                    "waiting_time": float(traci.lane.getWaitingTime(lane_id)),
                    "occupancy": float(
                        traci.lane.getLastStepOccupancy(lane_id)
                    ),
                    "allowed_speed": float(traci.lane.getMaxSpeed(lane_id)),
                }
                lane_telemetry_cache[lane_id] = lane_telemetry
            count = lane_telemetry["vehicle_count"]
            lane_connections = connections_by_lane.get(lane_id, [])
            signal_states = []
            for connection in lane_connections:
                tls_id = str(connection["tls_id"])
                tls_state = tls_states.setdefault(
                    tls_id,
                    str(traci.trafficlight.getRedYellowGreenState(tls_id)),
                )
                link_index = int(connection["link_index"])
                if 0 <= link_index < len(tls_state):
                    signal_states.append(tls_state[link_index])
            edge_id, _, lane_index = lane_id.rpartition("_")
            lane_length_m = length_by_lane.get(lane_id)
            if lane_length_m is not None:
                queue_length_m = estimate_queue_length_m(
                    lane_length_m=float(lane_length_m),
                    halting_count=int(lane_telemetry["halting_count"]),
                    occupancy=float(lane_telemetry["occupancy"]),
                    samples=samples_by_lane.get(lane_id, ()),
                    default_vehicle_space=default_vehicle_space,
                )
            elif int(lane_telemetry["halting_count"]) <= 0:
                queue_length_m = 0.0
            else:
                queue_length_m = None
            lanes[lane_id] = LaneRuntimeSnapshot(
                vehicle_count=count,
                halting_count=lane_telemetry["halting_count"],
                mean_speed=lane_telemetry["mean_speed"],
                waiting_time=lane_telemetry["waiting_time"],
                occupancy=lane_telemetry["occupancy"],
                edge_id=edge_id,
                lane_index=int(lane_index),
                role="incoming" if lane_connections else "outgoing" if lane_id in outgoing_lanes else "",
                approach_id=(str(lane_connections[0]["approach"]) if lane_connections else None),
                downstream_lane_ids=tuple(
                    sorted(
                        f"{connection['to_edge']}_{connection['to_lane']}"
                        for connection in lane_connections
                    )
                ),
                lane_has_green=(
                    any(state in {"G", "g"} for state in signal_states)
                    if signal_states else None
                ),
                signal_state=(
                    signal_states[0]
                    if len(set(signal_states)) == 1 and signal_states else
                    "mixed" if signal_states else None
                ),
                current_allowed_speed_mps=lane_telemetry["allowed_speed"],
                queue_length_m=queue_length_m,
                queue_length_is_estimate=True,
                lane_length_m=(
                    None if lane_length_m is None else float(lane_length_m)
                ),
            )
        if intersection_id in controllers:
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
    vehicle_values = []
    speeds = []
    for vehicle_id in traci.vehicle.getIDList():
        vehicle_id = str(vehicle_id)
        telemetry = vehicle_tracker.runtime_fields(vehicle_id)
        if telemetry is not None:
            x, y = telemetry["position"]
            speed = float(telemetry["speed"])
            angle = float(telemetry["angle"])
            road_id = str(telemetry["road_id"])
            lane_id = str(telemetry["lane_id"])
            speeds.append(speed)
            action = (
                vehicle_action_controller.current_action(vehicle_id)
                if vehicle_action_controller is not None
                else None
            )
            vehicle_values.append(
                VehicleRuntimeSnapshot(
                    vehicle_id=vehicle_id,
                    x=float(x),
                    y=float(y),
                    speed=speed,
                    angle=angle,
                    road_id=road_id,
                    lane_id=lane_id,
                    controllable=True,
                    type_id=str(telemetry["type_id"]),
                    acceleration=float(telemetry["acceleration"]),
                    lane_index=int(telemetry["lane_index"]),
                    lane_position=float(telemetry["lane_position"]),
                    allowed_speed=float(telemetry["allowed_speed"]),
                    route_id=str(telemetry["route_id"]),
                    route_index=int(telemetry["route_index"]),
                    waiting_time=float(telemetry["waiting_time"]),
                    time_loss=float(telemetry["time_loss"]),
                    distance=float(telemetry["distance"]),
                    fuel_rate_mg_s=float(telemetry["fuel_rate_mg_s"]),
                    fuel_total_mg=float(telemetry["fuel_total_mg"]),
                    fuel_total_ml=float(telemetry["fuel_total_ml"]),
                    hard_braking_events=int(telemetry["hard_braking_events"]),
                    next_intersection_id=telemetry["next_intersection_id"],
                    target_speed=(action.target_speed_mps if action else None),
                    target_lane_index=(
                        action.target_lane_index if action else None
                    ),
                )
            )
            continue
        x, y = traci.vehicle.getPosition(vehicle_id)
        speed = float(traci.vehicle.getSpeed(vehicle_id))
        angle = float(traci.vehicle.getAngle(vehicle_id))
        road_id = str(traci.vehicle.getRoadID(vehicle_id))
        lane_id = str(traci.vehicle.getLaneID(vehicle_id))
        speeds.append(speed)
        vehicle_values.append(
            VehicleRuntimeSnapshot(
                vehicle_id=str(vehicle_id),
                x=float(x),
                y=float(y),
                speed=speed,
                angle=angle,
                road_id=road_id,
                lane_id=lane_id,
                controllable=vehicle_tracker.contains(vehicle_id),
                type_id=(
                    vehicle_tracker.type_metadata(vehicle_id).type_id
                    if vehicle_tracker.contains(vehicle_id)
                    else ""
                ),
            )
        )
    halting = sum(
        int(lane_telemetry_cache[lane]["halting_count"])
        for lane in unique_lanes
    )
    waiting = sum(
        float(lane_telemetry_cache[lane]["waiting_time"])
        for lane in unique_lanes
    )
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
        v2x_events=tuple(record.v2x_events),
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
        ai_takeover=record.ai_status,
        evaluation_scope=record.evaluation_scope,
    )
