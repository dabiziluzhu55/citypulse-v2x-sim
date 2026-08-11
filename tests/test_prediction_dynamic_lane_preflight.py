import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from algorithms.prediction.preflight_official20_lane206 import run_preflight


def _write_fixture(root: Path, *, interval: int = 5, omit_lane: bool = False):
    lanes = tuple(f"lane_{index:03d}_0" for index in range(206))
    dataset_dir = root / "tensors"
    dataset_dir.mkdir()
    split_counts = {
        "train": 18,
        "validation": 3,
        "test_in_distribution": 3,
        "test_extrapolation": 6,
    }
    metadata = {
        "target": "vehicle_count",
        "features": ["vehicle_count", "halting_count", "mean_speed", "occupancy"],
        "n_his": 12,
        "n_pred": 12,
        "interval_seconds": 5.0,
        "lane_count": 206,
        "lanes": list(lanes),
        "normalization": {
            "fit_split": "train",
            "feature_mean": [0.0] * 4,
            "feature_std": [1.0] * 4,
            "target_mean": 0.0,
            "target_std": 1.0,
        },
        "splits": {
            split: {"samples": count} for split, count in split_counts.items()
        },
        "episodes": [
            {"id": f"{split}_{index}", "split": split, "samples": 1}
            for split, count in split_counts.items()
            for index in range(count)
        ],
    }
    (dataset_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    for split in metadata["splits"]:
        sample_count = split_counts[split]
        np.savez_compressed(
            dataset_dir / f"{split}.npz",
            x=np.zeros((sample_count, 4, 12, 206), dtype=np.float32),
            y=np.zeros((sample_count, 12, 206), dtype=np.float32),
        )

    episode = root / "episode.csv"
    with episode.open("w", newline="", encoding="utf-8") as handle:
        fields = ["elapsed_seconds", "lane_id", "vehicle_count", "halting_count", "mean_speed", "occupancy"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for time_index in range(25):
            for lane_index, lane in enumerate(lanes):
                if omit_lane and time_index == 4 and lane_index == 10:
                    continue
                writer.writerow(
                    {
                        "elapsed_seconds": time_index * interval,
                        "lane_id": lane,
                        "vehicle_count": 0,
                        "halting_count": 0,
                        "mean_speed": 1,
                        "occupancy": 0,
                    }
                )

    graph = root / "graph.npz"
    np.savez_compressed(graph, nodes=np.asarray(lanes), adjacency=np.eye(206, dtype=np.float32))
    return dataset_dir, episode, graph


class DynamicLanePreflightTests(unittest.TestCase):
    def test_accepts_frozen_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset_dir, episode, graph = _write_fixture(Path(directory))
            result = run_preflight(
                dataset_dir=dataset_dir,
                episode_file=episode,
                graph_path=graph,
                output_dir=Path(directory) / "out",
            )
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["episode"]["window_samples"], 2)

    def test_rejects_missing_lane_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset_dir, episode, graph = _write_fixture(Path(directory), omit_lane=True)
            with self.assertRaisesRegex(ValueError, "incomplete lane rows"):
                run_preflight(
                    dataset_dir=dataset_dir,
                    episode_file=episode,
                    graph_path=graph,
                    output_dir=Path(directory) / "out",
                )

    def test_rejects_non_five_second_axis(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset_dir, episode, graph = _write_fixture(Path(directory), interval=4)
            with self.assertRaisesRegex(ValueError, "continuous 5-second grid"):
                run_preflight(
                    dataset_dir=dataset_dir,
                    episode_file=episode,
                    graph_path=graph,
                    output_dir=Path(directory) / "out",
                )

    def test_rejects_duplicate_lane_time_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset_dir, episode, graph = _write_fixture(Path(directory))
            with episode.open("a", encoding="utf-8") as handle:
                handle.write("0,lane_000_0,0,0,1,0\n")
            with self.assertRaisesRegex(ValueError, "duplicate row"):
                run_preflight(
                    dataset_dir=dataset_dir,
                    episode_file=episode,
                    graph_path=graph,
                    output_dir=Path(directory) / "out",
                )


if __name__ == "__main__":
    unittest.main()
