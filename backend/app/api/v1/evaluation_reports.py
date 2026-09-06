"""管控评估报告 PDF 导出接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ...schemas.evaluation_reports import EvaluationReportRequest
from ...services.evaluation_report_service import (
    EvaluationReportService,
    build_content_disposition,
)
from ...services.simulation_service import SimulationService
from ..deps import get_simulation_service

router = APIRouter()


@router.post("/evaluation-reports/pdf")
def export_evaluation_report_pdf(
    request_body: EvaluationReportRequest,
    service: SimulationService = Depends(get_simulation_service),
) -> Response:
    filename, payload = EvaluationReportService(service).build_pdf(request_body)
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={"Content-Disposition": build_content_disposition(filename)},
    )
