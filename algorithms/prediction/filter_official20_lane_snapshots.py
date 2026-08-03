"""Filter SUMO snapshots to the fixed official20 incoming-lane node set.

The graph generator is the source of truth for the 206 forecasting nodes.  This
streaming filter preserves the original snapshot columns, rejects missing graph
nodes, and validates that every retained time point has exactly one row per
node.  It deliberately never rewrites its raw input files.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np


def _nodes_from_adjacency(path: Path) -> tuple[str, ...]:
    archive = np.load(path)
    if "nodes" not in archive:
        raise ValueError(f"adjacency archive lacks nodes: {path}")
    nodes = tuple(str(value) for value in archive["nodes"].tolist())
    if not nodes or len(nodes) != len(set(nodes)):
        raise ValueError("adjacency nodes must be a non-empty unique sequence.")
    if tuple(sorted(nodes)) != nodes:
        raise ValueError("adjacency nodes must use lexical SUMO lane-id order.")
    return nodes


def _filter_one(source: Path, destination: Path, nodes: tuple[str, ...]) -> dict[str, object]:
    node_set = set(nodes)
    seen_by_time: dict[str, set[str]] = defaultdict(set)
    retained_rows = 0
    source_rows = 0
    destination.parent.mkdir(parents=True, exist_ok=True)

    with source.open("r", newline="", encoding="utf-8") as input_handle, destination.open(
        "w", newline="", encoding="utf-8"
    ) as output_handle:
        reader = csv.DictReader(input_handle)
        required = {"elapsed_seconds", "lane_id"}
        if reader.fieldnames is None or required - set(reader.fieldnames):
            raise ValueError(f"{source}: requires columns {sorted(required)}")
        writer = csv.DictWriter(output_handle, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            source_rows += 1
            lane_id = row["lane_id"]
            if lane_id not in node_set:
                continue
            elapsed = row["elapsed_seconds"]
            if lane_id in seen_by_time[elapsed]:
                raise ValueError(f"{source}: duplicate lane {lane_id!r} at {elapsed!r}")
            seen_by_time[elapsed].add(lane_id)
            writer.writerow(row)
            retained_rows += 1

    if not seen_by_time:
        raise ValueError(f"{source}: no official20 graph nodes were found")
    missing = {
        elapsed: sorted(node_set - present)
        for elapsed, present in seen_by_time.items()
        if present != node_set
    }
    if missing:
        example_time, missing_nodes = next(iter(sorted(missing.items())))
        raise ValueError(
            f"{source}: graph-node rows are incomplete at {example_time!r}; "
            f"missing {len(missing_nodes)} lanes (for example {missing_nodes[:5]})."
        )
    return {
        "source": str(source),
        "output": str(destination),
        "source_rows": source_rows,
        "retained_rows": retained_rows,
        "time_points": len(seen_by_time),
        "lane_count": len(nodes),
    }


def filter_snapshots(*, adjacency: Path, inputs: tuple[Path, ...], output_dir: Path) -> dict[str, object]:
    if not inputs:
        raise ValueError("at least one --input is required")
    nodes = _nodes_from_adjacency(adjacency)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for source in inputs:
        if not source.is_file():
            raise FileNotFoundError(source)
        outputs.append(_filter_one(source, output_dir / source.name, nodes))
    payload = {
        "node_count": len(nodes),
        "nodes_sha256": hashlib.sha256("\n".join(nodes).encode("utf-8")).hexdigest(),
        "adjacency": str(adjacency.resolve()),
        "outputs": outputs,
    }
    (output_dir / "official20_lane_filter_metadata.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Filter SUMO snapshots to official20 graph lanes.")
    parser.add_argument("--adjacency", type=Path, required=True)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(filter_snapshots(adjacency=args.adjacency, inputs=tuple(args.input), output_dir=args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
