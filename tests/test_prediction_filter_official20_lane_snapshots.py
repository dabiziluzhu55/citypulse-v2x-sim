import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from algorithms.prediction.filter_official20_lane_snapshots import filter_snapshots


class FilterOfficial20LaneSnapshotsTests(unittest.TestCase):
    def test_keeps_fixed_nodes_and_validates_dense_time_points(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adjacency = root / "adjacency.npz"
            np.savez_compressed(adjacency, nodes=np.array(["edgeA_0", "edgeB_0"]), adjacency=np.eye(2))
            source = root / "episode_5s_lanes.csv"
            with source.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=("elapsed_seconds", "lane_id", "vehicle_count"))
                writer.writeheader()
                writer.writerows((
                    {"elapsed_seconds": "0", "lane_id": "edgeA_0", "vehicle_count": "1"},
                    {"elapsed_seconds": "0", "lane_id": "ignored_0", "vehicle_count": "9"},
                    {"elapsed_seconds": "0", "lane_id": "edgeB_0", "vehicle_count": "2"},
                    {"elapsed_seconds": "5", "lane_id": "edgeA_0", "vehicle_count": "3"},
                    {"elapsed_seconds": "5", "lane_id": "edgeB_0", "vehicle_count": "4"},
                ))
            output_dir = root / "filtered"
            result = filter_snapshots(adjacency=adjacency, inputs=(source,), output_dir=output_dir)
            self.assertEqual(result["node_count"], 2)
            self.assertEqual(result["outputs"][0]["retained_rows"], 4)
            with (output_dir / source.name).open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["lane_id"] for row in rows], ["edgeA_0", "edgeB_0", "edgeA_0", "edgeB_0"])
            metadata = json.loads((output_dir / "official20_lane_filter_metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["outputs"][0]["time_points"], 2)


if __name__ == "__main__":
    unittest.main()
