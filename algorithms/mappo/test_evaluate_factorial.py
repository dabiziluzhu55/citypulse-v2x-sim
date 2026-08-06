from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from algorithms.mappo.checkpoint import state_dict_digest
from algorithms.mappo.evaluate_factorial import (
    aggregate_factorial,
    hierarchical_paired_bootstrap,
    validate_factorial_inputs,
)
from algorithms.mappo.experiment_v2 import EVALUATION_SEEDS, ExperimentJob, build_jobs


def _metric_values(arm: str, seed: int) -> dict[str, float]:
    jitter = float(seed - EVALUATION_SEEDS[0])
    values = {
        "throughput_veh_per_h": 1000.0 + jitter,
        "completed_waiting_mean_s": 50.0 + jitter,
        "completion_rate": 0.80,
        "all_waiting_total_s": 1000.0 + jitter,
        "all_time_loss_total_s": 500.0 + jitter,
    }
    if arm == "S-G":
        values["throughput_veh_per_h"] += 5.0
        values["completed_waiting_mean_s"] -= 1.0
    elif arm == "R-G":
        values["throughput_veh_per_h"] += 100.0
        values["completed_waiting_mean_s"] -= 10.0
        values["completion_rate"] += 0.05
        values["all_waiting_total_s"] -= 100.0
        values["all_time_loss_total_s"] -= 50.0
    return values


def _write_cell(root: Path, job: ExperimentJob) -> None:
    directory = job.output_directory
    directory.mkdir(parents=True, exist_ok=True)
    policy_state = {
        "synthetic.weight": torch.tensor(
            [
                float(job.lineage.actor_init_seed),
                float(job.lineage.critic_init_seed),
                float(("S-L", "S-G", "R-L", "R-G").index(job.arm.name)),
            ]
        )
    }
    final_policy_digest = state_dict_digest(policy_state)
    torch.save(
        {
            "policy_state_dict": policy_state,
            "actor_optimizer_state_dict": {"state": {0: {"step": 1}}},
            "critic_optimizer_state_dict": {"state": {0: {"step": 1}}},
        },
        job.checkpoint_path,
    )
    batches = []
    for batch_index in range(4):
        seed_start = job.lineage.training_seed_start + batch_index * 8
        batches.append(
            {
                "batch_number": batch_index + 1,
                "seeds": list(range(seed_start, seed_start + 8)),
                "sampled_policy_generation": batch_index,
                "policy_generation": batch_index + 1,
                "policy_digest": (
                    final_policy_digest
                    if batch_index == 3
                    else f"digest-{job.cell_id}-{batch_index + 1}"
                ),
                "worker_success_count": 8,
                "worker_expected_count": 8,
                "unique_joint_state_count": 155 + batch_index,
                "unique_critic_input_count": 3000,
                "finite": True,
            }
        )
    diagnostics = {
        "status": "complete",
        "schema": "mappo_training_diagnostics_v1",
        "frozen_config": {
            "model_version": job.arm.model_version,
            "actor_variant": job.arm.actor_variant,
            "actor_init_seed": job.lineage.actor_init_seed,
            "critic_init_seed": job.lineage.critic_init_seed,
            "residual_init_seed": (
                job.lineage.residual_init_seed
                if job.arm.actor_variant == "residual"
                else None
            ),
            "training_seed_start": job.lineage.training_seed_start,
            "training_seed_end": job.lineage.training_seed_end,
            "periods": ["off_peak"],
            "workers": 8,
            "episode_duration_s": 300,
            "config": {
                "intersection_ids": [f"demo_{index}" for index in range(1, 21)],
                "critic_scope": job.arm.critic_scope,
            },
        },
        "batches": batches,
        "completed_episodes": 32,
        "final_policy_generation": 4,
        "final_policy_digest": final_policy_digest,
        "final_checkpoint": str(job.checkpoint_path),
    }
    job.diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")

    metadata = {
        "model_version": job.arm.model_version,
        "actor_variant": job.arm.actor_variant,
        "critic_scope": job.arm.critic_scope,
        "episode": 32,
        "policy_generation": 4,
        "intersection_ids": [f"demo_{index}" for index in range(1, 21)],
        "actor_init_seed": job.lineage.actor_init_seed,
        "critic_init_seed": job.lineage.critic_init_seed,
        "residual_init_seed": (
            job.lineage.residual_init_seed
            if job.arm.actor_variant == "residual"
            else None
        ),
        "training_seed_start": job.lineage.training_seed_start,
        "training_seed_end": job.lineage.training_seed_end,
        "training_periods": ["off_peak"],
        "training_workers": 8,
        "episode_duration_s": 300.0,
    }
    runs = []
    for seed in EVALUATION_SEEDS:
        metrics = _metric_values(job.arm.name, seed)
        runs.append(
            {
                "status": "complete",
                "seed": seed,
                "completed_waiting_mean_s": metrics[
                    "completed_waiting_mean_s"
                ],
                "completion_rate": metrics["completion_rate"],
                "all_waiting_total_s": metrics["all_waiting_total_s"],
                "all_time_loss_total_s": metrics["all_time_loss_total_s"],
                "official_metrics": {
                    "throughput_veh_per_h": metrics["throughput_veh_per_h"]
                },
                "missing_official_metrics": [],
            }
        )
    evaluation = {
        "label": job.cell_id,
        "checkpoint": str(job.checkpoint_path),
        "checkpoint_metadata": metadata,
        "config": {
            "seeds": list(EVALUATION_SEEDS),
            "duration_s": 300,
            "intersections": [f"demo_{index}" for index in range(1, 21)],
            "period": "off_peak",
            "deterministic": True,
            "action_interval_s": 15.0,
            "parallel_workers": 8,
        },
        "summary": {
            "episodes_requested": 8,
            "episodes_complete": 8,
            "episodes_failed": 0,
            "action_diagnostics": {
                "episodes_available": 8,
                "intersections": {},
            },
        },
        "runs": runs,
    }
    job.evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")


def _complete_matrix(tmp_path: Path) -> Path:
    root = tmp_path / "mappo_v2"
    for job in build_jobs(run_root=root):
        _write_cell(root, job)
    return root


def test_hierarchical_bootstrap_is_deterministic_and_matches_nested_reference() -> None:
    values = {
        "A": (1.0, 2.0, 3.0),
        "B": (10.0, 20.0, 30.0),
        "C": (-5.0, -4.0, -3.0),
    }
    result = hierarchical_paired_bootstrap(values, seed=77, samples=500)

    rng = np.random.default_rng(77)
    labels = tuple(values)
    draws = []
    for _ in range(500):
        selected = rng.integers(0, len(labels), size=len(labels))
        lineage_means = []
        for lineage_index in selected:
            lineage = np.asarray(values[labels[lineage_index]], dtype=np.float64)
            paired = rng.integers(0, len(lineage), size=len(lineage))
            lineage_means.append(float(lineage[paired].mean()))
        draws.append(float(np.mean(lineage_means)))

    assert result == hierarchical_paired_bootstrap(values, seed=77, samples=500)
    assert result["lower"] == pytest.approx(np.percentile(draws, 2.5))
    assert result["upper"] == pytest.approx(np.percentile(draws, 97.5))


def test_valid_factorial_matrix_passes_gate_and_records_attribution(tmp_path) -> None:
    root = _complete_matrix(tmp_path)

    report = aggregate_factorial(root, bootstrap_seed=19, bootstrap_samples=500)

    assert report["gate"] == {
        "decision": "pass-short-gate",
        "full_training_authorized": False,
    }
    assert report["attribution"]["shared_actor_cancellation"] == {
        "aggregate_gradient_cosine": 0.9993,
        "probability_response_cosine": 0.999989,
        "selected_action_sign_disagreements": "1/3200",
    }
    assert report["attribution"]["joint_state_diversity"]["expected_ceiling"] == 160
    assert len(report["attribution"]["joint_state_diversity"]["actual_by_cell"]) == 12


@pytest.mark.parametrize(
    "mutation,match",
    (
        ("missing_cell", "missing cell"),
        ("incomplete_training", "training diagnostics.*complete"),
        ("failed_evaluation", "evaluation run.*complete"),
        ("missing_primary", "primary metric.*N/A"),
        ("wrong_workers", "workers"),
    ),
)
def test_invalid_or_incomplete_evidence_is_rejected(
    tmp_path, mutation: str, match: str
) -> None:
    root = _complete_matrix(tmp_path)
    job = build_jobs(run_root=root)[-1]
    if mutation == "missing_cell":
        job.evaluation_path.unlink()
    elif mutation == "incomplete_training":
        payload = json.loads(job.diagnostics_path.read_text())
        payload["status"] = "running"
        job.diagnostics_path.write_text(json.dumps(payload))
    elif mutation == "failed_evaluation":
        payload = json.loads(job.evaluation_path.read_text())
        payload["runs"][0]["status"] = "failed"
        job.evaluation_path.write_text(json.dumps(payload))
    elif mutation == "missing_primary":
        payload = json.loads(job.evaluation_path.read_text())
        payload["runs"][0]["completed_waiting_mean_s"] = None
        job.evaluation_path.write_text(json.dumps(payload))
    else:
        payload = json.loads(job.diagnostics_path.read_text())
        payload["frozen_config"]["workers"] = 12
        job.diagnostics_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=match):
        validate_factorial_inputs(root)


def test_primary_metric_regression_freezes_to_ippo_v8(tmp_path) -> None:
    root = _complete_matrix(tmp_path)
    for job in build_jobs(run_root=root):
        if job.arm.name != "R-G":
            continue
        payload = json.loads(job.evaluation_path.read_text())
        for row in payload["runs"]:
            row["official_metrics"]["throughput_veh_per_h"] -= 200.0
        job.evaluation_path.write_text(json.dumps(payload))

    report = aggregate_factorial(root, bootstrap_seed=19, bootstrap_samples=500)

    assert report["gate"]["decision"] == "freeze-to-ippo-v8-ep160"
    assert report["gate"]["full_training_authorized"] is False
    assert any("throughput" in reason for reason in report["gate_reasons"])


def test_checkpoint_policy_digest_must_match_training_evidence(tmp_path) -> None:
    root = _complete_matrix(tmp_path)
    job = build_jobs(run_root=root)[0]
    payload = torch.load(job.checkpoint_path, weights_only=False)
    payload["policy_state_dict"]["synthetic.weight"] += 1.0
    torch.save(payload, job.checkpoint_path)

    with pytest.raises(ValueError, match="policy digest"):
        validate_factorial_inputs(root)


def test_joint_primary_success_must_occur_in_the_same_two_lineages(tmp_path) -> None:
    root = _complete_matrix(tmp_path)
    jobs = build_jobs(run_root=root)
    for job in jobs:
        if job.arm.name != "R-G":
            continue
        payload = json.loads(job.evaluation_path.read_text())
        if job.lineage.name == "A":
            for row in payload["runs"]:
                row["completed_waiting_mean_s"] += 20.0
        elif job.lineage.name == "C":
            for row in payload["runs"]:
                row["official_metrics"]["throughput_veh_per_h"] -= 200.0
        job.evaluation_path.write_text(json.dumps(payload))

    report = aggregate_factorial(root, bootstrap_seed=19, bootstrap_samples=500)

    assert report["gate"]["decision"] == "freeze-to-ippo-v8-ep160"
    assert any(
        "both higher throughput and lower completed-trip waiting" in reason
        for reason in report["gate_reasons"]
    )
