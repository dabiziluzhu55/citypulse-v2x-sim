"""场景导出接口测试"""

import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app.schemas.simulations import StartSimulationRequest
from backend.app.services.scenario_export_service import ScenarioExportService


@dataclass(frozen=True)
class FakeCompiledScenario:
    session_id: str
    directory: Path
    sumocfg: Path
    route_file: Path
    additional_file: Path
    period: str
    official_start_seconds: int
    window_start_seconds: float
    duration_seconds: float
    planned_vehicle_count: int
    selected_origins: dict
    vehicle_type_profiles: dict
    vehicle_profiles: dict


@pytest.fixture
def export_bundle(tmp_path: Path) -> FakeCompiledScenario:
    bundle_dir = tmp_path / "export-abc"
    bundle_dir.mkdir()
    sumocfg = bundle_dir / "session.sumocfg"
    sumocfg.write_text(
        """<?xml version='1.0' encoding='utf-8'?>
<configuration><input><net-file value="/abs/TotalMap_20.signals.net.xml"/></input></configuration>
""",
        encoding="utf-8",
    )
    route_file = bundle_dir / "session.rou.xml"
    route_file.write_text("<routes/>", encoding="utf-8")
    additional_file = bundle_dir / "session.add.xml"
    additional_file.write_text("<additional/>", encoding="utf-8")
    (bundle_dir / "session_manifest.json").write_text("{}", encoding="utf-8")
    return FakeCompiledScenario(
        session_id="export-abc",
        directory=bundle_dir,
        sumocfg=sumocfg,
        route_file=route_file,
        additional_file=additional_file,
        period="morning_peak",
        official_start_seconds=0,
        window_start_seconds=0.0,
        duration_seconds=600.0,
        planned_vehicle_count=1,
        selected_origins={},
        vehicle_type_profiles={},
        vehicle_profiles={},
    )


def _build_export_service(
    mock_manager: MagicMock,
    tmp_path: Path,
) -> ScenarioExportService:
    from backend.tests.test_od_and_major_events import _write_od_fixture

    generated = _write_od_fixture(tmp_path, period="morning_peak")
    net_path = generated / "network" / "TotalMap_20.signals.net.xml"
    net_path.parent.mkdir(parents=True, exist_ok=True)
    net_path.write_text("<net/>", encoding="utf-8")

    settings = MagicMock()
    settings.project_root = tmp_path
    settings.generated_dir = generated
    settings.signals_net_path = net_path
    settings.scenario_export_root = tmp_path / "exports"
    return ScenarioExportService(settings, mock_manager)


def test_export_scenario_returns_zip_bundle(
    mock_manager: MagicMock,
    export_bundle: FakeCompiledScenario,
    tmp_path: Path,
) -> None:
    service = _build_export_service(mock_manager, tmp_path)
    request = StartSimulationRequest(
        scenario_preset_id="east_dense",
        period="morning_peak",
        duration_seconds=600,
        disturbance_targets=[
            {
                "event_type": "lane_closure",
                "intersection_id": "demo_3",
                "start_seconds": 60,
                "end_seconds": 300,
            }
        ],
    )

    with patch(
        "backend.app.services.scenario_export_service.compile_session_scenario",
        return_value=export_bundle,
    ):
        filename, payload = service.export_zip(request)

    assert filename.startswith("citypulse-east_dense-morning_peak-")
    assert filename.endswith(".zip")

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert "session.sumocfg" in names
        assert "session.rou.xml" in names
        assert "session.add.xml" in names
        assert "events.json" in names
        assert "export_manifest.json" in names
        assert "TotalMap_20.signals.net.xml" in names
        assert "od/od_matrix_morning_peak.csv" in names
        assert "od/taz_9_zones.json" in names
        assert "od/od_heatmap_morning_peak.png" in names
        sumocfg = archive.read("session.sumocfg").decode("utf-8")
        assert 'net-file value="TotalMap_20.signals.net.xml"' in sumocfg
        events = json.loads(archive.read("events.json"))
        assert events["events"][0]["event_type"] == "lane_closure"
        assert str(tmp_path) not in archive.read("od/taz_9_zones.json").decode("utf-8")


def test_export_endpoint_returns_zip(
    client: TestClient,
    mock_manager: MagicMock,
    export_bundle: FakeCompiledScenario,
    tmp_path: Path,
) -> None:
    export_service = _build_export_service(mock_manager, tmp_path)
    client.app.state.scenario_export_service = export_service

    with patch(
        "backend.app.services.scenario_export_service.compile_session_scenario",
        return_value=export_bundle,
    ):
        response = client.post(
            "/api/v1/scenarios/export",
            json={
                "scenario_preset_id": "east_dense",
                "period": "morning_peak",
                "duration_seconds": 600,
                "disturbance_targets": [],
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


def test_export_requires_artifacts(degraded_client: TestClient) -> None:
    response = degraded_client.post(
        "/api/v1/scenarios/export",
        json={
            "scenario_preset_id": "east_dense",
            "period": "morning_peak",
            "duration_seconds": 600,
        },
    )
    assert response.status_code == 503
