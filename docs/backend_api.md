# 后端 API 文档

完整 Mock 规格见 **[backend_mock_spec.md](./backend_mock_spec.md)**。

## 端口与访问链路

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

## 快速索引

- API Base URL：`/api/v1`
- 写接口：`POST /scenarios`、`POST /runs`、`POST /runs/{run_id}/control`、`POST /runs/{run_id}/algorithm`
- WebSocket 主入口：`/api/v1/ws/runs/{run_id}`
- WebSocket 兼容入口：`/api/v1/ws?run_id={run_id}`
- 地图：`GET /scenario-templates` 返回 `map_center`、`map_bounds` 和 `default_zoom`
- 3D Tiles：`GET /3dtiles/xiongan/tileset.json`

## 环境变量

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

## Swagger 说明

Swagger 已按“系统、场景、仿真、态势、协同、算法、事件、指标”分类，并提供请求与响应模型。WebSocket 不支持在 Swagger 中直接执行，应使用前端页面或 WebSocket 客户端测试。

## 联调 Fixture

- 场景创建示例：`fixtures/scenario_create.json`

## 安全约定

`.env` 和真实 Cesium/天地图 token 不得提交到仓库；部署时通过环境变量或 Secrets 注入。
