"""Tests for the reproducible SUMO road-network conversion."""

from __future__ import annotations

import math

import pytest
from pyproj import Transformer
from shapely.geometry import LineString, Point, shape
from shapely.ops import transform

from backend.app.core.config import get_settings
from backend.app.core.sumo_env import configure_sumo_home, import_sumolib
from simulation.utils.convert_sumo_road_network import LOCAL_METRIC_CRS, convert_network

CENTER = (116.126756, 38.99115)
RADIUS_METERS = 600.0


@pytest.fixture(scope="module")
def converted_collection():
    settings = get_settings()
    configure_sumo_home(settings)
    net = import_sumolib().net.readNet(str(settings.signals_net_path))
    return convert_network(
        net,
        intersection_id="demo_2",
        center_lon=CENTER[0],
        center_lat=CENTER[1],
        radius_m=RADIUS_METERS,
        source_name=settings.signals_net_path.name,
    )


def test_real_network_conversion_is_valid(converted_collection) -> None:
    metadata = converted_collection["metadata"]
    features = converted_collection["features"]

    assert metadata["output_crs"] == "WGS84"
    assert metadata["feature_count"] == len(features) == 15
    assert metadata["vertex_count"] == 490
    assert all(not feature["properties"]["edge_id"].startswith(":") for feature in features)
    assert len({feature["id"] for feature in features}) == len(features)

    to_metric = Transformer.from_crs("EPSG:4326", LOCAL_METRIC_CRS, always_xy=True).transform
    clip_area = transform(to_metric, Point(*CENTER)).buffer(RADIUS_METERS + 0.05)
    for feature in features:
        geometry = shape(feature["geometry"])
        assert isinstance(geometry, LineString)
        assert geometry.is_valid and not geometry.is_empty
        assert len(geometry.coords) >= 2
        assert all(math.isfinite(value) for coordinate in geometry.coords for value in coordinate)
        assert transform(to_metric, geometry).within(clip_area)
        assert feature["properties"]["length_m"] > 0
        assert feature["properties"]["width_m"] > 0


def test_conversion_is_deterministic(converted_collection) -> None:
    settings = get_settings()
    configure_sumo_home(settings)
    net = import_sumolib().net.readNet(str(settings.signals_net_path))
    repeated = convert_network(
        net,
        intersection_id="demo_2",
        center_lon=CENTER[0],
        center_lat=CENTER[1],
        radius_m=RADIUS_METERS,
        source_name=settings.signals_net_path.name,
    )
    assert repeated == converted_collection


def test_geojson_geometry_round_trip(converted_collection) -> None:
    for feature in converted_collection["features"]:
        geometry = shape(feature["geometry"])
        reconstructed = LineString(list(geometry.coords))
        assert reconstructed.equals_exact(geometry, tolerance=1e-12)


def test_expected_baidu_coordinate_and_double_conversion_distance() -> None:
    from math import atan2, cos, pi, sin, sqrt

    x_pi = pi * 3000.0 / 180.0
    a = 6378245.0
    ee = 0.00669342162296594323

    def transform_lat(longitude: float, latitude: float) -> float:
        result = -100.0 + 2.0 * longitude + 3.0 * latitude
        result += 0.2 * latitude**2 + 0.1 * longitude * latitude + 0.2 * sqrt(abs(longitude))
        result += (20.0 * sin(6.0 * longitude * pi) + 20.0 * sin(2.0 * longitude * pi)) * 2.0 / 3.0
        result += (20.0 * sin(latitude * pi) + 40.0 * sin(latitude / 3.0 * pi)) * 2.0 / 3.0
        return result + (160.0 * sin(latitude / 12.0 * pi) + 320.0 * sin(latitude * pi / 30.0)) * 2.0 / 3.0

    def transform_lon(longitude: float, latitude: float) -> float:
        result = 300.0 + longitude + 2.0 * latitude
        result += 0.1 * longitude**2 + 0.1 * longitude * latitude + 0.1 * sqrt(abs(longitude))
        result += (20.0 * sin(6.0 * longitude * pi) + 20.0 * sin(2.0 * longitude * pi)) * 2.0 / 3.0
        result += (20.0 * sin(longitude * pi) + 40.0 * sin(longitude / 3.0 * pi)) * 2.0 / 3.0
        return result + (150.0 * sin(longitude / 12.0 * pi) + 300.0 * sin(longitude * pi / 30.0)) * 2.0 / 3.0

    def wgs84_to_bd09(longitude: float, latitude: float) -> tuple[float, float]:
        rad_lat = latitude / 180.0 * pi
        magic = 1 - ee * sin(rad_lat) ** 2
        sqrt_magic = sqrt(magic)
        gcj_lat = latitude + transform_lat(longitude - 105.0, latitude - 35.0) * 180.0 / (
            (a * (1 - ee)) / (magic * sqrt_magic) * pi
        )
        gcj_lon = longitude + transform_lon(longitude - 105.0, latitude - 35.0) * 180.0 / (
            a / sqrt_magic * cos(rad_lat) * pi
        )
        magnitude = sqrt(gcj_lon**2 + gcj_lat**2) + 0.00002 * sin(gcj_lat * x_pi)
        angle = atan2(gcj_lat, gcj_lon) + 0.000003 * cos(gcj_lon * x_pi)
        return magnitude * cos(angle) + 0.0065, magnitude * sin(angle) + 0.006

    once = wgs84_to_bd09(*CENTER)
    twice = wgs84_to_bd09(*once)
    assert once == pytest.approx((116.13940618, 38.99825162), abs=1e-8)

    to_metric = Transformer.from_crs("EPSG:4326", LOCAL_METRIC_CRS, always_xy=True)
    once_xy = to_metric.transform(*once)
    twice_xy = to_metric.transform(*twice)
    assert math.dist(once_xy, twice_xy) > 1000
