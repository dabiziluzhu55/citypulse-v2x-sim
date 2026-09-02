"""On-demand SUMO vehicle telemetry and leased control actions."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from ..algorithm.policy import (
    NextSignalObservation,
    PreviousActionResults,
    PreviousVehicleActionResult,
    VehicleAction,
    VehicleDrivingEventsObservation,
    VehicleEnergyObservation,
    VehicleLocationObservation,
    VehicleMotionObservation,
    VehicleObservation,
    VehiclePositionObservation,
    VehicleTrafficObservation,
    VehicleTypeMetadata,
)
from ..building.vehicle_profiles import VehicleProfile


STOPPED_SPEED_EPSILON_MPS = 1e-3
STOPPED_LANE_CHANGE_MODE = 512
DEFAULT_LANE_CHANGE_MODE = 1621

@dataclass
class _TrackedVehicle:
    type_id: str
    last_observed_time: float
    values: dict[str, object] = field(default_factory=dict)
    fuel_total_mg: float = 0.0
    fuel_interval_mg: float = 0.0
    hard_braking_total: int = 0
    hard_braking_interval: int = 0
    hard_braking_active: bool = False
    previous_road_id: str | None = None
    previous_lane_index: int | None = None
    last_lane_change_time: float | None = None


def build_vehicle_type_metadata(
    vehicle_type_profiles: Mapping[str, str],
    profiles: Mapping[str, VehicleProfile],
) -> Mapping[str, VehicleTypeMetadata]:
    result = {}
    for type_id, profile_id in vehicle_type_profiles.items():
        if profile_id not in profiles:
            raise ValueError(f"Vehicle type {type_id!r} uses unknown profile {profile_id!r}.")
        profile = profiles[profile_id]
        result[type_id] = VehicleTypeMetadata(
            type_id=type_id,
            profile_id=profile.profile_id,
            pcu_factor=profile.pcu_factor,
            vehicle_class=profile.v_class,
            powertrain=profile.powertrain,
            emission_class=profile.emission_class,
            accel_mps2=profile.accel_mps2,
            decel_mps2=profile.decel_mps2,
            length_m=profile.length_m,
            width_m=profile.width_m,
            min_gap_m=profile.min_gap_m,
            max_speed_mps=profile.max_speed_mps,
            fuel_density_mg_per_ml=profile.fuel_density_mg_per_ml,
            hard_braking_threshold_mps2=profile.hard_braking_threshold_mps2,
        )
    return result


class VehicleTelemetryTracker:
    """Maintain the active set cheaply and refresh full observations on demand."""

    def __init__(
        self,
        traci,
        vehicle_types: Mapping[str, VehicleTypeMetadata],
        tls_to_intersection: Mapping[str, str],
    ) -> None:
        self.traci = traci
        self.vehicle_types = dict(vehicle_types)
        self.tls_to_intersection = dict(tls_to_intersection)
        self._tracked: dict[str, _TrackedVehicle] = {}
        self._last_time = 0.0
        self._sampled_fuel_mg = 0.0
        self._sampled_fuel_ml = 0.0
        self._sampled_braking = 0
        constants = getattr(traci, "constants", None)
        vehicle_api = getattr(traci, "vehicle", None)
        self._subscription_enabled = bool(
            constants is not None
            and vehicle_api is not None
            and hasattr(vehicle_api, "subscribe")
            and hasattr(vehicle_api, "getAllSubscriptionResults")
        )
        self._subscription_variables: tuple[int, ...] = ()
        if self._subscription_enabled:
            self._subscription_variables = (
                constants.VAR_POSITION,
                constants.VAR_SPEED,
                constants.VAR_ACCELERATION,
                constants.VAR_ANGLE,
                constants.VAR_ROAD_ID,
                constants.VAR_LANE_ID,
                constants.VAR_LANE_INDEX,
                constants.VAR_LANEPOSITION,
                constants.VAR_ALLOWED_SPEED,
                constants.VAR_ROUTE_ID,
                constants.VAR_ROUTE_INDEX,
                constants.VAR_WAITING_TIME,
                constants.VAR_ACCUMULATED_WAITING_TIME,
                constants.VAR_TIMELOSS,
                constants.VAR_DISTANCE,
                constants.VAR_FUELCONSUMPTION,
            )

    def update_vehicle_set(
        self,
        departed_vehicle_ids: Iterable[object],
        arrived_vehicle_ids: Iterable[object],
        elapsed: float,
    ) -> None:
        """Apply SUMO's per-step lifecycle deltas without reading full vehicle state."""

        now = float(elapsed)
        if now + 1e-9 < self._last_time:
            raise ValueError("Vehicle telemetry time cannot move backwards.")
        for value in arrived_vehicle_ids:
            self._tracked.pop(str(value), None)
        for value in departed_vehicle_ids:
            vehicle_id = str(value)
            if vehicle_id in self._tracked:
                continue
            type_id = str(self.traci.vehicle.getTypeID(vehicle_id))
            if type_id not in self.vehicle_types:
                continue
            self._tracked[vehicle_id] = _TrackedVehicle(
                type_id=type_id,
                last_observed_time=now,
            )
            if self._subscription_enabled:
                self.traci.vehicle.subscribe(
                    vehicle_id,
                    self._subscription_variables,
                )

    def sync_subscription_results(self) -> None:
        """Copy one coherent SUMO-step vehicle frame into the local cache."""

        if not self._subscription_enabled:
            return
        constants = self.traci.constants
        results = self.traci.vehicle.getAllSubscriptionResults() or {}
        for value, raw in results.items():
            vehicle_id = str(value)
            tracked = self._tracked.get(vehicle_id)
            if tracked is None or not isinstance(raw, Mapping):
                continue
            required = self._subscription_variables
            if any(variable not in raw for variable in required):
                continue
            tracked.values = {
                "position": raw[constants.VAR_POSITION],
                "speed": raw[constants.VAR_SPEED],
                "acceleration": raw[constants.VAR_ACCELERATION],
                "angle": raw[constants.VAR_ANGLE],
                "road_id": raw[constants.VAR_ROAD_ID],
                "lane_id": raw[constants.VAR_LANE_ID],
                "lane_index": raw[constants.VAR_LANE_INDEX],
                "lane_position": raw[constants.VAR_LANEPOSITION],
                "allowed_speed": raw[constants.VAR_ALLOWED_SPEED],
                "route_id": raw[constants.VAR_ROUTE_ID],
                "route_index": raw[constants.VAR_ROUTE_INDEX],
                # libsumo exposes subscribed compound VAR_NEXT_TLS values as
                # a SWIG TraCIResult rather than the iterable returned by the
                # direct getter. Refresh the compound value at decision time.
                "next_tls": tracked.values.get("next_tls", ()),
                "waiting_time": raw[constants.VAR_WAITING_TIME],
                "accumulated_waiting_time": raw[
                    constants.VAR_ACCUMULATED_WAITING_TIME
                ],
                "time_loss": raw[constants.VAR_TIMELOSS],
                "distance": raw[constants.VAR_DISTANCE],
                "fuel_rate": raw[constants.VAR_FUELCONSUMPTION],
            }

    def refresh_observations(self, elapsed: float) -> None:
        """Read full state once immediately before a decision or AI frame."""

        now = float(elapsed)
        if now + 1e-9 < self._last_time:
            raise ValueError("Vehicle telemetry time cannot move backwards.")
        for vehicle_id in sorted(self._tracked):
            tracked = self._tracked[vehicle_id]
            if not tracked.values:
                # A newly departed vehicle has no subscription frame until the
                # next simulationStep. Keep a direct first-frame fallback only.
                tracked.values = self._read_direct(vehicle_id)
            elif self._subscription_enabled:
                # VAR_ROUTE (0x57) is not a supported libsumo subscription
                # variable. Route edges are decision-only data, so one getter
                # per vehicle at the low-frequency observation boundary is
                # sufficient and avoids blocking each presentation snapshot.
                tracked.values["route_edges"] = self.traci.vehicle.getRoute(
                    vehicle_id
                )
                tracked.values["next_tls"] = self.traci.vehicle.getNextTLS(
                    vehicle_id
                )
            road_id = str(tracked.values["road_id"])
            lane_index = int(tracked.values["lane_index"])
            if (
                tracked.previous_road_id == road_id
                and tracked.previous_lane_index is not None
                and tracked.previous_lane_index != lane_index
                and not road_id.startswith(":")
            ):
                tracked.last_lane_change_time = now
            tracked.previous_road_id = road_id
            tracked.previous_lane_index = lane_index
            delta = max(0.0, now - tracked.last_observed_time)
            fuel_rate = max(0.0, float(tracked.values["fuel_rate"]))
            consumed = fuel_rate * delta
            tracked.fuel_total_mg += consumed
            tracked.fuel_interval_mg += consumed
            self._sampled_fuel_mg += consumed
            self._sampled_fuel_ml += (
                consumed / self.vehicle_types[tracked.type_id].fuel_density_mg_per_ml
            )
            acceleration = float(tracked.values["acceleration"])
            threshold = self.vehicle_types[tracked.type_id].hard_braking_threshold_mps2
            braking = acceleration <= threshold
            if braking and not tracked.hard_braking_active:
                tracked.hard_braking_total += 1
                tracked.hard_braking_interval += 1
                self._sampled_braking += 1
            tracked.hard_braking_active = braking
            tracked.last_observed_time = now
        self._last_time = now

    def _read_direct(self, vehicle_id: str) -> dict[str, object]:
        vehicle = self.traci.vehicle
        return {
            "position": vehicle.getPosition(vehicle_id),
            "speed": vehicle.getSpeed(vehicle_id),
            "acceleration": vehicle.getAcceleration(vehicle_id),
            "angle": vehicle.getAngle(vehicle_id),
            "road_id": vehicle.getRoadID(vehicle_id),
            "lane_id": vehicle.getLaneID(vehicle_id),
            "lane_index": vehicle.getLaneIndex(vehicle_id),
            "lane_position": vehicle.getLanePosition(vehicle_id),
            "allowed_speed": vehicle.getAllowedSpeed(vehicle_id),
            "route_id": vehicle.getRouteID(vehicle_id),
            "route_index": vehicle.getRouteIndex(vehicle_id),
            "route_edges": vehicle.getRoute(vehicle_id),
            "next_tls": vehicle.getNextTLS(vehicle_id),
            "waiting_time": vehicle.getWaitingTime(vehicle_id),
            "accumulated_waiting_time": vehicle.getAccumulatedWaitingTime(vehicle_id),
            "time_loss": vehicle.getTimeLoss(vehicle_id),
            "distance": vehicle.getDistance(vehicle_id),
            "fuel_rate": vehicle.getFuelConsumption(vehicle_id),
        }

    def observations(self, *, reset_interval: bool) -> Mapping[str, VehicleObservation]:
        result = {}
        neighbor_gaps = self._neighbor_gaps()
        for vehicle_id in sorted(self._tracked):
            tracked = self._tracked[vehicle_id]
            values = tracked.values
            if not values:
                continue
            type_metadata = self.vehicle_types[tracked.type_id]
            leader_gap, follower_gap = neighbor_gaps.get(
                vehicle_id, (None, None)
            )
            x, y = values["position"]
            lane_index = int(values["lane_index"])
            route_edges = tuple(str(edge) for edge in values["route_edges"])
            result[vehicle_id] = VehicleObservation(
                type_id=tracked.type_id,
                position=VehiclePositionObservation(float(x), float(y)),
                motion=VehicleMotionObservation(
                    speed_mps=float(values["speed"]),
                    acceleration_mps2=float(values["acceleration"]),
                    angle_deg=float(values["angle"]),
                    allowed_speed_mps=float(values["allowed_speed"]),
                ),
                location=VehicleLocationObservation(
                    road_id=str(values["road_id"]),
                    lane_id=str(values["lane_id"]),
                    lane_index=lane_index,
                    lane_position_m=float(values["lane_position"]),
                    route_id=str(values["route_id"]),
                    route_index=int(values["route_index"]),
                    route_edges=route_edges,
                ),
                traffic=VehicleTrafficObservation(
                    waiting_time_s=float(values["waiting_time"]),
                    accumulated_waiting_time_s=float(
                        values["accumulated_waiting_time"]
                    ),
                    time_loss_s=float(values["time_loss"]),
                    distance_m=float(values["distance"]),
                ),
                next_signal=self._next_signal(values["next_tls"]),
                energy=VehicleEnergyObservation(
                    fuel_rate_mg_s=max(0.0, float(values["fuel_rate"])),
                    fuel_since_last_decision_mg=tracked.fuel_interval_mg,
                    fuel_total_mg=tracked.fuel_total_mg,
                    fuel_total_ml=(
                        tracked.fuel_total_mg / type_metadata.fuel_density_mg_per_ml
                    ),
                ),
                driving_events=VehicleDrivingEventsObservation(
                    hard_braking_since_last_decision=tracked.hard_braking_interval,
                    hard_braking_total=tracked.hard_braking_total,
                ),
                leader_gap_m=leader_gap,
                follower_gap_m=follower_gap,
                time_since_last_lane_change_s=(
                    max(0.0, self._last_time - tracked.last_lane_change_time)
                    if tracked.last_lane_change_time is not None
                    else None
                ),
            )
        if reset_interval:
            for tracked in self._tracked.values():
                tracked.fuel_interval_mg = 0.0
                tracked.hard_braking_interval = 0
        return result

    def _neighbor_gaps(self) -> Mapping[str, tuple[float | None, float | None]]:
        """Derive bumper-to-bumper gaps from the subscribed lane-position cache."""
        lanes: dict[str, list[tuple[float, str, float]]] = {}
        for vehicle_id, tracked in self._tracked.items():
            if not tracked.values:
                continue
            metadata = self.vehicle_types[tracked.type_id]
            lanes.setdefault(str(tracked.values["lane_id"]), []).append(
                (
                    float(tracked.values["lane_position"]),
                    vehicle_id,
                    metadata.length_m,
                )
            )

        result = {}
        for vehicles in lanes.values():
            vehicles.sort(key=lambda item: (item[0], item[1]))
            for index, (position, vehicle_id, length) in enumerate(vehicles):
                leader_gap = None
                follower_gap = None
                if index + 1 < len(vehicles):
                    leader_position, _, leader_length = vehicles[index + 1]
                    leader_gap = max(
                        0.0,
                        leader_position - leader_length - position,
                    )
                if index > 0:
                    follower_position, _, _ = vehicles[index - 1]
                    follower_gap = max(
                        0.0,
                        position - length - follower_position,
                    )
                result[vehicle_id] = (leader_gap, follower_gap)
        return result

    def _next_signal(self, values) -> NextSignalObservation | None:
        for tls_id, _, distance, state in values:
            tls_key = str(tls_id)
            if tls_key in self.tls_to_intersection:
                return NextSignalObservation(
                    intersection_id=self.tls_to_intersection[tls_key],
                    tls_id=tls_key,
                    distance_m=float(distance),
                    state=str(state),
                )
        return None

    def contains(self, vehicle_id: str) -> bool:
        return vehicle_id in self._tracked

    def active_vehicle_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._tracked))

    def type_metadata(self, vehicle_id: str) -> VehicleTypeMetadata:
        return self.vehicle_types[self._tracked[vehicle_id].type_id]

    def speed(self, vehicle_id: str) -> float:
        return float(self._tracked[vehicle_id].values["speed"])

    def allowed_speed(self, vehicle_id: str) -> float:
        return float(self._tracked[vehicle_id].values["allowed_speed"])

    def location(self, vehicle_id: str) -> tuple[str, int]:
        road_id = str(self._tracked[vehicle_id].values["road_id"])
        return road_id, int(self._tracked[vehicle_id].values["lane_index"])

    def actual(self, vehicle_id: str) -> tuple[float, int]:
        return (
            float(self._tracked[vehicle_id].values["speed"]),
            int(self._tracked[vehicle_id].values["lane_index"]),
        )

    def runtime_fields(self, vehicle_id: str) -> Mapping[str, object] | None:
        """Return one coherent cached vehicle frame without direct SUMO getters."""

        tracked = self._tracked.get(vehicle_id)
        if tracked is None or not tracked.values:
            return None
        metadata = self.vehicle_types[tracked.type_id]
        next_signal = self._next_signal(tracked.values["next_tls"])
        return {
            "type_id": tracked.type_id,
            "position": tracked.values["position"],
            "speed": float(tracked.values["speed"]),
            "angle": float(tracked.values["angle"]),
            "road_id": str(tracked.values["road_id"]),
            "lane_id": str(tracked.values["lane_id"]),
            "acceleration": float(tracked.values["acceleration"]),
            "lane_index": int(tracked.values["lane_index"]),
            "lane_position": float(tracked.values["lane_position"]),
            "allowed_speed": float(tracked.values["allowed_speed"]),
            "route_id": str(tracked.values["route_id"]),
            "route_index": int(tracked.values["route_index"]),
            "waiting_time": float(tracked.values["accumulated_waiting_time"]),
            "time_loss": float(tracked.values["time_loss"]),
            "distance": float(tracked.values["distance"]),
            "fuel_rate_mg_s": max(0.0, float(tracked.values["fuel_rate"])),
            "fuel_total_mg": tracked.fuel_total_mg,
            "fuel_total_ml": tracked.fuel_total_mg / metadata.fuel_density_mg_per_ml,
            "hard_braking_events": tracked.hard_braking_total,
            "next_intersection_id": (
                next_signal.intersection_id if next_signal is not None else None
            ),
        }

    def totals(self) -> tuple[float, float, int]:
        """Return monotonic sampled estimates; tripinfo owns final totals."""

        return self._sampled_fuel_mg, self._sampled_fuel_ml, self._sampled_braking

    def lane_vehicle_samples_by_lane(
        self,
    ) -> dict[str, tuple[tuple[float, float, float, float], ...]]:
        """One in-memory pass over cached telemetry; not a TraCI traversal."""

        grouped: dict[str, list[tuple[float, float, float, float]]] = {}
        for tracked in self._tracked.values():
            values = tracked.values
            if not values:
                continue
            lane_id = str(values["lane_id"] or "")
            if not lane_id:
                continue
            metadata = self.vehicle_types[tracked.type_id]
            grouped.setdefault(lane_id, []).append(
                (
                    float(values["lane_position"]),
                    float(values["speed"]),
                    metadata.length_m,
                    metadata.min_gap_m,
                )
            )
        return {lane_id: tuple(items) for lane_id, items in grouped.items()}

    def lane_vehicle_samples(
        self, lane_id: str
    ) -> tuple[tuple[float, float, float, float], ...]:
        """Return lane position, speed, vehicle length and minimum gap from cache."""
        result = []
        for tracked in self._tracked.values():
            values = tracked.values
            if not values or str(values["lane_id"]) != lane_id:
                continue
            metadata = self.vehicle_types[tracked.type_id]
            result.append(
                (
                    float(values["lane_position"]),
                    float(values["speed"]),
                    metadata.length_m,
                    metadata.min_gap_m,
                )
            )
        return tuple(result)

    def default_vehicle_space(self) -> float:
        if not self.vehicle_types:
            return 7.5
        return sum(
            item.length_m + item.min_gap_m for item in self.vehicle_types.values()
        ) / len(self.vehicle_types)


class StoppedLaneChangeGuard:
    """Disable autonomous SUMO lane changes while official vehicles are stopped."""

    def __init__(
        self,
        traci,
        telemetry: VehicleTelemetryTracker,
        *,
        stopped_speed_epsilon_mps: float = STOPPED_SPEED_EPSILON_MPS,
    ) -> None:
        if stopped_speed_epsilon_mps < 0:
            raise ValueError("stopped_speed_epsilon_mps cannot be negative.")
        vehicle = traci.vehicle
        if not hasattr(vehicle, "getLaneChangeMode") or not hasattr(
            vehicle, "setLaneChangeMode"
        ):
            raise RuntimeError(
                "SUMO TraCI vehicle lane-change mode APIs are unavailable."
            )
        self.traci = traci
        self.telemetry = telemetry
        self.stopped_speed_epsilon_mps = float(stopped_speed_epsilon_mps)
        self._locked_modes: dict[str, int] = {}

    def tick(self) -> None:
        active_ids = set(self.telemetry.active_vehicle_ids())
        for vehicle_id in sorted(set(self._locked_modes) - active_ids):
            self._locked_modes.pop(vehicle_id, None)
        for vehicle_id in sorted(active_ids):
            speed = self.telemetry.speed(vehicle_id)
            if speed <= self.stopped_speed_epsilon_mps:
                if vehicle_id not in self._locked_modes:
                    self._lock(vehicle_id)
            elif vehicle_id in self._locked_modes:
                self._restore(vehicle_id)

    def _lock(self, vehicle_id: str) -> None:
        try:
            raw_mode = self.traci.vehicle.getLaneChangeMode(vehicle_id)
            original_mode = (
                DEFAULT_LANE_CHANGE_MODE if raw_mode is None else int(raw_mode)
            )
            self.traci.vehicle.setLaneChangeMode(
                vehicle_id, STOPPED_LANE_CHANGE_MODE
            )
        except Exception:
            return
        self._locked_modes[vehicle_id] = original_mode

    def _restore(self, vehicle_id: str) -> None:
        original_mode = self._locked_modes.pop(vehicle_id)
        try:
            self.traci.vehicle.setLaneChangeMode(vehicle_id, original_mode)
        except Exception:
            return


class VehicleActionController:
    """Validate and apply one-decision-period speed and lane-change leases."""

    def __init__(self, traci, telemetry: VehicleTelemetryTracker) -> None:
        self.traci = traci
        self.telemetry = telemetry
        self._previous_step_id: int | None = None
        self._leases: dict[str, VehicleAction] = {}

    def previous_results(self) -> PreviousActionResults:
        results = {}
        for vehicle_id, action in sorted(self._leases.items()):
            requested = {}
            if action.target_speed_mps is not None:
                requested["target_speed_mps"] = action.target_speed_mps
            if action.target_lane_index is not None:
                requested["target_lane_index"] = action.target_lane_index
            if self.telemetry.contains(vehicle_id):
                actual_speed, actual_lane = self.telemetry.actual(vehicle_id)
                speed_status = "applied" if action.target_speed_mps is not None else None
                lane_status = None
                if action.target_lane_index is not None:
                    lane_status = (
                        "completed"
                        if actual_lane == action.target_lane_index
                        else "not_completed"
                    )
                results[vehicle_id] = PreviousVehicleActionResult(
                    requested=requested,
                    actual_speed_mps=actual_speed,
                    actual_lane_index=actual_lane,
                    speed_status=speed_status,
                    lane_change_status=lane_status,
                )
            else:
                results[vehicle_id] = PreviousVehicleActionResult(
                    requested=requested,
                    actual_speed_mps=None,
                    actual_lane_index=None,
                    speed_status=(
                        "vehicle_arrived" if action.target_speed_mps is not None else None
                    ),
                    lane_change_status=(
                        "vehicle_arrived" if action.target_lane_index is not None else None
                    ),
                )
        return PreviousActionResults(self._previous_step_id, results)

    def validate(self, raw_actions: object) -> Mapping[str, VehicleAction]:
        if not isinstance(raw_actions, Mapping):
            raise TypeError("Vehicle actions must be an object keyed by vehicle ID.")
        result = {}
        for raw_vehicle_id, raw_action in raw_actions.items():
            vehicle_id = str(raw_vehicle_id)
            if not self.telemetry.contains(vehicle_id):
                raise ValueError(f"Algorithm returned unknown vehicle {vehicle_id!r}.")
            if not isinstance(raw_action, Mapping):
                raise TypeError(f"Action for vehicle {vehicle_id} must be an object.")
            unknown = set(raw_action) - {"target_speed_mps", "target_lane_index"}
            if unknown:
                raise ValueError(
                    f"Action for vehicle {vehicle_id} has unknown fields: {sorted(unknown)}"
                )
            if not raw_action:
                raise ValueError(f"Action for vehicle {vehicle_id} cannot be empty.")
            speed = self._validate_speed(vehicle_id, raw_action.get("target_speed_mps"))
            lane = self._validate_lane(vehicle_id, raw_action.get("target_lane_index"))
            self._validate_stopped_lane_change(vehicle_id, speed, lane)
            if speed is None and lane is None:
                raise ValueError(
                    f"Action for vehicle {vehicle_id} must set at least one target."
                )
            result[vehicle_id] = VehicleAction(speed, lane)
        return result

    def _validate_speed(self, vehicle_id: str, value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"target_speed_mps for {vehicle_id} must be a number.")
        speed = float(value)
        allowed = self.telemetry.allowed_speed(vehicle_id)
        if not math.isfinite(speed) or speed < 0 or speed > allowed + 1e-9:
            raise ValueError(
                f"target_speed_mps for {vehicle_id} must be between 0 and {allowed:g}."
            )
        return speed

    def _validate_lane(self, vehicle_id: str, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"target_lane_index for {vehicle_id} must be an integer.")
        road_id, _ = self.telemetry.location(vehicle_id)
        if road_id.startswith(":"):
            raise ValueError(f"Vehicle {vehicle_id} cannot change lanes on an internal edge.")
        lane_count = int(self.traci.edge.getLaneNumber(road_id))
        if value < 0 or value >= lane_count:
            raise ValueError(
                f"target_lane_index for {vehicle_id} must be between 0 and {lane_count - 1}."
            )
        lane_id = f"{road_id}_{value}"
        vehicle_class = self.telemetry.type_metadata(vehicle_id).vehicle_class
        allowed = tuple(self.traci.lane.getAllowed(lane_id))
        disallowed = tuple(self.traci.lane.getDisallowed(lane_id))
        if (
            (allowed and "all" not in allowed and vehicle_class not in allowed)
            or "all" in disallowed
            or vehicle_class in disallowed
        ):
            raise ValueError(f"Lane {lane_id} does not allow {vehicle_class} vehicles.")
        return value

    def _validate_stopped_lane_change(
        self,
        vehicle_id: str,
        target_speed_mps: float | None,
        target_lane_index: int | None,
    ) -> None:
        if target_lane_index is None:
            return
        if self.telemetry.speed(vehicle_id) <= STOPPED_SPEED_EPSILON_MPS:
            raise ValueError(f"Vehicle {vehicle_id} cannot change lanes while stopped.")
        if (
            target_speed_mps is not None
            and target_speed_mps <= STOPPED_SPEED_EPSILON_MPS
        ):
            raise ValueError(f"Vehicle {vehicle_id} cannot change lanes while stopped.")

    def apply(
        self,
        step_id: int,
        actions: Mapping[str, VehicleAction],
        decision_interval: float,
    ) -> None:
        for vehicle_id, previous in tuple(self._leases.items()):
            replacement = actions.get(vehicle_id)
            if (
                previous.target_speed_mps is not None
                and (replacement is None or replacement.target_speed_mps is None)
                and self.telemetry.contains(vehicle_id)
            ):
                self.traci.vehicle.setSpeed(vehicle_id, -1)
        for vehicle_id, action in actions.items():
            if action.target_speed_mps is not None:
                self.traci.vehicle.setSpeed(vehicle_id, action.target_speed_mps)
            if action.target_lane_index is not None:
                self.traci.vehicle.changeLane(
                    vehicle_id, action.target_lane_index, float(decision_interval)
                )
        self._previous_step_id = int(step_id)
        self._leases = dict(actions)

    def release(self) -> None:
        for vehicle_id, action in tuple(self._leases.items()):
            if action.target_speed_mps is not None and self.telemetry.contains(vehicle_id):
                self.traci.vehicle.setSpeed(vehicle_id, -1)
        self._leases.clear()

    def current_action(self, vehicle_id: str) -> VehicleAction | None:
        return self._leases.get(vehicle_id)

    def speed_control_summary(
        self, lane_id: str
    ) -> tuple[int, float | None, float | None]:
        targets = []
        for vehicle_id, action in self._leases.items():
            if action.target_speed_mps is None or not self.telemetry.contains(vehicle_id):
                continue
            road_id, lane_index = self.telemetry.location(vehicle_id)
            if f"{road_id}_{lane_index}" == lane_id:
                targets.append(action.target_speed_mps)
        if not targets:
            return 0, None, None
        return len(targets), min(targets), sum(targets) / len(targets)
