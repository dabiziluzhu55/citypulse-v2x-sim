"""Risk-only finite-storage safety envelope around Strong MaxPressure.

The module is intentionally independent from the frozen
``traffic_control.max_pressure`` implementation.  In nominal conditions it
delegates to that controller verbatim.  Only an observed downstream risk can
change the selected phase, and every such change is recorded for audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Mapping

from traffic_control.max_pressure import MaxPressureController
from traffic_control.protocol import (
    finish_response,
    initialize_response,
    signals_from_phase_map,
    step_response,
)
from traffic_control.safe_mp_cloud import (
    HARD_THRESHOLD,
    PRIOR_HARD_EXPIRE_S,
    PRIOR_SOFT_STALE_S,
    PRIOR_UPDATE_INTERVAL_S,
    RELEASE_THRESHOLD,
    SOFT_THRESHOLD,
    DeterministicRegionalPrior,
    finite_float,
    normalize_occupancy,
)
from traffic_control.safe_mp_v2x import (
    build_invalidation,
    build_map_v2_payload,
    build_spat_v2_payload,
    validate_map_v2,
    validate_spat_v2,
)


logger = logging.getLogger(__name__)

_TRANSITION_STAGES = frozenset({"YELLOW", "CLEARANCE"})
_NEAR_ZERO_SPEED_MPS = 0.1
_EPSILON = 1e-9


@dataclass(frozen=True)
class _Movement:
    connection_id: str
    from_lane: str
    to_lane: str
    phase_ids: tuple[int, ...]
    target_lanes: tuple[str, ...]


@dataclass(frozen=True)
class _IntersectionIndex:
    intersection_id: str
    phase_order: tuple[int, ...]
    phase_connections: dict[int, tuple[str, ...]]
    movements: dict[str, _Movement]
    lane_meta: dict[str, Mapping[str, Any]]


@dataclass(frozen=True)
class ConnectionRisk:
    level: str
    ratio: float
    max_occupancy: float | None
    blocked: bool
    demand: bool
    target_lanes: tuple[str, ...]
    reason: str

    @property
    def active(self) -> bool:
        return self.level in {"soft", "hard", "hysteresis_held"}

    @property
    def hard(self) -> bool:
        # A held soft risk keeps the latch but does not become a hard block.
        return self.level == "hard" or (
            self.level == "hysteresis_held" and self.blocked
        )


@dataclass
class _Runtime:
    risk_state: dict[str, str] = field(default_factory=dict)
    risk_blocked: dict[str, bool] = field(default_factory=dict)
    health_state: str = "HEALTHY"
    controller_epoch: int = 0
    last_epoch_reason: str | None = None
    counters: dict[str, int] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    spat_invalidations: list[dict[str, Any]] = field(default_factory=list)
    last_spat: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_error: str | None = None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _phase_value(phases: Mapping[str, Any], phase_id: int) -> Mapping[str, Any]:
    value = phases.get(str(phase_id), phases.get(phase_id, {}))
    return _as_mapping(value)


def _build_index(metadata: Mapping[str, Any]) -> dict[str, _IntersectionIndex]:
    result: dict[str, _IntersectionIndex] = {}
    all_lane_meta: dict[str, Mapping[str, Any]] = {}
    raw_intersections = metadata.get("intersections", {})
    if not isinstance(raw_intersections, Mapping):
        return result

    # Collect every lane definition before resolving downstream links.  The
    # generated metadata is ordered by intersection, while a movement can
    # point at a lane owned by a later intersection.
    for raw_meta in raw_intersections.values():
        meta = _as_mapping(raw_meta)
        lane_meta_raw = meta.get("lanes", {})
        if isinstance(lane_meta_raw, Mapping):
            all_lane_meta.update(
                {
                    str(lane_id): _as_mapping(value)
                    for lane_id, value in lane_meta_raw.items()
                }
            )

    for raw_iid, raw_meta in raw_intersections.items():
        meta = _as_mapping(raw_meta)
        lane_meta_raw = meta.get("lanes", {})
        lane_meta = {
            str(lane_id): _as_mapping(value)
            for lane_id, value in lane_meta_raw.items()
        } if isinstance(lane_meta_raw, Mapping) else {}

        order: list[int] = []
        for raw_phase in meta.get("phase_order", ()):
            try:
                order.append(int(raw_phase))
            except (TypeError, ValueError):
                continue
        movement_data: dict[str, dict[str, Any]] = {}
        for raw_conn in meta.get("connections", ()):
            conn = _as_mapping(raw_conn)
            if not conn.get("connection_id") or conn.get("from_lane") is None:
                continue
            cid = str(conn["connection_id"])
            movement_data[cid] = {
                "from_lane": str(conn["from_lane"]),
                "to_lane": str(conn.get("to_lane", "")),
            }
        phase_connections: dict[int, tuple[str, ...]] = {}
        phase_ids_by_connection: dict[str, list[int]] = {
            cid: [] for cid in movement_data
        }
        phases = meta.get("phases", {})
        phases = phases if isinstance(phases, Mapping) else {}
        for phase_id in order:
            phase = _phase_value(phases, phase_id)
            priorities = phase.get("connection_priorities", {})
            ids: list[str] = []
            if isinstance(priorities, Mapping):
                for raw_cid in priorities:
                    cid = str(raw_cid)
                    if cid in movement_data:
                        ids.append(cid)
                        phase_ids_by_connection[cid].append(phase_id)
            phase_connections[phase_id] = tuple(ids)

        movements: dict[str, _Movement] = {}
        for cid, data in movement_data.items():
            to_lane = str(data["to_lane"])
            target_lanes = [to_lane]
            target_meta = all_lane_meta.get(to_lane, {})
            target_lanes.extend(
                str(value) for value in target_meta.get("downstream_lane_ids", ())
            )
            # Preserve declaration order while removing duplicates.
            target_lanes = list(dict.fromkeys(target_lanes))
            movements[cid] = _Movement(
                connection_id=cid,
                from_lane=str(data["from_lane"]),
                to_lane=to_lane,
                phase_ids=tuple(phase_ids_by_connection[cid]),
                target_lanes=tuple(target_lanes),
            )
        iid = str(raw_iid)
        result[iid] = _IntersectionIndex(
            intersection_id=iid,
            phase_order=tuple(order),
            phase_connections=phase_connections,
            movements=movements,
            lane_meta=lane_meta,
        )

    return result


def _lane_observations(observation: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    intersections = observation.get("intersections", {})
    if not isinstance(intersections, Mapping):
        return result
    for raw_iobs in intersections.values():
        iobs = _as_mapping(raw_iobs)
        lanes = iobs.get("lanes", {})
        if not isinstance(lanes, Mapping):
            continue
        for raw_lane_id, raw_lane in lanes.items():
            result[str(raw_lane_id)] = _as_mapping(raw_lane)
    return result


def _lane_demand(lane: Mapping[str, Any], ratio: float) -> bool:
    return (
        ratio > _EPSILON
        or finite_float(lane.get("vehicle_count")) > 0.0
        or finite_float(lane.get("halting_count")) > 0.0
        or finite_float(lane.get("waiting_time")) > 0.0
    )


def _lane_ratio(
    lane: Mapping[str, Any],
    lane_meta: Mapping[str, Any],
) -> tuple[float, float | None]:
    length = max(
        0.0,
        finite_float(lane_meta.get("length_m", lane_meta.get("length", 0.0))),
    )
    raw_queue = lane.get("queue_length_m")
    queue_ratio: float | None = None
    if raw_queue is not None and length > 0.0:
        queue_ratio = min(1.0, max(0.0, finite_float(raw_queue) / length))
    occupancy = normalize_occupancy(lane.get("occupancy"))
    if occupancy is not None:
        # Queue length is the preferred finite-storage signal, while occupancy
        # remains a useful conservative upper bound when the queue estimator is
        # zero during a dense moving platoon.
        return max(queue_ratio or 0.0, occupancy), occupancy
    if queue_ratio is not None:
        return queue_ratio, None
    if length > 0.0:
        count = max(
            0.0,
            finite_float(lane.get("halting_count", lane.get("vehicle_count", 0.0))),
        )
        return min(1.0, max(0.0, count * 7.5 / length)), None
    return 0.0, None


def _connection_risk(
    movement: _Movement,
    lane_obs: Mapping[str, Mapping[str, Any]],
    lane_meta: Mapping[str, Mapping[str, Any]],
    previous: str | None,
    previous_blocked: bool = False,
) -> ConnectionRisk:
    max_ratio = 0.0
    max_occupancy: float | None = None
    blocked = False
    demand = False
    reasons: list[str] = []
    for lane_id in movement.target_lanes:
        lane = lane_obs.get(lane_id)
        if lane is None:
            continue
        ratio, occupancy = _lane_ratio(lane, lane_meta.get(lane_id, {}))
        max_ratio = max(max_ratio, ratio)
        if occupancy is not None:
            max_occupancy = occupancy if max_occupancy is None else max(max_occupancy, occupancy)
        lane_has_demand = _lane_demand(lane, ratio)
        demand = demand or lane_has_demand
        allowed_speed = lane.get("current_allowed_speed_mps")
        near_zero = (
            allowed_speed is not None
            and finite_float(allowed_speed, _NEAR_ZERO_SPEED_MPS) <= _NEAR_ZERO_SPEED_MPS
        )
        # A zero allowed speed can also mean that the lane is not passenger
        # compatible (the observation sets its effective speed to zero).  That
        # is not a blockage unless real vehicles are present or waiting.
        observed_vehicle_demand = (
            finite_float(lane.get("vehicle_count")) > 0.0
            or finite_float(lane.get("halting_count")) > 0.0
            or finite_float(lane.get("waiting_time")) > 0.0
        )
        lane_definition = lane_meta.get(lane_id, {})
        allowed_classes = {
            str(value) for value in lane_definition.get("allowed_vehicle_classes", ())
        }
        disallowed_classes = {
            str(value)
            for value in lane_definition.get("disallowed_vehicle_classes", ())
        }
        passenger_incompatible = (
            (bool(allowed_classes) and "passenger" not in allowed_classes)
            or "passenger" in disallowed_classes
        )
        if near_zero and observed_vehicle_demand and not passenger_incompatible:
            blocked = True
            reasons.append("near_zero_capacity")
        if ratio >= HARD_THRESHOLD:
            blocked = True
            reasons.append("hard_occupancy")

    observed = "normal"
    if blocked or max_ratio >= HARD_THRESHOLD:
        observed = "hard"
    elif max_ratio >= SOFT_THRESHOLD:
        observed = "soft"

    if previous in {"soft", "hard", "hysteresis_held"}:
        if max_ratio >= RELEASE_THRESHOLD and observed != "hard":
            # Keep the previous latch until the release threshold is crossed.
            # ``blocked`` distinguishes a held hard block from a held soft risk.
            observed = "hysteresis_held"
            blocked = previous_blocked or previous == "hard"
            reasons.append("hysteresis")
        elif max_ratio < RELEASE_THRESHOLD and observed != "hard":
            # Release is governed by the dedicated hysteresis threshold, not
            # by the lower soft-risk activation threshold.
            observed = "normal"
            blocked = False
            reasons.append("release")

    if observed == "hard" and not reasons:
        reasons.append("hard_threshold")
    if observed == "soft" and not reasons:
        reasons.append("soft_threshold")
    if observed == "normal" and not reasons:
        reasons.append("nominal")
    return ConnectionRisk(
        level=observed,
        ratio=max_ratio,
        max_occupancy=max_occupancy,
        blocked=blocked,
        demand=demand,
        target_lanes=movement.target_lanes,
        reason=", ".join(dict.fromkeys(reasons)),
    )


def _phase_score(
    ix: _IntersectionIndex,
    phase_id: int,
    pressures: Mapping[int, float],
    risks: Mapping[str, ConnectionRisk],
    lane_obs: Mapping[str, Mapping[str, Any]],
    lane_meta: Mapping[str, Mapping[str, Any]],
    prior: float,
) -> tuple[float, float, bool]:
    pressure = finite_float(pressures.get(phase_id))
    release_space = 0.0
    risk_penalty = 0.0
    hard_target = False
    for cid in ix.phase_connections.get(phase_id, ()):
        movement = ix.movements.get(cid)
        if movement is None:
            continue
        risk = risks.get(cid)
        if risk is not None:
            hard_target = hard_target or risk.hard
            risk_penalty += max(0.0, risk.ratio - SOFT_THRESHOLD)
        for lane_id in movement.target_lanes:
            lane = lane_obs.get(lane_id, {})
            ratio, _ = _lane_ratio(lane, lane_meta.get(lane_id, {}))
            demand = _lane_demand(lane, ratio)
            release_space += (1.0 - ratio) * (1.0 if demand else 0.25)
    # The cloud prior is bounded and only participates in a risk decision.
    score = pressure + release_space * (1.0 + 0.1 * min(1.0, max(0.0, prior))) - risk_penalty
    return score, release_space, hard_target


def _phase_pressure_proxy(
    ix: _IntersectionIndex,
    lane_obs: Mapping[str, Mapping[str, Any]],
    lane_meta: Mapping[str, Mapping[str, Any]],
) -> dict[int, float]:
    """Return a bounded risk-ranking proxy without touching Strong B code.

    Strong B remains the sole source of the nominal action.  This proxy is
    used only after a risk is active, where the Safe-MP layer needs a
    deterministic ordering signal for legal escape phases.
    """

    pressures: dict[int, float] = {}
    for phase_id in ix.phase_order:
        score = 0.0
        for connection_id in ix.phase_connections.get(phase_id, ()):
            movement = ix.movements.get(connection_id)
            if movement is None:
                continue
            upstream, _ = _lane_ratio(
                lane_obs.get(movement.from_lane, {}),
                lane_meta.get(movement.from_lane, {}),
            )
            downstream = 0.0
            for lane_id in movement.target_lanes:
                ratio, _ = _lane_ratio(
                    lane_obs.get(lane_id, {}),
                    lane_meta.get(lane_id, {}),
                )
                downstream = max(downstream, ratio)
            score += upstream - downstream
        pressures[phase_id] = score
    return pressures


class SafeMaxPressureController:
    """Finite-storage safety envelope around an immutable Strong-MP instance."""

    def __init__(self, metadata: Mapping[str, Any]) -> None:
        self._metadata = dict(metadata)
        self._episode_id = str(metadata.get("episode_id", ""))
        self._strong = MaxPressureController(dict(metadata))
        self._index = _build_index(metadata)
        self._lane_meta: dict[str, Mapping[str, Any]] = {
            lane_id: lane_meta
            for ix in self._index.values()
            for lane_id, lane_meta in ix.lane_meta.items()
        }
        self._runtime = {
            iid: _Runtime() for iid in self._index
        }
        self._prior = DeterministicRegionalPrior(metadata)
        self._trace_hasher = hashlib.sha256()
        self._trace_count = 0
        self._step_count = 0
        self._last_actions: dict[str, int | None] = {}
        self._last_diagnostics: dict[str, Any] | None = None
        self._step_time = 0.0
        self._initialized = True

    @property
    def strong_controller(self) -> MaxPressureController:
        return self._strong

    def _counter(self, runtime: _Runtime, name: str, amount: int = 1) -> None:
        runtime.counters[name] = runtime.counters.get(name, 0) + amount

    def _record(self, runtime: _Runtime, record: Mapping[str, Any]) -> None:
        if len(runtime.records) >= 10000:
            runtime.records.pop(0)
        runtime.records.append(dict(record))

    def _bump_epoch(self, runtime: _Runtime, reason: str) -> None:
        if runtime.last_epoch_reason == reason and runtime.controller_epoch > 0:
            return
        runtime.controller_epoch += 1
        runtime.last_epoch_reason = reason

    def _set_health(self, runtime: _Runtime, state: str, reason: str) -> None:
        if runtime.health_state == state:
            return
        previous = runtime.health_state
        runtime.health_state = state
        self._bump_epoch(runtime, reason)
        self._record(
            runtime,
            {
                "record_type": "health_transition",
                "from": previous,
                "to": state,
                "reason": reason,
                "epoch": runtime.controller_epoch,
            },
        )

    def _transition_passthrough(
        self,
        iid: str,
        i_obs: Mapping[str, Any],
        strong_phase: int | None,
    ) -> tuple[int | None, str]:
        if str(i_obs.get("stage", "GREEN")).upper() in _TRANSITION_STAGES:
            return strong_phase, "transition_passthrough"
        if i_obs.get("pending_phase") is not None:
            return strong_phase, "pending_passthrough"
        return strong_phase, "nominal_passthrough"

    def _choose_risk_phase(
        self,
        iid: str,
        ix: _IntersectionIndex,
        i_obs: Mapping[str, Any],
        strong_phase: int | None,
        risks: Mapping[str, ConnectionRisk],
        pressures: Mapping[int, float],
        lane_obs: Mapping[str, Mapping[str, Any]],
        prior: float,
        runtime: _Runtime,
    ) -> tuple[int | None, str, dict[str, Any]]:
        if strong_phase is None or strong_phase not in ix.phase_order:
            phase, reason = self._transition_passthrough(iid, i_obs, strong_phase)
            return phase, reason, {}
        if str(i_obs.get("stage", "GREEN")).upper() in _TRANSITION_STAGES:
            return strong_phase, "transition_passthrough", {}
        if i_obs.get("pending_phase") is not None:
            return strong_phase, "pending_passthrough", {}

        strong_connections = ix.phase_connections.get(strong_phase, ())
        active = [risks[cid] for cid in strong_connections if cid in risks and risks[cid].active]
        if not active:
            return strong_phase, "risk_observed_passthrough", {}

        scores: dict[int, tuple[float, float, bool]] = {}
        for phase_id in ix.phase_order:
            scores[phase_id] = _phase_score(
                ix,
                phase_id,
                pressures,
                risks,
                lane_obs,
                self._lane_meta,
                prior,
            )

        hard_active = any(risk.hard for risk in active)
        legal_safe = [
            phase_id
            for phase_id in ix.phase_order
            if not scores[phase_id][2]
        ]
        if hard_active:
            if legal_safe:
                target = max(
                    legal_safe,
                    key=lambda phase_id: (scores[phase_id][0], -ix.phase_order.index(phase_id)),
                )
                reason = "hard_block_override" if target != strong_phase else "hard_block_passthrough"
            else:
                # All phases send toward a blocked downstream.  Pick the
                # greatest observable release space, then official order.
                target = max(
                    ix.phase_order,
                    key=lambda phase_id: (scores[phase_id][1], -ix.phase_order.index(phase_id)),
                )
                reason = "overflow_clear"
            if reason in {"hard_block_override", "overflow_clear"}:
                self._set_health(runtime, "LOCAL_SAFE_MP", reason)
                return target, reason, {
                    "strong_phase": strong_phase,
                    "target_phase": target,
                    "scores": {str(k): list(v) for k, v in scores.items()},
                }
            return target, reason, {}

        # Soft risk: only change the action when the risk-aware candidate has a
        # strictly better bounded score than the Strong-MP request.
        target = max(
            legal_safe or list(ix.phase_order),
            key=lambda phase_id: (scores[phase_id][0], -ix.phase_order.index(phase_id)),
        )
        if target != strong_phase and scores[target][0] > scores[strong_phase][0] + _EPSILON:
            self._set_health(runtime, "LOCAL_SAFE_MP", "soft_risk_override")
            return target, "soft_risk_override", {
                "strong_phase": strong_phase,
                "target_phase": target,
                "scores": {str(k): list(v) for k, v in scores.items()},
            }
        return strong_phase, "soft_risk_passthrough", {
            "strong_phase": strong_phase,
            "target_phase": strong_phase,
            "scores": {str(k): list(v) for k, v in scores.items()},
        }

    def _refresh_v2x(
        self,
        observation: Mapping[str, Any],
        sim_time: float,
        plan_id: str,
    ) -> None:
        intersections = observation.get("intersections", {})
        if not isinstance(intersections, Mapping):
            return
        metadata_intersections = self._metadata.get("intersections", {})
        if not isinstance(metadata_intersections, Mapping):
            return
        prior = self._prior.effective(sim_time)
        confidence = float(prior.get("confidence", 0.0))
        for raw_iid, raw_state in intersections.items():
            iid = str(raw_iid)
            runtime = self._runtime.get(iid)
            if runtime is None:
                continue
            meta = _as_mapping(metadata_intersections.get(raw_iid, metadata_intersections.get(iid, {})))
            state = _as_mapping(raw_state)
            if not meta:
                continue
            try:
                previous_spat = runtime.last_spat
                if previous_spat and int(previous_spat.get("controller_epoch", -1)) != runtime.controller_epoch:
                    runtime.spat_invalidations.append(
                        build_invalidation(
                            intersection_id=iid,
                            controller_epoch=runtime.controller_epoch,
                            plan_id=plan_id,
                            sim_time=sim_time,
                            reason="controller_epoch_changed",
                        )
                    )
                    self._counter(runtime, "epoch_invalidation")
                payload = build_spat_v2_payload(
                    iid,
                    state,
                    meta,
                    sim_time=sim_time,
                    controller_epoch=runtime.controller_epoch,
                    plan_id=plan_id,
                    confidence=confidence,
                )
                validate_spat_v2(payload)
                runtime.last_spat = payload
                map_payload = build_map_v2_payload(
                    iid,
                    meta,
                    topology_version=str(self._metadata.get("topology_version", "unknown")),
                )
                validate_map_v2(map_payload)
                runtime.last_map = map_payload
            except Exception as exc:
                runtime.last_error = f"SPAT_MAP_INVALID:{exc}"
                self._counter(runtime, "spat_map_invalid")
                self._bump_epoch(runtime, "spat_map_invalid")
                runtime.last_spat = {}
                runtime.last_map = {}
                runtime.spat_invalidations.append(
                    build_invalidation(
                        intersection_id=iid,
                        controller_epoch=runtime.controller_epoch,
                        plan_id=plan_id,
                        sim_time=sim_time,
                        reason="spat_map_invalid",
                    )
                )

    def compute_actions(self, observation: Mapping[str, Any]) -> dict[str, int | None]:
        if not self._initialized:
            raise RuntimeError("Safe-MaxPressure is not initialized")
        sim_time = finite_float(observation.get("simulation_time"))
        self._step_time = sim_time
        step_id = observation.get("step_id")
        strong_actions = self._strong.compute_actions(dict(observation))
        final_actions = dict(strong_actions)

        try:
            self._prior.update(observation, sim_time)
            prior_effective = self._prior.effective(sim_time)
            prior_values = prior_effective.get("values", {})
            lane_obs = _lane_observations(observation)
            phase_pressures = {
                iid: _phase_pressure_proxy(ix, lane_obs, self._lane_meta)
                for iid, ix in self._index.items()
            }
            for iid, ix in self._index.items():
                runtime = self._runtime[iid]
                i_obs = _as_mapping(
                    observation.get("intersections", {}).get(iid, {})
                    if isinstance(observation.get("intersections", {}), Mapping)
                    else {}
                )
                strong_phase = strong_actions.get(iid)
                risks: dict[str, ConnectionRisk] = {}
                for cid, movement in ix.movements.items():
                    previous = runtime.risk_state.get(cid)
                    risk = _connection_risk(
                        movement,
                        lane_obs,
                        self._lane_meta,
                        previous,
                        runtime.risk_blocked.get(cid, False),
                    )
                    risks[cid] = risk
                    old_level = previous or "normal"
                    if risk.level != old_level:
                        self._bump_epoch(
                            runtime,
                            f"risk_{cid}_{risk.level}",
                        )
                    if risk.level == "normal" and old_level in {"soft", "hard", "hysteresis_held"}:
                        self._counter(runtime, "release")
                        self._record(
                            runtime,
                            {
                                "record_type": "risk",
                                "connection_id": cid,
                                "level": "release",
                                "ratio": risk.ratio,
                                "max_occupancy": risk.max_occupancy,
                                "reason": risk.reason,
                            },
                        )
                    elif risk.level != "normal" and risk.level != old_level:
                        self._record(
                            runtime,
                            {
                                "record_type": "risk",
                                "connection_id": cid,
                                "level": risk.level,
                                "ratio": risk.ratio,
                                "max_occupancy": risk.max_occupancy,
                                "blocked": risk.blocked,
                                "reason": risk.reason,
                            },
                        )
                    runtime.risk_state[cid] = risk.level if risk.level != "normal" else "normal"
                    runtime.risk_blocked[cid] = risk.blocked

                target, reason, detail = self._choose_risk_phase(
                    iid,
                    ix,
                    i_obs,
                    strong_phase,
                    risks,
                    phase_pressures.get(iid, {}),
                    lane_obs,
                    finite_float(prior_values.get(iid)),
                    runtime,
                )
                final_actions[iid] = target
                self._counter(runtime, reason)
                self._record(
                    runtime,
                    {
                        "record_type": "decision",
                        "step_id": step_id,
                        "simulation_time": sim_time,
                        "strong_requested_phase": strong_phase,
                        "final_phase": target,
                        "reason": reason,
                        "prior": finite_float(prior_values.get(iid)),
                        "risks": {
                            cid: {
                                "level": risk.level,
                                "ratio": risk.ratio,
                                "max_occupancy": risk.max_occupancy,
                                "blocked": risk.blocked,
                                "demand": risk.demand,
                                "reason": risk.reason,
                            }
                            for cid, risk in risks.items()
                        },
                        "detail": detail,
                    },
                )
                if reason in {"soft_risk_override", "hard_block_override", "overflow_clear"}:
                    self._set_health(runtime, "LOCAL_SAFE_MP", reason)
                elif prior_effective.get("status") in {"soft_stale", "hard_expired", "unavailable"}:
                    self._set_health(runtime, "DEGRADED_CLOUD_OFF", "cloud_stale_or_expired")
                elif runtime.health_state == "DEGRADED_CLOUD_OFF" and reason == "nominal_passthrough":
                    self._set_health(runtime, "HEALTHY", "cloud_recovered")

                if (
                    not any(risk.active for risk in risks.values())
                    and prior_effective.get("status") == "healthy"
                    and runtime.health_state in {
                        "LOCAL_SAFE_MP",
                        "DEGRADED_CLOUD_OFF",
                        "STRONG_B_FALLBACK",
                    }
                ):
                    self._set_health(runtime, "HEALTHY", "risk_cleared")

        except Exception as exc:
            # A Safe-MP calculation error must not take down the signal
            # controller.  The frozen Strong-MP action is the fail-closed path.
            final_actions = dict(strong_actions)
            for runtime in self._runtime.values():
                runtime.last_error = f"SAFE_MP_ERROR:{exc}"
                self._counter(runtime, "strong_b_fallback")
                self._set_health(runtime, "STRONG_B_FALLBACK", "safe_mp_error")
            logger.exception("Safe-MP risk layer failed; using Strong B")

        plan_id = f"{self._episode_id}:{step_id}:{max(runtime.controller_epoch for runtime in self._runtime.values()) if self._runtime else 0}"
        self._refresh_v2x(observation, sim_time, plan_id)
        trace = {
            "step_id": step_id,
            "requested": strong_actions,
            "final": final_actions,
        }
        encoded = json.dumps(trace, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self._trace_hasher.update(encoded.encode("utf-8") + b"\n")
        self._trace_count += 1
        self._step_count += 1
        self._last_actions = dict(final_actions)
        return final_actions

    def diagnostics(self) -> dict[str, Any]:
        runtime_payload: dict[str, Any] = {}
        aggregate_counters: dict[str, int] = {}
        total_invalidations = 0
        for iid, runtime in self._runtime.items():
            for key, value in runtime.counters.items():
                aggregate_counters[key] = aggregate_counters.get(key, 0) + int(value)
            total_invalidations += len(runtime.spat_invalidations)
            runtime_payload[iid] = {
                "health_state": runtime.health_state,
                "controller_epoch": runtime.controller_epoch,
                "last_epoch_reason": runtime.last_epoch_reason,
                "risk_state": dict(runtime.risk_state),
                "risk_blocked": dict(runtime.risk_blocked),
                "counters": dict(runtime.counters),
                "last_error": runtime.last_error,
                "records": list(runtime.records[-200:]),
                "last_spat": dict(runtime.last_spat),
                "last_map": dict(runtime.last_map),
                "spat_invalidations": list(runtime.spat_invalidations[-20:]),
                "advisory_invalidated": bool(runtime.spat_invalidations),
            }
        return {
            "algorithm": "safe_max_pressure",
            "signal_mode": "safe_max_pressure",
            "strong_reference": "traffic_control.max_pressure.MaxPressureController",
            "episode_id": self._episode_id,
            "steps": self._step_count,
            "signal_trace_sha256": self._trace_hasher.hexdigest(),
            "signal_trace_count": self._trace_count,
            "health_state": {
                iid: runtime.health_state for iid, runtime in self._runtime.items()
            },
            "controller_epoch": {
                iid: runtime.controller_epoch for iid, runtime in self._runtime.items()
            },
            "committed_spat_published": False,
            "vehicle_vtc_eligible": False,
            "vehicle_actions": {},
            "counters": aggregate_counters,
            "spat_invalidations_count": total_invalidations,
            "risk_thresholds": {
                "soft": SOFT_THRESHOLD,
                "hard": HARD_THRESHOLD,
                "release": RELEASE_THRESHOLD,
            },
            "cloud": self._prior.diagnostics(
                self._step_time if hasattr(self, "_step_time") else None
            ),
            "intersections": runtime_payload,
        }

    def finish(self) -> dict[str, Any]:
        diagnostics = self.diagnostics()
        output_path = os.environ.get("SAFE_MP_DIAGNOSTICS_PATH", "").strip()
        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(diagnostics, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        self._last_diagnostics = diagnostics
        self._initialized = False
        return diagnostics


_controller: SafeMaxPressureController | None = None
_last_diagnostics: dict[str, Any] | None = None


def initialize(payload: dict[str, Any]) -> dict[str, Any]:
    global _controller, _last_diagnostics
    if os.environ.get("SAFE_MP_PRODUCTION", "0").strip() == "1":
        raise ValueError(
            "safe_max_pressure is validation-only; production mode is fail-closed"
        )
    _controller = SafeMaxPressureController(payload)
    _last_diagnostics = None
    return initialize_response(episode_id=str(payload.get("episode_id", "")))


def step(payload: dict[str, Any]) -> dict[str, Any]:
    if _controller is None:
        raise RuntimeError("Safe-MaxPressure is not initialized")
    actions = _controller.compute_actions(payload)
    return step_response(
        episode_id=str(payload.get("episode_id", "")),
        step_id=payload.get("step_id"),
        signals=signals_from_phase_map(actions),
        vehicles={},
    )


def finish(payload: dict[str, Any]) -> dict[str, Any]:
    global _controller, _last_diagnostics
    if _controller is None:
        return finish_response(already_finished=True)
    diagnostics = _controller.finish()
    _last_diagnostics = diagnostics
    _controller = None
    return finish_response()


def diagnostics() -> dict[str, Any]:
    if _controller is not None:
        return _controller.diagnostics()
    return dict(_last_diagnostics or {
        "algorithm": "safe_max_pressure",
        "committed_spat_published": False,
        "vehicle_vtc_eligible": False,
    })


__all__ = [
    "ConnectionRisk",
    "SafeMaxPressureController",
    "diagnostics",
    "finish",
    "initialize",
    "step",
]
