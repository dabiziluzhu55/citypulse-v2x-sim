"""Protocol 2.0 adapters for the paired MaxPressure benchmark.

This package is intentionally isolated from the production algorithms.  It
loads the historical density-pressure controller from the old IPPO workspace
or the senior team's movement-pressure controller from a pinned Git commit,
then attaches the shared six-metric evaluation runtime.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

from algorithms.evaluation import runtime as evaluation_runtime


VARIANTS = frozenset({"ours", "senior"})
DEFAULT_LEGACY_ROOT = Path(
    "/home/kemove/devdata1/gsb/citypulse-v2x-sim"
)
DEFAULT_SENIOR_REF = "2df19c3ac3bda831d1dbec3a5c2f50f216f4b652"
SENIOR_SOURCE_PATH = "backend/app/controllers/max_pressure.py"

_variant = ""
_episode_id = ""
_legacy_controller: Any = None
_senior_controller: Any = None


def _legacy_root() -> Path:
    return Path(
        os.environ.get("MAXPRESSURE_LEGACY_ROOT", str(DEFAULT_LEGACY_ROOT))
    ).expanduser().resolve()


def _current_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_legacy_controller() -> Any:
    """Load the exact historical CoSLight controller under a private package."""

    package_name = "_maxpressure_benchmark_legacy_coslight"
    module_name = f"{package_name}.controller"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached

    package_dir = _legacy_root() / "algorithms" / "coslight"
    controller_path = package_dir / "controller.py"
    lane_state_path = package_dir / "lane_state.py"
    if not controller_path.is_file() or not lane_state_path.is_file():
        raise FileNotFoundError(
            "historical CoSLight controller or lane_state.py is missing under "
            f"{package_dir}"
        )

    package = types.ModuleType(package_name)
    package.__path__ = [str(package_dir)]
    package.__package__ = package_name
    sys.modules[package_name] = package

    spec = importlib.util.spec_from_file_location(module_name, controller_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load historical controller from {controller_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_senior_module() -> Any:
    """Compile the senior controller exactly as stored in the pinned Git ref."""

    ref = os.environ.get("MAXPRESSURE_SENIOR_REF", DEFAULT_SENIOR_REF)
    module_name = f"_maxpressure_benchmark_senior_{ref[:12]}"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached

    repo_root = _current_repo_root()
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "show",
            f"{ref}:{SENIOR_SOURCE_PATH}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    module = types.ModuleType(module_name)
    module.__file__ = f"git:{ref}:{SENIOR_SOURCE_PATH}"
    sys.modules[module_name] = module
    exec(compile(completed.stdout, module.__file__, "exec"), module.__dict__)
    if not hasattr(module, "MaxPressureController"):
        raise ImportError(
            f"{ref}:{SENIOR_SOURCE_PATH} has no MaxPressureController"
        )
    return module


def initialize(payload: dict[str, Any]) -> dict[str, Any]:
    global _variant, _episode_id, _legacy_controller, _senior_controller

    _variant = os.environ.get("MAXPRESSURE_BENCHMARK_VARIANT", "").strip().lower()
    if _variant not in VARIANTS:
        raise ValueError(
            "MAXPRESSURE_BENCHMARK_VARIANT must be one of "
            f"{sorted(VARIANTS)}, got {_variant!r}"
        )
    _episode_id = str(payload.get("episode_id", ""))
    evaluation_runtime.start(f"MaxPressure-{_variant}", payload)

    if _variant == "ours":
        os.environ["COSLIGHT_MODE"] = "max_pressure"
        os.environ["COSLIGHT_TOP_K"] = "5"
        os.environ["COSLIGHT_REWARD_MODE"] = "pressure"
        os.environ["COSLIGHT_MAX_GREEN_FACTOR"] = "2.0"
        os.environ["COSLIGHT_VEHICLE_GUIDANCE"] = "off"
        _legacy_controller = _load_legacy_controller()
        _senior_controller = None
        return _legacy_controller.initialize(payload)

    module = _load_senior_module()
    _senior_controller = module.MaxPressureController(payload)
    _legacy_controller = None
    return {
        "protocol_version": "2.0",
        "episode_id": _episode_id,
        "ready": True,
    }


def step(payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    if _variant == "ours":
        if _legacy_controller is None:
            raise RuntimeError("historical MaxPressure is not initialized")
        response = _legacy_controller.step(payload)
    elif _variant == "senior":
        if _senior_controller is None:
            raise RuntimeError("senior MaxPressure is not initialized")
        actions = _senior_controller.compute_actions(payload)
        response = {
            "protocol_version": "2.0",
            "episode_id": str(payload.get("episode_id", "")),
            "step_id": payload.get("step_id", 0),
            "actions": {
                "signals": {
                    str(intersection_id): {"target_phase": int(phase_id)}
                    for intersection_id, phase_id in actions.items()
                    if phase_id is not None
                },
                "vehicles": {},
            },
        }
    else:
        raise RuntimeError("MaxPressure benchmark adapter is not initialized")

    evaluation_runtime.record_latency(
        (time.perf_counter() - started) * 1000.0,
        episode_id=str(payload.get("episode_id", "")),
    )
    evaluation_runtime.observe_decision(payload)
    return response


def finish(summary: dict[str, Any]) -> None:
    if _variant == "ours" and _legacy_controller is not None:
        _legacy_controller.finish(summary)
    evaluation_runtime.finish(summary)


def evaluation_result() -> dict[str, Any] | None:
    result = evaluation_runtime.last_result(_episode_id)
    return None if result is None else result.to_dict()

