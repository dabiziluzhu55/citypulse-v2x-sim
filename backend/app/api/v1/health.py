"""健康检查接口"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    settings = request.app.state.settings
    mode = getattr(request.app.state, "simulation_manager_mode", settings.normalized_manager_mode())
    artifacts_ready = request.app.state.artifacts_ready
    sumo_home_configured = request.app.state.sumo_home_configured
    manager_ready = bool(getattr(request.app.state, "simulation_manager_ready", False))
    redis_ready = bool(getattr(request.app.state, "redis_ready", mode != "redis"))
    session_root_ready = bool(getattr(request.app.state, "session_root_ready", True))

    if mode == "redis":
        # redis 模式：不把缺失 SUMO_HOME 当成不可用；Redis/artifacts/session_root 才是关键
        healthy = artifacts_ready and redis_ready and manager_ready and session_root_ready
    else:
        healthy = artifacts_ready and sumo_home_configured and manager_ready

    status = "ok" if healthy else "degraded"
    payload = {
        "status": status,
        "app": settings.app_name,
        "simulation_manager_mode": mode,
        "sumo_home_configured": sumo_home_configured,
        "generated_artifacts_ready": artifacts_ready,
        "session_root": str(settings.session_root),
        "session_root_ready": session_root_ready,
        "simulation_manager_ready": manager_ready,
        "redis_ready": redis_ready,
        "algorithm_base_url": settings.algorithm_base_url,
        "algorithm_state_shared": bool(
            getattr(request.app.state, "algorithm_state_shared", False)
        ),
        "recommended_uvicorn_workers": getattr(
            request.app.state, "recommended_uvicorn_workers", 1
        ),
        "detected_uvicorn_workers": getattr(
            request.app.state, "detected_uvicorn_workers", None
        ),
    }
    if not artifacts_ready:
        payload["missing_files"] = request.app.state.missing_files
    if mode == "redis" and not redis_ready:
        payload["redis_error"] = getattr(request.app.state, "redis_error", None)
    if mode == "redis":
        payload["redis_state_url"] = settings.citypulse_redis_state_url
        payload["redis_key_prefix"] = settings.citypulse_redis_key_prefix
        payload["backend_redis_key_prefix"] = settings.backend_redis_key_prefix
    return payload
