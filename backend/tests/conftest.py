"""共享pytest"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.app.controllers.runtime import AlgorithmRuntimeStore
from backend.app.core.config import get_settings
from backend.app.main import create_app
from backend.app.services.map_service import MapService
from backend.app.services.scenario_export_service import ScenarioExportService
from backend.app.services.simulation_service import SimulationService
from backend.app.services.snapshot_serializer import SnapshotSerializer
from simulation.sumo.session import (
    IntersectionCapability,
    LaneCapability,
    OriginCapability,
    SimulationCatalog,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@dataclass(frozen=True)
class FakeCatalog:
    intersections: Mapping[str, IntersectionCapability]
    event_types: tuple[str, ...] = ("lane_closure", "speed_limit", "accident")
    flow_multiplier_min: float = 0.1
    flow_multiplier_max: float = 5.0


def build_demo_catalog() -> SimulationCatalog:
    intersections: dict[str, IntersectionCapability] = {}
    for index in range(1, 21):
        intersection_id = f"demo_{index}"
        lane_id = f"-{index}000_0"
        intersections[intersection_id] = IntersectionCapability(
            intersection_id=intersection_id,
            longitude=116.126756,
            latitude=38.99115,
            periods=("morning_peak", "off_peak", "evening_peak"),
            origins=(
                OriginCapability(
                    origin_id="incoming",
                    label="Incoming",
                    lane_ids=(lane_id,),
                ),
            ),
            lanes=(
                LaneCapability(
                    lane_id=lane_id,
                    edge_id=f"-{index}000",
                    lane_index=0,
                    role="incoming",
                    approach="west",
                    approach_label="West",
                    length=100.0,
                    max_speed=13.9,
                ),
            ),
        )
    return SimulationCatalog(intersections=intersections)


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
def simulation_service(
    mock_manager: MagicMock,
    serializer: SnapshotSerializer,
    algorithm_store: AlgorithmRuntimeStore,
) -> SimulationService:
    from backend.app.core.config import get_settings
    from backend.app.services.session_metadata import InMemorySessionMetadataStore

    settings = get_settings()
    meta = InMemorySessionMetadataStore(
        terminal_ttl_seconds=settings.citypulse_session_ttl_seconds
    )
    return SimulationService(
        mock_manager,
        serializer,
        settings,
        algorithm_store,
        metadata_store=meta,
    )


@pytest.fixture
def algorithm_store() -> AlgorithmRuntimeStore:
    return AlgorithmRuntimeStore()


@pytest.fixture
def scenario_export_service(
    mock_manager: MagicMock,
) -> ScenarioExportService:
    from backend.app.core.config import get_settings

    return ScenarioExportService(get_settings(), mock_manager)


@pytest.fixture
def client(
    mock_manager: MagicMock,
    simulation_service: SimulationService,
    scenario_export_service: ScenarioExportService,
    algorithm_store: AlgorithmRuntimeStore,
) -> TestClient:
    app = create_app()
    map_service = MagicMock(spec=MapService)
    map_service.xy_to_lonlat.return_value = (116.1267, 38.9911)

    with TestClient(app) as test_client:
        app.state.artifacts_ready = True
        app.state.sumo_home_configured = True
        app.state.missing_files = []
        app.state.simulation_manager = mock_manager
        app.state.simulation_service = simulation_service
        app.state.scenario_export_service = scenario_export_service
        app.state.algorithm_store = algorithm_store
        app.state.map_service = map_service
        app.state.simulation_manager_mode = "local"
        app.state.simulation_manager_ready = True
        app.state.redis_ready = True
        app.state.session_root_ready = True
        app.state.algorithm_state_shared = False
        app.state.recommended_uvicorn_workers = 1
        app.state.detected_uvicorn_workers = None
        yield test_client


@pytest.fixture
def degraded_client() -> TestClient:
    app = create_app()
    with TestClient(app) as test_client:
        app.state.artifacts_ready = False
        app.state.sumo_home_configured = True
        app.state.missing_files = ["data/maps/sumo/generated/traffic_manifest.json"]
        app.state.simulation_manager_mode = "local"
        app.state.simulation_manager_ready = True
        app.state.redis_ready = True
        app.state.session_root_ready = True
        yield test_client
