"""基于sumolib的GeoJSON生成与坐标转换"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
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
        self._cache: dict[tuple[str, float, int | None], MapGeoJsonResponse] = {}

    def xy_to_lonlat(self, x: float, y: float) -> tuple[float | None, float | None]:
        try:
            net = self._load_net()
            lon, lat = net.convertXY2LonLat(float(x), float(y))
            return round(float(lon), 7), round(float(lat), 7)
        except Exception:
            return None, None

    def get_geojson(self, intersection_id: str, radius_m: float) -> MapGeoJsonResponse:
        artifact_path = self._generated_geojson_path(intersection_id)
        artifact_mtime_ns = artifact_path.stat().st_mtime_ns if artifact_path.is_file() else None
        cache_key = (intersection_id, float(radius_m), artifact_mtime_ns)
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
        generated = self._load_generated_geojson(
            artifact_path,
            intersection_id=intersection_id,
            center_lon=center_lon,
            center_lat=center_lat,
            radius_m=radius_m,
        )
        if generated is not None:
            self._cache = {cache_key: generated}
            return generated
        net = self._load_net()
        generated = self._load_generated_geojson(
            artifact_path,
            intersection_id=intersection_id,
            center_lon=center_lon,
            center_lat=center_lat,
            radius_m=radius_m,
        )
        if generated is not None:
            self._cache = {cache_key: generated}
            return generated

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
        self._cache = {cache_key: response}
        return response

    def _generated_geojson_path(self, intersection_id: str) -> Path:
        return self._settings.generated_dir / "geojson" / f"{intersection_id}.roads.wgs84.geojson"

    def _load_generated_geojson(
        self,
        path: Path,
        *,
        intersection_id: str,
        center_lon: float,
        center_lat: float,
        radius_m: float,
    ) -> MapGeoJsonResponse | None:
        if not path.is_file():
            return None
        try:
            collection = json.loads(path.read_text(encoding="utf-8"))
            metadata = collection.get("metadata", {})
            if metadata.get("intersection_id") != intersection_id:
                raise ValueError("intersection metadata does not match")
            if metadata.get("output_crs") != "WGS84":
                raise ValueError("generated GeoJSON must use WGS84")
            if not math.isclose(float(metadata.get("radius_m")), float(radius_m), abs_tol=1e-6):
                logger.info("Generated GeoJSON radius mismatch; using runtime conversion")
                return None

            features = collection.get("features")
            if collection.get("type") != "FeatureCollection" or not isinstance(features, list):
                raise ValueError("invalid FeatureCollection")

            bounds = BoundsSchema(west=center_lon, south=center_lat, east=center_lon, north=center_lat)
            validated_features: list[dict[str, Any]] = []
            for feature in features:
                geometry = feature.get("geometry", {})
                coordinates = geometry.get("coordinates")
                if geometry.get("type") != "LineString" or not isinstance(coordinates, list) or len(coordinates) < 2:
                    raise ValueError("generated road must be a LineString with at least two points")
                for coordinate in coordinates:
                    if not isinstance(coordinate, list) or len(coordinate) < 2:
                        raise ValueError("invalid road coordinate")
                    lon, lat = float(coordinate[0]), float(coordinate[1])
                    if not math.isfinite(lon) or not math.isfinite(lat):
                        raise ValueError("non-finite road coordinate")
                    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                        raise ValueError("road coordinate outside WGS84 bounds")
                    bounds.west = min(bounds.west, lon)
                    bounds.south = min(bounds.south, lat)
                    bounds.east = max(bounds.east, lon)
                    bounds.north = max(bounds.north, lat)
                validated_features.append(feature)

            validated_features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "feature_type": "intersection",
                        "intersection_id": intersection_id,
                        "data_source": "generated",
                        "data_version": path.stat().st_mtime_ns,
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(center_lon, 7), round(center_lat, 7)],
                    },
                }
            )
            logger.info("Using generated WGS84 road network: %s", path)
            return MapGeoJsonResponse(
                intersection_id=intersection_id,
                center=CenterSchema(longitude=round(center_lon, 7), latitude=round(center_lat, 7)),
                radius_m=float(radius_m),
                bounds=bounds,
                geojson={
                    "type": "FeatureCollection",
                    "metadata": {**metadata, "data_source": "generated"},
                    "features": validated_features,
                },
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring invalid generated GeoJSON %s: %s", path, exc)
            return None

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
