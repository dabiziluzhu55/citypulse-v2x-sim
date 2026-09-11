"""Catalog, preset, checkpoint, and provenance helpers (no FastAPI)."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from simulation.sumo.building.artifacts import DEFAULT_GENERATED_DIR
from simulation.sumo.building.build_traffic import (
    DEFAULT_TRAFFIC_SCOPE_ID,
    SUPPORTED_TRAFFIC_SCOPE_IDS,
)
from simulation.sumo.engine.session import SimulationCatalog, load_catalog
from traffic_control.cov2x.presets import SCENARIO_PRESET_REGISTRY
from traffic_control.registry import CONTROL_MODE_REGISTRY, require_control_mode

from traffic_llm_runtime.manifest import expand_scope, neighbor_map, tls_phase_orders

from .schema import Provenance


SCOPE_TO_TRAFFIC = {
    "xiongan_20": DEFAULT_TRAFFIC_SCOPE_ID,
    "global": DEFAULT_TRAFFIC_SCOPE_ID,
    "east_dense": "east_dense",
    "west_dense": "west_dense",
}

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def sumo_version() -> str:
    try:
        result = subprocess.run(
            ["sumo", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        line = (result.stdout or result.stderr).splitlines()
        return line[0].strip() if line else "unknown"
    except Exception:
        return os.environ.get("SUMO_HOME", "unknown")


def load_runtime_catalog(generated_dir: Path | None = None) -> SimulationCatalog:
    return load_catalog(Path(generated_dir) if generated_dir else DEFAULT_GENERATED_DIR)


def list_scopes(catalog: SimulationCatalog) -> tuple[str, ...]:
    presets = tuple(sorted(SCENARIO_PRESET_REGISTRY))
    traffic_scopes = tuple(sorted(catalog.scenario_scopes))
    extra = tuple(
        scope_id
        for scope_id in traffic_scopes
        if scope_id not in presets and scope_id != DEFAULT_TRAFFIC_SCOPE_ID
    )
    return presets + extra


def resolve_scope(scope: str) -> tuple[str, str, tuple[str, ...]]:
    """Return (scenario_preset_id, scenario_scope, intersection_ids)."""

    if scope in SCENARIO_PRESET_REGISTRY:
        preset = SCENARIO_PRESET_REGISTRY[scope]
        traffic_scope = SCOPE_TO_TRAFFIC.get(scope, DEFAULT_TRAFFIC_SCOPE_ID)
        return preset.preset_id, traffic_scope, preset.intersection_ids
    if scope in SUPPORTED_TRAFFIC_SCOPE_IDS:
        if scope == DEFAULT_TRAFFIC_SCOPE_ID:
            preset = SCENARIO_PRESET_REGISTRY["xiongan_20"]
            return preset.preset_id, DEFAULT_TRAFFIC_SCOPE_ID, preset.intersection_ids
        if scope in SCENARIO_PRESET_REGISTRY:
            preset = SCENARIO_PRESET_REGISTRY[scope]
            return preset.preset_id, scope, preset.intersection_ids
    raise ValueError(
        f"Unknown scope {scope!r}. Allowed presets={sorted(SCENARIO_PRESET_REGISTRY)}, "
        f"traffic_scopes={list(SUPPORTED_TRAFFIC_SCOPE_IDS)}"
    )


def incoming_lanes(catalog: SimulationCatalog, intersection_id: str) -> list[Any]:
    intersection = catalog.intersections.get(intersection_id)
    if intersection is None:
        return []
    incoming = [lane for lane in intersection.lanes if lane.role in {"incoming", "both"}]
    return incoming or list(intersection.lanes)


def all_intersection_lanes(catalog: SimulationCatalog, intersection_id: str) -> list[Any]:
    intersection = catalog.intersections.get(intersection_id)
    if intersection is None:
        return []
    return list(intersection.lanes)


def probe_control_mode(
    control_mode: str,
    preset_id: str,
    intersection_ids: tuple[str, ...],
) -> tuple[bool, str, str | None, str | None]:
    """Return (ok, reason, model_alias, checkpoint_path). Does not load torch."""

    if control_mode not in CONTROL_MODE_REGISTRY:
        return False, f"unknown control_mode={control_mode!r}", None, None
    spec = require_control_mode(control_mode)
    if not spec.allows_preset(preset_id):
        return False, f"{control_mode} does not support preset {preset_id!r}", None, None
    if control_mode == "fixed":
        return True, "ok", None, None
    if control_mode in {"max_pressure", "sotl"}:
        return True, "ok", None, None
    try:
        if control_mode == "ippo":
            from traffic_control.ippo.aliases import (
                default_model_alias_for,
                validate_alias_combo,
            )

            alias = default_model_alias_for(preset_id)
            alias, path = validate_alias_combo(intersection_ids, alias)
            if not path.is_file():
                return False, f"IPPO checkpoint missing: {path}", alias, str(path)
            return True, "ok", alias, str(path)
        if control_mode == "mappo":
            from traffic_control.mappo.aliases import (
                default_model_alias_for,
                validate_alias_combo,
            )

            alias = default_model_alias_for(preset_id)
            alias, path = validate_alias_combo(intersection_ids, alias)
            if not path.is_file():
                return False, f"MAPPO checkpoint missing: {path}", alias, str(path)
            return True, "ok", alias, str(path)
        if control_mode == "cov2x":
            from traffic_control.cov2x.aliases import (
                default_model_alias_for,
                validate_alias_combo,
            )

            alias = default_model_alias_for(preset_id)
            alias, path = validate_alias_combo(intersection_ids, alias)
            if not path.is_file():
                return False, f"CoV2X checkpoint missing: {path}", alias, str(path)
            return True, "ok", alias, str(path)
    except Exception as exc:
        return False, str(exc), None, None
    return True, "ok", None, None


def apply_model_alias_env(control_mode: str, model_alias: str | None) -> None:
    if not model_alias:
        return
    if control_mode == "ippo":
        os.environ["IPPO_MODEL_ALIAS"] = model_alias
    elif control_mode == "mappo":
        os.environ["MAPPO_MODEL_ALIAS"] = model_alias
    elif control_mode == "cov2x":
        os.environ["COV2X_MODEL_ALIAS"] = model_alias


def make_provenance(
    *,
    control_mode: str,
    dataset_version: str,
    scoring_version: str,
    step_length: float,
    decision_interval: float,
    model_alias: str | None = None,
    checkpoint_path: str | None = None,
) -> Provenance:
    return Provenance(
        git_commit=git_commit(),
        dataset_version=dataset_version,
        scoring_version=scoring_version,
        timestamp=iso_now(),
        sumo_version=sumo_version(),
        step_length=float(step_length),
        decision_interval=float(decision_interval),
        control_mode=control_mode,
        model_alias=model_alias,
        checkpoint_path=checkpoint_path,
    )
