"""基于sumolib的GeoJSON生成与坐标转换"""

from __future__ import annotations

import logging
import math
from typing import Any

from simulation.sumo.session import SimulationCatalog, SimulationManager

from ..core.config import Settings
from ..core.exceptions import AppError
from ..core.sumo_env import import_sumolib
from ..schemas.maps import BoundsSchema, CenterSchema, MapGeoJsonResponse

logger = logging.getLogger(__name__)


class MapService:
    def __init__(self, settings: Settings, manager: SimulationManager) -> None:
        self._settings = settings
        self._manager = manager
        self._net = None
        self._cache: dict[tuple[str, float], MapGeoJsonResponse] = {}

    def xy_to_lonlat(self, x: float, y: float) -> tuple[float | None, float | None]:
        try:
            net = self._load_net()
            lon, lat = net.convertXY2LonLat(float(x), float(y))
            return round(float(lon), 7), round(float(lat), 7)
        except Exception:
            return None, None

    def get_geojson(self, intersection_id: str, radius_m: float) -> MapGeoJsonResponse:
        cache_key = (intersection_id, float(radius_m))
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.info(
                "GeoJSON cache hit for intersection=%s radius_m=%s",
                intersection_id,
                radius_m,
            )
            return cached

        catalog = self._manager.catalog()
        intersection = catalog.intersections.get(intersection_id)
        if intersection is None:
            raise AppError(
                code="UNKNOWN_INTERSECTION",
                message=f"Unknown intersection: {intersection_id}",
                status_code=404,
            )
        if intersection.longitude is None or intersection.latitude is None:
            raise AppError(
                code="INTERSECTION_COORDINATES_MISSING",
                message=f"Intersection {intersection_id} has no coordinates in catalog.",
                status_code=422,
            )

        center_lon = float(intersection.longitude)
        center_lat = float(intersection.latitude)
        net = self._load_net()
        center_x, center_y = net.convertLonLat2XY(center_lon, center_lat)

        features: list[dict[str, Any]] = []
        bounds = BoundsSchema(
            west=center_lon,
            south=center_lat,
            east=center_lon,
            north=center_lat,
        )

        for edge in net.getEdges():
            edge_id = edge.getID()
            if edge_id.startswith(":"):
                continue
            if not self._edge_within_radius(edge, center_x, center_y, radius_m):
                continue

            coordinates: list[list[float]] = []
            for point_x, point_y in edge.getShape():
                lon, lat = net.convertXY2LonLat(point_x, point_y)
                lon = round(float(lon), 7)
                lat = round(float(lat), 7)
                coordinates.append([lon, lat])
                bounds.west = min(bounds.west, lon)
                bounds.south = min(bounds.south, lat)
                bounds.east = max(bounds.east, lon)
                bounds.north = max(bounds.north, lat)

            if not coordinates:
                continue

            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "edge_id": edge_id,
                        "lane_count": len(edge.getLanes()),
                        "speed": round(float(edge.getSpeed()), 3),
                        "priority": int(edge.getPriority()),
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coordinates,
                    },
                }
            )

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "feature_type": "intersection",
                    "intersection_id": intersection_id,
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(center_lon, 7), round(center_lat, 7)],
                },
            }
        )

        response = MapGeoJsonResponse(
            intersection_id=intersection_id,
            center=CenterSchema(longitude=round(center_lon, 7), latitude=round(center_lat, 7)),
            radius_m=float(radius_m),
            bounds=bounds,
            geojson={"type": "FeatureCollection", "features": features},
        )
        self._cache[cache_key] = response
        return response

    def _load_net(self):
        if self._net is None:
            sumolib = import_sumolib()
            net_path = self._settings.signals_net_path
            if not net_path.is_file():
                raise AppError(
                    code="ARTIFACTS_NOT_READY",
                    message=f"Generated network file is missing: {net_path.name}",
                    status_code=503,
                )
            logger.info("Loading SUMO network: %s", net_path)
            self._net = sumolib.net.readNet(str(net_path))
        return self._net

    @staticmethod
    def _edge_within_radius(edge, center_x: float, center_y: float, radius_m: float) -> bool:
        for point_x, point_y in edge.getShape():
            distance = math.hypot(point_x - center_x, point_y - center_y)
            if distance <= radius_m:
                return True
        return False

    @staticmethod
    def serialize_catalog(catalog: SimulationCatalog, allowed_intersections: tuple[str, ...]):
        from ..schemas.catalog import (
            CatalogResponse,
            FlowMultiplierRangeSchema,
            IntersectionSchema,
            LaneSchema,
            OriginSchema,
        )

        intersections = []
        for intersection_id in allowed_intersections:
            item = catalog.intersections.get(intersection_id)
            if item is None:
                continue
            intersections.append(
                IntersectionSchema(
                    intersection_id=item.intersection_id,
                    longitude=item.longitude,
                    latitude=item.latitude,
                    periods=list(item.periods),
                    origins=[
                        OriginSchema(
                            origin_id=origin.origin_id,
                            label=origin.label,
                            lane_ids=list(origin.lane_ids),
                        )
                        for origin in item.origins
                    ],
                    lanes=[
                        LaneSchema(
                            lane_id=lane.lane_id,
                            edge_id=lane.edge_id,
                            lane_index=lane.lane_index,
                            role=lane.role,
                            approach=lane.approach,
                            approach_label=lane.approach_label,
                            length=lane.length,
                            max_speed=lane.max_speed,
                        )
                        for lane in item.lanes
                    ],
                )
            )

        return CatalogResponse(
            intersections=intersections,
            event_types=list(catalog.event_types),
            control_modes=["fixed"],
            flow_multiplier=FlowMultiplierRangeSchema(
                min=catalog.flow_multiplier_min,
                max=catalog.flow_multiplier_max,
            ),
        )
