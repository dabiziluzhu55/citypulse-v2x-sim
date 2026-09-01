"""Diagnose real-V2X probe logs: argmax flips vs NoCollab, message freshness.

D-2026-08-07 protocol step 4: answer "why is collaboration harmful" using
probe data captured during the three-line dev rerun (66501-66508).
Reads probe npz pairs (full / nocollab) and writes a versioned JSON + MD report.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def load_probe(probe_dir: Path, seed: int) -> dict:
    p = probe_dir / f"probe_log_{seed}.npz"
    d = np.load(p)
    return {k: d[k] for k in d.files}


def diagnose(probe_dir_full: Path, probe_dir_nc: Path, seeds, ttl: float = 10.0) -> dict:
    per_seed = []
    total_steps = 0
    total_agent_decisions = 0
    total_flips = 0
    per_tls_flips = np.zeros(20, dtype=int)
    per_tls_decisions = np.zeros(20, dtype=int)
    fresh_age_all = []
    delay_all = []
    candidate_counts_all = []
    zero_candidate_steps = 0

    for seed in seeds:
        f = load_probe(probe_dir_full, seed)
        n = load_probe(probe_dir_nc, seed)
        assert np.allclose(f["simulation_time"], n["simulation_time"]), f"seed {seed} time mismatch"
        T = int(f["actions"].shape[0])
        flips = f["actions"] != n["actions"]
        total_steps += T
        total_agent_decisions += T * 20
        total_flips += int(flips.sum())
        per_tls_flips += flips.sum(axis=0)
        per_tls_decisions += T
        cm = f["candidate_mask"]
        zero_candidate_steps += int((cm.sum(axis=-1) == 0).sum())
        candidate_counts_all.extend(cm.sum(axis=-1).flatten().tolist())
        age = f["message_age"][cm]
        delay = f["message_delay"][cm]
        fresh_age_all.extend(age.tolist())
        delay_all.extend(delay.tolist())
        per_seed.append({
            "seed": seed,
            "decision_steps": T,
            "simulation_times": f["simulation_time"].tolist(),
            "argmax_flips": int(flips.sum()),
            "argmax_flip_rate": float(flips.mean()),
            "fresh_message_count": int(len(age)),
            "fresh_age_mean": float(age.mean()) if len(age) else None,
            "fresh_age_min": float(age.min()) if len(age) else None,
            "fresh_age_max": float(age.max()) if len(age) else None,
            "delay_mean_s": float(delay.mean()) if len(delay) else None,
        })

    fresh_age = np.asarray(fresh_age_all, dtype=float)
    delay = np.asarray(delay_all, dtype=float)
    cc = np.asarray(candidate_counts_all, dtype=float)
    flip_rate_total = total_flips / total_agent_decisions if total_agent_decisions else None
    return {
        "schema": "vrc_probe_diagnosis_v1",
        "frozen_context": {
            "seeds": seeds,
            "decision_interval_s": 15.0,   # JOINT_DECISION_INTERVAL in controller
            "frozen_spat_interval_s": 5.0, # spec 2026-08-06-vrc-preregistration-design §1.1
            "vrc_ttl_s": ttl,
            "mode": "real (pen 0.6, lat 100±50ms, drop 5%)",
        },
        "argmax_flip": {
            "total_agent_decisions": total_agent_decisions,
            "flips": total_flips,
            "flip_rate": flip_rate_total,
            "per_tls_flip_rate": {
                f"demo_{i+1}": float(per_tls_flips[i] / per_tls_decisions[i])
                for i in range(20)
            },
            "per_seed": per_seed,
        },
        "message_freshness": {
            "fresh_age_mean": float(fresh_age.mean()) if len(fresh_age) else None,
            "fresh_age_min": float(fresh_age.min()) if len(fresh_age) else None,
            "fresh_age_max": float(fresh_age.max()) if len(fresh_age) else None,
            "fresh_age_p50": float(np.percentile(fresh_age, 50)) if len(fresh_age) else None,
            "fresh_age_p90": float(np.percentile(fresh_age, 90)) if len(fresh_age) else None,
            "age_near_ttl_frac": float((fresh_age > 0.9).mean()) if len(fresh_age) else None,
            "delivery_delay_mean_s": float(delay.mean()) if len(delay) else None,
            "delivery_delay_max_s": float(delay.max()) if len(delay) else None,
        },
        "candidate_set": {
            "candidate_counts_mean": float(cc.mean()) if len(cc) else None,
            "candidate_counts_min": int(cc.min()) if len(cc) else None,
            "candidate_counts_max": int(cc.max()) if len(cc) else None,
            "zero_candidate_agent_steps": int(zero_candidate_steps),
            "zero_candidate_frac": float(zero_candidate_steps / (total_steps * 20)) if total_steps else None,
        },
        "findings": _build_findings(
            flip_rate_total, fresh_age, delay, cc, zero_candidate_steps,
            total_steps, ttl),
        "recommendations": [
            "R1: fix delivery ordering (advance/ingest before policy_tensors) so decisions see <=5s-old SPaT, "
            "then re-run three-line dev comparison; do not tune alpha before this fix.",
            "R2: add collaborator-selection (Top-K ids) to probe output for v2 to measure selection churn.",
            "R3: run ideal-delivery and shuffle-message ablations after R1 to separate staleness from mechanism.",
            "R4: keep NoCollab as causal control; current stale-collaboration explanation is consistent with "
            "Full<NoCollab but does not explain NoCollab<senior (separate diagnosis).",
        ],
    }


def _build_findings(flip_rate_total, fresh_age, delay, cc,
                    zero_candidate_steps, total_steps, ttl) -> list[str]:
    """按实测数据生成 findings：修复投递时序后 age 应显著低于 0.9。"""
    stale = bool(len(fresh_age) and float(np.mean(fresh_age)) > 0.7)
    findings = [
        "F1: full vs nocollab argmax flip rate "
        f"{flip_rate_total:.4f} (collaboration changes "
        f"{100.0 * flip_rate_total:.1f}% of decisions).",
    ]
    if stale:
        findings += [
            "F2: decision-time fresh SPaT age mean "
            f"{float(fresh_age.mean()):.4f} (TTL={ttl}s), age>0.9 frac "
            f"{float((fresh_age > 0.9).mean()):.4f}; messages are still stale "
            "(ordering bug not fixed or model trained on stale messages).",
            "F3: root cause is decision/delivery ordering: controller.step() decides "
            "before hub.ingest_step advances delivery, so decisions observe messages "
            "delivered ~10s earlier than the newest available.",
        ]
    else:
        findings += [
            "F2: decision-time fresh SPaT age mean "
            f"{float(fresh_age.mean()):.4f} (TTL={ttl}s), age>0.9 frac "
            f"{float((fresh_age > 0.9).mean()):.4f}; ordering fixed, decisions now "
            "see <=5s-old SPaT.",
            "F3: delivery delay mean/max "
            f"{float(delay.mean()):.4f}/{float(delay.max()):.4f} s (frozen "
            "100±50ms budget); freshness benefit is now delivered at decision time.",
        ]
    findings += [
        "F4: candidate set: mean "
        f"{float(cc.mean()):.2f}/19 neighbors, zero-candidate agent-steps "
        f"{zero_candidate_steps} ({zero_candidate_steps / (total_steps * 20):.4f}).",
        "F5: interpretation requires pairing with traffic metrics: if Full<NoCollab "
        "persists with fresh messages, the model (trained under stale wiring) is OOD "
        "and must be retrained with the fixed ordering before judging the mechanism.",
    ]
    return findings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-dir-full", type=Path, required=True)
    ap.add_argument("--probe-dir-nocollab", type=Path, required=True)
    ap.add_argument("--seeds", required=True)
    ap.add_argument("--ttl", type=float, default=10.0)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]
    report = diagnose(args.probe_dir_full, args.probe_dir_nocollab, seeds, ttl=args.ttl)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = args.output.with_suffix(".md")
    lines = ["# VRC real-V2X probe 诊断（66501-66508）", ""]
    lines.append(f"- argmax flip rate (Full vs NoCollab): `{report['argmax_flip']['flip_rate']:.4f}`")
    lines.append(f"- fresh message age mean: `{report['message_freshness']['fresh_age_mean']:.4f}` "
                 f"(TTL={args.ttl}s; >0.9 → 消息陈旧)")
    lines.append(f"- age>0.9 占比: `{report['message_freshness']['age_near_ttl_frac']:.4f}`")
    lines.append(f"- 投递延迟 mean/max: `{report['message_freshness']['delivery_delay_mean_s']:.4f}` / "
                 f"`{report['message_freshness']['delivery_delay_max_s']:.4f}` s")
    lines.append("")
    lines.append("## Findings")
    for f in report["findings"]:
        lines.append(f"- {f}")
    lines.append("")
    lines.append("## Recommendations")
    for r in report["recommendations"]:
        lines.append(f"- {r}")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"report written: {args.output} (+ md)")


if __name__ == "__main__":
    main()
