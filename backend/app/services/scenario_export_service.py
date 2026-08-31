"""将场景配置编译为可下载的SUMO文件包（xiongan_20时含九区域OD）"""

from __future__ import annotations

import io
import json
import logging
import shutil
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from simulation.sumo.engine.scenario import ScenarioCompilationError, compile_session_scenario
from simulation.sumo.engine.session import SimulationManager

from ..core.config import Settings
from ..core.exceptions import AppError
from ..scenario.presets import require_scenario_preset
from ..scenario.resolver import resolve_start_simulation
from ..schemas.events import (
    AccidentRequest,
    EventRequest,
    LaneClosureRequest,
    MajorEventClosingRequest,
    MajorEventOpeningRequest,
    SpeedLimitRequest,
)
from ..schemas.simulations import StartSimulationRequest
from .od_export import OdExportArtifacts, write_od_bundle

logger = logging.getLogger(__name__)

DEFAULT_FLOW_MULTIPLIER = 1.0
# 九区域OD/TAZ是全网20路口口径，仅全网预设导出
OD_EXPORT_PRESET_ID = "xiongan_20"


class ScenarioExportService:
    def __init__(self, settings: Settings, manager: SimulationManager) -> None:
        self._settings = settings
        self._manager = manager

    def export_zip(self, request: StartSimulationRequest) -> tuple[str, bytes]:
        catalog = self._manager.catalog()
        resolved = resolve_start_simulation(request, catalog)
        export_id = f"export-{uuid4().hex[:12]}"
        export_root = self._settings.scenario_export_root / export_id

        if export_root.exists():
            shutil.rmtree(export_root)

        try:
            scenario = compile_session_scenario(
                export_id,
                resolved.intersection_ids,
                resolved.period,
                origins=resolved.origins,
                window_start_seconds=resolved.window_start_seconds,
                duration_seconds=resolved.duration_seconds,
                flow_multiplier=DEFAULT_FLOW_MULTIPLIER,
                step_length=resolved.step_length,
                generated_dir=self._settings.generated_dir,
                session_root=self._settings.scenario_export_root,
            )
            bundle_dir = scenario.directory
            net_filename = self._settings.signals_net_path.name
            net_destination = bundle_dir / net_filename
            shutil.copy2(self._settings.signals_net_path, net_destination)
            self._rewrite_sumocfg_net_file(scenario.sumocfg, net_filename)
            self._write_events_file(bundle_dir, resolved.initial_events)

            od_artifacts: OdExportArtifacts | None = None
            if resolved.scenario_preset_id == OD_EXPORT_PRESET_ID:
                od_artifacts = write_od_bundle(
                    project_root=self._settings.project_root,
                    generated_dir=self._settings.generated_dir,
                    period=resolved.period,
                    window_start_seconds=resolved.window_start_seconds,
                    duration_seconds=resolved.duration_seconds,
                    output_dir=bundle_dir / "od",
                )
            else:
                logger.info(
                    "Skip OD/TAZ export for preset %s (only %s includes global OD)",
                    resolved.scenario_preset_id,
                    OD_EXPORT_PRESET_ID,
                )

            self._write_export_manifest(
                bundle_dir,
                request,
                resolved.scenario_preset_id,
                od_artifacts=od_artifacts,
            )
            filename = self._build_download_filename(
                resolved.scenario_preset_id, resolved.period
            )
            return filename, self._create_zip(bundle_dir)
        except ScenarioCompilationError as exc:
            raise AppError(
                code="SCENARIO_EXPORT_FAILED",
                message=str(exc),
                status_code=422,
            ) from exc
        finally:
            if export_root.exists():
                shutil.rmtree(export_root, ignore_errors=True)

    @staticmethod
    def _rewrite_sumocfg_net_file(sumocfg_path: Path, net_filename: str) -> None:
        tree = ET.parse(sumocfg_path)
        root = tree.getroot()
        net_node = root.find("./input/net-file")
        if net_node is None:
            raise AppError(
                code="SCENARIO_EXPORT_FAILED",
                message=f"Missing net-file entry in {sumocfg_path.name}.",
                status_code=500,
            )
        net_node.set("value", net_filename)
        ET.indent(root, space="  ")
        tree.write(sumocfg_path, encoding="utf-8", xml_declaration=True)

    @staticmethod
    def _write_events_file(bundle_dir: Path, events: tuple[EventRequest, ...]) -> None:
        payload = {"events": [_serialize_event(event) for event in events]}
        (bundle_dir / "events.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_export_manifest(
        bundle_dir: Path,
        request: StartSimulationRequest,
        scenario_preset_id: str,
        *,
        od_artifacts: OdExportArtifacts | None,
    ) -> None:
        preset = require_scenario_preset(scenario_preset_id)
        files: dict[str, str] = {
            "sumocfg": "session.sumocfg",
            "routes": "session.rou.xml",
            "additional": "session.add.xml",
            "events": "events.json",
        }
        payload: dict[str, object] = {
            "schema_version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "scenario_preset_id": scenario_preset_id,
            "scenario_preset_label": preset.label,
            "controlled_intersection_ids": list(preset.intersection_ids),
            "period": request.period,
            "window_start_seconds": request.window_start_seconds,
            "duration_seconds": request.duration_seconds,
            "control_mode": request.control_mode,
            "playback_speed": request.playback_speed,
            "disturbance_targets": [
                target.model_dump(exclude_none=True)
                for target in request.disturbance_targets
            ],
            "files": files,
            "od_included": od_artifacts is not None,
        }
        if od_artifacts is not None:
            files["od_matrix_csv"] = f"od/{od_artifacts.csv_name}"
            files["od_taz_json"] = f"od/{od_artifacts.taz_json_name}"
            files["od_heatmap_png"] = f"od/{od_artifacts.heatmap_name}"
            payload["od_sources"] = od_artifacts.relative_sources
            payload["od_time_scope"] = "full_period"
        net_files = sorted(path.name for path in bundle_dir.glob("*.net.xml"))
        if net_files:
            files["network"] = net_files[0]
        (bundle_dir / "export_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _build_download_filename(scenario_preset_id: str, period: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"citypulse-{scenario_preset_id}-{period}-{timestamp}.zip"

    @staticmethod
    def _create_zip(bundle_dir: Path) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=path.relative_to(bundle_dir).as_posix())
        return buffer.getvalue()


def _serialize_event(event: EventRequest) -> dict[str, object]:
    if isinstance(event, LaneClosureRequest):
        return {
            "event_type": "lane_closure",
            "event_id": event.event_id,
            "start_seconds": event.start_seconds,
            "end_seconds": event.end_seconds,
            "lane_ids": event.lane_ids,
        }
    if isinstance(event, SpeedLimitRequest):
        return {
            "event_type": "speed_limit",
            "event_id": event.event_id,
            "start_seconds": event.start_seconds,
            "end_seconds": event.end_seconds,
            "lane_ids": event.lane_ids,
            "max_speed": float(event.max_speed),
        }
    if isinstance(event, AccidentRequest):
        return {
            "event_type": "accident",
            "event_id": event.event_id,
            "start_seconds": event.start_seconds,
            "end_seconds": event.end_seconds,
            "lane_id": event.lane_id,
            "position_ratio": event.position_ratio,
        }
    if isinstance(event, MajorEventOpeningRequest):
        return {
            "event_type": "major_event_opening",
            "event_id": event.event_id,
            "start_seconds": event.start_seconds,
            "end_seconds": event.end_seconds,
            "venue_lane_id": event.venue_lane_id,
            "vehicle_count": event.vehicle_count,
            "source_lane_ids": event.source_lane_ids,
            "vehicle_type_id": event.vehicle_type_id,
        }
    if isinstance(event, MajorEventClosingRequest):
        return {
            "event_type": "major_event_closing",
            "event_id": event.event_id,
            "start_seconds": event.start_seconds,
            "end_seconds": event.end_seconds,
            "venue_lane_id": event.venue_lane_id,
            "vehicle_count": event.vehicle_count,
            "destination_lane_ids": event.destination_lane_ids,
            "vehicle_type_id": event.vehicle_type_id,
        }
    raise AppError(
        code="INVALID_EVENT",
        message="Unsupported event type for export.",
        status_code=422,
    )
