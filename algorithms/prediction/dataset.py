"""Utilities for turning exported SUMO lane snapshots into time series."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LaneSeries:
    """A dense time-by-lane table for one numeric target."""

    target: str
    times: tuple[float, ...]
    official_times: tuple[str, ...]
    lanes: tuple[str, ...]
    values: tuple[tuple[float, ...], ...]

    @property
    def interval_seconds(self) -> float:
        if len(self.times) < 2:
            return 0.0
        diffs = [
            round(self.times[index] - self.times[index - 1], 6)
            for index in range(1, len(self.times))
        ]
        return max(set(diffs), key=diffs.count)


def _to_float(value: str, field: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"field {field!r} contains non-numeric value {value!r}") from exc


def load_lane_series(path: str | Path, target: str) -> LaneSeries:
    """Load one target from export_snapshots lane CSV.

    The exporter writes one row per (snapshot, lane). This function returns a
    dense matrix ordered by elapsed time and lane id.
    """

    csv_path = Path(path)
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        raise ValueError(f"no rows found in {csv_path}")
    if target not in rows[0]:
        available = ", ".join(rows[0].keys())
        raise ValueError(f"target {target!r} not found; available fields: {available}")

    times = tuple(sorted({_to_float(row["elapsed_seconds"], "elapsed_seconds") for row in rows}))
    lanes = tuple(sorted({row["lane_id"] for row in rows}))
    time_index = {time: index for index, time in enumerate(times)}
    lane_index = {lane: index for index, lane in enumerate(lanes)}

    values = [[0.0 for _ in lanes] for _ in times]
    official_times = ["" for _ in times]
    seen: set[tuple[int, int]] = set()

    for row in rows:
        t_index = time_index[_to_float(row["elapsed_seconds"], "elapsed_seconds")]
        l_index = lane_index[row["lane_id"]]
        key = (t_index, l_index)
        if key in seen:
            raise ValueError(
                f"duplicate row for time={row['elapsed_seconds']} lane={row['lane_id']}"
            )
        values[t_index][l_index] = _to_float(row[target], target)
        official_times[t_index] = row.get("official_time", "")
        seen.add(key)

    expected = len(times) * len(lanes)
    if len(seen) != expected:
        raise ValueError(
            f"CSV is not dense: found {len(seen)} cells, expected {expected} "
            f"({len(times)} times x {len(lanes)} lanes)"
        )

    return LaneSeries(
        target=target,
        times=times,
        official_times=tuple(official_times),
        lanes=lanes,
        values=tuple(tuple(row) for row in values),
    )
