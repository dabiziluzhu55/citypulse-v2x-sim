"""场景导出接口：编译SUMO配置并打包下载"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ...schemas.simulations import StartSimulationRequest
from ...services.scenario_export_service import ScenarioExportService
from ..deps import get_scenario_export_service

router = APIRouter()


@router.post("/scenarios/export")
def export_scenario_bundle(
    request_body: StartSimulationRequest,
    service: ScenarioExportService = Depends(get_scenario_export_service),
) -> Response:
    filename, payload = service.export_zip(request_body)
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=payload, media_type="application/zip", headers=headers)
