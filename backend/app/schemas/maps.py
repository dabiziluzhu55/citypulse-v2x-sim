"""地图GeoJSON响应Schema"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CenterSchema(BaseModel):
    longitude: float
    latitude: float


class BoundsSchema(BaseModel):
    west: float
    south: float
    east: float
    north: float


class MapGeoJsonResponse(BaseModel):
    intersection_id: str
    center: CenterSchema
    radius_m: float
    bounds: BoundsSchema
    geojson: dict[str, Any]
