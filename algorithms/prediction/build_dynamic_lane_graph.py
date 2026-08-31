"""Build a sparse, auditable candidate graph for dynamic lane forecasting.

The current official20 lane graph is stored as dense matrices because the
reference STGCN consumes a fixed GSO.  A dynamic graph should not materialize
one dense 206 x 206 matrix per sample.  This module converts the existing
static archive into a directed message-edge list while preserving the
relation provenance of every candidate pair.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


RELATION_LATERAL = 1
RELATION_DIRECT_TRANSITION = 2
RELATION_NEXT_TARGET = 4
RELATION_NAMES = {
    RELATION_LATERAL: "lateral",
    RELATION_DIRECT_TRANSITION: "direct_transition",
    RELATION_NEXT_TARGET: "next_target",
}


def nodes_sha256(nodes: tuple[str, ...] | list[str]) -> str:
    """Return the canonical hash used by the lane206 data contract."""

    return hashlib.sha256("\n".join(nodes).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_matrix(archive: np.lib.npyio.NpzFile, name: str, size: int) -> np.ndarray:
    if name not in archive:
        raise ValueError(f"static graph archive lacks {name!r}")
    matrix = np.asarray(archive[name], dtype=np.float32)
    if matrix.shape != (size, size):
        raise ValueError(f"{name} has shape {matrix.shape}; expected {(size, size)}")
    if not np.isfinite(matrix).all() or np.any(matrix < 0):
        raise ValueError(f"{name} contains invalid weights")
    return matrix


def _load_static_graph(path: Path) -> tuple[tuple[str, ...], dict[str, np.ndarray]]:
    with np.load(path, allow_pickle=False) as archive:
        if "nodes" not in archive:
            raise ValueError(f"static graph archive lacks nodes: {path}")
        nodes = tuple(str(value) for value in archive["nodes"].tolist())
        if not nodes or len(nodes) != len(set(nodes)):
            raise ValueError("static graph nodes must be non-empty and unique")
        if tuple(sorted(nodes)) != nodes:
            raise ValueError("static graph nodes must use lexical lane-id order")
        size = len(nodes)
        matrices = {
            name: _as_matrix(archive, name, size)
            for name in (
                "adjacency",
                "adjacency_lateral",
                "adjacency_direct_transition",
                "adjacency_next_target",
            )
        }

    adjacency = matrices["adjacency"]
    if not np.allclose(adjacency, adjacency.T, atol=1e-6):
        raise ValueError("static adjacency must be symmetric for the lane206 baseline")
    if np.any(np.diag(adjacency) <= 0):
        raise ValueError("static adjacency must contain positive self-loops")
    return nodes, matrices


def _relation_mask(
    *, lateral: float, direct: float, next_target: float
) -> int:
    mask = 0
    if lateral > 0:
        mask |= RELATION_LATERAL
    if direct > 0:
        mask |= RELATION_DIRECT_TRANSITION
    if next_target > 0:
        mask |= RELATION_NEXT_TARGET
    return mask


def _relation_names(mask: int) -> list[str]:
    return [name for bit, name in RELATION_NAMES.items() if mask & bit]


def build_sparse_graph(
    *, adjacency: Path, output: Path, report: Path | None = None
) -> dict[str, object]:
    """Convert an official20 static graph into a sparse candidate archive.

    Non-self candidate pairs are represented in both message directions, as
    the current official20 STGCN uses the symmetric graph.  ``edge_direction``
    records whether the underlying lane topology has a directed relation in
    the current row (1), only in the reverse row (-1), in both rows (2), or is
    lateral-only (0).  Duplicate relation types on one pair are represented by
    a bit mask rather than duplicate message edges.
    """

    nodes, matrices = _load_static_graph(adjacency)
    n = len(nodes)
    static = matrices["adjacency"]
    lateral = matrices["adjacency_lateral"]
    direct = matrices["adjacency_direct_transition"]
    next_target = matrices["adjacency_next_target"]

    candidate_pairs = np.argwhere(np.triu(static > 0, k=1))
    source: list[int] = []
    target: list[int] = []
    weights: list[float] = []
    relation_masks: list[int] = []
    directions: list[int] = []
    self_loops: list[bool] = []

    for left, right in candidate_pairs.tolist():
        forward_mask = _relation_mask(
            lateral=float(lateral[left, right]),
            direct=float(direct[left, right]),
            next_target=float(next_target[left, right]),
        )
        reverse_mask = _relation_mask(
            lateral=float(lateral[right, left]),
            direct=float(direct[right, left]),
            next_target=float(next_target[right, left]),
        )
        relation_mask = forward_mask | reverse_mask
        if relation_mask == 0:
            raise ValueError(f"candidate pair {left}->{right} has no relation provenance")
        forward_directed = bool(
            forward_mask & (RELATION_DIRECT_TRANSITION | RELATION_NEXT_TARGET)
        )
        reverse_directed = bool(
            reverse_mask & (RELATION_DIRECT_TRANSITION | RELATION_NEXT_TARGET)
        )
        pair_weight = float(static[left, right])
        if pair_weight <= 0:
            raise ValueError(f"candidate pair {left}->{right} has non-positive weight")

        for row_source, row_target in ((left, right), (right, left)):
            if row_source == left:
                row_forward, row_reverse = forward_directed, reverse_directed
            else:
                row_forward, row_reverse = reverse_directed, forward_directed
            direction = 2 if row_forward and row_reverse else 1 if row_forward else -1 if row_reverse else 0
            source.append(row_source)
            target.append(row_target)
            weights.append(pair_weight)
            relation_masks.append(relation_mask)
            directions.append(direction)
            self_loops.append(False)

    for index in range(n):
        source.append(index)
        target.append(index)
        weights.append(1.0)
        relation_masks.append(0)
        directions.append(0)
        self_loops.append(True)

    order = np.lexsort((np.asarray(target), np.asarray(source)))
    source_array = np.asarray(source, dtype=np.int64)[order]
    target_array = np.asarray(target, dtype=np.int64)[order]
    weight_array = np.asarray(weights, dtype=np.float32)[order]
    relation_array = np.asarray(relation_masks, dtype=np.uint8)[order]
    direction_array = np.asarray(directions, dtype=np.int8)[order]
    self_loop_array = np.asarray(self_loops, dtype=np.bool_)[order]

    keys = list(zip(source_array.tolist(), target_array.tolist()))
    if len(keys) != len(set(keys)):
        raise ValueError("sparse candidate graph contains duplicate directed edges")
    if int(self_loop_array.sum()) != n:
        raise ValueError("sparse candidate graph must contain exactly one self-loop per node")

    node_hash = nodes_sha256(nodes)
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output,
        nodes=np.asarray(nodes),
        nodes_sha256=np.asarray(node_hash),
        source_index=source_array,
        target_index=target_array,
        static_weight=weight_array,
        edge_type=relation_array,
        relation_mask=relation_array,
        edge_direction=direction_array,
        is_self_loop=self_loop_array,
    )

    nonself = ~self_loop_array
    summary: dict[str, object] = {
        "node_count": n,
        "node_definition": "official20 incoming lanes",
        "nodes_sha256": node_hash,
        "source_static_graph_sha256": sha256_file(adjacency),
        "edge_count_including_self_loops": int(len(source_array)),
        "nonself_directed_edge_count": int(nonself.sum()),
        "self_loop_count": int(self_loop_array.sum()),
        "undirected_nonself_edge_count": int(candidate_pairs.shape[0]),
        "relation_counts": {
            name: int(np.count_nonzero(nonself & ((relation_array & bit) != 0)))
            for bit, name in RELATION_NAMES.items()
        },
        "artifacts": {"graph": str(output.resolve())},
    }
    if report is None:
        report = output.with_suffix(".json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary["artifacts"] = {"graph": str(output.resolve()), "report": str(report.resolve())}
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def load_sparse_graph(path: Path) -> dict[str, np.ndarray | tuple[str, ...] | str]:
    """Load and validate a sparse graph archive for model construction."""

    with np.load(path, allow_pickle=False) as archive:
        required = (
            "nodes",
            "nodes_sha256",
            "source_index",
            "target_index",
            "static_weight",
            "edge_type",
            "relation_mask",
            "edge_direction",
            "is_self_loop",
        )
        missing = [name for name in required if name not in archive]
        if missing:
            raise ValueError(f"sparse graph archive lacks fields: {missing}")
        nodes = tuple(str(value) for value in archive["nodes"].tolist())
        source = np.asarray(archive["source_index"], dtype=np.int64)
        target = np.asarray(archive["target_index"], dtype=np.int64)
        weight = np.asarray(archive["static_weight"], dtype=np.float32)
        edge_type = np.asarray(archive["edge_type"], dtype=np.uint8)
        relation_mask = np.asarray(archive["relation_mask"], dtype=np.uint8)
        direction = np.asarray(archive["edge_direction"], dtype=np.int8)
        is_self_loop = np.asarray(archive["is_self_loop"], dtype=np.bool_)
        stored_hash = str(archive["nodes_sha256"].item()) if "nodes_sha256" in archive else ""

    n = len(nodes)
    if not nodes or tuple(sorted(nodes)) != nodes or len(set(nodes)) != n:
        raise ValueError("sparse graph nodes must be sorted, unique and non-empty")
    if stored_hash and stored_hash != nodes_sha256(nodes):
        raise ValueError("sparse graph nodes_sha256 does not match nodes")
    lengths = {
        len(source), len(target), len(weight), len(edge_type), len(relation_mask),
        len(direction), len(is_self_loop),
    }
    if len(lengths) != 1:
        raise ValueError("sparse graph edge arrays have inconsistent lengths")
    if np.any(source < 0) or np.any(source >= n) or np.any(target < 0) or np.any(target >= n):
        raise ValueError("sparse graph contains out-of-range node indices")
    if not np.isfinite(weight).all() or np.any(weight <= 0):
        raise ValueError("sparse graph weights must be finite and positive")
    if np.any(edge_type > 0b111):
        raise ValueError("sparse graph contains an unknown relation bit")
    if np.any(~np.isin(direction, np.asarray([-1, 0, 1, 2], dtype=np.int8))):
        raise ValueError("sparse graph contains an unknown edge direction")
    pairs = list(zip(source.tolist(), target.tolist()))
    if len(pairs) != len(set(pairs)):
        raise ValueError("sparse graph contains duplicate directed edges")
    if int(is_self_loop.sum()) != n or not np.all(is_self_loop == (source == target)):
        raise ValueError("sparse graph self-loop flags are inconsistent")
    if np.any((~is_self_loop) & (edge_type == 0)):
        raise ValueError("every non-self edge needs relation provenance")
    if not np.array_equal(edge_type, relation_mask):
        raise ValueError("edge_type and relation_mask disagree")
    if np.any(is_self_loop & (edge_type != 0)) or np.any(is_self_loop & (direction != 0)):
        raise ValueError("self-loops must have no relation provenance or direction")
    if not np.allclose(weight[is_self_loop], 1.0, atol=1e-6):
        raise ValueError("self-loop weights must remain fixed at 1")
    return {
        "nodes": nodes,
        "nodes_sha256": nodes_sha256(nodes),
        "source_index": source,
        "target_index": target,
        "static_weight": weight,
        "edge_type": edge_type,
        "relation_mask": relation_mask,
        "edge_direction": direction,
        "is_self_loop": is_self_loop,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a sparse dynamic candidate graph from the official20 static graph.")
    parser.add_argument("--adjacency", type=Path, required=True, help="Existing official20 adjacency NPZ.")
    parser.add_argument("--output", type=Path, required=True, help="Sparse candidate graph NPZ to create.")
    parser.add_argument("--report", type=Path, help="Optional JSON summary path.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    print(json.dumps(build_sparse_graph(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
