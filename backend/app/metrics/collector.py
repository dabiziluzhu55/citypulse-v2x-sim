"""公共交通指标采集器：主数据源为SimulationManager推送的SimulationSnapshot

六指标口径与algorithms/evaluation对齐：
- 行程/等待：终态由TripInfo回填；进行中可为快照临时值并标记来源
- 排队：仅role==incoming进口车道，先车道均值再时间均值（veh/lane）
- 通行能力：arrived / evaluation_duration_seconds * 3600
- 决策延迟：由外部AlgorithmRuntimeStore注入；无样本为None
- 燃油强度：仅gasoline/diesel/hybrid，同批车辆分子分母一致
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from simulation.sumo.session import SimulationSnapshot

from .models import EvalResult
from .tripinfo import apply_tripinfo_completed_metrics

FUEL_POWERTRAINS = frozenset({"gasoline", "diesel", "hybrid"})


class TrafficMetricsCollector:
    """按session生命周期采集交通运行指标"""

    def __init__(self, algorithm: str = "") -> None:
        self._algorithm = algorithm
        self._active: dict[str, dict[str, Any]] = {}
        self._closed: list[dict[str, Any]] = []
        self._provisional_travel: list[float] = []
        self._provisional_waiting: list[float] = []
        self._queue_samples: list[float] = []
        self._powertrain_by_type: dict[str, str] = {}
        self._seen_vehicle_ids: set[str] = set()
        self._warnings: list[str] = []
        self._total_departed: int = 0
        self._total_arrived: int = 0
        self._final_sim_time: float = 0.0
        self._last_sim_time: float = 0.0
        self._finished: bool = False
        self._tripinfo_applied: bool = False
        self._has_incoming_lanes: bool = False
        self._missing_incoming_warned: bool = False

    def reset(self, algorithm: str = "") -> None:
        if algorithm:
            self._algorithm = algorithm
        self._active.clear()
        self._closed.clear()
        self._provisional_travel.clear()
        self._provisional_waiting.clear()
        self._queue_samples.clear()
        self._powertrain_by_type.clear()
        self._seen_vehicle_ids.clear()
        self._warnings.clear()
        self._total_departed = 0
        self._total_arrived = 0
        self._final_sim_time = 0.0
        self._last_sim_time = 0.0
        self._finished = False
        self._tripinfo_applied = False
        self._has_incoming_lanes = False
        self._missing_incoming_warned = False

    def set_powertrain_by_type(self, mapping: Mapping[str, str]) -> None:
        self._powertrain_by_type = {
            str(type_id): str(powertrain).lower()
            for type_id, powertrain in mapping.items()
        }
        if not self._powertrain_by_type:
            self._warn("初始化数据缺少 vehicle_types.powertrain，燃油强度不可计算。")

    def extend_warnings(self, messages: list[str]) -> None:
        for message in messages:
            self._warn(message)

    def observe_snapshot(self, snapshot: SimulationSnapshot) -> None:
        """从统一Snapshot取数据"""
        if self._finished:
            return
        sim_time = float(snapshot.elapsed_seconds)
        vehicles: dict[str, Mapping[str, Any]] = {}
        for vehicle in snapshot.vehicles:
            vehicles[vehicle.vehicle_id] = {
                "waiting": float(vehicle.waiting_time),
                "distance": float(vehicle.distance),
                "fuel_ml": float(vehicle.fuel_total_ml),
                "type_id": str(vehicle.type_id or ""),
            }
        incoming_halting: list[float] = []
        saw_any_lane = False
        for i_state in snapshot.intersections.values():
            for lane in i_state.lanes.values():
                saw_any_lane = True
                if str(lane.role) == "incoming":
                    incoming_halting.append(float(lane.halting_count))
        if incoming_halting:
            self._has_incoming_lanes = True
        elif saw_any_lane and not self._missing_incoming_warned:
            self._warn("快照中没有 role=incoming 的进口车道，平均排队长度不可计算。")
            self._missing_incoming_warned = True

        self._observe(
            sim_time=sim_time,
            vehicles=vehicles,
            incoming_halting=incoming_halting,
        )
        self._total_departed = int(snapshot.metrics.departed_vehicles)
        self._total_arrived = int(snapshot.metrics.arrived_vehicles)

    def finalize_from_snapshot(
        self,
        snapshot: SimulationSnapshot,
        *,
        decision_latency_ms: Optional[float] = None,
        tripinfo_path: str | Path | None = None,
    ) -> EvalResult:
        """会话结束时结算最终交通指标，并可选TripInfo回填"""
        self.observe_snapshot(snapshot)
        self._finished = True
        self._final_sim_time = float(snapshot.elapsed_seconds)
        self._last_sim_time = self._final_sim_time
        self._total_departed = int(snapshot.metrics.departed_vehicles)
        self._total_arrived = int(snapshot.metrics.arrived_vehicles)
        result = self.result(
            finished=True,
            decision_latency_ms=decision_latency_ms,
        )
        if tripinfo_path is not None:
            apply_tripinfo_completed_metrics(
                result,
                tripinfo_path,
                expected_arrived=result.arrived,
            )
            self._tripinfo_applied = (
                result.metric_sources.get("avg_travel_time_s") == "tripinfo_completed"
            )
            self._warnings = list(result.warnings)
        return result

    def _warn(self, message: str) -> None:
        if message and message not in self._warnings:
            self._warnings.append(message)

    def _observe(
        self,
        *,
        sim_time: float,
        vehicles: Mapping[str, Mapping[str, Any]],
        incoming_halting: list[float],
    ) -> None:
        if self._last_sim_time > 0 and sim_time < self._last_sim_time:
            self._warn("评价帧时间倒退，已忽略该帧。")
            return
        if sim_time == self._last_sim_time and (
            self._queue_samples or self._active or self._closed
        ):
            # 与算法侧一致：同一仿真时刻不重复采样
            return
        self._last_sim_time = sim_time
        if incoming_halting:
            self._has_incoming_lanes = True
            self._queue_samples.append(
                sum(incoming_halting) / len(incoming_halting)
            )

        for vid in vehicles:
            if vid not in self._active:
                self._active[vid] = {
                    "first_seen_s": sim_time,
                    "type_id": str(vehicles[vid].get("type_id", "")),
                    "last_waiting": 0.0,
                    "last_distance": 0.0,
                    "last_fuel_ml": 0.0,
                }

        arrived_vids = set(self._active.keys()) - set(vehicles.keys())
        for vid in arrived_vids:
            rec = self._active.pop(vid)
            travel = sim_time - float(rec["first_seen_s"])
            if travel > 0:
                self._provisional_travel.append(travel)
            self._provisional_waiting.append(float(rec["last_waiting"]))
            self._closed.append(rec)

        for vid, vdata in vehicles.items():
            if vid not in self._active:
                continue
            self._active[vid].update(
                {
                    "type_id": str(vdata.get("type_id", self._active[vid]["type_id"])),
                    "last_waiting": float(vdata.get("waiting", 0.0)),
                    "last_distance": float(vdata.get("distance", 0.0)),
                    "last_fuel_ml": float(vdata.get("fuel_ml", 0.0)),
                }
            )
            self._seen_vehicle_ids.add(vid)

    def _fuel_metric(self) -> Optional[float]:
        if not self._powertrain_by_type:
            self._warn("缺少车辆 powertrain 元数据，燃油强度不可计算。")
            return None

        records = self._closed + list(self._active.values())
        fuel_records: list[dict[str, Any]] = []
        for record in records:
            type_id = str(record.get("type_id", ""))
            if type_id not in self._powertrain_by_type:
                self._warn(
                    f"车辆类型 {type_id!r} 缺少 powertrain，燃油强度记为不可用。"
                )
                return None
            powertrain = self._powertrain_by_type[type_id]
            if powertrain in FUEL_POWERTRAINS:
                fuel_records.append(record)

        total_distance_m = sum(float(r["last_distance"]) for r in fuel_records)
        total_fuel_ml = sum(float(r["last_fuel_ml"]) for r in fuel_records)
        if total_distance_m <= 0:
            self._warn("没有可用的燃油车辆行驶里程，燃油强度记为不可用。")
            return None
        return (total_fuel_ml / 1000.0) / (total_distance_m / 100000.0)

    def result(
        self,
        *,
        finished: bool = False,
        decision_latency_ms: Optional[float] = None,
    ) -> EvalResult:
        r = EvalResult(algorithm=self._algorithm)
        use_finish = finished or self._finished
        warnings = list(self._warnings)

        arrived = self._total_arrived
        departed = self._total_departed
        if not use_finish:
            if arrived <= 0:
                arrived = len(self._provisional_waiting)
            if departed <= 0:
                departed = arrived + len(self._active)

        sim_time = (
            self._final_sim_time
            if use_finish and self._final_sim_time > 0
            else self._last_sim_time
        )

        r.departed = departed
        r.arrived = arrived
        if departed > 0:
            r.completion_rate = arrived / departed
        else:
            r.completion_rate = None

        if decision_latency_ms is not None:
            r.avg_decision_latency_ms = float(decision_latency_ms)
            r.metric_sources["avg_decision_latency_ms"] = "algorithm_perf_counter"
        else:
            r.avg_decision_latency_ms = None
            if use_finish:
                warnings.append("缺少算法决策耗时样本，平均决策延迟不可用。")

        if self._queue_samples and self._has_incoming_lanes:
            r.avg_queue_length_veh = sum(self._queue_samples) / len(self._queue_samples)
            r.metric_sources[
                "avg_queue_length_veh"
            ] = "incoming_lane_halting_count"
        elif use_finish and not self._queue_samples:
            warnings.append("缺少进口车道排队样本，平均排队长度不可用。")

        if sim_time > 0:
            r.throughput_veh_per_h = arrived / sim_time * 3600.0
            r.metric_sources["throughput_veh_per_h"] = (
                "finish_totals" if use_finish else "snapshot_running_totals"
            )
        elif use_finish:
            warnings.append("缺少有效评估时长，通行能力不可用。")

        if not use_finish:
            if self._provisional_travel:
                r.avg_travel_time_s = sum(self._provisional_travel) / len(
                    self._provisional_travel
                )
                r.metric_sources["avg_travel_time_s"] = "snapshot_provisional"
            if self._provisional_waiting:
                r.avg_waiting_time_s = sum(self._provisional_waiting) / len(
                    self._provisional_waiting
                )
                r.metric_sources["avg_waiting_time_s"] = "snapshot_provisional"
            if self._provisional_travel or self._provisional_waiting:
                warnings.append(
                    "平均行程时间/等待时间为快照临时值，终态将等待 TripInfo 回填。"
                )
        else:
            # 终态默认不发布快照近似值；由TripInfo回填覆盖
            r.avg_travel_time_s = None
            r.avg_waiting_time_s = None
            if not self._tripinfo_applied:
                warnings.append(
                    "终态平均行程时间和等待时间等待 TripInfo 回填。"
                )

        fuel = self._fuel_metric()
        r.fuel_intensity_L_per_100km = fuel
        if fuel is not None:
            r.metric_sources[
                "fuel_intensity_L_per_100km"
            ] = "fuel_powertrain_vehicle_totals"

        # _fuel_metric可能追加warning
        for message in self._warnings:
            if message not in warnings:
                warnings.append(message)
        r.warnings = warnings
        return r

    @property
    def finished(self) -> bool:
        return self._finished


# 兼容旧导入名
MetricsCollector = TrafficMetricsCollector
