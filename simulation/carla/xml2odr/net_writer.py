"""SUMO .net.xml output writer.

Assembles a valid .net.xml file from kept element ID sets, reconstructing
XML elements from NetGraph dataclass fields.

CRITICAL: Junctions are cleaned up so that incLanes / intLanes only reference
lanes belonging to edges that are actually present in the output.  Requests
are rebuilt to match the filtered lane lists.  Junctions with no remaining
lanes after filtering are silently skipped.
"""

import os
import xml.etree.ElementTree as ET
from typing import Dict, List, Set

from .graph_model import Junction, NetGraph, Request


# ═══════════════════════════════════════════════════════════════════════
#  Attribute merge helpers
# ═══════════════════════════════════════════════════════════════════════

def _merge_attribs(extra: Dict[str, str], primary: Dict[str, str]) -> Dict[str, str]:
    """Merge extra XML attributes with structured fields (primary wins)."""
    merged = dict(extra)
    merged.update(primary)
    return merged


def _collect_referenced_type_ids(graph: NetGraph, kept_edges: Set[str]) -> Set[str]:
    """Collect type IDs referenced by kept edges and their lanes."""
    type_ids: Set[str] = set()
    for eid in kept_edges:
        edge = graph.edges.get(eid)
        if edge is None:
            continue
        edge_type = edge.extra_attribs.get('type')
        if edge_type:
            type_ids.add(edge_type)
        for lane in edge.lanes:
            lane_type = lane.extra_attribs.get('type')
            if lane_type:
                type_ids.add(lane_type)
    return type_ids


# ═══════════════════════════════════════════════════════════════════════
#  Helper: lane → edge resolution
# ═══════════════════════════════════════════════════════════════════════

def _lane_to_edge_id(lane_id: str) -> str:
    """Derive the parent edge ID from a SUMO lane ID.

    Examples:
        '-E0_0'    → '-E0'
        '-56713_1' → '-56713'
        ':J1_0_0'  → ':J1_0'
        ':315_8_0' → ':315_8'
    """
    parts = lane_id.rsplit('_', 1)
    return parts[0] if len(parts) == 2 else lane_id


# ═══════════════════════════════════════════════════════════════════════
#  Request rebuilding
# ═══════════════════════════════════════════════════════════════════════

def _rebuild_requests(
    requests: List[Request],
    old_combined: List[str],       # orig incLanes + intLanes
    new_combined: List[str],       # filtered incLanes + intLanes
) -> List[Request]:
    """Rebuild request elements after lane pruning.

    The ``response`` and ``foes`` bit-strings encode right-of-way
    information indexed against the *old* combined lane list.
    After some lanes are removed we must drop the corresponding bits
    and renumber the request indices.

    Requests whose own index was pruned are dropped entirely.
    """
    old_len = len(old_combined)
    new_len = len(new_combined)

    if old_len == new_len:
        return requests  # nothing pruned

    # Build position mapping: old_index → new_index (or None)
    new_set = set(new_combined)
    old_to_new: Dict[int, int] = {}
    for old_idx, lid in enumerate(old_combined):
        if lid in new_set:
            old_to_new[old_idx] = new_combined.index(lid)

    # Which old positions are kept (for filtering bit-strings)
    keep_mask = [i in old_to_new for i in range(old_len)]

    rebuilt: List[Request] = []
    for req in requests:
        old_idx = req.index
        if old_idx not in old_to_new:
            # This request corresponds to a pruned lane — drop it
            continue

        new_idx = old_to_new[old_idx]

        # Filter response/foes bit-strings to kept positions only
        new_response = ''.join(
            req.response[j] for j in range(old_len) if keep_mask[j]
        )
        new_foes = ''.join(
            req.foes[j] for j in range(old_len) if keep_mask[j]
        )

        rebuilt.append(Request(
            index=new_idx,
            response=new_response,
            foes=new_foes,
            cont=req.cont,
            extra_attribs=dict(req.extra_attribs),
        ))

    return rebuilt


# ═══════════════════════════════════════════════════════════════════════
#  Main writer
# ═══════════════════════════════════════════════════════════════════════

def write_clipped_net(
    graph: NetGraph,
    kept_edges: Set[str],
    kept_junctions: Set[str],
    kept_connections: Set[str],
    kept_tl_logics: Set[str],
    output_path: str,
) -> None:
    """Write a valid SUMO .net.xml containing only the kept elements.

    Elements are written in standard SUMO net.xml order:
      1. <location> metadata
      2. <type> definitions referenced by kept edges/lanes
      3. Internal <edge> elements (function="internal")
      4. Regular <edge> elements
      5. <tlLogic> elements
      6. Main <junction> elements (type != "internal")
      7. Internal <junction> elements (type == "internal")
      8. <connection> elements (preserving original order)

    Junctions have their incLanes / intLanes filtered so that they only
    reference lanes whose parent edge is in *kept_edges*.  Request
    matrices are rebuilt accordingly.  Junctions that end up with zero
    lanes after filtering are silently dropped.
    """
    # ── Pre-compute: junctions referenced as from/to by kept edges ──
    # These must never be dropped, even if their lane lists are empty.
    referenced_junctions: Set[str] = set()
    for eid in kept_edges:
        edge = graph.edges.get(eid)
        if edge is None:
            continue
        if edge.from_junction:
            referenced_junctions.add(edge.from_junction)
        if edge.to_junction:
            referenced_junctions.add(edge.to_junction)

    # Create parent directory if needed
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

    # ── Build <net> root element ──
    net_attrib = dict(graph.net_extra_attribs)
    net_attrib['version'] = graph.net_version
    if graph.junction_corner_detail:
        net_attrib['junctionCornerDetail'] = graph.junction_corner_detail
    if graph.limit_turn_speed:
        net_attrib['limitTurnSpeed'] = graph.limit_turn_speed
    if graph.avoid_overlap:
        net_attrib['avoidOverlap'] = graph.avoid_overlap
    # Namespace attributes — only set if not already in net_extra_attribs
    # (ElementTree may not exclude them reliably on all Python versions).
    if 'xmlns:xsi' not in net_attrib:
        net_attrib['xmlns:xsi'] = 'http://www.w3.org/2001/XMLSchema-instance'
    if 'xsi:noNamespaceSchemaLocation' not in net_attrib:
        net_attrib['xsi:noNamespaceSchemaLocation'] = 'http://sumo.dlr.de/xsd/net_file.xsd'

    root = ET.Element('net', net_attrib)

    # ── 1. <location> ──
    if graph.location:
        loc = graph.location
        root.append(_build_location_elem(loc))

    # ── 2. <type> definitions ──
    referenced_types = _collect_referenced_type_ids(graph, kept_edges)
    for type_id in sorted(referenced_types):
        net_type = graph.types.get(type_id)
        if net_type is not None:
            root.append(ET.Element('type', dict(net_type.attribs)))

    # ── 3. Internal edges (sorted by ID) ──
    internal_edges = sorted(
        [eid for eid in kept_edges
         if eid in graph.edges and graph.edges[eid].is_internal]
    )
    for eid in internal_edges:
        root.append(_build_edge_elem(graph.edges[eid]))

    # ── 4. Regular edges (sorted by ID) ──
    regular_edges = sorted(
        [eid for eid in kept_edges
         if eid in graph.edges and not graph.edges[eid].is_internal]
    )
    for eid in regular_edges:
        root.append(_build_edge_elem(graph.edges[eid]))

    # ── 5. tlLogic elements (sorted by ID) ──
    for tl_id in sorted(kept_tl_logics):
        if tl_id in graph.tl_logics:
            root.append(_build_tl_logic_elem(graph.tl_logics[tl_id]))

    # ── 6. Main junctions (type != "internal") — cleaned ──
    main_junctions = sorted(
        [jid for jid in kept_junctions
         if jid in graph.junctions and not graph.junctions[jid].is_internal]
    )
    written_main = 0
    for jid in main_junctions:
        j_elem = _build_junction_elem_clean(graph.junctions[jid], kept_edges, referenced_junctions)
        if j_elem is not None:
            root.append(j_elem)
            written_main += 1

    # ── 7. Internal junctions (type == "internal") — cleaned ──
    internal_junctions = sorted(
        [jid for jid in kept_junctions
         if jid in graph.junctions and graph.junctions[jid].is_internal]
    )
    written_internal = 0
    for jid in internal_junctions:
        j_elem = _build_junction_elem_clean(graph.junctions[jid], kept_edges, referenced_junctions)
        if j_elem is not None:
            root.append(j_elem)
            written_internal += 1

    # ── 8. Connections (preserving original order, filtering to kept set) ──
    for conn in graph.connections:
        if conn.connection_id in kept_connections:
            root.append(_build_connection_elem(conn))

    # ── Write ──
    tree = ET.ElementTree(root)
    ET.indent(tree, space='    ')
    tree.write(output_path, encoding='UTF-8', xml_declaration=True)

    # ── Summary ──
    size_kb = os.path.getsize(output_path) / 1024
    if written_main < len(main_junctions):
        print(
            f"[Writer] Dropped {len(main_junctions) - written_main} main junction(s) "
            f"that had zero remaining lanes after filtering."
        )
    if written_internal < len(internal_junctions):
        print(
            f"[Writer] Dropped {len(internal_junctions) - written_internal} internal "
            f"junction(s) that had zero remaining lanes after filtering."
        )
    print(
        f"[Writer] Written: {output_path} ({size_kb:.1f} KB) — "
        f"{len(referenced_types)} types, "
        f"{len(regular_edges)} regular + {len(internal_edges)} internal edges, "
        f"{written_main} + {written_internal} junctions, "
        f"{len(kept_connections)} connections, "
        f"{len(kept_tl_logics)} tlLogics"
    )


# ═══════════════════════════════════════════════════════════════════════
#  Element builders
# ═══════════════════════════════════════════════════════════════════════

def _build_location_elem(loc) -> ET.Element:
    """Build a <location> element."""
    primary = {
        'netOffset': f'{loc.net_offset[0]:.2f},{loc.net_offset[1]:.2f}',
        'convBoundary': (
            f'{loc.conv_boundary[0]:.2f},{loc.conv_boundary[1]:.2f},'
            f'{loc.conv_boundary[2]:.2f},{loc.conv_boundary[3]:.2f}'
        ),
        'origBoundary': (
            f'{loc.orig_boundary[0]:.2f},{loc.orig_boundary[1]:.2f},'
            f'{loc.orig_boundary[2]:.2f},{loc.orig_boundary[3]:.2f}'
        ),
        'projParameter': loc.proj_parameter,
    }
    return ET.Element('location', _merge_attribs(loc.extra_attribs, primary))


def _build_edge_elem(edge) -> ET.Element:
    """Build an <edge> element with child <lane> elements."""
    primary = {'id': edge.id}
    if edge.function:
        primary['function'] = edge.function
    if edge.from_junction:
        primary['from'] = edge.from_junction
    if edge.to_junction:
        primary['to'] = edge.to_junction
    if not edge.is_internal:
        primary['priority'] = str(edge.priority)

    elem = ET.Element('edge', _merge_attribs(edge.extra_attribs, primary))

    for lane in edge.lanes:
        shape_str = ' '.join(f'{x:.2f},{y:.2f}' for x, y in lane.shape)
        lane_primary = {
            'id': lane.id,
            'index': str(lane.index),
            'speed': f'{lane.speed:.2f}',
            'length': f'{lane.length:.2f}',
            'shape': shape_str,
        }
        lane_elem = ET.Element('lane', _merge_attribs(lane.extra_attribs, lane_primary))
        # Preserve <param> children (e.g. origId tracking)
        for key, value in lane.params.items():
            ET.SubElement(lane_elem, 'param', {'key': key, 'value': value})
        elem.append(lane_elem)

    return elem


def _build_tl_logic_elem(tl) -> ET.Element:
    """Build a <tlLogic> element with child <phase> elements."""
    primary = {
        'id': tl.id,
        'type': tl.tl_type,
        'programID': tl.program_id,
        'offset': str(tl.offset),
    }
    elem = ET.Element('tlLogic', _merge_attribs(tl.extra_attribs, primary))

    for phase in tl.phases:
        phase_primary = {
            'duration': str(phase.duration),
            'state': phase.state,
        }
        elem.append(ET.Element('phase', _merge_attribs(phase.extra_attribs, phase_primary)))

    # Preserve <param> children (e.g. linkSignalID mappings)
    for key, value in tl.params.items():
        ET.SubElement(elem, 'param', {'key': key, 'value': value})

    return elem


def _build_junction_elem_clean(
    junction: Junction,
    kept_edges: Set[str],
    referenced_junctions: Set[str],
) -> ET.Element | None:
    """Build a <junction> element with filtered lane lists and rebuilt requests.

    Returns None if the junction has zero remaining lanes after filtering
    AND no kept edge references it as a from/to node.
    """
    # ── Filter lane lists ──
    kept_inc = [lid for lid in junction.inc_lanes
                if _lane_to_edge_id(lid) in kept_edges]
    kept_int = [lid for lid in junction.int_lanes
                if _lane_to_edge_id(lid) in kept_edges]

    # Drop only when truly orphaned: no surviving lanes AND no kept edge
    # still references this junction as a from/to endpoint.
    if not kept_inc and not kept_int and junction.id not in referenced_junctions:
        return None

    # ── Build attributes ──
    inc_lanes_str = ' '.join(kept_inc)
    int_lanes_str = ' '.join(kept_int)
    shape_str = ' '.join(f'{x:.2f},{y:.2f}' for x, y in junction.shape)

    primary = {
        'id': junction.id,
        'type': junction.jtype,
        'x': f'{junction.x:.2f}',
        'y': f'{junction.y:.2f}',
        'incLanes': inc_lanes_str,
        'intLanes': int_lanes_str,
    }
    if shape_str:
        primary['shape'] = shape_str

    elem = ET.Element('junction', _merge_attribs(junction.extra_attribs, primary))

    # ── Rebuild / emit requests ──
    lanes_changed = (kept_inc != junction.inc_lanes or kept_int != junction.int_lanes)

    if lanes_changed and junction.requests:
        # Request matrix is indexed by intLanes only (internal links),
        # NOT by the combined incLanes + intLanes list.
        old_combined = junction.int_lanes
        new_combined = kept_int
        rebuilt = _rebuild_requests(junction.requests, old_combined, new_combined)
        for req in rebuilt:
            req_primary = {
                'index': str(req.index),
                'response': req.response,
                'foes': req.foes,
                'cont': str(req.cont),
            }
            elem.append(ET.Element('request', _merge_attribs(req.extra_attribs, req_primary)))
    else:
        # All lanes preserved, or no requests — emit as-is
        for req in junction.requests:
            req_primary = {
                'index': str(req.index),
                'response': req.response,
                'foes': req.foes,
                'cont': str(req.cont),
            }
            elem.append(ET.Element('request', _merge_attribs(req.extra_attribs, req_primary)))

    return elem


def _build_connection_elem(conn) -> ET.Element:
    """Build a <connection> element (state last, matching SUMO convention)."""
    primary = {
        'from': conn.from_edge,
        'to': conn.to_edge,
        'fromLane': str(conn.from_lane),
        'toLane': str(conn.to_lane),
    }
    if conn.via:
        primary['via'] = conn.via
    if conn.tl:
        primary['tl'] = conn.tl
    if conn.link_index is not None:
        primary['linkIndex'] = str(conn.link_index)
    if conn.cdir:
        primary['dir'] = conn.cdir
    primary['state'] = conn.state

    return ET.Element('connection', _merge_attribs(conn.extra_attribs, primary))
