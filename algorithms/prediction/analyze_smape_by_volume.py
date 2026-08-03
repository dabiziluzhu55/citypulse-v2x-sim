"""Explain sMAPE by actual traffic-volume band for a trained STGCN run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset

from .train_stgcn_stage1 import _build_model, _load_split


VOLUME_BANDS = (
    ("zero", 0.0, 0.5),
    ("one", 0.5, 1.5),
    ("two_to_four", 1.5, 4.5),
    ("five_or_more", 4.5, float("inf")),
)


def _smape_terms(prediction: np.ndarray, actual: np.ndarray) -> np.ndarray:
    denominator = np.abs(prediction) + np.abs(actual)
    return np.divide(
        2.0 * np.abs(prediction - actual), denominator,
        out=np.zeros_like(prediction, dtype=np.float64), where=denominator > 1e-9,
    )


def _band_rows(prediction: np.ndarray, actual: np.ndarray) -> list[dict[str, float | int | str]]:
    terms = _smape_terms(prediction, actual)
    total = int(actual.size)
    rows = []
    for name, lower, upper in VOLUME_BANDS:
        mask = (actual >= lower) & (actual < upper)
        count = int(mask.sum())
        rows.append({
            "band": name,
            "sample_count": count,
            "sample_fraction": count / total,
            "mean_actual": float(actual[mask].mean()) if count else 0.0,
            "mean_prediction": float(prediction[mask].mean()) if count else 0.0,
            "mae": float(np.abs(prediction[mask] - actual[mask]).mean()) if count else 0.0,
            "smape": float(terms[mask].mean()) if count else 0.0,
            "global_smape_contribution": float(terms[mask].sum() / total) if count else 0.0,
        })
    return rows


def _stgcn_prediction(
    *, dataset_dir: Path, stgcn_root: Path, output_dir: Path,
    metadata: dict[str, object], split: str, horizon_step: int, batch_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = _build_model(
        metadata=metadata, stgcn_root=stgcn_root, dataset_dir=dataset_dir,
        device=device, dropout=0.5,
    )
    checkpoint = torch.load(output_dir / "best.pt", map_location=device)
    model.load_state_dict(checkpoint["model"])
    x, y = _load_split(dataset_dir / f"{split}.npz", horizon_step)
    values = []
    model.eval()
    with torch.inference_mode():
        for (batch,) in DataLoader(TensorDataset(x), batch_size=batch_size, shuffle=False):
            values.append(model(batch.to(device)).reshape(len(batch), -1).cpu().numpy())
    normalization = metadata["normalization"]
    mean, std = float(normalization["target_mean"]), float(normalization["target_std"])
    return np.concatenate(values) * std + mean, np.rint(y.numpy() * std + mean)


def analyze(
    *, dataset_dir: Path, stgcn_root: Path, output_dir: Path, horizon_step: int, batch_size: int,
) -> dict[str, object]:
    metadata = json.loads((dataset_dir / "metadata.json").read_text(encoding="utf-8"))
    if horizon_step != int(metadata["n_pred"]):
        raise ValueError("diagnostics must use the final prepared forecast horizon.")
    target_index = int(metadata["target_feature_index"])
    mean, std = (float(metadata["normalization"][key]) for key in ("target_mean", "target_std"))
    result: dict[str, object] = {
        "horizon_steps": horizon_step,
        "horizon_seconds": horizon_step * float(metadata["interval_seconds"]),
        "models": {},
    }
    for split in ("test_in_distribution", "test_extrapolation"):
        stgcn, actual = _stgcn_prediction(
            dataset_dir=dataset_dir, stgcn_root=stgcn_root, output_dir=output_dir,
            metadata=metadata, split=split, horizon_step=horizon_step, batch_size=batch_size,
        )
        data = np.load(dataset_dir / f"{split}.npz")
        moving_average = data["x"][:, target_index, :, :].mean(axis=1) * std + mean
        for model, prediction in (("stgcn", stgcn), ("moving_average", moving_average)):
            models = result["models"].setdefault(model, {})
            models[split] = {
                "global_smape": float(_smape_terms(prediction, actual).mean()),
                "bands": _band_rows(prediction, actual),
            }
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Break down STGCN sMAPE by actual traffic volume.")
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--stgcn-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--horizon-step", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args(argv)
    result = analyze(
        dataset_dir=args.dataset_dir, stgcn_root=args.stgcn_root, output_dir=args.output_dir,
        horizon_step=args.horizon_step, batch_size=args.batch_size,
    )
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
