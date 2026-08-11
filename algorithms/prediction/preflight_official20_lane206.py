"""Preflight the frozen official20 lane206 data contract.

The preflight is intentionally read-only with respect to the formal dataset.
It validates metadata, prepared tensor shapes, one representative episode's
time axis and lane density, the static/sparse graph node order, and optionally
creates a baseline reference manifest for a new experiment.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from .build_dynamic_lane_graph import nodes_sha256


FEATURES = ("vehicle_count", "halting_count", "mean_speed", "occupancy")
EXPECTED_SPLITS = {
    "train": 18,
    "validation": 3,
    "test_in_distribution": 3,
    "test_extrapolation": 6,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON metadata {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"JSON metadata must be an object: {path}")
    return payload


def validate_metadata(dataset_dir: Path) -> dict[str, Any]:
    metadata_path = dataset_dir / "metadata.json"
    metadata = _load_json(metadata_path)
    _require(metadata.get("target") == "vehicle_count", "target must be vehicle_count")
    _require(tuple(metadata.get("features", ())) == FEATURES, f"features must be {FEATURES}")
    _require(int(metadata.get("n_his", -1)) == 12, "n_his must be 12")
    _require(int(metadata.get("n_pred", -1)) == 12, "n_pred must be 12")
    _require(math.isclose(float(metadata.get("interval_seconds", -1)), 5.0), "interval_seconds must be 5")
    _require(int(metadata.get("lane_count", -1)) == 206, "lane_count must be 206")

    lanes = tuple(str(value) for value in metadata.get("lanes", ()))
    _require(len(lanes) == 206 and len(set(lanes)) == 206, "metadata lanes must contain 206 unique nodes")
    _require(lanes == tuple(sorted(lanes)), "metadata lanes must use lexical lane-id order")

    normalization = metadata.get("normalization")
    _require(isinstance(normalization, dict), "normalization metadata is missing")
    _require(normalization.get("fit_split") == "train", "normalization must be fit on train")
    _require(len(normalization.get("feature_mean", ())) == 4, "feature_mean must have four values")
    _require(len(normalization.get("feature_std", ())) == 4, "feature_std must have four values")
    _require(all(float(value) > 0 for value in normalization["feature_std"]), "feature_std must be positive")
    _require(float(normalization.get("target_std", 0)) > 0, "target_std must be positive")

    episodes = metadata.get("episodes")
    _require(isinstance(episodes, list) and episodes, "metadata episodes are missing")
    split_counts = Counter(str(item.get("split")) for item in episodes if isinstance(item, dict))
    _require(dict(split_counts) == EXPECTED_SPLITS, f"episode split counts must be {EXPECTED_SPLITS}")

    split_files = metadata.get("splits")
    _require(isinstance(split_files, dict), "metadata splits are missing")
    for split in EXPECTED_SPLITS:
        split_episode_samples = sum(
            int(item.get("samples", -1))
            for item in episodes
            if isinstance(item, dict) and str(item.get("split")) == split
        )
        _require(
            split_episode_samples == int(split_files[split]["samples"]),
            f"{split} episode sample totals differ from split metadata",
        )
        path = dataset_dir / f"{split}.npz"
        _require(path.is_file(), f"missing prepared split: {path}")
        with np.load(path, allow_pickle=False) as data:
            _require(data["x"].ndim == 4, f"{split}.npz x must be rank 4")
            _require(data["y"].ndim == 3, f"{split}.npz y must be rank 3")
            _require(data["x"].shape[1:] == (4, 12, 206), f"{split}.npz x shape is incompatible")
            _require(data["y"].shape[1:] == (12, 206), f"{split}.npz y shape is incompatible")
            expected_samples = int(split_files[split]["samples"])
            _require(data["x"].shape[0] == expected_samples, f"{split}.npz sample count differs from metadata")
            _require(np.isfinite(data["x"]).all() and np.isfinite(data["y"]).all(), f"{split}.npz contains non-finite values")
    return metadata


def validate_episode(
    episode_file: Path,
    *,
    lanes: tuple[str, ...],
    interval_seconds: float = 5.0,
    n_his: int = 12,
    n_pred: int = 12,
) -> dict[str, Any]:
    required = {"elapsed_seconds", "lane_id", *FEATURES}
    present_by_time: dict[float, set[str]] = {}
    seen: set[tuple[float, str]] = set()
    row_count = 0
    with episode_file.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _require(reader.fieldnames is not None and required.issubset(reader.fieldnames), f"episode lacks required columns: {sorted(required)}")
        for row in reader:
            row_count += 1
            try:
                elapsed = float(row["elapsed_seconds"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid elapsed_seconds in {episode_file}: {row.get('elapsed_seconds')!r}") from exc
            lane = str(row["lane_id"])
            _require(lane in lanes, f"episode contains lane outside frozen node set: {lane}")
            key = (elapsed, lane)
            _require(key not in seen, f"duplicate row for time={elapsed} lane={lane}")
            seen.add(key)
            present_by_time.setdefault(elapsed, set()).add(lane)
            for feature in FEATURES:
                try:
                    value = float(row[feature])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid {feature} in {episode_file}: {row[feature]!r}") from exc
                _require(math.isfinite(value), f"non-finite {feature} in {episode_file}")

    _require(present_by_time, f"episode is empty: {episode_file}")
    missing = {
        elapsed: sorted(set(lanes) - present)
        for elapsed, present in present_by_time.items()
        if present != set(lanes)
    }
    if missing:
        example_time = next(iter(sorted(missing)))
        raise ValueError(f"episode has incomplete lane rows at {example_time}")
    times = sorted(present_by_time)
    diffs = [round(times[index] - times[index - 1], 6) for index in range(1, len(times))]
    _require(all(math.isclose(diff, interval_seconds, abs_tol=1e-6) for diff in diffs), "episode time axis is not a continuous 5-second grid")
    sample_count = len(times) - n_his - n_pred + 1
    _require(sample_count > 0, "episode is too short for the frozen window")
    _require(row_count == len(times) * len(lanes), "episode row count is not time_points x 206 lanes")
    return {
        "path": str(episode_file.resolve()),
        "row_count": row_count,
        "time_points": len(times),
        "first_time": times[0],
        "last_time": times[-1],
        "interval_seconds": interval_seconds,
        "lane_count": len(lanes),
        "window_samples": sample_count,
    }


def validate_graph(graph_path: Path, lanes: tuple[str, ...]) -> dict[str, Any]:
    with np.load(graph_path, allow_pickle=False) as archive:
        _require("nodes" in archive, "graph archive lacks nodes")
        _require("adjacency" in archive, "preflight graph must be the existing static adjacency archive")
        graph_nodes = tuple(str(value) for value in archive["nodes"].tolist())
        _require(graph_nodes == lanes, "graph node order differs from metadata lane order")
        adjacency = np.asarray(archive["adjacency"], dtype=np.float32)
        _require(adjacency.shape == (len(lanes), len(lanes)), "static graph adjacency shape is incompatible")
        _require(np.isfinite(adjacency).all() and np.all(adjacency >= 0), "static graph adjacency is invalid")
        _require(np.allclose(adjacency, adjacency.T, atol=1e-6), "static graph adjacency must be symmetric")
        _require(np.all(np.diag(adjacency) > 0), "static graph adjacency must contain self-loops")
    return {"path": str(graph_path.resolve()), "sha256": sha256_file(graph_path), "nodes_sha256": nodes_sha256(lanes)}


def build_baseline_reference(baseline_formal_dir: Path, *, metadata_path: Path, graph_path: Path | None) -> dict[str, Any]:
    results_path = baseline_formal_dir / "results_summary_60s.csv"
    stgcn_path = baseline_formal_dir / "stgcn" / "metrics.json"
    xgb_path = baseline_formal_dir / "xgb" / "metrics.json"
    simple_path = baseline_formal_dir / "baseline_metrics_three.json"
    required = (results_path, stgcn_path, xgb_path, simple_path)
    for path in required:
        _require(path.is_file(), f"missing baseline artifact: {path}")
    metadata = _load_json(metadata_path)
    lanes = tuple(str(value) for value in metadata.get("lanes", ()))
    _require(len(lanes) == 206 and lanes == tuple(sorted(lanes)), "baseline metadata has an invalid node order")
    with results_path.open("r", newline="", encoding="utf-8") as handle:
        result_rows = list(csv.DictReader(handle))
    return {
        "source_dir": str(baseline_formal_dir.resolve()),
        "task": "official20_lane206_vehicle_count_60s",
        "contract": {
            "target": metadata.get("target"),
            "features": metadata.get("features"),
            "n_his": metadata.get("n_his"),
            "n_pred": metadata.get("n_pred"),
            "interval_seconds": metadata.get("interval_seconds"),
            "lane_count": metadata.get("lane_count"),
            "node_order": list(lanes),
            "nodes_sha256": nodes_sha256(lanes),
            "normalization_fit_split": metadata.get("normalization", {}).get("fit_split"),
        },
        "metrics": {
            "results_summary_60s": result_rows,
            "stgcn": _load_json(stgcn_path),
            "xgboost": _load_json(xgb_path),
            "simple_baselines": json.loads(simple_path.read_text(encoding="utf-8")),
        },
        "sha256": {
            "dataset_metadata": sha256_file(metadata_path),
            "results_summary_60s": sha256_file(results_path),
            "stgcn_metrics": sha256_file(stgcn_path),
            "xgboost_metrics": sha256_file(xgb_path),
            "simple_baseline_metrics": sha256_file(simple_path),
            **({"static_graph": sha256_file(graph_path)} if graph_path else {}),
        },
    }


def run_preflight(
    *,
    dataset_dir: Path,
    episode_file: Path,
    graph_path: Path,
    output_dir: Path,
    baseline_formal_dir: Path | None = None,
) -> dict[str, Any]:
    metadata = validate_metadata(dataset_dir)
    lanes = tuple(str(value) for value in metadata["lanes"])
    episode = validate_episode(
        episode_file,
        lanes=lanes,
        interval_seconds=float(metadata["interval_seconds"]),
        n_his=int(metadata["n_his"]),
        n_pred=int(metadata["n_pred"]),
    )
    graph = validate_graph(graph_path, lanes)
    result: dict[str, Any] = {
        "status": "passed",
        "contract": {
            "target": metadata["target"],
            "features": metadata["features"],
            "n_his": metadata["n_his"],
            "n_pred": metadata["n_pred"],
            "interval_seconds": metadata["interval_seconds"],
            "lane_count": metadata["lane_count"],
            "split_counts": EXPECTED_SPLITS,
            "nodes_sha256": nodes_sha256(lanes),
            "normalization_fit_split": metadata["normalization"]["fit_split"],
        },
        "episode": episode,
        "graph": graph,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = dataset_dir / "metadata.json"
    if baseline_formal_dir is not None:
        baseline = build_baseline_reference(baseline_formal_dir, metadata_path=metadata_path, graph_path=graph_path)
        result["baseline_reference"] = str((output_dir / "baseline_reference.json").resolve())
        (output_dir / "baseline_reference.json").write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (output_dir / "preflight.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the frozen official20 lane206 prediction contract.")
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--episode-file", type=Path, required=True)
    parser.add_argument("--graph", dest="graph_path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline-formal-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    print(json.dumps(run_preflight(**vars(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
