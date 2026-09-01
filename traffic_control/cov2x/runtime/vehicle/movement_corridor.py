"""Route-aware Vehicle movement-corridor contract.

The resolver consumes simulation-side route intent, not a BSM field.  It maps
only the remaining route to the earliest controlled transition for the target
intersection.  Physical links may be non-unique, but every candidate must agree
on one movement identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


ROUTE_INTENT_SOURCE = "simulation_side_route_intent"


def _lane_edge(
    lane_id: str, lane_static: Mapping[str, Mapping[str, Any]]
) -> str | None:
    lane = lane_static.get(str(lane_id), {}) or {}
    edge_id = lane.get("edge_id")
    if edge_id is not None:
        return str(edge_id)
    parts = str(lane_id).rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return None


@dataclass(frozen=True)
class MovementCorridorResolution:
    intersection_id: str
    route_edges: tuple[str, ...]
    route_index: int | None
    route_transition_index: int | None
    predecessor_depth: int | None
    candidate_connection_ids: tuple[str, ...]
    controlled_terminal_lane_ids: tuple[str, ...]
    candidate_movement_ids: tuple[str, ...]
    resolved_movement_id: str | None
    failure_reason: str | None
    route_intent_source: str = ROUTE_INTENT_SOURCE

    @property
    def resolved(self) -> bool:
        return self.resolved_movement_id is not None and self.failure_reason is None


class MovementApproachCorridor:
    """Resolve upstream vehicles to movement-consistent terminal-link sets."""

    def __init__(self, metadata: Mapping[str, Any]) -> None:
        self._intersections = {
            str(key): dict(value or {})
            for key, value in (metadata.get("intersections", {}) or {}).items()
        }
        lane_static: dict[str, Mapping[str, Any]] = {}
        for edge_id, lanes in (metadata.get("edge_lanes", {}) or {}).items():
            for raw in lanes or ():
                if not isinstance(raw, Mapping) or raw.get("lane_id") is None:
                    continue
                lane = dict(raw)
                lane.setdefault("edge_id", str(edge_id))
                lane_static[str(lane["lane_id"])] = lane
        for intersection in self._intersections.values():
            for lane_id, raw in (intersection.get("lanes", {}) or {}).items():
                lane_static.setdefault(str(lane_id), dict(raw or {}))
        self._lane_static = lane_static

    def _failure(
        self,
        intersection_id: str,
        route: tuple[str, ...],
        route_index: int | None,
        reason: str,
        *,
        transition_index: int | None = None,
        predecessor_depth: int | None = None,
        connection_ids: tuple[str, ...] = (),
        terminal_lanes: tuple[str, ...] = (),
        movements: tuple[str, ...] = (),
    ) -> MovementCorridorResolution:
        return MovementCorridorResolution(
            intersection_id=str(intersection_id),
            route_edges=route,
            route_index=route_index,
            route_transition_index=transition_index,
            predecessor_depth=predecessor_depth,
            candidate_connection_ids=connection_ids,
            controlled_terminal_lane_ids=terminal_lanes,
            candidate_movement_ids=movements,
            resolved_movement_id=None,
            failure_reason=reason,
        )

    def resolve(
        self, intersection_id: str, vehicle: Mapping[str, Any]
    ) -> MovementCorridorResolution:
        tls_id = str(intersection_id)
        location = vehicle.get("location", {}) or {}
        route = tuple(str(value) for value in location.get("route_edges", ()) or ())
        raw_index = location.get("route_index")
        if raw_index is None:
            return self._failure(tls_id, route, None, "route_index_missing")
        try:
            route_index = int(raw_index)
        except (TypeError, ValueError):
            return self._failure(tls_id, route, None, "route_index_invalid")
        if not route or not 0 <= route_index < len(route):
            return self._failure(tls_id, route, route_index, "route_index_invalid")

        raw_connections = (
            self._intersections.get(tls_id, {}).get("connections", ()) or ()
        )
        connections = [
            dict(item) for item in raw_connections if isinstance(item, Mapping)
        ]
        if not connections:
            return self._failure(
                tls_id, route, route_index, "map_connections_missing"
            )

        lane_edges = {
            str(item.get("from_lane", "")): _lane_edge(
                str(item.get("from_lane", "")), self._lane_static
            )
            for item in connections
        }
        to_edges = {
            str(item.get("to_lane", "")): _lane_edge(
                str(item.get("to_lane", "")), self._lane_static
            )
            for item in connections
        }
        transition_index: int | None = None
        candidates: list[dict[str, Any]] = []
        for index in range(route_index, len(route) - 1):
            matched = [
                item
                for item in connections
                if lane_edges.get(str(item.get("from_lane", ""))) == route[index]
                and to_edges.get(str(item.get("to_lane", "")))
                == route[index + 1]
            ]
            if matched:
                transition_index = index
                candidates = matched
                break
        if transition_index is None:
            return self._failure(
                tls_id,
                route,
                route_index,
                "no_route_consistent_controlled_transition",
            )

        connection_ids = tuple(
            sorted(str(item.get("connection_id", "")) for item in candidates)
        )
        terminal_lanes = tuple(
            sorted({str(item.get("from_lane", "")) for item in candidates})
        )
        movements = tuple(
            sorted(
                {
                    str(item.get("movement", ""))
                    for item in candidates
                    if str(item.get("movement", ""))
                }
            )
        )
        depth = transition_index - route_index
        if not movements:
            return self._failure(
                tls_id,
                route,
                route_index,
                "movement_missing",
                transition_index=transition_index,
                predecessor_depth=depth,
                connection_ids=connection_ids,
                terminal_lanes=terminal_lanes,
            )
        if len(movements) != 1:
            return self._failure(
                tls_id,
                route,
                route_index,
                "ambiguous_movement",
                transition_index=transition_index,
                predecessor_depth=depth,
                connection_ids=connection_ids,
                terminal_lanes=terminal_lanes,
                movements=movements,
            )
        return MovementCorridorResolution(
            intersection_id=tls_id,
            route_edges=route,
            route_index=route_index,
            route_transition_index=transition_index,
            predecessor_depth=depth,
            candidate_connection_ids=connection_ids,
            controlled_terminal_lane_ids=terminal_lanes,
            candidate_movement_ids=movements,
            resolved_movement_id=movements[0],
            failure_reason=None,
        )
