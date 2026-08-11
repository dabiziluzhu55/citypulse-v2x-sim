"""Local sparse dynamic-graph model for the official20 lane206 task.

The reference STGCN used by the existing baseline accepts one fixed GSO.  This
module keeps the same input/output contract and broad temporal configuration,
but replaces the fixed spatial message passing with sparse candidate edges
whose weights are conditioned on each sample's 12-frame traffic history.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np
import torch
from torch import nn

from ...build_dynamic_lane_graph import load_sparse_graph


class TemporalGLU(nn.Module):
    """A same-length temporal convolution with a gated linear unit."""

    def __init__(self, in_channels: int, out_channels: int, kernel_size: int = 3) -> None:
        super().__init__()
        if kernel_size % 2 == 0:
            raise ValueError("kernel_size must be odd for same-length padding")
        self.conv = nn.Conv1d(
            in_channels,
            2 * out_channels,
            kernel_size=kernel_size,
            padding=kernel_size // 2,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [batch, node, time, channel]
        batch, nodes, time, channels = x.shape
        flattened = x.reshape(batch * nodes, time, channels).transpose(1, 2)
        values = self.conv(flattened).transpose(1, 2)
        values = values.reshape(batch, nodes, time, -1)
        left, gate = values.chunk(2, dim=-1)
        return left * torch.sigmoid(gate)


class SparseDynamicGraphConv(nn.Module):
    """Sparse message passing with one weight per directed edge and sample."""

    def __init__(
        self,
        *,
        in_channels: int,
        out_channels: int,
        source_index: torch.Tensor,
        target_index: torch.Tensor,
        node_count: int,
    ) -> None:
        super().__init__()
        self.node_count = int(node_count)
        self.out_channels = int(out_channels)
        self.message = nn.Linear(in_channels, out_channels, bias=False)
        self.register_buffer("source_index", source_index.to(dtype=torch.long))
        self.register_buffer("target_index", target_index.to(dtype=torch.long))

    def _forward_flat(self, x: torch.Tensor, edge_weight: torch.Tensor) -> torch.Tensor:
        # x: [batch, node, channel], edge_weight: [batch, edge]
        batch, nodes, _ = x.shape
        if nodes != self.node_count:
            raise ValueError(f"expected {self.node_count} nodes, got {nodes}")
        if edge_weight.shape != (batch, len(self.source_index)):
            raise ValueError(
                "edge_weight shape must be "
                f"({batch}, {len(self.source_index)}), got {tuple(edge_weight.shape)}"
            )

        projected = self.message(x)
        messages = projected.index_select(1, self.source_index)
        messages = messages * edge_weight.unsqueeze(-1)
        output = x.new_zeros((batch, self.node_count, self.out_channels))
        target = self.target_index.view(1, -1, 1).expand(batch, -1, self.out_channels)
        output.scatter_add_(1, target, messages)

        return output

    def forward(self, x: torch.Tensor, edge_weight: torch.Tensor) -> torch.Tensor:
        if x.ndim == 3:
            return self._forward_flat(x, edge_weight)
        if x.ndim != 4:
            raise ValueError(f"graph input must have rank 3 or 4, got {x.ndim}")

        batch, nodes, time, channels = x.shape
        flattened = x.permute(0, 2, 1, 3).reshape(batch * time, nodes, channels)
        repeated_weights = edge_weight.unsqueeze(1).expand(-1, time, -1).reshape(
            batch * time, -1
        )
        output = self._forward_flat(flattened, repeated_weights)
        return output.reshape(batch, time, nodes, self.out_channels).permute(0, 2, 1, 3)


class DynamicSpatioTemporalBlock(nn.Module):
    def __init__(
        self,
        *,
        in_channels: int,
        graph_channels: int,
        out_channels: int,
        source_index: torch.Tensor,
        target_index: torch.Tensor,
        node_count: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.temporal_in = TemporalGLU(in_channels, out_channels, kernel_size=3)
        self.graph = SparseDynamicGraphConv(
            in_channels=out_channels,
            out_channels=graph_channels,
            source_index=source_index,
            target_index=target_index,
            node_count=node_count,
        )
        self.graph_projection = nn.Linear(graph_channels, out_channels)
        self.temporal_out = TemporalGLU(out_channels, out_channels, kernel_size=3)
        self.residual = (
            nn.Identity()
            if in_channels == out_channels
            else nn.Linear(in_channels, out_channels)
        )
        self.dropout = nn.Dropout(dropout)
        self.norm = nn.LayerNorm(out_channels)

    def forward(self, x: torch.Tensor, edge_weight: torch.Tensor) -> torch.Tensor:
        residual = self.residual(x)
        x = self.temporal_in(x)
        x = self.graph(x, edge_weight)
        x = torch.relu(self.graph_projection(x))
        x = self.temporal_out(x)
        x = self.dropout(x)
        return self.norm(x + residual)


def _edge_features(edge_type: np.ndarray, edge_direction: np.ndarray, is_self_loop: np.ndarray) -> np.ndarray:
    """Encode relation provenance without making it a learned dense graph."""

    relation_bits = np.stack(
        [
            (edge_type & 1) != 0,
            (edge_type & 2) != 0,
            (edge_type & 4) != 0,
        ],
        axis=1,
    ).astype(np.float32)
    direction_one_hot = np.stack(
        [
            edge_direction == -1,
            edge_direction == 0,
            edge_direction == 1,
            edge_direction == 2,
        ],
        axis=1,
    ).astype(np.float32)
    self_loop = np.asarray(is_self_loop, dtype=np.float32).reshape(-1, 1)
    return np.concatenate((relation_bits, direction_one_hot, self_loop), axis=1)


class DynamicLaneSTGCN(nn.Module):
    """Direct 60-second lane forecast with sample-conditioned sparse edges."""

    def __init__(
        self,
        graph: Mapping[str, object] | str,
        *,
        input_features: int = 4,
        history_steps: int = 12,
        dropout: float = 0.5,
        temporal_channels: int = 64,
        graph_channels: int = 16,
        gate_hidden: int = 32,
        gate_mode: str = "dynamic",
    ) -> None:
        super().__init__()
        if isinstance(graph, (str, bytes)):
            graph = load_sparse_graph(Path(graph))
        graph_dict = graph
        nodes = tuple(graph_dict["nodes"])  # type: ignore[arg-type]
        source = np.asarray(graph_dict["source_index"], dtype=np.int64)
        target = np.asarray(graph_dict["target_index"], dtype=np.int64)
        static_weight = np.asarray(graph_dict["static_weight"], dtype=np.float32)
        edge_type = np.asarray(graph_dict["edge_type"], dtype=np.uint8)
        edge_direction = np.asarray(graph_dict["edge_direction"], dtype=np.int8)
        is_self_loop = np.asarray(graph_dict["is_self_loop"], dtype=np.bool_)
        if len(nodes) != 206:
            raise ValueError(f"dynamic lane model requires 206 nodes, got {len(nodes)}")
        if input_features != 4 or history_steps != 12:
            raise ValueError("dynamic lane model v1 requires four features and 12 history steps")
        if gate_mode not in {"dynamic", "fixed_one"}:
            raise ValueError("gate_mode must be 'dynamic' or 'fixed_one'")

        self.node_count = len(nodes)
        self.input_features = int(input_features)
        self.history_steps = int(history_steps)
        self.gate_mode = gate_mode
        self.register_buffer("source_index", torch.from_numpy(source))
        self.register_buffer("target_index", torch.from_numpy(target))
        self.register_buffer("static_weight", torch.from_numpy(static_weight))
        self.register_buffer("is_self_loop", torch.from_numpy(is_self_loop))
        self.register_buffer(
            "edge_features",
            torch.from_numpy(_edge_features(edge_type, edge_direction, is_self_loop)),
        )

        summary_channels = 2 * input_features
        gate_input_channels = 2 * summary_channels + self.edge_features.shape[1]
        self.gate_network = (
            nn.Sequential(
                nn.Linear(gate_input_channels, gate_hidden),
                nn.ReLU(),
                nn.Linear(gate_hidden, 1),
            )
            if gate_mode == "dynamic"
            else None
        )
        self.blocks = nn.ModuleList(
            [
                DynamicSpatioTemporalBlock(
                    in_channels=input_features,
                    graph_channels=graph_channels,
                    out_channels=temporal_channels,
                    source_index=self.source_index,
                    target_index=self.target_index,
                    node_count=self.node_count,
                    dropout=dropout,
                ),
                DynamicSpatioTemporalBlock(
                    in_channels=temporal_channels,
                    graph_channels=graph_channels,
                    out_channels=temporal_channels,
                    source_index=self.source_index,
                    target_index=self.target_index,
                    node_count=self.node_count,
                    dropout=dropout,
                ),
            ]
        )
        self.head = nn.Sequential(
            nn.Linear(temporal_channels, 128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, 1),
        )

    def compute_edge_weights(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        if x.ndim != 4:
            raise ValueError(f"model input must have rank 4, got {x.ndim}")
        batch, features, history, nodes = x.shape
        if (features, history, nodes) != (self.input_features, self.history_steps, self.node_count):
            raise ValueError(
                "model input must have shape "
                f"[batch, {self.input_features}, {self.history_steps}, {self.node_count}], "
                f"got {tuple(x.shape)}"
            )

        latest = x[:, :, -1, :].transpose(1, 2)
        history_mean = x.mean(dim=2).transpose(1, 2)
        summary = torch.cat((latest, history_mean), dim=-1)
        if self.gate_mode == "fixed_one":
            gate = x.new_ones((batch, len(self.source_index)))
        else:
            source_summary = summary.index_select(1, self.source_index)
            target_summary = summary.index_select(1, self.target_index)
            relation = self.edge_features.unsqueeze(0).expand(batch, -1, -1)
            gate_input = torch.cat((source_summary, target_summary, relation), dim=-1)
            gate = 0.5 + torch.sigmoid(self.gate_network(gate_input).squeeze(-1))
            gate = torch.where(self.is_self_loop.unsqueeze(0), torch.ones_like(gate), gate)
        effective_weight = gate * self.static_weight.unsqueeze(0)
        # Normalize once per sample, then reuse this same sparse edge list in
        # every spatial block.  The denominator is the incoming weight sum of
        # each target lane, i.e. row normalization for target aggregation.
        degree = x.new_zeros((batch, self.node_count))
        degree.scatter_add_(
            1,
            self.target_index.view(1, -1).expand(batch, -1),
            effective_weight,
        )
        normalized_weight = effective_weight / degree.index_select(
            1, self.target_index
        ).clamp_min(1e-6)
        return normalized_weight, gate

    def forward(self, x: torch.Tensor, *, return_edge_weights: bool = False):
        effective_weight, gate = self.compute_edge_weights(x)
        hidden = x.permute(0, 3, 2, 1).contiguous()
        # [B, F, T, N] -> [B, N, T, F]
        for block in self.blocks:
            hidden = block(hidden, effective_weight)
        prediction = self.head(hidden[:, :, -1, :]).squeeze(-1)
        if return_edge_weights:
            return prediction, effective_weight, gate
        return prediction


def build_model_from_graph(
    graph_path: str,
    *,
    dropout: float = 0.5,
    temporal_channels: int = 64,
    graph_channels: int = 16,
    gate_hidden: int = 32,
    gate_mode: str = "dynamic",
) -> DynamicLaneSTGCN:
    return DynamicLaneSTGCN(
        load_sparse_graph(Path(graph_path)),
        dropout=dropout,
        temporal_channels=temporal_channels,
        graph_channels=graph_channels,
        gate_hidden=gate_hidden,
        gate_mode=gate_mode,
    )
