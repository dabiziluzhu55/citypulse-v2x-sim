"""Build an auditable manifest for traffic-light junction forecast nodes.

The forecasting nodes are SUMO traffic-light junctions, never individual
roads or lanes. SUMO networks can use a connection@tl identifier that is
different from junction@id (for example, clustered junctions). This module
resolves that relationship from the connection's external source lane and the
junction's incLanes attribute instead of guessing from names.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET


DEFAULT_EXPECTED_COUNT = 100


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


def _is_internal_lane(lane_id: str) -> bool:
    return lane_id.startswith(":")


def _read_network(
    net_path: Path,
) -> tuple[dict[str, tuple[str, ...]], set[str], list[tuple[str, str]]]:
    junction_lanes: dict[str, tuple[str, ...]] = {}
    network_lanes: set[str] = set()
    signal_connections: list[tuple[str, str]] = []

    for _, element in ET.iterparse(net_path, events=("end",)):
        tag = _local_tag(element.tag)
        if tag == "junction" and element.get("type") == "traffic_light":
            junction_id = element.get("id")
            if not junction_id:
                raise ValueError("traffic_light junction is missing an id")
            junction_lanes[junction_id] = tuple(
                lane
                for lane in (element.get("incLanes") or "").split()
                if lane and not _is_internal_lane(lane)
            )
        elif tag == "lane":
            lane_id = element.get("id")
            if lane_id:
                network_lanes.add(lane_id)
        elif tag == "connection" and element.get("tl") is not None:
            from_edge = element.get("from")
            from_lane = element.get("fromLane")
            if from_edge is None or from_lane is None:
                raise ValueError("connection with tl is missing from/fromLane")
            if not from_edge.startswith(":"):
                signal_connections.append(
                    (str(element.get("tl")), _lane_id(from_edge, from_lane))
                )
        element.clear()

    if not junction_lanes:
        raise ValueError(f"no traffic_light junctions found in {net_path}")
    return junction_lanes, network_lanes, signal_connections


def build_manifest(
    *,
    net: Path,
    output: Path | None = None,
    expected_count: int = DEFAULT_EXPECTED_COUNT,
    force: bool = False,
) -> dict[str, object]:
    """Build and optionally write the TLS junction manifest."""

    if expected_count < 1:
        raise ValueError("expected_count must be positive")
    if not net.is_file():
        raise FileNotFoundError(net)

    junction_lanes, network_lanes, signal_connections = _read_network(net)
    nodes = tuple(sorted(junction_lanes))
    if len(nodes) != expected_count:
        raise ValueError(
            f"expected exactly {expected_count} traffic_light junctions, "
            f"found {len(nodes)}: {list(nodes)}"
        )

    lane_to_junctions: dict[str, set[str]] = defaultdict(set)
    for junction_id, lanes in junction_lanes.items():
        for lane in lanes:
            if lane not in network_lanes:
                raise ValueError(
                    f"{junction_id}: incoming lane {lane!r} is not present in the network"
                )
            lane_to_junctions[lane].add(junction_id)

    duplicated_lanes = {
        lane: sorted(junctions)
        for lane, junctions in lane_to_junctions.items()
        if len(junctions) > 1
    }
    if duplicated_lanes:
        raise ValueError(
            "incoming lane belongs to multiple traffic_light junctions: "
            f"{dict(list(duplicated_lanes.items())[:10])}"
        )

    tl_to_junctions: dict[str, set[str]] = defaultdict(set)
    tl_to_source_lanes: dict[str, set[str]] = defaultdict(set)
    source_lane_to_tls: dict[str, set[str]] = defaultdict(set)
    for tl_id, source_lane in signal_connections:
        if source_lane not in network_lanes:
            raise ValueError(
                f"connection tl={tl_id!r} refers to missing source lane {source_lane!r}"
            )
        candidates = lane_to_junctions.get(source_lane, set())
        if len(candidates) != 1:
            raise ValueError(
                f"cannot resolve connection tl={tl_id!r}, source lane={source_lane!r}; "
                f"junction candidates={sorted(candidates)}"
            )
        junction_id = next(iter(candidates))
        tl_to_junctions[tl_id].add(junction_id)
        tl_to_source_lanes[tl_id].add(source_lane)
        source_lane_to_tls[source_lane].add(tl_id)

    unresolved_tl = {
        tl_id: sorted(junctions)
        for tl_id, junctions in tl_to_junctions.items()
        if len(junctions) != 1
    }
    if unresolved_tl:
        raise ValueError(f"connection tl identifiers do not resolve uniquely: {unresolved_tl}")

    duplicate_tl_for_lane = {
        lane: sorted(tl_ids)
        for lane, tl_ids in source_lane_to_tls.items()
        if len(tl_ids) > 1
    }
    if duplicate_tl_for_lane:
        raise ValueError(
            "one incoming lane is assigned to multiple connection tl identifiers: "
            f"{dict(list(duplicate_tl_for_lane.items())[:10])}"
        )

    junction_to_tl_ids: dict[str, list[str]] = {node: [] for node in nodes}
    for tl_id, junctions in tl_to_junctions.items():
        junction_id = next(iter(junctions))
        junction_to_tl_ids[junction_id].append(tl_id)
    duplicate_junction_aliases = {
        junction_id: sorted(tl_ids)
        for junction_id, tl_ids in junction_to_tl_ids.items()
        if len(tl_ids) > 1
    }
    if duplicate_junction_aliases:
        raise ValueError(
            "multiple connection tl identifiers resolve to one junction: "
            f"{duplicate_junction_aliases}"
        )

    junction_entries: dict[str, dict[str, object]] = {}
    owner_by_lane: dict[str, str] = {}
    for junction_id in nodes:
        incoming_lanes = tuple(sorted(set(junction_lanes[junction_id])))
        if not incoming_lanes:
            raise ValueError(f"traffic_light junction {junction_id!r} has no incoming lanes")
        for lane in incoming_lanes:
            previous = owner_by_lane.setdefault(lane, junction_id)
            if previous != junction_id:
                raise ValueError(
                    f"incoming lane {lane!r} belongs to {previous!r} and {junction_id!r}"
                )
        tl_ids = sorted(junction_to_tl_ids[junction_id])
        junction_entries[junction_id] = {
            "tls_ids": tl_ids,
            "incoming_lanes": list(incoming_lanes),
            "incoming_lane_count": len(incoming_lanes),
            "mapping_method": (
                "connection_tl_to_incLanes"
                if tl_ids
                else "junction_incLanes_without_connection_tl"
            ),
        }

    payload: dict[str, object] = {
        "schema_version": 1,
        "node_definition": "SUMO traffic_light junction",
        "expected_node_count": expected_count,
        "node_count": len(nodes),
        "nodes": list(nodes),
        "node_order_sha256": _node_order_sha256(nodes),
        "junctions": junction_entries,
        "incoming_lane_count": len(owner_by_lane),
        "source_net": str(net.resolve()),
        "source_net_sha256": _sha256(net),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "connection_tl_count": len(tl_to_junctions),
        "connection_source_lane_count": len(source_lane_to_tls),
        "tl_to_junction": {
            tl_id: next(iter(junctions))
            for tl_id, junctions in sorted(tl_to_junctions.items())
        },
    }

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and not force:
            raise FileExistsError(
                f"refusing to overwrite existing manifest {output}; use --force explicitly"
            )
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + chr(10),
            encoding="utf-8",
        )
    return payload


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build an auditable traffic-light junction manifest."
    )
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=DEFAULT_EXPECTED_COUNT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            build_manifest(
                net=args.net,
                output=args.output,
                expected_count=args.expected_count,
                force=args.force,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
