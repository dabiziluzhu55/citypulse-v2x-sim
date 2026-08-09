"""Build fixed downstream/upstream message graphs for lane forecasting.

The official lane graph preserves directional transition provenance, but the
static Chebyshev control consumes a symmetric adjacency.  This module keeps
the control graph untouched and extracts the directed transition/next-target
relations as two receiver-by-sender matrices:

* ``downstream`` sends upstream lane state to the downstream receiver;
* ``upstream`` is its transpose and exposes downstream queue spillback.

The graph is derived only from the SUMO topology archive, never from future
traffic observations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Mapping

import numpy as np

from .build_dynamic_lane_graph import load_sparse_graph, sha256_file


RELATION_DIRECT_TRANSITION = 2
RELATION_NEXT_TARGET = 4
DIRECTED_RELATIONS = RELATION_DIRECT_TRANSITION | RELATION_NEXT_TARGET


def nodes_sha256(nodes: tuple[str, ...] | list[str]) -> str:
    return hashlib.sha256("\n".join(nodes).encode("utf-8")).hexdigest()


def _row_normalize(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    degree = matrix.sum(axis=1)
    normalized = np.zeros_like(matrix)
    positive = degree > 0
    normalized[positive] = matrix[positive] / degree[positive, None]
    return normalized


def _validate_matrix(matrix: np.ndarray, *, name: str, size: int) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.shape != (size, size):
        raise ValueError(f"{name} has shape {matrix.shape}; expected {(size, size)}")
    if not np.isfinite(matrix).all() or np.any(matrix < 0):
        raise ValueError(f"{name} contains invalid weights")
    return matrix


def _load_next_target_hops(topology_csv: Path | None) -> dict[tuple[str, str], int]:
    """Load the hop count for each topology ``next_target`` pair."""

    if topology_csv is None:
        return {}
    with topology_csv.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"source_lane_id", "target_lane_id", "relation", "hops"}
        if not required.issubset(set(reader.fieldnames or ())):
            raise ValueError(
                f"topology CSV must contain {sorted(required)}: {topology_csv}"
            )
        hops: dict[tuple[str, str], int] = {}
        for row in reader:
            if row.get("relation") != "next_target":
                continue
            source = str(row.get("source_lane_id", ""))
            target = str(row.get("target_lane_id", ""))
            try:
                value = int(row.get("hops", ""))
            except ValueError as exc:
                raise ValueError(f"invalid next_target hops in {topology_csv}: {row}") from exc
            if not source or not target or value < 1:
                raise ValueError(f"invalid next_target row in {topology_csv}: {row}")
            key = (source, target)
            hops[key] = min(value, hops.get(key, value))
    return hops


def build_directional_graph(
    *,
    candidate_graph: Path,
    output: Path,
    report: Path | None = None,
    topology_csv: Path | None = None,
    hop_decay: float = 1.0,
) -> dict[str, object]:
    """Extract direction-aware lane message matrices from a candidate graph."""

    if not 0 < hop_decay <= 1:
        raise ValueError("hop_decay must be in (0, 1]")

    graph = load_sparse_graph(candidate_graph)
    nodes = tuple(graph["nodes"])  # type: ignore[arg-type]
    source = np.asarray(graph["source_index"], dtype=np.int64)
    target = np.asarray(graph["target_index"], dtype=np.int64)
    weight = np.asarray(graph["static_weight"], dtype=np.float32)
    relation = np.asarray(graph["edge_type"], dtype=np.uint8)
    direction = np.asarray(graph["edge_direction"], dtype=np.int8)
    self_loop = np.asarray(graph["is_self_loop"], dtype=np.bool_)
    n = len(nodes)
    if n != 206:
        raise ValueError(f"directional lane graph requires 206 nodes, got {n}")

    hop_lookup = _load_next_target_hops(topology_csv)
    # Matrices use [receiver, sender] orientation, matching torch message
    # passing: downstream[target, source] receives upstream state.
    downstream_direct = np.zeros((n, n), dtype=np.float32)
    downstream_next_target = np.zeros((n, n), dtype=np.float32)
    selected_edges = 0
    two_way_edges = 0
    direct_edges = 0
    next_target_edges = 0
    missing_hop_rows = 0
    selected_hops: list[int] = []
    for src, dst, edge_weight, edge_relation, edge_direction, is_self in zip(
        source.tolist(),
        target.tolist(),
        weight.tolist(),
        relation.tolist(),
        direction.tolist(),
        self_loop.tolist(),
    ):
        if is_self or not (edge_relation & DIRECTED_RELATIONS):
            continue
        # edge_direction describes whether this row follows the underlying
        # SUMO flow (1), opposes it (-1), or is bidirectional (2).
        if edge_direction not in (1, 2):
            continue
        if edge_relation & RELATION_DIRECT_TRANSITION:
            downstream_direct[dst, src] = max(
                downstream_direct[dst, src], float(edge_weight)
            )
            direct_edges += 1
        if edge_relation & RELATION_NEXT_TARGET:
            key = (nodes[src], nodes[dst])
            hops = hop_lookup.get(key, 1)
            if topology_csv is not None and key not in hop_lookup:
                missing_hop_rows += 1
            scale = float(hop_decay ** (hops - 1))
            downstream_next_target[dst, src] = max(
                downstream_next_target[dst, src], float(edge_weight) * scale
            )
            next_target_edges += 1
            selected_hops.append(hops)
        if edge_relation & DIRECTED_RELATIONS:
            selected_edges += 1
        two_way_edges += int(edge_direction == 2)

    if selected_edges == 0:
        raise ValueError("candidate graph contains no directed transition edges")
    downstream = np.maximum(downstream_direct, downstream_next_target)
    upstream_direct = downstream_direct.T.copy()
    upstream_next_target = downstream_next_target.T.copy()
    upstream = downstream.T.copy()
    downstream_normalized = _row_normalize(downstream)
    upstream_normalized = _row_normalize(upstream)
    node_hash = nodes_sha256(nodes)

    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        nodes=np.asarray(nodes),
        nodes_sha256=np.asarray(node_hash),
        candidate_graph_sha256=np.asarray(sha256_file(candidate_graph)),
        downstream_adjacency=downstream,
        upstream_adjacency=upstream,
        downstream_normalized=downstream_normalized,
        upstream_normalized=upstream_normalized,
        downstream_direct_adjacency=downstream_direct,
        downstream_next_target_adjacency=downstream_next_target,
        upstream_direct_adjacency=upstream_direct,
        upstream_next_target_adjacency=upstream_next_target,
        downstream_direct_normalized=_row_normalize(downstream_direct),
        downstream_next_target_normalized=_row_normalize(downstream_next_target),
        upstream_direct_normalized=_row_normalize(upstream_direct),
        upstream_next_target_normalized=_row_normalize(upstream_next_target),
        relation_branches=np.asarray(True),
        hop_decay=np.asarray(hop_decay, dtype=np.float32),
        topology_csv_sha256=np.asarray(
            sha256_file(topology_csv) if topology_csv is not None else ""
        ),
    )

    summary: dict[str, object] = {
        "node_count": n,
        "node_definition": "official20 incoming lanes",
        "nodes_sha256": node_hash,
        "candidate_graph_sha256": sha256_file(candidate_graph),
        "directed_relation_bits": {
            "2": "direct_transition",
            "4": "next_target",
        },
        "downstream_edge_count": int(np.count_nonzero(downstream)),
        "upstream_edge_count": int(np.count_nonzero(upstream)),
        "downstream_receiver_count": int(np.count_nonzero(downstream.sum(axis=1) > 0)),
        "upstream_receiver_count": int(np.count_nonzero(upstream.sum(axis=1) > 0)),
        "selected_candidate_rows": int(selected_edges),
        "bidirectional_candidate_rows": int(two_way_edges),
        "relation_branch_counts": {
            "direct_transition": int(np.count_nonzero(downstream_direct)),
            "next_target": int(np.count_nonzero(downstream_next_target)),
        },
        "selected_relation_rows": {
            "direct_transition": int(direct_edges),
            "next_target": int(next_target_edges),
        },
        "hop_decay": float(hop_decay),
        "topology_csv_sha256": (
            sha256_file(topology_csv) if topology_csv is not None else None
        ),
        "missing_next_target_hop_rows": int(missing_hop_rows),
        "next_target_hop_range": (
            [int(min(selected_hops)), int(max(selected_hops))]
            if selected_hops
            else None
        ),
        "artifacts": {"graph": str(output.resolve())},
    }
    if report is None:
        report = output.with_suffix(".json")
    report.parent.mkdir(parents=True, exist_ok=True)
    summary["artifacts"] = {
        "graph": str(output.resolve()),
        "report": str(report.resolve()),
    }
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def load_directional_graph(path: Path) -> dict[str, object]:
    """Load and validate a fixed directional lane graph.

    D0 archives contain only combined downstream/upstream matrices.  D2
    archives additionally contain relation-separated direct-transition and
    next-target matrices; accepting both keeps old controls reproducible.
    """

    branch_names = (
        "downstream_direct_adjacency",
        "downstream_next_target_adjacency",
        "upstream_direct_adjacency",
        "upstream_next_target_adjacency",
        "downstream_direct_normalized",
        "downstream_next_target_normalized",
        "upstream_direct_normalized",
        "upstream_next_target_normalized",
    )
    with np.load(path, allow_pickle=False) as archive:
        required = (
            "nodes",
            "nodes_sha256",
            "candidate_graph_sha256",
            "downstream_adjacency",
            "upstream_adjacency",
            "downstream_normalized",
            "upstream_normalized",
        )
        missing = [name for name in required if name not in archive]
        if missing:
            raise ValueError(f"directional graph archive lacks fields: {missing}")
        nodes = tuple(str(value) for value in archive["nodes"].tolist())
        stored_hash = str(archive["nodes_sha256"].item())
        candidate_hash = str(archive["candidate_graph_sha256"].item())
        n = len(nodes)
        downstream = _validate_matrix(
            archive["downstream_adjacency"], name="downstream_adjacency", size=n
        )
        upstream = _validate_matrix(
            archive["upstream_adjacency"], name="upstream_adjacency", size=n
        )
        downstream_normalized = _validate_matrix(
            archive["downstream_normalized"], name="downstream_normalized", size=n
        )
        upstream_normalized = _validate_matrix(
            archive["upstream_normalized"], name="upstream_normalized", size=n
        )
        present_branches = [name for name in branch_names if name in archive]
        if present_branches and len(present_branches) != len(branch_names):
            raise ValueError(
                "directional graph must contain all relation branch fields or none"
            )
        relation_branches = bool(present_branches)
        branch_arrays = (
            {
                name: _validate_matrix(archive[name], name=name, size=n)
                for name in branch_names
            }
            if relation_branches
            else {}
        )
        stored_hop_decay = (
            float(archive["hop_decay"].item()) if "hop_decay" in archive else None
        )
        topology_hash = (
            str(archive["topology_csv_sha256"].item())
            if "topology_csv_sha256" in archive
            else None
        )

    if n != 206 or not nodes or tuple(sorted(nodes)) != nodes or len(set(nodes)) != n:
        raise ValueError("directional graph nodes must be sorted, unique and 206 lanes")
    if stored_hash != nodes_sha256(nodes):
        raise ValueError("directional graph nodes_sha256 does not match nodes")
    if not np.allclose(upstream, downstream.T, atol=1e-6):
        raise ValueError("upstream_adjacency must be downstream_adjacency.T")
    if not np.allclose(
        downstream_normalized, _row_normalize(downstream), atol=1e-6
    ):
        raise ValueError("downstream_normalized is not row-normalized downstream adjacency")
    if not np.allclose(upstream_normalized, _row_normalize(upstream), atol=1e-6):
        raise ValueError("upstream_normalized is not row-normalized upstream adjacency")
    if relation_branches:
        combined_downstream = np.maximum(
            branch_arrays["downstream_direct_adjacency"],
            branch_arrays["downstream_next_target_adjacency"],
        )
        combined_upstream = np.maximum(
            branch_arrays["upstream_direct_adjacency"],
            branch_arrays["upstream_next_target_adjacency"],
        )
        if not np.allclose(downstream, combined_downstream, atol=1e-6):
            raise ValueError("downstream_adjacency is not the maximum of relation branches")
        if not np.allclose(upstream, combined_upstream, atol=1e-6):
            raise ValueError("upstream_adjacency is not the maximum of relation branches")
        for adjacency_name, normalized_name in (
            ("downstream_direct_adjacency", "downstream_direct_normalized"),
            ("downstream_next_target_adjacency", "downstream_next_target_normalized"),
            ("upstream_direct_adjacency", "upstream_direct_normalized"),
            ("upstream_next_target_adjacency", "upstream_next_target_normalized"),
        ):
            if not np.allclose(
                branch_arrays[normalized_name],
                _row_normalize(branch_arrays[adjacency_name]),
                atol=1e-6,
            ):
                raise ValueError(f"{normalized_name} is not row-normalized {adjacency_name}")
        if stored_hop_decay is not None and not 0 < stored_hop_decay <= 1:
            raise ValueError("directional graph hop_decay must be in (0, 1]")
    if not candidate_hash:
        raise ValueError("directional graph candidate_graph_sha256 is empty")
    result: dict[str, object] = {
        "nodes": nodes,
        "nodes_sha256": stored_hash,
        "candidate_graph_sha256": candidate_hash,
        "downstream_adjacency": downstream,
        "upstream_adjacency": upstream,
        "downstream_normalized": downstream_normalized,
        "upstream_normalized": upstream_normalized,
    }
    if relation_branches:
        result.update(branch_arrays)
        result["has_relation_branches"] = True
        result["hop_decay"] = stored_hop_decay
        result["topology_csv_sha256"] = topology_hash
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build fixed downstream/upstream lane message graphs."
    )
    parser.add_argument("--candidate-graph", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--topology-csv",
        type=Path,
        help="official20 topology CSV used to obtain next_target hop counts",
    )
    parser.add_argument(
        "--hop-decay",
        type=float,
        default=1.0,
        help="next_target weight multiplier per additional hop; default 1.0",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    print(json.dumps(build_directional_graph(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
