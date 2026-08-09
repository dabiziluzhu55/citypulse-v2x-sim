"""Sparse dynamic Chebyshev STGCN for the lane206 experiment.

This model keeps the reference STGCN temporal blocks, output head, and
Chebyshev spatial recurrence.  Only the adjacency used by that recurrence is
conditioned on the current sample's traffic history.  The gate is shared by
the two directions of an undirected candidate pair, so the normalized
adjacency and the resulting Laplacian remain symmetric.

No per-sample dense 206 x 206 matrix is materialized.  The normalized
adjacency is represented by a sparse edge list and applied with scatter-add.
When ``gate_mode='fixed_one'``, the sparse operator is the same fixed
symmetrically normalized Laplacian used by the local static Chebyshev
control, up to normal floating-point round-off.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping

import numpy as np
import torch
from torch import nn

from ...build_dynamic_lane_graph import load_sparse_graph
from ...static_cheb_lane_model import Align, OutputBlock, TemporalConvLayer, _static_adjacency


def _pair_metadata(
    source: np.ndarray,
    target: np.ndarray,
    edge_type: np.ndarray,
    node_count: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return one relation record per undirected non-self candidate pair."""

    self_loop = source == target
    nonself_indices = np.flatnonzero(~self_loop)
    if len(nonself_indices) == 0:
        raise ValueError("dynamic Chebyshev graph needs non-self candidate edges")
    keys = np.minimum(source[nonself_indices], target[nonself_indices]) * node_count
    keys += np.maximum(source[nonself_indices], target[nonself_indices])
    unique_keys, first_indices, inverse, counts = np.unique(
        keys,
        return_index=True,
        return_inverse=True,
        return_counts=True,
    )
    if not np.all(counts == 2):
        raise ValueError("dynamic Chebyshev graph requires both directions of every pair")

    nonself_types = edge_type[nonself_indices]
    pair_relation = nonself_types[first_indices]
    if not np.array_equal(nonself_types, pair_relation[inverse]):
        raise ValueError("both directions of a pair must share the same relation mask")

    pair_source = (unique_keys // node_count).astype(np.int64)
    pair_target = (unique_keys % node_count).astype(np.int64)
    edge_to_pair = np.zeros(len(source), dtype=np.int64)
    edge_to_pair[nonself_indices] = inverse
    return pair_source, pair_target, pair_relation, edge_to_pair, self_loop


def _relation_features(relation: np.ndarray) -> np.ndarray:
    return np.stack(
        [
            (relation & 1) != 0,
            (relation & 2) != 0,
            (relation & 4) != 0,
        ],
        axis=1,
    ).astype(np.float32)


def _chebyshev_scale(adjacency: np.ndarray) -> float:
    """Return the fixed scale used by the reference Chebyshev GSO."""

    adjacency = np.maximum(adjacency, adjacency.T).astype(np.float64)
    identity = np.eye(adjacency.shape[0], dtype=np.float64)
    degree = adjacency.sum(axis=1)
    inv_sqrt = np.zeros_like(degree)
    positive = degree > 0
    inv_sqrt[positive] = np.power(degree[positive], -0.5)
    normalized = inv_sqrt[:, None] * adjacency * inv_sqrt[None, :]
    laplacian = identity - normalized
    eigval_max = float(np.linalg.norm(laplacian, ord=2))
    if eigval_max <= 1e-12:
        raise ValueError("cannot scale a zero GSO")
    return 1.0 if eigval_max >= 2.0 else 2.0 / eigval_max


class DynamicChebGraphConv(nn.Module):
    """Chebyshev graph convolution using a sample-conditioned sparse GSO."""

    def __init__(
        self,
        c_in: int,
        c_out: int,
        ks: int,
        gso_scale: float,
        source_index: torch.Tensor,
        target_index: torch.Tensor,
        node_count: int,
        bias: bool,
    ) -> None:
        super().__init__()
        if ks < 1:
            raise ValueError("ks must be a positive integer")
        self.c_in = int(c_in)
        self.c_out = int(c_out)
        self.ks = int(ks)
        self.gso_scale = float(gso_scale)
        self.node_count = int(node_count)
        self.register_buffer("source_index", source_index.to(dtype=torch.long))
        self.register_buffer("target_index", target_index.to(dtype=torch.long))
        self.weight = nn.Parameter(torch.empty(ks, c_in, c_out))
        self.bias = nn.Parameter(torch.empty(c_out)) if bias else None
        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.kaiming_uniform_(self.weight, a=math.sqrt(5))
        if self.bias is not None:
            fan_in, _ = nn.init._calculate_fan_in_and_fan_out(self.weight)
            bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
            nn.init.uniform_(self.bias, -bound, bound)

    def _apply_gso_flat(self, x: torch.Tensor, normalized_weight: torch.Tensor) -> torch.Tensor:
        """Apply ``scale * (I - A_norm) - I`` to ``[batch, node, channel]``."""

        batch, nodes, _ = x.shape
        if nodes != self.node_count:
            raise ValueError(f"expected {self.node_count} nodes, got {nodes}")
        if normalized_weight.shape != (batch, len(self.source_index)):
            raise ValueError(
                "normalized_weight shape must be "
                f"({batch}, {len(self.source_index)}), got {tuple(normalized_weight.shape)}"
            )

        source_values = x.index_select(1, self.source_index)
        source_values = source_values * normalized_weight.unsqueeze(-1)
        target = self.target_index.view(1, -1, 1).expand(batch, -1, x.shape[-1])
        adjacency_x = x.new_zeros((batch, self.node_count, x.shape[-1]))
        adjacency_x.scatter_add_(1, target, source_values)
        return (self.gso_scale - 1.0) * x - self.gso_scale * adjacency_x

    def _apply_gso(self, x: torch.Tensor, normalized_weight: torch.Tensor) -> torch.Tensor:
        batch, time, nodes, channels = x.shape
        flattened = x.reshape(batch * time, nodes, channels)
        repeated_weight = normalized_weight.unsqueeze(1).expand(-1, time, -1).reshape(
            batch * time, -1
        )
        output = self._apply_gso_flat(flattened, repeated_weight)
        return output.reshape(batch, time, nodes, channels)

    def forward(self, x: torch.Tensor, normalized_weight: torch.Tensor) -> torch.Tensor:
        # Reference layout: x [B, C, T, N] -> [B, T, N, C].
        x = x.permute(0, 2, 3, 1)
        x_list = [x]
        if self.ks >= 2:
            x_list.append(self._apply_gso(x, normalized_weight))
        for index in range(2, self.ks):
            x_list.append(
                2 * self._apply_gso(x_list[index - 1], normalized_weight)
                - x_list[index - 2]
            )
        cheb = torch.stack(x_list, dim=2)
        output = torch.einsum("btkhi,kij->bthj", cheb, self.weight)
        if self.bias is not None:
            output = output + self.bias
        return output


class DynamicChebGraphConvLayer(nn.Module):
    def __init__(
        self,
        c_in: int,
        c_out: int,
        ks: int,
        gso_scale: float,
        source_index: torch.Tensor,
        target_index: torch.Tensor,
        node_count: int,
    ) -> None:
        super().__init__()
        self.align = Align(c_in, c_out)
        self.cheb = DynamicChebGraphConv(
            c_out,
            c_out,
            ks,
            gso_scale,
            source_index,
            target_index,
            node_count,
            True,
        )

    def forward(self, x: torch.Tensor, normalized_weight: torch.Tensor) -> torch.Tensor:
        aligned = self.align(x)
        graph = self.cheb(aligned, normalized_weight).permute(0, 3, 1, 2)
        return graph + aligned


class DynamicChebSTConvBlock(nn.Module):
    def __init__(
        self,
        kt: int,
        ks: int,
        node_count: int,
        c_in: int,
        channels: tuple[int, int, int],
        gso_scale: float,
        source_index: torch.Tensor,
        target_index: torch.Tensor,
        dropout: float,
    ) -> None:
        super().__init__()
        self.tmp_conv1 = TemporalConvLayer(kt, c_in, channels[0])
        self.graph_conv = DynamicChebGraphConvLayer(
            channels[0],
            channels[1],
            ks,
            gso_scale,
            source_index,
            target_index,
            node_count,
        )
        self.tmp_conv2 = TemporalConvLayer(kt, channels[1], channels[2])
        self.layer_norm = nn.LayerNorm([node_count, channels[2]], eps=1e-12)
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor, normalized_weight: torch.Tensor) -> torch.Tensor:
        x = self.tmp_conv1(x)
        x = self.relu(self.graph_conv(x, normalized_weight))
        x = self.tmp_conv2(x)
        x = self.layer_norm(x.permute(0, 2, 3, 1)).permute(0, 3, 1, 2)
        return self.dropout(x)


class DynamicChebLaneSTGCN(nn.Module):
    """Reference-aligned STGCN with sample-conditioned sparse Chebyshev GSO."""

    def __init__(
        self,
        graph: Mapping[str, object] | str,
        *,
        input_features: int = 4,
        history_steps: int = 12,
        kt: int = 3,
        ks: int = 3,
        dropout: float = 0.5,
        temporal_channels: int = 64,
        graph_channels: int = 16,
        gate_hidden: int = 32,
        gate_half_range: float = 0.5,
        gate_mode: str = "dynamic",
    ) -> None:
        super().__init__()
        if isinstance(graph, (str, bytes)):
            graph = load_sparse_graph(Path(graph))
        if input_features != 4 or history_steps != 12 or kt != 3 or ks != 3:
            raise ValueError("dynamic Chebyshev lane v2 requires 4 features, 12 history steps, Kt=3 and Ks=3")
        if gate_mode not in {"dynamic", "fixed_one"}:
            raise ValueError("gate_mode must be 'dynamic' or 'fixed_one'")
        if gate_half_range <= 0 or gate_half_range > 1:
            raise ValueError("gate_half_range must be in (0, 1]")

        graph_dict = graph
        nodes = tuple(graph_dict["nodes"])  # type: ignore[arg-type]
        source = np.asarray(graph_dict["source_index"], dtype=np.int64)
        target = np.asarray(graph_dict["target_index"], dtype=np.int64)
        static_weight = np.asarray(graph_dict["static_weight"], dtype=np.float32)
        edge_type = np.asarray(graph_dict["edge_type"], dtype=np.uint8)
        node_count = len(nodes)
        if node_count != 206:
            raise ValueError(f"dynamic Chebyshev lane model requires 206 nodes, got {node_count}")
        if len(source) != len(target) or len(source) != len(static_weight) or len(source) != len(edge_type):
            raise ValueError("dynamic Chebyshev graph edge arrays have inconsistent lengths")

        pair_source, pair_target, pair_relation, edge_to_pair, self_loop = _pair_metadata(
            source, target, edge_type, node_count
        )
        adjacency = _static_adjacency(graph_dict)
        gso_scale = _chebyshev_scale(adjacency)

        self.node_count = node_count
        self.input_features = input_features
        self.history_steps = history_steps
        self.gate_mode = gate_mode
        self.gate_half_range = float(gate_half_range)
        self.gso_scale = gso_scale
        self.register_buffer("source_index", torch.from_numpy(source))
        self.register_buffer("target_index", torch.from_numpy(target))
        self.register_buffer("static_weight", torch.from_numpy(static_weight))
        self.register_buffer("is_self_loop", torch.from_numpy(self_loop))
        self.register_buffer("edge_to_pair", torch.from_numpy(edge_to_pair))
        self.register_buffer("pair_source_index", torch.from_numpy(pair_source))
        self.register_buffer("pair_target_index", torch.from_numpy(pair_target))
        self.register_buffer("pair_relation_features", torch.from_numpy(_relation_features(pair_relation)))

        summary_channels = 2 * input_features
        gate_input_channels = 2 * summary_channels + self.pair_relation_features.shape[1]
        self.gate_network = (
            nn.Sequential(
                nn.Linear(gate_input_channels, gate_hidden),
                nn.ReLU(),
                nn.Linear(gate_hidden, 1),
            )
            if gate_mode == "dynamic"
            else None
        )
        if self.gate_network is not None:
            # Start exactly at the static operator.  The final layer is still
            # trainable, so the model can learn a bounded residual after the
            # first optimizer steps.
            nn.init.zeros_(self.gate_network[-1].weight)
            nn.init.zeros_(self.gate_network[-1].bias)

        self.blocks = nn.ModuleList(
            [
                DynamicChebSTConvBlock(
                    kt,
                    ks,
                    node_count,
                    4,
                    (temporal_channels, graph_channels, temporal_channels),
                    gso_scale,
                    self.source_index,
                    self.target_index,
                    dropout,
                ),
                DynamicChebSTConvBlock(
                    kt,
                    ks,
                    node_count,
                    temporal_channels,
                    (temporal_channels, graph_channels, temporal_channels),
                    gso_scale,
                    self.source_index,
                    self.target_index,
                    dropout,
                ),
            ]
        )
        self.output = OutputBlock(4, temporal_channels, (128, 128), node_count, dropout)

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
            edge_gate = x.new_ones((batch, len(self.source_index)))
        else:
            source_summary = summary.index_select(1, self.pair_source_index)
            target_summary = summary.index_select(1, self.pair_target_index)
            pair_input = torch.cat(
                (
                    source_summary + target_summary,
                    torch.abs(source_summary - target_summary),
                    self.pair_relation_features.unsqueeze(0).expand(batch, -1, -1),
                ),
                dim=-1,
            )
            pair_gate = 1.0 + self.gate_half_range * torch.tanh(
                self.gate_network(pair_input).squeeze(-1)
            )
            edge_gate = torch.where(
                self.is_self_loop.unsqueeze(0),
                torch.ones_like(self.is_self_loop, dtype=x.dtype).unsqueeze(0),
                pair_gate.index_select(1, self.edge_to_pair),
            )

        effective_weight = edge_gate * self.static_weight.unsqueeze(0)
        degree = x.new_zeros((batch, self.node_count))
        degree.scatter_add_(
            1,
            self.target_index.view(1, -1).expand(batch, -1),
            effective_weight,
        )
        denominator = torch.sqrt(
            degree.index_select(1, self.source_index)
            * degree.index_select(1, self.target_index)
        ).clamp_min(1e-6)
        normalized_weight = effective_weight / denominator
        return normalized_weight, edge_gate

    def forward(self, x: torch.Tensor, *, return_edge_weights: bool = False):
        normalized_weight, edge_gate = self.compute_edge_weights(x)
        hidden = x
        for block in self.blocks:
            hidden = block(hidden, normalized_weight)
        output = self.output(hidden)
        prediction = output[:, 0, 0, :]
        if return_edge_weights:
            return prediction, normalized_weight, edge_gate
        return prediction


def build_model_from_graph(
    graph_path: str,
    *,
    dropout: float = 0.5,
    temporal_channels: int = 64,
    graph_channels: int = 16,
    gate_hidden: int = 32,
    gate_half_range: float = 0.5,
    gate_mode: str = "dynamic",
) -> DynamicChebLaneSTGCN:
    return DynamicChebLaneSTGCN(
        load_sparse_graph(Path(graph_path)),
        dropout=dropout,
        temporal_channels=temporal_channels,
        graph_channels=graph_channels,
        gate_hidden=gate_hidden,
        gate_half_range=gate_half_range,
        gate_mode=gate_mode,
    )
