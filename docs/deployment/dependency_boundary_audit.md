# 部署依赖边界审计（develop — 2026-09-11 第二轮）

基于当前代码静态扫描与边界测试，反映 **simulation_protocol 抽取后** 的实际状态。

## 1. 包职责矩阵

| 包 | 职责 | 禁止 import |
|---|---|---|
| `simulation_protocol/` | DTO / codec / Redis store / Celery client / catalog loader | libsumo, traci, sumolib, SimulationManager, traffic_control 算法, backend |
| `backend/app/` | FastAPI、会话、指标封装、Copilot、Narrow-TDP | algorithms；redis 模式禁止 simulation.sumo.engine.session |
| `traffic_control/` | Worker 内管控算法 + checkpoint | algorithms（训练）, backend |
| `traffic_eval/` | 正式评估公式 | backend, algorithms |
| `traffic_intelligence/` | 运行时事件识别 | algorithms, backend |
| `scenario_catalog/` | 场景预设 + 公共 model alias 元数据 | checkpoint 路径解析 |
| `simulation/` | SUMO 内核、Worker、scenario 编译 | backend |

## 2. 已修复项（相对第一轮）

- Backend redis 模式 → `simulation_protocol.RedisSimulationClient`（无 `SimulationManager` validator）
- Worker 负责 `compile_session_scenario`（Backend start 不再编译）
- Backend Celery → `simulation_protocol.celery_client`（仅 send_task，不 import worker tasks）
- `backend/app/scenario/resolver.py` → `scenario_catalog.control_modes`（无 traffic_control.ippo/mappo/cov2x）
- `MapService` redis 模式禁止 sumolib fallback
- `simulation/utils/convert_sumo_road_network.py` 移除 backend 依赖
- CityPulse-Qwen 生产启动 → `deploy/citypulse-qwen/`（不挂载 algorithms）

## 3. 剩余 local-only 耦合（可接受）

| 路径 | 说明 |
|---|---|
| `backend/app/services/manager_factory.py` | `local` 模式 lazy import `SimulationManager` |
| `backend/app/services/scenario_export_service.py` | 场景导出 lazy import `compile_session_scenario` + `SimulationManager` |
| `backend/app/services/map_service.py` | `local` 模式 sumolib GeoJSON fallback |

## 4. 五容器 READY / BLOCKED

| 容器 | 状态 | 说明 |
|---|---|---|
| **frontend** | **READY** | MapV-Three 生产路径；Nginx `/api/` 代理已配置；`npm run build` 通过 |
| **backend** | **READY** | redis 模式无 SUMO import；`celery` client 已声明；321 pytest 通过 |
| **sumo-worker** | **READY** | simulation + traffic_control + Celery worker；scenario 编译在 worker |
| **redis** | **READY** | 官方 Redis 镜像；无代码阻塞 |
| **citypulse-qwen** | **READY** | `deploy/citypulse-qwen/start.sh` vLLM AWQ；不依赖 algorithms |

## 5. 进入 Dockerfile 前仍需验证

- [ ] 干净 venv 仅装 `deploy/requirements/backend.in` 全量 pytest
- [ ] Redis + Backend + Worker 四组件端到端联调
- [ ] `scripts/deployment/check_map_artifacts.py` 在 CI/deploy 前执行
- [ ] `pip-compile` 锁定 backend.txt / sumo-worker.txt

## 6. 边界测试

`tests/deployment/test_import_boundaries.py` 覆盖：

- backend 不 import algorithms
- backend 不 import traffic_control.ippo/mappo/cov2x
- simulation 不 import backend
- redis lifespan + block sumolib/libsumo/traci 仍可构造 client
