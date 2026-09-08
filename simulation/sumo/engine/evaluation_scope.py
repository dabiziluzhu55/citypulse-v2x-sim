"""从selected_manifest / catalog解析评估范围，供session写入数据快照snapshot

不依赖traffic_eval，避免SUMO worker反向导入评估包
"""

from __future__ import annotations

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


def lane_edge_ids_from_catalog(
    catalog_intersections: Mapping[str, Any] | None,
    intersection_ids: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    lane_ids: list[str] = []
    edge_ids: list[str] = []
    seen_lanes: set[str] = set()
    seen_edges: set[str] = set()
    if not catalog_intersections:
        return (), ()
    for intersection_id in intersection_ids:
        item = catalog_intersections.get(str(intersection_id))
        if item is None:
            continue
        lanes = getattr(item, "lanes", None)
        if lanes is None and isinstance(item, Mapping):
            lanes = item.get("lanes") or ()
        for lane in lanes or ():
            lane_id = str(getattr(lane, "lane_id", "") or (lane.get("lane_id") if isinstance(lane, Mapping) else "") or "")
            edge_id = str(getattr(lane, "edge_id", "") or (lane.get("edge_id") if isinstance(lane, Mapping) else "") or "")
            if lane_id and lane_id not in seen_lanes:
                seen_lanes.add(lane_id)
                lane_ids.append(lane_id)
            if edge_id and edge_id not in seen_edges:
                seen_edges.add(edge_id)
                edge_ids.append(edge_id)
    return tuple(lane_ids), tuple(edge_ids)


def build_evaluation_scope_payload(
    *,
    preset_id: str,
    intersection_ids: Sequence[str],
    selected_manifest: Mapping[str, Any] | None = None,
    catalog_intersections: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_ids = _string_tuple(intersection_ids)
    lane_ids, edge_ids = lane_edge_ids_from_selected_manifest(selected_manifest)
    if not lane_ids and not edge_ids:
        lane_ids, edge_ids = lane_edge_ids_from_catalog(
            catalog_intersections, resolved_ids
        )
    catalog_ids = _string_tuple(
        getattr(catalog_intersections, "keys", lambda: ())()
        if catalog_intersections is not None
        else ()
    )
    covers_full_network = bool(catalog_ids) and set(resolved_ids) >= set(catalog_ids)
    return {
        "preset_id": str(preset_id or ""),
        "intersection_ids": list(resolved_ids),
        "lane_ids": list(lane_ids),
        "edge_ids": list(edge_ids),
        "covers_full_network": covers_full_network,
    }
