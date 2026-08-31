"""Deterministic bounded regional prior for Safe-MaxPressure."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


SOFT_THRESHOLD = 0.80
HARD_THRESHOLD = 0.95
RELEASE_THRESHOLD = 0.90
PRIOR_UPDATE_INTERVAL_S = 30.0
PRIOR_SOFT_STALE_S = 15.0
PRIOR_HARD_EXPIRE_S = 30.0


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def clamp01(value: Any) -> float:
    return min(1.0, max(0.0, finite_float(value)))


def normalize_occupancy(value: Any) -> float | None:
    """Normalize SUMO's fraction-or-percent occupancy convention."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    if result > 1.0:
        result /= 100.0
    return min(1.0, max(0.0, result))


@dataclass(frozen=True)
class PriorSnapshot:
    generated_at: float
    soft_stale_at: float
    hard_expire_at: float
    values: dict[str, float]
    queue_ratio: dict[str, float]
    arrival_ratio: dict[str, float]
    downstream_risk: dict[str, float]


class DeterministicRegionalPrior:
    """A deterministic cloud-side prior with explicit expiry semantics."""

    def __init__(
        self,
        metadata: Mapping[str, Any],
        *,
        update_interval_s: float = PRIOR_UPDATE_INTERVAL_S,
        soft_stale_s: float = PRIOR_SOFT_STALE_S,
        hard_expire_s: float = PRIOR_HARD_EXPIRE_S,
    ) -> None:
        self._metadata = metadata
        self._update_interval_s = float(update_interval_s)
        self._soft_stale_s = float(soft_stale_s)
        self._hard_expire_s = float(hard_expire_s)
        self._snapshot: PriorSnapshot | None = None
        self._updates = 0

    @property
    def snapshot(self) -> PriorSnapshot | None:
        return self._snapshot

    def update(
        self,
        observation: Mapping[str, Any],
        sim_time: float,
    ) -> PriorSnapshot:
        now = finite_float(sim_time)
        if (
            self._snapshot is not None
            and now + 1e-9
            < self._snapshot.generated_at + self._update_interval_s
        ):
            return self._snapshot

        observations = observation.get("intersections", {})
        if not isinstance(observations, Mapping):
            observations = {}
        intersections = self._metadata.get("intersections", {})
        if not isinstance(intersections, Mapping):
            intersections = {}

        values: dict[str, float] = {}
        queue_ratios: dict[str, float] = {}
        arrival_ratios: dict[str, float] = {}
        downstream_risks: dict[str, float] = {}

        for raw_iid, raw_meta in intersections.items():
            iid = str(raw_iid)
            meta = raw_meta if isinstance(raw_meta, Mapping) else {}
            i_obs = observations.get(raw_iid, observations.get(iid, {}))
            if not isinstance(i_obs, Mapping):
                i_obs = {}
            lanes = i_obs.get("lanes", {})
            if not isinstance(lanes, Mapping):
                lanes = {}
            lane_meta = meta.get("lanes", {})
            if not isinstance(lane_meta, Mapping):
                lane_meta = {}

            incoming = [str(v) for v in meta.get("incoming_lanes", ())]
            outgoing = [str(v) for v in meta.get("outgoing_lanes", ())]
            if not incoming:
                incoming = [str(v) for v in lane_meta]

            storage = 0.0
            queue = 0.0
            arrivals = 0.0
            for lane_id in incoming:
                lane_meta_item = lane_meta.get(lane_id, {})
                if not isinstance(lane_meta_item, Mapping):
                    lane_meta_item = {}
                storage += max(
                    0.0,
                    finite_float(
                        lane_meta_item.get(
                            "length_m", lane_meta_item.get("length", 0.0)
                        )
                    ),
                )
                lane = lanes.get(lane_id, {})
                if not isinstance(lane, Mapping):
                    lane = {}
                queue += max(0.0, finite_float(lane.get("queue_length_m")))
                arrivals += max(
                    0.0,
                    finite_float(
                        lane.get(
                            "next_arrivals",
                            lane.get("arrivals", lane.get("arrival_count", 0.0)),
                        )
                    ),
                )

            q_ratio = clamp01(queue / storage) if storage > 0.0 else 0.0
            a_ratio = clamp01(arrivals / storage) if storage > 0.0 else 0.0
            downstream = 0.0
            for lane_id in outgoing:
                lane = lanes.get(lane_id, {})
                if not isinstance(lane, Mapping):
                    lane = {}
                occupancy = normalize_occupancy(lane.get("occupancy"))
                if occupancy is not None:
                    downstream = max(downstream, occupancy)

            queue_ratios[iid] = q_ratio
            arrival_ratios[iid] = a_ratio
            downstream_risks[iid] = downstream
            # Equal, frozen weights.  The value is advisory and bounded.
            values[iid] = clamp01((q_ratio + a_ratio + downstream) / 3.0)

        self._snapshot = PriorSnapshot(
            generated_at=now,
            soft_stale_at=now + self._soft_stale_s,
            hard_expire_at=now + self._hard_expire_s,
            values=values,
            queue_ratio=queue_ratios,
            arrival_ratio=arrival_ratios,
            downstream_risk=downstream_risks,
        )
        self._updates += 1
        return self._snapshot

    def effective(self, sim_time: float) -> dict[str, Any]:
        """Apply stale decay and hard expiry to the latest snapshot."""
        now = finite_float(sim_time)
        snapshot = self._snapshot
        if snapshot is None:
            return {
                "status": "unavailable",
                "confidence": 0.0,
                "values": {},
                "generated_at": None,
                "soft_stale_at": None,
                "hard_expire_at": None,
            }

        age = max(0.0, now - snapshot.generated_at)
        if now < snapshot.soft_stale_at:
            status = "healthy"
            confidence = 1.0
            values = dict(snapshot.values)
        elif now < snapshot.hard_expire_at:
            status = "soft_stale"
            confidence = max(
                0.0,
                (snapshot.hard_expire_at - now)
                / max(
                    1e-9,
                    snapshot.hard_expire_at - snapshot.soft_stale_at,
                ),
            )
            values = {
                key: value * confidence
                for key, value in snapshot.values.items()
            }
        else:
            status = "hard_expired"
            confidence = 0.0
            values = {key: 0.0 for key in snapshot.values}

        return {
            "status": status,
            "confidence": confidence,
            "age_s": age,
            "values": values,
            "generated_at": snapshot.generated_at,
            "soft_stale_at": snapshot.soft_stale_at,
            "hard_expire_at": snapshot.hard_expire_at,
        }

    def diagnostics(self, sim_time: float | None = None) -> dict[str, Any]:
        if sim_time is None:
            effective_time = (
                self._snapshot.generated_at if self._snapshot is not None else 0.0
            )
        else:
            effective_time = sim_time
        snapshot = self._snapshot
        return {
            "update_interval_s": self._update_interval_s,
            "soft_stale_s": self._soft_stale_s,
            "hard_expire_s": self._hard_expire_s,
            "updates": self._updates,
            "effective": self.effective(effective_time),
            "queue_ratio": dict(snapshot.queue_ratio) if snapshot else {},
            "arrival_ratio": dict(snapshot.arrival_ratio) if snapshot else {},
            "downstream_risk": dict(snapshot.downstream_risk) if snapshot else {},
        }
