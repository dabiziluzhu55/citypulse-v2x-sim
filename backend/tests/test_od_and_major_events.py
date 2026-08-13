"""OD导出与大型活动事件单元测试"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.app.core.exceptions import AppError
from backend.app.schemas.disturbance_targets import DisturbanceTarget
from backend.app.schemas.events import EventRequest
from backend.app.schemas.simulations import StartSimulationRequest
from backend.app.scenario.resolver import resolve_disturbance_targets, resolve_start_simulation
from backend.app.scenario.presets import require_scenario_preset
from backend.app.services.od_export import (
    EXPECTED_ZONE_IDS,
    extract_ordered_matrix,
    load_and_validate_od_zones,
    render_od_csv,
    render_od_heatmap_png,
    write_od_bundle,
)
from backend.app.services.scenario_export_service import ScenarioExportService, _serialize_event
from backend.app.services.simulation_service import SimulationService
from backend.app.controllers.runtime import AlgorithmRuntimeStore
from backend.app.services.snapshot_serializer import SnapshotSerializer
from simulation.sumo import MajorEventClosingEvent, MajorEventOpeningEvent
from simulation.sumo.engine.session import (
    IntersectionCapability,
    LaneCapability,
    OriginCapability,
    SimulationCatalog,
)


ZONE_MAP = {
    "zone_1": ["demo_1", "demo_8", "demo_10"],
    "zone_2": ["demo_2", "demo_4"],
    "zone_3": ["demo_3", "demo_5", "demo_6", "demo_9"],
    "zone_4": ["demo_7"],
    "zone_5": ["demo_11", "demo_12"],
    "zone_6": ["demo_13"],
    "zone_7": ["demo_14", "demo_15", "demo_19"],
    "zone_8": ["demo_16"],
    "zone_9": ["demo_17", "demo_18", "demo_20"],
}


def _matrix_pcu() -> list[list[float]]:
    matrix = [[0.0 for _ in range(9)] for _ in range(9)]
    matrix[0][1] = 10.5
    matrix[1][0] = 8.0
    matrix[2][8] = 3.5
    return matrix


def _write_od_fixture(project_root: Path, period: str = "morning_peak") -> Path:
    demands = {
        "schema_version": 1,
        "unit": "pcu",
        "od_zones": ZONE_MAP,
    }
    demands_path = project_root / "data/maps/sumo/official_traffic_demands.json"
    demands_path.parent.mkdir(parents=True, exist_ok=True)
    demands_path.write_text(json.dumps(demands), encoding="utf-8")

    generated = project_root / "data/maps/sumo/generated"
    reports = generated / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    matrix = _matrix_pcu()
    report = {
        "schema_version": 1,
        "period_id": period,
        "unit": "pcu",
        "diagonal_policy": "excluded_and_written_as_zero",
        "matrix_pcu": matrix,
        "interzonal_pcu": 22.0,
        "excluded_intra_zone_pcu": 5.0,
        "zones": [
            {"zone_id": zone_id, "intersection_ids": ZONE_MAP[zone_id]}
            for zone_id in EXPECTED_ZONE_IDS
        ],
    }
    (reports / f"traffic_od_{period}.json").write_text(
        json.dumps(report), encoding="utf-8"
    )
    (reports / f"traffic_od_{period}.csv").write_text(
        render_od_csv(matrix), encoding="utf-8"
    )
    manifest = {
        "scenarios": {
            f"global_{period}": {
                "od_report": f"reports/traffic_od_{period}.json",
                "od_matrix_csv": f"reports/traffic_od_{period}.csv",
            }
        }
    }
    manifests = generated / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    (manifests / "traffic_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return generated


def _full_catalog() -> SimulationCatalog:
    intersections: dict[str, IntersectionCapability] = {}
    for index in range(1, 21):
        iid = f"demo_{index}"
        lane = f"-{index}000_0"
        intersections[iid] = IntersectionCapability(
            intersection_id=iid,
            longitude=116.0,
            latitude=38.9,
            periods=("morning_peak", "off_peak", "evening_peak"),
            origins=(
                OriginCapability(
                    origin_id="incoming",
                    label="Incoming",
                    lane_ids=(lane,),
                ),
            ),
            lanes=(
                LaneCapability(
                    lane_id=lane,
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


def test_validate_taz_no_missing_or_duplicate(tmp_path: Path) -> None:
    generated = _write_od_fixture(tmp_path)
    zones = load_and_validate_od_zones(
        tmp_path / "data/maps/sumo/official_traffic_demands.json"
    )
    assert list(zones) == list(EXPECTED_ZONE_IDS)
    owned = [item for values in zones.values() for item in values]
    assert len(owned) == 20
    assert len(set(owned)) == 20
    del generated


def test_od_csv_is_fixed_9x9_order() -> None:
    matrix = _matrix_pcu()
    text = render_od_csv(matrix)
    lines = text.strip().splitlines()
    assert lines[0].startswith("origin_zone/destination_zone,zone_1,")
    assert lines[0].endswith("zone_9")
    assert len(lines) == 10
    assert lines[1].startswith("zone_1,")
    assert lines[9].startswith("zone_9,")
    # diagonal zeros preserved
    assert lines[1].split(",")[1] == "0"


def test_od_missing_report_raises(tmp_path: Path) -> None:
    demands = tmp_path / "data/maps/sumo/official_traffic_demands.json"
    demands.parent.mkdir(parents=True)
    demands.write_text(json.dumps({"od_zones": ZONE_MAP}), encoding="utf-8")
    with pytest.raises(AppError) as exc:
        write_od_bundle(
            project_root=tmp_path,
            generated_dir=tmp_path / "data/maps/sumo/generated",
            period="morning_peak",
            window_start_seconds=0.0,
            duration_seconds=600.0,
            output_dir=tmp_path / "od",
        )
    assert exc.value.code == "SCENARIO_EXPORT_FAILED"
    assert "missing" in exc.value.message.lower() or "OD" in exc.value.message


def test_heatmap_png_non_empty() -> None:
    png = render_od_heatmap_png(
        matrix=_matrix_pcu(),
        zones=ZONE_MAP,
        period="morning_peak",
        diagonal_policy="excluded_and_written_as_zero",
        window_start_seconds=0.0,
        duration_seconds=600.0,
        unit="pcu",
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 1000


def test_export_zip_contains_od_artifacts(
    mock_manager: MagicMock,
    tmp_path: Path,
) -> None:
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _Bundle:
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

    bundle_dir = tmp_path / "export-bundle"
    bundle_dir.mkdir()
    sumocfg = bundle_dir / "session.sumocfg"
    sumocfg.write_text(
        "<?xml version='1.0' encoding='utf-8'?>\n"
        "<configuration><input>"
        "<net-file value='/abs/TotalMap_20.signals.net.xml'/>"
        "</input></configuration>\n",
        encoding="utf-8",
    )
    (bundle_dir / "session.rou.xml").write_text("<routes/>", encoding="utf-8")
    (bundle_dir / "session.add.xml").write_text("<additional/>", encoding="utf-8")
    export_bundle = _Bundle(
        session_id="export-bundle",
        directory=bundle_dir,
        sumocfg=sumocfg,
        route_file=bundle_dir / "session.rou.xml",
        additional_file=bundle_dir / "session.add.xml",
        period="morning_peak",
        official_start_seconds=0,
        window_start_seconds=0.0,
        duration_seconds=600.0,
        planned_vehicle_count=1,
        selected_origins={},
        vehicle_type_profiles={},
        vehicle_profiles={},
    )

    generated = _write_od_fixture(tmp_path)
    net_path = generated / "network" / "TotalMap_20.signals.net.xml"
    net_path.parent.mkdir(parents=True, exist_ok=True)
    net_path.write_text("<net/>", encoding="utf-8")

    settings = MagicMock()
    settings.project_root = tmp_path
    settings.generated_dir = generated
    settings.signals_net_path = net_path
    settings.scenario_export_root = tmp_path / "exports"
    service = ScenarioExportService(settings, mock_manager)
    mock_manager.catalog.return_value = _full_catalog()

    request = StartSimulationRequest(
        scenario_preset_id="xiongan_20",
        period="morning_peak",
        duration_seconds=600,
        disturbance_targets=[
            {
                "event_type": "major_event_opening",
                "intersection_id": "demo_2",
                "start_seconds": 60,
                "end_seconds": 300,
                "vehicle_count": 20,
            }
        ],
    )
    with patch(
        "backend.app.services.scenario_export_service.compile_session_scenario",
        return_value=export_bundle,
    ):
        filename, payload = service.export_zip(request)

    assert filename.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert "od/od_matrix_morning_peak.csv" in names
        assert "od/taz_9_zones.json" in names
        assert "od/od_heatmap_morning_peak.png" in names
        csv_text = archive.read("od/od_matrix_morning_peak.csv").decode("utf-8")
        assert "zone_1,zone_2" in csv_text.splitlines()[0]
        taz = json.loads(archive.read("od/taz_9_zones.json"))
        assert taz["zone_count"] == 9
        assert taz["od_unit"] == "pcu"
        assert archive.read("od/od_heatmap_morning_peak.png")[:8] == b"\x89PNG\r\n\x1a\n"
        assert taz["time_scope"]["od_matrix_scope"] == "full_period"
        # no absolute server paths
        blob = archive.read("od/taz_9_zones.json").decode("utf-8")
        assert str(tmp_path) not in blob
        assert "/home/" not in blob
        events = json.loads(archive.read("events.json"))
        assert events["events"][0]["event_type"] == "major_event_opening"
        assert events["events"][0]["vehicle_count"] == 20
        manifest = json.loads(archive.read("export_manifest.json"))
        assert "major_event_opening" in json.dumps(manifest["disturbance_targets"])
        assert manifest["od_included"] is True
        assert manifest["files"]["od_matrix_csv"] == "od/od_matrix_morning_peak.csv"


def test_export_zip_skips_od_for_dense_presets(
    mock_manager: MagicMock,
    tmp_path: Path,
) -> None:
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class _Bundle:
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

    for preset_id in ("east_dense", "west_dense"):
        bundle_dir = tmp_path / f"export-{preset_id}"
        bundle_dir.mkdir()
        sumocfg = bundle_dir / "session.sumocfg"
        sumocfg.write_text(
            "<?xml version='1.0' encoding='utf-8'?>\n"
            "<configuration><input>"
            "<net-file value='/abs/TotalMap_20.signals.net.xml'/>"
            "</input></configuration>\n",
            encoding="utf-8",
        )
        (bundle_dir / "session.rou.xml").write_text("<routes/>", encoding="utf-8")
        (bundle_dir / "session.add.xml").write_text("<additional/>", encoding="utf-8")
        export_bundle = _Bundle(
            session_id=f"export-{preset_id}",
            directory=bundle_dir,
            sumocfg=sumocfg,
            route_file=bundle_dir / "session.rou.xml",
            additional_file=bundle_dir / "session.add.xml",
            period="morning_peak",
            official_start_seconds=0,
            window_start_seconds=0.0,
            duration_seconds=600.0,
            planned_vehicle_count=1,
            selected_origins={},
            vehicle_type_profiles={},
            vehicle_profiles={},
        )

        generated = _write_od_fixture(tmp_path)
        net_path = generated / "network" / "TotalMap_20.signals.net.xml"
        net_path.parent.mkdir(parents=True, exist_ok=True)
        net_path.write_text("<net/>", encoding="utf-8")

        settings = MagicMock()
        settings.project_root = tmp_path
        settings.generated_dir = generated
        settings.signals_net_path = net_path
        settings.scenario_export_root = tmp_path / f"exports-{preset_id}"
        service = ScenarioExportService(settings, mock_manager)
        mock_manager.catalog.return_value = _full_catalog()

        request = StartSimulationRequest(
            scenario_preset_id=preset_id,
            period="morning_peak",
            duration_seconds=600,
            disturbance_targets=[],
        )
        with patch(
            "backend.app.services.scenario_export_service.compile_session_scenario",
            return_value=export_bundle,
        ):
            _, payload = service.export_zip(request)

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            assert not any(name.startswith("od/") for name in names)
            manifest = json.loads(archive.read("export_manifest.json"))
            assert manifest["od_included"] is False
            assert "od_matrix_csv" not in manifest["files"]
            assert manifest["scenario_preset_id"] == preset_id


def test_major_event_request_conversion() -> None:
    opening = TypeAdapter(EventRequest).validate_python(
        {
            "event_type": "major_event_opening",
            "event_id": "open-1",
            "start_seconds": 10,
            "end_seconds": 100,
            "venue_lane_id": "-2000_0",
            "vehicle_count": 12,
            "source_lane_ids": ["-1000_0"],
            "vehicle_type_id": "official_passenger",
        }
    )
    closing = TypeAdapter(EventRequest).validate_python(
        {
            "event_type": "major_event_closing",
            "event_id": "close-1",
            "start_seconds": 10,
            "end_seconds": 100,
            "venue_lane_id": "-2000_0",
            "vehicle_count": 8,
            "destination_lane_ids": ["-3000_0"],
            "vehicle_type_id": "official_passenger",
        }
    )
    opened = SimulationService._to_disturbance_event(opening)
    closed = SimulationService._to_disturbance_event(closing)
    assert isinstance(opened, MajorEventOpeningEvent)
    assert opened.source_lane_ids == ("-1000_0",)
    assert opened.vehicle_count == 12
    assert isinstance(closed, MajorEventClosingEvent)
    assert closed.destination_lane_ids == ("-3000_0",)
    assert list(SimulationService._event_lane_ids(opened)) == ["-2000_0", "-1000_0"]
    assert list(SimulationService._event_lane_ids(closed)) == ["-2000_0", "-3000_0"]


def test_major_event_initial_and_runtime_paths(
    mock_manager: MagicMock,
    serializer: SnapshotSerializer,
    algorithm_store: AlgorithmRuntimeStore,
) -> None:
    catalog = _full_catalog()
    mock_manager.catalog.return_value = catalog
    mock_manager.add_event.return_value = "open-1"
    from backend.app.services.session_metadata import InMemorySessionMetadataStore
    from backend.app.core.config import Settings

    settings = Settings(simulation_manager_mode="local")
    service = SimulationService(
        mock_manager,
        serializer,
        settings,
        algorithm_store,
        metadata_store=InMemorySessionMetadataStore(),
    )
    service._metadata.upsert(
        "session-1",
        control_mode="fixed",
        scenario_preset_id="xiongan_20",
        state="RUNNING",
    )

    # initial disturbance target resolution
    preset = require_scenario_preset("xiongan_20")
    events = resolve_disturbance_targets(
        [
            TypeAdapter(DisturbanceTarget).validate_python(
                {
                    "event_type": "major_event_closing",
                    "intersection_id": "demo_2",
                    "start_seconds": 50,
                    "end_seconds": 200,
                    "vehicle_count": 15,
                    "destination_lane_ids": ["-3000_0"],
                }
            )
        ],
        preset,
        catalog,
    )
    assert events[0].event_type == "major_event_closing"
    assert events[0].venue_lane_id == "-2000_0"
    assert events[0].destination_lane_ids == ["-3000_0"]

    # runtime add_event
    mock_manager.snapshot.return_value = MagicMock(state="RUNNING", progress=0.1)
    event_id = service.add_event(
        "session-1",
        TypeAdapter(EventRequest).validate_python(
            {
                "event_type": "major_event_opening",
                "event_id": "open-1",
                "start_seconds": 10,
                "end_seconds": 80,
                "venue_lane_id": "-2000_0",
                "vehicle_count": 9,
                "source_lane_ids": [],
                "vehicle_type_id": "citypulse_event_passenger",
            }
        ),
    )
    assert event_id == "open-1"
    added = mock_manager.add_event.call_args[0][1]
    assert isinstance(added, MajorEventOpeningEvent)
    assert added.source_lane_ids == ()


def test_major_event_validation_rejects_bad_count_and_lanes() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(EventRequest).validate_python(
            {
                "event_type": "major_event_opening",
                "event_id": "bad",
                "start_seconds": 10,
                "end_seconds": 20,
                "venue_lane_id": "-2000_0",
                "vehicle_count": 0,
                "vehicle_type_id": "official_passenger",
            }
        )
    with pytest.raises(ValidationError):
        TypeAdapter(EventRequest).validate_python(
            {
                "event_type": "major_event_closing",
                "event_id": "bad",
                "start_seconds": 30,
                "end_seconds": 10,
                "venue_lane_id": "-2000_0",
                "vehicle_count": 3,
                "vehicle_type_id": "official_passenger",
            }
        )


def test_legacy_three_events_still_serialize() -> None:
    adapter = TypeAdapter(EventRequest)
    for payload in (
        {
            "event_type": "lane_closure",
            "event_id": "c1",
            "start_seconds": 1,
            "end_seconds": 2,
            "lane_ids": ["a"],
        },
        {
            "event_type": "speed_limit",
            "event_id": "s1",
            "start_seconds": 1,
            "end_seconds": 2,
            "lane_ids": ["a"],
            "max_speed": 5,
        },
        {
            "event_type": "accident",
            "event_id": "a1",
            "start_seconds": 1,
            "end_seconds": 2,
            "lane_id": "a",
            "position_ratio": 0.5,
        },
    ):
        event = adapter.validate_python(payload)
        serialized = _serialize_event(event)
        assert serialized["event_type"] == payload["event_type"]


def test_period_mismatch_rejected(tmp_path: Path) -> None:
    generated = _write_od_fixture(tmp_path, period="morning_peak")
    report = json.loads(
        (generated / "reports/traffic_od_morning_peak.json").read_text(encoding="utf-8")
    )
    with pytest.raises(AppError):
        extract_ordered_matrix(report, period="evening_peak")
