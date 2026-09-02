"""Summarize the dev four-line comparison (VRC Full / NoCollab / senior / Shuffle).

M = pooled all_waiting_total_s / departed_count over the dev seeds
(66501-66508).  The Shuffle line is mechanism evidence (D-2026-08-08-17):

  * Full == Shuffle (exact per-seed)  => collab pathway is content-invariant
    (constant bias; v6 failure mode persists);
  * Full != Shuffle                   => message content now changes decisions.

Reports pooled M, per-seed M, paired win rates, and the Full-vs-Shuffle
mechanism flags.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from algorithms.evaluation.vrc_v8_protocol import pooled_m as protocol_pooled_m


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
    """Historical descriptive helper; preserve its pre-v8 input contract."""

    total_wait = 0.0
    total_dep = 0.0
    for run in raw["runs"]:
        metrics = run["official_metrics"]
        total_wait += float(metrics["all_waiting_total_s"])
        total_dep += float(metrics["departed_count"])
    return total_wait / total_dep if total_dep > 0 else float("nan")


def strict_protocol_pooled_m(raw: dict) -> float:
    """Explicit adapter to the fail-closed v8 protocol authority."""

    return protocol_pooled_m(raw["runs"])


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
    ap.add_argument("--shuffle", type=Path, required=True)
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
    sh = load(args.shuffle)
    f_m, n_m, s_m, h_m = (
        per_seed_m(full),
        per_seed_m(nc),
        per_seed_m(sr),
        per_seed_m(sh),
    )

    pooled = {
        "full": pooled_m(full),
        "nocollab": pooled_m(nc),
        "senior": pooled_m(sr),
        "shuffle": pooled_m(sh),
    }
    report = {
        "schema": "dev_four_line_summary_v1",
        "seeds": seeds,
        "pooled_m": pooled,
        "per_seed_m": {
            str(s): {
                "full": f_m[s],
                "nocollab": n_m[s],
                "senior": s_m[s],
                "shuffle": h_m[s],
            }
            for s in seeds
        },
        "win_rates": {
            "full_vs_senior": dict(zip(("wins", "total"), win_rate(f_m, s_m, seeds))),
            "full_vs_nocollab": dict(zip(("wins", "total"), win_rate(f_m, n_m, seeds))),
            "full_vs_shuffle": dict(zip(("wins", "total"), win_rate(f_m, h_m, seeds))),
            "nocollab_vs_senior": dict(
                zip(("wins", "total"), win_rate(n_m, s_m, seeds))
            ),
        },
        "mechanism": {
            "full_shuffle_exact_equal_per_seed": all(
                f_m[s] == h_m[s] for s in seeds
            ),
            "full_shuffle_pooled_abs_diff": abs(pooled["full"] - pooled["shuffle"]),
            "full_shuffle_max_per_seed_abs_diff": float(
                max(abs(f_m[s] - h_m[s]) for s in seeds)
            ),
        },
    }
    if args.output:
        Path(args.output).write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
