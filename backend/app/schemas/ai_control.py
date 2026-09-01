"""API schemas for event-scoped Qwen signal-control plans."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from simulation.sumo.engine.ai_control import (
    AIControlConfig as RuntimeAIControlConfig,
    AIControlPlan as RuntimeAIControlPlan,
    AIControlValidationError,
)


class AIControlPlan(BaseModel):
    """Strict JSON shape accepted from the Qwen control planner."""

    model_config = ConfigDict(extra="forbid")

    controlled_intersections: list[str] = Field(default_factory=list)
    valid_seconds: float = 30.0
    signal_plan: dict[str, list[int]] = Field(default_factory=dict)
    objective: str
    reason: str
    fallback_to_baseline: bool = False

    @field_validator("controlled_intersections")
    @classmethod
    def _unique_intersections(cls, value: list[str]) -> list[str]:
        normalized = [str(item).strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("controlled_intersections cannot contain empty IDs.")
        if len(normalized) != len(set(normalized)):
            raise ValueError("controlled_intersections must not contain duplicates.")
        return normalized

    @field_validator("objective")
    @classmethod
    def _objective(cls, value: str) -> str:
        value = str(value).strip()
        if not value or len(value) > 200:
            raise ValueError("objective must contain 1-200 characters.")
        return value

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        value = str(value).strip()
        if not value or len(value) > 1_000:
            raise ValueError("reason must contain 1-1000 characters.")
        return value

    @model_validator(mode="after")
    def _validate_shape(self) -> "AIControlPlan":
        try:
            RuntimeAIControlPlan.from_mapping(
                self.model_dump(mode="python"),
                config=RuntimeAIControlConfig(),
            )
        except AIControlValidationError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def to_runtime(self) -> RuntimeAIControlPlan:
        return RuntimeAIControlPlan.from_mapping(
            self.model_dump(mode="python"), config=RuntimeAIControlConfig()
        )


class AIControlStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = "INACTIVE"
    ai_enabled: bool = False
    active_event_id: str | None = None
    allowed_scope: list[str] = Field(default_factory=list)
    controlled_intersections: list[str] = Field(default_factory=list)
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


class AIControlPlanRequest(BaseModel):
    """Internal command payload used when installing a validated plan."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    plan: AIControlPlan
    allowed_scope: list[str] = Field(default_factory=list)
    plan_id: str
    plan_started_at: float


__all__ = ["AIControlPlan", "AIControlPlanRequest", "AIControlStatus"]
