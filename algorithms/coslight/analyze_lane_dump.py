#!/usr/bin/env python3
"""
Analyze lane state dump → CSV + summary.json + gate_report.txt

Usage:  python3 analyze_lane_dump.py runs/diag_lane_state/samples.jsonl
"""
from __future__ import annotations

import json, csv, sys, os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

FIELD_NAMES = [
    "A00_speed_ratio","A01_accel_norm","A02_dist_to_stop_ratio","A03_lane_idx_ratio",
    "A04_time_since_lc_norm","A05_movement_left","A06_movement_straight","A07_movement_right",
    "B00_left_veh_count","B01_left_occupancy","B02_left_mean_speed","B03_left_leader_gap",
    "B04_left_follower_gap","B05_left_downstream_storage",
    "B06_cur_veh_count","B07_cur_occupancy","B08_cur_mean_speed","B09_cur_leader_gap",
    "B10_cur_follower_gap","B11_cur_downstream_storage",
    "B12_right_veh_count","B13_right_occupancy","B14_right_mean_speed","B15_right_leader_gap",
    "B16_right_follower_gap","B17_right_downstream_storage",
    "C00_cur_serves_movement","C01_target_serves_movement","C02_same_phase",
    "C03_elapsed_ratio","C04_met_min_green","C05_is_transition","C06_downstream_green",
    "D00_flow_rate","D01_downstream_occ","D02_intersection_queue",
    "E00_hist_left","E01_hist_straight","E02_hist_right","E03_last_ok","E04_cooldown",
]
LANE_STATE_DIM = 41

def load_samples(jsonl_path: str) -> List[dict]:
    samples = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    return samples

def compute_stats(all_states: np.ndarray) -> dict:
    summary = {
        "sample_count": all_states.shape[0],
        "total_active_slots": all_states.shape[0],
    }
    if all_states.shape[0] == 0:
        return summary

    mins = all_states.min(axis=0)
    maxs = all_states.max(axis=0)
    means = all_states.mean(axis=0)
    stds = all_states.std(axis=0)
    percs = {}
    for p in [1, 50, 99]:
        percs[f"p{p:02d}"] = np.percentile(all_states, p, axis=0)
    zero_ratio = (all_states == 0).mean(axis=0)
    nan_count = int(np.isnan(all_states).any(axis=1).sum())
    inf_count = int(np.isinf(all_states).any(axis=1).sum())

    summary["nan_rows"] = nan_count
    summary["inf_rows"] = inf_count

    # Field-level stats
    fields_stats = {}
    for i, fn in enumerate(FIELD_NAMES):
        fields_stats[fn] = {
            "min": float(mins[i]),
            "max": float(maxs[i]),
            "mean": float(means[i]),
            "std": float(stds[i]),
            "p01": float(percs["p01"][i]),
            "p50": float(percs["p50"][i]),
            "p99": float(percs["p99"][i]),
            "zero_ratio": float(zero_ratio[i]),
        }
    summary["fields"] = fields_stats

    # High-zero fields (>90% zeros)
    high_zero = {fn: float(zero_ratio[i]) for i, fn in enumerate(FIELD_NAMES) if zero_ratio[i] > 0.9}
    summary["high_zero_ratio_fields"] = high_zero

    return summary

def compute_action_mask_stats(samples: List[dict]) -> dict:
    am_stats = {
        "only_keep_count": 0,
        "left_valid_count": 0,
        "right_valid_count": 0,
    }
    for s in samples:
        am = s.get("action_mask", [True, False, False])
        if am[1]:
            am_stats["left_valid_count"] += 1
        if am[2]:
            am_stats["right_valid_count"] += 1
        if sum(am) == 1:
            am_stats["only_keep_count"] += 1
    return am_stats

def write_csv(samples: List[dict], out_path: str):
    header = ["sim_time","slot_index","vehicle_id"] + FIELD_NAMES + ["keep_valid","left_valid","right_valid"]
    # Select: first 20 active, last 5, and diverse boundary samples
    selected = samples[:20] + samples[-5:] if len(samples) > 25 else samples

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for s in selected:
            row = {
                "sim_time": f"{s['sim_time']:.1f}",
                "slot_index": s["slot_index"],
                "vehicle_id": s.get("vehicle_id", ""),
            }
            for i, fn in enumerate(FIELD_NAMES):
                row[fn] = f"{s['states'][i]:.6f}" if i < len(s['states']) else "?"
            am = s.get("action_mask", [True, False, False])
            row["keep_valid"] = bool(am[0])
            row["left_valid"] = bool(am[1]) if len(am) > 1 else False
            row["right_valid"] = bool(am[2]) if len(am) > 2 else False
            writer.writerow(row)

def write_gate_report(all_states: np.ndarray, samples: List[dict], am_stats: dict, out_path: str):
    lines = ["=== Lane State Builder Gate Report ===\n"]
    ok = True

    # S1
    s1_ok = True
    s1_msg = []
    if all_states.ndim != 2:
        s1_msg.append(f"Wrong ndim: {all_states.ndim}")
        s1_ok = False
    if all_states.shape[1] != LANE_STATE_DIM:
        s1_msg.append(f"Wrong dim: {all_states.shape[1]} != {LANE_STATE_DIM}")
        s1_ok = False
    nan_c = int(np.isnan(all_states).any(axis=1).sum())
    inf_c = int(np.isinf(all_states).any(axis=1).sum())
    if nan_c:
        s1_msg.append(f"NaN rows: {nan_c}")
        s1_ok = False
    if inf_c:
        s1_msg.append(f"Inf rows: {inf_c}")
        s1_ok = False
    lines.append(f"  S1_data_contract: {'PASS' if s1_ok else 'FAIL'}")
    for m in s1_msg:
        lines.append(f"    - {m}")

    # S2
    if all_states.shape[0] > 0:
        mins, maxs = all_states.min(axis=0), all_states.max(axis=0)
        range_issues = []
        for i, fn in enumerate(FIELD_NAMES):
            lo, hi = mins[i], maxs[i]
            if "speed" in fn or "accel" in fn:
                if lo < -1.1 or hi > 1.1:
                    range_issues.append(f"{fn}: [{lo:.3f}, {hi:.3f}]")
            else:
                if lo < -0.05 or hi > 1.05:
                    range_issues.append(f"{fn}: [{lo:.3f}, {hi:.3f}]")
        s2_ok = len(range_issues) == 0
        lines.append(f"  S2_value_ranges: {'PASS' if s2_ok else 'FAIL'}  ({len(range_issues)} issues)")
        for m in range_issues[:15]:
            lines.append(f"    - {m}")
    else:
        lines.append("  S2_value_ranges: SKIP (no samples)")

    # S3
    all_keep_ok = True
    for s in samples:
        am = s.get("action_mask", [True, False, False])
        if not am[0]:
            all_keep_ok = False
            break
    lines.append(f"  S3_action_mask_keep_valid: {'PASS' if all_keep_ok else 'FAIL'}")
    lines.append(f"    only_keep={am_stats['only_keep_count']} left={am_stats['left_valid_count']} right={am_stats['right_valid_count']}")

    # Sample counts
    lines.append(f"\n  Samples: {all_states.shape[0]} active slots")

    # Verdict
    final = s1_ok and s2_ok and all_keep_ok
    if final and all_states.shape[0] < 20:
        lines.append("\n  ⚠️  SAMPLE COUNT LOW — insufficient for semantic audit (need >20)")
    lines.append(f"\n  VERDICT: {'LANE-STATE-V1-PASS' if final and all_states.shape[0] >= 20 else 'LANE-STATE-SHAPE-PASS / SEMANTIC-AUDIT-PENDING'}")

    with open(out_path, "w") as f:
        f.write("\n".join(lines))

def main():
    jsonl_path = sys.argv[1] if len(sys.argv) > 1 else "runs/diag_lane_state/samples.jsonl"
    if not os.path.exists(jsonl_path):
        print(f"File not found: {jsonl_path}")
        sys.exit(1)

    out_dir = os.path.dirname(jsonl_path)
    samples = load_samples(jsonl_path)
    if not samples:
        print("No samples found!")
        sys.exit(1)

    all_states = np.array([s["states"] for s in samples], dtype=np.float32)
    print(f"Loaded {len(samples)} active samples, shape={all_states.shape}")

    # Summary
    summary = compute_stats(all_states)
    summary["action_mask_stats"] = am_stats = compute_action_mask_stats(samples)

    # Write outputs
    csv_path = os.path.join(out_dir, "dump.csv")
    write_csv(samples, csv_path)
    print(f"CSV: {csv_path}")

    json_path = os.path.join(out_dir, "summary.json")
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"Summary: {json_path}")

    report_path = os.path.join(out_dir, "gate_report.txt")
    write_gate_report(all_states, samples, am_stats, report_path)
    print(f"Report: {report_path}")

    # Print quick stats
    print(f"\nActive slots: {len(samples)}")
    print(f"State range: [{all_states.min():.3f}, {all_states.max():.3f}]")
    print(f"NaN: {summary['nan_rows']}, Inf: {summary['inf_rows']}")
    print(f"Only-keep: {am_stats['only_keep_count']}, Left-valid: {am_stats['left_valid_count']}, Right-valid: {am_stats['right_valid_count']}")
    high_z = summary.get("high_zero_ratio_fields", {})
    if high_z:
        print(f"High-zero fields (>90%): {list(high_z.keys())}")

if __name__ == "__main__":
    main()
