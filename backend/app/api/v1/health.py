"""健康检查接口"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    settings = request.app.state.settings
    artifacts_ready = request.app.state.artifacts_ready
    sumo_home_configured = request.app.state.sumo_home_configured
    status = "ok" if artifacts_ready and sumo_home_configured else "degraded"
    payload = {
        "status": status,
        "app": settings.app_name,
        "sumo_home_configured": sumo_home_configured,
        "generated_artifacts_ready": artifacts_ready,
        "simulation_manager_ready": request.app.state.simulation_manager_ready,
    }
    if not artifacts_ready:
        payload["missing_files"] = request.app.state.missing_files
    return payload
