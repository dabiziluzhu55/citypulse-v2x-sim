"""Topological distance clipping — BFS from seed junction(s) along road edges.

Supports two modes:
  - Single-seed clipping (``clip_by_topological_distance``): BFS from one junction.
  - TAZ clipping (``clip_taz``): Multi-seed clipping preserving shortest-path
    connectivity between TAZ junctions plus per-junction distance-limited BFS
    expansion.

Keeps entire edges and junctions (no truncation) to preserve valid
SUMO topology for SUMO-CARLA co-simulation.
"""

from collections import deque
from typing import List, Set, Tuple

from .graph_model import NetGraph


def clip_by_topological_distance(
    graph: NetGraph,
    seed_junction_id: str,
    dist_m: float,
) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
    """Perform BFS from seed junction, collecting elements within topological distance.

    Algorithm:
        BFS outward from seed junction. For each edge traversed via adjacency:
          - Always keep the edge and the far-end junction (no truncation).
          - Accumulate distance along edge lengths (first lane length).
          - If accumulated distance <= dist_m, enqueue the far junction for further
            traversal.
          - If accumulated distance > dist_m, keep edge + far junction but do NOT
            enqueue for further expansion.
        After BFS: collect all internal edges, internal junctions, tlLogics,
        and connections associated with kept junctions and edges.

    Args:
        graph: Parsed NetGraph with populated indexes.
        seed_junction_id: The junction ID to start from (e.g. 'J1').
        dist_m: Topological distance threshold in meters.

    Returns:
        (kept_edge_ids, kept_junction_ids, kept_connection_ids, kept_tl_logic_ids)
        Each is a Set[str] of element IDs.

    Raises:
        KeyError: seed_junction_id not found in graph.junctions.
    """
    if seed_junction_id not in graph.junctions:
        available = sorted(graph.junctions.keys())
        raise KeyError(
            f"Seed junction '{seed_junction_id}' not found in net.xml.\n"
            f"Available junction IDs (first 30): {available[:30]}"
            + (f" ... and {len(available) - 30} more" if len(available) > 30 else "")
        )

    seed_junction = graph.junctions[seed_junction_id]
    print(
        f"[Clipper] Seed junction: {seed_junction_id} "
        f"({seed_junction.jtype}) at ({seed_junction.x:.1f}, {seed_junction.y:.1f})"
    )
    print(f"[Clipper] Topological distance threshold: {dist_m:.1f} m")

    # ── Phase 1: BFS along regular edges ──
    kept_junctions: Set[str] = {seed_junction_id}
    kept_edges: Set[str] = set()
    visited: Set[str] = {seed_junction_id}  # junctions whose adjacency has been expanded

    queue = deque([(seed_junction_id, 0.0)])

    while queue:
        cur_jid, cur_dist = queue.popleft()

        for edge_id, neighbor_jid in graph.junction_edges.get(cur_jid, []):
            edge = graph.edges.get(edge_id)
            if edge is None or edge.is_internal:
                continue

            edge_len = edge.first_lane_length
            new_dist = cur_dist + edge_len

            # Always keep the edge and the far junction
            kept_edges.add(edge_id)
            kept_junctions.add(neighbor_jid)

            if new_dist <= dist_m and neighbor_jid not in visited:
                visited.add(neighbor_jid)
                queue.append((neighbor_jid, new_dist))
                print(
                    f"  [Traverse] {edge_id}: {cur_jid} → {neighbor_jid} "
                    f"(edge_len={edge_len:.1f}m, acc_dist={new_dist:.1f}m) ✓ expand"
                )
            elif new_dist > dist_m:
                print(
                    f"  [Boundary] {edge_id}: {cur_jid} → {neighbor_jid} "
                    f"(edge_len={edge_len:.1f}m, acc_dist={new_dist:.1f}m) ✗ stop"
                )

    # ── Phases 2–5: Collect related elements ──
    return _collect_related_elements(graph, kept_edges, kept_junctions)


def clip_taz(
    graph: NetGraph,
    seed_junction_ids: List[str],
    dist_m: float,
    path_dist_m: float = 50.0,
) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
    """Clip a TAZ (Typical Area Zone) — multi-seed topological clipping.

    Algorithm:

    **Phase A — TAZ connectivity paths (always kept, regardless of distance):**
      For every pair of TAZ seed junctions, BFS finds the shortest path
      between them.  All edges and junctions on those shortest paths are
      unconditionally preserved to guarantee the TAZ subgraph is connected.
      This handles the common real-world case where TAZ junctions are not
      direct neighbors but are separated by intermediate junctions.

    **Phase B1 — Seed BFS with *dist_m*:**
      All TAZ seed junctions start at distance 0. BFS expands outward from
      each seed simultaneously, bounded by *dist_m*. Edges and far junctions
      beyond *dist_m* are kept but not expanded further (boundary).

    **Phase B2 — Path-junction BFS with *path_dist_m*:**
      MST path junctions (intermediate junctions on the Phase-A paths) are
      expanded with a smaller, fixed distance threshold *path_dist_m* —
      independent of the CLI ``--dist``.  This preserves their adjacent
      edges and turning connections, preventing roads from breaking at
      path junctions, while keeping the output small.

    After BFS: collect internal edges, internal junctions, tlLogics, and
    connections (shared helper).

    Args:
        graph: Parsed NetGraph with populated indexes.
        seed_junction_ids: Junction IDs belonging to this TAZ.
        dist_m: Topological distance threshold in meters for seed-junction
            expansion (CLI ``--dist``).
        path_dist_m: Smaller expansion distance for MST path junctions
            (default 50.0).

    Returns:
        (kept_edge_ids, kept_junction_ids, kept_connection_ids, kept_tl_logic_ids)

    Raises:
        KeyError: Any seed_junction_id not found in graph.junctions.
    """
    # ── Validate all seed junctions ──
    seed_set = set(seed_junction_ids)
    for jid in seed_junction_ids:
        if jid not in graph.junctions:
            available = sorted(graph.junctions.keys())
            raise KeyError(
                f"TAZ seed junction '{jid}' not found in net.xml.\n"
                f"Available junction IDs (first 30): {available[:30]}"
                + (f" ... and {len(available) - 30} more" if len(available) > 30 else "")
            )

    print(f"[TAZ Clipper] {len(seed_junction_ids)} seed junctions: {seed_junction_ids}")
    print(f"[TAZ Clipper] Topological distance threshold: {dist_m:.1f} m")

    kept_junctions: Set[str] = set(seed_junction_ids)
    kept_edges: Set[str] = set()
    visited: Set[str] = set(seed_junction_ids)  # junctions whose adjacency has been expanded

    # ═══════════════════════════════════════════════════════════════════
    # Phase A: TAZ connectivity paths — find shortest paths between all
    #          TAZ junction pairs and unconditionally preserve every edge
    #          and junction along those paths (regardless of distance).
    # ═══════════════════════════════════════════════════════════════════
    if len(seed_junction_ids) > 1:
        path_edges, path_junctions, pair_details = _find_taz_connection_paths(
            graph, seed_junction_ids, seed_set
        )
        kept_edges |= path_edges
        kept_junctions |= path_junctions

        if path_edges:
            print(
                f"[TAZ Clipper] Phase A: {len(pair_details)} pair(s) connected — "
                f"{len(path_edges)} edge(s), {len(path_junctions)} junction(s) preserved"
            )
            for (j1, j2), (edge_count, total_dist) in pair_details:
                print(f"  [TAZ-Path] {j1} ↔ {j2}: {edge_count} edges, {total_dist:.0f}m")
        else:
            print(f"[TAZ Clipper] Phase A: no paths found between TAZ junctions")
    else:
        print(f"[TAZ Clipper] Phase A: single seed — no pairwise paths needed")

    # ═══════════════════════════════════════════════════════════════════
    # Phase B1: Seed BFS with dist_m
    # ═══════════════════════════════════════════════════════════════════
    _bfs_expand(
        graph,
        queue=deque([(jid, 0.0) for jid in seed_junction_ids]),
        visited=visited,
        threshold=dist_m,
        kept_edges=kept_edges,
        kept_junctions=kept_junctions,
        log_prefix="[TAZ-Traverse]",
        boundary_prefix="[TAZ-Boundary]",
        skip_log_seeds=seed_set,
    )

    # ═══════════════════════════════════════════════════════════════════
    # Phase B2: Path-junction BFS with path_dist_m (smaller fixed distance)
    # ═══════════════════════════════════════════════════════════════════
    if len(seed_junction_ids) > 1:
        path_junction_seeds = [
            jid for jid in path_junctions
            if jid not in seed_set and jid not in visited
        ]
        if path_junction_seeds:
            print(
                f"[TAZ Clipper] Phase B2: expanding {len(path_junction_seeds)} path "
                f"junction(s) with path_dist={path_dist_m:.0f}m"
            )
            _bfs_expand(
                graph,
                queue=deque([(jid, 0.0) for jid in path_junction_seeds]),
                visited=visited,
                threshold=path_dist_m,
                kept_edges=kept_edges,
                kept_junctions=kept_junctions,
                log_prefix="[TAZ-PathExpand]",
                boundary_prefix="[TAZ-PathBoundary]",
                skip_log_seeds=seed_set,
            )

    # ── Phases 2–5: Collect related elements ──
    return _collect_related_elements(graph, kept_edges, kept_junctions)


# ═══════════════════════════════════════════════════════════════════════
#  Shared BFS expansion helper
# ═══════════════════════════════════════════════════════════════════════

def _bfs_expand(
    graph: NetGraph,
    queue,
    visited: Set[str],
    threshold: float,
    kept_edges: Set[str],
    kept_junctions: Set[str],
    log_prefix: str,
    boundary_prefix: str,
    skip_log_seeds: Set[str],
) -> None:
    """Expand BFS from an initial queue of (junction, accumulated_dist).

    For every edge traversed:
      - Always keep the edge and the far-end junction (no truncation).
      - Accumulate distance along edge lengths (first lane length).
      - If accumulated distance <= threshold, enqueue the far junction
        for further traversal.
      - If accumulated distance > threshold, keep edge + far junction but
        do NOT enqueue for further expansion (boundary).

    Args:
        graph: Parsed NetGraph.
        queue: Deque of (junction_id, accumulated_distance) to start from.
        visited: Set of junction IDs whose adjacency has been expanded.
            Mutated in-place.
        threshold: Topological distance limit in meters.
        kept_edges / kept_junctions: Sets mutated in-place.
        log_prefix / boundary_prefix: Log labels for traverse/boundary lines.
        skip_log_seeds: Junction IDs that should not be logged when expanded
            (e.g. TAZ seeds already logged in Phase A).
    """
    while queue:
        cur_jid, cur_dist = queue.popleft()

        for edge_id, neighbor_jid in graph.junction_edges.get(cur_jid, []):
            edge = graph.edges.get(edge_id)
            if edge is None or edge.is_internal:
                continue

            edge_len = edge.first_lane_length
            new_dist = cur_dist + edge_len

            # Always keep the edge and the far junction
            kept_edges.add(edge_id)
            kept_junctions.add(neighbor_jid)

            if new_dist <= threshold and neighbor_jid not in visited:
                visited.add(neighbor_jid)
                queue.append((neighbor_jid, new_dist))
                if neighbor_jid not in skip_log_seeds:
                    print(
                        f"  [{log_prefix}] {edge_id}: {cur_jid} → {neighbor_jid} "
                        f"(edge_len={edge_len:.1f}m, acc_dist={new_dist:.1f}m) ✓ expand"
                    )
            elif new_dist > threshold:
                print(
                    f"  [{boundary_prefix}] {edge_id}: {cur_jid} → {neighbor_jid} "
                    f"(edge_len={edge_len:.1f}m, acc_dist={new_dist:.1f}m) ✗ stop"
                )


# ═══════════════════════════════════════════════════════════════════════
#  TAZ connectivity helpers: find shortest paths between seed junctions
# ═══════════════════════════════════════════════════════════════════════

def _find_shortest_path(
    graph: NetGraph,
    start_jid: str,
    end_jid: str,
) -> Tuple[List[str], List[str]]:
    """BFS from *start_jid* to *end_jid*, returning the shortest path.

    Only traverses regular (non-internal) edges.  Edge weight is
    ``first_lane_length`` — the same metric used for distance-based clipping.

    Args:
        graph: Parsed NetGraph.
        start_jid: Starting junction ID.
        end_jid: Target junction ID.

    Returns:
        ``(edge_ids, junction_ids)`` — the edges and junctions (including
        *start_jid* and *end_jid*) along the shortest path. Both lists are
        empty if no path exists.
    """
    if start_jid == end_jid:
        return [], [start_jid]

    queue = deque([(start_jid, [], [start_jid])])
    visited = {start_jid}

    while queue:
        cur_jid, edge_path, junction_path = queue.popleft()

        for edge_id, neighbor_jid in graph.junction_edges.get(cur_jid, []):
            edge = graph.edges.get(edge_id)
            if edge is None or edge.is_internal:
                continue

            if neighbor_jid == end_jid:
                return (
                    edge_path + [edge_id],
                    junction_path + [neighbor_jid],
                )

            if neighbor_jid not in visited:
                visited.add(neighbor_jid)
                queue.append((
                    neighbor_jid,
                    edge_path + [edge_id],
                    junction_path + [neighbor_jid],
                ))

    # No path found
    return [], []


def _find_taz_connection_paths(
    graph: NetGraph,
    seed_junction_ids: List[str],
    seed_set: Set[str],
) -> Tuple[Set[str], Set[str], List[Tuple[Tuple[str, str], Tuple[int, float]]]]:
    """Build a Minimum Spanning Tree over TAZ seed junctions.

    Computes all-pairs shortest-path distances between seed junctions,
    then uses Prim's algorithm to select the subset of paths that forms
    a minimum spanning tree (MST).  This guarantees all TAZ junctions
    are connected while minimising the number of edges and junctions
    preserved beyond the per-junction ``--dist`` expansion.

    Args:
        graph: Parsed NetGraph.
        seed_junction_ids: TAZ seed junction IDs.
        seed_set: The same IDs as a set (pre-computed).

    Returns:
        ``(path_edges, path_junctions, pair_details)`` where *pair_details*
        is a list of ``((jid1, jid2), (edge_count, total_dist))`` for
        logging — one entry per MST edge.
    """
    n = len(seed_junction_ids)
    if n <= 1:
        return set(), set(seed_junction_ids), []

    # ── Step 1: all-pairs shortest paths ──
    # dist_matrix[i][j] = (total_distance_m, edges_list, junctions_list)
    INF = (float('inf'), [], [])
    dist_matrix: List[List[Tuple[float, List[str], List[str]]]] = [
        [INF for _ in range(n)] for _ in range(n)
    ]

    for i in range(n):
        dist_matrix[i][i] = (0.0, [], [seed_junction_ids[i]])

    for i in range(n):
        for j in range(i + 1, n):
            j1, j2 = seed_junction_ids[i], seed_junction_ids[j]
            edges, junctions = _find_shortest_path(graph, j1, j2)
            if edges:
                total_dist = sum(
                    graph.edges[e].first_lane_length
                    for e in edges if e in graph.edges
                )
                dist_matrix[i][j] = (total_dist, edges, junctions)
                dist_matrix[j][i] = (total_dist, edges, junctions)
            else:
                print(f"  [TAZ-Warn] No path found between {j1} and {j2}")

    # ── Step 2: Prim's algorithm to build MST ──
    in_tree = [False] * n
    in_tree[0] = True
    tree_edges: List[Tuple[int, int, float, List[str], List[str]]] = []

    for _ in range(n - 1):
        best_dist = float('inf')
        best_i, best_j = -1, -1
        best_edges: List[str] = []
        best_junctions: List[str] = []

        for i in range(n):
            if not in_tree[i]:
                continue
            for j in range(n):
                if in_tree[j]:
                    continue
                d, edges, junctions = dist_matrix[i][j]
                if d < best_dist:
                    best_dist = d
                    best_i, best_j = i, j
                    best_edges = edges
                    best_junctions = junctions

        if best_j == -1:
            break  # unreachable nodes remain — graph is disconnected

        in_tree[best_j] = True
        tree_edges.append((best_i, best_j, best_dist, best_edges, best_junctions))

    # ── Step 3: collect edges and junctions from MST paths ──
    path_edges: Set[str] = set()
    path_junctions: Set[str] = set()
    pair_details: List[Tuple[Tuple[str, str], Tuple[int, float]]] = []

    for i, j, dist, edges, junctions in tree_edges:
        j1, j2 = seed_junction_ids[i], seed_junction_ids[j]
        path_edges.update(edges)
        path_junctions.update(junctions)
        pair_details.append(((j1, j2), (len(edges), dist)))

    return path_edges, path_junctions, pair_details


# ═══════════════════════════════════════════════════════════════════════
#  Shared helper: collect internal edges, internal junctions,
#  tlLogics, and connections for a set of kept elements
# ═══════════════════════════════════════════════════════════════════════

def _collect_related_elements(
    graph: NetGraph,
    kept_edges: Set[str],
    kept_junctions: Set[str],
) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
    """Collect internal edges, internal junctions, tlLogics, and connections.

    Takes a set of kept regular edges and junctions (from BFS) and returns
    the complete set of all element IDs needed for a valid .net.xml output.

    Args:
        graph: Parsed NetGraph.
        kept_edges: Regular edge IDs already kept (mutated in-place with internal edges).
        kept_junctions: Junction IDs already kept (mutated in-place with internal junctions).

    Returns:
        (kept_edges, kept_junctions, kept_connections, kept_tl_logics)
    """
    # ── Collect internal edges for all kept junctions ──
    for jid in list(kept_junctions):
        for int_edge_id in graph.junction_internal_edges.get(jid, []):
            kept_edges.add(int_edge_id)

    # ── Collect internal junctions ──
    # Internal junctions are those whose incLanes/intLanes reference kept edges/lanes
    kept_internal_junctions: Set[str] = set()
    for jid, junction in graph.junctions.items():
        if not junction.is_internal:
            continue
        # An internal junction is kept if any lane it references belongs to a kept
        # junction's internal lanes or to a kept edge
        all_referenced_lanes = set(junction.inc_lanes) | set(junction.int_lanes)
        # Check if any referenced lane belongs to a kept edge
        for lane_id in all_referenced_lanes:
            # Lane IDs are like '-E0_0', ':J1_0_0' — derive edge ID
            # For regular edges: '-E0_0' → '-E0'
            # For internal edges: ':J1_0_0' → ':J1_0'
            # The edge ID is the lane ID minus the last '_index' suffix
            edge_id = _lane_to_edge_id(lane_id)
            if edge_id in kept_edges:
                kept_internal_junctions.add(jid)
                break

    kept_junctions |= kept_internal_junctions

    # ── Collect tlLogics for kept junctions ──
    kept_tl_logics: Set[str] = set()
    for tl_id in graph.tl_logics:
        if tl_id in kept_junctions:
            kept_tl_logics.add(tl_id)

    # ── Filter connections ──
    kept_connections: Set[str] = set()
    for conn in graph.connections:
        if conn.from_edge in kept_edges and conn.to_edge in kept_edges:
            kept_connections.add(conn.connection_id)

    # ── Also keep tlLogics referenced by kept connections ──
    # Some .net.xml files use tlLogic IDs that differ from junction IDs
    # (e.g. junction "4393" has tlLogic "J1").  We must also collect
    # tlLogics from the tl= attribute of kept connections.
    for conn in graph.connections:
        if conn.connection_id in kept_connections and conn.tl:
            kept_tl_logics.add(conn.tl)

    # ── Summary ──
    main_juncs = [j for j in kept_junctions
                  if j in graph.junctions and not graph.junctions[j].is_internal]
    int_juncs = [j for j in kept_junctions
                 if j in graph.junctions and graph.junctions[j].is_internal]
    reg_edges = [e for e in kept_edges
                 if e in graph.edges and not graph.edges[e].is_internal]
    int_edges_kept = [e for e in kept_edges
                      if e in graph.edges and graph.edges[e].is_internal]

    print(
        f"[Clipper] Kept: {len(reg_edges)} regular edges + {len(int_edges_kept)} internal edges, "
        f"{len(main_juncs)} junctions + {len(int_juncs)} internal junctions, "
        f"{len(kept_connections)} connections, "
        f"{len(kept_tl_logics)} tlLogics"
    )

    return kept_edges, kept_junctions, kept_connections, kept_tl_logics


def _lane_to_edge_id(lane_id: str) -> str:
    """Derive the edge ID from a lane ID.

    Examples:
        '-E0_0' → '-E0'
        '-E0_1' → '-E0'
        ':J1_0_0' → ':J1_0'
        ':J1_18_0' → ':J1_18'
    """
    # Lane IDs end with '_<index>'
    # For internal lanes like ':J1_18_0', the edge is ':J1_18'
    # We need to strip the last '_<number>' suffix
    parts = lane_id.rsplit('_', 1)
    return parts[0] if len(parts) == 2 else lane_id
