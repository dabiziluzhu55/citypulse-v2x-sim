import unittest

import numpy as np

try:
    import torch
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    torch = None


def _small_graph_and_directional():
    nodes = tuple(f"lane_{index:03d}_0" for index in range(206))
    source = np.concatenate((np.arange(206), np.array([0, 1])))
    target = np.concatenate((np.arange(206), np.array([1, 0])))
    graph = {
        "nodes": nodes,
        "source_index": source,
        "target_index": target,
        "static_weight": np.concatenate(
            (np.ones(206, dtype=np.float32), np.ones(2, dtype=np.float32))
        ),
    }
    downstream = np.zeros((206, 206), dtype=np.float32)
    downstream[1, 0] = 1.0
    directional = {
        "nodes": nodes,
        "downstream_normalized": downstream,
        "upstream_normalized": downstream.T.copy(),
    }
    return graph, directional


@unittest.skipUnless(torch is not None, "PyTorch is not installed in this environment")
class StaticDirectionalLaneModelTests(unittest.TestCase):
    def test_shape_and_zero_initialized_directional_residual(self):
        from algorithms.prediction.static_directional_lane_model import (
            StaticDirectionalLaneSTGCN,
        )

        graph, directional = _small_graph_and_directional()
        torch.manual_seed(42)
        model = StaticDirectionalLaneSTGCN(graph, directional, dropout=0.0)
        model.eval()
        x = torch.randn((2, 4, 12, 206), dtype=torch.float32)
        hidden = x
        for block, residual in zip(model.blocks, model.directional_residuals):
            hidden = block(hidden)
            self.assertAlmostEqual(float(residual.downstream_logit.item()), 0.0)
            self.assertAlmostEqual(float(residual.upstream_logit.item()), 0.0)
            self.assertTrue(torch.allclose(residual(hidden), hidden))
            hidden = residual(hidden)
        prediction = model(x)
        self.assertEqual(tuple(prediction.shape), (2, 206))
        self.assertTrue(torch.isfinite(prediction).all())
        prediction.mean().backward()
        self.assertTrue(any(parameter.grad is not None for parameter in model.parameters()))

    def test_relation_separated_branches_keep_static_initialization(self):
        from algorithms.prediction.static_directional_lane_model import (
            StaticDirectionalLaneSTGCN,
        )

        graph, directional = _small_graph_and_directional()
        directional = {
            **directional,
            "has_relation_branches": True,
            "downstream_direct_normalized": directional["downstream_normalized"],
            "downstream_next_target_normalized": np.zeros((206, 206), dtype=np.float32),
            "upstream_direct_normalized": directional["upstream_normalized"],
            "upstream_next_target_normalized": np.zeros((206, 206), dtype=np.float32),
        }
        torch.manual_seed(42)
        model = StaticDirectionalLaneSTGCN(graph, directional, dropout=0.0)
        model.eval()
        x = torch.randn((2, 4, 12, 206), dtype=torch.float32)
        prediction = model(x)
        self.assertEqual(tuple(prediction.shape), (2, 206))
        for residual in model.directional_residuals:
            self.assertTrue(residual.has_relation_branches)
            for name in (
                "downstream_direct",
                "downstream_next_target",
                "upstream_direct",
                "upstream_next_target",
            ):
                self.assertAlmostEqual(float(getattr(residual, f"{name}_logit").item()), 0.0)
        self.assertTrue(torch.isfinite(prediction).all())


if __name__ == "__main__":
    unittest.main()
