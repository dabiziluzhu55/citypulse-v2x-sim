"""Frozen training seeds required by the maintained CoV2X entry point."""

from __future__ import annotations


TRAIN_SEEDS = tuple(range(24501, 24513))


def assert_train_seed(seed: int) -> None:
    """Reject accidental training on non-registered or evaluation seeds."""
    value = int(seed)
    if value not in TRAIN_SEEDS:
        raise ValueError(f"seed {value} is not registered for TRAIN")
