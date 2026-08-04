"""MAPPO scenario/model alias resolution tests (mirrors IPPO coverage)."""

from __future__ import annotations

import pytest

from traffic_control.mappo.aliases import (
    MODEL_ALIASES,
    SCENARIO_ALIASES,
    resolve_model_path,
    validate_alias_combo,
)
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS


def test_mappo_default_scenario_aliases_use_generalist():
    for preset_id in ("xiongan_20", "east_dense", "west_dense"):
        scenario = SCENARIO_ALIASES[preset_id]
        assert scenario.scenario_preset_id == preset_id
        assert scenario.model_alias == "mappo_cooperative_20tls_ep160"


def test_mappo_validate_alias_combo_accepts_subset():
    model_alias, path = validate_alias_combo(
        ("demo_3", "demo_5", "demo_6", "demo_9"),
        "mappo_cooperative_20tls_ep160",
    )
    assert model_alias == "mappo_cooperative_20tls_ep160"
    assert path.name == "mappo_cooperative_20tls_ep160.pt"


def test_mappo_validate_alias_combo_rejects_outside_model():
    with pytest.raises(ValueError, match="was not trained on intersections"):
        validate_alias_combo(("demo_1", "demo_99"), "mappo_cooperative_20tls_ep160")


def test_mappo_validate_alias_combo_rejects_unknown_alias():
    with pytest.raises(ValueError, match="Unknown MAPPO model alias"):
        validate_alias_combo(("demo_1",), "does_not_exist")


def test_mappo_resolve_model_path_uses_models_dir():
    path = resolve_model_path("mappo_cooperative_20tls_ep160")
    assert path.name == "mappo_cooperative_20tls_ep160.pt"
    assert path.is_file()


def test_mappo_training_ids_are_canonical_20():
    assert (
        MODEL_ALIASES["mappo_cooperative_20tls_ep160"].training_intersection_ids
        == IDENTITY_SLOT_IDS
    )
