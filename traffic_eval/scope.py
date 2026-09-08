"""典型场景评估范围：由 session 传入，traffic_eval 不硬编码路口 ID。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


def _string_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        return (values,) if values else ()
    seen: set[str] = set()
    ordered: list[str] = []
    for item in values:
        key = str(item or "")
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return tuple(ordered)


@dataclass(frozen=True)
class EvaluationScope:
    preset_id: str
    intersection_ids: tuple[str, ...]
    lane_ids: tuple[str, ...] = ()
    edge_ids: tuple[str, ...] = ()
    covers_full_network: bool = False

    def contains_vehicle(self, lane_id: str | None, road_id: str | None) -> bool:
        if self.covers_full_network:
            return True
        lane = str(lane_id or "")
        if lane and lane in self._lane_set:
            return True
        road = str(road_id or "")
        if road and road in self._edge_set:
            return True
        # 路口内部 lane/edge（如 :intersectionId_0）仍视为场景内
        for intersection_id in self.intersection_ids:
            token = f":{intersection_id}"
            if (lane and token in lane) or (road and token in road):
                return True
        return False

    @property
    def _lane_set(self) -> frozenset[str]:
        return frozenset(self.lane_ids)

    @property
    def _edge_set(self) -> frozenset[str]:
        return frozenset(self.edge_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "intersection_ids": list(self.intersection_ids),
            "lane_ids": list(self.lane_ids),
            "edge_ids": list(self.edge_ids),
            "covers_full_network": bool(self.covers_full_network),
        }


def parse_evaluation_scope(value: Any) -> EvaluationScope | None:
    if value is None:
        return None
    if isinstance(value, EvaluationScope):
        return value
    if not isinstance(value, Mapping):
        return None
    preset_id = str(value.get("preset_id") or value.get("scenario_preset_id") or "")
    intersection_ids = _string_tuple(
        value.get("intersection_ids") or value.get("controlled_intersection_ids")
    )
    if not preset_id and not intersection_ids:
        return None
    covers = value.get("covers_full_network")
    return EvaluationScope(
        preset_id=preset_id,
        intersection_ids=intersection_ids,
        lane_ids=_string_tuple(value.get("lane_ids")),
        edge_ids=_string_tuple(value.get("edge_ids")),
        covers_full_network=bool(covers) if covers is not None else False,
    )


def lane_edge_ids_from_selected_manifest(
    selected_manifest: Mapping[str, Any] | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    lane_ids: list[str] = []
    edge_ids: list[str] = []
    seen_lanes: set[str] = set()
    seen_edges: set[str] = set()
    if not selected_manifest:
        return (), ()
    for item in selected_manifest.values():
        if not isinstance(item, Mapping):
            continue
        connections = item.get("connections") or ()
        if isinstance(connections, Mapping):
            connections = connections.values()
        for connection in connections:
            if not isinstance(connection, Mapping):
                continue
            for edge_key, lane_key in (("from_edge", "from_lane"), ("to_edge", "to_lane")):
                edge_id = str(connection.get(edge_key) or "")
                if not edge_id:
                    continue
                if edge_id not in seen_edges:
                    seen_edges.add(edge_id)
                    edge_ids.append(edge_id)
                lane_index = connection.get(lane_key)
                if lane_index is None or str(lane_index) == "":
                    continue
                lane_id = f"{edge_id}_{lane_index}"
                if lane_id not in seen_lanes:
                    seen_lanes.add(lane_id)
                    lane_ids.append(lane_id)
        for lane in item.get("lanes") or ():
            if isinstance(lane, Mapping):
                lane_id = str(lane.get("lane_id") or "")
                edge_id = str(lane.get("edge_id") or "")
            else:
                lane_id = str(getattr(lane, "lane_id", "") or "")
                edge_id = str(getattr(lane, "edge_id", "") or "")
            if lane_id and lane_id not in seen_lanes:
                seen_lanes.add(lane_id)
                lane_ids.append(lane_id)
            if edge_id and edge_id not in seen_edges:
                seen_edges.add(edge_id)
                edge_ids.append(edge_id)
    return tuple(lane_ids), tuple(edge_ids)


def build_evaluation_scope(
    *,
    preset_id: str,
    intersection_ids: Sequence[str],
    selected_manifest: Mapping[str, Any] | None = None,
    catalog_intersection_ids: Sequence[str] | None = None,
    lane_ids: Sequence[str] | None = None,
    edge_ids: Sequence[str] | None = None,
    covers_full_network: bool | None = None,
) -> EvaluationScope:
    resolved_intersections = _string_tuple(intersection_ids)
    extracted_lanes, extracted_edges = lane_edge_ids_from_selected_manifest(selected_manifest)
    resolved_lanes = _string_tuple(lane_ids) if lane_ids is not None else extracted_lanes
    resolved_edges = _string_tuple(edge_ids) if edge_ids is not None else extracted_edges
    catalog_ids = _string_tuple(catalog_intersection_ids)
    if covers_full_network is None:
        covers_full_network = bool(catalog_ids) and set(resolved_intersections) >= set(catalog_ids)
    return EvaluationScope(
        preset_id=str(preset_id or ""),
        intersection_ids=resolved_intersections,
        lane_ids=resolved_lanes,
        edge_ids=resolved_edges,
        covers_full_network=bool(covers_full_network),
    )


def snapshot_evaluation_scope(snapshot: Any) -> EvaluationScope | None:
    return parse_evaluation_scope(getattr(snapshot, "evaluation_scope", None))
