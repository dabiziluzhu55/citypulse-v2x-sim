"""Export SUMO lane CSV data into the simple STGCN repository format."""

from __future__ import annotations

import argparse
import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import scipy.sparse as sp

from .dataset import load_lane_series


def _lane_id(edge_id: str, lane_index: str) -> str:
    return f"{edge_id}_{lane_index}"


def build_lane_adjacency(lanes: tuple[str, ...], net_path: Path | None) -> np.ndarray:
    lane_to_index = {lane: index for index, lane in enumerate(lanes)}
    adj = np.zeros((len(lanes), len(lanes)), dtype=np.float32)

    if net_path is not None and net_path.exists():
        root = ET.parse(net_path).getroot()
        for connection in root.iter("connection"):
            from_edge = connection.get("from")
            to_edge = connection.get("to")
            from_lane = connection.get("fromLane")
            to_lane = connection.get("toLane")
            if None in (from_edge, to_edge, from_lane, to_lane):
                continue
            source = _lane_id(from_edge, from_lane)
            target = _lane_id(to_edge, to_lane)
            if source in lane_to_index and target in lane_to_index:
                adj[lane_to_index[source], lane_to_index[target]] = 1.0

    # STGCN needs a connected graph signal. For this single-intersection first
    # version, keep all lanes weakly related if SUMO lane-level connections are
    # sparse after filtering to only the controlled lanes.
    if float(adj.sum()) == 0.0:
        adj[:] = 1.0
        np.fill_diagonal(adj, 0.0)
    np.fill_diagonal(adj, 1.0)
    return adj


def export_dataset(args: argparse.Namespace) -> None:
    series = load_lane_series(args.input, args.target)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    vel_path = output_dir / "vel.csv"
    with vel_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerows(series.values)

    adj = build_lane_adjacency(series.lanes, args.net)
    sp.save_npz(output_dir / "adj.npz", sp.csc_matrix(adj))

    metadata = {
        "source": str(args.input),
        "target": args.target,
        "time_points": len(series.times),
        "lane_count": len(series.lanes),
        "interval_seconds": series.interval_seconds,
        "lanes": list(series.lanes),
        "adjacency_edges": int(adj.sum()),
        "net": str(args.net) if args.net else None,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create STGCN vel.csv and adj.npz from SUMO lane CSV.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--target", default="vehicle_count")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--net",
        type=Path,
        default=Path("data/maps/sumo/generated/TotalMap_20.signals.net.xml"),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    export_dataset(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
