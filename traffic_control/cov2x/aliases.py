"""Explicit scenario/model aliases for CoV2X deployment.

Scenario presets declare only controlled intersection IDs
(backend/app/scenario/presets.py).  This module maps a model alias to a
checkpoint path plus the model training IDs, and a scenario alias to
(scenario_preset_id, model_alias).  The resolver rejects combinations where
the scenario controlled IDs are not a subset of the model training IDs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contract import (
    DEFAULT_JOINT_MODEL_FILENAME,
    TRAINING_INTERSECTION_IDS,
)


@dataclass(frozen=True)
class ModelAlias:
    alias: str
    checkpoint_path: Path
    training_intersection_ids: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class ScenarioAlias:
    alias: str
    scenario_preset_id: str
    model_alias: str
    description: str = ""


MODEL_ALIASES: dict[str, ModelAlias] = {
    "cov2x_joint_ep12": ModelAlias(
        alias="cov2x_joint_ep12",
        checkpoint_path=Path(__file__).resolve().parent
        / "models"
        / DEFAULT_JOINT_MODEL_FILENAME,
        training_intersection_ids=TRAINING_INTERSECTION_IDS,
        description="EP12 车路云三端联合 CTDE 初版（format_version=2，默认部署）",
    ),
}

SCENARIO_ALIASES: dict[str, ScenarioAlias] = {
    "xiongan_20": ScenarioAlias(
        alias="xiongan_20",
        scenario_preset_id="xiongan_20",
        model_alias="cov2x_joint_ep12",
        description="雄安 20 路口全集，车路云三端联合初版",
    ),
    "east_dense": ScenarioAlias(
        alias="east_dense",
        scenario_preset_id="east_dense",
        model_alias="cov2x_joint_ep12",
        description="东部密集路口（demo_3/5/6/9）零样本推理，车路云联合初版",
    ),
    "west_dense": ScenarioAlias(
        alias="west_dense",
        scenario_preset_id="west_dense",
        model_alias="cov2x_joint_ep12",
        description="西部密集路口（demo_14/15/19）零样本推理，车路云联合初版",
    ),
}


def resolve_model_path(alias: str) -> Path:
    model = MODEL_ALIASES.get(alias)
    if model is None:
        raise ValueError(
            f"Unknown CoV2X model alias: {alias!r}; "
            f"available: {sorted(MODEL_ALIASES)}"
        )
    path = model.checkpoint_path
    if not path.is_file():
        raise FileNotFoundError(f"CoV2X checkpoint does not exist: {path}")
    return path


def default_model_alias_for(scenario_preset_id: str) -> str:
    scenario = SCENARIO_ALIASES.get(scenario_preset_id)
    if scenario is None:
        raise ValueError(
            f"No default CoV2X model alias for scenario preset {scenario_preset_id!r}; "
            f"available: {sorted(SCENARIO_ALIASES)}"
        )
    return scenario.model_alias


def validate_alias_combo(
    intersection_ids: Any,
    model_alias: str,
) -> tuple[str, Path]:
    """Validate that controlled IDs are a subset of the model training IDs."""
    model = MODEL_ALIASES.get(model_alias)
    if model is None:
        raise ValueError(
            f"Unknown CoV2X model alias: {model_alias!r}; "
            f"available: {sorted(MODEL_ALIASES)}"
        )
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
