from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np


MAX_WAITING = 200.0
MAX_OCCUPANCY = 100.0
REWARD_MIN = -3.0
REWARD_MAX = 1.0
TIME_TOLERANCE_S = 1e-9


def normalize_reward(raw_reward: float) -> float:
    return float(np.clip(float(raw_reward), REWARD_MIN, REWARD_MAX))


def spillback_penalty(occupancy_percent: float) -> float:
    occupancy = float(
        np.clip(float(occupancy_percent) / MAX_OCCUPANCY, 0.0, 1.0)
    )
    return float(np.clip((occupancy - 0.70) / 0.20, 0.0, 1.0) ** 2)


@dataclass(frozen=True)
class V5ARewardResult:
    reward: float
    raw_reward: float
    components: dict[str, float]
    observations: int
    observed_seconds: float


class FrozenComponents(dict[str, float]):
    """A JSON- and pickle-compatible immutable mapping of scalar components."""

    def __reduce__(self) -> tuple[object, tuple[dict[str, float]]]:
        return (type(self), (dict(self),))

    def _immutable(self, *args: object, **kwargs: object) -> None:
        raise TypeError("team reward components are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


@dataclass(frozen=True)
class TeamRewardResult:
    reward: float
    raw_reward: float
    components: dict[str, float]
    per_intersection_raw_rewards: tuple[float, ...]
    observations: int
    observed_seconds: float
    window_start_s: float
    window_end_s: float
    schema: str


def aggregate_team_reward(
    results: Mapping[str, V5ARewardResult],
    intersection_ids: Sequence[str],
    window_start_s: float,
    window_end_s: float,
) -> TeamRewardResult:
    ordered_ids = tuple(str(value) for value in intersection_ids)
    if not ordered_ids or len(set(ordered_ids)) != len(ordered_ids):
        raise ValueError("intersection IDs must be non-empty and unique")
    if set(results) != set(ordered_ids) or len(results) != len(ordered_ids):
        raise ValueError("results must exactly match the requested intersections")

    start = float(window_start_s)
    end = float(window_end_s)
    if not np.isfinite(start) or not np.isfinite(end) or end <= start:
        raise ValueError("reward window must be finite and positive")
    window_seconds = end - start

    ordered_results = tuple(results[intersection_id] for intersection_id in ordered_ids)
    first = ordered_results[0]
    component_keys = tuple(first.components)
    if not component_keys:
        raise ValueError("result components must be non-empty")
    observations = first.observations
    observed_seconds = float(first.observed_seconds)

    for result in ordered_results:
        if set(result.components) != set(component_keys):
            raise ValueError("result component keys must exactly match")
        scalar_values = (
            result.reward,
            result.raw_reward,
            result.observations,
            result.observed_seconds,
            *result.components.values(),
        )
        if not all(np.isfinite(float(value)) for value in scalar_values):
            raise ValueError("result values must be finite")
        if result.observations != observations:
            raise ValueError("result observation counts must match")
        if (
            abs(float(result.observed_seconds) - observed_seconds)
            > TIME_TOLERANCE_S
        ):
            raise ValueError("result durations must match")

    if observations <= 0:
        raise ValueError("result observations must be positive")
    if (
        observed_seconds <= 0.0
        or abs(observed_seconds - window_seconds) > TIME_TOLERANCE_S
    ):
        raise ValueError("result duration must match the reward window")

    raw_values = tuple(float(result.raw_reward) for result in ordered_results)
    raw_reward = float(np.mean(raw_values))
    components = {
        name: float(np.mean([float(result.components[name]) for result in ordered_results]))
        for name in component_keys
    }
    return TeamRewardResult(
        reward=normalize_reward(raw_reward),
        raw_reward=raw_reward,
        components=FrozenComponents(components),
        per_intersection_raw_rewards=raw_values,
        observations=observations,
        observed_seconds=observed_seconds,
        window_start_s=start,
        window_end_s=end,
        schema="v5a_team_mean_raw_then_clip_v1",
    )


class V5ARewardAccumulator:
    def __init__(
        self,
        *,
        incoming_lanes: Sequence[str],
        outgoing_lanes: Sequence[str],
        lane_capacities: Mapping[str, float],
        incoming_capacity: float,
        flow_reference_rate: float,
        waiting_start: float,
    ) -> None:
        self.incoming_lanes = tuple(str(value) for value in incoming_lanes)
        self.outgoing_lanes = tuple(str(value) for value in outgoing_lanes)
        self.lane_capacities = {
            str(key): max(float(value), 1.0)
            for key, value in lane_capacities.items()
        }
        self.incoming_capacity = max(float(incoming_capacity), 1.0)
        self.flow_reference_rate = max(float(flow_reference_rate), 0.0)
        self.waiting_start = float(waiting_start)
        self.waiting_end = float(waiting_start)
        self.delay_increment = 0.0
        self.stopped_vehicle_seconds = 0.0
        self.queue_ratio_seconds = 0.0
        self.outgoing_occupancy_seconds: dict[str, float] = {}
        self.crossings = 0
        self.safe_crossings = 0.0
        self.blocked_crossings = 0.0
        self.observed_seconds = 0.0
        self.observations = 0
        self._pressure_regret: float | None = None
        self._pressure_alpha: float = 0.0

    def set_pressure_context(self, regret: float, alpha: float) -> None:
        """Set pre-computed pressure regret and effective alpha.

        Called at the decision boundary, before any observe() calls.
        Must be called at most once per accumulator lifetime.
        """
        if self._pressure_regret is not None:
            raise RuntimeError("pressure context already set for this accumulator")
        self._pressure_regret = float(regret)
        self._pressure_alpha = float(alpha)

    def observe(
        self,
        intersection: Mapping[str, object],
        *,
        elapsed_seconds: float,
        delay_increment: float,
        crossings: int,
    ) -> None:
        elapsed = float(elapsed_seconds)
        if elapsed <= 0.0:
            return
        lanes = intersection.get("lanes", {})
        if not isinstance(lanes, Mapping):
            lanes = {}
        incoming_halt = 0.0
        queue_ratio = 0.0
        for lane_id in self.incoming_lanes:
            lane = lanes.get(lane_id, {})
            if not isinstance(lane, Mapping):
                lane = {}
            halting = float(lane.get("halting_count", 0.0))
            incoming_halt += halting
            queue_ratio = max(
                queue_ratio,
                halting / self.lane_capacities.get(lane_id, 1.0),
            )
        current_spillbacks: list[float] = []
        for lane_id in self.outgoing_lanes:
            lane = lanes.get(lane_id, {})
            if not isinstance(lane, Mapping):
                lane = {}
            occupancy = float(lane.get("occupancy", 0.0))
            current_spillbacks.append(spillback_penalty(occupancy))
            self.outgoing_occupancy_seconds[lane_id] = (
                self.outgoing_occupancy_seconds.get(lane_id, 0.0)
                + float(np.clip(occupancy / MAX_OCCUPANCY, 0.0, 1.0))
                * elapsed
            )
        current_spillback = max(current_spillbacks, default=0.0)
        accepted_crossings = max(int(crossings), 0)
        self.delay_increment += max(float(delay_increment), 0.0)
        self.stopped_vehicle_seconds += incoming_halt * elapsed
        self.queue_ratio_seconds += queue_ratio * elapsed
        self.crossings += accepted_crossings
        self.safe_crossings += accepted_crossings * (1.0 - current_spillback)
        self.blocked_crossings += accepted_crossings * current_spillback
        self.observed_seconds += elapsed
        self.observations += 1
        self.waiting_end = sum(
            float(
                lanes.get(lane_id, {}).get("waiting_time", 0.0)
                if isinstance(lanes.get(lane_id, {}), Mapping)
                else 0.0
            )
            for lane_id in self.incoming_lanes
        )

    def finalize(self) -> V5ARewardResult:
        if self.observations == 0 or self.observed_seconds <= 0.0:
            raise RuntimeError(
                "v5a reward requires at least one follow-up observation"
            )
        duration = max(self.observed_seconds, 1e-6)
        capacity = self.incoming_capacity
        delay = float(
            np.clip(self.delay_increment / (duration * capacity), 0.0, 1.5)
        )
        stopped = float(
            np.clip(
                self.stopped_vehicle_seconds / (duration * capacity),
                0.0,
                1.5,
            )
        )
        queue = float(
            np.clip(self.queue_ratio_seconds / duration, 0.0, 1.5)
        )
        congestion = 0.45 * delay + 0.40 * stopped + 0.15 * queue
        average_outgoing = (
            value / duration
            for value in self.outgoing_occupancy_seconds.values()
        )
        maximum_spillback = max(
            (
                spillback_penalty(value * MAX_OCCUPANCY)
                for value in average_outgoing
            ),
            default=0.0,
        )
        flow_weighted_spillback = (
            self.blocked_crossings / self.crossings
            if self.crossings > 0
            else 0.0
        )
        spillback = (
            0.70 * maximum_spillback + 0.30 * flow_weighted_spillback
        )
        flow_reference = max(self.flow_reference_rate * duration, 1.0)
        safe_flow = float(
            np.clip(self.safe_crossings / flow_reference, 0.0, 1.0)
        )
        waiting_reference = max(MAX_WAITING * capacity, 1.0)
        waiting_gain = float(
            np.clip(
                (self.waiting_start - self.waiting_end) / waiting_reference,
                -1.0,
                1.0,
            )
        )
        raw_reward = (
            -0.60 * congestion
            + 0.20 * safe_flow
            - 0.15 * spillback
            + 0.05 * waiting_gain
        )
        if self._pressure_regret is not None:
            raw_reward = float(raw_reward + self._pressure_alpha * self._pressure_regret)
        return V5ARewardResult(
            reward=normalize_reward(raw_reward),
            raw_reward=float(raw_reward),
            components={
                "D": float(congestion),
                "L": delay,
                "S": stopped,
                "Qmax": queue,
                "F_safe": safe_flow,
                "B": float(spillback),
                "H": waiting_gain,
                "MP_regret": self._pressure_regret if self._pressure_regret is not None else 0.0,
                "MP_alpha": self._pressure_alpha,
            },
            observations=self.observations,
            observed_seconds=float(self.observed_seconds),
        )
