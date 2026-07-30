"""Evaluate leakage-free persistence, moving, and historical baselines for stage 1."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np


def _metrics(prediction: np.ndarray, actual: np.ndarray) -> dict[str, float]:
    error = prediction - actual
    absolute = np.abs(error)
    denominator = np.abs(actual).sum()
    # ``vehicle_count`` is integer-valued before normalization.  A 0 count
    # can become a tiny non-zero value after the float32 normalize/de-normalize
    # round trip, so use the count scale rather than a numerical epsilon.
    nonzero = np.abs(actual) >= 0.5
    smape_denominator = np.abs(prediction) + np.abs(actual)
    smape_terms = np.divide(
        2.0 * absolute,
        smape_denominator,
        out=np.zeros_like(absolute, dtype=np.float64),
        where=smape_denominator > 1e-9,
    )
    return {
        "mae": float(absolute.mean()),
        "rmse": float(np.sqrt(np.square(error).mean())),
        "mape": float((absolute[nonzero] / np.abs(actual[nonzero])).mean()) if nonzero.any() else 0.0,
        "smape": float(smape_terms.mean()),
        "wmape": float(absolute.sum() / denominator) if denominator > 1e-9 else 0.0,
    }


def _raw(values: np.ndarray, mean: float, std: float) -> np.ndarray:
    return values.astype(np.float64) * std + mean


def evaluate(dataset_dir: Path, output: Path) -> list[dict[str, object]]:
    metadata = json.loads((dataset_dir / "metadata.json").read_text(encoding="utf-8"))
    normalization = metadata["normalization"]
    mean = float(normalization["target_mean"])
    std = float(normalization["target_std"])
    target_feature_index = metadata.get("target_feature_index")
    if target_feature_index is None:
        raise ValueError("target must be included in historical features for persistence baseline.")
    train = np.load(dataset_dir / "train.npz")
    # Mean per lane is fit on training histories only. Windows overlap, but
    # they never introduce validation/test observations into this statistic.
    lane_history_mean = train["x"][:, target_feature_index, :, :].mean(axis=(0, 1))
    rows: list[dict[str, object]] = []
    for split in ("validation", "test_in_distribution", "test_extrapolation"):
        data = np.load(dataset_dir / f"{split}.npz")
        for horizon in (1, 6, 12):
            actual = _raw(data["y"][:, horizon - 1, :], mean, std)
            candidates = {
                "persistence": data["x"][:, target_feature_index, -1, :],
                # The model's input window contains the preceding 60 seconds
                # (12 observations at 5-second intervals), so this average is
                # available at forecast time and uses no future samples.
                "moving_average": data["x"][:, target_feature_index, :, :].mean(axis=1),
                "historical_average": np.broadcast_to(lane_history_mean, actual.shape),
            }
            for model, prediction in candidates.items():
                row = {
                    "split": split,
                    "model": model,
                    "horizon_steps": horizon,
                    "horizon_seconds": horizon * float(metadata["interval_seconds"]),
                    "sample_count": int(actual.shape[0]),
                    "lane_count": int(actual.shape[1]),
                    **_metrics(_raw(prediction, mean, std), actual),
                }
                rows.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    output.with_suffix(".json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate stage-1 prediction baselines from prepared NPZ data.")
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    for row in evaluate(args.dataset_dir, args.output):
        print(
            f"{row['split']:22s} {row['model']:16s} h={row['horizon_steps']:2d} "
            f"MAE={row['mae']:.4f} RMSE={row['rmse']:.4f} WMAPE={row['wmape']:.4f}"
        )


if __name__ == "__main__":
    main()
