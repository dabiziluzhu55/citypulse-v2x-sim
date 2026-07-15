"""地图GeoJSON接口"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ...core.config import get_settings
from ...core.exceptions import AppError
from ...schemas.maps import MapGeoJsonResponse
from ...services.map_service import MapService
from ..deps import get_map_service

router = APIRouter()


@router.get("/maps/{intersection_id}/geojson", response_model=MapGeoJsonResponse)
def get_map_geojson(
    intersection_id: str,
    request: Request,
    radius_m: float = Query(default_factory=lambda: get_settings().default_map_radius_meters, gt=0),
    map_service: MapService = Depends(get_map_service),
) -> MapGeoJsonResponse:
    settings = request.app.state.settings
    if intersection_id not in settings.mvp_intersection_ids:
        raise AppError(
            code="UNSUPPORTED_INTERSECTION",
            message=f"MVP only supports intersection_id={settings.default_intersection_id!r}.",
            status_code=422,
        )
    return map_service.get_geojson(intersection_id, radius_m)
