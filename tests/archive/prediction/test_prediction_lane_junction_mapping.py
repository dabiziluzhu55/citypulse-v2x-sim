import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from algorithms.prediction.archive.experiments.build_lane_junction_mapping import (
    build_mapping,
    load_lane_junction_mapping,
)


class LaneJunctionMappingTests(unittest.TestCase):
    @staticmethod
    def _write_fixture(root: Path):
        lanes = tuple(f"lane_{index:03d}_0" for index in range(206))
        junctions = tuple(sorted(f"demo_{index}" for index in range(1, 21)))
        groups: dict[str, list[str]] = {}
        start = 0
        for index, junction in enumerate(junctions):
            count = 16 if index == len(junctions) - 1 else 10
            groups[junction] = list(lanes[start : start + count])
            start += count
        assert start == len(lanes)

        metadata = root / "metadata.json"
        metadata.write_text(json.dumps({"lanes": list(lanes)}), encoding="utf-8")
        manifest = root / "tls_manifest.json"
        # Deliberately write reverse insertion order; the builder must use a
        # deterministic junction order rather than JSON insertion order.
        intersections = {
            junction: {"incoming_lanes": {"approach": groups[junction]}}
            for junction in reversed(junctions)
        }
        manifest.write_text(json.dumps({"intersections": intersections}), encoding="utf-8")

        direct = np.zeros((206, 206), dtype=np.float32)
        next_target = np.zeros_like(direct)
        for index in range(205):
            direct[index, index + 1] = 1.0
            next_target[index, index + 1] = 1.0
        adjacency = np.maximum(direct, direct.T)
        np.fill_diagonal(adjacency, 1.0)
        graph = root / "lane_graph.npz"
        np.savez_compressed(
            graph,
            nodes=np.asarray(lanes),
            adjacency=adjacency,
            adjacency_direct_transition=direct,
            adjacency_next_target=next_target,
        )
        return metadata, manifest, graph, lanes, junctions, groups

    def test_builds_stable_mapping_and_junction_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, manifest, graph, lanes, junctions, groups = self._write_fixture(root)
            output = root / "mapping.npz"
            report = root / "mapping.json"

            result = build_mapping(
                metadata=metadata,
                tls_manifest=manifest,
                lane_graph=graph,
                output=output,
                report=report,
            )
            mapping = load_lane_junction_mapping(output)

            self.assertEqual(tuple(mapping["lane_order"]), lanes)
            self.assertEqual(tuple(mapping["junction_order"]), junctions)
            self.assertEqual(mapping["pooling_matrix"].shape, (20, 206))
            self.assertEqual(mapping["broadcast_matrix"].shape, (206, 20))
            self.assertTrue(
                np.allclose(mapping["pooling_matrix"].sum(axis=1), np.ones(20))
            )
            self.assertTrue(
                np.array_equal(
                    mapping["broadcast_matrix"].sum(axis=1), np.ones(206)
                )
            )
            self.assertEqual(
                int(np.count_nonzero(np.triu(mapping["junction_adjacency"], k=1))),
                19,
            )
            self.assertEqual(result["hashes"]["lane_order_sha256"], mapping["lane_order_sha256"])
            self.assertEqual(
                [item["lane_count"] for item in result["junctions"]],
                [len(groups[junction]) for junction in junctions],
            )

    def test_rejects_manifest_lane_assigned_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, manifest, graph, _lanes, junctions, _groups = self._write_fixture(root)
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["intersections"][junctions[1]]["incoming_lanes"]["approach"].append(
                payload["intersections"][junctions[0]]["incoming_lanes"]["approach"][0]
            )
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "assigned to both"):
                build_mapping(
                    metadata=metadata,
                    tls_manifest=manifest,
                    lane_graph=graph,
                    output=root / "mapping.npz",
                )

    def test_rejects_lane_set_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, manifest, graph, lanes, _junctions, _groups = self._write_fixture(root)
            metadata.write_text(
                json.dumps({"lanes": list(lanes[:-1]) + ["lane_missing_0"]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "lane set differs"):
                build_mapping(
                    metadata=metadata,
                    tls_manifest=manifest,
                    lane_graph=graph,
                    output=root / "mapping.npz",
                )


if __name__ == "__main__":
    unittest.main()
