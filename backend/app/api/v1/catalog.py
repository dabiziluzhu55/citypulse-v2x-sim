"""仿真目录接口"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...schemas.catalog import CatalogResponse
from ...services.simulation_service import SimulationService
from ..deps import get_simulation_service

router = APIRouter()


@router.get("/catalog", response_model=CatalogResponse)
def get_catalog(service: SimulationService = Depends(get_simulation_service)) -> CatalogResponse:
    return service.get_catalog_response()
