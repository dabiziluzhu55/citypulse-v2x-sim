"""Data model for SUMO .net.xml road network graph."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════
#  Element dataclasses (1:1 with XML elements)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Lane:
    """A single lane within an edge."""
    id: str
    index: int
    speed: float
    length: float
    shape: List[Tuple[float, float]]  # [(x, y), ...]
    extra_attribs: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, str] = field(default_factory=dict)  # <param key="..." value="..."/> children


@dataclass
class Edge:
    """A SUMO edge (road segment or internal junction link)."""
    id: str
    function: Optional[str]          # "internal" or None for regular edges
    from_junction: Optional[str]     # None for internal edges
    to_junction: Optional[str]       # None for internal edges
    priority: int
    lanes: List[Lane]
    extra_attribs: Dict[str, str] = field(default_factory=dict)

    @property
    def is_internal(self) -> bool:
        return self.function == "internal"

    # owner_junction removed — logic is now inline in NetGraph.build_indexes()
    # where it has access to the full junction registry for longest-prefix matching.

    @property
    def first_lane_length(self) -> float:
        """Length of the first lane, used for topological distance."""
        return self.lanes[0].length if self.lanes else 0.0


@dataclass
class Phase:
    """A traffic light phase."""
    duration: int
    state: str
    extra_attribs: Dict[str, str] = field(default_factory=dict)


@dataclass
class TlLogic:
    """Traffic light logic for a junction."""
    id: str              # junction ID this tlLogic belongs to
    tl_type: str         # "static" or "actuated"
    program_id: str
    offset: int
    phases: List[Phase]
    extra_attribs: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, str] = field(default_factory=dict)  # <param key="..." value="..."/> children


@dataclass
class Request:
    """Right-of-way request within a junction."""
    index: int
    response: str
    foes: str
    cont: int
    extra_attribs: Dict[str, str] = field(default_factory=dict)


@dataclass
class Junction:
    """A SUMO junction (intersection or dead-end)."""
    id: str
    jtype: str                      # "traffic_light", "dead_end", "internal", etc.
    x: float
    y: float
    inc_lanes: List[str]            # incoming lane IDs
    int_lanes: List[str]            # internal lane IDs
    shape: List[Tuple[float, float]]
    requests: List[Request] = field(default_factory=list)
    extra_attribs: Dict[str, str] = field(default_factory=dict)

    @property
    def is_internal(self) -> bool:
        return self.jtype == "internal"


@dataclass
class NetType:
    """A SUMO edge/lane type definition from <type> elements."""
    id: str
    attribs: Dict[str, str] = field(default_factory=dict)


@dataclass
class Connection:
    """A connection linking incoming edge to outgoing edge via internal lanes."""
    from_edge: str
    to_edge: str
    from_lane: int
    to_lane: int
    via: Optional[str]             # internal edge ID
    tl: Optional[str]              # traffic light junction ID
    link_index: Optional[int]
    cdir: Optional[str]            # "r" (right), "s" (straight), "l" (left)
    state: str                     # "o", "O", "M", "m"
    extra_attribs: Dict[str, str] = field(default_factory=dict)

    @property
    def connection_id(self) -> str:
        """Unique identifier for this connection."""
        via_str = f"_via_{self.via}" if self.via else ""
        return f"{self.from_edge}_to_{self.to_edge}_l{self.from_lane}_l{self.to_lane}{via_str}"


@dataclass
class Location:
    """Geographic location metadata from the net.xml."""
    net_offset: Tuple[float, float]
    conv_boundary: Tuple[float, float, float, float]
    orig_boundary: Tuple[float, float, float, float]
    proj_parameter: str
    extra_attribs: Dict[str, str] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════
#  Top-level graph container
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class NetGraph:
    """Complete parsed .net.xml road network with fast index lookups."""

    # Root-level metadata
    net_version: str = "1.0"
    junction_corner_detail: Optional[str] = None
    limit_turn_speed: Optional[str] = None
    avoid_overlap: Optional[str] = None
    net_extra_attribs: Dict[str, str] = field(default_factory=dict)
    location: Optional[Location] = None

    # Element registries by ID
    types: Dict[str, NetType] = field(default_factory=dict)
    edges: Dict[str, Edge] = field(default_factory=dict)
    junctions: Dict[str, Junction] = field(default_factory=dict)
    connections: List[Connection] = field(default_factory=list)
    tl_logics: Dict[str, TlLogic] = field(default_factory=dict)

    # ── Adjacency index (populated after parsing) ──
    # For each junction ID, list of (edge_id, neighbor_junction_id) tuples
    junction_edges: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)

    # Internal edges grouped by owner junction ID
    # e.g. {'J1': [':J1_0', ':J1_1', ...], ...}
    junction_internal_edges: Dict[str, List[str]] = field(default_factory=dict)

    def build_indexes(self):
        """Build adjacency and internal-edge indexes after all elements are parsed.

        Call this once after parsing is complete.
        """
        # ── Junction edges adjacency ──
        for edge in self.edges.values():
            if edge.is_internal:
                continue
            # Add to from_junction's adjacency
            if edge.from_junction and edge.from_junction in self.junctions:
                self.junction_edges.setdefault(edge.from_junction, []).append(
                    (edge.id, edge.to_junction)
                )
            # Add to to_junction's adjacency (bidirectional for traversal)
            if edge.to_junction and edge.to_junction in self.junctions:
                self.junction_edges.setdefault(edge.to_junction, []).append(
                    (edge.id, edge.from_junction)
                )

        # ── Internal edges by owner junction ──
        # Internal-edge IDs follow the pattern  :<junction_id>_<index>
        # where <junction_id> may itself contain underscores (e.g.
        # cluster_205_J67).  We find the owner by trying the longest
        # possible prefix that matches a known junction ID.
        for edge in self.edges.values():
            if not edge.is_internal:
                continue
            inner = edge.id.lstrip(':')
            parts = inner.split('_')
            # Walk from longest candidate to shortest
            for n in range(len(parts), 0, -1):
                candidate = '_'.join(parts[:n])
                if candidate in self.junctions:
                    self.junction_internal_edges.setdefault(candidate, []).append(edge.id)
                    break

    def get_junction_count(self) -> int:
        """Return counts for summary display."""
        main = sum(1 for j in self.junctions.values() if not j.is_internal)
        internal = sum(1 for j in self.junctions.values() if j.is_internal)
        return main, internal

    def get_edge_count(self) -> int:
        """Return counts for summary display."""
        regular = sum(1 for e in self.edges.values() if not e.is_internal)
        internal = sum(1 for e in self.edges.values() if e.is_internal)
        return regular, internal
