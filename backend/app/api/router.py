"""聚合API路由"""

from __future__ import annotations

from fastapi import APIRouter

from .v1 import catalog, health, maps, simulations

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(catalog.router, tags=["catalog"])
api_router.include_router(maps.router, tags=["maps"])
api_router.include_router(simulations.router, tags=["simulations"])
