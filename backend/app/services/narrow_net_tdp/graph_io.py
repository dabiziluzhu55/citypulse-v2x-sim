# NarrowNet-TDP在线推理用静态候选图与方向图加载

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np


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


def load_sparse_candidate_graph(path: Path) -> dict[str, Any]:
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
        raise ValueError("sparse graph nodes must be sorted unique and non-empty")
    if stored_hash and stored_hash != nodes_sha256(nodes):
        raise ValueError("sparse graph nodes_sha256 does not match nodes")
    lengths = {
        len(source),
        len(target),
        len(weight),
        len(edge_type),
        len(relation_mask),
        len(direction),
        len(is_self_loop),
    }
    if len(lengths) != 1:
        raise ValueError("sparse graph edge arrays have inconsistent lengths")
    if np.any(source < 0) or np.any(source >= n) or np.any(target < 0) or np.any(target >= n):
        raise ValueError("sparse graph contains out-of-range node indices")
    if not np.isfinite(weight).all() or np.any(weight <= 0):
        raise ValueError("sparse graph weights must be finite and positive")
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


def load_directional_lane_graph(path: Path) -> dict[str, Any]:
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
        raise ValueError("directional graph nodes must be sorted unique and 206 lanes")
    if stored_hash != nodes_sha256(nodes):
        raise ValueError("directional graph nodes_sha256 does not match nodes")
    if not np.allclose(upstream, downstream.T, atol=1e-6):
        raise ValueError("upstream_adjacency must be downstream_adjacency.T")
    if not np.allclose(downstream_normalized, _row_normalize(downstream), atol=1e-6):
        raise ValueError("downstream_normalized is not row-normalized")
    if not np.allclose(upstream_normalized, _row_normalize(upstream), atol=1e-6):
        raise ValueError("upstream_normalized is not row-normalized")
    result: dict[str, Any] = {
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


def static_adjacency_from_sparse(graph: dict[str, Any]) -> np.ndarray:
    nodes = tuple(graph["nodes"])
    source = np.asarray(graph["source_index"], dtype=np.int64)
    target = np.asarray(graph["target_index"], dtype=np.int64)
    weight = np.asarray(graph["static_weight"], dtype=np.float32)
    if len(nodes) != 206:
        raise ValueError(f"NarrowNet-TDP requires 206 nodes, got {len(nodes)}")
    adjacency = np.zeros((len(nodes), len(nodes)), dtype=np.float64)
    adjacency[source, target] = weight
    if not np.allclose(adjacency, adjacency.T, atol=1e-6):
        raise ValueError("static Chebyshev adjacency must be symmetric")
    if not np.all(np.diag(adjacency) > 0):
        raise ValueError("static Chebyshev adjacency requires positive self-loops")
    return adjacency


def build_chebyshev_gso(adjacency: np.ndarray) -> np.ndarray:
    adjacency = np.asarray(adjacency, dtype=np.float64)
    if adjacency.ndim != 2 or adjacency.shape[0] != adjacency.shape[1]:
        raise ValueError("adjacency must be a square matrix")
    adjacency = np.maximum(adjacency, adjacency.T)
    identity = np.eye(adjacency.shape[0], dtype=np.float64)
    row_sum = adjacency.sum(axis=1)
    inv_sqrt = np.zeros_like(row_sum)
    positive = row_sum > 0
    inv_sqrt[positive] = np.power(row_sum[positive], -0.5)
    normalized = inv_sqrt[:, None] * adjacency * inv_sqrt[None, :]
    gso = identity - normalized
    eigval_max = float(np.linalg.norm(gso, ord=2))
    if eigval_max <= 1e-12:
        raise ValueError("cannot scale a zero GSO")
    if eigval_max >= 2:
        gso = gso - identity
    else:
        gso = 2 * gso / eigval_max - identity
    return gso.astype(np.float32)
