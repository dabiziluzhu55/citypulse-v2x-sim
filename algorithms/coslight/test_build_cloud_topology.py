import pytest

from algorithms.coslight import build_cloud_topology as cloud_topology


def test_strong_components_are_deterministic_and_cover_isolates():
    nodes = ["demo_3", "demo_1", "demo_2", "demo_4"]
    adjacency = {
        "demo_1": ["demo_2"],
        "demo_2": ["demo_1", "demo_3"],
        "demo_3": [],
    }

    assert cloud_topology._strongly_connected_components(nodes, adjacency) == [
        ["demo_1", "demo_2"],
        ["demo_3"],
        ["demo_4"],
    ]


def test_corridor_components_ignore_direction_but_keep_isolates_separate():
    links = [
        {"source": "demo_2", "target": "demo_1"},
        {"source": "demo_2", "target": "demo_3"},
    ]

    assert cloud_topology._weak_components(
        ["demo_1", "demo_2", "demo_3", "demo_4"], links
    ) == [["demo_1", "demo_2", "demo_3"], ["demo_4"]]


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        ((0.0, 0.0), (10.0, 1.0), "east"),
        ((0.0, 0.0), (-10.0, 1.0), "west"),
        ((0.0, 0.0), (1.0, 10.0), "north"),
        ((0.0, 0.0), (1.0, -10.0), "south"),
    ],
)
def test_cardinal_direction_uses_network_coordinates(source, target, expected):
    assert cloud_topology._direction(source, target) == expected


def test_document_validation_rejects_overlapping_regions():
    document = {
        "schema_version": cloud_topology.SCHEMA_VERSION,
        "intersections": {"demo_1": {}, "demo_2": {}},
        "regions": [
            {"intersections": ["demo_1", "demo_2"]},
            {"intersections": ["demo_2"]},
        ],
        "corridors": [],
        "directed_links": [],
    }

    with pytest.raises(ValueError, match="partition"):
        cloud_topology._validate_document(document)


def test_shortest_path_records_target_edge_and_free_flow_time():
    class Node:
        def __init__(self, node_id):
            self.node_id = node_id
            self.outgoing = []

        def getID(self):
            return self.node_id

        def getOutgoing(self):
            return self.outgoing

    class Edge:
        def __init__(self, edge_id, length, speed, target):
            self.edge_id = edge_id
            self.length = length
            self.speed = speed
            self.target = target

        def getID(self):
            return self.edge_id

        def getLength(self):
            return self.length

        def getSpeed(self):
            return self.speed

        def getToNode(self):
            return self.target

        def allows(self, _vehicle_class):
            return True

    class Net:
        def __init__(self, nodes):
            self.nodes = nodes

        def getNode(self, node_id):
            return self.nodes[node_id]

    middle = Node("middle")
    target = Node("target")
    first = Edge("source_out", 100.0, 10.0, middle)
    middle.outgoing = [Edge("target_in", 50.0, 5.0, target)]

    routes = cloud_topology._targets_from_outgoing_edge(
        Net({"middle": middle, "target": target}),
        first,
        source_node_id="source",
        intersection_by_node={"target": "b"},
        vehicle_class="passenger",
        max_distance_m=1000.0,
    )

    assert routes == {
        "b": {
            "distance_m": 150.0,
            "free_flow_time_s": 20.0,
            "target_incoming_edge": "target_in",
        }
    }
