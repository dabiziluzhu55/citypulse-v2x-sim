# 后端 API 文档

入口：`backend/app/main.py`（`uvicorn backend.app.main:app`）。  
后端包装 `SimulationManager`，提供真实 SUMO 仿真接口；**不再提供 Mock 假后端**。

## 仿真后端 API

后端应直接包装 `SimulationManager`，不要自行启动 TraCI。

对外 HTTP/WebSocket（`/api/v1`）与内核调用对应关系：

| HTTP/WebSocket | 仿真内核 / 控制层 |
|---|---|
| `GET /api/v1/catalog` | `manager.catalog()` |
| `POST /api/v1/simulations` | `SimulationControlService.start()` → `manager.start(config)` |
| `GET /api/v1/simulations/{id}` | `manager.snapshot(id)`（可附带 evaluation） |
| `GET /api/v1/simulations/{id}/metrics` | 评估指标（算法采集或 fixed 兜底） |
| `POST /api/v1/simulations/{id}/stop` | `manager.stop(id)` |
| `POST /api/v1/simulations/{id}/events` | `manager.add_event(id, event)` |
| `DELETE /api/v1/simulations/{id}/events/{event_id}` | `manager.cancel_event(id, event_id)` |
| `WS /api/v1/simulations/{id}/stream` | `manager.subscribe(id)` |

内部算法协议（仅供 SUMO worker 回调，非前端业务 API）：

| HTTP | 说明 |
|---|---|
| `POST /api/v1/internal/algorithm/{name}/initialize` | 协议 2.0 初始化 |
| `POST /api/v1/internal/algorithm/{name}/step` | 协议 2.0 决策 |
| `POST /api/v1/internal/algorithm/{name}/finish` | 协议 2.0 结束 |

内核会话类型、字段约束与示例见 [simulation_core_api.md](simulation_core_api.md)。  
算法协议见 [algorithm_interface.md](algorithm_interface.md)。  
启动与联调说明见 [backend/README.md](../backend/README.md)。

### 端口与访问链路

| 服务 | 地址 |
|---|---|
| 前端页面 | `http://localhost:5173` |
| FastAPI | `http://localhost:8000` |
| Swagger | `http://localhost:8000/docs` |
| OpenAPI | `http://localhost:8000/openapi.json` |
| 健康检查 | `http://localhost:8000/api/v1/health` |

开发环境中，浏览器只访问前端同源地址；Vite 将 `/api` 转发到 `http://127.0.0.1:8000`。

## 安全约定

`.env` 和真实 Cesium/天地图 token 不得提交到仓库；部署时通过环境变量或 Secrets 注入。
