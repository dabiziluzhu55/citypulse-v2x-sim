"""Static deployment boundary checks."""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

FORBIDDEN_IMPORTS = {
    "backend/app": ("algorithms",),
    "traffic_control": ("algorithms", "backend"),
    "traffic_eval": ("backend", "algorithms"),
    "traffic_intelligence": ("algorithms", "backend"),
}

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


def test_backend_does_not_import_algorithms() -> None:
    assert _scan_package("backend/app", ("algorithms",)) == []


def test_traffic_control_does_not_import_algorithms_or_backend() -> None:
    assert _scan_package("traffic_control", ("algorithms", "backend")) == []


def test_traffic_eval_does_not_import_backend_or_algorithms() -> None:
    assert _scan_package("traffic_eval", ("backend", "algorithms")) == []


def test_traffic_intelligence_does_not_import_algorithms_or_backend() -> None:
    assert _scan_package("traffic_intelligence", ("algorithms", "backend")) == []


def test_registry_algorithm_modules_are_traffic_control() -> None:
    from traffic_control.registry import CONTROL_MODE_REGISTRY

    for name, spec in CONTROL_MODE_REGISTRY.items():
        if not spec.needs_algorithm:
            continue
        assert spec.algorithm_transport == "local", name
        assert spec.algorithm_module.startswith("traffic_control."), (
            f"{name} -> {spec.algorithm_module}"
        )


def test_backend_redis_import_without_sumo_home(monkeypatch) -> None:
    monkeypatch.delenv("SUMO_HOME", raising=False)
    monkeypatch.setenv("SIMULATION_MANAGER_MODE", "redis")
    import importlib
    import backend.app.main as main_module

    importlib.reload(main_module)
    assert main_module.create_app is not None
