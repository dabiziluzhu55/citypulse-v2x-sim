# algorithms/ippo/regress_v8_subset.py
"""Three-level strict regression for the v8 20-intersection generalist.

L1 state arrays: StateBuilder.build() for a deterministic synthetic frame.
L2 network forward: IPPONetwork logits+value on those states/phase features.
L3 SUMO decision trace: full session via tools.traced_ippo wrapper.

--mode capture writes golden artifacts with the CURRENT controller (must run
before M1 changes the controller).  --mode verify compares the current code
against the golden artifacts.  L1/L2 compare exactly (allclose rtol=1e-6,
atol=1e-7); L3 compares decision sequences exactly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import torch

REPO_ROOT = Path(__file__).resolve().parents[2]
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

from traffic_control.ippo.controller import (  # noqa: E402
    StateBuilder,
    load_checkpoint_metadata,
)
from traffic_control.ippo.model import IPPONetwork  # noqa: E402
from simulation.sumo.session import SimulationConfig, SimulationManager  # noqa: E402


GOLDEN_DIR = REPO_ROOT / "algorithms" / "ippo" / "regression_golden"
TRACE_DIR = REPO_ROOT / "algorithms" / "ippo" / "regression_tmp"  # gitignored
L1_PATH = GOLDEN_DIR / "states_xiongan20.npz"
L2_PATH = GOLDEN_DIR / "forward_xiongan20.npz"
L3_PATH = GOLDEN_DIR / "trace_xiongan20_seed1042.json"


def _synthetic_frame(metadata: dict) -> dict:
    """Deterministic frame: per-lane values derived from lane id hash."""
    frame = {"intersections": {}}
    for iid, item in metadata["intersections"].items():
        lanes = {}
        for lane_id in item.get("incoming_lanes", ()) + item.get("outgoing_lanes", ()):
            seed = sum(ord(ch) for ch in str(lane_id))
            lanes[lane_id] = {
                "vehicle_count": seed % 9,
                "halting_count": seed % 5,
                "waiting_time": float(seed % 7) * 4.0,
                "mean_speed": 8.0 + (seed % 5),
                "occupancy": float(seed % 11) * 3.0,
            }
        frame["intersections"][iid] = {
            "current_phase": item["phase_order"][0],
            "stage_elapsed": 12.5,
            "stage": "GREEN",
            "lanes": lanes,
        }
    return frame


def _capture_l1(metadata: dict) -> dict[str, np.ndarray]:
    builder = StateBuilder(metadata)
    frame = _synthetic_frame(metadata)
    return builder.get_all_states(frame)


def _capture_l2(metadata: dict, checkpoint: dict) -> dict[str, np.ndarray]:
    builder = StateBuilder(metadata)
    frame = _synthetic_frame(metadata)
    states = builder.get_all_states(frame)
    model = IPPONetwork(int(checkpoint["obs_dim"]), int(checkpoint["act_dim"]))
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    outputs: dict[str, np.ndarray] = {}
    with torch.no_grad():
        for iid in builder.intersection_ids:
            item = metadata["intersections"][iid]
            phase_order = builder.get_phase_order(iid)
            phase_features = builder.build_phase_features(
                iid,
                frame["intersections"][iid],
                simulation_time=100.0,
                last_service_times={phase: 0.0 for phase in phase_order},
                vehicles={},
                demand_horizon_seconds=15.0,
            )
            mask, _ = builder.build_action_mask(
                iid,
                frame["intersections"][iid],
                max_green_factor=float(checkpoint["max_green_factor"]),
            )
            padded_features = np.zeros(
                (int(checkpoint["act_dim"]), phase_features.shape[1]),
                dtype=np.float32,
            )
            padded_features[: len(phase_order)] = phase_features
            padded_mask = np.zeros(int(checkpoint["act_dim"]), dtype=np.bool_)
            padded_mask[: len(phase_order)] = mask
            logits, value = model(
                torch.from_numpy(states[iid]).unsqueeze(0).float(),
                torch.from_numpy(padded_features).unsqueeze(0).float(),
            )
            outputs[iid] = np.concatenate(
                [logits.numpy().ravel(), np.asarray([value.item()], dtype=np.float32)]
            )
    return outputs


def _capture_l3(seed: int, duration: int) -> list[dict]:
    trace_path = TRACE_DIR / f"trace_{seed}.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["IPPO_TRACE_OUTPUT"] = str(trace_path)
    config = SimulationConfig(
        intersection_ids=tuple(f"demo_{i}" for i in range(1, 21)),
        period="off_peak",
        duration_seconds=duration,
        control_mode="algorithm",
        algorithm_transport="local",
        algorithm_module="tools.traced_ippo",
        decision_interval=5.0,
        minimum_green=5.0,
        seed=seed,
        step_length=0.05,
    )
    manager = SimulationManager()
    session_id = manager.start(config)
    snapshot = manager.wait(session_id, timeout=max(600.0, duration * 3.0))
    if snapshot.state != "COMPLETED":
        raise RuntimeError(f"SUMO state={snapshot.state} error={snapshot.error or ''}")
    if not trace_path.is_file():
        raise RuntimeError(f"trace not captured: {trace_path}")
    return json.loads(trace_path.read_text(encoding="utf-8"))


def _compare_npz(golden: np.lib.npyio.NpzFile, live: dict[str, np.ndarray], label: str) -> None:
    assert set(golden.files) == set(live), f"{label}: key set mismatch"
    for key in golden.files:
        if not np.allclose(golden[key], live[key], rtol=1e-6, atol=1e-7):
            raise AssertionError(f"{label}: {key} differs")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("capture", "verify"), required=True)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=1042)
    parser.add_argument("--duration", type=int, default=60)
    args = parser.parse_args(argv)

    checkpoint_path = args.checkpoint or (
        REPO_ROOT / "traffic_control" / "ippo" / "models" / "ippo_v8_20tls_ep160.pt"
    )
    metadata = json.loads(
        (GOLDEN_DIR / "metadata_xiongan20.json").read_text(encoding="utf-8")
    )
    checkpoint = load_checkpoint_metadata(checkpoint_path)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)

    l1 = _capture_l1(metadata)
    l2 = _capture_l2(metadata, checkpoint)
    l3 = _capture_l3(args.seed, args.duration)

    if args.mode == "capture":
        np.savez(L1_PATH, **l1)
        np.savez(L2_PATH, **l2)
        (L3_PATH).write_text(json.dumps(l3, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"captured golden: L1={L1_PATH} L2={L2_PATH} L3={L3_PATH} (decisions={len(l3)})")
        return 0

    _compare_npz(np.load(L1_PATH), l1, "L1")
    _compare_npz(np.load(L2_PATH), l2, "L2")
    golden_l3 = json.loads(L3_PATH.read_text(encoding="utf-8"))
    if golden_l3 != l3:
        raise AssertionError(
            f"L3 decision trace differs: golden={len(golden_l3)} live={len(l3)}"
        )
    print(f"verify passed: L1/L2/L3 strict equal (decisions={len(l3)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
