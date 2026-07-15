"""共享pytest"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.schemas.catalog import (
    CatalogResponse,
    FlowMultiplierRangeSchema,
    IntersectionSchema,
    LaneSchema,
    OriginSchema,
)
from backend.app.services.map_service import MapService
from backend.app.services.simulation_service import SimulationService
from backend.app.services.snapshot_serializer import SnapshotSerializer
from simulation.sumo.session import (
    IntersectionCapability,
    LaneCapability,
    OriginCapability,
    SessionMetrics,
    SimulationCatalog,
    SimulationSnapshot,
    VehicleRuntimeSnapshot,
)


@dataclass(frozen=True)
class FakeCatalog:
    intersections: Mapping[str, IntersectionCapability]
    event_types: tuple[str, ...] = ("lane_closure", "speed_limit", "accident")
    flow_multiplier_min: float = 0.1
    flow_multiplier_max: float = 5.0


def build_demo_catalog() -> SimulationCatalog:
    lanes = (
        LaneCapability(
            lane_id="-56734_0",
            edge_id="-56734",
            lane_index=0,
            role="incoming",
            approach="west",
            approach_label="West",
            length=100.0,
            max_speed=13.9,
        ),
    )
    intersection = IntersectionCapability(
        intersection_id="demo_2",
        longitude=116.126756,
        latitude=38.99115,
        periods=("morning_peak", "off_peak", "evening_peak"),
        origins=(
            OriginCapability(
                origin_id="west",
                label="West",
                lane_ids=("-56734_0",),
            ),
        ),
        lanes=lanes,
    )
    return SimulationCatalog(intersections={"demo_2": intersection})


@pytest.fixture
def demo_catalog() -> SimulationCatalog:
    return build_demo_catalog()


@pytest.fixture
def mock_manager(demo_catalog: SimulationCatalog) -> MagicMock:
    manager = MagicMock()
    manager.catalog.return_value = demo_catalog
    return manager


@pytest.fixture
def coordinate_converter() -> MagicMock:
    converter = MagicMock()
    converter.xy_to_lonlat.return_value = (116.1267, 38.9911)
    return converter


@pytest.fixture
def serializer(coordinate_converter: MagicMock) -> SnapshotSerializer:
    return SnapshotSerializer(coordinate_converter)


@pytest.fixture
def simulation_service(mock_manager: MagicMock, serializer: SnapshotSerializer) -> SimulationService:
    from backend.app.core.config import get_settings

    settings = get_settings()
    return SimulationService(mock_manager, serializer, settings)


@pytest.fixture
def client(mock_manager: MagicMock, simulation_service: SimulationService) -> TestClient:
    app = create_app()
    map_service = MagicMock(spec=MapService)
    map_service.xy_to_lonlat.return_value = (116.1267, 38.9911)

    with TestClient(app) as test_client:
        app.state.artifacts_ready = True
        app.state.sumo_home_configured = True
        app.state.missing_files = []
        app.state.simulation_manager = mock_manager
        app.state.simulation_service = simulation_service
        app.state.map_service = map_service
        yield test_client


@pytest.fixture
def degraded_client() -> TestClient:
    app = create_app()
    with TestClient(app) as test_client:
        app.state.artifacts_ready = False
        app.state.sumo_home_configured = True
        app.state.missing_files = ["data/maps/sumo/generated/traffic_manifest.json"]
        yield test_client
