"""Public control-mode metadata for Backend validation (no checkpoint paths)."""

from __future__ import annotations

from dataclasses import dataclass

TRAINING_INTERSECTION_IDS_20: tuple[str, ...] = tuple(
    f"demo_{index}" for index in range(1, 21)
)


@dataclass(frozen=True)
class PublicModelAlias:
    alias: str
    training_intersection_ids: tuple[str, ...]
    description: str = ""


@dataclass(frozen=True)
class ScenarioModelDefault:
    scenario_preset_id: str
    default_model_alias: str
    description: str = ""


IPPO_MODEL_ALIASES: dict[str, PublicModelAlias] = {
    "ippo_v8_20tls_ep160": PublicModelAlias(
        alias="ippo_v8_20tls_ep160",
        training_intersection_ids=TRAINING_INTERSECTION_IDS_20,
        description="IPPO v8 generalist 20-intersection model",
    ),
}

IPPO_SCENARIO_DEFAULTS: dict[str, ScenarioModelDefault] = {
    preset_id: ScenarioModelDefault(
        scenario_preset_id=preset_id,
        default_model_alias="ippo_v8_20tls_ep160",
    )
    for preset_id in ("xiongan_20", "east_dense", "west_dense")
}

MAPPO_MODEL_ALIASES: dict[str, PublicModelAlias] = {
    "mappo_cooperative_20tls_ep160": PublicModelAlias(
        alias="mappo_cooperative_20tls_ep160",
        training_intersection_ids=TRAINING_INTERSECTION_IDS_20,
        description="MAPPO cooperative 20-intersection model",
    ),
}

MAPPO_SCENARIO_DEFAULTS: dict[str, ScenarioModelDefault] = {
    preset_id: ScenarioModelDefault(
        scenario_preset_id=preset_id,
        default_model_alias="mappo_cooperative_20tls_ep160",
    )
    for preset_id in ("xiongan_20", "east_dense", "west_dense")
}

COV2X_MODEL_ALIASES: dict[str, PublicModelAlias] = {
    "cov2x_g30_temp_cap_u24": PublicModelAlias(
        alias="cov2x_g30_temp_cap_u24",
        training_intersection_ids=TRAINING_INTERSECTION_IDS_20,
        description="CoV2X G30 temporary cap candidate (default)",
    ),
    "cov2x_joint_ep12": PublicModelAlias(
        alias="cov2x_joint_ep12",
        training_intersection_ids=TRAINING_INTERSECTION_IDS_20,
        description="Legacy EP12 joint CTDE candidate",
    ),
}

COV2X_SCENARIO_DEFAULTS: dict[str, ScenarioModelDefault] = {
    preset_id: ScenarioModelDefault(
        scenario_preset_id=preset_id,
        default_model_alias="cov2x_g30_temp_cap_u24",
    )
    for preset_id in ("xiongan_20", "east_dense", "west_dense")
}


def default_model_alias_for(control_mode: str, scenario_preset_id: str) -> str:
    if control_mode == "ippo":
        entry = IPPO_SCENARIO_DEFAULTS.get(scenario_preset_id)
    elif control_mode == "mappo":
        entry = MAPPO_SCENARIO_DEFAULTS.get(scenario_preset_id)
    elif control_mode == "cov2x":
        entry = COV2X_SCENARIO_DEFAULTS.get(scenario_preset_id)
    else:
        raise ValueError(f"control_mode {control_mode!r} has no model aliases")
    if entry is None:
        raise ValueError(
            f"No default model alias for {control_mode!r} preset {scenario_preset_id!r}"
        )
    return entry.default_model_alias


def validate_model_alias_combo(
    control_mode: str,
    intersection_ids: object,
    model_alias: str,
) -> str:
    """Validate alias exists and controlled IDs ⊆ training IDs. Returns alias."""
    if control_mode == "ippo":
        registry = IPPO_MODEL_ALIASES
    elif control_mode == "mappo":
        registry = MAPPO_MODEL_ALIASES
    elif control_mode == "cov2x":
        registry = COV2X_MODEL_ALIASES
    else:
        raise ValueError(f"Unknown alias control_mode: {control_mode!r}")
    model = registry.get(model_alias)
    if model is None:
        raise ValueError(
            f"Unknown {control_mode} model alias: {model_alias!r}; "
            f"available: {sorted(registry)}"
        )
    controlled = tuple(str(item) for item in intersection_ids)
    trained = set(model.training_intersection_ids)
    unknown = [item for item in controlled if item not in trained]
    if unknown:
        raise ValueError(
            f"model_alias={model_alias!r} was not trained on intersections "
            f"{unknown}; controlled subset must be a subset of "
            f"{model.training_intersection_ids}"
        )
    return model_alias
