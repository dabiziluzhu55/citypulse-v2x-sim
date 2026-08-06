"""Frozen MAPPO-v2 2x2x3 short-experiment manifest and guarded runner."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys

from algorithms.mappo.config import (
    MAPPO_V2_RESIDUAL_MODEL_VERSION,
    MAPPO_V2_SHARED_MODEL_VERSION,
)


EPISODES = 32
WORKERS = 8
EPISODE_DURATION_S = 300
INTERSECTION_COUNT = 20
PERIOD = "off_peak"
EVALUATION_SEEDS = tuple(range(65301, 65309))
RUN_ROOT = Path(__file__).resolve().parent / "runs" / "mappo_v2"


@dataclass(frozen=True)
class Arm:
    name: str
    model_version: str
    actor_variant: str
    critic_scope: str


@dataclass(frozen=True)
class Lineage:
    name: str
    training_seed_start: int
    training_seed_end: int
    actor_init_seed: int
    critic_init_seed: int
    residual_init_seed: int


ARMS = (
    Arm("S-L", MAPPO_V2_SHARED_MODEL_VERSION, "shared", "local"),
    Arm("S-G", MAPPO_V2_SHARED_MODEL_VERSION, "shared", "global"),
    Arm("R-L", MAPPO_V2_RESIDUAL_MODEL_VERSION, "residual", "local"),
    Arm("R-G", MAPPO_V2_RESIDUAL_MODEL_VERSION, "residual", "global"),
)

LINEAGES = (
    Lineage("A", 95101, 95132, 142, 242, 342),
    Lineage("B", 95201, 95232, 143, 243, 343),
    Lineage("C", 95301, 95332, 144, 244, 344),
)


@dataclass(frozen=True)
class ExperimentJob:
    arm: Arm
    lineage: Lineage
    run_root: Path = RUN_ROOT

    @property
    def cell_id(self) -> str:
        return f"{self.lineage.name}-{self.arm.name}"

    @property
    def output_directory(self) -> Path:
        return self.run_root / self.lineage.name / self.arm.name

    @property
    def checkpoint_path(self) -> Path:
        return self.output_directory / "mappo_ep32.pt"

    @property
    def diagnostics_path(self) -> Path:
        return self.output_directory / "training_diagnostics.json"

    @property
    def evaluation_path(self) -> Path:
        return self.output_directory / "evaluation.json"

    def training_command(self) -> tuple[str, ...]:
        return (
            "python",
            "-m",
            "algorithms.mappo.train",
            "--model-version",
            self.arm.model_version,
            "--critic-scope",
            self.arm.critic_scope,
            "--init",
            "random",
            "--intersections",
            str(INTERSECTION_COUNT),
            "--episodes",
            str(EPISODES),
            "--workers",
            str(WORKERS),
            "--duration",
            str(EPISODE_DURATION_S),
            "--base-seed",
            str(self.lineage.training_seed_start),
            "--period",
            PERIOD,
            "--actor-init-seed",
            str(self.lineage.actor_init_seed),
            "--critic-init-seed",
            str(self.lineage.critic_init_seed),
            "--residual-init-seed",
            str(self.lineage.residual_init_seed),
            "--checkpoint-every",
            str(EPISODES),
            "--save",
            str(self.checkpoint_path),
            "--diagnostics-output",
            str(self.diagnostics_path),
        )

    def evaluation_command(self) -> tuple[str, ...]:
        return (
            "python",
            "-m",
            "algorithms.mappo.evaluate_checkpoint",
            "--checkpoint",
            str(self.checkpoint_path),
            "--seeds",
            *(str(seed) for seed in EVALUATION_SEEDS),
            "--workers",
            str(WORKERS),
            "--duration",
            str(EPISODE_DURATION_S),
            "--period",
            PERIOD,
            "--label",
            self.cell_id,
            "--output",
            str(self.evaluation_path),
        )

    def manifest_record(self) -> dict[str, object]:
        return {
            "cell_id": self.cell_id,
            "arm": self.arm.name,
            "actor_variant": self.arm.actor_variant,
            "model_version": self.arm.model_version,
            "critic_scope": self.arm.critic_scope,
            "lineage": self.lineage.name,
            "training_seed_range": [
                self.lineage.training_seed_start,
                self.lineage.training_seed_end,
            ],
            "actor_init_seed": self.lineage.actor_init_seed,
            "critic_init_seed": self.lineage.critic_init_seed,
            "residual_init_seed": self.lineage.residual_init_seed,
            "training_command": list(self.training_command()),
            "evaluation_command": list(self.evaluation_command()),
        }


def build_jobs(*, run_root: Path = RUN_ROOT) -> tuple[ExperimentJob, ...]:
    return tuple(
        ExperimentJob(arm=arm, lineage=lineage, run_root=run_root)
        for lineage in LINEAGES
        for arm in ARMS
    )


def execution_command(command: tuple[str, ...]) -> tuple[str, ...]:
    """Run a printed manifest command in the invoking Python environment."""

    if not command or command[0] != "python":
        raise ValueError("manifest command must start with python")
    return (sys.executable, *command[1:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-short-cell", action="store_true")
    parser.add_argument("--arm", choices=tuple(arm.name for arm in ARMS))
    parser.add_argument(
        "--lineage", choices=tuple(lineage.name for lineage in LINEAGES)
    )
    args = parser.parse_args(argv)

    jobs = build_jobs()
    if not args.execute_short_cell:
        if args.arm is not None or args.lineage is not None:
            parser.error("--arm/--lineage require --execute-short-cell")
        print(
            json.dumps(
                [job.manifest_record() for job in jobs],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.arm is None or args.lineage is None:
        parser.error(
            "--execute-short-cell requires exactly one --arm and --lineage"
        )
    job = next(
        item
        for item in jobs
        if item.arm.name == args.arm and item.lineage.name == args.lineage
    )
    job.output_directory.mkdir(parents=True, exist_ok=True)
    subprocess.run(execution_command(job.training_command()), check=True)
    subprocess.run(execution_command(job.evaluation_command()), check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
