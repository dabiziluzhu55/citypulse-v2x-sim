import json

import numpy as np

from algorithms.prediction.build_official20_lane_adjacency import build


def test_builds_auditable_official20_lane_graph(tmp_path):
    intersections = {}
    for number in range(1, 21):
        intersection_id = f"demo_{number}"
        source_edge = f"in{number}"
        target_edge = "bridge" if number == 1 else f"out{number}"
        intersections[intersection_id] = {
            "connections": [{
                "from_edge": source_edge, "from_lane": 0,
                "to_edge": target_edge, "to_lane": 0,
                "movement": "through",
            }]
        }
    intersections["demo_1"]["connections"].append({
        "from_edge": "in1", "from_lane": 1,
        "to_edge": "bridge", "to_lane": 0,
        "movement": "right",
    })
    manifest = tmp_path / "tls_manifest.json"
    manifest.write_text(json.dumps({"intersections": intersections}), encoding="utf-8")
    net = tmp_path / "network.net.xml"
    net.write_text(
        "<net>"
        "<connection from=\"in1\" fromLane=\"0\" to=\"bridge\" toLane=\"0\"/>"
        "<connection from=\"in1\" fromLane=\"1\" to=\"bridge\" toLane=\"0\"/>"
        "<connection from=\"bridge\" fromLane=\"0\" to=\"in2\" toLane=\"0\"/>"
        "</net>",
        encoding="utf-8",
    )
    output = tmp_path / "adjacency.npz"
    summary = build(tls_manifest=manifest, net=net, output=output, report_dir=tmp_path / "report")

    graph = np.load(output)
    nodes = list(graph["nodes"])
    source, adjacent, target = (nodes.index("in1_0"), nodes.index("in1_1"), nodes.index("in2_0"))
    assert summary["node_count"] == 21
    assert graph["adjacency_lateral"][source, adjacent] > 0
    assert graph["adjacency_next_target"][source, target] == 1.0
    assert graph["adjacency_directed"][source, target] == 1.0
    assert graph["adjacency"][source, target] == graph["adjacency"][target, source] == 1.0
    assert (tmp_path / "report" / "official20_lane_topology.csv").is_file()
