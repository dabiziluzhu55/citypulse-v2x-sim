"""Tests for SUMO-net-derived intersection adjacency (BFS, not kNN)."""

import json
from pathlib import Path

import pytest

from algorithms.mappo.adjacency import (
    build_intersection_adjacency,
    write_adjacency_files,
)

pytest.importorskip("sumolib")  # 无 sumo 环境跳过；服务器必须 PASS

NET = "algorithms/mappo/testdata/two_tls.net.xml"


def test_two_tls_direct_edge():
    result = build_intersection_adjacency(NET, controlled_ids=("A", "B"))
    assert result["directed"]["A"] == ["B"]
    assert result["directed"]["B"] == ["A"]
    assert result["symmetric"]["A"] == ["B"]
    assert "A" not in result["symmetric"]["A"]  # M_ii=0


def test_adjacency_metadata_contains_hashes_and_degrees():
    result = build_intersection_adjacency(NET, controlled_ids=("A", "B"))
    assert result["meta"]["net_xml_sha256"]
    assert result["meta"]["weakly_connected"] is True
    assert result["meta"]["degrees"]["A"] >= 1
    assert result["meta"]["isolated_nodes"] == []


def test_write_adjacency_files(tmp_path):
    directed_path, symmetric_path = write_adjacency_files(
        NET, ("A", "B"), str(tmp_path)
    )
    directed = json.loads(Path(directed_path).read_text())
    symmetric = json.loads(Path(symmetric_path).read_text())
    assert directed["edges"]["A"] == ["B"]
    assert symmetric["meta"]["weakly_connected"] is True
