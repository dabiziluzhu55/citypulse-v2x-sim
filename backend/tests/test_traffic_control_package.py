"""traffic_control 包与 backend 对接测试。"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.controllers.registry import (
    CONTROL_MODE_REGISTRY,
    create_controller,
    list_control_modes,
    require_control_mode,
)
from backend.app.core.exceptions import AppError
from backend.app.scenario.resolver import ResolvedStartSimulation
from backend.app.services.simulation_service import SimulationService
from simulation.sumo.distributed.codec import dumps_config, loads_config
from simulation.sumo.session import SimulationConfig
from traffic_control import max_pressure as tc_max_pressure
from traffic_control import sotl as tc_sotl
from traffic_control.registry import CONTROL_MODE_REGISTRY as TC_REGISTRY


def _sotl_metadata() -> dict:
    return {
        "episode_id": "ep-sotl-proto",
        "decision_interval": 5.0,
        "minimum_green": 5.0,
        "sotl_threshold": 30.0,
        "sotl_omega": 25.0,
        "sotl_mu": 3,
        "intersections": {
            "ix_a": {
                "intersection_id": "ix_a",
                "phase_order": [1, 2],
                "incoming_lanes": ["in_a", "in_b"],
                "outgoing_lanes": ["out_a", "out_b"],
                "lanes": {
                    "in_a": {"role": "incoming", "length_m": 100.0},
                    "in_b": {"role": "incoming", "length_m": 100.0},
                },
                "phases": {
                    "1": {"connection_priorities": {"c0": "protected"}},
                    "2": {"connection_priorities": {"c1": "protected"}},
                },
                "connections": [
                    {"connection_id": "c0", "from_lane": "in_a", "to_lane": "out_a"},
                    {"connection_id": "c1", "from_lane": "in_b", "to_lane": "out_b"},
                ],
            }
        },
    }


def _max_pressure_metadata() -> dict:
    return {
        "protocol_version": "2.0",
        "episode_id": "ep-mp-proto",
        "decision_interval": 5.0,
        "intersections": {
            "demo_2": {
                "intersection_id": "demo_2",
                "phase_order": [1, 2],
                "phases": {
                    "1": {
                        "phase_id": 1,
                        "connection_priorities": {"connection_0": "protected"},
                    },
                    "2": {
                        "phase_id": 2,
                        "connection_priorities": {"connection_1": "protected"},
                    },
                },
                "connections": [
                    {
                        "connection_id": "connection_0",
                        "from_lane": "in_a",
                        "to_lane": "out_a",
                    },
                    {
                        "connection_id": "connection_1",
                        "from_lane": "in_b",
                        "to_lane": "out_b",
                    },
                ],
                "incoming_lanes": ["in_a", "in_b"],
                "outgoing_lanes": ["out_a", "out_b"],
                "lanes": {
                    "in_a": {"edge_id": "e_in_a", "length_m": 100.0},
                    "in_b": {"edge_id": "e_in_b", "length_m": 100.0},
                    "out_a": {"edge_id": "e_out_a", "length_m": 100.0},
                    "out_b": {"edge_id": "e_out_b", "length_m": 100.0},
                },
            }
        },
    }


def _ippo_metadata() -> dict:
    intersections = {}
    for index in range(1, 21):
        iid = f"demo_{index}"
        phases = {}
        connections = []
        incoming = []
        outgoing = []
        lanes = {}
        for phase in range(4):
            conn_id = f"{iid}_c{phase}"
            in_lane = f"{iid}_in_{phase}"
            out_lane = f"{iid}_out_{phase}"
            incoming.append(in_lane)
            outgoing.append(out_lane)
            lanes[in_lane] = {
                "edge_id": f"{iid}_in_e{phase}",
                "length_m": 150.0,
                "speed_limit_mps": 15.0,
            }
            lanes[out_lane] = {
                "edge_id": f"{iid}_out_e{phase}",
                "length_m": 150.0,
                "speed_limit_mps": 15.0,
            }
            connections.append(
                {
                    "connection_id": conn_id,
                    "from_lane": in_lane,
                    "to_lane": out_lane,
                }
            )
            phases[str(phase)] = {
                "green_seconds": 30.0,
                "connection_priorities": {conn_id: "protected"},
            }
        intersections[iid] = {
            "intersection_id": iid,
            "phase_order": [0, 1, 2, 3],
            "incoming_lanes": incoming,
            "outgoing_lanes": outgoing,
            "lanes": lanes,
            "connections": connections,
            "phases": phases,
        }
    return {
        "episode_id": "ep-ippo",
        "decision_interval": 5.0,
        "minimum_green": 5.0,
        "intersections": intersections,
    }


def _make_service(**settings_kwargs: object) -> SimulationService:
    from pathlib import Path

    settings = SimpleNamespace(
        algorithm_timeout=2.0,
        decision_interval=5.0,
        enabled_control_modes=lambda: tuple(list_control_modes()),
        normalized_manager_mode=lambda: "local",
        citypulse_redis_state_url="redis://localhost:6379/0",
        backend_redis_key_prefix="citypulse:backend",
        citypulse_session_ttl_seconds=3600,
        session_root=Path("/tmp/citypulse-sessions"),
        generated_dir=Path("/tmp/citypulse-generated"),
        **settings_kwargs,
    )
    return SimulationService(
        manager=MagicMock(),
        serializer=MagicMock(),
        settings=settings,
        algorithm_store=MagicMock(),
        metrics_hub=MagicMock(),
        metadata_store=MagicMock(),
    )


def _resolved(
    *,
    control_mode: str,
    scenario_preset_id: str = "xiongan_20",
    intersection_ids: tuple[str, ...] = tuple(f"demo_{i}" for i in range(1, 21)),
) -> ResolvedStartSimulation:
    return ResolvedStartSimulation(
        scenario_preset_id=scenario_preset_id,
        intersection_ids=intersection_ids,
        period="morning_peak",
        origins={},
        window_start_seconds=0.0,
        duration_seconds=300.0,
        control_mode=control_mode,
        seed=42,
        step_length=0.05,
        realtime=False,
        gui=False,
        snapshot_interval_seconds=1.0,
        playback_speed=None,
        initial_events=(),
    )


def test_four_control_modes_registered() -> None:
    assert list_control_modes() == ["fixed", "max_pressure", "sotl", "ippo"]
    assert list(TC_REGISTRY.keys()) == ["fixed", "max_pressure", "sotl", "ippo"]
    assert CONTROL_MODE_REGISTRY is TC_REGISTRY

    fixed = require_control_mode("fixed")
    assert fixed.kernel_mode == "fixed"
    assert fixed.algorithm_module == ""

    for name, module in (
        ("sotl", "traffic_control.sotl"),
        ("max_pressure", "traffic_control.max_pressure"),
        ("ippo", "traffic_control.ippo"),
    ):
        spec = require_control_mode(name)
        assert spec.kernel_mode == "algorithm"
        assert spec.algorithm_transport == "local"
        assert spec.algorithm_module == module

    assert require_control_mode("ippo").supported_presets == ("xiongan_20",)


def test_registry_import_does_not_load_torch() -> None:
    before = {name for name in sys.modules if name == "torch" or name.startswith("torch.")}
    importlib.reload(importlib.import_module("traffic_control.registry"))
    importlib.reload(importlib.import_module("backend.app.controllers.registry"))
    after = {name for name in sys.modules if name == "torch" or name.startswith("torch.")}
    # 允许环境里已有 torch，但 registry 路径不得新增 torch 依赖链
    assert "traffic_control.ippo" not in sys.modules
    assert "traffic_control.ippo.controller" not in sys.modules
    assert "traffic_control.ippo.model" not in sys.modules
    assert after == before or "torch" in before


def test_simulation_config_local_module_for_algorithms() -> None:
    service = _make_service()

    fixed_cfg = service._build_config(_resolved(control_mode="fixed"))
    assert fixed_cfg.control_mode == "fixed"
    assert fixed_cfg.algorithm_module == ""
    assert fixed_cfg.algorithm_endpoint == ""

    for mode, module in (
        ("sotl", "traffic_control.sotl"),
        ("max_pressure", "traffic_control.max_pressure"),
        ("ippo", "traffic_control.ippo"),
    ):
        cfg = service._build_config(_resolved(control_mode=mode))
        assert cfg.control_mode == "algorithm"
        assert cfg.algorithm_transport == "local"
        assert cfg.algorithm_module == module
        assert cfg.algorithm_endpoint == ""


def test_ippo_rejects_non_xiongan_20_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _make_service()
    from backend.app.schemas.simulations import StartSimulationRequest

    start_request = StartSimulationRequest(
        scenario_preset_id="east_dense",
        period="morning_peak",
        duration_seconds=60,
        control_mode="ippo",
        realtime=False,
    )

    def _fake_resolve(request, catalog):
        return _resolved(
            control_mode="ippo",
            scenario_preset_id="east_dense",
            intersection_ids=("demo_3", "demo_5", "demo_6", "demo_9"),
        )

    monkeypatch.setattr(
        "backend.app.services.simulation_service.resolve_start_simulation",
        _fake_resolve,
    )
    service._manager.catalog.return_value = MagicMock()

    with pytest.raises(AppError) as exc:
        service.start(start_request)
    assert exc.value.status_code == 422
    assert "xiongan_20" in exc.value.message


def test_local_algorithm_protocol_lifecycle() -> None:
    cases = (
        ("traffic_control.sotl", _sotl_metadata()),
        ("traffic_control.max_pressure", _max_pressure_metadata()),
    )
    for module_path, metadata in cases:
        module = importlib.import_module(module_path)
        init = module.initialize(metadata)
        assert init["protocol_version"] == "2.0"
        assert init["ready"] is True
        assert init["episode_id"] == metadata["episode_id"]

        intersection_id = next(iter(metadata["intersections"]))
        phase_order = metadata["intersections"][intersection_id]["phase_order"]
        lanes = {
            lane_id: {"vehicle_count": 0, "halting_count": 0, "waiting_time": 0.0}
            for lane_id in metadata["intersections"][intersection_id]["incoming_lanes"]
        }
        for lane_id in metadata["intersections"][intersection_id]["incoming_lanes"][1:]:
            lanes[lane_id] = {
                "vehicle_count": 8,
                "halting_count": 8,
                "waiting_time": 40.0,
            }

        step_body = {
            "episode_id": metadata["episode_id"],
            "step_id": 1,
            "simulation_time": 10.0,
            "intersections": {
                intersection_id: {
                    "current_phase": phase_order[0],
                    "pending_phase": None,
                    "stage": "GREEN",
                    "stage_elapsed": 20.0,
                    "lanes": lanes,
                }
            },
            "vehicles": {},
        }
        response = module.step(step_body)
        assert response["protocol_version"] == "2.0"
        assert response["episode_id"] == metadata["episode_id"]
        assert response["step_id"] == 1
        assert set(response["actions"]) == {"signals", "vehicles"}
        assert response["actions"]["vehicles"] == {}
        for target in response["actions"]["signals"].values():
            assert "target_phase" in target

        finish = module.finish(
            {"episode_id": metadata["episode_id"], "reason": "completed"}
        )
        assert finish["ok"] is True


def test_ippo_protocol_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IPPO_MODEL_PATH", raising=False)
    monkeypatch.setenv("IPPO_MODE", "model")
    import traffic_control.ippo as ippo
    import traffic_control.ippo.controller as controller

    importlib.reload(controller)
    importlib.reload(ippo)

    metadata = _ippo_metadata()
    init = ippo.initialize(metadata)
    assert init["protocol_version"] == "2.0"
    assert init["ready"] is True

    intersections = {
        f"demo_{index}": {
            "current_phase": 0,
            "pending_phase": None,
            "stage": "GREEN",
            "stage_elapsed": 20.0,
            "lanes": {
                **{
                    f"demo_{index}_in_{phase}": {
                        "vehicle_count": 1,
                        "halting_count": 0,
                        "waiting_time": 1.0,
                        "mean_speed": 8.0,
                        "occupancy": 10.0,
                    }
                    for phase in range(4)
                },
                **{
                    f"demo_{index}_out_{phase}": {
                        "vehicle_count": 0,
                        "halting_count": 0,
                        "waiting_time": 0.0,
                        "mean_speed": 10.0,
                        "occupancy": 0.0,
                    }
                    for phase in range(4)
                },
            },
        }
        for index in range(1, 21)
    }
    response = ippo.step(
        {
            "episode_id": metadata["episode_id"],
            "step_id": 4,
            "simulation_time": 20.0,
            "intersections": intersections,
            "vehicles": {},
        }
    )
    assert set(response["actions"]) == {"signals", "vehicles"}
    assert response["actions"]["vehicles"] == {}
    finish = ippo.finish({"episode_id": metadata["episode_id"], "reason": "completed"})
    assert finish["ok"] is True


def test_sotl_migration_matches_controller_output() -> None:
    from backend.app.controllers.sotl import SOTLController as BackendSOTL

    assert BackendSOTL is tc_sotl.SOTLController
    metadata = _sotl_metadata()
    controller = create_controller("sotl", metadata)
    tc_sotl.initialize(metadata)
    observation = {
        "episode_id": metadata["episode_id"],
        "step_id": 2,
        "intersections": {
            "ix_a": {
                "current_phase": 1,
                "pending_phase": None,
                "stage": "GREEN",
                "stage_elapsed": 20.0,
                "lanes": {
                    "in_a": {"vehicle_count": 0, "halting_count": 0},
                    "in_b": {"vehicle_count": 10, "halting_count": 10},
                },
            }
        },
        "vehicles": {},
    }
    # 先累计积分
    for _ in range(3):
        controller.compute_actions(observation)
    actions = controller.compute_actions(observation)

    # 重新初始化模块级协议状态，复放相同输入序列
    tc_sotl.finish({})
    tc_sotl.initialize(metadata)
    for _ in range(3):
        tc_sotl.step(observation)
    proto = tc_sotl.step(observation)
    expected = {
        iid: {"target_phase": phase}
        for iid, phase in actions.items()
        if phase is not None
    }
    assert proto["actions"]["signals"] == expected
    tc_sotl.finish({})


def test_max_pressure_migration_matches_controller_output() -> None:
    from backend.app.controllers.max_pressure import (
        MaxPressureController as BackendMP,
    )

    assert BackendMP is tc_max_pressure.MaxPressureController
    metadata = _max_pressure_metadata()
    controller = create_controller("max_pressure", metadata)
    observation = {
        "episode_id": metadata["episode_id"],
        "step_id": 1,
        "intersections": {
            "demo_2": {
                "current_phase": 1,
                "pending_phase": None,
                "stage": "GREEN",
                "stage_elapsed": 10.0,
                "lanes": {
                    "in_a": {"vehicle_count": 1, "halting_count": 1},
                    "in_b": {"vehicle_count": 5, "halting_count": 5},
                    "out_a": {"vehicle_count": 0, "halting_count": 0},
                    "out_b": {"vehicle_count": 0, "halting_count": 0},
                },
            }
        },
        "vehicles": {},
    }
    actions = controller.compute_actions(observation)
    tc_max_pressure.initialize(metadata)
    proto = tc_max_pressure.step(observation)
    expected = {
        iid: {"target_phase": phase}
        for iid, phase in actions.items()
        if phase is not None
    }
    assert proto["actions"]["signals"] == expected
    tc_max_pressure.finish({})


def test_ippo_checkpoint_loads_and_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IPPO_MODEL_PATH", raising=False)
    monkeypatch.setenv("IPPO_MODE", "model")
    import traffic_control.ippo as ippo
    import traffic_control.ippo.controller as controller

    importlib.reload(controller)
    importlib.reload(ippo)

    metadata = _ippo_metadata()
    init = ippo.initialize(metadata)
    assert init["ready"] is True
    assert controller._loaded_model_path is not None
    assert controller._loaded_model_path.endswith("ippo_v8_20tls_ep160.pt")

    intersections = {}
    for index in range(1, 21):
        iid = f"demo_{index}"
        lanes = {}
        for phase in range(4):
            lanes[f"{iid}_in_{phase}"] = {
                "vehicle_count": 2 + phase,
                "halting_count": 1,
                "waiting_time": 10.0,
                "mean_speed": 5.0,
                "occupancy": 20.0,
            }
            lanes[f"{iid}_out_{phase}"] = {
                "vehicle_count": 0,
                "halting_count": 0,
                "waiting_time": 0.0,
                "mean_speed": 10.0,
                "occupancy": 5.0,
            }
        intersections[iid] = {
            "current_phase": 0,
            "pending_phase": None,
            "stage": "GREEN",
            "stage_elapsed": 20.0,
            "lanes": lanes,
        }
    step_body = {
        "episode_id": metadata["episode_id"],
        "step_id": 4,
        "simulation_time": 20.0,
        "intersections": intersections,
        "vehicles": {},
    }
    first = ippo.step(step_body)
    # 重置决策时钟后再次同观测，确定性模型应给出相同动作
    controller._last_decision_times = {
        iid: -1e9 for iid in controller._intersection_ids
    }
    second = ippo.step(step_body)
    assert first["actions"]["signals"]
    assert first["actions"]["signals"] == second["actions"]["signals"]
    assert first["actions"]["vehicles"] == {}
    ippo.finish({"episode_id": metadata["episode_id"], "reason": "completed"})


def test_redis_codec_preserves_algorithm_transport_and_module() -> None:
    config = SimulationConfig(
        intersection_ids=tuple(f"demo_{i}" for i in range(1, 21)),
        duration_seconds=120,
        control_mode="algorithm",
        algorithm_transport="local",
        algorithm_module="traffic_control.ippo",
        algorithm_endpoint="",
    )
    restored = loads_config(dumps_config(config))
    assert restored.algorithm_transport == "local"
    assert restored.algorithm_module == "traffic_control.ippo"
    assert restored.algorithm_endpoint == ""
    assert restored == config
