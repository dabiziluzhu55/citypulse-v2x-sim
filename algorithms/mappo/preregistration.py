"""Preregistration manifest builder/validator (fixed YAML structure, no pyyaml)."""

from __future__ import annotations

import re
from typing import Mapping, Sequence

FINAL_SEEDS = (1042, 1142, 1242, 1342, 1442, 1542, 1642, 1742, 1842, 1942)
DEV_SEEDS = tuple(range(66501, 66509))
TRAIN_SEEDS_32 = tuple(range(95501, 95533))
TRAIN_SEEDS_CONT = tuple(range(95533, 95661))


def _fmt_seq(values: Sequence[int]) -> str:
    return ", ".join(str(v) for v in values)


def build_manifest_yaml(
    *,
    ippo_baseline_sha256: str,
    adjacency_sha256: Mapping[str, str],
    shared_init_sha256: str,
    net_xml_sha256: str,
    vanilla_ckpt_sha256: str,
    ippo_ckpt_sha256: str,
    arrival_delta_min: float,
    ep32_gate: Mapping[str, object],
) -> str:
    return f"""mappo_v2_preregistration:
  statistical_tests:
    primary: wilcoxon_one_sided_less(delta_waiting)  # H1: delta_waiting < 0
    secondary: wilcoxon_one_sided_less([-d for d in delta_arrival])  # H1: delta_arrival > 0
    win_rate_primary: "mean(delta_waiting < 0) >= 7/10"
    win_rate_secondary: "mean(delta_arrival > 0) >= 7/10"
    effect_waiting_s: 0.5
    effect_arrival: "max(2.0, 1.5 * sigma_ippo_arrival_10)"
    arrival_delta_min: {arrival_delta_min}
    non_inferiority: "UCB95(paired_diff) <= delta_ni (travel +1.0s, queue +0.005veh, fuel +0.05L/100km)"
    ucb95: "np.percentile(bootstrap_paired_mean_diffs(diffs, b=10000, seed=20260804), 95.0)"
    safety: "aggregate events/exposure, cluster bootstrap; R_IPPO=0 -> absolute event gate"
    decision_latency: "p95(MAPPO) <= p95(IPPO) * 1.25"
  seeds:
    final: [{_fmt_seq(FINAL_SEEDS)}]
    dev: [{_fmt_seq(DEV_SEEDS)}]
    train_32ep: [{_fmt_seq(TRAIN_SEEDS_32)}]
    train_continue: [{_fmt_seq(TRAIN_SEEDS_CONT)}]
  arms:
    m1_0: {{local: 0.0, neighbor: 0.0, team: 1.0}}
    m1_a: {{local: 0.95, neighbor: 0.0, team: 0.05}}
    m1_b: {{local: 0.70, neighbor: 0.25, team: 0.05}}
  ep32_gate:
    waiting_delta_vs_m10_mean_max: 0.0
    arrival_delta_vs_m10_mean_min: 0.0
    waiting_vs_vanilla_anchor_ucb95_max: 0.5
    target_duplicate_rate_max: 0.05
    win_rate_vs_m10_min: 5/8
    nan_inf: 0
  select_arm:
    both_pass: "m1_b only if waiting_improvement>=0.25s AND head_to_head>=5/8 AND arrival not worse"
    checkpoint: "strict ep160 checkpoint"
  artifacts:
    ippo_baseline_sha256: {ippo_baseline_sha256}
    adjacency_directed_sha256: {adjacency_sha256.get("directed", "")}
    adjacency_symmetric_sha256: {adjacency_sha256.get("symmetric", "")}
    shared_init_sha256: {shared_init_sha256}
    net_xml_sha256: {net_xml_sha256}
    vanilla_ckpt_sha256: {vanilla_ckpt_sha256}
    ippo_ckpt_sha256: {ippo_ckpt_sha256}
  invalid_run_policy:
    evaluation: "single seed rerun allowed with log; no selective rerun by performance"
    training: "worker drop -> whole arm invalid"
    smoke: "fix and rerun"
"""


def validate_manifest(yaml_text: str) -> None:
    required = (
        "statistical_tests",
        "final:",
        "dev:",
        "m1_0:",
        "m1_a:",
        "m1_b:",
        "ep32_gate:",
        "ippo_baseline_sha256:",
        "shared_init_sha256:",
        "net_xml_sha256:",
        "vanilla_ckpt_sha256:",
        "ippo_ckpt_sha256:",
    )
    for token in required:
        if token not in yaml_text:
            raise ValueError(f"manifest missing required section: {token}")
    artifact_lines = [
        ln
        for ln in yaml_text.splitlines()
        if "sha256:" in ln and "test" not in ln
    ]
    for ln in artifact_lines:
        value = ln.split("sha256:")[1].strip()
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError(f"artifact hash missing or malformed: {ln.strip()}")
