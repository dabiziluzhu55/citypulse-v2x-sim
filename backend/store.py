"""In-memory mock simulation state."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

from data import (
    COMPARISON_BASELINES,
    COMPARISON_RESULTS,
    DEFAULT_EVENTS,
    INTERSECTIONS,
    LANES,
    VEHICLES,
)

DEFAULT_RUN_ID = "run_20260704_001"
DEFAULT_SCENARIO_ID = "scenario_20260704_001"


@dataclass
class RunState:
    run_id: str
    scenario_id: str
    scenario_name: str = "雄安窄路密网20路口"
    status: str = "running"
    algorithm: str = "ippo"
    cloud_edge_enabled: bool = True
    sim_time: int = 1250
    step: int = 1250
    vehicle_count: int = 842
    message: str = "仿真运行中"
    _last_tick: float = field(default_factory=time.time)

    def tick(self) -> None:
        if self.status != "running":
            return
        now = time.time()
        elapsed = now - self._last_tick
        if elapsed >= 1:
            increment = int(elapsed * 2)
            if increment > 0:
                self.sim_time += increment
                self.step = self.sim_time
                self.vehicle_count = 820 + (self.sim_time % 50)
                self._last_tick = now

    def overview(self) -> dict:
        self.tick()
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "scenario_name": self.scenario_name,
            "status": self.status,
            "sim_time": self.sim_time,
            "vehicle_count": self.vehicle_count,
            "active_vehicle_count": max(self.vehicle_count - 22, 0),
            "algorithm": self.algorithm,
            "cloud_edge_enabled": self.cloud_edge_enabled,
            "avg_speed": 8.7,
            "avg_waiting_time": 42.5,
            "avg_queue_length": 18.3,
            "congested_intersections": 3,
        }

    def status_payload(self) -> dict:
        self.tick()
        return {
            "run_id": self.run_id,
            "status": self.status,
            "sim_time": self.sim_time,
            "step": self.step,
            "vehicle_count": self.vehicle_count,
            "message": self.message,
        }

    def traffic_state(self) -> dict:
        self.tick()
        return {
            "run_id": self.run_id,
            "sim_time": self.sim_time,
            "intersections": INTERSECTIONS,
            "lanes": LANES,
            "vehicles": [
                {
                    **VEHICLES[0],
                    "x": 438 + (self.sim_time % 20),
                    "waiting_time": 45 + (self.sim_time % 10),
                }
            ],
        }

    def collaboration_state(self) -> dict:
        self.tick()
        return {
            "run_id": self.run_id,
            "sim_time": self.sim_time,
            "cloud": {
                "strategy": "corridor_priority",
                "target_area": "J09-J12-J16",
                "reason": "南北走廊排队超阈值",
                "algorithm": self.algorithm,
            },
            "edges": [
                {
                    "edge_agent_id": "agent_J12",
                    "intersection_id": "J12",
                    "local_state": {
                        "queue_length": 35,
                        "avg_waiting_time": 64.2,
                        "current_phase": 1,
                    },
                    "local_rule_check": {
                        "min_green_satisfied": True,
                        "conflict_free": True,
                    },
                    "last_action": {
                        "action_type": "extend_green",
                        "target_phase": 1,
                        "duration": 10,
                    },
                    "status": "executed",
                }
            ],
            "vehicles": [
                {
                    "vehicle_id": "veh_1024",
                    "lane_id": "E12_0",
                    "speed": 9.5,
                    "waiting_time": 12,
                    "received_advice": {
                        "type": "speed_advice",
                        "recommended_speed": 10,
                        "recommended_path": "J12→J16",
                    },
                }
            ],
        }

    def events(self) -> dict:
        self.tick()
        events = []
        for item in DEFAULT_EVENTS:
            events.append({**item, "time": self.sim_time})
        return {"events": events}

    def prediction(self, target: str, horizon: int) -> dict:
        self.tick()
        return {
            "target": target,
            "horizon": horizon,
            "predictions": [
                {
                    "time_offset": 60,
                    "predicted_flow": 86,
                    "predicted_queue": 22,
                    "congestion_risk": 0.62,
                },
                {
                    "time_offset": 300,
                    "predicted_flow": 110,
                    "predicted_queue": 35,
                    "congestion_risk": 0.81,
                },
            ],
            "model": "gru_onnx",
            "updated_at": self.sim_time,
        }

    def metrics_realtime(self) -> dict:
        self.tick()
        return {
            "run_id": self.run_id,
            "time": self.sim_time,
            "metrics": {
                "avg_speed": 8.7,
                "avg_waiting_time": 42.5,
                "avg_travel_time": 410.2,
                "avg_queue_length": 18.3,
                "throughput": 1370,
                "fuel_consumption": 91.0,
                "co2_emission": 88.4,
            },
        }

    def metrics_timeseries(self) -> dict:
        self.tick()
        t = self.sim_time
        return {
            "run_id": self.run_id,
            "series": [
                {"time": 0, "avg_waiting_time": 0, "avg_queue_length": 0, "throughput": 0},
                {
                    "time": min(300, t),
                    "avg_waiting_time": 18.2,
                    "avg_queue_length": 10.4,
                    "throughput": 220,
                },
                {
                    "time": t,
                    "avg_waiting_time": 42.5,
                    "avg_queue_length": 18.3,
                    "throughput": 1370,
                },
            ],
        }

    def experiment_comparison(self, experiment_id: str) -> dict:
        return {
            "experiment_id": experiment_id,
            "scenario_id": self.scenario_id,
            "baselines": COMPARISON_BASELINES,
            "results": [COMPARISON_RESULTS[algo] for algo in COMPARISON_BASELINES],
        }

    def ws_traffic_delta(self) -> dict:
        self.tick()
        return {
            "type": "traffic_state",
            "timestamp": self.sim_time,
            "data": {
                "vehicle_count": self.vehicle_count,
                "avg_speed": 8.7,
                "intersections": [],
                "lanes": [],
                "vehicles": [
                    {
                        "vehicle_id": "veh_1024",
                        "x": 438 + (self.sim_time % 20),
                        "y": 378,
                        "speed": 2.5,
                        "waiting_time": 46 + (self.sim_time % 5),
                        "lane_id": "E12_0",
                        "type": "car",
                    }
                ],
            },
        }

    def ws_collaboration_delta(self) -> dict:
        self.tick()
        return {
            "type": "collaboration_state",
            "timestamp": self.sim_time,
            "data": {
                "cloud": {
                    "strategy": "corridor_priority",
                    "target_area": "J09-J12-J16",
                    "reason": "南北走廊排队超阈值",
                    "algorithm": self.algorithm,
                },
                "edges": [],
                "vehicles": [],
            },
        }

    def ws_overview(self) -> dict:
        return {"type": "overview", "data": self.overview()}

    def ws_event_detected(self) -> dict:
        self.tick()
        return {
            "type": "event_detected",
            "timestamp": self.sim_time,
            "data": {
                "event_id": "event_001",
                "type": "congestion",
                "level": "high",
                "location": {"intersection_id": "J12"},
            },
        }


class MockStore:
    def __init__(self) -> None:
        self.runs: dict[str, RunState] = {}
        self.scenarios: dict[str, dict] = {}
        self._scenario_counter = 1
        self._run_counter = 2
        self.seed_default_run()

    def seed_default_run(self) -> None:
        self.runs[DEFAULT_RUN_ID] = RunState(
            run_id=DEFAULT_RUN_ID,
            scenario_id=DEFAULT_SCENARIO_ID,
        )
        self.scenarios[DEFAULT_SCENARIO_ID] = {
            "scenario_id": DEFAULT_SCENARIO_ID,
            "status": "ready",
            "template_id": "xiongan20",
            "name": "雄安窄路密网20路口",
        }

    def get_run(self, run_id: str) -> RunState | None:
        return self.runs.get(run_id)

    def require_run(self, run_id: str) -> RunState:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def next_scenario_id(self) -> str:
        today = datetime.now().strftime("%Y%m%d")
        scenario_id = f"scenario_{today}_{self._scenario_counter:03d}"
        self._scenario_counter += 1
        return scenario_id

    def next_run_id(self) -> str:
        today = datetime.now().strftime("%Y%m%d")
        run_id = f"run_{today}_{self._run_counter:03d}"
        self._run_counter += 1
        return run_id

    def create_scenario(self, payload: dict) -> dict:
        scenario_id = self.next_scenario_id()
        template_id = payload.get("template_id", "xiongan20")
        self.scenarios[scenario_id] = {
            "scenario_id": scenario_id,
            "status": "ready",
            "template_id": template_id,
            "name": payload.get("name", scenario_id),
        }
        return {
            "scenario_id": scenario_id,
            "status": "ready",
            "files": {
                "net": f"scenarios/{scenario_id}/net.xml",
                "route": f"scenarios/{scenario_id}/rou.xml",
                "config": f"scenarios/{scenario_id}/sumo.cfg",
            },
        }

    def create_run(self, payload: dict) -> RunState:
        run_id = self.next_run_id()
        scenario_id = payload.get("scenario_id", DEFAULT_SCENARIO_ID)
        scenario = self.scenarios.get(scenario_id, {})
        run = RunState(
            run_id=run_id,
            scenario_id=scenario_id,
            scenario_name=scenario.get("name", "雄安窄路密网20路口"),
            status="starting",
            algorithm=payload.get("algorithm", "fixed_time"),
            cloud_edge_enabled=bool(payload.get("cloud_edge_enabled", True)),
            sim_time=0,
            step=0,
            vehicle_count=0,
            message="仿真已启动，正在加载 SUMO 场景",
        )
        self.runs[run_id] = run
        run.status = "running"
        run.message = "仿真运行中"
        return run

    def control_run(self, run_id: str, command: str) -> RunState:
        run = self.require_run(run_id)
        if command == "pause":
            run.status = "paused"
            run.message = "仿真已暂停"
        elif command == "resume":
            run.status = "running"
            run.message = "仿真运行中"
            run._last_tick = time.time()
        elif command == "stop":
            run.status = "stopped"
            run.message = "仿真已停止"
        elif command == "reset":
            run.status = "idle"
            run.sim_time = 0
            run.step = 0
            run.vehicle_count = 0
            run.message = "仿真已重置"
        elif command == "step":
            if run.status != "running":
                run.status = "running"
            run.sim_time += 1
            run.step = run.sim_time
            run.message = "单步推进完成"
        return run

    def apply_algorithm(self, run_id: str, algorithm_id: str) -> RunState:
        run = self.require_run(run_id)
        run.algorithm = algorithm_id
        return run


store = MockStore()
