"""Versioned dataclasses for Traffic-Qwen offline dataset records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping


DATASET_VERSION = "traffic_qwen_sft_v1"
FULL_EXPERT_VERSION = "traffic_qwen_full_expert_v1"
OBSERVATION_VERSION = "traffic_qwen_observation_v1"
ACTION_SPACE_SIGNAL_ONLY = "signal_only"
ACTION_SPACE_SIGNAL_VEHICLE = "signal_vehicle"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class EventSpec:
    event_type: str
    start_seconds: float
    end_seconds: float
    intersection_id: str
    lane_ids: tuple[str, ...] = ()
    lane_id: str | None = None
    max_speed: float | None = None
    position_ratio: float | None = None
    venue_lane_id: str | None = None
    source_lane_ids: tuple[str, ...] = ()
    destination_lane_ids: tuple[str, ...] = ()
    vehicle_count: int | None = None
    vehicle_type_id: str = "citypulse_event_passenger"
    severity: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True)
class ScenarioSpec:
    scenario_id: str
    scenario_group_id: str
    period: str
    scope: str
    scenario_preset_id: str
    scenario_scope: str
    seed: int
    intersection_ids: tuple[str, ...]
    duration_seconds: float
    step_length: float
    decision_interval: float
    snapshot_interval_seconds: float
    event: EventSpec
    dataset_version: str = DATASET_VERSION

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScenarioSpec":
        event_payload = dict(payload["event"])
        event_payload["lane_ids"] = tuple(event_payload.get("lane_ids") or ())
        event_payload["source_lane_ids"] = tuple(event_payload.get("source_lane_ids") or ())
        event_payload["destination_lane_ids"] = tuple(
            event_payload.get("destination_lane_ids") or ()
        )
        event_payload["severity"] = dict(event_payload.get("severity") or {})
        return cls(
            scenario_id=str(payload["scenario_id"]),
            scenario_group_id=str(payload["scenario_group_id"]),
            period=str(payload["period"]),
            scope=str(payload["scope"]),
            scenario_preset_id=str(payload["scenario_preset_id"]),
            scenario_scope=str(payload["scenario_scope"]),
            seed=int(payload["seed"]),
            intersection_ids=tuple(str(item) for item in payload["intersection_ids"]),
            duration_seconds=float(payload["duration_seconds"]),
            step_length=float(payload["step_length"]),
            decision_interval=float(payload["decision_interval"]),
            snapshot_interval_seconds=float(payload["snapshot_interval_seconds"]),
            event=EventSpec(**event_payload),
            dataset_version=str(payload.get("dataset_version") or DATASET_VERSION),
        )


@dataclass(frozen=True)
class Provenance:
    git_commit: str
    dataset_version: str
    scoring_version: str
    timestamp: str
    sumo_version: str
    step_length: float
    decision_interval: float
    control_mode: str
    model_alias: str | None = None
    checkpoint_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)
