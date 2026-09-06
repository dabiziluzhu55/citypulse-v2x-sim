"""聚合API路由：对外统一 /api/v1；算法协议挂在internal子路径"""

from __future__ import annotations

from fastapi import APIRouter

from .v1 import (
    catalog,
    config,
    copilot,
    evaluation_reports,
    health,
    internal_algorithm,
    maps,
    scenarios,
    simulations,
    tiles,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(config.router, tags=["config"])
api_router.include_router(catalog.router, tags=["catalog"])
api_router.include_router(maps.router, tags=["maps"])
api_router.include_router(tiles.router, tags=["tiles"])
api_router.include_router(simulations.router, tags=["simulations"])
api_router.include_router(evaluation_reports.router, tags=["evaluation-reports"])
api_router.include_router(copilot.router, tags=["copilot"])
api_router.include_router(scenarios.router, tags=["scenarios"])
api_router.include_router(internal_algorithm.router, tags=["internal-algorithm"])
