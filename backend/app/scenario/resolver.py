"""将scenario_preset_id与disturbance_targets解析为仿真启动参数"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from simulation.sumo.session import SimulationCatalog

from ..core.exceptions import AppError
from ..schemas.disturbance_targets import (
    DisturbanceTarget,
    DisturbanceTargetAccident,
    DisturbanceTargetLaneClosure,
    DisturbanceTargetSpeedLimit,
)
from ..schemas.events import (
    AccidentRequest,
    EventRequest,
    LaneClosureRequest,
    SpeedLimitRequest,
)
from ..schemas.simulations import StartSimulationRequest
from .presets import ScenarioPreset, require_scenario_preset


@dataclass(frozen=True)
class ResolvedStartSimulation:
    scenario_preset_id: str
    intersection_ids: tuple[str, ...]
    period: str
    origins: dict[str, tuple[str, ...]]
    window_start_seconds: float
    duration_seconds: float
    control_mode: str
    seed: int
    step_length: float
    realtime: bool
    gui: bool
    snapshot_interval_seconds: float
    playback_speed: float | None
    initial_events: tuple[EventRequest, ...]


def resolve_start_simulation(
    request: StartSimulationRequest,
    catalog: SimulationCatalog,
) -> ResolvedStartSimulation:
    preset = require_scenario_preset(request.scenario_preset_id)
    intersection_ids = _resolve_preset_intersections(preset, catalog)
    _validate_period(request.period, intersection_ids, catalog)
    _validate_origins(request.origins, intersection_ids, catalog)
    initial_events = tuple(
        resolve_disturbance_targets(
            request.disturbance_targets,
            preset,
            catalog,
        )
    )
    return ResolvedStartSimulation(
        scenario_preset_id=preset.preset_id,
        intersection_ids=intersection_ids,
        period=request.period,
        origins={
            intersection_id: tuple(origin_ids)
            for intersection_id, origin_ids in request.origins.items()
        },
        window_start_seconds=request.window_start_seconds,
        duration_seconds=request.duration_seconds,
        control_mode=request.control_mode,
        seed=request.seed,
        step_length=request.step_length,
        realtime=request.realtime,
        gui=request.gui,
        snapshot_interval_seconds=request.snapshot_interval_seconds,
        playback_speed=request.playback_speed,
        initial_events=initial_events,
    )


def resolve_disturbance_targets(
    targets: list[DisturbanceTarget],
    preset: ScenarioPreset,
    catalog: SimulationCatalog,
) -> list[EventRequest]:
    preset_intersections = set(preset.intersection_ids)
    seen_event_ids: set[str] = set()
    events: list[EventRequest] = []

    for target in targets:
        if target.intersection_id not in preset_intersections:
            raise AppError(
                code="INVALID_DISTURBANCE_INTERSECTION",
                message=(
                    f"Intersection {target.intersection_id!r} is outside preset "
                    f"{preset.preset_id!r}."
                ),
                status_code=422,
            )
        if target.intersection_id not in catalog.intersections:
            raise AppError(
                code="UNKNOWN_INTERSECTION",
                message=f"Unknown intersection: {target.intersection_id!r}.",
                status_code=422,
            )

        event_id = target.event_id or _generated_event_id(target)
        if event_id in seen_event_ids:
            raise AppError(
                code="DUPLICATE_EVENT_ID",
                message=f"Duplicate disturbance event_id: {event_id!r}.",
                status_code=422,
            )
        seen_event_ids.add(event_id)
        events.append(_resolve_disturbance_target(target, event_id, catalog))

    return events


def _resolve_preset_intersections(
    preset: ScenarioPreset,
    catalog: SimulationCatalog,
) -> tuple[str, ...]:
    missing = [
        intersection_id
        for intersection_id in preset.intersection_ids
        if intersection_id not in catalog.intersections
    ]
    if missing:
        raise AppError(
            code="PRESET_INTERSECTION_UNAVAILABLE",
            message=(
                f"Preset {preset.preset_id!r} references intersections missing from "
                f"catalog: {missing}."
            ),
            status_code=422,
        )
    return preset.intersection_ids


def _validate_period(
    period: str,
    intersection_ids: tuple[str, ...],
    catalog: SimulationCatalog,
) -> None:
    for intersection_id in intersection_ids:
        intersection = catalog.intersections[intersection_id]
        if period not in intersection.periods:
            raise AppError(
                code="INVALID_PERIOD",
                message=(
                    f"Period {period!r} is not supported for intersection "
                    f"{intersection_id!r}."
                ),
                status_code=422,
            )


def _validate_origins(
    origins: dict[str, list[str]],
    intersection_ids: tuple[str, ...],
    catalog: SimulationCatalog,
) -> None:
    for intersection_id, origin_ids in origins.items():
        if intersection_id not in catalog.intersections:
            raise AppError(
                code="INVALID_ORIGIN",
                message=f"Unknown intersection in origins: {intersection_id}",
                status_code=422,
            )
        if intersection_id not in intersection_ids:
            raise AppError(
                code="INVALID_ORIGIN",
                message=(
                    f"Origin intersection {intersection_id!r} is outside the selected "
                    f"scenario preset."
                ),
                status_code=422,
            )
        valid_origins = {
            origin.origin_id
            for origin in catalog.intersections[intersection_id].origins
        }
        unknown = set(origin_ids) - valid_origins
        if unknown:
            raise AppError(
                code="INVALID_ORIGIN",
                message=(
                    f"Unknown origin IDs for {intersection_id}: {sorted(unknown)}"
                ),
                status_code=422,
            )


def _resolve_disturbance_target(
    target: DisturbanceTarget,
    event_id: str,
    catalog: SimulationCatalog,
) -> EventRequest:
    if isinstance(target, DisturbanceTargetLaneClosure):
        lane_ids = _resolve_lane_ids(
            target.intersection_id,
            target.lane_ids,
            catalog,
        )
        return LaneClosureRequest(
            event_type="lane_closure",
            event_id=event_id,
            start_seconds=target.start_seconds,
            end_seconds=target.end_seconds,
            lane_ids=lane_ids,
        )
    if isinstance(target, DisturbanceTargetSpeedLimit):
        lane_ids = _resolve_lane_ids(
            target.intersection_id,
            target.lane_ids,
            catalog,
        )
        return SpeedLimitRequest(
            event_type="speed_limit",
            event_id=event_id,
            start_seconds=target.start_seconds,
            end_seconds=target.end_seconds,
            lane_ids=lane_ids,
            max_speed=target.max_speed,
        )
    if isinstance(target, DisturbanceTargetAccident):
        lane_id = _resolve_single_lane_id(
            target.intersection_id,
            target.lane_id,
            catalog,
        )
        return AccidentRequest(
            event_type="accident",
            event_id=event_id,
            start_seconds=target.start_seconds,
            end_seconds=target.end_seconds,
            lane_id=lane_id,
            position_ratio=target.position_ratio,
        )
    raise AppError(
        code="INVALID_EVENT",
        message="Unsupported disturbance target type.",
        status_code=422,
    )


def _resolve_lane_ids(
    intersection_id: str,
    requested_lane_ids: list[str] | None,
    catalog: SimulationCatalog,
) -> list[str]:
    valid_lanes = _intersection_lane_ids(intersection_id, catalog)
    if requested_lane_ids:
        unknown = set(requested_lane_ids) - valid_lanes
        if unknown:
            raise AppError(
                code="INVALID_LANE",
                message=(
                    f"Lane IDs {sorted(unknown)} do not belong to intersection "
                    f"{intersection_id!r}."
                ),
                status_code=422,
            )
        return requested_lane_ids

    default_lane = _default_incoming_lane_id(intersection_id, catalog)
    return [default_lane]


def _resolve_single_lane_id(
    intersection_id: str,
    requested_lane_id: str | None,
    catalog: SimulationCatalog,
) -> str:
    valid_lanes = _intersection_lane_ids(intersection_id, catalog)
    if requested_lane_id is not None:
        if requested_lane_id not in valid_lanes:
            raise AppError(
                code="INVALID_LANE",
                message=(
                    f"Lane ID {requested_lane_id!r} does not belong to intersection "
                    f"{intersection_id!r}."
                ),
                status_code=422,
            )
        return requested_lane_id
    return _default_incoming_lane_id(intersection_id, catalog)


def _intersection_lane_ids(intersection_id: str, catalog: SimulationCatalog) -> set[str]:
    return {
        lane.lane_id
        for lane in catalog.intersections[intersection_id].lanes
    }


def _default_incoming_lane_id(intersection_id: str, catalog: SimulationCatalog) -> str:
    incoming_lanes = [
        lane.lane_id
        for lane in catalog.intersections[intersection_id].lanes
        if lane.role == "incoming"
    ]
    if not incoming_lanes:
        raise AppError(
            code="INVALID_LANE",
            message=(
                f"Intersection {intersection_id!r} has no incoming lanes available for "
                "disturbance injection."
            ),
            status_code=422,
        )
    return incoming_lanes[0]


def _generated_event_id(target: DisturbanceTarget) -> str:
    suffix = uuid4().hex[:8]
    return f"evt_{target.event_type}_{target.intersection_id}_{suffix}"
