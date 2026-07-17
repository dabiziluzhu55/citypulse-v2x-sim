"""地图运行时配置接口"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ...core.config import Settings, get_settings
from ...schemas.map_config import MapConfigResponse

router = APIRouter()


@router.get("/config/map", response_model=MapConfigResponse)
def get_map_config(settings: Settings = Depends(get_settings)) -> MapConfigResponse:
    cesium_token = (settings.cesium_ion_token or "").strip() or None
    tianditu_token = (settings.tianditu_token or "").strip()
    return MapConfigResponse(
        cesium_ion_token=cesium_token,
        tianditu_enabled=bool(tianditu_token),
    )
