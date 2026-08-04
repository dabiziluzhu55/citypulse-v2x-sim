"""公共交通指标采集器：与管控算法解耦，fixed / max_pressure 共用同一套累计与公式。

主数据源为 SimulationManager 推送的 SimulationSnapshot（按 snapshot_interval），
不依赖前端轮询频率。算法决策耗时由 AlgorithmRuntimeStore 另行统计后合并。
"""

from __future__ import annotations

from typing import Any, Mapping

from simulation.sumo.session import SimulationSnapshot

from .models import EvalResult


class TrafficMetricsCollector:
    """按 session 生命周期采集交通运行指标（不含算法决策延迟）。"""

    def __init__(self, algorithm: str = "") -> None:
        self._algorithm = algorithm
        self._active: dict[str, dict[str, float]] = {}
        self._arrived_waiting: list[float] = []
        self._arrived_travel: list[float] = []
        self._arrived_distance: list[float] = []
        self._arrived_fuel_ml: list[float] = []
        self._queue_samples: list[float] = []
        self._total_departed: int = 0
        self._total_arrived: int = 0
        self._final_sim_time: float = 0.0
        self._final_fuel_ml: float = 0.0
        self._last_sim_time: float = 0.0
        self._finished: bool = False

    def reset(self, algorithm: str = "") -> None:
        if algorithm:
            self._algorithm = algorithm
        self._active.clear()
        self._arrived_waiting.clear()
        self._arrived_travel.clear()
        self._arrived_distance.clear()
        self._arrived_fuel_ml.clear()
        self._queue_samples.clear()
        self._total_departed = 0
        self._total_arrived = 0
        self._final_sim_time = 0.0
        self._final_fuel_ml = 0.0
        self._last_sim_time = 0.0
        self._finished = False

    def observe_snapshot(self, snapshot: SimulationSnapshot) -> None:
        """从统一 Snapshot 观测一次（两种 control_mode 共用）。"""
        if self._finished:
            return
        sim_time = float(snapshot.elapsed_seconds)
        vehicles: dict[str, Mapping[str, float]] = {}
        for vehicle in snapshot.vehicles:
            vehicles[vehicle.vehicle_id] = {
                "waiting": float(vehicle.waiting_time),
                "time_loss": float(vehicle.time_loss),
                "distance": float(vehicle.distance),
                "fuel_ml": float(vehicle.fuel_total_ml),
            }
        lane_halting: list[float] = []
        for i_state in snapshot.intersections.values():
            for lane in i_state.lanes.values():
                lane_halting.append(float(lane.halting_count))
        self._observe(
            sim_time=sim_time,
            vehicles=vehicles,
            lane_halting=lane_halting,
        )
        # 用内核累计计数刷新，保证与 SessionMetrics 口径一致
        self._total_departed = int(snapshot.metrics.departed_vehicles)
        self._total_arrived = int(snapshot.metrics.arrived_vehicles)
        self._final_fuel_ml = float(snapshot.metrics.fuel_consumed_ml)

    def finalize_from_snapshot(self, snapshot: SimulationSnapshot) -> EvalResult:
        """会话结束时结算最终交通指标。"""
        self.observe_snapshot(snapshot)
        self._finished = True
        self._final_sim_time = float(snapshot.elapsed_seconds)
        self._last_sim_time = self._final_sim_time
        self._total_departed = int(snapshot.metrics.departed_vehicles)
        self._total_arrived = int(snapshot.metrics.arrived_vehicles)
        self._final_fuel_ml = float(snapshot.metrics.fuel_consumed_ml)
        return self.result(finished=True)

    def _observe(
        self,
        *,
        sim_time: float,
        vehicles: Mapping[str, Mapping[str, float]],
        lane_halting: list[float],
    ) -> None:
        self._last_sim_time = sim_time
        self._queue_samples.extend(lane_halting)

        for vid in vehicles:
            if vid not in self._active:
                self._active[vid] = {
                    "first_seen_s": sim_time,
                    "last_waiting": 0.0,
                    "last_distance": 0.0,
                    "last_fuel_ml": 0.0,
                }

        arrived_vids = set(self._active.keys()) - set(vehicles.keys())
        for vid in arrived_vids:
            rec = self._active.pop(vid)
            travel = sim_time - rec["first_seen_s"]
            if travel > 0:
                self._arrived_travel.append(travel)
            self._arrived_waiting.append(rec["last_waiting"])
            self._arrived_distance.append(rec["last_distance"])
            self._arrived_fuel_ml.append(rec["last_fuel_ml"])

        for vid, vdata in vehicles.items():
            if vid not in self._active:
                continue
            self._active[vid].update(
                {
                    "last_waiting": float(vdata.get("waiting", 0.0)),
                    "last_distance": float(vdata.get("distance", 0.0)),
                    "last_fuel_ml": float(vdata.get("fuel_ml", 0.0)),
                }
            )

    def result(
        self,
        *,
        finished: bool = False,
        decision_latency_ms: float = 0.0,
    ) -> EvalResult:
        r = EvalResult(algorithm=self._algorithm)
        use_finish = finished or self._finished

        arrived = (
            self._total_arrived
            if use_finish and self._total_arrived > 0
            else len(self._arrived_waiting)
        )
        departed = (
            self._total_departed
            if use_finish and self._total_departed > 0
            else arrived + len(self._active)
        )
        sim_time = (
            self._final_sim_time
            if use_finish and self._final_sim_time > 0
            else self._last_sim_time
        )

        r.departed = departed
        r.arrived = arrived
        r.avg_decision_latency_ms = float(decision_latency_ms)

        if self._arrived_travel:
            r.avg_travel_time_s = sum(self._arrived_travel) / len(self._arrived_travel)
        if self._arrived_waiting:
            r.avg_waiting_time_s = sum(self._arrived_waiting) / len(self._arrived_waiting)
        if self._queue_samples:
            r.avg_queue_length_veh = sum(self._queue_samples) / len(self._queue_samples)
        if sim_time > 0 and arrived > 0:
            r.throughput_veh_per_h = arrived / sim_time * 3600.0

        total_distance_m = sum(self._arrived_distance)
        # 会话级油耗优先；否则用已到达车辆累计
        total_fuel = (
            self._final_fuel_ml if self._final_fuel_ml > 0 else sum(self._arrived_fuel_ml)
        )
        # 活跃车辆里程/油耗也计入强度估计，避免仅 arrived=0 时恒为 0
        if total_distance_m <= 0:
            total_distance_m = sum(rec["last_distance"] for rec in self._active.values())
        if total_fuel <= 0:
            total_fuel = sum(rec["last_fuel_ml"] for rec in self._active.values())
        if total_distance_m > 0 and total_fuel > 0:
            fuel_l = total_fuel / 1000.0
            dist_100km = total_distance_m / 100000.0
            r.fuel_intensity_L_per_100km = fuel_l / dist_100km if dist_100km > 0 else 0.0

        return r

    @property
    def finished(self) -> bool:
        return self._finished


# 兼容旧导入名
MetricsCollector = TrafficMetricsCollector
