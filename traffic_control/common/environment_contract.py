"""Versioned environment contracts shared by IPPO and MAPPO."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

ENVIRONMENT_CONTRACT_VERSION = 3
MULTISCENARIO_ENVIRONMENT_CONTRACT_VERSION = 4
SUPPORTED_ENVIRONMENT_CONTRACT_VERSIONS = frozenset({3, 4})
JOINT_PERIODS = ("morning_peak", "off_peak", "evening_peak")
MULTISCENARIO_CONTRACT_LABEL = "v4_multiscenario"


class EnvironmentContractError(ValueError):
    """Base class for invalid or incompatible environment contracts."""


class ContractIntegrityError(EnvironmentContractError):
    """A saved contract or signed payload was malformed or tampered."""


class PolicySpaceMismatch(EnvironmentContractError):
    """The live tensor or road-topology space differs from training."""


class UnsupportedPeriod(EnvironmentContractError):
    """The live period was not signed into the checkpoint."""


class UnsupportedPreset(EnvironmentContractError):
    """The live scenario preset was not signed into the checkpoint."""


class UnsupportedCombination(EnvironmentContractError):
    """The live period/preset pair was not signed into the checkpoint."""


class ProgramMismatch(EnvironmentContractError):
    """The live signal program differs from its saved period program."""


def canonicalize(value: Any) -> Any:
    """Return the JSON-compatible canonical representation used for hashing."""
    if isinstance(value, Mapping):
        return {
            str(key): canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("environment contract floats must be finite")
        rounded = round(value, 6)
        return 0.0 if rounded == 0.0 else rounded
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise TypeError(
        f"environment contract value is not JSON-compatible: {type(value).__name__}"
    )


def payload_sha256(payload: Any) -> str:
    """Return the SHA-256 digest of a canonical payload."""
    canonical = canonicalize(payload)
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def signed_payload(payload: Any) -> dict[str, Any]:
    """Pair a canonical payload with its deterministic digest."""
    canonical = canonicalize(payload)
    return {
        "sha256": payload_sha256(canonical),
        "payload": canonical,
    }


def _policy_value(policy_spec: Mapping[str, Any], name: str) -> Any:
    if name not in policy_spec:
        raise ValueError(f"policy_spec is missing {name!r}")
    return policy_spec[name]


def _ordered_intersection_ids(
    metadata: Mapping[str, Any],
    policy_spec: Mapping[str, Any],
) -> list[str]:
    intersections = metadata.get("intersections")
    if not isinstance(intersections, Mapping) or not intersections:
        raise ValueError("simulation metadata must contain intersections")
    identity_slots = [
        str(value)
        for value in _policy_value(policy_spec, "identity_slots")
    ]
    slot_set = set(identity_slots)
    unknown = sorted(
        str(value)
        for value in intersections
        if str(value) not in slot_set
    )
    if unknown:
        raise ValueError(
            f"simulation intersections are outside policy identity slots: {unknown}"
        )
    return [
        intersection_id
        for intersection_id in identity_slots
        if intersection_id in intersections
    ]


def _ordered_lanes(intersection: Mapping[str, Any]) -> list[dict[str, Any]]:
    lanes = intersection.get("lanes", {})
    if not isinstance(lanes, Mapping):
        raise ValueError("intersection lanes must be a mapping")
    ordered_ids: list[str] = []
    for key in ("incoming_lanes", "outgoing_lanes"):
        for raw_lane_id in intersection.get(key, ()):
            lane_id = str(raw_lane_id)
            if lane_id not in ordered_ids:
                ordered_ids.append(lane_id)
    ordered_ids.extend(
        sorted(str(lane_id) for lane_id in lanes if str(lane_id) not in ordered_ids)
    )
    payload: list[dict[str, Any]] = []
    for lane_id in ordered_ids:
        lane = lanes.get(lane_id)
        if not isinstance(lane, Mapping):
            raise ValueError(f"lane metadata is missing for {lane_id!r}")
        approach_id = lane.get("approach_id")
        payload.append(
            {
                "lane_id": lane_id,
                "edge_id": str(lane.get("edge_id", "")),
                "lane_index": int(lane.get("lane_index", 0)),
                "role": str(lane.get("role", "")),
                "length_m": float(
                    lane.get(
                        "length_m",
                        lane.get("length", 150.0),
                    )
                ),
                "speed_limit_mps": float(
                    lane.get(
                        "speed_limit_mps",
                        lane.get("max_speed", 20.0),
                    )
                ),
                "approach_id": (
                    None if approach_id is None else str(approach_id)
                ),
                "movements": [
                    str(value) for value in lane.get("movements", ())
                ],
                "downstream_lane_ids": [
                    str(value)
                    for value in lane.get("downstream_lane_ids", ())
                ],
            }
        )
    return payload


def build_policy_space_signature(
    metadata: Mapping[str, Any],
    *,
    policy_spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the program-independent tensor and road-topology signature."""
    intersections = metadata["intersections"]
    intersection_ids = _ordered_intersection_ids(metadata, policy_spec)
    intersection_payload = []
    for intersection_id in intersection_ids:
        intersection = intersections[intersection_id]
        if not isinstance(intersection, Mapping):
            raise ValueError(
                f"intersection metadata must be a mapping: {intersection_id}"
            )
        intersection_payload.append(
            {
                "intersection_id": intersection_id,
                "incoming_lanes": [
                    str(value) for value in intersection.get("incoming_lanes", ())
                ],
                "outgoing_lanes": [
                    str(value) for value in intersection.get("outgoing_lanes", ())
                ],
                "lanes": _ordered_lanes(intersection),
                "connections": [
                    dict(connection)
                    for connection in intersection.get("connections", ())
                ],
                "direct_neighbors": [
                    str(value) for value in intersection.get("direct_neighbors", ())
                ],
            }
        )
    return signed_payload(
        {
            "protocol_version": str(metadata.get("protocol_version", "")),
            "policy_spec": dict(policy_spec),
            "intersection_ids": intersection_ids,
            "intersections": intersection_payload,
        }
    )


def _phase_for_id(
    phases: Mapping[Any, Any],
    phase_id: int,
    *,
    intersection_id: str,
) -> Mapping[str, Any]:
    phase = phases.get(str(phase_id), phases.get(phase_id))
    if not isinstance(phase, Mapping):
        raise ValueError(
            f"phase {phase_id} is missing for intersection {intersection_id}"
        )
    return phase


def _flow_reference_rate(
    intersection: Mapping[str, Any],
    *,
    saturation_flow_per_lane: float,
) -> float:
    connection_lanes = {
        str(connection.get("connection_id")): str(connection.get("from_lane"))
        for connection in intersection.get("connections", ())
        if isinstance(connection, Mapping)
        and connection.get("connection_id") is not None
        and connection.get("from_lane") is not None
    }
    phases = intersection.get("phases", {})
    if not isinstance(phases, Mapping):
        raise ValueError("intersection phases must be a mapping")
    served_lane_counts: list[int] = []
    for raw_phase_id in intersection.get("phase_order", ()):
        phase_id = int(raw_phase_id)
        phase = _phase_for_id(
            phases,
            phase_id,
            intersection_id=str(intersection.get("intersection_id", "")),
        )
        priorities = phase.get("connection_priorities", {})
        if not isinstance(priorities, Mapping):
            raise ValueError("phase connection_priorities must be a mapping")
        served_lane_counts.append(
            len(
                {
                    connection_lanes[str(connection_id)]
                    for connection_id in priorities
                    if str(connection_id) in connection_lanes
                }
            )
        )
    max_served_lanes = max(served_lane_counts, default=0)
    if max_served_lanes <= 0:
        incoming_count = len(intersection.get("incoming_lanes", ()))
        max_served_lanes = max(1, min(incoming_count, 4))
    return float(saturation_flow_per_lane) * max_served_lanes


def build_program_signature(
    metadata: Mapping[str, Any],
    *,
    policy_spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the current period's local phase-program signature."""
    max_action_dim = int(_policy_value(policy_spec, "max_action_dim"))
    saturation_flow = float(
        _policy_value(policy_spec, "saturation_flow_per_lane")
    )
    intersections = metadata["intersections"]
    intersection_ids = _ordered_intersection_ids(metadata, policy_spec)
    intersection_payload = []
    for intersection_id in intersection_ids:
        intersection = intersections[intersection_id]
        phase_order = [
            int(value) for value in intersection.get("phase_order", ())
        ]
        if len(phase_order) > max_action_dim:
            raise ValueError(
                f"intersection {intersection_id} has {len(phase_order)} phases, "
                f"exceeding max_action_dim={max_action_dim}"
            )
        phases = intersection.get("phases", {})
        if not isinstance(phases, Mapping):
            raise ValueError("intersection phases must be a mapping")
        phase_payload = []
        for phase_id in phase_order:
            phase = _phase_for_id(
                phases,
                phase_id,
                intersection_id=intersection_id,
            )
            priorities = phase.get("connection_priorities", {})
            if not isinstance(priorities, Mapping):
                raise ValueError("phase connection_priorities must be a mapping")
            phase_payload.append(
                {
                    "phase_id": int(phase.get("phase_id", phase_id)),
                    "name": str(phase.get("name", "")),
                    "movement": str(phase.get("movement", "")),
                    "approaches": [
                        str(value) for value in phase.get("approaches", ())
                    ],
                    "green_seconds": float(phase.get("green_seconds", 0.0)),
                    "yellow_seconds": float(phase.get("yellow_seconds", 0.0)),
                    "clearance_seconds": float(
                        phase.get("clearance_seconds", 0.0)
                    ),
                    "connection_priorities": {
                        str(connection_id): str(priority)
                        for connection_id, priority in priorities.items()
                    },
                }
            )
        intersection_payload.append(
            {
                "intersection_id": intersection_id,
                "phase_order": phase_order,
                "phase_count": len(phase_order),
                "valid_phase_mask": [
                    offset < len(phase_order) for offset in range(max_action_dim)
                ],
                "phases": phase_payload,
                "flow_reference_rate": _flow_reference_rate(
                    intersection,
                    saturation_flow_per_lane=saturation_flow,
                ),
            }
        )
    return signed_payload(
        {
            "period": str(metadata.get("period", "")),
            "decision_interval_s": float(metadata.get("decision_interval", 0.0)),
            "minimum_green_s": float(metadata.get("minimum_green", 0.0)),
            "intersections": intersection_payload,
        }
    )


def validate_training_periods(periods: Any) -> tuple[str, ...]:
    """Require the frozen three-period order for a joint-model run."""
    normalized = tuple(str(period) for period in periods)
    if normalized != JOINT_PERIODS:
        raise EnvironmentContractError(
            "joint training periods must exactly match "
            f"{JOINT_PERIODS}; got {normalized}"
        )
    return normalized


def validate_balanced_period_batch(periods: Any) -> dict[str, int]:
    """Require a non-empty synchronous batch with equal period counts."""
    normalized = tuple(str(period) for period in periods)
    if not normalized:
        raise EnvironmentContractError("joint period batch must not be empty")
    unknown = sorted(set(normalized) - set(JOINT_PERIODS))
    if unknown:
        raise EnvironmentContractError(
            f"joint period batch contains unsupported periods: {unknown}"
        )
    counts = {
        period: sum(value == period for value in normalized)
        for period in JOINT_PERIODS
    }
    expected_count = counts[JOINT_PERIODS[0]]
    if expected_count <= 0 or any(
        count != expected_count for count in counts.values()
    ):
        raise EnvironmentContractError(
            "joint period batch must be exactly balanced; "
            f"counts={counts}"
        )
    return counts


def _validate_signed_payload(
    signature: Any,
    *,
    label: str,
) -> None:
    if not isinstance(signature, Mapping):
        raise ContractIntegrityError(f"{label} must be a signed payload")
    if "payload" not in signature or "sha256" not in signature:
        raise ContractIntegrityError(f"{label} is missing payload or sha256")
    actual = payload_sha256(signature["payload"])
    saved = str(signature["sha256"])
    if actual != saved:
        raise ContractIntegrityError(
            f"{label} SHA-256 mismatch: saved={saved}, actual={actual}"
        )


def _contract_version(contract: Any) -> int:
    if not isinstance(contract, Mapping):
        raise ContractIntegrityError("environment contract must be a mapping")
    try:
        return int(contract["environment_contract_version"])
    except (KeyError, TypeError, ValueError) as error:
        raise ContractIntegrityError(
            "environment contract version is missing or invalid"
        ) from error


def _validated_contract_periods(contract: Mapping[str, Any]) -> tuple[str, ...]:
    try:
        return validate_training_periods(contract["supported_periods"])
    except (KeyError, EnvironmentContractError) as error:
        raise ContractIntegrityError(
            "environment contract supported_periods are invalid"
        ) from error


def _validate_contract_hash(contract: Mapping[str, Any]) -> None:
    saved_hash = str(contract.get("sha256", ""))
    core = {
        str(key): value
        for key, value in contract.items()
        if str(key) != "sha256"
    }
    actual_hash = payload_sha256(core)
    if saved_hash != actual_hash:
        raise ContractIntegrityError(
            "environment contract SHA-256 mismatch: "
            f"saved={saved_hash}, actual={actual_hash}"
        )


def _normalize_supported_presets(
    supported_presets: Any,
    *,
    intersection_universe: tuple[str, ...],
) -> dict[str, list[str]]:
    if not isinstance(supported_presets, Mapping) or not supported_presets:
        raise ContractIntegrityError(
            "supported_presets must be a non-empty mapping"
        )
    universe_set = set(intersection_universe)
    normalized: dict[str, list[str]] = {}
    for raw_preset_id in sorted(supported_presets, key=str):
        preset_id = str(raw_preset_id)
        if not preset_id:
            raise ContractIntegrityError("supported preset ID must not be empty")
        raw_ids = supported_presets[raw_preset_id]
        if isinstance(raw_ids, (str, bytes)) or not isinstance(
            raw_ids, (list, tuple)
        ):
            raise ContractIntegrityError(
                f"supported_presets[{preset_id!r}] must be an ordered list"
            )
        ids = [str(value) for value in raw_ids]
        if not ids or len(ids) != len(set(ids)):
            raise ContractIntegrityError(
                f"supported_presets[{preset_id!r}] must be non-empty and unique"
            )
        unknown = [value for value in ids if value not in universe_set]
        if unknown:
            raise ContractIntegrityError(
                f"supported_presets[{preset_id!r}] contains unknown IDs: {unknown}"
            )
        id_set = set(ids)
        canonical_ids = [
            value for value in intersection_universe if value in id_set
        ]
        if ids != canonical_ids:
            raise ContractIntegrityError(
                f"supported_presets[{preset_id!r}] is not in canonical order: "
                f"saved={ids}, canonical={canonical_ids}"
            )
        normalized[preset_id] = ids
    return normalized


def _validate_v3_contract_integrity(
    contract: Mapping[str, Any],
    periods: tuple[str, ...],
) -> None:
    program_signatures = contract.get("program_signatures")
    if not isinstance(program_signatures, Mapping):
        raise ContractIntegrityError("program_signatures must be a mapping")
    if set(str(key) for key in program_signatures) != set(periods):
        raise ContractIntegrityError(
            "program_signatures do not match supported_periods"
        )
    for period in periods:
        signature = program_signatures.get(period)
        _validate_signed_payload(
            signature,
            label=f"program_signatures[{period!r}]",
        )
        payload = signature["payload"]
        if (
            not isinstance(payload, Mapping)
            or str(payload.get("period", "")) != period
        ):
            raise ContractIntegrityError(
                f"program signature period mismatch for {period!r}"
            )


def _validate_v4_contract_integrity(
    contract: Mapping[str, Any],
    periods: tuple[str, ...],
) -> None:
    if str(contract.get("contract_version", "")) != MULTISCENARIO_CONTRACT_LABEL:
        raise ContractIntegrityError(
            "v4 environment contract label must be v4_multiscenario"
        )
    policy_payload = contract["policy_space_signature"]["payload"]
    if not isinstance(policy_payload, Mapping):
        raise ContractIntegrityError(
            "policy_space_signature.payload must be a mapping"
        )
    signed_ids_value = policy_payload.get("intersection_ids")
    universe_value = contract.get("intersection_universe")
    if not isinstance(signed_ids_value, list) or not isinstance(
        universe_value, list
    ):
        raise ContractIntegrityError(
            "v4 intersection universe and signed intersection IDs must be lists"
        )
    signed_ids = tuple(str(value) for value in signed_ids_value)
    universe = tuple(str(value) for value in universe_value)
    if (
        not universe
        or len(universe) != len(set(universe))
        or universe != signed_ids
    ):
        raise ContractIntegrityError(
            "v4 intersection_universe must exactly match signed policy IDs"
        )
    fixed_ids = tuple(
        _intersection_payloads(
            policy_payload,
            label="policy_space_signature.payload",
        )
    )
    if fixed_ids != universe:
        raise ContractIntegrityError(
            "v4 fixed intersection entries do not match intersection_universe"
        )

    program_signatures = contract.get("program_signatures")
    if not isinstance(program_signatures, Mapping):
        raise ContractIntegrityError("program_signatures must be a mapping")
    if tuple(str(key) for key in program_signatures) != periods:
        raise ContractIntegrityError(
            "v4 program signature period order does not match supported_periods"
        )
    for period in periods:
        period_signature = program_signatures.get(period)
        if not isinstance(period_signature, Mapping):
            raise ContractIntegrityError(
                f"program_signatures[{period!r}] must be a mapping"
            )
        if str(period_signature.get("period", "")) != period:
            raise ContractIntegrityError(
                f"program signature period mismatch for {period!r}"
            )
        signatures = period_signature.get("intersections")
        if not isinstance(signatures, Mapping):
            raise ContractIntegrityError(
                f"program_signatures[{period!r}].intersections must be a mapping"
            )
        if tuple(str(key) for key in signatures) != universe:
            raise ContractIntegrityError(
                f"program {period!r} intersection order does not match universe"
            )
        for intersection_id in universe:
            signature = signatures.get(intersection_id)
            _validate_signed_payload(
                signature,
                label=(
                    f"program_signatures[{period!r}]"
                    f".intersections[{intersection_id!r}]"
                ),
            )
            payload = signature["payload"]
            if not isinstance(payload, Mapping):
                raise ContractIntegrityError(
                    f"program payload for {period}/{intersection_id} is malformed"
                )
            if (
                str(payload.get("period", "")) != period
                or str(payload.get("intersection_id", "")) != intersection_id
                or payload.get("decision_interval_s")
                != period_signature.get("decision_interval_s")
                or payload.get("minimum_green_s")
                != period_signature.get("minimum_green_s")
            ):
                raise ContractIntegrityError(
                    f"program payload identity mismatch for "
                    f"{period}/{intersection_id}"
                )

    presets = _normalize_supported_presets(
        contract.get("supported_presets"),
        intersection_universe=universe,
    )
    if dict(contract["supported_presets"]) != presets:
        raise ContractIntegrityError(
            "supported_presets must use canonical preset and intersection order"
        )
    expected_combinations = [
        [period, preset_id]
        for period in periods
        for preset_id in presets
    ]
    if contract.get("supported_combinations") != expected_combinations:
        raise ContractIntegrityError(
            "supported_combinations must be the exact ordered period/preset matrix"
        )


def validate_contract_integrity(contract: Any) -> None:
    """Verify a v3 or v4 envelope and every nested signed payload."""
    version = _contract_version(contract)
    if version not in SUPPORTED_ENVIRONMENT_CONTRACT_VERSIONS:
        raise ContractIntegrityError(
            f"unsupported environment contract version: {version}"
        )
    periods = _validated_contract_periods(contract)
    _validate_signed_payload(
        contract.get("policy_space_signature"),
        label="policy_space_signature",
    )
    if version == ENVIRONMENT_CONTRACT_VERSION:
        _validate_v3_contract_integrity(contract, periods)
    else:
        _validate_v4_contract_integrity(contract, periods)
    _validate_contract_hash(contract)



def validate_checkpoint_binding(
    contract: Mapping[str, Any],
    *,
    periods: Any,
    policy_spec: Mapping[str, Any],
    intersection_ids: Any,
) -> None:
    """Cross-bind a signed v3 contract to checkpoint-level policy facts."""
    validate_contract_integrity(contract)
    expected_periods = tuple(str(value) for value in periods)
    saved_periods = tuple(
        str(value) for value in contract["supported_periods"]
    )
    if saved_periods != expected_periods:
        raise ContractIntegrityError(
            "checkpoint training periods do not match environment contract: "
            f"checkpoint={expected_periods}, contract={saved_periods}"
        )

    policy_signature = contract["policy_space_signature"]
    policy_payload = policy_signature["payload"]
    if not isinstance(policy_payload, Mapping):
        raise ContractIntegrityError(
            "policy_space_signature.payload must be a mapping"
        )
    saved_policy_spec = policy_payload.get("policy_spec")
    if not isinstance(saved_policy_spec, Mapping):
        raise ContractIntegrityError(
            "policy_space_signature.payload.policy_spec must be a mapping"
        )
    saved_policy_spec = canonicalize(saved_policy_spec)
    expected_policy_spec = canonicalize(policy_spec)
    difference = _first_difference(
        saved_policy_spec,
        expected_policy_spec,
        path="policy_space.policy_spec",
    )
    if difference is not None:
        path, saved, expected = difference
        raise ContractIntegrityError(
            "checkpoint policy_spec does not match environment contract at "
            f"{path}: contract={saved!r}, checkpoint={expected!r}"
        )

    expected_ids = tuple(str(value) for value in intersection_ids)
    if (
        not expected_ids
        or len(expected_ids) != len(set(expected_ids))
    ):
        raise ContractIntegrityError(
            "checkpoint intersection IDs must be non-empty and unique"
        )
    signed_ids_value = policy_payload.get("intersection_ids")
    if not isinstance(signed_ids_value, list):
        raise ContractIntegrityError(
            "policy space intersection_ids must be a list"
        )
    signed_ids = tuple(str(value) for value in signed_ids_value)
    if signed_ids != expected_ids:
        raise ContractIntegrityError(
            "checkpoint intersection order does not match environment contract: "
            f"checkpoint={expected_ids}, contract={signed_ids}"
        )

    fixed_ids = tuple(
        _intersection_payloads(
            policy_payload,
            label="policy_space_signature.payload",
        )
    )
    if fixed_ids != signed_ids:
        raise ContractIntegrityError(
            "policy space intersection order does not match its intersection_ids: "
            f"entries={fixed_ids}, intersection_ids={signed_ids}"
        )

    version = _contract_version(contract)
    for period in saved_periods:
        if version == ENVIRONMENT_CONTRACT_VERSION:
            program_payload = contract["program_signatures"][period]["payload"]
            if not isinstance(program_payload, Mapping):
                raise ContractIntegrityError(
                    f"program signature payload for {period!r} must be a mapping"
                )
            program_ids = tuple(
                _intersection_payloads(
                    program_payload,
                    label=f"program_signatures[{period!r}].payload",
                )
            )
        else:
            program_ids = tuple(
                str(value)
                for value in contract["program_signatures"][period][
                    "intersections"
                ]
            )
        if program_ids != signed_ids:
            raise ContractIntegrityError(
                f"program {period!r} intersection order does not match policy "
                f"space: program={program_ids}, policy={signed_ids}"
            )

def build_environment_contract(
    metadata_by_period: Mapping[str, Mapping[str, Any]],
    *,
    policy_spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a complete three-period v3 environment contract."""
    periods = validate_training_periods(metadata_by_period.keys())
    policy_signatures: dict[str, dict[str, Any]] = {}
    program_signatures: dict[str, dict[str, Any]] = {}
    for period in periods:
        metadata = metadata_by_period[period]
        live_period = str(metadata.get("period", ""))
        if live_period != period:
            raise EnvironmentContractError(
                f"metadata period mismatch: key={period!r}, metadata={live_period!r}"
            )
        policy_signatures[period] = build_policy_space_signature(
            metadata,
            policy_spec=policy_spec,
        )
        program_signatures[period] = build_program_signature(
            metadata,
            policy_spec=policy_spec,
        )

    fixed_hashes = {
        period: signature["sha256"]
        for period, signature in policy_signatures.items()
    }
    if len(set(fixed_hashes.values())) != 1:
        baseline_period = periods[0]
        baseline_signature = policy_signatures[baseline_period]
        for period in periods[1:]:
            candidate_signature = policy_signatures[period]
            if candidate_signature["sha256"] == baseline_signature["sha256"]:
                continue
            difference = _first_difference(
                baseline_signature["payload"],
                candidate_signature["payload"],
                path=f"policy_space[{period}]",
            )
            assert difference is not None
            _raise_difference(
                PolicySpaceMismatch,
                label=f"training policy space mismatch for {period}",
                difference=difference,
                saved_hash=str(baseline_signature["sha256"]),
                live_hash=str(candidate_signature["sha256"]),
            )

    core: dict[str, Any] = {
        "environment_contract_version": ENVIRONMENT_CONTRACT_VERSION,
        "supported_periods": list(periods),
        "policy_space_signature": policy_signatures[periods[0]],
        "program_signatures": program_signatures,
    }
    contract = dict(core)
    contract["sha256"] = payload_sha256(core)
    validate_contract_integrity(contract)
    return contract


def _program_signatures_by_intersection(
    v3_contract: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for period in JOINT_PERIODS:
        period_payload = v3_contract["program_signatures"][period]["payload"]
        if not isinstance(period_payload, Mapping):
            raise ContractIntegrityError(
                f"program signature payload for {period!r} must be a mapping"
            )
        decision_interval = float(period_payload["decision_interval_s"])
        minimum_green = float(period_payload["minimum_green_s"])
        entries = _intersection_payloads(
            period_payload,
            label=f"program_signatures[{period!r}].payload",
        )
        signatures: dict[str, dict[str, Any]] = {}
        for intersection_id, entry in entries.items():
            local_payload = {
                "period": period,
                "intersection_id": intersection_id,
                "decision_interval_s": decision_interval,
                "minimum_green_s": minimum_green,
                **{
                    str(key): value
                    for key, value in entry.items()
                    if str(key) != "intersection_id"
                },
            }
            signatures[intersection_id] = signed_payload(local_payload)
        result[period] = {
            "period": period,
            "decision_interval_s": decision_interval,
            "minimum_green_s": minimum_green,
            "intersections": signatures,
        }
    return result


def upgrade_environment_contract_v4(
    v3_contract: Mapping[str, Any],
    *,
    supported_presets: Mapping[str, Any],
) -> dict[str, Any]:
    """Upgrade a complete v3 contract without changing its policy facts."""
    validate_contract_integrity(v3_contract)
    version = _contract_version(v3_contract)
    if version != ENVIRONMENT_CONTRACT_VERSION:
        raise ContractIntegrityError(
            "only a v3 environment contract can be upgraded to v4"
        )
    policy_signature = v3_contract["policy_space_signature"]
    policy_payload = policy_signature["payload"]
    if not isinstance(policy_payload, Mapping):
        raise ContractIntegrityError(
            "policy_space_signature.payload must be a mapping"
        )
    raw_ids = policy_payload.get("intersection_ids")
    if not isinstance(raw_ids, list):
        raise ContractIntegrityError(
            "policy_space_signature.payload.intersection_ids must be a list"
        )
    universe = tuple(str(value) for value in raw_ids)
    presets = _normalize_supported_presets(
        supported_presets,
        intersection_universe=universe,
    )
    periods = validate_training_periods(v3_contract["supported_periods"])
    core: dict[str, Any] = {
        "environment_contract_version": (
            MULTISCENARIO_ENVIRONMENT_CONTRACT_VERSION
        ),
        "contract_version": MULTISCENARIO_CONTRACT_LABEL,
        "supported_periods": list(periods),
        "intersection_universe": list(universe),
        "policy_space_signature": dict(policy_signature),
        "program_signatures": _program_signatures_by_intersection(
            v3_contract
        ),
        "supported_presets": presets,
        "supported_combinations": [
            [period, preset_id]
            for period in periods
            for preset_id in presets
        ],
    }
    contract = dict(core)
    contract["sha256"] = payload_sha256(core)
    validate_contract_integrity(contract)
    return contract


def build_multiscenario_environment_contract(
    metadata_by_period: Mapping[str, Mapping[str, Any]],
    *,
    policy_spec: Mapping[str, Any],
    supported_presets: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a complete three-period v4 multiscenario contract."""
    v3_contract = build_environment_contract(
        metadata_by_period,
        policy_spec=policy_spec,
    )
    return upgrade_environment_contract_v4(
        v3_contract,
        supported_presets=supported_presets,
    )



def _first_difference(
    saved: Any,
    live: Any,
    *,
    path: str,
) -> tuple[str, Any, Any] | None:
    if type(saved) is not type(live):
        return path, saved, live
    if isinstance(saved, Mapping):
        saved_keys = sorted(str(key) for key in saved)
        live_keys = sorted(str(key) for key in live)
        if saved_keys != live_keys:
            return f"{path}.keys", saved_keys, live_keys
        for key in saved_keys:
            difference = _first_difference(
                saved[key],
                live[key],
                path=f"{path}.{key}",
            )
            if difference is not None:
                return difference
        return None
    if isinstance(saved, list):
        if len(saved) != len(live):
            return f"{path}.length", len(saved), len(live)
        for index, (saved_item, live_item) in enumerate(zip(saved, live)):
            difference = _first_difference(
                saved_item,
                live_item,
                path=f"{path}[{index}]",
            )
            if difference is not None:
                return difference
        return None
    if saved != live:
        return path, saved, live
    return None


def _intersection_payloads(
    payload: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Mapping[str, Any]]:
    entries = payload.get("intersections")
    if not isinstance(entries, list):
        raise ContractIntegrityError(f"{label}.intersections must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ContractIntegrityError(
                f"{label}.intersections contains a non-mapping entry"
            )
        intersection_id = str(entry.get("intersection_id", ""))
        if not intersection_id or intersection_id in result:
            raise ContractIntegrityError(
                f"{label} has an empty or duplicate intersection_id"
            )
        result[intersection_id] = entry
    return result


def _raise_difference(
    error_type: type[EnvironmentContractError],
    *,
    label: str,
    difference: tuple[str, Any, Any],
    saved_hash: str,
    live_hash: str,
) -> None:
    path, saved, live = difference
    raise error_type(
        f"{label} at {path}: saved={saved!r}, live={live!r}; "
        f"saved_hash={saved_hash}, live_hash={live_hash}"
    )


def validate_checkpoint_environment(
    contract: Mapping[str, Any],
    live_metadata: Mapping[str, Any],
    *,
    policy_spec: Mapping[str, Any],
    controlled_intersection_ids: Any = None,
) -> dict[str, Any]:
    """Validate one live period against a complete v3/v4 checkpoint contract."""
    validate_contract_integrity(contract)
    version = _contract_version(contract)
    period = str(live_metadata.get("period", ""))
    supported_periods = tuple(
        str(value) for value in contract["supported_periods"]
    )
    if period not in supported_periods:
        raise UnsupportedPeriod(
            f"period {period!r} is not supported by checkpoint; "
            f"supported={supported_periods}"
        )

    scenario_preset_id: str | None = None
    declared_ids: list[str] | None = None
    if version == MULTISCENARIO_ENVIRONMENT_CONTRACT_VERSION:
        scenario_preset_id = str(
            live_metadata.get("scenario_preset_id", "")
        )
        supported_presets = contract["supported_presets"]
        if not scenario_preset_id and controlled_intersection_ids is not None:
            candidate_ids = [
                str(value)
                for value in controlled_intersection_ids
            ]
            controlled_intersection_ids = tuple(candidate_ids)
            matching_presets = [
                str(preset_id)
                for preset_id, preset_ids in supported_presets.items()
                if [str(value) for value in preset_ids] == candidate_ids
            ]
            if len(matching_presets) != 1:
                raise UnsupportedPreset(
                    "missing scenario preset cannot be uniquely inferred from "
                    f"controlled IDs {candidate_ids}; matches={matching_presets}"
                )
            scenario_preset_id = matching_presets[0]
        if scenario_preset_id not in supported_presets:
            raise UnsupportedPreset(
                f"scenario preset {scenario_preset_id!r} is not supported by "
                f"checkpoint; supported={tuple(supported_presets)}"
            )
        supported_combinations = {
            (str(value[0]), str(value[1]))
            for value in contract["supported_combinations"]
        }
        if (period, scenario_preset_id) not in supported_combinations:
            raise UnsupportedCombination(
                f"period/preset combination is not supported by checkpoint: "
                f"period={period!r}, preset={scenario_preset_id!r}"
            )
        declared_ids = [
            str(value)
            for value in supported_presets[scenario_preset_id]
        ]

    saved_policy_signature = contract["policy_space_signature"]
    saved_policy = saved_policy_signature["payload"]
    live_policy_signature = build_policy_space_signature(
        live_metadata,
        policy_spec=policy_spec,
    )
    live_policy = live_policy_signature["payload"]

    for key in ("protocol_version", "policy_spec"):
        difference = _first_difference(
            saved_policy.get(key),
            live_policy.get(key),
            path=f"policy_space.{key}",
        )
        if difference is not None:
            _raise_difference(
                PolicySpaceMismatch,
                label="policy space mismatch",
                difference=difference,
                saved_hash=str(saved_policy_signature["sha256"]),
                live_hash=str(live_policy_signature["sha256"]),
            )

    saved_fixed = _intersection_payloads(
        saved_policy,
        label="policy_space_signature.payload",
    )
    live_fixed = _intersection_payloads(
        live_policy,
        label="live_policy_space_signature.payload",
    )
    if controlled_intersection_ids is None:
        controlled_ids = list(live_fixed)
    else:
        controlled_ids = [str(value) for value in controlled_intersection_ids]
        if (
            not controlled_ids
            or len(controlled_ids) != len(set(controlled_ids))
        ):
            raise PolicySpaceMismatch(
                "controlled intersection IDs must be non-empty and unique"
            )
    if declared_ids is not None and controlled_ids != declared_ids:
        raise PolicySpaceMismatch(
            "controlled intersection IDs do not exactly match declared preset: "
            f"preset={scenario_preset_id!r}, declared={declared_ids}, "
            f"controlled={controlled_ids}"
        )
    missing_live = [
        intersection_id
        for intersection_id in controlled_ids
        if intersection_id not in live_fixed
    ]
    unknown_saved = [
        intersection_id
        for intersection_id in controlled_ids
        if intersection_id not in saved_fixed
    ]
    if missing_live or unknown_saved:
        raise PolicySpaceMismatch(
            "controlled intersection subset mismatch: "
            f"missing_live={missing_live}, unknown_saved={unknown_saved}"
        )

    for intersection_id in controlled_ids:
        difference = _first_difference(
            saved_fixed[intersection_id],
            live_fixed[intersection_id],
            path=f"intersections[{intersection_id}]",
        )
        if difference is not None:
            _raise_difference(
                PolicySpaceMismatch,
                label=f"policy space mismatch for {intersection_id}",
                difference=difference,
                saved_hash=str(saved_policy_signature["sha256"]),
                live_hash=str(live_policy_signature["sha256"]),
            )

    live_program_signature = build_program_signature(
        live_metadata,
        policy_spec=policy_spec,
    )
    live_program = live_program_signature["payload"]
    live_programs = _intersection_payloads(
        live_program,
        label=f"live_program_signature[{period!r}].payload",
    )
    saved_program_hashes: dict[str, str] = {}
    if version == ENVIRONMENT_CONTRACT_VERSION:
        saved_program_signature = contract["program_signatures"][period]
        saved_program = saved_program_signature["payload"]
        saved_common = saved_program
        saved_programs = _intersection_payloads(
            saved_program,
            label=f"program_signatures[{period!r}].payload",
        )
        common_saved_hash = str(saved_program_signature["sha256"])
    else:
        saved_period_signature = contract["program_signatures"][period]
        saved_common = saved_period_signature
        signed_programs = saved_period_signature["intersections"]
        saved_programs = {
            str(intersection_id): signature["payload"]
            for intersection_id, signature in signed_programs.items()
        }
        saved_program_hashes = {
            str(intersection_id): str(signature["sha256"])
            for intersection_id, signature in signed_programs.items()
        }
        common_saved_hash = payload_sha256(
            {
                key: saved_period_signature[key]
                for key in (
                    "period",
                    "decision_interval_s",
                    "minimum_green_s",
                )
            }
        )

    live_common_hash = payload_sha256(
        {
            key: live_program[key]
            for key in (
                "period",
                "decision_interval_s",
                "minimum_green_s",
            )
        }
    )
    for key in ("period", "decision_interval_s", "minimum_green_s"):
        difference = _first_difference(
            saved_common.get(key),
            live_program.get(key),
            path=f"program[{period}].{key}",
        )
        if difference is not None:
            _raise_difference(
                ProgramMismatch,
                label=f"program mismatch for {period}",
                difference=difference,
                saved_hash=common_saved_hash,
                live_hash=live_common_hash,
            )

    for intersection_id in controlled_ids:
        if (
            intersection_id not in saved_programs
            or intersection_id not in live_programs
        ):
            raise ProgramMismatch(
                f"program {period!r} has no entry for {intersection_id!r}"
            )
        live_entry = live_programs[intersection_id]
        if version == ENVIRONMENT_CONTRACT_VERSION:
            saved_entry = saved_programs[intersection_id]
            saved_entry_hash = common_saved_hash
            live_entry_hash = str(live_program_signature["sha256"])
        else:
            live_entry = {
                "period": period,
                "intersection_id": intersection_id,
                "decision_interval_s": live_program["decision_interval_s"],
                "minimum_green_s": live_program["minimum_green_s"],
                **{
                    str(key): value
                    for key, value in live_entry.items()
                    if str(key) != "intersection_id"
                },
            }
            saved_entry = saved_programs[intersection_id]
            saved_entry_hash = saved_program_hashes[intersection_id]
            live_entry_hash = payload_sha256(live_entry)
        difference = _first_difference(
            saved_entry,
            live_entry,
            path=f"program[{period}].intersections[{intersection_id}]",
        )
        if difference is not None:
            _raise_difference(
                ProgramMismatch,
                label=f"program mismatch for {period}/{intersection_id}",
                difference=difference,
                saved_hash=saved_entry_hash,
                live_hash=live_entry_hash,
            )

    if version == ENVIRONMENT_CONTRACT_VERSION:
        program_sha256 = common_saved_hash
        active_program_hashes = {
            intersection_id: common_saved_hash
            for intersection_id in controlled_ids
        }
    else:
        active_program_hashes = {
            intersection_id: saved_program_hashes[intersection_id]
            for intersection_id in controlled_ids
        }
        program_sha256 = payload_sha256(active_program_hashes)

    return {
        "period": period,
        "scenario_preset_id": scenario_preset_id,
        "controlled_intersection_ids": controlled_ids,
        "policy_space_sha256": str(saved_policy_signature["sha256"]),
        "program_sha256": program_sha256,
        "program_sha256_by_intersection": active_program_hashes,
    }
