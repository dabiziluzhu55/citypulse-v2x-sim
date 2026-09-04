#!/usr/bin/env python3
"""离线导出九区域 OD 矩阵热力图，无需启动前后端。

从 backend/app/services/scenario_export_service.py 的 OD 导出分支抽离：
直接读取已生成的 SUMO 交通报告，复用 od_export 的热力图绘制逻辑写出 PNG。

Examples:

    python scripts/export_od_heatmap.py
    python scripts/export_od_heatmap.py --period morning_peak
    python scripts/export_od_heatmap.py --period all --output-dir outputs/od_export
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)

OD_EXPORT_PRESET_ID = "xiongan_20"
SUPPORTED_PERIODS = ("morning_peak", "off_peak", "evening_peak")
EXPECTED_ZONE_IDS = tuple(f"zone_{index}" for index in range(1, 10))
HEATMAP_ZONE_LABELS = tuple(f"TAZ {index}" for index in range(1, 10))
EXPECTED_INTERSECTION_COUNT = 20
OFFICIAL_DEMANDS_RELATIVE = (
    "data/maps/sumo/official/traffic/official_traffic_demands.json"
)
DEFAULT_GENERATED_DIR = REPOSITORY_ROOT / "data" / "maps" / "sumo" / "generated"
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "outputs" / "od_export"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--period",
        choices=(*SUPPORTED_PERIODS, "all"),
        default="all",
        help="交通时段；all 表示早高峰/平峰/晚高峰各导出一张",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="OD 产物输出目录（默认 outputs/od_export）",
    )
    parser.add_argument(
        "--generated-dir",
        type=Path,
        default=DEFAULT_GENERATED_DIR,
        help="SUMO 已生成产物目录（含 reports/traffic_od_*.json）",
    )
    return parser.parse_args()


def resolve_periods(period: str) -> tuple[str, ...]:
    if period == "all":
        return SUPPORTED_PERIODS
    return (period,)


def load_and_validate_od_zones(demands_path: Path) -> dict[str, list[str]]:
    if not demands_path.is_file():
        raise RuntimeError(f"Official traffic demands file missing: {OFFICIAL_DEMANDS_RELATIVE}")
    try:
        payload = json.loads(demands_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to parse official traffic demands: {exc}") from exc

    raw_zones = payload.get("od_zones")
    if not isinstance(raw_zones, Mapping) or not raw_zones:
        raise RuntimeError("official_traffic_demands.json missing top-level od_zones.")

    zones: dict[str, list[str]] = {}
    for zone_id in EXPECTED_ZONE_IDS:
        if zone_id not in raw_zones:
            raise RuntimeError(f"od_zones missing required zone {zone_id!r}.")
        intersections = raw_zones[zone_id]
        if not isinstance(intersections, list) or not intersections:
            raise RuntimeError(f"od_zones[{zone_id!r}] must be a non-empty intersection list.")
        zones[zone_id] = [str(item) for item in intersections]

    extra = sorted(set(raw_zones) - set(EXPECTED_ZONE_IDS))
    if extra:
        raise RuntimeError(f"od_zones contains unexpected zones: {extra}")

    seen: dict[str, str] = {}
    for zone_id, intersections in zones.items():
        for intersection_id in intersections:
            if intersection_id in seen:
                raise RuntimeError(
                    f"Intersection {intersection_id!r} belongs to both "
                    f"{seen[intersection_id]!r} and {zone_id!r}."
                )
            seen[intersection_id] = zone_id

    if len(seen) != EXPECTED_INTERSECTION_COUNT:
        raise RuntimeError(
            f"od_zones must cover exactly {EXPECTED_INTERSECTION_COUNT} "
            f"intersections, got {len(seen)}."
        )
    return zones


def load_od_report(
    *,
    generated_dir: Path,
    period: str,
) -> tuple[dict[str, Any], str]:
    manifest_path = generated_dir / "manifests" / "traffic_manifest.json"
    report_rel = f"reports/traffic_od_{period}.json"

    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Failed to parse traffic_manifest.json: {exc}") from exc
        scenario_key = f"global_{period}"
        scenarios = manifest.get("scenarios", {})
        scenario = scenarios.get(scenario_key) if isinstance(scenarios, Mapping) else None
        if isinstance(scenario, Mapping) and scenario.get("od_report"):
            report_rel = str(scenario["od_report"]).lstrip("/")

    report_path = generated_dir / report_rel
    if not report_path.is_file():
        raise RuntimeError(
            f"OD report missing for period {period!r}: "
            f"data/maps/sumo/generated/{report_rel}"
        )
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to parse OD report {report_rel}: {exc}") from exc
    if not isinstance(report, Mapping):
        raise RuntimeError(f"OD report {report_rel} root must be an object.")
    return dict(report), report_rel


def extract_ordered_matrix(
    report: Mapping[str, Any],
    *,
    period: str,
) -> list[list[float]]:
    period_id = str(report.get("period_id", ""))
    if period_id and period_id != period:
        raise RuntimeError(
            f"OD report period_id={period_id!r} does not match export period {period!r}."
        )
    unit = str(report.get("unit", "")).lower()
    if unit and unit != "pcu":
        raise RuntimeError(f"OD report unit must be pcu, got {unit!r}.")
    matrix = report.get("matrix_pcu")
    if not isinstance(matrix, list) or len(matrix) != len(EXPECTED_ZONE_IDS):
        raise RuntimeError("OD matrix_pcu must be a complete 9x9 matrix.")
    ordered: list[list[float]] = []
    for row in matrix:
        if not isinstance(row, list) or len(row) != len(EXPECTED_ZONE_IDS):
            raise RuntimeError("OD matrix_pcu must be a complete 9x9 matrix.")
        ordered.append([float(value) for value in row])
    return ordered


def _format_pcu(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def render_od_csv(matrix: Sequence[Sequence[float]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["origin_zone/destination_zone", *EXPECTED_ZONE_IDS])
    for zone_id, row in zip(EXPECTED_ZONE_IDS, matrix):
        writer.writerow([zone_id, *[_format_pcu(value) for value in row]])
    return buffer.getvalue()


def _resolve_plot_font_family() -> str | None:
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


def render_od_heatmap_png(
    *,
    matrix: Sequence[Sequence[float]],
    period: str,
    unit: str,
) -> bytes:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        import numpy as np
    except (ImportError, OSError) as exc:
        raise RuntimeError(
            "OD heatmap dependencies are unavailable. Install matplotlib and numpy, "
            "for example: python -m pip install matplotlib numpy"
        ) from exc

    values = np.asarray(matrix, dtype=float)
    font_family = _resolve_plot_font_family()
    if font_family:
        plt.rcParams["font.sans-serif"] = [font_family, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(11.0, 10.5))
    image = ax.imshow(values, cmap="YlOrRd", aspect="equal")
    ax.set_xticks(range(len(HEATMAP_ZONE_LABELS)))
    ax.set_yticks(range(len(HEATMAP_ZONE_LABELS)))
    ax.set_xticklabels(HEATMAP_ZONE_LABELS, rotation=45, ha="right")
    ax.set_yticklabels(HEATMAP_ZONE_LABELS)
    ax.set_xlabel("终点交通分析区")
    ax.set_ylabel("起点交通分析区")

    fig.subplots_adjust(left=0.12, right=0.78, top=0.92, bottom=0.28)
    colorbar = fig.colorbar(image, ax=ax, fraction=0.046, pad=0.06)
    colorbar.set_label(f"PCU ({unit})", labelpad=12)

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
                fontsize=7,
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
        fontsize=7.5,
        wrap=True,
        fontproperties=font_manager.FontProperties(family=font_family or "DejaVu Sans"),
    )
    buffer = io.BytesIO()
    try:
        fig.savefig(buffer, format="png", dpi=140, bbox_inches="tight", pad_inches=0.4)
    finally:
        plt.close(fig)
    return buffer.getvalue()


def export_period(*, period: str, generated_dir: Path, output_dir: Path) -> Path:
    load_and_validate_od_zones(REPOSITORY_ROOT / OFFICIAL_DEMANDS_RELATIVE)
    report, report_rel = load_od_report(generated_dir=generated_dir, period=period)
    matrix = extract_ordered_matrix(report, period=period)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_name = f"od_matrix_{period}.csv"
    heatmap_name = f"od_heatmap_{period}.png"
    (output_dir / csv_name).write_text(render_od_csv(matrix), encoding="utf-8")

    heatmap_bytes = render_od_heatmap_png(
        matrix=matrix,
        period=period,
        unit=str(report.get("unit", "pcu")),
    )
    heatmap_path = output_dir / heatmap_name
    heatmap_path.write_bytes(heatmap_bytes)
    logger.info(
        "Exported %s OD heatmap for preset %s from %s: %s",
        period,
        OD_EXPORT_PRESET_ID,
        report_rel,
        heatmap_path,
    )
    return heatmap_path


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    generated_dir = args.generated_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    if not generated_dir.is_dir():
        logger.error("Generated directory does not exist: %s", generated_dir)
        return 1

    written: list[Path] = []
    try:
        for period in resolve_periods(args.period):
            written.append(
                export_period(
                    period=period,
                    generated_dir=generated_dir,
                    output_dir=output_dir,
                )
            )
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    print("OD heatmap exported:")
    for path in written:
        print(f"  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
