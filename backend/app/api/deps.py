"""应用依赖"""

from __future__ import annotations

from fastapi import Request

from ..core.exceptions import ArtifactsNotReadyError, SumoHomeUnavailableError
from ..services.map_service import MapService
from ..services.simulation_service import SimulationService


def require_artifacts_ready(request: Request) -> None:
    if not request.app.state.artifacts_ready:
        raise ArtifactsNotReadyError(request.app.state.missing_files)


def require_sumo_home(request: Request) -> None:
    if not request.app.state.sumo_home_configured:
        raise SumoHomeUnavailableError()


def get_simulation_service(request: Request) -> SimulationService:
    require_artifacts_ready(request)
    require_sumo_home(request)
    return request.app.state.simulation_service


def get_map_service(request: Request) -> MapService:
    require_artifacts_ready(request)
    require_sumo_home(request)
    return request.app.state.map_service
