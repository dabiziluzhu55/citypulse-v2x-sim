"""No-training paired actuator characterization runtime.

This Protocol 2.0 local module deliberately holds Strong-MP signal decisions
for the five-second scripted Vehicle policy cadence while the outer worker calls
it every SUMO step.  It never imports or invokes the CoV2X PPO stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from statistics import mean
from typing import Any, Mapping

from algorithms.cov2x.vehicle.actuator import (
    AdapterMode,
    ConstraintState,
    StateClassification,
    VehicleLimits,
    classify_constraint,
    eligible_for_command,
    is_realized,
    outcome_reason,
    realized_acceleration,
    scripted_acceleration,
    speed_reference,
    transfer_gain,
    vehicle_limits,
)
from traffic_control.max_pressure import MaxPressureController
from traffic_control.protocol import (
    finish_response,
    initialize_response,
    signals_from_phase_map,
    step_response,
)


@dataclass
class _Issued:
    vehicle_id: str
    policy_generation: int
    state: ConstraintState
    state_evidence: str
    requested_acceleration_mps2: float
    target_speed_mps: float
    reference_clipped: bool
    speed_before_mps: float
    issued_at_s: float
    limits: VehicleLimits
    leader_gap_m: float | None
    next_signal_id: str | None
    next_signal_state: str | None
    next_signal_distance_m: float | None
    type_id: str
    road_id: str
    waiting_time_s: float
    allowed_speed_mps: float


@dataclass
class _Lease:
    vehicle_id: str
    policy_generation: int
    state: ConstraintState
    state_evidence: str
    requested_acceleration_mps2: float
    start_speed_mps: float
    start_time_s: float
    limits: VehicleLimits
    last_speed_mps: float
    last_time_s: float
    accepted_steps: int = 0
    issued_steps: int = 0
    first_realized_at_s: float | None = None
    target_errors_mps: list[float] = field(default_factory=list)
    clamp_reasons: dict[str, int] = field(default_factory=dict)
    outcome_reasons: dict[str, int] = field(default_factory=dict)
    observed_constraint_states: dict[str, int] = field(default_factory=dict)
    type_id: str = ""
    start_road_id: str = ""
    start_waiting_time_s: float = 0.0
    start_allowed_speed_mps: float = 0.0


_adapter_mode = AdapterMode.ONE_SHOT
_episode_id = ""
_step_length_s = 0.05
_policy_cadence_s = 5.0
_max_per_state = 2
_vehicle_types: dict[str, Any] = {}
_signal_controller: MaxPressureController | None = None
_signal_actions: dict[str, dict[str, int]] = {}
_next_policy_time_s = 0.0
_policy_generation = -1
_leases: dict[str, _Lease] = {}
_issued: dict[str, _Issued] = {}
_step_rows: list[dict[str, Any]] = []
_lease_rows: list[dict[str, Any]] = []
_report: dict[str, Any] | None = None
_finished = True


def _float_env(name: str, default: float) -> float:
    value = float(os.environ.get(name, str(default)) or default)
    if value <= 0.0:
        raise ValueError(f"{name} must be positive")
    return value


def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _adapter_mode, _episode_id, _step_length_s, _policy_cadence_s
    global _max_per_state, _vehicle_types, _signal_controller, _signal_actions
    global _next_policy_time_s, _policy_generation, _leases, _issued
    global _step_rows, _lease_rows, _report, _finished

    _adapter_mode = AdapterMode(
        os.environ.get("COV2X_ACTUATOR_ADAPTER", AdapterMode.ONE_SHOT.value)
    )
    _episode_id = str(payload.get("episode_id", ""))
    _step_length_s = float(payload.get("decision_interval", 0.05) or 0.05)
    _policy_cadence_s = _float_env("COV2X_ACTUATOR_POLICY_CADENCE", 5.0)
    _max_per_state = int(os.environ.get("COV2X_ACTUATOR_MAX_PER_STATE", "2"))
    if _max_per_state <= 0:
        raise ValueError("COV2X_ACTUATOR_MAX_PER_STATE must be positive")
    _vehicle_types = dict(payload.get("vehicle_types", {}) or {})
    _signal_controller = MaxPressureController(dict(payload))
    _signal_actions = {}
    _next_policy_time_s = 0.0
    _policy_generation = -1
    _leases = {}
    _issued = {}
    _step_rows = []
    _lease_rows = []
    _report = None
    _finished = False
    return initialize_response(episode_id=_episode_id)


def _vehicle_speed(vehicle: Mapping[str, Any]) -> float:
    return max(0.0, float((vehicle.get("motion") or {}).get("speed_mps", 0.0)))


def _vehicle_vmax(vehicle: Mapping[str, Any], limits: VehicleLimits) -> float:
    allowed = float(
        (vehicle.get("motion") or {}).get("allowed_speed_mps", limits.max_speed_mps)
    )
    return max(0.0, min(allowed, limits.max_speed_mps))


def _settle_previous(payload: Mapping[str, Any], now_s: float) -> None:
    previous = payload.get("previous_action_results") or {}
    raw_results = previous.get("vehicles", {}) if isinstance(previous, Mapping) else {}
    if not isinstance(raw_results, Mapping):
        raw_results = {}
    current_vehicles = payload.get("vehicles", {}) or {}
    for vehicle_id, issued in list(_issued.items()):
        raw = raw_results.get(vehicle_id) or {}
        status = raw.get("speed_status") if isinstance(raw, Mapping) else None
        actual = raw.get("actual_speed_mps") if isinstance(raw, Mapping) else None
        accepted = status == "applied" and actual is not None
        realized = None
        gain = None
        within = False
        if actual is not None:
            realized = realized_acceleration(
                issued.speed_before_mps, float(actual), max(1e-9, now_s - issued.issued_at_s)
            )
            gain = transfer_gain(realized, issued.requested_acceleration_mps2)
            within = is_realized(realized, issued.requested_acceleration_mps2)
        native_reason, result_reason = outcome_reason(
            accepted=accepted,
            status=str(status) if status is not None else None,
            realized=within,
            state=issued.state,
            reference_clipped=issued.reference_clipped,
            requested_mps2=issued.requested_acceleration_mps2,
            realized_mps2=realized,
            limits=issued.limits,
        )
        target_error = (
            None if actual is None else float(actual) - issued.target_speed_mps
        )
        current_vehicle = current_vehicles.get(vehicle_id) or {}
        current_signal = (
            current_vehicle.get("next_signal")
            if isinstance(current_vehicle, Mapping)
            else None
        )
        current_signal_id = (
            str(current_signal.get("tls_id"))
            if isinstance(current_signal, Mapping) and current_signal.get("tls_id") is not None
            else None
        )
        restrictive_before = issued.next_signal_state not in {None, "G", "g"}
        crossing_distance = max(1.0, issued.speed_before_mps * max(1e-9, now_s - issued.issued_at_s) + 0.5)
        red_light_crossing_proxy = bool(
            restrictive_before
            and issued.next_signal_distance_m is not None
            and issued.next_signal_distance_m <= crossing_distance
            and current_signal_id != issued.next_signal_id
            and actual is not None
            and float(actual) > 0.1
        )
        current_gap = (
            current_vehicle.get("leader_gap_m")
            if isinstance(current_vehicle, Mapping)
            else None
        )
        minimum_gap_breach = bool(
            current_gap is not None and float(current_gap) + 1e-9 < issued.limits.min_gap_m
        )
        current_classification = (
            classify_constraint(current_vehicle, issued.limits)
            if isinstance(current_vehicle, Mapping) and current_vehicle
            else StateClassification(ConstraintState.UNCLASSIFIED, "vehicle_absent")
        )
        row = {
            "episode_id": _episode_id,
            "adapter": _adapter_mode.value,
            "vehicle_id": vehicle_id,
            "policy_generation": issued.policy_generation,
            "constraint_state": issued.state.value,
            "constraint_evidence": issued.state_evidence,
            "issued_at_s": issued.issued_at_s,
            "observed_at_s": now_s,
            "command_accepted": accepted,
            "speed_status": status,
            "requested_acceleration_mps2": issued.requested_acceleration_mps2,
            "realized_acceleration_mps2": realized,
            "transfer_gain": gain,
            "realized_within_tolerance": within,
            "target_speed_mps": issued.target_speed_mps,
            "actual_speed_mps": actual,
            "target_speed_error_mps": target_error,
            "native_safety_clamp_reason": native_reason,
            "outcome_reason": result_reason,
            "type_id": issued.type_id,
            "road_id_at_issue": issued.road_id,
            "waiting_time_at_issue_s": issued.waiting_time_s,
            "allowed_speed_at_issue_mps": issued.allowed_speed_mps,
            "leader_gap_m": current_gap,
            "minimum_gap_breach": minimum_gap_breach,
            "red_light_crossing_proxy": red_light_crossing_proxy,
            "observed_constraint_state": current_classification.state.value,
        }
        _step_rows.append(row)
        lease = _leases.get(vehicle_id)
        if lease is not None and lease.policy_generation == issued.policy_generation:
            lease.issued_steps += 1
            lease.accepted_steps += int(accepted)
            if actual is not None:
                lease.last_speed_mps = float(actual)
                lease.last_time_s = now_s
            if target_error is not None:
                lease.target_errors_mps.append(float(target_error))
            if within and lease.first_realized_at_s is None:
                lease.first_realized_at_s = now_s
            if native_reason is not None:
                lease.clamp_reasons[native_reason] = lease.clamp_reasons.get(native_reason, 0) + 1
            lease.outcome_reasons[result_reason] = lease.outcome_reasons.get(result_reason, 0) + 1
            state_key = current_classification.state.value
            lease.observed_constraint_states[state_key] = lease.observed_constraint_states.get(state_key, 0) + 1
    _issued.clear()


def _close_lease(lease: _Lease, *, end_time_s: float, terminal_reason: str) -> None:
    elapsed = max(0.0, lease.last_time_s - lease.start_time_s)
    realized = (
        None
        if elapsed <= 1e-9
        else realized_acceleration(lease.start_speed_mps, lease.last_speed_mps, elapsed)
    )
    gain = (
        None
        if realized is None
        else transfer_gain(realized, lease.requested_acceleration_mps2)
    )
    within = bool(
        realized is not None
        and is_realized(realized, lease.requested_acceleration_mps2)
        and lease.accepted_steps == lease.issued_steps
        and lease.issued_steps > 0
    )
    tracking_lag = (
        None
        if lease.first_realized_at_s is None
        else max(0.0, lease.first_realized_at_s - lease.start_time_s)
    )
    state_stable = all(
        state == lease.state.value or count == 0
        for state, count in lease.observed_constraint_states.items()
    )
    eligible_for_gate = bool(
        elapsed + 1e-9 >= 0.90 * _policy_cadence_s
        and state_stable
        and terminal_reason == "lease_completion"
    )
    _lease_rows.append({
        "episode_id": _episode_id,
        "adapter": _adapter_mode.value,
        "vehicle_id": lease.vehicle_id,
        "policy_generation": lease.policy_generation,
        "constraint_state": lease.state.value,
        "constraint_evidence": lease.state_evidence,
        "type_id": lease.type_id,
        "start_road_id": lease.start_road_id,
        "start_waiting_time_s": lease.start_waiting_time_s,
        "start_allowed_speed_mps": lease.start_allowed_speed_mps,
        "requested_acceleration_mps2": lease.requested_acceleration_mps2,
        "realized_acceleration_mps2": realized,
        "transfer_gain": gain,
        "realized_within_tolerance": within,
        "tracking_lag_s": tracking_lag,
        "command_acceptance_rate": (
            lease.accepted_steps / lease.issued_steps if lease.issued_steps else 0.0
        ),
        "issued_steps": lease.issued_steps,
        "mean_abs_target_speed_error_mps": (
            mean(abs(value) for value in lease.target_errors_mps)
            if lease.target_errors_mps else None
        ),
        "native_safety_clamp_reasons": dict(sorted(lease.clamp_reasons.items())),
        "outcome_reasons": dict(sorted(lease.outcome_reasons.items())),
        "observed_constraint_states": dict(sorted(lease.observed_constraint_states.items())),
        "constraint_state_stable": state_stable,
        "eligible_for_gate": eligible_for_gate,
        "start_time_s": lease.start_time_s,
        "end_time_s": end_time_s,
        "observed_duration_s": elapsed,
        "terminal_reason": terminal_reason,
    })


def _start_policy_generation(payload: Mapping[str, Any], now_s: float) -> None:
    global _policy_generation, _signal_actions, _leases
    assert _signal_controller is not None
    for lease in _leases.values():
        _close_lease(lease, end_time_s=now_s, terminal_reason="lease_completion")
    _leases = {}
    _policy_generation += 1
    _signal_actions = signals_from_phase_map(
        _signal_controller.compute_actions(dict(payload))
    )

    vehicles = payload.get("vehicles", {}) or {}
    grouped: dict[ConstraintState, list[tuple[str, Mapping[str, Any], VehicleLimits, StateClassification]]] = {
        state: [] for state in (
            ConstraintState.FREE_FLOW,
            ConstraintState.LEADER_LIMITED,
            ConstraintState.SIGNAL_LIMITED,
        )
    }
    for raw_id, raw_vehicle in sorted(vehicles.items()):
        if not isinstance(raw_vehicle, Mapping):
            continue
        limits = vehicle_limits(raw_vehicle, _vehicle_types)
        classification = classify_constraint(raw_vehicle, limits)
        if classification.state not in grouped:
            continue
        command = scripted_acceleration(classification.state, _policy_generation)
        if eligible_for_command(
            raw_vehicle, limits, classification.state, command, _policy_cadence_s
        ):
            grouped[classification.state].append(
                (str(raw_id), raw_vehicle, limits, classification)
            )

    selected: set[str] = set()
    for state in (
        ConstraintState.FREE_FLOW,
        ConstraintState.LEADER_LIMITED,
        ConstraintState.SIGNAL_LIMITED,
    ):
        by_type: dict[str, list[tuple[str, Mapping[str, Any], VehicleLimits, StateClassification]]] = {}
        for item in grouped[state]:
            by_type.setdefault(str(item[1].get("type_id", "unknown")), []).append(item)
        ordered: list[tuple[str, Mapping[str, Any], VehicleLimits, StateClassification]] = []
        depth = 0
        while len(ordered) < len(grouped[state]):
            added = False
            for type_id in sorted(by_type):
                candidates = by_type[type_id]
                if depth < len(candidates):
                    ordered.append(candidates[depth])
                    added = True
            if not added:
                break
            depth += 1
        for vehicle_id, vehicle, limits, classification in ordered:
            if vehicle_id in selected:
                continue
            selected.add(vehicle_id)
            speed = _vehicle_speed(vehicle)
            _leases[vehicle_id] = _Lease(
                vehicle_id=vehicle_id,
                policy_generation=_policy_generation,
                state=state,
                state_evidence=classification.evidence,
                requested_acceleration_mps2=scripted_acceleration(state, _policy_generation),
                start_speed_mps=speed,
                start_time_s=now_s,
                limits=limits,
                last_speed_mps=speed,
                last_time_s=now_s,
                type_id=str(vehicle.get("type_id", "")),
                start_road_id=str((vehicle.get("location") or {}).get("road_id", "")),
                start_waiting_time_s=float((vehicle.get("traffic") or {}).get("waiting_time_s", 0.0)),
                start_allowed_speed_mps=float((vehicle.get("motion") or {}).get("allowed_speed_mps", 0.0)),
            )
            if sum(1 for item in _leases.values() if item.state == state) >= _max_per_state:
                break


def _build_vehicle_actions(payload: Mapping[str, Any], now_s: float) -> dict[str, Any]:
    vehicles = payload.get("vehicles", {}) or {}
    actions: dict[str, Any] = {}
    for vehicle_id, lease in list(_leases.items()):
        vehicle = vehicles.get(vehicle_id)
        if not isinstance(vehicle, Mapping):
            _close_lease(lease, end_time_s=now_s, terminal_reason="vehicle_arrived")
            _leases.pop(vehicle_id, None)
            continue
        speed = _vehicle_speed(vehicle)
        reference = speed_reference(
            mode=_adapter_mode,
            realized_speed_mps=speed,
            policy_start_speed_mps=lease.start_speed_mps,
            acceleration_mps2=lease.requested_acceleration_mps2,
            step_length_s=_step_length_s,
            policy_cadence_s=_policy_cadence_s,
            max_speed_mps=_vehicle_vmax(vehicle, lease.limits),
        )
        actions[vehicle_id] = {"target_speed_mps": reference.target_speed_mps}
        _issued[vehicle_id] = _Issued(
            vehicle_id=vehicle_id,
            policy_generation=lease.policy_generation,
            state=lease.state,
            state_evidence=lease.state_evidence,
            requested_acceleration_mps2=lease.requested_acceleration_mps2,
            target_speed_mps=reference.target_speed_mps,
            reference_clipped=reference.clipped,
            speed_before_mps=speed,
            issued_at_s=now_s,
            limits=lease.limits,
            leader_gap_m=(
                float(vehicle["leader_gap_m"])
                if vehicle.get("leader_gap_m") is not None else None
            ),
            next_signal_id=(
                str(vehicle["next_signal"].get("tls_id"))
                if isinstance(vehicle.get("next_signal"), Mapping)
                and vehicle["next_signal"].get("tls_id") is not None
                else None
            ),
            next_signal_state=(
                str(vehicle["next_signal"].get("state"))
                if isinstance(vehicle.get("next_signal"), Mapping)
                and vehicle["next_signal"].get("state") is not None
                else None
            ),
            next_signal_distance_m=(
                float(vehicle["next_signal"].get("distance_m"))
                if isinstance(vehicle.get("next_signal"), Mapping)
                and vehicle["next_signal"].get("distance_m") is not None
                else None
            ),
            type_id=str(vehicle.get("type_id", "")),
            road_id=str((vehicle.get("location") or {}).get("road_id", "")),
            waiting_time_s=float((vehicle.get("traffic") or {}).get("waiting_time_s", 0.0)),
            allowed_speed_mps=float((vehicle.get("motion") or {}).get("allowed_speed_mps", 0.0)),
        )
    return actions


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _next_policy_time_s
    if _finished or _signal_controller is None:
        raise RuntimeError("actuator runtime is not initialized")
    now_s = float(payload.get("simulation_time", 0.0))
    _settle_previous(payload, now_s)
    if now_s + 1e-9 >= _next_policy_time_s:
        _start_policy_generation(payload, now_s)
        while _next_policy_time_s <= now_s + 1e-9:
            _next_policy_time_s += _policy_cadence_s
    vehicles = _build_vehicle_actions(payload, now_s)
    return step_response(
        episode_id=_episode_id,
        step_id=payload.get("step_id"),
        signals=_signal_actions,
        vehicles=vehicles,
    )


def finish(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _leases, _issued, _report, _finished, _signal_controller
    if _finished:
        return finish_response(already_finished=True)
    now_s = float(payload.get("simulation_time", 0.0))
    for lease in _leases.values():
        _close_lease(lease, end_time_s=now_s, terminal_reason="episode_terminal")
    _leases = {}
    _issued = {}
    _report = {
        "milestone_id": "ACTUATOR_BLOCKER_v1",
        "episode_id": _episode_id,
        "adapter": _adapter_mode.value,
        "policy_cadence_s": _policy_cadence_s,
        "sumo_step_s": _step_length_s,
        "ppo_updates": 0,
        "signal_controller": "traffic_control.max_pressure.MaxPressureController",
        "step_records": list(_step_rows),
        "lease_records": list(_lease_rows),
        "finish": dict(payload),
    }
    _finished = True
    _signal_controller = None
    return finish_response()


def take_report() -> dict[str, Any] | None:
    """Return the latest immutable-by-copy report without clearing evidence."""

    if _report is None:
        return None
    return {
        **{key: value for key, value in _report.items() if key not in {"step_records", "lease_records"}},
        "step_records": [dict(row) for row in _report["step_records"]],
        "lease_records": [dict(row) for row in _report["lease_records"]],
    }
