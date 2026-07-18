# SUMO 仿真内核接口

本接口供后端进程直接调用。后端不得直接调用 TraCI；`SimulationManager` 的 worker
线程是唯一 TraCI 所有者。当前一次只允许一个活动会话。

## 查询能力

```python
from simulation.sumo import SimulationManager

manager = SimulationManager()
catalog = manager.catalog()
```

`catalog.intersections` 给出当前已生成的路口、可用时段、官方进口、坐标和可用于扰动的
lane。前端应从 catalog 生成选项，不要硬编码 `demo_2`、`west` 或 SUMO lane ID。

运行前必须先执行：

```bash
python -m simulation.sumo.build_tls --intersections demo_2
```

该命令在 `generated/manifests/` 下生成 schema v2 的信号与车流 manifest。旧生成物会被
内核拒绝，重新构建即可。

## 启动会话

```python
from simulation.sumo import SimulationConfig, SimulationManager

manager = SimulationManager()
session_id = manager.start(
    SimulationConfig(
        intersection_ids=("demo_2",),
        period="morning_peak",
        origins={"demo_2": ("west",)},
        window_start_seconds=1800,
        duration_seconds=1200,
        flow_multiplier=1.5,
        control_mode="fixed",
        start_paused=True,
        playback_speed=1.0,
        seed=42,
    )
)
```

字段规则：

| 字段 | 规则 |
|---|---|
| `intersection_ids` | 非空、唯一，且必须出现在 catalog 中 |
| `period` | `morning_peak`、`off_peak`、`evening_peak` |
| `origins` | `intersection_id -> 官方进口列表`；省略某路口表示全部进口 |
| `window_start_seconds` | 相对该高峰开始的偏移，必须大于等于 0 |
| `duration_seconds` | 大于 0 且不能超过该高峰剩余时间；`None` 表示运行到时段末尾 |
| `flow_multiplier` | 启动前固定的全局倍率，范围 `0.1-5.0` |
| `control_mode` | `fixed` 或 `algorithm` |
| `algorithm_transport` | `http`（默认）或 `local`；仅 algorithm 模式使用 |
| `algorithm_endpoint` | HTTP algorithm 模式必填，协议见 `algorithm_interface.md` |
| `algorithm_module` | local algorithm 模式必填，例如 `algorithms.local_policy_example` |
| `ai_observer_module` | 可选的本地 AI 观察模块；可与 fixed、HTTP 或 local 控制并用 |
| `ai_frame_interval_seconds` | AI 帧仿真时间间隔，默认 0.1 秒且不得小于 `step_length` |
| `ai_observer_shutdown_timeout` | 结束时排空 AI 帧并调用 finish 的超时，默认 5 秒 |
| `start_paused` | `True` 时 SUMO 加载完成后停在 `elapsed=0`，等待 `resume()` |
| `playback_speed` | 初始播放倍速，只允许 `1、1.25、1.5、2、3、5`；`None` 表示不限速 |
| `realtime` | 兼容参数；`playback_speed=None` 时，`True` 表示按 `1×` 播放 |
| `snapshot_interval_seconds` | 快照的仿真时间间隔，默认 0.5 秒 |

时间窗口会被平移为本轮 `elapsed_seconds=0`。例如早高峰偏移 1800 秒对应官方
`07:30:00`。车辆数按窗口重叠比例和倍率确定，并使用确定性最大余数法取整。

## 开始、暂停与倍速

交互式会话建议使用 `start_paused=True` 创建。SUMO 和 TraCI 初始化完成后，snapshot 状态
会从 `STARTING` 变为 `PAUSED`，但尚未执行第一个仿真步。前端准备好后调用：

```python
manager.resume(session_id)                   # 开始或继续
manager.pause(session_id)                    # 暂停
manager.set_playing(session_id, True)         # 后端布尔播放接口
manager.set_playback_speed(session_id, 2.0)  # 运行中或暂停时均可变速
```

`pause()` 和 `resume()` 都是幂等的，重复点击不会报错。暂停期间车辆、红绿灯、事件时间、
算法决策周期和官方时钟全部冻结，但仍可变速、添加/取消事件、恢复或停止。倍速只改变
仿真相对于真实墙钟的播放速度，不改变车辆的物理速度、交通需求或算法参数。控制命令
在下一个仿真步边界生效；如果当时正在等待算法 HTTP 响应，最多会额外等待该请求的超时
时间。

允许倍速由 `catalog.playback_speeds` 返回，目前为：

```json
[1.0, 1.25, 1.5, 2.0, 3.0, 5.0]
```

snapshot 的 `state` 会在 `RUNNING` 与 `PAUSED` 之间切换，`playback_speed` 返回当前倍速。
历史批处理会话在 `realtime=False` 且未调用变速接口时不做墙钟限速，此时该字段为
`null`；第一次调用 `set_playback_speed()` 后立即进入受控播放模式。倍速是目标上限，若
SUMO、算法服务或服务器本身计算不够快，实际播放速度可能低于所选倍速。

## 实时状态

读取最新快照：

```python
snapshot = manager.snapshot(session_id)
```

订阅最新状态：

```python
subscription = manager.subscribe(session_id)
try:
    while True:
        snapshot = subscription.get(timeout=2)
        if snapshot.state in {"STOPPED", "COMPLETED", "FAILED"}:
            break
finally:
    subscription.close()
```

订阅队列容量为 1。后端消费较慢时旧快照会被新快照覆盖，不会阻塞 SUMO。

快照包含：

- 会话状态、序号、elapsed、duration、进度和官方时钟；
- 路口当前/待切换相位、GREEN/YELLOW/CLEARANCE 和 lane 指标；
- 活动车辆 ID、x/y、速度、角度、road 和 lane；
- 官方可控车辆的车型、加速度、车道位置、路线、允许速度、等待/延误/里程、下一受控
  信号、瞬时/累计油耗、急制动次数和当前速度/换道目标；事故占位车仍可见但
  `controllable=false`；
- 事件状态；
- 累计出发/到达、活动/剩余/停车车辆、总等待、平均速度、累计油耗和急制动次数。

## 扰动事件

事件时间均相对本轮开始，可放入 `initial_events`，也可运行中添加：

```python
from simulation.sumo import AccidentEvent, LaneClosureEvent, SpeedLimitEvent

manager.add_event(
    session_id,
    LaneClosureEvent(
        event_id="construction-1",
        start_seconds=60,
        end_seconds=180,
        lane_ids=("-56734_0",),
    ),
)

manager.add_event(
    session_id,
    SpeedLimitEvent(
        event_id="slow-zone-1",
        start_seconds=200,
        end_seconds=300,
        lane_ids=("-56734_1",),
        max_speed=5.0,
    ),
)

manager.add_event(
    session_id,
    AccidentEvent(
        event_id="accident-1",
        start_seconds=320,
        end_seconds=420,
        lane_id="-56734_0",
        position_ratio=0.6,
    ),
)
```

取消事件：

```python
manager.cancel_event(session_id, "construction-1")
```

占道事件可重叠，直到最后一个占道结束才恢复；限速重叠时取最低速度。事故会生成红色
静止车辆。相同 lane 上的事故不能与事故或占道在时间上重叠。事件状态为
`SCHEDULED`、`ACTIVE`、`COMPLETED`、`CANCELLED`、`FAILED`。

## 停止与等待

```python
manager.stop(session_id)
final_snapshot = manager.wait(session_id, timeout=30)
```

会话状态为 `RUNNING`、`PAUSED`、`COMPLETED`、`STOPPED` 或 `FAILED`。`COMPLETED`
表示自然结束，`STOPPED` 表示人工停止，`FAILED` 时查看
`snapshot.error`。会话配置、route、additional 和 manifest 诊断文件位于
`outputs/sessions/<session_id>/`。

## CLI

```bash
python -m simulation.sumo.run --mode fixed --gui \
  --intersection demo_2 \
  --period morning_peak \
  --origin demo_2:west \
  --window-start 1800 \
  --duration 1200 \
  --flow-multiplier 1.5 \
  --playback-speed 2 \
  --event-file events.json
```

`--origin` 可重复。`--playback-speed` 会自动启用墙钟限速；不传时可继续使用原来的
`--realtime` 表示 `1x`。事件文件格式：

```json
{
  "events": [
    {
      "event_type": "lane_closure",
      "event_id": "construction-1",
      "start_seconds": 60,
      "end_seconds": 180,
      "lane_ids": ["-56734_0"]
    },
    {
      "event_type": "speed_limit",
      "event_id": "slow-zone-1",
      "start_seconds": 200,
      "end_seconds": 300,
      "lane_ids": ["-56734_1"],
      "max_speed": 5.0
    },
    {
      "event_type": "accident",
      "event_id": "accident-1",
      "start_seconds": 320,
      "end_seconds": 420,
      "lane_id": "-56734_0",
      "position_ratio": 0.6
    }
  ]
}
```
