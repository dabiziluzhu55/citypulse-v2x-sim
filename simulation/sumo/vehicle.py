"""Per-vehicle SUMO telemetry, fuel accounting and leased control actions."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

from .policy import (
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
from .vehicle_profiles import VehicleProfile


_SUBSCRIPTION_FIELDS = {
    "VAR_POSITION": "position",
    "VAR_SPEED": "speed",
    "VAR_ACCELERATION": "acceleration",
    "VAR_ANGLE": "angle",
    "VAR_ROAD_ID": "road_id",
    "VAR_LANE_ID": "lane_id",
    "VAR_LANE_INDEX": "lane_index",
    "VAR_LANEPOSITION": "lane_position",
    "VAR_ALLOWED_SPEED": "allowed_speed",
    "VAR_ROUTE_ID": "route_id",
    "VAR_ROUTE_INDEX": "route_index",
    "VAR_EDGES": "route_edges",
    "VAR_NEXT_TLS": "next_tls",
    "VAR_WAITING_TIME": "waiting_time",
    "VAR_ACCUMULATED_WAITING_TIME": "accumulated_waiting_time",
    "VAR_TIMELOSS": "time_loss",
    "VAR_DISTANCE": "distance",
    "VAR_FUELCONSUMPTION": "fuel_rate",
}


@dataclass
class _TrackedVehicle:
    type_id: str
    values: dict[str, object] = field(default_factory=dict)
    fuel_total_mg: float = 0.0
    fuel_interval_mg: float = 0.0
    hard_braking_total: int = 0
    hard_braking_interval: int = 0
    hard_braking_active: bool = False


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
    """Maintain current vehicle state while integrating step-level fuel rates."""

    def __init__(
        self,
        traci,
        vehicle_types: Mapping[str, VehicleTypeMetadata],
        tls_to_intersection: Mapping[str, str],
    ) -> None:
        self.traci = traci
        self.vehicle_types = dict(vehicle_types)
        self.tls_to_intersection = dict(tls_to_intersection)
        constants = getattr(traci, "constants", None)
        if constants is None:
            raise RuntimeError("TraCI constants are unavailable for vehicle subscriptions.")
        missing = [name for name in _SUBSCRIPTION_FIELDS if not hasattr(constants, name)]
        if missing:
            raise RuntimeError(f"SUMO lacks required vehicle telemetry constants: {missing}")
        self._variables = {
            getattr(constants, name): field_name
            for name, field_name in _SUBSCRIPTION_FIELDS.items()
        }
        self._tracked: dict[str, _TrackedVehicle] = {}
        self._last_time = 0.0
        self._completed_fuel_mg = 0.0
        self._completed_fuel_ml = 0.0
        self._completed_braking = 0

    def tick(self, elapsed: float) -> None:
        now = float(elapsed)
        if now + 1e-9 < self._last_time:
            raise ValueError("Vehicle telemetry time cannot move backwards.")
        delta = max(0.0, now - self._last_time)
        active_ids = {str(value) for value in self.traci.vehicle.getIDList()}
        for vehicle_id in sorted(active_ids - set(self._tracked)):
            type_id = str(self.traci.vehicle.getTypeID(vehicle_id))
            if type_id not in self.vehicle_types:
                continue
            self.traci.vehicle.subscribe(vehicle_id, tuple(self._variables))
            self._tracked[vehicle_id] = _TrackedVehicle(type_id=type_id)

        for vehicle_id in sorted(set(self._tracked) & active_ids):
            tracked = self._tracked[vehicle_id]
            raw = self.traci.vehicle.getSubscriptionResults(vehicle_id) or {}
            tracked.values = {
                field_name: raw[variable]
                for variable, field_name in self._variables.items()
                if variable in raw
            }
            if len(tracked.values) != len(self._variables):
                tracked.values = self._read_direct(vehicle_id)
            fuel_rate = max(0.0, float(tracked.values["fuel_rate"]))
            consumed = fuel_rate * delta
            tracked.fuel_total_mg += consumed
            tracked.fuel_interval_mg += consumed
            acceleration = float(tracked.values["acceleration"])
            threshold = self.vehicle_types[tracked.type_id].hard_braking_threshold_mps2
            braking = acceleration <= threshold
            if braking and not tracked.hard_braking_active:
                tracked.hard_braking_total += 1
                tracked.hard_braking_interval += 1
            tracked.hard_braking_active = braking

        for vehicle_id in sorted(set(self._tracked) - active_ids):
            tracked = self._tracked.pop(vehicle_id)
            metadata = self.vehicle_types[tracked.type_id]
            final_consumed = max(0.0, float(tracked.values.get("fuel_rate", 0.0))) * delta
            tracked.fuel_total_mg += final_consumed
            tracked.fuel_interval_mg += final_consumed
            self._completed_fuel_mg += tracked.fuel_total_mg
            self._completed_fuel_ml += (
                tracked.fuel_total_mg / metadata.fuel_density_mg_per_ml
            )
            self._completed_braking += tracked.hard_braking_total
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
        for vehicle_id in sorted(self._tracked):
            tracked = self._tracked[vehicle_id]
            values = tracked.values
            if not values:
                continue
            type_metadata = self.vehicle_types[tracked.type_id]
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
            )
        if reset_interval:
            for tracked in self._tracked.values():
                tracked.fuel_interval_mg = 0.0
                tracked.hard_braking_interval = 0
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

    def type_metadata(self, vehicle_id: str) -> VehicleTypeMetadata:
        return self.vehicle_types[self._tracked[vehicle_id].type_id]

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

    def totals(self) -> tuple[float, float, int]:
        fuel_mg = self._completed_fuel_mg
        fuel_ml = self._completed_fuel_ml
        braking = self._completed_braking
        for tracked in self._tracked.values():
            metadata = self.vehicle_types[tracked.type_id]
            fuel_mg += tracked.fuel_total_mg
            fuel_ml += tracked.fuel_total_mg / metadata.fuel_density_mg_per_ml
            braking += tracked.hard_braking_total
        return fuel_mg, fuel_ml, braking


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
        if (allowed and vehicle_class not in allowed) or vehicle_class in disallowed:
            raise ValueError(f"Lane {lane_id} does not allow {vehicle_class} vehicles.")
        return value

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
