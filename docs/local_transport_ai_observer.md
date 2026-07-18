# 本地算法传输与 AI 观察者

本地算法和 AI 观察者都是可信 Python 模块，与 SUMO 仿真 worker 位于同一进程。它们不调用
TraCI；仿真内核把普通 `dict/list` 数据传入模块。本地算法与 HTTP 协议 2.0 的字段和校验完全
一致，只省去网络及 JSON 编解码。后端快照、WebSocket 和前端接口不受影响。

## 本地管控算法

模块必须实现：

```python
def initialize(payload: dict) -> dict: ...
def step(payload: dict) -> dict: ...
def finish(payload: dict) -> object: ...
```

`initialize` 接收 `SimulationMetadata` 并返回 `ready=true`；`step` 接收与 HTTP `/step` 相同的
路口、车道、车辆和汇总状态，并返回 `actions.signals` 与 `actions.vehicles`；`finish` 接收结束
原因及汇总指标。本地 `step` 在 SUMO worker 中同步执行，因此仿真会等待控制动作。

```bash
python -m simulation.sumo.run --mode algorithm \
  --algorithm-transport local \
  --algorithm-module algorithms.local_policy_example \
  --intersection demo_2 --period morning_peak
```

HTTP 仍是默认方式。使用 HTTP 时继续传 `--algorithm-endpoint`，不需要改算法服务。模块导入
失败、缺少函数、函数抛出异常或协议响应非法都会使本轮会话进入 `FAILED`。

## AI 观察者

AI 模块只读数据，不返回预测或动作：

```python
def initialize(metadata: dict) -> None: ...
def on_frame(frame: dict) -> None: ...
def finish(summary: dict) -> None: ...
```

它可与固定配时、HTTP 算法或本地算法同时启用：

```bash
python -m simulation.sumo.run --mode fixed \
  --ai-observer-module algorithms.ai_observer_example \
  --ai-frame-interval 0.1 \
  --intersection demo_2 --period morning_peak
```

`initialize` 在第一个 SUMO 仿真步之前同步接收扩展后的静态元数据。`on_frame` 接收：

- `protocol_version`、`episode_id`、单调递增的 `frame_id` 和 `simulation_time`；
- 完整的 `intersections` 车道实时状态和 `vehicles` 遥测；
- `traffic` 汇总及 `previous_action_results` 最近控制结果。

默认每 0.1 秒仿真时间生产一帧；设为 0.05 秒时可与当前默认 SUMO step 对齐。暂停时仿真时间
冻结，不产生帧。观察者在线程中消费容量为 1 的最新帧队列，推理较慢时尚未消费的旧帧被
覆盖，SUMO 不等待；`frame_id` 不重新编号，所以序号缺口就是跳帧证据。

自然结束或人工停止时，仿真停止生产，投递最终帧，等待当前帧和队列内最新帧，然后调用一次
`finish`。结束汇总的 `observer_frames` 包含 `generated`、`consumed`、`dropped`。后台异常会在
仿真循环中传播并使会话失败；排空或 `finish` 超过默认 5 秒也会失败。

## 车道字段口径

静态车道同时保留 `length/max_speed` 和带单位别名 `length_m/speed_limit_mps`。进口车道有
`approach_id=<intersection_id>_<topology_approach>_in`、多值 `movements` 和去重排序后的
`downstream_lane_ids`。转向词汇固定为 `through/left/right/uturn`。纯出口车道的进口相关
字段为空。

实时帧按 connection 直接读取 SUMO 原始 `G/g/r/y` 灯色。同车道灯色相同则车道
`signal_state` 为该字符，不同则为 `mixed`；任何 connection 为 `G/g` 时 `lane_has_green=true`。
纯出口车道的信号汇总为 `null`。

`queue_length_m` 当前是确定性空间估算值，综合停车队尾到停止线范围、停车车辆平均占用空间
和 lane occupancy，并限制在 `[0, length_m]`；`queue_length_is_estimate` 始终为 `true`。

`current_allowed_speed_mps` 是车道此刻的允许速度，可反映施工限速或封闭。它与单车动作的
`target_speed_mps` 不同；当前车道受速度租约车辆的数量、最小目标和平均目标分别在
`controlled_vehicle_count`、`min_target_speed_mps`、`mean_target_speed_mps` 中给出。
