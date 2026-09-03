"""将scenario_preset_id与disturbance_targets解析为仿真启动参数"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from simulation.sumo.engine.session import SimulationCatalog

from ..core.exceptions import AppError
from ..schemas.disturbance_targets import (
    DisturbanceTarget,
    DisturbanceTargetAccident,
    DisturbanceTargetLaneClosure,
    DisturbanceTargetMajorEventClosing,
    DisturbanceTargetMajorEventOpening,
    DisturbanceTargetSpeedLimit,
)
from ..schemas.events import (
    AccidentRequest,
    EventRequest,
    LaneClosureRequest,
    MajorEventClosingRequest,
    MajorEventOpeningRequest,
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
    model_alias: str | None = None


def resolve_start_simulation(
    request: StartSimulationRequest,
    catalog: SimulationCatalog,
) -> ResolvedStartSimulation:
    preset = require_scenario_preset(request.scenario_preset_id)
    intersection_ids = _resolve_preset_intersections(preset, catalog)
    model_alias: str | None = None
    if request.control_mode == "ippo":
        from traffic_control.ippo.aliases import (
            default_model_alias_for,
            validate_alias_combo,
        )

        effective = request.model_alias or default_model_alias_for(preset.preset_id)
        model_alias, _model_path = validate_alias_combo(
            intersection_ids, effective
        )
    elif request.control_mode == "mappo":
        from traffic_control.mappo.aliases import (
            default_model_alias_for,
            validate_alias_combo,
        )

        effective = request.model_alias or default_model_alias_for(preset.preset_id)
        model_alias, _model_path = validate_alias_combo(
            intersection_ids, effective
        )
    elif request.control_mode == "cov2x":
        from traffic_control.cov2x.aliases import (
            default_model_alias_for,
            validate_alias_combo,
        )

        effective = request.model_alias or default_model_alias_for(preset.preset_id)
        model_alias, _model_path = validate_alias_combo(
            intersection_ids, effective
        )
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
        model_alias=model_alias,
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
        events.append(
            _resolve_disturbance_target(
                target,
                event_id,
                catalog,
                preset_intersection_ids=preset_intersections,
            )
        )

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
    *,
    preset_intersection_ids: set[str],
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
            ai_control_enabled=target.ai_control_enabled,
        )
    if isinstance(target, DisturbanceTargetSpeedLimit):
        lane_ids = _resolve_lane_ids(
            target.intersection_id,
            target.lane_ids,
            catalog,
        )
        if target.max_speed is None:
            raise AppError(
                code="INVALID_SPEED_LIMIT",
                message="speed_limit requires max_speed or speed_kmh.",
                status_code=422,
            )
        max_speed = float(target.max_speed)
        validate_speed_limit_against_catalog(
            max_speed,
            lane_ids,
            catalog,
            intersection_ids={target.intersection_id},
        )
        return SpeedLimitRequest(
            event_type="speed_limit",
            event_id=event_id,
            start_seconds=target.start_seconds,
            end_seconds=target.end_seconds,
            lane_ids=lane_ids,
            max_speed=max_speed,
            ai_control_enabled=target.ai_control_enabled,
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
            ai_control_enabled=target.ai_control_enabled,
        )
    if isinstance(target, DisturbanceTargetMajorEventOpening):
        venue_lane_id = _resolve_single_lane_id(
            target.intersection_id,
            target.venue_lane_id,
            catalog,
        )
        source_lane_ids = _resolve_optional_scene_lanes(
            target.source_lane_ids,
            catalog,
            preset_intersection_ids=preset_intersection_ids,
        )
        return MajorEventOpeningRequest(
            event_type="major_event_opening",
            event_id=event_id,
            start_seconds=target.start_seconds,
            end_seconds=target.end_seconds,
            venue_lane_id=venue_lane_id,
            vehicle_count=target.vehicle_count,
            source_lane_ids=source_lane_ids,
            vehicle_type_id=target.vehicle_type_id,
            ai_control_enabled=target.ai_control_enabled,
        )
    if isinstance(target, DisturbanceTargetMajorEventClosing):
        venue_lane_id = _resolve_single_lane_id(
            target.intersection_id,
            target.venue_lane_id,
            catalog,
        )
        destination_lane_ids = _resolve_optional_scene_lanes(
            target.destination_lane_ids,
            catalog,
            preset_intersection_ids=preset_intersection_ids,
        )
        return MajorEventClosingRequest(
            event_type="major_event_closing",
            event_id=event_id,
            start_seconds=target.start_seconds,
            end_seconds=target.end_seconds,
            venue_lane_id=venue_lane_id,
            vehicle_count=target.vehicle_count,
            destination_lane_ids=destination_lane_ids,
            vehicle_type_id=target.vehicle_type_id,
            ai_control_enabled=target.ai_control_enabled,
        )
    raise AppError(
        code="INVALID_EVENT",
        message="Unsupported disturbance target type.",
        status_code=422,
    )


def validate_speed_limit_against_catalog(
    max_speed: float,
    lane_ids: list[str],
    catalog: SimulationCatalog,
    *,
    intersection_ids: set[str] | None = None,
) -> None:
    """限速必须严格低于目标车道的原始 max_speed，否则 SUMO 激活会失败。"""

    scope = intersection_ids if intersection_ids is not None else set(catalog.intersections)
    baselines: dict[str, float] = {}
    for intersection_id in scope:
        intersection = catalog.intersections.get(intersection_id)
        if intersection is None:
            continue
        for lane in intersection.lanes:
            baselines[lane.lane_id] = float(lane.max_speed)
    for lane_id in lane_ids:
        baseline = baselines.get(lane_id)
        if baseline is None:
            continue
        if max_speed + 1e-9 >= baseline:
            raise AppError(
                code="INVALID_SPEED_LIMIT",
                message=(
                    f"Speed limit for {lane_id} must be below the lane's original "
                    f"max_speed {baseline:g} m/s ({baseline * 3.6:g} km/h)."
                ),
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


def _resolve_optional_scene_lanes(
    requested_lane_ids: list[str] | None,
    catalog: SimulationCatalog,
    *,
    preset_intersection_ids: set[str],
) -> list[str]:
    """空列表表示沿用simulation默认端点语义；非空则必须属于当前场景"""

    if not requested_lane_ids:
        return []
    scene_lanes = {
        lane.lane_id
        for intersection_id in preset_intersection_ids
        if intersection_id in catalog.intersections
        for lane in catalog.intersections[intersection_id].lanes
    }
    unknown = set(requested_lane_ids) - scene_lanes
    if unknown:
        raise AppError(
            code="INVALID_LANE",
            message=f"Lane IDs outside current scenario: {sorted(unknown)}",
            status_code=422,
        )
    return list(requested_lane_ids)


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
