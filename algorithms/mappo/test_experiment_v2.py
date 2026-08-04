from __future__ import annotations

import sys

from algorithms.mappo.config import (
    MAPPO_V2_RESIDUAL_MODEL_VERSION,
    MAPPO_V2_SHARED_MODEL_VERSION,
)
from algorithms.mappo.experiment_v2 import (
    EVALUATION_SEEDS,
    build_jobs,
    execution_command,
)


def _option(command: tuple[str, ...], name: str) -> str:
    return command[command.index(name) + 1]


def test_manifest_is_exactly_four_arms_by_three_lineages() -> None:
    jobs = build_jobs()

    assert len(jobs) == 12
    assert len({job.cell_id for job in jobs}) == 12
    assert {job.arm.name for job in jobs} == {"S-L", "S-G", "R-L", "R-G"}
    assert {job.lineage.name for job in jobs} == {"A", "B", "C"}
    assert EVALUATION_SEEDS == tuple(range(65301, 65309))


def test_lineages_freeze_environment_and_initialization_seeds() -> None:
    expected = {
        "A": (95101, 95132, 142, 242, 342),
        "B": (95201, 95232, 143, 243, 343),
        "C": (95301, 95332, 144, 244, 344),
    }

    for job in build_jobs():
        lineage = job.lineage
        assert (
            lineage.training_seed_start,
            lineage.training_seed_end,
            lineage.actor_init_seed,
            lineage.critic_init_seed,
            lineage.residual_init_seed,
        ) == expected[lineage.name]


def test_arm_versions_and_contexts_are_the_only_factorial_changes() -> None:
    expected = {
        "S-L": (MAPPO_V2_SHARED_MODEL_VERSION, "shared", "local"),
        "S-G": (MAPPO_V2_SHARED_MODEL_VERSION, "shared", "global"),
        "R-L": (MAPPO_V2_RESIDUAL_MODEL_VERSION, "residual", "local"),
        "R-G": (MAPPO_V2_RESIDUAL_MODEL_VERSION, "residual", "global"),
    }

    for job in build_jobs():
        assert (
            job.arm.model_version,
            job.arm.actor_variant,
            job.arm.critic_scope,
        ) == expected[job.arm.name]


def test_every_training_command_encodes_the_frozen_short_cell() -> None:
    for job in build_jobs():
        command = job.training_command()
        assert command[:4] == ("python", "-m", "algorithms.mappo.train", "--model-version")
        assert _option(command, "--model-version") == job.arm.model_version
        assert _option(command, "--critic-scope") == job.arm.critic_scope
        assert _option(command, "--init") == "random"
        assert _option(command, "--intersections") == "20"
        assert _option(command, "--episodes") == "32"
        assert _option(command, "--workers") == "8"
        assert _option(command, "--duration") == "300"
        assert _option(command, "--period") == "off_peak"
        assert _option(command, "--base-seed") == str(
            job.lineage.training_seed_start
        )
        assert _option(command, "--actor-init-seed") == str(
            job.lineage.actor_init_seed
        )
        assert _option(command, "--critic-init-seed") == str(
            job.lineage.critic_init_seed
        )
        assert _option(command, "--residual-init-seed") == str(
            job.lineage.residual_init_seed
        )
        assert "--resume" not in command
        assert "ippo" not in " ".join(command).lower()
        assert command.count("--diagnostics-output") == 1


def test_evaluation_command_uses_the_shared_held_out_seed_set() -> None:
    for job in build_jobs():
        command = job.evaluation_command()
        seed_index = command.index("--seeds") + 1
        worker_index = command.index("--workers")
        assert tuple(map(int, command[seed_index:worker_index])) == EVALUATION_SEEDS
        assert _option(command, "--workers") == "8"
        assert _option(command, "--duration") == "300"
        assert _option(command, "--period") == "off_peak"
        assert _option(command, "--checkpoint") == str(job.checkpoint_path)


def test_guarded_execution_reuses_the_current_python_environment() -> None:
    command = ("python", "-m", "algorithms.mappo.train", "--help")

    assert execution_command(command) == (
        sys.executable,
        "-m",
        "algorithms.mappo.train",
        "--help",
    )
