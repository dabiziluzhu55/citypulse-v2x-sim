import unittest

import numpy as np

try:
    import torch
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    torch = None


@unittest.skipUnless(torch is not None, "PyTorch is not installed in this environment")
class DynamicLaneModelTests(unittest.TestCase):
    def test_shapes_gate_bounds_and_traffic_conditioning(self):
        from algorithms.prediction.archive.experiments.dynamic_lane_model import DynamicLaneSTGCN

        nodes = tuple(f"lane_{index:03d}_0" for index in range(206))
        source = np.concatenate((np.arange(206), np.array([0, 1])))
        target = np.concatenate((np.arange(206), np.array([1, 0])))
        graph = {
            "nodes": nodes,
            "nodes_sha256": "unused-in-direct-constructor",
            "source_index": source,
            "target_index": target,
            "static_weight": np.ones(len(source), dtype=np.float32),
            "edge_type": np.concatenate((np.zeros(206, dtype=np.uint8), np.ones(2, dtype=np.uint8))),
            "edge_direction": np.zeros(len(source), dtype=np.int8),
            "is_self_loop": np.concatenate((np.ones(206, dtype=np.bool_), np.zeros(2, dtype=np.bool_))),
        }
        torch.manual_seed(42)
        model = DynamicLaneSTGCN(
            graph,
            dropout=0.0,
            temporal_channels=8,
            graph_channels=4,
            gate_hidden=8,
        )
        model.eval()
        x = torch.zeros((2, 4, 12, 206), dtype=torch.float32)
        x[1, 0, :, 0] = 1.0
        prediction, edge_weight, gate = model(x, return_edge_weights=True)
        self.assertEqual(tuple(prediction.shape), (2, 206))
        self.assertEqual(tuple(edge_weight.shape), (2, len(source)))
        self.assertTrue(torch.all(gate >= 0.5))
        self.assertTrue(torch.all(gate <= 1.5))
        self.assertTrue(torch.allclose(gate[:, :206], torch.ones_like(gate[:, :206])))
        self.assertFalse(torch.allclose(gate[0, 206:], gate[1, 206:]))
        for node in range(206):
            incoming = edge_weight[:, target == node].sum(dim=1)
            self.assertTrue(torch.allclose(incoming, torch.ones_like(incoming)))
        prediction.mean().backward()
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))

        torch.manual_seed(42)
        repeated = DynamicLaneSTGCN(
            graph,
            dropout=0.0,
            temporal_channels=8,
            graph_channels=4,
            gate_hidden=8,
        )
        repeated.eval()
        repeated_prediction = repeated(x)
        self.assertTrue(torch.allclose(prediction.detach(), repeated_prediction))

        fixed = DynamicLaneSTGCN(
            graph,
            dropout=0.0,
            temporal_channels=8,
            graph_channels=4,
            gate_hidden=8,
            gate_mode="fixed_one",
        )
        fixed.eval()
        fixed_prediction, fixed_weights, fixed_gate = fixed(x, return_edge_weights=True)
        self.assertTrue(torch.allclose(fixed_gate, torch.ones_like(fixed_gate)))
        self.assertTrue(torch.allclose(fixed_weights[0], fixed_weights[1]))
        self.assertEqual(tuple(fixed_prediction.shape), (2, 206))


if __name__ == "__main__":
    unittest.main()
