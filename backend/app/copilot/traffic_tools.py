"""Traffic Copilot 的只读交通查询工具。

本模块故意不依赖 Qwen、FastAPI 或 TraCI。工具只接收一个受控的数据源，
数据源可以来自现有 ``SimulationService``，也可以是测试用的内存快照。
这样可以先验证工具的业务口径，再在后续步骤接入模型和 HTTP API。

所有工具都返回统一的 ``source / scope / timestamp / data`` 外壳，且没有
任何会修改仿真、信号控制或车辆状态的入口。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..services.history import (
    DEFAULT_HISTORY_LOOKBACK_SECONDS,
    DEFAULT_HISTORY_MAX_POINTS,
    DEFAULT_HISTORY_MAX_QUERY_SECONDS,
    HistoryQuery,
    HistoryRepository,
    HistoryUnavailableError,
)
from ..services.traffic_metrics import (
    aggregate_lane_rows as _shared_aggregate_lane_rows,
    lane_payload as _shared_lane_payload,
)
from .rag import (
    KnowledgeError,
    KnowledgeQuery,
    KnowledgeRetriever,
)


Number = int | float


class TrafficToolError(ValueError):
    """工具输入或数据不可用时抛出的可映射业务错误。"""

    def __init__(self, message: str, *, code: str = "TOOL_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolInputError(TrafficToolError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_TOOL_INPUT")


class ToolDataUnavailableError(TrafficToolError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="TOOL_DATA_UNAVAILABLE")


@dataclass(frozen=True)
class TrafficToolResult:
    """所有交通工具共享的轻量返回外壳。"""

    source: str
    scope: str
    timestamp: float | None
    data: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "scope": self.scope,
            "timestamp": self.timestamp,
            "data": _json_safe(self.data),
        }


class TrafficDataSource(Protocol):
    """工具层所需的最小只读数据源协议。"""

    def get_snapshot(self, session_id: str) -> Mapping[str, Any]:
        ...

    def get_history(self, session_id: str) -> Sequence[Mapping[str, Any]]:
        ...

    def query_history(self, request: HistoryQuery) -> Any:
        ...

    def get_intelligence(self, session_id: str) -> Mapping[str, Any] | None:
        ...

    def get_catalog(self) -> Any:
        ...

    def get_topology(self) -> "RoadTopology | None":
        ...

    def get_knowledge_documents(self) -> Sequence[Mapping[str, Any]]:
        ...


@dataclass
class RoadTopology:
    """由 TLS manifest 构造的受控车道/路口拓扑索引。

    ``from_tls_manifest`` 只读取生成的 manifest，不读取 SUMO/TraCI 运行时
    对象。跨路口关系按 manifest 中 ``from_lane -> to_lane`` 和车道归属
    推导；无法从 manifest 证明的关系不会被补猜。
    """

    lane_to_intersection: dict[str, str] = field(default_factory=dict)
    downstream_by_lane: dict[str, tuple[str, ...]] = field(default_factory=dict)
    upstream_by_lane: dict[str, tuple[str, ...]] = field(default_factory=dict)
    upstream_intersections: dict[str, tuple[str, ...]] = field(default_factory=dict)
    downstream_intersections: dict[str, tuple[str, ...]] = field(default_factory=dict)
    adjacent_intersections: dict[str, tuple[str, ...]] = field(default_factory=dict)
    connections_by_intersection: dict[str, tuple[dict[str, Any], ...]] = field(
        default_factory=dict
    )
    lane_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    phase_orders: dict[str, tuple[int, ...]] = field(default_factory=dict)

    @classmethod
    def from_tls_manifest(cls, path: str | Path) -> "RoadTopology":
        manifest_path = Path(path)
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ToolDataUnavailableError(
                f"Cannot read TLS manifest: {manifest_path}"
            ) from exc

        intersections = payload.get("intersections")
        if not isinstance(intersections, Mapping):
            raise ToolDataUnavailableError(
                "TLS manifest has no valid intersections mapping."
            )

        lane_to_intersection: dict[str, str] = {}
        lane_metadata: dict[str, dict[str, Any]] = {}
        downstream_sets: dict[str, set[str]] = {}
        connection_rows: dict[str, list[dict[str, Any]]] = {}
        incoming_edges: dict[str, set[str]] = {}
        outgoing_edges: dict[str, set[str]] = {}
        phase_orders: dict[str, tuple[int, ...]] = {}

        def lane_id(edge: object, index: object) -> str:
            return f"{edge}_{int(index)}"

        for raw_intersection_id, raw_item in intersections.items():
            intersection_id = str(raw_intersection_id)
            if not isinstance(raw_item, Mapping):
                continue
            if "phase_order" in raw_item:
                raw_phase_order = raw_item.get("phase_order")
                if not isinstance(raw_phase_order, Sequence) or isinstance(
                    raw_phase_order, (str, bytes)
                ):
                    raise ToolDataUnavailableError(
                        f"TLS manifest has invalid phase_order for {intersection_id}."
                    )
                try:
                    phases = tuple(int(value) for value in raw_phase_order)
                except (TypeError, ValueError) as exc:
                    raise ToolDataUnavailableError(
                        f"TLS manifest has invalid phase_order for {intersection_id}."
                    ) from exc
                if not phases or len(set(phases)) != len(phases):
                    raise ToolDataUnavailableError(
                        f"TLS manifest has invalid phase_order for {intersection_id}."
                    )
                phase_orders[intersection_id] = phases
            incoming_edges.setdefault(intersection_id, set())
            outgoing_edges.setdefault(intersection_id, set())
            raw_incoming = raw_item.get("incoming_lanes", {})
            if isinstance(raw_incoming, Mapping):
                for approach, raw_lane_ids in raw_incoming.items():
                    if not isinstance(raw_lane_ids, Sequence) or isinstance(
                        raw_lane_ids, (str, bytes)
                    ):
                        continue
                    for raw_lane in raw_lane_ids:
                        current_lane = str(raw_lane)
                        lane_to_intersection.setdefault(current_lane, intersection_id)
                        lane_metadata.setdefault(current_lane, {}).update(
                            {
                                "lane_id": current_lane,
                                "intersection_id": intersection_id,
                                "approach": str(approach),
                                "role": "incoming",
                            }
                        )

            rows: list[dict[str, Any]] = []
            raw_connections = raw_item.get("connections", ())
            if isinstance(raw_connections, Sequence) and not isinstance(
                raw_connections, (str, bytes)
            ):
                for index, raw_connection in enumerate(raw_connections):
                    if not isinstance(raw_connection, Mapping):
                        continue
                    try:
                        from_lane = lane_id(
                            raw_connection["from_edge"], raw_connection["from_lane"]
                        )
                        to_lane = lane_id(
                            raw_connection["to_edge"], raw_connection["to_lane"]
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                    row = {
                        "connection_id": str(
                            raw_connection.get(
                                "connection_id", f"{intersection_id}:{index}"
                            )
                        ),
                        "intersection_id": intersection_id,
                        "approach": str(raw_connection.get("approach", "")),
                        "movement": str(raw_connection.get("movement", "")),
                        "from_lane": from_lane,
                        "to_lane": to_lane,
                        "from_edge": str(raw_connection.get("from_edge", "")),
                        "to_edge": str(raw_connection.get("to_edge", "")),
                        "direction": str(raw_connection.get("direction", "")),
                        "tls_id": str(raw_connection.get("tls_id", "")),
                        "link_index": _optional_int(raw_connection.get("link_index")),
                    }
                    rows.append(row)
                    incoming_edges[intersection_id].add(row["from_edge"])
                    outgoing_edges[intersection_id].add(row["to_edge"])
                    lane_to_intersection.setdefault(from_lane, intersection_id)
                    lane_metadata.setdefault(from_lane, {}).update(
                        {
                            "lane_id": from_lane,
                            "intersection_id": intersection_id,
                            "approach": row["approach"],
                            "role": "incoming",
                            "edge_id": row["from_edge"],
                        }
                    )
                    lane_metadata.setdefault(to_lane, {}).update(
                        {
                            "lane_id": to_lane,
                            "edge_id": row["to_edge"],
                            "role": "outgoing",
                        }
                    )
                    downstream_sets.setdefault(from_lane, set()).add(to_lane)
            connection_rows[intersection_id] = rows

        downstream_intersection_sets: dict[str, set[str]] = {
            str(key): set() for key in intersections
        }
        upstream_intersection_sets: dict[str, set[str]] = {
            str(key): set() for key in intersections
        }
        for intersection_id, rows in connection_rows.items():
            for row in rows:
                source = intersection_id
                target = lane_to_intersection.get(row["to_lane"])
                if target is None or target == source:
                    continue
                downstream_intersection_sets.setdefault(source, set()).add(target)
                upstream_intersection_sets.setdefault(target, set()).add(source)

        all_intersections = sorted({str(key) for key in intersections})
        adjacent_sets: dict[str, set[str]] = {
            intersection_id: set() for intersection_id in all_intersections
        }
        for source in all_intersections:
            for target in all_intersections:
                if source == target:
                    continue
                if outgoing_edges.get(source, set()) & incoming_edges.get(target, set()):
                    adjacent_sets[source].add(target)

        upstream_by_lane: dict[str, set[str]] = {}
        for source_lane, targets in downstream_sets.items():
            for target_lane in targets:
                upstream_by_lane.setdefault(target_lane, set()).add(source_lane)

        return cls(
            lane_to_intersection=lane_to_intersection,
            downstream_by_lane={
                lane: tuple(sorted(values)) for lane, values in downstream_sets.items()
            },
            upstream_by_lane={
                lane: tuple(sorted(values)) for lane, values in upstream_by_lane.items()
            },
            upstream_intersections={
                intersection_id: tuple(sorted(values))
                for intersection_id, values in upstream_intersection_sets.items()
            },
            downstream_intersections={
                intersection_id: tuple(sorted(values))
                for intersection_id, values in downstream_intersection_sets.items()
            },
            adjacent_intersections={
                intersection_id: tuple(sorted(values))
                for intersection_id, values in adjacent_sets.items()
            },
            connections_by_intersection={
                intersection_id: tuple(rows)
                for intersection_id, rows in connection_rows.items()
            },
            lane_metadata=lane_metadata,
            phase_orders=phase_orders,
        )


class InMemoryTrafficDataSource:
    """固定快照数据源，供工具单测和后续 Mock Provider 使用。"""

    def __init__(
        self,
        snapshots: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        catalog: Any = None,
        topology: RoadTopology | None = None,
        knowledge_documents: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._snapshots = {
            str(session_id): list(values)
            for session_id, values in snapshots.items()
        }
        self._catalog = catalog
        self._topology = topology
        self._knowledge_documents = list(knowledge_documents)

    def get_snapshot(self, session_id: str) -> Mapping[str, Any]:
        values = self._snapshots.get(str(session_id))
        if not values:
            raise KeyError(session_id)
        return values[-1]

    def get_history(self, session_id: str) -> Sequence[Mapping[str, Any]]:
        values = self._snapshots.get(str(session_id))
        if not values:
            raise KeyError(session_id)
        return tuple(values)

    def query_history(self, request: HistoryQuery) -> Any:
        # 固定快照数据源保留原有路径；真实历史仓库由下面的
        # SimulationServiceTrafficDataSource 提供带事件时间线的查询结果。
        return None

    def get_intelligence(self, session_id: str) -> Mapping[str, Any] | None:
        snapshot = self.get_snapshot(session_id)
        intelligence = snapshot.get("intelligence")
        return intelligence if isinstance(intelligence, Mapping) else snapshot

    def get_catalog(self) -> Any:
        return self._catalog

    def get_topology(self) -> RoadTopology | None:
        return self._topology

    def get_knowledge_documents(self) -> Sequence[Mapping[str, Any]]:
        return tuple(self._knowledge_documents)


class SimulationServiceTrafficDataSource:
    """将现有 ``SimulationService`` 适配为工具层数据源。

    真实历史仓库在服务构造时注入；未注入时仅保留旧的测试读取器兼容路径。
    """

    def __init__(
        self,
        simulation_service: Any,
        *,
        catalog_reader: Callable[[], Any] | None = None,
        history_reader: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
        history_repository: HistoryRepository | None = None,
        topology: RoadTopology | None = None,
        knowledge_documents: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._simulation_service = simulation_service
        self._catalog_reader = catalog_reader
        self._history_reader = history_reader
        self._history_repository = history_repository
        self._topology = topology
        self._knowledge_documents = list(knowledge_documents)

    def get_snapshot(self, session_id: str) -> Mapping[str, Any]:
        payload = self._simulation_service.snapshot(session_id)
        if not isinstance(payload, Mapping):
            raise ToolDataUnavailableError("Simulation service returned an invalid snapshot.")
        return payload

    def get_history(self, session_id: str) -> Sequence[Mapping[str, Any]]:
        if self._history_reader is not None:
            return tuple(self._history_reader(session_id))
        if self._history_repository is not None:
            result = self._history_repository.query(
                HistoryQuery(
                    session_id=str(session_id),
                    lookback_seconds=None,
                    max_points=DEFAULT_HISTORY_MAX_POINTS,
                )
            )
            return tuple(result.frames)
        return (self.get_snapshot(session_id),)

    def query_history(self, request: HistoryQuery) -> Any:
        if self._history_repository is None:
            return None
        return self._history_repository.query(request)

    def get_intelligence(self, session_id: str) -> Mapping[str, Any] | None:
        reader = getattr(self._simulation_service, "get_intelligence", None)
        if reader is None:
            return None
        payload = reader(session_id)
        return payload if isinstance(payload, Mapping) else None

    def get_catalog(self) -> Any:
        if self._catalog_reader is not None:
            return self._catalog_reader()
        manager = getattr(self._simulation_service, "_manager", None)
        reader = getattr(manager, "catalog", None)
        return reader() if reader is not None else None

    def get_topology(self) -> RoadTopology | None:
        return self._topology

    def get_knowledge_documents(self) -> Sequence[Mapping[str, Any]]:
        return tuple(self._knowledge_documents)


class TrafficToolService:
    """面向 Copilot 的固定只读工具集合。"""

    def __init__(
        self,
        data_source: TrafficDataSource,
        *,
        session_id: str | None = None,
        topology: RoadTopology | None = None,
        knowledge_documents: Sequence[Mapping[str, Any]] | None = None,
        knowledge_retriever: KnowledgeRetriever | None = None,
        knowledge_default_limit: int = 5,
        history_default_lookback_seconds: float = DEFAULT_HISTORY_LOOKBACK_SECONDS,
        history_max_query_seconds: float = DEFAULT_HISTORY_MAX_QUERY_SECONDS,
        history_max_points: int = DEFAULT_HISTORY_MAX_POINTS,
    ) -> None:
        self._data_source = data_source
        self.session_id = str(session_id) if session_id is not None else None
        self._topology = topology or data_source.get_topology()
        self._knowledge_documents = (
            list(knowledge_documents)
            if knowledge_documents is not None
            else list(data_source.get_knowledge_documents())
        )
        self._knowledge_retriever = knowledge_retriever
        self._knowledge_default_limit = int(knowledge_default_limit)
        if not 1 <= self._knowledge_default_limit <= 10:
            raise ValueError("knowledge_default_limit must be between 1 and 10")
        self._history_default_lookback_seconds = float(
            history_default_lookback_seconds
        )
        self._history_max_query_seconds = float(history_max_query_seconds)
        self._history_max_points = int(history_max_points)
        if self._history_default_lookback_seconds <= 0:
            raise ValueError("history_default_lookback_seconds must be positive")
        if self._history_max_query_seconds <= 0:
            raise ValueError("history_max_query_seconds must be positive")
        if self._history_max_points <= 0:
            raise ValueError("history_max_points must be positive")

    @classmethod
    def tool_names(cls) -> tuple[str, ...]:
        return tuple(TOOL_HANDLERS)

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        if name not in TOOL_HANDLERS:
            raise ToolInputError(f"Unsupported read-only traffic tool: {name!r}")
        parsed = _parse_arguments(arguments)
        return getattr(self, TOOL_HANDLERS[name])(parsed)

    def get_current_traffic(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = _prepare_arguments(arguments, {"intersection_id", "lane_id"})
        intersection_id = _optional_id(args.get("intersection_id"), "intersection_id")
        lane_id = _optional_id(args.get("lane_id"), "lane_id")
        if not intersection_id and not lane_id:
            raise ToolInputError("Provide intersection_id or lane_id.")

        snapshot = self._require_snapshot()
        selected = self._select_snapshot_lanes(
            snapshot, intersection_id=intersection_id, lane_id=lane_id
        )
        if not selected:
            target = lane_id or intersection_id or "unknown"
            raise ToolInputError(f"No current traffic data found for {target!r}.")

        styles = _traffic_style_edges(snapshot)
        lane_rows = [
            _lane_payload(
                intersection_id=current_intersection_id,
                lane_id=current_lane_id,
                lane=lane,
                style=styles.get(_lane_edge_id(current_lane_id, lane)),
            )
            for current_intersection_id, current_lane_id, lane in selected
        ]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in lane_rows:
            groups.setdefault(str(row["intersection_id"]), []).append(row)

        intersections = []
        snapshot_intersections = _mapping_field(snapshot, "intersections")
        for current_intersection_id in sorted(groups):
            raw_intersection = snapshot_intersections.get(current_intersection_id, {})
            intersections.append(
                {
                    "intersection_id": current_intersection_id,
                    "current_phase": _field(raw_intersection, "current_phase"),
                    "pending_phase": _field(raw_intersection, "pending_phase"),
                    "stage": str(_field(raw_intersection, "stage", "") or ""),
                    "stage_elapsed_seconds": _number_or_zero(
                        _field(raw_intersection, "stage_elapsed", 0.0)
                    ),
                    "totals": _aggregate_lane_rows(groups[current_intersection_id]),
                    "lanes": groups[current_intersection_id],
                }
            )

        timestamp = _snapshot_timestamp(snapshot)
        scope = _scope_for_target(intersection_id, lane_id)
        return _result(
            "get_current_traffic",
            scope,
            timestamp,
            {
                "as_of_seconds": timestamp,
                "intersections": intersections,
                "lanes": lane_rows,
            },
        )

    def get_event_details(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = _prepare_arguments(arguments, {"event_id", "event_ids", "include_ended"})
        raw_ids = args.get("event_ids", args.get("event_id"))
        event_ids = _string_list(raw_ids, "event_ids", max_items=20)
        if not event_ids:
            raise ToolInputError("event_ids must contain at least one event ID.")
        include_ended = _bool_value(args.get("include_ended", True), "include_ended")
        snapshot = self._require_snapshot()
        cards = _event_cards(snapshot)
        disturbances = _snapshot_events(snapshot)
        card_by_id = {str(_field(item, "event_id", "")): item for item in cards}
        disturbance_by_id = {
            str(_field(item, "event_id", "")): item for item in disturbances
        }

        events: list[dict[str, Any]] = []
        not_found: list[str] = []
        for event_id in event_ids:
            card = card_by_id.get(event_id)
            disturbance = disturbance_by_id.get(event_id)
            record = _merge_event_record(
                event_id,
                card=card,
                disturbance=disturbance,
                snapshot=snapshot,
                topology=self._topology,
            )
            if record is None:
                not_found.append(event_id)
                continue
            if not include_ended and str(record["status"]).lower() not in {
                "active",
                "running",
            }:
                continue
            events.append(record)

        return _result(
            "get_event_details",
            "events:" + ",".join(event_ids),
            _snapshot_timestamp(snapshot),
            {
                "events": events,
                "not_found": not_found,
                "count": len(events),
                "include_ended": include_ended,
            },
        )

    def get_traffic_history(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = _prepare_arguments(
            arguments,
            {
                "intersection_ids",
                "lane_ids",
                "lookback_seconds",
                "from_seconds",
                "to_seconds",
                "metrics",
            },
        )
        intersection_ids = _string_list(
            args.get("intersection_ids"), "intersection_ids", max_items=20
        )
        lane_ids = _string_list(args.get("lane_ids"), "lane_ids", max_items=50)
        if not intersection_ids and not lane_ids:
            raise ToolInputError("Provide intersection_ids or lane_ids.")
        metrics = _history_metrics(args.get("metrics"))
        has_explicit_window = "from_seconds" in args or "to_seconds" in args
        if has_explicit_window and "lookback_seconds" in args:
            raise ToolInputError(
                "lookback_seconds cannot be combined with from_seconds or to_seconds."
            )
        lookback_seconds: float | None = None
        if not has_explicit_window:
            lookback_seconds = _bounded_number(
                args.get(
                    "lookback_seconds", self._history_default_lookback_seconds
                ),
                "lookback_seconds",
                minimum=1.0,
                maximum=self._history_max_query_seconds,
            )
        from_seconds = _optional_bounded_number(
            args.get("from_seconds"),
            "from_seconds",
            minimum=0.0,
            maximum=1_000_000_000.0,
        )
        to_seconds = _optional_bounded_number(
            args.get("to_seconds"),
            "to_seconds",
            minimum=0.0,
            maximum=1_000_000_000.0,
        )
        current = self._require_snapshot()
        current_timestamp = _snapshot_timestamp(current) or 0.0
        if has_explicit_window and from_seconds is None and to_seconds is None:
            raise ToolInputError(
                "from_seconds or to_seconds must be provided for an explicit window."
            )
        if from_seconds is not None and to_seconds is not None:
            if from_seconds > to_seconds:
                raise ToolInputError("from_seconds must not exceed to_seconds.")
            if to_seconds - from_seconds > self._history_max_query_seconds:
                raise ToolInputError(
                    "The requested history window exceeds the configured maximum."
                )
        if from_seconds is not None and to_seconds is None:
            # 仿真时间范围以 session 内的秒数表达；没有终点时读取到最新帧。
            if current_timestamp - from_seconds > self._history_max_query_seconds:
                raise ToolInputError(
                    "The requested history window exceeds the configured maximum."
                )
        if from_seconds is None and to_seconds is not None:
            from_seconds = max(
                0.0, to_seconds - self._history_max_query_seconds
            )
        session_id = self._require_session_id()
        query_request = HistoryQuery(
            session_id=session_id,
            intersection_ids=tuple(intersection_ids),
            lane_ids=tuple(lane_ids),
            lookback_seconds=lookback_seconds,
            from_seconds=from_seconds,
            to_seconds=to_seconds,
            metrics=tuple(metrics),
            max_points=self._history_max_points,
        )

        query_history = getattr(self._data_source, "query_history", None)
        queried = None
        if callable(query_history):
            try:
                queried = query_history(query_request)
            except (HistoryUnavailableError, KeyError, LookupError) as exc:
                raise ToolDataUnavailableError(
                    "Traffic history is unavailable for this session."
                ) from exc

        if queried is not None:
            history = [
                item for item in getattr(queried, "frames", ()) if isinstance(item, Mapping)
            ]
            history_events = [
                item
                for item in getattr(queried, "events", ())
                if isinstance(item, Mapping)
            ]
            downsampled = bool(getattr(queried, "downsampled", False))
            total_frames = int(getattr(queried, "total_frames", len(history)))
        else:
            try:
                raw_history = self._data_source.get_history(session_id)
            except (KeyError, LookupError) as exc:
                raise ToolDataUnavailableError(
                    "Traffic history is unavailable for this session."
                ) from exc
            history = [item for item in raw_history if isinstance(item, Mapping)]
            history.sort(key=lambda item: _snapshot_timestamp(item) or 0.0)
            latest_for_fallback = (
                _snapshot_timestamp(history[-1]) if history else None
            )
            if latest_for_fallback is None:
                latest_for_fallback = _snapshot_timestamp(current) or 0.0
            effective_to = (
                to_seconds if to_seconds is not None else latest_for_fallback
            )
            effective_from = (
                from_seconds
                if from_seconds is not None
                else effective_to - float(lookback_seconds or 0.0)
                if lookback_seconds is not None
                else float("-inf")
            )
            history = [
                item
                for item in history
                if (_snapshot_timestamp(item) is not None)
                and effective_from <= (_snapshot_timestamp(item) or 0.0) <= effective_to
            ]
            total_frames = len(history)
            history, downsampled = _downsample_history_snapshots(
                history, self._history_max_points
            )
            history_events = []

        history.sort(key=lambda item: _snapshot_timestamp(item) or 0.0)
        selected_history = history
        latest_timestamp = (
            _snapshot_timestamp(history[-1])
            if history
            else _snapshot_timestamp(current)
        )
        if latest_timestamp is None:
            latest_timestamp = 0.0
        if from_seconds is not None:
            start_timestamp = from_seconds
        elif lookback_seconds is not None:
            start_timestamp = max(0.0, latest_timestamp - lookback_seconds)
        else:
            start_timestamp = _snapshot_timestamp(history[0]) if history else 0.0
            if start_timestamp is None:
                start_timestamp = 0.0

        known_intersections, known_lanes = _known_snapshot_scopes(
            [*history, current]
        )
        unknown_intersections = sorted(set(intersection_ids) - known_intersections)
        unknown_lanes = sorted(set(lane_ids) - known_lanes)
        if unknown_intersections or unknown_lanes:
            missing = unknown_intersections + unknown_lanes
            raise ToolInputError(f"Unknown traffic scope(s): {missing}")

        if not history:
            return _result(
                "get_traffic_history",
                _history_scope(intersection_ids, lane_ids, lookback_seconds),
                latest_timestamp,
                {
                    "from_seconds": start_timestamp,
                    "to_seconds": latest_timestamp,
                    "lookback_seconds": lookback_seconds,
                    "metrics": metrics,
                    "series": [],
                    "trends": [],
                    "event_timeline": [],
                    "sample_count": 0,
                    "total_frames": total_frames,
                    "downsampled": downsampled,
                    "history_available": False,
                },
            )

        series: list[dict[str, Any]] = []
        for item in selected_history:
            item_timestamp = _snapshot_timestamp(item)
            if item_timestamp is None:
                continue
            for current_intersection_id in intersection_ids:
                raw_intersection = _mapping_field(item, "intersections").get(
                    current_intersection_id
                )
                if raw_intersection is None:
                    continue
                rows = [
                    _lane_payload(
                        intersection_id=current_intersection_id,
                        lane_id=str(current_lane_id),
                        lane=raw_lane,
                        style=_traffic_style_edges(item).get(
                            _lane_edge_id(str(current_lane_id), raw_lane)
                        ),
                    )
                    for current_lane_id, raw_lane in _mapping_field(
                        raw_intersection, "lanes"
                    ).items()
                ]
                series.append(
                    _history_point(
                        scope=f"intersection:{current_intersection_id}",
                        timestamp=item_timestamp,
                        metrics=(
                            _mapping_field(raw_intersection, "totals")
                            or _aggregate_lane_rows(rows)
                        ),
                        events=_events_for_scope(
                            _event_cards(item), current_intersection_id
                        ),
                        prediction=_history_prediction_for_scope(
                            item, current_intersection_id
                        ),
                        requested_metrics=metrics,
                    )
                )
            for requested_lane_id in lane_ids:
                found = _find_lane(item, requested_lane_id)
                if found is None:
                    continue
                current_intersection_id, raw_lane = found
                row = _lane_payload(
                    intersection_id=current_intersection_id,
                    lane_id=requested_lane_id,
                    lane=raw_lane,
                    style=_traffic_style_edges(item).get(
                        _lane_edge_id(requested_lane_id, raw_lane)
                    ),
                )
                series.append(
                    _history_point(
                        scope=f"lane:{requested_lane_id}",
                        timestamp=item_timestamp,
                        metrics=row,
                        events=_events_for_scope(
                            _event_cards(item),
                            current_intersection_id,
                            lane_id=requested_lane_id,
                        ),
                        prediction=None,
                        requested_metrics=metrics,
                    )
                )

        trends = _history_trends(series)
        event_timeline = (
            _filter_history_event_changes(
                history_events,
                intersection_ids=intersection_ids,
                lane_ids=lane_ids,
            )
            if "events" in metrics
            else []
        )
        return _result(
            "get_traffic_history",
            _history_scope(intersection_ids, lane_ids, lookback_seconds),
            latest_timestamp,
            {
                "from_seconds": start_timestamp,
                "to_seconds": latest_timestamp,
                "lookback_seconds": lookback_seconds,
                "metrics": metrics,
                "series": series,
                "trends": trends,
                "sample_count": len(series),
                "total_frames": total_frames,
                "downsampled": downsampled,
                "event_timeline": event_timeline,
                "history_available": True,
            },
        )

    def get_prediction(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = _prepare_arguments(
            arguments, {"intersection_id", "intersection_ids"}
        )
        singular = _optional_id(args.get("intersection_id"), "intersection_id")
        intersection_ids = _string_list(
            args.get("intersection_ids"), "intersection_ids", max_items=20
        )
        if singular:
            intersection_ids = list(dict.fromkeys([singular, *intersection_ids]))

        snapshot = self._require_snapshot()
        prediction = self._payload(snapshot, "prediction")
        if not prediction:
            return _result(
                "get_prediction",
                _prediction_scope(intersection_ids),
                _snapshot_timestamp(snapshot),
                {
                    "available": False,
                    "reason": "prediction_not_available",
                    "intersections": [],
                },
            )
        raw_intersections = _mapping_field(prediction, "intersections")
        if not intersection_ids:
            intersection_ids = sorted(str(key) for key in raw_intersections)
        missing = sorted(set(intersection_ids) - {str(key) for key in raw_intersections})
        rows = []
        cards = _event_cards(snapshot)
        for current_intersection_id in intersection_ids:
            raw = raw_intersections.get(current_intersection_id)
            if raw is None:
                continue
            current = _number_or_zero(_field(raw, "current_vehicle_count", 0.0))
            predicted = _number_or_zero(_field(raw, "predicted_vehicle_count", 0.0))
            delta = _number_or_zero(_field(raw, "delta", predicted - current))
            ratio_value = _field(raw, "delta_ratio")
            ratio = (
                _number_or_none(ratio_value)
                if ratio_value is not None
                else (delta / current if abs(current) > 1e-9 else None)
            )
            rows.append(
                {
                    "intersection_id": current_intersection_id,
                    "current_vehicle_count": current,
                    "predicted_vehicle_count": predicted,
                    "delta": delta,
                    "delta_ratio": ratio,
                    "trend": _direction_from_delta(delta),
                    "risk": _risk_for_intersection(snapshot, current_intersection_id, cards),
                }
            )

        return _result(
            "get_prediction",
            _prediction_scope(intersection_ids),
            _number_or_none(_field(prediction, "as_of_seconds"))
            or _snapshot_timestamp(snapshot),
            {
                "available": True,
                "as_of_seconds": _field(prediction, "as_of_seconds"),
                "horizon_seconds": _field(prediction, "horizon_seconds"),
                "model": _field(prediction, "model", ""),
                "model_version": _field(prediction, "model_version", ""),
                "ready": bool(_field(prediction, "ready", False)),
                "fallback": bool(_field(prediction, "fallback", False)),
                "fallback_reason": str(_field(prediction, "fallback_reason", "") or ""),
                "inference_latency_ms": _field(prediction, "inference_latency_ms"),
                "intersections": rows,
                "not_found": missing,
                "predicted_affected_intersections": list(
                    _field(prediction, "predicted_affected_intersections", ()) or ()
                ),
            },
        )

    def get_network_summary(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        _prepare_arguments(arguments, set())
        snapshot = self._require_snapshot()
        cards = _event_cards(snapshot)
        active_cards = [
            card
            for card in cards
            if str(_field(card, "status", "active")).lower() == "active"
        ]
        disturbances = _snapshot_events(snapshot)
        active_disturbances = [
            event
            for event in disturbances
            if str(_field(event, "state", "")).lower() == "active"
        ]
        event_ids = {
            str(_field(item, "event_id", ""))
            for item in [*active_cards, *active_disturbances]
            if str(_field(item, "event_id", ""))
        }

        style_edges = _traffic_style_edges(snapshot)
        level_counts = {level: 0 for level in ("free", "slow", "congested", "severe")}
        edge_intersections: dict[str, set[str]] = {}
        for current_intersection_id, _, lane in self._select_snapshot_lanes(snapshot):
            edge_id = _lane_edge_id("", lane)
            edge_intersections.setdefault(edge_id, set()).add(current_intersection_id)
        for edge_id, style in style_edges.items():
            level = str(_field(style, "level", "free"))
            level_counts.setdefault(level, 0)
            level_counts[level] += 1
            edge_intersections.setdefault(str(edge_id), set())

        snapshot_intersections = _mapping_field(snapshot, "intersections")
        hotspot_rows = []
        for current_intersection_id, raw_intersection in sorted(
            snapshot_intersections.items(), key=lambda item: str(item[0])
        ):
            lane_rows = [
                _lane_payload(
                    intersection_id=str(current_intersection_id),
                    lane_id=str(current_lane_id),
                    lane=raw_lane,
                    style=style_edges.get(_lane_edge_id(str(current_lane_id), raw_lane)),
                )
                for current_lane_id, raw_lane in _mapping_field(
                    raw_intersection, "lanes"
                ).items()
            ]
            edge_levels = {
                str(row.get("congestion_level"))
                for row in lane_rows
                if row.get("congestion_level")
            }
            severe_edges = sum(1 for row in lane_rows if row.get("congestion_level") == "severe")
            congested_edges = sum(
                1 for row in lane_rows if row.get("congestion_level") == "congested"
            )
            slow_edges = sum(1 for row in lane_rows if row.get("congestion_level") == "slow")
            local_cards = [
                card
                for card in active_cards
                if str(_field(card, "intersection_id", ""))
                == str(current_intersection_id)
            ]
            hotspot_score = (
                severe_edges * 3
                + congested_edges * 2
                + slow_edges
                + len(local_cards) * 2
            )
            if hotspot_score <= 0:
                continue
            risk = "high" if severe_edges or any(
                str(_field(card, "severity", "")).lower() == "high"
                for card in local_cards
            ) else "medium" if congested_edges or local_cards else "low"
            hotspot_rows.append(
                {
                    "intersection_id": str(current_intersection_id),
                    "hotspot_score": hotspot_score,
                    "risk": risk,
                    "congestion_levels": sorted(
                        edge_levels, key=lambda level: _CONGESTION_RANK.get(level, -1), reverse=True
                    ),
                    "active_event_count": len(local_cards),
                    "totals": _aggregate_lane_rows(lane_rows),
                }
            )
        hotspot_rows.sort(
            key=lambda row: (-int(row["hotspot_score"]), str(row["intersection_id"]))
        )

        high_events = [
            _event_brief(card)
            for card in active_cards
            if str(_field(card, "severity", "")).lower() == "high"
        ]
        medium_events = [
            _event_brief(card)
            for card in active_cards
            if str(_field(card, "severity", "")).lower() == "medium"
        ]
        prediction = self._payload(snapshot, "prediction")
        network_trend = _network_prediction_trend(prediction)
        all_lane_rows = []
        for current_intersection_id, _, lane in self._select_snapshot_lanes(snapshot):
            all_lane_rows.append(
                _lane_payload(
                    intersection_id=current_intersection_id,
                    lane_id="",
                    lane=lane,
                    style=style_edges.get(_lane_edge_id("", lane)),
                )
            )

        return _result(
            "get_network_summary",
            "network",
            _snapshot_timestamp(snapshot),
            {
                "as_of_seconds": _snapshot_timestamp(snapshot),
                "active_event_count": len(event_ids),
                "active_detected_event_count": len(active_cards),
                "active_disturbance_count": len(active_disturbances),
                "high_risk_events": high_events,
                "medium_risk_events": medium_events,
                "traffic_levels": level_counts,
                "hotspot_intersections": hotspot_rows,
                "network_trend": network_trend,
                "current_totals": _aggregate_lane_rows(all_lane_rows),
            },
        )

    def get_road_context(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = _prepare_arguments(arguments, {"intersection_id", "lane_id"})
        requested_intersection = _optional_id(
            args.get("intersection_id"), "intersection_id"
        )
        requested_lane = _optional_id(args.get("lane_id"), "lane_id")
        if not requested_intersection and not requested_lane:
            raise ToolInputError("Provide intersection_id or lane_id.")

        snapshot = self._require_snapshot(required=False)
        catalog = self._data_source.get_catalog()
        intersection_id = self._resolve_context_intersection(
            requested_intersection,
            requested_lane,
            snapshot=snapshot,
            catalog=catalog,
        )
        if intersection_id is None:
            target = requested_lane or requested_intersection or "unknown"
            raise ToolInputError(f"Cannot resolve road context for {target!r}.")

        current_intersection = (
            _mapping_field(snapshot, "intersections").get(intersection_id)
            if snapshot is not None
            else None
        )
        current_lanes = (
            _mapping_field(current_intersection, "lanes")
            if current_intersection is not None
            else {}
        )
        catalog_lanes = _catalog_lanes(catalog, intersection_id)
        lane_ids = sorted(set(catalog_lanes) | set(current_lanes))
        lane_rows = []
        for current_lane_id in lane_ids:
            metadata = dict(catalog_lanes.get(current_lane_id, {}))
            raw_lane = current_lanes.get(current_lane_id)
            if raw_lane is not None:
                live = _lane_payload(
                    intersection_id=intersection_id,
                    lane_id=current_lane_id,
                    lane=raw_lane,
                    style=(_traffic_style_edges(snapshot).get(
                        _lane_edge_id(current_lane_id, raw_lane)
                    ) if snapshot is not None else None),
                )
                metadata.update(live)
            metadata.setdefault("lane_id", current_lane_id)
            metadata.setdefault("edge_id", _lane_edge_id(current_lane_id, metadata))
            metadata["downstream_lane_ids"] = list(
                self._lane_downstream(current_lane_id, raw_lane)
            )
            metadata["upstream_lane_ids"] = list(
                self._topology.upstream_by_lane.get(current_lane_id, ())
                if self._topology is not None
                else ()
            )
            lane_rows.append(metadata)

        connections = []
        if self._topology is not None:
            connections = [
                dict(row)
                for row in self._topology.connections_by_intersection.get(
                    intersection_id, ()
                )
            ]

        upstream = self._topology.upstream_intersections.get(intersection_id, ()) if self._topology else ()
        downstream = self._topology.downstream_intersections.get(intersection_id, ()) if self._topology else ()
        adjacent = self._topology.adjacent_intersections.get(intersection_id, ()) if self._topology else ()
        if snapshot is not None and not self._topology:
            derived_downstream: set[str] = set()
            for raw_lane in current_lanes.values():
                for downstream_lane in _string_list(
                    _field(raw_lane, "downstream_lane_ids", ()),
                    "downstream_lane_ids",
                    max_items=100,
                ):
                    owner = _find_lane_owner(snapshot, downstream_lane)
                    if owner and owner != intersection_id:
                        derived_downstream.add(owner)
            derived_upstream: set[str] = set()
            for other_id, other_intersection in _mapping_field(
                snapshot, "intersections"
            ).items():
                if str(other_id) == intersection_id:
                    continue
                for other_lane in _mapping_field(other_intersection, "lanes").values():
                    if any(
                        _find_lane_owner(snapshot, downstream_lane) == intersection_id
                        for downstream_lane in _string_list(
                            _field(other_lane, "downstream_lane_ids", ()),
                            "downstream_lane_ids",
                            max_items=100,
                        )
                    ):
                        derived_upstream.add(str(other_id))
            upstream = tuple(sorted(derived_upstream))
            downstream = tuple(sorted(derived_downstream))
            adjacent = tuple(sorted(set(upstream) | set(downstream)))

        target_type = "lane" if requested_lane else "intersection"
        scope = f"{target_type}:{requested_lane or intersection_id}"
        timestamp = _snapshot_timestamp(snapshot) if snapshot is not None else None
        return _result(
            "get_road_context",
            scope,
            timestamp,
            {
                "target": {
                    "type": target_type,
                    "id": requested_lane or intersection_id,
                    "intersection_id": intersection_id,
                },
                "topology_available": self._topology is not None,
                "upstream_intersections": list(upstream),
                "downstream_intersections": list(downstream),
                "adjacent_intersections": list(adjacent),
                "lanes": lane_rows,
                "connections": connections,
            },
        )

    def search_knowledge(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = _prepare_arguments(
            arguments,
            {
                "query",
                "limit",
                "profile",
                "event_type",
                "preset_id",
                "information_types",
                "knowledge_sources",
            },
        )
        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolInputError("query must not be empty.")
        if len(query) > 500:
            raise ToolInputError("query must be at most 500 characters.")
        limit = int(
            _bounded_number(
                args.get("limit", self._knowledge_default_limit),
                "limit",
                minimum=1,
                maximum=10,
            )
        )

        if self._knowledge_retriever is not None:
            profile = str(args.get("profile", "general") or "general").strip()
            event_type = _optional_id(args.get("event_type"), "event_type")
            preset_id = _optional_id(args.get("preset_id"), "preset_id")
            information_types = _string_list(
                args.get("information_types"),
                "information_types",
                max_items=4,
            )
            knowledge_sources = _string_list(
                args.get("knowledge_sources"),
                "knowledge_sources",
                max_items=3,
            )
            try:
                request = KnowledgeQuery(
                    query=query,
                    limit=limit,
                    profile=profile,
                    event_type=event_type,
                    preset_id=preset_id,
                    information_types=tuple(information_types),
                    knowledge_sources=tuple(knowledge_sources),
                )
                response = self._knowledge_retriever.search(request)
            except ValueError as exc:
                raise ToolInputError(str(exc)) from exc
            except KnowledgeError as exc:
                raise ToolDataUnavailableError(str(exc)) from exc
            return _result(
                "search_knowledge",
                "knowledge",
                None,
                {
                    "query": query,
                    "profile": request.profile,
                    "knowledge_sources": list(request.knowledge_sources),
                    "results": [item.as_dict() for item in response.results],
                    "matched_count": len(response.results),
                    "search_mode": response.search_mode,
                    "index": dict(response.index_metadata),
                },
            )

        # This branch is intentionally kept for deterministic unit tests that
        # inject a small in-memory document set. Production construction uses
        # ChromaKnowledgeRetriever and never silently falls back here.
        if not self._knowledge_documents:
            raise ToolDataUnavailableError(
                "Knowledge vector index is unavailable."
            )
        terms = _query_terms(query)
        matches = []
        for document in self._knowledge_documents:
            if not isinstance(document, Mapping):
                continue
            title = str(document.get("title", ""))
            content = str(document.get("content", ""))
            tags = " ".join(str(item) for item in document.get("tags", ()) or ())
            haystack = f"{title} {content} {tags}".lower()
            score = sum(haystack.count(term) for term in terms if term)
            if query.lower() in haystack:
                score += 2
            if score <= 0:
                continue
            matches.append(
                {
                    "document_id": str(document.get("document_id", document.get("id", ""))),
                    "title": title,
                    "content": content,
                    "tags": list(document.get("tags", ()) or ()),
                    "source": str(document.get("source", "static_knowledge")),
                    "score": score,
                }
            )
        matches.sort(key=lambda item: (-int(item["score"]), item["document_id"]))
        matches = matches[:limit]
        return _result(
            "search_knowledge",
            "knowledge",
            None,
            {
                "query": query,
                "results": matches,
                "matched_count": len(matches),
                "search_mode": "in_memory_keyword_test",
            },
        )

    def calculator(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = _prepare_arguments(arguments, {"operation", "values", "precision"})
        operation = str(args.get("operation", "")).strip().lower()
        allowed_operations = {
            "sum",
            "average",
            "mean",
            "max",
            "min",
            "difference",
            "percentage_change",
            "ratio",
            "sort_ascending",
            "sort_descending",
        }
        if operation not in allowed_operations:
            raise ToolInputError(
                f"operation must be one of {sorted(allowed_operations)}."
            )
        raw_values = args.get("values")
        if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes)):
            raise ToolInputError("values must be a numeric array.")
        values = [_finite_number(value, "values") for value in raw_values]
        if not values:
            raise ToolInputError("values must not be empty.")
        precision = int(
            _bounded_number(args.get("precision", 4), "precision", minimum=0, maximum=8)
        )

        if operation in {"average", "mean"}:
            value: Any = sum(values) / len(values)
        elif operation == "sum":
            value = sum(values)
        elif operation == "max":
            value = max(values)
        elif operation == "min":
            value = min(values)
        elif operation == "difference":
            _require_two(values, operation)
            value = values[1] - values[0]
        elif operation == "percentage_change":
            _require_two(values, operation)
            if abs(values[0]) < 1e-12:
                raise ToolInputError("percentage_change requires a non-zero baseline.")
            value = (values[1] - values[0]) / abs(values[0]) * 100.0
        elif operation == "ratio":
            _require_two(values, operation)
            if abs(values[1]) < 1e-12:
                raise ToolInputError("ratio denominator must not be zero.")
            value = values[0] / values[1]
        elif operation == "sort_ascending":
            value = sorted(values)
        else:
            value = sorted(values, reverse=True)

        rounded = (
            [_round(item, precision) for item in value]
            if isinstance(value, list)
            else _round(value, precision)
        )
        return _result(
            "calculator",
            "calculator",
            self._optional_session_timestamp(),
            {
                "operation": operation,
                "inputs": values,
                "value": rounded,
                "precision": precision,
            },
        )

    def _require_session_id(self) -> str:
        if not self.session_id:
            raise ToolDataUnavailableError("A simulation session is required for this tool.")
        return self.session_id

    def _require_snapshot(self, *, required: bool = True) -> Mapping[str, Any] | None:
        if not self.session_id:
            if required:
                raise ToolDataUnavailableError("A simulation session is required for this tool.")
            return None
        try:
            payload = self._data_source.get_snapshot(self.session_id)
        except (KeyError, LookupError) as exc:
            raise ToolDataUnavailableError(
                f"No current snapshot is available for session {self.session_id!r}."
            ) from exc
        if not isinstance(payload, Mapping):
            raise ToolDataUnavailableError("Current snapshot is not a JSON object.")
        return payload

    def _optional_session_timestamp(self) -> float | None:
        snapshot = self._require_snapshot(required=False)
        return _snapshot_timestamp(snapshot) if snapshot is not None else None

    def _payload(
        self, snapshot: Mapping[str, Any], name: str
    ) -> Mapping[str, Any]:
        direct = _field(snapshot, name)
        if isinstance(direct, Mapping):
            return direct
        if self.session_id:
            try:
                intelligence = self._data_source.get_intelligence(self.session_id)
            except (KeyError, LookupError):
                intelligence = None
            if isinstance(intelligence, Mapping):
                value = intelligence.get(name)
                if isinstance(value, Mapping):
                    return value
        return {}

    def _select_snapshot_lanes(
        self,
        snapshot: Mapping[str, Any],
        *,
        intersection_id: str | None = None,
        lane_id: str | None = None,
    ) -> list[tuple[str, str, Mapping[str, Any]]]:
        intersections = _mapping_field(snapshot, "intersections")
        if intersection_id and intersection_id not in intersections:
            raise ToolInputError(f"Unknown intersection_id: {intersection_id}")
        selected = []
        for current_intersection_id, raw_intersection in sorted(
            intersections.items(), key=lambda item: str(item[0])
        ):
            current_intersection_id = str(current_intersection_id)
            if intersection_id and current_intersection_id != intersection_id:
                continue
            for current_lane_id, lane in sorted(
                _mapping_field(raw_intersection, "lanes").items(),
                key=lambda item: str(item[0]),
            ):
                current_lane_id = str(current_lane_id)
                if lane_id and current_lane_id != lane_id:
                    continue
                if isinstance(lane, Mapping):
                    selected.append((current_intersection_id, current_lane_id, lane))
        if lane_id and not selected:
            raise ToolInputError(f"Unknown lane_id: {lane_id}")
        return selected

    def _resolve_context_intersection(
        self,
        intersection_id: str | None,
        lane_id: str | None,
        *,
        snapshot: Mapping[str, Any] | None,
        catalog: Any,
    ) -> str | None:
        if intersection_id:
            if (
                intersection_id in _mapping_field(snapshot, "intersections")
                or intersection_id in _catalog_intersections(catalog)
                or intersection_id in (self._topology.connections_by_intersection if self._topology else {})
            ):
                return intersection_id
            raise ToolInputError(f"Unknown intersection_id: {intersection_id}")
        assert lane_id is not None
        owners = set()
        if self._topology is not None:
            owner = self._topology.lane_to_intersection.get(lane_id)
            if owner:
                owners.add(owner)
        if snapshot is not None:
            owner = _find_lane_owner(snapshot, lane_id)
            if owner:
                owners.add(owner)
        for current_intersection_id in _catalog_intersections(catalog):
            if lane_id in _catalog_lanes(catalog, current_intersection_id):
                owners.add(current_intersection_id)
        if len(owners) > 1:
            raise ToolInputError(
                f"lane_id {lane_id!r} belongs to multiple intersections: {sorted(owners)}"
            )
        return next(iter(owners), None)

    def _lane_downstream(
        self, lane_id: str, raw_lane: Mapping[str, Any] | None
    ) -> tuple[str, ...]:
        if self._topology is not None and lane_id in self._topology.downstream_by_lane:
            return self._topology.downstream_by_lane[lane_id]
        return tuple(
            _string_list(
                _field(raw_lane, "downstream_lane_ids", ()) if raw_lane else (),
                "downstream_lane_ids",
                max_items=100,
            )
        )


TOOL_HANDLERS: dict[str, str] = {
    "get_event_details": "get_event_details",
    "get_current_traffic": "get_current_traffic",
    "get_traffic_history": "get_traffic_history",
    "get_prediction": "get_prediction",
    "get_network_summary": "get_network_summary",
    "get_road_context": "get_road_context",
    "search_knowledge": "search_knowledge",
    "calculator": "calculator",
}


# OpenAI/Qwen tool calling 所需的固定只读工具定义；session_id 由后端会话上下文注入。
TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "get_event_details",
            "description": "查询一个或多个交通事件的当前详情、状态、证据和风险。",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 20,
                    },
                    "include_ended": {"type": "boolean", "default": True},
                },
                "required": ["event_ids"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_traffic",
            "description": "查询指定路口或车道的当前车辆数、停车数、速度、占有率和信号状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "intersection_id": {"type": "string"},
                    "lane_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_traffic_history",
            "description": "按路口或车道、仿真时间范围和指标查询交通历史时间序列、趋势、事件时间线或当时的预测。默认回看最近300秒；不要一次请求无关的全部指标。",
            "parameters": {
                "type": "object",
                "properties": {
                    "intersection_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                    },
                    "lane_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 50,
                    },
                    "lookback_seconds": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 3600,
                        "default": 300,
                    },
                    "from_seconds": {
                        "type": "number",
                        "minimum": 0,
                    },
                    "to_seconds": {
                        "type": "number",
                        "minimum": 0,
                    },
                    "metrics": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "vehicle_count",
                                "halting_count",
                                "mean_speed",
                                "waiting_time",
                                "occupancy",
                                "congestion_level",
                                "events",
                                "prediction",
                            ],
                        },
                        "uniqueItems": True,
                        "maxItems": 8,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_prediction",
            "description": "查询 NarrowNet-TDP 最新短时预测、变化趋势和风险摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "intersection_id": {"type": "string"},
                    "intersection_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_network_summary",
            "description": "查询全网当前事件、风险、交通等级、热点路口和网络趋势。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_road_context",
            "description": "查询路口或车道的上游、下游、相邻路口及车道连接关系。",
            "parameters": {
                "type": "object",
                "properties": {
                    "intersection_id": {"type": "string"},
                    "lane_id": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "使用交通知识库查询处置原则、项目规则、国家/行业标准或一般交通知识；不用于实时交通事实。可按 profile、事件、场景、信息类型和知识来源过滤。一般查询不要填写 information_types；如需筛选，只使用索引已有类别。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 500},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    "profile": {
                        "type": "string",
                        "enum": ["control", "general"],
                        "default": "general",
                    },
                    "event_type": {"type": "string"},
                    "preset_id": {"type": "string"},
                    "information_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 4,
                        "uniqueItems": True,
                    },
                    "knowledge_sources": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["traffic", "standards", "policy"],
                        },
                        "maxItems": 3,
                        "uniqueItems": True,
                        "description": "可选知识来源；默认使用已配置的交通索引，标准和政策索引按 profile 合并。",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "执行精确的简单统计或数值计算，不执行任意代码。",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "sum",
                            "average",
                            "max",
                            "min",
                            "difference",
                            "percentage_change",
                            "ratio",
                            "sort_ascending",
                            "sort_descending",
                        ],
                    },
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 1,
                    },
                    "precision": {"type": "integer", "minimum": 0, "maximum": 8, "default": 4},
                },
                "required": ["operation", "values"],
                "additionalProperties": False,
            },
        },
    },
)


_CONGESTION_RANK = {"free": 0, "slow": 1, "congested": 2, "severe": 3}
_RISK_RANK = {"normal": 0, "low": 1, "medium": 2, "high": 3}
_HISTORY_METRICS = (
    "vehicle_count",
    "halting_count",
    "mean_speed",
    "waiting_time",
    "occupancy",
    "congestion_level",
    "events",
    "prediction",
)
_DEFAULT_HISTORY_METRICS = tuple(
    metric for metric in _HISTORY_METRICS if metric != "prediction"
)


def _result(
    source: str,
    scope: str,
    timestamp: float | None,
    data: Mapping[str, Any],
) -> dict[str, Any]:
    return TrafficToolResult(source, scope, timestamp, data).as_dict()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _parse_arguments(arguments: Mapping[str, Any] | str | None) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ToolInputError("Tool arguments must be valid JSON.") from exc
    if not isinstance(arguments, Mapping):
        raise ToolInputError("Tool arguments must be a JSON object.")
    return dict(arguments)


def _prepare_arguments(
    arguments: Mapping[str, Any], allowed: set[str]
) -> dict[str, Any]:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ToolInputError(f"Unsupported argument(s): {unknown}")
    return dict(arguments)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _mapping_field(value: Any, name: str | None = None) -> Mapping[str, Any]:
    target = _field(value, name, {}) if name is not None else value
    return target if isinstance(target, Mapping) else {}


def _optional_id(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolInputError(f"{field_name} must be a string.")
    normalized = value.strip()
    return normalized or None


def _string_list(value: Any, field_name: str, *, max_items: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, str)):
        values = list(value)
    else:
        raise ToolInputError(f"{field_name} must be a string or string array.")
    if len(values) > max_items:
        raise ToolInputError(f"{field_name} must contain at most {max_items} items.")
    result = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ToolInputError(f"{field_name} must contain non-empty strings.")
        normalized = item.strip()
        if normalized not in result:
            result.append(normalized)
    return result


def _bool_value(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ToolInputError(f"{field_name} must be a boolean.")


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ToolInputError(f"{field_name} must contain numbers, not booleans.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ToolInputError(f"{field_name} must contain numbers.") from exc
    if not math.isfinite(result):
        raise ToolInputError(f"{field_name} must contain finite numbers.")
    return result


def _bounded_number(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    result = _finite_number(value, field_name)
    if result < minimum or result > maximum:
        raise ToolInputError(
            f"{field_name} must be between {minimum:g} and {maximum:g}."
        )
    return result


def _optional_bounded_number(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float | None:
    if value is None:
        return None
    return _bounded_number(
        value,
        field_name,
        minimum=minimum,
        maximum=maximum,
    )


def _history_metrics(value: Any) -> list[str]:
    if value is None:
        return list(_DEFAULT_HISTORY_METRICS)
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, str)):
        values = list(value)
    else:
        raise ToolInputError("metrics must be a string or string array.")
    result: list[str] = []
    for item in values:
        if not isinstance(item, str) or item not in _HISTORY_METRICS:
            raise ToolInputError(
                "metrics contains an unsupported value; expected one of "
                + ", ".join(_HISTORY_METRICS)
                + "."
            )
        if item not in result:
            result.append(item)
    if not result:
        raise ToolInputError("metrics must contain at least one metric.")
    return result


def _number_or_zero(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _optional_int(value: Any) -> int | None:
    number = _number_or_none(value)
    return int(round(number)) if number is not None else None


def _snapshot_timestamp(snapshot: Mapping[str, Any] | None) -> float | None:
    if snapshot is None:
        return None
    for name in ("elapsed_seconds", "as_of_seconds", "simulation_time", "timestamp"):
        value = _number_or_none(_field(snapshot, name))
        if value is not None:
            return value
    return None


def _traffic_style_edges(snapshot: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if snapshot is None:
        return {}
    style = _mapping_field(snapshot, "traffic_style")
    return _mapping_field(style, "edges")


def _lane_edge_id(lane_id: str, lane: Any) -> str:
    explicit = str(_field(lane, "edge_id", "") or "")
    if explicit:
        return explicit
    if lane_id and "_" in lane_id:
        return lane_id.rsplit("_", 1)[0]
    return lane_id


def _occupancy_pct(lane: Any) -> float:
    value = _number_or_zero(
        _field(lane, "occupancy_pct", _field(lane, "occupancy", 0.0))
    )
    return max(0.0, min(100.0, value))


def _lane_payload(
    *,
    intersection_id: str,
    lane_id: str,
    lane: Any,
    style: Any = None,
) -> dict[str, Any]:
    return _shared_lane_payload(
        intersection_id=intersection_id,
        lane_id=lane_id,
        lane=lane,
        style=style,
    )


def _aggregate_lane_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return _shared_aggregate_lane_rows(rows)


def _select_lane_rows(
    snapshot: Mapping[str, Any],
) -> list[tuple[str, str, Mapping[str, Any]]]:
    result = []
    for intersection_id, raw_intersection in _mapping_field(
        snapshot, "intersections"
    ).items():
        for lane_id, lane in _mapping_field(raw_intersection, "lanes").items():
            if isinstance(lane, Mapping):
                result.append((str(intersection_id), str(lane_id), lane))
    return result


def _event_cards(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    payload = _mapping_field(snapshot, "event_detection")
    cards = payload.get("cards", ())
    if isinstance(cards, Sequence) and not isinstance(cards, (str, bytes)):
        return [item for item in cards if isinstance(item, Mapping)]
    return []


def _snapshot_events(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    events = _field(snapshot, "events", ())
    if isinstance(events, Sequence) and not isinstance(events, (str, bytes)):
        return [item for item in events if isinstance(item, Mapping)]
    return []


def _event_lane_ids(event: Any) -> list[str]:
    values: list[str] = []
    field_names = (
        "lane_ids",
        "lane_id",
        "venue_lane_id",
        "source_lane_ids",
        "destination_lane_ids",
    )
    for name in field_names:
        for container in (event, _mapping_field(event, "details")):
            raw = _field(container, name, ())
            if isinstance(raw, str):
                raw_values = [raw]
            elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                raw_values = list(raw)
            else:
                raw_values = []
            for value in raw_values:
                if str(value) and str(value) not in values:
                    values.append(str(value))
    return values


def _find_lane(snapshot: Mapping[str, Any], lane_id: str) -> tuple[str, Mapping[str, Any]] | None:
    for intersection_id, current_lane_id, lane in _select_lane_rows(snapshot):
        if current_lane_id == lane_id:
            return intersection_id, lane
    return None


def _find_lane_owner(snapshot: Mapping[str, Any] | None, lane_id: str) -> str | None:
    if snapshot is None:
        return None
    found = _find_lane(snapshot, lane_id)
    return found[0] if found else None


def _merge_event_record(
    event_id: str,
    *,
    card: Mapping[str, Any] | None,
    disturbance: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any],
    topology: RoadTopology | None,
) -> dict[str, Any] | None:
    if card is None and disturbance is None:
        return None
    card_lane_ids = _event_lane_ids(card or {})
    disturbance_lane_ids = _event_lane_ids(disturbance or {})
    lane_ids = list(dict.fromkeys([*card_lane_ids, *disturbance_lane_ids]))
    locations = {
        str(_field(card, "intersection_id", ""))
        for _ in [0]
        if _field(card, "intersection_id", "")
    }
    locations.update(
        owner
        for lane_id in lane_ids
        for owner in (
            _find_lane_owner(snapshot, lane_id),
            topology.lane_to_intersection.get(lane_id) if topology else None,
        )
        if owner
    )
    start = _number_or_none(
        _field(card, "start_seconds", _field(disturbance, "start_seconds"))
    )
    end = _number_or_none(
        _field(card, "end_seconds", _field(disturbance, "end_seconds"))
    )
    timestamp = _snapshot_timestamp(snapshot) or 0.0
    status = str(
        _field(card, "status", _field(disturbance, "state", "unknown")) or "unknown"
    )
    duration = _number_or_none(_field(card, "duration_seconds"))
    if duration is None and start is not None:
        duration = max(0.0, (end if end is not None else timestamp) - start)
    cause = str(_field(card, "cause", "unknown") or "unknown")
    severity = str(_field(card, "severity", "unknown") or "unknown")
    risk = severity if severity in _RISK_RANK else "unknown"
    source = "combined" if card is not None and disturbance is not None else (
        "event_detection" if card is not None else "disturbance"
    )
    related = set(locations)
    for location in tuple(locations):
        if topology:
            related.update(topology.upstream_intersections.get(location, ()))
            related.update(topology.downstream_intersections.get(location, ()))
    record: dict[str, Any] = {
        "event_id": event_id,
        "source": source,
        "event_type": str(
            _field(card, "event_type", _field(disturbance, "event_type", "unknown"))
            or "unknown"
        ),
        "traffic_state": _field(card, "traffic_state"),
        "status": status,
        "intersection_id": _field(card, "intersection_id") or (sorted(locations)[0] if locations else None),
        "related_intersections": sorted(related),
        "lane_ids": lane_ids,
        "edge_id": str(_field(card, "edge_id", "") or ""),
        "severity": severity,
        "risk": risk,
        "confidence": _number_or_none(_field(card, "confidence")),
        "start_seconds": start,
        "end_seconds": end,
        "duration_seconds": duration,
        "cause": cause,
        "cause_status": "unconfirmed" if cause in {"", "unknown"} else "inferred",
        "evidence": list(_field(card, "evidence", ()) or ()),
        "suggestion": str(_field(card, "suggestion", "") or ""),
    }
    if disturbance is not None:
        record["disturbance"] = {
            "state": _field(disturbance, "state"),
            "event_type": _field(disturbance, "event_type"),
            "error": _field(disturbance, "error"),
            "details": dict(_mapping_field(disturbance, "details")),
        }
    return record


def _events_for_scope(
    cards: Sequence[Mapping[str, Any]],
    intersection_id: str,
    *,
    lane_id: str | None = None,
) -> list[dict[str, Any]]:
    result = []
    for card in cards:
        if str(_field(card, "intersection_id", "")) != intersection_id:
            continue
        card_lanes = set(_event_lane_ids(card))
        if lane_id is not None and lane_id not in card_lanes:
            continue
        result.append(
            {
                "event_id": str(_field(card, "event_id", "")),
                "status": str(_field(card, "status", "")),
                "traffic_state": _field(card, "traffic_state"),
                "severity": _field(card, "severity"),
            }
        )
    return result


def _history_point(
    *,
    scope: str,
    timestamp: float,
    metrics: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    prediction: Mapping[str, Any] | None = None,
    requested_metrics: Sequence[str] | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "scope": scope,
        "timestamp": timestamp,
        "vehicle_count": int(_number_or_zero(metrics.get("vehicle_count", 0))),
        "halting_count": int(_number_or_zero(metrics.get("halting_count", 0))),
        "mean_speed_mps": _number_or_zero(metrics.get("mean_speed_mps", 0.0)),
        "occupancy_pct": _number_or_zero(metrics.get("occupancy_pct", 0.0)),
        "waiting_time_seconds": _number_or_zero(
            metrics.get("waiting_time_seconds", 0.0)
        ),
        "congestion_level": metrics.get("congestion_level", "free"),
        "events": list(events),
    }
    if prediction is not None:
        values["prediction"] = dict(prediction)
    if requested_metrics is None:
        return values

    output = {"scope": scope, "timestamp": timestamp}
    field_by_metric = {
        "vehicle_count": "vehicle_count",
        "halting_count": "halting_count",
        "mean_speed": "mean_speed_mps",
        "waiting_time": "waiting_time_seconds",
        "occupancy": "occupancy_pct",
        "congestion_level": "congestion_level",
        "events": "events",
        "prediction": "prediction",
    }
    for metric in requested_metrics:
        field_name = field_by_metric[metric]
        if field_name in values:
            output[field_name] = values[field_name]
    return output


def _history_trends(series: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for point in series:
        grouped.setdefault(str(point["scope"]), []).append(point)
    trends = []
    for scope, points in sorted(grouped.items()):
        points = sorted(points, key=lambda item: float(item["timestamp"]))
        first = points[0]
        last = points[-1]
        vehicle_delta = _optional_delta(first, last, "vehicle_count", integer=True)
        halting_delta = _optional_delta(first, last, "halting_count", integer=True)
        speed_delta = _optional_delta(first, last, "mean_speed_mps")
        occupancy_delta = _optional_delta(first, last, "occupancy_pct")
        if len(points) < 2 or (
            vehicle_delta is None
            and halting_delta is None
            and speed_delta is None
            and occupancy_delta is None
        ):
            direction = "insufficient_data"
        elif (
            halting_delta is not None
            and halting_delta >= 1
        ) or (
            speed_delta is not None
            and speed_delta <= -1.0
            and (vehicle_delta is None or vehicle_delta >= 0)
        ):
            direction = "worsening"
        elif (
            halting_delta is not None
            and halting_delta <= -1
        ) or (speed_delta is not None and speed_delta >= 1.0):
            direction = "improving"
        else:
            direction = "stable"
        trends.append(
            {
                "scope": scope,
                "sample_count": len(points),
                "direction": direction,
                "vehicle_count_delta": vehicle_delta,
                "halting_count_delta": halting_delta,
                "mean_speed_delta_mps": speed_delta,
                "occupancy_delta_pct": occupancy_delta,
            }
        )
    return trends


def _optional_delta(
    first: Mapping[str, Any],
    last: Mapping[str, Any],
    field_name: str,
    *,
    integer: bool = False,
) -> int | float | None:
    if field_name not in first or field_name not in last:
        return None
    if integer:
        return int(_number_or_zero(last[field_name])) - int(
            _number_or_zero(first[field_name])
        )
    return _number_or_zero(last[field_name]) - _number_or_zero(first[field_name])


def _history_prediction_for_scope(
    snapshot: Mapping[str, Any], intersection_id: str
) -> Mapping[str, Any] | None:
    prediction = _mapping_field(snapshot, "prediction")
    intersections = _mapping_field(prediction, "intersections")
    value = intersections.get(intersection_id)
    return value if isinstance(value, Mapping) else None


def _filter_history_event_changes(
    changes: Sequence[Mapping[str, Any]],
    *,
    intersection_ids: Sequence[str],
    lane_ids: Sequence[str],
) -> list[dict[str, Any]]:
    requested_intersections = set(intersection_ids)
    requested_lanes = set(lane_ids)
    result: list[dict[str, Any]] = []
    for change in changes:
        event = _mapping_field(change, "event")
        event_intersection = str(_field(event, "intersection_id", "") or "")
        raw_related = _field(event, "related_intersections", ()) or ()
        related_values = (
            [raw_related]
            if isinstance(raw_related, str)
            else list(raw_related)
            if isinstance(raw_related, Sequence)
            else []
        )
        event_intersections = {str(item) for item in related_values}
        event_intersections.add(event_intersection)
        event_lanes = set(_event_lane_ids(event))
        if requested_intersections and not (
            requested_intersections & event_intersections
        ):
            if not requested_lanes or not (requested_lanes & event_lanes):
                continue
        elif requested_lanes and not (requested_lanes & event_lanes):
            # 路口筛选命中时，允许返回该路口的完整事件；同时提供 lane
            # 筛选时仍需满足具体车道。
            if not requested_intersections:
                continue
        result.append(dict(change))
    return result


def _downsample_history_snapshots(
    snapshots: Sequence[Mapping[str, Any]], max_points: int
) -> tuple[list[Mapping[str, Any]], bool]:
    if len(snapshots) <= max_points:
        return list(snapshots), False
    if max_points == 1:
        return [snapshots[-1]], True
    last_index = len(snapshots) - 1
    indexes = {
        round(index * last_index / (max_points - 1))
        for index in range(max_points)
    }
    return [snapshots[index] for index in sorted(indexes)], True


def _known_snapshot_scopes(
    snapshots: Sequence[Mapping[str, Any]],
) -> tuple[set[str], set[str]]:
    intersections: set[str] = set()
    lanes: set[str] = set()
    for snapshot in snapshots:
        for intersection_id, raw_intersection in _mapping_field(
            snapshot, "intersections"
        ).items():
            intersections.add(str(intersection_id))
            lanes.update(str(lane_id) for lane_id in _mapping_field(raw_intersection, "lanes"))
    return intersections, lanes


def _scope_for_target(intersection_id: str | None, lane_id: str | None) -> str:
    if lane_id:
        return f"lane:{lane_id}"
    return f"intersection:{intersection_id}"


def _history_scope(
    intersection_ids: Sequence[str],
    lane_ids: Sequence[str],
    lookback: float | None,
) -> str:
    targets = [*(f"intersection:{item}" for item in intersection_ids), *(f"lane:{item}" for item in lane_ids)]
    window = f"lookback:{lookback:g}s" if lookback is not None else "range:explicit"
    return f"{','.join(targets)};{window}"


def _prediction_scope(intersection_ids: Sequence[str]) -> str:
    return "prediction:network" if not intersection_ids else "prediction:" + ",".join(intersection_ids)


def _direction_from_delta(delta: float) -> str:
    if delta > 1e-9:
        return "increasing"
    if delta < -1e-9:
        return "decreasing"
    return "stable"


def _risk_for_intersection(
    snapshot: Mapping[str, Any],
    intersection_id: str,
    cards: Sequence[Mapping[str, Any]],
) -> str:
    risk = "normal"
    for card in cards:
        if str(_field(card, "intersection_id", "")) != intersection_id:
            continue
        severity = str(_field(card, "severity", "unknown") or "unknown").lower()
        if _RISK_RANK.get(severity, 0) > _RISK_RANK[risk]:
            risk = severity
    intersection = _mapping_field(snapshot, "intersections").get(intersection_id, {})
    styles = _traffic_style_edges(snapshot)
    for lane_id, lane in _mapping_field(intersection, "lanes").items():
        level = str(
            _field(styles.get(_lane_edge_id(str(lane_id), lane)), "level", "free")
            or "free"
        )
        candidate = "high" if level == "severe" else "medium" if level == "congested" else "low" if level == "slow" else "normal"
        if _RISK_RANK[candidate] > _RISK_RANK[risk]:
            risk = candidate
    return risk


def _event_brief(card: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": _field(card, "event_id"),
        "event_type": _field(card, "event_type"),
        "traffic_state": _field(card, "traffic_state"),
        "intersection_id": _field(card, "intersection_id"),
        "severity": _field(card, "severity"),
        "confidence": _field(card, "confidence"),
        "duration_seconds": _field(card, "duration_seconds"),
    }


def _network_prediction_trend(prediction: Mapping[str, Any]) -> dict[str, Any]:
    intersections = _mapping_field(prediction, "intersections")
    if not intersections:
        return {"direction": "unknown", "source": "prediction", "delta_vehicle_count": None}
    delta = sum(
        _number_or_zero(_field(item, "delta", 0.0)) for item in intersections.values()
    )
    return {
        "direction": _direction_from_delta(delta),
        "source": "prediction",
        "delta_vehicle_count": delta,
    }


def _catalog_intersections(catalog: Any) -> Mapping[str, Any]:
    return _mapping_field(catalog, "intersections")


def _catalog_lanes(catalog: Any, intersection_id: str) -> dict[str, dict[str, Any]]:
    intersection = _catalog_intersections(catalog).get(intersection_id)
    raw_lanes = _field(intersection, "lanes", ()) if intersection is not None else ()
    result: dict[str, dict[str, Any]] = {}
    if isinstance(raw_lanes, Mapping):
        items = raw_lanes.items()
    elif isinstance(raw_lanes, Sequence) and not isinstance(raw_lanes, (str, bytes)):
        items = ((str(_field(item, "lane_id", "")), item) for item in raw_lanes)
    else:
        items = ()
    for lane_id, raw_lane in items:
        lane_id = str(lane_id)
        if not lane_id:
            continue
        result[lane_id] = {
            "lane_id": lane_id,
            "edge_id": str(_field(raw_lane, "edge_id", "") or ""),
            "lane_index": _field(raw_lane, "lane_index"),
            "role": _field(raw_lane, "role", ""),
            "approach": _field(raw_lane, "approach"),
            "approach_label": _field(raw_lane, "approach_label"),
            "length_m": _field(raw_lane, "length", _field(raw_lane, "length_m")),
            "max_speed_mps": _field(raw_lane, "max_speed", _field(raw_lane, "max_speed_mps")),
        }
    return result


def _find_lane_locations(
    snapshot: Mapping[str, Any], lane_ids: Sequence[str]
) -> set[str]:
    return {
        intersection_id
        for intersection_id, _, lane in _select_lane_rows(snapshot)
        if str(_field(lane, "lane_id", "")) in lane_ids
    }


def _query_terms(query: str) -> list[str]:
    normalized = query.lower().strip()
    pieces = re.split(r"[\s,，。；;、/\\|]+", normalized)
    terms = [piece for piece in pieces if piece]
    return terms or [normalized]


def _require_two(values: Sequence[float], operation: str) -> None:
    if len(values) != 2:
        raise ToolInputError(f"{operation} requires exactly two values.")


def _round(value: float, precision: int) -> float:
    return round(float(value), precision)


def _find_lane_owner_from_catalog(catalog: Any, lane_id: str) -> str | None:
    for intersection_id in _catalog_intersections(catalog):
        if lane_id in _catalog_lanes(catalog, str(intersection_id)):
            return str(intersection_id)
    return None


__all__ = [
    "InMemoryTrafficDataSource",
    "RoadTopology",
    "SimulationServiceTrafficDataSource",
    "TOOL_DEFINITIONS",
    "TOOL_HANDLERS",
    "ToolDataUnavailableError",
    "ToolInputError",
    "TrafficDataSource",
    "TrafficToolError",
    "TrafficToolResult",
    "TrafficToolService",
]
