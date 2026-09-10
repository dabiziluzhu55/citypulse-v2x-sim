"""Scenario-group split with optional held-out seeds and OOD event combos."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Mapping, Sequence

from .schema import ScenarioSpec


def _bucket(group_id: str, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}|{group_id}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def assign_splits(
    scenarios: Sequence[ScenarioSpec],
    split_cfg: Mapping[str, Any],
) -> dict[str, str]:
    seed = int(split_cfg.get("seed", 0))
    train_p = float(split_cfg.get("train", 0.8))
    val_p = float(split_cfg.get("val", 0.1))
    holdout_seeds = {int(item) for item in split_cfg.get("holdout_seeds") or ()}
    val_seeds = {int(item) for item in split_cfg.get("val_seeds") or ()}
    analysis_seeds = {int(item) for item in split_cfg.get("analysis_seeds") or ()}
    ood_fraction = float(split_cfg.get("ood_event_fraction") or 0.0)

    groups: dict[str, list[ScenarioSpec]] = defaultdict(list)
    for spec in scenarios:
        groups[spec.scenario_group_id].append(spec)

    event_keys = sorted(
        {
            (spec.event.event_type, float(spec.event.start_seconds), float(spec.event.end_seconds - spec.event.start_seconds))
            for spec in scenarios
        }
    )
    n_ood = int(round(len(event_keys) * ood_fraction)) if event_keys else 0
    ood_keys = set(event_keys[:n_ood])

    assignment: dict[str, str] = {}
    for group_id, members in groups.items():
        spec = members[0]
        event_key = (
            spec.event.event_type,
            float(spec.event.start_seconds),
            float(spec.event.end_seconds - spec.event.start_seconds),
        )
        if spec.seed in analysis_seeds:
            assignment[group_id] = "analysis"
            continue
        if spec.seed in val_seeds:
            assignment[group_id] = "val"
            continue
        if spec.seed in holdout_seeds or event_key in ood_keys:
            assignment[group_id] = "test"
            continue
        score = _bucket(group_id, seed)
        if score < train_p:
            assignment[group_id] = "train"
        elif score < train_p + val_p:
            assignment[group_id] = "val"
        else:
            assignment[group_id] = "test"
    return assignment


def split_manifest(
    scenarios: Sequence[ScenarioSpec],
    assignment: Mapping[str, str],
    split_cfg: Mapping[str, Any],
) -> dict[str, Any]:
    counts = {"train": 0, "val": 0, "test": 0, "analysis": 0}
    for spec in scenarios:
        counts[assignment.get(spec.scenario_group_id, "train")] += 1
    return {
        "rule": "scenario_group_id atomic split; holdout seeds and OOD event keys forced to test",
        "seed": split_cfg.get("seed"),
        "ratios": {
            "train": split_cfg.get("train"),
            "val": split_cfg.get("val"),
            "test": split_cfg.get("test"),
        },
        "holdout_seeds": list(split_cfg.get("holdout_seeds") or ()),
        "val_seeds": list(split_cfg.get("val_seeds") or ()),
        "analysis_seeds": list(split_cfg.get("analysis_seeds") or ()),
        "ood_event_fraction": split_cfg.get("ood_event_fraction"),
        "n_scenarios": dict(counts),
        "n_groups": len(set(assignment)),
        "assignment": dict(assignment),
    }


def leak_check(
    samples: Sequence[Mapping[str, Any]],
    assignment: Mapping[str, str],
) -> list[str]:
    """Return errors if the same scenario_group_id appears in multiple splits."""

    seen: dict[str, str] = {}
    errors: list[str] = []
    for sample in samples:
        meta = sample.get("metadata") or {}
        group_id = str(meta.get("scenario_group_id") or "")
        split = str(meta.get("split") or "")
        if not group_id:
            errors.append("sample missing scenario_group_id")
            continue
        expected = assignment.get(group_id)
        if expected and split and expected != split:
            errors.append(f"{group_id} assigned {expected} but sample split={split}")
        prev = seen.get(group_id)
        if prev and split and prev != split:
            errors.append(f"{group_id} leaked across {prev} and {split}")
        if split:
            seen[group_id] = split
    return errors
