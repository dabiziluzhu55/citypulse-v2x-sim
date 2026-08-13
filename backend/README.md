# CityPulse V2X Backend

基于 SUMO 的交通管控仿真后端，入口为 `backend.app.main:app`

- 前端本地运行在 `http://localhost:5173`
- 后端本地运行在 `http://localhost:8000`

## 架构边界

- FastAPI **不直接** import 或调用 `traci`
- FastAPI **不通过 subprocess** 启动 `python -m simulation.sumo.engine.run`
- FastAPI **不自行**启动 `sumo` / `sumo-gui`
- TraCI 由 `simulation` 层的仿真管理器持有：
  - `local` 模式：`SimulationManager`（本机进程内）
  - `redis` 模式：`RedisSimulationManager`（Celery SUMO worker + Redis 会话状态）
- Backend **复用** `simulation.sumo.RedisSimulationManager`，不在 backend 重写 Celery / TraCI / SUMO worker
- `AlgorithmRuntimeStore` 为**进程内**状态，Uvicorn 必须 `--workers 1`

分层职责：

| 层 | 路径 | 说明 |
|---|---|---|
| API | `app/api/v1/` | 对外统一 REST / WebSocket |
| 管控算法 | `app/controllers/` | fixed / max_pressure / sotl 等纯决策逻辑 |
| 指标计算 | `app/metrics/` | 薄封装，转发至仓库根目录 `traffic_eval/` |
| 会话元数据 | `app/services/session_metadata.py` | backend 独立 Redis/内存命名空间 |
| 场景层 | `app/scenario/` | 场景预设与启动请求解析 |
| 场景导出 | `app/services/scenario_export_service.py` / `od_export.py` | ZIP 含路网与九区域 OD |
| 内部算法协议 | `app/api/v1/internal_algorithm.py` | 供 SUMO worker HTTP 回调 |

---

## 运行模式：local / redis

通过环境变量 `SIMULATION_MANAGER_MODE` 选择：

| 模式 | 管理器 | 并发 | 说明 |
|------|--------|------|------|
| `local`（默认） | `SimulationManager` | 本机单会话语义仍由内核限制 | 方便本地调试；关闭 backend 时会停止本机活动会话 |
| `redis` | `RedisSimulationManager` | 多会话排队与并发 | API 容器不跑 SUMO；关闭 backend **不会**自动停止 SUMO worker 中的会话 |

### redis 模式要点

1. **共享目录**：backend 与 SUMO worker 共用 `generated_dir`、`outputs/sessions`
2. **会话元数据**：写入 Redis，key 前缀为 `{CITYPULSE_REDIS_KEY_PREFIX}:backend:...`，与 simulation 的 `{prefix}:session:...` 隔离
3. **指标 watcher**：按 `session_id` 订阅快照；用 Redis 锁保证同一 session 只有一个 watcher；WebSocket 断开不影响采集
4. **重启恢复**：backend 启动后可根据元数据恢复未完成会话的指标 watcher
5. **Redis 不可用**：健康检查 `degraded`，仿真 API 返回 **503** `REDIS_UNAVAILABLE`，**不会**静默降级为 local
6. **SUMO_HOME**：redis 模式下调用仿真 API **不强制**本机 `SUMO_HOME`（SUMO 在 worker 侧）；仍需检查 generated 产物与共享 `session_root`
7. **算法回调地址**：`ALGORITHM_BASE_URL` 必须是 SUMO worker 可达的 backend 地址，不能假设 worker 里的 `127.0.0.1` 就是 API 容器

### 相关环境变量

见 `backend/.env.example`：

```bash
SIMULATION_MANAGER_MODE=local   # 或 redis

CITYPULSE_REDIS_STATE_URL=redis://127.0.0.1:6380/1
CITYPULSE_REDIS_KEY_PREFIX=citypulse
CITYPULSE_SESSION_TTL_SECONDS=86400
CITYPULSE_COMMAND_TIMEOUT_SECONDS=30
CITYPULSE_WORKER_HEARTBEAT_TTL_SECONDS=15

ALGORITHM_BASE_URL=http://127.0.0.1:8000
```

redis 基础设施可参考仓库根目录 `compose.redis.yml` 与 `docs/distributed_simulation.md`（SUMO worker 启动方式）

---

## 配置 SUMO_HOME

```bash
export SUMO_HOME=/usr/share/sumo
```

或复制环境模板：

```bash
cp backend/.env.example backend/.env
```

## 构建 generated 产物

在仓库根目录执行（20 路口示例）：

```bash
python -m simulation.sumo.building.build_tls
python -m simulation.sumo.building.build_traffic
```

生成目录：

```text
data/maps/sumo/generated/
├── manifests/traffic_manifest.json
├── manifests/tls_manifest.json
├── network/TotalMap_20.signals.net.xml
└── reports/traffic_od_{period}.json|csv
```

若这些文件缺失，后端仍可启动，但 `/api/v1/health` 会返回 `status: degraded`；仿真相关接口返回 `503`

## 安装依赖

在仓库根目录执行：

```bash
pip install -r backend/requirements.txt
```

（含 `matplotlib` / `numpy`，用于场景导出 OD 热力图）

## 启动后端

在仓库根目录执行（把仓库根加入 `PYTHONPATH`；事件识别会 import `algorithms.event_detection`）：

```bash
cd <repo-root>
export PYTHONPATH=.
uvicorn backend.app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1
```

或一行：

```bash
PYTHONPATH=. uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

注意：

- 场景预设定义在 `backend/app/scenario/presets.py`，与 `algorithms/` 训练代码完全解耦
- 必须使用 `--workers 1`（算法控制器状态不跨进程共享）
- **local 模式**下活动仿真运行时不要使用 `--reload`，否则 reload 会导致本机会话丢失
- **redis 模式**下 reload/重启 API 不会停止已在 SUMO worker 中运行的会话

Swagger 文档：`http://localhost:8000/docs`

## 测试

```bash
PYTHONPATH=. pytest backend/tests -q
python -m compileall backend/app
```

## 命令行评估（管控算法对比）

### A. 无 Backend（推荐算法团队日常批跑）

直连 `SimulationManager` + `traffic_control`，指标走 `traffic_eval`：

```bash
python -m traffic_eval \
  --preset xiongan_20 --period morning_peak --duration 900 \
  --modes fixed,max_pressure,sotl --seed 42 \
  --output outputs/eval_900_local.json
```

### B. 经 Backend HTTP（联调 / 与前端同路径）

脚本位于 `backend/tools/eval.py`。无头默认走 **libsumo**（`gui=false`），默认步长 **0.1s**、快照间隔 **0.5s**；支持 `fixed / max_pressure / sotl / ippo / mappo`。

```bash
# 后端需已启动
PYTHONPATH=. uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --workers 1

python backend/tools/eval.py
python backend/tools/eval.py \
  --preset xiongan_20 --period morning_peak --duration 900 \
  --modes fixed,max_pressure,sotl,ippo,mappo --seed 42 \
  --output outputs/eval_results.json
```

---

# 功能改动说明

## A. 多会话与 Redis

- 支持连续创建多个 session；状态可包括 `QUEUED` / `STARTING` / `RUNNING` / `PAUSED` / `STOPPING` / `STOPPED` / `COMPLETED` / `FAILED`
- `QUEUED` 时仅允许查询与停止；pause / resume / 倍速 / 加事件会返回业务错误（如 `SESSION_QUEUED`）
- 新增 `GET /api/v1/simulations`：分页列出会话，可按 `state` 筛选
- WebSocket 可从 `QUEUED` 一直推送到终态
- 未知 session → **404**；Redis 不可用 → **503**

## B. 评估指标

统一口径，结果字段可为 `null`（不可用时不伪造 0；急刹车事件数为 0 时仍返回 0）：

| 指标 | 字段（API） | 终态口径 |
|------|-------------|----------|
| 平均行程时间 | `avg_travel_time` | TripInfo 已完成且未 vaporize 车辆的 `duration` 均值 |
| 平均等待时间 | `avg_waiting_time` | 同上车辆的 `waitingTime` 均值 |
| 平均排队长度 | `avg_queue_length` | 仅 `role=incoming` 进口道；每帧车道均值再对时间平均（veh/lane） |
| 通行能力 | `throughput` | `arrived / evaluation_duration_s × 3600` |
| 决策延迟 | `avg_decision_latency_ms` | `AlgorithmRuntimeStore` 的 perf_counter；Fixed 无样本为 `null` |
| 燃油强度 | `fuel_consumption` / `fuel_intensity_L_per_100km` | 见下方「百公里油耗」 |
| 急刹车事件数 | `hard_braking_events` | 终态快照 `metrics.hard_braking_events`（单调累计，取终态/历史最大，禁止多帧相加） |
| 急刹车率 | `hard_braking_rate` | `hard_braking_events / departed × 100`，单位「次/100辆」 |

### 百公里油耗（终态正式结果）

1. 直接解析 `outputs/sessions/{session_id}/tripinfo.xml`
2. **不**把快照采样的 vehicle distance/fuel 作为终态正式结果（运行中可作 `snapshot_provisional` 临时值）
3. 当前 SUMO 配置下 `emissions.fuel_abs` 单位按 **mg** 处理
4. 从 `session_manifest` / `traffic_manifest` 读取车型 `powertrain` 与 `fuel_density_mg_per_ml`
5. 只统计：**已完成**、**未 vaporized**、且 `powertrain ∈ {gasoline, diesel, hybrid}` 的同一批车辆；`electric` 等排除
6. 单车：`fuel_ml = fuel_abs_mg / fuel_density_mg_per_ml`，`distance_m = tripinfo.routeLength`
7. 汇总（禁止先算单车百公里再平均）：

```text
fuel_L_per_100km = (sum(fuel_ml) / 1000) / (sum(distance_m) / 100000)
```

8. 无 TripInfo、缺 emissions、未知 vType（空类型或未登记类型）、密度非法或总里程为 0 → `null` + `warnings`，**不抛 500**；`citypulse_*` 扰动/活动车跳过不计入
9. `metric_sources["fuel_intensity_L_per_100km"] = "tripinfo_completed_fuel_vehicles"`

### 急刹车率

- 数据源：simulation 终态 `SimulationSnapshot.metrics.hard_braking_events`（backend **禁止**用低频快照加速度重估）
- `hard_braking_rate = hard_braking_events / departed × 100`（「每 100 辆的事件次数」，**不是**发生过急刹车的车辆占比，数值可超过 100）
- `departed=0` 或数据不可用 → `null` + warning；**0 次急刹车返回 0 而不是 null**
- `metric_sources["hard_braking_rate"] = "final_snapshot_hard_braking_events_per_100_departed"`

附加字段：

- `completion_rate`：`arrived/departed`（departed=0 时为 `null`）
- `metric_sources`：各指标数据来源
- `warnings`：缺失/不一致说明

仿真进行中可返回快照临时行程/等待/燃油，并标记 `snapshot_provisional`；终态读取共享目录 `outputs/sessions/{session_id}/tripinfo.xml` 回填行程/等待与燃油。TripInfo 缺失、解析失败或完成数与 `arrived` 不一致时对应指标为 `null` 并写入 warning，**不导致接口 500**。

WebSocket 进入终态时：先完成 TripInfo 解析、最终指标计算与持久化，再推送**一次**最终快照；该帧 `evaluation.finished=true`，行程/等待来源为 `tripinfo_completed`、燃油来源为 `tripinfo_completed_fuel_vehicles`（不可用则为 `null`），不会把运行中临时指标伪装成终态结果。

## C. OD 场景导出

仅当 `scenario_preset_id=xiongan_20`（雄安20路口路网）时，`POST /api/v1/scenarios/export` 的 ZIP **额外**包含：

```text
od/
├── od_matrix_{period}.csv
├── taz_9_zones.json
└── od_heatmap_{period}.png
```

`east_dense` / `west_dense` 只导出场景 SUMO 包（路网、路由、附加、events、manifest），**不**含全局九区域 OD/TAZ（该 OD 口径覆盖全部 20 路口，与局部预设不符）。

- TAZ 读取 `data/maps/sumo/official_traffic_demands.json` 顶层 `od_zones`（校验 zone_1～zone_9、20 路口无遗漏/重复）
- OD 矩阵读取 `generated/reports/traffic_od_{period}.*`（或 manifest 中路径）；缺失则导出失败并返回明确错误
- CSV 为固定顺序 9×9、单位 PCU；同区行程按报告规则（对角线为 0）
- 热力图下方 caption 为中文：典型出行需求（OD矩阵说明）、行列方向、PCU、全时段 OD、同区行程不计入（对角线为0）
- ZIP 内全部为相对路径，不写入服务器绝对路径
- `export_manifest.json` 含 `od_included` 及管控路口列表，便于核对预设差异
## D. 大型活动开场 / 散场

在原有 `lane_closure` / `speed_limit` / `accident` 之外，增加：

| event_type | 含义 | 关键字段 |
|------------|------|----------|
| `major_event_opening` | 开场（车辆汇入场馆） | `venue_lane_id`, `vehicle_count`, `source_lane_ids`, `vehicle_type_id` |
| `major_event_closing` | 散场（车辆离开场馆） | `venue_lane_id`, `vehicle_count`, `destination_lane_ids`, `vehicle_type_id` |

支持：

- 启动仿真时的 `disturbance_targets`
- 运行中 `POST /simulations/{session_id}/events`
- 经 `RedisSimulationManager` 传递到 SUMO worker

后端转换为 simulation 的 `MajorEventOpeningEvent` / `MajorEventClosingEvent`；**不会**再把开场/散场改成限速或占道。`source_lane_ids` / `destination_lane_ids` 为空时沿用 simulation 默认端点语义

---

# API 接口文档

统一前缀：`/api/v1`

## 通用错误格式

```json
{
  "detail": {
    "code": "ARTIFACTS_NOT_READY",
    "message": "Required SUMO generated artifacts are missing.",
    "missing_files": ["data/maps/sumo/generated/manifests/traffic_manifest.json"]
  }
}
```

| HTTP | 常见 code | 说明 |
|------|-----------|------|
| 422 | `REQUEST_VALIDATION_ERROR` | 请求参数校验失败 |
| 503 | `ARTIFACTS_NOT_READY` | SUMO 产物缺失 |
| 503 | `SUMO_HOME_UNAVAILABLE` | local 模式 SUMO 未配置 |
| 503 | `REDIS_UNAVAILABLE` | redis 模式 Redis 不可用 |
| 503 | `SIMULATION_MANAGER_NOT_READY` | 管理器未就绪 |
| 404 | `UNKNOWN_SESSION` | 仿真会话不存在 |
| 409 | `SIMULATION_BUSY` | local 模式已有仿真在运行 |
| 409 | `SESSION_QUEUED` | 排队中不允许该命令 |

---

## 1. 健康检查 Health

### 1.1 后端状态检查

**接口：** `GET /api/v1/health`

**说明：** 始终可调用；返回当前运行模式与依赖就绪情况

**返回示例（就绪）：** HTTP 200

```json
{
  "status": "ok",
  "app": "CityPulse-V2X Backend",
  "simulation_manager_mode": "local",
  "sumo_home_configured": true,
  "generated_artifacts_ready": true,
  "session_root_ready": true,
  "simulation_manager_ready": true,
  "redis_ready": true,
  "algorithm_base_url": "http://127.0.0.1:8000",
  "algorithm_state_shared": false,
  "recommended_uvicorn_workers": 1
}
```

redis 模式额外字段示例：`redis_state_url`、`redis_key_prefix`、`backend_redis_key_prefix`；Redis 失败时 `status=degraded` 且带 `redis_error`。**不要**把「API 容器没有 SUMO_HOME」单独误判为 redis 模式不可用

---

## 2. 地图配置 Config

### 2.1 获取地图运行时配置

**接口：** `GET /api/v1/config/map`

**返回示例：** HTTP 200

```json
{
  "cesium_ion_token": "your-cesium-ion-token",
  "tianditu_enabled": true
}
```

---

## 3. 仿真目录 Catalog

### 3.1 获取仿真目录

**接口：** `GET /api/v1/catalog`

**返回示例：** HTTP 200

```json
{
  "intersections": [],
  "scenario_presets": [
    {
      "preset_id": "east_dense",
      "label": "东部密集路口场景",
      "intersection_ids": ["demo_3", "demo_5", "demo_6", "demo_9"],
      "map_template": "east_dense"
    }
  ],
  "event_types": [
    "lane_closure",
    "speed_limit",
    "accident",
    "major_event_opening",
    "major_event_closing"
  ],
  "control_modes": ["fixed", "max_pressure", "sotl"],
  "playback_speeds": [1.0, 1.25, 1.5, 2.0, 3.0, 5.0]
}
```

---

## 4. 地图 Maps

### 4.1 获取路口 GeoJSON

**接口：** `GET /api/v1/maps/{intersection_id}/geojson?radius_m=600`

**返回示例：** HTTP 200

```json
{
  "intersection_id": "demo_3",
  "center": {"longitude": 116.0123, "latitude": 38.9876},
  "radius_m": 600.0,
  "bounds": {"west": 116.005, "south": 38.98, "east": 116.02, "north": 38.995},
  "geojson": {
    "type": "FeatureCollection",
    "features": []
  }
}
```

---

## 5. 地图瓦片 Tiles

### 5.1 天地图 WMTS 代理

**接口：** `GET /api/v1/tiles/tianditu/{layer}/wmts`

**Path：** `layer` 为 `img`（影像）或 `cia`（注记）

**返回：** HTTP 200，瓦片二进制；Token 未配置时 HTTP 503

---

## 6. 仿真 Simulations

### 6.0 列出会话

**接口：** `GET /api/v1/simulations?state=RUNNING&offset=0&limit=50`

**返回示例：**

```json
{
  "items": [
    {
      "session_id": "session-a1b2c3d4",
      "state": "QUEUED",
      "control_mode": "max_pressure",
      "scenario_preset_id": "xiongan_20",
      "progress": 0.0,
      "created_at": "2026-08-04T06:00:00+00:00",
      "updated_at": "2026-08-04T06:00:00+00:00",
      "metrics_status": "collecting"
    }
  ],
  "total": 1,
  "offset": 0,
  "limit": 50
}
```

### 6.1 启动仿真

**接口：** `POST /api/v1/simulations`

**请求体：**

```json
{
  "scenario_preset_id": "east_dense",
  "period": "morning_peak",
  "duration_seconds": 900,
  "control_mode": "fixed",
  "playback_speed": 1.0,
  "disturbance_targets": [
    {
      "event_type": "lane_closure",
      "intersection_id": "demo_3",
      "start_seconds": 60,
      "end_seconds": 300
    },
    {
      "event_type": "major_event_opening",
      "intersection_id": "demo_2",
      "start_seconds": 120,
      "end_seconds": 400,
      "vehicle_count": 30
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `scenario_preset_id` | `xiongan_20` / `east_dense` / `west_dense` |
| `period` | `morning_peak` / `off_peak` / `evening_peak` |
| `control_mode` | `fixed` / `max_pressure` / `sotl` |
| `playback_speed` | 可选：1.0 / 1.25 / 1.5 / 2.0 / 3.0 / 5.0 |
| `disturbance_targets` | 按路口描述的扰动（含大型活动）；后端解析为 lane 级事件 |

**返回示例：** HTTP 201

```json
{
  "session_id": "session-a1b2c3d4",
  "state": "QUEUED",
  "status_url": "/api/v1/simulations/session-a1b2c3d4",
  "websocket_url": "/api/v1/simulations/session-a1b2c3d4/stream",
  "metrics_url": "/api/v1/simulations/session-a1b2c3d4/metrics",
  "intelligence_url": "/api/v1/simulations/session-a1b2c3d4/intelligence",
  "scenario_preset_id": "east_dense"
}
```

（local 模式启动后常见为 `STARTING`/`RUNNING`；redis 模式首先多为 `QUEUED`）

### 6.2 查询仿真状态

**接口：** `GET /api/v1/simulations/{session_id}`

**`state`：** `QUEUED` / `STARTING` / `RUNNING` / `PAUSED` / `STOPPING` / `STOPPED` / `COMPLETED` / `FAILED`

状态与 WebSocket 快照额外包含（与扰动 `events` 分离）：

- `event_detection`：算法识别事件卡片（含经纬度、`display_label`、`prediction_summary`）
- `prediction`：官方路口未来约 60 秒 `vehicle_count`；`PREDICTION_MODEL_DIR` 指向NarrowNet-TDP交付包时为模型推理（206车道聚合到路口），否则 `moving_average` 降级（含 `fallback`/`fallback_reason`）
- `traffic_style.edges`：后端唯一计算的拥堵等级（`occupancy_pct` 为 0～100），供蓝线着色，独立于事件图标

`traffic_state` 展示约定：`localized_blockage`=疑似局部阻塞，`spillback`=排队溢出，`unknown_abnormal`=交通异常

### 6.2.1 查询事件识别与短时预测

**接口：**

- `GET /api/v1/simulations/{session_id}/intelligence`：完整智能分析结果
- `GET /api/v1/simulations/{session_id}/event-detection`：仅事件卡片
- `GET /api/v1/simulations/{session_id}/prediction`：仅短时预测

### 6.3 查询仿真指标

**接口：** `GET /api/v1/simulations/{session_id}/metrics`

```json
{
  "episode_id": "session-a1b2c3d4",
  "algorithm": "sotl",
  "avg_waiting_time": 12.3,
  "avg_travel_time": 85.6,
  "avg_queue_length": 2.1,
  "throughput": 880.0,
  "fuel_consumption": 8.5,
  "fuel_intensity_L_per_100km": 8.5,
  "hard_braking_events": 42,
  "hard_braking_rate": 12.0,
  "avg_decision_latency_ms": 1.234,
  "departed": 350,
  "arrived": 220,
  "completion_rate": 0.6286,
  "metric_sources": {
    "avg_queue_length_veh": "incoming_lane_halting_count",
    "throughput_veh_per_h": "finish_totals",
    "fuel_intensity_L_per_100km": "tripinfo_completed_fuel_vehicles",
    "hard_braking_rate": "final_snapshot_hard_braking_events_per_100_departed"
  },
  "warnings": [],
  "finished": true
}
```

不可用指标为 `null`（例如 Fixed 的 `avg_decision_latency_ms`）

### 6.4 停止仿真

**接口：** `POST /api/v1/simulations/{session_id}/stop`

```json
{"session_id": "session-a1b2c3d4", "state": "STOPPED"}
```

### 6.5 暂停仿真

**接口：** `POST /api/v1/simulations/{session_id}/pause`

### 6.6 恢复仿真

**接口：** `POST /api/v1/simulations/{session_id}/resume`

### 6.7 设置播放倍速

**接口：** `POST /api/v1/simulations/{session_id}/playback-speed`

**请求体：** `{"playback_speed": 2.0}`

### 6.8 添加扰动事件

**接口：** `POST /api/v1/simulations/{session_id}/events`

占道示例：

```json
{
  "event_type": "lane_closure",
  "event_id": "closure-001",
  "start_seconds": 100,
  "end_seconds": 400,
  "lane_ids": ["-30_0"]
}
```

大型活动开场示例：

```json
{
  "event_type": "major_event_opening",
  "event_id": "open-001",
  "start_seconds": 100,
  "end_seconds": 400,
  "venue_lane_id": "-2000_0",
  "vehicle_count": 20,
  "source_lane_ids": [],
  "vehicle_type_id": "citypulse_event_passenger"
}
```

大型活动散场示例：

```json
{
  "event_type": "major_event_closing",
  "event_id": "close-001",
  "start_seconds": 500,
  "end_seconds": 800,
  "venue_lane_id": "-2000_0",
  "vehicle_count": 20,
  "destination_lane_ids": [],
  "vehicle_type_id": "citypulse_event_passenger"
}
```

**返回：** HTTP 201，`{"event_id": "..."}`

### 6.9 取消扰动事件

**接口：** `DELETE /api/v1/simulations/{session_id}/events/{event_id}`

**返回：** HTTP 204

### 6.10 WebSocket 实时快照

**接口：** `WS /api/v1/simulations/{session_id}/stream`

可从 `QUEUED` 推送到终态。终态帧在指标 finalize（含 TripInfo 回填）之后发送，且仅发送一次。快照消息：

```json
{
  "type": "snapshot",
  "data": {"session_id": "...", "state": "RUNNING", "vehicles": []}
}
```

心跳消息：

```json
{
  "type": "heartbeat",
  "session_id": "...",
  "timestamp": "2026-08-04T06:00:00+00:00"
}
```

---

## 7. 场景导出 Scenarios

### 7.1 导出场景 ZIP

**接口：** `POST /api/v1/scenarios/export`

**请求体：** 与启动仿真相同（`StartSimulationRequest`），编译 SUMO 配置并打包下载，**不启动仿真**

**返回：** HTTP 200，`Content-Type: application/zip`

ZIP 主要内容：

```text
session.sumocfg
session.rou.xml
session.add.xml
*.net.xml
events.json
export_manifest.json
od/                         # 仅 xiongan_20
  od_matrix_{period}.csv
  taz_9_zones.json
  od_heatmap_{period}.png
```

- `xiongan_20`：含全局九区域 OD/TAZ/热力图；OD 缺失或 TAZ 校验失败时返回明确错误
- `east_dense` / `west_dense`：不含 `od/`；manifest 中 `od_included=false`
- 不同预设的差异主要体现在：下载文件名、管控路口集合（`compile_session_scenario` 的 control/observation 范围）、events，以及是否包含 OD；路网文件仍为同一份全网 `*.signals.net.xml`

---

## 8. 内部算法协议 Internal Algorithm

> 供 SUMO worker 回调，非前端接口。算法名：`max_pressure` / `sotl`

Worker 应回调 `ALGORITHM_BASE_URL` + `/api/v1/internal/algorithm/{name}/...`

### 8.1 初始化

**接口：** `POST /api/v1/internal/algorithm/{algorithm_name}/initialize`

### 8.2 决策 Step

**接口：** `POST /api/v1/internal/algorithm/{algorithm_name}/step`

### 8.3 结束

**接口：** `POST /api/v1/internal/algorithm/{algorithm_name}/finish`

**返回：** `{"ok": true}`

---

## 接口总览

| 模块 | 方法 | 路径 |
|------|------|------|
| 1 Health | GET | `/health` |
| 2 Config | GET | `/config/map` |
| 3 Catalog | GET | `/catalog` |
| 4 Maps | GET | `/maps/{id}/geojson` |
| 5 Tiles | GET | `/tiles/tianditu/{layer}/wmts` |
| 6 Simulations | GET | `/simulations` |
| 6 Simulations | POST | `/simulations` |
| 6 Simulations | GET | `/simulations/{id}` |
| 6 Simulations | GET | `/simulations/{id}/metrics` |
| 6 Simulations | GET | `/simulations/{id}/intelligence` |
| 6 Simulations | GET | `/simulations/{id}/event-detection` |
| 6 Simulations | GET | `/simulations/{id}/prediction` |
| 6 Simulations | POST | `/simulations/{id}/stop` |
| 6 Simulations | POST | `/simulations/{id}/pause` |
| 6 Simulations | POST | `/simulations/{id}/resume` |
| 6 Simulations | POST | `/simulations/{id}/playback-speed` |
| 6 Simulations | POST | `/simulations/{id}/events` |
| 6 Simulations | DELETE | `/simulations/{id}/events/{event_id}` |
| 6 Simulations | WS | `/simulations/{id}/stream` |
| 7 Scenarios | POST | `/scenarios/export` |
| 8 Internal | POST | `/internal/algorithm/{name}/initialize\|step\|finish` |

---

## 场景预设

| preset_id | 管控路口 |
|-----------|----------|
| `xiongan_20` | demo_1 … demo_20 |
| `east_dense` | demo_3、demo_5、demo_6、demo_9 |
| `west_dense` | demo_14、demo_15、demo_19 |

## 管控算法

| control_mode | 说明 |
|--------------|------|
| `fixed` | SUMO 固定配时（决策延迟通常为 N/A） |
| `max_pressure` | Max Pressure 压力控制 |
| `sotl` | Gershenson SOTL-phase / platoon |

## 安全约定

- 不要提交 `.env` 或真实 token
- Cesium / 天地图 token 通过本地环境变量或部署 Secrets 注入
- redis 模式勿将 Redis 6379/6380 暴露到公网
