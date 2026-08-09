"""Validate official signal and demand mappings against a source SUMO network."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, List, Mapping, Sequence, Set, Tuple

from .tls import IntersectionConfiguration, SignalConfigurationError
from .traffic import TrafficDemandConfiguration, TrafficDemandError


def _context(
    intersection_id: str,
    period_id: str,
    approach: str,
    movement: str,
) -> str:
    return f"{intersection_id}/{period_id}/{approach}/{movement}"


def _nonzero_movements(demand, period) -> Sequence[Tuple[str, str]]:
    return tuple(
        (approach_name, movement)
        for approach_name, approach in demand.approaches.items()
        for movement in approach.movements
        if any(
            interval.volumes[approach_name][movement] > 0
            for interval in period.intervals
        )
    )


def validate_source_compatibility(
    source_net: Path,
    selected: Sequence[IntersectionConfiguration],
    demands: TrafficDemandConfiguration,
) -> Mapping[str, int]:
    """Validate selected official routes without requiring SUMO binaries."""
    selected_by_id = {item.intersection_id: item for item in selected}
    missing_demands = set(selected_by_id) - set(demands.intersections)
    if missing_demands:
        raise TrafficDemandError(
            f"No official traffic demand is configured for: {sorted(missing_demands)}"
        )

    incoming_owner: Dict[str, IntersectionConfiguration] = {}
    junction_owner: Dict[str, IntersectionConfiguration] = {}
    for config in selected:
        for edge_id in config.topology.incoming_edges:
            if edge_id in incoming_owner:
                raise SignalConfigurationError(
                    f"Incoming edge {edge_id!r} is shared by multiple official "
                    "intersections."
                )
            incoming_owner[edge_id] = config
        for junction_id in config.junction_ids:
            if junction_id in junction_owner:
                raise SignalConfigurationError(
                    f"Junction {junction_id!r} is shared by multiple official "
                    "intersections."
                )
            junction_owner[junction_id] = config

    override_edge_contexts: DefaultDict[str, Set[str]] = defaultdict(set)
    override_pair_contexts: DefaultDict[Tuple[str, str], Set[str]] = defaultdict(set)
    endpoint_extensions = []
    required_endpoint_edges: Set[str] = set()
    required_endpoint_pairs: Set[Tuple[str, str]] = set()
    for intersection_id in selected_by_id:
        demand = demands.intersections[intersection_id]
        for period in demand.periods.values():
            for approach_name, movement_routes in period.route_overrides.items():
                for movement, routes in movement_routes.items():
                    context = _context(
                        intersection_id,
                        period.period_id,
                        approach_name,
                        movement,
                    )
                    for route in routes:
                        for edge_id in route.edges:
                            override_edge_contexts[edge_id].add(context)
                        for pair in zip(route.edges, route.edges[1:]):
                            override_pair_contexts[pair].add(context)
        configured = demand.route_endpoint_extensions
        for direction in ("upstream", "downstream"):
            for near_edge, far_edge in getattr(configured, direction).items():
                context = (
                    f"{intersection_id}/route_endpoint_extensions/{direction}/"
                    f"{near_edge}"
                )
                pair = (
                    (far_edge, near_edge)
                    if direction == "upstream"
                    else (near_edge, far_edge)
                )
                endpoint_extensions.append(
                    (intersection_id, direction, near_edge, far_edge, context)
                )
                required_endpoint_edges.update((near_edge, far_edge))
                required_endpoint_pairs.add(pair)

    required_override_edges = set(override_edge_contexts)
    required_override_pairs = set(override_pair_contexts)

    found_junctions: Set[str] = set()
    incoming_targets: Dict[str, str] = {}
    found_override_edges: Set[str] = set()
    found_override_pairs: Set[Tuple[str, str]] = set()
    endpoint_edge_nodes: Dict[str, Tuple[str, str]] = {}
    positive_endpoint_edges: Set[str] = set()
    endpoint_pair_directions: DefaultDict[Tuple[str, str], Set[str]] = defaultdict(set)
    traffic_light_junctions: Set[str] = set()
    incoming_connections: Dict[str, List[Mapping[str, str]]] = defaultdict(list)
    current_edge_id = ""
    try:
        for event, elem in ET.iterparse(source_net, events=("start", "end")):
            if event == "start" and elem.tag == "edge":
                edge_id = elem.get("id", "")
                current_edge_id = (
                    edge_id
                    if edge_id and elem.get("function") != "internal"
                    else ""
                )
                if current_edge_id in incoming_owner:
                    incoming_targets[current_edge_id] = elem.get("to", "")
                if current_edge_id in required_override_edges:
                    found_override_edges.add(current_edge_id)
                if current_edge_id in required_endpoint_edges:
                    endpoint_edge_nodes[current_edge_id] = (
                        elem.get("from", ""),
                        elem.get("to", ""),
                    )
                continue
            if event == "start" and elem.tag == "lane":
                if current_edge_id in required_endpoint_edges:
                    try:
                        length = float(elem.get("length", ""))
                    except ValueError:
                        length = 0.0
                    if math.isfinite(length) and length > 0:
                        positive_endpoint_edges.add(current_edge_id)
                continue
            if event == "start":
                continue

            if elem.tag == "junction":
                junction_id = elem.get("id", "")
                if junction_id in junction_owner:
                    found_junctions.add(junction_id)
                if elem.get("type") == "traffic_light":
                    traffic_light_junctions.add(junction_id)
            elif elem.tag == "connection":
                from_edge = elem.get("from", "")
                to_edge = elem.get("to", "")
                if from_edge in incoming_owner:
                    incoming_connections[from_edge].append(dict(elem.attrib))
                pair = (from_edge, to_edge)
                if pair in required_override_pairs:
                    found_override_pairs.add(pair)
                if pair in required_endpoint_pairs:
                    endpoint_pair_directions[pair].add(elem.get("dir", ""))
            elif elem.tag == "edge":
                current_edge_id = ""
            elem.clear()
    except (FileNotFoundError, ET.ParseError) as exc:
        raise SignalConfigurationError(
            f"Cannot inspect source SUMO network {source_net}: {exc}"
        ) from exc

    missing_junctions = set(junction_owner) - found_junctions
    if missing_junctions:
        raise SignalConfigurationError(
            "Mapped junctions are missing from source network: "
            f"{sorted(missing_junctions)}"
        )

    missing_incoming = set(incoming_owner) - set(incoming_targets)
    if missing_incoming:
        raise SignalConfigurationError(
            f"Configured incoming edges are missing from source network: "
            f"{sorted(missing_incoming)}"
        )
    for edge_id, config in incoming_owner.items():
        target = incoming_targets[edge_id]
        if target not in config.junction_ids:
            raise SignalConfigurationError(
                f"{config.intersection_id}: incoming edge {edge_id!r} ends at "
                f"junction {target!r}, expected one of {list(config.junction_ids)}."
            )
        if not incoming_connections.get(edge_id):
            raise SignalConfigurationError(
                f"{config.intersection_id}: incoming edge {edge_id!r} has no "
                "connections."
            )

    problems = []
    missing_override_edges = required_override_edges - found_override_edges
    for edge_id in sorted(missing_override_edges):
        for context in sorted(override_edge_contexts[edge_id]):
            problems.append(
                f"{context}: route override edge {edge_id!r} is missing from "
                "the source network."
            )
    missing_override_pairs = required_override_pairs - found_override_pairs
    for first, second in sorted(missing_override_pairs):
        if first in missing_override_edges or second in missing_override_edges:
            continue
        for context in sorted(override_pair_contexts[(first, second)]):
            problems.append(
                f"{context}: route override edges {first!r} and {second!r} "
                "are not connected in the source network."
            )

    protected_junctions = set(junction_owner) | traffic_light_junctions
    for intersection_id, direction, near_edge, far_edge, context in endpoint_extensions:
        missing_edges = [
            edge_id
            for edge_id in (near_edge, far_edge)
            if edge_id not in endpoint_edge_nodes
        ]
        if missing_edges:
            problems.append(
                f"{context}: endpoint extension edges are missing from the source "
                f"network: {missing_edges}."
            )
            continue
        invalid_lengths = [
            edge_id
            for edge_id in (near_edge, far_edge)
            if edge_id not in positive_endpoint_edges
        ]
        if invalid_lengths:
            problems.append(
                f"{context}: endpoint extension edges have no positive lane length: "
                f"{invalid_lengths}."
            )
        pair = (
            (far_edge, near_edge)
            if direction == "upstream"
            else (near_edge, far_edge)
        )
        directions = endpoint_pair_directions[pair]
        if "s" not in directions:
            problems.append(
                f"{context}: edges {pair[0]!r} and {pair[1]!r} must have a "
                f"straight connection; found directions {sorted(directions)}."
            )
        endpoint_index = 0 if direction == "upstream" else 1
        junction_id = endpoint_edge_nodes[near_edge][endpoint_index]
        if junction_id in protected_junctions:
            problems.append(
                f"{context}: extension would cross protected junction "
                f"{junction_id!r}."
            )

    routes_by_intersection: Dict[
        str, Dict[Tuple[str, str], Set[Tuple[str, str]]]
    ] = {}
    implicit_routes_by_intersection: Dict[
        str, Dict[Tuple[str, str], Set[Tuple[str, str]]]
    ] = {}
    for config in selected:
        routes: Dict[Tuple[str, str], Set[Tuple[str, str]]] = defaultdict(set)
        primary_routes: Dict[Tuple[str, str], Set[Tuple[str, str]]] = defaultdict(set)
        for edge_id in config.topology.incoming_edges:
            approach = config.topology.approach_for_edge(edge_id)
            for connection in incoming_connections[edge_id]:
                direction = connection.get("dir", "")
                movement = config.topology.movement_for_direction(edge_id, direction)
                if movement is None:
                    raise SignalConfigurationError(
                        f"{config.intersection_id}: unsupported SUMO direction "
                        f"{direction!r} on {edge_id}->{connection.get('to', '')}."
                    )
                if movement != "blocked":
                    route = (edge_id, connection.get("to", ""))
                    key = (approach, movement)
                    routes[key].add(route)
                    # Match build_traffic's implicit-route preference.
                    if direction not in {"L", "R"}:
                        primary_routes[key].add(route)
        routes_by_intersection[config.intersection_id] = routes
        implicit_routes_by_intersection[config.intersection_id] = {
            key: primary_routes.get(key) or candidates
            for key, candidates in routes.items()
        }

    checked_movements = 0
    override_routes = 0
    count_endpoint_edges = {
        intersection_id: {"upstream": set(), "downstream": set()}
        for intersection_id in selected_by_id
    }

    def record_count_path(intersection_id: str, edges: Sequence[str]) -> None:
        if edges:
            count_endpoint_edges[intersection_id]["upstream"].add(edges[0])
            count_endpoint_edges[intersection_id]["downstream"].add(edges[-1])

    for intersection_id in selected_by_id:
        config = selected_by_id[intersection_id]
        demand = demands.intersections[intersection_id]
        routes = routes_by_intersection[intersection_id]
        implicit_routes = implicit_routes_by_intersection[intersection_id]
        for period in demand.periods.values():
            for official_approach, official_movement in _nonzero_movements(
                demand, period
            ):
                checked_movements += 1
                approach = demand.approaches[official_approach]
                context = _context(
                    intersection_id,
                    period.period_id,
                    official_approach,
                    official_movement,
                )
                if approach.sumo_approach not in config.topology.approaches:
                    problems.append(
                        f"{context}: SUMO approach {approach.sumo_approach!r} is not "
                        "configured in the signal topology."
                    )
                    continue
                sumo_movement = approach.movements[official_movement]
                all_route_pairs = sorted(
                    routes.get((approach.sumo_approach, sumo_movement), set())
                )
                overrides = period.route_overrides.get(official_approach, {}).get(
                    official_movement, ()
                )
                if overrides:
                    override_routes += len(overrides)
                    for route in overrides:
                        record_count_path(intersection_id, route.edges)
                    continue
                splits = period.route_splits.get(official_approach, {}).get(
                    official_movement, ()
                )
                if splits:
                    routes_by_target = {
                        to_edge: (from_edge, to_edge)
                        for from_edge, to_edge in all_route_pairs
                    }
                    configured_targets = {item.to_edge for item in splits}
                    if (
                        len(routes_by_target) != len(all_route_pairs)
                        or set(routes_by_target) != configured_targets
                    ):
                        problems.append(
                            f"{context}: configured split targets "
                            f"{sorted(configured_targets)} do not match SUMO routes "
                            f"{all_route_pairs}."
                        )
                    else:
                        for route_pair in routes_by_target.values():
                            record_count_path(intersection_id, route_pair)
                    continue
                route_pairs = sorted(
                    implicit_routes.get(
                        (approach.sumo_approach, sumo_movement), set()
                    )
                )
                if len(route_pairs) != 1:
                    problems.append(
                        f"{context}: expected one SUMO route for "
                        f"{approach.sumo_approach}/{sumo_movement}, found "
                        f"{route_pairs}."
                    )
                else:
                    record_count_path(intersection_id, route_pairs[0])

    for intersection_id, direction, near_edge, _far_edge, context in endpoint_extensions:
        if near_edge not in count_endpoint_edges[intersection_id][direction]:
            problems.append(
                f"{context}: near edge is not a {direction} endpoint of an "
                "official count path."
            )

    if problems:
        details = "\n".join(f"- {problem}" for problem in problems)
        raise TrafficDemandError(
            f"Source network compatibility failed with {len(problems)} issue(s):\n"
            f"{details}"
        )

    return {
        "intersections": len(selected),
        "periods": sum(
            len(demands.intersections[item.intersection_id].periods)
            for item in selected
        ),
        "nonzero_movements": checked_movements,
        "override_routes": override_routes,
    }
