"""Minimal static Chebyshev lane/junction hierarchical STGCN.

The lane-level path deliberately reuses the local reference-aligned static
Chebyshev STGCN blocks.  After the lane blocks, hidden lane representations
are pooled to 20 junctions, passed through one fixed junction-level
Chebyshev graph convolution, broadcast back to the owning lanes, and added as
a bounded residual before the existing output head.

The junction residual scale is initialized to zero.  Therefore a freshly
constructed model starts with the same lane-level computation as the local
``static_cheb`` control, while training can learn whether the junction
context is useful.  The public input/output contract remains
``[batch, 4, 12, 206] -> [batch, 206]``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np
import torch
from torch import nn

from ...build_dynamic_lane_graph import load_sparse_graph
from .build_lane_junction_mapping import load_lane_junction_mapping
from ...static_cheb_lane_model import (
    GraphConvLayer,
    OutputBlock,
    STConvBlock,
    _static_adjacency,
    build_chebyshev_gso,
)


class StaticHierarchicalLaneSTGCN(nn.Module):
    """Reference-aligned lane STGCN with a fixed junction context residual."""

    def __init__(
        self,
        graph: Mapping[str, object] | str,
        mapping: Mapping[str, object] | str,
        *,
        history_steps: int = 12,
        kt: int = 3,
        ks: int = 3,
        dropout: float = 0.5,
        temporal_channels: int = 64,
        graph_channels: int = 16,
    ) -> None:
        super().__init__()
        if history_steps != 12 or kt != 3 or ks != 3:
            raise ValueError("the hierarchical lane model requires n_his=12, Kt=3 and Ks=3")
        if isinstance(graph, (str, bytes)):
            graph = load_sparse_graph(Path(graph))
        if isinstance(mapping, (str, bytes)):
            mapping = load_lane_junction_mapping(Path(mapping))

        lane_adjacency = _static_adjacency(graph)
        lane_count = lane_adjacency.shape[0]
        lane_order = tuple(graph["nodes"])  # type: ignore[arg-type]
        mapping_lane_order = tuple(mapping["lane_order"])  # type: ignore[arg-type]
        if lane_count != 206 or lane_order != mapping_lane_order:
            raise ValueError("lane graph and hierarchy mapping must share the frozen 206-node order")

        pooling = np.asarray(mapping["pooling_matrix"], dtype=np.float32)
        broadcast = np.asarray(mapping["broadcast_matrix"], dtype=np.float32)
        junction_adjacency = np.asarray(mapping["junction_adjacency"], dtype=np.float32)
        junction_order = tuple(mapping["junction_order"])  # type: ignore[arg-type]
        if pooling.shape != (len(junction_order), lane_count):
            raise ValueError("hierarchy pooling matrix has an incompatible shape")
        if broadcast.shape != (lane_count, len(junction_order)):
            raise ValueError("hierarchy broadcast matrix has an incompatible shape")
        if len(junction_order) != 20:
            raise ValueError(f"hierarchical lane model requires 20 junctions, got {len(junction_order)}")
        if junction_adjacency.shape != (20, 20):
            raise ValueError("hierarchy junction adjacency must have shape [20, 20]")

        self.node_count = lane_count
        self.junction_count = len(junction_order)
        self.input_features = 4
        self.history_steps = history_steps
        self.register_buffer("pooling_matrix", torch.from_numpy(pooling))
        self.register_buffer("broadcast_matrix", torch.from_numpy(broadcast))

        lane_gso = torch.from_numpy(build_chebyshev_gso(lane_adjacency))
        junction_gso = torch.from_numpy(build_chebyshev_gso(junction_adjacency))
        self.register_buffer("lane_gso", lane_gso)
        self.register_buffer("junction_gso", junction_gso)

        self.lane_blocks = nn.ModuleList(
            [
                STConvBlock(
                    kt,
                    ks,
                    lane_count,
                    4,
                    (temporal_channels, graph_channels, temporal_channels),
                    self.lane_gso,
                    dropout,
                ),
                STConvBlock(
                    kt,
                    ks,
                    lane_count,
                    temporal_channels,
                    (temporal_channels, graph_channels, temporal_channels),
                    self.lane_gso,
                    dropout,
                ),
            ]
        )
        self.junction_graph = GraphConvLayer(
            temporal_channels,
            temporal_channels,
            ks,
            self.junction_gso,
            True,
        )
        self.junction_norm = nn.LayerNorm(
            [self.junction_count, temporal_channels], eps=1e-12
        )
        self.junction_relu = nn.ReLU()
        self.junction_dropout = nn.Dropout(dropout)
        # Start exactly at the static Chebyshev control.  tanh keeps the
        # learned hierarchy contribution bounded while allowing gradients to
        # decide whether it should be used.
        self.junction_residual_logit = nn.Parameter(torch.zeros(()))
        # 12 - 2 lane blocks * 2 temporal convolutions * (Kt - 1) = 4.
        self.output = OutputBlock(4, temporal_channels, (128, 128), lane_count, dropout)

    def _junction_context(self, lane_hidden: torch.Tensor) -> torch.Tensor:
        # lane_hidden: [B, C, T, 206] -> [B, C, T, 20]
        junction_hidden = torch.einsum(
            "jn,bctn->bctj", self.pooling_matrix, lane_hidden
        )
        junction_hidden = self.junction_graph(junction_hidden)
        junction_hidden = self.junction_relu(junction_hidden)
        junction_hidden = self.junction_norm(
            junction_hidden.permute(0, 2, 3, 1)
        ).permute(0, 3, 1, 2)
        return self.junction_dropout(junction_hidden)

    def forward(self, x: torch.Tensor, *, return_hierarchy: bool = False):
        if x.ndim != 4 or tuple(x.shape[1:]) != (4, 12, self.node_count):
            raise ValueError(
                f"expected input [batch, 4, 12, {self.node_count}], got {tuple(x.shape)}"
            )
        lane_hidden = x
        for block in self.lane_blocks:
            lane_hidden = block(lane_hidden)

        junction_hidden = self._junction_context(lane_hidden)
        lane_context = torch.einsum(
            "nj,bctj->bctn", self.broadcast_matrix, junction_hidden
        )
        residual_scale = torch.tanh(self.junction_residual_logit)
        fused = lane_hidden + residual_scale * lane_context
        output = self.output(fused)
        prediction = output[:, 0, 0, :]
        if return_hierarchy:
            return prediction, junction_hidden, lane_context, residual_scale
        return prediction


def build_model_from_graph(
    graph_path: str,
    mapping_path: str,
    *,
    dropout: float = 0.5,
    temporal_channels: int = 64,
    graph_channels: int = 16,
) -> StaticHierarchicalLaneSTGCN:
    return StaticHierarchicalLaneSTGCN(
        load_sparse_graph(Path(graph_path)),
        load_lane_junction_mapping(Path(mapping_path)),
        dropout=dropout,
        temporal_channels=temporal_channels,
        graph_channels=graph_channels,
    )
