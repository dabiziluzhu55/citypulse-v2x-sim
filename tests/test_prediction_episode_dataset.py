import csv
import json

import numpy as np

from algorithms.prediction.prepare_stgcn_episode_dataset import prepare_episode_dataset


def _write_episode(path, lane_values):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("elapsed_seconds", "lane_id", "vehicle_count"))
        writer.writeheader()
        for time, values in enumerate(lane_values):
            for lane, value in zip(("a_0", "b_0"), values):
                writer.writerow({"elapsed_seconds": str(time * 5), "lane_id": lane, "vehicle_count": value})


def test_episode_windows_do_not_cross_boundaries_and_normalize_on_train_only(tmp_path):
    _write_episode(tmp_path / "train.csv", [(1, 2), (2, 3), (3, 4), (4, 5), (5, 6)])
    _write_episode(tmp_path / "validation.csv", [(100, 200)] * 5)
    manifest = {"episodes": [
        {"id": "train", "split": "train", "demand_scale": 1.0, "seed": 1, "file": "train.csv"},
        {"id": "validation", "split": "validation", "demand_scale": 1.0, "seed": 2, "file": "validation.csv"},
    ]}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    metadata = prepare_episode_dataset(
        manifest_path=manifest_path, input_dir=tmp_path, output_dir=tmp_path / "out",
        net_path=tmp_path / "missing.net.xml", target="vehicle_count", n_his=2, n_pred=2,
    )
    train = np.load(tmp_path / "out" / "train.npz")
    validation = np.load(tmp_path / "out" / "validation.npz")
    assert train["x"].shape == (2, 1, 2, 2)
    assert train["y"].shape == (2, 2, 2)
    assert validation["x"].shape == (2, 1, 2, 2)
    assert metadata["normalization"]["fit_split"] == "train"
    assert metadata["normalization"]["mean"] < 10
