"""Build an algorithm-owned CoSLight cloud topology from read-only SUMO data.

The generated JSON is an offline artifact.  CoSLight never imports this module
or reads a SUMO network during training/evaluation; runtime coordination uses
only Protocol 2.0 observations plus the generated JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence


SCHEMA_VERSION = 2
DEFAULT_VEHICLE_CLASS = "passenger"
DEFAULT_MAX_SEARCH_DISTANCE_M = 10_000.0
DEFAULT_MAX_CORRIDOR_DISTANCE_M = 1_500.0


def _intersection_sort_key(value: str) -> tuple[str, int, int | str]:
    prefix, separator, suffix = str(value).rpartition("_")
    if separator and suffix.isdigit():
        return prefix, 0, int(suffix)
    return str(value), 1, str(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strongly_connected_components(
    nodes: Sequence[str], adjacency: Mapping[str, Iterable[str]]
) -> list[list[str]]:
    """Return deterministic Tarjan SCCs for the controlled-intersection graph."""

    index = 0
    indices: Dict[str, int] = {}
    low_links: Dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        low_links[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(
            set(adjacency.get(node, ())), key=_intersection_sort_key
        ):
            if neighbor not in indices:
                visit(neighbor)
                low_links[node] = min(low_links[node], low_links[neighbor])
            elif neighbor in on_stack:
                low_links[node] = min(low_links[node], indices[neighbor])
        if low_links[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        components.append(sorted(component, key=_intersection_sort_key))

    for node in sorted(set(nodes), key=_intersection_sort_key):
        if node not in indices:
            visit(node)
    return sorted(components, key=lambda item: _intersection_sort_key(item[0]))


def _weak_components(
    nodes: Sequence[str], links: Sequence[Mapping[str, Any]]
) -> list[list[str]]:
    undirected: Dict[str, set[str]] = {str(node): set() for node in nodes}
    for link in links:
        source = str(link["source"])
        target = str(link["target"])
        undirected[source].add(target)
        undirected[target].add(source)
    components: list[list[str]] = []
    unseen = set(undirected)
    while unseen:
        root = min(unseen, key=_intersection_sort_key)
        stack = [root]
        unseen.remove(root)
        component = []
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbor in sorted(
                undirected[node], key=_intersection_sort_key, reverse=True
            ):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component, key=_intersection_sort_key))
    return sorted(components, key=lambda item: _intersection_sort_key(item[0]))


def _direction(source_xy: Sequence[float], target_xy: Sequence[float]) -> str:
    dx = float(target_xy[0]) - float(source_xy[0])
    dy = float(target_xy[1]) - float(source_xy[1])
    if abs(dx) >= abs(dy):
        return "east" if dx >= 0.0 else "west"
    return "north" if dy >= 0.0 else "south"


def _load_sumolib():
    candidates = []
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        candidates.append(Path(sumo_home) / "tools")
    candidates.append(Path("/usr/share/sumo/tools"))
    for candidate in candidates:
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.append(str(candidate))
    try:
        import sumolib  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "sumolib is required only for offline topology generation; set "
            "SUMO_HOME or install SUMO tools"
        ) from exc
    return sumolib


def _map_controlled_nodes(net, topology: Mapping[str, Any]) -> Dict[str, str]:
    node_by_intersection: Dict[str, str] = {}
    owner_by_node: Dict[str, str] = {}
    for intersection_id in sorted(topology, key=_intersection_sort_key):
        config = topology[intersection_id]
        node_ids = set()
        for approach in config.get("approaches", {}).values():
            for edge_id in approach.get("incoming_edges", []):
                try:
                    node_ids.add(net.getEdge(str(edge_id)).getToNode().getID())
                except KeyError as exc:
                    raise ValueError(
                        f"{intersection_id}: incoming edge {edge_id!r} is absent "
                        "from the generated network"
                    ) from exc
        if len(node_ids) != 1:
            raise ValueError(
                f"{intersection_id}: approaches map to {sorted(node_ids)!r}, "
                "expected exactly one controlled junction"
            )
        node_id = next(iter(node_ids))
        if node_id in owner_by_node:
            raise ValueError(
                f"controlled node {node_id!r} is shared by "
                f"{owner_by_node[node_id]!r} and {intersection_id!r}"
            )
        owner_by_node[node_id] = str(intersection_id)
        node_by_intersection[str(intersection_id)] = str(node_id)
    return node_by_intersection


def _targets_from_outgoing_edge(
    net,
    edge,
    *,
    source_node_id: str,
    intersection_by_node: Mapping[str, str],
    vehicle_class: str,
    max_distance_m: float,
) -> Dict[str, dict]:
    first_distance = float(edge.getLength())
    first_time = first_distance / max(float(edge.getSpeed()), 0.1)
    first_node = str(edge.getToNode().getID())
    first_edge_id = str(edge.getID())
    queue = [(first_distance, first_time, first_node, first_edge_id)]
    best = {first_node: first_distance}
    targets: Dict[str, dict] = {}
    while queue:
        distance, travel_time, node_id, incoming_edge_id = heapq.heappop(queue)
        if distance != best.get(node_id) or distance > max_distance_m:
            continue
        if node_id in intersection_by_node:
            if node_id != source_node_id:
                target = intersection_by_node[node_id]
                previous = targets.get(target)
                if previous is None or distance < float(previous["distance_m"]):
                    targets[target] = {
                        "distance_m": float(distance),
                        "free_flow_time_s": float(travel_time),
                        "target_incoming_edge": incoming_edge_id,
                    }
            # The next controlled junction terminates this branch.  Do not
            # fabricate a direct cloud link through another traffic signal.
            continue
        for next_edge in net.getNode(node_id).getOutgoing():
            if not next_edge.allows(vehicle_class):
                continue
            next_node = str(next_edge.getToNode().getID())
            next_distance = distance + float(next_edge.getLength())
            next_time = travel_time + float(next_edge.getLength()) / max(
                float(next_edge.getSpeed()), 0.1
            )
            if (
                next_distance <= max_distance_m
                and next_distance < best.get(next_node, float("inf"))
            ):
                best[next_node] = next_distance
                heapq.heappush(
                    queue,
                    (
                        next_distance,
                        next_time,
                        next_node,
                        str(next_edge.getID()),
                    ),
                )
    return targets


def _validate_document(document: Mapping[str, Any]) -> None:
    if int(document.get("schema_version", -1)) != SCHEMA_VERSION:
        raise ValueError("unsupported cloud topology schema")
    intersections = document.get("intersections", {})
    if not intersections:
        raise ValueError("cloud topology contains no intersections")
    expected = set(intersections)
    covered = []
    for region in document.get("regions", []):
        covered.extend(str(item) for item in region.get("intersections", []))
    if len(covered) != len(set(covered)) or set(covered) != expected:
        raise ValueError("cloud regions must partition all intersections exactly once")
    for link in document.get("directed_links", []):
        source = str(link.get("source", ""))
        target = str(link.get("target", ""))
        distance = float(link.get("distance_m", 0.0))
        if source not in expected or target not in expected or source == target:
            raise ValueError(f"invalid cloud directed link {source!r}->{target!r}")
        if distance <= 0.0:
            raise ValueError("cloud directed-link distance must be positive")
        if SCHEMA_VERSION >= 2:
            if not str(link.get("source_outgoing_edge", "")) or not str(
                link.get("target_incoming_edge", "")
            ):
                raise ValueError(
                    "cloud directed links need shortest-path boundary edges"
                )
            if float(link.get("free_flow_time_s", 0.0)) <= 0.0:
                raise ValueError(
                    "cloud directed-link free-flow time must be positive"
                )
    for corridor in document.get("corridors", []):
        members = {str(item) for item in corridor.get("intersections", [])}
        if len(members) < 2 or not members <= expected:
            raise ValueError("every cloud corridor needs at least two known intersections")


def build_document(
    net_path: Path,
    official_topology_path: Path,
    *,
    vehicle_class: str = DEFAULT_VEHICLE_CLASS,
    max_search_distance_m: float = DEFAULT_MAX_SEARCH_DISTANCE_M,
    max_corridor_distance_m: float = DEFAULT_MAX_CORRIDOR_DISTANCE_M,
) -> dict:
    if max_search_distance_m <= 0.0 or max_corridor_distance_m <= 0.0:
        raise ValueError("topology distances must be positive")
    if max_corridor_distance_m > max_search_distance_m:
        raise ValueError("corridor distance cannot exceed search distance")
    sumolib = _load_sumolib()
    net = sumolib.net.readNet(str(net_path), withInternal=False)
    official_document = json.loads(official_topology_path.read_text())
    official_topology = official_document.get("intersections", {})
    node_by_intersection = _map_controlled_nodes(net, official_topology)
    intersection_by_node = {
        node_id: intersection_id
        for intersection_id, node_id in node_by_intersection.items()
    }

    outgoing_edge_targets: Dict[str, Dict[str, list[dict]]] = {}
    aggregate: Dict[tuple[str, str], dict] = {}
    intersection_rows = {}
    for intersection_id in sorted(node_by_intersection, key=_intersection_sort_key):
        node_id = node_by_intersection[intersection_id]
        node = net.getNode(node_id)
        x_m, y_m = node.getCoord()
        intersection_rows[intersection_id] = {
            "node_id": node_id,
            "x_m": float(x_m),
            "y_m": float(y_m),
        }
        roots: Dict[str, list[dict]] = {}
        for edge in sorted(node.getOutgoing(), key=lambda item: item.getID()):
            if not edge.allows(vehicle_class):
                continue
            targets = _targets_from_outgoing_edge(
                net,
                edge,
                source_node_id=node_id,
                intersection_by_node=intersection_by_node,
                vehicle_class=vehicle_class,
                max_distance_m=max_search_distance_m,
            )
            if not targets:
                continue
            rows = [
                {
                    "intersection_id": target,
                    "distance_m": float(route["distance_m"]),
                    "free_flow_time_s": float(route["free_flow_time_s"]),
                    "target_incoming_edge": str(route["target_incoming_edge"]),
                }
                for target, route in sorted(
                    targets.items(),
                    key=lambda item: (
                        float(item[1]["distance_m"]),
                        _intersection_sort_key(item[0]),
                    ),
                )
            ]
            roots[str(edge.getID())] = rows
            for row in rows:
                key = (intersection_id, str(row["intersection_id"]))
                item = aggregate.setdefault(
                    key,
                    {
                        "source": intersection_id,
                        "target": str(row["intersection_id"]),
                        "distance_m": float(row["distance_m"]),
                        "free_flow_time_s": float(row["free_flow_time_s"]),
                        "source_outgoing_edge": str(edge.getID()),
                        "target_incoming_edge": str(
                            row["target_incoming_edge"]
                        ),
                        "source_outgoing_edges": [],
                    },
                )
                if float(row["distance_m"]) < float(item["distance_m"]):
                    item.update(
                        {
                            "distance_m": float(row["distance_m"]),
                            "free_flow_time_s": float(
                                row["free_flow_time_s"]
                            ),
                            "source_outgoing_edge": str(edge.getID()),
                            "target_incoming_edge": str(
                                row["target_incoming_edge"]
                            ),
                        }
                    )
                item["source_outgoing_edges"].append(str(edge.getID()))
        outgoing_edge_targets[intersection_id] = roots

    directed_links = []
    for item in aggregate.values():
        item["source_outgoing_edges"] = sorted(set(item["source_outgoing_edges"]))
        item["direction"] = _direction(
            (
                intersection_rows[item["source"]]["x_m"],
                intersection_rows[item["source"]]["y_m"],
            ),
            (
                intersection_rows[item["target"]]["x_m"],
                intersection_rows[item["target"]]["y_m"],
            ),
        )
        directed_links.append(item)
    directed_links.sort(
        key=lambda item: (
            _intersection_sort_key(item["source"]),
            float(item["distance_m"]),
            _intersection_sort_key(item["target"]),
        )
    )

    nodes = sorted(intersection_rows, key=_intersection_sort_key)
    adjacency: Dict[str, set[str]] = defaultdict(set)
    for link in directed_links:
        adjacency[str(link["source"])].add(str(link["target"]))
    components = _strongly_connected_components(nodes, adjacency)
    regions = [
        {
            "region_id": f"region_{index}",
            "intersections": component,
        }
        for index, component in enumerate(components, start=1)
    ]

    corridor_links = [
        link
        for link in directed_links
        if float(link["distance_m"]) <= max_corridor_distance_m
    ]
    corridor_nodes = sorted(
        {
            str(link[key])
            for link in corridor_links
            for key in ("source", "target")
        },
        key=_intersection_sort_key,
    )
    corridor_components = [
        component
        for component in _weak_components(corridor_nodes, corridor_links)
        if len(component) >= 2
    ]
    corridors = [
        {
            "corridor_id": f"corridor_{index}",
            "intersections": component,
            "directed_links": [
                {
                    "source": link["source"],
                    "target": link["target"],
                    "direction": link["direction"],
                    "distance_m": link["distance_m"],
                    "free_flow_time_s": link["free_flow_time_s"],
                    "source_outgoing_edge": link["source_outgoing_edge"],
                    "target_incoming_edge": link["target_incoming_edge"],
                }
                for link in corridor_links
                if link["source"] in component and link["target"] in component
            ],
        }
        for index, component in enumerate(corridor_components, start=1)
    ]

    document = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "net_file": str(net_path),
            "net_sha256": _sha256(net_path),
            "official_topology_file": str(official_topology_path),
            "official_topology_sha256": _sha256(official_topology_path),
        },
        "generation_config": {
            "vehicle_class": vehicle_class,
            "max_search_distance_m": float(max_search_distance_m),
            "max_corridor_distance_m": float(max_corridor_distance_m),
        },
        "intersections": intersection_rows,
        "regions": regions,
        "corridors": corridors,
        "directed_links": directed_links,
        "outgoing_edge_targets": outgoing_edge_targets,
    }
    _validate_document(document)
    return document


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--official-topology", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--vehicle-class", default=DEFAULT_VEHICLE_CLASS)
    parser.add_argument(
        "--max-search-distance",
        type=float,
        default=DEFAULT_MAX_SEARCH_DISTANCE_M,
    )
    parser.add_argument(
        "--max-corridor-distance",
        type=float,
        default=DEFAULT_MAX_CORRIDOR_DISTANCE_M,
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    document = build_document(
        args.net,
        args.official_topology,
        vehicle_class=args.vehicle_class,
        max_search_distance_m=args.max_search_distance,
        max_corridor_distance_m=args.max_corridor_distance,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    )
    print(
        f"cloud topology: {len(document['intersections'])} intersections, "
        f"{len(document['regions'])} regions, "
        f"{len(document['corridors'])} corridors, "
        f"{len(document['directed_links'])} directed links -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
