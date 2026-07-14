# 后端 API 映射建议

本轮未实现 FastAPI。后端应直接包装 `SimulationManager`，不要自行启动 TraCI。

| 未来 HTTP/WebSocket | 仿真内核调用 |
|---|---|
| `GET /simulation/catalog` | `manager.catalog()` |
| `POST /simulation/sessions` | `manager.start(config)` |
| `GET /simulation/sessions/{id}` | `manager.snapshot(id)` |
| `POST /simulation/sessions/{id}/stop` | `manager.stop(id)` |
| `POST /simulation/sessions/{id}/events` | `manager.add_event(id, event)` |
| `DELETE /simulation/sessions/{id}/events/{event_id}` | `manager.cancel_event(id, event_id)` |
| `WS /simulation/sessions/{id}/stream` | `manager.subscribe(id)` |

完整 Python 类型、字段约束和示例见 [simulation_core_api.md](simulation_core_api.md)。
