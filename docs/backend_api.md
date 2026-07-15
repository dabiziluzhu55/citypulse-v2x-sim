# 后端 API 文档

完整 Mock 规格见 **[backend_mock_spec.md](./backend_mock_spec.md)**。

## 快速索引

- Base URL：`/api/v1`
- 开发代理：`localhost:5173` → `localhost:8000`
- 写接口：`POST /scenarios`、`POST /runs`、`POST /runs/{run_id}/control`、`POST /runs/{run_id}/algorithm`
- WebSocket：`/api/v1/ws?run_id=`、`/api/v1/ws/runs/{run_id}`
- 地图：`GET /scenario-templates` 返回 `map_center` / `map_bounds` / `default_zoom`（前端 fallback 见 `constants/mapDefaults.ts`）

## 联调 Fixture

- 场景创建示例：`fixtures/scenario_create.json`
