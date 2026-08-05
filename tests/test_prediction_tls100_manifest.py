import json

from algorithms.prediction.build_tls100_junction_manifest import build_manifest


def _write_network(path):
    path.write_text(
        """<net>
  <junction id="A" type="traffic_light" incLanes="in_a_0"/>
  <junction id="B" type="traffic_light" incLanes="in_b_0"/>
  <edge id="in_a" from="outside_a" to="A"><lane id="in_a_0" index="0" speed="10" length="10"/></edge>
  <edge id="in_b" from="A" to="B"><lane id="in_b_0" index="0" speed="10" length="10"/></edge>
  <edge id="out_b" from="B" to="outside_b"><lane id="out_b_0" index="0" speed="10" length="10"/></edge>
  <connection from="in_a" to="in_b" fromLane="0" toLane="0" tl="alias_A"/>
  <connection from="in_b" to="out_b" fromLane="0" toLane="0"/>
</net>
""",
        encoding="utf-8",
    )


def test_tls_manifest_resolves_connection_alias_and_keeps_exact_nodes(tmp_path):
    net = tmp_path / "network.net.xml"
    manifest_path = tmp_path / "manifest.json"
    _write_network(net)

    payload = build_manifest(net=net, output=manifest_path, expected_count=2)

    assert payload["nodes"] == ["A", "B"]
    assert payload["tl_to_junction"] == {"alias_A": "A"}
    assert payload["junctions"]["A"]["mapping_method"] == "connection_tl_to_incLanes"
    assert payload["junctions"]["B"]["mapping_method"] == "junction_incLanes_without_connection_tl"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["node_count"] == 2
