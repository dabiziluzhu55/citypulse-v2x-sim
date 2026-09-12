"""第四章附加实验的静态配置与扰动车道解析。

只读取 SimulationManager.catalog() / 生成路网，不改系统代码。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from backend.app.scenario.presets import require_scenario_preset
from simulation.sumo.building.artifacts import DEFAULT_GENERATED_DIR, GeneratedArtifactLayout
from simulation.sumo.engine.events import (
    ACCIDENT_VEHICLE_CLASS,
    DEFAULT_ACTIVITY_VEHICLE_TYPE_ID,
    MIN_ACCIDENT_LANE_LENGTH_M,
    AccidentEvent,
    DisturbanceEvent,
    LaneClosureEvent,
    MajorEventClosingEvent,
    MajorEventOpeningEvent,
    SpeedLimitEvent,
    lane_allows_vehicle_class,
)
from simulation.sumo.engine.session import LaneCapability, SimulationCatalog

DEFAULT_DURATION_SECONDS = 900.0
DEFAULT_SEED = 42
DEFAULT_STEP_LENGTH = 0.1
DEFAULT_DECISION_INTERVAL = 5.0
DEFAULT_SNAPSHOT_INTERVAL = 1.0
SPEED_LIMIT_KMH = 30.0
SPEED_LIMIT_MPS = SPEED_LIMIT_KMH / 3.6
ACCIDENT_POSITION_RATIO = 0.6
MAJOR_EVENT_VEHICLE_COUNT = 20
KMH_PER_MPS = 3.6

ALL_MODE_NAMES = (
    "fixed",
    "max_pressure",
    "sotl",
    "ippo",
    "mappo",
    "cov2x",
)

CORE_METRIC_KEYS = (
    "path_avg_speed_kmh",
    "avg_stops_per_vehicle",
    "regional_max_queue_length_m",
    "avg_travel_time",
    "avg_waiting_time",
    "throughput",
    "travel_time_index",
    "delay_time_proportion",
    "traffic_performance_index",
    "spillback_rate",
    "hard_braking_events",
    "hard_braking_rate",
    "fuel_consumption",
    "departed",
    "arrived",
    "completion_rate",
    "avg_decision_latency_ms",
)

LOWER_BETTER_METRICS = (
    "avg_travel_time",
    "avg_waiting_time",
    "regional_max_queue_length_m",
    "spillback_rate",
    "hard_braking_rate",
    "fuel_consumption",
    "travel_time_index",
    "delay_time_proportion",
    "traffic_performance_index",
)

HIGHER_BETTER_METRICS = (
    "path_avg_speed_kmh",
    "throughput",
)

IMPROVEMENT_METRICS = LOWER_BETTER_METRICS + HIGHER_BETTER_METRICS

ROBUSTNESS_PAIRS = (
    ("B2", "B1"),
    ("C2", "C1"),
    ("C3", "C1"),
    ("C4", "C1"),
)

METRIC_LABELS_ZH = {
    "path_avg_speed_kmh": "路径均速(km/h)",
    "avg_stops_per_vehicle": "均停车(次/车)",
    "regional_max_queue_length_m": "最大排队(m)",
    "avg_travel_time": "平均行程(s)",
    "avg_waiting_time": "平均停车等待(s)",
    "throughput": "吞吐流率(辆/h)",
    "travel_time_index": "TTI",
    "delay_time_proportion": "DTP",
    "traffic_performance_index": "TPI",
    "spillback_rate": "溢流率(%)",
    "hard_braking_events": "急刹车事件数",
    "hard_braking_rate": "急刹车率(次/100辆)",
    "fuel_consumption": "油耗强度(L/100km)",
    "departed": "出发",
    "arrived": "到达",
    "completion_rate": "完成率",
    "avg_decision_latency_ms": "平均决策时延(ms)",
}


@dataclass(frozen=True)
class EventIntent:
    event_id: str
    event_type: str
    start_seconds: float
    end_seconds: float
    share_venue_with: str | None = None


@dataclass(frozen=True)
class ExperimentGroup:
    experiment_id: str
    preset: str
    period: str
    label: str
    event_intents: tuple[EventIntent, ...] = ()


@dataclass(frozen=True)
class ResolvedLane:
    intersection_id: str
    lane_id: str
    role: str
    length_m: float
    original_max_speed_mps: float
    original_max_speed_kmh: float
    allow: tuple[str, ...] = ()
    disallow: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allow"] = list(self.allow)
        payload["disallow"] = list(self.disallow)
        return payload


@dataclass(frozen=True)
class ResolvedEvent:
    event_id: str
    event_type: str
    start_seconds: float
    end_seconds: float
    intersection_id: str
    lane_id: str
    original_max_speed_mps: float
    original_max_speed_kmh: float
    parameters: dict[str, Any]
    event: DisturbanceEvent = field(repr=False)
    rejected_candidates: tuple[dict[str, Any], ...] = ()

    def to_manifest(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "intersection_id": self.intersection_id,
            "lane_id": self.lane_id,
            "original_max_speed_mps": self.original_max_speed_mps,
            "original_max_speed_kmh": self.original_max_speed_kmh,
            "parameters": self.parameters,
            "rejected_candidates": list(self.rejected_candidates),
        }


EXPERIMENT_GROUPS: tuple[ExperimentGroup, ...] = (
    ExperimentGroup(
        experiment_id="B1",
        preset="east_dense",
        period="morning_peak",
        label="校园周边 / 早高峰 / 无扰动",
    ),
    ExperimentGroup(
        experiment_id="B2",
        preset="east_dense",
        period="morning_peak",
        label="校园周边 / 早高峰 / 大型活动开场+散场",
        event_intents=(
            EventIntent(
                event_id="b2_major_event_opening",
                event_type="major_event_opening",
                start_seconds=180.0,
                end_seconds=360.0,
            ),
            EventIntent(
                event_id="b2_major_event_closing",
                event_type="major_event_closing",
                start_seconds=540.0,
                end_seconds=720.0,
                share_venue_with="b2_major_event_opening",
            ),
        ),
    ),
    ExperimentGroup(
        experiment_id="C1",
        preset="west_dense",
        period="morning_peak",
        label="窄路密网 / 早高峰 / 无扰动",
    ),
    ExperimentGroup(
        experiment_id="C2",
        preset="west_dense",
        period="morning_peak",
        label="窄路密网 / 早高峰 / 车道关闭",
        event_intents=(
            EventIntent(
                event_id="c2_lane_closure",
                event_type="lane_closure",
                start_seconds=300.0,
                end_seconds=600.0,
            ),
        ),
    ),
    ExperimentGroup(
        experiment_id="C3",
        preset="west_dense",
        period="morning_peak",
        label="窄路密网 / 早高峰 / 临时限速",
        event_intents=(
            EventIntent(
                event_id="c3_speed_limit",
                event_type="speed_limit",
                start_seconds=300.0,
                end_seconds=600.0,
            ),
        ),
    ),
    ExperimentGroup(
        experiment_id="C4",
        preset="west_dense",
        period="morning_peak",
        label="窄路密网 / 早高峰 / 交通事故",
        event_intents=(
            EventIntent(
                event_id="c4_accident",
                event_type="accident",
                start_seconds=300.0,
                end_seconds=600.0,
            ),
        ),
    ),
)


def group_by_id() -> dict[str, ExperimentGroup]:
    return {group.experiment_id: group for group in EXPERIMENT_GROUPS}


def iter_incoming_lanes(
    catalog: SimulationCatalog,
    intersection_ids: tuple[str, ...],
) -> Iterator[tuple[str, LaneCapability]]:
    """固定排序：preset 路口顺序 × incoming × lane_id。"""

    for intersection_id in intersection_ids:
        capability = catalog.intersections.get(intersection_id)
        if capability is None:
            continue
        incoming = [
            lane
            for lane in capability.lanes
            if lane.role == "incoming"
        ]
        incoming.sort(key=lambda lane: (lane.lane_id, lane.lane_index))
        for lane in incoming:
            yield intersection_id, lane


def load_lane_permissions(
    generated_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """从 generated net.xml 读取 allow/disallow，供 accident 预校验。"""

    root = Path(generated_dir) if generated_dir is not None else DEFAULT_GENERATED_DIR
    net_path = GeneratedArtifactLayout(root).network_file
    index: dict[str, dict[str, Any]] = {}
    try:
        for _event, elem in ET.iterparse(net_path, events=("end",)):
            if elem.tag != "lane":
                continue
            lane_id = str(elem.get("id") or "")
            if not lane_id:
                elem.clear()
                continue
            allow_raw = elem.get("allow")
            disallow_raw = elem.get("disallow")
            index[lane_id] = {
                "allow": tuple(str(item) for item in (allow_raw or "").split() if item),
                "disallow": tuple(
                    str(item) for item in (disallow_raw or "").split() if item
                ),
                "length_m": float(elem.get("length") or 0.0),
                "speed_mps": float(elem.get("speed") or 0.0),
            }
            elem.clear()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise RuntimeError(f"Cannot inspect generated network {net_path}: {exc}") from exc
    return index


def _resolved_lane(
    intersection_id: str,
    lane: LaneCapability,
    permissions: Mapping[str, Mapping[str, Any]],
) -> ResolvedLane:
    meta = permissions.get(lane.lane_id, {})
    return ResolvedLane(
        intersection_id=intersection_id,
        lane_id=lane.lane_id,
        role=lane.role,
        length_m=float(lane.length),
        original_max_speed_mps=float(lane.max_speed),
        original_max_speed_kmh=round(float(lane.max_speed) * KMH_PER_MPS, 3),
        allow=tuple(meta.get("allow") or ()),
        disallow=tuple(meta.get("disallow") or ()),
    )


def _incoming_on_same_edge(
    catalog: SimulationCatalog,
    intersection_id: str,
    lane: LaneCapability,
) -> list[LaneCapability]:
    capability = catalog.intersections[intersection_id]
    return [
        item
        for item in capability.lanes
        if item.role == "incoming" and item.edge_id == lane.edge_id
    ]


def _candidate_ok(
    event_type: str,
    lane: LaneCapability,
    permissions: Mapping[str, Mapping[str, Any]],
    *,
    catalog: SimulationCatalog,
    intersection_id: str,
) -> tuple[bool, str]:
    if lane.role != "incoming":
        return False, "not an incoming lane"
    if event_type == "speed_limit":
        if float(lane.max_speed) <= SPEED_LIMIT_MPS + 1e-9:
            return False, (
                f"original max_speed {lane.max_speed:g} m/s "
                f"({lane.max_speed * KMH_PER_MPS:g} km/h) is not above {SPEED_LIMIT_KMH:g} km/h"
            )
        return True, ""
    if event_type == "accident":
        meta = permissions.get(lane.lane_id) or {}
        length_m = float(meta.get("length_m") or lane.length)
        if length_m + 1e-9 < MIN_ACCIDENT_LANE_LENGTH_M:
            return False, f"lane shorter than {MIN_ACCIDENT_LANE_LENGTH_M:g} m"
        if meta and not lane_allows_vehicle_class(
            tuple(meta.get("allow") or ()),
            tuple(meta.get("disallow") or ()),
            ACCIDENT_VEHICLE_CLASS,
        ):
            return False, f"lane does not allow {ACCIDENT_VEHICLE_CLASS} departure"
        return True, ""
    if event_type == "lane_closure":
        siblings = _incoming_on_same_edge(catalog, intersection_id, lane)
        if len(siblings) < 2:
            return False, (
                f"closing {lane.lane_id} would block the only incoming lane "
                f"on edge {lane.edge_id}"
            )
        indices: list[int] = []
        for item in siblings:
            try:
                indices.append(int(item.lane_id.rsplit("_", 1)[1]))
            except ValueError:
                continue
        try:
            lane_index = int(lane.lane_id.rsplit("_", 1)[1])
        except ValueError:
            lane_index = None
        if (
            len(indices) >= 3
            and lane_index is not None
            and lane_index not in {min(indices), max(indices)}
        ):
            return False, (
                f"{lane.lane_id} is an interior lane; closing it blocks in-edge lane changing"
            )
        return True, ""
    return True, ""


def _build_event(intent: EventIntent, lane: ResolvedLane) -> DisturbanceEvent:
    common = {
        "event_id": intent.event_id,
        "start_seconds": float(intent.start_seconds),
        "end_seconds": float(intent.end_seconds),
        "ai_control_enabled": False,
    }
    if intent.event_type == "lane_closure":
        return LaneClosureEvent(lane_ids=(lane.lane_id,), **common)
    if intent.event_type == "speed_limit":
        return SpeedLimitEvent(
            lane_ids=(lane.lane_id,),
            max_speed=SPEED_LIMIT_MPS,
            **common,
        )
    if intent.event_type == "accident":
        return AccidentEvent(
            lane_id=lane.lane_id,
            position_ratio=ACCIDENT_POSITION_RATIO,
            **common,
        )
    if intent.event_type == "major_event_opening":
        return MajorEventOpeningEvent(
            venue_lane_id=lane.lane_id,
            vehicle_count=MAJOR_EVENT_VEHICLE_COUNT,
            source_lane_ids=(),
            vehicle_type_id=DEFAULT_ACTIVITY_VEHICLE_TYPE_ID,
            **common,
        )
    if intent.event_type == "major_event_closing":
        return MajorEventClosingEvent(
            venue_lane_id=lane.lane_id,
            vehicle_count=MAJOR_EVENT_VEHICLE_COUNT,
            destination_lane_ids=(),
            vehicle_type_id=DEFAULT_ACTIVITY_VEHICLE_TYPE_ID,
            **common,
        )
    raise ValueError(f"Unsupported event_type={intent.event_type!r}")


def _event_parameters(intent: EventIntent, lane: ResolvedLane) -> dict[str, Any]:
    if intent.event_type == "lane_closure":
        return {"lane_ids": [lane.lane_id]}
    if intent.event_type == "speed_limit":
        return {
            "lane_ids": [lane.lane_id],
            "max_speed_mps": SPEED_LIMIT_MPS,
            "max_speed_kmh": SPEED_LIMIT_KMH,
        }
    if intent.event_type == "accident":
        return {
            "lane_id": lane.lane_id,
            "position_ratio": ACCIDENT_POSITION_RATIO,
            "vehicle_class": ACCIDENT_VEHICLE_CLASS,
            "min_lane_length_m": MIN_ACCIDENT_LANE_LENGTH_M,
        }
    if intent.event_type == "major_event_opening":
        return {
            "venue_lane_id": lane.lane_id,
            "vehicle_count": MAJOR_EVENT_VEHICLE_COUNT,
            "source_lane_ids": [],
            "source_lane_ids_mode": "events.py_auto_default_incoming",
            "vehicle_type_id": DEFAULT_ACTIVITY_VEHICLE_TYPE_ID,
        }
    return {
        "venue_lane_id": lane.lane_id,
        "vehicle_count": MAJOR_EVENT_VEHICLE_COUNT,
        "destination_lane_ids": [],
        "destination_lane_ids_mode": "events.py_auto_default_outgoing",
        "vehicle_type_id": DEFAULT_ACTIVITY_VEHICLE_TYPE_ID,
    }


def resolve_group_events(
    group: ExperimentGroup,
    catalog: SimulationCatalog,
    *,
    generated_dir: Path | None = None,
) -> list[ResolvedEvent]:
    """在 preset 路口范围内按固定顺序选出合法车道；失败则报错，不静默跳过。"""

    preset = require_scenario_preset(group.preset)
    intersection_ids = preset.intersection_ids
    unknown = [iid for iid in intersection_ids if iid not in catalog.intersections]
    if unknown:
        raise RuntimeError(
            f"{group.experiment_id}: catalog missing intersections {unknown}"
        )
    permissions = load_lane_permissions(generated_dir)
    resolved: dict[str, ResolvedEvent] = {}

    for intent in group.event_intents:
        if intent.share_venue_with:
            shared = resolved.get(intent.share_venue_with)
            if shared is None:
                raise RuntimeError(
                    f"{group.experiment_id}: {intent.event_id} shares venue with "
                    f"{intent.share_venue_with}, but that event is not resolved yet"
                )
            lane = ResolvedLane(
                intersection_id=shared.intersection_id,
                lane_id=shared.lane_id,
                role="incoming",
                length_m=0.0,
                original_max_speed_mps=shared.original_max_speed_mps,
                original_max_speed_kmh=shared.original_max_speed_kmh,
            )
            event = _build_event(intent, lane)
            resolved[intent.event_id] = ResolvedEvent(
                event_id=intent.event_id,
                event_type=intent.event_type,
                start_seconds=intent.start_seconds,
                end_seconds=intent.end_seconds,
                intersection_id=shared.intersection_id,
                lane_id=shared.lane_id,
                original_max_speed_mps=shared.original_max_speed_mps,
                original_max_speed_kmh=shared.original_max_speed_kmh,
                parameters=_event_parameters(intent, lane),
                rejected_candidates=(),
                event=event,
            )
            continue

        rejected: list[dict[str, Any]] = []
        chosen: ResolvedEvent | None = None
        for intersection_id, lane in iter_incoming_lanes(catalog, intersection_ids):
            ok, reason = _candidate_ok(
                intent.event_type,
                lane,
                permissions,
                catalog=catalog,
                intersection_id=intersection_id,
            )
            candidate = _resolved_lane(intersection_id, lane, permissions)
            if not ok:
                rejected.append(
                    {
                        "intersection_id": intersection_id,
                        "lane_id": lane.lane_id,
                        "reason": reason,
                        "original_max_speed_mps": candidate.original_max_speed_mps,
                        "original_max_speed_kmh": candidate.original_max_speed_kmh,
                    }
                )
                continue
            event = _build_event(intent, candidate)
            chosen = ResolvedEvent(
                event_id=intent.event_id,
                event_type=intent.event_type,
                start_seconds=intent.start_seconds,
                end_seconds=intent.end_seconds,
                intersection_id=intersection_id,
                lane_id=lane.lane_id,
                original_max_speed_mps=candidate.original_max_speed_mps,
                original_max_speed_kmh=candidate.original_max_speed_kmh,
                parameters=_event_parameters(intent, candidate),
                rejected_candidates=tuple(rejected),
                event=event,
            )
            break
        if chosen is None:
            raise RuntimeError(
                f"{group.experiment_id}: no legal incoming lane for "
                f"{intent.event_type} among {list(intersection_ids)}; "
                f"rejected={rejected}"
            )
        resolved[intent.event_id] = chosen
    return [resolved[intent.event_id] for intent in group.event_intents]


def run_key(experiment_id: str, algorithm: str, seed: int) -> str:
    return f"{experiment_id}|{algorithm}|{seed}"
