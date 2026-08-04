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


def topology_links(nodes: list[dict]) -> list[dict]:
    links: dict[str, dict] = {}
    for source in nodes:
        candidates = sorted(
            (
                {"node": target, "distance": haversine_meters(source, target)}
                for target in nodes
                if target["intersectionId"] != source["intersectionId"]
            ),
            key=lambda item: item["distance"],
        )
        local = [item for item in candidates if item["distance"] <= MAX_NEIGHBOR_DISTANCE_METERS]
        for item in (local or candidates)[:2]:
            target = item["node"]
            identifiers = sorted(
                (source["intersectionId"], target["intersectionId"]),
                key=lambda value: int(value.replace("demo_", "")),
            )
            route_id = f"{identifiers[0]}:{identifiers[1]}"
            links.setdefault(route_id, {
                "routeId": route_id,
                "from": identifiers[0],
                "to": identifiers[1],
            })
    return sorted(links.values(), key=lambda item: tuple(
        int(value.replace("demo_", "")) for value in item["routeId"].split(":")
    ))


def representative_lane(edge):
    lanes = [
        lane for lane in edge.getLanes()
        if any(lane.allows(vehicle_class) for vehicle_class in MOTOR_VEHICLE_CLASSES)
    ]
    if not lanes:
        return None
    return lanes[len(lanes) // 2]


def build_graph(network):
    graph: dict[str, list[tuple[str, float, object, object]]] = {}
    for edge in network.getEdges(withInternal=False):
        lane = representative_lane(edge)
        if lane is None:
            continue
        source_id = edge.getFromNode().getID()
        target_id = edge.getToNode().getID()
        graph.setdefault(source_id, []).append((
            target_id,
            max(0.1, float(edge.getLength())) + 12.0,
            edge,
            lane,
        ))
    return graph


def shortest_path(graph, start_id: str, end_id: str):
    queue: list[tuple[float, str]] = [(0.0, start_id)]
    distances = {start_id: 0.0}
    previous: dict[str, tuple[str, object, object]] = {}
    while queue:
        distance, node_id = heapq.heappop(queue)
        if distance != distances.get(node_id):
            continue
        if node_id == end_id:
            break
        for target_id, cost, edge, lane in graph.get(node_id, []):
            candidate = distance + cost
            if candidate >= distances.get(target_id, math.inf):
                continue
            distances[target_id] = candidate
            previous[target_id] = (node_id, edge, lane)
            heapq.heappush(queue, (candidate, target_id))
    if end_id not in distances:
        return None
    path = []
    cursor = end_id
    while cursor != start_id:
        source_id, edge, lane = previous[cursor]
        path.append((edge, lane))
        cursor = source_id
    path.reverse()
    return path


def append_unique(points: list[tuple[float, float]], candidate) -> None:
    point = (float(candidate[0]), float(candidate[1]))
    if points and math.hypot(point[0] - points[-1][0], point[1] - points[-1][1]) <= 0.01:
        return
    points.append(point)


def path_shape(network, path, start_node, end_node):
    points: list[tuple[float, float]] = []
    append_unique(points, start_node.getCoord())
    length = 0.0
    for edge, lane in path:
        length += float(edge.getLength())
        for point in lane.getShape():
            append_unique(points, point)
    append_unique(points, end_node.getCoord())
    coordinates = [network.convertXY2LonLat(x, y) for x, y in points]
    return coordinates, length


def route_between(network, graph, mapping: dict, link: dict) -> dict:
    source_id = str(mapping[link["from"]]["junction_id"])
    target_id = str(mapping[link["to"]]["junction_id"])
    source_node = network.getNode(source_id)
    target_node = network.getNode(target_id)
    path = shortest_path(graph, source_id, target_id)
    reversed_route = False
    if path is None:
        path = shortest_path(graph, target_id, source_id)
        source_node, target_node = target_node, source_node
        reversed_route = True
    if path is None:
        raise RuntimeError(f"No motor-vehicle road path for {link['routeId']}")
    coordinates, length = path_shape(network, path, source_node, target_node)
    if reversed_route:
        coordinates.reverse()
    return {
        **link,
        "lengthMeters": round(length, 2),
        "coordinates": [[round(float(lon), 8), round(float(lat), 8)] for lon, lat in coordinates],
    }


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit("usage: generate-topology-routes.py <network> <mapping> <catalog> <output>")
    network_path, mapping_path, catalog_path, output_path = map(Path, sys.argv[1:])
    sumolib = load_sumolib()
    network = sumolib.net.readNet(str(network_path), withInternal=False)
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    source_hash = hashlib.sha256(network_path.read_bytes()).hexdigest()
    if catalog.get("sourceSha256") != source_hash:
        raise RuntimeError("Intersection catalog and SUMO network source hashes differ")
    nodes = catalog.get("intersections", [])
    links = topology_links(nodes)
    graph = build_graph(network)
    routes = [route_between(network, graph, mapping, link) for link in links]
    payload = {
        "schemaVersion": 1,
        "generatedAt": catalog.get("generatedAt"),
        "sourceSha256": source_hash,
        "coordinateSystem": "WGS84",
        "routes": routes,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(routes)} road-following routes to {output_path}")


if __name__ == "__main__":
    main()
