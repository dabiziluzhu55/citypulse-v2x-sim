"""Versioned scenario/model aliases for CoV2X deployment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRAINING_INTERSECTION_IDS = tuple(f"demo_{i}" for i in range(1, 21))
DEFAULT_MODEL_ALIAS = "cv_joint_v1"


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
    "cv_joint_v1": ModelAlias(
        alias="cv_joint_v1",
        checkpoint_path=_MODEL_DIR / "cv_joint_v1_generation_003.pt",
        manifest_path=_MODEL_DIR / "cv_joint_v1_manifest.json",
        training_intersection_ids=TRAINING_INTERSECTION_IDS,
        adapter_module="traffic_control.cov2x.deployment",
        description=(
            "Generation-3 CV Joint Cloud/Vehicle candidate with frozen IPPO Road"
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
    if (
        model_alias == "cv_joint_v1"
        and (
            len(controlled) != len(TRAINING_INTERSECTION_IDS)
            or set(controlled) != set(TRAINING_INTERSECTION_IDS)
        )
    ):
        raise ValueError(
            "cv_joint_v1 requires exactly demo_1..demo_20 intersections"
        )
    trained = set(model.training_intersection_ids)
    unknown = [iid for iid in controlled if iid not in trained]
    if unknown:
        raise ValueError(
            f"model_alias={model_alias!r} was not trained on intersections "
            f"{unknown}; controlled subset must be a subset of "
            f"{model.training_intersection_ids}"
        )
    return model_alias, model.checkpoint_path
