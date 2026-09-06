"""读取已有终态评估结果并生成管控评估 PDF，不重算任何指标。"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from io import BytesIO
from typing import Any
from urllib.parse import quote

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..core.exceptions import AppError
from ..scenario.presets import SCENARIO_PRESET_REGISTRY
from ..schemas.evaluation_reports import (
    REPORT_ALGORITHMS,
    EvaluationReportRequest,
    EvaluationReportScenario,
)
from .simulation_service import SimulationService

logger = logging.getLogger(__name__)

MISSING_CELL = "—"
FOOTNOTE_TEXT = "— 表示该算法尚未完成本场景仿真与评估。"
ASCII_FALLBACK_FILENAME = "control-evaluation.pdf"

REPORT_ALGORITHM_LABELS: dict[str, str] = {
    "fixed": "固定配时",
    "max_pressure": "Max Pressure",
    "sotl": "SOTL",
    "ippo": "IPPO",
    "mappo": "MAPPO",
    "cov2x": "CoV2X",
}

PERIOD_LABELS: dict[str, str] = {
    "morning_peak": "早高峰",
    "off_peak": "平峰",
    "evening_peak": "晚高峰",
}

PERIOD_START_SECONDS: dict[str, int] = {
    "morning_peak": 7 * 3600,
    "off_peak": 14 * 3600 + 30 * 60,
    "evening_peak": 17 * 3600 + 30 * 60,
}

# 仅控制 PDF 小数位，不改变 traffic_eval 已有指标语义。
METRIC_DIGITS: dict[str, int] = {
    "path_avg_speed_kmh": 2,
    "avg_stops_per_vehicle": 2,
    "regional_max_queue_length_m": 2,
    "avg_travel_time": 2,
    "avg_waiting_time": 2,
    "throughput": 2,
    "travel_time_index": 3,
    "delay_time_proportion": 3,
    "traffic_performance_index": 2,
    "spillback_rate": 2,
    "hard_braking_rate": 2,
    "fuel_intensity_L_per_100km": 2,
    "avg_decision_latency_ms": 2,
}

TABLE1_FIELDS: tuple[str, ...] = (
    "path_avg_speed_kmh",
    "avg_stops_per_vehicle",
    "regional_max_queue_length_m",
    "avg_travel_time",
    "avg_waiting_time",
    "throughput",
    "travel_time_index",
    "delay_time_proportion",
    "traffic_performance_index",
)

TABLE2_FIELDS: tuple[str, ...] = (
    "spillback_rate",
    "hard_braking_rate",
    "fuel_intensity_L_per_100km",
    "avg_decision_latency_ms",
)

TABLE1_HEADERS: tuple[str, ...] = (
    "算法",
    "平均行程速度<br/>(km/h) ↑",
    "平均停车次数<br/>↓",
    "最大排队长度<br/>(m) ↓",
    "平均行程时间<br/>(s) ↓",
    "平均等待时间<br/>(s) ↓",
    "吞吐流率<br/>(veh/h) ↑",
    "TTI<br/>↓",
    "DTP<br/>↓",
    "TPI<br/>↓",
)

TABLE2_HEADERS: tuple[str, ...] = (
    "算法",
    "溢流率<br/>↓",
    "急刹车率<br/>↓",
    "百公里油耗强度<br/>↓",
    "平均决策时延<br/>↓",
)

_CID_FONTS_REGISTERED = False
BODY_FONT = "STSong-Light"
PAGE_WIDTH = A4[0]
HORIZONTAL_MARGIN = 16 * mm


def format_scenario_label(preset_id: str) -> str:
    preset = SCENARIO_PRESET_REGISTRY.get(preset_id)
    return preset.label if preset is not None else preset_id


def format_period_label(period: str) -> str:
    return PERIOD_LABELS.get(period, period)


def format_clock(total_seconds: float) -> str:
    normalized = max(0, int(round(total_seconds)))
    hours = (normalized // 3600) % 24
    minutes = (normalized % 3600) // 60
    return f"{hours:02d}:{minutes:02d}"


def format_time_window(
    period: str,
    window_start_seconds: float,
    duration_seconds: float,
) -> str:
    absolute_start = PERIOD_START_SECONDS.get(period, 0) + window_start_seconds
    return f"{format_clock(absolute_start)}-{format_clock(absolute_start + duration_seconds)}"


def build_report_title(
    table_index: int,
    scenario: EvaluationReportScenario | None,
    suffix: str,
) -> str:
    if scenario is None:
        return f"表 {table_index} {suffix}"
    scene = format_scenario_label(scenario.scenario_preset_id)
    period = format_period_label(scenario.period)
    window = format_time_window(
        scenario.period,
        scenario.window_start_seconds,
        scenario.duration_seconds,
    )
    return f"表 {table_index} {scene}{period} {window} {suffix}"


def format_metric_value(value: object, digits: int) -> str:
    if value is None or isinstance(value, bool):
        return MISSING_CELL
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return MISSING_CELL
        return f"{number:.{digits}f}"
    return MISSING_CELL


def _fuel_intensity(metrics: dict[str, Any]) -> object:
    fuel = metrics.get("fuel_intensity_L_per_100km")
    if isinstance(fuel, (int, float)):
        return fuel
    # fuel_consumption 当前是 L/100km 的兼容别名，不是另一套公式。
    alias = metrics.get("fuel_consumption")
    if isinstance(alias, (int, float)):
        return alias
    return None


@dataclass(frozen=True)
class ReportAlgorithmRow:
    algorithm: str
    label: str
    completed: bool
    values: dict[str, str]


def _blank_row(algorithm: str) -> ReportAlgorithmRow:
    fields = (*TABLE1_FIELDS, *TABLE2_FIELDS)
    return ReportAlgorithmRow(
        algorithm=algorithm,
        label=REPORT_ALGORITHM_LABELS[algorithm],
        completed=False,
        values={field: MISSING_CELL for field in fields},
    )


def _completed_row(algorithm: str, metrics: dict[str, Any]) -> ReportAlgorithmRow:
    values = {
        field: format_metric_value(metrics.get(field), METRIC_DIGITS[field])
        for field in (*TABLE1_FIELDS, *TABLE2_FIELDS)
        if field != "fuel_intensity_L_per_100km"
    }
    values["fuel_intensity_L_per_100km"] = format_metric_value(
        _fuel_intensity(metrics),
        METRIC_DIGITS["fuel_intensity_L_per_100km"],
    )
    return ReportAlgorithmRow(
        algorithm=algorithm,
        label=REPORT_ALGORITHM_LABELS[algorithm],
        completed=True,
        values=values,
    )


def _session_map(request: EvaluationReportRequest) -> dict[str, str | None]:
    mapped = {algorithm: None for algorithm in REPORT_ALGORITHMS}
    for run in request.runs:
        mapped[run.algorithm] = run.session_id
    return mapped


def build_report_rows(
    request: EvaluationReportRequest,
    metrics_by_algorithm: dict[str, dict[str, Any] | None],
) -> list[ReportAlgorithmRow]:
    rows: list[ReportAlgorithmRow] = []
    for algorithm in REPORT_ALGORITHMS:
        metrics = metrics_by_algorithm.get(algorithm)
        if metrics is None or metrics.get("finished") is not True:
            rows.append(_blank_row(algorithm))
            continue
        actual = metrics.get("algorithm")
        if actual != algorithm:
            raise AppError(
                code="ALGORITHM_SESSION_MISMATCH",
                message=(
                    f"session algorithm is {actual!r}, expected {algorithm!r}"
                ),
                status_code=400,
            )
        rows.append(_completed_row(algorithm, metrics))
    if not any(row.completed for row in rows):
        raise AppError(
            code="NO_FINAL_EVALUATION_AVAILABLE",
            message="当前场景暂无已完成的终态评估结果",
            status_code=409,
        )
    return rows


def build_download_filename(scenario: EvaluationReportScenario | None) -> str:
    if scenario is None:
        return "管控评估结果.pdf"
    scene = format_scenario_label(scenario.scenario_preset_id)
    period = format_period_label(scenario.period)
    window = format_time_window(
        scenario.period,
        scenario.window_start_seconds,
        scenario.duration_seconds,
    ).replace(":", "-")
    return f"{scene}_{period}_{window}_管控评估结果.pdf"


def build_content_disposition(filename: str) -> str:
    encoded = quote(filename, safe="")
    return (
        f'attachment; filename="{ASCII_FALLBACK_FILENAME}"; '
        f"filename*=UTF-8''{encoded}"
    )


def _ensure_cid_fonts() -> str:
    global _CID_FONTS_REGISTERED
    if not _CID_FONTS_REGISTERED:
        pdfmetrics.registerFont(UnicodeCIDFont(BODY_FONT))
        _CID_FONTS_REGISTERED = True
    return BODY_FONT


def _cell_style(font_name: str, size: float, leading: float) -> ParagraphStyle:
    return ParagraphStyle(
        name=f"eval-cell-{font_name}-{size}-{leading}",
        fontName=font_name,
        fontSize=size,
        leading=leading,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#222222"),
    )


def _title_style(font_name: str) -> ParagraphStyle:
    return ParagraphStyle(
        name="eval-title",
        fontName=font_name,
        fontSize=11,
        leading=16,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#111111"),
        spaceAfter=8,
    )


def _footnote_style(font_name: str) -> ParagraphStyle:
    return ParagraphStyle(
        name="eval-footnote",
        fontName=font_name,
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#555555"),
        alignment=TA_CENTER,
    )


def _table_style(font_name: str) -> TableStyle:
    return TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#222222")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("LINEABOVE", (0, 0), (-1, 0), 1.15, colors.black),
            ("LINEBELOW", (0, 0), (-1, 0), 0.45, colors.black),
            ("LINEBELOW", (0, -1), (-1, -1), 1.15, colors.black),
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ]
    )


def _header_cells(
    headers: tuple[str, ...],
    style: ParagraphStyle,
) -> list[Paragraph]:
    return [Paragraph(text, style) for text in headers]


def _value_cells(
    row: ReportAlgorithmRow,
    fields: tuple[str, ...],
    style: ParagraphStyle,
    emphasis_style: ParagraphStyle,
) -> list[Paragraph]:
    cell_style = emphasis_style if row.algorithm == "cov2x" else style
    return [Paragraph(row.label, cell_style)] + [
        Paragraph(row.values[field], cell_style) for field in fields
    ]


def generate_evaluation_report_pdf(
    title1: str,
    title2: str,
    rows: list[ReportAlgorithmRow],
) -> bytes:
    font_name = _ensure_cid_fonts()
    header_style = _cell_style(font_name, 7.5, 10)
    body_style = _cell_style(font_name, 8, 11)
    emphasis_style = _cell_style(font_name, 8.5, 11)
    usable_width = PAGE_WIDTH - 2 * HORIZONTAL_MARGIN

    table1_widths = [
        usable_width * ratio
        for ratio in (0.118, 0.112, 0.100, 0.112, 0.108, 0.108, 0.104, 0.080, 0.080, 0.078)
    ]
    table2_widths = [
        usable_width * ratio for ratio in (0.18, 0.20, 0.20, 0.22, 0.20)
    ]

    table1_data = [_header_cells(TABLE1_HEADERS, header_style)] + [
        _value_cells(row, TABLE1_FIELDS, body_style, emphasis_style) for row in rows
    ]
    table2_data = [_header_cells(TABLE2_HEADERS, header_style)] + [
        _value_cells(row, TABLE2_FIELDS, body_style, emphasis_style) for row in rows
    ]

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=HORIZONTAL_MARGIN,
        rightMargin=HORIZONTAL_MARGIN,
        topMargin=18 * mm,
        bottomMargin=16 * mm,
        title=title1,
        author="CityPulse",
    )
    table1 = Table(table1_data, colWidths=table1_widths, repeatRows=0)
    table1.setStyle(_table_style(font_name))
    table2 = Table(table2_data, colWidths=table2_widths, repeatRows=0)
    table2.setStyle(_table_style(font_name))
    document.build(
        [
            Paragraph(title1, _title_style(font_name)),
            table1,
            Spacer(1, 14 * mm),
            Paragraph(title2, _title_style(font_name)),
            table2,
            Spacer(1, 8 * mm),
            Paragraph(FOOTNOTE_TEXT, _footnote_style(font_name)),
        ]
    )
    return buffer.getvalue()


class EvaluationReportService:
    def __init__(self, simulation_service: SimulationService) -> None:
        self._simulation_service = simulation_service

    def _read_metrics(self, algorithm: str, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        try:
            payload = self._simulation_service.get_metrics(session_id)
        except Exception as exc:
            logger.warning(
                "evaluation report skipped %s session %s: %s",
                algorithm,
                session_id,
                exc,
            )
            return None
        if not isinstance(payload, dict):
            logger.warning(
                "evaluation report skipped %s session %s: invalid metrics payload",
                algorithm,
                session_id,
            )
            return None
        return payload

    def build_report_rows(self, request: EvaluationReportRequest) -> list[ReportAlgorithmRow]:
        metrics_by_algorithm = {
            algorithm: self._read_metrics(algorithm, session_id)
            for algorithm, session_id in _session_map(request).items()
        }
        return build_report_rows(request, metrics_by_algorithm)

    def build_pdf(self, request: EvaluationReportRequest) -> tuple[str, bytes]:
        rows = self.build_report_rows(request)
        title1 = build_report_title(1, request.scenario, "管控算法通行效率对比")
        title2 = build_report_title(2, request.scenario, "管控算法其他指标对比")
        pdf_bytes = generate_evaluation_report_pdf(title1, title2, rows)
        return build_download_filename(request.scenario), pdf_bytes
