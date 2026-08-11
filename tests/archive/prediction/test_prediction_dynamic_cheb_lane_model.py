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
class DynamicChebLaneModelTests(unittest.TestCase):
    def test_gate_symmetry_static_operator_and_shapes(self):
        from algorithms.prediction.archive.experiments.dynamic_cheb_lane_model import DynamicChebLaneSTGCN
        from algorithms.prediction.static_cheb_lane_model import (
            _static_adjacency,
            build_chebyshev_gso,
        )

        graph = _small_symmetric_graph()
        torch.manual_seed(42)
        fixed = DynamicChebLaneSTGCN(
            graph,
            dropout=0.0,
            temporal_channels=8,
            graph_channels=4,
            gate_hidden=8,
            gate_mode="fixed_one",
        )
        fixed.eval()
        x = torch.randn((2, 4, 12, 206), dtype=torch.float32)
        prediction, normalized_weight, gate = fixed(x, return_edge_weights=True)
        self.assertEqual(tuple(prediction.shape), (2, 206))
        self.assertTrue(torch.allclose(gate, torch.ones_like(gate)))
        self.assertTrue(torch.isfinite(prediction).all())
        self.assertTrue(torch.allclose(normalized_weight[:, 206], normalized_weight[:, 207]))

        cheb = fixed.blocks[0].graph_conv.cheb
        probe = torch.randn((2, 206, 3), dtype=torch.float32)
        actual = cheb._apply_gso_flat(probe, normalized_weight)
        dense_gso = torch.from_numpy(build_chebyshev_gso(_static_adjacency(graph)))
        expected = torch.einsum("ij,bjc->bic", dense_gso, probe)
        self.assertTrue(torch.allclose(actual, expected, atol=2e-5, rtol=2e-5))

        dynamic = DynamicChebLaneSTGCN(
            graph,
            dropout=0.0,
            temporal_channels=8,
            graph_channels=4,
            gate_hidden=8,
        )
        with torch.no_grad():
            dynamic.gate_network[-1].weight.fill_(0.05)
        dynamic.eval()
        conditioned = torch.zeros((2, 4, 12, 206), dtype=torch.float32)
        conditioned[1, 0, :, 0] = 1.0
        dynamic_prediction, dynamic_weight, dynamic_gate = dynamic(
            conditioned,
            return_edge_weights=True,
        )
        self.assertEqual(tuple(dynamic_prediction.shape), (2, 206))
        self.assertTrue(torch.all(dynamic_gate >= 0.5))
        self.assertTrue(torch.all(dynamic_gate <= 1.5))
        self.assertTrue(torch.allclose(dynamic_gate[:, 206], dynamic_gate[:, 207]))
        self.assertFalse(torch.allclose(dynamic_gate[0, 206:], dynamic_gate[1, 206:]))
        self.assertFalse(torch.allclose(dynamic_weight[0], dynamic_weight[1]))
        dynamic_prediction.mean().backward()
        self.assertTrue(any(parameter.grad is not None for parameter in dynamic.parameters()))

        narrow = DynamicChebLaneSTGCN(
            graph,
            dropout=0.0,
            temporal_channels=8,
            graph_channels=4,
            gate_hidden=8,
            gate_half_range=0.2,
        )
        with torch.no_grad():
            narrow.gate_network[-1].weight.fill_(10.0)
        narrow.eval()
        _, _, narrow_gate = narrow(conditioned, return_edge_weights=True)
        self.assertTrue(torch.all(narrow_gate >= 0.8))
        self.assertTrue(torch.all(narrow_gate <= 1.2))


if __name__ == "__main__":
    unittest.main()
