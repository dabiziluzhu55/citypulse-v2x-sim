"""Simple non-neural traffic prediction baselines."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from .dataset import LaneSeries, load_lane_series


@dataclass(frozen=True)
class MetricRow:
    target: str
    model: str
    horizon_steps: int
    horizon_seconds: float
    window_steps: int
    eval_points: int
    lane_count: int
    sample_count: int
    mae: float
    rmse: float
    mape: float
    wmape: float


def _mean(items: list[float]) -> float:
    return sum(items) / len(items) if items else 0.0


def _predict_persistence(series: LaneSeries, index: int, _window: int) -> list[float]:
    return list(series.values[index])


def _predict_moving_average(series: LaneSeries, index: int, window: int) -> list[float]:
    start = max(0, index - window + 1)
    rows = series.values[start : index + 1]
    return [_mean([row[lane] for row in rows]) for lane in range(len(series.lanes))]


def _predict_expanding_average(series: LaneSeries, index: int, _window: int) -> list[float]:
    rows = series.values[: index + 1]
    return [_mean([row[lane] for row in rows]) for lane in range(len(series.lanes))]


BASELINES = {
    "persistence": _predict_persistence,
    "moving_average": _predict_moving_average,
    "historical_average": _predict_expanding_average,
}


def evaluate_baseline(
    series: LaneSeries,
    model: str,
    horizon_steps: int,
    window_steps: int,
    test_ratio: float,
) -> MetricRow:
    if model not in BASELINES:
        raise ValueError(f"unknown baseline {model!r}")
    if horizon_steps < 1:
        raise ValueError("horizon_steps must be >= 1")
    if window_steps < 1:
        raise ValueError("window_steps must be >= 1")
    if not 0.0 < test_ratio < 1.0:
        raise ValueError("test_ratio must be between 0 and 1")

    predictor = BASELINES[model]
    start_index = max(window_steps - 1, int(len(series.times) * (1.0 - test_ratio)))
    end_index = len(series.times) - horizon_steps
    if start_index >= end_index:
        raise ValueError(
            f"not enough time points for horizon={horizon_steps}, window={window_steps}, "
            f"test_ratio={test_ratio}; got {len(series.times)} time points"
        )

    abs_errors: list[float] = []
    squared_errors: list[float] = []
    percentage_errors: list[float] = []
    actual_abs_sum = 0.0

    for index in range(start_index, end_index):
        prediction = predictor(series, index, window_steps)
        actual = series.values[index + horizon_steps]
        for lane_index, actual_value in enumerate(actual):
            error = prediction[lane_index] - actual_value
            abs_error = abs(error)
            abs_errors.append(abs_error)
            squared_errors.append(error * error)
            actual_abs_sum += abs(actual_value)
            if abs(actual_value) > 1e-9:
                percentage_errors.append(abs_error / abs(actual_value))

    sample_count = len(abs_errors)
    mae = _mean(abs_errors)
    rmse = math.sqrt(_mean(squared_errors))
    mape = _mean(percentage_errors)
    wmape = sum(abs_errors) / actual_abs_sum if actual_abs_sum > 1e-9 else 0.0

    return MetricRow(
        target=series.target,
        model=model,
        horizon_steps=horizon_steps,
        horizon_seconds=horizon_steps * series.interval_seconds,
        window_steps=window_steps,
        eval_points=end_index - start_index,
        lane_count=len(series.lanes),
        sample_count=sample_count,
        mae=mae,
        rmse=rmse,
        mape=mape,
        wmape=wmape,
    )


def run_baselines(args: argparse.Namespace) -> list[MetricRow]:
    rows: list[MetricRow] = []
    models = args.models or sorted(BASELINES)
    for target in args.targets:
        series = load_lane_series(args.input, target)
        for horizon in args.horizons:
            for model in models:
                rows.append(
                    evaluate_baseline(
                        series=series,
                        model=model,
                        horizon_steps=horizon,
                        window_steps=args.window_steps,
                        test_ratio=args.test_ratio,
                    )
                )
    return rows


def write_csv(path: Path, rows: list[MetricRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate simple traffic prediction baselines on lane CSV data."
    )
    parser.add_argument("--input", type=Path, required=True, help="Lane CSV from export_snapshots.")
    parser.add_argument(
        "--targets",
        nargs="+",
        default=["vehicle_count", "halting_count", "mean_speed"],
    )
    parser.add_argument("--horizons", nargs="+", type=int, default=[1, 6, 12])
    parser.add_argument("--window-steps", type=int, default=12)
    parser.add_argument("--test-ratio", type=float, default=0.3)
    parser.add_argument("--models", nargs="+", choices=sorted(BASELINES), default=None)
    parser.add_argument("--output", type=Path, default=Path("outputs/prediction/baseline_metrics.csv"))
    parser.add_argument("--json-output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    rows = run_baselines(args)
    write_csv(args.output, rows)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps([asdict(row) for row in rows], indent=2),
            encoding="utf-8",
        )

    print(f"Wrote {len(rows)} metric rows to {args.output}")
    for row in rows:
        print(
            f"{row.target:14s} {row.model:18s} "
            f"h={row.horizon_steps:>2d} ({row.horizon_seconds:>5.1f}s) "
            f"MAE={row.mae:.4f} RMSE={row.rmse:.4f} WMAPE={row.wmape:.4f}"
        )


if __name__ == "__main__":
    main()
