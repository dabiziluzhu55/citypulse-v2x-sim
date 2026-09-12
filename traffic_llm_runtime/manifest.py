"""TLS manifest helpers for observation scope (no SUMO subprocess)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from simulation_protocol.artifacts import DEFAULT_GENERATED_DIR


def tls_phase_orders(generated_dir: Path | None = None) -> dict[str, tuple[int, ...]]:
    manifest_path = (Path(generated_dir) if generated_dir else DEFAULT_GENERATED_DIR) / "manifests" / "tls_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    orders: dict[str, tuple[int, ...]] = {}
    for intersection_id, item in payload.get("intersections", {}).items():
        orders[str(intersection_id)] = tuple(int(phase) for phase in item.get("phase_order", ()))
    return orders


def neighbor_map(generated_dir: Path | None = None) -> dict[str, tuple[str, ...]]:
    """1-hop neighbors via shared outgoing/incoming edges in tls_manifest."""

    manifest_path = (Path(generated_dir) if generated_dir else DEFAULT_GENERATED_DIR) / "manifests" / "tls_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    incoming_edges: dict[str, set[str]] = {}
    outgoing_edges: dict[str, set[str]] = {}
    for intersection_id, item in payload.get("intersections", {}).items():
        incoming: set[str] = set()
        outgoing: set[str] = set()
        for connection in item.get("connections", ()):
            incoming.add(str(connection["from_edge"]))
            outgoing.add(str(connection["to_edge"]))
        incoming_edges[str(intersection_id)] = incoming
        outgoing_edges[str(intersection_id)] = outgoing
    neighbors: dict[str, tuple[str, ...]] = {}
    ids = tuple(incoming_edges)
    for intersection_id in ids:
        linked = sorted(
            other
            for other in ids
            if other != intersection_id
            and (
                outgoing_edges[intersection_id] & incoming_edges[other]
                or outgoing_edges[other] & incoming_edges[intersection_id]
            )
        )
        neighbors[intersection_id] = tuple(linked)
    return neighbors


def expand_scope(
    seeds: tuple[str, ...],
    hops: int,
    neighbors: Mapping[str, tuple[str, ...]] | dict[str, tuple[str, ...]],
    allowed: set[str],
) -> tuple[str, ...]:
    scope = set(seeds)
    frontier = set(seeds)
    for _ in range(max(0, hops)):
        nxt: set[str] = set()
        for node in frontier:
            nxt.update(neighbors.get(node, ()))
        nxt -= scope
        scope.update(nxt)
        frontier = nxt
    return tuple(sorted(scope & allowed))
