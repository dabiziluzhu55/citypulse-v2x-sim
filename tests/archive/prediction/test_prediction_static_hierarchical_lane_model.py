import unittest

import numpy as np

try:
    import torch
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    torch = None


def _small_graph_and_mapping():
    nodes = tuple(f"lane_{index:03d}_0" for index in range(206))
    source = np.concatenate((np.arange(206), np.array([0, 1])))
    target = np.concatenate((np.arange(206), np.array([1, 0])))
    graph = {
        "nodes": nodes,
        "source_index": source,
        "target_index": target,
        "static_weight": np.concatenate(
            (np.ones(206, dtype=np.float32), np.full(2, 0.5, dtype=np.float32))
        ),
    }
    junction_order = tuple(f"demo_{index:02d}" for index in range(20))
    owner = np.arange(206, dtype=np.int64) % 20
    pooling = np.zeros((20, 206), dtype=np.float32)
    broadcast = np.zeros((206, 20), dtype=np.float32)
    counts = np.bincount(owner, minlength=20)
    for lane_index, junction_index in enumerate(owner.tolist()):
        pooling[junction_index, lane_index] = 1.0 / counts[junction_index]
        broadcast[lane_index, junction_index] = 1.0
    junction_adjacency = np.eye(20, dtype=np.float32)
    for index in range(19):
        junction_adjacency[index, index + 1] = 1.0
        junction_adjacency[index + 1, index] = 1.0
    mapping = {
        "lane_order": nodes,
        "junction_order": junction_order,
        "pooling_matrix": pooling,
        "broadcast_matrix": broadcast,
        "junction_adjacency": junction_adjacency,
    }
    return graph, mapping


@unittest.skipUnless(torch is not None, "PyTorch is not installed in this environment")
class StaticHierarchicalLaneModelTests(unittest.TestCase):
    def test_shapes_and_zero_initialized_hierarchy(self):
        from algorithms.prediction.archive.experiments.static_hierarchical_lane_model import (
            StaticHierarchicalLaneSTGCN,
        )

        graph, mapping = _small_graph_and_mapping()
        torch.manual_seed(42)
        model = StaticHierarchicalLaneSTGCN(graph, mapping, dropout=0.0)
        model.eval()
        x = torch.randn((2, 4, 12, 206), dtype=torch.float32)
        prediction, junction_hidden, lane_context, scale = model(
            x, return_hierarchy=True
        )
        self.assertEqual(tuple(prediction.shape), (2, 206))
        self.assertEqual(tuple(junction_hidden.shape), (2, 64, 8, 20))
        self.assertEqual(tuple(lane_context.shape), (2, 64, 8, 206))
        self.assertTrue(torch.isfinite(prediction).all())
        self.assertAlmostEqual(float(scale.item()), 0.0, places=7)
        prediction.mean().backward()
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))


if __name__ == "__main__":
    unittest.main()
