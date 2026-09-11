"""Static deployment boundary checks."""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[2]

STDLIB = {"typing", "typing_extensions", "__future__", "dataclasses", "enum", "abc"}


def _imports_in_file(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, alias.name.split(".")[0]))
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or not node.module:
                continue
            found.append((node.lineno, node.module.split(".")[0]))
    return found


def _scan_package(rel: str, forbidden_roots: tuple[str, ...]) -> list[str]:
    base = REPO / rel
    if not base.exists():
        return []
    violations: list[str] = []
    for path in base.rglob("*.py"):
        if "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        for lineno, root in _imports_in_file(path):
            if root in STDLIB:
                continue
            if root in forbidden_roots:
                violations.append(
                    f"{path.relative_to(REPO)}:{lineno} imports {root}"
                )
    return violations


def _scan_forbidden_modules(rel: str, forbidden_modules: tuple[str, ...]) -> list[str]:
    base = REPO / rel
    violations: list[str] = []
    for path in base.rglob("*.py"):
        if "/tests/" in str(path) or path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for module in forbidden_modules:
            if f"import {module}" in text or f"from {module}" in text:
                violations.append(f"{path.relative_to(REPO)} references {module}")
    return violations


def test_backend_does_not_import_algorithms() -> None:
    assert _scan_package("backend/app", ("algorithms",)) == []


def test_backend_does_not_import_simulation_sumo_engine_session() -> None:
    violations = _scan_package("backend/app", ("simulation",))
    session_violations = [
        item
        for item in violations
        if "simulation.sumo.engine.session" in item
        or item.endswith(" imports simulation")
        and "manager_factory" not in item
        and "scenario_export" not in item
    ]
    # local-only lazy imports in manager_factory / scenario_export are allowed.
    allowed = {
        "backend/app/services/manager_factory.py",
        "backend/app/services/scenario_export_service.py",
    }
    filtered = [
        item
        for item in violations
        if not any(allowed_path in item for allowed_path in allowed)
    ]
    assert filtered == []


def test_backend_does_not_import_traffic_control_checkpoints() -> None:
    forbidden = (
        "traffic_control.ippo",
        "traffic_control.mappo",
        "traffic_control.cov2x",
    )
    violations: list[str] = []
    for path in (REPO / "backend/app").rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        for lineno, root in _imports_in_file(path):
            module = path.read_text(encoding="utf-8")
            # AST root is only first segment; inspect import lines directly.
            _ = module
            if root == "traffic_control":
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        if node.module.startswith(forbidden):
                            violations.append(
                                f"{path.relative_to(REPO)}:{node.lineno} imports {node.module}"
                            )
    assert violations == []


def test_traffic_control_does_not_import_algorithms_or_backend() -> None:
    assert _scan_package("traffic_control", ("algorithms", "backend")) == []


def test_traffic_eval_does_not_import_backend_or_algorithms() -> None:
    assert _scan_package("traffic_eval", ("backend", "algorithms")) == []


def test_traffic_intelligence_does_not_import_algorithms_or_backend() -> None:
    assert _scan_package("traffic_intelligence", ("algorithms", "backend")) == []


def test_simulation_does_not_import_backend() -> None:
    assert _scan_package("simulation", ("backend",)) == []


def test_registry_algorithm_modules_are_traffic_control() -> None:
    from traffic_control.registry import CONTROL_MODE_REGISTRY

    for name, spec in CONTROL_MODE_REGISTRY.items():
        if not spec.needs_algorithm:
            continue
        assert spec.algorithm_transport == "local", name
        assert spec.algorithm_module.startswith("traffic_control."), (
            f"{name} -> {spec.algorithm_module}"
        )


def test_backend_redis_lifespan_without_sumo_imports(monkeypatch) -> None:
    blocked = {"sumolib", "libsumo", "traci"}
    original_import = builtins.__import__ if (builtins := sys.modules.get("builtins")) else __import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = name.split(".", 1)[0]
        if root in blocked:
            raise ImportError(f"blocked import {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.delenv("SUMO_HOME", raising=False)
    monkeypatch.setenv("SIMULATION_MANAGER_MODE", "redis")
    monkeypatch.setenv("CITYPULSE_REDIS_STATE_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setitem(sys.modules, "builtins", sys.modules["builtins"])
    monkeypatch.setattr("builtins.__import__", guarded_import)

    fake_redis = MagicMock()
    fake_redis.ping.return_value = True
    fake_store = MagicMock()
    fake_store.ping.return_value = True

    monkeypatch.setattr(
        "simulation_protocol.store.RedisSessionStore",
        lambda *args, **kwargs: fake_store,
    )
    monkeypatch.setattr(
        "simulation_protocol.celery_client.get_celery_client",
        lambda: MagicMock(),
    )

    import backend.app.main as main_module

    importlib.reload(main_module)
    app = main_module.create_app()
    assert app is not None

    from simulation_protocol.client import RedisSimulationClient

    client = RedisSimulationClient(
        redis_url="redis://127.0.0.1:6379/0",
        store=fake_store,
        celery_app=MagicMock(),
    )
    assert client.catalog() is not None
