"""Self-contained deployment tests for the frozen update-24 candidate."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from traffic_control.cov2x.aliases import resolve_model
from traffic_control.cov2x.candidates import temporary_cap_u24
from traffic_control.cov2x.communication import V2XEventDrain
from traffic_control.cov2x.test_joint_deploy import _metadata, _payload


COV2X_ROOT = Path(__file__).resolve().parent
TRAFFIC_CONTROL_ROOT = COV2X_ROOT.parent


def test_cov2x_runtime_has_no_algorithms_imports() -> None:
    violations = []
    for path in COV2X_ROOT.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == "algorithms" or name.startswith("algorithms."):
                    violations.append(f"{path.relative_to(COV2X_ROOT)}:{node.lineno}")
    assert violations == []


def test_step_exports_backend_batch_and_optional_sink(monkeypatch) -> None:
    monkeypatch.setenv("COV2X_MODEL_ALIAS", "cov2x_g30_temp_cap_u24")
    monkeypatch.setenv("COV2X_MODE", "eval")

    import traffic_control.cov2x as cov2x

    sink = V2XEventDrain()
    cov2x.set_v2x_event_sink(sink)
    try:
        initialized = cov2x.initialize(_metadata())
        assert initialized["v2x_event_export"] == {
            "schema": "cov2x.v2x.event_batch",
            "schema_version": "1.0",
            "inline_step_field": "v2x",
            "drain_api": "traffic_control.cov2x.drain_v2x_events",
        }

        decision = cov2x.step(_payload(0, 0.0))
        batch = decision["v2x"]
        assert batch["event_count"] > 0
        assert {row["event"] for row in batch["events"]} == {
            "SEND",
            "DELIVER",
            "CONSUME",
        }
        routes = {
            (
                row["message_type"],
                row["source_role"],
                row["destination_role"],
            )
            for row in batch["events"]
        }
        assert ("VehicleStateV1", "vehicle", "cloud") in routes
        assert ("IntersectionSummaryV1", "road", "cloud") in routes
        assert ("RegionalPriorityV1", "cloud", "road") in routes
        assert ("RegionalPriorityV1", "cloud", "vehicle") in routes
        assert ("SPaTV2", "road", "vehicle") in routes
        assert ("MAPV1", "road", "vehicle") in routes
        json.dumps(batch, allow_nan=False)

        drained = cov2x.drain_v2x_events()
        assert drained["event_count"] == batch["event_count"]
        assert cov2x.drain_v2x_events()["event_count"] == 0
        assert len(sink.snapshot()) == batch["event_count"]
    finally:
        cov2x.finish(
            {
                "protocol_version": "2.0",
                "episode_id": "deploy-test",
                "simulation_time": 5.0,
                "vehicles": {},
                "intersections": _metadata()["intersections"],
            }
        )
        cov2x.set_v2x_event_sink(None)


def test_deployment_runtime_matches_frozen_training_runtime() -> None:
    environment_before = {
        name: value
        for name, value in os.environ.items()
        if name.startswith("COV2X_")
    }

    from algorithms.cov2x import mvp_runtime as training_runtime
    from traffic_control.cov2x.runtime import mvp_runtime as deploy_runtime

    finish_payload = {
        "protocol_version": "2.0",
        "episode_id": "deploy-test",
        "simulation_time": 5.0,
        "vehicles": {},
        "intersections": _metadata()["intersections"],
    }

    try:
        os.environ["COV2X_MODE"] = "eval"
        temporary_cap_u24.configure(
            resolve_model("cov2x_g30_temp_cap_u24")
        )

        training_runtime.reset_untrained_state()
        training_init = training_runtime.initialize(_metadata())
        training_step = training_runtime.step(_payload(0, 0.0))
        training_runtime.finish(finish_payload)

        deploy_runtime.reset_untrained_state()
        deploy_init = deploy_runtime.initialize(_metadata())
        deploy_step = deploy_runtime.step(_payload(0, 0.0))
        deploy_runtime.finish(finish_payload)

        assert deploy_init["candidate_id"] == training_init["candidate_id"]
        assert (
            deploy_init["policy_generation"]
            == training_init["policy_generation"]
        )
        assert deploy_step["actions"] == training_step["actions"]
    finally:
        training_runtime.reset_untrained_state()
        deploy_runtime.reset_untrained_state()
        temporary_cap_u24.reset()
        for name in tuple(os.environ):
            if name.startswith("COV2X_"):
                os.environ.pop(name, None)
        os.environ.update(environment_before)


def test_copied_traffic_control_package_runs_without_algorithms(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "traffic_control"
    shutil.copytree(
        TRAFFIC_CONTROL_ROOT,
        package_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    script = """
import importlib.util
import os

assert importlib.util.find_spec("algorithms") is None
os.environ["COV2X_MODEL_ALIAS"] = "cov2x_g30_temp_cap_u24"
os.environ["COV2X_MODE"] = "eval"

import traffic_control.cov2x as cov2x
from traffic_control.cov2x.test_joint_deploy import _metadata, _payload

response = cov2x.initialize(_metadata())
assert response["ready"] is True
decision = cov2x.step(_payload(0, 0.0))
assert decision["v2x"]["event_count"] > 0
cov2x.finish({
    "protocol_version": "2.0",
    "episode_id": "deploy-test",
    "simulation_time": 5.0,
    "vehicles": {},
    "intersections": _metadata()["intersections"],
})
print("SELF_CONTAINED_PASS")
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(tmp_path)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "SELF_CONTAINED_PASS" in completed.stdout
