"""Deterministic paired replay runtime for Vehicle action utility Phase A.

The module is an algorithm adapter, not a new policy.  Discovery delegates to
the frozen G30 runtime.  Counterfactual arms replay its Road/Cloud joint action
tape and replace only the selected Vehicle command for the 20-second horizon.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from threading import RLock
from typing import Any, Mapping, Sequence
import json
import math
import os

from algorithms.cov2x import mvp_runtime
from algorithms.cov2x.contract import (
    DELTA_V_MAX_SPEED_CEILING_FRACTION,
    NATIVE_RELEASE_TOLERANCE_MPS,
)
from algorithms.cov2x.vehicle.actuator import vehicle_limits
from algorithms.cov2x.vehicle.speed_advice import (
    apply_incremental_speed_advice,
    reference_base_speed,
)


ARMS = (
    "NATIVE_RELEASE",
    "NATIVE_RELEASE_PLACEBO",
    "u=-0.25",
    "u=-0.50",
    "u=-0.75",
)
NEGATIVE_ARM_U = {"u=-0.25": -0.25, "u=-0.50": -0.50, "u=-0.75": -0.75}
CONTEXT_GATE_REFERENCE_ARM = "QUEUE_AWARE_GLOSA"


def _canonical_hash(value: object) -> str:
    body = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(body).hexdigest()


def _without_episode_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(payload))
    result.pop("episode_id", None)
    return result


def decision_record(
    payload: Mapping[str, Any], response: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the canonical decision tape row used for exact prefix checks."""
    diagnostics = dict(response.get("diagnostics") or {})
    evidence = dict(diagnostics.get("phase_a_evidence") or {})
    actions = dict(response.get("actions") or {})
    signals = deepcopy(dict(actions.get("signals") or {}))
    cloud = deepcopy(dict(evidence.get("cloud_priority") or {}))
    observation = _without_episode_identity(payload)
    return {
        "step_id": int(payload.get("step_id", 0) or 0),
        "simulation_time": float(payload.get("simulation_time", 0.0) or 0.0),
        "pre_action_hashes": {
            "observation": _canonical_hash(observation),
            "tls": _canonical_hash(observation.get("intersections", {})),
            "road_action": _canonical_hash(signals),
            "cloud_action": _canonical_hash(cloud),
            "message": _canonical_hash(evidence.get("transport_trace", [])),
            "ledger": _canonical_hash(evidence.get("ledger", {})),
            "safety": _canonical_hash(evidence.get("safety", {})),
        },
        "joint_action": {
            "signals": signals,
            "cloud_priority": cloud,
        },
        "opportunities": deepcopy(
            list(diagnostics.get("phase_a_opportunities") or ())
        ),
    }


def assert_prefix_equal(
    expected: Mapping[str, Any], observed: Mapping[str, Any]
) -> None:
    if int(expected.get("step_id", -1)) != int(observed.get("step_id", -2)):
        raise RuntimeError("Phase A prefix mismatch: step_id")
    expected_hashes = dict(expected.get("pre_action_hashes") or {})
    observed_hashes = dict(observed.get("pre_action_hashes") or {})
    for name in (
        "observation",
        "tls",
        "road_action",
        "cloud_action",
        "message",
        "ledger",
        "safety",
    ):
        if expected_hashes.get(name) != observed_hashes.get(name):
            raise RuntimeError(f"Phase A prefix mismatch: {name}")
    if expected.get("joint_action") != observed.get("joint_action"):
        raise RuntimeError("Phase A prefix mismatch: joint_action")


def select_treatments(
    candidates: Sequence[Mapping[str, Any]],
    *,
    target_count: int,
    minimum_spacing_s: float,
    episode_duration_s: float,
    horizon_s: float,
) -> list[dict[str, Any]]:
    """Greedily select the earliest preregistered valid, independent snapshots."""
    if target_count <= 0:
        raise ValueError("target_count must be positive")
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    last_time = -math.inf
    ordered = sorted(
        (deepcopy(dict(row)) for row in candidates),
        key=lambda row: (
            float(row.get("simulation_time", 0.0)),
            int(row.get("step_id", 0)),
            str((row.get("opportunity") or {}).get("vehicle_id", "")),
        ),
    )
    for row in ordered:
        opportunity = dict(row.get("opportunity") or {})
        now = float(row.get("simulation_time", 0.0))
        identity = (
            str(opportunity.get("vehicle_id", "")),
            str(opportunity.get("movement_id", "")),
        )
        valid = bool(
            all(identity)
            and opportunity.get("intersection_id")
            and opportunity.get("previous_advice_mps") is None
            and opportunity.get("transition_kind") == "native_release"
            and identity not in seen
            and now - last_time + 1e-9 >= float(minimum_spacing_s)
            and now + float(horizon_s) <= float(episode_duration_s) + 1e-9
        )
        if not valid:
            continue
        row["horizon_s"] = float(horizon_s)
        selected.append(row)
        seen.add(identity)
        last_time = now
        if len(selected) == target_count:
            break
    return selected


def vehicle_command(
    *,
    arm: str,
    opportunity: Mapping[str, Any],
    vehicle: Mapping[str, Any] | None,
    vehicle_types: Mapping[str, Mapping[str, Any]],
    previous_cap_mps: float | None,
    first_decision: bool,
) -> tuple[dict[str, dict[str, float]], float | None]:
    """Map the preregistered arm to the frozen speed-advice command contract."""
    if arm not in ARMS and arm != CONTEXT_GATE_REFERENCE_ARM:
        raise ValueError(f"unknown Phase A arm: {arm}")
    if arm in {"NATIVE_RELEASE", "NATIVE_RELEASE_PLACEBO"}:
        return {}, None
    vehicle_id = str(opportunity.get("vehicle_id", ""))
    if not vehicle_id or not isinstance(vehicle, Mapping):
        return {}, previous_cap_mps
    if first_decision:
        base_speed = float(opportunity["base_speed_mps"])
        delta_v_max = float(opportunity["delta_v_max_mps"])
        if arm == CONTEXT_GATE_REFERENCE_ARM:
            decision = dict(opportunity.get("gate_decision") or {})
            raw_target = decision.get("reference_speed_cap_mps")
            if raw_target is None:
                raise RuntimeError("context GLOSA arm is missing its frozen reference cap")
            target = float(raw_target)
            if (
                not math.isfinite(target)
                or target < 0.0
                or target >= base_speed - NATIVE_RELEASE_TOLERANCE_MPS
            ):
                raise RuntimeError("context GLOSA reference cap is outside the frozen envelope")
            return {vehicle_id: {"target_speed_mps": target}}, target
        latent_u = NEGATIVE_ARM_U[arm]
    else:
        motion = dict(vehicle.get("motion") or {})
        limits = vehicle_limits(vehicle, vehicle_types)
        allowed = float(motion.get("allowed_speed_mps"))
        base_speed = reference_base_speed(allowed, limits.max_speed_mps)
        delta_v_max = base_speed * DELTA_V_MAX_SPEED_CEILING_FRACTION
        latent_u = 0.0
    decision = apply_incremental_speed_advice(
        previous_advice_mps=previous_cap_mps,
        base_speed_mps=base_speed,
        latent_u=latent_u,
        delta_v_max_mps=delta_v_max,
        authority_gain=1.0,
        native_release_tolerance_mps=NATIVE_RELEASE_TOLERANCE_MPS,
    )
    if decision.release_native or decision.target_speed_mps is None:
        if first_decision:
            raise RuntimeError("negative Phase A arm unexpectedly released native speed")
        return {}, None
    target = float(decision.target_speed_mps)
    return {vehicle_id: {"target_speed_mps": target}}, target


_lock = RLock()
_configuration: dict[str, Any] | None = None
_metadata: dict[str, Any] = {}
_records: list[dict[str, Any]] = []
_candidates: list[dict[str, Any]] = []
_completed: list[dict[str, Any]] = []
_active_cap_mps: float | None = None
_previous_diagnostics_env: str | None = None


def configure_run(
    *,
    mode: str,
    tape: Mapping[str, Any] | None = None,
    treatment: Mapping[str, Any] | None = None,
    arm: str | None = None,
) -> None:
    """Configure exactly one subsequent LocalAlgorithmClient episode."""
    normalized_mode = str(mode).lower()
    if normalized_mode not in {"discovery", "arm"}:
        raise ValueError("Phase A mode must be discovery or arm")
    if normalized_mode == "arm":
        if (
            arm not in ARMS
            and arm != CONTEXT_GATE_REFERENCE_ARM
        ) or tape is None or treatment is None:
            raise ValueError("Phase A arm requires tape, treatment, and valid arm")
    global _configuration
    with _lock:
        _configuration = {
            "mode": normalized_mode,
            "tape": deepcopy(dict(tape or {})),
            "treatment": deepcopy(dict(treatment or {})),
            "arm": arm,
        }


def initialize(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _metadata, _records, _candidates, _active_cap_mps
    global _previous_diagnostics_env
    with _lock:
        if _configuration is None:
            raise RuntimeError("Phase A runtime is not configured")
        _metadata = deepcopy(dict(payload))
        _records = []
        _candidates = []
        _active_cap_mps = None
        _previous_diagnostics_env = os.environ.get("COV2X_PHASE_A_DIAGNOSTICS")
        os.environ["COV2X_PHASE_A_DIAGNOSTICS"] = "1"
    response = mvp_runtime.initialize(payload)
    return {
        **dict(response),
        "phase_a_mode": str(_configuration["mode"]),
    }


def _expected_by_step() -> dict[int, Mapping[str, Any]]:
    assert _configuration is not None
    return {
        int(row["step_id"]): row
        for row in (_configuration.get("tape") or {}).get("steps", ())
    }


def step(payload: Mapping[str, Any]) -> dict[str, Any]:
    global _active_cap_mps
    with _lock:
        configuration = deepcopy(_configuration)
    if configuration is None:
        raise RuntimeError("Phase A runtime is not configured")
    mode = str(configuration["mode"])
    step_id = int(payload.get("step_id", 0) or 0)
    now = float(payload.get("simulation_time", 0.0) or 0.0)
    expected = None
    treatment_time = math.inf
    horizon_s = 0.0
    if mode == "arm":
        expected = _expected_by_step().get(step_id)
        if expected is None:
            raise RuntimeError(f"Phase A tape is missing step {step_id}")
        treatment = dict(configuration["treatment"])
        treatment_time = float(treatment["simulation_time"])
        horizon_s = float(treatment.get("horizon_s", 20.0))
        if now + 1e-9 >= treatment_time and now < treatment_time + horizon_s - 1e-9:
            joint = dict(expected.get("joint_action") or {})
            mvp_runtime.set_phase_a_joint_action_override(
                cloud_priority=dict(joint.get("cloud_priority") or {}),
                signal_actions=dict(joint.get("signals") or {}),
            )
    try:
        response = mvp_runtime.step(payload)
    finally:
        mvp_runtime.clear_phase_a_joint_action_override()
    record = decision_record(payload, response)

    if mode == "discovery":
        if os.environ.get("COV2X_CONTEXT_GATE_DIAGNOSTICS") == "1":
            from algorithms.cov2x.vehicle.context_gate import (
                GateConfig,
                build_gate_evidence,
            )

            gate_config = GateConfig(
                sumo_step_s=float(
                    os.environ.get("COV2X_PHASE_HISTORY_STEP_LENGTH", "0.05")
                ),
                decision_interval_s=5.0,
                horizon_s=20.0,
            )
            for opportunity in record["opportunities"]:
                opportunity.update(
                    build_gate_evidence(
                        metadata=_metadata,
                        payload=payload,
                        joint_action=record["joint_action"],
                        opportunity=opportunity,
                        config=gate_config,
                    )
                )
        _records.append(record)
        for opportunity in record["opportunities"]:
            _candidates.append(
                {
                    "step_id": step_id,
                    "simulation_time": now,
                    "opportunity": deepcopy(opportunity),
                }
            )
        return response

    assert expected is not None
    if now <= treatment_time + 1e-9:
        assert_prefix_equal(expected, record)
    elif now < treatment_time + horizon_s - 1e-9:
        expected_joint = expected.get("joint_action")
        if record.get("joint_action") != expected_joint:
            raise RuntimeError("Phase A horizon joint action tape mismatch")
    base_vehicle_actions = dict(
        (response.get("actions") or {}).get("vehicles") or {}
    )
    if base_vehicle_actions:
        raise RuntimeError(
            "frozen G30 emitted a non-release Vehicle action in Phase A replay"
        )
    vehicle_actions: dict[str, dict[str, float]] = {}
    if treatment_time - 1e-9 <= now < treatment_time + horizon_s - 1e-9:
        opportunity = dict(configuration["treatment"]["opportunity"])
        vehicle = (payload.get("vehicles") or {}).get(
            str(opportunity.get("vehicle_id", ""))
        )
        vehicle_actions, _active_cap_mps = vehicle_command(
            arm=str(configuration["arm"]),
            opportunity=opportunity,
            vehicle=vehicle if isinstance(vehicle, Mapping) else None,
            vehicle_types=dict(_metadata.get("vehicle_types") or {}),
            previous_cap_mps=_active_cap_mps,
            first_decision=abs(now - treatment_time) <= 1e-9,
        )
    elif now >= treatment_time + horizon_s - 1e-9:
        _active_cap_mps = None
    result = deepcopy(dict(response))
    result.setdefault("actions", {})["vehicles"] = vehicle_actions
    result.setdefault("diagnostics", {})["phase_a_arm"] = {
        "arm": configuration["arm"],
        "treatment_time": treatment_time,
        "horizon_s": horizon_s,
        "active_cap_mps": _active_cap_mps,
    }
    _records.append(record)
    return result


def finish(payload: Mapping[str, Any]) -> None:
    global _previous_diagnostics_env
    try:
        mvp_runtime.finish(payload)
        with _lock:
            assert _configuration is not None
            _completed.append(
                {
                    "mode": _configuration["mode"],
                    "arm": _configuration.get("arm"),
                    "treatment": deepcopy(_configuration.get("treatment") or {}),
                    "steps": deepcopy(_records),
                    "candidates": deepcopy(_candidates),
                }
            )
    finally:
        mvp_runtime.clear_phase_a_joint_action_override()
        if _previous_diagnostics_env is None:
            os.environ.pop("COV2X_PHASE_A_DIAGNOSTICS", None)
        else:
            os.environ["COV2X_PHASE_A_DIAGNOSTICS"] = _previous_diagnostics_env
        _previous_diagnostics_env = None


def take_completed() -> dict[str, Any] | None:
    with _lock:
        return _completed.pop(0) if _completed else None


def reset_completed() -> None:
    with _lock:
        _completed.clear()
