"""Deployment contract tests for the frozen update-24 CoV2X candidate."""

from __future__ import annotations

import os

import pytest

torch = pytest.importorskip("torch")

from traffic_control.cov2x.aliases import (
    DEFAULT_MODEL_ALIAS,
    default_model_alias_for,
    resolve_model,
    resolve_model_path,
)
from traffic_control.cov2x.contract import (
    TEMPORARY_CAP_CHECKPOINT_SHA256,
    load_temporary_cap_contract,
)
from traffic_control.cov2x.test_joint_deploy import _metadata, _payload


MODEL_ALIAS = "cov2x_g30_temp_cap_u24"


def test_update24_alias_is_default_and_exact() -> None:
    assert DEFAULT_MODEL_ALIAS == MODEL_ALIAS
    for scenario in ("xiongan_20", "east_dense", "west_dense"):
        assert default_model_alias_for(scenario) == MODEL_ALIAS

    model = resolve_model(MODEL_ALIAS)
    path = resolve_model_path(MODEL_ALIAS)
    assert path.name == "cov2x_g30_temporary_cap_u24.pt"
    assert model.adapter_module.endswith(".temporary_cap_u24")

    checkpoint = torch.load(path, map_location="cpu", weights_only=False)
    version, view = load_temporary_cap_contract(
        path,
        checkpoint,
        manifest_path=model.manifest_path,
    )
    assert version == 3
    assert view["sha256"] == TEMPORARY_CAP_CHECKPOINT_SHA256
    assert view["format_version"] == 9
    assert view["policy_generation"] == 24
    assert view["vehicle_action_semantics"] == (
        "temporary_base_relative_speed_cap_v1"
    )
    assert view["actor_update_schedule_id"] == (
        "temporary_base_relative_three_scope_latin_gain_1_3_2_3_1_x22_v1"
    )


def test_package_dispatches_update24_through_protocol_2(monkeypatch) -> None:
    monkeypatch.setenv("COV2X_MODEL_ALIAS", MODEL_ALIAS)
    monkeypatch.setenv("COV2X_MODE", "eval")
    monkeypatch.delenv("COV2X_MODEL_PATH", raising=False)
    import traffic_control.cov2x as cov2x_pkg

    response = cov2x_pkg.initialize(_metadata())
    assert response["ready"] is True
    assert response["candidate_id"] == "cov2x_temporary_speed_cap_final_v1"
    assert response["deployment_model_alias"] == MODEL_ALIAS
    assert response["policy_generation"] == 24

    decision = cov2x_pkg.step(_payload(0, 0.0))
    assert decision["protocol_version"] == "2.0"
    assert decision["candidate_id"] == "cov2x_temporary_speed_cap_final_v1"
    assert set(decision["actions"]) == {"signals", "vehicles"}

    cov2x_pkg.finish(
        {
            "protocol_version": "2.0",
            "episode_id": "deploy-test",
            "simulation_time": 5.0,
            "vehicles": {},
            "intersections": _metadata()["intersections"],
        }
    )
    assert "COV2X_MODEL_PATH" not in os.environ


def test_update24_adapter_rejects_training_mode(monkeypatch) -> None:
    monkeypatch.setenv("COV2X_MODEL_ALIAS", MODEL_ALIAS)
    monkeypatch.setenv("COV2X_MODE", "train")
    import traffic_control.cov2x as cov2x_pkg

    with pytest.raises(ValueError, match="eval-only"):
        cov2x_pkg.initialize(_metadata())
