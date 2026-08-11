"""Explicit scenario/model aliases for IPPO deployment.

Scenario presets declare only controlled intersection IDs
(backend/app/scenario/presets.py).  This module maps a model alias to a
checkpoint path plus the model's training IDs, and a scenario alias to
(scenario_preset_id, model_alias).  The resolver rejects combinations where
the scenario's controlled IDs are not a subset of the model's training IDs.
Alias composition is explicit: a fine-tuned model gets a NEW model alias and
a NEW scenario alias instead of mutating existing entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .identity import IDENTITY_SLOT_IDS


@dataclass(frozen=True)
class ModelAlias:
    alias: str
    checkpoint_path: Path
    training_intersection_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioAlias:
    alias: str
    scenario_preset_id: str
    model_alias: str
    description: str = ""


MODEL_ALIASES: dict[str, ModelAlias] = {
    "ippo_v8_20tls_ep240": ModelAlias(
        alias="ippo_v8_20tls_ep240",
        checkpoint_path=Path(__file__).resolve().parent
        / "models"
        / "ippo_v8_20tls_ep240.pt",
        training_intersection_ids=IDENTITY_SLOT_IDS,
    ),
    "ippo_v8_20tls_ep160": ModelAlias(
        alias="ippo_v8_20tls_ep160",
        checkpoint_path=Path(__file__).resolve().parent
        / "models"
        / "ippo_v8_20tls_ep160.pt",
        training_intersection_ids=IDENTITY_SLOT_IDS,
    ),
}

SCENARIO_ALIASES: dict[str, ScenarioAlias] = {
    "xiongan_20": ScenarioAlias(
        alias="xiongan_20",
        scenario_preset_id="xiongan_20",
        model_alias="ippo_v8_20tls_ep240",
        description="雄安 20 路口全集，通用 20 路口模型",
    ),
    "east_dense": ScenarioAlias(
        alias="east_dense",
        scenario_preset_id="east_dense",
        model_alias="ippo_v8_20tls_ep240",
        description="东部密集路口（demo_3/5/6/9）零样本推理，通用 20 路口模型",
    ),
    "west_dense": ScenarioAlias(
        alias="west_dense",
        scenario_preset_id="west_dense",
        model_alias="ippo_v8_20tls_ep240",
        description="西部密集路口（demo_14/15/19）零样本推理，通用 20 路口模型",
    ),
}


def resolve_model_path(alias: str) -> Path:
    model = MODEL_ALIASES.get(alias)
    if model is None:
        raise ValueError(
            f"Unknown IPPO model alias: {alias!r}; "
            f"available: {sorted(MODEL_ALIASES)}"
        )
    path = model.checkpoint_path
    if not path.is_file():
        raise FileNotFoundError(f"IPPO checkpoint does not exist: {path}")
    return path


def default_model_alias_for(scenario_preset_id: str) -> str:
    scenario = SCENARIO_ALIASES.get(scenario_preset_id)
    if scenario is None:
        raise ValueError(
            f"No default IPPO model alias for scenario preset {scenario_preset_id!r}; "
            f"available: {sorted(SCENARIO_ALIASES)}"
        )
    return scenario.model_alias


def validate_alias_combo(
    intersection_ids: Any,
    model_alias: str,
) -> tuple[str, Path]:
    """Validate that controlled IDs are a subset of the model's training IDs.

    Returns ``(model_alias, checkpoint_path)``.
    """
    model = MODEL_ALIASES.get(model_alias)
    if model is None:
        raise ValueError(
            f"Unknown IPPO model alias: {model_alias!r}; "
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
