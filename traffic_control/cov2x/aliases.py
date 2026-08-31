"""Versioned scenario/model aliases for CoV2X deployment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import (
    DEFAULT_JOINT_MODEL_FILENAME,
    TEMPORARY_CAP_MANIFEST_FILENAME,
    TEMPORARY_CAP_MODEL_FILENAME,
    TRAINING_INTERSECTION_IDS,
)


DEFAULT_MODEL_ALIAS = "cov2x_g30_temp_cap_u24"


@dataclass(frozen=True)
class ModelAlias:
    alias: str
    checkpoint_path: Path
    training_intersection_ids: tuple[str, ...]
    adapter_module: str
    manifest_path: Path | None = None
    description: str = ""


@dataclass(frozen=True)
class ScenarioAlias:
    alias: str
    scenario_preset_id: str
    model_alias: str
    description: str = ""


_MODEL_DIR = Path(__file__).resolve().parent / "models"

MODEL_ALIASES: dict[str, ModelAlias] = {
    DEFAULT_MODEL_ALIAS: ModelAlias(
        alias=DEFAULT_MODEL_ALIAS,
        checkpoint_path=_MODEL_DIR / TEMPORARY_CAP_MODEL_FILENAME,
        manifest_path=_MODEL_DIR / TEMPORARY_CAP_MANIFEST_FILENAME,
        training_intersection_ids=TRAINING_INTERSECTION_IDS,
        adapter_module=(
            "traffic_control.cov2x.candidates.temporary_cap_u24"
        ),
        description=(
            "Frozen G30 Road/Cloud plus update-24 temporary base-relative "
            "Vehicle speed-cap candidate"
        ),
    ),
    "cov2x_joint_ep12": ModelAlias(
        alias="cov2x_joint_ep12",
        checkpoint_path=_MODEL_DIR / DEFAULT_JOINT_MODEL_FILENAME,
        training_intersection_ids=TRAINING_INTERSECTION_IDS,
        adapter_module=(
            "traffic_control.cov2x.candidates.legacy_joint_ep12"
        ),
        description=(
            "Legacy EP12 joint CTDE candidate (format_version=2)"
        ),
    ),
}

SCENARIO_ALIASES: dict[str, ScenarioAlias] = {
    "xiongan_20": ScenarioAlias(
        alias="xiongan_20",
        scenario_preset_id="xiongan_20",
        model_alias=DEFAULT_MODEL_ALIAS,
        description="Global demo_1..demo_20 scope",
    ),
    "east_dense": ScenarioAlias(
        alias="east_dense",
        scenario_preset_id="east_dense",
        model_alias=DEFAULT_MODEL_ALIAS,
        description="East demo_3/5/6/9 scope; other intersections stay Fixed",
    ),
    "west_dense": ScenarioAlias(
        alias="west_dense",
        scenario_preset_id="west_dense",
        model_alias=DEFAULT_MODEL_ALIAS,
        description="West demo_14/15/19 scope; other intersections stay Fixed",
    ),
}


def resolve_model(alias: str) -> ModelAlias:
    model = MODEL_ALIASES.get(alias)
    if model is None:
        raise ValueError(
            f"Unknown CoV2X model alias: {alias!r}; "
            f"available: {sorted(MODEL_ALIASES)}"
        )
    return model


def resolve_model_path(alias: str) -> Path:
    model = resolve_model(alias)
    path = model.checkpoint_path
    if not path.is_file():
        raise FileNotFoundError(f"CoV2X checkpoint does not exist: {path}")
    return path


def default_model_alias_for(scenario_preset_id: str) -> str:
    scenario = SCENARIO_ALIASES.get(scenario_preset_id)
    if scenario is None:
        raise ValueError(
            f"No default CoV2X model alias for scenario preset "
            f"{scenario_preset_id!r}; available: {sorted(SCENARIO_ALIASES)}"
        )
    return scenario.model_alias


def validate_alias_combo(
    intersection_ids: Any,
    model_alias: str,
) -> tuple[str, Path]:
    """Validate that controlled IDs are a subset of model training IDs."""
    model = resolve_model(model_alias)
    controlled = tuple(str(iid) for iid in intersection_ids)
    trained = set(model.training_intersection_ids)
    unknown = [iid for iid in controlled if iid not in trained]
    if unknown:
        raise ValueError(
            f"model_alias={model_alias!r} was not trained on intersections "
            f"{unknown}; controlled subset must be a subset of "
            f"{model.training_intersection_ids}"
        )
    return model_alias, model.checkpoint_path
