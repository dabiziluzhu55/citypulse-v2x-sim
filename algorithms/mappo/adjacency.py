"""Intersection adjacency derived from the SUMO network (BFS, not kNN).

Spec v2 §2.5: one-hop controlled-intersection adjacency is derived from the
road network graph; internal edges are skipped; a restricted BFS from each
controlled node stops at the first controlled node reached.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence

try:
    import sumolib  # PYTHONPATH=/usr/share/sumo/tools
except ImportError:  # pragma: no cover
    sumolib = None

DEFAULT_NET_PATH = "data/maps/sumo/TotalMap_20.net.xml"
DEFAULT_MANIFEST_PATH = "data/maps/sumo/generated/manifests/tls_manifest.json"


def _is_internal(edge: object) -> bool:
    """SUMO 1.12 sumolib 无 isInternal(): internal edge 的 getFunction()=='internal'."""
    function = getattr(edge, "getFunction", lambda: "")()
    return function == "internal"


def _net_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _resolve_controlled_node_ids(
    controlled_ids: Sequence[str],
    manifest_path: str | None,
) -> tuple[str, ...]:
    """demo_N -> SUMO 节点 ID (经 tls_manifest.json 权威映射)。

    manifest_path=None 时（单元测试夹具）把 controlled_ids 直接视为 SUMO 节点 ID。
    """
    ids = tuple(str(v) for v in controlled_ids)
    if len(ids) != len(set(ids)):
        raise ValueError("controlled_ids must be unique")
    if manifest_path is None:
        return ids
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    intersections = manifest["intersections"]
    resolved: list[str] = []
    for demo_id in ids:
        entry = intersections.get(demo_id)
        if entry is None:
            raise ValueError(f"controlled id not in tls_manifest: {demo_id}")
        junction_ids = entry.get("junction_ids") or []
        if not junction_ids:
            raise ValueError(f"tls_manifest entry has no junction_ids: {demo_id}")
        resolved.append(str(junction_ids[0]))
    if len(resolved) != len(set(resolved)):
        raise ValueError("resolved SUMO node ids must be unique")
    return tuple(resolved)


def build_intersection_adjacency(
    net_path: str,
    controlled_ids: Sequence[str],
    *,
    manifest_path: str | None = None,
    max_hops: int = 12,
) -> dict[str, object]:
    """从 SUMO net 推导受控路口一跳邻接（路网 BFS，非 kNN）。

    算法（spec §2.5）：
    1. 受控路口集合 J（经 manifest 解析为 SUMO 节点 ID）；
    2. 跳过 internal edges（getFunction()=='internal'）；
    3. 从 i 的 outgoing edges 受限 BFS：沿非 internal edge 前进，首次到达
       受控路口 j（j != i）即得 i->j，该分支停止；中间非受控路口继续向下游
       展开；max_hops 防病态环；
    4. M1 用对称无向化 M_ij=1[(i->j) or (j->i)]，M_ii=0。
    """
    if sumolib is None:
        raise RuntimeError("sumolib unavailable; set PYTHONPATH=/usr/share/sumo/tools")
    net = sumolib.net.readNet(net_path)
    demo_ids = tuple(str(v) for v in controlled_ids)
    node_ids = _resolve_controlled_node_ids(demo_ids, manifest_path)
    controlled_set = set(node_ids)
    for node_id in node_ids:
        if net.getNode(node_id) is None:
            raise ValueError(f"controlled node not in net: {node_id}")

    demo_to_node = dict(zip(demo_ids, node_ids))
    node_to_demo = {node: demo for demo, node in demo_to_node.items()}
    # 输出按 demo_id 键控（与 config.intersection_ids / M1 20x20 矩阵一致）；
    # node_ids 仅存于 meta 作溯源映射。
    directed: dict[str, set[str]] = {demo: set() for demo in demo_ids}
    for demo_id, node_id in zip(demo_ids, node_ids):
        frontier = [
            e for e in net.getNode(node_id).getOutgoing() if not _is_internal(e)
        ]
        visited: set[str] = set()
        for _ in range(int(max_hops)):
            next_frontier: list[object] = []
            for edge in frontier:
                edge_id = str(edge.getID())
                if edge_id in visited:
                    continue
                visited.add(edge_id)
                to_node = str(edge.getToNode().getID())
                if to_node in controlled_set:
                    if to_node != node_id:
                        directed[demo_id].add(node_to_demo[to_node])
                    continue  # 到达受控路口，该分支停止
                next_frontier.extend(
                    e2
                    for e2 in net.getNode(to_node).getOutgoing()
                    if not _is_internal(e2)
                )
            frontier = next_frontier
            if not frontier:
                break

    symmetric = {
        demo: {
            j for j in demo_ids
            if j != demo and (j in directed[demo] or demo in directed[j])
        }
        for demo in demo_ids
    }
    degrees = {demo: len(symmetric[demo]) for demo in demo_ids}
    isolated = [demo for demo in demo_ids if degrees[demo] == 0]

    def _weakly_connected(nodes: Sequence[str], edges: Mapping[str, set[str]]) -> bool:
        if not nodes:
            return True
        seen: set[str] = set()
        stack = [nodes[0]]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            for nxt in edges[cur]:
                if nxt not in seen:
                    stack.append(nxt)
        return seen == set(nodes)

    return {
        "directed": {n: sorted(directed[n]) for n in demo_ids},
        "symmetric": {n: sorted(symmetric[n]) for n in demo_ids},
        "meta": {
            "net_xml_sha256": _net_sha256(Path(net_path)),
            "manifest_sha256": (
                None
                if manifest_path is None
                else hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()
            ),
            "generator_version": "adjacency_v2_bfs",
            "demo_ids": list(demo_ids),
            "node_ids": list(node_ids),
            "degrees": degrees,
            "isolated_nodes": isolated,
            "weakly_connected": _weakly_connected(demo_ids, symmetric),
            "max_hops": int(max_hops),
        },
    }


def write_adjacency_files(
    net_path: str,
    controlled_ids: Sequence[str],
    out_dir: str,
    *,
    manifest_path: str | None = None,
) -> tuple[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = build_intersection_adjacency(
        net_path, controlled_ids, manifest_path=manifest_path
    )
    directed_path = out / "intersection_adjacency_directed.json"
    symmetric_path = out / "intersection_adjacency_m1_symmetric.json"
    directed_path.write_text(
        json.dumps(
            {"meta": result["meta"], "edges": result["directed"]},
            indent=2, sort_keys=True,
        )
    )
    symmetric_path.write_text(
        json.dumps(
            {"meta": result["meta"], "edges": result["symmetric"]},
            indent=2, sort_keys=True,
        )
    )
    return str(directed_path), str(symmetric_path)
