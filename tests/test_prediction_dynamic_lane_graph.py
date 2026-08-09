import tempfile
import unittest
from pathlib import Path

import numpy as np

from algorithms.prediction.build_dynamic_lane_graph import (
    RELATION_DIRECT_TRANSITION,
    RELATION_LATERAL,
    RELATION_NEXT_TARGET,
    build_sparse_graph,
    load_sparse_graph,
    nodes_sha256,
)


class DynamicLaneGraphTests(unittest.TestCase):
    def test_builds_one_sparse_edge_per_direction_and_preserves_relations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static_path = root / "static.npz"
            output_path = root / "dynamic.npz"
            nodes = np.asarray(["a_0", "b_0", "c_0"])
            adjacency = np.eye(3, dtype=np.float32)
            adjacency[0, 1] = adjacency[1, 0] = 0.25
            adjacency[1, 2] = adjacency[2, 1] = 1.0
            lateral = np.zeros((3, 3), dtype=np.float32)
            lateral[0, 1] = lateral[1, 0] = 0.25
            direct = np.zeros((3, 3), dtype=np.float32)
            direct[1, 2] = 1.0
            next_target = np.zeros((3, 3), dtype=np.float32)
            next_target[1, 2] = 1.0
            np.savez_compressed(
                static_path,
                nodes=nodes,
                adjacency=adjacency,
                adjacency_lateral=lateral,
                adjacency_direct_transition=direct,
                adjacency_next_target=next_target,
            )

            summary = build_sparse_graph(adjacency=static_path, output=output_path)
            graph = load_sparse_graph(output_path)
            pairs = list(zip(graph["source_index"].tolist(), graph["target_index"].tolist()))
            self.assertEqual(summary["node_count"], 3)
            self.assertEqual(summary["edge_count_including_self_loops"], 7)
            self.assertEqual(len(pairs), len(set(pairs)))
            self.assertEqual(int(graph["is_self_loop"].sum()), 3)
            self.assertTrue(np.allclose(graph["static_weight"][graph["is_self_loop"]], 1.0))
            self.assertEqual(graph["nodes_sha256"], nodes_sha256(tuple(nodes.tolist())))

            relation_by_pair = {
                pair: int(edge_type)
                for pair, edge_type in zip(pairs, graph["edge_type"].tolist())
            }
            direction_by_pair = {
                pair: int(direction)
                for pair, direction in zip(pairs, graph["edge_direction"].tolist())
            }
            self.assertEqual(relation_by_pair[(0, 1)], RELATION_LATERAL)
            self.assertEqual(
                relation_by_pair[(1, 2)],
                RELATION_DIRECT_TRANSITION | RELATION_NEXT_TARGET,
            )
            self.assertEqual(relation_by_pair[(2, 1)], RELATION_DIRECT_TRANSITION | RELATION_NEXT_TARGET)
            self.assertEqual(direction_by_pair[(1, 2)], 1)
            self.assertEqual(direction_by_pair[(2, 1)], -1)


if __name__ == "__main__":
    unittest.main()
