"""应用依赖注入"""

from __future__ import annotations

from fastapi import Request

from ..core.exceptions import (
    ArtifactsNotReadyError,
    RedisUnavailableAppError,
    SimulationManagerNotReadyError,
    SumoHomeUnavailableError,
)
from ..services.map_service import MapService
from ..services.scenario_export_service import ScenarioExportService
from ..services.simulation_service import SimulationService


def require_artifacts_ready(request: Request) -> None:
    if not request.app.state.artifacts_ready:
        raise ArtifactsNotReadyError(request.app.state.missing_files)


def require_sumo_home(request: Request) -> None:
    if not request.app.state.sumo_home_configured:
        raise SumoHomeUnavailableError()


def require_simulation_manager_ready(request: Request) -> None:
    mode = getattr(request.app.state, "simulation_manager_mode", "local")
    if mode == "redis" and not getattr(request.app.state, "redis_ready", False):
        raise RedisUnavailableAppError(
            getattr(request.app.state, "redis_error", None)
            or "Redis session store is unavailable."
        )
    if not getattr(request.app.state, "simulation_manager_ready", False):
        raise SimulationManagerNotReadyError(
            getattr(request.app.state, "redis_error", None)
            or "Simulation manager is not ready."
        )
    if request.app.state.simulation_service is None:
        raise SimulationManagerNotReadyError("Simulation service is not initialized.")


def get_simulation_service(request: Request) -> SimulationService:
    require_artifacts_ready(request)
    mode = getattr(request.app.state, "simulation_manager_mode", "local")
    # redis 模式由 SUMO worker 运行仿真，API 容器不强制要求本机 SUMO_HOME
    if mode == "local":
        require_sumo_home(request)
    require_simulation_manager_ready(request)
    return request.app.state.simulation_service


def get_map_service(request: Request) -> MapService:
    require_artifacts_ready(request)
    require_sumo_home(request)
    return request.app.state.map_service


def get_scenario_export_service(request: Request) -> ScenarioExportService:
    require_artifacts_ready(request)
    return request.app.state.scenario_export_service
