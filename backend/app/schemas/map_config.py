"""地图运行时配置Schema"""

from __future__ import annotations

from pydantic import BaseModel


class MapConfigResponse(BaseModel):
    cesium_ion_token: str | None = None
    tianditu_enabled: bool = False
