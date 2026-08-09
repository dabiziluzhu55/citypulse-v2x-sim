"""Aggregate full lane snapshots into exactly 100 TLS junction nodes.

The input remains untouched. The output uses the historical prediction schema but
stores a traffic-light junction ID in the lane_id column. Every source snapshot
must contain every manifest incoming lane exactly once; unrelated road lanes are
ignored and counted in the metadata.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path


OUTPUT_COLUMNS = (
    "elapsed_seconds",
    "lane_id",
    "vehicle_count",
    "halting_count",
    "mean_speed",
    "occupancy",
)
DEFAULT_EXPECTED_SNAPSHOTS = 721
DEFAULT_INTERVAL_SECONDS = 5.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _node_order_sha256(nodes: tuple[str, ...]) -> str:
    encoded = json.dumps(nodes, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_manifest(
    manifest_path: Path, expected_nodes: int
) -> tuple[tuple[str, ...], dict[str, str], dict[str, object]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes = tuple(str(node) for node in payload.get("nodes", ()))
    if len(nodes) != expected_nodes or len(set(nodes)) != expected_nodes:
        raise ValueError(
            f"manifest must contain exactly {expected_nodes} unique nodes; found {len(nodes)}"
        )
    if tuple(sorted(nodes)) != nodes:
        raise ValueError("manifest nodes must be in stable dictionary order")
    junctions = payload.get("junctions")
    if not isinstance(junctions, dict):
        raise ValueError("manifest must contain a junctions object")

    lane_to_node: dict[str, str] = {}
    for node in nodes:
        entry = junctions.get(node)
        if not isinstance(entry, dict):
            raise ValueError(f"manifest has no entry for node {node!r}")
        incoming = entry.get("incoming_lanes")
        if not isinstance(incoming, list) or not incoming:
            raise ValueError(f"manifest node {node!r} has no incoming_lanes")
        for lane in incoming:
            lane_id = str(lane)
            previous = lane_to_node.setdefault(lane_id, node)
            if previous != node:
                raise ValueError(
                    f"incoming lane {lane_id!r} belongs to {previous!r} and {node!r}"
                )

    recorded_hash = payload.get("node_order_sha256")
    if recorded_hash and recorded_hash != _node_order_sha256(nodes):
        raise ValueError("manifest node_order_sha256 does not match nodes")
    return nodes, lane_to_node, payload


def _number(value: str, field: str, *, nonnegative: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"field {field!r} contains non-numeric value {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"field {field!r} contains non-finite value {value!r}")
    if nonnegative and number < 0:
        raise ValueError(f"field {field!r} contains negative value {value!r}")
    return number


def _metadata(
    *,
    input_path: Path,
    manifest_path: Path,
    output_path: Path | None,
    nodes: tuple[str, ...],
    lane_to_node: dict[str, str],
    input_lane_ids: set[str],
    times: list[float],
    source_rows: int,
    target_rows: int,
    rows_per_snapshot: list[int],
    manifest_payload: dict[str, object],
    expected_snapshots: int,
    interval_seconds: float,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "node_definition": "SUMO traffic_light junction",
        "status": "preflight_ok",
        "input": str(input_path.resolve()),
        "input_sha256": _sha256(input_path),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": _sha256(manifest_path),
        "output": str(output_path.resolve()) if output_path is not None else None,
        "node_count": len(nodes),
        "nodes": list(nodes),
        "node_order_sha256": _node_order_sha256(nodes),
        "incoming_lane_count": len(lane_to_node),
        "input_lane_count": len(input_lane_ids),
        "mapped_input_lane_count": len(input_lane_ids & set(lane_to_node)),
        "unmapped_input_lane_count": len(input_lane_ids - set(lane_to_node)),
        "unmapped_input_lane_sample": sorted(input_lane_ids - set(lane_to_node))[:20],
        "snapshot_count": len(times),
        "expected_snapshot_count": expected_snapshots,
        "first_elapsed_seconds": times[0] if times else None,
        "last_elapsed_seconds": times[-1] if times else None,
        "interval_seconds": interval_seconds,
        "source_rows": source_rows,
        "target_rows": target_rows,
        "output_rows": len(times) * len(nodes),
        "rows_per_source_snapshot_min": min(rows_per_snapshot) if rows_per_snapshot else 0,
        "rows_per_source_snapshot_max": max(rows_per_snapshot) if rows_per_snapshot else 0,
        "manifest_source_net_sha256": manifest_payload.get("source_net_sha256"),
        "aggregation": {
            "vehicle_count": "sum incoming lanes",
            "halting_count": "sum incoming lanes",
            "mean_speed": "vehicle-count weighted mean; zero when total vehicle_count is zero",
            "occupancy": "arithmetic mean over incoming lanes",
            "missing_target_lane": "error",
            "unmapped_non_target_lane": "ignored and counted in metadata",
        },
    }


def aggregate(
    *,
    input_path: Path,
    manifest_path: Path,
    output_path: Path | None,
    metadata_path: Path | None = None,
    expected_nodes: int = 100,
    expected_snapshots: int = DEFAULT_EXPECTED_SNAPSHOTS,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
) -> dict[str, object]:
    """Validate one episode and optionally write the TLS100 aggregate."""

    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if expected_nodes < 1 or expected_snapshots < 1:
        raise ValueError("expected_nodes and expected_snapshots must be positive")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    nodes, lane_to_node, manifest_payload = _load_manifest(manifest_path, expected_nodes)
    target_lanes = set(lane_to_node)

    output_partial: Path | None = None
    metadata_partial: Path | None = None
    writer = None
    target_handle = None
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise FileExistsError(f"refusing to overwrite existing output {output_path}")
        output_partial = Path(str(output_path) + ".partial")
        if output_partial.exists():
            raise FileExistsError(f"partial output already exists: {output_partial}")
        target_handle = output_partial.open("w", newline="", encoding="utf-8")
        writer = csv.DictWriter(target_handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        if metadata_path is None:
            metadata_path = output_path.with_suffix(".metadata.json")
        if metadata_path.exists():
            raise FileExistsError(f"refusing to overwrite existing metadata {metadata_path}")
        metadata_partial = Path(str(metadata_path) + ".partial")
        if metadata_partial.exists():
            raise FileExistsError(f"partial metadata already exists: {metadata_partial}")

    source_rows = 0
    target_rows = 0
    input_lane_ids: set[str] = set()
    times: list[float] = []
    rows_per_snapshot: list[int] = []
    finished_times: set[float] = set()
    expected_source_lanes: set[str] | None = None
    current_time_text: str | None = None
    current_time: float | None = None
    current_seen_lanes: set[str] = set()
    current_target_lanes: set[str] = set()
    current_rows = 0
    state: dict[str, list[float]] = {
        node: [0.0, 0.0, 0.0, 0.0, 0.0] for node in nodes
    }

    def reset_snapshot() -> None:
        nonlocal current_seen_lanes, current_target_lanes, current_rows, state
        current_seen_lanes = set()
        current_target_lanes = set()
        current_rows = 0
        state = {node: [0.0, 0.0, 0.0, 0.0, 0.0] for node in nodes}

    def flush_snapshot() -> None:
        nonlocal expected_source_lanes, target_rows
        if current_time_text is None or current_time is None:
            return
        if expected_source_lanes is None:
            expected_source_lanes = set(current_seen_lanes)
        elif current_seen_lanes != expected_source_lanes:
            missing = sorted(expected_source_lanes - current_seen_lanes)
            added = sorted(current_seen_lanes - expected_source_lanes)
            raise ValueError(
                "source lane set changed between snapshots: "
                f"missing={missing[:10]}, added={added[:10]}"
            )
        missing = target_lanes - current_target_lanes
        if missing:
            raise ValueError(
                f"elapsed_seconds={current_time_text} is missing {len(missing)} target lanes; "
                f"sample={sorted(missing)[:10]}"
            )
        for node in nodes:
            count, halting, speed_total, occupancy_total, lane_count = state[node]
            if writer is not None:
                writer.writerow(
                    {
                        "elapsed_seconds": current_time_text,
                        "lane_id": node,
                        "vehicle_count": f"{count:g}",
                        "halting_count": f"{halting:g}",
                        "mean_speed": f"{speed_total / count if count else 0.0:.6f}",
                        "occupancy": f"{occupancy_total / lane_count if lane_count else 0.0:.6f}",
                    }
                )
        target_rows += len(current_target_lanes)
        times.append(current_time)
        rows_per_snapshot.append(current_rows)

    try:
        with input_path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            missing_columns = set(OUTPUT_COLUMNS) - set(reader.fieldnames or ())
            if missing_columns:
                raise ValueError(
                    f"{input_path} lacks required columns {sorted(missing_columns)}"
                )
            for row in reader:
                source_rows += 1
                timestamp_text = row.get("elapsed_seconds", "")
                timestamp = _number(timestamp_text, "elapsed_seconds")
                if current_time is None:
                    current_time = timestamp
                    current_time_text = timestamp_text
                elif timestamp < current_time:
                    raise ValueError(
                        f"elapsed_seconds is not non-decreasing: {timestamp_text} after "
                        f"{current_time_text}"
                    )
                elif timestamp != current_time:
                    if timestamp in finished_times:
                        raise ValueError(
                            f"snapshot {timestamp_text} appears after a later timestamp"
                        )
                    flush_snapshot()
                    finished_times.add(current_time)
                    reset_snapshot()
                    current_time = timestamp
                    current_time_text = timestamp_text

                lane_id = row.get("lane_id", "")
                if not lane_id:
                    raise ValueError("row has an empty lane_id")
                if lane_id in current_seen_lanes:
                    raise ValueError(
                        f"duplicate row for elapsed_seconds={timestamp_text}, lane_id={lane_id}"
                    )
                current_seen_lanes.add(lane_id)
                input_lane_ids.add(lane_id)
                current_rows += 1

                node = lane_to_node.get(lane_id)
                if node is None:
                    continue
                current_target_lanes.add(lane_id)
                vehicle_count = _number(row["vehicle_count"], "vehicle_count", nonnegative=True)
                halting_count = _number(row["halting_count"], "halting_count", nonnegative=True)
                mean_speed = _number(row["mean_speed"], "mean_speed", nonnegative=True)
                occupancy = _number(row["occupancy"], "occupancy", nonnegative=True)
                item = state[node]
                item[0] += vehicle_count
                item[1] += halting_count
                item[2] += mean_speed * vehicle_count
                item[3] += occupancy
                item[4] += 1.0

            if current_time is None:
                raise ValueError(f"no rows found in {input_path}")
            flush_snapshot()
            finished_times.add(current_time)

        if len(times) != expected_snapshots:
            raise ValueError(
                f"expected {expected_snapshots} snapshots, found {len(times)}"
            )
        if any(
            abs((later - earlier) - interval_seconds) > 1e-6
            for earlier, later in zip(times, times[1:])
        ):
            raise ValueError("elapsed_seconds does not use the expected fixed interval")
        summary = _metadata(
            input_path=input_path,
            manifest_path=manifest_path,
            output_path=output_path,
            nodes=nodes,
            lane_to_node=lane_to_node,
            input_lane_ids=input_lane_ids,
            times=times,
            source_rows=source_rows,
            target_rows=target_rows,
            rows_per_snapshot=rows_per_snapshot,
            manifest_payload=manifest_payload,
            expected_snapshots=expected_snapshots,
            interval_seconds=interval_seconds,
        )

        if output_path is not None and output_partial is not None and metadata_path is not None:
            summary["status"] = "aggregated"
            metadata_partial = Path(str(metadata_path) + ".partial")
            metadata_partial.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + chr(10),
                encoding="utf-8",
            )
            if target_handle is not None:
                target_handle.flush()
                os.fsync(target_handle.fileno())
                target_handle.close()
                target_handle = None
            os.replace(output_partial, output_path)
            os.replace(metadata_partial, metadata_path)
        return summary
    except Exception:
        if target_handle is not None:
            target_handle.close()
        raise


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate dense lane snapshots into TLS100 junction snapshots."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--tls-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--expected-nodes", type=int, default=100)
    parser.add_argument("--expected-snapshots", type=int, default=DEFAULT_EXPECTED_SNAPSHOTS)
    parser.add_argument("--interval-seconds", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="validate and summarize without writing an aggregate CSV",
    )
    args = parser.parse_args(argv)
    if args.preflight_only and args.output is not None:
        raise SystemExit("--preflight-only cannot be combined with --output")
    if not args.preflight_only and args.output is None:
        raise SystemExit("--output is required unless --preflight-only is used")
    summary = aggregate(
        input_path=args.input,
        manifest_path=args.tls_manifest,
        output_path=None if args.preflight_only else args.output,
        metadata_path=args.metadata,
        expected_nodes=args.expected_nodes,
        expected_snapshots=args.expected_snapshots,
        interval_seconds=args.interval_seconds,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
