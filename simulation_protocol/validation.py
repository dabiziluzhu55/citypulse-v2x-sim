"""Config validation without SUMO runtime dependencies."""

from __future__ import annotations

from .dto import SimulationCatalog, SimulationConfig
from .events import (
    AccidentEvent,
    DisturbanceEvent,
    EventValidationError,
    MajorEventClosingEvent,
    MajorEventOpeningEvent,
)
from .playback import normalize_playback_speed

DEFAULT_TRAFFIC_SCOPE_ID = "global"
SUPPORTED_TRAFFIC_SCOPE_IDS = (
    DEFAULT_TRAFFIC_SCOPE_ID,
    "east_dense",
    "west_dense",
)


class ScenarioCompilationError(ValueError):
    """Raised before a requested session is buildable."""


def _config_event_lanes(event: DisturbanceEvent) -> set[str]:
    if isinstance(event, AccidentEvent):
        return {event.lane_id}
    if isinstance(event, MajorEventOpeningEvent):
        return {event.venue_lane_id, *event.source_lane_ids}
    if isinstance(event, MajorEventClosingEvent):
        return {event.venue_lane_id, *event.destination_lane_ids}
    return set(event.lane_ids)


def validate_simulation_config(
    config: SimulationConfig,
    catalog: SimulationCatalog,
) -> None:
    unknown = set(config.intersection_ids) - set(catalog.intersections)
    if unknown:
        raise ScenarioCompilationError(f"Unknown intersections: {sorted(unknown)}")
    if config.control_mode not in {"fixed", "algorithm"}:
        raise ScenarioCompilationError("control_mode must be fixed or algorithm.")
    ai_events = tuple(
        event
        for event in config.initial_events
        if bool(getattr(event, "ai_control_enabled", False))
    )
    if len(ai_events) > 1:
        raise ScenarioCompilationError(
            "当前Traffic-Qwen仅支持单一主要扰动事件接管，请只选择一个AI管控目标。"
        )
    if ai_events and abs(config.decision_interval - config.ai_control.slot_seconds) > 1e-6:
        raise ScenarioCompilationError(
            "AI control slot_seconds must equal the simulation decision_interval."
        )
    if ai_events and config.duration_seconds is not None and (
        config.duration_seconds < config.ai_control.plan_valid_seconds
    ):
        raise ScenarioCompilationError(
            "AI-controlled simulations must last at least one AI plan window."
        )
    ordered_ai_events = sorted(ai_events, key=lambda event: event.start_seconds)
    if any(
        current.start_seconds < previous.end_seconds
        for previous, current in zip(ordered_ai_events, ordered_ai_events[1:])
    ):
        raise ScenarioCompilationError(
            "AI-controlled disturbance windows cannot overlap."
        )
    if config.scenario_scope not in SUPPORTED_TRAFFIC_SCOPE_IDS:
        raise ScenarioCompilationError(
            f"scenario_scope must be one of {SUPPORTED_TRAFFIC_SCOPE_IDS}."
        )
    scope = catalog.scenario_scopes.get(config.scenario_scope)
    if scope is None:
        raise ScenarioCompilationError(
            f"Traffic scenario scope {config.scenario_scope!r} is unavailable."
        )
    unavailable = set(config.intersection_ids) - set(scope.intersection_ids)
    if unavailable:
        raise ScenarioCompilationError(
            f"Traffic scenario scope {config.scenario_scope!r} does not include "
            f"intersections: {sorted(unavailable)}"
        )
    if config.period not in scope.periods:
        raise ScenarioCompilationError(
            f"Traffic scenario scope {config.scenario_scope!r} has no period "
            f"{config.period!r}."
        )
    if config.algorithm_transport not in {"local"}:
        raise ScenarioCompilationError("algorithm_transport must be local.")
    if config.control_mode == "algorithm" and not config.algorithm_module:
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
            normalize_playback_speed(config.playback_speed)
        except ValueError as exc:
            raise ScenarioCompilationError(str(exc)) from exc
    lane_ids = {
        lane.lane_id
        for intersection_id in config.intersection_ids
        for lane in catalog.intersections[intersection_id].lanes
    }
    event_ids: set[str] = set()
    duration = config.duration_seconds
    for event in config.initial_events:
        if not event.event_id or event.event_id in event_ids:
            raise EventValidationError("Initial event IDs must be non-empty and unique.")
        event_ids.add(event.event_id)
        event_lanes = _config_event_lanes(event)
        if event_lanes - lane_ids:
            raise EventValidationError(
                f"Initial event {event.event_id} targets unknown lanes."
            )
        if isinstance(event, (MajorEventOpeningEvent, MajorEventClosingEvent)) and (
            event.vehicle_count <= 0
        ):
            raise EventValidationError(
                f"Initial event {event.event_id} vehicle_count must be positive."
            )
        if duration is not None and event.end_seconds > duration + 1e-9:
            raise EventValidationError(
                f"Initial event {event.event_id} exceeds duration."
            )
