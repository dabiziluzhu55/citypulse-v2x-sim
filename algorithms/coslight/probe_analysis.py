"""Read-only per-intersection probe: V17 learned Top-K vs V16 adjacency topology.

Wraps algorithms.coslight.controller so that every joint signal decision also
computes, on the SAME observation, the phase-action distribution under:
  - the V17 all-candidate learned Top-K path  (_probe_policy)
  - the V16 adjacency-topology path           (_probe_topology_policy)
and accumulates per-intersection KL / argmax-change / collaborator-selection
statistics.  Writes a JSON snapshot to COSLIGHT_PROBE_OUTPUT.
"""
from __future__ import annotations

import json
import os

import numpy as np
import torch

from . import controller as base


_OUT = os.environ.get(
    "COSLIGHT_PROBE_OUTPUT", "/tmp/probe_v17_vs_v16.json"
)
_stats: dict = {"joint_decisions": 0, "by_tid": {}, "errors": []}


def _snapshot() -> None:
    with open(_OUT, "w") as f:
        json.dump(_stats, f, ensure_ascii=False, indent=2)


def initialize(payload: dict) -> dict:
    response = base.initialize(payload)
    _stats["by_tid"] = {}
    for tid in base._tls_order:
        neighbors = list(base._neighbors.get(tid, ()))
        _stats["by_tid"][tid] = {
            "decisions": 0,
            "kl_sum": 0.0,
            "max_kl": 0.0,
            "argmax_changes": 0,
            "v17_nonlocal_sum": 0,
            "v17_self_sum": 0,
            "v17_selected_unique_sum": 0,
            "v16_neighbor_ids": neighbors,
            "v16_has_neighbors": bool(neighbors),
        }
    return response


def step(payload: dict) -> dict:
    result = base.step(payload)
    signals = result.get("actions", {}).get("signals", {})
    if not signals:
        return result
    intersections = payload.get("intersections", {})
    try:
        observations = base.build_state(intersections)  # [A, obs_dim]
        valid_counts = np.asarray(
            [len(base._phase_orders[tid]) for tid in base._tls_order],
            dtype=np.int64,
        )  # [A]
        action_masks, _ = base._build_action_masks(intersections)  # [A, act]
        phase_features = base._state_builder.build_phase_features(
            intersections, base._tls_order, base._model.act_dim
        )  # [A, pf]
        obs_t = torch.from_numpy(observations).unsqueeze(0).float()
        vc_t = torch.from_numpy(valid_counts)
        am_t = torch.from_numpy(action_masks)
        pf_t = torch.from_numpy(phase_features).unsqueeze(0)
        v17_probs, v17_collab, v17_argmax = base._probe_policy(
            obs_t, vc_t, am_t, pf_t
        )
        v16_probs, v16_argmax = base._probe_topology_policy(
            obs_t, vc_t, am_t, pf_t
        )
        eps = 1e-8
        kl = (
            v17_probs * (torch.log(v17_probs + eps) - torch.log(v16_probs + eps))
        ).sum(dim=-1)  # [1, A]
        argmax_change = (v17_argmax != v16_argmax).float()  # [1, A]
        _stats["joint_decisions"] += 1
        for ai, tid in enumerate(base._tls_order):
            s = _stats["by_tid"][tid]
            s["decisions"] += 1
            k = float(kl[0, ai])
            s["kl_sum"] += k
            s["max_kl"] = max(s["max_kl"], k)
            s["argmax_changes"] += int(argmax_change[0, ai])
            collab = v17_collab[0, ai]  # [num_agents]
            topk = torch.topk(collab, k=base._top_k, dim=-1).indices
            selected = [base._tls_order[int(x)] for x in topk.tolist()]
            neighbors = set(base._neighbors.get(tid, ()))
            s["v17_nonlocal_sum"] += sum(
                1 for c in selected if c != tid and c not in neighbors
            )
            s["v17_self_sum"] += sum(1 for c in selected if c == tid)
            s["v17_selected_unique_sum"] += len(set(selected))
        _snapshot()
    except Exception as exc:  # never break the controller loop
        _stats["errors"].append(f"{type(exc).__name__}: {exc}")
        _snapshot()
    return result


def finish(payload: dict) -> None:
    try:
        base.finish(payload)
    finally:
        _snapshot()
