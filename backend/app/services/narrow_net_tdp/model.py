# NarrowNet-TDP在线推理用Cheb时空块与方向残差模型

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
import torch
from torch import nn

from .graph_io import build_chebyshev_gso, static_adjacency_from_sparse


class Align(nn.Module):
    def __init__(self, c_in: int, c_out: int) -> None:
        super().__init__()
        self.c_in = int(c_in)
        self.c_out = int(c_out)
        self.align_conv = (
            nn.Conv2d(c_in, c_out, kernel_size=(1, 1)) if c_in > c_out else None
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
    def __init__(self, kt: int, c_in: int, c_out: int, act_func: str = "glu") -> None:
        super().__init__()
        if act_func != "glu":
            raise ValueError("NarrowNet-TDP only supports act_func='glu'")
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


class DirectionalLaneResidual(nn.Module):
    def __init__(
        self,
        channels: int,
        downstream: torch.Tensor,
        upstream: torch.Tensor,
        *,
        downstream_direct: torch.Tensor | None = None,
        downstream_next_target: torch.Tensor | None = None,
        upstream_direct: torch.Tensor | None = None,
        upstream_next_target: torch.Tensor | None = None,
        max_scale: float = 0.25,
    ) -> None:
        super().__init__()
        if downstream.ndim != 2 or downstream.shape != upstream.shape:
            raise ValueError("directional message matrices must have the same square shape")
        if max_scale <= 0:
            raise ValueError("max_scale must be positive")
        self.channels = int(channels)
        self.node_count = int(downstream.shape[0])
        self.max_scale = float(max_scale)
        relation_branches = (
            downstream_direct,
            downstream_next_target,
            upstream_direct,
            upstream_next_target,
        )
        if any(branch is not None for branch in relation_branches) and not all(
            branch is not None for branch in relation_branches
        ):
            raise ValueError("all four relation branch matrices must be supplied together")
        self.has_relation_branches = all(branch is not None for branch in relation_branches)
        self.register_buffer("downstream", downstream.to(dtype=torch.float32))
        self.register_buffer("upstream", upstream.to(dtype=torch.float32))
        if self.has_relation_branches:
            branch_names = (
                "downstream_direct",
                "downstream_next_target",
                "upstream_direct",
                "upstream_next_target",
            )
            for name, branch in zip(branch_names, relation_branches):
                branch_tensor = branch.to(dtype=torch.float32)  # type: ignore[union-attr]
                if branch_tensor.ndim != 2 or branch_tensor.shape != downstream.shape:
                    raise ValueError(
                        f"{name} must have shape {tuple(downstream.shape)}, "
                        f"got {tuple(branch_tensor.shape)}"
                    )
                self.register_buffer(name, branch_tensor)
            for name in branch_names:
                setattr(
                    self,
                    f"{name}_projection",
                    nn.Conv2d(channels, channels, kernel_size=(1, 1)),
                )
                setattr(self, f"{name}_logit", nn.Parameter(torch.zeros(())))
        else:
            self.downstream_projection = nn.Conv2d(channels, channels, kernel_size=(1, 1))
            self.upstream_projection = nn.Conv2d(channels, channels, kernel_size=(1, 1))
            self.downstream_logit = nn.Parameter(torch.zeros(()))
            self.upstream_logit = nn.Parameter(torch.zeros(()))

    def _message(self, matrix: torch.Tensor, x: torch.Tensor) -> torch.Tensor:
        return torch.einsum("ij,bctj->bcti", matrix, x)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.ndim != 4 or x.shape[1] != self.channels or x.shape[3] != self.node_count:
            raise ValueError(
                "directional residual expects "
                f"[batch, {self.channels}, time, {self.node_count}], got {tuple(x.shape)}"
            )
        if not self.has_relation_branches:
            downstream = self.downstream_projection(self._message(self.downstream, x))
            upstream = self.upstream_projection(self._message(self.upstream, x))
            downstream_scale = self.max_scale * torch.tanh(self.downstream_logit)
            upstream_scale = self.max_scale * torch.tanh(self.upstream_logit)
            return x + downstream_scale * downstream + upstream_scale * upstream

        output = x
        branch_scale = self.max_scale / 2.0
        for name in (
            "downstream_direct",
            "downstream_next_target",
            "upstream_direct",
            "upstream_next_target",
        ):
            matrix = getattr(self, name)
            projection = getattr(self, f"{name}_projection")
            logit = getattr(self, f"{name}_logit")
            message = projection(self._message(matrix, x))
            output = output + branch_scale * torch.tanh(logit) * message
        return output


class StaticDirectionalLaneSTGCN(nn.Module):
    def __init__(
        self,
        graph: Mapping[str, Any],
        directional_graph: Mapping[str, Any],
        *,
        history_steps: int = 12,
        kt: int = 3,
        ks: int = 3,
        dropout: float = 0.5,
        temporal_channels: int = 64,
        graph_channels: int = 16,
        max_scale: float = 0.25,
    ) -> None:
        super().__init__()
        if history_steps != 12 or kt != 3 or ks != 3:
            raise ValueError("NarrowNet-TDP requires n_his=12 Kt=3 Ks=3")
        adjacency = static_adjacency_from_sparse(graph)
        direction_nodes = tuple(directional_graph["nodes"])
        graph_nodes = tuple(graph["nodes"])
        if graph_nodes != direction_nodes:
            raise ValueError("directional graph node order differs from lane graph")
        if len(direction_nodes) != 206:
            raise ValueError("NarrowNet-TDP requires 206 lane nodes")

        self.node_count = adjacency.shape[0]
        self.history_steps = int(history_steps)
        self.register_buffer(
            "gso", torch.from_numpy(build_chebyshev_gso(adjacency))
        )
        downstream = torch.from_numpy(
            np.asarray(directional_graph["downstream_normalized"], dtype=np.float32)
        )
        upstream = torch.from_numpy(
            np.asarray(directional_graph["upstream_normalized"], dtype=np.float32)
        )
        branch_keys = {
            "downstream_direct_normalized",
            "downstream_next_target_normalized",
            "upstream_direct_normalized",
            "upstream_next_target_normalized",
        }
        has_relation_branches = bool(
            directional_graph.get("has_relation_branches", False)
        ) or branch_keys.issubset(directional_graph)
        branch_kwargs: dict[str, torch.Tensor] = {}
        if has_relation_branches:
            branch_kwargs = {
                "downstream_direct": torch.from_numpy(
                    np.asarray(
                        directional_graph["downstream_direct_normalized"],
                        dtype=np.float32,
                    )
                ),
                "downstream_next_target": torch.from_numpy(
                    np.asarray(
                        directional_graph["downstream_next_target_normalized"],
                        dtype=np.float32,
                    )
                ),
                "upstream_direct": torch.from_numpy(
                    np.asarray(
                        directional_graph["upstream_direct_normalized"],
                        dtype=np.float32,
                    )
                ),
                "upstream_next_target": torch.from_numpy(
                    np.asarray(
                        directional_graph["upstream_next_target_normalized"],
                        dtype=np.float32,
                    )
                ),
            }
        self.blocks = nn.ModuleList(
            [
                STConvBlock(
                    kt,
                    ks,
                    self.node_count,
                    4,
                    (temporal_channels, graph_channels, temporal_channels),
                    self.gso,
                    dropout,
                ),
                STConvBlock(
                    kt,
                    ks,
                    self.node_count,
                    temporal_channels,
                    (temporal_channels, graph_channels, temporal_channels),
                    self.gso,
                    dropout,
                ),
            ]
        )
        self.directional_residuals = nn.ModuleList(
            [
                DirectionalLaneResidual(
                    temporal_channels,
                    downstream,
                    upstream,
                    max_scale=max_scale,
                    **branch_kwargs,
                ),
                DirectionalLaneResidual(
                    temporal_channels,
                    downstream,
                    upstream,
                    max_scale=max_scale,
                    **branch_kwargs,
                ),
            ]
        )
        self.output = OutputBlock(
            4,
            temporal_channels,
            (128, 128),
            self.node_count,
            dropout,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.ndim != 4 or tuple(x.shape[1:]) != (4, 12, self.node_count):
            raise ValueError(
                f"expected input [batch, 4, 12, {self.node_count}], got {tuple(x.shape)}"
            )
        hidden = x
        for block, directional in zip(self.blocks, self.directional_residuals):
            hidden = directional(block(hidden))
        output = self.output(hidden)
        return output[:, 0, 0, :]
