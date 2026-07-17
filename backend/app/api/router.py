"""聚合API路由：对外统一 /api/v1；算法协议挂在 internal 子路径。"""

from __future__ import annotations

from fastapi import APIRouter

from .v1 import catalog, config, health, internal_algorithm, maps, simulations, tiles

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(config.router, tags=["config"])
api_router.include_router(catalog.router, tags=["catalog"])
api_router.include_router(maps.router, tags=["maps"])
api_router.include_router(tiles.router, tags=["tiles"])
api_router.include_router(simulations.router, tags=["simulations"])
api_router.include_router(internal_algorithm.router, tags=["internal-algorithm"])
