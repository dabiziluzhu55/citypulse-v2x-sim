"""Select active SUMO lanes using training episodes and filter dense CSVs.

The selection is deliberately fit on training inputs only.  The resulting lane
set is then applied unchanged to every split so validation and test traffic do
not influence which graph nodes the model is allowed to see.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class LaneActivity:
    lane_id: str
    observed_samples: int
    positive_vehicle_samples: int
    positive_ratio: float
    keep: bool


def _read_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"elapsed_seconds", "lane_id", "vehicle_count"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path} is missing required columns: {sorted(missing)}")
        yield from reader


def lane_activity(
    train_inputs: Iterable[Path],
    *,
    min_active_samples: int,
    min_active_ratio: float,
) -> list[LaneActivity]:
    """Count positive vehicle observations over training inputs only."""

    if min_active_samples < 1:
        raise ValueError("min_active_samples must be at least 1.")
    if not 0.0 <= min_active_ratio <= 1.0:
        raise ValueError("min_active_ratio must be between 0 and 1.")

    observed: dict[str, int] = defaultdict(int)
    positive: dict[str, int] = defaultdict(int)
    inputs = tuple(train_inputs)
    if not inputs:
        raise ValueError("at least one training input is required.")
    for path in inputs:
        for row in _read_rows(path):
            lane_id = row["lane_id"]
            observed[lane_id] += 1
            if float(row["vehicle_count"]) > 0.0:
                positive[lane_id] += 1

    rows = []
    for lane_id in sorted(observed):
        required_positive = max(min_active_samples, math.ceil(observed[lane_id] * min_active_ratio))
        count = positive[lane_id]
        rows.append(
            LaneActivity(
                lane_id=lane_id,
                observed_samples=observed[lane_id],
                positive_vehicle_samples=count,
                positive_ratio=count / observed[lane_id],
                keep=count >= required_positive,
            )
        )
    return rows


def filter_input(source: Path, destination: Path, active_lane_ids: set[str]) -> dict[str, int]:
    """Filter one dense snapshot CSV without changing its rows' field layout."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    input_rows = 0
    output_rows = 0
    with source.open("r", newline="", encoding="utf-8") as source_handle:
        reader = csv.DictReader(source_handle)
        if not reader.fieldnames:
            raise ValueError(f"{source} has no header row.")
        with destination.open("w", newline="", encoding="utf-8") as destination_handle:
            writer = csv.DictWriter(destination_handle, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                input_rows += 1
                if row.get("lane_id") not in active_lane_ids:
                    continue
                writer.writerow(row)
                output_rows += 1
    return {"input_rows": input_rows, "output_rows": output_rows}


def _unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)
    return tuple(unique)


def build_active_lane_dataset(
    *,
    train_inputs: Iterable[Path],
    inputs: Iterable[Path],
    output_dir: Path,
    min_active_samples: int,
    min_active_ratio: float,
) -> dict[str, object]:
    train_paths = _unique_paths(train_inputs)
    input_paths = _unique_paths(inputs)
    if not input_paths:
        raise ValueError("at least one input to filter is required.")

    activity = lane_activity(
        train_paths,
        min_active_samples=min_active_samples,
        min_active_ratio=min_active_ratio,
    )
    active_lane_ids = {row.lane_id for row in activity if row.keep}
    if not active_lane_ids:
        raise ValueError("active-lane thresholds removed every lane.")

    output_dir.mkdir(parents=True, exist_ok=True)
    stats_path = output_dir / "active_lane_stats.csv"
    with stats_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LaneActivity.__dataclass_fields__.keys())
        writer.writeheader()
        writer.writerows(asdict(row) for row in activity)

    filtered = []
    for source in input_paths:
        destination = output_dir / source.name
        counts = filter_input(source, destination, active_lane_ids)
        filtered.append({"source": str(source), "output": str(destination), **counts})

    metadata = {
        "train_inputs": [str(path) for path in train_paths],
        "inputs": [str(path) for path in input_paths],
        "min_active_samples": min_active_samples,
        "min_active_ratio": min_active_ratio,
        "total_lanes": len(activity),
        "active_lane_count": len(active_lane_ids),
        "active_lane_ids": sorted(active_lane_ids),
        "filtered_files": filtered,
    }
    (output_dir / "active_lane_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fit an active-lane mask on training SUMO CSVs and apply it to every split."
    )
    parser.add_argument("--train-input", type=Path, action="append", required=True)
    parser.add_argument(
        "--input",
        type=Path,
        action="append",
        default=None,
        help="CSV to filter; defaults to the training inputs when omitted.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-active-samples", type=int, default=3)
    parser.add_argument("--min-active-ratio", type=float, default=0.01)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    metadata = build_active_lane_dataset(
        train_inputs=args.train_input,
        inputs=args.input or args.train_input,
        output_dir=args.output_dir,
        min_active_samples=args.min_active_samples,
        min_active_ratio=args.min_active_ratio,
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
