# 后端 API 文档

本项目包含两套后端，分别服务于不同场景：

| 后端 | 入口 | 用途 |
|---|---|---|
| 仿真后端 | `backend/app/main.py` | 包装 `SimulationManager`，提供真实 SUMO 仿真接口 |
| Mock 后端 | `backend/main.py` | 前端联调 Mock，实现 `backend_mock_spec.md` 中的契约 |

## 仿真后端 API

后端应直接包装 `SimulationManager`，不要自行启动 TraCI。

| HTTP/WebSocket | 仿真内核调用 |
|---|---|
| `GET /simulation/catalog` | `manager.catalog()` |
| `POST /simulation/sessions` | `manager.start(config)` |
| `GET /simulation/sessions/{id}` | `manager.snapshot(id)` |
| `PUT /simulation/sessions/{id}/playback` | `manager.set_playing(id, playing)` |
| `PUT /simulation/sessions/{id}/playback-speed` | `manager.set_playback_speed(id, speed)` |
| `POST /simulation/sessions/{id}/stop` | `manager.stop(id)` |
| `POST /simulation/sessions/{id}/events` | `manager.add_event(id, event)` |
| `DELETE /simulation/sessions/{id}/events/{event_id}` | `manager.cancel_event(id, event_id)` |
| `WS /simulation/sessions/{id}/stream` | `manager.subscribe(id)` |

完整 Python 类型、字段约束和示例见 [simulation_core_api.md](simulation_core_api.md)。

交互式前端创建会话时应传 `start_paused=true` 和初始 `playback_speed=1.0`。创建成功、
WebSocket 建立后再将 `playing` 设为 `true`，这样加载页面期间仿真时间不会提前流逝。

两个播放控制接口的请求体固定为：

```http
PUT /simulation/sessions/{id}/playback
Content-Type: application/json

{"playing": true}
```

```http
PUT /simulation/sessions/{id}/playback-speed
Content-Type: application/json

{"speed": 2.0}
```

两个接口成功后都返回最新 snapshot。会话不存在返回 `404`，会话已经结束返回 `409`，
请求字段或倍速非法返回 `422`。前端不需要等待下一条 WebSocket 消息再更新按钮状态。

## Mock 后端 API

完整 Mock 规格见 **[backend_mock_spec.md](./backend_mock_spec.md)**。

### 端口与访问链路

| 服务 | 地址 |
|---|---|
| 前端页面 | `http://localhost:5173` |
| FastAPI | `http://localhost:8000` |
| Swagger | `http://localhost:8000/docs` |
| OpenAPI | `http://localhost:8000/openapi.json` |
| 健康检查 | `http://localhost:8000/health` |

开发环境中，浏览器只访问前端同源地址：

- HTTP：`http://localhost:5173/api/v1/...`
- WebSocket：`ws://localhost:5173/api/v1/ws/runs/{run_id}`
- 3D Tiles：`http://localhost:5173/3dtiles/xiongan/tileset.json`

Vite 将 `/api` 和 `/3dtiles` 转发到 `http://127.0.0.1:8000`。

### 快速索引

- API Base URL：`/api/v1`
- 写接口：`POST /scenarios`、`POST /runs`、`POST /runs/{run_id}/control`、`POST /runs/{run_id}/algorithm`
- WebSocket 主入口：`/api/v1/ws/runs/{run_id}`
- WebSocket 兼容入口：`/api/v1/ws?run_id={run_id}`
- 地图：`GET /scenario-templates` 返回 `map_center`、`map_bounds` 和 `default_zoom`
- 3D Tiles：`GET /3dtiles/xiongan/tileset.json`

### 环境变量

```text
VITE_BACKEND_PROXY_TARGET=http://127.0.0.1:8000
VITE_API_BASE_URL=/api/v1
VITE_TRAFFIC_WS_URL=
VITE_XIONGAN_3DTILES_URL=/3dtiles/xiongan/tileset.json
```

`VITE_TRAFFIC_WS_URL` 留空时使用同源 WebSocket；生产环境分域时可配置：

```text
VITE_TRAFFIC_WS_URL=wss://api.example.com/api/v1/ws/runs/{run_id}
```

### 联调 Fixture

- 场景创建示例：`fixtures/scenario_create.json`

## 安全约定

`.env` 和真实 Cesium/天地图 token 不得提交到仓库；部署时通过环境变量或 Secrets 注入。
