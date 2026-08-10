from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from algorithms.ippo.controller import StateBuilder as _IPPOV8StateBuilder


CENTRALIZED_STATE_SCHEMA = "centralized_local_obs_pool_v1"
IPPO_V8_LOCAL_OBSERVATION_SCHEMA = "ippo_v8_local_obs_112_plus_identity_v1"
IPPO_V8_IDENTITY_OFFSET = 9


class IPPOV8FeatureBuilder(_IPPOV8StateBuilder):
    """Pinned adapter for the verified IPPO-v8 local feature contract."""

    pass


@dataclass(frozen=True)
class CentralizedState:
    observations: np.ndarray
    agent_mask: np.ndarray
    intersection_ids: tuple[str, ...]
    schema: str = CENTRALIZED_STATE_SCHEMA


class CentralizedStateBuilder:
    SCHEMA = CENTRALIZED_STATE_SCHEMA

    def __init__(self, intersection_ids: Sequence[str], obs_dim: int):
        ids = tuple(str(value) for value in intersection_ids)
        if not ids:
            raise ValueError("centralized state requires at least one intersection")
        if any(not value for value in ids):
            raise ValueError("intersection identifiers must be non-empty")
        if len(ids) != len(set(ids)):
            raise ValueError("intersection identifiers must be unique")
        if int(obs_dim) <= 0:
            raise ValueError("observation dimension must be positive")
        self.intersection_ids = ids
        self.obs_dim = int(obs_dim)

    def build(
        self, local_observations: Mapping[str, np.ndarray]
    ) -> CentralizedState:
        supplied_ids = {str(value) for value in local_observations}
        expected_ids = set(self.intersection_ids)
        missing = [
            value for value in self.intersection_ids if value not in supplied_ids
        ]
        extra = sorted(supplied_ids - expected_ids)
        if missing:
            raise ValueError(
                "missing controlled intersections: " + ", ".join(missing)
            )
        if extra:
            raise ValueError("unexpected intersections: " + ", ".join(extra))

        rows: list[np.ndarray] = []
        for intersection_id in self.intersection_ids:
            row = np.asarray(
                local_observations[intersection_id], dtype=np.float32
            )
            if row.shape != (self.obs_dim,):
                raise ValueError(
                    f"{intersection_id} observation shape {row.shape}; "
                    f"expected ({self.obs_dim},)"
                )
            if not np.isfinite(row).all():
                raise ValueError(
                    f"{intersection_id} observation must contain only finite values"
                )
            rows.append(row)

        observations = np.stack(rows, axis=0).astype(np.float32, copy=True)
        agent_mask = np.ones(len(self.intersection_ids), dtype=np.bool_)
        observations.setflags(write=False)
        agent_mask.setflags(write=False)
        return CentralizedState(
            observations=observations,
            agent_mask=agent_mask,
            intersection_ids=self.intersection_ids,
        )
