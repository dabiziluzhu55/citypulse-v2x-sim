# 后端 API 映射建议

本轮未实现 FastAPI。后端应直接包装 `SimulationManager`，不要导入或自行启动 libsumo、
TraCI。后端把 `manager.subscribe()` 产生的 snapshot 转为 WebSocket 消息，前端只消费该
消息实时渲染，不直接连接 SUMO runtime。

| 未来 HTTP/WebSocket | 仿真内核调用 |
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

## 扰动事件类型

`POST /simulation/sessions/{id}/events` 应接受 catalog 中声明的全部事件类型：
`lane_closure`、`speed_limit`、`accident`、`major_event_opening` 和 `major_event_closing`。
其中大型活动开场/散场是运行时额外注入车流，不修改官方需求文件，也不计入 `planned_vehicle_count`。
