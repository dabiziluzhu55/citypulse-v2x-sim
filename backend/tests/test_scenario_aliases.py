"""Scenario/model alias resolution tests."""

from __future__ import annotations

import pytest

from traffic_control.ippo.aliases import (
    SCENARIO_ALIASES,
    resolve_model_path,
    validate_alias_combo,
)
from traffic_control.ippo.identity import IDENTITY_SLOT_IDS


def test_default_scenario_alias_uses_generalist():
    east = SCENARIO_ALIASES["east_dense"]
    assert east.scenario_preset_id == "east_dense"
    assert east.model_alias == "ippo_v8_20tls_ep160"


def test_validate_alias_combo_accepts_subset():
    model_alias, path = validate_alias_combo(
        ("demo_3", "demo_5", "demo_6", "demo_9"), "ippo_v8_20tls_ep160"
    )
    assert model_alias == "ippo_v8_20tls_ep160"
    assert path.name == "ippo_v8_20tls_ep160.pt"


def test_validate_alias_combo_rejects_outside_model():
    with pytest.raises(ValueError, match="was not trained on intersections"):
        validate_alias_combo(("demo_1", "demo_99"), "ippo_v8_20tls_ep160")


def test_validate_alias_combo_rejects_unknown_alias():
    with pytest.raises(ValueError, match="Unknown IPPO model alias"):
        validate_alias_combo(("demo_1",), "does_not_exist")


def test_generalist_training_ids_are_canonical_20():
    from traffic_control.ippo.aliases import MODEL_ALIASES

    assert MODEL_ALIASES["ippo_v8_20tls_ep160"].training_intersection_ids == IDENTITY_SLOT_IDS
