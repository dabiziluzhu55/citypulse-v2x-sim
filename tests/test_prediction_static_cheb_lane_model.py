import unittest

import numpy as np

try:
    import torch
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    torch = None


def _small_symmetric_graph():
    nodes = tuple(f"lane_{index:03d}_0" for index in range(206))
    source = np.concatenate((np.arange(206), np.array([0, 1])))
    target = np.concatenate((np.arange(206), np.array([1, 0])))
    return {
        "nodes": nodes,
        "nodes_sha256": "unused-in-direct-constructor",
        "source_index": source,
        "target_index": target,
        "static_weight": np.concatenate(
            (np.ones(206, dtype=np.float32), np.full(2, 0.5, dtype=np.float32))
        ),
        "edge_type": np.concatenate(
            (np.zeros(206, dtype=np.uint8), np.ones(2, dtype=np.uint8))
        ),
        "edge_direction": np.zeros(len(source), dtype=np.int8),
        "is_self_loop": np.concatenate(
            (np.ones(206, dtype=np.bool_), np.zeros(2, dtype=np.bool_))
        ),
    }


@unittest.skipUnless(torch is not None, "PyTorch is not installed in this environment")
class StaticChebLaneModelTests(unittest.TestCase):
    def test_gso_and_model_shapes(self):
        from algorithms.prediction.static_cheb_lane_model import (
            StaticChebLaneSTGCN,
            _static_adjacency,
            build_chebyshev_gso,
        )

        graph = _small_symmetric_graph()
        adjacency = _static_adjacency(graph)
        gso = build_chebyshev_gso(adjacency)
        self.assertEqual(gso.shape, (206, 206))
        self.assertTrue(np.isfinite(gso).all())
        self.assertTrue(np.allclose(gso, gso.T, atol=1e-6))

        torch.manual_seed(42)
        model = StaticChebLaneSTGCN(graph, dropout=0.0)
        model.eval()
        x = torch.randn((2, 4, 12, 206), dtype=torch.float32)
        prediction = model(x)
        self.assertEqual(tuple(prediction.shape), (2, 206))
        self.assertTrue(torch.isfinite(prediction).all())
        prediction.mean().backward()
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))


if __name__ == "__main__":
    unittest.main()
