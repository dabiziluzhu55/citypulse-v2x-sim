"""Validate official signal and demand mappings against a source SUMO network."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, List, Mapping, Sequence, Set, Tuple

from .config import IntersectionConfiguration, SignalConfigurationError
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

    required_override_edges = set(override_edge_contexts)
    required_override_pairs = set(override_pair_contexts)

    found_junctions: Set[str] = set()
    incoming_targets: Dict[str, str] = {}
    found_override_edges: Set[str] = set()
    found_override_pairs: Set[Tuple[str, str]] = set()
    incoming_connections: Dict[str, List[Mapping[str, str]]] = defaultdict(list)
    try:
        for _, elem in ET.iterparse(source_net, events=("end",)):
            if elem.tag == "junction":
                junction_id = elem.get("id", "")
                if junction_id in junction_owner:
                    found_junctions.add(junction_id)
            elif elem.tag == "edge" and elem.get("function") != "internal":
                edge_id = elem.get("id", "")
                if edge_id in incoming_owner:
                    incoming_targets[edge_id] = elem.get("to", "")
                if edge_id in required_override_edges:
                    found_override_edges.add(edge_id)
            elif elem.tag == "connection":
                from_edge = elem.get("from", "")
                to_edge = elem.get("to", "")
                if from_edge in incoming_owner:
                    incoming_connections[from_edge].append(dict(elem.attrib))
                pair = (from_edge, to_edge)
                if pair in required_override_pairs:
                    found_override_pairs.add(pair)
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
