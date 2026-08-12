# SUMO 仿真系统

本文整理仿真组当前维护的 SUMO 构建、运行和算法交互说明。真实部署环境为已全局安装
SUMO 的 Linux 服务器；本项目不要求后端、算法或前端直接访问 SUMO、libsumo、TraCI 或
路网 XML，所有运行时交互统一经过仿真内核。

## 一、构建：官方路网、信号与车流

构建部分负责把赛方指定的 20 个雄安路口、官方红绿灯配时和官方车流需求转换为 SUMO
可运行产物。

### 1. 源数据分层

| 文件 | 维护内容 |
|---|---|
| `data/maps/sumo/map/TotalMap_20.net.xml` | 20 路口基础路网，包括 edge、lane、connection 和 junction |
| `data/maps/sumo/map/TotalMap_20.intersections.json` | `demo_N` 与 SUMO junction/TLS 的权威映射 |
| `data/maps/sumo/tls/official_tls_plans.json` | 官方周期、相位、绿灯、黄灯、全红和时段 |
| `data/maps/sumo/tls/official_tls_topology.json` | 官方进口、相位与 SUMO edge/movement 的适配关系 |
| `data/maps/sumo/traffic/official_traffic_demands.json` | 官方 15 分钟转向交通量、进口映射和车流口径 |

配时与地图拓扑分开维护：修改官方秒数时只改 `official_tls_plans.json`；路网重建导致
edge、lane 或 connection 变化时，只修正 `official_tls_topology.json` 和
`TotalMap_20.intersections.json`。

### 2. 信号配置口径

每个 signal program 定义 `program_id`、官方时段、完整周期和相位。构建器会强校验：

- 每个相位满足 `green + yellow + all_red == total`；
- 所有相位 `total` 之和等于 `cycle_duration`；
- 每个官方相位在拓扑中有且只有一个定义；
- 所有正常转向至少被一个相位放行；
- 使用受保护绿 `G` 的 movement 之间不能被 SUMO foe 矩阵判定为冲突。

拓扑有两种写法：当相位语义在早高峰、平峰、晚高峰一致时使用顶层 `phases`；当相位编号
在不同时段代表不同方向，或相位数量不同时，使用
`programs -> program_id -> phases`。例如 `demo_4` 的早高峰相位 1 为“南北向直行”，
晚高峰相位 1 为“东西向直行”，平峰又只有 3 个相位，因此使用分 program 的相位定义。

相位内的 `protected` 表示受保护放行组，生成大写绿 `G`；`permissive` 表示让行放行组，
生成小写绿 `g`。当官方要求同相位放行、但 SUMO foe 矩阵判定主轨迹冲突时，应把对应
movement 或相位配置为让行放行，让车辆按冲突矩阵避让，而不是生成互相冲突的受保护绿。

右转策略由 `right_turn_policy` 和可选的 `phase_controlled_right_turn_approaches` 描述。
`permissive_always` 表示未受控右转全周期让行绿；列入受控右转列表的进口必须显式写入相位，
未写入的相位保持红灯。`phase_controlled` 是兼容写法，表示所有有效右转都受相位控制。

掉头由 `u_turn_policy` 控制。官方需求不包含掉头的路口使用 `blocked`，构建派生路网时删除
或阻断 SUMO `t/uturn` 连接；允许掉头跟随左转时使用 `with_left`，掉头连接继承同进口左转
灯色，但车流生成仍区分左转路线和掉头路线。

### 3. 全量构建

服务器已配置 SUMO 后，执行：

```bash
python -m simulation.sumo.building.build_tls
```

该命令会基于 `TotalMap_20.net.xml` 生成带官方信号的派生路网、官方 additional 文件、
manifest、连接核对报告，以及早高峰、平峰、晚高峰三套全局车流场景。

`data/maps/sumo/generated/` 是可删除、可重建且不提交的目录。完整构建会先清空该目录，
防止旧路口、旧信号或旧车流残留；不要手工修改其中的产物。

### 4. 派生路网规则

构建器会先检查基础路网中的 junction 类型。已经是 `traffic_light` 的路口会尽量保留
现有 `linkIndex` 和 foe 矩阵，不重复交给 `netconvert --tls.set`；仍是 `priority`
的路口才由 `netconvert` 信号化。

只要本次构建需要执行 `netconvert`，命令都会带上：

```bash
--tls.ignore-internal-junction-jam true
```

这允许派生网络接受后续 additional file 中的官方让行相位。实际通行优先级仍由 `G`、`g`
和 junction foe 矩阵共同决定，构建器不会为了绕过 SUMO 报错而改写官方配时。

派生网络中每个受控 TLS 都必须有可用 `tlLogic`，且状态字符串长度必须与实际 `linkIndex`
一致。构建器会在写入 manifest 前检查这些条件，避免无效信号产物进入运行时。

### 5. 生成产物

| 生成路径 | 用途 |
|---|---|
| `data/maps/sumo/generated/network/TotalMap_20.signals.net.xml` | 加入目标 TLS 的公共派生路网 |
| `data/maps/sumo/generated/signals/official_tls.add.xml` | 所有官方 SUMO signal programs |
| `data/maps/sumo/generated/manifests/tls_manifest.json` | runner 使用的相位、连接、lane 和灯色桥接数据 |
| `data/maps/sumo/generated/manifests/traffic_manifest.json` | 场景路径、官方时间、车流合计和端点延长映射 |
| `data/maps/sumo/generated/reports/official_tls_connections.csv` | 人工核对 connection、movement 和 linkIndex |
| `data/maps/sumo/generated/traffic/global/PERIOD/routes.rou.xml` | 满足全部路口 15 分钟约束的全局车流 |
| `data/maps/sumo/generated/traffic/global/PERIOD/signals.add.xml` | 全部路口在该时段使用的官方 program |
| `data/maps/sumo/generated/traffic/global/PERIOD/simulation.sumocfg` | 可直接运行的全局 SUMO 场景 |
| `data/maps/sumo/generated/reports/traffic_quality_PERIOD.json/csv` | PCU、GEH、车型和跨路口路线质量报告 |
| `data/maps/sumo/generated/reports/traffic_od_PERIOD.json/csv` | 九区域 OD PCU 矩阵 |

`PERIOD` 取值为 `morning_peak`、`off_peak`、`evening_peak`。全量构建完成后，
`tls_manifest.json` 应包含 20 个路口；`traffic_manifest.json` 应为 schema v3，且只包含
3 个 `global_PERIOD` 场景。真实车流的数据口径、PCU 和路线质量说明继续见
`docs/traffic_demand.md`。

## 二、模拟：仿真内核与后端接入

运行部分负责启动 SUMO 会话、向后端提供快照、接收播放控制和事件，并在需要时通过
Redis/Celery 支持多会话。

### 1. 运行边界

后端不直接导入 libsumo 或 TraCI，不自行启动 SUMO runtime。后端应包装
`SimulationManager` 或 `RedisSimulationManager`，把 `subscribe()` 产生的 snapshot 转为
WebSocket 消息；前端只消费后端消息，不连接 SUMO。

无界面会话严格使用进程内 libsumo；单进程 `SimulationManager` 一次只允许一个活动
runtime。并行多会话使用 `RedisSimulationManager`，它通过 Celery prefork 让每个子进程
只运行一个 libsumo 会话，避免共享 libsumo 全局状态。

### 2. 查询能力

```python
from simulation.sumo import SimulationManager

manager = SimulationManager()
catalog = manager.catalog()
```

`catalog.intersections` 给出当前已生成的路口、可用时段、官方进口、坐标和可用于扰动的
lane。后端和前端应从 catalog 生成选项，不要硬编码 `demo_2`、进口名或 SUMO lane ID。
运行前必须已执行全量构建，否则内核会拒绝旧 manifest 或缺失产物。

### 3. 启动会话

```python
from simulation.sumo import SimulationConfig, SimulationManager

manager = SimulationManager()
session_id = manager.start(
    SimulationConfig(
        intersection_ids=("demo_2",),
        period="morning_peak",
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

核心字段规则：

| 字段 | 规则 |
|---|---|
| `intersection_ids` | 非空、唯一，且必须出现在 catalog 中；只决定控制、观测和事件范围 |
| `period` | `morning_peak`、`off_peak`、`evening_peak` |
| `window_start_seconds` | 相对该高峰开始的偏移，必须大于等于 0 |
| `duration_seconds` | 大于 0 且不能超过该高峰剩余时间；`None` 表示运行到时段末尾 |
| `flow_multiplier` | 启动前固定的全局倍率，范围 `0.1-5.0` |
| `control_mode` | `fixed` 或 `algorithm` |
| `algorithm_transport` | 当前只支持 `local`，仅在 `algorithm` 模式使用 |
| `algorithm_module` | 本地 algorithm 模式必填，如 `algorithms.local_policy_example` |
| `decision_interval` | 算法决策周期，默认 5 秒 |
| `step_length` | SUMO 物理仿真步长，默认 0.1 秒 |
| `ai_observer_module` | 可选 AI 观察者模块，可与固定配时或本地算法并用 |
| `ai_frame_interval_seconds` | AI 帧仿真时间间隔，默认 0.5 秒且不得小于 `step_length` |
| `start_paused` | `True` 时 SUMO 加载后停在 `elapsed=0`，等待 `resume()` |
| `playback_speed` | 允许 `1、1.25、1.5、2、3、5`；`None` 表示不限速 |
| `snapshot_interval_seconds` | 快照仿真时间间隔，默认 0.5 秒 |
| `gui` | `False` 使用 libsumo；`True` 仅用于有图形桌面的 TraCI/sumo-gui 调试 |

车流始终是全部已构建路口的全局车流；`intersection_ids` 只选择局部管控、观测和事件范围，
不会从 route 文件中删除其他路口车辆。时间窗口会被平移为本轮 `elapsed_seconds=0`，
例如早高峰偏移 1800 秒对应官方 `07:30:00`。

### 4. 播放控制与订阅

交互式会话建议用 `start_paused=True` 创建。创建成功并建立 WebSocket 后，再设置为播放，
避免页面加载期间仿真时间提前流逝。

```python
manager.resume(session_id)
manager.pause(session_id)
manager.set_playing(session_id, True)
manager.set_playback_speed(session_id, 2.0)
snapshot = manager.snapshot(session_id)
```

`pause()` 和 `resume()` 幂等。暂停期间车辆、红绿灯、事件时间、算法决策周期和官方时钟
全部冻结，但仍可变速、添加/取消事件、恢复或停止。倍速只改变仿真相对于墙钟的播放速度，
不改变车辆物理速度、交通需求或算法参数。

订阅接口：

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

### 5. Snapshot 内容

snapshot 面向后端和前端渲染，主要包含：

- 会话状态、序号、elapsed、duration、进度和官方时钟；
- 路口当前相位、待切换相位、`GREEN/YELLOW/CLEARANCE` 阶段和 lane 指标；
- 活动车辆 ID、车型、坐标、速度、角度、road、lane、路线和下一受控信号；
- 等待时间、延误、里程、在线油耗、急制动次数、当前速度目标和换道目标；
- 事件状态；
- 累计出发/到达、活动/剩余/停车车辆、总等待、平均速度、累计油耗和急制动次数。

事故占位车会出现在快照中，但 `controllable=false`，算法不能对其下发车辆动作。自然结束或
人工停止后，最终 `fuel_consumed_mg/ml`、`departed_vehicles` 和 `arrived_vehicles`
由 `tripinfo.xml` 重新汇总，终态快照和算法 `finish` 汇总是最终口径。

### 6. 扰动事件

事件时间均相对本轮开始，可放入 `initial_events`，也可运行中添加。当前事件类型：

| 事件 | 作用 |
|---|---|
| `lane_closure` | 在指定时间窗封闭 lane |
| `speed_limit` | 在指定时间窗对 lane 施加临时限速 |
| `accident` | 在 lane 上生成红色静止事故占位车 |
| `major_event_opening` | 开场时向场馆接驳道路额外注入车流 |
| `major_event_closing` | 散场时从场馆接驳道路向外扩散额外车流 |

大型活动开场/散场是运行时扰动车流，不写入官方需求文件，也不计入
`planned_vehicle_count`。占道事件可重叠，直到最后一个占道结束才恢复；限速重叠时取最低
速度。相同 lane 上的事故不能与事故或占道在时间上重叠。事件状态为
`SCHEDULED`、`ACTIVE`、`COMPLETED`、`CANCELLED`、`FAILED`。

### 7. 后端 HTTP/WebSocket 映射

后端可以按下表包装仿真内核：

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

播放接口请求体固定为：

```json
{"playing": true}
```

倍速接口请求体固定为：

```json
{"speed": 2.0}
```

两个接口成功后都返回最新 snapshot。会话不存在返回 `404`，会话已结束返回 `409`，请求字段
或倍速非法返回 `422`。前端不需要等待下一条 WebSocket 消息再更新按钮状态。

### 8. Redis/Celery 多会话

多会话服务器使用 `RedisSimulationManager`：

```python
from simulation.sumo import RedisSimulationManager, SimulationConfig

manager = RedisSimulationManager()
session_id = manager.start(
    SimulationConfig(
        intersection_ids=("demo_2",),
        period="morning_peak",
        duration_seconds=600,
        gui=False,
        start_paused=True,
        playback_speed=1.0,
    )
)
```

真实服务器应为 Linux，并已全局配置 SUMO。Redis 使用数据库 0 作为 Celery broker、数据库 1
保存会话状态、数据库 2 保存 Celery 结果。不要把 Redis 的 6379 端口暴露到公网。

启动 worker 时必须使用 prefork：

```bash
celery -A simulation.sumo.engine.distributed.celery_app:app worker \
  --queues citypulse-sumo \
  --pool prefork \
  --concurrency "${CITYPULSE_SUMO_WORKER_CONCURRENCY:-4}" \
  --loglevel INFO
```

不得改用 threads、gevent 或 eventlet；libsumo 是进程内单例，`--concurrency N` 表示 N 个
互相隔离的 libsumo 子进程。分布式模式严格使用 libsumo 并拒绝 `gui=True`，图形调试继续
使用本地 CLI 的 TraCI/sumo-gui 路径。

新会话首先处于 `QUEUED`。worker 领取后依次进入 `STARTING`，再进入 `RUNNING` 或
`PAUSED`。排队时可查询、订阅、等待或停止；暂停、倍速和运行时事件命令需要等到
`STARTING` 之后。

如果构造函数传入自定义 `redis_url`、`generated_dir` 或 `session_root`，必须让 Celery
worker 的 `CITYPULSE_REDIS_STATE_URL`、`CITYPULSE_SUMO_GENERATED_DIR` 和
`CITYPULSE_SUMO_SESSION_ROOT` 指向相同 Redis 与共享目录。终态 Redis 数据默认保存
24 小时，`outputs/sessions/<session_id>/` 中的诊断文件不会自动删除。

### 9. CLI 调试入口

服务器生产运行推荐不带 `--gui`，使用 libsumo：

```bash
python -m simulation.sumo.engine.run --mode fixed \
  --intersection demo_2 \
  --period morning_peak \
  --window-start 1800 \
  --duration 1200 \
  --flow-multiplier 1.5 \
  --playback-speed 2 \
  --event-file events.json
```

算法模式：

```bash
python -m simulation.sumo.engine.run --mode algorithm \
  --algorithm-transport local \
  --algorithm-module algorithms.local_policy_example \
  --intersection demo_2 \
  --period morning_peak \
  --decision-interval 1
```

`--intersection` 可重复选择多个路口，但仍使用同一套全局车流。`--gui` 仅用于具备图形桌面
和 SUMO GUI 的调试环境。

## 三、算法交互：协议 2.0 与 AI 观察者

算法模块不需要安装 SUMO，不读取地图 XML，不调用 TraCI。仿真端独占 TraCI，通过同进程
Python 函数调用把路口、车道和单车状态发送给算法，并执行算法返回的信号灯和车辆动作。

### 1. 本地管控算法

当前算法控制只支持本地 Python 模块。模块必须实现：

```python
def initialize(payload: dict) -> dict: ...
def step(payload: dict) -> dict: ...
def finish(payload: dict) -> object: ...
```

调用时序：

| 函数 | 调用次数 | 用途 |
|---|---:|---|
| `initialize(payload)` | 每轮 1 次 | 接收路口拓扑、相位、车道、车型和控制能力 |
| `step(payload)` | 每个决策周期 1 次 | 接收路口与单车实时状态，返回信号和车辆动作 |
| `finish(payload)` | 每轮 1 次 | 接收结束原因和汇总指标 |

`step` 在 SUMO worker 中同步执行；函数返回前仿真不会进入下一步。模块导入失败、函数缺失、
函数抛出异常、版本或 episode/step 回显错误以及非法动作都会使本轮会话进入 `FAILED`。

### 2. 初始化 payload

初始化请求包含：

- `protocol_version="2.0"`、`episode_id`、`period`、`seed`、`decision_interval` 和
  `minimum_green`；
- `intersections`：每个受控路口的 `phase_order`、官方 phase、车道、connection、
  incoming/outgoing lane 和直接邻接关系；
- `vehicle_types`：官方车辆类型画像，包括 PCU、车辆类别、动力类型、排放模型、尺寸、
  加减速度、最高速度、油耗密度和急制动阈值；
- `edge_lanes`：全网普通 edge 的静态车道权限表，不包含 `:` 开头的 internal edge；
- `vehicle_control`：当前支持的单车动作、动作租约、速度单位和换道范围。

算法必须响应：

```json
{
  "protocol_version": "2.0",
  "episode_id": "<same episode_id>",
  "ready": true
}
```

`phase_order` 是该路口允许的信号动作集合。`connections` 描述上下游车道、movement、
`tls_id` 和 `link_index`；`connection_priorities` 表示相位放行的
`protected/permissive` movement。算法应保存这些静态信息，不要在每一步重新推断拓扑。

`movements` 固定使用 `through/left/right/uturn`。一个物理车道可支持多个转向，所以它是
列表而不是单值。纯出口车道的 `approach_id=null`、`movements=[]`、
`downstream_lane_ids=[]`。

算法选择 `target_lane_index` 时，应使用车辆 `type_id` 和 `location.road_id` 查询
`edge_lanes[road_id]`，只选择 `allowed_vehicle_type_ids` 包含该 `type_id` 的 lane。
如果 `road_id` 不在 `edge_lanes`，或 `road_id` 以 `:` 开头，不应返回换道动作。

### 3. 决策 payload

仿真端按 `decision_interval` 调用 `step(payload)`。决策请求包含：

- `protocol_version`、`episode_id`、`step_id` 和 `simulation_time`；
- `intersections`：每个路口的 `current_phase`、`pending_phase`、`stage`、
  `stage_elapsed` 和 lane 实时指标；
- `vehicles`：每辆官方可控车的位置、运动状态、route、等待/延误、下一受控信号、油耗、
  急制动、前后车间距和距上次换道时间；
- `traffic`：活动车辆、周期出发/到达、剩余车辆、累计油耗和急制动汇总；
- `previous_action_results`：上一决策周期车辆动作的执行结果。

字段口径：

- `stage` 为 `GREEN`、`YELLOW` 或 `CLEARANCE`，安全切换过程由仿真端负责；
- `lane_has_green=true` 表示至少一个 connection 原始灯色为 `G/g`；
- 同车道 connection 灯色不同时，`signal_state="mixed"`；
- 纯出口车道的信号汇总字段为 `null`；
- `queue_length_m` 当前为确定性空间估算值，`queue_length_is_estimate=true`；
- `current_allowed_speed_mps` 是车道当前允许速度，会反映施工限速或封闭；
- `target_speed_mps` 是算法返回的单车速度目标，两者不是同一字段；
- 油耗在线值来自 SUMO HBEFA 采样估计，最终汇总由 `tripinfo.xml` 覆盖；
- 急制动在加速度首次进入阈值时计一次，持续制动不重复计数；
- `leader_gap_m` 和 `follower_gap_m` 是同车道可观察车辆的保险杠间距；
- `time_since_last_lane_change_s` 按仿真时间计算，暂停期间不会增长；
- `active_vehicles` 不包含事故占位车。

### 4. 决策响应

算法响应示例：

```json
{
  "protocol_version": "2.0",
  "episode_id": "<same episode_id>",
  "step_id": 12,
  "actions": {
    "signals": {
      "demo_2": {"target_phase": 2}
    },
    "vehicles": {
      "demo_2_morning_peak_00_west_left.0": {
        "target_speed_mps": 8.0,
        "target_lane_index": 1
      }
    }
  }
}
```

动作规则：

1. `protocol_version`、`episode_id` 和 `step_id` 必须原样回显。
2. `actions` 必须且只能包含对象 `signals` 和 `vehicles`，二者允许为空。
3. 信号动作必须使用初始化给出的路口和 phase ID；省略路口表示保持当前目标相位。
4. 车辆动作只能引用本次请求中的车辆；动作至少设置速度或车道之一。
5. `target_speed_mps` 必须在 `[0, allowed_speed_mps]` 内。
6. `target_lane_index` 只指当前普通 road 上的车道；internal edge、越界车道和禁行车道非法。
7. 单车动作只租用一个决策周期。下一周期省略速度会恢复 SUMO 自主速度，换道不续期。
8. 换道可能因安全间隙不足而未完成；下一步返回 `completed` 或 `not_completed`，这不是协议错误。
9. 仿真端在写入任何 TraCI 状态前验证全部动作；任一非法动作都会拒绝整步并终止 episode。

仿真端负责最小绿灯、黄灯、全红、一个官方路口对应多个物理 TLS 的同步，以及切换期间目标
保留。算法只提交官方 phase ID，不接触灯色字符串、`tls_id` 或 `linkIndex`。

### 5. 结束 payload

会话结束时调用：

```json
{
  "protocol_version": "2.0",
  "episode_id": "<same episode_id>",
  "reason": "completed",
  "simulation_time": 7500.0,
  "departed_vehicles": 2761,
  "arrived_vehicles": 2761,
  "fuel_consumed_mg": 18230000.0,
  "fuel_consumed_ml": 24469.7987,
  "hard_braking_events": 184
}
```

`reason` 为 `completed`、`stopped` 或 `error`。`completed` 和 `stopped` 的车辆完成数及
油耗来自已写完的 `tripinfo.xml`，不是在线遥测积分。`finish` 可返回任意 JSON 可序列化对象。

最小算法模块：

```python
def initialize(payload: dict):
    return {
        "protocol_version": "2.0",
        "episode_id": payload["episode_id"],
        "ready": True,
    }


def step(payload: dict):
    signal_actions = {
        intersection_id: {"target_phase": state["current_phase"]}
        for intersection_id, state in payload["intersections"].items()
    }
    return {
        "protocol_version": "2.0",
        "episode_id": payload["episode_id"],
        "step_id": payload["step_id"],
        "actions": {"signals": signal_actions, "vehicles": {}},
    }


def finish(payload: dict):
    return {"ok": True}
```

### 6. AI 观察者

AI 观察者是只读模块，不返回预测或动作。它可与固定配时或本地算法同时启用：

```python
def initialize(metadata: dict) -> None: ...
def on_frame(frame: dict) -> None: ...
def finish(summary: dict) -> None: ...
```

CLI 示例：

```bash
python -m simulation.sumo.engine.run --mode fixed \
  --ai-observer-module algorithms.ai_observer_example \
  --ai-frame-interval 0.1 \
  --intersection demo_2 \
  --period morning_peak
```

`initialize` 在第一个 SUMO 仿真步之前同步接收扩展后的静态元数据。`on_frame` 接收
`protocol_version`、`episode_id`、单调递增的 `frame_id`、`simulation_time`、完整
`intersections` 车道实时状态、`vehicles` 遥测、`traffic` 汇总和
`previous_action_results`。

默认每 0.5 秒仿真时间生产一帧。AI 帧与算法决策落在同一仿真步时只刷新一次完整车辆观测；
暂停时仿真时间冻结，不产生帧。观察者在线程中消费容量为 1 的最新帧队列，推理较慢时旧帧
会被覆盖，SUMO 不等待；`frame_id` 不重新编号，因此序号缺口就是跳帧证据。

自然结束或人工停止时，仿真停止生产，投递最终帧，等待当前帧和队列内最新帧处理完，然后
调用一次 `finish`。结束汇总中的 `observer_frames` 包含 `generated`、`consumed`、
`dropped`。后台异常会传播并使会话失败；排空或 `finish` 超过
`ai_observer_shutdown_timeout` 也会失败。
