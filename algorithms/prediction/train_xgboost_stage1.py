"""Train a leakage-free multivariate XGBoost traffic-flow baseline.

The model sees only each lane's preceding history window (all selected SUMO
features plus a static lane index) and predicts vehicle_count at one future
horizon.  Training rows are deterministically sampled so this CPU baseline is
bounded even when the lane graph is large.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def _rows(x: np.ndarray, y: np.ndarray, *, max_rows: int | None, seed: int) -> tuple[np.ndarray, np.ndarray]:
    samples, _, _, lanes = x.shape
    total = samples * lanes
    rng = np.random.default_rng(seed)
    selected = np.arange(total) if max_rows is None or max_rows >= total else rng.choice(total, max_rows, replace=False)
    sample_index, lane_index = np.divmod(selected, lanes)
    # Advanced indexing yields (row, feature, history); flatten its final two axes.
    history = x[sample_index, :, :, lane_index].reshape(len(selected), -1)
    lane_feature = (lane_index / max(lanes - 1, 1)).astype(np.float32)[:, None]
    return np.concatenate((history, lane_feature), axis=1).astype(np.float32), y[sample_index, lane_index].astype(np.float32)


def _predict(model, x: np.ndarray, *, chunk_rows: int = 100_000) -> np.ndarray:
    samples, _, _, lanes = x.shape
    output = np.empty((samples, lanes), dtype=np.float32)
    for start in range(0, samples * lanes, chunk_rows):
        flat = np.arange(start, min(start + chunk_rows, samples * lanes))
        sample_index, lane_index = np.divmod(flat, lanes)
        history = x[sample_index, :, :, lane_index].reshape(len(flat), -1)
        lane_feature = (lane_index / max(lanes - 1, 1)).astype(np.float32)[:, None]
        output[sample_index, lane_index] = model.predict(np.concatenate((history, lane_feature), axis=1))
    return output


def _metrics(prediction: np.ndarray, actual: np.ndarray) -> dict[str, float]:
    error = prediction - actual
    absolute = np.abs(error)
    # Counts that are exactly zero can acquire float32 round-off after
    # de-normalization.  Mask on the original count scale for MAPE instead of
    # treating numerical residue as a real denominator.
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
        "wmape": float(absolute.sum() / np.abs(actual).sum()),
    }


def _evaluate_model(model, dataset_dir: Path, horizon_step: int) -> dict[str, object]:
    metadata = json.loads((dataset_dir / "metadata.json").read_text(encoding="utf-8"))
    if horizon_step != int(metadata["n_pred"]):
        raise ValueError("XGBoost baseline must use the prepared final forecast horizon.")
    normalization = metadata["normalization"]
    mean, std = float(normalization["target_mean"]), float(normalization["target_std"])
    result: dict[str, object] = {
        "model": "XGBoost multivariate lane-history baseline",
        "features": metadata["features"],
        "horizon_steps": horizon_step,
        "horizon_seconds": horizon_step * float(metadata["interval_seconds"]),
    }
    for split in ("validation", "test_in_distribution", "test_extrapolation"):
        data = np.load(dataset_dir / f"{split}.npz")
        actual = data["y"][:, horizon_step - 1, :] * std + mean
        prediction = _predict(model, data["x"]) * std + mean
        result[split] = _metrics(prediction, actual)
    return result


def evaluate_checkpoint(args: argparse.Namespace) -> dict[str, object]:
    try:
        from xgboost import XGBRegressor
    except ImportError as exc:
        raise RuntimeError("xgboost is required; install it in the v2x-ai-py310 environment.") from exc

    model_path = args.output_dir / "model.json"
    if not model_path.is_file():
        raise FileNotFoundError(f"evaluate-only requires {model_path}")
    model = XGBRegressor()
    model.load_model(model_path)
    result = _evaluate_model(model, args.dataset_dir, args.horizon_step)
    existing_metrics = args.output_dir / "metrics.json"
    if existing_metrics.is_file():
        existing = json.loads(existing_metrics.read_text(encoding="utf-8"))
        result = {**existing, **result}
    existing_metrics.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def train(args: argparse.Namespace) -> dict[str, object]:
    try:
        from xgboost import XGBRegressor
    except ImportError as exc:
        raise RuntimeError("xgboost is required; install it in the v2x-ai-py310 environment.") from exc

    metadata = json.loads((args.dataset_dir / "metadata.json").read_text(encoding="utf-8"))
    if args.horizon_step != int(metadata["n_pred"]):
        raise ValueError("XGBoost baseline must use the prepared final forecast horizon.")
    train_data = np.load(args.dataset_dir / "train.npz")
    train_x, train_y = train_data["x"], train_data["y"][:, args.horizon_step - 1, :]
    x_train, y_train = _rows(train_x, train_y, max_rows=args.max_train_rows, seed=args.seed)
    model = XGBRegressor(
        n_estimators=args.n_estimators, max_depth=args.max_depth, learning_rate=args.learning_rate,
        subsample=args.subsample, colsample_bytree=args.colsample_bytree, objective="reg:squarederror",
        tree_method="hist", n_jobs=args.n_jobs, random_state=args.seed,
    )
    model.fit(x_train, y_train, verbose=False)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.save_model(args.output_dir / "model.json")
    result = _evaluate_model(model, args.dataset_dir, args.horizon_step)
    result.update(
        train_rows=int(train_x.shape[0] * train_x.shape[3]),
        sampled_train_rows=int(len(y_train)),
        input_columns=int(x_train.shape[1]),
    )
    (args.output_dir / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train a multivariate XGBoost stage-1 baseline.")
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--horizon-step", type=int, default=12)
    parser.add_argument("--max-train-rows", type=int, default=250_000)
    parser.add_argument("--n-estimators", type=int, default=300)
    parser.add_argument("--max-depth", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=0.05)
    parser.add_argument("--subsample", type=float, default=0.8)
    parser.add_argument("--colsample-bytree", type=float, default=0.8)
    parser.add_argument("--n-jobs", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--evaluate-only", action="store_true", help="recompute metrics from output-dir/model.json without training")
    args = parser.parse_args(argv)
    action = evaluate_checkpoint if args.evaluate_only else train
    print(json.dumps(action(args), indent=2))


if __name__ == "__main__":
    main()
