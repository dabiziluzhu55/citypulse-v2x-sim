"""管控评估报告导出请求 Schema：只传场景与 session 映射，不传指标数值。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

REPORT_ALGORITHMS = (
    "fixed",
    "max_pressure",
    "sotl",
    "ippo",
    "mappo",
    "cov2x",
)

ReportAlgorithm = Literal[
    "fixed",
    "max_pressure",
    "sotl",
    "ippo",
    "mappo",
    "cov2x",
]


class EvaluationReportScenario(BaseModel):
    scenario_preset_id: str = Field(min_length=1)
    period: str = Field(min_length=1)
    window_start_seconds: float = Field(ge=0.0)
    duration_seconds: float = Field(gt=0.0)


class EvaluationReportRun(BaseModel):
    algorithm: ReportAlgorithm
    session_id: str | None = None

    @field_validator("session_id")
    @classmethod
    def normalize_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class EvaluationReportRequest(BaseModel):
    scenario: EvaluationReportScenario | None = None
    runs: list[EvaluationReportRun] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_algorithms(self) -> EvaluationReportRequest:
        seen: set[str] = set()
        duplicates: list[str] = []
        for run in self.runs:
            if run.algorithm in seen:
                duplicates.append(run.algorithm)
            seen.add(run.algorithm)
        if duplicates:
            raise ValueError(f"duplicate algorithms: {duplicates}")
        return self
