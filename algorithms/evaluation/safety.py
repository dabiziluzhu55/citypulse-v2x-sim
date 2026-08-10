"""Safety exposure metrics derived from existing Protocol 2.0 frames."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


CONTROL_ZONE_DISTANCE_M = 100.0
TTC_THRESHOLD_S = 3.0
DRAC_THRESHOLD_MPS2 = 3.0


@dataclass(frozen=True)
class _VehicleState:
    vehicle_id: str
    lane_id: str
    lane_position_m: float
    speed_mps: float
    length_m: float
    zone_intersection_id: str


class SafetyExposureTracker:
    """Count safety events and controlled-intersection passage exposures.

    Severe conflicts are longitudinal conflicts between adjacent vehicles on
    the same lane.  A pair is severe when TTC is below 3 seconds or DRAC is
    above 3 m/s².  A continuously unsafe pair is counted once.  Emergency
    braking uses the cumulative onset counter already carried by Protocol 2.0.
    """

    def __init__(self) -> None:
        self._incoming_to_intersection: Dict[str, str] = {}
        self._controlled_intersections: set[str] = set()
        self._lane_lengths: Dict[str, float] = {}
        self._tls_to_intersection: Dict[str, str] = {}
        self._vehicle_lengths: Dict[str, float] = {}
        self._previous_incoming: Dict[str, str] = {}
        self._previous_zone: Dict[str, Optional[str]] = {}
        self._hard_braking_totals: Dict[str, int] = {}
        self._active_conflicts: set[tuple[str, str]] = set()
        self.passages = 0
        self.severe_conflicts = 0
        self.emergency_braking_events = 0
        self.passage_tracking_complete = True
        self.conflict_tracking_complete = True
        self.braking_tracking_complete = True
        self.passage_waiting_samples: list[float] = []
        self.passage_waiting_complete = True

    def initialize(self, metadata: Mapping[str, Any]) -> None:
        self.__init__()
        intersections = metadata.get("intersections", {})
        for intersection_id, intersection in intersections.items():
            own_id = str(intersection_id)
            self._controlled_intersections.add(own_id)
            for lane_id in intersection.get("incoming_lanes", ()):
                self._incoming_to_intersection[str(lane_id)] = own_id
            for lane_id, lane in intersection.get("lanes", {}).items():
                length = float(lane.get("length_m", lane.get("length", 0.0)))
                if length > 0:
                    self._lane_lengths[str(lane_id)] = length
            for connection in intersection.get("connections", ()):
                tls_id = str(connection.get("tls_id", ""))
                if tls_id:
                    self._tls_to_intersection[tls_id] = own_id
        self._vehicle_lengths = {
            str(type_id): float(vehicle_type.get("length_m", 0.0))
            for type_id, vehicle_type in metadata.get("vehicle_types", {}).items()
        }
        if not self._incoming_to_intersection:
            self.passage_tracking_complete = False
            self.conflict_tracking_complete = False
            self.braking_tracking_complete = False

    def _zone_intersection(
        self, vehicle: Mapping[str, Any], lane_id: str, lane_position_m: float
    ) -> Optional[str]:
        next_signal = vehicle.get("next_signal")
        if isinstance(next_signal, Mapping):
            intersection_id = str(next_signal.get("intersection_id", ""))
            distance = float(next_signal.get("distance_m", float("inf")))
            if (
                intersection_id in self._controlled_intersections
                and distance <= CONTROL_ZONE_DISTANCE_M
            ):
                return intersection_id

        intersection_id = self._incoming_to_intersection.get(lane_id)
        lane_length = self._lane_lengths.get(lane_id)
        if (
            intersection_id is not None
            and lane_length is not None
            and lane_length - lane_position_m <= CONTROL_ZONE_DISTANCE_M
        ):
            return intersection_id

        road_id = str(vehicle.get("location", {}).get("road_id", ""))
        if road_id.startswith(":"):
            for tls_id, own_id in self._tls_to_intersection.items():
                if road_id == f":{tls_id}" or road_id.startswith(f":{tls_id}_"):
                    return own_id
        return None

    @staticmethod
    def _is_severe(leader: _VehicleState, follower: _VehicleState) -> bool:
        gap = leader.lane_position_m - leader.length_m - follower.lane_position_m
        if gap <= 0:
            return True
        closing_speed = follower.speed_mps - leader.speed_mps
        if closing_speed <= 0:
            return False
        ttc = gap / closing_speed
        drac = closing_speed * closing_speed / (2.0 * gap)
        return ttc < TTC_THRESHOLD_S or drac > DRAC_THRESHOLD_MPS2

    def observe(self, vehicles: Mapping[str, Any]) -> None:
        current_ids = {str(vehicle_id) for vehicle_id in vehicles}
        states_by_lane: Dict[str, list[_VehicleState]] = {}

        for raw_vehicle_id, vehicle in vehicles.items():
            vehicle_id = str(raw_vehicle_id)
            location = vehicle.get("location")
            if not isinstance(location, Mapping) or "lane_id" not in location:
                self.passage_tracking_complete = False
                self.conflict_tracking_complete = False
                self.braking_tracking_complete = False
                self.passage_waiting_complete = False
                continue

            lane_id = str(location["lane_id"])
            lane_position_m = float(location.get("lane_position_m", 0.0))
            current_incoming = self._incoming_to_intersection.get(lane_id)
            previous_incoming = self._previous_incoming.get(vehicle_id)
            if (
                previous_incoming is not None
                and current_incoming != previous_incoming
            ):
                self.passages += 1
                traffic = vehicle.get("traffic")
                if not isinstance(traffic, Mapping) or "accumulated_waiting_time_s" not in traffic:
                    self.passage_waiting_complete = False
                else:
                    self.passage_waiting_samples.append(
                        float(traffic["accumulated_waiting_time_s"])
                    )
            if current_incoming is None:
                self._previous_incoming.pop(vehicle_id, None)
            else:
                self._previous_incoming[vehicle_id] = current_incoming

            zone_intersection = self._zone_intersection(
                vehicle, lane_id, lane_position_m
            )
            driving_events = vehicle.get("driving_events")
            if not isinstance(driving_events, Mapping):
                self.braking_tracking_complete = False
                self.passage_waiting_complete = False
            else:
                total = int(driving_events.get("hard_braking_total", 0))
                previous_total = self._hard_braking_totals.get(vehicle_id, 0)
                if total < previous_total:
                    self.braking_tracking_complete = False
                delta = max(0, total - previous_total)
                if delta and (
                    zone_intersection is not None
                    or self._previous_zone.get(vehicle_id) is not None
                ):
                    self.emergency_braking_events += delta
                self._hard_braking_totals[vehicle_id] = total
            self._previous_zone[vehicle_id] = zone_intersection

            if zone_intersection is None:
                continue
            motion = vehicle.get("motion")
            length_m = self._vehicle_lengths.get(
                str(vehicle.get("type_id", "")), 0.0
            )
            if not isinstance(motion, Mapping) or length_m <= 0:
                self.conflict_tracking_complete = False
                continue
            states_by_lane.setdefault(lane_id, []).append(
                _VehicleState(
                    vehicle_id=vehicle_id,
                    lane_id=lane_id,
                    lane_position_m=lane_position_m,
                    speed_mps=float(motion.get("speed_mps", 0.0)),
                    length_m=length_m,
                    zone_intersection_id=zone_intersection,
                )
            )

        for vehicle_id in set(self._previous_incoming) - current_ids:
            self.passage_tracking_complete = False
            self._previous_incoming.pop(vehicle_id, None)
        for state in (
            self._previous_zone,
            self._hard_braking_totals,
        ):
            for vehicle_id in set(state) - current_ids:
                state.pop(vehicle_id, None)

        current_conflicts: set[tuple[str, str]] = set()
        for lane_states in states_by_lane.values():
            ordered = sorted(
                lane_states, key=lambda state: state.lane_position_m, reverse=True
            )
            for leader, follower in zip(ordered, ordered[1:]):
                if (
                    leader.zone_intersection_id == follower.zone_intersection_id
                    and self._is_severe(leader, follower)
                ):
                    current_conflicts.add((leader.vehicle_id, follower.vehicle_id))
        self.severe_conflicts += len(current_conflicts - self._active_conflicts)
        self._active_conflicts = current_conflicts
