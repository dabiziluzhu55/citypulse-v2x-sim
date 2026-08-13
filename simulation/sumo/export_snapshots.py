"""Export SUMO runtime snapshots as flat CSV training data."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from .run import _load_events
from .engine.session import SimulationConfig, SimulationManager


LANE_FIELDS = [
    "session_id",
    "state",
    "sequence",
    "elapsed_seconds",
    "official_time",
    "intersection_id",
    "current_phase",
    "pending_phase",
    "stage",
    "stage_elapsed",
    "lane_id",
    "edge_id",
    "lane_index",
    "role",
    "approach_id",
    "downstream_lane_ids",
    "lane_has_green",
    "signal_state",
    "current_allowed_speed_mps",
    "vehicle_count",
    "halting_count",
    "mean_speed",
    "waiting_time",
    "occupancy",
    "active_vehicles",
    "departed_vehicles",
    "arrived_vehicles",
    "remaining_vehicles",
    "halting_vehicles",
    "total_waiting_time",
    "global_mean_speed",
]

VEHICLE_FIELDS = [
    "session_id",
    "sequence",
    "elapsed_seconds",
    "official_time",
    "vehicle_id",
    "x",
    "y",
    "speed",
    "angle",
    "road_id",
    "lane_id",
]


def _parse_origins(values: list[str]) -> dict[str, tuple[str, ...]]:
    origins: dict[str, list[str]] = {}
    for value in values:
        if ":" not in value:
            raise argparse.ArgumentTypeError(
                f"origin must look like intersection:origin, got {value!r}"
            )
        intersection_id, origin_id = value.split(":", 1)
        if not intersection_id or not origin_id:
            raise argparse.ArgumentTypeError(
                f"origin must look like intersection:origin, got {value!r}"
            )
        origins.setdefault(intersection_id, []).append(origin_id)
    return {key: tuple(items) for key, items in origins.items()}


def _lane_rows(snapshot):
    metrics = snapshot.metrics
    for intersection_id, intersection in snapshot.intersections.items():
        for lane_id, lane in intersection.lanes.items():
            yield {
                "session_id": snapshot.session_id,
                "state": snapshot.state,
                "sequence": snapshot.sequence,
                "elapsed_seconds": snapshot.elapsed_seconds,
                "official_time": snapshot.official_time,
                "intersection_id": intersection_id,
                "current_phase": intersection.current_phase,
                "pending_phase": intersection.pending_phase,
                "stage": intersection.stage,
                "stage_elapsed": intersection.stage_elapsed,
                "lane_id": lane_id,
                "edge_id": lane.edge_id,
                "lane_index": lane.lane_index,
                "role": lane.role,
                "approach_id": lane.approach_id or "",
                "downstream_lane_ids": ";".join(lane.downstream_lane_ids),
                "lane_has_green": lane.lane_has_green,
                "signal_state": lane.signal_state or "",
                "current_allowed_speed_mps": lane.current_allowed_speed_mps,
                "vehicle_count": lane.vehicle_count,
                "halting_count": lane.halting_count,
                "mean_speed": lane.mean_speed,
                "waiting_time": lane.waiting_time,
                "occupancy": lane.occupancy,
                "active_vehicles": metrics.active_vehicles,
                "departed_vehicles": metrics.departed_vehicles,
                "arrived_vehicles": metrics.arrived_vehicles,
                "remaining_vehicles": metrics.remaining_vehicles,
                "halting_vehicles": metrics.halting_vehicles,
                "total_waiting_time": metrics.total_waiting_time,
                "global_mean_speed": metrics.mean_speed,
            }


def _vehicle_rows(snapshot):
    for vehicle in snapshot.vehicles:
        yield {
            "session_id": snapshot.session_id,
            "sequence": snapshot.sequence,
            "elapsed_seconds": snapshot.elapsed_seconds,
            "official_time": snapshot.official_time,
            "vehicle_id": vehicle.vehicle_id,
            "x": vehicle.x,
            "y": vehicle.y,
            "speed": vehicle.speed,
            "angle": vehicle.angle,
            "road_id": vehicle.road_id,
            "lane_id": vehicle.lane_id,
        }


def _writer(path: Path, fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    return handle, writer


def export_snapshots(args: argparse.Namespace) -> None:
    manager_kwargs = {}
    if args.generated_dir is not None:
        manager_kwargs["generated_dir"] = args.generated_dir
    if args.session_root is not None:
        manager_kwargs["session_root"] = args.session_root
    manager = SimulationManager(**manager_kwargs)
    config = SimulationConfig(
        intersection_ids=tuple(args.intersection or ["demo_2"]),
        period=args.period,
        origins=_parse_origins(args.origin),
        window_start_seconds=args.window_start,
        duration_seconds=args.duration,
        flow_multiplier=args.flow_multiplier,
        control_mode="fixed",
        seed=args.seed,
        step_length=args.step_length,
        snapshot_interval_seconds=args.snapshot_interval,
        initial_events=_load_events(args.event_file),
        gui=args.gui,
        realtime=args.realtime,
    )
    session_id = manager.start(config)
    subscription = manager.subscribe(session_id)
    lane_handle, lane_writer = _writer(args.output, LANE_FIELDS)
    vehicle_handle = None
    vehicle_writer = None
    if args.vehicle_output:
        vehicle_handle, vehicle_writer = _writer(args.vehicle_output, VEHICLE_FIELDS)

    try:
        seen_sequences: set[int] = set()
        while True:
            snapshot = subscription.get(timeout=args.timeout)
            if snapshot.sequence not in seen_sequences:
                lane_writer.writerows(_lane_rows(snapshot))
                lane_handle.flush()
                if vehicle_writer is not None:
                    vehicle_writer.writerows(_vehicle_rows(snapshot))
                    vehicle_handle.flush()
                seen_sequences.add(snapshot.sequence)
            if snapshot.state in {"STOPPED", "COMPLETED", "FAILED"}:
                if snapshot.error:
                    raise RuntimeError(snapshot.error)
                break
    finally:
        subscription.close()
        lane_handle.close()
        if vehicle_handle is not None:
            vehicle_handle.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a fixed-time SUMO session and export lane snapshots to CSV."
    )
    parser.add_argument("--intersection", action="append", default=None)
    parser.add_argument(
        "--period",
        choices=("morning_peak", "off_peak", "evening_peak"),
        default="morning_peak",
    )
    parser.add_argument(
        "--origin",
        action="append",
        default=[],
        help="Limit traffic to one official origin, e.g. demo_2:west. Repeatable.",
    )
    parser.add_argument("--window-start", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--flow-multiplier", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--event-file", type=Path, default=None)
    parser.add_argument("--step-length", type=float, default=0.05)
    parser.add_argument("--snapshot-interval", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--generated-dir",
        type=Path,
        default=None,
        help="Use an explicit generated SUMO artifact directory.",
    )
    parser.add_argument(
        "--session-root",
        type=Path,
        default=None,
        help="Write temporary SUMO session files under this directory.",
    )
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--realtime", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/prediction/demo_2_morning_peak_lanes.csv"),
    )
    parser.add_argument("--vehicle-output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    export_snapshots(parser.parse_args(argv))


if __name__ == "__main__":
    main()
