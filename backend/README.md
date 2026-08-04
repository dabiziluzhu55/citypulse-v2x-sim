# CityPulse V2X Backend

基于 `SimulationManager` 的真实 SUMO 仿真后端，入口为 `backend.app.main:app`。

- 前端本地运行在 `http://localhost:5173`
- 后端本地运行在 `http://localhost:8000`

## 架构边界

- FastAPI **不直接** import 或调用 `traci`
- FastAPI **不通过 subprocess** 启动 `python -m simulation.sumo.run`
- FastAPI **不自行**启动 `sumo` / `sumo-gui`
- 唯一 TraCI 所有者是 `SimulationManager` 内部工作线程
- 全应用只创建一个全局 `SimulationManager` 实例
- 同一时间只允许一个活动仿真会话

分层职责：

| 层 | 路径 | 说明 |
|---|---|---|
| API | `app/api/v1/` | 对外统一 REST / WebSocket |
| 管控算法 | `app/controllers/` | fixed / max_pressure / sotl 等纯决策逻辑 |
| 指标计算 | `app/metrics/` | 评估指标采集与汇总 |
| 场景层 | `app/scenario/` | 场景预设与启动请求解析 |
| 内部算法协议 | `app/api/v1/internal_algorithm.py` | 供 SUMO worker HTTP 回调 |

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
python -m simulation.sumo.build_tls
python -m simulation.sumo.build_traffic
```

生成目录：

```text
data/maps/sumo/generated/
├── manifests/traffic_manifest.json
├── manifests/tls_manifest.json
└── network/TotalMap_20.signals.net.xml
```

若这些文件缺失，后端仍可启动，但 `/api/v1/health` 会返回 `status: degraded`；仿真相关接口返回 `503`。

## 安装依赖

在仓库根目录执行：

```bash
pip install -r backend/requirements.txt
```

## 启动后端

在仓库根目录执行：

```bash
uvicorn backend.app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1
```

注意：

- 必须使用 `--workers 1`
- **活动仿真运行时不要使用** `--reload`，否则 reload 会导致活动会话丢失

Swagger 文档：`http://localhost:8000/docs`

## 测试

```bash
pytest backend/tests -q
python -m compileall backend/app
```

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
| 503 | `SUMO_HOME_UNAVAILABLE` | SUMO 未配置 |
| 404 | `UNKNOWN_SESSION` | 仿真会话不存在 |
| 409 | `SIMULATION_BUSY` | 已有仿真在运行 |

---

## 1. 健康检查 Health

### 1.1 后端状态检查

**接口：** `GET /api/v1/health`

**说明：** 不依赖 SUMO 产物，始终可调用。

**返回示例（就绪）：** HTTP 200

```json
{
  "status": "ok",
  "app": "CityPulse-V2X Backend",
  "sumo_home_configured": true,
  "generated_artifacts_ready": true,
  "simulation_manager_ready": true
}
```

**返回示例（未就绪）：** HTTP 200（`status` 为 `degraded`；业务接口在未就绪时返回 503）

```json
{
  "status": "degraded",
  "app": "CityPulse-V2X Backend",
  "sumo_home_configured": false,
  "generated_artifacts_ready": false,
  "simulation_manager_ready": true,
  "missing_files": [
    "data/maps/sumo/generated/manifests/traffic_manifest.json"
  ]
}
```

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
  "intersections": [
    {
      "intersection_id": "demo_3",
      "longitude": 116.0123,
      "latitude": 38.9876,
      "periods": ["morning_peak", "off_peak", "evening_peak"],
      "origins": [
        {
          "origin_id": "origin_north",
          "label": "北向入口",
          "lane_ids": ["-30_0"]
        }
      ],
      "lanes": [
        {
          "lane_id": "-30_0",
          "edge_id": "-30",
          "lane_index": 0,
          "role": "incoming",
          "approach": "north",
          "approach_label": "北",
          "length": 120.5,
          "max_speed": 13.89
        }
      ]
    }
  ],
  "scenario_presets": [
    {
      "preset_id": "east_dense",
      "label": "东部密集路口场景",
      "intersection_ids": ["demo_3", "demo_5", "demo_6", "demo_9"],
      "map_template": "east_dense"
    },
    {
      "preset_id": "west_dense",
      "label": "西部密集路口场景",
      "intersection_ids": ["demo_14", "demo_15", "demo_19"],
      "map_template": "west_dense"
    },
    {
      "preset_id": "xiongan_20",
      "label": "雄安20路口路网",
      "intersection_ids": ["demo_1", "demo_2", "demo_20"],
      "map_template": "xiongan20"
    }
  ],
  "event_types": ["lane_closure", "speed_limit", "accident"],
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
| `disturbance_targets` | 按路口描述的扰动（后端解析为 lane 级事件） |

**返回示例：** HTTP 201

```json
{
  "session_id": "session-a1b2c3d4",
  "state": "RUNNING",
  "status_url": "/api/v1/simulations/session-a1b2c3d4",
  "websocket_url": "/api/v1/simulations/session-a1b2c3d4/stream",
  "metrics_url": "/api/v1/simulations/session-a1b2c3d4/metrics",
  "scenario_preset_id": "east_dense"
}
```

### 6.2 查询仿真状态

**接口：** `GET /api/v1/simulations/{session_id}`

**返回示例：** HTTP 200

```json
{
  "session_id": "session-a1b2c3d4",
  "state": "RUNNING",
  "sequence": 42,
  "elapsed_seconds": 85.6,
  "duration_seconds": 900.0,
  "progress": 0.0951,
  "official_time": "07:31:25",
  "playback_speed": 1.0,
  "intersections": {},
  "vehicles": [],
  "events": [],
  "metrics": {},
  "evaluation": null,
  "error": null
}
```

**`state`：** `RUNNING` / `PAUSED` / `STOPPED` / `COMPLETED` / `FAILED`

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
  "avg_decision_latency_ms": 1.234,
  "departed": 350,
  "arrived": 220,
  "finished": false
}
```

### 6.4 停止仿真

**接口：** `POST /api/v1/simulations/{session_id}/stop`

```json
{"session_id": "session-a1b2c3d4", "state": "STOPPED"}
```

### 6.5 暂停仿真

**接口：** `POST /api/v1/simulations/{session_id}/pause`

```json
{"session_id": "session-a1b2c3d4", "state": "PAUSED", "playback_speed": 1.0}
```

### 6.6 恢复仿真

**接口：** `POST /api/v1/simulations/{session_id}/resume`

```json
{"session_id": "session-a1b2c3d4", "state": "RUNNING", "playback_speed": 1.0}
```

### 6.7 设置播放倍速

**接口：** `POST /api/v1/simulations/{session_id}/playback-speed`

**请求体：** `{"playback_speed": 2.0}`

**返回：**

```json
{"session_id": "session-a1b2c3d4", "state": "RUNNING", "playback_speed": 2.0}
```

### 6.8 添加扰动事件

**接口：** `POST /api/v1/simulations/{session_id}/events`

```json
{
  "event_type": "lane_closure",
  "event_id": "closure-001",
  "start_seconds": 100,
  "end_seconds": 400,
  "lane_ids": ["-30_0"]
}
```

**返回：** HTTP 201，`{"event_id": "closure-001"}`

### 6.9 取消扰动事件

**接口：** `DELETE /api/v1/simulations/{session_id}/events/{event_id}`

**返回：** HTTP 204

### 6.10 WebSocket 实时快照

**接口：** `WS /api/v1/simulations/{session_id}/stream`

**快照消息：**

```json
{
  "type": "snapshot",
  "data": {"session_id": "...", "state": "RUNNING", "vehicles": []}
}
```

**心跳消息：**

```json
{
  "type": "heartbeat",
  "session_id": "...",
  "timestamp": "2026-07-28T13:19:00+00:00"
}
```

---

## 7. 场景导出 Scenarios

### 7.1 导出场景 ZIP

**接口：** `POST /api/v1/scenarios/export`

**请求体：** 与启动仿真相同（`StartSimulationRequest`），编译 SUMO 配置并打包下载，不启动仿真。

**返回：** HTTP 200，`Content-Type: application/zip`

ZIP 含：`session.sumocfg`、`session.rou.xml`、`session.add.xml`、路网、`events.json`、`export_manifest.json`

---

## 8. 内部算法协议 Internal Algorithm

> 供 SUMO worker 回调，非前端接口。算法名：`max_pressure` / `sotl`

### 8.1 初始化

**接口：** `POST /api/v1/internal/algorithm/{algorithm_name}/initialize`

```json
{
  "protocol_version": "2.0",
  "episode_id": "session-a1b2c3d4",
  "decision_interval": 5.0,
  "minimum_green": 5.0,
  "intersections": {}
}
```

**返回：**

```json
{"protocol_version": "2.0", "episode_id": "session-a1b2c3d4", "ready": true}
```

### 8.2 决策 Step

**接口：** `POST /api/v1/internal/algorithm/{algorithm_name}/step`

**返回：**

```json
{
  "protocol_version": "2.0",
  "episode_id": "session-a1b2c3d4",
  "step_id": 1,
  "actions": {
    "signals": {"demo_3": {"target_phase": 2}},
    "vehicles": {}
  }
}
```

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
| 6 Simulations | POST | `/simulations` |
| 6 Simulations | GET | `/simulations/{id}` |
| 6 Simulations | GET | `/simulations/{id}/metrics` |
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
| `fixed` | SUMO 固定配时 |
| `max_pressure` | Max Pressure 压力控制 |
| `sotl` | Gershenson SOTL-phase / platoon |

## 安全约定

- 不要提交 `.env` 或真实 token
- Cesium / 天地图 token 通过本地环境变量或部署 Secrets 注入
