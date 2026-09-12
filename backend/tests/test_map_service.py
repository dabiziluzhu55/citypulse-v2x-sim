"""地图GeoJSON服务测试"""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.schemas.maps import MapGeoJsonResponse
from backend.app.services.map_service import MapService
from backend.tests.conftest import build_demo_catalog


class FakeEdge:
    def __init__(self, edge_id: str, shape: list[tuple[float, float]]) -> None:
        self._edge_id = edge_id
        self._shape = shape

    def getID(self) -> str:
        return self._edge_id

    def getShape(self) -> list[tuple[float, float]]:
        return self._shape

    def getLanes(self) -> list[str]:
        return ["lane"]

    def getSpeed(self) -> float:
        return 13.9

    def getPriority(self) -> int:
        return 1


class FakeNet:
    def convertLonLat2XY(self, lon: float, lat: float) -> tuple[float, float]:
        return 100.0, 200.0

    def convertXY2LonLat(self, x: float, y: float) -> tuple[float, float]:
        return 116.126756 + x * 1e-6, 38.99115 + y * 1e-6

    def getEdges(self) -> list[FakeEdge]:
        return [
            FakeEdge("road-1", [(95.0, 195.0), (105.0, 205.0)]),
            FakeEdge(":internal", [(100.0, 200.0), (101.0, 201.0)]),
            FakeEdge("road-far", [(1000.0, 2000.0), (1001.0, 2001.0)]),
        ]


class MissingProjectionNet:
    def convertXY2LonLat(self, _x: float, _y: float) -> tuple[float, float]:
        raise ModuleNotFoundError("No module named 'pyproj'")


def test_projection_validation_fails_with_an_actionable_error(monkeypatch) -> None:
    from backend.app.core.config import get_settings

    service = MapService(get_settings(), MagicMock())
    monkeypatch.setattr(service, "_load_net", lambda: MissingProjectionNet())

    with pytest.raises(RuntimeError, match="pip install -r backend/requirements.txt"):
        service.validate_coordinate_projection()


def test_projection_validation_accepts_a_working_network(monkeypatch) -> None:
    from backend.app.core.config import get_settings

    service = MapService(get_settings(), MagicMock())
    monkeypatch.setattr(service, "_load_net", lambda: FakeNet())

    service.validate_coordinate_projection()


def test_geojson_format(monkeypatch) -> None:
    from backend.app.core.config import get_settings

    settings = get_settings()
    manager = MagicMock()
    manager.catalog.return_value = build_demo_catalog()
    service = MapService(settings, manager)
    monkeypatch.setattr(service, "_load_net", lambda: FakeNet())
    monkeypatch.setattr(
        service,
        "_generated_geojson_path",
        lambda _intersection_id, _radius: settings.generated_dir / "missing.geojson",
    )

    response = service.get_geojson("demo_2", 600.0)
    assert isinstance(response, MapGeoJsonResponse)
    assert response.geojson["type"] == "FeatureCollection"
    edge_features = [
        feature
        for feature in response.geojson["features"]
        if feature["geometry"]["type"] == "LineString"
    ]
    assert len(edge_features) == 1
    assert edge_features[0]["properties"]["edge_id"] == "road-1"
    assert edge_features[0]["geometry"]["coordinates"][0][0] != 100.0

    point_features = [
        feature
        for feature in response.geojson["features"]
        if feature["geometry"]["type"] == "Point"
    ]
    assert point_features[0]["properties"]["intersection_id"] == "demo_2"

    cached = service.get_geojson("demo_2", 600.0)
    assert cached is response


@pytest.mark.parametrize("radius", [600.0, 900.0])
def test_generated_geojson_does_not_load_sumo(monkeypatch, tmp_path, radius) -> None:
    from backend.app.core.config import get_settings

    settings = get_settings().model_copy(update={
        "sumo_generated_dir": str(tmp_path), "simulation_manager_mode": "redis",
    })
    manager = MagicMock()
    manager.catalog.return_value = build_demo_catalog()
    service = MapService(settings, manager)
    directory = tmp_path / "geojson"
    if radius != 600:
        directory = directory / f"radius_{radius:g}"
    directory.mkdir(parents=True)
    artifact_path = directory / "demo_2.roads.wgs84.geojson"
    artifact_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "metadata": {
                    "intersection_id": "demo_2",
                    "output_crs": "WGS84",
                    "radius_m": radius,
                },
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"edge_id": "generated-road"},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [116.1267, 38.9911],
                                [116.1268, 38.9912],
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    load_net = MagicMock(side_effect=AssertionError("SUMO must not load for generated GeoJSON"))
    monkeypatch.setattr(service, "_load_net", load_net)

    response = service.get_geojson("demo_2", radius)
    lines = [
        feature
        for feature in response.geojson["features"]
        if feature["geometry"]["type"] == "LineString"
    ]

    assert response.geojson["metadata"]["data_source"] == "generated"
    assert response.radius_m == radius
    assert len(lines) == 1
    load_net.assert_not_called()


def test_redis_vehicle_coordinates_match_network_projection_without_sumo(tmp_path, monkeypatch):
    from backend.app.core.config import Settings
    from backend.app.services.snapshot_serializer import SnapshotSerializer
    from simulation_protocol.dto import VehicleRuntimeSnapshot

    settings = Settings(_env_file=None, simulation_manager_mode="redis", sumo_generated_dir=str(tmp_path))
    settings.signals_net_path.parent.mkdir(parents=True)
    settings.signals_net_path.write_text(
        '<net><location netOffset="-500000,10" projParameter="+proj=utm +zone=50 +datum=WGS84"/>'
        '<edge id="road"><lane id="road_0" shape="-2,10 0,10 2,10"/></edge></net>',
        encoding="utf-8",
    )
    service = MapService(settings, None)
    forbidden = MagicMock(side_effect=AssertionError("Redis must never load sumolib"))
    monkeypatch.setattr(service, "_load_net", forbidden)
    service.validate_coordinate_projection()
    vehicle = VehicleRuntimeSnapshot(vehicle_id="v", x=0, y=10, speed=5, angle=90,
                                     road_id="road", lane_id="road_0")
    payload = SnapshotSerializer(service)._serialize_vehicle(vehicle)
    assert (payload["longitude"], payload["latitude"]) == pytest.approx((117, 0))
    assert service.lane_center_lonlat("road_0") == pytest.approx((117, 0))
    assert service.lane_center_lonlat("missing") == (None, None)
    forbidden.assert_not_called()
