"""地图GeoJSON服务测试"""

from unittest.mock import MagicMock

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
        lambda _intersection_id: settings.generated_dir / "missing.geojson",
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


def test_generated_geojson_does_not_load_sumo(monkeypatch) -> None:
    from backend.app.core.config import get_settings

    settings = get_settings()
    manager = MagicMock()
    manager.catalog.return_value = build_demo_catalog()
    service = MapService(settings, manager)
    load_net = MagicMock(side_effect=AssertionError("SUMO must not load for generated GeoJSON"))
    monkeypatch.setattr(service, "_load_net", load_net)

    response = service.get_geojson("demo_2", 600.0)
    lines = [
        feature
        for feature in response.geojson["features"]
        if feature["geometry"]["type"] == "LineString"
    ]

    assert response.geojson["metadata"]["data_source"] == "generated"
    assert len(lines) == 15
    load_net.assert_not_called()
