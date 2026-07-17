"""
采集计算指标，在算法/step与/finish时累计样本，计算6大评估指标
计算参考algorithms/evaluation/collector.py
"""

from __future__ import annotations

from typing import Any

from .models import EvalResult


class MetricsCollector:
    """按initialize → step* → finish生命周期采集并汇总指标"""

    def __init__(self, algorithm: str = "") -> None:
        self._algorithm = algorithm
        self._active: dict[str, dict[str, float]] = {}
        self._arrived_waiting: list[float] = []
        self._arrived_travel: list[float] = []
        self._arrived_time_loss: list[float] = []
        self._arrived_distance: list[float] = []
        self._arrived_fuel_ml: list[float] = []
        self._queue_samples: list[float] = []
        self._latency_samples: list[float] = []
        self._total_departed: int = 0
        self._total_arrived: int = 0
        self._final_sim_time: float = 0.0
        self._final_fuel_ml: float = 0.0
        self._last_sim_time: float = 0.0

    def on_initialize(self, _body: dict[str, Any]) -> None:
        self._active.clear()
        self._arrived_waiting.clear()
        self._arrived_travel.clear()
        self._arrived_time_loss.clear()
        self._arrived_distance.clear()
        self._arrived_fuel_ml.clear()
        self._queue_samples.clear()
        self._latency_samples.clear()
        self._total_departed = 0
        self._total_arrived = 0
        self._final_sim_time = 0.0
        self._final_fuel_ml = 0.0
        self._last_sim_time = 0.0

    def record_latency(self, ms: float) -> None:
        self._latency_samples.append(ms)

    def on_step(self, body: dict[str, Any]) -> None:
        sim_time = float(body.get("simulation_time", 0.0))
        self._last_sim_time = sim_time
        current_vehicles: dict[str, Any] = body.get("vehicles", {})

        for _iid, i_state in body.get("intersections", {}).items():
            for _lane_id, lane_data in i_state.get("lanes", {}).items():
                self._queue_samples.append(float(lane_data.get("halting_count", 0)))

        for vid in current_vehicles:
            if vid not in self._active:
                self._active[vid] = {
                    "first_seen_s": sim_time,
                    "last_waiting": 0.0,
                    "last_time_loss": 0.0,
                    "last_distance": 0.0,
                    "last_fuel_ml": 0.0,
                }

        arrived_vids = set(self._active.keys()) - set(current_vehicles.keys())
        for vid in arrived_vids:
            rec = self._active.pop(vid)
            travel = sim_time - rec["first_seen_s"]
            if travel > 0:
                self._arrived_travel.append(travel)
            self._arrived_waiting.append(rec["last_waiting"])
            self._arrived_time_loss.append(rec["last_time_loss"])
            self._arrived_distance.append(rec["last_distance"])
            self._arrived_fuel_ml.append(rec["last_fuel_ml"])

        for vid, vdata in current_vehicles.items():
            if vid not in self._active:
                continue
            traffic = vdata.get("traffic", {})
            energy = vdata.get("energy", {})
            self._active[vid].update(
                {
                    "last_waiting": float(traffic.get("accumulated_waiting_time_s", 0)),
                    "last_time_loss": float(traffic.get("time_loss_s", 0)),
                    "last_distance": float(traffic.get("distance_m", 0)),
                    "last_fuel_ml": float(energy.get("fuel_total_ml", 0)),
                }
            )

    def on_finish(self, body: dict[str, Any]) -> None:
        self._total_departed = int(body.get("departed_vehicles", 0))
        self._total_arrived = int(body.get("arrived_vehicles", 0))
        self._final_sim_time = float(body.get("simulation_time", 0.0))
        self._final_fuel_ml = float(body.get("fuel_consumed_ml", 0.0))
        self._last_sim_time = self._final_sim_time

    def result(self, *, finished: bool = True) -> EvalResult:
        r = EvalResult(algorithm=self._algorithm)
        arrived = self._total_arrived if finished and self._total_arrived > 0 else len(
            self._arrived_waiting
        )
        departed = self._total_departed if finished and self._total_departed > 0 else (
            arrived + len(self._active)
        )
        sim_time = self._final_sim_time if finished and self._final_sim_time > 0 else self._last_sim_time

        r.departed = departed
        r.arrived = arrived

        if self._arrived_travel:
            r.avg_travel_time_s = sum(self._arrived_travel) / len(self._arrived_travel)
        if self._arrived_waiting:
            r.avg_waiting_time_s = sum(self._arrived_waiting) / len(self._arrived_waiting)
        if self._queue_samples:
            r.avg_queue_length_veh = sum(self._queue_samples) / len(self._queue_samples)
        if sim_time > 0 and arrived > 0:
            r.throughput_veh_per_h = arrived / sim_time * 3600.0
        if self._latency_samples:
            r.avg_decision_latency_ms = sum(self._latency_samples) / len(self._latency_samples)

        total_distance_m = sum(self._arrived_distance)
        total_fuel = (
            self._final_fuel_ml if self._final_fuel_ml > 0 else sum(self._arrived_fuel_ml)
        )
        if total_distance_m > 0 and total_fuel > 0:
            fuel_l = total_fuel / 1000.0
            dist_100km = total_distance_m / 100000.0
            r.fuel_intensity_L_per_100km = fuel_l / dist_100km if dist_100km > 0 else 0.0

        return r
