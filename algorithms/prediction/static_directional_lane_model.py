"""Static Chebyshev lane model with fixed downstream/spillback residuals.

The lane Chebyshev backbone is intentionally the same as the aligned static
control.  The only extra spatial signal is a pair of fixed, direction-aware
message paths derived from SUMO lane transitions:

* downstream messages carry upstream state toward the lanes that vehicles
  can enter next;
* upstream messages carry downstream state back toward possible queue origins.

Both residual strengths start at exactly zero, so the initialized model is the
static control plus a learnable, bounded road-direction correction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np
import torch
from torch import nn

from .build_directional_lane_graph import load_directional_graph
from .build_dynamic_lane_graph import load_sparse_graph
from .static_cheb_lane_model import OutputBlock, STConvBlock, _static_adjacency


class DirectionalLaneResidual(nn.Module):
    """Apply bounded fixed downstream/upstream message residuals.

    D0 uses one branch for each direction.  D2 can additionally keep direct
    transitions and multi-hop next-target relations separate, allowing the
    model to learn how much of each road-topology signal to use.
    """

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
                # Zero initialization makes D2 an exact static-control
                # extension at initialization, just like D0.
                setattr(self, f"{name}_logit", nn.Parameter(torch.zeros(())))
        else:
            self.downstream_projection = nn.Conv2d(channels, channels, kernel_size=(1, 1))
            self.upstream_projection = nn.Conv2d(channels, channels, kernel_size=(1, 1))
            # Zero initialization makes the experiment a controlled residual
            # extension of the static Chebyshev control.
            self.downstream_logit = nn.Parameter(torch.zeros(()))
            self.upstream_logit = nn.Parameter(torch.zeros(()))

    def _message(self, matrix: torch.Tensor, x: torch.Tensor) -> torch.Tensor:
        # matrix is [receiver, sender], x is [B, C, T, sender].
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
        # Split the per-direction scale between the direct and next-target
        # branches so their combined bounded correction remains comparable to
        # the two-branch D0 residual.
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
    """Reference static Cheb STGCN with fixed road-direction residuals."""

    def __init__(
        self,
        graph: Mapping[str, object] | str,
        directional_graph: Mapping[str, object] | str,
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
            raise ValueError(
                "the aligned directional lane control requires n_his=12, Kt=3 and Ks=3"
            )
        if isinstance(graph, (str, bytes)):
            graph = load_sparse_graph(Path(graph))
        if isinstance(directional_graph, (str, bytes)):
            directional_graph = load_directional_graph(Path(directional_graph))
        adjacency = _static_adjacency(graph)
        direction_nodes = tuple(directional_graph["nodes"])  # type: ignore[arg-type]
        graph_nodes = tuple(graph["nodes"])  # type: ignore[arg-type]
        if graph_nodes != direction_nodes:
            raise ValueError("directional graph node order differs from lane graph")
        if len(direction_nodes) != 206:
            raise ValueError("directional lane model requires 206 nodes")

        self.node_count = adjacency.shape[0]
        self.history_steps = int(history_steps)
        self.register_buffer("gso", torch.from_numpy(_build_gso(adjacency)))
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
        has_relation_branches = bool(directional_graph.get("has_relation_branches", False)) or branch_keys.issubset(
            directional_graph
        )
        branch_kwargs: dict[str, torch.Tensor] = {}
        if has_relation_branches:
            branch_kwargs = {
                "downstream_direct": torch.from_numpy(
                    np.asarray(directional_graph["downstream_direct_normalized"], dtype=np.float32)
                ),
                "downstream_next_target": torch.from_numpy(
                    np.asarray(directional_graph["downstream_next_target_normalized"], dtype=np.float32)
                ),
                "upstream_direct": torch.from_numpy(
                    np.asarray(directional_graph["upstream_direct_normalized"], dtype=np.float32)
                ),
                "upstream_next_target": torch.from_numpy(
                    np.asarray(directional_graph["upstream_next_target_normalized"], dtype=np.float32)
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


def _build_gso(adjacency: np.ndarray) -> np.ndarray:
    """Import the local static GSO builder without changing its contract."""

    from .static_cheb_lane_model import build_chebyshev_gso

    return build_chebyshev_gso(adjacency)


def build_model_from_graph(
    graph_path: str,
    directional_graph_path: str,
    *,
    dropout: float = 0.5,
    temporal_channels: int = 64,
    graph_channels: int = 16,
    max_scale: float = 0.25,
) -> StaticDirectionalLaneSTGCN:
    return StaticDirectionalLaneSTGCN(
        graph_path,
        directional_graph_path,
        dropout=dropout,
        temporal_channels=temporal_channels,
        graph_channels=graph_channels,
        max_scale=max_scale,
    )
