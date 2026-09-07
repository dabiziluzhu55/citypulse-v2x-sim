"""九区域OD导出：读取TAZ与已生成OD报告，输出CSV/JSON/热力图"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.exceptions import AppError

logger = logging.getLogger(__name__)

EXPECTED_ZONE_IDS = tuple(f"zone_{index}" for index in range(1, 10))
HEATMAP_ZONE_LABELS = tuple(f"TAZ {index}" for index in range(1, 10))
EXPECTED_INTERSECTION_COUNT = 20
OFFICIAL_DEMANDS_RELATIVE = (
    "data/maps/sumo/official/traffic/official_traffic_demands.json"
)


@dataclass(frozen=True)
class OdExportArtifacts:
    csv_name: str
    csv_bytes: bytes
    taz_json_name: str
    taz_json_bytes: bytes
    heatmap_name: str
    heatmap_bytes: bytes
    relative_sources: dict[str, str]


def write_od_bundle(
    *,
    project_root: Path,
    generated_dir: Path,
    period: str,
    window_start_seconds: float,
    duration_seconds: float,
    output_dir: Path,
) -> OdExportArtifacts:
    """生成od/ 目录内容并返回字节与相对来源说明"""

    zones = load_and_validate_od_zones(project_root / OFFICIAL_DEMANDS_RELATIVE)
    report, report_rel, csv_rel = load_od_report(
        generated_dir=generated_dir,
        period=period,
    )
    matrix = extract_ordered_matrix(report, period=period)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_name = f"od_matrix_{period}.csv"
    taz_json_name = "taz_9_zones.json"
    heatmap_name = f"od_heatmap_{period}.png"

    csv_text = render_od_csv(matrix)
    csv_path = output_dir / csv_name
    csv_path.write_text(csv_text, encoding="utf-8")

    taz_payload = build_taz_metadata(
        zones=zones,
        period=period,
        report=report,
        report_relative_path=report_rel,
        csv_relative_path=csv_rel,
        window_start_seconds=window_start_seconds,
        duration_seconds=duration_seconds,
    )
    taz_text = json.dumps(taz_payload, ensure_ascii=False, indent=2) + "\n"
    taz_path = output_dir / taz_json_name
    taz_path.write_text(taz_text, encoding="utf-8")

    heatmap_bytes = render_od_heatmap_png(
        matrix=matrix,
        zones=zones,
        period=period,
        diagonal_policy=str(report.get("diagonal_policy", "")),
        window_start_seconds=window_start_seconds,
        duration_seconds=duration_seconds,
        unit=str(report.get("unit", "pcu")),
    )
    heatmap_path = output_dir / heatmap_name
    heatmap_path.write_bytes(heatmap_bytes)

    return OdExportArtifacts(
        csv_name=csv_name,
        csv_bytes=csv_text.encode("utf-8"),
        taz_json_name=taz_json_name,
        taz_json_bytes=taz_text.encode("utf-8"),
        heatmap_name=heatmap_name,
        heatmap_bytes=heatmap_bytes,
        relative_sources={
            "official_demands": OFFICIAL_DEMANDS_RELATIVE,
            "od_report": report_rel,
            "od_matrix_csv": csv_rel,
        },
    )


def load_and_validate_od_zones(demands_path: Path) -> dict[str, list[str]]:
    if not demands_path.is_file():
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message=f"Official traffic demands file missing: {OFFICIAL_DEMANDS_RELATIVE}",
            status_code=422,
        )
    try:
        payload = json.loads(demands_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message=f"Failed to parse official traffic demands: {exc}",
            status_code=422,
        ) from exc

    raw_zones = payload.get("od_zones")
    if not isinstance(raw_zones, Mapping) or not raw_zones:
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message="official_traffic_demands.json missing top-level od_zones.",
            status_code=422,
        )

    zones: dict[str, list[str]] = {}
    for zone_id in EXPECTED_ZONE_IDS:
        if zone_id not in raw_zones:
            raise AppError(
                code="SCENARIO_EXPORT_FAILED",
                message=f"od_zones missing required zone {zone_id!r}.",
                status_code=422,
            )
        intersections = raw_zones[zone_id]
        if not isinstance(intersections, list) or not intersections:
            raise AppError(
                code="SCENARIO_EXPORT_FAILED",
                message=f"od_zones[{zone_id!r}] must be a non-empty intersection list.",
                status_code=422,
            )
        zones[zone_id] = [str(item) for item in intersections]

    extra = sorted(set(raw_zones) - set(EXPECTED_ZONE_IDS))
    if extra:
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message=f"od_zones contains unexpected zones: {extra}",
            status_code=422,
        )

    seen: dict[str, str] = {}
    for zone_id, intersections in zones.items():
        for intersection_id in intersections:
            if intersection_id in seen:
                raise AppError(
                    code="SCENARIO_EXPORT_FAILED",
                    message=(
                        f"Intersection {intersection_id!r} belongs to both "
                        f"{seen[intersection_id]!r} and {zone_id!r}."
                    ),
                    status_code=422,
                )
            seen[intersection_id] = zone_id

    if len(seen) != EXPECTED_INTERSECTION_COUNT:
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message=(
                f"od_zones must cover exactly {EXPECTED_INTERSECTION_COUNT} "
                f"intersections, got {len(seen)}."
            ),
            status_code=422,
        )
    return zones


def load_od_report(
    *,
    generated_dir: Path,
    period: str,
) -> tuple[dict[str, Any], str, str]:
    manifest_path = generated_dir / "manifests" / "traffic_manifest.json"
    report_rel = f"reports/traffic_od_{period}.json"
    csv_rel = f"reports/traffic_od_{period}.csv"

    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppError(
                code="SCENARIO_EXPORT_FAILED",
                message=f"Failed to parse traffic_manifest.json: {exc}",
                status_code=422,
            ) from exc
        scenario_key = f"global_{period}"
        scenarios = manifest.get("scenarios", {})
        scenario = scenarios.get(scenario_key) if isinstance(scenarios, Mapping) else None
        if isinstance(scenario, Mapping):
            if scenario.get("od_report"):
                report_rel = str(scenario["od_report"]).lstrip("/")
            if scenario.get("od_matrix_csv"):
                csv_rel = str(scenario["od_matrix_csv"]).lstrip("/")

    report_path = generated_dir / report_rel
    if not report_path.is_file():
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message=(
                f"OD report missing for period {period!r}: "
                f"data/maps/sumo/generated/{report_rel}"
            ),
            status_code=422,
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message=f"Failed to parse OD report {report_rel}: {exc}",
            status_code=422,
        ) from exc
    if not isinstance(report, Mapping):
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message=f"OD report {report_rel} root must be an object.",
            status_code=422,
        )
    return dict(report), report_rel, csv_rel


def extract_ordered_matrix(
    report: Mapping[str, Any],
    *,
    period: str,
) -> list[list[float]]:
    period_id = str(report.get("period_id", ""))
    if period_id and period_id != period:
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message=(
                f"OD report period_id={period_id!r} does not match export period "
                f"{period!r}."
            ),
            status_code=422,
        )
    unit = str(report.get("unit", "")).lower()
    if unit and unit != "pcu":
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message=f"OD report unit must be pcu, got {unit!r}.",
            status_code=422,
        )
    matrix = report.get("matrix_pcu")
    if not isinstance(matrix, list) or len(matrix) != len(EXPECTED_ZONE_IDS):
        raise AppError(
            code="SCENARIO_EXPORT_FAILED",
            message="OD matrix_pcu must be a complete 9x9 matrix.",
            status_code=422,
        )
    ordered: list[list[float]] = []
    for row in matrix:
        if not isinstance(row, list) or len(row) != len(EXPECTED_ZONE_IDS):
            raise AppError(
                code="SCENARIO_EXPORT_FAILED",
                message="OD matrix_pcu must be a complete 9x9 matrix.",
                status_code=422,
            )
        ordered.append([float(value) for value in row])
    return ordered


def render_od_csv(matrix: Sequence[Sequence[float]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["origin_zone/destination_zone", *EXPECTED_ZONE_IDS])
    for zone_id, row in zip(EXPECTED_ZONE_IDS, matrix):
        writer.writerow([zone_id, *[_format_pcu(value) for value in row]])
    return buffer.getvalue()


def build_taz_metadata(
    *,
    zones: Mapping[str, Sequence[str]],
    period: str,
    report: Mapping[str, Any],
    report_relative_path: str,
    csv_relative_path: str,
    window_start_seconds: float,
    duration_seconds: float,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "zone_count": len(EXPECTED_ZONE_IDS),
        "zone_ids": list(EXPECTED_ZONE_IDS),
        "zones": [
            {
                "zone_id": zone_id,
                "intersection_ids": list(zones[zone_id]),
            }
            for zone_id in EXPECTED_ZONE_IDS
        ],
        "od_unit": "pcu",
        "period": period,
        "od_generation_method": (
            "Typical inter-zonal OD derived from official traffic counts and "
            "complete validated routes; first/last official count locations assign "
            "origin/destination zones; intermediate zones are ignored."
        ),
        "diagonal_policy": report.get(
            "diagonal_policy", "excluded_and_written_as_zero"
        ),
        "same_zone_trips": (
            "Intra-zone trips are excluded from the OD matrix and written as 0 "
            "on the diagonal; excluded mass is recorded in OD report fields "
            "excluded_intra_zone_pcu / excluded_intra_zone_vehicle_count."
        ),
        "coverage_note": (
            "This OD matrix is a typical OD based on final complete routes. "
            "It is not a unique true resident travel OD."
        ),
        "time_scope": {
            "od_matrix_scope": "full_period",
            "export_window_start_seconds": float(window_start_seconds),
            "export_window_duration_seconds": float(duration_seconds),
            "note": (
                "The OD matrix covers the full traffic period "
                f"{period!r}. It is NOT sliced to the export window "
                f"[{window_start_seconds:g}, "
                f"{window_start_seconds + duration_seconds:g})."
            ),
        },
        "data_sources": {
            "od_zones": OFFICIAL_DEMANDS_RELATIVE,
            "od_report": f"data/maps/sumo/generated/{report_relative_path}",
            "od_matrix_csv": f"data/maps/sumo/generated/{csv_relative_path}",
        },
        "interzonal_pcu": report.get("interzonal_pcu"),
        "excluded_intra_zone_pcu": report.get("excluded_intra_zone_pcu"),
    }


def render_od_heatmap_png(
    *,
    matrix: Sequence[Sequence[float]],
    zones: Mapping[str, Sequence[str]],
    period: str,
    diagonal_policy: str,
    window_start_seconds: float,
    duration_seconds: float,
    unit: str,
) -> bytes:
    try:
        import numpy as np
    except ImportError as exc:
        raise AppError(
            code="SCENARIO_EXPORT_DEPENDENCY_MISSING",
            message=(
                "OD heatmap dependencies are unavailable. Run "
                "'python -m pip install -r backend/requirements.txt' "
                f"with the backend Python environment. ({exc})"
            ),
            status_code=503,
        ) from exc

    values = np.asarray(matrix, dtype=float)
    try:
        return _render_od_heatmap_with_matplotlib(
            values=values,
            unit=unit,
        )
    except Exception as exc:
        logger.warning(
            "matplotlib OD heatmap unavailable (%s); falling back to Pillow",
            exc,
        )
        try:
            return _render_od_heatmap_with_pillow(values=values, unit=unit)
        except (ImportError, OSError, ValueError) as fallback_exc:
            raise AppError(
                code="SCENARIO_EXPORT_DEPENDENCY_MISSING",
                message=(
                    "OD heatmap dependencies are unavailable. Run "
                    "'python -m pip install -r backend/requirements.txt' "
                    "with the backend Python environment. "
                    f"(matplotlib: {exc}; pillow: {fallback_exc})"
                ),
                status_code=503,
            ) from fallback_exc


def _render_od_heatmap_with_matplotlib(*, values, unit: str) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    font_family = _resolve_plot_font_family()
    if font_family:
        plt.rcParams["font.sans-serif"] = [font_family, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

    fig_height = 10.5
    fig, ax = plt.subplots(figsize=(11.0, fig_height))
    image = ax.imshow(values, cmap="YlOrRd", aspect="equal")
    ax.set_xticks(range(len(HEATMAP_ZONE_LABELS)))
    ax.set_yticks(range(len(HEATMAP_ZONE_LABELS)))
    ax.set_xticklabels(HEATMAP_ZONE_LABELS, rotation=45, ha="right", fontsize=12)
    ax.set_yticklabels(HEATMAP_ZONE_LABELS, fontsize=12)
    ax.tick_params(axis="x", pad=12)
    ax.tick_params(axis="y", pad=10)
    ax.set_xlabel("终点交通分析区", fontsize=13, labelpad=32)
    ax.set_ylabel("起点交通分析区", fontsize=13, labelpad=32)

    fig.subplots_adjust(left=0.18, right=0.78, top=0.92, bottom=0.36)
    colorbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.06)
    colorbar.ax.tick_params(labelsize=13)
    colorbar.set_label(f"PCU ({unit})", labelpad=12, fontsize=13)

    max_value = float(values.max()) if values.size else 0.0
    threshold = max_value * 0.55 if max_value > 0 else 0.0
    for row_index in range(values.shape[0]):
        for col_index in range(values.shape[1]):
            value = values[row_index, col_index]
            ax.text(
                col_index,
                row_index,
                _format_pcu(value),
                ha="center",
                va="center",
                fontsize=12,
                color="white" if value >= threshold and value > 0 else "black",
            )

    caption = (
        "典型出行需求（OD矩阵说明）\n"
        "1) 单元格(i,j)表示从起点交通分析区（TAZ） i到终点交通分析区（TAZ） j的典型出行量\n"
        "2) Y轴=起点TAZ，X轴=终点TAZ；区域顺序为1-9\n"
        "3) 数值单位为PCU\n"
        "4) 时间范围：所选交通时段的全时段OD\n"
        "说明：OD矩阵表示起点TAZ到终点TAZ的典型出行量；同区域行程不计入矩阵（对角线为0）"
    )
    fig.text(
        0.02,
        0.02,
        caption,
        ha="left",
        va="bottom",
        fontsize=12,
        wrap=True,
        fontproperties=font_manager.FontProperties(family=font_family or "DejaVu Sans"),
    )
    buffer = io.BytesIO()
    try:
        fig.savefig(buffer, format="png", dpi=140, bbox_inches="tight", pad_inches=0.4)
    finally:
        plt.close(fig)
    return buffer.getvalue()


def _render_od_heatmap_with_pillow(*, values, unit: str) -> bytes:
    """matplotlib 不可用时用 Pillow 生成同样必需的 OD 热力图 PNG。"""

    from PIL import Image, ImageDraw

    rows, cols = int(values.shape[0]), int(values.shape[1])
    max_value = float(values.max()) if values.size else 0.0
    threshold = max_value * 0.55 if max_value > 0 else 0.0
    font_path = _resolve_pillow_font_path()
    title_font = _load_pillow_font(font_path, 28)
    label_font = _load_pillow_font(font_path, 16)
    cell_font = _load_pillow_font(font_path, 18)
    caption_font = _load_pillow_font(font_path, 16)

    left, top, right, bottom = 150, 80, 160, 220
    cell = 72
    width = left + cols * cell + right
    height = top + rows * cell + bottom
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    draw.text((left, 24), "典型出行需求（OD矩阵）", fill=(20, 20, 20), font=title_font)
    for row_index in range(rows):
        for col_index in range(cols):
            value = float(values[row_index, col_index])
            color = _ylorrd_color(value, max_value)
            x0 = left + col_index * cell
            y0 = top + row_index * cell
            draw.rectangle((x0, y0, x0 + cell, y0 + cell), fill=color, outline=(255, 255, 255))
            text = _format_pcu(value)
            text_color = (255, 255, 255) if value >= threshold and value > 0 else (20, 20, 20)
            tx, ty = _center_text(draw, text, cell_font, x0, y0, cell, cell)
            draw.text((tx, ty), text, fill=text_color, font=cell_font)

    for index, label in enumerate(HEATMAP_ZONE_LABELS):
        x0 = left + index * cell
        y0 = top + rows * cell
        tx, ty = _center_text(draw, label, label_font, x0, y0, cell, 36)
        draw.text((tx, ty + 4), label, fill=(40, 40, 40), font=label_font)
        y1 = top + index * cell
        bbox = draw.textbbox((0, 0), label, font=label_font)
        draw.text(
            (left - 12 - (bbox[2] - bbox[0]), y1 + (cell - (bbox[3] - bbox[1])) / 2),
            label,
            fill=(40, 40, 40),
            font=label_font,
        )

    draw.text(
        (left, top + rows * cell + 44),
        "终点交通分析区",
        fill=(40, 40, 40),
        font=label_font,
    )
    draw.text((24, top - 8), "起点交通分析区", fill=(40, 40, 40), font=label_font)

    bar_x = left + cols * cell + 24
    bar_y = top
    bar_h = rows * cell
    for pixel in range(bar_h):
        ratio = 1.0 - (pixel / max(bar_h - 1, 1))
        color = _ylorrd_color(ratio * max_value, max_value)
        draw.line((bar_x, bar_y + pixel, bar_x + 18, bar_y + pixel), fill=color)
    draw.text((bar_x, bar_y + bar_h + 8), f"PCU ({unit})", fill=(40, 40, 40), font=label_font)
    draw.text((bar_x + 24, bar_y - 4), _format_pcu(max_value), fill=(40, 40, 40), font=label_font)
    draw.text((bar_x + 24, bar_y + bar_h - 18), "0", fill=(40, 40, 40), font=label_font)

    caption = (
        "1) 单元格(i,j)表示从起点TAZ i到终点TAZ j的典型出行量；单位为PCU\n"
        "2) Y轴=起点TAZ，X轴=终点TAZ；区域顺序为1-9；同区域行程不计入（对角线为0）"
    )
    draw.multiline_text(
        (24, height - 96),
        caption,
        fill=(50, 50, 50),
        font=caption_font,
        spacing=6,
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _ylorrd_color(value: float, max_value: float) -> tuple[int, int, int]:
    if max_value <= 0:
        return (255, 255, 204)
    ratio = max(0.0, min(1.0, float(value) / max_value))
    stops = (
        (255, 255, 204),
        (254, 217, 118),
        (254, 153, 41),
        (217, 95, 14),
        (153, 52, 4),
    )
    scaled = ratio * (len(stops) - 1)
    index = min(int(scaled), len(stops) - 2)
    frac = scaled - index
    start, end = stops[index], stops[index + 1]
    return (
        int(start[0] + (end[0] - start[0]) * frac),
        int(start[1] + (end[1] - start[1]) * frac),
        int(start[2] + (end[2] - start[2]) * frac),
    )


def _center_text(draw, text: str, font, x0: int, y0: int, width: int, height: int):
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    return x0 + (width - text_w) / 2, y0 + (height - text_h) / 2


def _resolve_pillow_font_path() -> Path | None:
    candidates = (
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    return next((path for path in candidates if path.is_file()), None)


def _load_pillow_font(font_path: Path | None, size: int):
    from PIL import ImageFont

    if font_path is None:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(str(font_path), size=size, index=2)
    except OSError:
        try:
            return ImageFont.truetype(str(font_path), size=size)
        except OSError:
            return ImageFont.load_default()


def _resolve_plot_font_family() -> str | None:
    """Prefer a CJK-capable font when available; otherwise fall back to DejaVu."""

    from matplotlib import font_manager

    candidates = (
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "SimHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    )
    available = {font.name for font in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            return name
    return None


def _format_pcu(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"
