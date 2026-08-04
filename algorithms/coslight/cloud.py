"""Low-frequency, auditable cloud prior for CoSLight Stage 2.

The coordinator consumes only Protocol 2.0 intersection observations and an
offline algorithm-owned topology JSON.  It owns no trainable parameters and
can only scale the explicit physical pressure prior between 1.0 and the
configured maximum; a local spillback gate restores the neutral weight 1.0.
"""

from __future__ import annotations

import copy
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

import numpy as np


SUPPORTED_CLOUD_TOPOLOGY_SCHEMA_VERSIONS = (1, 2)
DEFAULT_UPDATE_INTERVAL_S = 60.0
DEFAULT_MAX_WEIGHT = 1.20
DEFAULT_TARGET_QUEUE_RATIO = 0.01
DEFAULT_SPILL_THRESHOLD = 0.70
DEFAULT_MAX_CORRIDOR_DISTANCE_M = 1_500.0
DEFAULT_MIN_PLATOON_VEHICLES = 1
DEFAULT_PLATOON_LEAD_S = 15.0
DEFAULT_PLATOON_LAG_S = 15.0
DEFAULT_HOLD_COOLDOWN_S = 0.0


def _occupancy_ratio(value: Any) -> float:
    occupancy = max(float(value or 0.0), 0.0)
    if occupancy > 1.0:
        occupancy /= 100.0
    return float(np.clip(occupancy, 0.0, 1.0))


def load_cloud_topology(path: str | Path) -> dict:
    topology_path = Path(path).expanduser().resolve()
    if not topology_path.is_file():
        raise FileNotFoundError(f"CoSLight cloud topology not found: {topology_path}")
    document = json.loads(topology_path.read_text(encoding="utf-8"))
    if int(document.get("schema_version", -1)) not in (
        SUPPORTED_CLOUD_TOPOLOGY_SCHEMA_VERSIONS
    ):
        raise ValueError("unsupported CoSLight cloud topology schema")
    intersections = document.get("intersections", {})
    if not isinstance(intersections, dict) or not intersections:
        raise ValueError("CoSLight cloud topology has no intersections")
    covered = [
        str(tid)
        for region in document.get("regions", [])
        for tid in region.get("intersections", [])
    ]
    if len(covered) != len(set(covered)) or set(covered) != set(intersections):
        raise ValueError("CoSLight cloud regions must partition all intersections")
    return document


class RegionalCloudCoordinator:
    """Translate regional queue imbalance into bounded phase-prior weights."""

    def __init__(
        self,
        topology: Mapping[str, Any],
        *,
        tls_order: Sequence[str],
        incoming_lanes: Mapping[str, Sequence[str]],
        lane_capacity: Mapping[str, float],
        lane_length: Mapping[str, float],
        phase_connections: Mapping[
            str, Sequence[Sequence[Tuple[str, str]]]
        ],
        source_phase_connections: Mapping[
            str, Sequence[Sequence[Tuple[str, str]]]
        ]
        | None = None,
        lane_edges: Mapping[str, Mapping[str, str]],
        lane_speed: Mapping[str, float] | None = None,
        phase_orders: Mapping[str, Sequence[int]] | None = None,
        action_dim: int,
        coordination_mode: str = "regional_rule",
        update_interval_s: float = DEFAULT_UPDATE_INTERVAL_S,
        max_weight: float = DEFAULT_MAX_WEIGHT,
        target_queue_ratio: float = DEFAULT_TARGET_QUEUE_RATIO,
        spill_threshold: float = DEFAULT_SPILL_THRESHOLD,
        min_platoon_vehicles: int = DEFAULT_MIN_PLATOON_VEHICLES,
        platoon_lead_s: float = DEFAULT_PLATOON_LEAD_S,
        platoon_lag_s: float = DEFAULT_PLATOON_LAG_S,
        hold_cooldown_s: float = DEFAULT_HOLD_COOLDOWN_S,
    ) -> None:
        if not math.isfinite(update_interval_s) or update_interval_s <= 0.0:
            raise ValueError("cloud update interval must be finite and positive")
        if not math.isfinite(max_weight) or not 1.0 <= max_weight <= 2.0:
            raise ValueError("cloud maximum weight must be in [1, 2]")
        if not 0.0 <= target_queue_ratio < 1.0:
            raise ValueError("cloud target queue ratio must be in [0, 1)")
        if not 0.0 < spill_threshold <= 1.0:
            raise ValueError("cloud spill threshold must be in (0, 1]")
        if coordination_mode not in {
            "regional_rule",
            "platoon_shadow",
            "platoon_control",
            "platoon_hold_shadow",
            "platoon_hold_control",
            "platoon_hold_safe_shadow",
            "platoon_hold_safe_control",
        }:
            raise ValueError("unsupported CoSLight cloud coordination mode")
        if int(min_platoon_vehicles) < 1:
            raise ValueError("cloud minimum platoon size must be at least one")
        if not math.isfinite(platoon_lead_s) or platoon_lead_s < 0.0:
            raise ValueError("cloud platoon lead time must be finite and non-negative")
        if not math.isfinite(platoon_lag_s) or platoon_lag_s < 0.0:
            raise ValueError("cloud platoon lag time must be finite and non-negative")
        if not math.isfinite(hold_cooldown_s) or hold_cooldown_s < 0.0:
            raise ValueError("cloud hold cooldown must be finite and non-negative")
        self.tls_order = tuple(str(tid) for tid in tls_order)
        self._tls_set = set(self.tls_order)
        self._tls_index = {tid: index for index, tid in enumerate(self.tls_order)}
        known = set(topology.get("intersections", {}))
        unknown = self._tls_set - known
        if unknown:
            raise ValueError(
                f"cloud topology is missing controlled intersections: {sorted(unknown)}"
            )
        self.incoming_lanes = {
            str(tid): tuple(str(lane) for lane in lanes)
            for tid, lanes in incoming_lanes.items()
        }
        self.lane_capacity = {
            str(lane): max(float(capacity), 1.0)
            for lane, capacity in lane_capacity.items()
        }
        self.lane_length = {
            str(lane): max(float(length), 1.0)
            for lane, length in lane_length.items()
        }
        self.lane_speed = {
            str(lane): max(float(speed), 0.1)
            for lane, speed in (lane_speed or {}).items()
        }
        self.phase_orders = {
            str(tid): tuple(int(value) for value in values)
            for tid, values in (phase_orders or {}).items()
        }
        self._phase_index_by_id = {
            tid: {int(phase_id): index for index, phase_id in enumerate(order)}
            for tid, order in self.phase_orders.items()
        }
        self.coordination_mode = str(coordination_mode)
        self.action_dim = int(action_dim)
        self.update_interval_s = float(update_interval_s)
        self.max_weight = float(max_weight)
        self.target_queue_ratio = float(target_queue_ratio)
        self.spill_threshold = float(spill_threshold)
        self.min_platoon_vehicles = int(min_platoon_vehicles)
        self.platoon_lead_s = float(platoon_lead_s)
        self.platoon_lag_s = float(platoon_lag_s)
        self.hold_cooldown_s = float(hold_cooldown_s)
        self.max_corridor_distance_m = float(
            topology.get("generation_config", {}).get(
                "max_corridor_distance_m", DEFAULT_MAX_CORRIDOR_DISTANCE_M
            )
        )
        if (
            not math.isfinite(self.max_corridor_distance_m)
            or self.max_corridor_distance_m <= 0.0
        ):
            raise ValueError("cloud corridor distance must be finite and positive")

        self._region_by_tls: Dict[str, str] = {}
        self._region_members: Dict[str, Tuple[str, ...]] = {}
        for region in topology.get("regions", []):
            region_id = str(region["region_id"])
            members = tuple(
                str(tid)
                for tid in region.get("intersections", [])
                if str(tid) in self._tls_set
            )
            if not members:
                continue
            self._region_members[region_id] = members
            for tid in members:
                self._region_by_tls[tid] = region_id

        corridor_link: Dict[Tuple[str, str], Tuple[str, str, float]] = {}
        self._corridor_links: Dict[str, list[dict]] = {}
        for corridor in topology.get("corridors", []):
            corridor_id = str(corridor["corridor_id"])
            links = []
            for row in corridor.get("directed_links", []):
                source = str(row["source"])
                target = str(row["target"])
                if source not in self._tls_set or target not in self._tls_set:
                    continue
                direction = str(row["direction"])
                distance_m = float(row.get("distance_m", float("nan")))
                if not math.isfinite(distance_m) or distance_m <= 0.0:
                    raise ValueError(
                        "cloud corridor links need finite positive distances"
                    )
                link = {
                    "source": source,
                    "target": target,
                    "direction": direction,
                    "distance_m": distance_m,
                    "free_flow_time_s": row.get("free_flow_time_s"),
                    "source_outgoing_edge": row.get("source_outgoing_edge"),
                    "target_incoming_edge": row.get("target_incoming_edge"),
                }
                links.append(link)
                corridor_link[(source, target)] = (
                    corridor_id,
                    direction,
                    distance_m,
                )
            if links:
                self._corridor_links[corridor_id] = links

        outgoing_targets = topology.get("outgoing_edge_targets", {})
        self._phase_links: list[list[Tuple[Tuple[str, str, str], ...]]] = []
        indirect_corridor_routes_rejected = 0
        for tid in self.tls_order:
            edge_targets = outgoing_targets.get(tid, {})
            action_links = []
            configured_phases = phase_connections.get(tid, ())
            for action_index in range(self.action_dim):
                links = set()
                pairs = (
                    configured_phases[action_index]
                    if action_index < len(configured_phases)
                    else ()
                )
                for _, to_lane in pairs:
                    edge_id = lane_edges.get(tid, {}).get(str(to_lane))
                    if not edge_id:
                        continue
                    for target_row in edge_targets.get(edge_id, []):
                        # ``outgoing_edge_targets`` stores general reachability up
                        # to 10 km.  A target may therefore be reachable only
                        # after leaving in the opposite direction and looping
                        # back.  Such a path must not label this phase as serving
                        # a short cloud corridor.
                        route_distance_m = float(
                            target_row.get("distance_m", float("inf"))
                        )
                        if route_distance_m > self.max_corridor_distance_m:
                            continue
                        target = str(target_row["intersection_id"])
                        corridor = corridor_link.get((tid, target))
                        if corridor is not None:
                            corridor_id, direction, direct_distance_m = corridor
                            # A target may be reachable from several outgoing
                            # edges.  Only the edge on the source-target
                            # shortest path represents the corridor bearing;
                            # accepting any sub-1500 m detour can label a west
                            # exit as "north", for example.  Both distances are
                            # produced by the same offline Dijkstra pass, so
                            # equality up to serialization error identifies the
                            # correct first hop without a tuned threshold.
                            if not math.isclose(
                                route_distance_m,
                                direct_distance_m,
                                rel_tol=1e-9,
                                abs_tol=1e-6,
                            ):
                                indirect_corridor_routes_rejected += 1
                                continue
                            links.add((target, corridor_id, direction))
                action_links.append(tuple(sorted(links)))
            self._phase_links.append(action_links)
        self._mapped_phase_actions = sum(
            bool(links)
            for actions in self._phase_links
            for links in actions
        )
        self._indirect_corridor_routes_rejected = (
            indirect_corridor_routes_rejected
        )

        self._platoon_links: list[dict] = []
        self._unmapped_platoon_links = 0
        self._unmapped_platoon_link_details: list[dict] = []
        release_connections = source_phase_connections or phase_connections
        for corridor_id, links in self._corridor_links.items():
            for link in links:
                free_flow_time_s = link.get("free_flow_time_s")
                source_edge = str(link.get("source_outgoing_edge") or "")
                target_edge = str(link.get("target_incoming_edge") or "")
                if (
                    not source_edge
                    or not target_edge
                    or free_flow_time_s is None
                    or not math.isfinite(float(free_flow_time_s))
                    or float(free_flow_time_s) <= 0.0
                ):
                    self._unmapped_platoon_links += 1
                    self._unmapped_platoon_link_details.append(
                        {
                            "source": str(link["source"]),
                            "target": str(link["target"]),
                            "reason": "missing_schema_v2_route_fields",
                        }
                    )
                    continue
                source = str(link["source"])
                target = str(link["target"])
                source_lanes_by_action: Dict[int, Tuple[str, ...]] = {}
                for action_index, pairs in enumerate(
                    release_connections.get(source, ())
                ):
                    lanes = sorted(
                        {
                            str(from_lane)
                            for from_lane, to_lane in pairs
                            if lane_edges.get(source, {}).get(str(to_lane))
                            == source_edge
                        }
                    )
                    if lanes:
                        source_lanes_by_action[action_index] = tuple(lanes)
                target_actions = tuple(
                    action_index
                    for action_index, pairs in enumerate(
                        phase_connections.get(target, ())
                    )
                    if any(
                        lane_edges.get(target, {}).get(str(from_lane))
                        == target_edge
                        for from_lane, _ in pairs
                    )
                )
                if not source_lanes_by_action or not target_actions:
                    self._unmapped_platoon_links += 1
                    missing = []
                    if not source_lanes_by_action:
                        missing.append("source_protected_release_phase")
                    if not target_actions:
                        missing.append("target_protected_receiving_phase")
                    self._unmapped_platoon_link_details.append(
                        {
                            "source": source,
                            "target": target,
                            "source_outgoing_edge": source_edge,
                            "target_incoming_edge": target_edge,
                            "reason": "+".join(missing),
                        }
                    )
                    continue
                self._platoon_links.append(
                    {
                        "corridor_id": corridor_id,
                        "source": source,
                        "target": target,
                        "source_outgoing_edge": source_edge,
                        "target_incoming_edge": target_edge,
                        "free_flow_time_s": float(free_flow_time_s),
                        "source_lanes_by_action": source_lanes_by_action,
                        "target_actions": target_actions,
                    }
                )
        if self.coordination_mode in {
            "platoon_shadow",
            "platoon_control",
            "platoon_hold_shadow",
            "platoon_hold_control",
            "platoon_hold_safe_shadow",
            "platoon_hold_safe_control",
        } and not self._platoon_links:
            raise ValueError(
                "platoon shadow needs schema-v2 corridor boundary edges and phase mappings"
            )

        self._last_update_s: float | None = None
        self._link_weights: Dict[Tuple[str, str], float] = {}
        self._preferred_directions: Dict[str, str] = {}
        self._last_regions: Dict[str, dict] = {}
        self._updates = 0
        self._decisions = 0
        self._amplified_phase_decisions = 0
        self._spill_suppressed_phase_decisions = 0
        self._local_pressure_suppressed_phase_decisions = 0
        self._weight_total = 0.0
        self._weight_count = 0
        self._weight_max = 1.0
        self._update_history: list[dict] = []
        self._counterfactual_agent_decisions = 0
        self._counterfactual_action_changes = 0
        self._counterfactual_per_intersection = {
            tid: {"decisions": 0, "action_changes": 0}
            for tid in self.tls_order
        }
        self._counterfactual_events: list[dict] = []
        self._counterfactual_abs_logit_shifts: list[float] = []
        self._counterfactual_signed_logit_shifts: list[float] = []
        self._counterfactual_top_margins: list[float] = []
        self._counterfactual_weighted_agent_decisions = 0
        self._counterfactual_selected_action_amplified = 0
        self._counterfactual_challenger_decisions = 0
        self._counterfactual_challenger_base_gaps: list[float] = []
        self._counterfactual_challenger_post_gaps: list[float] = []
        self._incoming_priority_shadow_agent_decisions = 0
        self._incoming_priority_shadow_action_changes = 0
        self._incoming_priority_shadow_per_intersection = {
            tid: {"decisions": 0, "action_changes": 0}
            for tid in self.tls_order
        }
        self._incoming_priority_shadow_events: list[dict] = []
        self._direction_priority_shadow_agent_decisions = 0
        self._direction_priority_shadow_action_changes = 0
        self._direction_priority_shadow_per_intersection = {
            tid: {"decisions": 0, "action_changes": 0}
            for tid in self.tls_order
        }
        self._direction_priority_shadow_events: list[dict] = []
        self._weighted_probe_events: list[dict] = []
        self._last_platoon_update_s: float | None = None
        self._platoon_predictions: list[dict] = []
        self._platoon_updates = 0
        self._platoon_source_samples = 0
        self._platoon_source_phase_misses = 0
        self._platoon_source_below_minimum = 0
        self._platoon_predictions_created = 0
        self._platoon_active_link_decisions = 0
        self._platoon_boosted_phase_decisions = 0
        self._platoon_spill_suppressed_phase_decisions = 0
        self._platoon_hold_only_decisions = 0
        self._platoon_hold_candidate_phase_decisions = 0
        self._platoon_hold_noncurrent_suppressed_phase_decisions = 0
        self._platoon_hold_nonpositive_pressure_suppressed_phase_decisions = 0
        self._platoon_hold_cooldown_suppressed_phase_decisions = 0
        self._last_cloud_hold_s: Dict[str, float] = {}
        self._platoon_prediction_history: list[dict] = []

    def _queue_excess_ratio(self, queue_ratio: float) -> float:
        """Scale excess by the target itself, not by full lane capacity.

        Region queue is an average across every incoming lane, so values around
        0.01 already represent recurring stopped demand.  Dividing by
        ``1-target`` made the rule effectively inert on the production graph.
        """

        return float(
            np.clip(
                (float(queue_ratio) - self.target_queue_ratio)
                / max(self.target_queue_ratio, 0.01),
                0.0,
                1.0,
            )
        )

    @classmethod
    def from_file(cls, path: str | Path, **kwargs: Any) -> "RegionalCloudCoordinator":
        return cls(load_cloud_topology(path), **kwargs)

    def _intersection_metrics(
        self, intersections: Mapping[str, Any]
    ) -> Dict[str, dict]:
        metrics: Dict[str, dict] = {}
        for tid in self.tls_order:
            lanes = intersections.get(tid, {}).get("lanes", {})
            queue_values = []
            demand_values = []
            spill_values = []
            for lane_id in self.incoming_lanes.get(tid, ()):
                lane = lanes.get(lane_id, {})
                capacity = self.lane_capacity.get(lane_id, 1.0)
                length = self.lane_length.get(lane_id, 50.0)
                density = float(lane.get("vehicle_count", 0.0)) / capacity
                queue = max(
                    float(lane.get("halting_count", 0.0)) / capacity,
                    float(lane.get("queue_length_m", 0.0)) / length,
                )
                queue_values.append(float(np.clip(queue, 0.0, 1.0)))
                demand_values.append(float(np.clip(density, 0.0, 1.0)))
                spill_values.append(
                    max(
                        _occupancy_ratio(lane.get("occupancy", 0.0)),
                        float(np.clip(density, 0.0, 1.0)),
                        float(np.clip(queue, 0.0, 1.0)),
                    )
                )
            metrics[tid] = {
                "queue": float(np.mean(queue_values)) if queue_values else 0.0,
                "demand": float(np.mean(demand_values)) if demand_values else 0.0,
                "spill": max(spill_values, default=0.0),
            }
        return metrics

    def _update_plan(
        self, intersections: Mapping[str, Any], simulation_time: float
    ) -> None:
        metrics = self._intersection_metrics(intersections)
        regions = {}
        for region_id, members in self._region_members.items():
            queue = float(np.mean([metrics[tid]["queue"] for tid in members]))
            demand = float(np.mean([metrics[tid]["demand"] for tid in members]))
            priority = self._queue_excess_ratio(queue)
            regions[region_id] = {
                "queue_ratio": queue,
                "demand_ratio": demand,
                "spill_risk": max(metrics[tid]["spill"] for tid in members),
                "target_queue_ratio": self.target_queue_ratio,
                "priority": priority,
            }

        preferred = {}
        link_weights: Dict[Tuple[str, str], float] = {}
        for corridor_id, links in self._corridor_links.items():
            direction_scores: Dict[str, float] = defaultdict(float)
            for link in links:
                source = metrics[link["source"]]
                target = metrics[link["target"]]
                urgency = self._queue_excess_ratio(source["queue"])
                safety = max(1.0 - target["spill"], 0.0)
                direction_scores[link["direction"]] += (
                    urgency * (0.5 + 0.5 * source["demand"]) * safety
                )
            if not direction_scores:
                continue
            direction, score = max(
                sorted(direction_scores.items()), key=lambda item: item[1]
            )
            if score <= 0.0:
                continue
            preferred[corridor_id] = direction
            for link in links:
                if link["direction"] != direction:
                    continue
                source = metrics[link["source"]]
                target = metrics[link["target"]]
                if target["spill"] >= self.spill_threshold:
                    continue
                region_id = self._region_by_tls[link["source"]]
                region_priority = regions[region_id]["priority"]
                urgency = self._queue_excess_ratio(source["queue"])
                strength = math.sqrt(max(region_priority * urgency, 0.0))
                safety = float(
                    np.clip(
                        (self.spill_threshold - target["spill"])
                        / self.spill_threshold,
                        0.0,
                        1.0,
                    )
                )
                link_weights[(link["source"], link["target"])] = (
                    1.0 + (self.max_weight - 1.0) * strength * safety
                )

        self._last_update_s = float(simulation_time)
        self._link_weights = link_weights
        self._preferred_directions = preferred
        self._last_regions = regions
        self._updates += 1
        if len(self._update_history) < 20:
            self._update_history.append(
                {
                    "simulation_time_s": float(simulation_time),
                    "regions": copy.deepcopy(regions),
                    "intersections": copy.deepcopy(metrics),
                    "preferred_directions": copy.deepcopy(preferred),
                    "planned_link_boosts": len(link_weights),
                    "max_planned_weight": max(link_weights.values(), default=1.0),
                }
            )

    def phase_weights(
        self,
        intersections: Mapping[str, Any],
        phase_features: np.ndarray,
        simulation_time: float,
    ) -> np.ndarray:
        features = np.asarray(phase_features, dtype=np.float32)
        expected = (len(self.tls_order), self.action_dim)
        if features.ndim != 3 or features.shape[:2] != expected:
            raise ValueError("cloud phase features must match [agents, actions]")
        if (
            self._last_update_s is None
            or simulation_time < self._last_update_s
            or simulation_time >= self._last_update_s + self.update_interval_s - 1e-9
        ):
            self._update_plan(intersections, simulation_time)

        weights = np.ones(expected, dtype=np.float32)
        for agent_index, tid in enumerate(self.tls_order):
            for action_index, links in enumerate(self._phase_links[agent_index]):
                raw_weight = max(
                    (
                        self._link_weights.get((tid, target), 1.0)
                        for target, corridor_id, direction in links
                        if self._preferred_directions.get(corridor_id) == direction
                    ),
                    default=1.0,
                )
                if raw_weight <= 1.0 + 1e-12:
                    continue
                # A regional preference cannot justify serving an empty
                # approach or pushing traffic towards a denser downstream
                # movement.  Zero is a physical feasibility boundary, not a
                # tuned traffic threshold: only locally positive served-
                # movement pressure may receive the cloud direction bonus.
                if float(features[agent_index, action_index, 5]) <= 0.0:
                    self._local_pressure_suppressed_phase_decisions += 1
                    continue
                # V16 feature 4 is the mean density of this phase's immediate
                # outgoing lanes.  This road-side gate is evaluated every
                # decision, even while the cloud plan remains low-frequency.
                if float(features[agent_index, action_index, 4]) >= self.spill_threshold:
                    self._spill_suppressed_phase_decisions += 1
                    continue
                weights[agent_index, action_index] = raw_weight

        self._decisions += 1
        amplified = int(np.count_nonzero(weights > 1.0 + 1e-12))
        self._amplified_phase_decisions += amplified
        self._weight_total += float(weights.sum())
        self._weight_count += int(weights.size)
        self._weight_max = max(self._weight_max, float(weights.max()))
        return weights

    def _update_platoon_predictions(
        self,
        intersections: Mapping[str, Any],
        simulation_time: float,
    ) -> None:
        now = float(simulation_time)
        if self._last_platoon_update_s is not None and now < self._last_platoon_update_s:
            self._platoon_predictions.clear()
        self._platoon_predictions = [
            item
            for item in self._platoon_predictions
            if float(item["active_until_s"]) >= now - 1e-9
        ]
        created = []
        for link in self._platoon_links:
            source = str(link["source"])
            current_phase = intersections.get(source, {}).get("current_phase")
            try:
                action_index = self._phase_index_by_id.get(source, {})[
                    int(current_phase)
                ]
            except (KeyError, TypeError, ValueError):
                self._platoon_source_phase_misses += 1
                continue
            source_lanes = link["source_lanes_by_action"].get(action_index, ())
            if not source_lanes:
                self._platoon_source_phase_misses += 1
                continue
            self._platoon_source_samples += 1
            lanes = intersections.get(source, {}).get("lanes", {})
            moving_vehicles = 0.0
            moving_speed_ratio_total = 0.0
            capacity_total = 0.0
            for lane_id in source_lanes:
                lane = lanes.get(lane_id, {})
                moving = max(
                    float(lane.get("vehicle_count", 0.0))
                    - float(lane.get("halting_count", 0.0)),
                    0.0,
                )
                capacity_total += self.lane_capacity.get(lane_id, 1.0)
                moving_vehicles += moving
                speed_ratio = float(
                    np.clip(
                        float(lane.get("mean_speed", 0.0))
                        / self.lane_speed.get(lane_id, 13.9),
                        0.0,
                        1.0,
                    )
                )
                moving_speed_ratio_total += moving * speed_ratio
            if moving_vehicles < self.min_platoon_vehicles:
                self._platoon_source_below_minimum += 1
                continue
            mean_speed_ratio = moving_speed_ratio_total / max(moving_vehicles, 1.0)
            if mean_speed_ratio <= 0.0:
                self._platoon_source_below_minimum += 1
                continue
            free_flow_time_s = float(link["free_flow_time_s"])
            predicted_arrival_s = now + free_flow_time_s
            prediction = {
                "detected_at_s": now,
                "source": source,
                "target": str(link["target"]),
                "corridor_id": str(link["corridor_id"]),
                "source_action_index": int(action_index),
                "source_lanes": list(source_lanes),
                "target_actions": list(link["target_actions"]),
                "moving_vehicles": float(moving_vehicles),
                "moving_demand_ratio": float(
                    moving_vehicles / max(capacity_total, 1.0)
                ),
                "mean_speed_ratio": float(mean_speed_ratio),
                "free_flow_time_s": free_flow_time_s,
                "predicted_arrival_s": predicted_arrival_s,
                "active_from_s": max(now, predicted_arrival_s - self.platoon_lead_s),
                "active_until_s": predicted_arrival_s + self.platoon_lag_s,
                "weight": 1.0
                + (self.max_weight - 1.0) * float(mean_speed_ratio),
            }
            self._platoon_predictions.append(prediction)
            created.append(copy.deepcopy(prediction))
        self._last_platoon_update_s = now
        self._platoon_updates += 1
        self._platoon_predictions_created += len(created)
        if created and len(self._platoon_prediction_history) < 200:
            remaining = 200 - len(self._platoon_prediction_history)
            self._platoon_prediction_history.extend(created[:remaining])

    def platoon_shadow_weights(
        self,
        intersections: Mapping[str, Any],
        phase_features: np.ndarray,
        simulation_time: float,
        *,
        hold_current_only: bool = False,
        require_positive_pressure: bool = False,
    ) -> np.ndarray:
        """Return a non-executed downstream receiving-phase prior.

        A prediction is emitted only when the protected phase currently active
        at an upstream signal has moving vehicles on lanes feeding the
        shortest-path corridor exit.  The prediction becomes active around the
        offline free-flow arrival time and is mapped to protected phases that
        receive the corresponding downstream entry edge.  The controller uses
        these weights only for same-state counterfactual diagnostics in shadow
        modes.  ``hold_current_only`` prevents proactive switching: a cloud
        bonus may only keep an already-active protected receiving phase green.
        ``require_positive_pressure`` additionally prevents the cloud from
        extending a movement whose immediate outgoing load already exceeds its
        incoming demand.
        """

        features = np.asarray(phase_features, dtype=np.float32)
        expected = (len(self.tls_order), self.action_dim)
        if features.ndim != 3 or features.shape[:2] != expected:
            raise ValueError("cloud phase features must match [agents, actions]")
        if (
            self._last_platoon_update_s is None
            or simulation_time < self._last_platoon_update_s
            or simulation_time
            >= self._last_platoon_update_s + self.update_interval_s - 1e-9
        ):
            self._update_platoon_predictions(intersections, simulation_time)

        now = float(simulation_time)
        weights = np.ones(expected, dtype=np.float32)
        if hold_current_only:
            self._platoon_hold_only_decisions += 1
        for prediction in self._platoon_predictions:
            if not (
                float(prediction["active_from_s"]) - 1e-9
                <= now
                <= float(prediction["active_until_s"]) + 1e-9
            ):
                continue
            self._platoon_active_link_decisions += 1
            target = str(prediction["target"])
            target_index = self._tls_index[target]
            current_action_index = None
            if hold_current_only:
                current_phase = intersections.get(target, {}).get("current_phase")
                try:
                    current_action_index = self._phase_index_by_id.get(target, {})[
                        int(current_phase)
                    ]
                except (KeyError, TypeError, ValueError):
                    current_action_index = None
            for action_index in prediction["target_actions"]:
                action_index = int(action_index)
                if action_index >= self.action_dim:
                    continue
                if hold_current_only and action_index != current_action_index:
                    self._platoon_hold_noncurrent_suppressed_phase_decisions += 1
                    continue
                last_hold_s = self._last_cloud_hold_s.get(target)
                if (
                    hold_current_only
                    and self.hold_cooldown_s > 0.0
                    and last_hold_s is not None
                    and now < last_hold_s + self.hold_cooldown_s - 1e-9
                ):
                    self._platoon_hold_cooldown_suppressed_phase_decisions += 1
                    continue
                if (
                    hold_current_only
                    and require_positive_pressure
                    and float(features[target_index, action_index, 5]) <= 0.0
                ):
                    self._platoon_hold_nonpositive_pressure_suppressed_phase_decisions += (
                        1
                    )
                    continue
                if (
                    float(features[target_index, action_index, 4])
                    >= self.spill_threshold
                ):
                    self._platoon_spill_suppressed_phase_decisions += 1
                    continue
                if hold_current_only:
                    self._platoon_hold_candidate_phase_decisions += 1
                weights[target_index, action_index] = max(
                    weights[target_index, action_index],
                    float(prediction["weight"]),
                )

        self._decisions += 1
        boosted = int(np.count_nonzero(weights > 1.0 + 1e-12))
        self._platoon_boosted_phase_decisions += boosted
        self._weight_total += float(weights.sum())
        self._weight_count += int(weights.size)
        self._weight_max = max(self._weight_max, float(weights.max()))
        return weights

    def diagnostics(self) -> dict:
        return {
            "mode": self.coordination_mode,
            "phase_prior_formula": (
                "time_aware_upstream_platoon_downstream_receiving_shadow_v1"
                if self.coordination_mode
                in {
                    "platoon_shadow",
                    "platoon_control",
                    "platoon_hold_shadow",
                    "platoon_hold_control",
                    "platoon_hold_safe_shadow",
                    "platoon_hold_safe_control",
                }
                else "bounded_additive_direction_bonus_positive_pressure_v2"
            ),
            "updates": self._updates,
            "decisions": self._decisions,
            "update_interval_s": self.update_interval_s,
            "hold_cooldown_s": self.hold_cooldown_s,
            "max_weight_config": self.max_weight,
            "target_queue_ratio": self.target_queue_ratio,
            "spill_threshold": self.spill_threshold,
            "max_corridor_distance_m": self.max_corridor_distance_m,
            "mapped_phase_actions": self._mapped_phase_actions,
            "indirect_corridor_routes_rejected": (
                self._indirect_corridor_routes_rejected
            ),
            "planned_link_boosts": len(self._link_weights),
            "amplified_phase_decisions": self._amplified_phase_decisions,
            "spill_suppressed_phase_decisions": (
                self._spill_suppressed_phase_decisions
            ),
            "local_pressure_suppressed_phase_decisions": (
                self._local_pressure_suppressed_phase_decisions
            ),
            "mean_weight": (
                self._weight_total / self._weight_count
                if self._weight_count
                else 1.0
            ),
            "max_weight_observed": self._weight_max,
            "preferred_directions": copy.deepcopy(self._preferred_directions),
            "regions": copy.deepcopy(self._last_regions),
            "update_history": copy.deepcopy(self._update_history),
            "counterfactual_agent_decisions": self._counterfactual_agent_decisions,
            "counterfactual_action_changes": self._counterfactual_action_changes,
            "counterfactual_action_change_rate": (
                self._counterfactual_action_changes
                / self._counterfactual_agent_decisions
                if self._counterfactual_agent_decisions
                else 0.0
            ),
            "counterfactual_per_intersection": copy.deepcopy(
                self._counterfactual_per_intersection
            ),
            "counterfactual_events": copy.deepcopy(self._counterfactual_events),
            "counterfactual_abs_logit_shift_mean": (
                float(np.mean(self._counterfactual_abs_logit_shifts))
                if self._counterfactual_abs_logit_shifts
                else 0.0
            ),
            "counterfactual_abs_logit_shift_max": max(
                self._counterfactual_abs_logit_shifts, default=0.0
            ),
            "counterfactual_positive_logit_shifts": sum(
                value > 1e-12 for value in self._counterfactual_signed_logit_shifts
            ),
            "counterfactual_negative_logit_shifts": sum(
                value < -1e-12 for value in self._counterfactual_signed_logit_shifts
            ),
            "counterfactual_base_top_margin_p50": (
                float(np.percentile(self._counterfactual_top_margins, 50))
                if self._counterfactual_top_margins
                else 0.0
            ),
            "counterfactual_base_top_margin_p95": (
                float(np.percentile(self._counterfactual_top_margins, 95))
                if self._counterfactual_top_margins
                else 0.0
            ),
            "counterfactual_weighted_agent_decisions": (
                self._counterfactual_weighted_agent_decisions
            ),
            "counterfactual_selected_action_amplified": (
                self._counterfactual_selected_action_amplified
            ),
            "counterfactual_selected_action_shifted": (
                self._counterfactual_selected_action_amplified
            ),
            "counterfactual_challenger_decisions": (
                self._counterfactual_challenger_decisions
            ),
            "counterfactual_challenger_base_gap_p50": (
                float(np.percentile(self._counterfactual_challenger_base_gaps, 50))
                if self._counterfactual_challenger_base_gaps
                else 0.0
            ),
            "counterfactual_challenger_post_gap_min": min(
                self._counterfactual_challenger_post_gaps, default=0.0
            ),
            "counterfactual_challenger_post_gap_p50": (
                float(np.percentile(self._counterfactual_challenger_post_gaps, 50))
                if self._counterfactual_challenger_post_gaps
                else 0.0
            ),
            "incoming_priority_shadow_agent_decisions": (
                self._incoming_priority_shadow_agent_decisions
            ),
            "incoming_priority_shadow_action_changes": (
                self._incoming_priority_shadow_action_changes
            ),
            "incoming_priority_shadow_action_change_rate": (
                self._incoming_priority_shadow_action_changes
                / self._incoming_priority_shadow_agent_decisions
                if self._incoming_priority_shadow_agent_decisions
                else 0.0
            ),
            "incoming_priority_shadow_per_intersection": copy.deepcopy(
                self._incoming_priority_shadow_per_intersection
            ),
            "incoming_priority_shadow_events": copy.deepcopy(
                self._incoming_priority_shadow_events
            ),
            "direction_priority_shadow_agent_decisions": (
                self._direction_priority_shadow_agent_decisions
            ),
            "direction_priority_shadow_action_changes": (
                self._direction_priority_shadow_action_changes
            ),
            "direction_priority_shadow_action_change_rate": (
                self._direction_priority_shadow_action_changes
                / self._direction_priority_shadow_agent_decisions
                if self._direction_priority_shadow_agent_decisions
                else 0.0
            ),
            "direction_priority_shadow_per_intersection": copy.deepcopy(
                self._direction_priority_shadow_per_intersection
            ),
            "direction_priority_shadow_events": copy.deepcopy(
                self._direction_priority_shadow_events
            ),
            "weighted_probe_events": copy.deepcopy(self._weighted_probe_events),
            "platoon_topology_links": len(self._platoon_links),
            "platoon_unmapped_links": self._unmapped_platoon_links,
            "platoon_unmapped_link_details": copy.deepcopy(
                self._unmapped_platoon_link_details
            ),
            "platoon_updates": self._platoon_updates,
            "platoon_min_vehicles": self.min_platoon_vehicles,
            "platoon_lead_s": self.platoon_lead_s,
            "platoon_lag_s": self.platoon_lag_s,
            "platoon_source_samples": self._platoon_source_samples,
            "platoon_source_phase_misses": self._platoon_source_phase_misses,
            "platoon_source_below_minimum": self._platoon_source_below_minimum,
            "platoon_predictions_created": self._platoon_predictions_created,
            "platoon_pending_predictions": len(self._platoon_predictions),
            "platoon_active_link_decisions": self._platoon_active_link_decisions,
            "platoon_boosted_phase_decisions": (
                self._platoon_boosted_phase_decisions
            ),
            "platoon_spill_suppressed_phase_decisions": (
                self._platoon_spill_suppressed_phase_decisions
            ),
            "platoon_hold_only_decisions": self._platoon_hold_only_decisions,
            "platoon_hold_candidate_phase_decisions": (
                self._platoon_hold_candidate_phase_decisions
            ),
            "platoon_hold_noncurrent_suppressed_phase_decisions": (
                self._platoon_hold_noncurrent_suppressed_phase_decisions
            ),
            "platoon_hold_nonpositive_pressure_suppressed_phase_decisions": (
                self._platoon_hold_nonpositive_pressure_suppressed_phase_decisions
            ),
            "platoon_hold_cooldown_suppressed_phase_decisions": (
                self._platoon_hold_cooldown_suppressed_phase_decisions
            ),
            "platoon_prediction_history": copy.deepcopy(
                self._platoon_prediction_history
            ),
        }

    def record_counterfactual_actions(
        self,
        baseline_actions: np.ndarray,
        cloud_actions: np.ndarray,
        simulation_time: float,
        *,
        baseline_logits: np.ndarray | None = None,
        cloud_logits: np.ndarray | None = None,
        action_masks: np.ndarray | None = None,
        pressure_prior_weights: np.ndarray | None = None,
        phase_features: np.ndarray | None = None,
    ) -> None:
        baseline = np.asarray(baseline_actions, dtype=np.int64)
        selected = np.asarray(cloud_actions, dtype=np.int64)
        expected = (len(self.tls_order),)
        if baseline.shape != expected or selected.shape != expected:
            raise ValueError("cloud counterfactual actions need one value per TLS")
        current_action_indices: list[int | None] = [None] * len(self.tls_order)
        logit_inputs = (baseline_logits, cloud_logits, action_masks)
        shadow_inputs = (pressure_prior_weights, phase_features)
        if any(value is not None for value in shadow_inputs) and not all(
            value is not None for value in logit_inputs
        ):
            raise ValueError("cloud incoming-priority shadow needs logit diagnostics")
        if any(value is not None for value in logit_inputs):
            if any(value is None for value in logit_inputs):
                raise ValueError("cloud counterfactual logit diagnostics are all-or-none")
            base_scores = np.asarray(baseline_logits, dtype=np.float64)
            cloud_scores = np.asarray(cloud_logits, dtype=np.float64)
            masks = np.asarray(action_masks, dtype=np.bool_)
            score_shape = (len(self.tls_order), self.action_dim)
            if (
                base_scores.shape != score_shape
                or cloud_scores.shape != score_shape
                or masks.shape != score_shape
            ):
                raise ValueError("cloud counterfactual logits must match [TLS, actions]")
            for agent_index in range(len(self.tls_order)):
                legal = np.flatnonzero(masks[agent_index])
                base_legal = base_scores[agent_index, legal]
                cloud_legal = cloud_scores[agent_index, legal]
                if not np.isfinite(base_legal).all() or not np.isfinite(
                    cloud_legal
                ).all():
                    raise ValueError("cloud counterfactual legal logits must be finite")
                signed_shifts = cloud_legal - base_legal
                shifts = np.abs(signed_shifts)
                self._counterfactual_signed_logit_shifts.extend(
                    signed_shifts.tolist()
                )
                self._counterfactual_abs_logit_shifts.extend(shifts.tolist())
                if legal.size >= 2:
                    ordered = np.sort(base_legal)
                    self._counterfactual_top_margins.append(
                        float(ordered[-1] - ordered[-2])
                    )
                shifted_actions = legal[shifts > 1e-12]
                if not shifted_actions.size:
                    continue
                self._counterfactual_weighted_agent_decisions += 1
                baseline_action = int(baseline[agent_index])
                if baseline_action in shifted_actions:
                    self._counterfactual_selected_action_amplified += 1
                challengers = shifted_actions[shifted_actions != baseline_action]
                if not challengers.size:
                    continue
                self._counterfactual_challenger_decisions += 1
                best_base_challenger = float(
                    np.max(base_scores[agent_index, challengers])
                )
                best_cloud_challenger = float(
                    np.max(cloud_scores[agent_index, challengers])
                )
                self._counterfactual_challenger_base_gaps.append(
                    float(base_scores[agent_index, baseline_action])
                    - best_base_challenger
                )
                self._counterfactual_challenger_post_gaps.append(
                    float(cloud_scores[agent_index, baseline_action])
                    - best_cloud_challenger
                )
            if any(value is not None for value in shadow_inputs):
                if any(value is None for value in shadow_inputs):
                    raise ValueError(
                        "cloud incoming-priority shadow inputs are all-or-none"
                    )
                weights = np.asarray(pressure_prior_weights, dtype=np.float64)
                features = np.asarray(phase_features, dtype=np.float64)
                if (
                    weights.shape != score_shape
                    or features.ndim != 3
                    or features.shape[:2] != score_shape
                    or features.shape[2] < 7
                ):
                    raise ValueError(
                        "cloud incoming-priority shadow inputs have invalid shape"
                    )
                for agent_index in range(len(self.tls_order)):
                    current_flags = features[agent_index, :, 6]
                    if np.max(current_flags, initial=0.0) > 0.5:
                        current_action_indices[agent_index] = int(
                            np.argmax(current_flags)
                        )
                incoming_bonus = (weights - 1.0) * np.maximum(
                    features[..., 0], 0.0
                )
                for agent_index, tid in enumerate(self.tls_order):
                    legal = np.flatnonzero(masks[agent_index])
                    legal_weights = weights[agent_index, legal]
                    if (
                        np.max(legal_weights, initial=1.0) <= 1.0 + 1e-12
                        or len(self._weighted_probe_events) >= 200
                    ):
                        continue
                    self._weighted_probe_events.append(
                        {
                            "simulation_time_s": float(simulation_time),
                            "intersection_id": tid,
                            "baseline_action_index": int(baseline[agent_index]),
                            "current_action_index": current_action_indices[
                                agent_index
                            ],
                            "legal_action_indices": legal.astype(int).tolist(),
                            "baseline_logits": base_scores[
                                agent_index, legal
                            ].tolist(),
                            "signed_pressure_logits": cloud_scores[
                                agent_index, legal
                            ].tolist(),
                            "weights": legal_weights.tolist(),
                            "mean_incoming": features[
                                agent_index, legal, 0
                            ].tolist(),
                            "movement_pressure": features[
                                agent_index, legal, 5
                            ].tolist(),
                        }
                    )
                shadow_scores = base_scores + incoming_bonus
                shadow_actions = baseline.copy()
                for agent_index in range(len(self.tls_order)):
                    legal = np.flatnonzero(masks[agent_index])
                    shadow_actions[agent_index] = int(
                        legal[np.argmax(shadow_scores[agent_index, legal])]
                    )
                shadow_changed = shadow_actions != baseline
                self._incoming_priority_shadow_agent_decisions += len(
                    self.tls_order
                )
                self._incoming_priority_shadow_action_changes += int(
                    shadow_changed.sum()
                )
                for agent_index, tid in enumerate(self.tls_order):
                    item = self._incoming_priority_shadow_per_intersection[tid]
                    item["decisions"] += 1
                    if not bool(shadow_changed[agent_index]):
                        continue
                    item["action_changes"] += 1
                    if len(self._incoming_priority_shadow_events) < 200:
                        item_weights = weights[agent_index]
                        self._incoming_priority_shadow_events.append(
                            {
                                "simulation_time_s": float(simulation_time),
                                "intersection_id": tid,
                                "current_action_index": current_action_indices[
                                    agent_index
                                ],
                                "baseline_action_index": int(
                                    baseline[agent_index]
                                ),
                                "shadow_action_index": int(
                                    shadow_actions[agent_index]
                                ),
                                "baseline_action_logit": float(
                                    base_scores[agent_index, baseline[agent_index]]
                                ),
                                "shadow_action_logit": float(
                                    shadow_scores[
                                        agent_index, shadow_actions[agent_index]
                                    ]
                                ),
                                "shadow_action_weight": float(
                                    item_weights[shadow_actions[agent_index]]
                                ),
                            }
                        )
                direction_scores = base_scores + np.maximum(weights - 1.0, 0.0)
                direction_actions = baseline.copy()
                for agent_index in range(len(self.tls_order)):
                    legal = np.flatnonzero(masks[agent_index])
                    direction_actions[agent_index] = int(
                        legal[np.argmax(direction_scores[agent_index, legal])]
                    )
                direction_changed = direction_actions != baseline
                self._direction_priority_shadow_agent_decisions += len(
                    self.tls_order
                )
                self._direction_priority_shadow_action_changes += int(
                    direction_changed.sum()
                )
                for agent_index, tid in enumerate(self.tls_order):
                    item = self._direction_priority_shadow_per_intersection[tid]
                    item["decisions"] += 1
                    if not bool(direction_changed[agent_index]):
                        continue
                    item["action_changes"] += 1
                    if len(self._direction_priority_shadow_events) < 200:
                        action_index = int(direction_actions[agent_index])
                        self._direction_priority_shadow_events.append(
                            {
                                "simulation_time_s": float(simulation_time),
                                "intersection_id": tid,
                                "current_action_index": current_action_indices[
                                    agent_index
                                ],
                                "baseline_action_index": int(
                                    baseline[agent_index]
                                ),
                                "shadow_action_index": action_index,
                                "baseline_action_logit": float(
                                    base_scores[agent_index, baseline[agent_index]]
                                ),
                                "shadow_action_logit": float(
                                    direction_scores[agent_index, action_index]
                                ),
                                "shadow_action_weight": float(
                                    weights[agent_index, action_index]
                                ),
                            }
                        )
        changed = baseline != selected
        self._counterfactual_agent_decisions += len(self.tls_order)
        self._counterfactual_action_changes += int(changed.sum())
        for agent_index, tid in enumerate(self.tls_order):
            item = self._counterfactual_per_intersection[tid]
            item["decisions"] += 1
            if not bool(changed[agent_index]):
                continue
            item["action_changes"] += 1
            current_action = current_action_indices[agent_index]
            if (
                current_action is not None
                and int(selected[agent_index]) == current_action
                and int(baseline[agent_index]) != current_action
            ):
                effect = "hold_current_receiving"
                self._last_cloud_hold_s[tid] = float(simulation_time)
            elif (
                current_action is not None
                and int(baseline[agent_index]) == current_action
                and int(selected[agent_index]) != current_action
            ):
                effect = "switch_away_from_current"
            else:
                effect = "switch_to_noncurrent"
            if len(self._counterfactual_events) < 200:
                self._counterfactual_events.append(
                    {
                        "simulation_time_s": float(simulation_time),
                        "intersection_id": tid,
                        "current_action_index": current_action,
                        "baseline_action_index": int(baseline[agent_index]),
                        "cloud_action_index": int(selected[agent_index]),
                        "effect": effect,
                    }
                )
