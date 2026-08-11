"""Protocol 2.0 live collector for traffic and safety evaluation metrics.

The collector only consumes algorithm-side ``initialize``/``step``/``finish``
payloads.  It never calls TraCI and does not require a SUMO-side code change.
Unavailable measurements are returned as ``None`` rather than a misleading
numeric zero.
"""

from __future__ import annotations

import os
import re
import subprocess

import numpy as np
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from .safety import SafetyExposureTracker


FUEL_POWERTRAINS = frozenset({"gasoline", "diesel", "hybrid"})
FUEL_TELEMETRY_UNITS = frozenset({"auto", "protocol_ml", "legacy_ml_as_mg"})
_AUTO_UNIT_CACHE: Optional[tuple[str, Optional[str]]] = None


def _resolve_fuel_telemetry_unit(requested: str) -> tuple[str, Optional[str]]:
    global _AUTO_UNIT_CACHE
    override = os.environ.get("EVALUATION_FUEL_TELEMETRY_UNIT", requested)
    unit = str(override).strip().lower()
    if unit not in FUEL_TELEMETRY_UNITS:
        raise ValueError(
            "EVALUATION_FUEL_TELEMETRY_UNIT must be auto, protocol_ml or "
            "legacy_ml_as_mg"
        )
    if unit != "auto":
        return unit, None
    if _AUTO_UNIT_CACHE is not None:
        return _AUTO_UNIT_CACHE
    try:
        completed = subprocess.run(
            ["sumo", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        match = re.search(r"Version\s+(\d+)\.(\d+)", completed.stdout)
    except (OSError, subprocess.SubprocessError):
        match = None
    if match is None:
        _AUTO_UNIT_CACHE = (
            "protocol_ml",
            "无法检测 SUMO 版本；按 Protocol 2.0 的 fuel_total_ml 解释燃油数据。",
        )
        return _AUTO_UNIT_CACHE
    version = (int(match.group(1)), int(match.group(2)))
    if version < (1, 14):
        _AUTO_UNIT_CACHE = (
            "legacy_ml_as_mg",
            f"检测到 SUMO {version[0]}.{version[1]}：按 1.14 以前的 mL/s 单位修正燃油遥测。",
        )
        return _AUTO_UNIT_CACHE
    _AUTO_UNIT_CACHE = ("protocol_ml", None)
    return _AUTO_UNIT_CACHE


def _rounded(value: Optional[float], digits: int) -> Optional[float]:
    return None if value is None else round(value, digits)


@dataclass
class EvalResult:
    """Metrics for one simulation episode."""

    algorithm: str = ""
    avg_travel_time_s: Optional[float] = None
    avg_waiting_time_s: Optional[float] = None
    avg_queue_length_veh: Optional[float] = None
    throughput_veh_per_h: Optional[float] = None
    avg_decision_latency_ms: Optional[float] = None
    decision_latency_p95_ms: Optional[float] = None
    fuel_intensity_L_per_100km: Optional[float] = None
    emergency_braking_exposure_per_1000: Optional[float] = None
    controlled_intersection_passages: int = 0
    emergency_braking_events: int = 0
    controlled_avg_waiting_time_s: Optional[float] = None
    controlled_waiting_availability: Optional[Dict[str, Any]] = None
    emergency_braking_availability: Optional[Dict[str, Any]] = None
    departed: int = 0
    arrived: int = 0
    all_waiting_total_s: Optional[float] = None
    unfinished_waiting_total_s: Optional[float] = None
    end_waiting_total_s: Optional[float] = None
    end_queue_veh: Optional[float] = None
    ai_frame_interval_seconds: Optional[float] = None
    simulation_duration_s: Optional[float] = None
    departed_count: Optional[int] = None
    arrived_count: Optional[int] = None
    trip_records: Optional[int] = None
    availability: Dict[str, str] = field(default_factory=dict)
    provenance: Dict[str, str] = field(default_factory=dict)
    fuel_audit: Optional[Dict[str, Any]] = None
    metric_sources: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "avg_travel_time_s": _rounded(self.avg_travel_time_s, 2),
            "avg_waiting_time_s": _rounded(self.avg_waiting_time_s, 2),
            "avg_queue_length_veh": _rounded(self.avg_queue_length_veh, 2),
            "throughput_veh_per_h": _rounded(self.throughput_veh_per_h, 1),
            "avg_decision_latency_ms": _rounded(
                self.avg_decision_latency_ms, 3
            ),
            "decision_latency_p95_ms": _rounded(
                self.decision_latency_p95_ms, 3
            ),
            "fuel_intensity_L_per_100km": _rounded(
                self.fuel_intensity_L_per_100km, 2
            ),
            "emergency_braking_exposure_per_1000": _rounded(
                self.emergency_braking_exposure_per_1000, 2
            ),
            "controlled_intersection_passages": (
                self.controlled_intersection_passages
            ),
            "emergency_braking_events": self.emergency_braking_events,
            "controlled_avg_waiting_time_s": _rounded(
                self.controlled_avg_waiting_time_s, 2
            ),
            "controlled_waiting_availability": (
                None
                if self.controlled_waiting_availability is None
                else dict(self.controlled_waiting_availability)
            ),
            "emergency_braking_availability": (
                None
                if self.emergency_braking_availability is None
                else dict(self.emergency_braking_availability)
            ),
            "departed": self.departed,
            "arrived": self.arrived,
            "all_waiting_total_s": _rounded(self.all_waiting_total_s, 2),
            "unfinished_waiting_total_s": _rounded(
                self.unfinished_waiting_total_s, 2
            ),
            "end_waiting_total_s": _rounded(self.end_waiting_total_s, 2),
            "end_queue_veh": _rounded(self.end_queue_veh, 2),
            "simulation_duration_s": _rounded(self.simulation_duration_s, 2),
            "ai_frame_interval_seconds": (
                None
                if self.ai_frame_interval_seconds is None
                else round(self.ai_frame_interval_seconds, 6)
            ),
            "departed_count": (
                self.departed_count
                if self.departed_count is not None
                else self.departed
            ),
            "arrived_count": (
                self.arrived_count
                if self.arrived_count is not None
                else self.arrived
            ),
            "trip_records": self.trip_records,
            "passage_count": self.controlled_intersection_passages,
            "emergency_braking_event_count": self.emergency_braking_events,
            "availability": dict(self.availability),
            "provenance": dict(self.provenance),
            "metric_sources": dict(self.metric_sources),
            "warnings": list(self.warnings),
        }


class HttpMetricsCollector:
    """Collect live metrics from Protocol 2.0 dictionaries.

    Per-vehicle travel/waiting values use the latest snapshot before a vehicle
    disappears.  The collector reports them only when interval arrival counts
    and the final arrival total prove that every completed trip was observed.
    """

    def __init__(
        self, algorithm: str = "", *, fuel_telemetry_unit: str = "auto"
    ) -> None:
        self._algorithm = algorithm
        self._fuel_telemetry_unit, self._fuel_unit_warning = (
            _resolve_fuel_telemetry_unit(fuel_telemetry_unit)
        )
        self._warnings: List[str] = []
        self._incoming_lanes: Dict[str, tuple[str, ...]] = {}
        self._powertrain_by_type: Dict[str, str] = {}
        self._active: Dict[str, Dict[str, Any]] = {}
        self._closed: List[Dict[str, Any]] = []
        self._arrived: List[Dict[str, Any]] = []
        self._seen_vehicle_ids: set[str] = set()
        self._seen_fuel_ids: set[str] = set()
        self._queue_samples: List[float] = []
        self._latency_samples: List[float] = []
        self._arrival_tracking_complete = True
        self._observer_frames_dropped = 0
        self._last_step_time: Optional[float] = None
        self._total_departed = 0
        self._total_arrived = 0
        self._final_sim_time = 0.0
        self._final_fuel_ml = 0.0
        self._final_fuel_mg = 0.0
        self._last_queue_mean: Optional[float] = None
        self._last_queue_time: Optional[float] = None
        self._frame_times: List[float] = []
        self._frame_deltas: List[float] = []
        self._safety = SafetyExposureTracker()
        self._finished = False

    def _warn(self, message: str) -> None:
        if message not in self._warnings:
            self._warnings.append(message)

    def on_initialize(self, body: Dict[str, Any]) -> None:
        """Reset state and cache lane/vehicle-type metadata."""

        self._warnings.clear()
        self._incoming_lanes = {
            str(intersection_id): tuple(
                str(lane_id) for lane_id in metadata.get("incoming_lanes", ())
            )
            for intersection_id, metadata in body.get("intersections", {}).items()
        }
        self._powertrain_by_type = {
            str(type_id): str(metadata.get("powertrain", "")).lower()
            for type_id, metadata in body.get("vehicle_types", {}).items()
        }
        self._active.clear()
        self._closed.clear()
        self._arrived.clear()
        self._seen_vehicle_ids.clear()
        self._seen_fuel_ids.clear()
        self._queue_samples.clear()
        self._last_queue_mean = None
        self._last_queue_time = None
        self._frame_times.clear()
        self._frame_deltas.clear()
        self._latency_samples.clear()
        self._arrival_tracking_complete = True
        self._observer_frames_dropped = 0
        self._last_step_time = None
        self._total_departed = 0
        self._total_arrived = 0
        self._final_sim_time = 0.0
        self._final_fuel_ml = 0.0
        self._final_fuel_mg = 0.0
        self._safety.initialize(body)
        self._finished = False

        if not self._incoming_lanes:
            self._warn("初始化数据缺少 incoming_lanes，平均排队长度不可计算。")
        if not self._powertrain_by_type:
            self._warn("初始化数据缺少 vehicle_types.powertrain，燃油强度不可计算。")
        if self._fuel_unit_warning:
            self._warn(self._fuel_unit_warning)

    def record_latency(self, ms: float) -> None:
        """Record one pure algorithm-computation latency sample in ms."""

        value = float(ms)
        if value >= 0:
            self._latency_samples.append(value)

    def _record_queue(
        self, body: Mapping[str, Any], sim_time: float
    ) -> None:
        lane_values: List[float] = []
        intersections = body.get("intersections", {})
        for intersection_id, incoming_lane_ids in self._incoming_lanes.items():
            lanes = intersections.get(intersection_id, {}).get("lanes", {})
            for lane_id in incoming_lane_ids:
                if lane_id in lanes:
                    lane_values.append(
                        float(lanes[lane_id].get("halting_count", 0.0))
                    )
        if lane_values:
            self._queue_samples.append(sum(lane_values) / len(lane_values))
            self._last_queue_mean = self._queue_samples[-1]
            self._last_queue_time = sim_time
        else:
            self._warn("实时帧没有可用的进口车道数据，部分排队样本缺失。")

    @staticmethod
    def _new_vehicle_record(
        sim_time: float,
        vehicle_id: str,
        vehicle_data: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return {
            "vehicle_id": vehicle_id,
            "active": True,
            "first_seen_s": sim_time,
            "last_seen_s": sim_time,
            "type_id": str(vehicle_data.get("type_id", "")),
            "last_waiting": 0.0,
            "last_time_loss": 0.0,
            "last_distance": 0.0,
            "last_fuel_ml": 0.0,
        }

    def _update_vehicle_record(
        self,
        record: Dict[str, Any],
        sim_time: float,
        vehicle_data: Mapping[str, Any],
    ) -> None:
        traffic = vehicle_data.get("traffic", {})
        energy = vehicle_data.get("energy", {})
        record.update(
            {
                "last_seen_s": sim_time,
                "type_id": str(vehicle_data.get("type_id", record["type_id"])),
                "last_waiting": float(
                    traffic.get("accumulated_waiting_time_s", 0.0)
                ),
                "last_time_loss": float(traffic.get("time_loss_s", 0.0)),
                "last_distance": float(traffic.get("distance_m", 0.0)),
                "last_fuel_ml": float(
                    energy.get(
                        "fuel_total_mg"
                        if self._fuel_telemetry_unit == "legacy_ml_as_mg"
                        else "fuel_total_ml",
                        0.0,
                    )
                ),
            }
        )

    def on_step(self, body: Dict[str, Any]) -> None:
        """Record one decision observation or high-frequency observer frame."""

        if self._finished:
            raise RuntimeError("Cannot add evaluation samples after finish.")
        sim_time = float(body.get("simulation_time", 0.0))
        if self._last_step_time is not None:
            if sim_time < self._last_step_time:
                raise ValueError("Evaluation frame time cannot move backwards.")
            if sim_time == self._last_step_time:
                return
        self._last_step_time = sim_time
        if self._frame_times:
            delta = sim_time - self._frame_times[-1]
            if delta > 1e-9:
                self._frame_deltas.append(delta)
        self._frame_times.append(sim_time)
        current_vehicles: Dict[str, Any] = body.get("vehicles", {})
        current_ids = set(current_vehicles)
        previous_ids = set(self._active)

        self._record_queue(body, sim_time)
        self._safety.observe(current_vehicles)

        disappeared = previous_ids - current_ids
        interval_arrived = body.get("traffic", {}).get("arrived_vehicles")
        if interval_arrived is None:
            self._arrival_tracking_complete = False
            self._warn("实时帧缺少 arrived_vehicles，完成车辆统计不可验证。")
        elif int(interval_arrived) != len(disappeared):
            self._arrival_tracking_complete = False
            self._warn(
                "到达计数与逐车消失数量不一致，存在未完整观测或非到达移除车辆。"
            )

        for vehicle_id in disappeared:
            record = self._active.pop(vehicle_id)
            record["active"] = False
            record["disappeared_s"] = sim_time
            self._closed.append(record)
            self._arrived.append(record)

        for vehicle_id, vehicle_data in current_vehicles.items():
            if vehicle_id not in self._active:
                self._active[vehicle_id] = self._new_vehicle_record(
                    sim_time, str(vehicle_id), vehicle_data
                )
                if (
                    str(
                        self._powertrain_by_type.get(
                            str(vehicle_data.get("type_id", "")), ""
                        )
                    ).lower()
                    in FUEL_POWERTRAINS
                ):
                    self._seen_fuel_ids.add(str(vehicle_id))
            self._update_vehicle_record(
                self._active[vehicle_id], sim_time, vehicle_data
            )
            self._seen_vehicle_ids.add(vehicle_id)

    def on_finish(self, body: Dict[str, Any]) -> None:
        """Record final totals. Calling twice with the same episode is safe."""

        self._total_departed = int(body.get("departed_vehicles", 0))
        self._total_arrived = int(body.get("arrived_vehicles", 0))
        self._final_sim_time = float(body.get("simulation_time", 0.0))
        self._final_fuel_ml = float(body.get("fuel_consumed_ml", 0.0))
        self._final_fuel_mg = float(body.get("fuel_consumed_mg", 0.0))
        frame_stats = body.get("observer_frames", {})
        dropped = int(frame_stats.get("dropped", 0)) if frame_stats else 0
        if dropped:
            self._observer_frames_dropped = max(
                self._observer_frames_dropped, dropped
            )
            self._warn(f"高频评价观察器丢弃了 {dropped} 帧，时序指标不完整。")
        self._finished = True

    def _arrival_metrics_are_complete(self) -> bool:
        complete = (
            self._arrival_tracking_complete
            and self._observer_frames_dropped == 0
            and len(self._arrived) == self._total_arrived
        )
        if not complete and self._total_arrived:
            self._warn(
                "未完整观测全部到达车辆，平均行程时间和等待时间记为不可用。"
            )
        return complete

    def _fuel_metric(self) -> Optional[float]:
        if self._observer_frames_dropped:
            self._warn("高频观察数据不完整，燃油强度记为不可用。")
            return None
        records = self._closed + list(self._active.values())
        if self._total_departed > len(self._seen_vehicle_ids):
            self._warn("未完整观测全部出发车辆，燃油强度记为不可用。")
            return None

        fuel_records: List[Dict[str, Any]] = []
        for record in records:
            powertrain = self._powertrain_by_type.get(str(record["type_id"]))
            if powertrain is None:
                type_id = str(record["type_id"])
                self._warn(
                    f"车辆类型 {type_id!r} 缺少 powertrain，燃油强度记为不可用。"
                )
                return None
            if powertrain in FUEL_POWERTRAINS:
                fuel_records.append(record)

        total_distance_m = sum(
            float(record["last_distance"]) for record in fuel_records
        )
        if total_distance_m <= 0:
            self._warn("没有可用的燃油车辆行驶里程，燃油强度记为不可用。")
            return None

        # D-2026-08-07-01: official fuel = event-level finish totals.
        if self._fuel_telemetry_unit == "legacy_ml_as_mg":
            total_fuel_ml = self._final_fuel_mg
        else:
            total_fuel_ml = self._final_fuel_ml
        if total_fuel_ml <= 0:
            self._warn(
                "结束汇总未提供事件级燃油总量（finish totals），燃油强度记为不可用。"
            )
            return None

        per_vehicle_fuel_ml = sum(
            float(record["last_fuel_ml"]) for record in fuel_records
        )
        if per_vehicle_fuel_ml > 0:
            residual = abs(per_vehicle_fuel_ml - total_fuel_ml) / max(
                1.0, total_fuel_ml
            )
            if residual > 0.05:
                self._warn(
                    "逐车燃油累计值与事件级 totals 偏差 "
                    f"{residual * 100.0:.1f}%；正式口径使用 totals（决策 D-2026-08-07-01）。"
                )

        return (total_fuel_ml / 1000.0) / (total_distance_m / 100000.0)

    @staticmethod
    def _availability(
        *,
        frames_complete: bool,
        tracking_complete: bool,
        passages: int,
        metric_name: str,
    ) -> Dict[str, Any]:
        if not frames_complete:
            return {"status": "unavailable", "reason": "observer frames dropped"}
        if not tracking_complete:
            return {"status": "unavailable", "reason": "tracking fields incomplete"}
        if passages <= 0:
            return {
                "status": "unavailable",
                "reason": "no controlled-intersection passages observed",
            }
        return {"status": "available", "reason": None}

    def fuel_audit_snapshot(self) -> Dict[str, Any]:
        """Expose the per-vehicle fuel records and totals for the M0 audit."""
        records = self._closed + list(self._active.values())
        per_vehicle = []
        for record in records:
            type_id = str(record.get("type_id", ""))
            per_vehicle.append(
                {
                    "vehicle_id": str(record.get("vehicle_id", "")),
                    "type_id": type_id,
                    "powertrain": str(
                        self._powertrain_by_type.get(type_id, "")
                    ),
                    "fuel_ml": float(record.get("last_fuel_ml", 0.0)),
                    "distance_m": float(record.get("last_distance", 0.0)),
                    "active": bool(record.get("active", False)),
                }
            )
        return {
            "per_vehicle": per_vehicle,
            "finish_fuel_ml": self._final_fuel_ml,
            "finish_fuel_mg": self._final_fuel_mg,
            "fuel_telemetry_unit": self._fuel_telemetry_unit,
            "departed_fuel_vehicles": len(self._seen_fuel_ids),
            "active_records": len(self._active),
        }

    def result(self) -> EvalResult:
        """Compute traffic and safety metrics without changing prior definitions."""

        result = EvalResult(
            algorithm=self._algorithm,
            departed=self._total_departed,
            arrived=self._total_arrived,
        )
        arrival_complete = self._arrival_metrics_are_complete()
        if arrival_complete and self._arrived:
            self._warn(
                "实时接口不含精确 depart/arrival 时刻；平均行程时间和等待时间等待 TripInfo 回填。"
            )

        if self._observer_frames_dropped:
            self._warn("高频观察数据不完整，平均排队长度记为不可用。")
        elif self._queue_samples:
            result.avg_queue_length_veh = sum(self._queue_samples) / len(
                self._queue_samples
            )
            result.metric_sources[
                "avg_queue_length_veh"
            ] = "incoming_lane_halting_count"
            result.provenance[
                "avg_queue_length_veh"
            ] = "incoming_lane_halting_count"
            result.availability["avg_queue_length_veh"] = "available"

        if self._final_sim_time > 0:
            result.throughput_veh_per_h = (
                self._total_arrived / self._final_sim_time * 3600.0
            )
            result.metric_sources["throughput_veh_per_h"] = "finish_totals"

        # Unified per-run schema fields (Task A).
        end_queue_ready = (
            self._observer_frames_dropped == 0
            and self._last_queue_mean is not None
            and self._last_queue_time is not None
            and (
                self._final_sim_time <= 0
                or abs(self._last_queue_time - self._final_sim_time) <= 1e-6
            )
        )
        if end_queue_ready:
            result.end_queue_veh = self._last_queue_mean
            result.provenance[
                "end_queue_veh"
            ] = "collector_last_observer_frame"
            result.availability["end_queue_veh"] = "available"
        elif self._last_queue_mean is not None:
            result.availability["end_queue_veh"] = "unavailable"

        if self._final_sim_time > 0:
            result.simulation_duration_s = self._final_sim_time
            result.provenance[
                "simulation_duration_s"
            ] = "collector_finish_summary"
            result.availability["simulation_duration_s"] = "available"

        if self._frame_deltas:
            first_delta = self._frame_deltas[0]
            if all(
                abs(delta - first_delta) <= 1e-6
                for delta in self._frame_deltas
            ):
                result.ai_frame_interval_seconds = first_delta
                result.provenance[
                    "ai_frame_interval_seconds"
                ] = "observed_frame_delta_s"
                result.availability["ai_frame_interval_seconds"] = "available"
            else:
                result.availability[
                    "ai_frame_interval_seconds"
                ] = "inconsistent"

        if self._latency_samples:
            result.avg_decision_latency_ms = sum(self._latency_samples) / len(
                self._latency_samples
            )
            result.metric_sources[
                "avg_decision_latency_ms"
            ] = "algorithm_perf_counter"
            result.decision_latency_p95_ms = float(
                np.percentile(self._latency_samples, 95.0)
            )

        result.fuel_intensity_L_per_100km = self._fuel_metric()
        if result.fuel_intensity_L_per_100km is not None:
            result.metric_sources[
                "fuel_intensity_L_per_100km"
            ] = (
                "tripinfo_fuel_totals_legacy_ml"
                if self._fuel_telemetry_unit == "legacy_ml_as_mg"
                else "tripinfo_fuel_totals"
            )
            result.metric_sources[
                "fuel_intensity_l_per_100km"
            ] = result.metric_sources["fuel_intensity_L_per_100km"]
            result.availability["fuel_intensity_l_per_100km"] = "available"
        else:
            result.availability["fuel_intensity_l_per_100km"] = "missing"

        result.controlled_intersection_passages = self._safety.passages
        result.emergency_braking_events = self._safety.emergency_braking_events
        result.provenance[
            "passage_count"
        ] = "protocol_controlled_intersection_passages"
        result.provenance[
            "emergency_braking_event_count"
        ] = "protocol_hard_braking_onset_delta"
        result.availability["passage_count"] = (
            "available" if self._safety.passage_tracking_complete else "unavailable"
        )
        result.availability["emergency_braking_event_count"] = (
            "available" if self._safety.braking_tracking_complete else "unavailable"
        )
        safety_frames_complete = self._observer_frames_dropped == 0
        if not safety_frames_complete:
            self._warn("高频观察数据不完整，紧急制动暴露率记为不可用。")
        elif not self._safety.passage_tracking_complete:
            self._warn("未完整观测受控路口通行次数，紧急制动暴露率记为不可用。")
        elif self._safety.passages <= 0:
            self._warn("未观测到受控路口通行，紧急制动暴露率记为不可用。")
        elif not self._safety.braking_tracking_complete:
            self._warn("紧急制动观测字段不完整，该安全暴露率记为不可用。")
        else:
            result.emergency_braking_exposure_per_1000 = (
                self._safety.emergency_braking_events
                / self._safety.passages
                * 1000.0
            )
            result.metric_sources[
                "emergency_braking_exposure_per_1000"
            ] = "protocol_hard_braking_per_controlled_intersection_passage"

        if self._observer_frames_dropped:
            result.controlled_avg_waiting_time_s = None
            result.controlled_waiting_availability = {
                "status": "unavailable", "reason": "observer frames dropped"
            }
        elif not self._safety.passage_waiting_complete:
            result.controlled_avg_waiting_time_s = None
            result.controlled_waiting_availability = {
                "status": "unavailable", "reason": "passage waiting fields incomplete"
            }
        elif not self._safety.passage_waiting_samples:
            result.controlled_avg_waiting_time_s = None
            result.controlled_waiting_availability = {
                "status": "unavailable", "reason": "no controlled passages observed"
            }
        else:
            result.controlled_avg_waiting_time_s = sum(
                self._safety.passage_waiting_samples
            ) / len(self._safety.passage_waiting_samples)
            result.controlled_waiting_availability = {
                "status": "available", "reason": None
            }
        result.emergency_braking_availability = self._availability(
            frames_complete=safety_frames_complete,
            tracking_complete=self._safety.braking_tracking_complete,
            passages=self._safety.passages,
            metric_name="emergency_braking_exposure_per_1000",
        )

        result.fuel_audit = self.fuel_audit_snapshot()
        result.warnings = list(self._warnings)
        return result
