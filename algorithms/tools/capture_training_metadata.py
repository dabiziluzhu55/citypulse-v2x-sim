"""Capture Protocol 2.0 initialize metadata for the xiongan20 network.

Runs a short SUMO session with algorithm_module=\"tools.metadata_recorder\"
and writes the initialize payload (metadata.intersections) to JSON.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("SUMO_HOME", "/usr/share/sumo")
sumo_bin = str(Path(os.environ["SUMO_HOME"]) / "bin")
path_entries = [
    entry
    for entry in os.environ.get("PATH", "").split(os.pathsep)
    if entry and entry != sumo_bin
]
os.environ["PATH"] = os.pathsep.join([*path_entries, sumo_bin])

from simulation.sumo.engine.session import SimulationConfig, SimulationManager  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=10)
    parser.add_argument(
        "--intersection-ids",
        nargs="+",
        default=[f"demo_{i}" for i in range(1, 21)],
    )
    args = parser.parse_args(argv)

    os.environ["IPPO_METADATA_OUTPUT"] = str(args.output)
    config = SimulationConfig(
        intersection_ids=tuple(args.intersection_ids),
        period="off_peak",
        duration_seconds=args.duration,
        control_mode="algorithm",
        algorithm_transport="local",
        algorithm_module="tools.metadata_recorder",
        decision_interval=5.0,
        minimum_green=5.0,
        seed=1042,
        step_length=0.05,
    )
    manager = SimulationManager()
    session_id = manager.start(config)
    snapshot = manager.wait(session_id, timeout=max(300.0, args.duration * 3.0))
    if snapshot.state != "COMPLETED":
        raise RuntimeError(f"SUMO state={snapshot.state} error={snapshot.error or ''}")
    if not args.output.is_file():
        raise RuntimeError(f"Metadata was not captured: {args.output}")
    print(f"captured metadata -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
