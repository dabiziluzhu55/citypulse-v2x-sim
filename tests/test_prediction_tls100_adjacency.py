import json

import numpy as np

from algorithms.prediction.build_tls100_junction_adjacency import build
from algorithms.prediction.build_tls100_junction_manifest import build_manifest
from tests.test_prediction_tls100_manifest import _write_network


def test_junction_adjacency_is_node_level_and_symmetric_for_stgcn(tmp_path):
    net = tmp_path / "network.net.xml"
    manifest = tmp_path / "manifest.json"
    archive = tmp_path / "adjacency.npz"
    report_dir = tmp_path / "report"
    _write_network(net)
    build_manifest(net=net, output=manifest, expected_count=2)

    summary = build(
        tls_manifest=manifest,
        net=net,
        output=archive,
        report_dir=report_dir,
        expected_count=2,
        max_hops=4,
    )

    saved = np.load(archive)
    adjacency = saved["adjacency"]
    directed = saved["adjacency_directed"]
    assert tuple(saved["nodes"].tolist()) == ("A", "B")
    assert adjacency.shape == (2, 2)
    assert directed[0, 1] == 1
    assert directed[1, 0] == 0
    assert np.array_equal(adjacency, adjacency.T)
    assert np.all(np.diag(adjacency) == 1)
    assert summary["node_definition"] == "SUMO traffic_light junction"
    assert summary["fallback"] == "none"
    assert summary["isolated_node_count"] == 0
    assert json.loads(
        (report_dir / "tls100_junction_adjacency_summary.json").read_text(encoding="utf-8")
    )["directed_edge_count"] == 1
