"""Shared contracts for event-scoped AI signal control.

The backend creates plans, while the SUMO runtime validates the same JSON
contract again before any traffic-light state is changed.  This module is
dependency-light so it can be imported by both the API process and a
Redis/Celery worker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


AI_CONTROL_STATES = frozenset(
    {"INACTIVE", "ARMED", "ACTIVE", "RECOVERY", "FINISHED", "FALLBACK"}
)
AI_CONTROL_PLAN_FIELDS = frozenset(
    {
        "controlled_intersections",
        "valid_seconds",
        "signal_plan",
        "objective",
        "reason",
        "fallback_to_baseline",
    }
)


class AIControlValidationError(ValueError):
    """Raised when a control plan/configuration is unsafe or malformed."""


def _finite_positive(value: object, field_name: str) -> float:
    if isinstance(value, bool):
        raise AIControlValidationError(f"{field_name} must be a number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AIControlValidationError(f"{field_name} must be a number.") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise AIControlValidationError(f"{field_name} must be positive and finite.")
    return parsed


def _text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise AIControlValidationError(f"{field_name} must be a string.")
    parsed = value.strip()
    if not parsed:
        raise AIControlValidationError(f"{field_name} must not be empty.")
    if len(parsed) > max_length:
        raise AIControlValidationError(
            f"{field_name} must be at most {max_length} characters."
        )
    return parsed


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise AIControlValidationError(f"{field_name} must be an array.")
    result = []
    for item in value:
        parsed = _text(item, field_name, max_length=128)
        result.append(parsed)
    if len(result) != len(set(result)):
        raise AIControlValidationError(f"{field_name} must not contain duplicates.")
    return tuple(result)


def _phase_mapping(
    value: object, field_name: str
) -> dict[str, tuple[int, ...]]:
    """Normalize a serialized intersection -> phase-sequence mapping."""

    if not isinstance(value, Mapping):
        raise AIControlValidationError(f"{field_name} must be an object.")
    result: dict[str, tuple[int, ...]] = {}
    for raw_intersection_id, raw_phases in value.items():
        if not isinstance(raw_intersection_id, str):
            raise AIControlValidationError(
                f"{field_name} keys must be non-empty strings."
            )
        intersection_id = raw_intersection_id.strip()
        if not intersection_id:
            raise AIControlValidationError(
                f"{field_name} keys must be non-empty strings."
            )
        if intersection_id in result:
            raise AIControlValidationError(
                f"{field_name} keys must be unique after trimming."
            )
        if not isinstance(raw_phases, Sequence) or isinstance(
            raw_phases, (str, bytes)
        ):
            raise AIControlValidationError(
                f"{field_name}[{intersection_id!r}] must be an array."
            )
        phases: list[int] = []
        for phase in raw_phases:
            if isinstance(phase, bool) or not isinstance(phase, int):
                raise AIControlValidationError(
                    f"{field_name}[{intersection_id!r}] must contain integer phases."
                )
            phases.append(int(phase))
        result[intersection_id] = tuple(phases)
    return result


@dataclass(frozen=True)
class AIControlConfig:
    """Runtime policy serialized with a simulation session."""

    plan_valid_seconds: float = 30.0
    slot_seconds: float = 5.0
    replan_seconds: float = 30.0
    recovery_seconds: float = 60.0
    recovery_clear_samples: int = 3
    scope_hops: int = 1
    max_plan_failures: int = 2

    def __post_init__(self) -> None:
        normalized_floats = {}
        for name in (
            "plan_valid_seconds",
            "slot_seconds",
            "replan_seconds",
            "recovery_seconds",
        ):
            normalized_floats[name] = _finite_positive(getattr(self, name), name)
        for name, value in normalized_floats.items():
            object.__setattr__(self, name, value)
        if self.plan_valid_seconds < self.slot_seconds:
            raise AIControlValidationError(
                "plan_valid_seconds must be at least slot_seconds."
            )
        if not math.isclose(self.plan_valid_seconds % self.slot_seconds, 0.0, abs_tol=1e-6):
            raise AIControlValidationError(
                "plan_valid_seconds must be divisible by slot_seconds."
            )
        for name in ("recovery_clear_samples", "scope_hops", "max_plan_failures"):
            value = getattr(self, name)
            if isinstance(value, bool):
                raise AIControlValidationError(f"{name} must be an integer.")
            try:
                parsed = int(value)
            except (TypeError, ValueError) as exc:
                raise AIControlValidationError(f"{name} must be an integer.") from exc
            if isinstance(value, float) and not value.is_integer():
                raise AIControlValidationError(f"{name} must be an integer.")
            object.__setattr__(self, name, parsed)
        if self.recovery_clear_samples < 1:
            raise AIControlValidationError("recovery_clear_samples must be positive.")
        if self.scope_hops < 0 or self.scope_hops > 3:
            raise AIControlValidationError("scope_hops must be between 0 and 3.")
        if self.max_plan_failures < 0:
            raise AIControlValidationError("max_plan_failures cannot be negative.")

    @property
    def slot_count(self) -> int:
        return int(round(self.plan_valid_seconds / self.slot_seconds))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_valid_seconds": self.plan_valid_seconds,
            "slot_seconds": self.slot_seconds,
            "replan_seconds": self.replan_seconds,
            "recovery_seconds": self.recovery_seconds,
            "recovery_clear_samples": self.recovery_clear_samples,
            "scope_hops": self.scope_hops,
            "max_plan_failures": self.max_plan_failures,
        }


@dataclass(frozen=True)
class AIControlPlan:
    """Strict high-level signal plan returned by Qwen."""

    controlled_intersections: tuple[str, ...]
    valid_seconds: float
    signal_plan: Mapping[str, tuple[int, ...]]
    objective: str
    reason: str
    fallback_to_baseline: bool = False

    @classmethod
    def from_mapping(
        cls,
        value: object,
        *,
        config: AIControlConfig | None = None,
    ) -> "AIControlPlan":
        if not isinstance(value, Mapping):
            raise AIControlValidationError("AI control plan must be a JSON object.")
        unknown = set(value) - AI_CONTROL_PLAN_FIELDS
        missing = AI_CONTROL_PLAN_FIELDS - set(value)
        if unknown:
            raise AIControlValidationError(
                f"AI control plan has unknown fields: {sorted(str(item) for item in unknown)}."
            )
        if missing:
            raise AIControlValidationError(
                f"AI control plan is missing fields: {sorted(str(item) for item in missing)}."
            )
        policy = config or AIControlConfig()
        controlled = _string_tuple(
            value["controlled_intersections"], "controlled_intersections"
        )
        raw_valid_seconds = value["valid_seconds"]
        if isinstance(raw_valid_seconds, bool) or not isinstance(
            raw_valid_seconds, (int, float)
        ):
            raise AIControlValidationError("valid_seconds must be a JSON number.")
        valid_seconds = _finite_positive(raw_valid_seconds, "valid_seconds")
        if not math.isclose(valid_seconds, policy.plan_valid_seconds, abs_tol=1e-6):
            raise AIControlValidationError(
                f"valid_seconds must equal {policy.plan_valid_seconds:g}."
            )
        raw_signal_plan = value["signal_plan"]
        if not isinstance(raw_signal_plan, Mapping):
            raise AIControlValidationError("signal_plan must be an object.")
        signal_plan: dict[str, tuple[int, ...]] = {}
        for intersection_id, raw_phases in raw_signal_plan.items():
            if not isinstance(intersection_id, str) or not intersection_id.strip():
                raise AIControlValidationError("signal_plan keys must be non-empty strings.")
            normalized_intersection_id = intersection_id.strip()
            if normalized_intersection_id in signal_plan:
                raise AIControlValidationError(
                    "signal_plan keys must be unique after trimming."
                )
            if not isinstance(raw_phases, Sequence) or isinstance(raw_phases, (str, bytes)):
                raise AIControlValidationError(
                    f"signal_plan[{intersection_id!r}] must be an array."
                )
            phases: list[int] = []
            for phase in raw_phases:
                if isinstance(phase, bool) or not isinstance(phase, int):
                    raise AIControlValidationError(
                        f"signal_plan[{intersection_id!r}] must contain integer phases."
                    )
                phases.append(int(phase))
            if len(phases) != policy.slot_count:
                raise AIControlValidationError(
                    f"signal_plan[{intersection_id!r}] must contain {policy.slot_count} phases."
                )
            signal_plan[normalized_intersection_id] = tuple(phases)

        fallback = value["fallback_to_baseline"]
        if not isinstance(fallback, bool):
            raise AIControlValidationError("fallback_to_baseline must be boolean.")
        if fallback:
            if controlled or signal_plan:
                raise AIControlValidationError(
                    "A fallback plan must not control intersections."
                )
        else:
            if not controlled:
                raise AIControlValidationError(
                    "controlled_intersections must not be empty unless falling back."
                )
            if set(signal_plan) != set(controlled):
                raise AIControlValidationError(
                    "signal_plan keys must exactly match controlled_intersections."
                )
        return cls(
            controlled_intersections=controlled,
            valid_seconds=valid_seconds,
            signal_plan=signal_plan,
            objective=_text(value["objective"], "objective", max_length=200),
            reason=_text(value["reason"], "reason", max_length=1_000),
            fallback_to_baseline=fallback,
        )

    def validate_runtime(
        self,
        *,
        allowed_scope: Sequence[str],
        phase_orders: Mapping[str, Sequence[int]],
    ) -> None:
        allowed = {str(item) for item in allowed_scope}
        controlled = set(self.controlled_intersections)
        if not controlled <= allowed:
            raise AIControlValidationError(
                f"AI plan controls intersections outside allowed scope: "
                f"{sorted(controlled - allowed)}."
            )
        unknown = controlled - set(phase_orders)
        if unknown:
            raise AIControlValidationError(
                f"AI plan controls unknown intersections: {sorted(unknown)}."
            )
        for intersection_id, phases in self.signal_plan.items():
            valid_phases = {int(value) for value in phase_orders[intersection_id]}
            invalid = set(phases) - valid_phases
            if invalid:
                raise AIControlValidationError(
                    f"AI plan has invalid phases for {intersection_id}: {sorted(invalid)}."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "controlled_intersections": list(self.controlled_intersections),
            "valid_seconds": self.valid_seconds,
            "signal_plan": {
                intersection_id: list(phases)
                for intersection_id, phases in self.signal_plan.items()
            },
            "objective": self.objective,
            "reason": self.reason,
            "fallback_to_baseline": self.fallback_to_baseline,
        }


@dataclass(frozen=True)
class AIControlPlanSummary:
    """The last successfully installed plan, retained for read-only Copilot Q&A."""

    event_id: str
    plan_id: str
    sequence: int
    plan_started_at: float
    plan_valid_until: float
    controlled_intersections: tuple[str, ...]
    target_phase_sequence: Mapping[str, tuple[int, ...]]
    objective: str
    reason: str

    @classmethod
    def from_plan(
        cls,
        *,
        event_id: str,
        plan_id: str,
        sequence: int,
        plan_started_at: float,
        plan: AIControlPlan,
    ) -> "AIControlPlanSummary":
        return cls(
            event_id=event_id,
            plan_id=plan_id,
            sequence=sequence,
            plan_started_at=plan_started_at,
            plan_valid_until=plan_started_at + plan.valid_seconds,
            controlled_intersections=plan.controlled_intersections,
            target_phase_sequence=plan.signal_plan,
            objective=plan.objective,
            reason=plan.reason,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AIControlPlanSummary":
        return cls(**dict(value))

    def __post_init__(self) -> None:
        event_id = _text(self.event_id, "event_id", max_length=256)
        plan_id = _text(self.plan_id, "plan_id", max_length=512)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "plan_id", plan_id)
        try:
            sequence = int(self.sequence)
        except (TypeError, ValueError) as exc:
            raise AIControlValidationError(
                "AI control plan summary sequence must be an integer."
            ) from exc
        if isinstance(self.sequence, float) and not self.sequence.is_integer():
            raise AIControlValidationError(
                "AI control plan summary sequence must be an integer."
            )
        if sequence < 1:
            raise AIControlValidationError(
                "AI control plan summary sequence must be positive."
            )
        object.__setattr__(self, "sequence", sequence)
        for name in ("plan_started_at", "plan_valid_until"):
            value = getattr(self, name)
            try:
                parsed = float(value)
            except (TypeError, ValueError) as exc:
                raise AIControlValidationError(
                    f"{name} must be numeric."
                ) from exc
            if not math.isfinite(parsed):
                raise AIControlValidationError(f"{name} must be finite.")
            object.__setattr__(self, name, parsed)
        controlled = _string_tuple(
            self.controlled_intersections, "controlled_intersections"
        )
        target_phase_sequence = _phase_mapping(
            self.target_phase_sequence, "target_phase_sequence"
        )
        if set(target_phase_sequence) != set(controlled):
            raise AIControlValidationError(
                "target_phase_sequence keys must exactly match controlled_intersections."
            )
        if self.plan_valid_until < self.plan_started_at:
            raise AIControlValidationError(
                "plan_valid_until cannot be before plan_started_at."
            )
        object.__setattr__(self, "controlled_intersections", controlled)
        object.__setattr__(self, "target_phase_sequence", target_phase_sequence)
        object.__setattr__(
            self, "objective", _text(self.objective, "objective", max_length=200)
        )
        object.__setattr__(
            self, "reason", _text(self.reason, "reason", max_length=1_000)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "plan_id": self.plan_id,
            "sequence": self.sequence,
            "plan_started_at": self.plan_started_at,
            "plan_valid_until": self.plan_valid_until,
            "controlled_intersections": list(self.controlled_intersections),
            "target_phase_sequence": {
                intersection_id: list(phases)
                for intersection_id, phases in self.target_phase_sequence.items()
            },
            "objective": self.objective,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AIControlStatus:
    """Serializable AI takeover status embedded in every simulation snapshot."""

    state: str = "INACTIVE"
    ai_enabled: bool = False
    active_event_id: str | None = None
    allowed_scope: tuple[str, ...] = ()
    controlled_intersections: tuple[str, ...] = ()
    plan_sequence: int = 0
    plan_id: str | None = None
    plan_started_at: float | None = None
    plan_valid_until: float | None = None
    recovery_deadline: float | None = None
    baseline_controller: str | None = None
    last_error: str | None = None
    fallback_reason: str | None = None
    last_objective: str | None = None
    last_reason: str | None = None
    rag_status: str | None = None
    last_plan: AIControlPlanSummary | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, str):
            raise AIControlValidationError("state must be a string.")
        if self.state not in AI_CONTROL_STATES:
            raise AIControlValidationError(f"Unsupported AI control state: {self.state!r}.")
        if isinstance(self.ai_enabled, bool) is False:
            raise AIControlValidationError("ai_enabled must be boolean.")
        try:
            plan_sequence = int(self.plan_sequence)
        except (TypeError, ValueError) as exc:
            raise AIControlValidationError("plan_sequence must be an integer.") from exc
        if isinstance(self.plan_sequence, float) and not self.plan_sequence.is_integer():
            raise AIControlValidationError("plan_sequence must be an integer.")
        object.__setattr__(self, "plan_sequence", plan_sequence)
        object.__setattr__(
            self,
            "allowed_scope",
            _string_tuple(self.allowed_scope, "allowed_scope"),
        )
        object.__setattr__(
            self,
            "controlled_intersections",
            _string_tuple(
                self.controlled_intersections, "controlled_intersections"
            ),
        )
        last_plan = self.last_plan
        if last_plan is not None and not isinstance(last_plan, AIControlPlanSummary):
            if not isinstance(last_plan, Mapping):
                raise AIControlValidationError("last_plan must be an object.")
            last_plan = AIControlPlanSummary.from_mapping(last_plan)
        object.__setattr__(self, "last_plan", last_plan)
        for name in (
            "plan_started_at",
            "plan_valid_until",
            "recovery_deadline",
        ):
            value = getattr(self, name)
            if value is not None:
                try:
                    parsed = float(value)
                except (TypeError, ValueError) as exc:
                    raise AIControlValidationError(f"{name} must be numeric.") from exc
                if not math.isfinite(parsed):
                    raise AIControlValidationError(f"{name} must be finite.")
                object.__setattr__(self, name, parsed)
        if plan_sequence < 0:
            raise AIControlValidationError("plan_sequence cannot be negative.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "ai_enabled": self.ai_enabled,
            "active_event_id": self.active_event_id,
            "allowed_scope": list(self.allowed_scope),
            "controlled_intersections": list(self.controlled_intersections),
            "plan_sequence": self.plan_sequence,
            "plan_id": self.plan_id,
            "plan_started_at": self.plan_started_at,
            "plan_valid_until": self.plan_valid_until,
            "recovery_deadline": self.recovery_deadline,
            "baseline_controller": self.baseline_controller,
            "last_error": self.last_error,
            "fallback_reason": self.fallback_reason,
            "last_objective": self.last_objective,
            "last_reason": self.last_reason,
            "rag_status": self.rag_status,
            "last_plan": self.last_plan.to_dict() if self.last_plan else None,
        }


def plan_from_json(
    raw: str,
    *,
    config: AIControlConfig | None = None,
) -> AIControlPlan:
    """Parse a raw JSON object without accepting prose or code fences."""

    import json

    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AIControlValidationError("Qwen control output is not valid JSON.") from exc
    return AIControlPlan.from_mapping(value, config=config)


__all__ = [
    "AI_CONTROL_PLAN_FIELDS",
    "AI_CONTROL_STATES",
    "AIControlConfig",
    "AIControlPlan",
    "AIControlPlanSummary",
    "AIControlStatus",
    "AIControlValidationError",
    "plan_from_json",
]
