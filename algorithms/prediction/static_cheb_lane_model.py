"""Local reference-aligned static Chebyshev STGCN for lane206 controls.

The existing formal baseline imports its layers from an external STGCN
repository.  This module mirrors that repository's tensor layout and block
structure locally so a later dynamic model can change only the spatial path.
The GSO here is fixed; no per-sample dense dynamic matrix is constructed.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Mapping

import numpy as np
import torch
from torch import nn

from .build_dynamic_lane_graph import load_sparse_graph


def _static_adjacency(graph: Mapping[str, object] | str) -> np.ndarray:
    if isinstance(graph, (str, bytes)):
        graph = load_sparse_graph(Path(graph))
    nodes = tuple(graph["nodes"])  # type: ignore[arg-type]
    source = np.asarray(graph["source_index"], dtype=np.int64)
    target = np.asarray(graph["target_index"], dtype=np.int64)
    weight = np.asarray(graph["static_weight"], dtype=np.float32)
    if len(nodes) != 206:
        raise ValueError(f"static Chebyshev lane model requires 206 nodes, got {len(nodes)}")
    adjacency = np.zeros((len(nodes), len(nodes)), dtype=np.float64)
    adjacency[source, target] = weight
    if not np.allclose(adjacency, adjacency.T, atol=1e-6):
        raise ValueError("static Chebyshev control requires a symmetric adjacency")
    if not np.all(np.diag(adjacency) > 0):
        raise ValueError("static Chebyshev control requires positive self-loops")
    return adjacency


def build_chebyshev_gso(adjacency: np.ndarray) -> np.ndarray:
    """Match utility.calc_gso(..., 'sym_norm_lap') and calc_chebynet_gso."""

    adjacency = np.asarray(adjacency, dtype=np.float64)
    if adjacency.ndim != 2 or adjacency.shape[0] != adjacency.shape[1]:
        raise ValueError("adjacency must be a square matrix")
    # The external utility first symmetrizes by taking the larger directional
    # weight.  The official lane graph is already symmetric.
    adjacency = np.maximum(adjacency, adjacency.T)
    identity = np.eye(adjacency.shape[0], dtype=np.float64)
    row_sum = adjacency.sum(axis=1)
    inv_sqrt = np.zeros_like(row_sum)
    positive = row_sum > 0
    inv_sqrt[positive] = np.power(row_sum[positive], -0.5)
    normalized = inv_sqrt[:, None] * adjacency * inv_sqrt[None, :]
    gso = identity - normalized
    eigval_max = float(np.linalg.norm(gso, ord=2))
    if eigval_max <= 1e-12:
        raise ValueError("cannot scale a zero GSO")
    if eigval_max >= 2:
        gso = gso - identity
    else:
        gso = 2 * gso / eigval_max - identity
    return gso.astype(np.float32)


class Align(nn.Module):
    def __init__(self, c_in: int, c_out: int) -> None:
        super().__init__()
        self.c_in = int(c_in)
        self.c_out = int(c_out)
        self.align_conv = (
            nn.Conv2d(c_in, c_out, kernel_size=(1, 1))
            if c_in > c_out
            else None
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.c_in > self.c_out:
            return self.align_conv(x)
        if self.c_in < self.c_out:
            padding = x.new_zeros(
                x.shape[0], self.c_out - self.c_in, x.shape[2], x.shape[3]
            )
            return torch.cat((x, padding), dim=1)
        return x


class TemporalConvLayer(nn.Module):
    """Causal Kt x 1 convolution with the reference GLU residual."""

    def __init__(self, kt: int, c_in: int, c_out: int, act_func: str = "glu") -> None:
        super().__init__()
        if act_func != "glu":
            raise ValueError("the aligned control currently supports act_func='glu' only")
        self.kt = int(kt)
        self.align = Align(c_in, c_out)
        self.causal_conv = nn.Conv2d(c_in, 2 * c_out, kernel_size=(kt, 1))
        self.c_out = int(c_out)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x_in = self.align(x)[:, :, self.kt - 1 :, :]
        convolved = self.causal_conv(x)
        x_p = convolved[:, : self.c_out, :, :]
        x_q = convolved[:, self.c_out :, :, :]
        return (x_p + x_in) * torch.sigmoid(x_q)


class ChebGraphConv(nn.Module):
    def __init__(self, c_in: int, c_out: int, ks: int, gso: torch.Tensor, bias: bool) -> None:
        super().__init__()
        if ks < 1:
            raise ValueError("ks must be a positive integer")
        self.c_in = int(c_in)
        self.c_out = int(c_out)
        self.ks = int(ks)
        self.register_buffer("gso", gso.to(dtype=torch.float32))
        self.weight = nn.Parameter(torch.empty(ks, c_in, c_out))
        self.bias = nn.Parameter(torch.empty(c_out)) if bias else None
        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.kaiming_uniform_(self.weight, a=math.sqrt(5))
        if self.bias is not None:
            fan_in, _ = nn.init._calculate_fan_in_and_fan_out(self.weight)
            bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
            nn.init.uniform_(self.bias, -bound, bound)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Reference layout: x [B, C, T, N] -> [B, T, N, C].
        x = x.permute(0, 2, 3, 1)
        x_list = [x]
        if self.ks >= 2:
            x_list.append(torch.einsum("hi,btij->bthj", self.gso, x))
        for index in range(2, self.ks):
            x_list.append(
                torch.einsum("hi,btij->bthj", 2 * self.gso, x_list[index - 1])
                - x_list[index - 2]
            )
        cheb = torch.stack(x_list, dim=2)
        output = torch.einsum("btkhi,kij->bthj", cheb, self.weight)
        if self.bias is not None:
            output = output + self.bias
        return output


class GraphConvLayer(nn.Module):
    def __init__(self, c_in: int, c_out: int, ks: int, gso: torch.Tensor, bias: bool) -> None:
        super().__init__()
        self.align = Align(c_in, c_out)
        self.cheb = ChebGraphConv(c_out, c_out, ks, gso, bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        aligned = self.align(x)
        graph = self.cheb(aligned).permute(0, 3, 1, 2)
        return graph + aligned


class STConvBlock(nn.Module):
    def __init__(
        self,
        kt: int,
        ks: int,
        node_count: int,
        c_in: int,
        channels: tuple[int, int, int],
        gso: torch.Tensor,
        dropout: float,
    ) -> None:
        super().__init__()
        self.tmp_conv1 = TemporalConvLayer(kt, c_in, channels[0])
        self.graph_conv = GraphConvLayer(channels[0], channels[1], ks, gso, True)
        self.tmp_conv2 = TemporalConvLayer(kt, channels[1], channels[2])
        self.layer_norm = nn.LayerNorm([node_count, channels[2]], eps=1e-12)
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.tmp_conv1(x)
        x = self.relu(self.graph_conv(x))
        x = self.tmp_conv2(x)
        x = self.layer_norm(x.permute(0, 2, 3, 1)).permute(0, 3, 1, 2)
        return self.dropout(x)


class OutputBlock(nn.Module):
    def __init__(
        self,
        ko: int,
        c_in: int,
        channels: tuple[int, int],
        node_count: int,
        dropout: float,
    ) -> None:
        super().__init__()
        self.tmp_conv = TemporalConvLayer(ko, c_in, channels[0])
        self.layer_norm = nn.LayerNorm([node_count, channels[0]], eps=1e-12)
        self.fc1 = nn.Linear(channels[0], channels[1])
        self.fc2 = nn.Linear(channels[1], 1)
        self.relu = nn.ReLU()
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.tmp_conv(x)
        x = self.layer_norm(x.permute(0, 2, 3, 1))
        x = self.dropout(self.relu(self.fc1(x)))
        return self.fc2(x).permute(0, 3, 1, 2)


class StaticChebLaneSTGCN(nn.Module):
    """Reference-aligned local static STGCN control."""

    def __init__(
        self,
        graph: Mapping[str, object] | str,
        *,
        history_steps: int = 12,
        kt: int = 3,
        ks: int = 3,
        dropout: float = 0.5,
    ) -> None:
        super().__init__()
        if history_steps != 12 or kt != 3 or ks != 3:
            raise ValueError("the aligned lane control requires n_his=12, Kt=3 and Ks=3")
        adjacency = _static_adjacency(graph)
        self.node_count = adjacency.shape[0]
        self.register_buffer("gso", torch.from_numpy(build_chebyshev_gso(adjacency)))
        self.blocks = nn.ModuleList(
            [
                STConvBlock(kt, ks, self.node_count, 4, (64, 16, 64), self.gso, dropout),
                STConvBlock(kt, ks, self.node_count, 64, (64, 16, 64), self.gso, dropout),
            ]
        )
        # 12 - 2 blocks * 2 temporal convolutions * (Kt - 1) = 4.
        self.output = OutputBlock(4, 64, (128, 128), self.node_count, dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.ndim != 4 or tuple(x.shape[1:]) != (4, 12, self.node_count):
            raise ValueError(
                f"expected input [batch, 4, 12, {self.node_count}], got {tuple(x.shape)}"
            )
        hidden = x
        for block in self.blocks:
            hidden = block(hidden)
        output = self.output(hidden)
        return output[:, 0, 0, :]


def build_model_from_graph(
    graph_path: str,
    *,
    dropout: float = 0.5,
) -> StaticChebLaneSTGCN:
    return StaticChebLaneSTGCN(graph_path, dropout=dropout)
