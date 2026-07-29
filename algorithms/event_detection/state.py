"""Common state objects for offline and realtime event detection inputs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping, Protocol


def to_float(row: Mapping[str, object], field: str, default: float = 0.0) -> float:
    value = row.get(field, "")
    if value == "" or value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"field {field!r} contains non-numeric value {value!r}") from exc


def to_int(row: Mapping[str, object], field: str, default: int = 0) -> int:
    return int(round(to_float(row, field, float(default))))


def to_bool(value: object | None) -> bool | None:
    if value is None or value == "":
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "green"}:
        return True
    if normalized in {"0", "false", "no", "n", "red"}:
        return False
    raise ValueError(f"cannot parse boolean value {value!r}")


def edge_id_from_lane(lane_id: str) -> str:
    if "_" not in lane_id:
        return lane_id
    return lane_id.rsplit("_", 1)[0]


def current_phase_from_row(row: Mapping[str, object]) -> int | None:
    value = row.get("current_phase")
    if value in {None, ""}:
        return None
    return int(float(value))


class LaneGreenResolver(Protocol):
    def lane_has_green(
        self,
        row: Mapping[str, object],
        current_phase: int | None,
        stage: str,
    ) -> bool:
        ...


@dataclass(frozen=True)
class LaneState:
    lane_id: str
    edge_id: str
    lane_has_green: bool
    vehicle_count: int
    halting_count: int
    mean_speed: float
    waiting_time: float
    occupancy: float
    approach_id: str = ""
    movement: str = ""
    signal_state: str = ""
    queue_length_m: float | None = None
    current_allowed_speed_mps: float | None = None
    downstream_lane_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntersectionState:
    source: str
    session_id: str
    sequence: int | None
    elapsed_seconds: float
    official_time: str
    intersection_id: str
    current_phase: int | None
    pending_phase: int | None
    stage: str
    stage_elapsed: float
    signal_state: str = ""
    lanes: tuple[LaneState, ...] = field(default_factory=tuple)


def rows_to_intersection_states(
    rows: list[Mapping[str, object]],
    *,
    resolver: LaneGreenResolver,
    source: str = "csv",
) -> list[IntersectionState]:
    """Group flat lane CSV rows into timestamped intersection states."""

    grouped: dict[
        tuple[str, str, float, str],
        list[Mapping[str, object]],
    ] = defaultdict(list)
    for row in rows:
        grouped[
            (
                str(row.get("session_id", "")),
                str(row.get("sequence", "")),
                to_float(row, "elapsed_seconds"),
                str(row.get("intersection_id", "")),
            )
        ].append(
            row
        )

    states = []
    for _, group_rows in sorted(grouped.items()):
        first = group_rows[0]
        current_phase = current_phase_from_row(first)
        pending_phase = current_phase_from_row({"current_phase": first.get("pending_phase")})
        stage = str(first.get("stage", ""))
        lanes = []
        for row in sorted(group_rows, key=lambda item: str(item.get("lane_id", ""))):
            lane_id = str(row.get("lane_id", ""))
            signal_state = str(row.get("signal_state", ""))
            lane_has_green = _resolve_lane_green(
                row,
                current_phase=current_phase,
                stage=stage,
                signal_state=signal_state,
                resolver=resolver,
            )
            downstream = row.get("downstream_lane_ids", "")
            downstream_lane_ids = tuple(
                item.strip() for item in str(downstream).split(";") if item.strip()
            )
            if not downstream_lane_ids:
                resolver_downstream = getattr(resolver, "downstream_lane_ids", None)
                if resolver_downstream is not None:
                    downstream_lane_ids = tuple(
                        str(item)
                        for item in resolver_downstream(
                            str(first.get("intersection_id", "")),
                            lane_id,
                        )
                    )
            lanes.append(
                LaneState(
                    lane_id=lane_id,
                    edge_id=str(row.get("edge_id") or edge_id_from_lane(lane_id)),
                    approach_id=str(row.get("approach_id", "")),
                    movement=str(row.get("movement", "")),
                    lane_has_green=lane_has_green,
                    vehicle_count=to_int(row, "vehicle_count"),
                    halting_count=to_int(row, "halting_count"),
                    mean_speed=to_float(row, "mean_speed"),
                    waiting_time=to_float(row, "waiting_time"),
                    occupancy=to_float(row, "occupancy"),
                    signal_state=signal_state,
                    queue_length_m=to_optional_float(row, "queue_length_m"),
                    current_allowed_speed_mps=to_optional_float(
                        row,
                        "current_allowed_speed_mps",
                    ),
                    downstream_lane_ids=downstream_lane_ids,
                )
            )
        states.append(
            IntersectionState(
                source=source,
                session_id=str(first.get("session_id", "")),
                sequence=(
                    to_int(first, "sequence")
                    if first.get("sequence", "") not in {None, ""}
                    else None
                ),
                elapsed_seconds=to_float(first, "elapsed_seconds"),
                official_time=str(first.get("official_time", "")),
                intersection_id=str(first.get("intersection_id", "")),
                current_phase=current_phase,
                pending_phase=(
                    to_int(first, "pending_phase")
                    if first.get("pending_phase", "") not in {None, ""}
                    else pending_phase
                ),
                stage=stage,
                stage_elapsed=to_float(first, "stage_elapsed"),
                signal_state=str(first.get("signal_state", "")),
                lanes=tuple(lanes),
            )
        )
    return states


def realtime_json_to_intersection_states(
    payload: Mapping[str, object],
    *,
    resolver: LaneGreenResolver,
    source: str = "realtime_json",
) -> list[IntersectionState]:
    """Convert the realtime SUMO JSON contract into common intersection states."""

    intersections = _as_mapping(payload.get("intersections"), "intersections")
    states = []
    for intersection_id, raw_intersection in sorted(intersections.items()):
        intersection = _as_mapping(raw_intersection, f"intersections.{intersection_id}")
        current_phase = _optional_int(intersection.get("current_phase"))
        pending_phase = _optional_int(intersection.get("pending_phase"))
        stage = str(intersection.get("stage", ""))
        signal_state = str(intersection.get("signal_state", ""))
        lanes_payload = _as_mapping(
            intersection.get("lanes"),
            f"intersections.{intersection_id}.lanes",
        )
        lanes = []
        for lane_id, raw_lane in sorted(lanes_payload.items()):
            lane = _as_mapping(raw_lane, f"lanes.{lane_id}")
            lane_signal_state = str(lane.get("signal_state", signal_state))
            row = {
                **lane,
                "intersection_id": str(intersection_id),
                "current_phase": current_phase,
                "stage": stage,
                "lane_id": str(lane_id),
                "signal_state": lane_signal_state,
            }
            lane_has_green = _resolve_lane_green(
                row,
                current_phase=current_phase,
                stage=stage,
                signal_state=lane_signal_state,
                resolver=resolver,
            )
            downstream = lane.get("downstream_lane_ids", ())
            lanes.append(
                LaneState(
                    lane_id=str(lane_id),
                    edge_id=str(lane.get("edge_id") or edge_id_from_lane(str(lane_id))),
                    approach_id=str(lane.get("approach_id", "")),
                    movement=str(lane.get("movement", "")),
                    lane_has_green=lane_has_green,
                    vehicle_count=to_int(lane, "vehicle_count"),
                    halting_count=to_int(lane, "halting_count"),
                    mean_speed=to_float(lane, "mean_speed"),
                    waiting_time=to_float(lane, "waiting_time"),
                    occupancy=to_float(lane, "occupancy"),
                    signal_state=lane_signal_state,
                    queue_length_m=to_optional_float(lane, "queue_length_m"),
                    current_allowed_speed_mps=to_optional_float(
                        lane,
                        "current_allowed_speed_mps",
                    ),
                    downstream_lane_ids=_to_string_tuple(downstream),
                )
            )

        states.append(
            IntersectionState(
                source=source,
                session_id=str(payload.get("episode_id") or payload.get("session_id", "")),
                sequence=_optional_int(payload.get("step_id", payload.get("sequence"))),
                elapsed_seconds=to_float(
                    payload,
                    "simulation_time"
                    if "simulation_time" in payload
                    else "elapsed_seconds",
                ),
                official_time=str(payload.get("official_time", "")),
                intersection_id=str(intersection_id),
                current_phase=current_phase,
                pending_phase=pending_phase,
                stage=stage,
                stage_elapsed=to_float(intersection, "stage_elapsed"),
                signal_state=signal_state,
                lanes=tuple(lanes),
            )
        )
    return states


def snapshot_to_intersection_states(
    snapshot: object,
    *,
    resolver: LaneGreenResolver,
    source: str = "realtime",
) -> list[IntersectionState]:
    """Convert a SimulationSnapshot-like object into common intersection states."""

    states = []
    for intersection_id, intersection in getattr(snapshot, "intersections", {}).items():
        current_phase = getattr(intersection, "current_phase", None)
        stage = str(getattr(intersection, "stage", ""))
        row_base = {
            "intersection_id": intersection_id,
            "current_phase": current_phase,
            "stage": stage,
        }
        lanes = []
        for lane_id, lane in getattr(intersection, "lanes", {}).items():
            row = {**row_base, "lane_id": lane_id}
            lanes.append(
                LaneState(
                    lane_id=str(lane_id),
                    edge_id=edge_id_from_lane(str(lane_id)),
                    lane_has_green=resolver.lane_has_green(row, current_phase, stage),
                    vehicle_count=int(getattr(lane, "vehicle_count")),
                    halting_count=int(getattr(lane, "halting_count")),
                    mean_speed=float(getattr(lane, "mean_speed")),
                    waiting_time=float(getattr(lane, "waiting_time")),
                    occupancy=float(getattr(lane, "occupancy")),
                    signal_state=str(getattr(lane, "signal_state", "")),
                    queue_length_m=getattr(lane, "queue_length_m", None),
                    current_allowed_speed_mps=getattr(
                        lane,
                        "current_allowed_speed_mps",
                        None,
                    ),
                )
            )
        states.append(
            IntersectionState(
                source=source,
                session_id=str(getattr(snapshot, "session_id", "")),
                sequence=getattr(snapshot, "sequence", None),
                elapsed_seconds=float(getattr(snapshot, "elapsed_seconds")),
                official_time=str(getattr(snapshot, "official_time", "")),
                intersection_id=str(intersection_id),
                current_phase=current_phase,
                pending_phase=getattr(intersection, "pending_phase", None),
                stage=stage,
                stage_elapsed=float(getattr(intersection, "stage_elapsed")),
                lanes=tuple(sorted(lanes, key=lambda lane: lane.lane_id)),
            )
        )
    return states


def to_optional_float(row: Mapping[str, object], field: str) -> float | None:
    value = row.get(field, None)
    if value in {None, ""}:
        return None
    return to_float(row, field)


def _optional_int(value: object | None) -> int | None:
    if value in {None, ""}:
        return None
    return int(round(float(value)))


def _as_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"field {field!r} must be an object")
    return value


def _to_string_tuple(value: object) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(";") if item.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    raise ValueError(f"cannot parse string tuple value {value!r}")


def _signal_has_green(signal_state: str) -> bool | None:
    state = signal_state.strip()
    if not state:
        return None
    if len(state) == 1:
        return state in {"G", "g"}
    return None


def _resolve_lane_green(
    row: Mapping[str, object],
    *,
    current_phase: int | None,
    stage: str,
    signal_state: str,
    resolver: LaneGreenResolver,
) -> bool:
    try:
        explicit = to_bool(row.get("lane_has_green"))
    except ValueError:
        explicit = _signal_has_green(str(row.get("lane_has_green", "")))
    if explicit is not None:
        return explicit
    signal_green = _signal_has_green(signal_state)
    if signal_green is not None:
        return signal_green
    return resolver.lane_has_green(row, current_phase, stage)
