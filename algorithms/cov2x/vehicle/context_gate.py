"""Pure, preregistered context gate for Vehicle speed-advice diagnostics.

The gate consumes only the pre-action Protocol 2.0 payload, the current Road
action, and static signal metadata.  It never reads later rows from the frozen
Road/Cloud replay tape.  The output is a diagnostic category and, only for an
eligible state, a queue-aware reference-speed cap.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping, Sequence

from algorithms.cov2x.contract import NATIVE_RELEASE_TOLERANCE_MPS
from algorithms.cov2x.vehicle.actuator import (
    LEADER_HEADWAY_SECONDS,
    MIN_CONSTRAINT_DISTANCE_M,
    vehicle_limits,
)
from algorithms.cov2x.vehicle.movement_corridor import MovementApproachCorridor


PASS_CURRENT_GREEN = "PASS_CURRENT_GREEN"
ADVICE_ELIGIBLE = "ADVICE_ELIGIBLE"
NO_FEASIBLE_ADVICE = "NO_FEASIBLE_ADVICE"

QUEUE_AWARE_GLOSA_ARM = "QUEUE_AWARE_GLOSA"
FIXED_CAP_ARMS = ("u=-0.25", "u=-0.50", "u=-0.75")
RELEASE_ARMS = ("NATIVE_RELEASE", "NATIVE_RELEASE_PLACEBO")
ELIGIBLE_ARMS = RELEASE_ARMS + FIXED_CAP_ARMS + (QUEUE_AWARE_GLOSA_ARM,)
PASS_CURRENT_GREEN_ARMS = RELEASE_ARMS + FIXED_CAP_ARMS
NO_FEASIBLE_ARMS = RELEASE_ARMS


@dataclass(frozen=True)
class GateConfig:
    sumo_step_s: float = 0.05
    decision_interval_s: float = 5.0
    horizon_s: float = 20.0
    queue_seconds_per_meter: float = 0.21
    queue_startup_s: float = 3.0
    glosa_min_speed_mps: float = 5.0
    native_speed_floor_mps: float = 0.1
    release_tolerance_mps: float = NATIVE_RELEASE_TOLERANCE_MPS
    leader_headway_s: float = LEADER_HEADWAY_SECONDS
    minimum_constraint_distance_m: float = MIN_CONSTRAINT_DISTANCE_M

    def __post_init__(self) -> None:
        positive = (
            "sumo_step_s",
            "decision_interval_s",
            "horizon_s",
            "queue_seconds_per_meter",
            "glosa_min_speed_mps",
            "native_speed_floor_mps",
            "leader_headway_s",
            "minimum_constraint_distance_m",
        )
        for name in positive:
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        if not math.isfinite(self.queue_startup_s) or self.queue_startup_s < 0.0:
            raise ValueError("queue_startup_s must be finite and non-negative")
        if not math.isfinite(self.release_tolerance_mps) or self.release_tolerance_mps < 0.0:
            raise ValueError("release_tolerance_mps must be finite and non-negative")


@dataclass(frozen=True)
class _Window:
    source: str
    phase_id: int
    nominal_start_s: float
    nominal_end_s: float
    usable_start_s: float
    usable_end_s: float
    target_arrival_time_s: float
    queue_clearance_delay_s: float


@dataclass(frozen=True)
class GateDecision:
    category: str
    reason: str
    native_arrival_time_s: float | None = None
    nominal_window_start_s: float | None = None
    nominal_window_end_s: float | None = None
    usable_window_start_s: float | None = None
    usable_window_end_s: float | None = None
    target_arrival_time_s: float | None = None
    queue_clearance_delay_s: float | None = None
    reference_speed_cap_mps: float | None = None
    window_source: str | None = None
    window_phase_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def arms_for_category(category: str) -> tuple[str, ...]:
    if category == ADVICE_ELIGIBLE:
        return ELIGIBLE_ARMS
    if category == PASS_CURRENT_GREEN:
        return PASS_CURRENT_GREEN_ARMS
    if category == NO_FEASIBLE_ADVICE:
        return NO_FEASIBLE_ARMS
    raise ValueError(f"unknown context-gate category: {category}")


def _finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("value must be finite")
    return result


def _normalized_phase_map(raw: Mapping[Any, Any]) -> dict[int, frozenset[str]]:
    result: dict[int, frozenset[str]] = {}
    for phase, movements in raw.items():
        if isinstance(movements, str):
            values = movements.split("|")
        else:
            values = movements or ()
        result[int(phase)] = frozenset(
            str(value).strip() for value in values if str(value).strip()
        )
    return result


def _normalized_timings(raw: Mapping[Any, Any]) -> dict[int, dict[str, float]]:
    result: dict[int, dict[str, float]] = {}
    for phase, value in raw.items():
        item = dict(value or {})
        result[int(phase)] = {
            "green_s": _finite(item.get("green_s", item.get("green_seconds", 0.0))),
            "yellow_s": _finite(item.get("yellow_s", item.get("yellow_seconds", 0.0))),
            "clearance_s": _finite(
                item.get("clearance_s", item.get("clearance_seconds", 0.0))
            ),
        }
    return result


def _no_feasible(reason: str, *, native_arrival: float | None = None) -> GateDecision:
    return GateDecision(
        category=NO_FEASIBLE_ADVICE,
        reason=str(reason),
        native_arrival_time_s=native_arrival,
    )


def _window_decision(
    *,
    category: str,
    reason: str,
    native_arrival: float,
    window: _Window,
    cap: float | None = None,
) -> GateDecision:
    return GateDecision(
        category=category,
        reason=reason,
        native_arrival_time_s=native_arrival,
        nominal_window_start_s=window.nominal_start_s,
        nominal_window_end_s=window.nominal_end_s,
        usable_window_start_s=window.usable_start_s,
        usable_window_end_s=window.usable_end_s,
        target_arrival_time_s=window.target_arrival_time_s,
        queue_clearance_delay_s=window.queue_clearance_delay_s,
        reference_speed_cap_mps=cap,
        window_source=window.source,
        window_phase_id=window.phase_id,
    )


def _causal_windows(
    snapshot: Mapping[str, Any], config: GateConfig
) -> list[_Window]:
    movement = str(snapshot["movement_id"])
    phase_movements = _normalized_phase_map(snapshot["phase_movements"])
    timings = _normalized_timings(snapshot["phase_timings"])
    current = int(snapshot["current_phase"])
    target = int(snapshot["target_phase"])
    if current not in timings or target not in timings:
        return []
    stage = str(snapshot["stage"]).upper()
    elapsed = max(0.0, _finite(snapshot["stage_elapsed_s"]))
    minimum_green = max(0.0, _finite(snapshot["minimum_green_s"]))
    queue_length = max(0.0, _finite(snapshot["queue_length_m"]))
    full_queue_delay = (
        config.queue_seconds_per_meter * queue_length + config.queue_startup_s
    )
    windows: list[_Window] = []

    if stage in {"G", "GREEN"} and movement in phase_movements.get(current, ()):
        if target != current:
            planned_remaining = max(0.0, minimum_green - elapsed)
        else:
            planned_remaining = max(
                config.decision_interval_s,
                max(0.0, minimum_green - elapsed),
            )
        queue_residual = max(0.0, full_queue_delay - elapsed)
        usable_start = queue_residual
        usable_end = planned_remaining - config.sumo_step_s
        target_arrival = usable_start + config.sumo_step_s
        if target_arrival <= usable_end + 1e-9:
            windows.append(
                _Window(
                    source="current_green",
                    phase_id=current,
                    nominal_start_s=0.0,
                    nominal_end_s=planned_remaining,
                    usable_start_s=usable_start,
                    usable_end_s=usable_end,
                    target_arrival_time_s=target_arrival,
                    queue_clearance_delay_s=queue_residual,
                )
            )

    current_is_target_green = (
        stage in {"G", "GREEN"}
        and current == target
        and movement in phase_movements.get(current, ())
    )
    pending = snapshot.get("pending_phase")
    if stage in {"G", "GREEN"}:
        next_window_committed = (
            target != current and (pending is None or int(pending) == target)
        )
    elif stage in {"YELLOW", "CLEARANCE", "ALL_RED"}:
        next_window_committed = pending is not None and int(pending) == target
    else:
        next_window_committed = False
    if (
        not current_is_target_green
        and next_window_committed
        and movement in phase_movements.get(target, ())
    ):
        if stage in {"G", "GREEN"}:
            nominal_start = (
                max(0.0, minimum_green - elapsed)
                + timings[current]["yellow_s"]
                + timings[current]["clearance_s"]
            )
        elif stage == "YELLOW":
            nominal_start = (
                max(0.0, timings[current]["yellow_s"] - elapsed)
                + timings[current]["clearance_s"]
            )
        elif stage in {"CLEARANCE", "ALL_RED"}:
            nominal_start = max(0.0, timings[current]["clearance_s"] - elapsed)
        else:
            nominal_start = math.inf
        nominal_end = nominal_start + minimum_green
        usable_start = nominal_start + full_queue_delay
        usable_end = nominal_end - config.sumo_step_s
        target_arrival = usable_start + config.sumo_step_s
        if (
            math.isfinite(nominal_start)
            and target_arrival <= usable_end + 1e-9
        ):
            windows.append(
                _Window(
                    source="committed_next_green",
                    phase_id=target,
                    nominal_start_s=nominal_start,
                    nominal_end_s=nominal_end,
                    usable_start_s=usable_start,
                    usable_end_s=usable_end,
                    target_arrival_time_s=target_arrival,
                    queue_clearance_delay_s=full_queue_delay,
                )
            )
    return sorted(windows, key=lambda value: (value.target_arrival_time_s, value.source))


def classify_gate(
    snapshot: Mapping[str, Any], config: GateConfig | None = None
) -> GateDecision:
    """Classify one pre-action snapshot without reading any outcome."""
    config = config or GateConfig()
    if snapshot.get("context_error"):
        return _no_feasible(f"context_error:{snapshot['context_error']}")
    try:
        distance = _finite(snapshot["distance_m"])
        speed = _finite(snapshot["speed_mps"])
        base_speed = _finite(snapshot["base_speed_mps"])
        minimum_gap = max(0.0, _finite(snapshot["minimum_gap_m"]))
        max_decel = _finite(snapshot["max_decel_mps2"])
        if distance <= 0.0 or speed <= 0.0 or base_speed <= 0.0 or max_decel <= 0.0:
            return _no_feasible("invalid_kinematic_envelope")
        native_arrival = distance / max(speed, config.native_speed_floor_mps)
        raw_gap = snapshot.get("leader_gap_m")
        if raw_gap is not None:
            gap = _finite(raw_gap)
            leader_horizon = max(
                config.minimum_constraint_distance_m,
                minimum_gap + speed * config.leader_headway_s,
            )
            if gap <= leader_horizon + 1e-9:
                return _no_feasible(
                    "leader_gap_not_feasible", native_arrival=native_arrival
                )
        windows = _causal_windows(snapshot, config)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _no_feasible(f"invalid_gate_context:{type(error).__name__}")

    if not windows:
        return _no_feasible(
            "no_causally_available_usable_window", native_arrival=native_arrival
        )

    for window in windows:
        if (
            window.target_arrival_time_s - 1e-9
            <= native_arrival
            <= window.usable_end_s + 1e-9
        ):
            if window.source == "current_green":
                return _window_decision(
                    category=PASS_CURRENT_GREEN,
                    reason="native_release_passes_current_green",
                    native_arrival=native_arrival,
                    window=window,
                )
            return _window_decision(
                category=NO_FEASIBLE_ADVICE,
                reason="native_release_reaches_usable_window",
                native_arrival=native_arrival,
                window=window,
            )

    candidates = [
        window
        for window in windows
        if native_arrival < window.target_arrival_time_s - 1e-9
        and window.target_arrival_time_s <= config.horizon_s + 1e-9
    ]
    if not candidates:
        return _no_feasible(
            "cap_only_advice_cannot_reach_usable_window",
            native_arrival=native_arrival,
        )
    window = candidates[0]
    cap = distance / window.target_arrival_time_s
    if cap < config.glosa_min_speed_mps - 1e-9:
        return _no_feasible("reference_cap_below_glosa_minimum", native_arrival=native_arrival)
    if cap >= base_speed - config.release_tolerance_mps - 1e-9:
        return _no_feasible("reference_cap_is_native_release", native_arrival=native_arrival)
    required_decel = max(0.0, (speed * speed - cap * cap) / (2.0 * distance))
    if required_decel > max_decel + 1e-9:
        return _no_feasible("reference_cap_deceleration_not_feasible", native_arrival=native_arrival)
    return _window_decision(
        category=ADVICE_ELIGIBLE,
        reason="queue_aware_cap_reaches_usable_window",
        native_arrival=native_arrival,
        window=window,
        cap=cap,
    )


def build_gate_evidence(
    *,
    metadata: Mapping[str, Any],
    payload: Mapping[str, Any],
    joint_action: Mapping[str, Any],
    opportunity: Mapping[str, Any],
    config: GateConfig | None = None,
) -> dict[str, Any]:
    """Extract route-resolved observable context and evaluate the pure gate."""
    config = config or GateConfig()
    vehicle_id = str(opportunity.get("vehicle_id", ""))
    tls_id = str(opportunity.get("intersection_id", ""))
    vehicle = (payload.get("vehicles") or {}).get(vehicle_id)
    static_intersection = (metadata.get("intersections") or {}).get(tls_id)
    dynamic_intersection = (payload.get("intersections") or {}).get(tls_id)
    try:
        if not isinstance(vehicle, Mapping):
            raise ValueError("vehicle_missing")
        if not isinstance(static_intersection, Mapping):
            raise ValueError("static_intersection_missing")
        if not isinstance(dynamic_intersection, Mapping):
            raise ValueError("dynamic_intersection_missing")
        resolution = MovementApproachCorridor(metadata).resolve(tls_id, vehicle)
        if not resolution.resolved:
            raise ValueError(f"corridor:{resolution.failure_reason}")
        movement = str(resolution.resolved_movement_id)
        if movement != str(opportunity.get("movement_id", "")):
            raise ValueError("movement_identity_mismatch")
        dynamic_lanes = dynamic_intersection.get("lanes") or {}
        queue_values = []
        for lane_id in resolution.controlled_terminal_lane_ids:
            lane = dynamic_lanes.get(lane_id)
            if not isinstance(lane, Mapping) or "queue_length_m" not in lane:
                raise ValueError(f"queue_length_missing:{lane_id}")
            queue_values.append(max(0.0, _finite(lane["queue_length_m"])))
        if not queue_values:
            raise ValueError("terminal_lane_queue_missing")
        phases = static_intersection.get("phases") or {}
        phase_movements = {
            int(phase): str((value or {}).get("movement", ""))
            for phase, value in phases.items()
        }
        phase_timings = {
            int(phase): {
                "green_s": (value or {}).get("green_seconds", 0.0),
                "yellow_s": (value or {}).get("yellow_seconds", 0.0),
                "clearance_s": (value or {}).get("clearance_seconds", 0.0),
            }
            for phase, value in phases.items()
        }
        signal_actions = joint_action.get("signals") or {}
        target_phase = int(signal_actions[tls_id]["target_phase"])
        limits = vehicle_limits(vehicle, metadata.get("vehicle_types") or {})
        gate_observation = {
            "movement_id": movement,
            "distance_m": opportunity.get("signal_distance_m"),
            "speed_mps": opportunity.get("speed_mps"),
            "base_speed_mps": opportunity.get("base_speed_mps"),
            "leader_gap_m": opportunity.get("leader_gap_m"),
            "minimum_gap_m": opportunity.get("minimum_gap_m", limits.min_gap_m),
            "max_decel_mps2": limits.decel_mps2,
            "queue_length_m": max(queue_values),
            "current_phase": dynamic_intersection.get("current_phase"),
            "pending_phase": dynamic_intersection.get("pending_phase"),
            "stage": dynamic_intersection.get("stage"),
            "stage_elapsed_s": dynamic_intersection.get("stage_elapsed", 0.0),
            "target_phase": target_phase,
            "minimum_green_s": static_intersection.get(
                "minimum_green", metadata.get("minimum_green", 5.0)
            ),
            "phase_movements": phase_movements,
            "phase_timings": phase_timings,
            "controlled_terminal_lane_ids": list(
                resolution.controlled_terminal_lane_ids
            ),
            "queue_aggregation": "max_route_resolved_terminal_lane_queue_length_m",
            "next_window_source": "current_signal_state_current_road_action_static_timing",
        }
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        gate_observation = {"context_error": str(error)}
    decision = classify_gate(gate_observation, config)
    return {
        "gate_observation": deepcopy(gate_observation),
        "gate_decision": decision.to_dict(),
    }


def _candidate_valid(
    row: Mapping[str, Any],
    *,
    selected_for_seed: Sequence[Mapping[str, Any]],
    minimum_spacing_s: float,
    episode_duration_s: float,
    horizon_s: float,
) -> bool:
    opportunity = dict(row.get("opportunity") or {})
    identity = (
        str(opportunity.get("vehicle_id", "")),
        str(opportunity.get("movement_id", "")),
    )
    now = float(row.get("simulation_time", 0.0))
    if not (
        all(identity)
        and opportunity.get("intersection_id")
        and opportunity.get("previous_advice_mps") is None
        and opportunity.get("transition_kind") == "native_release"
        and now + float(horizon_s) <= float(episode_duration_s) + 1e-9
    ):
        return False
    for selected in selected_for_seed:
        selected_opportunity = dict(selected.get("opportunity") or {})
        selected_identity = (
            str(selected_opportunity.get("vehicle_id", "")),
            str(selected_opportunity.get("movement_id", "")),
        )
        if identity == selected_identity:
            return False
        if abs(now - float(selected.get("simulation_time", 0.0))) + 1e-9 < float(
            minimum_spacing_s
        ):
            return False
    return True


def select_period_treatments(
    candidates_by_seed: Mapping[int, Sequence[Mapping[str, Any]]],
    *,
    quotas: Mapping[str, int],
    minimum_spacing_s: float,
    episode_duration_s: float,
    horizon_s: float,
    max_per_seed: int,
) -> list[dict[str, Any]]:
    """Select preregistered category quotas, primary category first.

    Seeds are visited round-robin.  No threshold or category can change after
    this deterministic selection is run.
    """
    category_order = (ADVICE_ELIGIBLE, PASS_CURRENT_GREEN, NO_FEASIBLE_ADVICE)
    if set(quotas) != set(category_order):
        raise ValueError("quotas must cover the three frozen gate categories")
    if any(int(quotas[category]) < 0 for category in category_order):
        raise ValueError("category quotas must be non-negative")
    if int(max_per_seed) <= 0:
        raise ValueError("max_per_seed must be positive")
    seeds = tuple(sorted(int(seed) for seed in candidates_by_seed))
    ordered = {
        seed: sorted(
            (deepcopy(dict(row)) for row in candidates_by_seed[seed]),
            key=lambda row: (
                float(row.get("simulation_time", 0.0)),
                int(row.get("step_id", 0)),
                str((row.get("opportunity") or {}).get("vehicle_id", "")),
            ),
        )
        for seed in seeds
    }
    selected_by_seed: dict[int, list[dict[str, Any]]] = {seed: [] for seed in seeds}
    selected: list[dict[str, Any]] = []
    for category in category_order:
        target = int(quotas[category])
        count = 0
        while count < target:
            progressed = False
            for seed in seeds:
                if count >= target:
                    break
                if len(selected_by_seed[seed]) >= int(max_per_seed):
                    continue
                for row in ordered[seed]:
                    decision = dict(
                        (row.get("opportunity") or {}).get("gate_decision") or {}
                    )
                    if decision.get("category") != category:
                        continue
                    if not _candidate_valid(
                        row,
                        selected_for_seed=selected_by_seed[seed],
                        minimum_spacing_s=minimum_spacing_s,
                        episode_duration_s=episode_duration_s,
                        horizon_s=horizon_s,
                    ):
                        continue
                    row["seed"] = seed
                    row["horizon_s"] = float(horizon_s)
                    row["category"] = category
                    selected.append(row)
                    selected_by_seed[seed].append(row)
                    ordered[seed].remove(row)
                    count += 1
                    progressed = True
                    break
            if not progressed:
                break
    return selected
