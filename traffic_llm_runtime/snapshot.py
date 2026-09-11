"""Compact live snapshot summaries for Traffic-Qwen observations."""

from __future__ import annotations

from typing import Any

from simulation.sumo.engine.session import SimulationSnapshot


def compact_snapshot_summary(snapshot: SimulationSnapshot) -> dict[str, Any]:
    intersections: dict[str, Any] = {}
    lane_to_iid: dict[str, str] = {}
    intersection_stats: dict[str, dict[str, float]] = {}
    for iid, i_obs in snapshot.intersections.items():
        lanes = {}
        incoming_waiting = 0.0
        incoming_count = 0.0
        outgoing_count = 0.0
        speed_w = 0.0
        speed_n = 0.0
        for lane_id, lane in i_obs.lanes.items():
            lane_to_iid[str(lane_id)] = str(iid)
            lanes[str(lane_id)] = {
                "vehicle_count": lane.vehicle_count,
                "halting_count": lane.halting_count,
                "mean_speed": lane.mean_speed,
                "occupancy": lane.occupancy,
                "queue_length_m": lane.queue_length_m,
                "lane_length_m": lane.lane_length_m,
                "waiting_time": lane.waiting_time,
                "role": lane.role,
            }
            role = str(lane.role or "")
            if role == "outgoing":
                outgoing_count += float(lane.vehicle_count or 0)
            if role in {"incoming", "both", ""}:
                incoming_count += float(lane.vehicle_count or 0)
                incoming_waiting += float(lane.waiting_time or 0)
                if lane.mean_speed is not None:
                    weight = max(float(lane.vehicle_count or 0), 1.0)
                    speed_w += float(lane.mean_speed) * weight
                    speed_n += weight
        intersections[str(iid)] = {
            "current_phase": i_obs.current_phase,
            "pending_phase": i_obs.pending_phase,
            "stage": i_obs.stage,
            "lanes": lanes,
        }
        intersection_stats[str(iid)] = {
            "incoming_vehicle_count": incoming_count,
            "outgoing_vehicle_count": outgoing_count,
            "incoming_waiting_time": incoming_waiting,
            "hard_braking_events": 0.0,
            "incoming_mean_speed_mps": None if speed_n <= 0 else speed_w / speed_n,
        }
    for vehicle in snapshot.vehicles or ():
        iid = lane_to_iid.get(str(vehicle.lane_id))
        if iid is None and vehicle.next_intersection_id:
            iid = str(vehicle.next_intersection_id)
        if iid in intersection_stats:
            intersection_stats[iid]["hard_braking_events"] += float(
                vehicle.hard_braking_events or 0
            )
    return {
        "elapsed_seconds": snapshot.elapsed_seconds,
        "metrics": {
            "active_vehicles": snapshot.metrics.active_vehicles,
            "departed_vehicles": snapshot.metrics.departed_vehicles,
            "arrived_vehicles": snapshot.metrics.arrived_vehicles,
            "halting_vehicles": snapshot.metrics.halting_vehicles,
            "mean_speed": snapshot.metrics.mean_speed,
            "total_waiting_time": snapshot.metrics.total_waiting_time,
            "hard_braking_events": snapshot.metrics.hard_braking_events,
        },
        "intersections": intersections,
        "intersection_stats": intersection_stats,
        "events": [
            {
                "event_id": item.event_id,
                "event_type": item.event_type,
                "state": item.state,
                "start_seconds": item.start_seconds,
                "end_seconds": item.end_seconds,
                "error": item.error,
            }
            for item in snapshot.events
        ],
    }
