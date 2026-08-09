"""Build an auditable traffic-light-junction graph from a SUMO network.

The graph nodes are exactly the TLS junction IDs in the manifest. A directed
edge A -> B exists when a SUMO lane transition starting from an incoming lane
of junction A reaches an incoming lane of junction B before reaching another
traffic-light junction. The STGCN archive also contains the symmetric
lane-transition graph with self-loops; no spatial or road-level fallback is
silently introduced.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import hashlib
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np


DEFAULT_EXPECTED_COUNT = 100
DEFAULT_MAX_HOPS = 128


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _node_order_sha256(nodes: tuple[str, ...]) -> str:
    encoded = json.dumps(nodes, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _lane_id(edge_id: str, lane_index: str) -> str:
    return f"{edge_id}_{lane_index}"


def _load_manifest(
    manifest_path: Path, expected_count: int
) -> tuple[tuple[str, ...], dict[str, str], dict[str, str], dict[str, object]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes = tuple(str(node) for node in payload.get("nodes", ()))
    if len(nodes) != expected_count or len(set(nodes)) != expected_count:
        raise ValueError(
            f"manifest must contain exactly {expected_count} unique nodes; found {len(nodes)}"
        )
    if tuple(sorted(nodes)) != nodes:
        raise ValueError("manifest nodes must be in stable dictionary order")
    recorded_hash = payload.get("node_order_sha256")
    if recorded_hash and recorded_hash != _node_order_sha256(nodes):
        raise ValueError("manifest node_order_sha256 does not match nodes")

    junctions = payload.get("junctions")
    if not isinstance(junctions, dict):
        raise ValueError("manifest must contain a junctions object")
    lane_to_node: dict[str, str] = {}
    for node in nodes:
        entry = junctions.get(node)
        if not isinstance(entry, dict):
            raise ValueError(f"manifest has no entry for node {node!r}")
        incoming = entry.get("incoming_lanes")
        if not isinstance(incoming, list) or not incoming:
            raise ValueError(f"manifest node {node!r} has no incoming_lanes")
        for lane in incoming:
            lane_id = str(lane)
            previous = lane_to_node.setdefault(lane_id, node)
            if previous != node:
                raise ValueError(
                    f"incoming lane {lane_id!r} belongs to {previous!r} and {node!r}"
                )

    tl_to_junction_payload = payload.get("tl_to_junction", {})
    if not isinstance(tl_to_junction_payload, dict):
        raise ValueError("manifest tl_to_junction must be an object")
    tl_to_junction = {
        str(tl_id): str(junction_id)
        for tl_id, junction_id in tl_to_junction_payload.items()
    }
    if any(junction_id not in nodes for junction_id in tl_to_junction.values()):
        raise ValueError("manifest tl_to_junction contains an unknown junction")
    return nodes, lane_to_node, tl_to_junction, payload


def _read_network_connections(
    net_path: Path,
    *,
    lane_to_node: dict[str, str],
    tl_to_junction: dict[str, str],
) -> tuple[dict[str, tuple[str, ...]], dict[str, object]]:
    successors: dict[str, set[str]] = defaultdict(set)
    signal_source_lanes: set[str] = set()
    unknown_tls: set[str] = set()
    mismatched_signal_sources: list[dict[str, str]] = []
    connection_count = 0
    external_source_connection_count = 0
    external_to_external_connection_count = 0
    signal_connection_count = 0

    for _, element in ET.iterparse(net_path, events=("end",)):
        if _local_tag(element.tag) != "connection":
            element.clear()
            continue
        from_edge = element.get("from")
        to_edge = element.get("to")
        from_lane = element.get("fromLane")
        to_lane = element.get("toLane")
        if None in (from_edge, to_edge, from_lane, to_lane):
            raise ValueError("network connection is missing from/to/fromLane/toLane")
        source_lane = _lane_id(str(from_edge), str(from_lane))
        target_lane = _lane_id(str(to_edge), str(to_lane))
        successors[source_lane].add(target_lane)
        connection_count += 1
        if not str(from_edge).startswith(":"):
            external_source_connection_count += 1
        if not str(from_edge).startswith(":") and not str(to_edge).startswith(":"):
            external_to_external_connection_count += 1

        tl_id = element.get("tl")
        if tl_id is not None and not str(from_edge).startswith(":"):
            signal_connection_count += 1
            signal_source_lanes.add(source_lane)
            if tl_id not in tl_to_junction:
                unknown_tls.add(tl_id)
            else:
                owner = lane_to_node.get(source_lane)
                if owner != tl_to_junction[tl_id]:
                    mismatched_signal_sources.append(
                        {
                            "tl": tl_id,
                            "source_lane": source_lane,
                            "manifest_owner": owner or "",
                            "manifest_junction": tl_to_junction[tl_id],
                        }
                    )
        element.clear()

    if unknown_tls:
        raise ValueError(
            "network contains connection@tl identifiers absent from the manifest: "
            f"{sorted(unknown_tls)}"
        )
    if mismatched_signal_sources:
        raise ValueError(
            "connection@tl source lanes disagree with the manifest: "
            f"{mismatched_signal_sources[:10]}"
        )
    if not successors:
        raise ValueError(f"no lane transitions found in {net_path}")

    return (
        {lane: tuple(sorted(targets)) for lane, targets in successors.items()},
        {
            "connection_count": connection_count,
            "external_source_connection_count": external_source_connection_count,
            "external_to_external_connection_count": external_to_external_connection_count,
            "signal_connection_count": signal_connection_count,
            "signal_source_lane_count": len(signal_source_lanes),
        },
    )


def _next_target_junctions(
    *,
    source_lane: str,
    source_junction: str,
    successors: dict[str, tuple[str, ...]],
    lane_to_node: dict[str, str],
    max_hops: int,
) -> tuple[dict[str, tuple[int, set[str], set[str]]], bool]:
    """Find first downstream TLS junctions for one source incoming lane.

    The search is over SUMO lane transitions. Once any manifest incoming lane
    is reached, that path stops, so a path cannot jump through an intermediate
    traffic-light junction.
    """

    queue: deque[tuple[str, int, str]] = deque(
        (target, 1, target) for target in successors.get(source_lane, ())
    )
    visited: set[str] = {source_lane}
    found: dict[str, tuple[int, set[str], set[str]]] = {}
    truncated = False

    while queue:
        lane, hops, first_target = queue.popleft()
        if lane in visited:
            continue
        visited.add(lane)

        target_junction = lane_to_node.get(lane)
        if target_junction is not None:
            if target_junction != source_junction:
                if target_junction not in found:
                    found[target_junction] = (hops, set(), set())
                min_hops, source_lanes, target_lanes = found[target_junction]
                found[target_junction] = (
                    min(min_hops, hops),
                    source_lanes | {source_lane},
                    target_lanes | {lane},
                )
            continue

        if hops >= max_hops:
            truncated = True
            continue
        for target in successors.get(lane, ()):
            queue.append((target, hops + 1, first_target))

    return found, truncated


def _union_find_component_count(adjacency: np.ndarray) -> tuple[int, int]:
    count = int(adjacency.shape[0])
    parent = list(range(count))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(count):
        for right in range(left + 1, count):
            if adjacency[left, right] > 0 or adjacency[right, left] > 0:
                union(left, right)
    return len({find(index) for index in range(count)}), sum(
        int(np.count_nonzero(adjacency[index]) == 0) for index in range(count)
    )


def _write_json(path: Path, payload: dict[str, object], *, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing report {path}")
    partial = Path(str(path) + ".partial")
    if partial.exists():
        raise FileExistsError(f"partial report already exists: {partial}")
    partial.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + chr(10),
        encoding="utf-8",
    )
    os.replace(partial, path)


def build(
    *,
    tls_manifest: Path,
    net: Path,
    output: Path,
    report_dir: Path,
    expected_count: int = DEFAULT_EXPECTED_COUNT,
    max_hops: int = DEFAULT_MAX_HOPS,
    force: bool = False,
) -> dict[str, object]:
    if not net.is_file():
        raise FileNotFoundError(net)
    if not tls_manifest.is_file():
        raise FileNotFoundError(tls_manifest)
    if max_hops < 1:
        raise ValueError("max_hops must be positive")

    nodes, lane_to_node, tl_to_junction, manifest_payload = _load_manifest(
        tls_manifest, expected_count
    )
    successors, network_summary = _read_network_connections(
        net, lane_to_node=lane_to_node, tl_to_junction=tl_to_junction
    )

    node_index = {node: index for index, node in enumerate(nodes)}
    directed = np.zeros((expected_count, expected_count), dtype=np.float32)
    edge_stats: dict[tuple[str, str], dict[str, object]] = {}
    source_lane_without_target = 0
    source_lane_truncated = 0
    source_lane_count = 0

    for source_node in nodes:
        incoming_lanes = manifest_payload["junctions"][source_node]["incoming_lanes"]
        if not isinstance(incoming_lanes, list):
            raise ValueError(f"manifest node {source_node!r} has invalid incoming_lanes")
        for source_lane in incoming_lanes:
            source_lane_count += 1
            found, truncated = _next_target_junctions(
                source_lane=str(source_lane),
                source_junction=source_node,
                successors=successors,
                lane_to_node=lane_to_node,
                max_hops=max_hops,
            )
            if not found:
                source_lane_without_target += 1
            if truncated:
                source_lane_truncated += 1
            for target_node, (min_hops, source_lanes, target_lanes) in found.items():
                directed[node_index[source_node], node_index[target_node]] = 1.0
                key = (source_node, target_node)
                item = edge_stats.setdefault(
                    key,
                    {
                        "source_node": source_node,
                        "target_node": target_node,
                        "lane_path_count": 0,
                        "min_hops": min_hops,
                        "source_lane_samples": set(),
                        "target_lane_samples": set(),
                    },
                )
                item["lane_path_count"] = int(item["lane_path_count"]) + len(target_lanes)
                item["min_hops"] = min(int(item["min_hops"]), min_hops)
                item["source_lane_samples"].update(source_lanes)
                item["target_lane_samples"].update(target_lanes)

    symmetric_without_self = np.maximum(directed, directed.T)
    adjacency = symmetric_without_self.copy()
    np.fill_diagonal(adjacency, 1.0)

    component_count, isolated_node_count = _union_find_component_count(symmetric_without_self)
    edge_records: list[dict[str, object]] = []
    for key in sorted(edge_stats):
        item = edge_stats[key]
        edge_records.append(
            {
                "source_node": item["source_node"],
                "target_node": item["target_node"],
                "lane_path_count": item["lane_path_count"],
                "min_hops": item["min_hops"],
                "source_lane_samples": sorted(item["source_lane_samples"])[:20],
                "target_lane_samples": sorted(item["target_lane_samples"])[:20],
            }
        )

    summary: dict[str, object] = {
        "schema_version": 1,
        "node_definition": "SUMO traffic_light junction",
        "graph_definition": (
            "directed lane-transition paths between TLS junctions; "
            "symmetric STGCN graph is max(directed, directed.T) plus self-loops"
        ),
        "node_count": len(nodes),
        "nodes": list(nodes),
        "node_order_sha256": _node_order_sha256(nodes),
        "incoming_lane_count": source_lane_count,
        "directed_edge_count": int(np.count_nonzero(directed)),
        "symmetric_edge_count_without_self_loops": int(
            np.count_nonzero(np.triu(symmetric_without_self, k=1))
        ),
        "stgcn_edge_count_including_self_loops": int(np.count_nonzero(adjacency)),
        "component_count": component_count,
        "isolated_node_count": isolated_node_count,
        "source_lanes_without_downstream_tls": source_lane_without_target,
        "source_lanes_truncated_at_max_hops": source_lane_truncated,
        "max_hops": max_hops,
        "fallback": "none",
        "network": network_summary,
        "source_net": str(net.resolve()),
        "source_net_sha256": _sha256(net),
        "manifest": str(tls_manifest.resolve()),
        "manifest_sha256": _sha256(tls_manifest),
        "edge_records": edge_records,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    if output.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing adjacency {output}")
    output_partial = Path(str(output) + ".partial.npz")
    if output_partial.exists():
        raise FileExistsError(f"partial adjacency already exists: {output_partial}")
    np.savez_compressed(
        output_partial,
        nodes=np.asarray(nodes),
        adjacency=adjacency,
        adjacency_directed=directed,
    )
    os.replace(output_partial, output)

    _write_json(
        report_dir / "tls100_junction_adjacency_summary.json",
        summary,
        force=force,
    )
    _write_json(
        report_dir / "tls100_junction_topology.json",
        {"summary": summary, "edges": edge_records},
        force=force,
    )
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build a traffic-light junction graph for TLS100 STGCN."
    )
    parser.add_argument("--tls-manifest", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=DEFAULT_EXPECTED_COUNT)
    parser.add_argument("--max-hops", type=int, default=DEFAULT_MAX_HOPS)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(build(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
