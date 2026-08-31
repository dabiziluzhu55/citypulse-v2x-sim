"""Build the deterministic lane-to-junction hierarchy contract.

The lane206 task has a frozen lane order, while the next model needs one
stable owner junction for every lane.  This module derives that ownership from
the official TLS manifest, validates it against the prepared dataset metadata,
and creates the fixed pooling/broadcast matrices used by the hierarchical
model.

The pooling matrix is a row-normalized uniform mean: each junction receives
the mean hidden representation of its incoming lanes.  The broadcast matrix
is a one-hot membership matrix used to send a junction representation back to
all of its lanes.  The junction graph is derived only from the existing
direct/downstream lane relations; no new lane-level edges are invented.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from ...build_dynamic_lane_graph import nodes_sha256


EXPECTED_LANE_COUNT = 206
EXPECTED_JUNCTION_COUNT = 20


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def _sequence_sha256(values: tuple[str, ...] | list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _bytes_sha256(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    return hashlib.sha256(contiguous.tobytes(order="C")).hexdigest()


def _mapping_sha256(
    lane_order: tuple[str, ...], junction_order: tuple[str, ...], owner_index: np.ndarray
) -> str:
    lines = [
        f"{lane}\t{junction_order[int(index)]}\t{int(index)}"
        for lane, index in zip(lane_order, owner_index.tolist())
    ]
    return _sequence_sha256(lines)


def _load_lane_order(metadata_path: Path) -> tuple[str, ...]:
    metadata = _read_json(metadata_path)
    lanes = tuple(str(value) for value in metadata.get("lanes", ()))
    if len(lanes) != EXPECTED_LANE_COUNT:
        raise ValueError(
            f"dataset metadata must contain {EXPECTED_LANE_COUNT} lanes, got {len(lanes)}"
        )
    if len(set(lanes)) != len(lanes):
        raise ValueError("dataset metadata lane order contains duplicates")
    if tuple(sorted(lanes)) != lanes:
        raise ValueError("dataset metadata lane order must be lexical and stable")
    return lanes


def _load_manifest(
    manifest_path: Path, lane_order: tuple[str, ...]
) -> tuple[tuple[str, ...], dict[str, str]]:
    payload = _read_json(manifest_path)
    intersections = payload.get("intersections")
    if not isinstance(intersections, dict) or len(intersections) != EXPECTED_JUNCTION_COUNT:
        raise ValueError(
            f"TLS manifest must contain exactly {EXPECTED_JUNCTION_COUNT} intersections"
        )

    junction_order = tuple(sorted(str(value) for value in intersections))
    if len(set(junction_order)) != EXPECTED_JUNCTION_COUNT:
        raise ValueError("TLS manifest intersection IDs must be unique")

    owner: dict[str, str] = {}
    for junction_id in junction_order:
        item = intersections[junction_id]
        if not isinstance(item, dict):
            raise ValueError(f"intersection {junction_id!r} must be an object")
        incoming = item.get("incoming_lanes")
        if isinstance(incoming, dict):
            lane_groups = incoming.values()
        elif isinstance(incoming, list):
            lane_groups = (incoming,)
        else:
            raise ValueError(f"intersection {junction_id!r} lacks incoming_lanes")

        for group in lane_groups:
            if not isinstance(group, list):
                raise ValueError(f"intersection {junction_id!r} has an invalid lane group")
            for lane_value in group:
                lane = str(lane_value)
                previous = owner.get(lane)
                if previous is not None:
                    raise ValueError(
                        f"lane {lane!r} is assigned to both {previous!r} and {junction_id!r}"
                    )
                owner[lane] = junction_id

    expected = set(lane_order)
    actual = set(owner)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={missing[:5]}")
        if extra:
            details.append(f"extra={extra[:5]}")
        raise ValueError("TLS manifest lane set differs from dataset metadata: " + ", ".join(details))
    return junction_order, owner


def _load_lane_graph(
    graph_path: Path,
    lane_order: tuple[str, ...],
    junction_order: tuple[str, ...],
    owner: dict[str, str],
) -> tuple[np.ndarray, np.ndarray, str]:
    with np.load(graph_path, allow_pickle=False) as archive:
        if "nodes" not in archive:
            raise ValueError(f"lane graph lacks nodes: {graph_path}")
        graph_nodes = tuple(str(value) for value in archive["nodes"].tolist())
        if graph_nodes != lane_order:
            raise ValueError("lane graph node order differs from dataset metadata")

        relation_matrices = []
        for name in ("adjacency_direct_transition", "adjacency_next_target"):
            if name not in archive:
                continue
            matrix = np.asarray(archive[name], dtype=np.float32)
            if matrix.shape != (len(lane_order), len(lane_order)):
                raise ValueError(f"{name} has incompatible shape {matrix.shape}")
            if not np.isfinite(matrix).all() or np.any(matrix < 0):
                raise ValueError(f"{name} contains invalid values")
            relation_matrices.append(matrix)
        if not relation_matrices:
            raise ValueError(
                "lane graph must contain adjacency_direct_transition or adjacency_next_target"
            )

    directed_lane = np.maximum.reduce(relation_matrices)
    junction_index = {junction: index for index, junction in enumerate(junction_order)}
    directed = np.zeros((len(junction_order), len(junction_order)), dtype=np.float32)
    for source, target in zip(*np.nonzero(directed_lane > 0)):
        source_junction = junction_index[owner[lane_order[int(source)]]]
        target_junction = junction_index[owner[lane_order[int(target)]]]
        if source_junction != target_junction:
            directed[source_junction, target_junction] = 1.0

    adjacency = np.maximum(directed, directed.T)
    np.fill_diagonal(adjacency, 1.0)
    return directed, adjacency, _sha256_file(graph_path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_lane_junction_mapping(path: Path) -> dict[str, object]:
    """Load and validate a generated hierarchy mapping archive."""

    required = (
        "lane_order",
        "junction_order",
        "lane_to_junction_index",
        "pooling_matrix",
        "broadcast_matrix",
        "junction_directed_adjacency",
        "junction_adjacency",
        "lane_order_sha256",
        "junction_order_sha256",
        "mapping_sha256",
        "pooling_matrix_sha256",
        "junction_adjacency_sha256",
    )
    with np.load(path, allow_pickle=False) as archive:
        missing = [name for name in required if name not in archive]
        if missing:
            raise ValueError(f"hierarchy mapping lacks fields: {missing}")
        lane_order = tuple(str(value) for value in archive["lane_order"].tolist())
        junction_order = tuple(str(value) for value in archive["junction_order"].tolist())
        owner_index = np.asarray(archive["lane_to_junction_index"], dtype=np.int64)
        pooling = np.asarray(archive["pooling_matrix"], dtype=np.float32)
        broadcast = np.asarray(archive["broadcast_matrix"], dtype=np.float32)
        directed = np.asarray(archive["junction_directed_adjacency"], dtype=np.float32)
        adjacency = np.asarray(archive["junction_adjacency"], dtype=np.float32)
        stored = {
            name: str(archive[name].item())
            for name in required
            if name.endswith("sha256")
        }

    if len(lane_order) != EXPECTED_LANE_COUNT or len(set(lane_order)) != len(lane_order):
        raise ValueError("hierarchy mapping must contain 206 unique lanes")
    if len(junction_order) != EXPECTED_JUNCTION_COUNT or len(set(junction_order)) != len(junction_order):
        raise ValueError("hierarchy mapping must contain 20 unique junctions")
    if tuple(sorted(lane_order)) != lane_order:
        raise ValueError("hierarchy lane order is not lexical")
    if owner_index.shape != (len(lane_order),):
        raise ValueError("lane_to_junction_index has an incompatible shape")
    if np.any(owner_index < 0) or np.any(owner_index >= len(junction_order)):
        raise ValueError("lane_to_junction_index contains an invalid junction index")
    if pooling.shape != (len(junction_order), len(lane_order)):
        raise ValueError("pooling_matrix has an incompatible shape")
    if broadcast.shape != (len(lane_order), len(junction_order)):
        raise ValueError("broadcast_matrix has an incompatible shape")
    if directed.shape != (len(junction_order), len(junction_order)):
        raise ValueError("junction_directed_adjacency has an incompatible shape")
    if adjacency.shape != (len(junction_order), len(junction_order)):
        raise ValueError("junction_adjacency has an incompatible shape")

    expected_pooling = np.zeros_like(pooling)
    expected_broadcast = np.zeros_like(broadcast)
    counts = np.bincount(owner_index, minlength=len(junction_order)).astype(np.float32)
    for lane_index, junction_index in enumerate(owner_index.tolist()):
        expected_pooling[junction_index, lane_index] = 1.0 / counts[junction_index]
        expected_broadcast[lane_index, junction_index] = 1.0
    if not np.allclose(pooling, expected_pooling, atol=1e-7):
        raise ValueError("pooling_matrix is not the deterministic uniform mean")
    if not np.array_equal(broadcast, expected_broadcast):
        raise ValueError("broadcast_matrix does not match lane ownership")
    if not np.isfinite(adjacency).all() or np.any(adjacency < 0):
        raise ValueError("junction_adjacency contains invalid values")
    if not np.allclose(adjacency, adjacency.T, atol=1e-6):
        raise ValueError("junction_adjacency must be symmetric")
    if not np.all(np.diag(adjacency) > 0):
        raise ValueError("junction_adjacency must contain positive self-loops")

    calculated = {
        "lane_order_sha256": nodes_sha256(lane_order),
        "junction_order_sha256": _sequence_sha256(junction_order),
        "mapping_sha256": _mapping_sha256(lane_order, junction_order, owner_index),
        "pooling_matrix_sha256": _bytes_sha256(pooling),
        "junction_adjacency_sha256": _bytes_sha256(adjacency),
    }
    for name, expected in calculated.items():
        if stored[name] != expected:
            raise ValueError(f"{name} does not match mapping contents")

    return {
        "lane_order": lane_order,
        "junction_order": junction_order,
        "lane_to_junction_index": owner_index,
        "pooling_matrix": pooling,
        "broadcast_matrix": broadcast,
        "junction_directed_adjacency": directed,
        "junction_adjacency": adjacency,
        **calculated,
    }


def build_mapping(
    *,
    metadata: Path,
    tls_manifest: Path,
    lane_graph: Path,
    output: Path,
    report: Path | None = None,
) -> dict[str, object]:
    lane_order = _load_lane_order(metadata)
    junction_order, owner = _load_manifest(tls_manifest, lane_order)
    directed, adjacency, graph_sha256 = _load_lane_graph(
        lane_graph, lane_order, junction_order, owner
    )
    junction_index = {junction: index for index, junction in enumerate(junction_order)}
    owner_index = np.asarray(
        [junction_index[owner[lane]] for lane in lane_order], dtype=np.int64
    )
    counts = np.bincount(owner_index, minlength=len(junction_order)).astype(np.float32)
    pooling = np.zeros((len(junction_order), len(lane_order)), dtype=np.float32)
    broadcast = np.zeros((len(lane_order), len(junction_order)), dtype=np.float32)
    for lane_index, junction_id in enumerate(owner_index.tolist()):
        pooling[junction_id, lane_index] = 1.0 / counts[junction_id]
        broadcast[lane_index, junction_id] = 1.0

    hashes = {
        "lane_order_sha256": nodes_sha256(lane_order),
        "junction_order_sha256": _sequence_sha256(junction_order),
        "mapping_sha256": _mapping_sha256(lane_order, junction_order, owner_index),
        "pooling_matrix_sha256": _bytes_sha256(pooling),
        "junction_adjacency_sha256": _bytes_sha256(adjacency),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        lane_order=np.asarray(lane_order),
        junction_order=np.asarray(junction_order),
        lane_to_junction_index=owner_index,
        pooling_matrix=pooling,
        broadcast_matrix=broadcast,
        junction_directed_adjacency=directed,
        junction_adjacency=adjacency,
        **{name: np.asarray(value) for name, value in hashes.items()},
    )

    junction_records = []
    for index, junction in enumerate(junction_order):
        lanes = [lane for lane in lane_order if owner[lane] == junction]
        junction_records.append(
            {"junction_id": junction, "index": index, "lane_count": len(lanes), "lanes": lanes}
        )
    result: dict[str, object] = {
        "artifact_type": "official20_lane206_hierarchy_mapping_v1",
        "lane_count": len(lane_order),
        "junction_count": len(junction_order),
        "lane_order": list(lane_order),
        "junction_order": list(junction_order),
        "junctions": junction_records,
        "lane_to_junction": {lane: owner[lane] for lane in lane_order},
        "pooling": "uniform row-normalized mean over incoming lanes",
        "broadcast": "one-hot membership broadcast to owning junction",
        "lane_graph_sha256": graph_sha256,
        "junction_directed_edge_count": int(np.count_nonzero(directed)),
        "junction_symmetric_edge_count": int(np.count_nonzero(np.triu(adjacency, k=1))),
        "hashes": hashes,
        "artifacts": {"mapping": str(output.resolve())},
    }
    if report is None:
        report = output.with_suffix(".json")
    report.parent.mkdir(parents=True, exist_ok=True)
    result["artifacts"] = {"mapping": str(output.resolve()), "report": str(report.resolve())}
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_lane_junction_mapping(output)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the deterministic official20 lane206 lane-junction hierarchy mapping."
    )
    parser.add_argument("--metadata", type=Path, required=True, help="Prepared lane206 metadata.json.")
    parser.add_argument("--tls-manifest", type=Path, required=True, help="Official20 TLS manifest JSON.")
    parser.add_argument("--lane-graph", type=Path, required=True, help="Existing lane206 graph NPZ.")
    parser.add_argument("--output", type=Path, required=True, help="Mapping NPZ to create.")
    parser.add_argument("--report", type=Path, help="Optional mapping report JSON path.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    print(json.dumps(build_mapping(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
