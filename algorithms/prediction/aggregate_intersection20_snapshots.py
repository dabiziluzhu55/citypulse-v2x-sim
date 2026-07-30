"""Aggregate dense lane snapshots into the official 20 target intersections.

The authoritative ``tls_manifest.json`` maps each CityPulse target intersection
to its incoming lanes.  This module keeps the usual snapshot CSV schema, using
``lane_id`` to hold the stable ``demo_N`` node identifier, so existing dataset
tools can consume the aggregated files without ambiguity.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


METRICS = ("vehicle_count", "halting_count", "mean_speed", "occupancy")


def _mapping(manifest: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    data = json.loads(manifest.read_text(encoding="utf-8"))["intersections"]
    nodes = tuple(data)
    if len(nodes) != 20:
        raise ValueError(f"expected 20 official intersections, found {len(nodes)}")
    lane_to_node: dict[str, str] = {}
    for node in nodes:
        for lanes in data[node]["incoming_lanes"].values():
            for lane in lanes:
                if lane in lane_to_node and lane_to_node[lane] != node:
                    raise ValueError(f"incoming lane {lane} belongs to multiple target intersections")
                lane_to_node[lane] = node
    return nodes, lane_to_node


def aggregate(input_path: Path, output_path: Path, manifest: Path) -> dict[str, object]:
    nodes, lane_to_node = _mapping(manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    seen_lanes: set[str] = set()
    snapshots = 0
    with input_path.open(newline="", encoding="utf-8") as source, output_path.open("w", newline="", encoding="utf-8") as target:
        reader = csv.DictReader(source)
        required = {"elapsed_seconds", "lane_id", *METRICS}
        if required - set(reader.fieldnames or ()):
            raise ValueError(f"{input_path} lacks {sorted(required - set(reader.fieldnames or ()))}")
        writer = csv.DictWriter(target, fieldnames=("elapsed_seconds", "lane_id", *METRICS))
        writer.writeheader()
        current_time: str | None = None
        # count, halting sum, speed numerator, occupancy sum, lane observations
        state: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0])

        def flush() -> None:
            nonlocal snapshots
            if current_time is None:
                return
            for node in nodes:
                count, halting, speed_total, occupancy_total, lane_count = state[node]
                writer.writerow({
                    "elapsed_seconds": current_time, "lane_id": node,
                    "vehicle_count": f"{count:g}", "halting_count": f"{halting:g}",
                    "mean_speed": f"{(speed_total / count) if count else 0.0:.6f}",
                    "occupancy": f"{(occupancy_total / lane_count) if lane_count else 0.0:.6f}",
                })
            snapshots += 1

        for row in reader:
            timestamp = row["elapsed_seconds"]
            if current_time is not None and timestamp != current_time:
                flush()
                state.clear()
            current_time = timestamp
            node = lane_to_node.get(row["lane_id"])
            if node is None:
                continue
            seen_lanes.add(row["lane_id"])
            count = float(row["vehicle_count"])
            item = state[node]
            item[0] += count
            item[1] += float(row["halting_count"])
            item[2] += float(row["mean_speed"]) * count
            item[3] += float(row["occupancy"])
            item[4] += 1
        flush()
    missing = sorted(set(lane_to_node) - seen_lanes)
    if missing:
        raise ValueError(f"source CSV lacks {len(missing)} official incoming lanes, e.g. {missing[:5]}")
    return {"input": str(input_path), "output": str(output_path), "snapshots": snapshots, "nodes": list(nodes), "incoming_lane_count": len(lane_to_node)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Aggregate lane CSV snapshots to the official 20 intersections.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tls-manifest", type=Path, required=True)
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args(argv)
    result = aggregate(args.input, args.output, args.tls_manifest)
    result["aggregation"] = {
        "vehicle_count": "sum incoming lanes", "halting_count": "sum incoming lanes",
        "mean_speed": "vehicle-count weighted mean", "occupancy": "mean over incoming lanes",
        "tls_manifest_sha256": hashlib.sha256(args.tls_manifest.read_bytes()).hexdigest(),
    }
    if args.metadata:
        args.metadata.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
