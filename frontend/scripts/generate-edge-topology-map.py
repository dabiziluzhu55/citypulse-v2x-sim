# 生成 SUMO edge_id → 有向拓扑线段(demo_A:demo_B) 映射，忽略内部边

from __future__ import annotations

import hashlib
import heapq
import json
import math
import sys
from pathlib import Path

EARTH_RADIUS_METERS = 6_371_008.8
MAX_NEIGHBOR_DISTANCE_METERS = 14_000.0
MOTOR_VEHICLE_CLASSES = ("passenger", "private", "bus", "truck")


def load_sumolib():
    try:
        import sumolib
        return sumolib
    except ModuleNotFoundError:
        import sumo
        tools_directory = Path(sumo.__file__).resolve().parent / "tools"
        sys.path.insert(0, str(tools_directory))
        import sumolib
        return sumolib


def haversine_meters(left: dict, right: dict) -> float:
    latitude_a = math.radians(float(left["latitude"]))
    latitude_b = math.radians(float(right["latitude"]))
    latitude_delta = latitude_b - latitude_a
    longitude_delta = math.radians(float(right["longitude"]) - float(left["longitude"]))
    value = math.sin(latitude_delta / 2) ** 2
    value += math.cos(latitude_a) * math.cos(latitude_b) * math.sin(longitude_delta / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(value)))


def directed_neighbor_pairs(nodes: list[dict]) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for source in nodes:
        candidates = sorted(
            (
                (target, haversine_meters(source, target))
                for target in nodes
                if target["intersectionId"] != source["intersectionId"]
            ),
            key=lambda item: item[1],
        )
        local = [item for item in candidates if item[1] <= MAX_NEIGHBOR_DISTANCE_METERS]
        for target, _distance in (local or candidates)[:2]:
            pairs.add((source["intersectionId"], target["intersectionId"]))
    return sorted(pairs)


def representative_lane(edge):
    lanes = [
        lane for lane in edge.getLanes()
        if any(lane.allows(vehicle_class) for vehicle_class in MOTOR_VEHICLE_CLASSES)
    ]
    if not lanes:
        return None
    return lanes[len(lanes) // 2]


def build_graph(network):
    graph: dict[str, list[tuple[str, float, object]]] = {}
    for edge in network.getEdges(withInternal=False):
        if representative_lane(edge) is None:
            continue
        source_id = edge.getFromNode().getID()
        target_id = edge.getToNode().getID()
        graph.setdefault(source_id, []).append(
            (target_id, max(0.1, float(edge.getLength())) + 12.0, edge)
        )
    return graph


def shortest_edges(graph, start_id: str, end_id: str):
    queue: list[tuple[float, str]] = [(0.0, start_id)]
    distances = {start_id: 0.0}
    previous: dict[str, tuple[str, object]] = {}
    while queue:
        distance, node_id = heapq.heappop(queue)
        if distance != distances.get(node_id):
            continue
        if node_id == end_id:
            break
        for target_id, cost, edge in graph.get(node_id, []):
            candidate = distance + cost
            if candidate >= distances.get(target_id, math.inf):
                continue
            distances[target_id] = candidate
            previous[target_id] = (node_id, edge)
            heapq.heappush(queue, (candidate, target_id))
    if end_id not in distances:
        return None
    edges = []
    cursor = end_id
    while cursor != start_id:
        source_id, edge = previous[cursor]
        edges.append(edge)
        cursor = source_id
    edges.reverse()
    return edges


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: generate-edge-topology-map.py <network> <mapping> <catalog> <output>"
        )
    network_path, mapping_path, catalog_path, output_path = map(Path, sys.argv[1:])
    sumolib = load_sumolib()
    network = sumolib.net.readNet(str(network_path), withInternal=False)
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    nodes = catalog.get("intersections", [])
    pairs = directed_neighbor_pairs(nodes)
    graph = build_graph(network)
    edge_to_segment: dict[str, str] = {}
    segment_edges: dict[str, list[str]] = {}
    for from_id, to_id in pairs:
        path = shortest_edges(
            graph,
            str(mapping[from_id]["junction_id"]),
            str(mapping[to_id]["junction_id"]),
        )
        segment_id = f"{from_id}:{to_id}"
        if path is None:
            raise RuntimeError(f"No motor-vehicle path for directed segment {segment_id}")
        edge_ids: list[str] = []
        for edge in path:
            edge_id = str(edge.getID())
            if edge_id.startswith(":"):
                continue
            edge_to_segment[edge_id] = segment_id
            edge_ids.append(edge_id)
        segment_edges[segment_id] = edge_ids
    payload = {
        "schemaVersion": 1,
        "generatedAt": catalog.get("generatedAt"),
        "sourceSha256": hashlib.sha256(network_path.read_bytes()).hexdigest(),
        "coordinateSystem": "WGS84",
        "segments": segment_edges,
        "edgeToSegment": edge_to_segment,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(edge_to_segment)} edge mappings / {len(segment_edges)} segments to {output_path}")


if __name__ == "__main__":
    main()
