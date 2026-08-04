"""Build leakage-free STGCN windows from separately simulated episodes.

This preparation step deliberately keeps episode boundaries intact: the final
history window of one SUMO run can never use future labels from the next run.
Normalization statistics and the active-lane mask are fit using training runs
only.  The generated NPZ files are framework-neutral inputs for stage-1 model
training and baseline evaluation.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .dataset import load_lane_series
from .export_stgcn_dataset import build_lane_adjacency


@dataclass(frozen=True)
class EpisodeSummary:
    id: str
    split: str
    demand_scale: float
    seed: int
    file: str
    samples: int


def _load_manifest(path: Path) -> tuple[dict[str, object], ...]:
    content = json.loads(path.read_text(encoding="utf-8"))
    episodes = content.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("manifest must contain a non-empty 'episodes' list.")
    required = {"id", "split", "demand_scale", "seed", "file"}
    for episode in episodes:
        if not isinstance(episode, dict) or required - set(episode):
            raise ValueError(f"invalid episode entry: {episode!r}")
    return tuple(episodes)


def _windows(
    features: np.ndarray, target_values: np.ndarray, n_his: int, n_pred: int
) -> tuple[np.ndarray, np.ndarray]:
    """Create ``(sample, feature, history, lane)`` windows within one episode."""

    count = target_values.shape[0] - n_his - n_pred + 1
    if count <= 0:
        raise ValueError(
            f"episode has {target_values.shape[0]} time points; need at least {n_his + n_pred}."
        )
    x = np.stack([features[:, index : index + n_his, :] for index in range(count)])
    y = np.stack([target_values[index + n_his : index + n_his + n_pred] for index in range(count)])
    return x.astype(np.float32), y.astype(np.float32)


def prepare_episode_dataset(
    *,
    manifest_path: Path,
    input_dir: Path,
    output_dir: Path,
    net_path: Path,
    target: str,
    features: tuple[str, ...],
    adjacency_path: Path | None,
    n_his: int,
    n_pred: int,
) -> dict[str, object]:
    if n_his < 1 or n_pred < 1:
        raise ValueError("n_his and n_pred must both be positive.")
    episodes = _load_manifest(manifest_path)
    by_split: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}
    raw_features_by_split: dict[str, list[np.ndarray]] = {}
    raw_target_by_split: dict[str, list[np.ndarray]] = {}
    summaries: list[EpisodeSummary] = []
    lanes: tuple[str, ...] | None = None
    interval_seconds: float | None = None

    for episode in episodes:
        path = input_dir / str(episode["file"])
        target_series = load_lane_series(path, target)
        feature_series = [load_lane_series(path, feature) for feature in features]
        if lanes is None:
            lanes = target_series.lanes
            interval_seconds = target_series.interval_seconds
        elif target_series.lanes != lanes:
            raise ValueError(f"lane set/order differs in {path}; apply one shared active-lane mask first.")
        elif target_series.interval_seconds != interval_seconds:
            raise ValueError(f"snapshot interval differs in {path}.")
        if any(series.lanes != lanes or series.interval_seconds != interval_seconds for series in feature_series):
            raise ValueError(f"feature lane set/order or interval differs in {path}.")
        feature_values = np.stack([np.asarray(series.values, dtype=np.float32) for series in feature_series])
        target_values = np.asarray(target_series.values, dtype=np.float32)
        x, y = _windows(feature_values, target_values, n_his, n_pred)
        split = str(episode["split"])
        by_split.setdefault(split, []).append((x, y))
        raw_features_by_split.setdefault(split, []).append(feature_values)
        raw_target_by_split.setdefault(split, []).append(target_values)
        summaries.append(
            EpisodeSummary(
                id=str(episode["id"]), split=split, demand_scale=float(episode["demand_scale"]),
                seed=int(episode["seed"]), file=str(episode["file"]), samples=len(x),
            )
        )

    if lanes is None or "train" not in by_split:
        raise ValueError("manifest must contain at least one training episode.")
    output_dir.mkdir(parents=True, exist_ok=True)
    train_features = np.concatenate(raw_features_by_split["train"], axis=1)
    feature_mean = train_features.mean(axis=(1, 2), dtype=np.float64).astype(np.float32)
    feature_std = train_features.std(axis=(1, 2), dtype=np.float64).astype(np.float32)
    if np.any(feature_std <= 0):
        raise ValueError("at least one training feature has zero variance.")
    train_target = np.concatenate(raw_target_by_split["train"], axis=0)
    target_mean = float(train_target.mean())
    target_std = float(train_target.std())
    if target_std <= 0:
        raise ValueError("training target has zero variance.")

    split_files = {}
    for split, windows in sorted(by_split.items()):
        x = np.concatenate([item[0] for item in windows], axis=0)
        y = np.concatenate([item[1] for item in windows], axis=0)
        destination = output_dir / f"{split}.npz"
        normalized_x = (x - feature_mean[None, :, None, None]) / feature_std[None, :, None, None]
        np.savez_compressed(destination, x=normalized_x, y=(y - target_mean) / target_std)
        split_files[split] = {"path": str(destination), "samples": len(x)}

    if adjacency_path is None:
        adjacency = build_lane_adjacency(lanes, net_path)
    else:
        adjacency = np.load(adjacency_path)["adjacency"].astype(np.float32)
        if adjacency.shape != (len(lanes), len(lanes)):
            raise ValueError(f"adjacency shape {adjacency.shape} does not match {len(lanes)} nodes")
    np.savez_compressed(output_dir / "adjacency.npz", adjacency=adjacency)
    metadata = {
        "manifest": str(manifest_path.resolve()),
        "input_dir": str(input_dir.resolve()),
        "target": target,
        "features": list(features),
        "target_feature_index": list(features).index(target) if target in features else None,
        "n_his": n_his,
        "n_pred": n_pred,
        "interval_seconds": interval_seconds,
        "lane_count": len(lanes),
        "lanes": list(lanes),
        "normalization": {
            "fit_split": "train",
            "feature_mean": feature_mean.tolist(),
            "feature_std": feature_std.tolist(),
            "target_mean": target_mean,
            "target_std": target_std,
        },
        "splits": split_files,
        "episodes": [asdict(summary) for summary in summaries],
        "adjacency_edges": int(adjacency.sum()),
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare episode-bounded STGCN tensors from SUMO snapshots.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--net", type=Path, required=True)
    parser.add_argument("--adjacency", type=Path, help="Optional precomputed node adjacency NPZ.")
    parser.add_argument("--target", default="vehicle_count")
    parser.add_argument(
        "--feature", action="append", default=None,
        help="Historical input feature; repeat to set order. Defaults to the target only.",
    )
    parser.add_argument("--n-his", type=int, default=12)
    parser.add_argument("--n-pred", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    print(
        json.dumps(
            prepare_episode_dataset(
                manifest_path=args.manifest,
                input_dir=args.input_dir,
                output_dir=args.output_dir,
                net_path=args.net,
                target=args.target,
                features=tuple(args.feature or [args.target]),
                adjacency_path=args.adjacency,
                n_his=args.n_his,
                n_pred=args.n_pred,
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
