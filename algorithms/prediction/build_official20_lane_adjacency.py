"""Build an auditable lane-level graph for the official 20 intersections.

The forecasting nodes are only the official intersections' incoming lanes, not
every SUMO lane.  The generated NPZ keeps directional traffic propagation
separate from the symmetric graph consumed by the current Chebyshev STGCN.
"""

from __future__ import annotations

import argparse
import csv
import json
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def _lane_id(edge_id: str, lane_index: object) -> str:
    return f"{edge_id}_{lane_index}"


@dataclass(frozen=True)
class ManifestTopology:
    nodes: tuple[str, ...]
    owners: dict[str, tuple[str, ...]]
    movements: dict[tuple[str, str], tuple[str, ...]]


def _read_manifest(path: Path) -> ManifestTopology:
    payload = json.loads(path.read_text(encoding="utf-8"))
    intersections = payload.get("intersections")
    if not isinstance(intersections, dict) or len(intersections) != 20:
        raise ValueError("tls manifest must contain exactly 20 official intersections.")

    owners: dict[str, set[str]] = defaultdict(set)
    movements: dict[tuple[str, str], set[str]] = defaultdict(set)
    for intersection_id, item in intersections.items():
        connections = item.get("connections") if isinstance(item, dict) else None
        if not isinstance(connections, list):
            raise ValueError(f"{intersection_id}: connections must be a list.")
        for connection in connections:
            if not isinstance(connection, dict):
                raise ValueError(f"{intersection_id}: invalid connection entry.")
            try:
                source = _lane_id(str(connection["from_edge"]), connection["from_lane"])
                target = _lane_id(str(connection["to_edge"]), connection["to_lane"])
            except KeyError as exc:
                raise ValueError(f"{intersection_id}: connection lacks {exc.args[0]!r}.") from exc
            owners[source].add(str(intersection_id))
            movement = str(connection.get("movement", "unknown"))
            movements[(source, target)].add(movement)

    nodes = tuple(sorted(owners))
    if not nodes:
        raise ValueError("tls manifest has no incoming lanes.")
    return ManifestTopology(
        nodes=nodes,
        owners={lane: tuple(sorted(value)) for lane, value in owners.items()},
        movements={key: tuple(sorted(value)) for key, value in movements.items()},
    )


def _read_network_connections(net_path: Path) -> dict[str, tuple[str, ...]]:
    successors: dict[str, set[str]] = defaultdict(set)
    for _event, element in ET.iterparse(net_path, events=("end",)):
        if element.tag != "connection":
            continue
        from_edge = element.get("from")
        to_edge = element.get("to")
        from_lane = element.get("fromLane")
        to_lane = element.get("toLane")
        if None not in (from_edge, to_edge, from_lane, to_lane):
            # Internal lanes are traversed by SUMO between these two external
            # lanes, but they are not stable 5-second forecasting nodes.
            if not from_edge.startswith(":") and not to_edge.startswith(":"):
                successors[_lane_id(from_edge, from_lane)].add(_lane_id(to_edge, to_lane))
        element.clear()
    if not successors:
        raise ValueError(f"no external lane connections found in {net_path}")
    return {lane: tuple(sorted(targets)) for lane, targets in successors.items()}


def _adjacent_lanes(nodes: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    by_edge: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for lane in nodes:
        edge, separator, index = lane.rpartition("_")
        if not separator:
            raise ValueError(f"unexpected SUMO lane id: {lane}")
        try:
            by_edge[edge].append((int(index), lane))
        except ValueError as exc:
            raise ValueError(f"unexpected lane index in {lane}") from exc

    result: dict[str, set[str]] = defaultdict(set)
    for lanes in by_edge.values():
        for index, lane in lanes:
            for other_index, other_lane in lanes:
                if abs(index - other_index) == 1:
                    result[lane].add(other_lane)
    return {lane: tuple(sorted(result[lane])) for lane in nodes}


def _next_target_lanes(
    source: str,
    *,
    successors: dict[str, tuple[str, ...]],
    target_nodes: set[str],
    max_hops: int,
) -> dict[str, tuple[int, str]]:
    """Find the first official incoming lane on each downstream path."""

    found: dict[str, tuple[int, str]] = {}
    queue: deque[tuple[str, int, str]] = deque()
    for target in successors.get(source, ()):
        queue.append((target, 1, target))
    visited: set[str] = {source}

    while queue:
        lane, hops, first_hop = queue.popleft()
        if lane in visited:
            continue
        visited.add(lane)
        if lane in target_nodes and lane != source:
            found.setdefault(lane, (hops, first_hop))
            continue
        if hops >= max_hops:
            continue
        for target in successors.get(lane, ()):
            queue.append((target, hops + 1, first_hop))
    return found


def build(
    *, tls_manifest: Path, net: Path, output: Path, report_dir: Path,
    max_hops: int = 4, lateral_weight: float = 0.25, transition_weight: float = 1.0,
) -> dict[str, object]:
    if max_hops < 1:
        raise ValueError("max_hops must be at least 1.")
    if lateral_weight <= 0 or transition_weight <= 0:
        raise ValueError("edge weights must be positive.")

    manifest = _read_manifest(tls_manifest)
    successors = _read_network_connections(net)
    nodes = manifest.nodes
    node_set = set(nodes)
    index = {lane: position for position, lane in enumerate(nodes)}
    lateral = _adjacent_lanes(nodes)

    adjacency_lateral = np.zeros((len(nodes), len(nodes)), dtype=np.float32)
    adjacency_direct = np.zeros_like(adjacency_lateral)
    adjacency_next = np.zeros_like(adjacency_lateral)
    lane_records: list[dict[str, object]] = []
    csv_rows: list[dict[str, object]] = []

    for source in nodes:
        source_index = index[source]
        source_owners = ";".join(manifest.owners[source])
        direct_downstream = successors.get(source, ())
        for target in lateral[source]:
            adjacency_lateral[source_index, index[target]] = lateral_weight
            csv_rows.append({
                "source_lane_id": source, "source_intersections": source_owners,
                "relation": "adjacent", "target_lane_id": target,
                "target_intersections": ";".join(manifest.owners[target]),
                "hops": 0, "weight": lateral_weight, "movements": "",
            })

        direct_targets = []
        for target in direct_downstream:
            if target not in node_set or target == source:
                continue
            adjacency_direct[source_index, index[target]] = transition_weight
            direct_targets.append(target)
            csv_rows.append({
                "source_lane_id": source, "source_intersections": source_owners,
                "relation": "direct_transition", "target_lane_id": target,
                "target_intersections": ";".join(manifest.owners[target]),
                "hops": 1, "weight": transition_weight,
                "movements": ";".join(manifest.movements.get((source, target), ("unknown",))),
            })

        next_targets = _next_target_lanes(
            source, successors=successors, target_nodes=node_set, max_hops=max_hops,
        )
        next_target_records = []
        for target, (hops, first_hop) in sorted(next_targets.items()):
            adjacency_next[source_index, index[target]] = transition_weight
            next_target_records.append({"lane_id": target, "hops": hops, "first_hop_lane": first_hop})
            csv_rows.append({
                "source_lane_id": source, "source_intersections": source_owners,
                "relation": "next_target", "target_lane_id": target,
                "target_intersections": ";".join(manifest.owners[target]),
                "hops": hops, "weight": transition_weight,
                "movements": ";".join(manifest.movements.get((source, first_hop), ("unknown",))),
            })

        lane_records.append({
            "lane_id": source,
            "official_intersections": manifest.owners[source],
            "adjacent_lanes": list(lateral[source]),
            "direct_downstream_lanes": list(direct_downstream),
            "direct_target_lanes": direct_targets,
            "next_target_lanes": next_target_records,
        })

    directed = np.maximum(adjacency_direct, adjacency_next)
    adjacency = np.maximum(np.maximum(adjacency_lateral, directed), directed.T)
    np.fill_diagonal(adjacency, 1.0)
    np.fill_diagonal(directed, 1.0)

    output.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        nodes=np.asarray(nodes),
        adjacency=adjacency,
        adjacency_directed=directed,
        adjacency_lateral=adjacency_lateral,
        adjacency_direct_transition=adjacency_direct,
        adjacency_next_target=adjacency_next,
    )
    summary = {
        "node_count": len(nodes),
        "node_definition": "official20 incoming lanes",
        "max_hops": max_hops,
        "lateral_weight": lateral_weight,
        "transition_weight": transition_weight,
        "stgcn_symmetric_edges": int(np.count_nonzero(np.triu(adjacency, k=1))),
        "directed_transition_edges": int(np.count_nonzero(adjacency_direct)),
        "next_target_edges": int(np.count_nonzero(adjacency_next)),
        "lateral_edges": int(np.count_nonzero(np.triu(adjacency_lateral, k=1))),
        "nodes_with_next_target": sum(bool(item["next_target_lanes"]) for item in lane_records),
        "artifacts": {
            "adjacency": str(output),
            "topology_json": str(report_dir / "official20_lane_topology.json"),
            "topology_csv": str(report_dir / "official20_lane_topology.csv"),
        },
    }
    topology_path = report_dir / "official20_lane_topology.json"
    topology_path.write_text(
        json.dumps({"summary": summary, "lanes": lane_records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (report_dir / "official20_lane_topology.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=(
            "source_lane_id", "source_intersections", "relation", "target_lane_id",
            "target_intersections", "hops", "weight", "movements",
        ))
        writer.writeheader()
        writer.writerows(csv_rows)
    (report_dir / "official20_lane_adjacency_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the official20 lane-level STGCN graph from a SUMO network.")
    parser.add_argument("--tls-manifest", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--max-hops", type=int, default=4)
    parser.add_argument("--lateral-weight", type=float, default=0.25)
    parser.add_argument("--transition-weight", type=float, default=1.0)
    args = parser.parse_args(argv)
    print(json.dumps(build(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
