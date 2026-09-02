"""Summarize the dev three-line comparison (VRC Full / NoCollab / senior).

M = pooled all_waiting_total_s / departed_count over the 8 dev seeds
(66501-66508).  Reports per-seed M, pooled M, and paired win rates for
Full vs senior and Full vs NoCollab (lower M is better).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def per_seed_m(raw: dict) -> dict:
    out = {}
    for run in raw["runs"]:
        m = run["official_metrics"]
        out[int(run["seed"])] = (
            float(m["all_waiting_total_s"]) / float(m["departed_count"])
            if float(m["departed_count"]) > 0 else float("nan")
        )
    return out


def pooled_m(raw: dict) -> float:
    total_wait = 0.0
    total_dep = 0.0
    for run in raw["runs"]:
        m = run["official_metrics"]
        total_wait += float(m["all_waiting_total_s"])
        total_dep += float(m["departed_count"])
    return total_wait / total_dep if total_dep > 0 else float("nan")


def win_rate(a: dict, b: dict, seeds) -> tuple[int, int]:
    wins = 0
    for s in seeds:
        if a[s] < b[s]:
            wins += 1
    return wins, len(seeds)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", type=Path, required=True)
    ap.add_argument("--nocollab", type=Path, required=True)
    ap.add_argument("--senior", type=Path, required=True)
    ap.add_argument("--seeds", default="66501-66508")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    if "-" in args.seeds:
        lo, hi = (int(x) for x in args.seeds.split("-"))
        seeds = list(range(lo, hi + 1))
    else:
        seeds = [int(x) for x in args.seeds.split(",")]
    full = load(args.full)
    nc = load(args.nocollab)
    sr = load(args.senior)
    f_m, n_m, s_m = per_seed_m(full), per_seed_m(nc), per_seed_m(sr)
    report = {
        "schema": "dev_three_line_summary_v1",
        "seeds": seeds,
        "pooled_m": {
            "full": pooled_m(full),
            "nocollab": pooled_m(nc),
            "senior": pooled_m(sr),
        },
        "per_seed_m": {
            str(s): {"full": f_m[s], "nocollab": n_m[s], "senior": s_m[s]}
            for s in seeds
        },
        "win_rates": {
            "full_vs_senior": {"wins": win_rate(f_m, s_m, seeds)[0], "total": len(seeds)},
            "full_vs_nocollab": {"wins": win_rate(f_m, n_m, seeds)[0], "total": len(seeds)},
            "nocollab_vs_senior": {"wins": win_rate(n_m, s_m, seeds)[0], "total": len(seeds)},
        },
        "ratios": {
            "full_vs_senior_median": float(np.median([f_m[s] / s_m[s] for s in seeds])),
            "full_vs_nocollab_median": float(np.median([f_m[s] / n_m[s] for s in seeds])),
            "nocollab_vs_senior_median": float(np.median([n_m[s] / s_m[s] for s in seeds])),
        },
    }
    if args.output:
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
