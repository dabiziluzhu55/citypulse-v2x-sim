"""Export dense lane snapshots from any standard SUMO ``.sumocfg`` scenario.

Unlike ``simulation.sumo.export_snapshots``, this module does not require this
project's official-map metadata.  It is intended for external benchmark
networks such as Grid4x4 and Ingolstadt21.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path


METRICS = (
    "vehicle_count",
    "halting_count",
    "mean_speed",
    "occupancy",
)


def _load_traci(sumo_home: Path | None):
    """Import SUMO's Python tools, including distro installations."""

    candidates = []
    if sumo_home is not None:
        candidates.append(sumo_home)
    if os.environ.get("SUMO_HOME"):
        candidates.append(Path(os.environ["SUMO_HOME"]))
    candidates.append(Path("/usr/share/sumo"))

    for home in candidates:
        tools = home / "tools"
        if tools.exists() and str(tools) not in sys.path:
            sys.path.insert(0, str(tools))
    try:
        import traci  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Could not import TraCI. Set SUMO_HOME to a SUMO installation "
            "whose tools/ directory contains traci."
        ) from exc
    return traci


def _external_lane_ids(traci) -> tuple[str, ...]:
    """Return deterministic, non-internal SUMO lane IDs."""

    return tuple(sorted(lane_id for lane_id in traci.lane.getIDList() if not lane_id.startswith(":")))


def _write_snapshot(
    writer: csv.DictWriter,
    traci,
    elapsed_seconds: float,
    lane_ids: tuple[str, ...],
    metrics: tuple[str, ...],
) -> None:
    for lane_id in lane_ids:
        row: dict[str, object] = {
            "elapsed_seconds": f"{elapsed_seconds:g}",
            "lane_id": lane_id,
        }
        if "vehicle_count" in metrics:
            row["vehicle_count"] = traci.lane.getLastStepVehicleNumber(lane_id)
        if "halting_count" in metrics:
            row["halting_count"] = traci.lane.getLastStepHaltingNumber(lane_id)
        if "mean_speed" in metrics:
            row["mean_speed"] = f"{traci.lane.getLastStepMeanSpeed(lane_id):.6f}"
        if "occupancy" in metrics:
            row["occupancy"] = f"{traci.lane.getLastStepOccupancy(lane_id):.6f}"
        writer.writerow(row)


def collect_snapshots(
    *,
    sumocfg: Path,
    output: Path,
    snapshot_interval: float,
    duration: float | None,
    seed: int | None,
    demand_scale: float | None,
    metrics: tuple[str, ...],
    sumo_binary: str,
    sumo_home: Path | None,
) -> dict[str, object]:
    if snapshot_interval <= 0:
        raise ValueError("snapshot_interval must be positive.")
    if duration is not None and duration <= 0:
        raise ValueError("duration must be positive when provided.")
    if demand_scale is not None and demand_scale <= 0:
        raise ValueError("demand_scale must be positive when provided.")
    if not metrics:
        raise ValueError("at least one metric is required.")
    unknown_metrics = set(metrics) - set(METRICS)
    if unknown_metrics:
        raise ValueError(f"unknown metrics: {sorted(unknown_metrics)}")
    sumocfg = sumocfg.resolve()
    if not sumocfg.is_file():
        raise FileNotFoundError(f"SUMO configuration not found: {sumocfg}")

    traci = _load_traci(sumo_home)
    command = [sumo_binary, "-c", str(sumocfg)]
    if seed is not None:
        command.extend(["--seed", str(seed)])
    if demand_scale is not None:
        # SUMO applies --scale while loading demand, so this remains a
        # deterministic scenario variant when paired with --seed.
        command.extend(["--scale", str(demand_scale)])

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = output.with_suffix(output.suffix + ".partial")
    traci.start(command)
    try:
        lane_ids = _external_lane_ids(traci)
        if not lane_ids:
            raise RuntimeError("The SUMO scenario has no non-internal lanes.")

        sample_count = 0
        simulation_start_seconds = float(traci.simulation.getTime())
        next_sample_seconds = 0.0
        # A failed or still-running collector must never look like a finished
        # resumable episode to an orchestration script.  Publish only after
        # every snapshot has been written successfully.
        with temporary_output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("elapsed_seconds", "lane_id", *metrics))
            writer.writeheader()
            while True:
                elapsed_seconds = float(traci.simulation.getTime()) - simulation_start_seconds
                if elapsed_seconds + 1e-9 >= next_sample_seconds:
                    _write_snapshot(writer, traci, elapsed_seconds, lane_ids, metrics)
                    sample_count += 1
                    next_sample_seconds += snapshot_interval

                if duration is not None and elapsed_seconds >= duration:
                    break
                if traci.simulation.getMinExpectedNumber() <= 0:
                    break
                traci.simulationStep()

        temporary_output.replace(output)

        return {
            "sumocfg": str(sumocfg),
            "output": str(output),
            "lane_count": len(lane_ids),
            "sample_count": sample_count,
            "snapshot_interval": snapshot_interval,
            "duration_limit": duration,
            "simulation_start_seconds": simulation_start_seconds,
            "seed": seed,
            "demand_scale": demand_scale,
            "metrics": metrics,
        }
    finally:
        traci.close(False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export dense lane snapshots from a standard SUMO .sumocfg file."
    )
    parser.add_argument("--sumocfg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot-interval", type=float, default=5.0)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--metrics",
        nargs="+",
        choices=METRICS,
        default=list(METRICS),
        help="Lane metrics to export; defaults to all metrics.",
    )
    parser.add_argument(
        "--demand-scale",
        type=float,
        default=None,
        help="Pass SUMO --scale to produce a demand-strength variant.",
    )
    parser.add_argument("--sumo-binary", default="sumo")
    parser.add_argument("--sumo-home", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    summary = collect_snapshots(
        sumocfg=args.sumocfg,
        output=args.output,
        snapshot_interval=args.snapshot_interval,
        duration=args.duration,
        seed=args.seed,
        demand_scale=args.demand_scale,
        metrics=tuple(args.metrics),
        sumo_binary=args.sumo_binary,
        sumo_home=args.sumo_home,
    )
    for key, value in summary.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
