import tempfile
import unittest
from pathlib import Path

import numpy as np

from algorithms.prediction.build_directional_lane_graph import (
    build_directional_graph,
    load_directional_graph,
)
from algorithms.prediction.build_dynamic_lane_graph import load_sparse_graph


def _candidate_graph():
    nodes = np.asarray([f"lane_{index:03d}_0" for index in range(206)])
    source = list(range(206))
    target = list(range(206))
    weight = [1.0] * 206
    relation = [0] * 206
    direction = [0] * 206
    # 0 -> 1 is a one-way downstream transition.
    for src, dst, edge_direction in ((0, 1, 1), (1, 0, -1)):
        source.append(src)
        target.append(dst)
        weight.append(1.0)
        relation.append(4)
        direction.append(edge_direction)
    # 2 <-> 3 is a bidirectional transition.
    for src, dst in ((2, 3), (3, 2)):
        source.append(src)
        target.append(dst)
        weight.append(1.0)
        relation.append(2)
        direction.append(2)
    # 4 <-> 5 is lateral-only and must not enter the directional graph.
    for src, dst in ((4, 5), (5, 4)):
        source.append(src)
        target.append(dst)
        weight.append(0.25)
        relation.append(1)
        direction.append(0)
    order = np.lexsort((np.asarray(target), np.asarray(source)))
    return {
        "nodes": nodes,
        "nodes_sha256": np.asarray(""),
        "source_index": np.asarray(source, dtype=np.int64)[order],
        "target_index": np.asarray(target, dtype=np.int64)[order],
        "static_weight": np.asarray(weight, dtype=np.float32)[order],
        "edge_type": np.asarray(relation, dtype=np.uint8)[order],
        "relation_mask": np.asarray(relation, dtype=np.uint8)[order],
        "edge_direction": np.asarray(direction, dtype=np.int8)[order],
        "is_self_loop": np.asarray(
            np.asarray(source)[order] == np.asarray(target)[order], dtype=np.bool_
        ),
    }


class DirectionalLaneGraphTests(unittest.TestCase):
    def test_extracts_downstream_and_transpose_upstream_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "candidate.npz"
            directional_path = root / "directional.npz"
            candidate = _candidate_graph()
            np.savez_compressed(candidate_path, **candidate)

            summary = build_directional_graph(
                candidate_graph=candidate_path,
                output=directional_path,
            )
            graph = load_directional_graph(directional_path)
            downstream = graph["downstream_adjacency"]
            upstream = graph["upstream_adjacency"]
            self.assertEqual(summary["downstream_edge_count"], 3)
            self.assertTrue(np.isclose(downstream[1, 0], 1.0))
            self.assertTrue(np.isclose(downstream[3, 2], 1.0))
            self.assertTrue(np.isclose(downstream[2, 3], 1.0))
            self.assertTrue(np.isclose(downstream[5, 4], 0.0))
            self.assertTrue(np.allclose(upstream, downstream.T))
            self.assertTrue(np.isclose(graph["downstream_normalized"][1, 0], 1.0))
            self.assertTrue(graph["has_relation_branches"])
            self.assertTrue(
                np.isclose(graph["downstream_next_target_adjacency"][1, 0], 1.0)
            )

    def test_hop_decay_separates_next_target_from_direct_transition(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_path = root / "candidate.npz"
            topology_path = root / "topology.csv"
            directional_path = root / "directional_hop_decay.npz"
            np.savez_compressed(candidate_path, **_candidate_graph())
            topology_path.write_text(
                "source_lane_id,target_lane_id,relation,hops\n"
                "lane_000_0,lane_001_0,next_target,3\n"
                "lane_002_0,lane_003_0,direct_transition,1\n"
                "lane_003_0,lane_002_0,direct_transition,1\n",
                encoding="utf-8",
            )

            summary = build_directional_graph(
                candidate_graph=candidate_path,
                topology_csv=topology_path,
                hop_decay=0.5,
                output=directional_path,
            )
            graph = load_directional_graph(directional_path)
            self.assertEqual(summary["hop_decay"], 0.5)
            self.assertEqual(summary["missing_next_target_hop_rows"], 0)
            self.assertTrue(
                np.isclose(graph["downstream_next_target_adjacency"][1, 0], 0.25)
            )
            self.assertTrue(np.isclose(graph["downstream_adjacency"][1, 0], 0.25))
            self.assertTrue(
                np.isclose(graph["downstream_direct_adjacency"][3, 2], 1.0)
            )
            self.assertTrue(
                np.isclose(graph["downstream_next_target_adjacency"][3, 2], 0.0)
            )


if __name__ == "__main__":
    unittest.main()
