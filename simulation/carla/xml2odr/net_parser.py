"""SUMO .net.xml parser using iterative (streaming) XML parsing.

Uses xml.etree.ElementTree.iterparse for memory efficiency with large files
(~195 km² maps can produce 50-200 MB .net.xml files).
"""

import xml.etree.ElementTree as ET
from typing import Dict, Set

from .graph_model import (
    Connection,
    Edge,
    Junction,
    Lane,
    Location,
    NetGraph,
    NetType,
    Phase,
    Request,
    TlLogic,
)


# ═══════════════════════════════════════════════════════════════════════
#  Attribute helpers
# ═══════════════════════════════════════════════════════════════════════

def _extra_attribs(elem: ET.Element, known: Set[str]) -> Dict[str, str]:
    """Return XML attributes not handled by structured dataclass fields."""
    return {k: v for k, v in elem.attrib.items() if k not in known}


# ═══════════════════════════════════════════════════════════════════════
#  Shape parsing
# ═══════════════════════════════════════════════════════════════════════

def _parse_shape(shape_str: str) -> list:
    """Parse a SUMO shape string 'x0,y0 x1,y1 ...' into [(x,y), ...]."""
    if not shape_str or not shape_str.strip():
        return []
    points = []
    for token in shape_str.strip().split():
        try:
            x_str, y_str = token.split(',')
            points.append((float(x_str), float(y_str)))
        except (ValueError, IndexError):
            # Malformed coordinate; skip or return empty
            pass
    return points


def _parse_lane(lane_elem: ET.Element) -> Lane:
    """Parse a <lane> element."""
    known = {'id', 'index', 'speed', 'length', 'shape'}
    # Collect <param> child elements
    params = {}
    for param_elem in lane_elem.findall('param'):
        key = param_elem.get('key', '')
        value = param_elem.get('value', '')
        if key:
            params[key] = value
    return Lane(
        id=lane_elem.get('id', ''),
        index=int(lane_elem.get('index', 0)),
        speed=float(lane_elem.get('speed', 0)),
        length=float(lane_elem.get('length', 0)),
        shape=_parse_shape(lane_elem.get('shape', '')),
        extra_attribs=_extra_attribs(lane_elem, known),
        params=params,
    )


def _parse_edge(edge_elem: ET.Element) -> Edge:
    """Parse an <edge> element including all child <lane> elements."""
    lanes = [_parse_lane(ln) for ln in edge_elem.findall('lane')]

    function = edge_elem.get('function')  # None for regular edges
    from_j = edge_elem.get('from')
    to_j = edge_elem.get('to')
    priority = int(edge_elem.get('priority', -1))
    known = {'id', 'function', 'from', 'to', 'priority'}

    return Edge(
        id=edge_elem.get('id', ''),
        function=function,
        from_junction=from_j,
        to_junction=to_j,
        priority=priority,
        lanes=lanes,
        extra_attribs=_extra_attribs(edge_elem, known),
    )


def _parse_phase(phase_elem: ET.Element) -> Phase:
    """Parse a <phase> element."""
    known = {'duration', 'state'}
    return Phase(
        duration=int(phase_elem.get('duration', 0)),
        state=phase_elem.get('state', ''),
        extra_attribs=_extra_attribs(phase_elem, known),
    )


def _parse_tl_logic(tl_elem: ET.Element) -> TlLogic:
    """Parse a <tlLogic> element including all child <phase> and <param> elements."""
    phases = [_parse_phase(ph) for ph in tl_elem.findall('phase')]
    params = {}
    for param_elem in tl_elem.findall('param'):
        key = param_elem.get('key', '')
        value = param_elem.get('value', '')
        if key:
            params[key] = value
    known = {'id', 'type', 'programID', 'offset'}
    return TlLogic(
        id=tl_elem.get('id', ''),
        tl_type=tl_elem.get('type', 'static'),
        program_id=tl_elem.get('programID', '0'),
        offset=int(tl_elem.get('offset', 0)),
        phases=phases,
        extra_attribs=_extra_attribs(tl_elem, known),
        params=params,
    )


def _parse_request(req_elem: ET.Element) -> Request:
    """Parse a <request> element."""
    known = {'index', 'response', 'foes', 'cont'}
    return Request(
        index=int(req_elem.get('index', 0)),
        response=req_elem.get('response', ''),
        foes=req_elem.get('foes', ''),
        cont=int(req_elem.get('cont', 0)),
        extra_attribs=_extra_attribs(req_elem, known),
    )


def _parse_junction(junc_elem: ET.Element) -> Junction:
    """Parse a <junction> element including all child <request> elements."""
    requests = [_parse_request(rq) for rq in junc_elem.findall('request')]
    inc_lanes_str = junc_elem.get('incLanes', '')
    int_lanes_str = junc_elem.get('intLanes', '')

    return Junction(
        id=junc_elem.get('id', ''),
        jtype=junc_elem.get('type', ''),
        x=float(junc_elem.get('x', 0)),
        y=float(junc_elem.get('y', 0)),
        inc_lanes=inc_lanes_str.split() if inc_lanes_str.strip() else [],
        int_lanes=int_lanes_str.split() if int_lanes_str.strip() else [],
        shape=_parse_shape(junc_elem.get('shape', '')),
        requests=requests,
        extra_attribs=_extra_attribs(
            junc_elem,
            {'id', 'type', 'x', 'y', 'incLanes', 'intLanes', 'shape'},
        ),
    )


def _parse_connection(conn_elem: ET.Element) -> Connection:
    """Parse a <connection> element."""
    link_idx = conn_elem.get('linkIndex')
    known = {'from', 'to', 'fromLane', 'toLane', 'via', 'tl', 'linkIndex', 'dir', 'state'}
    return Connection(
        from_edge=conn_elem.get('from', ''),
        to_edge=conn_elem.get('to', ''),
        from_lane=int(conn_elem.get('fromLane', 0)),
        to_lane=int(conn_elem.get('toLane', 0)),
        via=conn_elem.get('via'),
        tl=conn_elem.get('tl'),
        link_index=int(link_idx) if link_idx is not None else None,
        cdir=conn_elem.get('dir'),
        state=conn_elem.get('state', ''),
        extra_attribs=_extra_attribs(conn_elem, known),
    )


def _parse_location(loc_elem: ET.Element) -> Location:
    """Parse a <location> element."""

    def _parse_comma_pair(s: str) -> tuple:
        parts = s.split(',')
        return (float(parts[0]), float(parts[1]))

    def _parse_comma_four(s: str) -> tuple:
        parts = s.split(',')
        return (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))

    return Location(
        net_offset=_parse_comma_pair(loc_elem.get('netOffset', '0,0')),
        conv_boundary=_parse_comma_four(loc_elem.get('convBoundary', '0,0,0,0')),
        orig_boundary=_parse_comma_four(
            loc_elem.get('origBoundary', '-10000000000,-10000000000,10000000000,10000000000')
        ),
        proj_parameter=loc_elem.get('projParameter', '!'),
        extra_attribs=_extra_attribs(
            loc_elem,
            {'netOffset', 'convBoundary', 'origBoundary', 'projParameter'},
        ),
    )


def _parse_type(type_elem: ET.Element) -> NetType:
    """Parse a <type> element."""
    type_id = type_elem.get('id', '')
    return NetType(id=type_id, attribs=dict(type_elem.attrib))


# ═══════════════════════════════════════════════════════════════════════
#  Main parser
# ═══════════════════════════════════════════════════════════════════════

def parse_net_xml(filepath: str) -> NetGraph:
    """Parse a SUMO .net.xml file into a NetGraph.

    Uses iterative XML parsing (iterparse) for memory efficiency with
    large files. Builds element dataclasses and adjacency indexes.

    Args:
        filepath: Path to the SUMO .net.xml file.

    Returns:
        NetGraph with all edges, junctions, connections, and tlLogics
        plus populated adjacency indexes.

    Raises:
        FileNotFoundError: The file does not exist.
        ET.ParseError: The XML is malformed.
    """
    graph = NetGraph()
    location_elem = None

    # ── Pass 1: Stream-parse all elements ──
    # Track which tags we've seen the root end event for
    root_attrs = {}
    KNOWN_NET_ATTRS = {
        'version', 'junctionCornerDetail', 'limitTurnSpeed', 'avoidOverlap',
        'xmlns:xsi', 'xsi:noNamespaceSchemaLocation',
    }

    # Tags we parse — these are top-level children of <net>
    TOP_LEVEL_TAGS = {'location', 'type', 'edge', 'junction', 'connection', 'tlLogic'}

    try:
        for event, elem in ET.iterparse(filepath, events=('start', 'end')):
            if event == 'start' and elem.tag == 'net':
                # Capture root <net> attributes
                root_attrs = dict(elem.attrib)

            elif event == 'end':
                tag = elem.tag

                # Only process top-level elements; skip children (lane, phase,
                # request) so they remain intact when the parent is processed.
                if tag not in TOP_LEVEL_TAGS:
                    continue

                if tag == 'location':
                    location_elem = elem
                    graph.location = _parse_location(elem)

                elif tag == 'type':
                    net_type = _parse_type(elem)
                    graph.types[net_type.id] = net_type

                elif tag == 'edge':
                    edge = _parse_edge(elem)
                    graph.edges[edge.id] = edge

                elif tag == 'junction':
                    junction = _parse_junction(elem)
                    graph.junctions[junction.id] = junction

                elif tag == 'connection':
                    conn = _parse_connection(elem)
                    graph.connections.append(conn)

                elif tag == 'tlLogic':
                    tl = _parse_tl_logic(elem)
                    graph.tl_logics[tl.id] = tl

                # Clear element to free memory (after processing)
                elem.clear()

    except ET.ParseError as e:
        raise ET.ParseError(
            f"Failed to parse {filepath}: {e}. "
            f"The file may be malformed or not a valid SUMO .net.xml."
        ) from None

    # ── Root attributes ──
    graph.net_version = root_attrs.get('version', '1.0')
    graph.junction_corner_detail = root_attrs.get('junctionCornerDetail')
    graph.limit_turn_speed = root_attrs.get('limitTurnSpeed')
    graph.avoid_overlap = root_attrs.get('avoidOverlap')
    # ElementTree stores namespace-declaration attributes in Clark notation
    # (e.g. {http://www.w3.org/2000/xmlns/}xsi).  Filter them out so they
    # don't leak into net_extra_attribs and cause duplicates in output.
    graph.net_extra_attribs = {}
    for k, v in root_attrs.items():
        if k in KNOWN_NET_ATTRS:
            continue
        # ElementTree stores namespace-declaration and prefixed attributes
        # in Clark notation (e.g. {http://www.w3.org/2000/xmlns/}xsi).
        # Skip anything with a namespace-prefix-like URI to avoid
        # duplicate xmlns / xsi attrs in output.
        if '{' in k:
            continue
        graph.net_extra_attribs[k] = v

    # ── Pass 2: Build adjacency indexes ──
    graph.build_indexes()

    # ── Summary ──
    reg_edges, int_edges = graph.get_edge_count()
    main_juncs, int_juncs = graph.get_junction_count()
    print(
        f"[Parser] Loaded: {len(graph.types)} types, "
        f"{reg_edges} regular edges + {int_edges} internal edges, "
        f"{main_juncs} junctions + {int_juncs} internal junctions, "
        f"{len(graph.connections)} connections, "
        f"{len(graph.tl_logics)} tlLogics"
    )

    if graph.location:
        loc = graph.location
        print(
            f"[Parser] convBoundary: "
            f"X=[{loc.conv_boundary[0]:.0f}, {loc.conv_boundary[2]:.0f}] "
            f"Y=[{loc.conv_boundary[1]:.0f}, {loc.conv_boundary[3]:.0f}]"
        )

    return graph
